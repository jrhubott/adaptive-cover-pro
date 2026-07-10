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
# Resolved temperature source (issue #786)
# ---------------------------------------------------------------------------


def _patch_area(*, device_area_id=None, area_temp_entity=None):
    """Patch the device + area registries the AreaSensorResolver reads."""
    device = MagicMock()
    device.area_id = device_area_id
    device_reg = MagicMock()
    device_reg.async_get.return_value = device if device_area_id is not None else None
    area = MagicMock()
    area.temperature_entity_id = area_temp_entity
    area_reg = MagicMock()
    area_reg.async_get_area.return_value = area if device_area_id is not None else None
    mod = "custom_components.adaptive_cover_pro.state.area_resolver"
    return (
        patch(f"{mod}.dr.async_get", return_value=device_reg),
        patch(f"{mod}.ar.async_get", return_value=area_reg),
    )


class TestResolvedTempSource:
    """ClimateReadings carries the resolved temp entity_id + provenance."""

    @pytest.mark.unit
    def test_explicit_source(self, provider, mock_hass):
        """Explicit temp entity → source 'explicit', entity surfaced."""
        mock_hass.states.get.return_value = _mock_state("sensor.temp", "23.0")
        readings = provider.read(temp_entity="sensor.temp", temp_device_id="dev1")
        assert readings.inside_temperature == "23.0"
        assert readings.inside_temperature_entity_id == "sensor.temp"
        assert readings.inside_temperature_source == "explicit"

    @pytest.mark.unit
    def test_area_resolved_source(self, provider, mock_hass):
        """No explicit entity → area's temp entity resolved and read."""
        mock_hass.states.get.return_value = _mock_state("sensor.bedroom_temp", "19.5")
        dev_patch, area_patch = _patch_area(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        with dev_patch, area_patch:
            readings = provider.read(temp_entity=None, temp_device_id="dev1")
        assert readings.inside_temperature == "19.5"
        assert readings.inside_temperature_entity_id == "sensor.bedroom_temp"
        assert readings.inside_temperature_source == "area"

    @pytest.mark.unit
    def test_none_source_when_unresolved(self, provider):
        """No explicit entity and no area temp → source 'none'."""
        dev_patch, area_patch = _patch_area(device_area_id=None)
        with dev_patch, area_patch:
            readings = provider.read(temp_entity=None, temp_device_id="dev1")
        assert readings.inside_temperature is None
        assert readings.inside_temperature_entity_id is None
        assert readings.inside_temperature_source == "none"

    @pytest.mark.unit
    def test_auto_resolve_disabled_skips_area(self, provider, mock_hass):
        """auto_resolve off → area sensor ignored, source 'none'."""
        mock_hass.states.get.return_value = _mock_state("sensor.bedroom_temp", "19.5")
        dev_patch, area_patch = _patch_area(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        with dev_patch, area_patch:
            readings = provider.read(
                temp_entity=None,
                temp_device_id="dev1",
                auto_resolve_temp_from_area=False,
            )
        assert readings.inside_temperature is None
        assert readings.inside_temperature_entity_id is None
        assert readings.inside_temperature_source == "none"


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
    def test_person_home(self, provider, mock_hass):
        """Person 'home' → True (regression guard for #313)."""
        mock_hass.states.get.return_value = _mock_state("person.alice", "home")
        readings = provider.read(presence_entity="person.alice")
        assert readings.is_presence is True

    @pytest.mark.unit
    def test_person_away(self, provider, mock_hass):
        """Person 'not_home' → False (regression guard for #313)."""
        mock_hass.states.get.return_value = _mock_state("person.alice", "not_home")
        readings = provider.read(presence_entity="person.alice")
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
# is_sunny binary sensor override (issue #363)
# ---------------------------------------------------------------------------


class TestSunnySensor:
    """Optional binary 'is sunny' sensor authoritatively drives is_sunny."""

    @pytest.mark.unit
    def test_sensor_on_overrides_weather(self, provider, mock_hass):
        """Sensor on → True even when weather is rainy."""
        states = {
            "binary_sensor.sunny": _mock_state("binary_sensor.sunny", "on"),
            "weather.home": _mock_state("weather.home", "rainy"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
            is_sunny_sensor="binary_sensor.sunny",
        )
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_sensor_off_overrides_weather(self, provider, mock_hass):
        """Sensor off → False even when weather is sunny."""
        states = {
            "binary_sensor.sunny": _mock_state("binary_sensor.sunny", "off"),
            "weather.home": _mock_state("weather.home", "sunny"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
            is_sunny_sensor="binary_sensor.sunny",
        )
        assert readings.is_sunny is False

    @pytest.mark.unit
    def test_sensor_unavailable_falls_through_to_weather_true(
        self, provider, mock_hass
    ):
        """Sensor unavailable → fall through; weather sunny → True."""
        states = {
            "binary_sensor.sunny": _mock_state("binary_sensor.sunny", "unavailable"),
            "weather.home": _mock_state("weather.home", "sunny"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
            is_sunny_sensor="binary_sensor.sunny",
        )
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_sensor_unavailable_falls_through_to_weather_false(
        self, provider, mock_hass
    ):
        """Sensor unavailable → fall through; weather rainy → False."""
        states = {
            "binary_sensor.sunny": _mock_state("binary_sensor.sunny", "unknown"),
            "weather.home": _mock_state("weather.home", "rainy"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(
            weather_entity="weather.home",
            weather_condition=["sunny", "partlycloudy"],
            is_sunny_sensor="binary_sensor.sunny",
        )
        assert readings.is_sunny is False

    @pytest.mark.unit
    def test_sensor_only_no_weather_entity_falls_through_to_true(
        self, provider, mock_hass
    ):
        """Sensor unavailable, no weather entity → True (existing default)."""
        states = {
            "binary_sensor.sunny": _mock_state("binary_sensor.sunny", "unavailable"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(is_sunny_sensor="binary_sensor.sunny")
        assert readings.is_sunny is True

    @pytest.mark.unit
    def test_input_boolean_on(self, provider, mock_hass):
        """input_boolean on → True (any binary-on domain works)."""
        states = {
            "input_boolean.sun_present": _mock_state("input_boolean.sun_present", "on"),
        }
        mock_hass.states.get.side_effect = lambda eid: states.get(eid)
        readings = provider.read(is_sunny_sensor="input_boolean.sun_present")
        assert readings.is_sunny is True


# ---------------------------------------------------------------------------
# is_sunny condition template (issue #639) — needs a real hass to render Jinja
# ---------------------------------------------------------------------------


def _real_provider(hass):
    """ClimateProvider bound to a real hass for template rendering."""
    return ClimateProvider(hass=hass, logger=MagicMock())


class TestSunnyTemplate:
    """Optional Jinja condition template folds into is_sunny (issue #639)."""

    async def test_template_true_no_sensor_no_weather(self, hass):
        """Template ``{{ true }}`` alone → sunny."""
        p = _real_provider(hass)
        readings = p.read(is_sunny_template="{{ true }}")
        assert readings.is_sunny is True

    async def test_template_false_no_sensor_no_weather(self, hass):
        """Template ``{{ false }}`` alone → not sunny (overrides default-True)."""
        p = _real_provider(hass)
        readings = p.read(is_sunny_template="{{ false }}")
        assert readings.is_sunny is False

    async def test_template_states_expression_true(self, hass):
        """A states()-based template renders to True when the state is high."""
        hass.states.async_set("sensor.elev", "30")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(is_sunny_template="{{ states('sensor.elev') | float > 10 }}")
        assert readings.is_sunny is True

    async def test_template_states_expression_false(self, hass):
        """The same template renders to False when the state is low."""
        hass.states.async_set("sensor.elev", "5")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(is_sunny_template="{{ states('sensor.elev') | float > 10 }}")
        assert readings.is_sunny is False

    async def test_template_or_sensor_off_template_true(self, hass):
        """OR mode (default): sensor off, template true → sunny."""
        hass.states.async_set("binary_sensor.sunny", "off")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            is_sunny_sensor="binary_sensor.sunny",
            is_sunny_template="{{ true }}",
            is_sunny_template_mode="or",
        )
        assert readings.is_sunny is True

    async def test_template_and_sensor_off_template_true(self, hass):
        """AND mode: sensor off, template true → not sunny (both required)."""
        hass.states.async_set("binary_sensor.sunny", "off")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            is_sunny_sensor="binary_sensor.sunny",
            is_sunny_template="{{ true }}",
            is_sunny_template_mode="and",
        )
        assert readings.is_sunny is False

    async def test_empty_template_falls_through_to_weather(self, hass):
        """Empty template → no opinion → weather fallback wins."""
        hass.states.async_set("weather.home", "rainy")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            weather_entity="weather.home",
            weather_condition=["sunny"],
            is_sunny_template="",
        )
        assert readings.is_sunny is False

    async def test_broken_template_falls_through_to_weather(self, hass):
        """Broken template → no opinion → weather fallback wins."""
        hass.states.async_set("weather.home", "sunny")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            weather_entity="weather.home",
            weather_condition=["sunny"],
            is_sunny_template="{{ nonexistent_fn() }}",
        )
        # Template gives no opinion; weather sunny → True.
        assert readings.is_sunny is True

    async def test_non_template_string_falls_through(self, hass):
        """A plain (non-Jinja) string is not a template → weather fallback."""
        hass.states.async_set("weather.home", "rainy")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            weather_entity="weather.home",
            weather_condition=["sunny"],
            is_sunny_template="just text",
        )
        assert readings.is_sunny is False


# ---------------------------------------------------------------------------
# presence condition template (issue #639)
# ---------------------------------------------------------------------------


class TestPresenceTemplate:
    """Optional Jinja condition template folds into is_presence (issue #639)."""

    async def test_template_true_no_entity(self, hass):
        """Template ``{{ true }}`` alone → present."""
        p = _real_provider(hass)
        readings = p.read(presence_template="{{ true }}")
        assert readings.is_presence is True

    async def test_template_false_no_entity(self, hass):
        """Template ``{{ false }}`` alone → not present.

        With no entity, the fail-open ``is_entity_active(None)`` must NOT leak
        in as a True operand — a lone falsy template means not-present.
        """
        p = _real_provider(hass)
        readings = p.read(presence_template="{{ false }}")
        assert readings.is_presence is False

    async def test_template_states_expression(self, hass):
        """A states()-based presence template renders both directions."""
        hass.states.async_set("sensor.people", "2")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        assert (
            p.read(
                presence_template="{{ states('sensor.people') | int > 0 }}"
            ).is_presence
            is True
        )
        hass.states.async_set("sensor.people", "0")
        await hass.async_block_till_done()
        assert (
            p.read(
                presence_template="{{ states('sensor.people') | int > 0 }}"
            ).is_presence
            is False
        )

    async def test_template_or_entity_off_template_true(self, hass):
        """OR mode: entity not-home, template true → present."""
        hass.states.async_set("person.alice", "not_home")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            presence_entity="person.alice",
            presence_template="{{ true }}",
            presence_template_mode="or",
        )
        assert readings.is_presence is True

    async def test_template_and_entity_off_template_true(self, hass):
        """AND mode: entity not-home, template true → not present."""
        hass.states.async_set("person.alice", "not_home")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            presence_entity="person.alice",
            presence_template="{{ true }}",
            presence_template_mode="and",
        )
        assert readings.is_presence is False

    async def test_empty_template_falls_through_to_entity(self, hass):
        """Empty template → existing entity logic decides."""
        hass.states.async_set("binary_sensor.presence", "on")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            presence_entity="binary_sensor.presence",
            presence_template="",
        )
        assert readings.is_presence is True

    async def test_broken_template_falls_through_to_entity(self, hass):
        """Broken template → no opinion → entity logic decides."""
        hass.states.async_set("binary_sensor.presence", "off")
        await hass.async_block_till_done()
        p = _real_provider(hass)
        readings = p.read(
            presence_entity="binary_sensor.presence",
            presence_template="{{ nonexistent_fn() }}",
        )
        assert readings.is_presence is False

    async def test_no_template_no_entity_default_true(self, hass):
        """No template and no entity → present (existing fail-open behavior)."""
        p = _real_provider(hass)
        assert p.read().is_presence is True


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


# ---------------------------------------------------------------------------
# Hysteresis release-cleared fields (issue #864)
# ---------------------------------------------------------------------------


class TestReleaseClearedHysteresis:
    """Release-cleared edges the manager latch consumes (issue #864).

    The provider stays pure: it reports whether a trigger's *activate* edge is
    met and whether the value has cleared its *release* edge in the SAME read.
    A blank release threshold collapses the band to zero width, so
    ``release_cleared == (not activate_met)`` — exact back-compat.
    """

    @pytest.mark.unit
    def test_readings_expose_release_cleared_fields(self):
        """ClimateReadings carries the three release-cleared booleans."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=None,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        # Defaulted fields exist and default to "cleared" (no latch held).
        assert readings.lux_release_cleared is True
        assert readings.irradiance_release_cleared is True
        assert readings.cloud_coverage_release_cleared is True

    @pytest.mark.unit
    def test_lux_blank_release_mirrors_not_activate_dark(self, provider, mock_hass):
        """Dark (below activate) + blank release → not cleared (latch would hold)."""
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "4000")
        readings = provider.read(
            use_lux=True, lux_entity="sensor.lux", lux_threshold=5000
        )
        assert readings.lux_below_threshold is True
        assert readings.lux_release_cleared == (not readings.lux_below_threshold)
        assert readings.lux_release_cleared is False

    @pytest.mark.unit
    def test_lux_blank_release_mirrors_not_activate_bright(self, provider, mock_hass):
        """Bright (above activate) + blank release → cleared."""
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "6000")
        readings = provider.read(
            use_lux=True, lux_entity="sensor.lux", lux_threshold=5000
        )
        assert readings.lux_below_threshold is False
        assert readings.lux_release_cleared == (not readings.lux_below_threshold)
        assert readings.lux_release_cleared is True

    @pytest.mark.unit
    def test_lux_value_in_band_holds_latch(self, provider, mock_hass):
        """Value between activate and release → neither activate nor cleared."""
        # activate 5000, release 8000 → band (5000, 8000). value 6500 is inside.
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "6500")
        readings = provider.read(
            use_lux=True,
            lux_entity="sensor.lux",
            lux_threshold=5000,
            lux_release_threshold=8000,
        )
        assert readings.lux_below_threshold is False
        assert readings.lux_release_cleared is False

    @pytest.mark.unit
    def test_lux_value_above_release_clears(self, provider, mock_hass):
        """Value at/above release edge → cleared."""
        mock_hass.states.get.return_value = _mock_state("sensor.lux", "8000")
        readings = provider.read(
            use_lux=True,
            lux_entity="sensor.lux",
            lux_threshold=5000,
            lux_release_threshold=8000,
        )
        assert readings.lux_below_threshold is False
        assert readings.lux_release_cleared is True

    @pytest.mark.unit
    def test_irradiance_value_in_band_holds_latch(self, provider, mock_hass):
        """Irradiance between activate and release → latch would hold."""
        mock_hass.states.get.return_value = _mock_state("sensor.solar", "400")
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
            irradiance_release_threshold=500,
        )
        assert readings.irradiance_below_threshold is False
        assert readings.irradiance_release_cleared is False

    @pytest.mark.unit
    def test_cloud_value_in_band_holds_latch(self, provider, mock_hass):
        """Cloud coverage between release and activate → latch would hold.

        Cloud activate is "at or above" (overcast), so the band is inverted:
        release < activate. A value between them is neither overcast nor clear.
        """
        # activate 75 (overcast), release 50 (clear). value 60 is inside band.
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "60")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
            cloud_coverage_release_threshold=50,
        )
        assert readings.cloud_coverage_above_threshold is False
        assert readings.cloud_coverage_release_cleared is False

    @pytest.mark.unit
    def test_cloud_value_below_release_clears(self, provider, mock_hass):
        """Cloud coverage at/below release edge → cleared."""
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", "50")
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=75,
            cloud_coverage_release_threshold=50,
        )
        assert readings.cloud_coverage_above_threshold is False
        assert readings.cloud_coverage_release_cleared is True

    @pytest.mark.unit
    def test_disabled_trigger_is_cleared(self, provider):
        """A disabled trigger reports cleared so no latch can hold."""
        readings = provider.read(use_lux=False, use_irradiance=False)
        assert readings.lux_release_cleared is True
        assert readings.irradiance_release_cleared is True
