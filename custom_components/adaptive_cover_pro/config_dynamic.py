"""Dynamic config-flow section builders (sensor-unit / locale aware).

A handful of config sections cannot be generated from a static ``FieldSpec``
because their selector labels depend on a *bound sensor's*
``unit_of_measurement`` (weather thresholds, lux/irradiance, temperature) or on
the user's locale length unit (glare-zone coordinates). Those live here as
builder functions.

The field *metadata* (range, default, validator) for every key emitted here is
still declared once in :mod:`config_fields`; this module owns only the
selector construction. It imports the neutral selector primitives from
``config_fields`` plus ``unit_system`` — never ``config_flow`` or
``cover_types`` (those import this).
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .config_fields import (
    binary_on_selector,
    numeric_selector,
    presence_like_selector,
)
from .const import (
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_SUN_TRACKING,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_ELEVATION,
    CONF_MIN_ELEVATION,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
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
    CONF_WINTER_CLOSE_INSULATION,
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_GLARE_ZONE_Z,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MINIMIZE_MOVEMENTS,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WINDOW_AZIMUTH,
)
from .unit_system import length_default, length_selector

# Weather condition states offered by the weather-state multi-select. Kept in
# the documented HA order (sort=False preserves it).
_WEATHER_STATES = [
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
]


def _threshold_selector() -> selector.TemplateSelector:
    """Selector for a threshold that accepts a number *or* a Jinja2 template.

    Issue #577: these fields are rendered to a number once per cycle by
    ``templates.TemplateResolver``. ``TemplateSelector`` is the Jinja code
    editor — it gives entity autocomplete and syntax highlighting. It only
    renders a *string* value, so the config flow stringifies legacy numeric
    threshold values before handing them to ``add_suggested_values_to_schema``
    (see ``config_flow._stringify_templatable``). The unit lives in the field's
    translation description, since this selector carries no
    ``unit_of_measurement``.
    """
    return selector.TemplateSelector()


def sun_tracking_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Sun-tracking schema. ``hass=None`` → metric labels.

    Only ``CONF_DISTANCE`` is unit-dependent; every other field is angles or
    booleans.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_ENABLE_SUN_TRACKING, default=True
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_AZIMUTH, default=DEFAULT_WINDOW_AZIMUTH
            ): selector.NumberSelector(
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
            vol.Required(
                CONF_DISTANCE, default=length_default(0.5, hass)
            ): length_selector(
                hass,
                min_m=0.0,
                max_m=50,
                metric_step=0.1,
            ),
            vol.Optional(
                CONF_ENABLE_BLIND_SPOT, default=False
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MINIMIZE_MOVEMENTS, default=DEFAULT_MINIMIZE_MOVEMENTS
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MAX_COVERAGE_STEPS, default=DEFAULT_MAX_COVERAGE_STEPS
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=10,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
        }
    )


def blind_spot_schema(options: dict | None = None) -> vol.Schema:
    """Blind-spot wedge schema. Left/right bounds derive from the FOV edges.

    ``edges = fov_left + fov_right`` (defaulting to 90+90) sets the maximum
    left/right azimuth offset, matching the legacy in-step construction.
    """
    opts = options or {}
    edges = int(opts.get(CONF_FOV_LEFT, 90)) + int(opts.get(CONF_FOV_RIGHT, 90))
    return vol.Schema(
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


def weather_override_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Weather-override schema. Wind/rain thresholds accept number or template."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_WEATHER_BYPASS_AUTO_CONTROL, default=True
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_WEATHER_WIND_SPEED_SENSOR, default=vol.UNDEFINED
            ): numeric_selector(),
            vol.Optional(
                CONF_WEATHER_WIND_DIRECTION_SENSOR, default=vol.UNDEFINED
            ): numeric_selector(),
            vol.Optional(
                CONF_WEATHER_RAIN_SENSOR, default=vol.UNDEFINED
            ): numeric_selector(),
            vol.Optional(
                CONF_WEATHER_IS_RAINING_SENSOR, default=vol.UNDEFINED
            ): binary_on_selector(),
            vol.Optional(
                CONF_WEATHER_IS_WINDY_SENSOR, default=vol.UNDEFINED
            ): binary_on_selector(),
            vol.Optional(CONF_WEATHER_SEVERE_SENSORS, default=[]): binary_on_selector(
                multiple=True
            ),
            vol.Optional(
                CONF_WEATHER_WIND_SPEED_THRESHOLD,
                default=str(DEFAULT_WEATHER_WIND_SPEED_THRESHOLD),
            ): _threshold_selector(),
            vol.Optional(
                CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
                default=str(DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE),
            ): _threshold_selector(),
            vol.Optional(
                CONF_WEATHER_RAIN_THRESHOLD,
                default=str(DEFAULT_WEATHER_RAIN_THRESHOLD),
            ): _threshold_selector(),
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


def light_cloud_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Light/cloud schema. Lux/irradiance thresholds accept number or template."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_CLOUD_SUPPRESSION, default=False
            ): selector.BooleanSelector(),
            vol.Optional(CONF_CLOUDY_POSITION): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="%",
                )
            ),
            vol.Optional(
                CONF_WEATHER_ENTITY, default=vol.UNDEFINED
            ): selector.EntitySelector(
                selector.EntityFilterSelectorConfig(domain="weather")
            ),
            vol.Optional(
                CONF_IS_SUNNY_SENSOR, default=vol.UNDEFINED
            ): binary_on_selector(),
            vol.Optional(CONF_LUX_ENTITY, default=vol.UNDEFINED): numeric_selector(
                device_class="illuminance"
            ),
            vol.Optional(
                CONF_IRRADIANCE_ENTITY, default=vol.UNDEFINED
            ): numeric_selector(device_class="irradiance"),
            vol.Optional(
                CONF_CLOUD_COVERAGE_ENTITY, default=vol.UNDEFINED
            ): numeric_selector(),
            vol.Optional(
                CONF_WEATHER_STATE, default=["sunny", "partlycloudy", "cloudy", "clear"]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    multiple=True,
                    sort=False,
                    options=list(_WEATHER_STATES),
                )
            ),
            vol.Optional(CONF_LUX_THRESHOLD, default="1000"): _threshold_selector(),
            vol.Optional(
                CONF_IRRADIANCE_THRESHOLD, default="300"
            ): _threshold_selector(),
            vol.Optional(
                CONF_CLOUD_COVERAGE_THRESHOLD,
                default=str(DEFAULT_CLOUD_COVERAGE_THRESHOLD),
            ): _threshold_selector(),
        }
    )


def temperature_climate_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Climate-temperature schema. Temp thresholds accept number or template."""
    return vol.Schema(
        {
            vol.Optional(CONF_CLIMATE_MODE, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_TEMP_ENTITY): selector.EntitySelector(
                selector.EntityFilterSelectorConfig(domain=["climate", "sensor"])
            ),
            vol.Optional(
                CONF_OUTSIDETEMP_ENTITY, default=vol.UNDEFINED
            ): numeric_selector(),
            vol.Optional(
                CONF_PRESENCE_ENTITY, default=vol.UNDEFINED
            ): presence_like_selector(),
            vol.Optional(CONF_TEMP_LOW, default="21"): _threshold_selector(),
            vol.Optional(CONF_TEMP_HIGH, default="25"): _threshold_selector(),
            vol.Optional(CONF_OUTSIDE_THRESHOLD, default="25"): _threshold_selector(),
            vol.Optional(
                CONF_TRANSPARENT_BLIND, default=False
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_WINTER_CLOSE_INSULATION, default=False
            ): selector.BooleanSelector(),
        }
    )


def glare_zones_schema(
    options: dict | None = None, hass: HomeAssistant | None = None
) -> vol.Schema:
    """Glare-zones schema: name + x/y/radius/z for 4 zone slots (locale-aware)."""
    opts = options or {}

    def _default(key: str, canonical_fallback: float) -> float:
        canonical = float(opts.get(key, canonical_fallback))
        return length_default(canonical, hass)

    schema_dict: dict = {}
    for i in range(1, 5):
        prefix = f"glare_zone_{i}"
        schema_dict[
            vol.Optional(f"{prefix}_name", default=opts.get(f"{prefix}_name", ""))
        ] = selector.TextSelector()
        schema_dict[
            vol.Optional(f"{prefix}_x", default=_default(f"{prefix}_x", 0.0))
        ] = length_selector(
            hass,
            min_m=-5.0,
            max_m=5.0,
            metric_step=0.05,
            mode=selector.NumberSelectorMode.SLIDER,
        )
        schema_dict[
            vol.Optional(f"{prefix}_y", default=_default(f"{prefix}_y", 1.0))
        ] = length_selector(
            hass,
            min_m=0.0,
            max_m=10.0,
            metric_step=0.05,
            mode=selector.NumberSelectorMode.SLIDER,
        )
        schema_dict[
            vol.Optional(f"{prefix}_radius", default=_default(f"{prefix}_radius", 0.3))
        ] = length_selector(
            hass,
            min_m=0.1,
            max_m=2.0,
            metric_step=0.05,
            mode=selector.NumberSelectorMode.SLIDER,
        )
        schema_dict[
            vol.Optional(
                f"{prefix}_z",
                default=_default(f"{prefix}_z", DEFAULT_GLARE_ZONE_Z),
            )
        ] = length_selector(
            hass,
            min_m=0.0,
            max_m=3.0,
            metric_step=0.05,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    return vol.Schema(schema_dict)


def glare_zone_length_keys() -> tuple[str, ...]:
    """Return the 16 metres-stored option keys for the 4 glare-zone slots."""
    return tuple(
        f"glare_zone_{i}_{axis}"
        for i in range(1, 5)
        for axis in ("x", "y", "radius", "z")
    )
