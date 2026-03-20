"""Tests for manual override detection with grace period."""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_is_in_grace_period_returns_false_when_no_timestamp():
    """Test that _is_in_grace_period returns False when no timestamp exists."""
    from custom_components.adaptive_cover_pro.const import COMMAND_GRACE_PERIOD_SECONDS

    # Create minimal mock coordinator
    coordinator = MagicMock()
    coordinator._command_grace_period_seconds = COMMAND_GRACE_PERIOD_SECONDS
    coordinator._command_timestamps = {}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_grace_period(
        coordinator, "cover.test"
    )

    assert result is False


def test_is_in_grace_period_returns_true_when_within_period():
    """Test that _is_in_grace_period returns True when within grace period."""
    from custom_components.adaptive_cover_pro.const import COMMAND_GRACE_PERIOD_SECONDS

    # Create minimal mock coordinator
    coordinator = MagicMock()
    coordinator._command_grace_period_seconds = COMMAND_GRACE_PERIOD_SECONDS
    now = dt.datetime.now().timestamp()
    coordinator._command_timestamps = {"cover.test": now}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_grace_period(
        coordinator, "cover.test"
    )

    assert result is True


def test_is_in_grace_period_returns_false_when_expired():
    """Test that _is_in_grace_period returns False when grace period expired."""
    from custom_components.adaptive_cover_pro.const import COMMAND_GRACE_PERIOD_SECONDS

    # Create minimal mock coordinator
    coordinator = MagicMock()
    coordinator._command_grace_period_seconds = COMMAND_GRACE_PERIOD_SECONDS
    # Set timestamp to 10 seconds ago (past the 5-second grace period)
    past = dt.datetime.now().timestamp() - 10
    coordinator._command_timestamps = {"cover.test": past}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_grace_period(
        coordinator, "cover.test"
    )

    assert result is False


@pytest.mark.asyncio
async def test_grace_period_timeout_clears_tracking():
    """Test that grace period timeout clears tracking data."""

    # Create minimal mock coordinator
    coordinator = MagicMock()
    coordinator._command_grace_period_seconds = 0.1  # Short period for testing
    coordinator._command_timestamps = {"cover.test": dt.datetime.now().timestamp()}
    coordinator._grace_period_tasks = {}
    coordinator.logger = MagicMock()

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the timeout method
    await AdaptiveDataUpdateCoordinator._grace_period_timeout(
        coordinator, "cover.test"
    )

    # Verify tracking was cleared
    assert "cover.test" not in coordinator._command_timestamps
    assert "cover.test" not in coordinator._grace_period_tasks


def test_cancel_grace_period_removes_tracking():
    """Test that _cancel_grace_period removes all tracking data."""
    # Create minimal mock coordinator with active task
    coordinator = MagicMock()
    mock_task = MagicMock()
    mock_task.done.return_value = False
    coordinator._grace_period_tasks = {"cover.test": mock_task}
    coordinator._command_timestamps = {"cover.test": dt.datetime.now().timestamp()}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call cancel method
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Verify task was cancelled
    mock_task.cancel.assert_called_once()

    # Verify tracking was cleared (the method modifies the dicts)
    assert "cover.test" not in coordinator._grace_period_tasks
    assert "cover.test" not in coordinator._command_timestamps


def test_cancel_grace_period_handles_completed_task():
    """Test that _cancel_grace_period handles already completed tasks."""
    # Create minimal mock coordinator with completed task
    coordinator = MagicMock()
    mock_task = MagicMock()
    mock_task.done.return_value = True  # Task already done
    coordinator._grace_period_tasks = {"cover.test": mock_task}
    coordinator._command_timestamps = {"cover.test": dt.datetime.now().timestamp()}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call cancel method
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Verify cancel was NOT called (task already done)
    mock_task.cancel.assert_not_called()

    # Verify tracking was still cleared
    assert "cover.test" not in coordinator._grace_period_tasks
    assert "cover.test" not in coordinator._command_timestamps


def test_cancel_grace_period_handles_missing_entity():
    """Test that _cancel_grace_period handles entities with no active grace period."""
    # Create minimal mock coordinator with empty tracking
    coordinator = MagicMock()
    coordinator._grace_period_tasks = {}
    coordinator._command_timestamps = {}

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Should not raise any exceptions
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Tracking should still be empty
    assert "cover.test" not in coordinator._grace_period_tasks
    assert "cover.test" not in coordinator._command_timestamps


# ---------------------------------------------------------------------------
# Reset button tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_button_clears_manual_override_and_stays_cleared():
    """Reset button must clear manual override and leave it cleared after refresh."""
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.living_room"

    # Build a minimal coordinator mock
    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.state = 75
    coordinator.config_entry.options = {}
    coordinator.check_position_delta.return_value = True
    coordinator.async_set_position = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    # Simulate cover reaching target immediately
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False

    # Build button with the mocked coordinator
    config_entry = MagicMock()
    config_entry.options = {"entities": [entity_id]}
    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # Manager reset was called
    coordinator.manager.reset.assert_called_once_with(entity_id)
    # Refresh was called
    coordinator.async_refresh.assert_called_once()
    # After refresh, wait_for_target was cleared (False), not left suppressing events
    assert coordinator.wait_for_target[entity_id] is False


@pytest.mark.asyncio
async def test_reset_button_suppresses_redetection_during_refresh():
    """wait_for_target must be True while async_refresh runs, then cleared."""
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.bedroom"
    states_during_refresh = []

    async def capture_refresh():
        # Record wait_for_target value at the moment refresh runs
        states_during_refresh.append(coordinator.wait_for_target.get(entity_id))

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.state = 50
    coordinator.config_entry.options = {}
    coordinator.check_position_delta.return_value = True
    coordinator.async_set_position = AsyncMock()
    coordinator.async_refresh = AsyncMock(side_effect=capture_refresh)
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False

    config_entry = MagicMock()
    config_entry.options = {"entities": [entity_id]}
    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # During refresh, suppression must be active (True)
    assert states_during_refresh == [True]
    # After refresh, suppression must be cleared (False)
    assert coordinator.wait_for_target[entity_id] is False


@pytest.mark.asyncio
async def test_reset_button_times_out_if_cover_never_reaches_target():
    """Button must not hang forever when cover never reaches the target position."""
    import time

    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.stuck"

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.state = 80
    coordinator.config_entry.options = {}
    coordinator.check_position_delta.return_value = True
    coordinator.async_set_position = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    # Cover never clears wait_for_target — simulates position mismatch
    coordinator.wait_for_target = {entity_id: True}
    coordinator.cover_state_change = False

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    start = time.monotonic()
    # Patch event loop time so the 30-second timeout fires immediately
    fake_time = [0.0]

    original_sleep = asyncio.sleep

    async def fast_sleep(delay):
        fake_time[0] += 31  # Jump past the 30-second deadline

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.time.side_effect = lambda: fake_time[0]
        with patch("asyncio.sleep", side_effect=fast_sleep):
            await button.async_press()

    elapsed = time.monotonic() - start
    # Should complete almost instantly (well under a real second)
    assert elapsed < 5
    # Refresh was still called despite the timeout
    coordinator.async_refresh.assert_called_once()
