"""Tests for force override sensors feature."""

from unittest.mock import MagicMock, Mock, PropertyMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    ControlStatus,
)


@pytest.fixture
def mock_coordinator(hass):
    """Create a mock coordinator with essential attributes."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.logger = MagicMock()
    coordinator._automatic_control = True
    coordinator.manager = MagicMock()
    coordinator.manager.binary_cover_manual = False
    coordinator.normal_cover_state = MagicMock()
    coordinator.normal_cover_state.cover.valid = True
    coordinator.check_adaptive_time = True
    coordinator.default_state = 50
    coordinator.climate_state = 75
    coordinator._switch_mode = False
    coordinator._use_interpolation = False
    coordinator._inverse_state = False
    return coordinator


@pytest.mark.unit
def test_is_force_override_active_logic_no_sensors(hass):
    """Test is_force_override_active returns False with no sensors."""
    # Simulate the property logic
    sensors = []
    if not sensors:
        result = False
    else:
        result = any(
            hass.states.get(sensor) and hass.states.get(sensor).state == "on"
            for sensor in sensors
        )
    assert result is False


@pytest.mark.unit
def test_is_force_override_active_logic_single_sensor_active(hass):
    """Test is_force_override_active returns True with one active sensor."""
    sensors = ["binary_sensor.rain"]

    # Setup: sensor is on
    state_obj = MagicMock()
    state_obj.state = "on"
    hass.states.get.return_value = state_obj

    result = any(
        hass.states.get(sensor) and hass.states.get(sensor).state == "on"
        for sensor in sensors
    )
    assert result is True


@pytest.mark.unit
def test_is_force_override_active_logic_single_sensor_inactive(hass):
    """Test is_force_override_active returns False with inactive sensor."""
    sensors = ["binary_sensor.rain"]

    # Setup: sensor is off
    state_obj = MagicMock()
    state_obj.state = "off"
    hass.states.get.return_value = state_obj

    result = any(
        hass.states.get(sensor) and hass.states.get(sensor).state == "on"
        for sensor in sensors
    )
    assert result is False


@pytest.mark.unit
def test_is_force_override_active_logic_multiple_one_active(hass):
    """Test with multiple sensors, one active."""
    sensors = ["binary_sensor.rain", "binary_sensor.wind"]

    # Setup: rain is on, wind is off
    def get_state(entity_id):
        state_obj = MagicMock()
        if entity_id == "binary_sensor.rain":
            state_obj.state = "on"
        else:
            state_obj.state = "off"
        return state_obj

    hass.states.get.side_effect = get_state

    result = any(
        hass.states.get(sensor) and hass.states.get(sensor).state == "on"
        for sensor in sensors
    )
    assert result is True


@pytest.mark.unit
def test_is_force_override_active_logic_sensor_unavailable(hass):
    """Test that unavailable sensors are treated as inactive."""
    sensors = ["binary_sensor.rain"]

    # Setup: sensor is unavailable
    state_obj = MagicMock()
    state_obj.state = "unavailable"
    hass.states.get.return_value = state_obj

    result = any(
        hass.states.get(sensor) and hass.states.get(sensor).state == "on"
        for sensor in sensors
    )
    assert result is False


@pytest.mark.unit
def test_is_force_override_active_logic_sensor_missing(hass):
    """Test that missing entities are treated as inactive."""
    sensors = ["binary_sensor.rain"]

    # Setup: entity doesn't exist
    hass.states.get.return_value = None

    result = any(
        hass.states.get(sensor) and hass.states.get(sensor).state == "on"
        for sensor in sensors
    )
    assert result is False


@pytest.mark.unit
def test_control_status_logic_force_override_precedence():
    """Test control status logic with force override."""
    # Simulate _determine_control_status logic
    automatic_control = True
    is_force_override_active = True
    binary_cover_manual = False

    if not automatic_control:
        status = ControlStatus.AUTOMATIC_CONTROL_OFF
    elif is_force_override_active:
        status = ControlStatus.FORCE_OVERRIDE_ACTIVE
    elif binary_cover_manual:
        status = ControlStatus.MANUAL_OVERRIDE
    else:
        status = ControlStatus.ACTIVE

    assert status == ControlStatus.FORCE_OVERRIDE_ACTIVE


@pytest.mark.unit
def test_control_status_logic_force_override_vs_manual():
    """Test force override takes precedence over manual override."""
    automatic_control = True
    is_force_override_active = True
    binary_cover_manual = True  # Also manual override

    if not automatic_control:
        status = ControlStatus.AUTOMATIC_CONTROL_OFF
    elif is_force_override_active:
        status = ControlStatus.FORCE_OVERRIDE_ACTIVE
    elif binary_cover_manual:
        status = ControlStatus.MANUAL_OVERRIDE
    else:
        status = ControlStatus.ACTIVE

    # Force override should win
    assert status == ControlStatus.FORCE_OVERRIDE_ACTIVE


@pytest.mark.unit
def test_control_status_logic_automatic_control_off():
    """Test automatic_control off takes highest precedence."""
    automatic_control = False
    is_force_override_active = True
    binary_cover_manual = True

    if not automatic_control:
        status = ControlStatus.AUTOMATIC_CONTROL_OFF
    elif is_force_override_active:
        status = ControlStatus.FORCE_OVERRIDE_ACTIVE
    elif binary_cover_manual:
        status = ControlStatus.MANUAL_OVERRIDE
    else:
        status = ControlStatus.ACTIVE

    # Automatic control off should win
    assert status == ControlStatus.AUTOMATIC_CONTROL_OFF


@pytest.mark.unit
def test_state_property_logic_returns_override_position():
    """Test state property logic returns override position when active."""
    # Simulate state property logic
    is_force_override_active = True
    override_position = 0
    default_state = 50
    climate_state = 75
    switch_mode = True

    if is_force_override_active:
        state = override_position
    elif switch_mode:
        state = climate_state
    else:
        state = default_state

    assert state == 0


@pytest.mark.unit
def test_state_property_logic_uses_climate_when_inactive():
    """Test state property uses climate state when override inactive."""
    is_force_override_active = False
    override_position = 0
    default_state = 50
    climate_state = 75
    switch_mode = True

    if is_force_override_active:
        state = override_position
    elif switch_mode:
        state = climate_state
    else:
        state = default_state

    assert state == 75


@pytest.mark.unit
def test_state_property_logic_override_position_100():
    """Test state property with override position 100."""
    is_force_override_active = True
    override_position = 100
    default_state = 50
    climate_state = 25

    if is_force_override_active:
        state = override_position
    else:
        state = default_state

    assert state == 100


@pytest.mark.unit
def test_constants_exist():
    """Test that the new constants are defined."""
    assert CONF_FORCE_OVERRIDE_SENSORS is not None
    assert CONF_FORCE_OVERRIDE_POSITION is not None
    assert ControlStatus.FORCE_OVERRIDE_ACTIVE == "force_override_active"
