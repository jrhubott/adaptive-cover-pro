"""Imperial-locale config-flow tests: labels, ranges, and round-tripping."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro import unit_system
from custom_components.adaptive_cover_pro.config_flow import (
    light_cloud_schema,
    sun_tracking_schema,
    temperature_climate_schema,
    weather_override_schema,
    _build_glare_zones_schema,
    _glare_zone_length_keys,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_SILL_HEIGHT,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
)
from custom_components.adaptive_cover_pro.cover_types.blind import (
    geometry_vertical_schema,
)
from custom_components.adaptive_cover_pro.cover_types.tilt import geometry_tilt_schema


def _hass(*, imperial: bool):
    """Return a MagicMock hass scoped to the requested unit system."""
    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if imperial else METRIC_SYSTEM
    hass.states.get.return_value = None
    return hass


def _selector_for(schema, key) -> dict:
    """Return the NumberSelectorConfig dict for ``key`` in *schema*."""
    for k, v in schema.schema.items():
        if str(k) == key:
            return v.config
    raise AssertionError(f"key {key!r} not found in schema")


# --- Geometry schemas: lengths in inches in imperial ---------------------- #


@pytest.mark.unit
class TestGeometrySchemaLabels:
    """Verify the cover_types geometry schemas swap unit labels per locale."""

    def test_metric_uses_metres(self):
        schema = geometry_vertical_schema(_hass(imperial=False))
        for key in (
            CONF_HEIGHT_WIN,
            CONF_WINDOW_WIDTH,
            CONF_WINDOW_DEPTH,
            CONF_SILL_HEIGHT,
        ):
            cfg = _selector_for(schema, key)
            assert cfg["unit_of_measurement"] == "m"

    def test_imperial_uses_inches(self):
        schema = geometry_vertical_schema(_hass(imperial=True))
        for key in (
            CONF_HEIGHT_WIN,
            CONF_WINDOW_WIDTH,
            CONF_WINDOW_DEPTH,
            CONF_SILL_HEIGHT,
        ):
            cfg = _selector_for(schema, key)
            assert cfg["unit_of_measurement"] == "in"
            # Range is converted: 50 m max → ~1968 in (≥ 1968.5 after round-up).
            if key in (CONF_HEIGHT_WIN, CONF_WINDOW_WIDTH, CONF_SILL_HEIGHT):
                assert cfg["max"] >= 1968
            assert cfg["step"] == 0.5

    def test_no_decimal_feet(self):
        """Imperial must never label fields with 'ft' — see plan."""
        schema = geometry_vertical_schema(_hass(imperial=True))
        for k, v in schema.schema.items():
            if hasattr(v, "config") and "unit_of_measurement" in v.config:
                assert (
                    v.config["unit_of_measurement"] != "ft"
                ), f"{k} labelled 'ft' — must be 'in' per design"


@pytest.mark.unit
class TestTiltSlatLabels:
    """Slat dimensions: cm metric, in imperial."""

    def test_metric_uses_cm(self):
        schema = geometry_tilt_schema(_hass(imperial=False))
        cfg = _selector_for(schema, CONF_TILT_DEPTH)
        assert cfg["unit_of_measurement"] == "cm"

    def test_imperial_uses_inches(self):
        schema = geometry_tilt_schema(_hass(imperial=True))
        for key in (CONF_TILT_DEPTH, CONF_TILT_DISTANCE):
            cfg = _selector_for(schema, key)
            assert cfg["unit_of_measurement"] == "in"
            # 15 cm max → ~5.91 in → rounded up to 5.95 in at 0.05 step.
            assert cfg["max"] >= 5.9
            assert cfg["step"] == 0.05


@pytest.mark.unit
class TestSunTrackingDistance:
    """CONF_DISTANCE follows the length-unit locale."""

    def test_metric(self):
        cfg = _selector_for(sun_tracking_schema(_hass(imperial=False)), CONF_DISTANCE)
        assert cfg["unit_of_measurement"] == "m"

    def test_imperial(self):
        cfg = _selector_for(sun_tracking_schema(_hass(imperial=True)), CONF_DISTANCE)
        assert cfg["unit_of_measurement"] == "in"


@pytest.mark.unit
class TestGlareZoneSchema:
    """Glare-zone x/y/radius selectors follow the length-unit locale."""

    def test_metric(self):
        schema = _build_glare_zones_schema(options=None, hass=_hass(imperial=False))
        cfg = _selector_for(schema, "glare_zone_1_x")
        assert cfg["unit_of_measurement"] == "m"

    def test_imperial(self):
        schema = _build_glare_zones_schema(options=None, hass=_hass(imperial=True))
        for axis in ("x", "y", "radius"):
            cfg = _selector_for(schema, f"glare_zone_1_{axis}")
            assert cfg["unit_of_measurement"] == "in"

    def test_length_keys_exhaustive(self):
        keys = _glare_zone_length_keys()
        assert len(keys) == 12  # 4 slots × 3 axes
        assert "glare_zone_1_x" in keys
        assert "glare_zone_4_radius" in keys


# --- Sensor-driven thresholds: SENSOR's unit wins ------------------------- #


@pytest.mark.unit
class TestTemperatureSensorUnitLabel:
    """Temperature threshold labels track the configured sensor's UOM."""

    def _hass_with_sensor(self, *, imperial: bool, sensor_uom: str | None):
        hass = _hass(imperial=imperial)
        if sensor_uom is None:
            hass.states.get.return_value = None
        else:
            state = MagicMock()
            state.attributes = {"unit_of_measurement": sensor_uom}
            hass.states.get.return_value = state
        return hass

    def test_no_sensor_falls_back_to_ha_locale(self):
        # Metric locale, no sensor → label is HA's locale unit.
        hass = self._hass_with_sensor(imperial=False, sensor_uom=None)
        schema = temperature_climate_schema(hass, {})
        cfg = _selector_for(schema, CONF_TEMP_LOW)
        assert cfg["unit_of_measurement"] == str(hass.config.units.temperature_unit)

    def test_metric_locale_fahrenheit_sensor_shows_fahrenheit(self):
        """The sensor's unit governs even when HA is on metric."""
        hass = self._hass_with_sensor(imperial=False, sensor_uom="°F")
        schema = temperature_climate_schema(hass, {CONF_TEMP_ENTITY: "sensor.x"})
        cfg = _selector_for(schema, CONF_TEMP_LOW)
        assert cfg["unit_of_measurement"] == "°F"
        # Outside-temp threshold uses a DIFFERENT sensor entity (CONF_OUTSIDETEMP_ENTITY).
        # Not set in options → fallback to HA's locale.
        out_cfg = _selector_for(schema, CONF_OUTSIDE_THRESHOLD)
        assert out_cfg["unit_of_measurement"] == str(hass.config.units.temperature_unit)

    def test_temperature_range_widened(self):
        """Range covers both Celsius and Fahrenheit comfort thresholds."""
        cfg = _selector_for(
            temperature_climate_schema(_hass(imperial=False), {}), CONF_TEMP_LOW
        )
        # Must accommodate 70-90 °F (and well below 0 °C if the sensor reports
        # negative outdoor temperatures elsewhere).
        assert cfg["max"] >= 150
        cfg_high = _selector_for(
            temperature_climate_schema(_hass(imperial=False), {}), CONF_TEMP_HIGH
        )
        assert cfg_high["max"] >= 150


@pytest.mark.unit
class TestWeatherSensorUnitLabels:
    """Wind speed / rain threshold labels track the configured sensor's UOM."""

    def _hass_with_uom(self, entity_id_to_uom: dict[str, str]):
        hass = _hass(imperial=False)

        def _get(entity_id):
            uom = entity_id_to_uom.get(entity_id)
            if uom is None:
                return None
            st = MagicMock()
            st.attributes = {"unit_of_measurement": uom}
            return st

        hass.states.get.side_effect = _get
        return hass

    def test_wind_label_tracks_sensor(self):
        hass = self._hass_with_uom({"sensor.wind": "km/h"})
        schema = weather_override_schema(
            hass, {CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind"}
        )
        cfg = _selector_for(schema, CONF_WEATHER_WIND_SPEED_THRESHOLD)
        assert cfg["unit_of_measurement"] == "km/h"

    def test_rain_label_tracks_sensor(self):
        hass = self._hass_with_uom({"sensor.rain": "mm/h"})
        schema = weather_override_schema(
            hass, {CONF_WEATHER_RAIN_SENSOR: "sensor.rain"}
        )
        cfg = _selector_for(schema, CONF_WEATHER_RAIN_THRESHOLD)
        assert cfg["unit_of_measurement"] == "mm/h"


@pytest.mark.unit
class TestLightCloudSensorUnitLabels:
    """Lux / irradiance threshold labels track the configured sensor's UOM."""

    def test_lux_label_tracks_sensor(self):
        hass = _hass(imperial=False)
        st = MagicMock()
        st.attributes = {"unit_of_measurement": "klx"}
        hass.states.get.return_value = st
        schema = light_cloud_schema(hass, {CONF_LUX_ENTITY: "sensor.lux"})
        cfg = _selector_for(schema, CONF_LUX_THRESHOLD)
        assert cfg["unit_of_measurement"] == "klx"

    def test_irradiance_label_default(self):
        """No sensor → fallback to the conventional W/m²."""
        schema = light_cloud_schema(_hass(imperial=False), {})
        cfg = _selector_for(schema, CONF_IRRADIANCE_THRESHOLD)
        assert cfg["unit_of_measurement"] == "W/m²"


# --- Dict-level conversion: imperial round-trip --------------------------- #


@pytest.mark.unit
class TestDictRoundTrip:
    """Imperial users enter inches; stored value stays canonical metres / cm."""

    def test_length_roundtrip(self):
        hass = _hass(imperial=True)
        # User entered 82.7 in for window height.
        user_input = {CONF_HEIGHT_WIN: 82.7}
        canonical = unit_system.user_input_to_canonical(
            hass, user_input, length_keys=[CONF_HEIGHT_WIN]
        )
        assert canonical[CONF_HEIGHT_WIN] == pytest.approx(2.101, abs=0.01)

        # Re-displaying that canonical value (now stored as ~2.101 m) for a
        # metric user in metric mode shows 2.101 m unchanged.
        displayed = unit_system.options_to_display(
            _hass(imperial=False),
            canonical,
            length_keys=[CONF_HEIGHT_WIN],
        )
        assert displayed[CONF_HEIGHT_WIN] == pytest.approx(2.101, abs=0.01)

        # And re-displaying it to the same imperial user shows ~82.7 in.
        displayed_imp = unit_system.options_to_display(
            hass, canonical, length_keys=[CONF_HEIGHT_WIN]
        )
        assert displayed_imp[CONF_HEIGHT_WIN] == pytest.approx(82.7, abs=0.1)

    def test_slat_roundtrip(self):
        hass = _hass(imperial=True)
        user_input = {CONF_TILT_DEPTH: 1.0}  # 1 in
        canonical = unit_system.user_input_to_canonical(
            hass, user_input, slat_keys=[CONF_TILT_DEPTH]
        )
        # 1 in == 2.54 cm exactly.
        assert canonical[CONF_TILT_DEPTH] == pytest.approx(2.54, abs=1e-9)
