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
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
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
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
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
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DOMAIN,
    SensorType,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPE_MENU = [SensorType.BLIND, SensorType.AWNING, SensorType.TILT]


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
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_DISTANCE, default=0.5): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=5,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_DEPTH, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=0.5,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_SILL_HEIGHT, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=3.0,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
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
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_DISTANCE, default=0.5): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=5,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
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
        vol.Required(CONF_SUNSET_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="minutes"
            )
        ),
        vol.Required(CONF_SUNRISE_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="minutes"
            )
        ),
        vol.Optional(CONF_INVERSE_STATE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_INTERP, default=False): selector.BooleanSelector(),
    }
)

AUTOMATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DELTA_POSITION, default=1): selector.NumberSelector(
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
        vol.Optional(CONF_OPEN_CLOSE_THRESHOLD, default=50): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
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
    }
)

MOTION_OVERRIDES_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FORCE_OVERRIDE_SENSORS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor"],
                multiple=True,
            )
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
        vol.Optional(CONF_MOTION_SENSORS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor"],
                multiple=True,
                device_class=["motion", "occupancy"],
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
    }
)

WEATHER_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_WEATHER_WIND_SPEED_SENSOR, default=vol.UNDEFINED
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"])),
        vol.Optional(
            CONF_WEATHER_WIND_DIRECTION_SENSOR, default=vol.UNDEFINED
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"])),
        vol.Optional(
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            default=DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=200,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="km/h",
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
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"])),
        vol.Optional(
            CONF_WEATHER_RAIN_THRESHOLD, default=DEFAULT_WEATHER_RAIN_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=0.5,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="mm/h",
            )
        ),
        vol.Optional(
            CONF_WEATHER_IS_RAINING_SENSOR, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["binary_sensor"])
        ),
        vol.Optional(
            CONF_WEATHER_IS_WINDY_SENSOR, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["binary_sensor"])
        ),
        vol.Optional(CONF_WEATHER_SEVERE_SENSORS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["binary_sensor"], multiple=True)
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

CLIMATE_SCHEMA = vol.Schema(
    {
        # --- Light & Weather (works without climate mode) ---
        vol.Optional(
            CONF_WEATHER_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain="weather")
        ),
        vol.Optional(CONF_LUX_ENTITY, default=vol.UNDEFINED): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["sensor"], device_class="illuminance"
            )
        ),
        vol.Optional(CONF_LUX_THRESHOLD, default=1000): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="lux"
            )
        ),
        vol.Optional(
            CONF_IRRADIANCE_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["sensor"], device_class="irradiance"
            )
        ),
        vol.Optional(CONF_IRRADIANCE_THRESHOLD, default=300): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W/m²"
            )
        ),
        vol.Optional(
            CONF_CLOUD_COVERAGE_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["sensor"])
        ),
        vol.Optional(
            CONF_CLOUD_COVERAGE_THRESHOLD, default=DEFAULT_CLOUD_COVERAGE_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"
            )
        ),
        vol.Optional(CONF_CLOUD_SUPPRESSION, default=False): selector.BooleanSelector(),
        # --- Climate Mode (temperature-based control) ---
        vol.Optional(CONF_CLIMATE_MODE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_TEMP_ENTITY): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["climate", "sensor"])
        ),
        vol.Required(CONF_TEMP_LOW, default=21): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=86,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_TEMP_HIGH, default=25): selector.NumberSelector(
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
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["sensor"])
        ),
        vol.Optional(CONF_OUTSIDE_THRESHOLD, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(
            CONF_PRESENCE_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["device_tracker", "zone", "binary_sensor", "input_boolean"]
            )
        ),
        vol.Optional(CONF_TRANSPARENT_BLIND, default=False): selector.BooleanSelector(),
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


def _build_config_summary(config: dict, sensor_type: str | None) -> str:  # noqa: C901, PLR0912, PLR0915
    """Build a narrative summary of the current configuration.

    Produces four sections:
      1. Your Cover  — what is controlled and physical setup
      2. How It Decides — plain-English explanation of the active decision chain
      3. Position Limits — compact one-liner for range/default/flags
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
    has_weather = any([
        config.get(CONF_WEATHER_WIND_SPEED_SENSOR),
        config.get(CONF_WEATHER_RAIN_SENSOR),
        config.get(CONF_WEATHER_IS_RAINING_SENSOR),
        config.get(CONF_WEATHER_IS_WINDY_SENSOR),
        bool(config.get(CONF_WEATHER_SEVERE_SENSORS)),
    ])
    has_motion = bool(config.get(CONF_MOTION_SENSORS))
    has_cloud = bool(config.get(CONF_CLOUD_SUPPRESSION))
    has_climate = bool(config.get(CONF_CLIMATE_MODE))
    has_glare = bool(config.get(CONF_ENABLE_GLARE_ZONES)) and sensor_type == SensorType.BLIND

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
    # Section 2: How It Decides
    # =========================================================================
    lines.append("")
    lines.append("**How It Decides**")

    # Solar tracking (always present, establishes the baseline)
    azimuth = config.get(CONF_AZIMUTH)
    fov_l = config.get(CONF_FOV_LEFT)
    fov_r = config.get(CONF_FOV_RIGHT)
    min_elev = config.get(CONF_MIN_ELEVATION)
    max_elev = config.get(CONF_MAX_ELEVATION)
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
    lines.append(f"☀️ Tracks the sun{sun_desc} and calculates position to block direct sunlight.")

    # Timing window
    start_time = config.get(CONF_START_TIME)
    start_entity = config.get(CONF_START_ENTITY)
    end_time = config.get(CONF_END_TIME)
    end_entity = config.get(CONF_END_ENTITY)
    sunset_pos = config.get(CONF_SUNSET_POS)
    timing_parts = []
    if start_time:
        timing_parts.append(f"from {start_time}")
    elif start_entity:
        timing_parts.append(f"from {start_entity}")
    if end_time:
        timing_parts.append(f"until {end_time}")
    elif end_entity:
        timing_parts.append(f"until {end_entity}")
    if timing_parts or sunset_pos is not None:
        timing_str = " ".join(timing_parts) if timing_parts else "Active during daylight"
        if sunset_pos is not None:
            timing_str += f". After end time/sunset → {sunset_pos}%"
        lines.append(f"🕒 {timing_str}.")

    # Blind spot
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
        lines.append(f"🟥 Blind spot: ignores sun at {bs_str} (e.g. tree or roof overhang).")

    # Glare zones (vertical only)
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
            gz_parts.append(f"{width}cm window")
        gz_str = f" ({', '.join(gz_parts)})" if gz_parts else ""
        lines.append(f"🔆 Glare zones: lowers blind to protect floor areas from direct sun{gz_str}.")

    # Climate / cloud suppression (before force/weather so users understand layering)
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
            cl_parts.append(f"outside: {outside}")
        weather_ent = config.get(CONF_WEATHER_ENTITY)
        if weather_ent:
            cl_parts.append(f"weather: {weather_ent}")
        presence = config.get(CONF_PRESENCE_ENTITY)
        if presence:
            cl_parts.append(f"presence: {presence}")
        cl_str = f" ({', '.join(cl_parts)})" if cl_parts else ""
        lines.append(f"🌡️ Climate mode: adjusts strategy for heating/cooling{cl_str}.")

    if has_cloud:
        cloud_parts = []
        if (v := config.get(CONF_LUX_ENTITY)):
            t = config.get(CONF_LUX_THRESHOLD)
            cloud_parts.append(f"lux < {t} lx" if t is not None else f"lux ({v})")
        if (v := config.get(CONF_IRRADIANCE_ENTITY)):
            t = config.get(CONF_IRRADIANCE_THRESHOLD)
            cloud_parts.append(f"irradiance < {t} W/m²" if t is not None else f"irradiance ({v})")
        if (v := config.get(CONF_CLOUD_COVERAGE_ENTITY)):
            t = config.get(CONF_CLOUD_COVERAGE_THRESHOLD)
            cloud_parts.append(f"cloud > {t}%" if t is not None else f"cloud ({v})")
        cloud_str = f" when {', '.join(cloud_parts)}" if cloud_parts else ""
        lines.append(f"☁️ Cloud suppression: skips sun tracking{cloud_str} → default ({default_pos}%).")
    elif any([
        config.get(CONF_LUX_ENTITY),
        config.get(CONF_IRRADIANCE_ENTITY),
        config.get(CONF_CLOUD_COVERAGE_ENTITY),
    ]):
        # Sensors configured but suppression toggle off — mention them as informational
        sensor_names = []
        if config.get(CONF_LUX_ENTITY):
            sensor_names.append("lux")
        if config.get(CONF_IRRADIANCE_ENTITY):
            sensor_names.append("irradiance")
        if config.get(CONF_CLOUD_COVERAGE_ENTITY):
            sensor_names.append("cloud coverage")
        lines.append(f"📊 Light sensors configured ({', '.join(sensor_names)}) but cloud suppression is off.")

    # Manual override
    mo_parts = []
    if manual_dur is not None:
        mo_parts.append(f"pauses for {manual_dur} min")
    threshold = config.get(CONF_MANUAL_THRESHOLD)
    if threshold is not None:
        mo_parts.append(f"threshold {threshold}%")
    if config.get(CONF_MANUAL_OVERRIDE_RESET):
        mo_parts.append("resets on next move")
    mo_str = f" ({', '.join(mo_parts)})" if mo_parts else ""
    lines.append(f"✋ Manual override: pauses automatic control when you move the cover{mo_str}.")

    # Motion timeout
    if has_motion:
        n = len(config.get(CONF_MOTION_SENSORS) or [])
        sensor_word = "sensor" if n == 1 else "sensors"
        lines.append(
            f"🚶 Motion-based: if no occupancy for {motion_timeout}s "
            f"({n} {sensor_word}) → covers return to default ({default_pos}%)."
        )

    # Weather safety override
    if has_weather:
        wx_parts = []
        wind_sensor = config.get(CONF_WEATHER_WIND_SPEED_SENSOR)
        wind_thresh = config.get(CONF_WEATHER_WIND_SPEED_THRESHOLD)
        rain_sensor = config.get(CONF_WEATHER_RAIN_SENSOR)
        rain_thresh = config.get(CONF_WEATHER_RAIN_THRESHOLD)
        is_rain = config.get(CONF_WEATHER_IS_RAINING_SENSOR)
        is_wind = config.get(CONF_WEATHER_IS_WINDY_SENSOR)
        severe = config.get(CONF_WEATHER_SEVERE_SENSORS) or []
        if wind_sensor and wind_thresh is not None:
            wx_parts.append(f"wind > {wind_thresh} km/h")
        if rain_sensor and rain_thresh is not None:
            wx_parts.append(f"rain > {rain_thresh} mm/h")
        if is_rain:
            wx_parts.append("is-raining")
        if is_wind:
            wx_parts.append("is-windy")
        if severe:
            wx_parts.append(f"{len(severe)} severe weather sensor(s)")
        wx_condition = " or ".join(wx_parts) if wx_parts else "weather condition"
        wx_delay = config.get(CONF_WEATHER_TIMEOUT)
        delay_str = f" (waits {wx_delay}s after clearing)" if wx_delay else ""
        lines.append(
            f"🌧️ Weather safety: if {wx_condition} → covers retract to {weather_pos}%{delay_str}."
        )

    # Force override (hardest safety — listed last in narrative since it's a "trump card")
    if has_force:
        n = len(config.get(CONF_FORCE_OVERRIDE_SENSORS) or [])
        sensor_word = "sensor" if n == 1 else "sensors"
        lines.append(
            f"🔒 Force override: if any of {n} {sensor_word} is on → covers go to {force_pos}% "
            f"(overrides everything else)."
        )

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
        qualifier = ""
        if enable_min or enable_max:
            qualifier = " (during sun tracking only)"
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
    if config.get(CONF_INTERP):
        limit_parts.append("Interpolation on")
    if limit_parts:
        lines.append("")
        lines.append("**Position Limits**")
        lines.append(" · ".join(limit_parts))

    # =========================================================================
    # Section 4: Decision Priority (compact reference)
    # =========================================================================
    def _ch(active: bool, short: str, pri: int) -> str:
        mark = "✅" if active else "❌"
        return f"{mark}{short}({pri})"

    chain = [
        _ch(has_force, "Force", 100),
        _ch(has_weather, "Weather", 90),
        _ch(has_motion, "Motion", 80),
        _ch(True, "Manual", 70),
        _ch(has_cloud, "Cloud", 60),
        _ch(has_climate, "Climate", 50),
    ]
    if sensor_type == SensorType.BLIND or sensor_type is None:
        chain.append(_ch(has_glare, "Glare", 45))
    chain.extend([
        _ch(True, "Solar", 40),
        _ch(True, "Default", 0),
    ])

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
            CONF_DISTANCE,
            CONF_WINDOW_DEPTH,
            CONF_SILL_HEIGHT,
            CONF_LENGTH_AWNING,
            CONF_AWNING_ANGLE,
            CONF_TILT_DEPTH,
            CONF_TILT_DISTANCE,
            CONF_TILT_MODE,
        }
    ),
    "sun_tracking": frozenset(
        {
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_MIN_ELEVATION,
            CONF_MAX_ELEVATION,
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
            CONF_SUNSET_OFFSET,
            CONF_SUNRISE_OFFSET,
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
            CONF_OPEN_CLOSE_THRESHOLD,
            CONF_RETURN_SUNSET,
        }
    ),
    "manual_override": frozenset(
        {
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_MANUAL_OVERRIDE_RESET,
            CONF_MANUAL_THRESHOLD,
            CONF_MANUAL_IGNORE_INTERMEDIATE,
        }
    ),
    "motion_overrides": frozenset(
        {
            CONF_FORCE_OVERRIDE_SENSORS,
            CONF_FORCE_OVERRIDE_POSITION,
            CONF_MOTION_SENSORS,
            CONF_MOTION_TIMEOUT,
        }
    ),
    "weather_override": frozenset(
        {
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
            CONF_WEATHER_TIMEOUT,
        }
    ),
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
            CONF_CLIMATE_MODE,
            CONF_TEMP_ENTITY,
            CONF_TEMP_LOW,
            CONF_TEMP_HIGH,
            CONF_OUTSIDETEMP_ENTITY,
            CONF_OUTSIDE_THRESHOLD,
            CONF_PRESENCE_ENTITY,
            CONF_TRANSPARENT_BLIND,
        }
    ),
    "weather": frozenset(
        {
            CONF_WEATHER_STATE,
        }
    ),
}


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


def _build_cover_entity_schema(sensor_type: str) -> vol.Schema:
    """Build entity selector schema based on cover type."""
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
    return vol.Schema({vol.Optional(CONF_ENTITIES, default=[]): entity_selector})


def _get_geometry_schema(sensor_type: str) -> vol.Schema:
    """Return the geometry schema for the given sensor type."""
    if sensor_type == SensorType.BLIND:
        return GEOMETRY_VERTICAL_SCHEMA
    if sensor_type == SensorType.AWNING:
        return GEOMETRY_HORIZONTAL_SCHEMA
    if sensor_type == SensorType.TILT:
        return GEOMETRY_TILT_SCHEMA
    return GEOMETRY_VERTICAL_SCHEMA


def _build_glare_zones_schema(options: dict | None = None) -> vol.Schema:
    """Build the glare zones schema: enable toggle, window width, and 4 zone slots."""
    opts = options or {}
    schema_dict: dict = {
        vol.Optional(
            CONF_ENABLE_GLARE_ZONES, default=opts.get(CONF_ENABLE_GLARE_ZONES, False)
        ): (selector.BooleanSelector()),
        vol.Optional(CONF_WINDOW_WIDTH, default=opts.get(CONF_WINDOW_WIDTH, 100)): (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=500,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        ),
    }
    for i in range(1, 5):
        prefix = f"glare_zone_{i}"
        schema_dict[
            vol.Optional(f"{prefix}_name", default=opts.get(f"{prefix}_name", ""))
        ] = selector.TextSelector()
        schema_dict[vol.Optional(f"{prefix}_x", default=opts.get(f"{prefix}_x", 0))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-500,
                    max=500,
                    step=10,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        )
        schema_dict[
            vol.Optional(f"{prefix}_y", default=opts.get(f"{prefix}_y", 100))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1000,
                step=10,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        )
        schema_dict[
            vol.Optional(f"{prefix}_radius", default=opts.get(f"{prefix}_radius", 30))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=10,
                max=200,
                step=5,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        )
    return vol.Schema(schema_dict)


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle ConfigFlow."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__()
        self.type_blind: str | None = None
        self.config: dict[str, Any] = {}
        self.mode: str = "basic"
        self.selected_source_entry_id: str | None = None

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
            return await self.async_step_cover_entities()
        return self.async_show_form(
            step_id="create_new",
            data_schema=CONFIG_SCHEMA,
        )

    async def async_step_cover_entities(self, user_input: dict[str, Any] | None = None):
        """Select cover entities."""
        if user_input is not None:
            self.config.update(user_input)

            # Extract first cover entity's name to auto-populate device name
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

            # Check for device association
            entity_ids = self.config.get(CONF_ENTITIES, [])
            devices = await _get_devices_from_entities(self.hass, entity_ids)
            if devices:
                return await self.async_step_device_association()
            return await self.async_step_geometry()

        schema = _build_cover_entity_schema(self.type_blind)
        return self.async_show_form(step_id="cover_entities", data_schema=schema)

    async def async_step_device_association(
        self, user_input: dict[str, Any] | None = None
    ):
        """Show optional device association step."""
        _standalone_sentinel = "__standalone__"
        entity_ids = self.config.get(CONF_ENTITIES, [])
        devices = await _get_devices_from_entities(self.hass, entity_ids)

        if not devices:
            return await self.async_step_geometry()

        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE_ID, _standalone_sentinel)
            if device_id and device_id != _standalone_sentinel:
                self.config[CONF_DEVICE_ID] = device_id
            else:
                self.config.pop(CONF_DEVICE_ID, None)
            return await self.async_step_geometry()

        options_list = [
            {"value": _standalone_sentinel, "label": "None (standalone device)"}
        ]
        for device_id, device_name in devices.items():
            options_list.append({"value": device_id, "label": device_name})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_ID, default=_standalone_sentinel
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options_list,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="device_association", data_schema=schema)

    async def async_step_geometry(self, user_input: dict[str, Any] | None = None):
        """Configure cover geometry dimensions."""
        if user_input is not None:
            self.config.update(user_input)
            if self.type_blind == SensorType.BLIND:
                return await self.async_step_glare_zones()
            return await self.async_step_sun_tracking()

        schema = _get_geometry_schema(self.type_blind)
        return self.async_show_form(step_id="geometry", data_schema=schema)

    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (initial flow)."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_sun_tracking()

        schema = _build_glare_zones_schema(self.config)
        return self.async_show_form(step_id="glare_zones", data_schema=schema)

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
                    data_schema=SUN_TRACKING_SCHEMA,
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_position()
        return self.async_show_form(
            step_id="sun_tracking", data_schema=SUN_TRACKING_SCHEMA
        )

    async def async_step_position(self, user_input: dict[str, Any] | None = None):
        """Configure position settings."""
        if user_input is not None:
            self.config.update(user_input)
            if self.config.get(CONF_ENABLE_BLIND_SPOT):
                return await self.async_step_blind_spot()
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()
        return self.async_show_form(step_id="position", data_schema=POSITION_SCHEMA)

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
                )
            self.config.update(user_input)
            if self.config.get(CONF_INTERP):
                return await self.async_step_interp()
            return await self.async_step_automation()

        return self.async_show_form(step_id="blind_spot", data_schema=schema)

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
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Interpolation' list"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_automation()
        return self.async_show_form(step_id="interp", data_schema=INTERPOLATION_OPTIONS)

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            self.optional_entities([CONF_START_ENTITY, CONF_END_ENTITY], user_input)
            self.config.update(user_input)
            return await self.async_step_manual_override()
        return self.async_show_form(step_id="automation", data_schema=AUTOMATION_SCHEMA)

    async def async_step_manual_override(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure manual override settings."""
        if user_input is not None:
            self.optional_entities([CONF_MANUAL_THRESHOLD], user_input)
            self.config.update(user_input)
            return await self.async_step_motion_overrides()
        return self.async_show_form(
            step_id="manual_override", data_schema=MANUAL_OVERRIDE_SCHEMA
        )

    async def async_step_motion_overrides(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure motion and force override sensors."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_weather_override()
        return self.async_show_form(
            step_id="motion_overrides", data_schema=MOTION_OVERRIDES_SCHEMA
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
            return await self.async_step_climate()
        return self.async_show_form(
            step_id="weather_override", data_schema=WEATHER_OVERRIDE_SCHEMA
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options."""
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
                )
            self.config.update(user_input)
            if self.config.get(CONF_WEATHER_ENTITY):
                return await self.async_step_weather()
            return await self.async_step_summary()
        return self.async_show_form(step_id="climate", data_schema=CLIMATE_SCHEMA)

    async def async_step_weather(self, user_input: dict[str, Any] | None = None):
        """Manage weather conditions."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_summary()
        return self.async_show_form(step_id="weather", data_schema=WEATHER_OPTIONS)

    async def async_step_summary(self, user_input: dict[str, Any] | None = None):
        """Show a read-only summary of all collected configuration before creating the entry."""
        if user_input is not None:
            return await self.async_step_update()
        summary_text = _build_config_summary(self.config, self.type_blind)
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
                CONF_DELTA_POSITION: self.config.get(CONF_DELTA_POSITION),
                CONF_DELTA_TIME: self.config.get(CONF_DELTA_TIME),
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
                ),
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
                CONF_MIN_ELEVATION: self.config.get(CONF_MIN_ELEVATION, None),
                CONF_MAX_ELEVATION: self.config.get(CONF_MAX_ELEVATION, None),
                CONF_TRANSPARENT_BLIND: self.config.get(CONF_TRANSPARENT_BLIND, False),
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
        menu_options = [
            "cover_entities",
            "device",
            "geometry",
            "sun_tracking",
        ]
        if self.options.get(CONF_ENABLE_BLIND_SPOT):
            menu_options.append("blind_spot")
        if self.sensor_type == SensorType.BLIND:
            menu_options.append("glare_zones")
        menu_options.append("position")
        if self.options.get(CONF_INTERP):
            menu_options.append("interp")
        menu_options.extend(
            [
                "automation",
                "manual_override",
                "motion_overrides",
                "weather_override",
                "climate",
            ]
        )
        if self.options.get(CONF_WEATHER_ENTITY):
            menu_options.append("weather")
        menu_options.extend(
            [
                "summary",
                "sync",
                "done",
            ]
        )
        return self.async_show_menu(step_id="init", menu_options=menu_options)  # type: ignore[return-value]

    async def async_step_cover_entities(self, user_input: dict[str, Any] | None = None):
        """Adjust cover entities."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()

        schema = _build_cover_entity_schema(self.sensor_type)
        return self.async_show_form(
            step_id="cover_entities",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
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
                return self.async_show_form(
                    step_id="sun_tracking",
                    data_schema=self.add_suggested_values_to_schema(
                        SUN_TRACKING_SCHEMA, user_input or self.options
                    ),
                    errors={
                        CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="sun_tracking",
            data_schema=self.add_suggested_values_to_schema(
                SUN_TRACKING_SCHEMA, user_input or self.options
            ),
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
        )

    async def async_step_motion_overrides(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage motion and force override sensors."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="motion_overrides",
            data_schema=self.add_suggested_values_to_schema(
                MOTION_OVERRIDES_SCHEMA, user_input or self.options
            ),
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
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None):
        """Manage device association."""
        _standalone_sentinel = "__standalone__"
        entity_ids = self.options.get(CONF_ENTITIES, [])
        devices = await _get_devices_from_entities(self.hass, entity_ids)

        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE_ID, _standalone_sentinel)
            if device_id and device_id != _standalone_sentinel:
                self.options[CONF_DEVICE_ID] = device_id
            else:
                self.options.pop(CONF_DEVICE_ID, None)
            return await self.async_step_init()

        if not devices:
            # No devices available — clear any stale association and update immediately
            self.options.pop(CONF_DEVICE_ID, None)
            return await self.async_step_init()

        current_device = self.options.get(CONF_DEVICE_ID) or _standalone_sentinel
        options_list = [
            {"value": _standalone_sentinel, "label": "None (standalone device)"}
        ]
        for device_id, device_name in devices.items():
            options_list.append({"value": device_id, "label": device_name})

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options_list,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="device",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_DEVICE_ID: current_device}
            ),
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
            for cat, keys in SYNC_CATEGORIES.items()
            if any(k in self._config_entry.options for k in keys)
        ]

        if user_input is not None:
            targets = user_input.get("target_entries", [])
            if not targets:
                return self.async_abort(reason="no_targets_selected")  # type: ignore[return-value]
            selected = user_input.get("sync_categories", [])
            if not selected:
                return self.async_abort(reason="no_categories_selected")  # type: ignore[return-value]
            self.selected_sync_targets = targets
            self.selected_sync_categories = selected
            return await self.async_step_sync_confirm()

        return self.async_show_form(  # type: ignore[return-value]
            step_id="sync",
            data_schema=vol.Schema(
                {
                    vol.Required("target_entries"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {"value": e.entry_id, "label": e.title}
                                for e in other_entries
                            ],
                        )
                    ),
                    vol.Required(
                        "sync_categories", default=available
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
                return self.async_abort(reason="sync_complete")  # type: ignore[return-value]
            return self.async_abort(reason="user_cancelled")  # type: ignore[return-value]

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
            "interp": "Interpolation Values",
            "automation": "Schedule & Timing",
            "manual_override": "Manual Override",
            "motion_overrides": "Motion & Force Overrides",
            "weather_override": "Weather Override",
            "climate": "Climate",
            "weather": "Weather Conditions",
        }
        category_lines = [
            f"• {_category_labels.get(c, c)}" for c in self.selected_sync_categories
        ]

        return self.async_show_form(  # type: ignore[return-value]
            step_id="sync_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=True): selector.BooleanSelector()}
            ),
            description_placeholders={
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
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Interpolation' list"
                    },
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="interp",
            data_schema=self.add_suggested_values_to_schema(
                INTERPOLATION_OPTIONS, user_input or self.options
            ),
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
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="blind_spot",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options."""
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
                )
            self.options.update(user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id="climate",
            data_schema=self.add_suggested_values_to_schema(
                CLIMATE_SCHEMA, user_input or self.options
            ),
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
        )

    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show a read-only summary of the current configuration."""
        if user_input is not None:
            return await self.async_step_init()
        summary_text = _build_config_summary(self.options, self.sensor_type)
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": summary_text},
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
