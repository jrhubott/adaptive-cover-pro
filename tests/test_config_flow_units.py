"""Imperial-locale config-flow tests: labels, ranges, and round-tripping."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import selector
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro import unit_system
from custom_components.adaptive_cover_pro.config_flow import (
    light_cloud_schema,
    sun_tracking_schema,
    temperature_climate_schema,
    weather_override_schema,
    _build_glare_zones_schema,
    _glare_zone_length_keys,
    _stringify_templatable,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LUX_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_SILL_HEIGHT,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
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
        assert len(keys) == 16  # 4 slots × 4 axes (x, y, radius, z)
        assert "glare_zone_1_x" in keys
        assert "glare_zone_4_radius" in keys
        assert "glare_zone_1_z" in keys
        assert "glare_zone_4_z" in keys


# --- Templatable thresholds: TemplateSelector, no unit/range (#577) ------- #


def _selector_obj(schema, key):
    """Return the selector object bound to ``key`` in *schema*."""
    for k, v in schema.schema.items():
        if str(k) == key:
            return v
    raise AssertionError(f"key {key!r} not found in schema")


@pytest.mark.unit
class TestTemplatableThresholdSelectors:
    """The 9 threshold fields use a TemplateSelector (number or Jinja2 template).

    Issue #577 swapped these from unit-aware NumberSelectors to the Jinja code
    editor so they accept a number or a template, with entity autocomplete and
    syntax highlighting. They no longer carry a ``unit_of_measurement`` or
    numeric range — the unit now lives in the field's translation description.
    Legacy numeric values are stringified before the form so the editor (which
    only renders a string) does not collapse — see
    ``config_flow._stringify_templatable``.
    """

    @staticmethod
    def _assert_template(schema, key):
        assert isinstance(_selector_obj(schema, key), selector.TemplateSelector)

    def test_temperature_thresholds_are_template_selectors(self):
        schema = temperature_climate_schema(_hass(imperial=False), {})
        for key in (CONF_TEMP_LOW, CONF_TEMP_HIGH, CONF_OUTSIDE_THRESHOLD):
            self._assert_template(schema, key)

    def test_weather_thresholds_are_template_selectors(self):
        schema = weather_override_schema(_hass(imperial=False), {})
        for key in (
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
            CONF_WEATHER_RAIN_THRESHOLD,
        ):
            self._assert_template(schema, key)

    def test_light_cloud_thresholds_are_template_selectors(self):
        schema = light_cloud_schema(_hass(imperial=False), {})
        for key in (
            CONF_LUX_THRESHOLD,
            CONF_IRRADIANCE_THRESHOLD,
            CONF_CLOUD_COVERAGE_THRESHOLD,
        ):
            self._assert_template(schema, key)


@pytest.mark.unit
class TestStringifyTemplatable:
    """Legacy numeric thresholds are stringified so the template editor renders."""

    def test_int_becomes_string(self):
        out = _stringify_templatable({CONF_LUX_THRESHOLD: 1000})
        assert out[CONF_LUX_THRESHOLD] == "1000"

    def test_whole_float_drops_trailing_zero(self):
        out = _stringify_templatable({CONF_WEATHER_RAIN_THRESHOLD: 1.0})
        assert out[CONF_WEATHER_RAIN_THRESHOLD] == "1"

    def test_fractional_float_kept(self):
        out = _stringify_templatable({CONF_WEATHER_RAIN_THRESHOLD: 1.5})
        assert out[CONF_WEATHER_RAIN_THRESHOLD] == "1.5"

    def test_template_string_untouched(self):
        out = _stringify_templatable({CONF_TEMP_LOW: "{{ 21 }}"})
        assert out[CONF_TEMP_LOW] == "{{ 21 }}"

    def test_none_and_non_templatable_untouched(self):
        out = _stringify_templatable({CONF_TEMP_LOW: None, "name": "Office"})
        assert out[CONF_TEMP_LOW] is None
        assert out["name"] == "Office"


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
