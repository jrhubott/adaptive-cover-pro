"""Tests asserting each CONF_* key lives on its correct config-flow step."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro import config_flow as cf
from custom_components.adaptive_cover_pro.config_dynamic import (
    sun_tracking_schema,
    temperature_climate_schema,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_INVERSE_STATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_POSITION_TOLERANCE,
    CONF_RETURN_SUNSET,
    CONF_SUNRISE_GATES_START,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_TRANSIT_TIMEOUT,
    CONF_VENETIAN_MODE,
    CONF_VENETIAN_POST_SETTLE_MODE,
    CONF_VENETIAN_TILT_RESET_DIRECTION,
    CONF_VENETIAN_TILT_RESET_SCOPE,
    CONF_VENETIAN_TILT_SKIP_MODE,
    CONF_WEATHER_OVERRIDE_TILT,
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_OUTSIDE_TEMP_SOURCE,
    DEFAULT_ENABLE_PROXY_COVER,
    CoverType,
    OutsideTempSource,
)


def _schema_keys(schema) -> set[str]:
    return {str(k) for k in schema.schema}


# ---------------------------------------------------------------------------
# 4-layer split (#613): L2a positions (% values only) vs L2b behavior
# (timing/thresholds) vs L4 global motion constraints.
# ---------------------------------------------------------------------------

# All percentage target values live ONLY in the L2a positions schema.
_L2A_POSITION_KEYS = [
    CONF_DEFAULT_HEIGHT,
    CONF_MIN_POSITION,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_SUNSET_POS,
    CONF_OPEN_CLOSE_THRESHOLD,
]

# Timing & threshold behavior lives ONLY in the L2b behavior schema.
_L2B_BEHAVIOR_KEYS = [
    CONF_POSITION_TOLERANCE,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_INVERSE_STATE,
    CONF_SUNSET_OFFSET,
    CONF_SUNRISE_OFFSET,
    # The sunrise floor (#1340) is a timing gate, not a % target — it belongs
    # beside the sunrise offset it composes with, never on the position step.
    CONF_SUNRISE_GATES_START,
    CONF_RETURN_SUNSET,
    CONF_SUNSET_TIME_ENTITY,
]


def test_l2a_position_keys_in_position_schema_not_behavior() -> None:
    """Every % position value lives on the L2a position step only."""
    pos = _schema_keys(cf.POSITION_SCHEMA)
    beh = _schema_keys(cf.BEHAVIOR_SCHEMA)
    for key in _L2A_POSITION_KEYS:
        assert key in pos, f"{key} should be in POSITION_SCHEMA (L2a)"
        assert key not in beh, f"{key} must not be in BEHAVIOR_SCHEMA (L2b)"


def test_position_schema_has_enforce_delta_at_endpoints() -> None:
    """The enforce-delta-at-endpoints toggle lives on the position step (#679)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )

    assert CONF_ENFORCE_DELTA_AT_ENDPOINTS in _schema_keys(cf.POSITION_SCHEMA)


def test_l2b_behavior_keys_in_behavior_schema_not_position() -> None:
    """Every timing/threshold value lives on the L2b behavior step only."""
    pos = _schema_keys(cf.POSITION_SCHEMA)
    beh = _schema_keys(cf.BEHAVIOR_SCHEMA)
    for key in _L2B_BEHAVIOR_KEYS:
        assert key in beh, f"{key} should be in BEHAVIOR_SCHEMA (L2b)"
        assert key not in pos, f"{key} must not be in POSITION_SCHEMA (L2a)"


def test_position_tolerance_in_behavior_schema_with_default_three() -> None:
    """CONF_POSITION_TOLERANCE moved to the L2b behavior step, default 3 (#591/#613)."""
    marker = next(
        k for k in cf.BEHAVIOR_SCHEMA.schema if str(k) == CONF_POSITION_TOLERANCE
    )
    assert marker.default() == 3


def test_enable_position_matching_in_behavior_schema() -> None:
    """CONF_ENABLE_POSITION_MATCHING moved to L2b behavior, default False (#591/#613)."""
    marker = next(
        k for k in cf.BEHAVIOR_SCHEMA.schema if str(k) == CONF_ENABLE_POSITION_MATCHING
    )
    assert marker.default() is False


def test_l4_motion_constraints_in_automation_schema() -> None:
    """L4 global motion constraints (delta + minimize/coverage) on the automation step."""
    auto = _schema_keys(cf.AUTOMATION_SCHEMA)
    for key in (
        CONF_DELTA_POSITION,
        CONF_DELTA_TIME,
        CONF_MINIMIZE_MOVEMENTS,
        CONF_MAX_COVERAGE_STEPS,
    ):
        assert key in auto, f"{key} should be in AUTOMATION_SCHEMA (L4)"


def test_minimize_and_coverage_moved_off_sun_tracking_step() -> None:
    """minimize_movements / max_coverage_steps are L4, not on the L1 window step."""
    sun_keys = _schema_keys(sun_tracking_schema())
    assert CONF_MINIMIZE_MOVEMENTS not in sun_keys
    assert CONF_MAX_COVERAGE_STEPS not in sun_keys


@pytest.mark.parametrize(
    "conf_key, expected_schema_name, forbidden_schema_name",
    [
        (CONF_TRANSIT_TIMEOUT, "MANUAL_OVERRIDE_SCHEMA", "DEBUG_SCHEMA"),
        (CONF_DEBUG_EVENT_BUFFER_SIZE, "DEBUG_SCHEMA", "MANUAL_OVERRIDE_SCHEMA"),
        (CONF_MANUAL_OVERRIDE_DURATION, "MANUAL_OVERRIDE_SCHEMA", "DEBUG_SCHEMA"),
        (CONF_DEBUG_MODE, "DEBUG_SCHEMA", "MANUAL_OVERRIDE_SCHEMA"),
    ],
)
def test_conf_key_lives_on_correct_step(
    conf_key: str, expected_schema_name: str, forbidden_schema_name: str
) -> None:
    expected = _schema_keys(getattr(cf, expected_schema_name))
    forbidden = _schema_keys(getattr(cf, forbidden_schema_name))
    assert conf_key in expected, f"{conf_key} should be in {expected_schema_name}"
    assert (
        conf_key not in forbidden
    ), f"{conf_key} should NOT be in {forbidden_schema_name}"


def test_venetian_mode_in_geometry_venetian_schema() -> None:
    """CONF_VENETIAN_MODE must live on the venetian geometry step, not elsewhere."""
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    keys = _schema_keys(GEOMETRY_VENETIAN_SCHEMA)
    assert CONF_VENETIAN_MODE in keys


@pytest.mark.parametrize(
    "conf_key, expected_translation_key",
    [
        (CONF_VENETIAN_MODE, "venetian_mode"),
        (CONF_VENETIAN_TILT_RESET_DIRECTION, "venetian_tilt_reset_direction"),
        (CONF_VENETIAN_TILT_RESET_SCOPE, "venetian_tilt_reset_scope"),
        (CONF_VENETIAN_TILT_SKIP_MODE, "venetian_tilt_skip_mode"),
        (CONF_VENETIAN_POST_SETTLE_MODE, "venetian_post_settle_mode"),
    ],
)
def test_venetian_select_fields_use_selector_with_translation_key(
    conf_key: str, expected_translation_key: str
) -> None:
    """The 5 venetian enum fields must render via SelectSelector(translation_key=...),
    not a bare vol.In(...), or the frontend shows raw enum values instead of
    translated labels (issue: untranslated venetian select options).
    """
    from homeassistant.helpers import selector as ha_selector

    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    marker = next(k for k in GEOMETRY_VENETIAN_SCHEMA.schema if str(k) == conf_key)
    value_validator = GEOMETRY_VENETIAN_SCHEMA.schema[marker]
    assert isinstance(
        value_validator, ha_selector.SelectSelector
    ), f"{conf_key} must use SelectSelector, not vol.In"
    assert value_validator.config.get("translation_key") == expected_translation_key


def test_tilt_sun_only_toggles_in_venetian_schema_default_false() -> None:
    """min/max_tilt_sun_only toggles live on the venetian schema, default False (#503)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MAX_TILT_SUN_ONLY,
        CONF_MIN_TILT_SUN_ONLY,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    schema = GEOMETRY_VENETIAN_SCHEMA.schema
    keys = {str(k) for k in schema}
    assert CONF_MAX_TILT_SUN_ONLY in keys
    assert CONF_MIN_TILT_SUN_ONLY in keys
    for marker in schema:
        if str(marker) in (CONF_MAX_TILT_SUN_ONLY, CONF_MIN_TILT_SUN_ONLY):
            assert marker.default() is False


# ---------------------------------------------------------------------------
# Per-slot tilt slider — venetian only (Step 12)
# ---------------------------------------------------------------------------


def test_custom_position_schema_venetian_includes_tilt_slots() -> None:
    """_build_custom_position_schema_dict(sensor_type='cover_venetian') includes tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert slot_keys["tilt"] in keys, f"{slot_keys['tilt']} missing for venetian"


def test_custom_position_schema_blind_excludes_tilt_slots() -> None:
    """_build_custom_position_schema_dict(sensor_type='cover_blind') must not include tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt"] not in keys
        ), f"{slot_keys['tilt']} should not be in blind"


def test_custom_position_schema_awning_excludes_tilt_slots() -> None:
    """Awning schema must not include tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.AWNING)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt"] not in keys
        ), f"{slot_keys['tilt']} should not be in awning"


# ---------------------------------------------------------------------------
# Per-slot tilt-only boolean — venetian only (issue #514)
# ---------------------------------------------------------------------------


def test_custom_position_schema_venetian_includes_tilt_only_slots() -> None:
    """Venetian schema includes the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] in keys
        ), f"{slot_keys['tilt_only']} missing for venetian"


def test_custom_position_schema_blind_excludes_tilt_only_slots() -> None:
    """Blind schema must not include the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] not in keys
        ), f"{slot_keys['tilt_only']} should not be in blind"


def test_custom_position_schema_awning_excludes_tilt_only_slots() -> None:
    """Awning schema must not include the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.AWNING)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] not in keys
        ), f"{slot_keys['tilt_only']} should not be in awning"


# ---------------------------------------------------------------------------
# Default/sunset tilt sliders — venetian only (Step 13)
# ---------------------------------------------------------------------------


def test_default_tilt_in_venetian_custom_position_schema() -> None:
    """CONF_DEFAULT_TILT must be in the venetian custom_position schema."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    assert CONF_DEFAULT_TILT in keys, "default_tilt missing from venetian schema"


def test_sunset_tilt_in_venetian_custom_position_schema() -> None:
    """CONF_SUNSET_TILT must be in the venetian custom_position schema."""
    from custom_components.adaptive_cover_pro.const import CONF_SUNSET_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    assert CONF_SUNSET_TILT in keys, "sunset_tilt missing from venetian schema"


def test_default_tilt_absent_in_blind_schema() -> None:
    """CONF_DEFAULT_TILT must not appear in the blind schema."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    assert CONF_DEFAULT_TILT not in keys, "default_tilt should not be in blind schema"


def test_sunset_tilt_absent_in_blind_schema() -> None:
    """CONF_SUNSET_TILT must not appear in the blind schema."""
    from custom_components.adaptive_cover_pro.const import CONF_SUNSET_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    assert CONF_SUNSET_TILT not in keys, "sunset_tilt should not be in blind schema"


# ---------------------------------------------------------------------------
# Per-slot custom name (issue #867) — optional label overriding the reason/
# decision_trace/card label everywhere. Cover-type-agnostic (no tilt gating).
# ---------------------------------------------------------------------------


def test_custom_position_name_key_in_schema_for_every_slot() -> None:
    """custom_position_name_N must be present in the custom-position schema
    for every configured slot, regardless of cover type.
    """
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert slot_keys["name"] in keys, f"{slot_keys['name']} missing from schema"


def test_custom_position_name_1_in_all_slots_schema() -> None:
    """Slot 1's name key is present verbatim (spot-check the literal key)."""
    from custom_components.adaptive_cover_pro.const import CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    assert "custom_position_name_1" in keys


def test_custom_position_name_in_optional_keys_round_trips_absent() -> None:
    """custom_position_name_N has no schema default, so a cleared text box
    must round-trip to absent — it must be listed in
    _CUSTOM_POSITION_OPTIONAL_KEYS the same way "template" is.
    """
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS

    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert slot_keys["name"] in cf._CUSTOM_POSITION_OPTIONAL_KEYS


# ---------------------------------------------------------------------------
# Daytime gate (issue #632) — lives on the L2b BEHAVIOR step beside the
# sunset-timing options it overrides (relocated from positions in the #613 split).
# ---------------------------------------------------------------------------


def test_daytime_gate_keys_in_behavior_schema() -> None:
    from custom_components.adaptive_cover_pro.const import (
        CONF_DAYTIME_GATE_SENSORS,
        CONF_DAYTIME_GATE_TEMPLATE,
        CONF_DAYTIME_GATE_TEMPLATE_MODE,
    )

    keys = _schema_keys(cf.BEHAVIOR_SCHEMA)
    assert CONF_DAYTIME_GATE_SENSORS in keys
    assert CONF_DAYTIME_GATE_TEMPLATE in keys
    assert CONF_DAYTIME_GATE_TEMPLATE_MODE in keys
    # It is a timing boundary, not a % target — it must not leak into positions.
    assert CONF_DAYTIME_GATE_SENSORS not in _schema_keys(cf.POSITION_SCHEMA)
    # The gate replaces the astronomical boundary — it must not leak into L4 motion.
    assert CONF_DAYTIME_GATE_SENSORS not in _schema_keys(cf.AUTOMATION_SCHEMA)


def test_daytime_gate_template_is_optional_round_trips_absent() -> None:
    # The template has no schema default → voluptuous omits it when cleared, so it
    # must be in _BEHAVIOR_OPTIONAL_KEYS to round-trip as cleared (None).
    from custom_components.adaptive_cover_pro.const import CONF_DAYTIME_GATE_TEMPLATE

    assert CONF_DAYTIME_GATE_TEMPLATE in cf._BEHAVIOR_OPTIONAL_KEYS


def test_daytime_gate_sensors_default_empty_list() -> None:
    # The sensor list carries default=[] so a cleared multi-select round-trips as
    # [] (NOT None — None would be ambiguous). It must NOT be in the optional list.
    from custom_components.adaptive_cover_pro.const import CONF_DAYTIME_GATE_SENSORS

    marker = next(
        k for k in cf.BEHAVIOR_SCHEMA.schema if str(k) == CONF_DAYTIME_GATE_SENSORS
    )
    assert marker.default() == []
    assert CONF_DAYTIME_GATE_SENSORS not in cf._BEHAVIOR_OPTIONAL_KEYS


def test_daytime_gate_mode_default_is_shared_combine_default() -> None:
    from custom_components.adaptive_cover_pro.const import (
        CONF_DAYTIME_GATE_TEMPLATE_MODE,
        DEFAULT_TEMPLATE_COMBINE_MODE,
    )

    marker = next(
        k
        for k in cf.BEHAVIOR_SCHEMA.schema
        if str(k) == CONF_DAYTIME_GATE_TEMPLATE_MODE
    )
    assert marker.default() == DEFAULT_TEMPLATE_COMBINE_MODE


# ---------------------------------------------------------------------------
# Weather override master toggle (#719): the on/off switch must be the FIRST
# field of the weather schema and default to off for new covers.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Window-facing fields moved to the Geometry step (#778): azimuth, FOV left/
# right, and shaded distance now render on Geometry, not Sun Tracking. The
# behavioural sun-tracking settings (enable, elevation limits, blind spot) stay.
# ---------------------------------------------------------------------------


_WINDOW_FACING_TYPES = ["cover_blind", "cover_awning", "cover_tilt", "cover_venetian"]

_WINDOW_FACING_KEYS = ("set_azimuth", "fov_left", "fov_right", "distance_shaded_area")


@pytest.mark.parametrize("cover_type", _WINDOW_FACING_TYPES)
def test_window_facing_fields_on_geometry_step(cover_type) -> None:
    keys = _schema_keys(cf._get_geometry_schema(cover_type))
    for key in _WINDOW_FACING_KEYS:
        assert key in keys, f"{key} should be on the geometry step for {cover_type}"


@pytest.mark.parametrize("cover_type", _WINDOW_FACING_TYPES)
def test_window_facing_fields_not_on_sun_tracking_step(cover_type) -> None:
    keys = _schema_keys(cf._get_sun_tracking_schema(cover_type))
    for key in _WINDOW_FACING_KEYS:
        assert (
            key not in keys
        ), f"{key} must not be on the sun_tracking step for {cover_type}"


@pytest.mark.parametrize("cover_type", _WINDOW_FACING_TYPES)
def test_window_facing_keys_still_live_option_keys(cover_type) -> None:
    # Regression guard: moving the fields between steps must NOT drop them from
    # live_option_keys, or the options-service would reject azimuth/fov/distance.
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    live = get_policy(cover_type).live_option_keys()
    for key in _WINDOW_FACING_KEYS:
        assert key in live, f"{key} missing from live_option_keys for {cover_type}"


def test_geometry_field_order_places_azimuth_before_fov_before_distance() -> None:
    # Order on the geometry step: window dims → azimuth → fov L/R → distance.
    keys = [str(k) for k in cf._get_geometry_schema("cover_blind").schema]
    assert keys.index("set_azimuth") < keys.index("fov_left")
    assert keys.index("fov_left") < keys.index("fov_right")
    assert keys.index("fov_right") < keys.index("distance_shaded_area")


def test_weather_enabled_is_first_key_with_default_false() -> None:
    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )
    from custom_components.adaptive_cover_pro.const import CONF_WEATHER_ENABLED

    schema = weather_override_schema()
    first_key = next(iter(schema.schema))
    assert str(first_key) == CONF_WEATHER_ENABLED
    assert first_key.default() is False


# ---------------------------------------------------------------------------
# Proxy-cover toggle on the cover-entity step
#
# Folded in from the former test_config_flow_proxy_cover.py.
# ---------------------------------------------------------------------------


def _schema_defaults(schema) -> dict[str, object]:
    """Return {key_name: default_value} for every Optional key in ``schema``."""
    out: dict[str, object] = {}
    for key in schema.schema:
        if isinstance(key, vol.Optional):
            default = key.default
            out[str(key)] = default() if callable(default) else default
    return out


def test_cover_entity_schema_contains_enable_proxy_cover_field() -> None:
    """``_build_cover_entity_schema`` exposes the opt-in toggle."""
    schema = cf._build_cover_entity_schema(CoverType.BLIND)
    assert CONF_ENABLE_PROXY_COVER in [str(k) for k in schema.schema]


def test_proxy_cover_defaults_to_false() -> None:
    """The toggle defaults to the DEFAULT_ENABLE_PROXY_COVER value (False)."""
    defaults = _schema_defaults(cf._build_cover_entity_schema(CoverType.BLIND))
    assert (
        defaults.get(CONF_ENABLE_PROXY_COVER) is DEFAULT_ENABLE_PROXY_COVER
    ), f"expected default False; got {defaults!r}"


def test_proxy_cover_schema_validates_boolean_round_trip() -> None:
    """User input of ``True`` round-trips through the schema."""
    schema = cf._build_cover_entity_schema(CoverType.BLIND)
    out = schema({CONF_ENTITIES: [], CONF_ENABLE_PROXY_COVER: True})
    assert out[CONF_ENABLE_PROXY_COVER] is True


# ---------------------------------------------------------------------------
# Outdoor-temp source selector on the climate step (issue #547)
#
# Folded in from the former test_config_flow_outside_temp_source.py.
# ---------------------------------------------------------------------------


def _key(schema, name: str):
    return next((k for k in schema.schema if str(k) == name), None)


def test_schema_contains_outside_temp_source_with_default_live() -> None:
    key = _key(temperature_climate_schema(), CONF_OUTSIDE_TEMP_SOURCE)
    assert key is not None, "CONF_OUTSIDE_TEMP_SOURCE missing from climate schema"
    assert key.default() == OutsideTempSource.LIVE.value


def test_selector_offers_three_options() -> None:
    schema = temperature_climate_schema()
    selector = schema.schema[_key(schema, CONF_OUTSIDE_TEMP_SOURCE)]
    raw_options = selector.config["options"]
    option_values = {(o["value"] if isinstance(o, dict) else o) for o in raw_options}
    assert option_values == {"live", "forecast_max", "max_of_live_and_forecast"}


# ---------------------------------------------------------------------------
# Solar transmittance (#1236): five optional fields render on the geometry step
# for EVERY cover type — the estimate is a property of the window assembly, not
# of a particular drive mechanism, so there is no per-policy schema branch.
# ---------------------------------------------------------------------------


_SOLAR_PROPERTY_KEYS = (
    "solar_properties_enabled",
    "solar_cover_side",
    "solar_cover_shade",
    "solar_g_total",
    "solar_g_glazing",
)

_ALL_GEOMETRY_TYPES = [
    "cover_blind",
    "cover_awning",
    "cover_oscillating_awning",
    "cover_tilt",
    "cover_venetian",
    "cover_roof_window",
    "cover_sliding_curtain",
    "cover_louvered_roof",
    "cover_day_night_shade",
    "cover_dual_panel",
]


@pytest.mark.parametrize("cover_type", _ALL_GEOMETRY_TYPES)
def test_solar_property_fields_on_geometry_step(cover_type) -> None:
    keys = _schema_keys(cf._get_geometry_schema(cover_type))
    for key in _SOLAR_PROPERTY_KEYS:
        assert key in keys, f"{key} should be on the geometry step for {cover_type}"


@pytest.mark.parametrize("cover_type", _ALL_GEOMETRY_TYPES)
def test_solar_property_keys_are_live_option_keys(cover_type) -> None:
    # Rendering the field is not enough: the options service rejects any key
    # the policy does not advertise, so the section builder must carry them too.
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    live = get_policy(cover_type).live_option_keys()
    for key in _SOLAR_PROPERTY_KEYS:
        assert key in live, f"{key} missing from live_option_keys for {cover_type}"


@pytest.mark.parametrize(
    "conf_key, expected_translation_key",
    [
        ("solar_cover_side", "solar_cover_side"),
        ("solar_cover_shade", "solar_cover_shade"),
    ],
)
def test_solar_selects_use_selector_with_translation_key(
    conf_key: str, expected_translation_key: str
) -> None:
    """Both solar selects must render translated labels, not raw enum values."""
    from homeassistant.helpers import selector as ha_selector

    schema = cf._get_geometry_schema("cover_blind")
    marker = next(k for k in schema.schema if str(k) == conf_key)
    value_validator = schema.schema[marker]
    assert isinstance(value_validator, ha_selector.SelectSelector)
    assert value_validator.config.get("translation_key") == expected_translation_key


def test_solar_master_toggle_defaults_off() -> None:
    """Absent ⇒ off ⇒ the whole feature is inert on an untouched install."""
    schema = cf._get_geometry_schema("cover_blind")
    marker = next(k for k in schema.schema if str(k) == "solar_properties_enabled")
    assert marker.default() is False


def test_solar_g_total_has_no_default_so_unset_round_trips() -> None:
    """The optional override must stay absent until the user sets it (#1267)."""
    import voluptuous as vol_

    schema = cf._get_geometry_schema("cover_blind")
    marker = next(k for k in schema.schema if str(k) == "solar_g_total")
    assert isinstance(marker, vol_.Optional)
    assert marker.default is vol_.UNDEFINED
    assert "solar_g_total" not in schema({})


def test_solar_g_glazing_defaults_to_the_shared_glazing_constant() -> None:
    from custom_components.adaptive_cover_pro.const import DEFAULT_SOLAR_G_GLAZING

    schema = cf._get_geometry_schema("cover_blind")
    marker = next(k for k in schema.schema if str(k) == "solar_g_glazing")
    assert marker.default() == DEFAULT_SOLAR_G_GLAZING


def test_solar_fields_render_after_the_window_facing_fields() -> None:
    # The window's own geometry comes first; the optional material description
    # is appended, so the pinned azimuth → fov → distance order is untouched.
    keys = [str(k) for k in cf._get_geometry_schema("cover_blind").schema]
    assert keys.index("distance_shaded_area") < keys.index("solar_properties_enabled")


# ---------------------------------------------------------------------------
# Estimated solar gain (#1237): the glazed-area override sits with the other
# solar fields on the geometry step (it is a property of the window, so it
# renders for every cover type — including the ones whose geometry cannot
# derive an area, which is exactly who needs the override). The plane select
# sits with the irradiance entity it describes, on the light & cloud step.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _ALL_GEOMETRY_TYPES)
def test_glass_area_on_geometry_step(cover_type) -> None:
    keys = _schema_keys(cf._get_geometry_schema(cover_type))
    assert "glass_area" in keys


@pytest.mark.parametrize("cover_type", _ALL_GEOMETRY_TYPES)
def test_glass_area_is_a_live_option_key(cover_type) -> None:
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    assert "glass_area" in get_policy(cover_type).live_option_keys()


def test_glass_area_has_no_default_so_unset_round_trips() -> None:
    """Blank means "derive from geometry", so it must stay absent (#1267)."""
    import voluptuous as vol_

    schema = cf._get_geometry_schema("cover_blind")
    marker = next(k for k in schema.schema if str(k) == "glass_area")
    assert isinstance(marker, vol_.Optional)
    assert marker.default is vol_.UNDEFINED
    assert "glass_area" not in schema({})


def test_glass_area_renders_with_the_other_solar_fields() -> None:
    keys = [str(k) for k in cf._get_geometry_schema("cover_blind").schema]
    assert keys.index("solar_properties_enabled") < keys.index("glass_area")


def test_irradiance_plane_on_light_cloud_step() -> None:
    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema

    assert "irradiance_plane" in _schema_keys(light_cloud_schema())


def test_irradiance_plane_renders_beside_the_irradiance_entity() -> None:
    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema

    keys = [str(k) for k in light_cloud_schema().schema]
    assert keys.index("irradiance_plane") == keys.index("irradiance_entity") + 1


def test_irradiance_plane_uses_a_selector_with_a_translation_key() -> None:
    from homeassistant.helpers import selector as ha_selector

    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema

    schema = light_cloud_schema()
    marker = next(k for k in schema.schema if str(k) == "irradiance_plane")
    validator = schema.schema[marker]
    assert isinstance(validator, ha_selector.SelectSelector)
    assert validator.config.get("translation_key") == "irradiance_plane"


def test_irradiance_plane_defaults_to_horizontal() -> None:
    """Most users own a flat-mounted pyranometer, so that is the safe default."""
    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema
    from custom_components.adaptive_cover_pro.const import DEFAULT_IRRADIANCE_PLANE

    schema = light_cloud_schema()
    marker = next(k for k in schema.schema if str(k) == "irradiance_plane")
    assert marker.default() == DEFAULT_IRRADIANCE_PLANE == "horizontal"


def test_irradiance_plane_is_a_live_option_key() -> None:
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    assert "irradiance_plane" in get_policy("cover_blind").live_option_keys()


def test_irradiance_plane_is_not_on_the_geometry_step() -> None:
    assert "irradiance_plane" not in _schema_keys(
        cf._get_geometry_schema("cover_blind")
    )


# ---------------------------------------------------------------------------
# Weather-override slat angle (#1297): a second axis only exists on venetians,
# so the slider is opt-in per cover type. ``cover_tilt`` deliberately does NOT
# get it — its primary axis IS the slat, so weather_override_position already
# sets the angle and a second field would contradict the first.
# ---------------------------------------------------------------------------


def test_weather_override_tilt_slider_only_with_include_tilt() -> None:
    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )

    assert CONF_WEATHER_OVERRIDE_TILT not in _schema_keys(weather_override_schema())
    assert CONF_WEATHER_OVERRIDE_TILT in _schema_keys(
        weather_override_schema(include_tilt=True)
    )


def test_weather_override_tilt_has_no_default_so_unset_round_trips() -> None:
    """Blank means "leave the slats alone", so it must stay absent (#1267).

    A bare ``vol.Optional`` with no default is what makes voluptuous omit the
    key from ``user_input`` when the user clears the slider, which in turn is
    what lets ``optional_entities`` null it. Give this marker a default and the
    field becomes uncleanable. The end-to-end round trip through the real
    options flow is pinned by ``test_weather_override_tilt_saves_and_clears``
    in ``tests/test_config_flow_integration.py`` — this schema cannot be
    ``__call__``-ed here, because a sibling threshold field validates against
    the event loop.
    """
    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )

    schema = weather_override_schema(include_tilt=True)
    marker = next(k for k in schema.schema if str(k) == CONF_WEATHER_OVERRIDE_TILT)
    assert isinstance(marker, vol.Optional)
    assert marker.default is vol.UNDEFINED


def test_weather_override_tilt_is_a_percentage_slider() -> None:
    from homeassistant.helpers import selector as ha_selector

    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )

    schema = weather_override_schema(include_tilt=True)
    sel = next(
        v for k, v in schema.schema.items() if str(k) == CONF_WEATHER_OVERRIDE_TILT
    )
    assert isinstance(sel, ha_selector.NumberSelector)
    assert sel.config["min"] == 0
    assert sel.config["max"] == 100
    assert sel.config["unit_of_measurement"] == "%"


def test_weather_override_tilt_renders_between_position_and_min_mode() -> None:
    """Form order follows meaning: what to set, then how to apply it."""
    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )

    keys = [str(k) for k in weather_override_schema(include_tilt=True).schema]
    assert (
        keys.index("weather_override_position")
        < keys.index(CONF_WEATHER_OVERRIDE_TILT)
        < keys.index("weather_override_min_mode")
    )


@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        (CoverType.VENETIAN, True),
        (CoverType.BLIND, False),
        (CoverType.TILT, False),
        ("cover_day_night_shade", False),
    ],
)
def test_weather_override_tilt_is_live_option_key_for_venetian_only(
    cover_type, expected
) -> None:
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    live = get_policy(cover_type).live_option_keys()
    assert (CONF_WEATHER_OVERRIDE_TILT in live) is expected
