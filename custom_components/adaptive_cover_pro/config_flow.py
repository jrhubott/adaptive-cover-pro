"""Config flow for Adaptive Cover Pro integration."""

from __future__ import annotations

import logging
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
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_USE_MY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_LEFT,
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
    CONF_MOTION_TIMEOUT_MODE,
    DEFAULT_MOTION_TIMEOUT_MODE,
    MOTION_TIMEOUT_MODE_HOLD,
    MOTION_TIMEOUT_MODE_RETURN,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
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
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
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
    DOMAIN,
    SensorType,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPE_MENU = [SensorType.BLIND, SensorType.AWNING, SensorType.TILT]

_STANDALONE_SENTINEL = "__standalone__"

_GEOMETRY_WIKI_URL: dict[str, str] = {
    SensorType.BLIND: "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Vertical",
    SensorType.AWNING: "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Horizontal",
    SensorType.TILT: "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Tilt",
}


def _geometry_wiki_link(sensor_type: str | None) -> str:
    url = _GEOMETRY_WIKI_URL.get(
        sensor_type,
        "https://github.com/jrhubott/adaptive-cover-pro/wiki/Cover-Types",
    )
    return f"[Learn more]({url})"


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

GEOMETRY_VERTICAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HEIGHT_WIN, default=2.1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_WIDTH, default=1.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_DEPTH, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=5.0,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_SILL_HEIGHT, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
    }
)

GEOMETRY_HORIZONTAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LENGTH_AWNING, default=2.1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.3,
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_AWNING_ANGLE, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=45,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_HEIGHT_WIN, default=2.1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
    }
)

GEOMETRY_TILT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TILT_DEPTH, default=3): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_DISTANCE, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_MODE, default="mode2"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["mode1", "mode2"], translation_key="tilt_mode"
            )
        ),
    }
)

SUN_TRACKING_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_ENABLE_SUN_TRACKING, default=True
        ): selector.BooleanSelector(),
        vol.Required(CONF_AZIMUTH, default=180): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=359,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_FOV_LEFT, default=90): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=180,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_FOV_RIGHT, default=90): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=180,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(CONF_MIN_ELEVATION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(CONF_MAX_ELEVATION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_DISTANCE, default=0.5): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_ENABLE_BLIND_SPOT, default=False): selector.BooleanSelector(),
    }
)

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
            CONF_ENABLE_MAX_POSITION, default=False
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
        vol.Optional(
            CONF_ENABLE_MIN_POSITION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_SUNSET_POS): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_MY_POSITION_VALUE): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_SUNSET_USE_MY, default=False): selector.BooleanSelector(),
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
        vol.Optional(CONF_OPEN_CLOSE_THRESHOLD, default=50): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_INVERSE_STATE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_INTERP, default=False): selector.BooleanSelector(),
    }
)

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
        vol.Optional(CONF_START_TIME, default="00:00:00"): selector.TimeSelector(),
        vol.Optional(CONF_START_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_END_TIME, default="00:00:00"): selector.TimeSelector(),
        vol.Optional(CONF_END_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_RETURN_SUNSET, default=False): selector.BooleanSelector(),
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


def _numeric_selector(
    *, device_class: str | None = None, multiple: bool = False
) -> selector.EntitySelector:
    """Return a selector for numeric-state entities, optionally filtered by device_class."""
    if device_class is not None:
        return selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=_NUMERIC_DOMAINS, device_class=device_class
            )
        )
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_NUMERIC_DOMAINS, multiple=multiple)
    )


def _position_slider() -> selector.NumberSelector:
    """Return a reusable 0-100% position slider selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
            unit_of_measurement="%",
        )
    )


FORCE_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FORCE_OVERRIDE_SENSORS, default=[]): _binary_on_selector(
            multiple=True
        ),
        vol.Optional(CONF_FORCE_OVERRIDE_POSITION, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_FORCE_OVERRIDE_MIN_MODE, default=False
        ): selector.BooleanSelector(),
    }
)


def _priority_slider() -> selector.NumberSelector:
    """Return a number selector for pipeline priority (1-99)."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=99,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _build_custom_position_schema_dict() -> dict:
    """Compose the full custom-position schema by iterating CUSTOM_POSITION_SLOTS."""
    schema: dict = {}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        schema[vol.Optional(slot_keys["sensor"])] = _binary_on_selector()
        schema[vol.Optional(slot_keys["position"])] = _position_slider()
        schema[vol.Optional(slot_keys["priority"])] = _priority_slider()
        schema[vol.Optional(slot_keys["min_mode"], default=False)] = (
            selector.BooleanSelector()
        )
        schema[vol.Optional(slot_keys["use_my"], default=False)] = (
            selector.BooleanSelector()
        )
    return schema


CUSTOM_POSITION_SCHEMA = vol.Schema(_build_custom_position_schema_dict())

# Keys in CUSTOM_POSITION_SCHEMA that have no schema default (sensor, position,
# priority). Voluptuous omits them from user_input when cleared, so both flow
# handlers must call optional_entities() with this list before dict.update() --
# otherwise the prior value survives a clear (issue #323).
_CUSTOM_POSITION_OPTIONAL_KEYS: list[str] = [
    slot[field]
    for slot in CUSTOM_POSITION_SLOTS.values()
    for field in ("sensor", "position", "priority")
]

MOTION_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MOTION_SENSORS, default=[]): _presence_like_selector(
            multiple=True
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

WEATHER_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_WEATHER_BYPASS_AUTO_CONTROL, default=True
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_WEATHER_WIND_SPEED_SENSOR, default=vol.UNDEFINED
        ): _numeric_selector(),
        vol.Optional(
            CONF_WEATHER_WIND_DIRECTION_SENSOR, default=vol.UNDEFINED
        ): _numeric_selector(),
        vol.Optional(
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            default=DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=200,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(
            CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
            default=DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=180,
                step=5,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(
            CONF_WEATHER_RAIN_SENSOR, default=vol.UNDEFINED
        ): _numeric_selector(),
        vol.Optional(
            CONF_WEATHER_RAIN_THRESHOLD, default=DEFAULT_WEATHER_RAIN_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=0.5,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(
            CONF_WEATHER_IS_RAINING_SENSOR, default=vol.UNDEFINED
        ): _binary_on_selector(),
        vol.Optional(
            CONF_WEATHER_IS_WINDY_SENSOR, default=vol.UNDEFINED
        ): _binary_on_selector(),
        vol.Optional(CONF_WEATHER_SEVERE_SENSORS, default=[]): _binary_on_selector(
            multiple=True
        ),
        vol.Optional(
            CONF_WEATHER_OVERRIDE_POSITION, default=0
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_WEATHER_OVERRIDE_MIN_MODE, default=False
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_WEATHER_TIMEOUT, default=DEFAULT_WEATHER_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=30,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="seconds",
            )
        ),
    }
)

# --- Light & Cloud (works without climate mode) ---
LIGHT_CLOUD_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_WEATHER_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain="weather")
        ),
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
        ),
        vol.Optional(CONF_LUX_ENTITY, default=vol.UNDEFINED): _numeric_selector(
            device_class="illuminance"
        ),
        vol.Optional(CONF_LUX_THRESHOLD, default=1000): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="lux"
            )
        ),
        vol.Optional(CONF_IRRADIANCE_ENTITY, default=vol.UNDEFINED): _numeric_selector(
            device_class="irradiance"
        ),
        vol.Optional(CONF_IRRADIANCE_THRESHOLD, default=300): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W/m²"
            )
        ),
        vol.Optional(
            CONF_CLOUD_COVERAGE_ENTITY, default=vol.UNDEFINED
        ): _numeric_selector(),
        vol.Optional(
            CONF_CLOUD_COVERAGE_THRESHOLD, default=DEFAULT_CLOUD_COVERAGE_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"
            )
        ),
        vol.Optional(CONF_CLOUD_SUPPRESSION, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_CLOUDY_POSITION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
    }
)

# --- Temperature Climate Mode ---
TEMPERATURE_CLIMATE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CLIMATE_MODE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_TEMP_ENTITY): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["climate", "sensor"])
        ),
        vol.Optional(CONF_TEMP_LOW, default=21): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(CONF_TEMP_HIGH, default=25): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(
            CONF_OUTSIDETEMP_ENTITY, default=vol.UNDEFINED
        ): _numeric_selector(),
        vol.Optional(CONF_OUTSIDE_THRESHOLD, default=25): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(
            CONF_PRESENCE_ENTITY, default=vol.UNDEFINED
        ): _presence_like_selector(),
        vol.Optional(CONF_TRANSPARENT_BLIND, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_WINTER_CLOSE_INSULATION, default=False
        ): selector.BooleanSelector(),
    }
)

# Combined schema for backward compatibility (used by SYNC_CATEGORIES)
CLIMATE_SCHEMA = vol.Schema(
    {
        **dict(LIGHT_CLOUD_SCHEMA.schema.items()),
        **dict(TEMPERATURE_CLIMATE_SCHEMA.schema.items()),
    }
)

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
    """Calculate azimuth edges."""
    return data[CONF_FOV_LEFT] + data[CONF_FOV_RIGHT]


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

    for eid in entities:
        caps = check_cover_features(hass, eid)
        cap_map[eid] = caps
        if caps is None:
            warnings.append(f"⚠️ {eid}: not ready (unavailable)")
        else:
            if not caps.get("has_set_position"):
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
        has_pos = {eid for eid, caps in known.items() if caps.get("has_set_position")}
        no_pos = {
            eid for eid, caps in known.items() if not caps.get("has_set_position")
        }

        if has_pos and no_pos:
            warnings.append(
                "⚠️ Mixed capabilities: some covers support set_position, "
                "others are open/close-only — they will be driven differently."
            )

        if sensor_type == SensorType.TILT:
            if not any(caps.get("has_set_tilt_position") for caps in known.values()):
                warnings.append(
                    "⚠️ Configured as tilt (venetian) but no bound cover "
                    "advertises set_tilt_position."
                )
        elif sensor_type in (SensorType.BLIND, SensorType.AWNING):
            if not any(caps.get("has_set_position") for caps in known.values()):
                type_word = (
                    "vertical blind" if sensor_type == SensorType.BLIND else "awning"
                )
                warnings.append(
                    f"⚠️ Configured as {type_word} but no bound cover supports "
                    "set_position — only open/close will be issued."
                )

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

    cap_label_map = {
        "has_set_position": "set position",
        "has_set_tilt_position": "set tilt",
        "has_open": "open",
        "has_close": "close",
        "has_stop": "stop",
    }

    lines: list[str] = ["**Cover Capabilities**"]
    for eid in entities:
        caps = cap_map.get(eid)
        if caps is None:
            lines.append(f"{eid}: not ready (unavailable)")
        else:
            cap_list = ", ".join(
                label for key, label in cap_label_map.items() if caps.get(key)
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


def _build_config_summary(  # noqa: C901, PLR0912, PLR0915
    config: dict,
    sensor_type: str | None,
    hass: HomeAssistant | None = None,
    sun_times: dict | None = None,
) -> str:
    """Build a narrative summary of the current configuration.

    Produces four sections:
      1. Your Cover  — what is controlled and physical setup
      2. How It Decides — full decision chain: each rule's trigger, target, and
         today's sun times inline; priority badge [N] at end of each rule
      3. Position Limits — compact one-liner for range/default/delta/flags
      4. Decision Priority — compact chain showing active/inactive handlers
    """
    # ---- Gather all values up front ----------------------------------------
    type_labels = {
        SensorType.BLIND: "Vertical Blind",
        SensorType.AWNING: "Horizontal Awning",
        SensorType.TILT: "Venetian / Tilt Blind",
    }
    type_label = type_labels.get(sensor_type, "Cover") if sensor_type else "Cover"

    entities: list[str] = config.get(CONF_ENTITIES) or []
    default_pos = config.get(CONF_DEFAULT_HEIGHT, 0)
    force_pos = config.get(CONF_FORCE_OVERRIDE_POSITION, 0)
    weather_pos = config.get(CONF_WEATHER_OVERRIDE_POSITION, 0)
    motion_timeout = config.get(CONF_MOTION_TIMEOUT, 300)
    manual_dur = config.get(CONF_MANUAL_OVERRIDE_DURATION)

    has_force = bool(config.get(CONF_FORCE_OVERRIDE_SENSORS))
    has_weather = any(
        [
            config.get(CONF_WEATHER_WIND_SPEED_SENSOR),
            config.get(CONF_WEATHER_RAIN_SENSOR),
            config.get(CONF_WEATHER_IS_RAINING_SENSOR),
            config.get(CONF_WEATHER_IS_WINDY_SENSOR),
            bool(config.get(CONF_WEATHER_SEVERE_SENSORS)),
        ]
    )
    has_motion = bool(config.get(CONF_MOTION_SENSORS))
    # Build per-slot custom position data: list of (slot, entity_id, position, priority, use_my)
    _custom_slots: list[tuple[int, str, int, int, bool]] = []
    for _i in range(1, 5):
        _sensor = config.get(f"custom_position_sensor_{_i}")
        _pos = config.get(f"custom_position_{_i}")
        if _sensor and _pos is not None:
            _pri = int(
                config.get(f"custom_position_priority_{_i}")
                or DEFAULT_CUSTOM_POSITION_PRIORITY
            )
            _use_my = bool(config.get(f"custom_position_use_my_{_i}"))
            _custom_slots.append((_i, _sensor, int(_pos), _pri, _use_my))
    has_custom_position = bool(_custom_slots)
    my_pos = config.get(CONF_MY_POSITION_VALUE)  # None = not configured
    has_cloud = bool(config.get(CONF_CLOUD_SUPPRESSION))
    has_climate = bool(config.get(CONF_CLIMATE_MODE))
    sun_tracking_enabled = config.get(CONF_ENABLE_SUN_TRACKING, True)
    has_glare = (
        bool(config.get(CONF_ENABLE_GLARE_ZONES)) and sensor_type == SensorType.BLIND
    )

    def _pos_label(raw_pct: int, use_my: bool) -> str:
        """Render a target as 'My (N%)' when the My preset flag is active."""
        if use_my and my_pos is not None:
            return f"My ({my_pos}%)"
        if use_my:
            return f"My (not set → {raw_pct}%)"
        return f"{raw_pct}%"

    def _badge(priority: int) -> str:
        """Render a priority badge suffix: two nbsp + [N]."""
        return f"\u00a0\u00a0[{priority}]"

    def _fmt_sun_dt(value) -> str | None:
        """Format a sun-times datetime as HH:MM; None passes through."""
        return value.strftime("%H:%M") if value is not None else None

    def _offset_str(minutes: int) -> str:
        """Format a minutes offset as (+N min) / (-N min); 0 → empty."""
        if minutes > 0:
            return f"+{minutes} min"
        if minutes < 0:
            return f"{minutes} min"
        return ""

    _solar_start = sun_times.get("solar_start") if sun_times else None
    _solar_end = sun_times.get("solar_end") if sun_times else None
    _sunset_eff = sun_times.get("sunset_eff") if sun_times else None
    _sunrise_eff = sun_times.get("sunrise_eff") if sun_times else None

    lines: list[str] = []

    # =========================================================================
    # Section 1: Your Cover
    # =========================================================================
    lines.append("**Your Cover**")

    # Type + entities
    if entities:
        entity_str = ", ".join(entities)
        lines.append(f"{type_label} controlling {entity_str}")
    else:
        lines.append(type_label)

    # Physical dimensions in plain English
    if sensor_type in (SensorType.BLIND, None):
        h = config.get(CONF_HEIGHT_WIN)
        d = config.get(CONF_DISTANCE)
        depth = config.get(CONF_WINDOW_DEPTH) or 0
        sill = config.get(CONF_SILL_HEIGHT) or 0
        dim_parts = []
        if h is not None:
            dim_parts.append(f"{h}m tall window")
        if d is not None:
            dim_parts.append(f"blocking sun {d}m from the glass")
        extras = []
        if depth > 0:
            extras.append(f"reveal {depth}m")
        if sill > 0:
            extras.append(f"sill {sill}m")
        dim_str = ", ".join(dim_parts)
        if extras:
            dim_str += f" ({', '.join(extras)})"
        if dim_str:
            lines.append(dim_str)
    elif sensor_type == SensorType.AWNING:
        parts = []
        if (v := config.get(CONF_LENGTH_AWNING)) is not None:
            parts.append(f"{v}m awning")
        if (v := config.get(CONF_AWNING_ANGLE)) is not None:
            parts.append(f"angled at {v}°")
        if (v := config.get(CONF_HEIGHT_WIN)) is not None:
            parts.append(f"{v}m window height")
        if (v := config.get(CONF_DISTANCE)) is not None:
            parts.append(f"blocking sun {v}m from wall")
        if parts:
            lines.append(", ".join(parts))
    elif sensor_type == SensorType.TILT:
        parts = []
        if (v := config.get(CONF_TILT_DEPTH)) is not None:
            parts.append(f"slat depth {v}cm")
        if (v := config.get(CONF_TILT_DISTANCE)) is not None:
            parts.append(f"spacing {v}cm")
        if (v := config.get(CONF_TILT_MODE)) is not None:
            parts.append(f"mode: {v}")
        if parts:
            lines.append(", ".join(parts))

    # =========================================================================
    # Section 1c: Cover Capability Warnings
    # =========================================================================
    _, cap_warnings = _check_cover_capabilities(config, sensor_type, hass)
    if cap_warnings:
        lines.append("")
        lines.append("**Cover Warnings**")
        lines.extend(cap_warnings)

    # =========================================================================
    # Section 2: How It Decides
    # =========================================================================
    lines.append("")
    lines.append("**How It Decides** (first matching rule wins)")

    # Force override — highest priority safety (100)
    if has_force:
        n = len(config.get(CONF_FORCE_OVERRIDE_SENSORS) or [])
        sensor_word = "sensor" if n == 1 else "sensors"
        min_mode_str = (
            " (as minimum)" if config.get(CONF_FORCE_OVERRIDE_MIN_MODE) else ""
        )
        lines.append(
            f"🔒 Force override: if any of {n} {sensor_word} is on → covers go to "
            f"{force_pos}%{min_mode_str} (overrides everything else)"
            f"{_badge(100)}"
        )

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
            wind_part = f"wind > {wind_thresh}"
            if wind_dir_sensor and wind_dir_tol is not None:
                wind_part += f" from window ±{wind_dir_tol}°"
            wx_parts.append(wind_part)
        if rain_sensor and rain_thresh is not None:
            wx_parts.append(f"rain > {rain_thresh}")
        if is_rain:
            wx_parts.append("is-raining")
        if is_wind:
            wx_parts.append("is-windy")
        if severe:
            wx_parts.append(f"{len(severe)} severe weather sensor(s)")
        wx_condition = " or ".join(wx_parts) if wx_parts else "weather condition"
        wx_delay = config.get(CONF_WEATHER_TIMEOUT)
        delay_str = f" (waits {wx_delay}s after clearing)" if wx_delay else ""
        weather_min_str = (
            " (as minimum)" if config.get(CONF_WEATHER_OVERRIDE_MIN_MODE) else ""
        )
        bypass_str = (
            " ⚠️ halts all automation while triggered"
            if config.get(CONF_WEATHER_BYPASS_AUTO_CONTROL)
            else ""
        )
        lines.append(
            f"🌧️ Weather safety: if {wx_condition} → covers retract to "
            f"{weather_pos}%{weather_min_str}{delay_str}{bypass_str}"
            f"{_badge(90)}"
        )

    # Manual override (80)
    mo_parts = []
    if manual_dur is not None:
        mo_parts.append(f"pauses for {_format_duration(manual_dur)}")
    threshold = config.get(CONF_MANUAL_THRESHOLD)
    if threshold is not None:
        mo_parts.append(f"threshold {threshold}%")
    if config.get(CONF_MANUAL_OVERRIDE_RESET):
        mo_parts.append("resets on next move")
    if config.get(CONF_MANUAL_IGNORE_INTERMEDIATE):
        mo_parts.append("ignores intermediate positions")
    transit_timeout = config.get(CONF_TRANSIT_TIMEOUT)
    if (
        transit_timeout is not None
        and int(transit_timeout) != DEFAULT_TRANSIT_TIMEOUT_SECONDS
    ):
        mo_parts.append(f"transit timeout: {int(transit_timeout)}s")
    mo_str = f" ({', '.join(mo_parts)})" if mo_parts else ""
    lines.append(
        f"✋ Manual override: pauses automatic control when you move the cover"
        f"{mo_str}{_badge(80)}"
    )

    # Custom positions — each slot at its own configured priority
    if has_custom_position:
        for _slot, _eid, _pos, _pri, _use_my in _custom_slots:
            target = _pos_label(_pos, _use_my)
            cp_min = (
                " (as minimum)"
                if config.get(f"custom_position_min_mode_{_slot}")
                else ""
            )
            lines.append(
                f"🎯 Custom #{_slot}: if {_eid} is on → {target}{cp_min}"
                f" — bypasses delta gates and auto-control"
                f"{_badge(_pri)}"
            )

    # Motion timeout (75)
    timeout_mode = config.get(CONF_MOTION_TIMEOUT_MODE, DEFAULT_MOTION_TIMEOUT_MODE)
    if has_motion:
        n = len(config.get(CONF_MOTION_SENSORS) or [])
        sensor_word = "sensor" if n == 1 else "sensors"
        if timeout_mode == MOTION_TIMEOUT_MODE_HOLD:
            action = (
                "covers hold current position (return to default when sun leaves FOV)"
            )
        else:
            action = f"covers return to default ({default_pos}%)"
        lines.append(
            f"🚶 Motion-based: if no occupancy for {motion_timeout}s "
            f"({n} {sensor_word}) → {action}"
            f"{_badge(75)}"
        )
    elif timeout_mode == MOTION_TIMEOUT_MODE_HOLD:
        lines.append(
            "⚠️ hold_position mode is set but no motion sensors are configured "
            "— the setting has no effect until sensors are added"
        )

    # Cloud suppression (60)
    if has_cloud:
        cloud_parts = []
        if v := config.get(CONF_LUX_ENTITY):
            t = config.get(CONF_LUX_THRESHOLD)
            cloud_parts.append(f"lux < {t} lx" if t is not None else f"lux ({v})")
        if v := config.get(CONF_IRRADIANCE_ENTITY):
            t = config.get(CONF_IRRADIANCE_THRESHOLD)
            cloud_parts.append(
                f"irradiance < {t} W/m²" if t is not None else f"irradiance ({v})"
            )
        if v := config.get(CONF_CLOUD_COVERAGE_ENTITY):
            t = config.get(CONF_CLOUD_COVERAGE_THRESHOLD)
            cloud_parts.append(f"cloud > {t}%" if t is not None else f"cloud ({v})")
        wx_states = config.get(CONF_WEATHER_STATE) or []
        if wx_states and config.get(CONF_WEATHER_ENTITY):
            cloud_parts.append(f"weather in {{{', '.join(wx_states)}}}")
        cloud_str = f" when {', '.join(cloud_parts)}" if cloud_parts else ""
        cloudy_pos = config.get(CONF_CLOUDY_POSITION)
        if cloudy_pos is not None:
            fallback_label = f"cloudy position {cloudy_pos}%"
        else:
            fallback_label = f"default ({default_pos}%)"
        lines.append(
            f"☁️ Cloud suppression: skips sun tracking{cloud_str} → "
            f"{fallback_label}{_badge(60)}"
        )
    elif any(
        [
            config.get(CONF_LUX_ENTITY),
            config.get(CONF_IRRADIANCE_ENTITY),
            config.get(CONF_CLOUD_COVERAGE_ENTITY),
        ]
    ):
        # Sensors configured but suppression toggle off — mention them as informational
        sensor_names = []
        if config.get(CONF_LUX_ENTITY):
            sensor_names.append("lux")
        if config.get(CONF_IRRADIANCE_ENTITY):
            sensor_names.append("irradiance")
        if config.get(CONF_CLOUD_COVERAGE_ENTITY):
            sensor_names.append("cloud coverage")
        lines.append(
            f"📊 Light sensors configured ({', '.join(sensor_names)}) but cloud suppression is off."
        )

    # Warn if cloudy_position set but cloud suppression is disabled
    cloudy_pos_cfg = config.get(CONF_CLOUDY_POSITION)
    if cloudy_pos_cfg is not None and not has_cloud:
        lines.append(
            f"⚠️ Cloudy position ({cloudy_pos_cfg}%) configured but cloud suppression is disabled — value will be ignored."
        )

    # Climate mode (50)
    if has_climate:
        cl_parts = []
        lo = config.get(CONF_TEMP_LOW)
        hi = config.get(CONF_TEMP_HIGH)
        temp_entity = config.get(CONF_TEMP_ENTITY)
        if lo is not None and hi is not None:
            cl_parts.append(f"comfort range {lo}–{hi}°C")
        if temp_entity:
            cl_parts.append(f"using {temp_entity}")
        outside = config.get(CONF_OUTSIDETEMP_ENTITY)
        if outside:
            out_thresh = config.get(CONF_OUTSIDE_THRESHOLD)
            if out_thresh is not None:
                cl_parts.append(f"outside: {outside} > {out_thresh}°C")
            else:
                cl_parts.append(f"outside: {outside}")
        weather_ent = config.get(CONF_WEATHER_ENTITY)
        if weather_ent:
            cl_parts.append(f"weather: {weather_ent}")
        presence = config.get(CONF_PRESENCE_ENTITY)
        if presence:
            cl_parts.append(f"presence: {presence}")
        if config.get(CONF_TRANSPARENT_BLIND):
            cl_parts.append("transparent blind")
        if config.get(CONF_WINTER_CLOSE_INSULATION):
            cl_parts.append("closes fully in winter for insulation")
        cl_str = f" ({', '.join(cl_parts)})" if cl_parts else ""
        lines.append(
            f"🌡️ Climate mode: adjusts strategy for heating/cooling{cl_str}{_badge(50)}"
        )

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
            gz_parts.append(f"zones: {', '.join(zone_names)}")
        if width:
            gz_parts.append(f"{float(width):.2f}m window")
        gz_str = f" ({', '.join(gz_parts)})" if gz_parts else ""
        lines.append(
            f"🔆 Glare zones: lowers blind further to protect floor areas from glare"
            f"{gz_str}{_badge(45)}"
        )

    # Solar tracking — baseline calculation (40)
    azimuth = config.get(CONF_AZIMUTH)
    fov_l = config.get(CONF_FOV_LEFT)
    fov_r = config.get(CONF_FOV_RIGHT)
    min_elev = config.get(CONF_MIN_ELEVATION)
    max_elev = config.get(CONF_MAX_ELEVATION)
    if sun_tracking_enabled:
        sun_parts = []
        if azimuth is not None:
            sun_parts.append(f"azimuth {azimuth}°")
        if fov_l is not None and fov_r is not None:
            sun_parts.append(f"±{fov_l}°/{fov_r}° field of view")
        elev_parts = []
        if min_elev is not None:
            elev_parts.append(f"above {min_elev}°")
        if max_elev is not None:
            elev_parts.append(f"below {max_elev}°")
        if elev_parts:
            sun_parts.append(f"elevation {' and '.join(elev_parts)}")
        sun_desc = f" ({', '.join(sun_parts)})" if sun_parts else ""
        # Today's solar window annotation
        if _solar_start is not None and _solar_end is not None:
            today_str = (
                f" (today: sun in window {_fmt_sun_dt(_solar_start)} → "
                f"{_fmt_sun_dt(_solar_end)})"
            )
        elif sun_times is not None:
            today_str = " (today: sun does not enter window)"
        else:
            today_str = ""
        lines.append(
            f"☀️ Tracks the sun{sun_desc} and calculates position to block "
            f"direct sunlight{today_str}{_badge(40)}"
        )
    else:
        lines.append(
            "☀️ Sun tracking disabled — covers hold position; climate, manual override, "
            f"custom positions, and other overrides remain active{_badge(40)}"
        )

    # Timing window (sub-bullet under ☀️)
    start_time = config.get(CONF_START_TIME)
    start_entity = config.get(CONF_START_ENTITY)
    end_time = config.get(CONF_END_TIME)
    end_entity = config.get(CONF_END_ENTITY)
    sunset_pos = config.get(CONF_SUNSET_POS)
    sunset_off = config.get(CONF_SUNSET_OFFSET, 0) or 0
    sunrise_off = config.get(CONF_SUNRISE_OFFSET, 0) or 0
    timing_parts = []
    if start_entity:
        timing_parts.append(f"from {start_entity}")
    elif start_time:
        timing_parts.append(f"from {start_time}")
    if end_entity:
        timing_parts.append(f"until {end_entity}")
    elif end_time:
        timing_parts.append(f"until {end_time}")
    if timing_parts or sunset_pos is not None:
        timing_str = (
            " ".join(timing_parts) if timing_parts else "Active during daylight"
        )
        indent = "\u00a0" * 4
        lines.append(f"{indent}🕒 {timing_str}.")
        if sunset_pos is not None:
            # Merge today's effective time and the offset into one parenthetical
            def _sun_annotation(today_dt, offset_min: int) -> str:
                parts = []
                if today_dt is not None:
                    parts.append(f"today ~{_fmt_sun_dt(today_dt)}")
                off = _offset_str(int(offset_min))
                if off:
                    parts.append(off)
                return f" ({', '.join(parts)})" if parts else ""

            sunset_ann = _sun_annotation(_sunset_eff, sunset_off)
            sunrise_ann = _sun_annotation(_sunrise_eff, sunrise_off)
            has_end_time = bool(end_time or end_entity)
            _sunset_use_my = bool(config.get(CONF_SUNSET_USE_MY))
            _sunset_target = _pos_label(int(sunset_pos), _sunset_use_my)
            if has_end_time and int(sunset_pos) != int(default_pos):
                lines.append(f"{indent}🔚 After end time → {default_pos}%.")
                lines.append(f"{indent}🌅 After sunset{sunset_ann} → {_sunset_target}.")
            else:
                label = "end time/sunset" if has_end_time else "sunset"
                lines.append(
                    f"{indent}🌅 After {label}{sunset_ann} → {_sunset_target}."
                )
            lines.append(
                f"{indent}🌄 After sunrise{sunrise_ann} → {default_pos}% (tracking resumes)."
            )
            if config.get(CONF_RETURN_SUNSET):
                lines.append(f"{indent}🔚 Return to sunset position at end time: on")

    # Blind spot (sub-bullet / informational, no priority of its own)
    if config.get(CONF_ENABLE_BLIND_SPOT):
        bs_l = config.get(CONF_BLIND_SPOT_LEFT)
        bs_r = config.get(CONF_BLIND_SPOT_RIGHT)
        bs_e = config.get(CONF_BLIND_SPOT_ELEVATION)
        bs_parts = []
        if bs_l is not None and bs_r is not None:
            bs_parts.append(f"{bs_l}°–{bs_r}°")
        if bs_e is not None:
            bs_parts.append(f"up to {bs_e}° elevation")
        bs_str = " ".join(bs_parts)
        lines.append(
            f"🟥 Blind spot: ignores sun at {bs_str} inward from FOV left (e.g. tree or roof overhang)."
        )

    # Default fallback (priority 0) — shown as the final row of the chain
    lines.append(f"🌙 Default (no rule matches) → {default_pos}%{_badge(0)}")

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
            qualifier = " (during sun tracking only)"
        elif enable_min and not enable_max:
            qualifier = " (min during sun tracking only)"
        elif enable_max and not enable_min:
            qualifier = " (max during sun tracking only)"
        else:
            qualifier = ""
        limit_parts.append(f"Range: {lo_str}–{hi_str}{qualifier}")
    if default_pos is not None:
        limit_parts.append(f"Default: {default_pos}%")
    delta_pos = config.get(CONF_DELTA_POSITION)
    delta_time = config.get(CONF_DELTA_TIME)
    if delta_pos is not None:
        limit_parts.append(f"Min change: {delta_pos}%")
    if delta_time is not None:
        limit_parts.append(f"Min interval: {delta_time} min")
    if config.get(CONF_INVERSE_STATE):
        limit_parts.append("Inverse state")
    oc_thresh = config.get(CONF_OPEN_CLOSE_THRESHOLD)
    if oc_thresh is not None:
        limit_parts.append(f"Open/close threshold: {oc_thresh}%")
    if config.get(CONF_INTERP):
        interp_lo = config.get(CONF_INTERP_START)
        interp_hi = config.get(CONF_INTERP_END)
        if interp_lo is not None and interp_hi is not None:
            limit_parts.append(f"Calibration {interp_lo}→{interp_hi}")
        else:
            limit_parts.append("Position calibration on")
    if limit_parts:
        lines.append("")
        lines.append("**Position Limits**")
        lines.append(" · ".join(limit_parts))

    # Somfy My preset info / warning
    _any_use_my = bool(config.get(CONF_SUNSET_USE_MY)) or any(
        bool(config.get(f"custom_position_use_my_{_i}")) for _i in range(1, 5)
    )
    if my_pos is not None:
        lines.append(f"🎛️ Somfy My preset: {my_pos}% (used where enabled above)")
    elif _any_use_my:
        lines.append(
            "⚠️ Somfy My preset is enabled for one or more targets but "
            "My Preset Value is not set — falls back to configured %."
        )

    # =========================================================================
    # Section 4: Decision Priority (compact reference)
    # =========================================================================
    def _ch(active: bool, short: str, pri: int) -> str:
        mark = "✅" if active else "❌"
        return f"{mark}{short}"

    # Build the full priority chain including per-slot custom positions.
    # Each entry is (priority, label, active) so we can sort and render.
    _chain_entries: list[tuple[int, str, bool]] = [
        (100, "Force", has_force),
        (90, "Weather", has_weather),
        (80, "Manual", True),
        (75, "Motion", has_motion),
        (60, "Cloud", has_cloud),
        (50, "Climate", has_climate),
        (40, "Solar", sun_tracking_enabled),
        (0, "Default", True),
    ]
    if sensor_type == SensorType.BLIND or sensor_type is None:
        _chain_entries.append((45, "Glare", has_glare))
    # Insert one entry per custom slot at its configured priority
    for _slot, _eid, _pos, _pri, _use_my in _custom_slots:
        _chain_entries.append((_pri, f"Custom#{_slot}({_pri})", True))
    # Sort highest priority first
    _chain_entries.sort(key=lambda e: e[0], reverse=True)
    chain = [_ch(active, short, pri) for pri, short, active in _chain_entries]

    lines.append("")
    lines.append("**Decision Priority** (highest wins, ✅ active ❌ not configured)")
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
            CONF_SUNSET_POS,
            CONF_MY_POSITION_VALUE,
            CONF_SUNSET_USE_MY,
            CONF_SUNSET_OFFSET,
            CONF_SUNRISE_OFFSET,
            CONF_OPEN_CLOSE_THRESHOLD,
            CONF_INVERSE_STATE,
            CONF_INTERP,
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
            CONF_RETURN_SUNSET,
        }
    ),
    "manual_override": frozenset(
        {
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_MANUAL_OVERRIDE_RESET,
            CONF_MANUAL_THRESHOLD,
            CONF_MANUAL_IGNORE_INTERMEDIATE,
            CONF_TRANSIT_TIMEOUT,
        }
    ),
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
    # Legacy alias: full union of force_override_values + force_override_sensors
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
        for k in ("position", "priority", "min_mode", "use_my")
    ),
    "custom_position_sensors": frozenset(
        keys["sensor"] for keys in CUSTOM_POSITION_SLOTS.values()
    ),
    # Legacy alias: full union of custom_position_values + custom_position_sensors
    "custom_position": frozenset(
        v for keys in CUSTOM_POSITION_SLOTS.values() for v in keys.values()
    ),
    "motion_override_values": frozenset(
        {
            CONF_MOTION_TIMEOUT,
            CONF_MOTION_TIMEOUT_MODE,
        }
    ),
    "motion_override_sensors": frozenset(
        {
            CONF_MOTION_SENSORS,
        }
    ),
    # Legacy alias: full union of motion_override_values + motion_override_sensors
    "motion_override": frozenset(
        {
            CONF_MOTION_SENSORS,
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
        {
            CONF_ENABLE_GLARE_ZONES,
            "glare_zone_1_name",
            "glare_zone_1_x",
            "glare_zone_1_y",
            "glare_zone_1_radius",
            "glare_zone_2_name",
            "glare_zone_2_x",
            "glare_zone_2_y",
            "glare_zone_2_radius",
            "glare_zone_3_name",
            "glare_zone_3_x",
            "glare_zone_3_y",
            "glare_zone_3_radius",
            "glare_zone_4_name",
            "glare_zone_4_x",
            "glare_zone_4_y",
            "glare_zone_4_radius",
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
    "force_override_values",
    "force_override_sensors",
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
    if sensor_type == SensorType.TILT:
        entity_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                filter=selector.EntityFilterSelectorConfig(
                    domain="cover",
                    supported_features=["cover.CoverEntityFeature.SET_TILT_POSITION"],
                ),
            )
        )
    else:
        entity_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                filter=selector.EntityFilterSelectorConfig(
                    domain="cover",
                ),
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
    return vol.Schema(schema_dict)


def _get_geometry_schema(sensor_type: str) -> vol.Schema:
    """Return the geometry schema for the given sensor type."""
    if sensor_type == SensorType.BLIND:
        return GEOMETRY_VERTICAL_SCHEMA
    if sensor_type == SensorType.AWNING:
        return GEOMETRY_HORIZONTAL_SCHEMA
    if sensor_type == SensorType.TILT:
        return GEOMETRY_TILT_SCHEMA
    return GEOMETRY_VERTICAL_SCHEMA


def _get_sun_tracking_schema(sensor_type: str | None) -> vol.Schema:
    """Return sun tracking schema, adding glare zones toggle for vertical covers."""
    if sensor_type == SensorType.BLIND:
        return SUN_TRACKING_SCHEMA.extend(
            {
                vol.Optional(
                    CONF_ENABLE_GLARE_ZONES, default=False
                ): selector.BooleanSelector(),
            }
        )
    return SUN_TRACKING_SCHEMA


def _build_glare_zones_schema(options: dict | None = None) -> vol.Schema:
    """Build the glare zones schema: enable toggle, window width, and 4 zone slots."""
    opts = options or {}
    schema_dict: dict = {}
    for i in range(1, 5):
        prefix = f"glare_zone_{i}"
        schema_dict[
            vol.Optional(f"{prefix}_name", default=opts.get(f"{prefix}_name", ""))
        ] = selector.TextSelector()
        schema_dict[
            vol.Optional(f"{prefix}_x", default=opts.get(f"{prefix}_x", 0.0))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-5.0,
                max=5.0,
                step=0.05,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        )
        schema_dict[
            vol.Optional(f"{prefix}_y", default=opts.get(f"{prefix}_y", 1.0))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=10.0,
                step=0.05,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        )
        schema_dict[
            vol.Optional(f"{prefix}_radius", default=opts.get(f"{prefix}_radius", 0.3))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=2.0,
                step=0.05,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        )
    return vol.Schema(schema_dict)


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle ConfigFlow."""

    VERSION = 2

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
                if entity_entry:
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
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_sun_tracking()

        schema = _get_geometry_schema(self.type_blind)
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
            self.config.update(user_input)
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()

        schema = _build_glare_zones_schema(self.config)
        return self.async_show_form(
            step_id="glare_zones",
            data_schema=schema,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Glare-Zones"
            },
        )

    async def async_step_sun_tracking(self, user_input: dict[str, Any] | None = None):
        """Configure sun tracking parameters."""
        if user_input is not None:
            self.optional_entities([CONF_MIN_ELEVATION, CONF_MAX_ELEVATION], user_input)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
                and user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]
            ):
                return self.async_show_form(
                    step_id="sun_tracking",
                    data_schema=_get_sun_tracking_schema(self.type_blind),
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Sun-Tracking"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_position()
        return self.async_show_form(
            step_id="sun_tracking",
            data_schema=_get_sun_tracking_schema(self.type_blind),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Sun-Tracking"
            },
        )

    async def async_step_position(self, user_input: dict[str, Any] | None = None):
        """Configure position settings."""
        if user_input is not None:
            self.config.update(user_input)
            # Quick setup: skip optional screens, go straight to summary
            if self.setup_mode == "quick":
                return await self.async_step_summary()
            if self.config.get(CONF_ENABLE_BLIND_SPOT):
                return await self.async_step_blind_spot()
            if self.type_blind == SensorType.BLIND and self.config.get(
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
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
            },
        )

    async def async_step_blind_spot(self, user_input: dict[str, Any] | None = None):
        """Add blindspot to data."""
        edges = _get_azimuth_edges(self.config)
        schema = vol.Schema(
            {
                vol.Required(CONF_BLIND_SPOT_LEFT, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=0,
                        max=edges - 1,
                    )
                ),
                vol.Required(CONF_BLIND_SPOT_RIGHT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=1,
                        max=edges,
                    )
                ),
                vol.Optional(CONF_BLIND_SPOT_ELEVATION): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=90,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                    )
                ),
            }
        )
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
            if self.type_blind == SensorType.BLIND and self.config.get(
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
            return await self.async_step_force_override()
        return self.async_show_form(
            step_id="manual_override",
            data_schema=MANUAL_OVERRIDE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_force_override(self, user_input: dict[str, Any] | None = None):
        """Configure force override sensors."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_custom_position()
        return self.async_show_form(
            step_id="force_override",
            data_schema=FORCE_OVERRIDE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Force-Override"
            },
        )

    async def async_step_custom_position(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure custom position sensors."""
        if user_input is not None:
            self.optional_entities(_CUSTOM_POSITION_OPTIONAL_KEYS, user_input)
            self.config.update(user_input)
            return await self.async_step_motion_override()
        return self.async_show_form(
            step_id="custom_position",
            data_schema=CUSTOM_POSITION_SCHEMA,
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
            self.optional_entities(
                [
                    CONF_WEATHER_WIND_SPEED_SENSOR,
                    CONF_WEATHER_WIND_DIRECTION_SENSOR,
                    CONF_WEATHER_RAIN_SENSOR,
                    CONF_WEATHER_IS_RAINING_SENSOR,
                    CONF_WEATHER_IS_WINDY_SENSOR,
                ],
                user_input,
            )
            self.config.update(user_input)
            return await self.async_step_light_cloud()
        return self.async_show_form(
            step_id="weather_override",
            data_schema=WEATHER_OVERRIDE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Weather-Safety"
            },
        )

    async def async_step_light_cloud(self, user_input: dict[str, Any] | None = None):
        """Configure light sensors, weather conditions, and cloud suppression."""
        if user_input is not None:
            self.optional_entities(
                [
                    CONF_WEATHER_ENTITY,
                    CONF_LUX_ENTITY,
                    CONF_IRRADIANCE_ENTITY,
                    CONF_CLOUD_COVERAGE_ENTITY,
                ],
                user_input,
            )
            self.config.update(user_input)
            return await self.async_step_temperature_climate()
        return self.async_show_form(
            step_id="light_cloud",
            data_schema=LIGHT_CLOUD_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_temperature_climate(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure temperature-based climate mode."""
        if user_input is not None:
            entities = [
                CONF_TEMP_ENTITY,
                CONF_OUTSIDETEMP_ENTITY,
                CONF_PRESENCE_ENTITY,
            ]
            self.optional_entities(entities, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="temperature_climate",
                    data_schema=TEMPERATURE_CLIMATE_SCHEMA,
                    errors={CONF_TEMP_ENTITY: "Required when climate mode is enabled"},
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="temperature_climate",
            data_schema=TEMPERATURE_CLIMATE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options (combined, for backward compat with options flow)."""
        if user_input is not None:
            entities = [
                CONF_TEMP_ENTITY,
                CONF_OUTSIDETEMP_ENTITY,
                CONF_WEATHER_ENTITY,
                CONF_PRESENCE_ENTITY,
                CONF_LUX_ENTITY,
                CONF_IRRADIANCE_ENTITY,
            ]
            self.optional_entities(entities, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="climate",
                    data_schema=CLIMATE_SCHEMA,
                    errors={CONF_TEMP_ENTITY: "Required when climate mode is enabled"},
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Climate"
                    },
                )
            self.config.update(user_input)
            if self.config.get(CONF_WEATHER_ENTITY):
                return await self.async_step_weather()
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="climate",
            data_schema=CLIMATE_SCHEMA,
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Climate"
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
        summary_text = _build_config_summary(
            self.config, self.type_blind, self.hass, sun_times
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

        type_mapping = {
            "cover_blind": "Vertical",
            "cover_awning": "Horizontal",
            "cover_tilt": "Tilt",
        }
        return self.async_create_entry(
            title=f"{type_mapping[self.type_blind]} {self.config['name']}",
            data={
                "name": self.config["name"],
                CONF_SENSOR_TYPE: self.type_blind,
            },
            options={
                CONF_MODE: self.mode,
                CONF_AZIMUTH: self.config.get(CONF_AZIMUTH),
                CONF_HEIGHT_WIN: self.config.get(CONF_HEIGHT_WIN),
                CONF_DISTANCE: self.config.get(CONF_DISTANCE),
                CONF_WINDOW_DEPTH: self.config.get(CONF_WINDOW_DEPTH),
                CONF_SILL_HEIGHT: self.config.get(CONF_SILL_HEIGHT),
                CONF_DEFAULT_HEIGHT: self.config.get(CONF_DEFAULT_HEIGHT),
                CONF_MAX_POSITION: self.config.get(CONF_MAX_POSITION),
                CONF_ENABLE_MAX_POSITION: self.config.get(CONF_ENABLE_MAX_POSITION),
                CONF_MIN_POSITION: self.config.get(CONF_MIN_POSITION),
                CONF_ENABLE_MIN_POSITION: self.config.get(CONF_ENABLE_MIN_POSITION),
                CONF_FOV_LEFT: self.config.get(CONF_FOV_LEFT),
                CONF_FOV_RIGHT: self.config.get(CONF_FOV_RIGHT),
                CONF_ENTITIES: self.config.get(CONF_ENTITIES),
                CONF_INVERSE_STATE: self.config.get(CONF_INVERSE_STATE),
                CONF_SUNSET_POS: self.config.get(CONF_SUNSET_POS),
                CONF_SUNSET_OFFSET: self.config.get(CONF_SUNSET_OFFSET),
                CONF_SUNRISE_OFFSET: self.config.get(CONF_SUNRISE_OFFSET),
                CONF_LENGTH_AWNING: self.config.get(CONF_LENGTH_AWNING),
                CONF_AWNING_ANGLE: self.config.get(CONF_AWNING_ANGLE),
                CONF_TILT_DISTANCE: self.config.get(CONF_TILT_DISTANCE),
                CONF_TILT_DEPTH: self.config.get(CONF_TILT_DEPTH),
                CONF_TILT_MODE: self.config.get(CONF_TILT_MODE),
                CONF_TEMP_ENTITY: self.config.get(CONF_TEMP_ENTITY),
                CONF_PRESENCE_ENTITY: self.config.get(CONF_PRESENCE_ENTITY),
                CONF_WEATHER_ENTITY: self.config.get(CONF_WEATHER_ENTITY),
                CONF_TEMP_LOW: self.config.get(CONF_TEMP_LOW),
                CONF_TEMP_HIGH: self.config.get(CONF_TEMP_HIGH),
                CONF_OUTSIDETEMP_ENTITY: self.config.get(CONF_OUTSIDETEMP_ENTITY),
                CONF_CLIMATE_MODE: self.config.get(CONF_CLIMATE_MODE),
                CONF_WEATHER_STATE: self.config.get(CONF_WEATHER_STATE),
                CONF_DELTA_POSITION: self.config.get(CONF_DELTA_POSITION) or 2,
                CONF_DELTA_TIME: self.config.get(CONF_DELTA_TIME) or 2,
                CONF_START_TIME: self.config.get(CONF_START_TIME),
                CONF_START_ENTITY: self.config.get(CONF_START_ENTITY),
                CONF_END_TIME: self.config.get(CONF_END_TIME),
                CONF_END_ENTITY: self.config.get(CONF_END_ENTITY),
                CONF_FORCE_OVERRIDE_SENSORS: self.config.get(
                    CONF_FORCE_OVERRIDE_SENSORS, []
                ),
                CONF_FORCE_OVERRIDE_POSITION: self.config.get(
                    CONF_FORCE_OVERRIDE_POSITION, 0
                ),
                CONF_MOTION_SENSORS: self.config.get(CONF_MOTION_SENSORS, []),
                CONF_MOTION_TIMEOUT: self.config.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                CONF_MANUAL_OVERRIDE_DURATION: self.config.get(
                    CONF_MANUAL_OVERRIDE_DURATION
                )
                or {"hours": 2},
                CONF_MANUAL_OVERRIDE_RESET: self.config.get(CONF_MANUAL_OVERRIDE_RESET),
                CONF_MANUAL_THRESHOLD: self.config.get(CONF_MANUAL_THRESHOLD),
                CONF_MANUAL_IGNORE_INTERMEDIATE: self.config.get(
                    CONF_MANUAL_IGNORE_INTERMEDIATE
                ),
                CONF_OPEN_CLOSE_THRESHOLD: self.config.get(
                    CONF_OPEN_CLOSE_THRESHOLD, 50
                ),
                CONF_BLIND_SPOT_RIGHT: self.config.get(CONF_BLIND_SPOT_RIGHT, None),
                CONF_BLIND_SPOT_LEFT: self.config.get(CONF_BLIND_SPOT_LEFT, None),
                CONF_BLIND_SPOT_ELEVATION: self.config.get(
                    CONF_BLIND_SPOT_ELEVATION, None
                ),
                CONF_ENABLE_BLIND_SPOT: self.config.get(CONF_ENABLE_BLIND_SPOT),
                CONF_ENABLE_SUN_TRACKING: self.config.get(
                    CONF_ENABLE_SUN_TRACKING, True
                ),
                CONF_MIN_ELEVATION: self.config.get(CONF_MIN_ELEVATION, None),
                CONF_MAX_ELEVATION: self.config.get(CONF_MAX_ELEVATION, None),
                CONF_TRANSPARENT_BLIND: self.config.get(CONF_TRANSPARENT_BLIND, False),
                CONF_WINTER_CLOSE_INSULATION: self.config.get(
                    CONF_WINTER_CLOSE_INSULATION, False
                ),
                CONF_INTERP: self.config.get(CONF_INTERP),
                CONF_INTERP_START: self.config.get(CONF_INTERP_START, None),
                CONF_INTERP_END: self.config.get(CONF_INTERP_END, None),
                CONF_INTERP_LIST: self.config.get(CONF_INTERP_LIST, []),
                CONF_INTERP_LIST_NEW: self.config.get(CONF_INTERP_LIST_NEW, []),
                CONF_LUX_ENTITY: self.config.get(CONF_LUX_ENTITY),
                CONF_LUX_THRESHOLD: self.config.get(CONF_LUX_THRESHOLD),
                CONF_IRRADIANCE_ENTITY: self.config.get(CONF_IRRADIANCE_ENTITY),
                CONF_IRRADIANCE_THRESHOLD: self.config.get(CONF_IRRADIANCE_THRESHOLD),
                CONF_CLOUD_COVERAGE_ENTITY: self.config.get(CONF_CLOUD_COVERAGE_ENTITY),
                CONF_CLOUD_COVERAGE_THRESHOLD: self.config.get(
                    CONF_CLOUD_COVERAGE_THRESHOLD
                ),
                CONF_OUTSIDE_THRESHOLD: self.config.get(CONF_OUTSIDE_THRESHOLD),
                CONF_DEVICE_ID: self.config.get(CONF_DEVICE_ID),
                CONF_RETURN_SUNSET: self.config.get(CONF_RETURN_SUNSET, False),
                CONF_CLOUD_SUPPRESSION: self.config.get(CONF_CLOUD_SUPPRESSION, False),
            },
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

            type_mapping = {
                "cover_blind": "Vertical",
                "cover_awning": "Horizontal",
                "cover_tilt": "Tilt",
            }

            return self.async_create_entry(  # type: ignore[return-value]
                title=f"{type_mapping.get(sensor_type, 'Cover')} {new_name}",
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
        if sensor_type == SensorType.TILT:
            cover_entity_selector = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    multiple=True,
                    filter=selector.EntityFilterSelectorConfig(
                        domain="cover",
                        supported_features=[
                            "cover.CoverEntityFeature.SET_TILT_POSITION"
                        ],
                    ),
                )
            )
        else:
            cover_entity_selector = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    multiple=True,
                    filter=selector.EntityFilterSelectorConfig(domain="cover"),
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
        self.sensor_type: SensorType = (  # type: ignore[misc]
            self.current_config.get(CONF_SENSOR_TYPE) or SensorType.BLIND
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
        if self.sensor_type == SensorType.BLIND and self.options.get(
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
                "force_override",  # Priority 100
                "weather_override",  # Priority 90
                "manual_override",  # Priority 80
                "custom_position",  # Priority 77
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
            description_placeholders={"instance_name": self.config_entry.title},
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
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()

        schema = _get_geometry_schema(self.sensor_type)
        return self.async_show_form(
            step_id="geometry",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
            description_placeholders={
                "geometry_wiki_link": _geometry_wiki_link(self.sensor_type)
            },
        )

    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (options)."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()

        schema = _build_glare_zones_schema(self.options)
        return self.async_show_form(
            step_id="glare_zones",
            data_schema=self.add_suggested_values_to_schema(schema, self.options),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Glare-Zones"
            },
        )

    async def async_step_sun_tracking(self, user_input: dict[str, Any] | None = None):
        """Adjust sun tracking parameters."""
        if user_input is not None:
            self.optional_entities([CONF_MIN_ELEVATION, CONF_MAX_ELEVATION], user_input)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
                and user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]
            ):
                schema = _get_sun_tracking_schema(self.sensor_type)
                return self.async_show_form(
                    step_id="sun_tracking",
                    data_schema=self.add_suggested_values_to_schema(
                        schema, user_input or self.options
                    ),
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Sun-Tracking"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        schema = _get_sun_tracking_schema(self.sensor_type)
        return self.async_show_form(
            step_id="sun_tracking",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Sun-Tracking"
            },
        )

    async def async_step_position(self, user_input: dict[str, Any] | None = None):
        """Adjust position settings."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="position",
            data_schema=self.add_suggested_values_to_schema(
                POSITION_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Position"
            },
        )

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            self.optional_entities([CONF_START_ENTITY, CONF_END_ENTITY], user_input)
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

    async def async_step_force_override(self, user_input: dict[str, Any] | None = None):
        """Manage force override sensors."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="force_override",
            data_schema=self.add_suggested_values_to_schema(
                FORCE_OVERRIDE_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Force-Override"
            },
        )

    async def async_step_custom_position(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage custom position sensors."""
        if user_input is not None:
            self.optional_entities(_CUSTOM_POSITION_OPTIONAL_KEYS, user_input)
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="custom_position",
            data_schema=self.add_suggested_values_to_schema(
                CUSTOM_POSITION_SCHEMA, user_input or self.options
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
        if user_input is not None:
            self.optional_entities(
                [
                    CONF_WEATHER_WIND_SPEED_SENSOR,
                    CONF_WEATHER_WIND_DIRECTION_SENSOR,
                    CONF_WEATHER_RAIN_SENSOR,
                    CONF_WEATHER_IS_RAINING_SENSOR,
                    CONF_WEATHER_IS_WINDY_SENSOR,
                ],
                user_input,
            )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="weather_override",
            data_schema=self.add_suggested_values_to_schema(
                WEATHER_OVERRIDE_SCHEMA, user_input or self.options
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
            "force_override_values": "Force Override — Thresholds & Position",
            "force_override_sensors": "Force Override — Trigger Sensors",
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
        edges = _get_azimuth_edges(self.options)
        schema = vol.Schema(
            {
                vol.Required(CONF_BLIND_SPOT_LEFT, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=0,
                        max=edges - 1,
                    )
                ),
                vol.Required(CONF_BLIND_SPOT_RIGHT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=1,
                        max=edges,
                    )
                ),
                vol.Optional(CONF_BLIND_SPOT_ELEVATION): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=90,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                    )
                ),
            }
        )
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
        if user_input is not None:
            self.optional_entities(
                [
                    CONF_WEATHER_ENTITY,
                    CONF_LUX_ENTITY,
                    CONF_IRRADIANCE_ENTITY,
                    CONF_CLOUD_COVERAGE_ENTITY,
                ],
                user_input,
            )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="light_cloud",
            data_schema=self.add_suggested_values_to_schema(
                LIGHT_CLOUD_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides"
            },
        )

    async def async_step_temperature_climate(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage temperature-based climate mode."""
        if user_input is not None:
            entities = [
                CONF_TEMP_ENTITY,
                CONF_OUTSIDETEMP_ENTITY,
                CONF_PRESENCE_ENTITY,
            ]
            self.optional_entities(entities, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="temperature_climate",
                    data_schema=self.add_suggested_values_to_schema(
                        TEMPERATURE_CLIMATE_SCHEMA, user_input or self.options
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
                TEMPERATURE_CLIMATE_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode"
            },
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options (legacy combined step, kept for backward compat)."""
        if user_input is not None:
            entities = [
                CONF_TEMP_ENTITY,
                CONF_OUTSIDETEMP_ENTITY,
                CONF_WEATHER_ENTITY,
                CONF_PRESENCE_ENTITY,
                CONF_LUX_ENTITY,
                CONF_IRRADIANCE_ENTITY,
            ]
            self.optional_entities(entities, user_input)
            if user_input.get(CONF_CLIMATE_MODE) and not user_input.get(
                CONF_TEMP_ENTITY
            ):
                return self.async_show_form(
                    step_id="climate",
                    data_schema=self.add_suggested_values_to_schema(
                        CLIMATE_SCHEMA, user_input or self.options
                    ),
                    errors={CONF_TEMP_ENTITY: "Required when climate mode is enabled"},
                    description_placeholders={
                        "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Climate"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="climate",
            data_schema=self.add_suggested_values_to_schema(
                CLIMATE_SCHEMA, user_input or self.options
            ),
            description_placeholders={
                "learn_more": "https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Climate"
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
        summary_text = _build_config_summary(
            self.options, self.sensor_type, self.hass, sun_times
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
