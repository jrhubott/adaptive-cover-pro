"""Services for mutating config_entry.options at runtime (Issue #221).

Each service accepts a target (cover entity_id) and a set of option fields to update.
Changes are persisted to config_entry.options; the existing update listener performs
a full reload so all state-change listeners and pipeline handlers pick up new values.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import ServiceCall, ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import (
    BLANK_TIME,
    BLIND_SPOT_ELEVATION_MODES,
    BLIND_SPOT_SLOTS,
    CONF_ARM_LENGTH,
    CONF_AWNING_ANGLE,
    CONF_AWNING_HOUSING_OFFSET,
    CONF_AWNING_MAX_ANGLE,
    CONF_AWNING_MIN_ANGLE,
    CONF_AWNING_PIVOT_OFFSET,
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_CLIMATE_PRIORITY,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUD_SUPPRESSION_PRIORITY,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_SUN_TRACKING,
    CONF_ENDPOINT_USE_OPEN_CLOSE,
    CONF_END_ENTITY,
    CONF_END_OF_WINDOW_POS,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_GLARE_ZONE_PRIORITY,
    CONF_GROUP_STAGGER_DELAY,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_IS_SUNNY_TEMPLATE_MODE,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_IGNORE_EXTERNAL,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_PRIORITY,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MAX_SLAT_ANGLE,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_MODE,
    CONF_MOTION_MEDIA_PLAYERS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TEMPLATE,
    CONF_MOTION_TEMPLATE_MODE,
    CONF_MOTION_TIMEOUT,
    CONF_MOTION_TIMEOUT_PRIORITY,
    CONF_MY_POSITION_VALUE,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_POSITION_TOLERANCE,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_PRESENCE_TEMPLATE_MODE,
    CONF_RETURN_SUNSET,
    CONF_ROOF_HEIGHT_ABOVE,
    CONF_ROOF_PITCH,
    CONF_SILL_HEIGHT,
    CONF_SLIDING_ENABLE_SHADE_AREA,
    CONF_SLIDING_POINT1_X,
    CONF_SLIDING_POINT1_Y,
    CONF_SLIDING_POINT2_X,
    CONF_SLIDING_POINT2_Y,
    CONF_SLIDING_SLIDE_DIRECTION,
    CONF_SOLAR_PRIORITY,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_SUNSET_TILT,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_MIN_TILT,
    CONF_MIN_TILT_SUN_ONLY,
    CONF_TILT_ANGLE_0,
    CONF_TILT_ANGLE_100,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    CONF_VENETIAN_MODE,
    CONF_VENETIAN_POST_SETTLE_HOLD,
    CONF_VENETIAN_POST_SETTLE_MODE,
    CONF_VENETIAN_TILT_RESET_DIRECTION,
    CONF_VENETIAN_TILT_RESET_SCOPE,
    CONF_VENETIAN_TILT_RESET_THRESHOLD,
    CONF_VENETIAN_TILT_SAFETY_MARGIN,
    CONF_VENETIAN_TILT_SKIP_ABOVE,
    CONF_VENETIAN_TILT_SKIP_MODE,
    SLIDING_SLIDE_DIRECTIONS,
    VENETIAN_MODES,
    VENETIAN_POST_SETTLE_MODES,
    VENETIAN_TILT_RESET_DIRECTIONS,
    VENETIAN_TILT_RESET_SCOPES,
    VENETIAN_TILT_SKIP_MODES,
    TemplateCombineMode,
    CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_RAINING_TEMPLATE,
    CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
    CONF_WEATHER_OVERRIDE_MIN_MODE,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_PRIORITY,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_STATE,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    CONF_WINTER_CLOSE_INSULATION,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOT_NUMBERS,
    CUSTOM_POSITION_SLOTS,
    DOMAIN,
    OPTION_RANGES,
)
from ..helpers import custom_position_slot_sensors
from ..templates import is_template_string as _is_template_str

_LOGGER = logging.getLogger(__name__)

# Keys that cannot be mutated via services (identity + install-time structural)
IDENTITY_KEYS: frozenset[str] = frozenset(
    {"name", CONF_MODE, CONF_ENTITIES, CONF_DEVICE_ID}
)

# HA service call plumbing keys to strip when building a patch
_PLUMBING_KEYS: frozenset[str] = frozenset({"entity_id", "device_id", "area_id"})

_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

# ---------------------------------------------------------------------------
# Per-field validators
# ---------------------------------------------------------------------------


def _num(min_val: float, max_val: float):
    return vol.Any(
        None, vol.All(vol.Coerce(float), vol.Range(min=min_val, max=max_val))
    )


def _range(key: str):
    """Build the numeric validator for ``key`` from the canonical range in const.py.

    Replaces hand-coded ``_num(min, max)`` literals so a future change to a
    range tightens both this validator and the matching UI selector in
    ``config_flow.py`` in one edit.
    """
    return _num(*OPTION_RANGES[key])


def _as_number(value: Any) -> float | None:
    """Coerce *value* to a float for cross-field comparison, or None.

    Returns None for templates (unresolvable here) and non-numeric values, so
    callers skip ordering checks they cannot evaluate.
    """
    if value is None or _is_template_str(value):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _templatable_num(key: str):
    """Build a validator for ``None``, a number, or a Jinja2 template (#577).

    A plain number (or numeric string) is validated as a number — bounded by
    ``OPTION_RANGES[key]`` when the key has a declared range, unbounded
    otherwise. A string containing ``{{``/``{%`` is accepted as a template after
    a syntax check; it renders to a number at runtime via
    ``templates.TemplateResolver``.
    """
    number = _range(key) if key in OPTION_RANGES else vol.Any(None, vol.Coerce(float))

    def _validate(value):
        if _is_template_str(value):
            return _check_template_syntax(value)
        return number(value)

    return _validate


def _check_template_syntax(value: str) -> str:
    """Raise ``vol.Invalid`` if *value* is not syntactically valid Jinja2.

    Syntax-gate via jinja2 directly — a bare HA ``Template`` here would trip the
    frame helper (no hass at validation time) and log a usage warning. Semantic
    rendering happens later at runtime.
    """
    import jinja2

    try:
        jinja2.Environment().parse(value)
    except jinja2.TemplateError as err:
        raise vol.Invalid(f"Invalid template: {err}") from err
    return value


def _template_or_none(value):
    """Validate an optional *condition* template field (#577 follow-up).

    Accepts ``None`` / empty (cleared), or a syntactically valid Jinja2 template
    string. Unlike ``_templatable_num`` this never coerces to a number — the
    value is rendered to a boolean at runtime by ``templates.render_condition``.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise vol.Invalid("expected a template string")
    return _check_template_syntax(value)


def _bool_v():
    return vol.Any(None, bool)


def _entity_v():
    return vol.Any(None, str)


def _entities_v():
    return vol.Any(None, [str])


def _duration_v():
    return vol.Any(None, dict)


def _time_v():
    def _check(v):
        if v is not None and not _TIME_RE.match(str(v)):
            raise vol.Invalid(f"Time must be HH:MM:SS, got: {v!r}")
        return v

    return vol.Any(None, _check)


def _select_v(*options: str):
    return vol.Any(None, vol.In(list(options)))


# Maps option key → validator callable. Used by validate_options_patch and set_option.
# Numeric ranges live in ``const.OPTION_RANGES`` (single source of truth shared
# with config_flow.py); ``_range(key)`` reads from there. Per-slot custom-position
# validators are generated at the bottom from ``CUSTOM_POSITION_SLOTS``.
FIELD_VALIDATORS: dict[str, Any] = {
    # Geometry — vertical blind
    CONF_HEIGHT_WIN: _range(CONF_HEIGHT_WIN),
    CONF_WINDOW_WIDTH: _range(CONF_WINDOW_WIDTH),
    CONF_WINDOW_DEPTH: _range(CONF_WINDOW_DEPTH),
    CONF_SILL_HEIGHT: _range(CONF_SILL_HEIGHT),
    # Geometry — awning
    CONF_LENGTH_AWNING: _range(CONF_LENGTH_AWNING),
    CONF_AWNING_ANGLE: _range(CONF_AWNING_ANGLE),
    # Geometry — oscillating (drop-arm) awning (#412)
    CONF_ARM_LENGTH: _range(CONF_ARM_LENGTH),
    CONF_AWNING_MIN_ANGLE: _range(CONF_AWNING_MIN_ANGLE),
    CONF_AWNING_MAX_ANGLE: _range(CONF_AWNING_MAX_ANGLE),
    CONF_AWNING_HOUSING_OFFSET: _range(CONF_AWNING_HOUSING_OFFSET),
    CONF_AWNING_PIVOT_OFFSET: _range(CONF_AWNING_PIVOT_OFFSET),
    # Geometry — roof / skylight window (#212)
    CONF_ROOF_PITCH: _range(CONF_ROOF_PITCH),
    CONF_ROOF_HEIGHT_ABOVE: _range(CONF_ROOF_HEIGHT_ABOVE),
    # Geometry — louvered roof (#830 follow-up)
    CONF_MAX_SLAT_ANGLE: _range(CONF_MAX_SLAT_ANGLE),
    # Geometry — sliding curtain shade area (#829, Part 2)
    CONF_SLIDING_ENABLE_SHADE_AREA: _bool_v(),
    CONF_SLIDING_SLIDE_DIRECTION: _select_v(*SLIDING_SLIDE_DIRECTIONS),
    CONF_SLIDING_POINT1_X: _range(CONF_SLIDING_POINT1_X),
    CONF_SLIDING_POINT1_Y: _range(CONF_SLIDING_POINT1_Y),
    CONF_SLIDING_POINT2_X: _range(CONF_SLIDING_POINT2_X),
    CONF_SLIDING_POINT2_Y: _range(CONF_SLIDING_POINT2_Y),
    # Geometry — tilt/venetian
    CONF_TILT_DEPTH: _range(CONF_TILT_DEPTH),
    CONF_TILT_DISTANCE: _range(CONF_TILT_DISTANCE),
    CONF_TILT_MODE: _select_v("mode1", "mode2", "specify_angles"),
    CONF_TILT_ANGLE_0: _range(CONF_TILT_ANGLE_0),
    CONF_TILT_ANGLE_100: _range(CONF_TILT_ANGLE_100),
    CONF_MAX_TILT: _range(CONF_MAX_TILT),
    CONF_MAX_TILT_SUN_ONLY: _bool_v(),
    CONF_MIN_TILT: _range(CONF_MIN_TILT),
    CONF_MIN_TILT_SUN_ONLY: _bool_v(),
    # Venetian-specific options
    CONF_VENETIAN_TILT_SAFETY_MARGIN: _range(CONF_VENETIAN_TILT_SAFETY_MARGIN),
    CONF_VENETIAN_POST_SETTLE_HOLD: _range(CONF_VENETIAN_POST_SETTLE_HOLD),
    CONF_VENETIAN_POST_SETTLE_MODE: _select_v(*VENETIAN_POST_SETTLE_MODES),
    CONF_VENETIAN_TILT_SKIP_ABOVE: _range(CONF_VENETIAN_TILT_SKIP_ABOVE),
    CONF_VENETIAN_TILT_SKIP_MODE: _select_v(*VENETIAN_TILT_SKIP_MODES),
    CONF_VENETIAN_TILT_RESET_THRESHOLD: _range(CONF_VENETIAN_TILT_RESET_THRESHOLD),
    CONF_VENETIAN_TILT_RESET_DIRECTION: _select_v(*VENETIAN_TILT_RESET_DIRECTIONS),
    CONF_VENETIAN_TILT_RESET_SCOPE: _select_v(*VENETIAN_TILT_RESET_SCOPES),
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG: _range(CONF_VENETIAN_BACKROTATE_PUBLISH_LAG),
    CONF_VENETIAN_MODE: _select_v(*VENETIAN_MODES),
    # Sun tracking
    CONF_ENABLE_SUN_TRACKING: _bool_v(),
    CONF_AZIMUTH: _range(CONF_AZIMUTH),
    CONF_FOV_LEFT: _range(CONF_FOV_LEFT),
    CONF_FOV_RIGHT: _range(CONF_FOV_RIGHT),
    CONF_MIN_ELEVATION: _range(CONF_MIN_ELEVATION),
    CONF_MAX_ELEVATION: _range(CONF_MAX_ELEVATION),
    CONF_DISTANCE: _range(CONF_DISTANCE),
    CONF_MINIMIZE_MOVEMENTS: _bool_v(),
    CONF_MAX_COVERAGE_STEPS: _range(CONF_MAX_COVERAGE_STEPS),
    # Blind spot — master enable plus per-slot left/right/elevation ranges
    # (issue #701). Slot 1 reuses the legacy unsuffixed keys; slots 2/3 are
    # suffixed. Every slot pulls its range from OPTION_RANGES.
    CONF_ENABLE_BLIND_SPOT: _bool_v(),
    **{
        keys[sub]: _range(keys[sub])
        for keys in BLIND_SPOT_SLOTS.values()
        for sub in ("left", "right", "elevation")
    },
    # Per-slot elevation mode is a below/above select, not a numeric range.
    **{
        keys["elevation_mode"]: _select_v(*BLIND_SPOT_ELEVATION_MODES)
        for keys in BLIND_SPOT_SLOTS.values()
    },
    # Position limits & sunset/sunrise
    CONF_DEFAULT_HEIGHT: _range(CONF_DEFAULT_HEIGHT),
    CONF_MAX_POSITION: _range(CONF_MAX_POSITION),
    CONF_ENABLE_MAX_POSITION: _bool_v(),
    CONF_MIN_POSITION: _range(CONF_MIN_POSITION),
    CONF_ENABLE_MIN_POSITION: _bool_v(),
    CONF_ENDPOINT_USE_OPEN_CLOSE: _bool_v(),
    CONF_ENABLE_POSITION_MATCHING: _bool_v(),
    CONF_MIN_POSITION_SUN_TRACKING: _range(CONF_MIN_POSITION_SUN_TRACKING),
    CONF_SUNSET_POS: _range(CONF_SUNSET_POS),
    CONF_END_OF_WINDOW_POS: _range(CONF_END_OF_WINDOW_POS),
    CONF_MY_POSITION_VALUE: _range(CONF_MY_POSITION_VALUE),
    CONF_SUNSET_USE_MY: _bool_v(),
    CONF_SUNSET_OFFSET: _range(CONF_SUNSET_OFFSET),
    CONF_SUNRISE_OFFSET: _range(CONF_SUNRISE_OFFSET),
    CONF_SUNSET_TIME_ENTITY: _entity_v(),
    CONF_SUNRISE_TIME_ENTITY: _entity_v(),
    CONF_OPEN_CLOSE_THRESHOLD: _range(CONF_OPEN_CLOSE_THRESHOLD),
    CONF_INVERSE_STATE: _bool_v(),
    # Explicit tilt (venetian only) — None means use solar-computed tilt.
    CONF_DEFAULT_TILT: _range(CONF_DEFAULT_TILT),
    CONF_SUNSET_TILT: _range(CONF_SUNSET_TILT),
    CONF_INTERP: _bool_v(),
    # Interpolation
    CONF_INTERP_START: _range(CONF_INTERP_START),
    CONF_INTERP_END: _range(CONF_INTERP_END),
    CONF_INTERP_LIST: vol.Any(None, list),
    CONF_INTERP_LIST_NEW: vol.Any(None, list),
    # Automation timing
    CONF_DELTA_POSITION: _range(CONF_DELTA_POSITION),
    CONF_DELTA_TIME: _range(CONF_DELTA_TIME),
    CONF_POSITION_TOLERANCE: _range(CONF_POSITION_TOLERANCE),
    CONF_START_TIME: _time_v(),
    CONF_START_ENTITY: _entity_v(),
    CONF_END_TIME: _time_v(),
    CONF_END_ENTITY: _entity_v(),
    CONF_RETURN_SUNSET: _bool_v(),
    # Manual override
    CONF_MANUAL_OVERRIDE_DURATION: _duration_v(),
    CONF_MANUAL_OVERRIDE_RESET: _bool_v(),
    CONF_MANUAL_THRESHOLD: _range(CONF_MANUAL_THRESHOLD),
    CONF_MANUAL_IGNORE_INTERMEDIATE: _bool_v(),
    CONF_MANUAL_IGNORE_EXTERNAL: _bool_v(),
    # Force override
    CONF_FORCE_OVERRIDE_SENSORS: _entities_v(),
    CONF_FORCE_OVERRIDE_POSITION: _range(CONF_FORCE_OVERRIDE_POSITION),
    CONF_FORCE_OVERRIDE_MIN_MODE: _bool_v(),
    # Custom positions 1–5 — sensor(s)/template/min_mode/use_my are non-numeric;
    # position/priority pull their range from OPTION_RANGES.
    **{
        slot_keys["sensor"]: _entity_v() for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["sensors"]: _entities_v()
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["template"]: _template_or_none
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["template_mode"]: _select_v(*[m.value for m in TemplateCombineMode])
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["position"]: _range(slot_keys["position"])
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["priority"]: _range(slot_keys["priority"])
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["min_mode"]: _bool_v() for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{slot_keys["use_my"]: _bool_v() for slot_keys in CUSTOM_POSITION_SLOTS.values()},
    **{
        slot_keys["tilt_only"]: _bool_v()
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{
        slot_keys["tilt"]: _range(slot_keys["tilt"])
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    },
    **{slot_keys["enabled"]: _bool_v() for slot_keys in CUSTOM_POSITION_SLOTS.values()},
    # Glare zones 1–4 — name is free-form text; x/y/radius/z pull ranges from
    # OPTION_RANGES (bounds mirror config_flow._build_glare_zones_schema).
    **{
        f"glare_zone_{i}_{axis}": _range(f"glare_zone_{i}_{axis}")
        for i in range(1, 5)
        for axis in ("x", "y", "radius", "z")
    },
    # Motion
    CONF_MOTION_SENSORS: _entities_v(),
    CONF_MOTION_MEDIA_PLAYERS: _entities_v(),
    CONF_MOTION_TEMPLATE: _template_or_none,
    CONF_MOTION_TEMPLATE_MODE: _select_v(*[m.value for m in TemplateCombineMode]),
    CONF_MOTION_TIMEOUT: _range(CONF_MOTION_TIMEOUT),
    # Light & Cloud
    CONF_WEATHER_ENTITY: _entity_v(),
    CONF_WEATHER_STATE: vol.Any(None, list),
    CONF_LUX_ENTITY: _entity_v(),
    CONF_LUX_THRESHOLD: _templatable_num(CONF_LUX_THRESHOLD),
    CONF_IRRADIANCE_ENTITY: _entity_v(),
    CONF_IRRADIANCE_THRESHOLD: _templatable_num(CONF_IRRADIANCE_THRESHOLD),
    CONF_CLOUD_COVERAGE_ENTITY: _entity_v(),
    CONF_CLOUD_COVERAGE_THRESHOLD: _templatable_num(CONF_CLOUD_COVERAGE_THRESHOLD),
    CONF_CLOUD_SUPPRESSION: _bool_v(),
    CONF_IS_SUNNY_SENSOR: _entity_v(),
    CONF_IS_SUNNY_TEMPLATE: _template_or_none,
    CONF_IS_SUNNY_TEMPLATE_MODE: _select_v(*[m.value for m in TemplateCombineMode]),
    # Climate
    CONF_CLIMATE_MODE: _bool_v(),
    CONF_TEMP_ENTITY: _entity_v(),
    CONF_TEMP_LOW: _templatable_num(CONF_TEMP_LOW),
    CONF_TEMP_HIGH: _templatable_num(CONF_TEMP_HIGH),
    CONF_OUTSIDETEMP_ENTITY: _entity_v(),
    CONF_OUTSIDE_THRESHOLD: _templatable_num(CONF_OUTSIDE_THRESHOLD),
    CONF_PRESENCE_ENTITY: _entity_v(),
    CONF_PRESENCE_TEMPLATE: _template_or_none,
    CONF_PRESENCE_TEMPLATE_MODE: _select_v(*[m.value for m in TemplateCombineMode]),
    CONF_TRANSPARENT_BLIND: _bool_v(),
    CONF_WINTER_CLOSE_INSULATION: _bool_v(),
    CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR: _bool_v(),
    # Weather safety
    CONF_WEATHER_BYPASS_AUTO_CONTROL: _bool_v(),
    CONF_WEATHER_WIND_SPEED_SENSOR: _entity_v(),
    CONF_WEATHER_WIND_DIRECTION_SENSOR: _entity_v(),
    CONF_WEATHER_WIND_SPEED_THRESHOLD: _templatable_num(
        CONF_WEATHER_WIND_SPEED_THRESHOLD
    ),
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE: _templatable_num(
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE
    ),
    CONF_WEATHER_RAIN_SENSOR: _entity_v(),
    CONF_WEATHER_RAIN_THRESHOLD: _templatable_num(CONF_WEATHER_RAIN_THRESHOLD),
    CONF_WEATHER_IS_RAINING_SENSOR: _entity_v(),
    CONF_WEATHER_IS_RAINING_TEMPLATE: _template_or_none,
    CONF_WEATHER_IS_RAINING_TEMPLATE_MODE: _select_v(
        *[m.value for m in TemplateCombineMode]
    ),
    CONF_WEATHER_IS_WINDY_SENSOR: _entity_v(),
    CONF_WEATHER_IS_WINDY_TEMPLATE: _template_or_none,
    CONF_WEATHER_IS_WINDY_TEMPLATE_MODE: _select_v(
        *[m.value for m in TemplateCombineMode]
    ),
    CONF_WEATHER_SEVERE_SENSORS: _entities_v(),
    CONF_WEATHER_OVERRIDE_POSITION: _range(CONF_WEATHER_OVERRIDE_POSITION),
    CONF_WEATHER_OVERRIDE_MIN_MODE: _bool_v(),
    CONF_WEATHER_TIMEOUT: _range(CONF_WEATHER_TIMEOUT),
    # Built-in handler priority overrides (1-99; clear to restore class default)
    CONF_WEATHER_PRIORITY: _range(CONF_WEATHER_PRIORITY),
    CONF_MANUAL_OVERRIDE_PRIORITY: _range(CONF_MANUAL_OVERRIDE_PRIORITY),
    CONF_MOTION_TIMEOUT_PRIORITY: _range(CONF_MOTION_TIMEOUT_PRIORITY),
    CONF_CLOUD_SUPPRESSION_PRIORITY: _range(CONF_CLOUD_SUPPRESSION_PRIORITY),
    CONF_CLIMATE_PRIORITY: _range(CONF_CLIMATE_PRIORITY),
    CONF_GLARE_ZONE_PRIORITY: _range(CONF_GLARE_ZONE_PRIORITY),
    CONF_SOLAR_PRIORITY: _range(CONF_SOLAR_PRIORITY),
    # Cover group (issue #790, Phase 2)
    CONF_GROUP_STAGGER_DELAY: _range(CONF_GROUP_STAGGER_DELAY),
}

# ---------------------------------------------------------------------------
# Section key sets (used for building service-call patches)
# ---------------------------------------------------------------------------

_SECTION_POSITION_LIMITS = frozenset(
    {
        CONF_DEFAULT_HEIGHT,
        CONF_MIN_POSITION,
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION_SUN_TRACKING,
        CONF_MAX_POSITION,
        CONF_ENABLE_MAX_POSITION,
        CONF_OPEN_CLOSE_THRESHOLD,
        CONF_ENABLE_POSITION_MATCHING,
        CONF_INVERSE_STATE,
    }
)

_SECTION_SUNSET_SUNRISE = frozenset(
    {
        CONF_SUNSET_POS,
        CONF_SUNSET_OFFSET,
        CONF_SUNRISE_OFFSET,
        CONF_SUNSET_USE_MY,
        CONF_MY_POSITION_VALUE,
        CONF_SUNSET_TIME_ENTITY,
        CONF_SUNRISE_TIME_ENTITY,
    }
)

_SECTION_AUTOMATION_TIMING = frozenset(
    {
        CONF_DELTA_POSITION,
        CONF_DELTA_TIME,
        CONF_START_TIME,
        CONF_START_ENTITY,
        CONF_END_TIME,
        CONF_END_ENTITY,
        CONF_RETURN_SUNSET,
    }
)

_SECTION_MANUAL_OVERRIDE = frozenset(
    {
        CONF_MANUAL_OVERRIDE_DURATION,
        CONF_MANUAL_OVERRIDE_RESET,
        CONF_MANUAL_THRESHOLD,
        CONF_MANUAL_IGNORE_INTERMEDIATE,
        CONF_MANUAL_IGNORE_EXTERNAL,
    }
)

_SECTION_FORCE_OVERRIDE = frozenset(
    {
        CONF_FORCE_OVERRIDE_SENSORS,
        CONF_FORCE_OVERRIDE_POSITION,
        CONF_FORCE_OVERRIDE_MIN_MODE,
    }
)

_SECTION_MOTION = frozenset(
    {CONF_MOTION_SENSORS, CONF_MOTION_MEDIA_PLAYERS, CONF_MOTION_TIMEOUT}
)

_SECTION_LIGHT_CLOUD = frozenset(
    {
        CONF_WEATHER_ENTITY,
        CONF_WEATHER_STATE,
        CONF_LUX_ENTITY,
        CONF_LUX_THRESHOLD,
        CONF_IRRADIANCE_ENTITY,
        CONF_IRRADIANCE_THRESHOLD,
        CONF_CLOUD_COVERAGE_ENTITY,
        CONF_CLOUD_COVERAGE_THRESHOLD,
        CONF_CLOUD_SUPPRESSION,
        CONF_IS_SUNNY_SENSOR,
        CONF_IS_SUNNY_TEMPLATE,
        CONF_IS_SUNNY_TEMPLATE_MODE,
    }
)

_SECTION_CLIMATE = frozenset(
    {
        CONF_CLIMATE_MODE,
        CONF_TEMP_ENTITY,
        CONF_TEMP_LOW,
        CONF_TEMP_HIGH,
        CONF_OUTSIDETEMP_ENTITY,
        CONF_OUTSIDE_THRESHOLD,
        CONF_PRESENCE_ENTITY,
        CONF_PRESENCE_TEMPLATE,
        CONF_PRESENCE_TEMPLATE_MODE,
        CONF_TRANSPARENT_BLIND,
        CONF_WINTER_CLOSE_INSULATION,
        CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    }
)

_SECTION_WEATHER_SAFETY = frozenset(
    {
        CONF_WEATHER_BYPASS_AUTO_CONTROL,
        CONF_WEATHER_WIND_SPEED_SENSOR,
        CONF_WEATHER_WIND_DIRECTION_SENSOR,
        CONF_WEATHER_WIND_SPEED_THRESHOLD,
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
        CONF_WEATHER_RAIN_SENSOR,
        CONF_WEATHER_RAIN_THRESHOLD,
        CONF_WEATHER_IS_RAINING_SENSOR,
        CONF_WEATHER_IS_RAINING_TEMPLATE,
        CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
        CONF_WEATHER_IS_WINDY_SENSOR,
        CONF_WEATHER_IS_WINDY_TEMPLATE,
        CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
        CONF_WEATHER_SEVERE_SENSORS,
        CONF_WEATHER_OVERRIDE_POSITION,
        CONF_WEATHER_OVERRIDE_MIN_MODE,
        CONF_WEATHER_TIMEOUT,
    }
)

_SECTION_SUN_TRACKING = frozenset(
    {
        CONF_ENABLE_SUN_TRACKING,
        CONF_AZIMUTH,
        CONF_FOV_LEFT,
        CONF_FOV_RIGHT,
        CONF_MIN_ELEVATION,
        CONF_MAX_ELEVATION,
        CONF_DISTANCE,
        CONF_MINIMIZE_MOVEMENTS,
        CONF_MAX_COVERAGE_STEPS,
    }
)

_SECTION_BLIND_SPOT = frozenset(
    {CONF_ENABLE_BLIND_SPOT}
    | {
        keys[sub]
        for keys in BLIND_SPOT_SLOTS.values()
        for sub in ("left", "right", "elevation", "elevation_mode")
    }
)

_SECTION_INTERPOLATION = frozenset(
    {
        CONF_INTERP,
        CONF_INTERP_START,
        CONF_INTERP_END,
        CONF_INTERP_LIST,
        CONF_INTERP_LIST_NEW,
    }
)

_SECTION_GEOMETRY_VERTICAL = frozenset(
    {CONF_HEIGHT_WIN, CONF_WINDOW_WIDTH, CONF_WINDOW_DEPTH, CONF_SILL_HEIGHT}
)
_SECTION_GEOMETRY_AWNING = frozenset(
    {CONF_LENGTH_AWNING, CONF_AWNING_ANGLE, CONF_HEIGHT_WIN}
)
_SECTION_GEOMETRY_TILT = frozenset(
    {
        CONF_TILT_DEPTH,
        CONF_TILT_DISTANCE,
        CONF_TILT_MODE,
        CONF_TILT_ANGLE_0,
        CONF_TILT_ANGLE_100,
    }
)
_SECTION_GEOMETRY_OSCILLATING = frozenset(
    {
        CONF_ARM_LENGTH,
        CONF_AWNING_MIN_ANGLE,
        CONF_AWNING_MAX_ANGLE,
        CONF_AWNING_HOUSING_OFFSET,
        CONF_AWNING_PIVOT_OFFSET,
    }
)
_SECTION_GEOMETRY_ROOF = frozenset(
    {CONF_ROOF_PITCH, CONF_ROOF_HEIGHT_ABOVE, CONF_MAX_SLAT_ANGLE}
)
_SECTION_GEOMETRY_SLIDING = frozenset(
    {
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_SLIDING_SLIDE_DIRECTION,
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
    }
)
_SECTION_GEOMETRY_ALL = (
    _SECTION_GEOMETRY_VERTICAL
    | _SECTION_GEOMETRY_AWNING
    | _SECTION_GEOMETRY_TILT
    | _SECTION_GEOMETRY_OSCILLATING
    | _SECTION_GEOMETRY_ROOF
    | _SECTION_GEOMETRY_SLIDING
)

_SECTION_VENETIAN = frozenset(
    {
        CONF_VENETIAN_POST_SETTLE_HOLD,
        CONF_VENETIAN_TILT_SKIP_ABOVE,
        CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
        CONF_VENETIAN_MODE,
    }
)

_SECTION_PIPELINE_PRIORITIES = frozenset(
    {
        CONF_WEATHER_PRIORITY,
        CONF_MANUAL_OVERRIDE_PRIORITY,
        CONF_MOTION_TIMEOUT_PRIORITY,
        CONF_CLOUD_SUPPRESSION_PRIORITY,
        CONF_CLIMATE_PRIORITY,
        CONF_GLARE_ZONE_PRIORITY,
        CONF_SOLAR_PRIORITY,
    }
)

# All settable keys (union of all sections)
ALL_SETTABLE_KEYS: frozenset[str] = (
    _SECTION_POSITION_LIMITS
    | _SECTION_SUNSET_SUNRISE
    | _SECTION_AUTOMATION_TIMING
    | _SECTION_MANUAL_OVERRIDE
    | _SECTION_FORCE_OVERRIDE
    | _SECTION_MOTION
    | _SECTION_LIGHT_CLOUD
    | _SECTION_CLIMATE
    | _SECTION_WEATHER_SAFETY
    | _SECTION_SUN_TRACKING
    | _SECTION_BLIND_SPOT
    | _SECTION_INTERPOLATION
    | _SECTION_GEOMETRY_ALL
    | _SECTION_VENETIAN
    | _SECTION_PIPELINE_PRIORITIES
    | frozenset(v for keys in CUSTOM_POSITION_SLOTS.values() for v in keys.values())
)

# Local alias kept for readability at the per-slot iteration sites below; the
# canonical map lives in const.CUSTOM_POSITION_SLOTS.
_CUSTOM_SLOT_KEYS = CUSTOM_POSITION_SLOTS

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


# Service field names that differ from their internal option key. The canonical
# service field is now `default_percentage`, matching CONF_DEFAULT_HEIGHT; the
# older `default_height` wire-format name is kept as a deprecated alias so
# existing automations keep working (issue #792).
_SERVICE_FIELD_ALIASES: dict[str, str] = {
    "default_height": CONF_DEFAULT_HEIGHT,
}


def _build_patch(call_data: dict, allowed_keys: frozenset[str]) -> dict:
    """Extract allowed keys from a service call's data dict.

    Any known field-name alias (_SERVICE_FIELD_ALIASES) is resolved to its
    internal option key before filtering. Keys whose value is None are included
    (they signal "clear this option"). HA plumbing keys (entity_id, device_id,
    area_id) are always excluded.
    """
    patch: dict = {}
    for k, v in call_data.items():
        key = _SERVICE_FIELD_ALIASES.get(k, k)
        if key in allowed_keys and key not in _PLUMBING_KEYS:
            patch[key] = v
    return patch


def _validate_fields(patch: dict) -> None:
    """Validate each field in *patch* using FIELD_VALIDATORS.

    Raises ServiceValidationError on the first invalid field.
    """
    for key, value in patch.items():
        validator = FIELD_VALIDATORS.get(key)
        if validator is None:
            raise ServiceValidationError(
                f"Option '{key}' is not supported by this service."
            )
        try:
            validator(value)
        except vol.Invalid as exc:
            raise ServiceValidationError(
                f"Invalid value for '{key}': {exc.msg} (got {value!r})"
            ) from exc


def _cross_field_validate(
    patch: dict, current: dict, *, check_slot_completeness: bool = True
) -> None:
    """Validate cross-field invariants on the merged options.

    Only checks invariants that involve at least one key present in *patch*
    so that unrelated existing options don't produce false errors.
    """
    merged = {**current, **patch}
    # Remove keys explicitly cleared (value=None) from the merged view
    merged_active = {k: v for k, v in merged.items() if v is not None}

    # Blind spot ordering — one check per slot (issue #701). Slot 1 uses the
    # legacy unsuffixed keys; slots 2/3 are suffixed.
    for keys in BLIND_SPOT_SLOTS.values():
        left_key = keys["left"]
        right_key = keys["right"]
        if left_key in patch or right_key in patch:
            left = merged_active.get(left_key)
            right = merged_active.get(right_key)
            if left is not None and right is not None and right <= left:
                raise ServiceValidationError(
                    f"{right_key} ({right}) must be greater than {left_key} ({left})."
                )

    # Temperature ordering (skipped when either side is a template — #577)
    if CONF_TEMP_LOW in patch or CONF_TEMP_HIGH in patch:
        low = _as_number(merged_active.get(CONF_TEMP_LOW))
        high = _as_number(merged_active.get(CONF_TEMP_HIGH))
        if low is not None and high is not None and low >= high:
            raise ServiceValidationError(
                f"temp_low ({low}) must be less than temp_high ({high})."
            )

    # Specify-angles endpoint ordering.
    if CONF_TILT_ANGLE_0 in patch or CONF_TILT_ANGLE_100 in patch:
        angle_0 = _as_number(merged_active.get(CONF_TILT_ANGLE_0))
        angle_100 = _as_number(merged_active.get(CONF_TILT_ANGLE_100))
        if angle_0 is not None and angle_100 is not None and angle_0 >= angle_100:
            raise ServiceValidationError(
                f"tilt_angle_0 ({angle_0}) must be less than tilt_angle_100 ({angle_100})."
            )

    # Custom position slot completeness: a slot needs a trigger (sensors,
    # legacy sensor, or template) AND a position — or neither.
    for i in CUSTOM_POSITION_SLOT_NUMBERS if check_slot_completeness else ():
        slot = _CUSTOM_SLOT_KEYS[i]
        trigger_keys = (slot["sensor"], slot["sensors"], slot["template"])
        p_key = slot["position"]
        if any(k in patch for k in trigger_keys) or p_key in patch:
            has_trigger = bool(
                custom_position_slot_sensors(merged_active, slot)
            ) or _is_template_str(merged_active.get(slot["template"]))
            pos_set = merged_active.get(p_key) is not None
            if has_trigger != pos_set:
                missing = (
                    p_key if has_trigger else f"{slot['sensors']} or {slot['template']}"
                )
                raise ServiceValidationError(
                    f"Custom position slot {i}: incomplete — '{missing}' is missing. "
                    "Set a trigger and a position, or clear both."
                )

    # Time window mutual exclusion
    if CONF_START_TIME in patch or CONF_START_ENTITY in patch:
        st = merged_active.get(CONF_START_TIME)
        se = merged_active.get(CONF_START_ENTITY)
        if st and st != BLANK_TIME and se:
            raise ServiceValidationError(
                f"start_time ('{st}') and start_entity ('{se}') are mutually exclusive. "
                "Set one or the other, not both."
            )

    if CONF_END_TIME in patch or CONF_END_ENTITY in patch:
        et = merged_active.get(CONF_END_TIME)
        ee = merged_active.get(CONF_END_ENTITY)
        if et and et != BLANK_TIME and ee:
            raise ServiceValidationError(
                f"end_time ('{et}') and end_entity ('{ee}') are mutually exclusive. "
                "Set one or the other, not both."
            )

    # sunset_use_my requires my_position_value
    if CONF_SUNSET_USE_MY in patch or CONF_MY_POSITION_VALUE in patch:
        if merged_active.get(CONF_SUNSET_USE_MY) and not merged_active.get(
            CONF_MY_POSITION_VALUE
        ):
            raise ServiceValidationError(
                "sunset_use_my=true requires my_position_value to be set."
            )


def validate_options_patch(
    patch: dict,
    current_options: dict,
    sensor_type: str | None = None,
    *,
    check_slot_completeness: bool = True,
) -> dict:
    """Validate a patch dict and return it (unchanged).

    Raises ServiceValidationError if any field is invalid, out of range,
    targets an identity key, or violates a cross-field invariant.

    ``check_slot_completeness=False`` skips the custom-position
    trigger+position pairing rule — used by the deprecated
    ``set_force_override`` shim, whose legacy contract allowed setting the
    position/min-mode independently of the sensor list.
    """
    if not patch:
        raise ServiceValidationError("No fields provided — nothing to update.")

    # Reject identity keys
    bad = set(patch) & IDENTITY_KEYS
    if bad:
        raise ServiceValidationError(
            f"The following options cannot be changed via services: {sorted(bad)}. "
            "Use the integration's Options flow to change them."
        )

    # Geometry keys must match the cover's sensor_type. The per-type
    # rejection rules live on each ``CoverTypePolicy`` so adding a new
    # cover type only requires implementing ``disallowed_geometry_fields``
    # — this caller stays type-agnostic.
    if sensor_type is not None:
        from ..cover_types import get_policy

        vertical_only = (
            _SECTION_GEOMETRY_VERTICAL
            - _SECTION_GEOMETRY_AWNING
            - _SECTION_GEOMETRY_TILT
        )
        awning_only = (
            _SECTION_GEOMETRY_AWNING
            - _SECTION_GEOMETRY_VERTICAL
            - _SECTION_GEOMETRY_TILT
        )
        tilt_only = (
            _SECTION_GEOMETRY_TILT
            - _SECTION_GEOMETRY_VERTICAL
            - _SECTION_GEOMETRY_AWNING
        )
        policy = get_policy(sensor_type)
        for stray_set, type_label in policy.disallowed_geometry_fields(
            vertical_only=vertical_only,
            awning_only=awning_only,
            tilt_only=tilt_only,
        ):
            stray = set(patch) & stray_set
            if stray:
                raise ServiceValidationError(
                    f"Geometry fields {sorted(stray)} are only valid for "
                    f"{type_label} covers (this cover is '{sensor_type}')."
                )

    _validate_fields(patch)
    _cross_field_validate(
        patch, current_options, check_slot_completeness=check_slot_completeness
    )
    return patch


async def apply_options_patch(hass: HomeAssistant, coord: Any, patch: dict) -> dict:
    """Merge *patch* into the coordinator's config_entry.options and persist.

    Keys with value=None are removed from the options (clearing optional fields).
    Keys absent from *patch* are left unchanged.
    Returns the resulting options dict.
    """
    entry = coord.config_entry
    current = dict(entry.options)

    new_options = dict(current)
    for key, value in patch.items():
        if value is None:
            new_options.pop(key, None)
        else:
            new_options[key] = value

    hass.config_entries.async_update_entry(entry, options=new_options)
    return new_options


# ---------------------------------------------------------------------------
# Service handlers
# ---------------------------------------------------------------------------


def _make_section_handler(hass: HomeAssistant, allowed_keys: frozenset[str]):
    """Return an async service handler for a section-specific service."""

    from . import _resolve_targets  # noqa: PLC0415  (avoids circular at module level)

    async def _handler(call: ServiceCall) -> None:
        patch = _build_patch(call.data, allowed_keys)
        targets = _resolve_targets(hass, call)
        for coord in targets:
            sensor_type = coord.config_entry.data.get("sensor_type")
            validate_options_patch(patch, dict(coord.config_entry.options), sensor_type)
            await apply_options_patch(hass, coord, patch)
            _LOGGER.debug(
                "options updated for entry %s: %s",
                coord.config_entry.entry_id,
                list(patch),
            )

    return _handler


async def _handle_set_custom_position(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle set_custom_position — routes slot 1–10 to the right option keys."""
    from . import _resolve_targets  # noqa: PLC0415

    slot = call.data.get("slot")
    if slot not in CUSTOM_POSITION_SLOT_NUMBERS:
        valid = ", ".join(str(n) for n in CUSTOM_POSITION_SLOT_NUMBERS)
        raise ServiceValidationError(f"'slot' must be one of {valid} (got {slot!r}).")

    slot_keys = _CUSTOM_SLOT_KEYS[slot]
    # Map human-readable service field names → actual option keys
    field_map = {
        "sensors": slot_keys["sensors"],
        "template": slot_keys["template"],
        "template_mode": slot_keys["template_mode"],
        "position": slot_keys["position"],
        "priority": slot_keys["priority"],
        "min_mode": slot_keys["min_mode"],
        "use_my": slot_keys["use_my"],
        "enabled": slot_keys["enabled"],
    }

    # Build patch: only include fields that were supplied in the call
    patch: dict[str, Any] = {}
    for service_field, option_key in field_map.items():
        if service_field in call.data:
            patch[option_key] = call.data[service_field]

    # Deprecated single-sensor alias: `sensor` maps onto the sensors list
    # (ignored when `sensors` is also supplied).
    if "sensor" in call.data and "sensors" not in call.data:
        sensor = call.data["sensor"]
        patch[slot_keys["sensors"]] = [sensor] if sensor else []

    if not patch:
        raise ServiceValidationError("No slot fields provided — nothing to update.")

    # Keep the legacy single-sensor key mirrored for rollback fidelity.
    if slot_keys["sensors"] in patch:
        sensors = patch[slot_keys["sensors"]] or []
        patch[slot_keys["sensor"]] = sensors[0] if sensors else None

    targets = _resolve_targets(hass, call)
    for coord in targets:
        validate_options_patch(patch, dict(coord.config_entry.options))
        await apply_options_patch(hass, coord, patch)
        _LOGGER.debug(
            "custom_position slot %d updated for entry %s: %s",
            slot,
            coord.config_entry.entry_id,
            list(patch),
        )


async def _handle_set_force_override(hass: HomeAssistant, call: ServiceCall) -> None:
    """Map the deprecated set_force_override service onto slot 5 (issue #563).

    The standalone force-override feature merged into custom-position slot 5
    at safety priority. Existing automations keep working for one release;
    they should migrate to ``set_custom_position`` with ``slot: 5``.
    """
    from . import _resolve_targets  # noqa: PLC0415

    _LOGGER.warning(
        "adaptive_cover_pro.set_force_override is deprecated (issue #563): "
        "force override merged into custom-position slot 5. Use "
        "set_custom_position with slot: 5 instead."
    )
    slot_keys = _CUSTOM_SLOT_KEYS[5]
    field_map = {
        "force_override_sensors": slot_keys["sensors"],
        "force_override_position": slot_keys["position"],
        "force_override_min_mode": slot_keys["min_mode"],
    }
    patch: dict[str, Any] = {
        option_key: call.data[service_field]
        for service_field, option_key in field_map.items()
        if service_field in call.data
    }
    if not patch:
        raise ServiceValidationError("No fields provided — nothing to update.")
    # Pin the migrated slot at safety priority so behavior matches the old
    # force override exactly.
    patch[slot_keys["priority"]] = CUSTOM_POSITION_SAFETY_PRIORITY
    if slot_keys["sensors"] in patch:
        sensors = patch[slot_keys["sensors"]] or []
        patch[slot_keys["sensor"]] = sensors[0] if sensors else None

    targets = _resolve_targets(hass, call)
    for coord in targets:
        validate_options_patch(
            patch,
            dict(coord.config_entry.options),
            check_slot_completeness=False,
        )
        await apply_options_patch(hass, coord, patch)
        _LOGGER.debug(
            "set_force_override shim updated slot 5 for entry %s: %s",
            coord.config_entry.entry_id,
            list(patch),
        )


async def _handle_set_option(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle generic set_option service."""
    from . import _resolve_targets  # noqa: PLC0415

    option = call.data.get("option")
    if not option:
        raise ServiceValidationError("'option' field is required.")

    if option in IDENTITY_KEYS:
        raise ServiceValidationError(
            f"'{option}' cannot be changed via services. "
            "Use the integration's Options flow."
        )

    if option not in FIELD_VALIDATORS:
        raise ServiceValidationError(
            f"Unknown option '{option}'. "
            f"See the integration documentation for supported option keys."
        )

    value = call.data.get("value")
    patch = {option: value}

    targets = _resolve_targets(hass, call)
    for coord in targets:
        sensor_type = coord.config_entry.data.get("sensor_type")
        validate_options_patch(patch, dict(coord.config_entry.options), sensor_type)
        await apply_options_patch(hass, coord, patch)
        _LOGGER.debug(
            "set_option '%s' -> %r for entry %s",
            option,
            value,
            coord.config_entry.entry_id,
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_options_services(hass: HomeAssistant) -> None:
    """Register all options-mutation services. Called from async_setup_services."""

    def _section_handler(allowed_keys: frozenset[str]):
        return _make_section_handler(hass, allowed_keys)

    hass.services.async_register(
        DOMAIN, "set_position_limits", _section_handler(_SECTION_POSITION_LIMITS)
    )
    hass.services.async_register(
        DOMAIN, "set_sunset_sunrise", _section_handler(_SECTION_SUNSET_SUNRISE)
    )
    hass.services.async_register(
        DOMAIN, "set_automation_timing", _section_handler(_SECTION_AUTOMATION_TIMING)
    )
    hass.services.async_register(
        DOMAIN, "set_manual_override", _section_handler(_SECTION_MANUAL_OVERRIDE)
    )

    async def _force_override_shim(call: ServiceCall) -> None:
        await _handle_set_force_override(hass, call)

    # DEPRECATED (issue #563): kept one release so existing automations don't
    # hit service-not-found; routes onto custom-position slot 5.
    hass.services.async_register(DOMAIN, "set_force_override", _force_override_shim)
    hass.services.async_register(
        DOMAIN, "set_motion", _section_handler(_SECTION_MOTION)
    )
    hass.services.async_register(
        DOMAIN, "set_light_cloud", _section_handler(_SECTION_LIGHT_CLOUD)
    )
    hass.services.async_register(
        DOMAIN, "set_climate", _section_handler(_SECTION_CLIMATE)
    )
    hass.services.async_register(
        DOMAIN, "set_weather_safety", _section_handler(_SECTION_WEATHER_SAFETY)
    )
    hass.services.async_register(
        DOMAIN, "set_sun_tracking", _section_handler(_SECTION_SUN_TRACKING)
    )
    hass.services.async_register(
        DOMAIN, "set_blind_spot", _section_handler(_SECTION_BLIND_SPOT)
    )
    hass.services.async_register(
        DOMAIN, "set_interpolation", _section_handler(_SECTION_INTERPOLATION)
    )
    hass.services.async_register(
        DOMAIN, "set_geometry", _section_handler(_SECTION_GEOMETRY_ALL)
    )
    hass.services.async_register(
        DOMAIN, "set_venetian", _section_handler(_SECTION_VENETIAN)
    )

    async def _custom_pos_handler(call: ServiceCall) -> None:
        await _handle_set_custom_position(hass, call)

    hass.services.async_register(DOMAIN, "set_custom_position", _custom_pos_handler)

    async def _set_option_handler(call: ServiceCall) -> None:
        await _handle_set_option(hass, call)

    hass.services.async_register(DOMAIN, "set_option", _set_option_handler)


# Service names registered by this module (for unload)
OPTIONS_SERVICE_NAMES: tuple[str, ...] = (
    "set_position_limits",
    "set_sunset_sunrise",
    "set_automation_timing",
    "set_manual_override",
    "set_force_override",
    "set_custom_position",
    "set_motion",
    "set_light_cloud",
    "set_climate",
    "set_weather_safety",
    "set_sun_tracking",
    "set_blind_spot",
    "set_interpolation",
    "set_geometry",
    "set_venetian",
    "set_option",
)
