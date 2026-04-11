"""Tests for manual override detection with grace period."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_is_in_grace_period_returns_false_when_no_timestamp():
    """Test that _is_in_grace_period returns False when no timestamp exists."""
    from custom_components.adaptive_cover_pro.const import COMMAND_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        command_grace_seconds=COMMAND_GRACE_PERIOD_SECONDS,
    )

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
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        command_grace_seconds=COMMAND_GRACE_PERIOD_SECONDS,
    )
    coordinator._grace_mgr._command_timestamps["cover.test"] = (
        dt.datetime.now().timestamp()
    )

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
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        command_grace_seconds=COMMAND_GRACE_PERIOD_SECONDS,
    )
    # Set timestamp to 10 seconds ago (past the 5-second grace period)
    coordinator._grace_mgr._command_timestamps["cover.test"] = (
        dt.datetime.now().timestamp() - 10
    )

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
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Test the GracePeriodManager timeout directly (the coordinator delegates to it)
    mgr = GracePeriodManager(
        logger=MagicMock(),
        command_grace_seconds=0.1,
    )
    mgr._command_timestamps["cover.test"] = dt.datetime.now().timestamp()
    mgr._grace_period_tasks["cover.test"] = MagicMock()

    await mgr._command_grace_period_timeout("cover.test")

    # Verify tracking was cleared
    assert "cover.test" not in mgr._command_timestamps
    assert "cover.test" not in mgr._grace_period_tasks


def test_cancel_grace_period_removes_tracking():
    """Test that _cancel_grace_period removes all tracking data."""
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(logger=MagicMock())
    mock_task = MagicMock()
    mock_task.done.return_value = False
    coordinator._grace_mgr._grace_period_tasks["cover.test"] = mock_task
    coordinator._grace_mgr._command_timestamps["cover.test"] = (
        dt.datetime.now().timestamp()
    )

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call cancel method
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Verify task was cancelled
    mock_task.cancel.assert_called_once()

    # Verify tracking was cleared
    assert "cover.test" not in coordinator._grace_mgr._grace_period_tasks
    assert "cover.test" not in coordinator._grace_mgr._command_timestamps


def test_cancel_grace_period_handles_completed_task():
    """Test that _cancel_grace_period handles already completed tasks."""
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(logger=MagicMock())
    mock_task = MagicMock()
    mock_task.done.return_value = True  # Task already done
    coordinator._grace_mgr._grace_period_tasks["cover.test"] = mock_task
    coordinator._grace_mgr._command_timestamps["cover.test"] = (
        dt.datetime.now().timestamp()
    )

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call cancel method
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Verify cancel was NOT called (task already done)
    mock_task.cancel.assert_not_called()

    # Verify tracking was still cleared
    assert "cover.test" not in coordinator._grace_mgr._grace_period_tasks
    assert "cover.test" not in coordinator._grace_mgr._command_timestamps


def test_cancel_grace_period_handles_missing_entity():
    """Test that _cancel_grace_period handles entities with no active grace period."""
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(logger=MagicMock())

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Should not raise any exceptions
    AdaptiveDataUpdateCoordinator._cancel_grace_period(coordinator, "cover.test")

    # Tracking should still be empty
    assert "cover.test" not in coordinator._grace_mgr._grace_period_tasks
    assert "cover.test" not in coordinator._grace_mgr._command_timestamps


# ---------------------------------------------------------------------------
# Reset button tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_button_clears_manual_override_and_sends_post_refresh_position():
    """Reset button must reset override, refresh, then delegate to the shared send path.

    The shared path (_async_send_after_override_clear) owns force=True and the
    time-window / auto-control gates.  The button supplies the post-refresh state,
    the options dict, the target entities, and the "manual_reset" trigger.
    """
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.living_room"
    POST_REFRESH_STATE = 52  # position pipeline returns after override is cleared
    options = {"some_option": True}

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.config_entry.options = options
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator.state = POST_REFRESH_STATE
    coordinator.async_refresh = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_id})

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # Manager reset must be called before the refresh
    coordinator.manager.reset.assert_called_once_with(entity_id)
    coordinator.async_refresh.assert_called_once()

    # Must delegate to the shared send path with correct args
    coordinator._async_send_after_override_clear.assert_called_once()
    call = coordinator._async_send_after_override_clear.call_args
    assert call[0][0] == POST_REFRESH_STATE     # post-refresh state
    assert call[0][1] == options                 # options dict
    assert call[1].get("entities") == [entity_id]
    assert call[1].get("trigger") == "manual_reset"


@pytest.mark.asyncio
async def test_reset_button_suppresses_redetection_during_refresh():
    """wait_for_target must be True while async_refresh runs to block re-detection."""
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.bedroom"
    states_during_refresh = []

    async def capture_refresh():
        # Record wait_for_target state at the moment async_refresh executes
        states_during_refresh.append(coordinator.wait_for_target.get(entity_id))

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.state = 50
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock(side_effect=capture_refresh)
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_id})

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # During refresh the suppression flag must be active
    assert states_during_refresh == [True]


@pytest.mark.asyncio
async def test_reset_button_clears_wait_for_target_when_no_command_sent():
    """wait_for_target must be False after reset when the shared send path skips the entity.

    The shared method (_async_send_after_override_clear) returns a set of sent
    entity_ids.  If an entity is absent (gated by time window, auto-control off,
    or no positioning capability), the button must clear wait_for_target.
    """
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.no_position_support"

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.state = 50
    coordinator.config_entry.options = {}
    # Shared method returns empty set — entity was not sent to
    coordinator._async_send_after_override_clear = AsyncMock(return_value=set())
    coordinator.async_refresh = AsyncMock()
    coordinator.wait_for_target = {entity_id: True}  # was True from suppression
    coordinator.cover_state_change = False

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # Not in sent set — wait_for_target must be cleared so state tracking resumes
    assert coordinator.wait_for_target[entity_id] is False


# ---------------------------------------------------------------------------
# reset_if_needed — returns expired set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_if_needed_returns_expired_entity_ids():
    """reset_if_needed() must return the set of entity IDs whose override just expired."""
    from custom_components.adaptive_cover_pro.managers.manual_override import AdaptiveCoverManager

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"seconds": 1},
        logger=MagicMock(),
    )

    entity_a = "cover.a"
    entity_b = "cover.b"

    # Mark both as manual with a timestamp old enough to expire
    old_time = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
    manager.manual_control[entity_a] = True
    manager.manual_control_time[entity_a] = old_time
    manager.manual_control[entity_b] = True
    manager.manual_control_time[entity_b] = old_time

    expired = await manager.reset_if_needed()

    assert expired == {entity_a, entity_b}
    assert not manager.is_cover_manual(entity_a)
    assert not manager.is_cover_manual(entity_b)


@pytest.mark.asyncio
async def test_reset_if_needed_returns_empty_when_nothing_expired():
    """reset_if_needed() must return an empty set when no overrides have expired."""
    from custom_components.adaptive_cover_pro.managers.manual_override import AdaptiveCoverManager

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"minutes": 30},
        logger=MagicMock(),
    )

    entity = "cover.recent"
    manager.manual_control[entity] = True
    manager.manual_control_time[entity] = dt.datetime.now(dt.UTC)  # just set

    expired = await manager.reset_if_needed()

    assert expired == set()
    assert manager.is_cover_manual(entity)


@pytest.mark.asyncio
async def test_reset_button_sends_correct_position_with_climate_mode():
    """Button must pass the post-refresh pipeline position to the shared send path.

    Covers the climate-mode scenario where ManualOverrideHandler returns solar/default
    but ClimateHandler wins after the override is cleared.
    """
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    entity_id = "cover.climate_room"
    CLIMATE_POSITION = 70   # what ClimateHandler returns after override clears
    SOLAR_POSITION = 45     # what ManualOverrideHandler was returning during override

    coordinator = MagicMock()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.config_entry.options = {}
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False

    # Simulate: after refresh the pipeline now returns the climate position
    coordinator.state = CLIMATE_POSITION
    coordinator.async_refresh = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_id})

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = [entity_id]

    await button.async_press()

    # The state passed to the shared method must be the post-refresh climate position
    call = coordinator._async_send_after_override_clear.call_args
    sent_position = call[0][0]
    assert sent_position == CLIMATE_POSITION
    assert sent_position != SOLAR_POSITION
