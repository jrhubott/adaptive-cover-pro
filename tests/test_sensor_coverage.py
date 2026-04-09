"""Unit tests for sensor.py uncovered branches."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, SensorType
from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverClimateStatusSensor,
    AdaptiveCoverLastActionSensor,
    AdaptiveCoverSunPositionSensor,
)


def _make_config_entry(sensor_type=SensorType.BLIND):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: sensor_type}
    entry.options = {}
    return entry


def _make_coordinator(diagnostics: dict | None = None):
    coord = MagicMock()
    coord.logger = MagicMock()
    data = MagicMock()
    data.diagnostics = diagnostics
    data.states = {}
    coord.data = data
    return coord


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


# ---------------------------------------------------------------------------
# AdaptiveCoverClimateStatusSensor.native_value
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_climate_status_native_value_summer_mode():
    """Returns 'Summer Mode' when is_summer is True."""
    coord = _make_coordinator(diagnostics={
        "climate_conditions": {"is_summer": True, "is_winter": False}
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "Summer Mode"


@pytest.mark.unit
def test_climate_status_native_value_winter_mode():
    """Returns 'Winter Mode' when is_winter is True."""
    coord = _make_coordinator(diagnostics={
        "climate_conditions": {"is_summer": False, "is_winter": True}
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "Winter Mode"


@pytest.mark.unit
def test_climate_status_native_value_intermediate():
    """Returns 'Intermediate' when neither summer nor winter."""
    coord = _make_coordinator(diagnostics={
        "climate_conditions": {"is_summer": False, "is_winter": False}
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "Intermediate"


@pytest.mark.unit
def test_climate_status_native_value_none_when_no_diagnostics():
    """Returns None when diagnostics is None."""
    coord = _make_coordinator(diagnostics=None)
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value is None


@pytest.mark.unit
def test_climate_status_native_value_none_when_no_climate_conditions():
    """Returns None when climate_conditions key is absent."""
    coord = _make_coordinator(diagnostics={"other_key": "value"})
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# AdaptiveCoverSunPositionSensor.extra_state_attributes — elevation limits
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sun_position_attributes_include_min_max_elevation():
    """extra_state_attributes includes min/max elevation when configured."""
    coord = _make_coordinator(diagnostics={
        "sun_azimuth": 180.0,
        "sun_elevation": 45.0,
        "gamma": 0.0,
        "configuration": {
            "min_elevation": 10.0,
            "max_elevation": 80.0,
            "azimuth": 180,
            "fov_left": 45,
            "fov_right": 45,
        },
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs.get("min_elevation") == 10.0
    assert attrs.get("max_elevation") == 80.0


@pytest.mark.unit
def test_sun_position_attributes_no_min_max_when_not_configured():
    """extra_state_attributes omits min/max elevation when not in config."""
    coord = _make_coordinator(diagnostics={
        "sun_azimuth": 180.0,
        "sun_elevation": 45.0,
        "gamma": None,
        "configuration": {
            "azimuth": 180,
            "fov_left": 45,
            "fov_right": 45,
        },
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "min_elevation" not in attrs
    assert "max_elevation" not in attrs


@pytest.mark.unit
def test_sun_position_attributes_blind_spot_range_calculated():
    """extra_state_attributes includes blind_spot_range when blind spot is enabled."""
    coord = _make_coordinator(diagnostics={
        "sun_azimuth": 180.0,
        "sun_elevation": 45.0,
        "gamma": None,
        "configuration": {
            "azimuth": 180,
            "fov_left": 45,
            "fov_right": 45,
            "enable_blind_spot": True,
            "blind_spot_left": 10.0,
            "blind_spot_right": 5.0,
        },
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "blind_spot_range" in attrs
    # left_edge = fov_left - blind_spot_left = 45 - 10 = 35
    # right_edge = fov_left - blind_spot_right = 45 - 5 = 40
    assert attrs["blind_spot_range"] == [40.0, 35.0]


# ---------------------------------------------------------------------------
# AdaptiveCoverLastActionSensor
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_last_action_sensor_native_value_with_timestamp():
    """native_value formats timestamp correctly when action has a valid timestamp."""
    ts = "2024-06-21T14:30:00+00:00"
    coord = _make_coordinator(diagnostics={
        "last_cover_action": {
            "entity_id": "cover.test_blind",
            "service": "set_cover_position",
            "position": 50,
            "calculated_position": 50,
            "timestamp": ts,
        }
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    val = sensor.native_value
    assert val is not None
    assert "test_blind" in val
    assert "set_cover_position" in val
    assert "14:30:00" in val


@pytest.mark.unit
def test_last_action_sensor_native_value_without_timestamp():
    """native_value works when timestamp is absent."""
    coord = _make_coordinator(diagnostics={
        "last_cover_action": {
            "entity_id": "cover.test_blind",
            "service": "set_cover_position",
            "position": 50,
            "calculated_position": 50,
            "timestamp": "",  # empty timestamp
        }
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    val = sensor.native_value
    assert val == "set_cover_position → test_blind"


@pytest.mark.unit
def test_last_action_sensor_extra_state_attributes():
    """extra_state_attributes returns full action dict."""
    coord = _make_coordinator(diagnostics={
        "last_cover_action": {
            "entity_id": "cover.test_blind",
            "service": "set_cover_position",
            "position": 50,
            "calculated_position": 50,
            "inverse_state_applied": False,
            "timestamp": "2024-06-21T14:30:00+00:00",
            "covers_controlled": 2,
            "threshold_used": 50,
        }
    })
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["entity_id"] == "cover.test_blind"
    assert attrs["service"] == "set_cover_position"
    assert attrs["position"] == 50
    assert attrs["covers_controlled"] == 2
    assert "threshold_used" in attrs
    assert "threshold_comparison" in attrs


@pytest.mark.unit
def test_last_action_sensor_extra_state_attributes_no_action():
    """extra_state_attributes returns None when no action recorded."""
    coord = _make_coordinator(diagnostics={"last_cover_action": {}})
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    assert sensor.extra_state_attributes is None
