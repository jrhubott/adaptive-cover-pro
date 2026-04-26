"""Tests for coordinator debug logging (cover skip logging, motion cancel logging).

Tests cover:
- apply_position() on CoverCommandService logs and records reason when gate checks fail
- _record_skipped_action stores correct data
- _cancel_motion_timeout logs when canceling an active task
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)


def _make_cmd_svc():
    """Build a real CoverCommandService with mocked HA calls for gate-check tests."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    return CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )


def _make_context(**overrides):
    """Build a PositionContext with all gates passing by default."""
    defaults = {
        "auto_control": True,
        "manual_override": False,
        "sun_just_appeared": False,
        "min_change": 2,
        "time_threshold": 2,
        "special_positions": [0, 100],
        "inverse_state": False,
        "force": False,
    }
    defaults.update(overrides)
    return PositionContext(**defaults)


class TestApplyPositionGateLogging:
    """apply_position() records and returns skip reason for each failing gate."""

    @pytest.mark.asyncio
    async def test_skips_auto_control_off(self):
        """Returns skip when auto_control is False."""
        svc = _make_cmd_svc()
        ctx = _make_context(auto_control=False)

        outcome, reason = await svc.apply_position(
            "cover.test", 50, "solar", context=ctx
        )

        assert outcome == "skipped"
        assert reason == "auto_control_off"

    @pytest.mark.asyncio
    async def test_skips_position_delta_too_small(self):
        """Returns skip when position delta is below min_change."""
        svc = _make_cmd_svc()
        # Current position = 50, target = 51, min_change = 5 → delta too small
        svc._get_current_position = MagicMock(return_value=50)
        ctx = _make_context(min_change=5)

        outcome, reason = await svc.apply_position(
            "cover.test", 51, "solar", context=ctx
        )

        assert outcome == "skipped"
        assert reason == "delta_too_small"

    @pytest.mark.asyncio
    async def test_skips_time_delta_too_small(self):
        """Returns skip when time since last command is below threshold."""
        import datetime as dt

        svc = _make_cmd_svc()
        svc._get_current_position = MagicMock(return_value=30)  # big delta
        # Recent last_updated → time delta too small
        recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
        ctx = _make_context(time_threshold=5)

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ):
            outcome, reason = await svc.apply_position(
                "cover.test", 60, "solar", context=ctx
            )

        assert outcome == "skipped"
        assert reason == "time_delta_too_small"

    @pytest.mark.asyncio
    async def test_skips_manual_override(self):
        """Returns skip when manual_override is True."""
        svc = _make_cmd_svc()
        svc._get_current_position = MagicMock(return_value=30)
        ctx = _make_context(manual_override=True)

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ):
            outcome, reason = await svc.apply_position(
                "cover.test", 60, "solar", context=ctx
            )

        assert outcome == "skipped"
        assert reason == "manual_override"

    @pytest.mark.asyncio
    async def test_proceeds_when_all_conditions_pass(self):
        """Returns 'sent' when all gate checks pass."""
        svc = _make_cmd_svc()
        svc._get_current_position = MagicMock(return_value=30)

        with (
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
                return_value=None,
            ),
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
                return_value={"has_set_position": True, "has_set_tilt_position": False},
            ),
        ):
            ctx = _make_context()
            outcome, _ = await svc.apply_position(
                "cover.test", 60, "solar", context=ctx
            )

        assert outcome == "sent"
        assert "cover.test" in svc.target_call
        assert svc.target_call["cover.test"] == 60

    @pytest.mark.asyncio
    async def test_force_bypasses_delta_and_manual_override_gates(self):
        """force=True bypasses delta/time/manual_override but NOT auto_control (issue #293)."""
        svc = _make_cmd_svc()

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value={"has_set_position": True, "has_set_tilt_position": False},
        ):
            ctx = _make_context(
                auto_control=True,
                manual_override=True,
                force=True,
            )
            outcome, _ = await svc.apply_position(
                "cover.test", 0, "sunset", context=ctx
            )

        assert outcome == "sent"


class TestRecordSkippedAction:
    """CoverCommandService.record_skipped_action stores correct data."""

    def test_stores_entity_reason_position(self):
        """Stores entity_id, reason, calculated_position, and timestamp."""
        svc = _make_cmd_svc()
        svc.record_skipped_action("cover.living_room", "Outside time window", 75)

        assert svc.last_skipped_action["entity_id"] == "cover.living_room"
        assert svc.last_skipped_action["reason"] == "Outside time window"
        assert svc.last_skipped_action["calculated_position"] == 75
        assert svc.last_skipped_action["current_position"] is None
        assert svc.last_skipped_action["trigger"] is None
        assert svc.last_skipped_action["inverse_state_applied"] is False
        assert svc.last_skipped_action["timestamp"] is not None


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


# ---------------------------------------------------------------------------
# Manual override detection gate — debug log emitted when gate is closed
# ---------------------------------------------------------------------------


class TestManualGateClosedLogging:
    """Both coordinator gate sites emit a debug log when manual_toggle or automatic_control is off."""

    def _make_state_change_coordinator(self, *, manual_toggle, automatic_control):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock()
        coord.manual_toggle = manual_toggle
        coord.automatic_control = automatic_control
        coord.logger = MagicMock()
        coord.cover_state_change = True
        coord._pending_cover_events = []
        coord._is_in_startup_grace_period = MagicMock(return_value=False)
        coord._manual_gate_closed_log = (
            AdaptiveDataUpdateCoordinator._manual_gate_closed_log.__get__(coord)
        )
        return coord

    @pytest.mark.asyncio
    async def test_state_change_logs_when_manual_toggle_off(self):
        """async_handle_cover_state_change emits debug log when manual_toggle is False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_state_change_coordinator(
            manual_toggle=False, automatic_control=True
        )
        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coord, state=50
        )
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("manual override detection gate closed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_state_change_does_NOT_log_gate_closed_when_only_auto_control_off(self):
        """Issue #293: auto_control off no longer closes the manual-override gate.

        Only manual_toggle=False emits the "gate closed" log line; with
        automatic_control=False the handler now observes events and records
        manual overrides (observation is not action).
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_state_change_coordinator(
            manual_toggle=True, automatic_control=False
        )
        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coord, state=50
        )
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert not any("manual override detection gate closed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_service_call_logs_when_manual_toggle_off(self):
        """async_check_cover_service_call emits debug log when manual_toggle is False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = MagicMock()
        event.data = {
            "domain": "cover",
            "service": "stop_cover",
            "service_data": {"entity_id": "cover.test"},
        }
        event.context = MagicMock()
        event.context.id = "ctx-001"

        coord = MagicMock()
        coord.entities = ["cover.test"]
        coord.manual_toggle = False
        coord.automatic_control = True
        coord.logger = MagicMock()
        coord._cmd_svc._acp_stop_contexts = []
        coord._manual_gate_closed_log = (
            AdaptiveDataUpdateCoordinator._manual_gate_closed_log.__get__(coord)
        )

        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("manual override detection gate closed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_service_call_logs_when_auto_control_off(self):
        """async_check_cover_service_call emits debug log when automatic_control is False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = MagicMock()
        event.data = {
            "domain": "cover",
            "service": "stop_cover",
            "service_data": {"entity_id": "cover.test"},
        }
        event.context = MagicMock()
        event.context.id = "ctx-001"

        coord = MagicMock()
        coord.entities = ["cover.test"]
        coord.manual_toggle = True
        coord.automatic_control = False
        coord.logger = MagicMock()
        coord._cmd_svc._acp_stop_contexts = []
        coord._manual_gate_closed_log = (
            AdaptiveDataUpdateCoordinator._manual_gate_closed_log.__get__(coord)
        )

        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("manual override detection gate closed" in c for c in calls)
