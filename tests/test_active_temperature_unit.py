"""Regression test for issue #48: active_temperature sensor must have a valid unit."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE
from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverAdvancedDiagnosticSensor,
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
def test_active_temperature_sensor_has_valid_unit(
    hass, mock_config_entry, mock_coordinator
):
    """active_temperature sensor must not have None as unit when device_class is TEMPERATURE.

    Regression test for GitHub issue #48.
    """
    sensor = AdaptiveCoverAdvancedDiagnosticSensor(
        mock_config_entry.entry_id,
        hass,
        mock_config_entry,
        "Test Cover",
        mock_coordinator,
        "Active Temperature",
        "active_temperature",
        hass.config.units.temperature_unit,
        "mdi:thermometer",
        SensorStateClass.MEASUREMENT,
        SensorDeviceClass.TEMPERATURE,
    )

    assert sensor._attr_native_unit_of_measurement is not None
    assert sensor._attr_native_unit_of_measurement in (
        UnitOfTemperature.CELSIUS,
        UnitOfTemperature.FAHRENHEIT,
        UnitOfTemperature.KELVIN,
    )
    assert sensor._attr_device_class == SensorDeviceClass.TEMPERATURE


@pytest.mark.unit
def test_active_temperature_unit_matches_hass_config(
    hass, mock_config_entry, mock_coordinator
):
    """active_temperature sensor unit must match hass.config.units.temperature_unit."""
    sensor = AdaptiveCoverAdvancedDiagnosticSensor(
        mock_config_entry.entry_id,
        hass,
        mock_config_entry,
        "Test Cover",
        mock_coordinator,
        "Active Temperature",
        "active_temperature",
        hass.config.units.temperature_unit,
        "mdi:thermometer",
        SensorStateClass.MEASUREMENT,
        SensorDeviceClass.TEMPERATURE,
    )

    assert sensor._attr_native_unit_of_measurement == hass.config.units.temperature_unit
