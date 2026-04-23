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
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CUSTOM_POSITION_1,
    CONF_CUSTOM_POSITION_2,
    CONF_CUSTOM_POSITION_3,
    CONF_CUSTOM_POSITION_4,
    CONF_CUSTOM_POSITION_MIN_MODE_1,
    CONF_CUSTOM_POSITION_MIN_MODE_2,
    CONF_CUSTOM_POSITION_MIN_MODE_3,
    CONF_CUSTOM_POSITION_MIN_MODE_4,
    CONF_CUSTOM_POSITION_PRIORITY_1,
    CONF_CUSTOM_POSITION_PRIORITY_2,
    CONF_CUSTOM_POSITION_PRIORITY_3,
    CONF_CUSTOM_POSITION_PRIORITY_4,
    CONF_CUSTOM_POSITION_SENSOR_1,
    CONF_CUSTOM_POSITION_SENSOR_2,
    CONF_CUSTOM_POSITION_SENSOR_3,
    CONF_CUSTOM_POSITION_SENSOR_4,
    CONF_CUSTOM_POSITION_USE_MY_1,
    CONF_CUSTOM_POSITION_USE_MY_2,
    CONF_CUSTOM_POSITION_USE_MY_3,
    CONF_CUSTOM_POSITION_USE_MY_4,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MODE,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_MY_POSITION_VALUE,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_SILL_HEIGHT,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_OVERRIDE_MIN_MODE,
    CONF_WEATHER_OVERRIDE_POSITION,
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
    DOMAIN,
    SensorType,
)

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
FIELD_VALIDATORS: dict[str, Any] = {
    # Geometry — vertical blind
    CONF_HEIGHT_WIN: _num(0.1, 6),
    CONF_WINDOW_WIDTH: _num(0.1, 5),
    CONF_WINDOW_DEPTH: _num(0.0, 5.0),
    CONF_SILL_HEIGHT: _num(0.0, 3.0),
    # Geometry — awning
    CONF_LENGTH_AWNING: _num(0.3, 6),
    CONF_AWNING_ANGLE: _num(0, 45),
    # Geometry — tilt/venetian
    CONF_TILT_DEPTH: _num(0.1, 15),
    CONF_TILT_DISTANCE: _num(0.1, 15),
    CONF_TILT_MODE: _select_v("mode1", "mode2"),
    # Sun tracking
    CONF_ENABLE_SUN_TRACKING: _bool_v(),
    CONF_AZIMUTH: _num(0, 359),
    CONF_FOV_LEFT: _num(0, 180),
    CONF_FOV_RIGHT: _num(0, 180),
    CONF_MIN_ELEVATION: _num(0, 90),
    CONF_MAX_ELEVATION: _num(0, 90),
    CONF_DISTANCE: _num(0.1, 5),
    # Blind spot
    CONF_ENABLE_BLIND_SPOT: _bool_v(),
    CONF_BLIND_SPOT_LEFT: _num(0, 359),
    CONF_BLIND_SPOT_RIGHT: _num(0, 360),
    CONF_BLIND_SPOT_ELEVATION: _num(0, 90),
    # Position limits & sunset/sunrise
    CONF_DEFAULT_HEIGHT: _num(0, 100),
    CONF_MAX_POSITION: _num(1, 100),
    CONF_ENABLE_MAX_POSITION: _bool_v(),
    CONF_MIN_POSITION: _num(0, 99),
    CONF_ENABLE_MIN_POSITION: _bool_v(),
    CONF_SUNSET_POS: _num(0, 100),
    CONF_MY_POSITION_VALUE: _num(1, 99),
    CONF_SUNSET_USE_MY: _bool_v(),
    CONF_SUNSET_OFFSET: _num(-120, 120),
    CONF_SUNRISE_OFFSET: _num(-120, 120),
    CONF_OPEN_CLOSE_THRESHOLD: _num(1, 99),
    CONF_INVERSE_STATE: _bool_v(),
    CONF_INTERP: _bool_v(),
    # Interpolation
    CONF_INTERP_START: _num(0, 100),
    CONF_INTERP_END: _num(0, 100),
    CONF_INTERP_LIST: vol.Any(None, list),
    CONF_INTERP_LIST_NEW: vol.Any(None, list),
    # Automation timing
    CONF_DELTA_POSITION: _num(1, 90),
    CONF_DELTA_TIME: _num(2, 60),
    CONF_START_TIME: _time_v(),
    CONF_START_ENTITY: _entity_v(),
    CONF_END_TIME: _time_v(),
    CONF_END_ENTITY: _entity_v(),
    CONF_RETURN_SUNSET: _bool_v(),
    # Manual override
    CONF_MANUAL_OVERRIDE_DURATION: _duration_v(),
    CONF_MANUAL_OVERRIDE_RESET: _bool_v(),
    CONF_MANUAL_THRESHOLD: _num(0, 99),
    CONF_MANUAL_IGNORE_INTERMEDIATE: _bool_v(),
    # Force override
    CONF_FORCE_OVERRIDE_SENSORS: _entities_v(),
    CONF_FORCE_OVERRIDE_POSITION: _num(0, 100),
    CONF_FORCE_OVERRIDE_MIN_MODE: _bool_v(),
    # Custom positions 1–4
    CONF_CUSTOM_POSITION_SENSOR_1: _entity_v(),
    CONF_CUSTOM_POSITION_1: _num(0, 100),
    CONF_CUSTOM_POSITION_PRIORITY_1: _num(1, 99),
    CONF_CUSTOM_POSITION_MIN_MODE_1: _bool_v(),
    CONF_CUSTOM_POSITION_USE_MY_1: _bool_v(),
    CONF_CUSTOM_POSITION_SENSOR_2: _entity_v(),
    CONF_CUSTOM_POSITION_2: _num(0, 100),
    CONF_CUSTOM_POSITION_PRIORITY_2: _num(1, 99),
    CONF_CUSTOM_POSITION_MIN_MODE_2: _bool_v(),
    CONF_CUSTOM_POSITION_USE_MY_2: _bool_v(),
    CONF_CUSTOM_POSITION_SENSOR_3: _entity_v(),
    CONF_CUSTOM_POSITION_3: _num(0, 100),
    CONF_CUSTOM_POSITION_PRIORITY_3: _num(1, 99),
    CONF_CUSTOM_POSITION_MIN_MODE_3: _bool_v(),
    CONF_CUSTOM_POSITION_USE_MY_3: _bool_v(),
    CONF_CUSTOM_POSITION_SENSOR_4: _entity_v(),
    CONF_CUSTOM_POSITION_4: _num(0, 100),
    CONF_CUSTOM_POSITION_PRIORITY_4: _num(1, 99),
    CONF_CUSTOM_POSITION_MIN_MODE_4: _bool_v(),
    CONF_CUSTOM_POSITION_USE_MY_4: _bool_v(),
    # Motion
    CONF_MOTION_SENSORS: _entities_v(),
    CONF_MOTION_TIMEOUT: _num(30, 3600),
    # Light & Cloud
    CONF_WEATHER_ENTITY: _entity_v(),
    CONF_WEATHER_STATE: vol.Any(None, list),
    CONF_LUX_ENTITY: _entity_v(),
    CONF_LUX_THRESHOLD: vol.Any(None, vol.Coerce(float)),
    CONF_IRRADIANCE_ENTITY: _entity_v(),
    CONF_IRRADIANCE_THRESHOLD: vol.Any(None, vol.Coerce(float)),
    CONF_CLOUD_COVERAGE_ENTITY: _entity_v(),
    CONF_CLOUD_COVERAGE_THRESHOLD: vol.Any(None, vol.Coerce(float)),
    CONF_CLOUD_SUPPRESSION: _bool_v(),
    # Climate
    CONF_CLIMATE_MODE: _bool_v(),
    CONF_TEMP_ENTITY: _entity_v(),
    CONF_TEMP_LOW: _num(0, 90),
    CONF_TEMP_HIGH: _num(0, 90),
    CONF_OUTSIDETEMP_ENTITY: _entity_v(),
    CONF_OUTSIDE_THRESHOLD: _num(0, 100),
    CONF_PRESENCE_ENTITY: _entity_v(),
    CONF_TRANSPARENT_BLIND: _bool_v(),
    CONF_WINTER_CLOSE_INSULATION: _bool_v(),
    # Weather safety
    CONF_WEATHER_BYPASS_AUTO_CONTROL: _bool_v(),
    CONF_WEATHER_WIND_SPEED_SENSOR: _entity_v(),
    CONF_WEATHER_WIND_DIRECTION_SENSOR: _entity_v(),
    CONF_WEATHER_WIND_SPEED_THRESHOLD: _num(0, 200),
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE: _num(5, 180),
    CONF_WEATHER_RAIN_SENSOR: _entity_v(),
    CONF_WEATHER_RAIN_THRESHOLD: _num(0, 100),
    CONF_WEATHER_IS_RAINING_SENSOR: _entity_v(),
    CONF_WEATHER_IS_WINDY_SENSOR: _entity_v(),
    CONF_WEATHER_SEVERE_SENSORS: _entities_v(),
    CONF_WEATHER_OVERRIDE_POSITION: _num(0, 100),
    CONF_WEATHER_OVERRIDE_MIN_MODE: _bool_v(),
    CONF_WEATHER_TIMEOUT: _num(0, 3600),
}

# ---------------------------------------------------------------------------
# Section key sets (used for building service-call patches)
# ---------------------------------------------------------------------------

_SECTION_POSITION_LIMITS = frozenset(
    {
        CONF_DEFAULT_HEIGHT,
        CONF_MIN_POSITION,
        CONF_ENABLE_MIN_POSITION,
        CONF_MAX_POSITION,
        CONF_ENABLE_MAX_POSITION,
        CONF_OPEN_CLOSE_THRESHOLD,
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
    }
)

_SECTION_FORCE_OVERRIDE = frozenset(
    {
        CONF_FORCE_OVERRIDE_SENSORS,
        CONF_FORCE_OVERRIDE_POSITION,
        CONF_FORCE_OVERRIDE_MIN_MODE,
    }
)

_SECTION_MOTION = frozenset({CONF_MOTION_SENSORS, CONF_MOTION_TIMEOUT})

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
        CONF_TRANSPARENT_BLIND,
        CONF_WINTER_CLOSE_INSULATION,
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
        CONF_WEATHER_IS_WINDY_SENSOR,
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
    }
)

_SECTION_BLIND_SPOT = frozenset(
    {
        CONF_ENABLE_BLIND_SPOT,
        CONF_BLIND_SPOT_LEFT,
        CONF_BLIND_SPOT_RIGHT,
        CONF_BLIND_SPOT_ELEVATION,
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
    {CONF_TILT_DEPTH, CONF_TILT_DISTANCE, CONF_TILT_MODE}
)
_SECTION_GEOMETRY_ALL = (
    _SECTION_GEOMETRY_VERTICAL | _SECTION_GEOMETRY_AWNING | _SECTION_GEOMETRY_TILT
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
    | frozenset(
        {
            CONF_CUSTOM_POSITION_SENSOR_1,
            CONF_CUSTOM_POSITION_1,
            CONF_CUSTOM_POSITION_PRIORITY_1,
            CONF_CUSTOM_POSITION_MIN_MODE_1,
            CONF_CUSTOM_POSITION_USE_MY_1,
            CONF_CUSTOM_POSITION_SENSOR_2,
            CONF_CUSTOM_POSITION_2,
            CONF_CUSTOM_POSITION_PRIORITY_2,
            CONF_CUSTOM_POSITION_MIN_MODE_2,
            CONF_CUSTOM_POSITION_USE_MY_2,
            CONF_CUSTOM_POSITION_SENSOR_3,
            CONF_CUSTOM_POSITION_3,
            CONF_CUSTOM_POSITION_PRIORITY_3,
            CONF_CUSTOM_POSITION_MIN_MODE_3,
            CONF_CUSTOM_POSITION_USE_MY_3,
            CONF_CUSTOM_POSITION_SENSOR_4,
            CONF_CUSTOM_POSITION_4,
            CONF_CUSTOM_POSITION_PRIORITY_4,
            CONF_CUSTOM_POSITION_MIN_MODE_4,
            CONF_CUSTOM_POSITION_USE_MY_4,
        }
    )
)

# Custom position slot key maps
_CUSTOM_SLOT_KEYS: dict[int, dict[str, str]] = {
    i: {
        "sensor": f"custom_position_sensor_{i}",
        "position": f"custom_position_{i}",
        "priority": f"custom_position_priority_{i}",
        "min_mode": f"custom_position_min_mode_{i}",
        "use_my": f"custom_position_use_my_{i}",
    }
    for i in range(1, 5)
}

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _build_patch(call_data: dict, allowed_keys: frozenset[str]) -> dict:
    """Extract allowed keys from a service call's data dict.

    Keys whose value is None are included (they signal "clear this option").
    HA plumbing keys (entity_id, device_id, area_id) are always excluded.
    """
    return {
        k: v
        for k, v in call_data.items()
        if k in allowed_keys and k not in _PLUMBING_KEYS
    }


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


def _cross_field_validate(patch: dict, current: dict) -> None:
    """Validate cross-field invariants on the merged options.

    Only checks invariants that involve at least one key present in *patch*
    so that unrelated existing options don't produce false errors.
    """
    merged = {**current, **patch}
    # Remove keys explicitly cleared (value=None) from the merged view
    merged_active = {k: v for k, v in merged.items() if v is not None}

    # Blind spot ordering
    if CONF_BLIND_SPOT_LEFT in patch or CONF_BLIND_SPOT_RIGHT in patch:
        left = merged_active.get(CONF_BLIND_SPOT_LEFT)
        right = merged_active.get(CONF_BLIND_SPOT_RIGHT)
        if left is not None and right is not None and right <= left:
            raise ServiceValidationError(
                f"blind_spot_right ({right}) must be greater than blind_spot_left ({left})."
            )

    # Temperature ordering
    if CONF_TEMP_LOW in patch or CONF_TEMP_HIGH in patch:
        low = merged_active.get(CONF_TEMP_LOW)
        high = merged_active.get(CONF_TEMP_HIGH)
        if low is not None and high is not None and low >= high:
            raise ServiceValidationError(
                f"temp_low ({low}) must be less than temp_high ({high})."
            )

    # Custom position slot completeness
    for i in range(1, 5):
        slot = _CUSTOM_SLOT_KEYS[i]
        s_key, p_key = slot["sensor"], slot["position"]
        if s_key in patch or p_key in patch:
            sensor_set = merged_active.get(s_key) is not None
            pos_set = merged_active.get(p_key) is not None
            if sensor_set != pos_set:
                missing = p_key if sensor_set else s_key
                present = s_key if sensor_set else p_key
                raise ServiceValidationError(
                    f"Custom position slot {i}: '{present}' is set but '{missing}' is missing. "
                    "Set both or clear both."
                )

    # Time window mutual exclusion
    if CONF_START_TIME in patch or CONF_START_ENTITY in patch:
        st = merged_active.get(CONF_START_TIME)
        se = merged_active.get(CONF_START_ENTITY)
        if st and st != "00:00:00" and se:
            raise ServiceValidationError(
                f"start_time ('{st}') and start_entity ('{se}') are mutually exclusive. "
                "Set one or the other, not both."
            )

    if CONF_END_TIME in patch or CONF_END_ENTITY in patch:
        et = merged_active.get(CONF_END_TIME)
        ee = merged_active.get(CONF_END_ENTITY)
        if et and et != "00:00:00" and ee:
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
) -> dict:
    """Validate a patch dict and return it (unchanged).

    Raises ServiceValidationError if any field is invalid, out of range,
    targets an identity key, or violates a cross-field invariant.
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

    # Geometry keys must match the cover's sensor_type
    if sensor_type is not None:
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
        if sensor_type != SensorType.BLIND and set(patch) & vertical_only:
            raise ServiceValidationError(
                f"Geometry fields {sorted(set(patch) & vertical_only)} are only valid for "
                f"vertical blind covers (this cover is '{sensor_type}')."
            )
        if sensor_type != SensorType.AWNING and set(patch) & awning_only:
            raise ServiceValidationError(
                f"Geometry fields {sorted(set(patch) & awning_only)} are only valid for "
                f"awning covers (this cover is '{sensor_type}')."
            )
        if sensor_type != SensorType.TILT and set(patch) & tilt_only:
            raise ServiceValidationError(
                f"Geometry fields {sorted(set(patch) & tilt_only)} are only valid for "
                f"tilt covers (this cover is '{sensor_type}')."
            )

    _validate_fields(patch)
    _cross_field_validate(patch, current_options)
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
    """Handle set_custom_position — routes slot 1–4 to the right option keys."""
    from . import _resolve_targets  # noqa: PLC0415

    slot = call.data.get("slot")
    if slot not in (1, 2, 3, 4):
        raise ServiceValidationError(f"'slot' must be 1, 2, 3, or 4 (got {slot!r}).")

    slot_keys = _CUSTOM_SLOT_KEYS[slot]
    # Map human-readable service field names → actual option keys
    field_map = {
        "sensor": slot_keys["sensor"],
        "position": slot_keys["position"],
        "priority": slot_keys["priority"],
        "min_mode": slot_keys["min_mode"],
        "use_my": slot_keys["use_my"],
    }

    # Build patch: only include fields that were supplied in the call
    patch: dict[str, Any] = {}
    for service_field, option_key in field_map.items():
        if service_field in call.data:
            patch[option_key] = call.data[service_field]

    if not patch:
        raise ServiceValidationError("No slot fields provided — nothing to update.")

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
    hass.services.async_register(
        DOMAIN, "set_force_override", _section_handler(_SECTION_FORCE_OVERRIDE)
    )
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
    "set_option",
)
