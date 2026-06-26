"""Unit tests for _weather_override_placeholders helper (COMMIT 6 — issue #693).

The helper builds the description_placeholders dict for the weather_override
config/options step: learn_more URL plus live unit hints read from the
configured wind-speed and rain sensors, falling back to HA's locale units.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro.config_flow import (
    _weather_override_placeholders,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_WIND_SPEED_SENSOR,
)


def _make_hass(
    *,
    imperial: bool = False,
    wind_unit: str | None = None,
    rain_unit: str | None = None,
):
    """Return a MagicMock hass that mimics the attributes used by _weather_override_placeholders.

    *wind_unit* / *rain_unit*: unit_of_measurement reported by sensor.wind / sensor.rain.
    Pass None to simulate a missing / unavailable sensor state.
    """
    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if imperial else METRIC_SYSTEM

    def _states_get(entity_id: str):
        if entity_id == "sensor.wind" and wind_unit is not None:
            state = MagicMock()
            state.attributes = {"unit_of_measurement": wind_unit}
            return state
        if entity_id == "sensor.rain" and rain_unit is not None:
            state = MagicMock()
            state.attributes = {"unit_of_measurement": rain_unit}
            return state
        return None

    hass.states.get.side_effect = _states_get
    return hass


pytestmark = pytest.mark.unit


class TestWeatherOverridePlaceholders:
    """_weather_override_placeholders always returns the expected keys."""

    def test_all_keys_present(self):
        """Returned dict must have learn_more, wind_unit, rain_unit."""
        hass = _make_hass()
        result = _weather_override_placeholders(hass, {})
        assert set(result) >= {"learn_more", "wind_unit", "rain_unit"}

    def test_learn_more_is_nonempty_url(self):
        """learn_more must be a non-empty string (the wiki URL)."""
        hass = _make_hass()
        result = _weather_override_placeholders(hass, {})
        assert result["learn_more"]
        assert "github.com" in result["learn_more"]

    def test_placeholders_use_sensor_unit_wind(self):
        """wind_unit is taken from the configured sensor's unit_of_measurement."""
        hass = _make_hass(wind_unit="mph")
        options = {CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind"}
        result = _weather_override_placeholders(hass, options)
        assert result["wind_unit"] == "mph"

    def test_placeholders_use_sensor_unit_rain(self):
        """rain_unit is taken from the configured sensor's unit_of_measurement."""
        hass = _make_hass(rain_unit="in/h")
        options = {CONF_WEATHER_RAIN_SENSOR: "sensor.rain"}
        result = _weather_override_placeholders(hass, options)
        assert result["rain_unit"] == "in/h"

    def test_placeholders_fall_back_to_locale_unit_wind_metric(self):
        """When no wind sensor is set, wind_unit falls back to HA's metric unit."""
        hass = _make_hass(imperial=False)
        result = _weather_override_placeholders(hass, {})
        expected = str(METRIC_SYSTEM.wind_speed_unit)
        assert result["wind_unit"] == expected
        assert result["wind_unit"]  # non-empty

    def test_placeholders_fall_back_to_locale_unit_rain_metric(self):
        """When no rain sensor is set, rain_unit falls back to HA's metric unit."""
        hass = _make_hass(imperial=False)
        result = _weather_override_placeholders(hass, {})
        expected = str(METRIC_SYSTEM.accumulated_precipitation_unit)
        assert result["rain_unit"] == expected
        assert result["rain_unit"]  # non-empty

    def test_placeholders_fall_back_to_locale_unit_wind_imperial(self):
        """When no wind sensor is set in imperial mode, wind_unit is the US customary unit."""
        hass = _make_hass(imperial=True)
        result = _weather_override_placeholders(hass, {})
        expected = str(US_CUSTOMARY_SYSTEM.wind_speed_unit)
        assert result["wind_unit"] == expected

    def test_placeholders_fall_back_to_locale_unit_rain_imperial(self):
        """When no rain sensor is set in imperial mode, rain_unit is the US customary unit."""
        hass = _make_hass(imperial=True)
        result = _weather_override_placeholders(hass, {})
        expected = str(US_CUSTOMARY_SYSTEM.accumulated_precipitation_unit)
        assert result["rain_unit"] == expected

    def test_sensor_unit_overrides_locale_unit(self):
        """Sensor unit wins over locale unit even when they differ."""
        # Use metric hass but sensor reports mph (edge case)
        hass = _make_hass(imperial=False, wind_unit="mph")
        options = {CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind"}
        result = _weather_override_placeholders(hass, options)
        assert result["wind_unit"] == "mph"
        # locale would be m/s but sensor wins
        assert result["wind_unit"] != str(METRIC_SYSTEM.wind_speed_unit)

    def test_none_options_does_not_crash(self):
        """Passing None as options returns the locale fallback (graceful handling)."""
        hass = _make_hass()
        # Should not raise — must handle None safely
        result = _weather_override_placeholders(hass, None)
        assert "wind_unit" in result
        assert "rain_unit" in result

    def test_hass_none_returns_empty_fallback(self):
        """When hass is None (schema constant path), units fall back to empty string."""
        result = _weather_override_placeholders(None, {})
        assert "wind_unit" in result
        assert "rain_unit" in result
        # Both should be strings (not crash)
        assert isinstance(result["wind_unit"], str)
        assert isinstance(result["rain_unit"], str)
