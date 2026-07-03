"""Tests for the SunProvider state provider."""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.state import sun_provider
from custom_components.adaptive_cover_pro.state.sun_provider import SunProvider
from custom_components.adaptive_cover_pro.sun import SunData


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance with real config values.

    ``sun_provider.create_sun_data`` builds an astral ``Location`` directly
    from ``hass.config`` (see #815), so latitude/longitude/elevation/time_zone
    must be real values — an astral ``LocationInfo``/``Location`` raises on
    plain ``MagicMock`` attributes.
    """
    hass = MagicMock()
    hass.config.latitude = 52.0
    hass.config.longitude = 13.0
    hass.config.elevation = 100.0
    hass.config.time_zone = "Europe/Berlin"
    return hass


class TestSunProvider:
    """Tests for SunProvider."""

    def test_create_sun_data_returns_sun_data(self, mock_hass):
        """SunProvider.create_sun_data returns a SunData instance built from hass.config."""
        provider = SunProvider(hass=mock_hass)
        result = provider.create_sun_data("Europe/Berlin")

        assert isinstance(result, SunData)
        assert result.timezone == "Europe/Berlin"
        assert result.location.latitude == 52.0
        assert result.location.longitude == 13.0
        assert result.elevation == 100.0

    def test_create_sun_data_passes_hass_config_to_location(self, mock_hass):
        """SunProvider reads latitude/longitude/elevation straight from hass.config."""
        mock_hass.config.latitude = 40.7
        mock_hass.config.longitude = -74.0
        mock_hass.config.elevation = 10.0

        provider = SunProvider(hass=mock_hass)
        result = provider.create_sun_data("America/New_York")

        assert result.location.latitude == 40.7
        assert result.location.longitude == -74.0
        assert result.elevation == 10.0

    def test_create_sun_data_different_timezones(self, mock_hass):
        """SunProvider can create SunData with different timezones."""
        provider = SunProvider(hass=mock_hass)

        utc_data = provider.create_sun_data("UTC")
        assert utc_data.timezone == "UTC"

        berlin_data = provider.create_sun_data("Europe/Berlin")
        assert berlin_data.timezone == "Europe/Berlin"

    def test_sun_data_no_hass_dependency(self):
        """SunData itself has no HomeAssistant dependency."""
        mock_location = MagicMock()
        sun_data = SunData("UTC", mock_location, 42.0)

        assert sun_data.timezone == "UTC"
        assert sun_data.location is mock_location
        assert sun_data.elevation == 42.0

    def test_create_sun_data_does_not_call_get_astral_location(self, mock_hass):
        """SunProvider must not use HA's deprecated get_astral_location (#815).

        The old implementation imported ``get_astral_location`` via
        ``from homeassistant.helpers.sun import get_astral_location``. That
        import (and the deprecated call) must be gone entirely, not merely
        unused.
        """
        assert not hasattr(sun_provider, "get_astral_location")

        provider = SunProvider(hass=mock_hass)
        provider.create_sun_data("Europe/Berlin")

        # Still doesn't exist post-call — nothing lazily re-imports it.
        assert not hasattr(sun_provider, "get_astral_location")

    def test_create_sun_data_builds_location_from_hass_config(self, mock_hass):
        """SunProvider builds the astral Location directly from hass.config (#815)."""
        provider = SunProvider(hass=mock_hass)
        result = provider.create_sun_data("Europe/Berlin")

        assert isinstance(result, SunData)
        assert result.location.latitude == 52.0
        assert result.location.longitude == 13.0
        assert result.elevation == 100.0
