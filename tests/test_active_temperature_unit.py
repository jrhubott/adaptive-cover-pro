"""Regression test for issue #48: climate status sensor must expose temperature with correct unit."""

from unittest.mock import MagicMock

import pytest
from homeassistant.const import UnitOfTemperature

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE
from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverClimateStatusSensor,
)


@pytest.fixture
def mock_config_entry():
    """Return a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test Cover", CONF_SENSOR_TYPE: "cover_blind"}
    entry.options = {}
    return entry


@pytest.fixture
def mock_coordinator(hass):
    """Return a mock coordinator."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator


@pytest.mark.unit
def test_climate_status_sensor_exposes_temperature_unit(
    hass, mock_config_entry, mock_coordinator
):
    """Climate status sensor must expose temperature with the correct HA unit in attributes.

    Regression test for GitHub issue #48.
    """
    sensor = AdaptiveCoverClimateStatusSensor(
        mock_config_entry.entry_id,
        hass,
        mock_config_entry,
        "Test Cover",
        mock_coordinator,
        hass,
    )

    assert sensor._temp_unit is not None
    assert sensor._temp_unit in (
        UnitOfTemperature.CELSIUS,
        UnitOfTemperature.FAHRENHEIT,
        UnitOfTemperature.KELVIN,
    )


@pytest.mark.unit
def test_climate_status_temperature_unit_matches_hass_config(
    hass, mock_config_entry, mock_coordinator
):
    """Climate status sensor temperature_unit attribute must match hass.config.units.temperature_unit."""
    sensor = AdaptiveCoverClimateStatusSensor(
        mock_config_entry.entry_id,
        hass,
        mock_config_entry,
        "Test Cover",
        mock_coordinator,
        hass,
    )

    assert sensor._temp_unit == hass.config.units.temperature_unit


@pytest.mark.unit
def test_climate_status_attributes_include_temperature(
    hass, mock_config_entry, mock_coordinator
):
    """Climate status sensor must include active_temperature and temperature_unit in attributes."""
    sensor = AdaptiveCoverClimateStatusSensor(
        mock_config_entry.entry_id,
        hass,
        mock_config_entry,
        "Test Cover",
        mock_coordinator,
        hass,
    )

    mock_coordinator.data = MagicMock()
    mock_coordinator.data.diagnostics = {
        "active_temperature": 22.5,
        "temperature_details": {
            "inside_temperature": 22.5,
            "outside_temperature": 30.0,
            "temp_switch": True,
        },
        "climate_conditions": {
            "is_summer": True,
            "is_winter": False,
            "is_presence": True,
            "is_sunny": True,
            "lux_below_threshold": False,
            "irradiance_below_threshold": False,
        },
    }
    sensor.coordinator = mock_coordinator

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "active_temperature" in attrs
    assert attrs["active_temperature"] == 22.5
    assert "temperature_unit" in attrs
    assert attrs["temperature_unit"] == hass.config.units.temperature_unit
