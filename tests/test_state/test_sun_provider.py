"""Tests for the SunProvider state provider."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.sun_provider import SunProvider
from custom_components.adaptive_cover_pro.sun import SunData


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance."""
    return MagicMock()


class TestSunProvider:
    """Tests for SunProvider."""

    def test_create_sun_data_returns_sun_data(self, mock_hass):
        """SunProvider.create_sun_data returns a SunData instance."""
        with patch(
            "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
        ) as mock_get_loc:
            mock_location = MagicMock()
            mock_get_loc.return_value = (mock_location, 100.0)

            provider = SunProvider(hass=mock_hass)
            result = provider.create_sun_data("Europe/Berlin")

            assert isinstance(result, SunData)
            assert result.timezone == "Europe/Berlin"
            assert result.location is mock_location
            assert result.elevation == 100.0
            mock_get_loc.assert_called_once_with(mock_hass)

    def test_create_sun_data_passes_hass_to_get_astral_location(self, mock_hass):
        """SunProvider passes its mock_hass instance to get_astral_location."""
        with patch(
            "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
        ) as mock_get_loc:
            mock_get_loc.return_value = (MagicMock(), 0.0)

            provider = SunProvider(hass=mock_hass)
            provider.create_sun_data("UTC")

            mock_get_loc.assert_called_once_with(mock_hass)

    def test_create_sun_data_different_timezones(self, mock_hass):
        """SunProvider can create SunData with different timezones."""
        with patch(
            "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
        ) as mock_get_loc:
            mock_get_loc.return_value = (MagicMock(), 50.0)

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
