"""Tests for SunProvider -- bridges Home Assistant to pure SunData."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.sun_provider import SunProvider
from custom_components.adaptive_cover_pro.sun import SunData


@pytest.fixture
def mock_location():
    """Return a mock astral Location."""
    return MagicMock()


@pytest.fixture
def mock_elevation():
    """Return a mock elevation value."""
    return 100.0


class TestSunProvider:
    """Tests for SunProvider initialization and SunData creation."""

    @patch(
        "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
    )
    def test_init_extracts_location(
        self, mock_get_astral, mock_location, mock_elevation
    ):
        """SunProvider should extract location and elevation from hass."""
        mock_get_astral.return_value = (mock_location, mock_elevation)
        hass = MagicMock()

        provider = SunProvider(hass)

        mock_get_astral.assert_called_once_with(hass)
        assert provider._location is mock_location
        assert provider._elevation == mock_elevation

    @patch(
        "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
    )
    def test_create_sun_data_returns_sun_data(
        self, mock_get_astral, mock_location, mock_elevation
    ):
        """create_sun_data should return a SunData instance."""
        mock_get_astral.return_value = (mock_location, mock_elevation)
        hass = MagicMock()

        provider = SunProvider(hass)
        sun_data = provider.create_sun_data("UTC")

        assert isinstance(sun_data, SunData)
        assert sun_data.timezone == "UTC"
        assert sun_data.location is mock_location
        assert sun_data.elevation == mock_elevation

    @patch(
        "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
    )
    def test_create_sun_data_different_timezones(
        self, mock_get_astral, mock_location, mock_elevation
    ):
        """create_sun_data should pass timezone through to SunData."""
        mock_get_astral.return_value = (mock_location, mock_elevation)
        hass = MagicMock()

        provider = SunProvider(hass)

        utc_data = provider.create_sun_data("UTC")
        berlin_data = provider.create_sun_data("Europe/Berlin")

        assert utc_data.timezone == "UTC"
        assert berlin_data.timezone == "Europe/Berlin"
        # Both share the same location
        assert utc_data.location is berlin_data.location

    @patch(
        "custom_components.adaptive_cover_pro.state.sun_provider.get_astral_location"
    )
    def test_create_multiple_sun_data_instances(
        self, mock_get_astral, mock_location, mock_elevation
    ):
        """Multiple calls to create_sun_data should create separate instances."""
        mock_get_astral.return_value = (mock_location, mock_elevation)
        hass = MagicMock()

        provider = SunProvider(hass)
        data1 = provider.create_sun_data("UTC")
        data2 = provider.create_sun_data("UTC")

        assert data1 is not data2
        # get_astral_location called only once (during init)
        mock_get_astral.assert_called_once()
