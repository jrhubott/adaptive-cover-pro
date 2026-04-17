"""Tests for ClimateProvider — reads HA state into ClimateReadings."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateProvider,
    ClimateReadings,
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
    """Create a mock state object."""
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attributes or {}
    return s


# ---------------------------------------------------------------------------
# Outside temperature
# ---------------------------------------------------------------------------


class TestOutsideTemperature:
    """Test outside temperature reading."""

    @pytest.mark.unit
    def test_from_outside_entity(self, provider, mock_hass):
        """Read from outside_entity."""
        mock_hass.states.get.return_value = _mock_state("sensor.outside", "22.5")
        readings = provider.read(outside_entity="sensor.outside")
        assert readings.outside_temperature == "22.5"

    @pytest.mark.unit
    def test_fallback_to_weather(self, provider, mock_hass):
        """Fall back to weather entity temperature attribute."""
        with patch(
            "custom_components.adaptive_cover_pro.state.climate_provider.state_attr",
            return_value=20.0,
        ):
            readings = provider.read(weather_entity="weather.home")
        assert readings.outside_temperature == 20.0

    @pytest.mark.unit
    def test_none_when_no_entity(self, provider):
        """Return None when neither entity is configured."""
        readings = provider.read()
        assert readings.outside_temperature is None

    @pytest.mark.unit
    def test_outside_entity_unavailable(self, provider, mock_hass):
        """Return None when outside entity is unavailable."""
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        mock_hass.states.get.return_value = unavailable
        readings = provider.read(outside_entity="sensor.outside")
        # get_safe_state returns None for unavailable
        assert readings.outside_temperature is None


# ---------------------------------------------------------------------------
# Inside temperature
# ---------------------------------------------------------------------------


class TestInsideTemperature:
    """Test inside temperature reading."""

    @pytest.mark.unit
    def test_from_sensor(self, provider, mock_hass):
        """Read from sensor entity."""
        mock_hass.states.get.return_value = _mock_state("sensor.temp", "23.0")
        readings = provider.read(temp_entity="sensor.temp")
        assert readings.inside_temperature == "23.0"

    @pytest.mark.unit
    def test_from_climate_entity(self, provider):
        """Read current_temperature attribute from climate entity."""
        with patch(
            "custom_components.adaptive_cover_pro.state.climate_provider.state_attr",
            return_value=21.5,
        ):
            readings = provider.read(temp_entity="climate.living_room")
        assert readings.inside_temperature == 21.5

    @pytest.mark.unit
    def test_none_when_no_entity(self, provider):
        """Return None when no temp entity configured."""
        readings = provider.read()
        assert readings.inside_temperature is None


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


class TestPresence:
    """Test presence reading."""

    @pytest.mark.unit
    def test_device_tracker_home(self, provider, mock_hass):
        """device_tracker 'home' → True."""
        mock_hass.states.get.return_value = _mock_state("device_tracker.phone", "home")
        readings = provider.read(presence_entity="device_tracker.phone")
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_device_tracker_away(self, provider, mock_hass):
        """device_tracker 'not_home' → False."""
        mock_hass.states.get.return_value = _mock_state(
            "device_tracker.phone", "not_home"
        )
        readings = provider.read(presence_entity="device_tracker.phone")
        assert readings.is_presence is False

    @pytest.mark.unit
    def test_zone_occupied(self, provider, mock_hass):
        """Zone count > 0 → True."""
        mock_hass.states.get.return_value = _mock_state("zone.home", "2")
        readings = provider.read(presence_entity="zone.home")
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_zone_empty(self, provider, mock_hass):
        """Zone count 0 → False."""
        mock_hass.states.get.return_value = _mock_state("zone.home", "0")
        readings = provider.read(presence_entity="zone.home")
        assert readings.is_presence is False

    @pytest.mark.unit
    def test_binary_sensor_on(self, provider, mock_hass):
        """binary_sensor 'on' → True."""
        mock_hass.states.get.return_value = _mock_state("binary_sensor.presence", "on")
        readings = provider.read(presence_entity="binary_sensor.presence")
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_binary_sensor_off(self, provider, mock_hass):
        """binary_sensor 'off' → False."""
        mock_hass.states.get.return_value = _mock_state("binary_sensor.presence", "off")
        readings = provider.read(presence_entity="binary_sensor.presence")
        assert readings.is_presence is False

    @pytest.mark.unit
    def test_input_boolean_on(self, provider, mock_hass):
        """input_boolean 'on' → True."""
        mock_hass.states.get.return_value = _mock_state("input_boolean.presence", "on")
        readings = provider.read(presence_entity="input_boolean.presence")
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_no_entity_defaults_to_true(self, provider):
        """No presence entity → True (assume present)."""
        readings = provider.read()
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_unavailable_sensor_defaults_to_true(self, provider, mock_hass):
        """Unavailable presence sensor → True (assume present)."""
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        mock_hass.states.get.return_value = unavailable
        readings = provider.read(presence_entity="binary_sensor.presence")
        assert readings.is_presence is True


# ---------------------------------------------------------------------------
# Weather / Sunny
# ---------------------------------------------------------------------------


class TestSunny:
    """Test sunny weather reading."""

    @pytest.mark.unit
    def test_sunny_match(self, provider, mock_hass):
        """Weather matches condition → True."""
        mock_hass.states.get.return_value = _mock_state("weather.home", "sunny")
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
        )
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_not_sunny(self, provider, mock_hass):
        """Weather doesn't match condition → False."""
        mock_hass.states.get.return_value = _mock_state("weather.home", "rainy")
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
        )
        assert readings.is_sunny is False

    @pytest.mark.unit
    def test_no_weather_entity(self, provider):
        """No weather entity → True (default)."""
        readings = provider.read()
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_no_weather_condition(self, provider, mock_hass):
        """Weather entity but no condition list → True."""
        mock_hass.states.get.return_value = _mock_state("weather.home", "rainy")
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=None,
        )
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_unavailable_weather_entity_returns_true(self, provider, mock_hass):
        """Unavailable weather entity → True (assume sunny, don't suppress)."""
        mock_hass.states.get.return_value = _mock_state("weather.home", "unavailable")
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
        )
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_missing_weather_entity_returns_true(self, provider, mock_hass):
        """Missing weather entity (states.get returns None) → True."""
        mock_hass.states.get.return_value = None
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
        )
        assert readings.is_sunny is True


# ---------------------------------------------------------------------------
# Lux
# ---------------------------------------------------------------------------


class TestLux:
    """Test lux threshold reading."""

    @pytest.mark.unit
    def test_below_threshold(self, provider, mock_hass):
        """Lux below threshold → True."""
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "4000")
        readings = provider.read(
            use_lux=True, lux_entity="sensor.lux", lux_threshold=5000
        )
        assert readings.lux_below_threshold is True

    @pytest.mark.unit
    def test_above_threshold(self, provider, mock_hass):
        """Lux above threshold → False."""
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "6000")
        readings = provider.read(
            use_lux=True, lux_entity="sensor.lux", lux_threshold=5000
        )
        assert readings.lux_below_threshold is False

    @pytest.mark.unit
    def test_disabled(self, provider):
        """Lux disabled → False."""
        readings = provider.read(use_lux=False)
        assert readings.lux_below_threshold is False

    @pytest.mark.unit
    def test_unavailable_sensor(self, provider, mock_hass):
        """Unavailable lux sensor → False."""
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        mock_hass.states.get.return_value = unavailable
        readings = provider.read(
            use_lux=True, lux_entity="sensor.lux", lux_threshold=5000
        )
        assert readings.lux_below_threshold is False


# ---------------------------------------------------------------------------
# Irradiance
# ---------------------------------------------------------------------------


class TestIrradiance:
    """Test irradiance threshold reading."""

    @pytest.mark.unit
    def test_below_threshold(self, provider, mock_hass):
        """Irradiance below threshold → True."""
        mock_hass.states.get.return_value = _mock_state("sensor.solar", "250")
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        assert readings.irradiance_below_threshold is True

    @pytest.mark.unit
    def test_above_threshold(self, provider, mock_hass):
        """Irradiance above threshold → False."""
        mock_hass.states.get.return_value = _mock_state("sensor.solar", "400")
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        assert readings.irradiance_below_threshold is False

    @pytest.mark.unit
    def test_disabled(self, provider):
        """Irradiance disabled → False."""
        readings = provider.read(use_irradiance=False)
        assert readings.irradiance_below_threshold is False

    @pytest.mark.unit
    def test_unavailable_sensor(self, provider, mock_hass):
        """Unavailable irradiance sensor → False."""
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        mock_hass.states.get.return_value = unavailable
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        assert readings.irradiance_below_threshold is False


# ---------------------------------------------------------------------------
# ClimateReadings frozen
# ---------------------------------------------------------------------------


class TestClimateReadings:
    """Test ClimateReadings dataclass."""

    @pytest.mark.unit
    def test_frozen(self):
        """ClimateReadings should be frozen (immutable)."""
        readings = ClimateReadings(
            outside_temperature=22.0,
            inside_temperature=21.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        with pytest.raises(AttributeError):
            readings.outside_temperature = 99.0


# ---------------------------------------------------------------------------
# Cloud coverage
# ---------------------------------------------------------------------------


class TestCloudCoverage:
    """Tests for _read_cloud_coverage()."""

    @pytest.mark.unit
    def test_above_threshold(self, provider, mock_hass):
        """Cloud coverage at or above threshold → True (overcast)."""
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "80")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
        )
        assert readings.cloud_coverage_above_threshold is True

    @pytest.mark.unit
    def test_at_threshold(self, provider, mock_hass):
        """Cloud coverage exactly at threshold → True."""
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "75")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
        )
        assert readings.cloud_coverage_above_threshold is True

    @pytest.mark.unit
    def test_below_threshold(self, provider, mock_hass):
        """Cloud coverage below threshold → False (clear sky)."""
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "40")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
        )
        assert readings.cloud_coverage_above_threshold is False

    @pytest.mark.unit
    def test_disabled(self, provider):
        """Feature disabled → False regardless of sensor."""
        readings = provider.read(use_cloud_coverage=False)
        assert readings.cloud_coverage_above_threshold is False

    @pytest.mark.unit
    def test_no_entity(self, provider):
        """No entity configured → False."""
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity=None,
            cloud_coverage_threshold=75,
        )
        assert readings.cloud_coverage_above_threshold is False

    @pytest.mark.unit
    def test_no_threshold(self, provider, mock_hass):
        """No threshold configured → False."""
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "90")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=None,
        )
        assert readings.cloud_coverage_above_threshold is False

    @pytest.mark.unit
    def test_unavailable_sensor(self, provider, mock_hass):
        """Unavailable sensor → False."""
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        mock_hass.states.get.return_value = unavailable
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
        )
        assert readings.cloud_coverage_above_threshold is False
