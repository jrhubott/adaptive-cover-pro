"""Config flow for Adaptive Cover Pro integration."""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    BLANK_TIME,
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_MY_POSITION_ENTITIES,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_PROXY_COVER,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_USE_MY,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOT_NUMBERS,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_ENABLE_MY_POSITION_ENTITIES,
    DEFAULT_ENABLE_POSITION_MATCHING,
    DEFAULT_ENABLE_PROXY_COVER,
    CONF_FOV_LEFT,
    CONF_FOV_MODE,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_IGNORE_EXTERNAL,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
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
    CONF_MOTION_TIMEOUT_MODE,
    DEFAULT_MOTION_TEMPLATE_MODE,
    DEFAULT_MOTION_TIMEOUT_MODE,
    MOTION_TIMEOUT_MODE_HOLD,
    MOTION_TIMEOUT_MODE_RETURN,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_POSITION_TOLERANCE,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_SUNSET_TILT,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSPARENT_BLIND,
    CONF_WINTER_CLOSE_INSULATION,
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
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    DEFAULT_DELTA_POSITION,
    DEFAULT_DELTA_TIME,
    DEFAULT_MANUAL_OVERRIDE_DURATION,
    DEFAULT_MOTION_TIMEOUT,
    CONF_DEBUG_CATEGORIES,
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_DRY_RUN,
    CONF_TRANSIT_TIMEOUT,
    DEBUG_CATEGORIES_ALL,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    MAX_DEBUG_EVENT_BUFFER_SIZE,
    MAX_TRANSIT_TIMEOUT,
    MIN_TRANSIT_TIMEOUT,
    MODE2_OPEN_HORIZONTAL_PERCENT,
    DOMAIN,
    CoverType,
    FovMode,
    TemplateCombineMode,
)
from .engine.sun_geometry import computed_fov_line, fov_from_reveal
from .helpers import (
    custom_position_slot_configured,
    custom_position_slot_sensors,
    mirror_legacy_slot_sensor_keys,
)

_LOGGER = logging.getLogger(__name__)

# Cover-type picker options, derived from the policy registry so a new cover
# type appears in the create flow automatically (no edit here). Order follows
# registration order (blind, awning, tilt, venetian, …).
from .cover_types import POLICY_REGISTRY as _POLICY_REGISTRY  # noqa: E402

SENSOR_TYPE_MENU = list(_POLICY_REGISTRY)

_STANDALONE_SENTINEL = "__standalone__"

_WIKI_BASE_URL = "https://github.com/jrhubott/adaptive-cover-pro/wiki"


def _geometry_wiki_link(sensor_type: str | None) -> str:
    """Build the per-type wiki "Learn more" link from the policy's anchor.

    A fifth cover type opts in by overriding ``CoverTypePolicy.wiki_anchor()``
    on its subclass — no edit here is required.
    """
    # Avoid POLICY_REGISTRY lookup before its module-level import below.
    from .cover_types import POLICY_REGISTRY as _registry, get_policy as _get

    anchor = (
        _get(sensor_type).wiki_anchor() if sensor_type in _registry else "Cover-Types"
    )
    return f"[Learn more]({_WIKI_BASE_URL}/{anchor})"


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Optional(CONF_MODE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=SENSOR_TYPE_MENU, translation_key="mode"
            )
        ),
    }
)

# ---------------------------------------------------------------------------
# Step-specific schemas (replace old monolithic OPTIONS / VERTICAL_OPTIONS / etc.)
# ---------------------------------------------------------------------------

# Geometry schemas live next to each cover-type policy. Re-exported here so
# in-tree consumers (tests, sync coverage) keep their existing import paths.
from .cover_types import (  # noqa: E402
    POLICY_REGISTRY,
    BlindPolicy,
    TiltPolicy,
    get_policy,
)
from .cover_types.awning import GEOMETRY_HORIZONTAL_SCHEMA  # noqa: E402, F401
from .cover_types.blind import GEOMETRY_VERTICAL_SCHEMA  # noqa: E402, F401
from .cover_types.tilt import GEOMETRY_TILT_SCHEMA  # noqa: E402, F401
from .cover_types.venetian import GEOMETRY_VENETIAN_SCHEMA  # noqa: E402, F401
from .unit_system import (  # noqa: E402
    options_to_display,
    user_input_to_canonical,
)

# Dynamic (sensor-unit / locale aware) section builders live in config_dynamic;
# re-exported here so the step handlers and the existing test imports keep their
# call sites. config_flow is a consumer of these — not their owner.
from . import config_fields  # noqa: E402
from .config_dynamic import (  # noqa: E402
    blind_spot_schema,
    glare_zones_schema as _glare_zones_schema,
    light_cloud_schema,
    sun_tracking_schema,
    temperature_climate_schema,
    weather_override_schema,
)


# Module-level constant for tests / imports. Identical to the legacy
# vol.Schema(...) shape — metric labels, no hass needed. ``sun_tracking_schema``
# is re-exported from ``config_dynamic`` above.
SUN_TRACKING_SCHEMA = sun_tracking_schema()


# Keys in SUN_TRACKING_SCHEMA stored in canonical metres.
_SUN_TRACKING_LENGTH_KEYS: tuple[str, ...] = (CONF_DISTANCE,)

POSITION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEFAULT_HEIGHT, default=60): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_ENABLE_MAX_POSITION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MAX_POSITION, default=100): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_ENABLE_MIN_POSITION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MIN_POSITION, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_MIN_POSITION_SUN_TRACKING): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_SUNSET_TIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_SUNRISE_TIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_SUNSET_POS): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_SUNSET_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-120,
                max=120,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Optional(CONF_SUNRISE_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-120,
                max=120,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Optional(CONF_RETURN_SUNSET, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_ENABLE_MY_POSITION_ENTITIES,
            default=DEFAULT_ENABLE_MY_POSITION_ENTITIES,
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MY_POSITION_VALUE): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_SUNSET_USE_MY, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_OPEN_CLOSE_THRESHOLD, default=50): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_POSITION_TOLERANCE, default=3): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=20,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_ENABLE_POSITION_MATCHING,
            default=DEFAULT_ENABLE_POSITION_MATCHING,
        ): selector.BooleanSelector(),
        vol.Optional(CONF_INVERSE_STATE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_INTERP, default=False): selector.BooleanSelector(),
    }
)

# Keys in POSITION_SCHEMA with default=vol.UNDEFINED that voluptuous omits when
# cleared by the user. Both flow handlers must call optional_entities() with this
# list before dict.update() — otherwise the prior value survives a clear
# (issue #439; same class as #323).
_POSITION_OPTIONAL_KEYS: list[str] = [
    CONF_SUNSET_POS,
    CONF_MY_POSITION_VALUE,
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_SUNSET_TIME_ENTITY,
    CONF_SUNRISE_TIME_ENTITY,
]

AUTOMATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DELTA_POSITION, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_DELTA_TIME, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=2,
                max=60,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Optional(CONF_START_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        # No default: a cleared TimeSelector must leave the key absent so it can
        # be stripped (issue #492). Blank stripping is enforced in
        # async_step_automation since the suggested-values path can re-add it.
        vol.Optional(CONF_START_TIME): selector.TimeSelector(),
        vol.Optional(CONF_END_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_END_TIME): selector.TimeSelector(),
    }
)

MANUAL_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_MANUAL_OVERRIDE_DURATION, default={"hours": 2}
        ): selector.DurationSelector(),
        vol.Optional(
            CONF_MANUAL_OVERRIDE_RESET, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MANUAL_THRESHOLD): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_MANUAL_IGNORE_INTERMEDIATE, default=False
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_MANUAL_IGNORE_EXTERNAL, default=False
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_TRANSIT_TIMEOUT,
            default=DEFAULT_TRANSIT_TIMEOUT_SECONDS,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_TRANSIT_TIMEOUT,
                max=MAX_TRANSIT_TIMEOUT,
                step=5,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="seconds",
            )
        ),
    }
)

_BINARY_ON_DOMAINS = ["binary_sensor", "input_boolean", "switch", "schedule"]
_PRESENCE_LIKE_DOMAINS = _BINARY_ON_DOMAINS + ["device_tracker", "person", "zone"]
_NUMERIC_DOMAINS = ["sensor", "input_number", "number"]


def _binary_on_selector(*, multiple: bool = False) -> selector.EntitySelector:
    """Return a single or multi-pick selector for on/off entities."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_BINARY_ON_DOMAINS, multiple=multiple)
    )


def _presence_like_selector(*, multiple: bool = False) -> selector.EntitySelector:
    """Return a selector for presence-shaped entities (motion, occupancy, presence)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_PRESENCE_LIKE_DOMAINS, multiple=multiple)
    )


def _build_custom_position_schema_dict(sensor_type: str | None = None) -> dict:
    """Compose the custom-position schema dict for the given cover type.

    Delegates to ``config_fields.custom_position_schema``; per-slot and global
    tilt fields are included for cover types whose policy advertises
    custom-position tilt extras (venetian today). A new cover type opts in by
    returning those keys from ``extra_field_keys`` — no edit here.
    """
    include_tilt = sensor_type in POLICY_REGISTRY and bool(
        get_policy(sensor_type).extra_field_keys(config_fields.SECTION_CUSTOM_POSITION)
    )
    return dict(config_fields.custom_position_schema(include_tilt=include_tilt).schema)


CUSTOM_POSITION_SCHEMA = vol.Schema(_build_custom_position_schema_dict())

# Keys in CUSTOM_POSITION_SCHEMA that have no schema default (template,
# position, priority, tilt). Voluptuous omits them from user_input when
# cleared, so both flow handlers must call optional_entities() with this list
# before dict.update() -- otherwise the prior value survives a clear (issue
# #323). The `sensors` list key carries default=[] so a cleared multi-select
# round-trips as [] on its own (it must NOT become None — None would re-enable
# the legacy single-sensor fallback).
_CUSTOM_POSITION_OPTIONAL_KEYS: list[str] = [
    slot[field]
    for slot in CUSTOM_POSITION_SLOTS.values()
    for field in ("template", "position", "priority", "tilt")
] + [CONF_DEFAULT_TILT, CONF_SUNSET_TILT]

MOTION_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MOTION_SENSORS, default=[]): _presence_like_selector(
            multiple=True
        ),
        vol.Optional(
            CONF_MOTION_MEDIA_PLAYERS, default=[]
        ): config_fields.media_player_selector(multiple=True),
        vol.Optional(CONF_MOTION_TEMPLATE): selector.TemplateSelector(),
        vol.Optional(
            CONF_MOTION_TEMPLATE_MODE, default=DEFAULT_MOTION_TEMPLATE_MODE
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[m.value for m in TemplateCombineMode],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="template_combine_mode",
            )
        ),
        vol.Optional(
            CONF_MOTION_TIMEOUT, default=DEFAULT_MOTION_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=30,
                max=3600,
                step=30,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="seconds",
            )
        ),
        vol.Optional(
            CONF_MOTION_TIMEOUT_MODE, default=DEFAULT_MOTION_TIMEOUT_MODE
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[MOTION_TIMEOUT_MODE_RETURN, MOTION_TIMEOUT_MODE_HOLD],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="motion_timeout_mode",
            )
        ),
    }
)

DEBUG_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DRY_RUN, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_DEBUG_MODE, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_DEBUG_CATEGORIES,
            default=[],
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=DEBUG_CATEGORIES_ALL,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key="debug_categories",
            )
        ),
        vol.Optional(
            CONF_DEBUG_EVENT_BUFFER_SIZE,
            default=DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=10,
                max=MAX_DEBUG_EVENT_BUFFER_SIZE,
                step=10,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
    }
)


# Module-level constant for tests / imports. Uses empty/fallback labels.
# ``weather_override_schema`` is re-exported from ``config_dynamic`` above.
WEATHER_OVERRIDE_SCHEMA = weather_override_schema()

# Keys in WEATHER_OVERRIDE_SCHEMA with default=vol.UNDEFINED. Voluptuous omits
# them from user_input when cleared, so both flow handlers must call
# optional_entities() with this list before dict.update() -- otherwise the prior
# value survives a clear (issue #323).
_WEATHER_OVERRIDE_OPTIONAL_KEYS: list[str] = [
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
]


# --- Light & Cloud (works without climate mode) ---
# ``light_cloud_schema`` is re-exported from ``config_dynamic`` above.
# Module-level constant for tests / imports.
LIGHT_CLOUD_SCHEMA = light_cloud_schema()

# Keys in LIGHT_CLOUD_SCHEMA with default=vol.UNDEFINED (entity fields use
# explicit UNDEFINED; CONF_CLOUDY_POSITION uses bare vol.Optional which also
# produces default=vol.UNDEFINED). Both flow handlers must call
# optional_entities() with this list before dict.update() -- see #323 and #392.
_LIGHT_CLOUD_OPTIONAL_KEYS: list[str] = [
    CONF_CLOUDY_POSITION,
    CONF_WEATHER_ENTITY,
    CONF_IS_SUNNY_SENSOR,
    CONF_LUX_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_CLOUD_COVERAGE_ENTITY,
]

# --- Temperature Climate Mode ---
#
# The temperature thresholds are interpreted in the configured **sensor's**
# unit, not Home Assistant's locale unit — so the selector label reflects the
# sensor's ``unit_of_measurement`` attribute when set, falling back to HA's
# ``temperature_unit`` otherwise. Ranges are kept wide enough for either
# Celsius or Fahrenheit users to enter sensible values.

# ``temperature_climate_schema`` is re-exported from ``config_dynamic`` above.
# Module-level constant for tests / imports. Uses literal "°" label (legacy).
TEMPERATURE_CLIMATE_SCHEMA = temperature_climate_schema()

# Keys in TEMPERATURE_CLIMATE_SCHEMA with default=vol.UNDEFINED (CONF_TEMP_ENTITY
# is a bare vol.Optional). Both flow handlers must call optional_entities() with
# this list before dict.update() -- see #323.
_TEMPERATURE_CLIMATE_OPTIONAL_KEYS: list[str] = [
    CONF_TEMP_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
]

WEATHER_OPTIONS = vol.Schema(
    {
        vol.Optional(
            CONF_WEATHER_STATE, default=["sunny", "partlycloudy", "cloudy", "clear"]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                sort=False,
                options=[
                    "clear-night",
                    "clear",
                    "cloudy",
                    "fog",
                    "hail",
                    "lightning",
                    "lightning-rainy",
                    "partlycloudy",
                    "pouring",
                    "rainy",
                    "snowy",
                    "snowy-rainy",
                    "sunny",
                    "windy",
                    "windy-variant",
                    "exceptional",
                ],
            )
        )
    }
)

INTERPOLATION_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_INTERP_START): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_INTERP_END): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_INTERP_LIST, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True, custom_value=True, options=["0", "50", "100"]
            )
        ),
        vol.Optional(CONF_INTERP_LIST_NEW, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True, custom_value=True, options=["0", "50", "100"]
            )
        ),
    }
)


def _get_azimuth_edges(data) -> int:
    """Return the total azimuth field-of-view span (fov_left + fov_right)."""
    return data[CONF_FOV_LEFT] + data[CONF_FOV_RIGHT]


def _stringify_templatable(suggested: dict) -> dict:
    """Coerce templatable threshold values to strings for the template editor.

    The ``TemplateSelector`` code editor only renders a *string* value; legacy
    entries store these thresholds as numbers, so a raw int/float collapses the
    field to nothing (issue #577). Stringify them before
    ``add_suggested_values_to_schema`` injects the suggested value. Whole-valued
    floats render without a trailing ``.0``; ``None`` and existing strings
    (including templates) are left untouched.
    """
    out = dict(suggested)
    for key in config_fields.TEMPLATABLE_KEYS:
        value = out.get(key)
        if value is None or isinstance(value, str):
            continue
        if isinstance(value, float) and value.is_integer():
            out[key] = str(int(value))
        else:
            out[key] = str(value)
    return out


def _format_duration(dur: dict | int | float | None) -> str:
    """Format a DurationSelector value (dict or legacy int minutes) as human-readable text.

    A DurationSelector stores ``{"hours": H, "minutes": M, "seconds": S}``.
    Legacy configs may store a plain number (treated as minutes).
    Zero-valued components are omitted unless all are zero (returns "0 min").
    Examples:
        {"hours": 5, "minutes": 0, "seconds": 0} -> "5 h"
        {"hours": 2, "minutes": 15, "seconds": 0} -> "2 h 15 min"
        {"hours": 0, "minutes": 30, "seconds": 0} -> "30 min"
        {"hours": 0, "minutes": 0, "seconds": 45} -> "45 s"
        120 (legacy int)                           -> "120 min"

    """
    if dur is None:
        return ""
    if isinstance(dur, int | float):
        return f"{int(dur)} min"
    h = int(dur.get("hours", 0) or 0)
    m = int(dur.get("minutes", 0) or 0)
    s = int(dur.get("seconds", 0) or 0)
    parts = []
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if s:
        parts.append(f"{s} s")
    return " ".join(parts) if parts else "0 min"


def _check_cover_capabilities(
    config: dict,
    sensor_type: str | None,
    hass: HomeAssistant | None,
) -> tuple[dict[str, dict[str, bool] | None], list[str]]:
    """Inspect bound cover entities and return capabilities + warning lines.

    Returns:
        cap_map:  entity_id → feature dict (None if entity unavailable)
        warnings: list of ⚠️ strings — per-entity and cross-entity issues

    """
    entities: list[str] = config.get(CONF_ENTITIES) or []
    if hass is None or not entities:
        return {}, []

    from .helpers import check_cover_features

    cap_map: dict[str, dict[str, bool] | None] = {}
    warnings: list[str] = []

    from .cover_types.base import CAP_HAS_SET_POSITION, caps_get

    for eid in entities:
        caps = check_cover_features(hass, eid)
        cap_map[eid] = caps
        if caps is None:
            warnings.append(f"⚠️ {eid}: not ready (unavailable)")
        else:
            if not caps_get(caps, CAP_HAS_SET_POSITION):
                warnings.append(
                    f"⚠️ {eid} is open/close-only — will be driven via "
                    "threshold compare, not set_position."
                )
            state = hass.states.get(eid)
            if state and state.attributes.get("assumed_state"):
                warnings.append(
                    f"⚠️ {eid} has assumed_state — real position cannot be "
                    "read back, which may affect position verification and delta-bypass."
                )

    known: dict[str, dict[str, bool]] = {
        eid: caps for eid, caps in cap_map.items() if caps is not None
    }

    if known:
        has_pos = {
            eid for eid, caps in known.items() if caps_get(caps, CAP_HAS_SET_POSITION)
        }
        no_pos = {
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_POSITION)
        }

        if has_pos and no_pos:
            warnings.append(
                "⚠️ Mixed capabilities: some covers support set_position, "
                "others are open/close-only — they will be driven differently."
            )

        if sensor_type is not None:
            warnings.extend(get_policy(sensor_type).cover_capability_warnings(known))

        min_pos_val = config.get(CONF_MIN_POSITION)
        max_pos_val = config.get(CONF_MAX_POSITION)
        enable_min_val = config.get(CONF_ENABLE_MIN_POSITION)
        enable_max_val = config.get(CONF_ENABLE_MAX_POSITION)
        limits_in_use = (
            (min_pos_val is not None and min_pos_val != 0)
            or (max_pos_val is not None and max_pos_val != 100)
            or enable_min_val
            or enable_max_val
        )
        oc_only = [eid for eid in no_pos if eid in known]
        if limits_in_use and oc_only:
            oc_str = ", ".join(oc_only)
            warnings.append(
                f"⚠️ Position limits are configured but {oc_str} "
                "is open/close-only — limits will be ignored on that cover."
            )

    return cap_map, warnings


def _build_cover_capabilities_text(
    config: dict,
    sensor_type: str | None,
    hass: HomeAssistant | None = None,
) -> str:
    """Build a Cover Capabilities block for the Debug & Diagnostics screen.

    Returns a markdown string (possibly empty) describing each bound cover's
    detected features plus any cross-entity consistency warnings.
    """
    entities: list[str] = config.get(CONF_ENTITIES) or []
    if hass is None or not entities:
        return ""

    cap_map, warnings = _check_cover_capabilities(config, sensor_type, hass)

    from .cover_types.base import (
        CAP_HAS_CLOSE,
        CAP_HAS_OPEN,
        CAP_HAS_SET_POSITION,
        CAP_HAS_SET_TILT_POSITION,
        CAP_HAS_STOP,
        caps_get,
    )

    cap_label_map = {
        CAP_HAS_SET_POSITION: "set position",
        CAP_HAS_SET_TILT_POSITION: "set tilt",
        CAP_HAS_OPEN: "open",
        CAP_HAS_CLOSE: "close",
        CAP_HAS_STOP: "stop",
    }

    lines: list[str] = ["**Cover Capabilities**"]
    for eid in entities:
        caps = cap_map.get(eid)
        if caps is None:
            lines.append(f"{eid}: not ready (unavailable)")
        else:
            cap_list = ", ".join(
                label for key, label in cap_label_map.items() if caps_get(caps, key)
            )
            lines.append(f"{eid}: {cap_list or 'none detected'}")

    if warnings:
        lines.extend(warnings)

    return "\n".join(lines)


async def _compute_todays_sun_times(hass: HomeAssistant, config: dict) -> dict | None:
    """Compute today's raw/effective sunrise/sunset + solar-control window.

    Runs the pandas/astral-heavy work in an executor. Returns ``None`` on any
    failure so the summary renders gracefully when location/astral data is
    unavailable. All returned datetimes are naive local (HA-configured TZ).
    """
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from .config_types import CoverConfig
    from .engine.sun_geometry import SunGeometry
    from .state.sun_provider import SunProvider

    def _to_local(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt_util.UTC)
        return dt_util.as_local(value).replace(tzinfo=None)

    def _compute() -> dict | None:
        try:
            sun_data = SunProvider(hass).create_sun_data(hass.config.time_zone)
            sunrise_raw_utc = sun_data.sunrise()
            sunset_raw_utc = sun_data.sunset()

            cfg = CoverConfig.from_options(config)
            geometry = SunGeometry(0.0, 0.0, sun_data, cfg, _LOGGER)
            solar_start_utc, solar_end_utc = geometry.solar_times()

            sunrise_local = _to_local(sunrise_raw_utc)
            sunset_local = _to_local(sunset_raw_utc)
            sunrise_eff = (
                sunrise_local + timedelta(minutes=int(cfg.sunrise_off))
                if sunrise_local is not None
                else None
            )
            sunset_eff = (
                sunset_local + timedelta(minutes=int(cfg.sunset_off))
                if sunset_local is not None
                else None
            )

            return {
                "sunrise_raw": sunrise_local,
                "sunset_raw": sunset_local,
                "sunrise_eff": sunrise_eff,
                "sunset_eff": sunset_eff,
                "solar_start": _to_local(solar_start_utc),
                "solar_end": _to_local(solar_end_utc),
            }
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to compute today's sun times", exc_info=True)
            return None

    return await hass.async_add_executor_job(_compute)


def _cover_type_label(
    sensor_type: str | None, labels: dict[str, str] | None = None
) -> str:
    """Return the human-readable label for a cover type, falling back to 'Cover'.

    ``labels`` is the translated ``cover_types.*`` bundle threaded from
    ``_build_config_summary``; ``None`` (entry titles, no flow context) keeps
    the policy's English default.
    """
    if sensor_type is not None and sensor_type in POLICY_REGISTRY:
        return get_policy(sensor_type).display_label(labels)
    return "Cover"


# ---------------------------------------------------------------------------
# Configuration-summary i18n (issue #258)
# ---------------------------------------------------------------------------
#
# Every user-facing phrase rendered by ``_build_config_summary`` lives here as a
# dotted-key → English template. This dict is BOTH:
#   * the single source of truth for the English output (so the 188 regression
#     tests in test_config_flow_summary.py stay byte-identical), AND
#   * the per-key fallback used when a translated key is missing/dropped.
#
# ``summary_i18n/en.json`` mirrors these exact keys/values as a nested tree. The
# flattened dotted keys (``rules`` → ``force`` → ``rules.force``) match the
# dotted keys here verbatim — see ``_load_summary_labels``. This data lives in a
# dedicated ``summary_i18n/`` bundle rather than under ``translations/`` because
# hassfest validates ``translations/en.json`` against HA's strict schema, which
# forbids a custom ``config_summary`` top-level category.
#
# Each value is a Python ``str.format`` template. Literal ``{`` / ``}`` that are
# NOT format fields (the cloud "weather in {set}" notation) are escaped as
# ``{{`` / ``}}``. Priority badges are NOT baked in — ``_badge(N)`` is appended
# AFTER ``.format()`` so handler-priority integers stay imported, never
# duplicated into translation strings.
#
# DEFERRED (stay English / policy-owned, translated in a follow-up):
#   * cover-type label (``policy.display_label()``)
#   * physical-dimension block (``policy.summary_geometry_lines()``)
#   * decision-priority short labels (Force/Weather/...) and the ✅/❌/→ marks
#   * cover-capability warning lines built in ``_check_cover_capabilities``
_SUMMARY_LABELS_EN: dict[str, str] = {
    # --- banners / section headers ---
    "banner.dry_run": (
        "⚠️ **Dry-run mode is ON** — positions are computed and logged, but "
        "no commands are sent and covers will NOT move."
    ),
    "headers.your_cover": "**Your Cover**",
    "cover.type_with_entities": "{type_label} controlling {entity_str}",
    "headers.cover_warnings": "**Cover Warnings**",
    "headers.how_it_decides": "**How It Decides** (first matching rule wins)",
    # --- singular/plural words ---
    "words.sensor_singular": "sensor",
    "words.sensor_plural": "sensors",
    "words.source_singular": "source",
    "words.source_plural": "sources",
    # --- shared fragments ---
    "fragments.as_minimum": " (as minimum)",
    "fragments.safety": " 🔒 safety: acts outside the time window too",
    "fragments.template_value": "[template]",
    # --- Weather safety (90) ---
    "rules.weather": (
        "🌧️ Weather safety: if {wx_condition} → covers retract to "
        "{weather_pos}%{weather_min}{delay}{bypass}"
    ),
    "weather.wind": "wind > {thresh}",
    "weather.wind_dir": " from window ±{tol}°",
    "weather.rain": "rain > {thresh}",
    "weather.is_raining": "is-raining",
    "weather.is_windy": "is-windy",
    "weather.severe": "{count} severe weather sensor(s)",
    "weather.condition_default": "weather condition",
    "weather.condition_join": " or ",
    "weather.delay": " (waits {delay}s after clearing)",
    "weather.bypass": " ⚠️ halts all automation while triggered",
    # --- Manual override (80) ---
    "rules.manual": (
        "✋ Manual override: pauses automatic control when you move the cover"
        "{detail}"
    ),
    "manual.pauses_for": "pauses for {duration}",
    "manual.threshold": "threshold {threshold}%",
    "manual.resets_on_move": "resets on next move",
    "manual.ignore_intermediate": "ignores intermediate positions",
    "manual.ignore_external": "ACP-only (ignores external moves)",
    "manual.transit_timeout": "transit timeout: {seconds}s",
    # --- Custom positions ---
    "rules.custom_tilt_only": (
        "🎯 Custom #{slot}: if {trigger} is on → tilt only "
        "(slat fixed at {slat}%; position driven by sun tracking)"
    ),
    "rules.custom": (
        "🎯 Custom #{slot}: if {trigger} is on → {target}{cp_min}{tilt_note}"
        " — bypasses delta gates and auto-control{safety}"
    ),
    "custom.tilt_note": ", tilt {tilt}%",
    "custom.trigger_sensors": "any of {n} sensors",
    "custom.trigger_template": "template",
    "custom.trigger_join": " or ",
    "warnings.custom_tilt_only_conflict": (
        "⚠️ Custom #{slot}: tilt only is on — "
        "Use as minimum / Use My position are ignored for this slot."
    ),
    "warnings.custom_and_no_sensors": (
        "⚠️ Custom #{slot}: combine mode AND is set but no trigger sensors are "
        "configured — the template alone activates the slot."
    ),
    # --- Motion (75) ---
    "rules.motion": (
        "🚶 Motion-based: if no occupancy for {motion_timeout}s "
        "({sources}) → {action}"
    ),
    "motion.template_source": "occupancy template",
    "motion.action_hold": (
        "covers hold current position (return to default when sun leaves FOV)"
    ),
    "motion.action_return": "covers return to default ({default_pos}%)",
    "warnings.motion_hold_no_sensors": (
        "⚠️ hold_position mode is set but no motion sensors or media "
        "players are configured — the setting has no effect until a "
        "motion source is added"
    ),
    # --- Cloud suppression (60) ---
    "rules.cloud": ("☁️ Cloud suppression: skips sun tracking{cloud} → {fallback}"),
    "cloud.is_sunny": "is_sunny={value}",
    "cloud.lux": "lux < {thresh} lx",
    "cloud.lux_no_thresh": "lux ({entity})",
    "cloud.irradiance": "irradiance < {thresh} W/m²",
    "cloud.irradiance_no_thresh": "irradiance ({entity})",
    "cloud.coverage": "cloud > {thresh}%",
    "cloud.coverage_no_thresh": "cloud ({entity})",
    "cloud.weather_in": "weather in {{{states}}}",
    "cloud.when": " when {parts}",
    "cloud.fallback_cloudy": "cloudy position {pos}%",
    "cloud.fallback_default": "default ({default_pos}%)",
    "info.light_sensors_off": (
        "📊 Light sensors configured ({names}) but cloud suppression is off."
    ),
    "info.light_lux": "lux",
    "info.light_irradiance": "irradiance",
    "info.light_cloud_coverage": "cloud coverage",
    "warnings.cloudy_pos_ignored": (
        "⚠️ Cloudy position ({pos}%) configured but cloud suppression is "
        "disabled — value will be ignored."
    ),
    # --- Climate (50) ---
    "rules.climate": ("🌡️ Climate mode: adjusts strategy for heating/cooling{detail}"),
    "climate.comfort_range": "comfort range {lo}–{hi}°C",
    "climate.using": "using {entity}",
    "climate.outside_thresh": "outside: {entity} > {thresh}°C",
    "climate.outside": "outside: {entity}",
    "climate.weather": "weather: {entity}",
    "climate.presence": "presence: {entity}",
    "climate.transparent": "transparent blind",
    "climate.winter_close": "closes fully in winter for insulation",
    # --- Glare (45) ---
    "rules.glare": (
        "🔆 Glare zones: lowers blind further to protect floor areas from "
        "glare{detail}"
    ),
    "glare.zones": "zones: {names}",
    "glare.window": "{width:.2f}m window",
    "glare.z_height": "Z height: {values}",
    "glare.z_value": "{z:.2f}m",
    # --- Solar (40) ---
    "rules.solar": (
        "☀️ Tracks the sun{sun_desc} and calculates position to block "
        "direct sunlight{today}"
    ),
    "rules.solar_disabled": (
        "☀️ Sun tracking disabled — covers hold position; climate, manual "
        "override, custom positions, and other overrides remain active"
    ),
    "solar.azimuth": "azimuth {azimuth}°",
    "solar.fov": "±{fov_l}°/{fov_r}° field of view",
    "solar.elev_above": "above {elev}°",
    "solar.elev_below": "below {elev}°",
    "solar.elevation": "elevation {parts}",
    "solar.elev_join": " and ",
    "solar.today_window": (" (today: sun in window {start} → {end})"),
    "solar.today_no_window": " (today: sun does not enter window)",
    "solar.minimize_one_step": "moves straight to full coverage and holds (1 step)",
    "solar.minimize_steps": "reaches full coverage in up to {steps} steps",
    "solar.minimize": (
        "{indent}🪟 Minimize movements — {detail}, rounding toward more "
        "coverage to reduce motor movements."
    ),
    # --- Timing window ---
    "timing.from_entity": "from {entity}",
    "timing.from_time": "from {time}",
    "timing.until_entity": "until {entity}",
    "timing.until_time": "until {time}",
    "timing.active_daylight": "Active during daylight",
    "timing.line": "{indent}🕒 {timing}.",
    "timing.ann_via": "via {entity}",
    "timing.ann_today": "today ~{time}",
    "timing.offset_plus": "+{minutes} min",
    "timing.offset_minus": "{minutes} min",
    "timing.after_end_to_default": "{indent}🔚 After end time → {default_pos}%.",
    "timing.after_sunset": "{indent}🌅 After sunset{ann} → {target}.",
    "timing.after_label": "{indent}🌅 After {label}{ann} → {target}.",
    "timing.label_end_or_sunset": "end time/sunset",
    "timing.label_sunset": "sunset",
    "timing.after_sunrise": (
        "{indent}🌄 After sunrise{ann} → {default_pos}% (tracking resumes)."
    ),
    "timing.return_sunset": "{indent}🔚 Return to sunset position at end time: on",
    # --- Blind spot ---
    "blind_spot.line": (
        "🟥 Blind spot: ignores sun at {bs} inward from FOV left (e.g. tree "
        "or roof overhang)."
    ),
    "blind_spot.range": "{left}°–{right}°",
    "blind_spot.elevation": "up to {elev}° elevation",
    # --- Default fallback (0) ---
    "rules.default": "🌙 Default (no rule matches) → {default_pos}%",
    "default.tilt": ("  ↳ Default tilt: {tilt}% (explicit; overrides solar-computed)"),
    "default.sunset_tilt": (
        "  ↳ Sunset tilt: {tilt}% (explicit; overrides solar-computed)"
    ),
    # --- Position limits ---
    "headers.position_limits": "**Position Limits**",
    "limits.range": "Range: {lo}–{hi}{qualifier}",
    "limits.qualifier_both": " (during sun tracking only)",
    "limits.qualifier_min": " (min during sun tracking only)",
    "limits.qualifier_max": " (max during sun tracking only)",
    "limits.default": "Default: {pos}%",
    "limits.min_change": "Min change: {delta}%",
    "limits.min_interval": "Min interval: {delta} min",
    "limits.position_tolerance": "Position tolerance: {tol}%",
    "limits.position_matching_on": "📍 Position matching on (re-sends until the cover reaches target)",
    "limits.position_matching_off": "📍 Position matching off (commands once; a settle past tolerance becomes a manual override)",
    "limits.inverse_state": "Inverse state",
    "limits.open_close_threshold": "Open/close threshold: {thresh}%",
    "limits.calibration": "Calibration {lo}→{hi}",
    "limits.calibration_on": "Position calibration on",
    "limits.sun_tracking_min": "Sun-tracking min: {pos}%",
    "limits.separator": " · ",
    "warnings.sun_track_min_below_floor": (
        "⚠️ Sun-tracking min {sun_min}% < min position {min_pos}% — "
        "always-on floor dominates; sun-tracking floor will be raised to "
        "{min_pos}%."
    ),
    "warnings.mode2_min_position": (
        "⚠️ Tilt MODE2 + min position {min_pos}% — in MODE2 the open "
        "(horizontal) slat angle IS 50%, so any min position ≥ 50 "
        "collapses every climate/glare-control decision to the floor "
        "and the cover stops blocking heat."
    ),
    # --- My preset / Somfy ---
    "my.entities_enabled": "🎛️ My-preset entities: enabled",
    "my.entities_disabled": "🎛️ My-preset entities: disabled",
    "my.somfy_preset": "🎛️ Somfy My preset: {pos}% (used where enabled above)",
    "my.label_my_set": "My ({pos}%)",
    "my.label_my_unset": "My (not set → {pct}%)",
    "my.label_plain": "{pct}%",
    "warnings.somfy_my_unset": (
        "⚠️ Somfy My preset is enabled for one or more targets but "
        "My Preset Value is not set — falls back to configured %."
    ),
    # --- Proxy cover ---
    "headers.proxy_enabled": "**Proxy cover**: enabled",
    "headers.proxy_disabled": "**Proxy cover**: disabled",
    "warnings.proxy_no_min": (
        "⚠️ Proxy cover is enabled but no custom-position slot has "
        "Use as minimum on — the managed cover will not clamp."
    ),
    # --- Decision priority chain ---
    "headers.decision_priority": (
        "**Decision Priority** (highest wins, ✅ active ❌ not configured)"
    ),
}


_SUMMARY_I18N_DIR = Path(__file__).parent / "summary_i18n"


def _flatten_summary_labels(node: object, prefix: str = "") -> dict[str, str]:
    """Flatten a nested label tree to dotted keys (``rules.force`` → template)."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(
                _flatten_summary_labels(value, f"{prefix}.{key}" if prefix else key)
            )
    elif isinstance(node, str):
        out[prefix] = node
    return out


@cache
def _summary_label_overlay(language: str) -> tuple[tuple[str, str], ...]:
    """Return the flattened ``summary_i18n/<language>.json`` bundle.

    Cached (the bundles are shipped, read-only) and returned as a tuple of items
    so the cached value cannot be mutated by callers. ``en`` and any missing or
    malformed file yield an empty overlay — the English defaults then apply.
    """
    if not language or language == "en":
        return ()
    path = _SUMMARY_I18N_DIR / f"{language}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(_flatten_summary_labels(data).items())


def _load_summary_labels_sync(language: str) -> dict[str, str]:
    """Build the config-summary labels for ``language``.

    English defaults overlaid with the translated bundle. Pure/synchronous —
    safe to unit-test directly.
    """
    return {**_SUMMARY_LABELS_EN, **dict(_summary_label_overlay(language))}


async def _load_summary_labels(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Load the translated config-summary labels for ``language``.

    The labels live in the integration's ``summary_i18n/`` bundle (a custom
    ``config_summary`` category cannot live under ``translations/`` — hassfest
    rejects it). This overlays the language bundle onto the English defaults so
    any missing key falls back to English. ``language`` is the per-user flow
    language (``self.context.get("language", "en")``) — never the system
    language. File I/O is offloaded to the executor.

    Both ``ConfigFlow.async_step_summary`` and ``OptionsFlow.async_step_summary``
    call this single helper (no duplication).
    """
    return await hass.async_add_executor_job(_load_summary_labels_sync, language)


def _build_config_summary(  # noqa: C901, PLR0912, PLR0915
    config: dict,
    sensor_type: str | None,
    hass: HomeAssistant | None = None,
    sun_times: dict | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """Build a narrative summary of the current configuration.

    Produces four sections:
      1. Your Cover  — what is controlled and physical setup
      2. How It Decides — full decision chain: each rule's trigger, target, and
         today's sun times inline; priority badge [N] at end of each rule
      3. Position Limits — compact one-liner for range/default/delta/flags
      4. Decision Priority — compact chain showing active/inactive handlers

    ``labels`` maps the summary's dotted keys to translated templates. When
    ``None`` (unit tests, no hass) the English defaults in ``_SUMMARY_LABELS_EN``
    are used, so the output is byte-identical to the pre-i18n strings.
    """
    L = labels or _SUMMARY_LABELS_EN
    _ph = L["fragments.template_value"]
    # ---- Gather all values up front ----------------------------------------
    # ``L`` here is the FULL flow bundle (``_SUMMARY_LABELS_EN`` keys + the
    # policy-owned ``cover_types.*`` / ``geometry.*`` keys when a translated
    # bundle is loaded). The policies layer it over their own English base
    # (``COVER_TYPE_LABELS_EN`` / ``GEOMETRY_LABELS_EN``), so passing ``L`` —
    # even the policy-key-less ``_SUMMARY_LABELS_EN`` default — still yields
    # English for the policy lines while translating everything present.
    type_label = _cover_type_label(sensor_type, L)

    entities: list[str] = config.get(CONF_ENTITIES) or []
    default_pos = config.get(CONF_DEFAULT_HEIGHT, 0)
    weather_pos = config.get(CONF_WEATHER_OVERRIDE_POSITION, 0)
    motion_timeout = config.get(CONF_MOTION_TIMEOUT, 300)
    manual_dur = config.get(CONF_MANUAL_OVERRIDE_DURATION)

    has_weather = any(
        [
            config.get(CONF_WEATHER_WIND_SPEED_SENSOR),
            config.get(CONF_WEATHER_RAIN_SENSOR),
            config.get(CONF_WEATHER_IS_RAINING_SENSOR),
            config.get(CONF_WEATHER_IS_WINDY_SENSOR),
            bool(config.get(CONF_WEATHER_SEVERE_SENSORS)),
        ]
    )
    from .helpers import motion_entities
    from .templates import is_template_string

    def _thresh_display(value: Any, *, placeholder: str) -> str:
        return placeholder if is_template_string(str(value)) else str(value)

    _motion_sources = motion_entities(config)
    _has_motion_template = is_template_string(config.get(CONF_MOTION_TEMPLATE))
    has_motion = bool(_motion_sources) or _has_motion_template
    # Build per-slot custom position data:
    # list of (slot, trigger_desc, position, priority, use_my, tilt, tilt_only)
    _custom_slots: list[tuple[int, str, int, int, bool, int | None, bool]] = []
    _and_no_sensor_slots: list[int] = []
    for _i, _slot_keys in CUSTOM_POSITION_SLOTS.items():
        if not custom_position_slot_configured(config, _slot_keys):
            continue
        _sensors = custom_position_slot_sensors(config, _slot_keys)
        _has_tpl = is_template_string(config.get(_slot_keys["template"]))
        # Footgun: AND mode with no sensors degenerates to template-only.
        if (
            _has_tpl
            and not _sensors
            and config.get(_slot_keys["template_mode"]) == "and"
        ):
            _and_no_sensor_slots.append(_i)
        _trigger_parts: list[str] = []
        if len(_sensors) == 1:
            _trigger_parts.append(_sensors[0])
        elif _sensors:
            _trigger_parts.append(L["custom.trigger_sensors"].format(n=len(_sensors)))
        if _has_tpl:
            _trigger_parts.append(L["custom.trigger_template"])
        _trigger = L["custom.trigger_join"].join(_trigger_parts)
        _pos = config.get(_slot_keys["position"])
        _pri = int(
            config.get(_slot_keys["priority"]) or DEFAULT_CUSTOM_POSITION_PRIORITY
        )
        _use_my = bool(config.get(_slot_keys["use_my"]))
        _slot_tilt = config.get(_slot_keys["tilt"])
        _tilt_only = bool(config.get(_slot_keys["tilt_only"]))
        _custom_slots.append(
            (_i, _trigger, int(_pos), _pri, _use_my, _slot_tilt, _tilt_only)
        )
    has_custom_position = bool(_custom_slots)
    my_pos = config.get(CONF_MY_POSITION_VALUE)  # None = not configured
    has_cloud = bool(config.get(CONF_CLOUD_SUPPRESSION))
    has_climate = bool(config.get(CONF_CLIMATE_MODE))
    sun_tracking_enabled = config.get(CONF_ENABLE_SUN_TRACKING, True)
    summary_policy = (
        get_policy(sensor_type)
        if sensor_type is not None and sensor_type in POLICY_REGISTRY
        else BlindPolicy()
    )
    has_glare = summary_policy.supports_glare_zones and bool(
        config.get(CONF_ENABLE_GLARE_ZONES)
    )

    def _pos_label(raw_pct: int, use_my: bool) -> str:
        """Render a target as 'My (N%)' when the My preset flag is active."""
        if use_my and my_pos is not None:
            return L["my.label_my_set"].format(pos=my_pos)
        if use_my:
            return L["my.label_my_unset"].format(pct=raw_pct)
        return L["my.label_plain"].format(pct=raw_pct)

    def _badge(priority: int) -> str:
        """Render a priority badge suffix: two nbsp + [N]."""
        return f"\u00a0\u00a0[{priority}]"

    def _fmt_sun_dt(value) -> str | None:
        """Format a sun-times datetime as HH:MM; None passes through."""
        return value.strftime("%H:%M") if value is not None else None

    def _offset_str(minutes: int) -> str:
        """Format a minutes offset as (+N min) / (-N min); 0 → empty."""
        if minutes > 0:
            return L["timing.offset_plus"].format(minutes=minutes)
        if minutes < 0:
            return L["timing.offset_minus"].format(minutes=minutes)
        return ""

    _solar_start = sun_times.get("solar_start") if sun_times else None
    _solar_end = sun_times.get("solar_end") if sun_times else None
    _sunset_eff = sun_times.get("sunset_eff") if sun_times else None
    _sunrise_eff = sun_times.get("sunrise_eff") if sun_times else None

    lines: list[str] = []

    # Dry-run banner — surfaced first because it overrides everything below: when
    # on, the full decision chain is still computed and logged but no commands are
    # sent, so covers never move. Without this the summary reads as if it drives
    # covers regardless of the dry-run toggle on the Debug screen.
    if config.get(CONF_DRY_RUN):
        lines.append(L["banner.dry_run"])
        lines.append("")

    # =========================================================================
    # Section 1: Your Cover
    # =========================================================================
    lines.append(L["headers.your_cover"])

    # Type + entities
    if entities:
        entity_str = ", ".join(entities)
        lines.append(
            L["cover.type_with_entities"].format(
                type_label=type_label, entity_str=entity_str
            )
        )
    else:
        lines.append(type_label)

    # Physical dimensions in plain English. The render mode is per-cover-type;
    # each ``CoverTypePolicy.summary_geometry_lines`` owns its block. Legacy
    # configs without ``sensor_type`` fall back to the vertical-blind layout
    # via ``summary_policy`` chosen at the top of this function. ``L`` threads
    # the translated ``geometry.*`` bundle (or the policy-key-less EN default,
    # which still renders English over the policy's own base layer).
    lines.extend(summary_policy.summary_geometry_lines(config, L))

    # =========================================================================
    # Section 1c: Cover Capability Warnings
    # =========================================================================
    _, cap_warnings = _check_cover_capabilities(config, sensor_type, hass)
    if cap_warnings:
        lines.append("")
        lines.append(L["headers.cover_warnings"])
        lines.extend(cap_warnings)

    # =========================================================================
    # Section 2: How It Decides
    # =========================================================================
    lines.append("")
    lines.append(L["headers.how_it_decides"])

    # Weather safety override (90)
    if has_weather:
        wx_parts = []
        wind_sensor = config.get(CONF_WEATHER_WIND_SPEED_SENSOR)
        wind_thresh = config.get(CONF_WEATHER_WIND_SPEED_THRESHOLD)
        wind_dir_sensor = config.get(CONF_WEATHER_WIND_DIRECTION_SENSOR)
        wind_dir_tol = config.get(CONF_WEATHER_WIND_DIRECTION_TOLERANCE)
        rain_sensor = config.get(CONF_WEATHER_RAIN_SENSOR)
        rain_thresh = config.get(CONF_WEATHER_RAIN_THRESHOLD)
        is_rain = config.get(CONF_WEATHER_IS_RAINING_SENSOR)
        is_wind = config.get(CONF_WEATHER_IS_WINDY_SENSOR)
        severe = config.get(CONF_WEATHER_SEVERE_SENSORS) or []
        if wind_sensor and wind_thresh is not None:
            wind_part = L["weather.wind"].format(
                thresh=_thresh_display(wind_thresh, placeholder=_ph)
            )
            if wind_dir_sensor and wind_dir_tol is not None:
                wind_part += L["weather.wind_dir"].format(
                    tol=_thresh_display(wind_dir_tol, placeholder=_ph)
                )
            wx_parts.append(wind_part)
        if rain_sensor and rain_thresh is not None:
            wx_parts.append(
                L["weather.rain"].format(
                    thresh=_thresh_display(rain_thresh, placeholder=_ph)
                )
            )
        if is_rain:
            wx_parts.append(L["weather.is_raining"])
        if is_wind:
            wx_parts.append(L["weather.is_windy"])
        if severe:
            wx_parts.append(L["weather.severe"].format(count=len(severe)))
        wx_condition = (
            L["weather.condition_join"].join(wx_parts)
            if wx_parts
            else L["weather.condition_default"]
        )
        wx_delay = config.get(CONF_WEATHER_TIMEOUT)
        delay_str = L["weather.delay"].format(delay=wx_delay) if wx_delay else ""
        weather_min_str = (
            L["fragments.as_minimum"]
            if config.get(CONF_WEATHER_OVERRIDE_MIN_MODE)
            else ""
        )
        bypass_str = (
            L["weather.bypass"] if config.get(CONF_WEATHER_BYPASS_AUTO_CONTROL) else ""
        )
        lines.append(
            L["rules.weather"].format(
                wx_condition=wx_condition,
                weather_pos=weather_pos,
                weather_min=weather_min_str,
                delay=delay_str,
                bypass=bypass_str,
            )
            + _badge(90)
        )

    # Manual override (80)
    mo_parts = []
    if manual_dur is not None:
        mo_parts.append(
            L["manual.pauses_for"].format(duration=_format_duration(manual_dur))
        )
    threshold = config.get(CONF_MANUAL_THRESHOLD)
    if threshold is not None:
        mo_parts.append(L["manual.threshold"].format(threshold=threshold))
    if config.get(CONF_MANUAL_OVERRIDE_RESET):
        mo_parts.append(L["manual.resets_on_move"])
    if config.get(CONF_MANUAL_IGNORE_INTERMEDIATE):
        mo_parts.append(L["manual.ignore_intermediate"])
    if config.get(CONF_MANUAL_IGNORE_EXTERNAL):
        mo_parts.append(L["manual.ignore_external"])
    transit_timeout = config.get(CONF_TRANSIT_TIMEOUT)
    if (
        transit_timeout is not None
        and int(transit_timeout) != DEFAULT_TRANSIT_TIMEOUT_SECONDS
    ):
        mo_parts.append(
            L["manual.transit_timeout"].format(seconds=int(transit_timeout))
        )
    mo_str = f" ({', '.join(mo_parts)})" if mo_parts else ""
    lines.append(L["rules.manual"].format(detail=mo_str) + _badge(80))

    # Custom positions — each slot at its own configured priority
    if has_custom_position:
        for (
            _slot,
            _trigger,
            _pos,
            _pri,
            _use_my,
            _slot_tilt,
            _tilt_only,
        ) in _custom_slots:
            tilt_note = (
                L["custom.tilt_note"].format(tilt=_slot_tilt)
                if _slot_tilt is not None
                else ""
            )
            # Priority-100 slots inherit the old force-override safety
            # semantics — flag it inline so the behavior is discoverable.
            safety_note = (
                L["fragments.safety"] if _pri >= CUSTOM_POSITION_SAFETY_PRIORITY else ""
            )
            if _tilt_only:
                # Tilt-only fixes the slat angle and lets the position pipeline
                # (solar etc.) drive the carriage — min_mode/use_my are ignored.
                slat = _slot_tilt if _slot_tilt is not None else 0
                lines.append(
                    L["rules.custom_tilt_only"].format(
                        slot=_slot, trigger=_trigger, slat=slat
                    )
                    + _badge(_pri)
                )
            else:
                target = _pos_label(_pos, _use_my)
                cp_min = (
                    L["fragments.as_minimum"]
                    if config.get(f"custom_position_min_mode_{_slot}")
                    else ""
                )
                lines.append(
                    L["rules.custom"].format(
                        slot=_slot,
                        trigger=_trigger,
                        target=target,
                        cp_min=cp_min,
                        tilt_note=tilt_note,
                        safety=safety_note,
                    )
                    + _badge(_pri)
                )
        # Mutual-exclusion warning: tilt_only wins over min_mode / use_my
        # (issue #514). Surface the conflict so the user knows the latter two
        # are ignored for that slot.
        for (
            _slot,
            _trigger,
            _pos,
            _pri,
            _use_my,
            _slot_tilt,
            _tilt_only,
        ) in _custom_slots:
            if _tilt_only and (
                config.get(f"custom_position_min_mode_{_slot}") or _use_my
            ):
                lines.append(L["warnings.custom_tilt_only_conflict"].format(slot=_slot))
        # Footgun warning: AND combine mode with no sensors — the template
        # gates nothing and the slot degenerates to template-only OR.
        for _slot in _and_no_sensor_slots:
            lines.append(L["warnings.custom_and_no_sensors"].format(slot=_slot))

    # Motion timeout (75)
    timeout_mode = config.get(CONF_MOTION_TIMEOUT_MODE, DEFAULT_MOTION_TIMEOUT_MODE)
    if has_motion:
        n = len(_motion_sources)
        sensor_word = L["words.source_singular"] if n == 1 else L["words.source_plural"]
        src_parts = []
        if n:
            src_parts.append(f"{n} {sensor_word}")
        if _has_motion_template:
            src_parts.append(L["motion.template_source"])
        sources = ", ".join(src_parts)
        if timeout_mode == MOTION_TIMEOUT_MODE_HOLD:
            action = L["motion.action_hold"]
        else:
            action = L["motion.action_return"].format(default_pos=default_pos)
        lines.append(
            L["rules.motion"].format(
                motion_timeout=motion_timeout,
                sources=sources,
                action=action,
            )
            + _badge(75)
        )
    elif timeout_mode == MOTION_TIMEOUT_MODE_HOLD:
        lines.append(L["warnings.motion_hold_no_sensors"])

    # Cloud suppression (60)
    if has_cloud:
        cloud_parts = []
        if v := config.get(CONF_IS_SUNNY_SENSOR):
            cloud_parts.append(L["cloud.is_sunny"].format(value=v))
        if v := config.get(CONF_LUX_ENTITY):
            t = config.get(CONF_LUX_THRESHOLD)
            cloud_parts.append(
                L["cloud.lux"].format(thresh=_thresh_display(t, placeholder=_ph))
                if t is not None
                else L["cloud.lux_no_thresh"].format(entity=v)
            )
        if v := config.get(CONF_IRRADIANCE_ENTITY):
            t = config.get(CONF_IRRADIANCE_THRESHOLD)
            cloud_parts.append(
                L["cloud.irradiance"].format(thresh=_thresh_display(t, placeholder=_ph))
                if t is not None
                else L["cloud.irradiance_no_thresh"].format(entity=v)
            )
        if v := config.get(CONF_CLOUD_COVERAGE_ENTITY):
            t = config.get(CONF_CLOUD_COVERAGE_THRESHOLD)
            cloud_parts.append(
                L["cloud.coverage"].format(thresh=_thresh_display(t, placeholder=_ph))
                if t is not None
                else L["cloud.coverage_no_thresh"].format(entity=v)
            )
        wx_states = config.get(CONF_WEATHER_STATE) or []
        if wx_states and config.get(CONF_WEATHER_ENTITY):
            cloud_parts.append(
                L["cloud.weather_in"].format(states=", ".join(wx_states))
            )
        cloud_str = (
            L["cloud.when"].format(parts=", ".join(cloud_parts)) if cloud_parts else ""
        )
        cloudy_pos = config.get(CONF_CLOUDY_POSITION)
        if cloudy_pos is not None:
            fallback_label = L["cloud.fallback_cloudy"].format(pos=cloudy_pos)
        else:
            fallback_label = L["cloud.fallback_default"].format(default_pos=default_pos)
        lines.append(
            L["rules.cloud"].format(cloud=cloud_str, fallback=fallback_label)
            + _badge(60)
        )
    elif any(
        [
            config.get(CONF_LUX_ENTITY),
            config.get(CONF_IRRADIANCE_ENTITY),
            config.get(CONF_CLOUD_COVERAGE_ENTITY),
            config.get(CONF_IS_SUNNY_SENSOR),
        ]
    ):
        # Sensors configured but suppression toggle off — mention them as informational
        sensor_names = []
        if config.get(CONF_LUX_ENTITY):
            sensor_names.append(L["info.light_lux"])
        if config.get(CONF_IRRADIANCE_ENTITY):
            sensor_names.append(L["info.light_irradiance"])
        if config.get(CONF_CLOUD_COVERAGE_ENTITY):
            sensor_names.append(L["info.light_cloud_coverage"])
        if v := config.get(CONF_IS_SUNNY_SENSOR):
            sensor_names.append(v)
        lines.append(L["info.light_sensors_off"].format(names=", ".join(sensor_names)))

    # Warn if cloudy_position set but cloud suppression is disabled
    cloudy_pos_cfg = config.get(CONF_CLOUDY_POSITION)
    if cloudy_pos_cfg is not None and not has_cloud:
        lines.append(L["warnings.cloudy_pos_ignored"].format(pos=cloudy_pos_cfg))

    # Climate mode (50)
    if has_climate:
        cl_parts = []
        lo = config.get(CONF_TEMP_LOW)
        hi = config.get(CONF_TEMP_HIGH)
        temp_entity = config.get(CONF_TEMP_ENTITY)
        if lo is not None and hi is not None:
            cl_parts.append(
                L["climate.comfort_range"].format(
                    lo=_thresh_display(lo, placeholder=_ph),
                    hi=_thresh_display(hi, placeholder=_ph),
                )
            )
        if temp_entity:
            cl_parts.append(L["climate.using"].format(entity=temp_entity))
        outside = config.get(CONF_OUTSIDETEMP_ENTITY)
        if outside:
            out_thresh = config.get(CONF_OUTSIDE_THRESHOLD)
            if out_thresh is not None:
                cl_parts.append(
                    L["climate.outside_thresh"].format(
                        entity=outside,
                        thresh=_thresh_display(out_thresh, placeholder=_ph),
                    )
                )
            else:
                cl_parts.append(L["climate.outside"].format(entity=outside))
        weather_ent = config.get(CONF_WEATHER_ENTITY)
        if weather_ent:
            cl_parts.append(L["climate.weather"].format(entity=weather_ent))
        presence = config.get(CONF_PRESENCE_ENTITY)
        if presence:
            cl_parts.append(L["climate.presence"].format(entity=presence))
        if config.get(CONF_TRANSPARENT_BLIND):
            cl_parts.append(L["climate.transparent"])
        if config.get(CONF_WINTER_CLOSE_INSULATION):
            cl_parts.append(L["climate.winter_close"])
        cl_str = f" ({', '.join(cl_parts)})" if cl_parts else ""
        lines.append(L["rules.climate"].format(detail=cl_str) + _badge(50))

    # Glare zones — vertical only (45, below climate)
    if has_glare:
        zone_names = [
            config.get(f"glare_zone_{i}_name")
            for i in range(1, 5)
            if config.get(f"glare_zone_{i}_name")
        ]
        width = config.get(CONF_WINDOW_WIDTH)
        gz_parts = []
        if zone_names:
            gz_parts.append(L["glare.zones"].format(names=", ".join(zone_names)))
        if width:
            gz_parts.append(L["glare.window"].format(width=float(width)))
        z_values = [
            float(config.get(f"glare_zone_{i}_z") or 0.0)
            for i in range(1, 5)
            if config.get(f"glare_zone_{i}_name")
        ]
        if any(z > 0 for z in z_values):
            gz_parts.append(
                L["glare.z_height"].format(
                    values=", ".join(L["glare.z_value"].format(z=z) for z in z_values)
                )
            )
        gz_str = f" ({', '.join(gz_parts)})" if gz_parts else ""
        lines.append(L["rules.glare"].format(detail=gz_str) + _badge(45))

    # Solar tracking — baseline calculation (40)
    azimuth = config.get(CONF_AZIMUTH)
    fov_l = config.get(CONF_FOV_LEFT)
    fov_r = config.get(CONF_FOV_RIGHT)
    min_elev = config.get(CONF_MIN_ELEVATION)
    max_elev = config.get(CONF_MAX_ELEVATION)
    if sun_tracking_enabled:
        sun_parts = []
        if azimuth is not None:
            sun_parts.append(L["solar.azimuth"].format(azimuth=azimuth))
        if fov_l is not None and fov_r is not None:
            sun_parts.append(L["solar.fov"].format(fov_l=fov_l, fov_r=fov_r))
        elev_parts = []
        if min_elev is not None:
            elev_parts.append(L["solar.elev_above"].format(elev=min_elev))
        if max_elev is not None:
            elev_parts.append(L["solar.elev_below"].format(elev=max_elev))
        if elev_parts:
            sun_parts.append(
                L["solar.elevation"].format(parts=L["solar.elev_join"].join(elev_parts))
            )
        sun_desc = f" ({', '.join(sun_parts)})" if sun_parts else ""
        # Today's solar window annotation
        if _solar_start is not None and _solar_end is not None:
            today_str = L["solar.today_window"].format(
                start=_fmt_sun_dt(_solar_start), end=_fmt_sun_dt(_solar_end)
            )
        elif sun_times is not None:
            today_str = L["solar.today_no_window"]
        else:
            today_str = ""
        lines.append(
            L["rules.solar"].format(sun_desc=sun_desc, today=today_str) + _badge(40)
        )
        if config.get(CONF_MINIMIZE_MOVEMENTS, False):
            steps = int(config.get(CONF_MAX_COVERAGE_STEPS, 1))
            indent = "\u00a0" * 4
            if steps <= 1:
                detail = L["solar.minimize_one_step"]
            else:
                detail = L["solar.minimize_steps"].format(steps=steps)
            lines.append(L["solar.minimize"].format(indent=indent, detail=detail))
    else:
        lines.append(L["rules.solar_disabled"] + _badge(40))

    # Timing window (sub-bullet under ☀️)
    start_time = config.get(CONF_START_TIME)
    start_entity = config.get(CONF_START_ENTITY)
    end_time = config.get(CONF_END_TIME)
    end_entity = config.get(CONF_END_ENTITY)
    sunset_pos = config.get(CONF_SUNSET_POS)
    sunset_off = config.get(CONF_SUNSET_OFFSET, 0) or 0
    sunrise_off = config.get(CONF_SUNRISE_OFFSET, 0) or 0
    sunset_time_entity = config.get(CONF_SUNSET_TIME_ENTITY)
    sunrise_time_entity = config.get(CONF_SUNRISE_TIME_ENTITY)
    timing_parts = []
    if start_entity:
        timing_parts.append(L["timing.from_entity"].format(entity=start_entity))
    elif start_time and start_time != BLANK_TIME:
        timing_parts.append(L["timing.from_time"].format(time=start_time))
    if end_entity:
        timing_parts.append(L["timing.until_entity"].format(entity=end_entity))
    elif end_time and end_time != BLANK_TIME:
        timing_parts.append(L["timing.until_time"].format(time=end_time))
    # A schedule key present but blank (cleared TimeSelector → "00:00:00") still
    # means the user configured the automation window — show "Active during
    # daylight" rather than nothing, so the summary reflects the real behavior
    # (issue #492). CONF_*_TIME default to BLANK_TIME, so test membership too.
    schedule_configured = any(
        config.get(key) not in (None, BLANK_TIME)
        for key in (CONF_START_ENTITY, CONF_END_ENTITY)
    ) or any(key in config for key in (CONF_START_TIME, CONF_END_TIME))
    if timing_parts or sunset_pos is not None or schedule_configured:
        timing_str = (
            " ".join(timing_parts) if timing_parts else L["timing.active_daylight"]
        )
        indent = "\u00a0" * 4
        lines.append(L["timing.line"].format(indent=indent, timing=timing_str))
        if sunset_pos is not None:
            # Merge today's effective time (or entity ID) and offset into one parenthetical
            def _sun_annotation(
                today_dt, offset_min: int, entity_id: str | None = None
            ) -> str:
                parts = []
                if entity_id is not None:
                    parts.append(L["timing.ann_via"].format(entity=entity_id))
                elif today_dt is not None:
                    parts.append(
                        L["timing.ann_today"].format(time=_fmt_sun_dt(today_dt))
                    )
                off = _offset_str(int(offset_min))
                if off:
                    parts.append(off)
                return f" ({', '.join(parts)})" if parts else ""

            sunset_ann = _sun_annotation(_sunset_eff, sunset_off, sunset_time_entity)
            sunrise_ann = _sun_annotation(
                _sunrise_eff, sunrise_off, sunrise_time_entity
            )
            has_end_time = bool(end_time or end_entity)
            _sunset_use_my = bool(config.get(CONF_SUNSET_USE_MY))
            _sunset_target = _pos_label(int(sunset_pos), _sunset_use_my)
            if has_end_time and int(sunset_pos) != int(default_pos):
                lines.append(
                    L["timing.after_end_to_default"].format(
                        indent=indent, default_pos=default_pos
                    )
                )
                lines.append(
                    L["timing.after_sunset"].format(
                        indent=indent, ann=sunset_ann, target=_sunset_target
                    )
                )
            else:
                label = (
                    L["timing.label_end_or_sunset"]
                    if has_end_time
                    else L["timing.label_sunset"]
                )
                lines.append(
                    L["timing.after_label"].format(
                        indent=indent,
                        label=label,
                        ann=sunset_ann,
                        target=_sunset_target,
                    )
                )
            lines.append(
                L["timing.after_sunrise"].format(
                    indent=indent, ann=sunrise_ann, default_pos=default_pos
                )
            )
            if config.get(CONF_RETURN_SUNSET):
                lines.append(L["timing.return_sunset"].format(indent=indent))

    # Blind spot (sub-bullet / informational, no priority of its own)
    if config.get(CONF_ENABLE_BLIND_SPOT):
        bs_l = config.get(CONF_BLIND_SPOT_LEFT)
        bs_r = config.get(CONF_BLIND_SPOT_RIGHT)
        bs_e = config.get(CONF_BLIND_SPOT_ELEVATION)
        bs_parts = []
        if bs_l is not None and bs_r is not None:
            bs_parts.append(L["blind_spot.range"].format(left=bs_l, right=bs_r))
        if bs_e is not None:
            bs_parts.append(L["blind_spot.elevation"].format(elev=bs_e))
        bs_str = " ".join(bs_parts)
        lines.append(L["blind_spot.line"].format(bs=bs_str))

    # Default fallback (priority 0) — shown as the final row of the chain
    lines.append(L["rules.default"].format(default_pos=default_pos) + _badge(0))
    # Explicit tilt for venetian covers (solar-computed when absent)
    _default_tilt = config.get(CONF_DEFAULT_TILT)
    _sunset_tilt = config.get(CONF_SUNSET_TILT)
    if _default_tilt is not None:
        lines.append(L["default.tilt"].format(tilt=_default_tilt))
    if _sunset_tilt is not None:
        lines.append(L["default.sunset_tilt"].format(tilt=_sunset_tilt))

    # =========================================================================
    # Section 3: Position Limits
    # =========================================================================
    limit_parts = []
    min_pos = config.get(CONF_MIN_POSITION)
    max_pos = config.get(CONF_MAX_POSITION)
    enable_min = config.get(CONF_ENABLE_MIN_POSITION)
    enable_max = config.get(CONF_ENABLE_MAX_POSITION)
    if min_pos is not None or max_pos is not None:
        lo_str = f"{min_pos}%" if min_pos is not None else "0%"
        hi_str = f"{max_pos}%" if max_pos is not None else "100%"
        # Per-side tracking-only qualifier for precision
        if enable_min and enable_max:
            qualifier = L["limits.qualifier_both"]
        elif enable_min and not enable_max:
            qualifier = L["limits.qualifier_min"]
        elif enable_max and not enable_min:
            qualifier = L["limits.qualifier_max"]
        else:
            qualifier = ""
        limit_parts.append(
            L["limits.range"].format(lo=lo_str, hi=hi_str, qualifier=qualifier)
        )
    if default_pos is not None:
        limit_parts.append(L["limits.default"].format(pos=default_pos))
    delta_pos = config.get(CONF_DELTA_POSITION)
    delta_time = config.get(CONF_DELTA_TIME)
    if delta_pos is not None:
        limit_parts.append(L["limits.min_change"].format(delta=delta_pos))
    if delta_time is not None:
        limit_parts.append(L["limits.min_interval"].format(delta=delta_time))
    pos_tol = config.get(CONF_POSITION_TOLERANCE)
    if pos_tol is not None:
        limit_parts.append(L["limits.position_tolerance"].format(tol=pos_tol))
    if config.get(CONF_ENABLE_POSITION_MATCHING):
        limit_parts.append(L["limits.position_matching_on"])
    else:
        limit_parts.append(L["limits.position_matching_off"])
    if config.get(CONF_INVERSE_STATE):
        limit_parts.append(L["limits.inverse_state"])
    oc_thresh = config.get(CONF_OPEN_CLOSE_THRESHOLD)
    if oc_thresh is not None:
        limit_parts.append(L["limits.open_close_threshold"].format(thresh=oc_thresh))
    if config.get(CONF_INTERP):
        interp_lo = config.get(CONF_INTERP_START)
        interp_hi = config.get(CONF_INTERP_END)
        if interp_lo is not None and interp_hi is not None:
            limit_parts.append(
                L["limits.calibration"].format(lo=interp_lo, hi=interp_hi)
            )
        else:
            limit_parts.append(L["limits.calibration_on"])
    min_pos_sun_track = config.get(CONF_MIN_POSITION_SUN_TRACKING)
    if min_pos_sun_track is not None:
        limit_parts.append(L["limits.sun_tracking_min"].format(pos=min_pos_sun_track))
    if limit_parts:
        lines.append("")
        lines.append(L["headers.position_limits"])
        lines.append(L["limits.separator"].join(limit_parts))

    # Footgun: sun-tracking floor below always-on floor is a no-op (issue #467).
    # The always-on min_pos dominates, so min_pos_sun_tracking < min_pos is a
    # configuration mistake. Surface it so the user can correct it.
    if (
        min_pos_sun_track is not None
        and min_pos is not None
        and min_pos > min_pos_sun_track
    ):
        lines.append(
            L["warnings.sun_track_min_below_floor"].format(
                sun_min=min_pos_sun_track, min_pos=min_pos
            )
        )

    # MODE2 + min_position footgun warning (issue #373).
    # In MODE2 the OPEN (horizontal) slat angle IS 50%, so any min_position
    # >= 50% collapses every climate/glare-control decision to the floor and
    # the cover stops blocking heat or glare. Surface this as a ⚠️ line so
    # users see it before saving the config.
    if (
        sensor_type in (CoverType.TILT, CoverType.VENETIAN)
        and TiltPolicy.is_mode2(config.get(CONF_TILT_MODE))
        and min_pos is not None
        and min_pos >= MODE2_OPEN_HORIZONTAL_PERCENT
    ):
        lines.append(L["warnings.mode2_min_position"].format(min_pos=min_pos))

    # Somfy My preset info / warning
    _any_use_my = bool(config.get(CONF_SUNSET_USE_MY)) or any(
        bool(config.get(f"custom_position_use_my_{_i}"))
        for _i in CUSTOM_POSITION_SLOT_NUMBERS
    )
    _my_entities_enabled = bool(
        config.get(
            CONF_ENABLE_MY_POSITION_ENTITIES, DEFAULT_ENABLE_MY_POSITION_ENTITIES
        )
    )
    lines.append(
        L["my.entities_enabled"] if _my_entities_enabled else L["my.entities_disabled"]
    )
    if my_pos is not None:
        lines.append(L["my.somfy_preset"].format(pos=my_pos))
    elif _any_use_my or _my_entities_enabled:
        lines.append(L["warnings.somfy_my_unset"])

    # Proxy cover toggle (system-wide; not part of the decision chain)
    proxy_enabled = bool(config.get(CONF_ENABLE_PROXY_COVER))
    lines.append("")
    lines.append(
        L["headers.proxy_enabled"] if proxy_enabled else L["headers.proxy_disabled"]
    )
    if proxy_enabled:
        _any_min_mode = any(
            bool(config.get(f"custom_position_min_mode_{_i}"))
            for _i in CUSTOM_POSITION_SLOT_NUMBERS
        )
        if not _any_min_mode:
            lines.append(L["warnings.proxy_no_min"])

    # =========================================================================
    # Section 4: Decision Priority (compact reference)
    # =========================================================================
    def _ch(active: bool, short: str) -> str:
        mark = "✅" if active else "❌"
        return f"{mark}{short}"

    # Build the full priority chain including per-slot custom positions.
    # Each entry is (priority, label, active) so we can sort and render.
    _chain_entries: list[tuple[int, str, bool]] = [
        (90, "Weather", has_weather),
        (80, "Manual", True),
        (75, "Motion", has_motion),
        (60, "Cloud", has_cloud),
        (50, "Climate", has_climate),
        (40, "Solar", sun_tracking_enabled),
        (0, "Default", True),
    ]
    if summary_policy.supports_glare_zones:
        _chain_entries.append((45, "Glare", has_glare))
    # Insert one entry per custom slot at its configured priority
    for _slot, _trigger, _pos, _pri, _use_my, _slot_tilt, _tilt_only in _custom_slots:
        _chain_entries.append((_pri, f"Custom#{_slot}({_pri})", True))
    # Sort highest priority first
    _chain_entries.sort(key=lambda e: e[0], reverse=True)
    chain = [_ch(active, short) for _pri, short, active in _chain_entries]

    lines.append("")
    lines.append(L["headers.decision_priority"])
    lines.append(" → ".join(chain))

    return "\n".join(lines)


async def _get_devices_from_entities(
    hass: HomeAssistant, entity_ids: list[str]
) -> dict[str, str]:
    """Get devices associated with the given cover entity IDs."""
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    devices: dict[str, str] = {}
    for entity_id in entity_ids:
        entity_entry = entity_reg.async_get(entity_id)
        if entity_entry and entity_entry.device_id:
            device_entry = device_reg.async_get(entity_entry.device_id)
            if device_entry and entity_entry.device_id not in devices:
                name = (
                    device_entry.name_by_user
                    or device_entry.name
                    or entity_entry.device_id
                )
                devices[entity_entry.device_id] = name
    return devices


async def _get_device_name_for_entity(
    hass: HomeAssistant, entity_id: str
) -> str | None:
    """Return the parent device's display name for entity_id, or None.

    Returns name_by_user or name only — never the device_id UUID — so callers
    can safely use the result as a default user-facing name.
    """
    entity_reg = er.async_get(hass)
    entity_entry = entity_reg.async_get(entity_id)
    if not entity_entry or not entity_entry.device_id:
        return None
    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get(entity_entry.device_id)
    if not device_entry:
        return None
    return device_entry.name_by_user or device_entry.name or None


_SHARED_OPTIONS_EXCLUDED = frozenset({CONF_ENTITIES, CONF_AZIMUTH, CONF_DEVICE_ID})

# Maps each syncable category (matching options menu names) to its config keys.
# Used by the sync flow to let users choose which setting groups to copy.
SYNC_CATEGORIES: dict[str, frozenset[str]] = {
    "geometry": frozenset(
        {
            CONF_HEIGHT_WIN,
            CONF_WINDOW_DEPTH,
            CONF_SILL_HEIGHT,
            CONF_WINDOW_WIDTH,
            CONF_LENGTH_AWNING,
            CONF_AWNING_ANGLE,
            CONF_TILT_DEPTH,
            CONF_TILT_DISTANCE,
            CONF_TILT_MODE,
        }
    ),
    "sun_tracking": frozenset(
        {
            CONF_ENABLE_SUN_TRACKING,
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_MIN_ELEVATION,
            CONF_MAX_ELEVATION,
            CONF_DISTANCE,
            CONF_ENABLE_BLIND_SPOT,
            CONF_MINIMIZE_MOVEMENTS,
            CONF_MAX_COVERAGE_STEPS,
        }
    ),
    "blind_spot": frozenset(
        {
            CONF_BLIND_SPOT_LEFT,
            CONF_BLIND_SPOT_RIGHT,
            CONF_BLIND_SPOT_ELEVATION,
        }
    ),
    "position": frozenset(
        {
            CONF_DEFAULT_HEIGHT,
            CONF_MAX_POSITION,
            CONF_ENABLE_MAX_POSITION,
            CONF_MIN_POSITION,
            CONF_ENABLE_MIN_POSITION,
            CONF_MIN_POSITION_SUN_TRACKING,
            CONF_SUNSET_POS,
            CONF_ENABLE_MY_POSITION_ENTITIES,
            CONF_MY_POSITION_VALUE,
            CONF_SUNSET_USE_MY,
            CONF_SUNSET_OFFSET,
            CONF_SUNRISE_OFFSET,
            CONF_SUNSET_TIME_ENTITY,
            CONF_SUNRISE_TIME_ENTITY,
            CONF_OPEN_CLOSE_THRESHOLD,
            CONF_POSITION_TOLERANCE,
            CONF_ENABLE_POSITION_MATCHING,
            CONF_INVERSE_STATE,
            CONF_INTERP,
            CONF_RETURN_SUNSET,
        }
    ),
    "interp": frozenset(
        {
            CONF_INTERP_START,
            CONF_INTERP_END,
            CONF_INTERP_LIST,
            CONF_INTERP_LIST_NEW,
        }
    ),
    "automation": frozenset(
        {
            CONF_DELTA_POSITION,
            CONF_DELTA_TIME,
            CONF_START_TIME,
            CONF_START_ENTITY,
            CONF_END_TIME,
            CONF_END_ENTITY,
        }
    ),
    "manual_override": frozenset(
        {
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_MANUAL_OVERRIDE_RESET,
            CONF_MANUAL_THRESHOLD,
            CONF_MANUAL_IGNORE_INTERMEDIATE,
            CONF_MANUAL_IGNORE_EXTERNAL,
            CONF_TRANSIT_TIMEOUT,
        }
    ),
    # Legacy aliases (issue #563): force override merged into custom-position
    # slot 5. Kept for programmatic sync callers; the legacy keys are inert on
    # current code but still drive a rolled-back install.
    "force_override_values": frozenset(
        {
            CONF_FORCE_OVERRIDE_POSITION,
            CONF_FORCE_OVERRIDE_MIN_MODE,
        }
    ),
    "force_override_sensors": frozenset(
        {
            CONF_FORCE_OVERRIDE_SENSORS,
        }
    ),
    "force_override": frozenset(
        {
            CONF_FORCE_OVERRIDE_SENSORS,
            CONF_FORCE_OVERRIDE_POSITION,
            CONF_FORCE_OVERRIDE_MIN_MODE,
        }
    ),
    "custom_position_values": frozenset(
        keys[k]
        for keys in CUSTOM_POSITION_SLOTS.values()
        for k in ("position", "priority", "min_mode", "use_my", "template_mode")
    ),
    "custom_position_sensors": frozenset(
        keys[k]
        for keys in CUSTOM_POSITION_SLOTS.values()
        for k in ("sensor", "sensors", "template")
    ),
    # Legacy alias: full union of custom_position_values + custom_position_sensors
    "custom_position": frozenset(
        v for keys in CUSTOM_POSITION_SLOTS.values() for v in keys.values()
    ),
    "motion_override_values": frozenset(
        {
            CONF_MOTION_TEMPLATE_MODE,
            CONF_MOTION_TIMEOUT,
            CONF_MOTION_TIMEOUT_MODE,
        }
    ),
    "motion_override_sensors": frozenset(
        {
            CONF_MOTION_SENSORS,
            CONF_MOTION_MEDIA_PLAYERS,
            CONF_MOTION_TEMPLATE,
        }
    ),
    # Legacy alias: full union of motion_override_values + motion_override_sensors
    "motion_override": frozenset(
        {
            CONF_MOTION_SENSORS,
            CONF_MOTION_MEDIA_PLAYERS,
            CONF_MOTION_TEMPLATE,
            CONF_MOTION_TEMPLATE_MODE,
            CONF_MOTION_TIMEOUT,
            CONF_MOTION_TIMEOUT_MODE,
        }
    ),
    "weather_override_values": frozenset(
        {
            CONF_WEATHER_BYPASS_AUTO_CONTROL,
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
            CONF_WEATHER_RAIN_THRESHOLD,
            CONF_WEATHER_OVERRIDE_POSITION,
            CONF_WEATHER_OVERRIDE_MIN_MODE,
            CONF_WEATHER_TIMEOUT,
        }
    ),
    "weather_override_sensors": frozenset(
        {
            CONF_WEATHER_WIND_SPEED_SENSOR,
            CONF_WEATHER_WIND_DIRECTION_SENSOR,
            CONF_WEATHER_RAIN_SENSOR,
            CONF_WEATHER_IS_RAINING_SENSOR,
            CONF_WEATHER_IS_WINDY_SENSOR,
            CONF_WEATHER_SEVERE_SENSORS,
        }
    ),
    # Legacy alias: full union of weather_override_values + weather_override_sensors
    "weather_override": frozenset(
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
    ),
    "light_cloud_values": frozenset(
        {
            CONF_WEATHER_STATE,
            CONF_LUX_THRESHOLD,
            CONF_IRRADIANCE_THRESHOLD,
            CONF_CLOUD_COVERAGE_THRESHOLD,
            CONF_CLOUD_SUPPRESSION,
            CONF_CLOUDY_POSITION,
        }
    ),
    "light_cloud_sensors": frozenset(
        {
            CONF_WEATHER_ENTITY,
            CONF_LUX_ENTITY,
            CONF_IRRADIANCE_ENTITY,
            CONF_CLOUD_COVERAGE_ENTITY,
            CONF_IS_SUNNY_SENSOR,
        }
    ),
    # Legacy alias: full union of light_cloud_values + light_cloud_sensors
    "light_cloud": frozenset(
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
            CONF_CLOUDY_POSITION,
            CONF_IS_SUNNY_SENSOR,
        }
    ),
    "temperature_climate_values": frozenset(
        {
            CONF_CLIMATE_MODE,
            CONF_TEMP_LOW,
            CONF_TEMP_HIGH,
            CONF_OUTSIDE_THRESHOLD,
            CONF_TRANSPARENT_BLIND,
            CONF_WINTER_CLOSE_INSULATION,
        }
    ),
    "temperature_climate_sensors": frozenset(
        {
            CONF_TEMP_ENTITY,
            CONF_OUTSIDETEMP_ENTITY,
            CONF_PRESENCE_ENTITY,
        }
    ),
    # Legacy alias: full union of temperature_climate_values + temperature_climate_sensors
    "temperature_climate": frozenset(
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
    ),
    # Legacy alias for backward compat
    "climate": frozenset(
        {
            CONF_WEATHER_ENTITY,
            CONF_LUX_ENTITY,
            CONF_LUX_THRESHOLD,
            CONF_IRRADIANCE_ENTITY,
            CONF_IRRADIANCE_THRESHOLD,
            CONF_CLOUD_COVERAGE_ENTITY,
            CONF_CLOUD_COVERAGE_THRESHOLD,
            CONF_CLOUD_SUPPRESSION,
            CONF_CLOUDY_POSITION,
            CONF_IS_SUNNY_SENSOR,
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
    ),
    "glare_zones": frozenset(
        {CONF_ENABLE_GLARE_ZONES}
        | {
            f"glare_zone_{i}_{axis}"
            for i in range(1, 5)
            for axis in ("name", "x", "y", "radius", "z")
        }
    ),
    "weather": frozenset(
        {
            CONF_WEATHER_STATE,
        }
    ),
}

# Categories shown in the sync selector UI.
# Mixed categories (force_override, custom_position, motion_override, weather_override,
# light_cloud, temperature_climate) are split into *_values (thresholds/flags/modes)
# and *_sensors (entity_id assignments) so users can copy global values without
# overwriting room-specific sensor assignments (issue #125).
# Legacy aliases remain in SYNC_CATEGORIES for programmatic callers.
_SYNC_UI_CATEGORIES: list[str] = [
    "geometry",
    "sun_tracking",
    "blind_spot",
    "position",
    "interp",
    "automation",
    "manual_override",
    "custom_position_values",
    "custom_position_sensors",
    "motion_override_values",
    "motion_override_sensors",
    "weather_override_values",
    "weather_override_sensors",
    "light_cloud_values",
    "light_cloud_sensors",
    "temperature_climate_values",
    "temperature_climate_sensors",
    "glare_zones",
]


def _extract_shared_options(
    entry: ConfigEntry,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Return options safe to copy across covers.

    Excludes per-window fields: CONF_ENTITIES, CONF_AZIMUTH, CONF_DEVICE_ID.
    When categories is None, returns all shared options (used by duplicate flow).
    When categories is a list, returns only options belonging to those categories.
    """
    if categories is None:
        return {
            k: v for k, v in entry.options.items() if k not in _SHARED_OPTIONS_EXCLUDED
        }
    allowed_keys = frozenset().union(
        *(SYNC_CATEGORIES[c] for c in categories if c in SYNC_CATEGORIES)
    )
    return {k: v for k, v in entry.options.items() if k in allowed_keys}


def _build_cover_entity_schema(
    sensor_type: str,
    devices: dict[str, str] | None = None,
) -> vol.Schema:
    """Build entity selector schema based on cover type.

    When devices is provided and non-empty, a device association selector is
    appended so both fields appear on the same form.
    """
    entity_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(
            multiple=True,
            filter=get_policy(sensor_type).entity_selector_filter(),
        )
    )
    schema_dict: dict = {vol.Optional(CONF_ENTITIES, default=[]): entity_selector}
    if devices:
        options_list = [
            {"value": _STANDALONE_SENTINEL, "label": "None (standalone device)"}
        ]
        for device_id, device_name in devices.items():
            options_list.append({"value": device_id, "label": device_name})
        schema_dict[vol.Required(CONF_DEVICE_ID, default=_STANDALONE_SENTINEL)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options_list,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        )
    schema_dict[
        vol.Optional(CONF_ENABLE_PROXY_COVER, default=DEFAULT_ENABLE_PROXY_COVER)
    ] = selector.BooleanSelector()
    return vol.Schema(schema_dict)


def _get_geometry_schema(
    sensor_type: str | None,
    hass: HomeAssistant | None = None,
    options: dict | None = None,
) -> vol.Schema:
    """Return the geometry schema for the given sensor type.

    Falls back to the vertical-blind schema for unknown / missing types so
    legacy configs still render *something* in the options flow. When *hass*
    is supplied the schema follows HA's configured unit system (metric vs.
    US-customary); ``hass=None`` keeps the legacy metric schema and is the
    path the existing test suite uses.
    """
    cls = POLICY_REGISTRY.get(sensor_type) if sensor_type is not None else None
    if cls is None:
        if hass is None:
            return GEOMETRY_VERTICAL_SCHEMA
        from .cover_types.blind import geometry_vertical_schema

        return geometry_vertical_schema(hass)
    return get_policy(sensor_type).geometry_schema(hass, options)


def _geometry_unit_keys(
    sensor_type: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(length_keys, slat_keys)`` for the given cover type.

    ``length_keys`` are option keys stored in canonical metres,
    ``slat_keys`` in canonical centimetres. Empty tuples for unknown types.
    """
    cls = POLICY_REGISTRY.get(sensor_type) if sensor_type is not None else None
    if cls is None:
        return ((), ())
    policy = get_policy(sensor_type)
    return (policy.geometry_length_keys(), policy.geometry_slat_keys())


def _fov_mode_supported(sensor_type: str | None) -> bool:
    """Whether *sensor_type*'s policy exposes the two-mode FOV selector (#565)."""
    return sensor_type in POLICY_REGISTRY and get_policy(sensor_type).supports_fov_mode


_SUN_TRACKING_WIKI = (
    "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Sun-Tracking"
)


def _sun_tracking_placeholders(
    sensor_type: str | None,
    mode: str | None,
    source_config: dict[str, Any],
) -> dict[str, str]:
    """Build description placeholders for the sun-tracking form.

    Always includes ``learn_more``; in Measurements mode (#565) it adds a
    read-only ``computed_fov`` line so the user sees the derived FOV.
    """
    # ``computed_fov`` is referenced unconditionally in the step description
    # template, so it must always be present — empty string outside Measurements
    # mode (HA raises if a referenced placeholder is missing).
    computed = ""
    if _fov_mode_supported(sensor_type) and str(mode) == FovMode.MEASUREMENTS:
        computed = computed_fov_line(
            source_config.get(CONF_WINDOW_WIDTH),
            source_config.get(CONF_WINDOW_DEPTH),
        )
    return {"learn_more": _SUN_TRACKING_WIKI, "computed_fov": computed}


def _resolve_fov_mode_submit(
    sensor_type: str | None,
    prior_mode: str | None,
    user_input: dict[str, Any],
    source_config: dict[str, Any],
) -> str | None:
    """Process a sun-tracking submit for the FOV-mode selector (#565).

    Single home for the FOV-mode save logic shared by the create-flow and
    options-flow ``async_step_sun_tracking`` handlers (no-duplication
    guideline). Returns the mode to **re-render** the form in (when the user
    switched modes — HA can't hide fields mid-render), or ``None`` to proceed.

    When proceeding in ``MEASUREMENTS`` mode the derived
    ``fov_left``/``fov_right`` are written into *user_input* from the entry's
    current window width + reveal depth, so the engine (which reads the stored
    fov values unchanged) gets the computed angles. ``window_depth`` is left
    untouched. In ``ANGLES`` mode the user's typed fov values pass through.
    """
    if not _fov_mode_supported(sensor_type) or CONF_FOV_MODE not in user_input:
        return None

    submitted = str(user_input[CONF_FOV_MODE])
    resolved_prior = str(prior_mode) if prior_mode is not None else FovMode.ANGLES
    if submitted != resolved_prior:
        # Mode changed → re-render so the right fields show/hide.
        return submitted

    if submitted == FovMode.MEASUREMENTS:
        width = float(source_config.get(CONF_WINDOW_WIDTH) or 0.0)
        depth = float(source_config.get(CONF_WINDOW_DEPTH) or 0.0)
        derived = round(fov_from_reveal(width, depth))
        if CONF_FOV_LEFT not in user_input:
            user_input[CONF_FOV_LEFT] = derived
        if CONF_FOV_RIGHT not in user_input:
            user_input[CONF_FOV_RIGHT] = derived
    return None


def _get_sun_tracking_schema(
    sensor_type: str | None,
    hass: HomeAssistant | None = None,
    mode: str | None = None,
    source_config: dict | None = None,
) -> vol.Schema:
    """Return sun tracking schema for *sensor_type* in the given FOV *mode*.

    Adds the glare-zones toggle for cover types that support it, and routes the
    FOV-field shaping (mode selector + per-mode slider visibility, #565) through
    the cover-type policy so no cover-type string branching leaks out here.

    *source_config* is forwarded to the policy so that, in Measurements mode,
    the fov sliders can be pre-populated with the geometric suggested_value
    derived from the stored window width + reveal depth.
    """
    base = sun_tracking_schema(hass) if hass is not None else SUN_TRACKING_SCHEMA
    if sensor_type in POLICY_REGISTRY:
        policy = get_policy(sensor_type)
        base = policy.fov_mode_schema(base, mode, source_config=source_config)
        if policy.supports_glare_zones:
            base = base.extend(
                {
                    vol.Optional(
                        CONF_ENABLE_GLARE_ZONES, default=False
                    ): selector.BooleanSelector(),
                }
            )
    return base


def _glare_zone_length_keys() -> tuple[str, ...]:
    """Return the 16 metres-stored option keys for the 4 glare zone slots."""
    return tuple(
        f"glare_zone_{i}_{axis}"
        for i in range(1, 5)
        for axis in ("x", "y", "radius", "z")
    )


# Glare-zones schema is built in ``config_dynamic`` (locale-aware). Thin alias
# preserves the existing call sites / signature ``(options, hass)``.
_build_glare_zones_schema = _glare_zones_schema


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle ConfigFlow."""

    VERSION = 3
    # 3.2 (issue #563): force override merged into custom-position slot 5.
    # Minor bump on purpose — older releases can still load 3.2 entries, so
    # users can roll back; the migration is additive (legacy keys retained).
    MINOR_VERSION = 2

    def __init__(self) -> None:  # noqa: D107
        super().__init__()
        self.type_blind: str | None = None
        self.config: dict[str, Any] = {}
        self.mode: str = "basic"
        self.selected_source_entry_id: str | None = None
        self.setup_mode: str = "quick"  # "quick" or "full"
        self._has_device_options: bool = False
        self._cover_devices: dict[str, str] = {}

    def optional_entities(self, keys: list, user_input: dict[str, Any]) -> None:
        """Set value to None if key does not exist in user_input."""
        for key in keys:
            if key not in user_input:
                user_input[key] = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step — show menu if other covers exist, else go straight to create."""
        acp_entries = self.hass.config_entries.async_entries(DOMAIN)
        if acp_entries:
            return self.async_show_menu(
                step_id="user",
                menu_options=["create_new", "duplicate_existing"],
            )
        return await self.async_step_create_new()

    async def async_step_create_new(self, user_input: dict[str, Any] | None = None):
        """Handle create new cover flow."""
        if user_input:
            self.config = user_input
            self.type_blind = self.config[CONF_MODE]
            return await self.async_step_setup_mode()
        return self.async_show_form(
            step_id="create_new",
            data_schema=CONFIG_SCHEMA,
        )

    async def async_step_setup_mode(self, user_input: dict[str, Any] | None = None):
        """Choose between quick and full setup."""
        return self.async_show_menu(
            step_id="setup_mode",
            menu_options=["quick_setup", "full_setup"],
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup"
            },
        )

    async def async_step_quick_setup(self, user_input: dict[str, Any] | None = None):
        """Start quick setup — minimal steps."""
        self.setup_mode = "quick"
        return await self.async_step_cover_entities()

    async def async_step_full_setup(self, user_input: dict[str, Any] | None = None):
        """Start full setup — all configuration steps."""
        self.setup_mode = "full"
        return await self.async_step_cover_entities()

    async def async_step_cover_entities(self, user_input: dict[str, Any] | None = None):
        """Select cover entities and optionally link to a physical device.

        Pass 1 (entities only): user selects cover entities; if they have associated
        physical devices the form is re-rendered with a device selector appended.
        Pass 2 (combined, only when devices exist): both fields are submitted together
        and the flow proceeds to geometry.
        """
        if user_input is not None:
            if self._has_device_options:
                # Pass 2: process entity + device selection
                self.config.update(user_input)
                device_id = user_input.get(CONF_DEVICE_ID, _STANDALONE_SENTINEL)
                if device_id and device_id != _STANDALONE_SENTINEL:
                    self.config[CONF_DEVICE_ID] = device_id
                else:
                    self.config.pop(CONF_DEVICE_ID, None)
                return await self.async_step_geometry()

            # Pass 1: store entities, auto-name, check for associated devices
            self.config.update(user_input)
            if CONF_ENTITIES in user_input and user_input[CONF_ENTITIES]:
                first_entity_id = user_input[CONF_ENTITIES][0]
                entity_reg = er.async_get(self.hass)
                entity_entry = entity_reg.async_get(first_entity_id)
                if entity_entry and not self.config.get("name"):
                    device_name = await _get_device_name_for_entity(
                        self.hass, first_entity_id
                    )
                    if device_name:
                        self.config["name"] = device_name
                        self.config["_title_is_device_name"] = True
                    else:
                        entity_name = (
                            entity_entry.original_name
                            or entity_entry.name
                            or first_entity_id.split(".")[-1].replace("_", " ").title()
                        )
                        self.config["name"] = f"Adaptive {entity_name}"

            entity_ids = self.config.get(CONF_ENTITIES, [])
            devices = await _get_devices_from_entities(self.hass, entity_ids)
            if devices:
                self._has_device_options = True
                self._cover_devices = devices
                schema = _build_cover_entity_schema(self.type_blind, devices=devices)
                return self.async_show_form(
                    step_id="cover_entities",
                    data_schema=self.add_suggested_values_to_schema(
                        schema, self.config
                    ),
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup"
                    },
                )
            return await self.async_step_geometry()

        schema = _build_cover_entity_schema(self.type_blind)
        return self.async_show_form(
            step_id="cover_entities",
            data_schema=schema,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup"
            },
        )

    async def async_step_geometry(self, user_input: dict[str, Any] | None = None):
        """Configure cover geometry dimensions."""
        length_keys, slat_keys = _geometry_unit_keys(self.type_blind)
        if user_input is not None:
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=length_keys, slat_keys=slat_keys
            )
            self.config.update(canonical)
            return await self.async_step_sun_tracking()

        schema = _get_geometry_schema(self.type_blind, self.hass, self.config)
        return self.async_show_form(
            step_id="geometry",
            data_schema=schema,
            description_placeholders={
                "geometry_wiki_link": _geometry_wiki_link(self.type_blind)
            },
        )

    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (initial flow)."""
        if user_input is not None:
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=_glare_zone_length_keys()
            )
            self.config.update(canonical)
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()

        schema = _build_glare_zones_schema(self.config, self.hass)
        return self.async_show_form(
            step_id="glare_zones",
            data_schema=schema,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Glare-Zones"
            },
        )

    async def async_step_sun_tracking(self, user_input: dict[str, Any] | None = None):
        """Configure sun tracking parameters."""
        prior_mode = self.config.get(CONF_FOV_MODE)
        if user_input is not None:
            self.optional_entities([CONF_MIN_ELEVATION, CONF_MAX_ELEVATION], user_input)
            rerender = _resolve_fov_mode_submit(
                self.type_blind, prior_mode, user_input, self.config
            )
            # Canonicalize once: ``_show_sun_tracking_form`` re-displays via
            # ``options_to_display``, so feeding it raw (already display-unit)
            # input would convert metres->inches a second time and the value
            # would compound on every rerender (#565). Canonical here keeps the
            # rerender re-feed symmetric with the initial render and save path.
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=_SUN_TRACKING_LENGTH_KEYS
            )
            if rerender is not None:
                self.config[CONF_FOV_MODE] = rerender
                return self._show_sun_tracking_form(canonical, mode=rerender)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
                and user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]
            ):
                return self._show_sun_tracking_form(
                    canonical,
                    mode=user_input.get(CONF_FOV_MODE, prior_mode),
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                )
            self.config.update(canonical)
            return await self.async_step_position()
        return self._show_sun_tracking_form(self.config, mode=prior_mode)

    def _show_sun_tracking_form(
        self,
        values: dict[str, Any] | None = None,
        *,
        mode: str | None = None,
        errors: dict | None = None,
    ):
        """Render the create-flow sun-tracking form for the given FOV *mode*."""
        schema = _get_sun_tracking_schema(
            self.type_blind, self.hass, mode, source_config=self.config
        )
        suggested = options_to_display(
            self.hass, values or self.config, length_keys=_SUN_TRACKING_LENGTH_KEYS
        )
        return self.async_show_form(
            step_id="sun_tracking",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=_sun_tracking_placeholders(
                self.type_blind, mode, self.config
            ),
        )

    async def async_step_position(self, user_input: dict[str, Any] | None = None):
        """Configure position settings."""
        if user_input is not None:
            self.optional_entities(_POSITION_OPTIONAL_KEYS, user_input)
            self.config.update(user_input)
            # Quick setup: skip optional screens, go straight to summary
            if self.setup_mode == "quick":
                return await self.async_step_summary()
            if self.config.get(CONF_ENABLE_BLIND_SPOT):
                return await self.async_step_blind_spot()
            if get_policy(self.type_blind).supports_glare_zones and self.config.get(
                CONF_ENABLE_GLARE_ZONES
            ):
                return await self.async_step_glare_zones()
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()
        return self.async_show_form(
            step_id="position",
            data_schema=POSITION_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position",
                "position_matching_wiki": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position-Matching",
            },
        )

    async def async_step_blind_spot(self, user_input: dict[str, Any] | None = None):
        """Add blindspot to data."""
        schema = blind_spot_schema(self.config)
        if user_input is not None:
            if user_input[CONF_BLIND_SPOT_RIGHT] <= user_input[CONF_BLIND_SPOT_LEFT]:
                return self.async_show_form(
                    step_id="blind_spot",
                    data_schema=schema,
                    errors={
                        CONF_BLIND_SPOT_RIGHT: "Must be greater than 'Blind Spot Left Edge'"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Blindspot"
                    },
                )
            self.config.update(user_input)
            if get_policy(self.type_blind).supports_glare_zones and self.config.get(
                CONF_ENABLE_GLARE_ZONES
            ):
                return await self.async_step_glare_zones()
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()

        return self.async_show_form(
            step_id="blind_spot",
            data_schema=schema,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Blindspot"
            },
        )

    async def async_step_interp(self, user_input: dict[str, Any] | None = None):
        """Show interpolation options."""
        if user_input is not None:
            if len(user_input[CONF_INTERP_LIST]) != len(
                user_input[CONF_INTERP_LIST_NEW]
            ):
                return self.async_show_form(
                    step_id="interp",
                    data_schema=INTERPOLATION_OPTIONS,
                    errors={
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Calculated positions (input)' list"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_automation()
        return self.async_show_form(
            step_id="interp",
            data_schema=INTERPOLATION_OPTIONS,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
            },
        )

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            self.optional_entities([CONF_START_ENTITY, CONF_END_ENTITY], user_input)
            self.config.update(user_input)
            return await self.async_step_manual_override()
        return self.async_show_form(
            step_id="automation",
            data_schema=AUTOMATION_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Automation"
            },
        )

    async def async_step_manual_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure manual override settings."""
        if user_input is not None:
            self.optional_entities([CONF_MANUAL_THRESHOLD], user_input)
            self.config.update(user_input)
            return await self.async_step_custom_position()
        return self.async_show_form(
            step_id="manual_override",
            data_schema=MANUAL_OVERRIDE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_custom_position(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure custom position sensors."""
        if user_input is not None:
            self.optional_entities(_CUSTOM_POSITION_OPTIONAL_KEYS, user_input)
            self.config.update(user_input)
            # Mirror on the merged dict so a cleared slot can null a stale
            # legacy key carried over from a copied/source entry.
            mirror_legacy_slot_sensor_keys(self.config)
            return await self.async_step_motion_override()
        schema = vol.Schema(
            _build_custom_position_schema_dict(sensor_type=self.type_blind)
        )
        return self.async_show_form(
            step_id="custom_position",
            data_schema=schema,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Custom-Position"
            },
        )

    async def async_step_motion_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure motion/occupancy-based control."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_weather_override()
        return self.async_show_form(
            step_id="motion_override",
            data_schema=MOTION_OVERRIDE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_weather_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure weather-based safety overrides."""
        if user_input is not None:
            self.optional_entities(_WEATHER_OVERRIDE_OPTIONAL_KEYS, user_input)
            self.config.update(user_input)
            return await self.async_step_light_cloud()
        return self.async_show_form(
            step_id="weather_override",
            data_schema=weather_override_schema(self.hass, self.config),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Weather-Safety"
            },
        )

    async def async_step_light_cloud(self, user_input: dict[str, Any] | None = None):
        """Configure light sensors, weather conditions, and cloud suppression."""
        if user_input is not None:
            self.optional_entities(_LIGHT_CLOUD_OPTIONAL_KEYS, user_input)
            self.config.update(user_input)
            return await self.async_step_temperature_climate()
        return self.async_show_form(
            step_id="light_cloud",
            data_schema=light_cloud_schema(self.hass, self.config),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_temperature_climate(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure temperature-based climate mode."""
        if user_input is not None:
            self.optional_entities(_TEMPERATURE_CLIMATE_OPTIONAL_KEYS, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="temperature_climate",
                    data_schema=temperature_climate_schema(self.hass, user_input),
                    errors={CONF_TEMP_ENTITY: "Required when climate mode is enabled"},
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="temperature_climate",
            data_schema=temperature_climate_schema(self.hass, self.config),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None):
        """Manage weather conditions."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="weather",
            data_schema=WEATHER_OPTIONS,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_summary(self, user_input: dict[str, Any] | None = None):
        """Show a read-only summary of all collected configuration before creating the entry."""
        if user_input is not None:
            return await self.async_step_update()
        sun_times = await _compute_todays_sun_times(self.hass, self.config)
        labels = await _load_summary_labels(
            self.hass, self.context.get("language", "en")
        )
        summary_text = _build_config_summary(
            self.config, self.type_blind, self.hass, sun_times, labels=labels
        )
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": summary_text},
        )

    async def async_step_update(self, user_input: dict[str, Any] | None = None):
        """Create entry."""
        if self.type_blind is None:
            msg = "type_blind must be set before calling async_step_update"
            raise ValueError(msg)

        if self.config.pop("_title_is_device_name", False):
            title = self.config["name"]
        else:
            title = f"{_cover_type_label(self.type_blind)} {self.config['name']}"

        # Build options from the full accumulated config dict, mirroring the
        # options-flow contract (data=self.options).  Strip only the data-level
        # keys that belong in entry.data rather than entry.options; override
        # CONF_MODE (which holds the cover-type string in self.config) with the
        # strategy-mode value stored on self.mode.
        _DATA_KEYS = {"name", CONF_SENSOR_TYPE}
        options = {k: v for k, v in self.config.items() if k not in _DATA_KEYS}
        # CONF_MODE in self.config is the cover-type selector value (CoverType.*).
        # entry.options["mode"] must carry the strategy mode ("basic" / "advanced").
        options[CONF_MODE] = self.mode

        # Quick setup skips some steps (e.g. automation) leaving critical keys
        # absent from self.config.  Apply constant-backed defaults so the
        # coordinator never receives None for gating values (issue #133).
        options.setdefault(CONF_DELTA_POSITION, DEFAULT_DELTA_POSITION)
        options.setdefault(CONF_DELTA_TIME, DEFAULT_DELTA_TIME)
        options.setdefault(
            CONF_MANUAL_OVERRIDE_DURATION, DEFAULT_MANUAL_OVERRIDE_DURATION
        )
        options.setdefault(CONF_MOTION_SENSORS, [])
        options.setdefault(CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT)

        return self.async_create_entry(
            title=title,
            data={
                "name": self.config["name"],
                CONF_SENSOR_TYPE: self.type_blind,
            },
            options=options,
        )

    async def async_step_duplicate_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle duplicate existing configuration flow."""
        return await self.async_step_duplicate_select(user_input)

    async def async_step_duplicate_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select the source cover to duplicate from."""
        acp_entries = self.hass.config_entries.async_entries(DOMAIN)

        if not acp_entries:
            return self.async_abort(reason="source_not_found")  # type: ignore[return-value]

        if user_input is not None:
            self.selected_source_entry_id = user_input["source_entry"]
            return await self.async_step_duplicate_configure()

        return self.async_show_form(  # type: ignore[return-value]
            step_id="duplicate_select",
            data_schema=vol.Schema(
                {
                    vol.Required("source_entry"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": e.entry_id, "label": e.title}
                                for e in acp_entries
                            ],
                        )
                    )
                }
            ),
        )

    async def async_step_duplicate_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure the unique fields for the duplicated cover."""
        source_entry = self.hass.config_entries.async_get_entry(
            self.selected_source_entry_id or ""
        )
        if not source_entry:
            return self.async_abort(reason="source_not_found")  # type: ignore[return-value]

        if user_input is not None:
            shared_options = _extract_shared_options(source_entry)
            sensor_type = source_entry.data.get(CONF_SENSOR_TYPE)
            new_name = await self._ensure_unique_name(user_input["name"], suffix="Copy")

            return self.async_create_entry(  # type: ignore[return-value]
                title=f"{_cover_type_label(sensor_type)} {new_name}",
                data={"name": new_name, CONF_SENSOR_TYPE: sensor_type},
                options={
                    **shared_options,
                    CONF_ENTITIES: user_input.get(CONF_ENTITIES, []),
                    CONF_AZIMUTH: user_input[CONF_AZIMUTH],
                    # CONF_DEVICE_ID intentionally omitted — device association skipped for duplicates
                },
            )

        source_azimuth = source_entry.options.get(CONF_AZIMUTH, 180)
        sensor_type = source_entry.data.get(CONF_SENSOR_TYPE)
        cover_entity_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                filter=get_policy(sensor_type).entity_selector_filter(),
            )
        )

        schema = vol.Schema(
            {
                vol.Required("name"): selector.TextSelector(),
                vol.Optional(CONF_ENTITIES, default=[]): cover_entity_selector,
                vol.Required(
                    CONF_AZIMUTH, default=source_azimuth
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=359,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                    )
                ),
            }
        )

        return self.async_show_form(  # type: ignore[return-value]
            step_id="duplicate_configure",
            data_schema=schema,
        )

    async def _ensure_unique_name(self, name: str, suffix: str = "Imported") -> str:
        """Ensure name doesn't conflict with existing entries.

        Appends ' (suffix)' or ' (suffix N)' if a conflict exists.
        Default suffix is 'Imported' for backward compatibility with legacy import flow.
        """
        existing_entries = self.hass.config_entries.async_entries(DOMAIN)
        existing_names = {e.data.get("name") for e in existing_entries}

        if name not in existing_names:
            return name

        suffixed_name = f"{name} ({suffix})"
        if suffixed_name not in existing_names:
            return suffixed_name

        counter = 2
        while f"{name} ({suffix} {counter})" in existing_names:
            counter += 1

        return f"{name} ({suffix} {counter})"


class OptionsFlowHandler(OptionsFlow):
    """Options to adjust parameters."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self.current_config: dict = dict(config_entry.data)
        self.options = dict(config_entry.options)
        self.sensor_type: CoverType = (  # type: ignore[misc]
            self.current_config.get(CONF_SENSOR_TYPE) or CoverType.BLIND
        )
        self.selected_sync_targets: list[str] = []
        self.selected_sync_categories: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        # ── Core Setup ───────────────────────────────────────────────
        keys = [
            "cover_entities",
            "geometry",
            "sun_tracking",
        ]

        # ── Position & Zones ─────────────────────────────────────────
        keys.append("position")
        if self.options.get(CONF_INTERP):
            keys.append("interp")
        if self.options.get(CONF_ENABLE_BLIND_SPOT):
            keys.append("blind_spot")
        if get_policy(self.sensor_type).supports_glare_zones and self.options.get(
            CONF_ENABLE_GLARE_ZONES
        ):
            keys.append("glare_zones")

        # ── Schedule & Automation ────────────────────────────────────
        keys.append("automation")

        # ── Light, Climate & Weather ────────────────────────────────
        keys.extend(["light_cloud", "temperature_climate"])

        # ── Override Controls (priority order: highest → lowest) ─────
        keys.extend(
            [
                "weather_override",  # Priority 90
                "manual_override",  # Priority 80
                "custom_position",  # Priority 1-100 per slot (100 = safety)
                "motion_override",  # Priority 75
            ]
        )

        # ── Multi-Cover Management ──────────────────────────────────
        keys.append("sync")

        # ── Admin ────────────────────────────────────────────────────
        keys.extend(["summary", "debug", "done"])

        # Use a list so HA translates labels client-side using the user's language preference.
        # Icons are embedded directly in each translation string (e.g. "🪟 Covers & Device").
        menu_options: list[str] = keys

        return self.async_show_menu(  # type: ignore[return-value]
            step_id="init",
            menu_options=menu_options,
            description_placeholders={
                "instance_name": self.config_entry.title,
                "coffee_url": "https://www.buymeacoffee.com/jrhubott",
            },
        )

    async def async_step_cover_entities(self, user_input: dict[str, Any] | None = None):
        """Adjust cover entities and device association on a single combined form."""
        entity_ids = self.options.get(CONF_ENTITIES, [])
        devices = await _get_devices_from_entities(self.hass, entity_ids)

        if user_input is not None:
            self.options.update(user_input)
            device_id = user_input.get(CONF_DEVICE_ID, _STANDALONE_SENTINEL)
            if device_id and device_id != _STANDALONE_SENTINEL:
                self.options[CONF_DEVICE_ID] = device_id
            else:
                self.options.pop(CONF_DEVICE_ID, None)
            return await self.async_step_init()

        current_device = self.options.get(CONF_DEVICE_ID) or _STANDALONE_SENTINEL
        schema = _build_cover_entity_schema(self.sensor_type, devices=devices or None)
        suggested = dict(self.options)
        if devices:
            suggested.setdefault(CONF_DEVICE_ID, current_device)
        return self.async_show_form(
            step_id="cover_entities",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup"
            },
        )

    async def async_step_geometry(self, user_input: dict[str, Any] | None = None):
        """Adjust geometry parameters."""
        length_keys, slat_keys = _geometry_unit_keys(self.sensor_type)
        if user_input is not None:
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=length_keys, slat_keys=slat_keys
            )
            self.options.update(canonical)
            return await self.async_step_init()

        schema = _get_geometry_schema(self.sensor_type, self.hass, self.options)
        suggested = options_to_display(
            self.hass,
            user_input or self.options,
            length_keys=length_keys,
            slat_keys=slat_keys,
        )
        return self.async_show_form(
            step_id="geometry",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            description_placeholders={
                "geometry_wiki_link": _geometry_wiki_link(self.sensor_type)
            },
        )

    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (options)."""
        gz_keys = _glare_zone_length_keys()
        if user_input is not None:
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=gz_keys
            )
            self.options.update(canonical)
            return await self.async_step_init()

        schema = _build_glare_zones_schema(self.options, self.hass)
        suggested = options_to_display(self.hass, self.options, length_keys=gz_keys)
        return self.async_show_form(
            step_id="glare_zones",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Glare-Zones"
            },
        )

    async def async_step_sun_tracking(self, user_input: dict[str, Any] | None = None):
        """Adjust sun tracking parameters."""
        prior_mode = self.options.get(CONF_FOV_MODE)
        if user_input is not None:
            self.optional_entities([CONF_MIN_ELEVATION, CONF_MAX_ELEVATION], user_input)
            rerender = _resolve_fov_mode_submit(
                self.sensor_type, prior_mode, user_input, self.options
            )
            # Canonicalize once: ``_show_sun_tracking_form`` re-displays via
            # ``options_to_display``, so feeding it raw (already display-unit)
            # input would convert metres->inches a second time and the value
            # would compound on every rerender (#565). Canonical here keeps the
            # rerender re-feed symmetric with the initial render and save path.
            canonical = user_input_to_canonical(
                self.hass, user_input, length_keys=_SUN_TRACKING_LENGTH_KEYS
            )
            if rerender is not None:
                self.options[CONF_FOV_MODE] = rerender
                return self._show_sun_tracking_form(canonical, mode=rerender)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
                and user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]
            ):
                return self._show_sun_tracking_form(
                    canonical,
                    mode=user_input.get(CONF_FOV_MODE, prior_mode),
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                )
            self.options.update(canonical)
            return await self.async_step_init()
        return self._show_sun_tracking_form(self.options, mode=prior_mode)

    def _show_sun_tracking_form(
        self,
        values: dict[str, Any],
        *,
        mode: str | None = None,
        errors: dict | None = None,
    ):
        """Render the sun-tracking form for the given FOV *mode* (#565)."""
        schema = _get_sun_tracking_schema(
            self.sensor_type, self.hass, mode, source_config=self.options
        )
        suggested = options_to_display(
            self.hass, values, length_keys=_SUN_TRACKING_LENGTH_KEYS
        )
        return self.async_show_form(
            step_id="sun_tracking",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=_sun_tracking_placeholders(
                self.sensor_type, mode, self.options
            ),
        )

    async def async_step_position(self, user_input: dict[str, Any] | None = None):
        """Adjust position settings."""
        if user_input is not None:
            self.optional_entities(_POSITION_OPTIONAL_KEYS, user_input)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="position",
            data_schema=self.add_suggested_values_to_schema(
                POSITION_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position",
                "position_matching_wiki": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position-Matching",
            },
        )

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            self.optional_entities([CONF_START_ENTITY, CONF_END_ENTITY], user_input)
            # A cleared TimeSelector either omits the key or coerces to the blank
            # sentinel "00:00:00". Treat both as "unset": drop the key from the
            # submission and from any previously-stored option so it never
            # persists as a literal midnight window (issue #492).
            for time_key in (CONF_START_TIME, CONF_END_TIME):
                if user_input.get(time_key) in (None, BLANK_TIME):
                    user_input.pop(time_key, None)
                    self.options.pop(time_key, None)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="automation",
            data_schema=self.add_suggested_values_to_schema(
                AUTOMATION_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Automation"
            },
        )

    async def async_step_manual_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage manual override options."""
        if user_input is not None:
            self.optional_entities([CONF_MANUAL_THRESHOLD], user_input)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="manual_override",
            data_schema=self.add_suggested_values_to_schema(
                MANUAL_OVERRIDE_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_custom_position(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage custom position sensors."""
        if user_input is not None:
            self.optional_entities(_CUSTOM_POSITION_OPTIONAL_KEYS, user_input)
            self.options.update(user_input)
            # Mirror on the merged options so a cleared slot nulls its stale
            # legacy single-sensor key (rollback fidelity, issue #563).
            mirror_legacy_slot_sensor_keys(self.options)
            return await self.async_step_init()
        sensor_type = self._config_entry.data.get(CONF_SENSOR_TYPE)
        schema = vol.Schema(_build_custom_position_schema_dict(sensor_type=sensor_type))
        return self.async_show_form(
            step_id="custom_position",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Custom-Position"
            },
        )

    async def async_step_motion_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage motion/occupancy-based control."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="motion_override",
            data_schema=self.add_suggested_values_to_schema(
                MOTION_OVERRIDE_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_weather_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage weather-based safety overrides."""
        suggested = _stringify_templatable(user_input or self.options)
        if user_input is not None:
            self.optional_entities(_WEATHER_OVERRIDE_OPTIONAL_KEYS, user_input)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="weather_override",
            data_schema=self.add_suggested_values_to_schema(
                weather_override_schema(self.hass, suggested), suggested
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Weather-Safety"
            },
        )

    async def async_step_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select target covers and setting categories to sync."""
        current_type = self._config_entry.data.get(CONF_SENSOR_TYPE)
        other_entries = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != self._config_entry.entry_id
            and e.data.get(CONF_SENSOR_TYPE) == current_type
        ]

        if not other_entries:
            return self.async_abort(reason="no_covers_to_sync")  # type: ignore[return-value]

        available = [
            cat
            for cat in _SYNC_UI_CATEGORIES
            if cat in SYNC_CATEGORIES
            and any(k in self._config_entry.options for k in SYNC_CATEGORIES[cat])
        ]

        if user_input is not None:
            targets = user_input.get("target_entries", [])
            if not targets:
                return await self.async_step_init()
            selected = user_input.get("sync_categories", [])
            if not selected:
                return await self.async_step_init()
            self.selected_sync_targets = targets
            self.selected_sync_categories = selected
            return await self.async_step_sync_confirm()

        return self.async_show_form(  # type: ignore[return-value]
            step_id="sync",
            data_schema=vol.Schema(
                {
                    vol.Required("target_entries", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {"value": e.entry_id, "label": e.title}
                                for e in other_entries
                            ],
                        )
                    ),
                    vol.Required(
                        "sync_categories", default=[]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=available,
                            translation_key="sync_categories",
                        )
                    ),
                }
            ),
        )

    async def async_step_sync_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm and execute sync to selected covers."""
        if user_input is not None:
            if user_input.get("confirm"):
                # Save current cover's settings first so sync copies the latest values
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    options=dict(self.options),
                )
                shared_options = _extract_shared_options(
                    self._config_entry, categories=self.selected_sync_categories
                )
                for entry_id in self.selected_sync_targets:
                    target = self.hass.config_entries.async_get_entry(entry_id)
                    if target:
                        self.hass.config_entries.async_update_entry(
                            target,
                            options={**target.options, **shared_options},
                        )
            return await self.async_step_init()

        # Build summary of selected targets
        target_titles = []
        for entry_id in self.selected_sync_targets:
            target = self.hass.config_entries.async_get_entry(entry_id)
            if target:
                target_titles.append(f"• {target.title}")

        # Build summary of selected categories using friendly names
        _category_labels = {
            "geometry": "Window Dimensions",
            "sun_tracking": "Sun Tracking",
            "blind_spot": "Blind Spot Configuration",
            "position": "Position Settings",
            "interp": "Position Calibration",
            "automation": "Schedule & Timing",
            "manual_override": "Manual Override",
            "custom_position_values": "Custom Positions — Values & Priorities",
            "custom_position_sensors": "Custom Positions — Trigger Sensors",
            "motion_override_values": "Motion Override — Timeout",
            "motion_override_sensors": "Motion Override — Sensors",
            "weather_override_values": "Weather Override — Thresholds & Position",
            "weather_override_sensors": "Weather Override — Sensors",
            "light_cloud_values": "Light & Cloud — Thresholds",
            "light_cloud_sensors": "Light & Cloud — Sensors",
            "temperature_climate_values": "Climate Mode — Thresholds & Settings",
            "temperature_climate_sensors": "Climate Mode — Room Sensors",
            "glare_zones": "Glare Zones",
            # Legacy aliases (kept for back-compat; not shown in UI)
            "force_override": "Force Override",
            "custom_position": "Custom Positions",
            "motion_override": "Motion Override",
            "weather_override": "Weather Override",
            "light_cloud": "Light Sensors & Cloud Suppression",
            "temperature_climate": "Temperature & Climate Mode",
        }
        category_lines = [
            f"• {_category_labels.get(c, c)}" for c in self.selected_sync_categories
        ]

        return self.async_show_form(  # type: ignore[return-value]
            step_id="sync_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "source_name": self._config_entry.title,
                "entries_summary": "\n".join(target_titles) or "(none selected)",
                "categories_summary": "\n".join(category_lines) or "(none selected)",
            },
        )

    async def async_step_interp(self, user_input: dict[str, Any] | None = None):
        """Show interpolation options."""
        if user_input is not None:
            if len(user_input[CONF_INTERP_LIST]) != len(
                user_input[CONF_INTERP_LIST_NEW]
            ):
                return self.async_show_form(
                    step_id="interp",
                    data_schema=self.add_suggested_values_to_schema(
                        INTERPOLATION_OPTIONS, user_input
                    ),
                    errors={
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Calculated positions (input)' list"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="interp",
            data_schema=self.add_suggested_values_to_schema(
                INTERPOLATION_OPTIONS, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
            },
        )

    async def async_step_blind_spot(self, user_input: dict[str, Any] | None = None):
        """Add blindspot to data."""
        schema = blind_spot_schema(self.options)
        if user_input is not None:
            if user_input[CONF_BLIND_SPOT_RIGHT] <= user_input[CONF_BLIND_SPOT_LEFT]:
                return self.async_show_form(
                    step_id="blind_spot",
                    data_schema=schema,
                    errors={
                        CONF_BLIND_SPOT_RIGHT: "Must be greater than 'Blind Spot Left Edge'"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Blindspot"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="blind_spot",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Blindspot"
            },
        )

    async def async_step_light_cloud(self, user_input: dict[str, Any] | None = None):
        """Manage light sensors, weather conditions, and cloud suppression."""
        suggested = _stringify_templatable(user_input or self.options)
        if user_input is not None:
            self.optional_entities(_LIGHT_CLOUD_OPTIONAL_KEYS, user_input)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="light_cloud",
            data_schema=self.add_suggested_values_to_schema(
                light_cloud_schema(self.hass, suggested), suggested
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_temperature_climate(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage temperature-based climate mode."""
        suggested = _stringify_templatable(user_input or self.options)
        if user_input is not None:
            self.optional_entities(_TEMPERATURE_CLIMATE_OPTIONAL_KEYS, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="temperature_climate",
                    data_schema=self.add_suggested_values_to_schema(
                        temperature_climate_schema(self.hass, suggested), suggested
                    ),
                    errors={CONF_TEMP_ENTITY: "Required when climate mode is enabled"},
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="temperature_climate",
            data_schema=self.add_suggested_values_to_schema(
                temperature_climate_schema(self.hass, suggested), suggested
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None):
        """Manage weather conditions."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="weather",
            data_schema=self.add_suggested_values_to_schema(
                WEATHER_OPTIONS, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show a read-only summary of the current configuration."""
        if user_input is not None:
            return await self.async_step_init()
        sun_times = await _compute_todays_sun_times(self.hass, self.options)
        labels = await _load_summary_labels(
            self.hass, self.context.get("language", "en")
        )
        summary_text = _build_config_summary(
            self.options, self.sensor_type, self.hass, sun_times, labels=labels
        )
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": summary_text},
        )

    async def async_step_debug(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Debug & Diagnostics options."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        caps_text = _build_cover_capabilities_text(
            self.options, self.sensor_type, self.hass
        )
        return self.async_show_form(
            step_id="debug",
            data_schema=self.add_suggested_values_to_schema(
                DEBUG_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "cover_capabilities": caps_text,
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Debug-Diagnostics",
            },
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save and exit the options flow."""
        return await self._update_options()

    async def _update_options(self) -> FlowResult:
        """Update config entry options."""
        return self.async_create_entry(title="", data=self.options)  # type: ignore[return-value]

    def optional_entities(self, keys: list, user_input: dict[str, Any]):
        """Set value to None if key does not exist."""
        for key in keys:
            if key not in user_input:
                user_input[key] = None
