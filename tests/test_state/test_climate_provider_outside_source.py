"""Tests for the forecast-aware outdoor-temp source switch (issue #547).

The single seam is ``ClimateProvider._read_outside_temperature``. The reader
grows two additive kwargs — ``outside_temp_source`` (``live`` /
``forecast_max`` / ``max_of_live_and_forecast``) and a pre-fetched
``forecast_max_outside`` daily-max — and records provenance on
``ClimateReadings.outside_temperature_source``.
"""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateProvider,
)


@pytest.fixture
def mock_hass():
    """Mock HomeAssistant."""
    h = MagicMock()
    h.states.get.return_value = None
    return h


@pytest.fixture
def provider(mock_hass, mock_logger):
    """ClimateProvider instance."""
    return ClimateProvider(hass=mock_hass, logger=mock_logger)


def _mock_state(entity_id, state, attributes=None):
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attributes or {}
    return s


class TestOutsideTempSource:
    """The unified live/forecast source switch."""

    @pytest.mark.unit
    def test_default_source_is_live(self, provider, mock_hass):
        """No kwargs → today's live behaviour, source label ``live``."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "22.5")
        readings = provider.read(outside_entity="sensor.outside")
        assert readings.outside_temperature == "22.5"
        assert readings.outside_temperature_source == "live"

    @pytest.mark.unit
    def test_explicit_live_source(self, provider, mock_hass):
        """``live`` passed explicitly matches the default."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "18.0")
        readings = provider.read(
            outside_entity="sensor.outside", outside_temp_source="live"
        )
        assert readings.outside_temperature == "18.0"
        assert readings.outside_temperature_source == "live"

    @pytest.mark.unit
    def test_forecast_max_uses_prefetched_value(self, provider, mock_hass):
        """``forecast_max`` uses the pre-fetched daily max when numeric."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "15.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="forecast_max",
            forecast_max_outside=26.0,
        )
        assert readings.outside_temperature == 26.0
        assert readings.outside_temperature_source == "forecast_max"

    @pytest.mark.unit
    def test_forecast_max_falls_back_to_live_when_missing(self, provider, mock_hass):
        """``forecast_max`` with no forecast → live read, label ``live_fallback``."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "15.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="forecast_max",
            forecast_max_outside=None,
        )
        assert readings.outside_temperature == "15.0"
        assert readings.outside_temperature_source == "live_fallback"

    @pytest.mark.unit
    def test_max_of_live_and_forecast_picks_higher(self, provider, mock_hass):
        """Combined mode returns max(live, forecast) when both numeric."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "15.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="max_of_live_and_forecast",
            forecast_max_outside=26.0,
        )
        assert readings.outside_temperature == 26.0
        assert readings.outside_temperature_source == "max_of_live_and_forecast"

    @pytest.mark.unit
    def test_max_of_live_and_forecast_live_higher(self, provider, mock_hass):
        """Combined mode keeps the live value when it is the higher one."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "30.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="max_of_live_and_forecast",
            forecast_max_outside=26.0,
        )
        assert readings.outside_temperature == 30.0
        assert readings.outside_temperature_source == "max_of_live_and_forecast"

    @pytest.mark.unit
    def test_max_of_live_and_forecast_only_forecast(self, provider, mock_hass):
        """Combined mode with a non-numeric live read uses the forecast max."""
        mock_hass.states.get.return_value = None  # live unavailable
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="max_of_live_and_forecast",
            forecast_max_outside=26.0,
        )
        assert readings.outside_temperature == 26.0
        assert readings.outside_temperature_source == "forecast_max"

    @pytest.mark.unit
    def test_max_of_live_and_forecast_only_live(self, provider, mock_hass):
        """Combined mode with no forecast falls back to the live read."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "15.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="max_of_live_and_forecast",
            forecast_max_outside=None,
        )
        assert readings.outside_temperature == "15.0"
        assert readings.outside_temperature_source == "live_fallback"

    @pytest.mark.unit
    def test_unknown_source_falls_back_to_live(self, provider, mock_hass):
        """An unrecognized source label degrades to the live read."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "17.0")
        readings = provider.read(
            outside_entity="sensor.outside",
            outside_temp_source="bogus",
            forecast_max_outside=26.0,
        )
        assert readings.outside_temperature == "17.0"
        assert readings.outside_temperature_source == "live"

    @pytest.mark.unit
    def test_forecast_max_via_weather_entity_live_fallback(self, provider):
        """The live fallback still routes through the weather temp attribute."""
        with patch(
            "custom_components.adaptive_cover_pro.state.climate_provider.state_attr",
            return_value=20.0,
        ):
            readings = provider.read(
                weather_entity="weather.home",
                outside_temp_source="forecast_max",
                forecast_max_outside=None,
            )
        assert readings.outside_temperature == 20.0
        assert readings.outside_temperature_source == "live_fallback"
