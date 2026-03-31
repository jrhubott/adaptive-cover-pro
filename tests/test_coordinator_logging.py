"""Tests for coordinator debug logging (cover skip logging, motion cancel logging).

Tests cover:
- async_handle_call_service logs and records reason when conditions prevent a move
- _record_skipped_action stores correct data
- _cancel_motion_timeout logs when canceling an active task
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestHandleCallServiceLogging:
    """async_handle_call_service logs skip reason for each failing condition."""

    def _make_coordinator(self):
        """Build a minimal mock coordinator with real method bound."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord.logger.debug = MagicMock()
        coord.last_skipped_action = {
            "entity_id": None,
            "reason": None,
            "calculated_position": None,
            "timestamp": None,
        }

        # Bind real methods
        coord.async_handle_call_service = (
            AdaptiveDataUpdateCoordinator.async_handle_call_service.__get__(coord)
        )
        coord._record_skipped_action = (
            AdaptiveDataUpdateCoordinator._record_skipped_action.__get__(coord)
        )
        coord.async_set_position = AsyncMock()

        # Default: all conditions pass
        coord.check_adaptive_time = True
        coord.check_position_delta = MagicMock(return_value=True)
        coord.check_time_delta = MagicMock(return_value=True)
        coord.manager = MagicMock()
        coord.manager.is_cover_manual = MagicMock(return_value=False)

        return coord

    @pytest.mark.asyncio
    async def test_skips_outside_time_window(self):
        """Logs and records skip when outside time window."""
        coord = self._make_coordinator()
        coord.check_adaptive_time = False

        await coord.async_handle_call_service("cover.test", 50, {})

        coord.async_set_position.assert_not_called()
        assert coord.last_skipped_action["reason"] == "Outside time window"
        assert coord.last_skipped_action["entity_id"] == "cover.test"
        assert coord.last_skipped_action["calculated_position"] == 50
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("outside time window" in c.lower() for c in calls)

    @pytest.mark.asyncio
    async def test_skips_position_delta_too_small(self):
        """Logs and records skip when position delta too small."""
        coord = self._make_coordinator()
        coord.check_position_delta.return_value = False

        await coord.async_handle_call_service("cover.test", 50, {})

        coord.async_set_position.assert_not_called()
        assert coord.last_skipped_action["reason"] == "Position delta too small"
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("position delta too small" in c.lower() for c in calls)

    @pytest.mark.asyncio
    async def test_skips_time_delta_too_small(self):
        """Logs and records skip when time delta too small."""
        coord = self._make_coordinator()
        coord.check_time_delta.return_value = False

        await coord.async_handle_call_service("cover.test", 50, {})

        coord.async_set_position.assert_not_called()
        assert coord.last_skipped_action["reason"] == "Time delta too small"
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("time delta too small" in c.lower() for c in calls)

    @pytest.mark.asyncio
    async def test_skips_manual_override(self):
        """Logs and records skip when cover is under manual override."""
        coord = self._make_coordinator()
        coord.manager.is_cover_manual.return_value = True

        await coord.async_handle_call_service("cover.test", 50, {})

        coord.async_set_position.assert_not_called()
        assert coord.last_skipped_action["reason"] == "Manual override active"
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("manual override" in c.lower() for c in calls)

    @pytest.mark.asyncio
    async def test_proceeds_when_all_conditions_pass(self):
        """Calls async_set_position when all conditions pass (no skip)."""
        coord = self._make_coordinator()

        await coord.async_handle_call_service("cover.test", 50, {})

        coord.async_set_position.assert_called_once_with("cover.test", 50)
        # No skip recorded
        assert coord.last_skipped_action["entity_id"] is None


class TestRecordSkippedAction:
    """_record_skipped_action stores correct data."""

    def _make_coordinator(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.last_skipped_action = {}
        coord._record_skipped_action = (
            AdaptiveDataUpdateCoordinator._record_skipped_action.__get__(coord)
        )
        return coord

    def test_stores_entity_reason_position(self):
        """Stores entity_id, reason, calculated_position, and timestamp."""
        coord = self._make_coordinator()
        coord._record_skipped_action("cover.living_room", "Outside time window", 75)

        assert coord.last_skipped_action["entity_id"] == "cover.living_room"
        assert coord.last_skipped_action["reason"] == "Outside time window"
        assert coord.last_skipped_action["calculated_position"] == 75
        assert coord.last_skipped_action["timestamp"] is not None


class TestCancelMotionTimeoutLogging:
    """_cancel_motion_timeout logs when canceling an active task."""

    def _make_coordinator(self):
        from unittest.mock import MagicMock

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.motion import MotionManager

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord.logger.debug = MagicMock()

        # Wire a real MotionManager so _cancel_motion_timeout delegates correctly
        mgr = MotionManager(hass=MagicMock(), logger=coord.logger)
        mgr.update_config(sensors=[], timeout_seconds=300)
        coord._motion_mgr = mgr

        coord._cancel_motion_timeout = (
            AdaptiveDataUpdateCoordinator._cancel_motion_timeout.__get__(coord)
        )
        return coord

    def test_logs_when_task_active(self):
        """Logs 'Motion timeout canceled' when an active task is canceled."""
        coord = self._make_coordinator()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        coord._motion_mgr._motion_timeout_task = mock_task

        coord._cancel_motion_timeout()

        mock_task.cancel.assert_called_once()
        assert coord._motion_mgr._motion_timeout_task is None
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("Motion timeout canceled" in c for c in calls)

    def test_no_log_when_no_task(self):
        """Does not log when no task is active."""
        coord = self._make_coordinator()
        coord._motion_mgr._motion_timeout_task = None

        coord._cancel_motion_timeout()

        coord.logger.debug.assert_not_called()
        assert coord._motion_mgr._motion_timeout_task is None

    def test_no_log_when_task_already_done(self):
        """Does not log when task is already done."""
        coord = self._make_coordinator()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        coord._motion_mgr._motion_timeout_task = mock_task

        coord._cancel_motion_timeout()

        mock_task.cancel.assert_not_called()
