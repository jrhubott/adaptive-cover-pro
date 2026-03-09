"""Tests for motion-based automatic control feature."""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


def test_is_motion_detected_no_sensors_configured():
    """Test motion detection when no sensors configured (feature disabled)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.config_entry.options.get.return_value = []

    # Should return True (assume presence) when no sensors configured
    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_single_sensor_on():
    """Test motion detected with single sensor on."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return ["binary_sensor.motion_living_room"]
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock sensor state as "on"
    state = MagicMock()
    state.state = "on"
    coordinator.hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_single_sensor_off():
    """Test no motion detected with single sensor off."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return ["binary_sensor.motion_living_room"]
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock sensor state as "off"
    state = MagicMock()
    state.state = "off"
    coordinator.hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_detected_multiple_sensors_or_logic():
    """Test OR logic: ANY sensor on means motion detected."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return [
                "binary_sensor.motion_living_room",
                "binary_sensor.motion_kitchen",
                "binary_sensor.motion_bedroom",
            ]
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock: only kitchen sensor is on
    def get_state(entity_id):
        state = MagicMock()
        if entity_id == "binary_sensor.motion_kitchen":
            state.state = "on"
        else:
            state.state = "off"
        return state

    coordinator.hass.states.get.side_effect = get_state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_all_sensors_off():
    """Test no motion when all sensors are off."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return [
                "binary_sensor.motion_living_room",
                "binary_sensor.motion_kitchen",
                "binary_sensor.motion_bedroom",
            ]
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock: all sensors off
    state = MagicMock()
    state.state = "off"
    coordinator.hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_detected_sensor_unavailable():
    """Test that unavailable sensors are treated as off."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return ["binary_sensor.motion_living_room"]
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock sensor unavailable (None state)
    coordinator.hass.states.get.return_value = None

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_timeout_active_no_sensors():
    """Test motion timeout inactive when no sensors configured."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return []
        return default

    coordinator.config_entry.options.get.side_effect = get_option
    coordinator._motion_timeout_active = False

    result = AdaptiveDataUpdateCoordinator.is_motion_timeout_active.fget(coordinator)
    assert result is False


def test_is_motion_timeout_active_with_sensors():
    """Test motion timeout active flag when sensors configured."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_SENSORS
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    def get_option(key, default=None):
        if key == CONF_MOTION_SENSORS:
            return ["binary_sensor.motion_living_room"]
        return default

    coordinator.config_entry.options.get.side_effect = get_option
    coordinator._motion_timeout_active = True

    result = AdaptiveDataUpdateCoordinator.is_motion_timeout_active.fget(coordinator)
    assert result is True


@pytest.mark.asyncio
async def test_motion_timeout_handler_sets_active_flag():
    """Test that motion timeout handler sets the active flag."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._motion_timeout_active = False
    coordinator.state_change = False
    coordinator.logger = MagicMock()
    coordinator.async_refresh = AsyncMock()

    # Mock is_motion_detected to return False (no motion after timeout)
    def mock_is_motion_detected():
        return False

    type(coordinator).is_motion_detected = property(lambda self: False)

    # Call timeout handler with short timeout
    await AdaptiveDataUpdateCoordinator._motion_timeout_handler(coordinator, 0.1)

    # Verify timeout was activated
    assert coordinator._motion_timeout_active is True
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_motion_timeout_handler_cancels_if_motion_detected():
    """Test that motion timeout handler cancels if motion detected during timeout."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._motion_timeout_active = False
    coordinator.logger = MagicMock()
    coordinator.async_refresh = AsyncMock()

    # Mock is_motion_detected to return True (motion detected during timeout)
    type(coordinator).is_motion_detected = property(lambda self: True)

    # Call timeout handler
    await AdaptiveDataUpdateCoordinator._motion_timeout_handler(coordinator, 0.1)

    # Verify timeout was NOT activated
    assert coordinator._motion_timeout_active is False
    coordinator.async_refresh.assert_not_called()


def test_cancel_motion_timeout():
    """Test canceling motion timeout task."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    # Create a mock task
    task = MagicMock()
    task.done.return_value = False
    coordinator._motion_timeout_task = task

    # Cancel timeout
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)

    # Verify task was canceled and cleared
    task.cancel.assert_called_once()
    assert coordinator._motion_timeout_task is None


def test_cancel_motion_timeout_no_task():
    """Test canceling motion timeout when no task exists."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._motion_timeout_task = None

    # Should not raise an error
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)
    assert coordinator._motion_timeout_task is None


def test_cancel_motion_timeout_task_done():
    """Test canceling motion timeout when task is already done."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()

    # Create a mock task that's done
    task = MagicMock()
    task.done.return_value = True
    coordinator._motion_timeout_task = task

    # Cancel timeout
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)

    # Task should not be canceled (already done), but should be cleared
    task.cancel.assert_not_called()
    assert coordinator._motion_timeout_task is None


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on():
    """Test motion state change handler for motion detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._motion_timeout_active = True
    coordinator.state_change = False
    coordinator.logger = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator._last_motion_time = None

    # Mock _cancel_motion_timeout
    coordinator._cancel_motion_timeout = Mock()

    # Create event with motion detected
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="on"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify timeout was canceled
    coordinator._cancel_motion_timeout.assert_called_once()

    # Verify last motion time was updated
    assert coordinator._last_motion_time is not None

    # Verify motion timeout was deactivated and refresh was called
    assert coordinator._motion_timeout_active is False
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_check_motion_state_change_off():
    """Test motion state change handler for motion stopped."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.logger = MagicMock()

    # Mock is_motion_detected to return False (no other sensors active)
    type(coordinator).is_motion_detected = property(lambda self: False)

    # Mock _start_motion_timeout
    coordinator._start_motion_timeout = Mock()

    # Create event with motion stopped
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="off"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify timeout was started
    coordinator._start_motion_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_async_check_motion_state_change_off_other_sensors_active():
    """Test motion stopped but other sensors still active."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.logger = MagicMock()

    # Mock is_motion_detected to return True (other sensors still active)
    type(coordinator).is_motion_detected = property(lambda self: True)

    # Mock _start_motion_timeout
    coordinator._start_motion_timeout = Mock()

    # Create event with motion stopped
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="off"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify timeout was NOT started (other sensors still active)
    coordinator._start_motion_timeout.assert_not_called()


def test_determine_control_status_motion_timeout():
    """Test control status returns MOTION_TIMEOUT when active."""
    from custom_components.adaptive_cover_pro.const import ControlStatus
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._automatic_control = True

    # Mock is_force_override_active to return False
    type(coordinator).is_force_override_active = property(lambda self: False)

    # Mock is_motion_timeout_active to return True
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    result = AdaptiveDataUpdateCoordinator._determine_control_status(coordinator)
    assert result == ControlStatus.MOTION_TIMEOUT


def test_determine_control_status_force_override_precedence():
    """Test force override takes precedence over motion timeout."""
    from custom_components.adaptive_cover_pro.const import ControlStatus
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._automatic_control = True

    # Both active: force override and motion timeout
    type(coordinator).is_force_override_active = property(lambda self: True)
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    result = AdaptiveDataUpdateCoordinator._determine_control_status(coordinator)
    assert result == ControlStatus.FORCE_OVERRIDE_ACTIVE


def test_state_property_motion_timeout_returns_default():
    """Test state property returns default position during motion timeout."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.default_state = 60
    coordinator.logger = MagicMock()

    # Mock is_force_override_active to return False
    type(coordinator).is_force_override_active = property(lambda self: False)

    # Mock is_motion_timeout_active to return True
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    result = AdaptiveDataUpdateCoordinator.state.fget(coordinator)
    assert result == 60


def test_state_property_force_override_precedence():
    """Test state property prioritizes force override over motion timeout."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_FORCE_OVERRIDE_POSITION,
    )
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.logger = MagicMock()

    def get_option(key, default=None):
        if key == CONF_FORCE_OVERRIDE_POSITION:
            return 0
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Both active: force override and motion timeout
    type(coordinator).is_force_override_active = property(lambda self: True)
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    result = AdaptiveDataUpdateCoordinator.state.fget(coordinator)
    assert result == 0


def test_build_configuration_diagnostics_includes_motion_data():
    """Test diagnostic data includes motion control information."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MOTION_SENSORS,
        CONF_MOTION_TIMEOUT,
        DEFAULT_MOTION_TIMEOUT,
    )
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator._motion_timeout_active = False

    def get_option(key, default=None):
        options_map = {
            CONF_MOTION_SENSORS: ["binary_sensor.motion_living_room"],
            CONF_MOTION_TIMEOUT: 300,
        }
        return options_map.get(key, default)

    coordinator.config_entry.options.get.side_effect = get_option

    # Mock is_force_override_active
    type(coordinator).is_force_override_active = property(lambda self: False)

    # Mock is_motion_detected
    type(coordinator).is_motion_detected = property(lambda self: True)

    result = AdaptiveDataUpdateCoordinator._build_configuration_diagnostics(coordinator)

    config = result["configuration"]
    assert "motion_sensors" in config
    assert config["motion_sensors"] == ["binary_sensor.motion_living_room"]
    assert "motion_timeout" in config
    assert config["motion_timeout"] == 300
    assert "motion_detected" in config
    assert config["motion_detected"] is True
    assert "motion_timeout_active" in config
    assert config["motion_timeout_active"] is False


@pytest.mark.asyncio
async def test_async_shutdown_cancels_motion_timeout():
    """Test shutdown cancels motion timeout task."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.logger = MagicMock()
    coordinator._grace_period_tasks = {}

    # Mock _cancel_motion_timeout
    coordinator._cancel_motion_timeout = Mock()

    # Mock _stop_position_verification
    coordinator._stop_position_verification = Mock()

    # Call shutdown
    await AdaptiveDataUpdateCoordinator.async_shutdown(coordinator)

    # Verify motion timeout was canceled
    coordinator._cancel_motion_timeout.assert_called_once()
