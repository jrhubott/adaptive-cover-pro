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

from collections.abc import Mapping

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .config_fields import (
    binary_on_selector,
    numeric_selector,
    presence_like_selector,
)
from .const import (
    BLIND_SPOT_ELEVATION_MODES,
    BLIND_SPOT_FORM_KEYS,
    BLIND_SPOT_SLOT_NUMBERS,
    BLIND_SPOT_SLOTS,
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_CLIMATE_TEMP_HOLD_TIME,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUD_SUPPRESSION_HOLD_TIME,
    CONF_CLOUDY_POSITION,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DAYTIME_GATE_TEMPLATE_MODE,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_SUN_TRACKING,
    CONF_EXTREME_HEAT_POSITION,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_RELEASE_THRESHOLD,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_IS_SUNNY_TEMPLATE_MODE,
    CONF_LUX_ENTITY,
    CONF_LUX_RELEASE_THRESHOLD,
    CONF_LUX_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MIN_ELEVATION,
    CONF_OUTSIDE_TEMP_SOURCE,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD_RELEASE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_PRESENCE_TEMPLATE_MODE,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_INVERSE_STATE,
    CONF_POSITION_TOLERANCE,
    CONF_RETURN_SUNSET,
    CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_TIME_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_EXTREME_HEAT,
    CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD,
    CONF_TEMP_HIGH,
    CONF_TEMP_HIGH_RELEASE_THRESHOLD,
    CONF_TEMP_LOW,
    CONF_TEMP_LOW_RELEASE_THRESHOLD,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_RAINING_TEMPLATE,
    CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
    CONF_WEATHER_OVERRIDE_MIN_MODE,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_SEVERE_TEMPLATE,
    CONF_WEATHER_SEVERE_TEMPLATE_MODE,
    CONF_WEATHER_STATE,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_TRACKING_SEASONS,
    CONF_WINTER_CLOSE_INSULATION,
    DEFAULT_BLIND_SPOT_ELEVATION_MODE,
    DEFAULT_CLIMATE_TEMP_HOLD_TIME,
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
    DEFAULT_AUTO_RESOLVE_TEMP_FROM_AREA,
    DEFAULT_ENABLE_POSITION_MATCHING,
    DEFAULT_GLARE_ZONE_Z,
    GLARE_ZONE_FORM_KEYS,
    GLARE_ZONE_SLOT_NUMBERS,
    GLARE_ZONE_SLOTS,
    DEFAULT_OUTSIDE_TEMP_SOURCE,
    DEFAULT_TRACKING_SEASONS,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    DEFAULT_WINDOW_AZIMUTH,
    OutsideTempSource,
    TemplateCombineMode,
    TrackingSeason,
    clamp_gamma_pair,
    resolve_fov_left,
    resolve_fov_right,
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


def _template_combine_mode_selector() -> selector.SelectSelector:
    """Return the shared OR/AND combine-mode selector (template condition fields).

    Single source of truth for the ``template_combine_mode`` SelectSelector
    used by ``_condition_template_schema`` and ``building_profile_sensors_schema``.
    """
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[m.value for m in TemplateCombineMode],
            mode=selector.SelectSelectorMode.LIST,
            translation_key="template_combine_mode",
        )
    )


def _condition_template_schema(template_key: str, mode_key: str) -> dict:
    """Build a schema fragment for a condition template + combine mode (#639).

    The single source for the is_sunny / presence / is-raining / is-windy
    template selectors: a ``TemplateSelector`` plus the shared OR/AND combine-mode
    ``SelectSelector`` (``template_combine_mode`` translation key), mirroring the
    custom-position / daytime-gate template UI.
    """
    return {
        vol.Optional(template_key): selector.TemplateSelector(),
        vol.Optional(
            mode_key, default=DEFAULT_TEMPLATE_COMBINE_MODE
        ): _template_combine_mode_selector(),
    }


def window_facing_schema(
    hass: HomeAssistant | None = None, *, include_distance: bool = True
) -> vol.Schema:
    """Per-window facing fields: azimuth + FOV left/right + shaded distance.

    Single definition of the four fields relocated from the sun-tracking step to
    the geometry step (#778), composed onto every cover type's geometry schema so
    they sit beside the window width/depth the FOV button derives from. Only
    ``CONF_DISTANCE`` is unit-dependent; azimuth and FOV are angles. ``min_m=0.0``
    keeps a flush shaded distance of 0 valid (#427).

    ``include_distance=False`` omits the ``CONF_DISTANCE`` marker for cover types
    whose engine never reads it (the tilt-only louvered roof, #830); the default
    keeps it so every existing caller is unchanged.
    """
    fields: dict = {
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
    }
    if include_distance:
        fields[vol.Required(CONF_DISTANCE, default=length_default(0.5, hass))] = (
            length_selector(
                hass,
                min_m=0.0,
                max_m=50,
                metric_step=0.1,
            )
        )
    return vol.Schema(fields)


def sun_tracking_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Sun-tracking (behavioural) schema. ``hass=None`` → metric labels.

    Purely behavioural sun-tracking settings: the master enable toggle, the
    min/max elevation limits, and the blind-spot enable. The per-window facing
    fields (azimuth, FOV, shaded distance) moved to the geometry step (#778) —
    see ``window_facing_schema``. ``hass`` is retained in the signature so the
    call sites stay symmetric with the other locale-aware builders even though
    no field here is unit-dependent any more.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_ENABLE_SUN_TRACKING, default=True
            ): selector.BooleanSelector(),
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
            vol.Optional(
                CONF_ENABLE_BLIND_SPOT, default=False
            ): selector.BooleanSelector(),
            # minimize_movements / max_coverage_steps moved to the L4 global
            # motion-constraints (automation) step — see config_flow.AUTOMATION_SCHEMA (#613).
        }
    )


def blind_spot_edges(options: dict | None = None) -> int:
    """Return the blind-spot azimuth span: ``fov_left + fov_right``.

    Each side defaults to ``DEFAULT_FOV_LEFT``/``DEFAULT_FOV_RIGHT`` when absent
    (None-tolerant via the shared resolvers).

    NOTE: production-dead as of the signed-gamma switch (issue #247) —
    ``blind_spot_schema`` and ``clamp_blind_spots_to_fov`` now derive their
    signed bounds from ``fov_left``/``fov_right`` directly (and the shared
    ``clamp_gamma_pair``), not from this sum. Retained only for external callers
    and the ``test_blind_spot_edges_*`` tests that document the span formula.
    """
    opts = options or {}
    return resolve_fov_left(opts) + resolve_fov_right(opts)


def clamp_blind_spots_to_fov(options: dict) -> dict:
    """Re-clamp stored signed-gamma blind-spot edges to the current FOV (issue #852/#247).

    Blind-spot edges are stored as signed gamma from the window normal
    (issue #247): ``left_gamma`` (upper edge) ∈ ``[-fov_right, fov_left]`` and
    ``right_gamma`` (negated lower edge) ∈ ``[-fov_left, fov_right]``. Nothing
    re-clamps them when ``fov_left``/``fov_right`` narrow on the geometry step,
    so an edge saved under a wider FOV can exceed the new span — silently
    disagreeing with the options-flow slider and mis-shaping the wedge at
    runtime (issue #852). The bounds here mirror ``blind_spot_schema`` exactly.

    Call this right after any options/config update that changes
    ``CONF_FOV_LEFT``/``CONF_FOV_RIGHT`` (the geometry-step save sites in
    ``config_flow.py``, plus the geometry sync-merge).

    Mutates *options* in place (and returns it) for every slot in
    ``BLIND_SPOT_SLOTS``. A slot key that is absent or explicitly ``None`` is
    left untouched — an unconfigured slot must stay inactive, never coerced
    into existence by the clamp. The legacy FOV-relative keys are
    migration-read-only and are NEVER touched here.

    The bound arithmetic + non-empty repair lives in the shared
    ``clamp_gamma_pair`` (single source shared with the migration), so a
    previously non-empty wedge (e.g. the default 1° sliver) can never be
    clamped into the empty-wedge lockout when the FOV narrows (issue #247).
    """
    fov_left = resolve_fov_left(options)
    fov_right = resolve_fov_right(options)
    for keys in BLIND_SPOT_SLOTS.values():
        left = options.get(keys["left_gamma"])
        right = options.get(keys["right_gamma"])
        if left is None and right is None:
            continue  # unconfigured slot — never coerce into existence
        new_left, new_right = clamp_gamma_pair(left, right, fov_left, fov_right)
        if left is not None:
            options[keys["left_gamma"]] = new_left
        if right is not None:
            options[keys["right_gamma"]] = new_right
    return options


def blind_spot_schema(options: dict | None = None) -> vol.Schema:
    """Blind-spot wedge schema for up to 3 slots — signed gamma (issue #247/#701).

    Edges are signed gamma from the window normal: the left (upper) edge slider
    spans ``[-fov_right, fov_left]`` and the right (negated lower) edge slider
    spans ``[-fov_left, fov_right]`` (each side defaulting to 90 when absent).
    ``clamp_blind_spots_to_fov`` (issue #852) clamps to the identical bounds so
    schema and clamp can never disagree. The wedge is
    ``-right_gamma <= gamma <= left_gamma``.

    Slot 1 keeps ``Required`` markers whose default is a harmless 1° sliver at
    the LEFT acceptance edge (``left=fov_left``, ``right=1-fov_left`` → wedge
    ``fov_left-1 <= gamma <= fov_left``): non-empty (``fov_left + (1-fov_left) =
    1 > 0``) so the empty-wedge gate never trips, yet it never swallows the
    window normal (gamma 0) the way the old ``0 / 1`` default did — passing the
    step without touching a slider leaves direct sun at transit unblocked
    (issue #247, finding 6). Slots 2/3's left marker also gets ``default=0``
    — HA validates submitted form data against this schema before the step
    handler runs, and a frontend slider resting at 0 may never appear in the
    raw payload; without a default, ``vol.Optional`` drops the absent key
    entirely, silently losing a brand-new slot's left edge (issue #868). Slot
    2/3's right marker stays a default-less ``Optional`` so an unconfigured
    slot's right edge remains genuinely absent — ``_make_blind_spot``'s "both
    edges present" gate still keeps a truly untouched slot inactive.
    """
    opts = options or {}
    fov_left = resolve_fov_left(opts)
    fov_right = resolve_fov_right(opts)

    schema: dict = {}
    for n in BLIND_SPOT_SLOT_NUMBERS:
        schema.update(
            _blind_spot_slot_fields(n, BLIND_SPOT_SLOTS[n], fov_left, fov_right)
        )
    return vol.Schema(schema)


def _blind_spot_gamma_slider(min_v: int, max_v: int, *, step: int | None = None):
    """Signed-gamma degree slider for a blind-spot edge."""
    cfg: dict = {
        "mode": selector.NumberSelectorMode.SLIDER,
        "unit_of_measurement": "°",
        "min": min_v,
        "max": max_v,
    }
    if step is not None:
        cfg["step"] = step
    return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))


def _blind_spot_slot_fields(
    n: int, keys: Mapping[str, str], fov_left: int, fov_right: int
) -> dict:
    """Build one blind-spot slot's schema markers, keyed by *keys*.

    ``keys`` is a ``BLIND_SPOT_SLOTS[n]`` mapping for the multi-slot form, or
    ``BLIND_SPOT_FORM_KEYS`` for the generic single-slot page (issue #945). The
    *slot number* — not the keys — selects the marker semantics, so slot 1 keeps
    its legacy ``Required`` 1° sliver and slots 2/3 stay ``Optional`` regardless
    of which key set renders them.
    """
    schema: dict = {}
    if n == 1:
        # Harmless 1° sliver at the left acceptance edge — non-empty but
        # never blocks the window normal (issue #247, finding 6).
        left_marker = vol.Required(keys["left_gamma"], default=fov_left)
        right_marker = vol.Required(keys["right_gamma"], default=1 - fov_left)
    else:
        left_marker = vol.Optional(keys["left_gamma"], default=0)
        right_marker = vol.Optional(keys["right_gamma"])
    # left (upper) edge ∈ [-fov_right, fov_left]; right (negated lower)
    # edge ∈ [-fov_left, fov_right].
    schema[left_marker] = _blind_spot_gamma_slider(-fov_right, fov_left)
    schema[right_marker] = _blind_spot_gamma_slider(-fov_left, fov_right)
    schema[vol.Optional(keys["elevation"])] = _blind_spot_gamma_slider(0, 90, step=1)
    # Per-slot below/above elevation mode (issue #702). Defaults to "below"
    # so an unconfigured slot keeps today's "blocks low sun" behavior.
    schema[
        vol.Optional(keys["elevation_mode"], default=DEFAULT_BLIND_SPOT_ELEVATION_MODE)
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(BLIND_SPOT_ELEVATION_MODES),
            mode=selector.SelectSelectorMode.LIST,
            translation_key="blind_spot_elevation_mode",
        )
    )
    return schema


def blind_spot_slot_schema(slot_n: int, options: dict | None = None) -> vol.Schema:
    """Single-slot blind-spot schema for the per-slot options page (issue #945).

    Renders exactly slot *slot_n* under the generic ``BLIND_SPOT_FORM_KEYS`` so
    the translation block is authored once. Slot-1 semantics (``Required`` + the
    #247 sliver default) and the FOV-tracking slider bounds are preserved.
    """
    opts = options or {}
    fov_left = resolve_fov_left(opts)
    fov_right = resolve_fov_right(opts)
    return vol.Schema(
        _blind_spot_slot_fields(slot_n, BLIND_SPOT_FORM_KEYS, fov_left, fov_right)
    )


def weather_override_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Weather-override schema. Wind/rain thresholds accept number or template.

    The wind/rain/severe retraction sensor pickers are shown unconditionally for
    every cover type, alongside the thresholds/position/timeout fields. Linked
    covers also show the profile-owned pickers (pre-filled with the inherited
    value) under the inherit/override model — changing one records a local override.
    """
    schema: dict = {
        # Master on/off toggle for the whole feature (issue #719). New covers
        # start OFF (the one allowed static literal — selector default
        # convention, matching the other bool toggles); pre-existing covers are
        # migrated to ON via async_migrate_entry (v3.5 → v3.6).
        vol.Optional(CONF_WEATHER_ENABLED, default=False): selector.BooleanSelector(),
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
        **_condition_template_schema(
            CONF_WEATHER_IS_RAINING_TEMPLATE,
            CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
        ),
        **_condition_template_schema(
            CONF_WEATHER_IS_WINDY_TEMPLATE,
            CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
        ),
        **_condition_template_schema(
            CONF_WEATHER_SEVERE_TEMPLATE,
            CONF_WEATHER_SEVERE_TEMPLATE_MODE,
        ),
        vol.Optional(CONF_WEATHER_SEVERE_SENSORS, default=[]): binary_on_selector(
            multiple=True
        ),
    }
    schema.update(
        {
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
    return vol.Schema(schema)


def light_cloud_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Light/cloud schema. Lux/irradiance thresholds accept number or template."""
    schema: dict = {
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
        vol.Optional(
            CONF_WEATHER_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain="weather")
        ),
        vol.Optional(CONF_IS_SUNNY_SENSOR, default=vol.UNDEFINED): binary_on_selector(),
        **_condition_template_schema(
            CONF_IS_SUNNY_TEMPLATE, CONF_IS_SUNNY_TEMPLATE_MODE
        ),
        vol.Optional(CONF_LUX_ENTITY, default=vol.UNDEFINED): numeric_selector(
            device_class="illuminance"
        ),
        vol.Optional(CONF_IRRADIANCE_ENTITY, default=vol.UNDEFINED): numeric_selector(
            device_class="irradiance"
        ),
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
        vol.Optional(CONF_IRRADIANCE_THRESHOLD, default="300"): _threshold_selector(),
        vol.Optional(
            CONF_CLOUD_COVERAGE_THRESHOLD,
            default=str(DEFAULT_CLOUD_COVERAGE_THRESHOLD),
        ): _threshold_selector(),
        # Smoothing controls (issue #864). Optional per-trigger hysteresis
        # release edges (blank = off) accept a number or template like the
        # activate thresholds above; the symmetric hold-time debounces the
        # aggregate decision.
        vol.Optional(CONF_LUX_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(CONF_IRRADIANCE_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(
            CONF_CLOUD_SUPPRESSION_HOLD_TIME,
            default=DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=30,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="s",
            )
        ),
    }
    return vol.Schema(schema)


def building_profile_sensors_schema() -> vol.Schema:
    """Sensor-only schema for a Building Profile entry.

    Renders exactly the ``BUILDING_PROFILE_SENSOR_KEYS`` pickers — no
    thresholds, geometry, or cover selection. Reuses the same selector
    primitives as the weather-override / light-cloud / climate / behavior
    steps so the profile collects the building-level sensor IDs once and copies
    them into each linked cover.
    """
    selectors: dict = {
        # Light & cloud sensors
        CONF_WEATHER_ENTITY: selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain="weather")
        ),
        CONF_IS_SUNNY_SENSOR: binary_on_selector(),
        CONF_IS_SUNNY_TEMPLATE: selector.TemplateSelector(),
        CONF_IS_SUNNY_TEMPLATE_MODE: _template_combine_mode_selector(),
        CONF_LUX_ENTITY: numeric_selector(device_class="illuminance"),
        CONF_IRRADIANCE_ENTITY: numeric_selector(device_class="irradiance"),
        CONF_CLOUD_COVERAGE_ENTITY: numeric_selector(),
        # Weather-override retraction sensors
        CONF_WEATHER_WIND_SPEED_SENSOR: numeric_selector(),
        CONF_WEATHER_WIND_DIRECTION_SENSOR: numeric_selector(),
        CONF_WEATHER_RAIN_SENSOR: numeric_selector(),
        CONF_WEATHER_IS_RAINING_SENSOR: binary_on_selector(),
        CONF_WEATHER_IS_RAINING_TEMPLATE: selector.TemplateSelector(),
        CONF_WEATHER_IS_RAINING_TEMPLATE_MODE: _template_combine_mode_selector(),
        CONF_WEATHER_IS_WINDY_SENSOR: binary_on_selector(),
        CONF_WEATHER_IS_WINDY_TEMPLATE: selector.TemplateSelector(),
        CONF_WEATHER_IS_WINDY_TEMPLATE_MODE: _template_combine_mode_selector(),
        CONF_WEATHER_SEVERE_TEMPLATE: selector.TemplateSelector(),
        CONF_WEATHER_SEVERE_TEMPLATE_MODE: _template_combine_mode_selector(),
        CONF_WEATHER_SEVERE_SENSORS: binary_on_selector(multiple=True),
        # Outside temperature
        CONF_OUTSIDETEMP_ENTITY: numeric_selector(),
        # Daytime gate
        CONF_DAYTIME_GATE_SENSORS: binary_on_selector(multiple=True),
        CONF_DAYTIME_GATE_TEMPLATE: selector.TemplateSelector(),
        CONF_DAYTIME_GATE_TEMPLATE_MODE: _template_combine_mode_selector(),
        # Sunrise / sunset time entities (offsets stay per-cover)
        CONF_SUNSET_TIME_ENTITY: selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        CONF_SUNRISE_TIME_ENTITY: selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
    }
    return vol.Schema(
        {
            vol.Optional(key): sel
            for key, sel in selectors.items()
            if key in BUILDING_PROFILE_SENSOR_KEYS
        }
    )


def temperature_climate_schema(
    hass: HomeAssistant | None = None, options: dict | None = None
) -> vol.Schema:
    """Climate-temperature schema. Temp thresholds accept number or template."""
    schema: dict = {
        vol.Optional(CONF_CLIMATE_MODE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_TEMP_ENTITY): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["climate", "sensor"])
        ),
        vol.Optional(
            CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
            default=DEFAULT_AUTO_RESOLVE_TEMP_FROM_AREA,
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_OUTSIDETEMP_ENTITY, default=vol.UNDEFINED
        ): numeric_selector(),
        # Outdoor-temp source (issue #547): live (default), forecast daily-max,
        # or the max of the two. The forecast is fetched from the configured
        # weather entity — no separate picker.
        vol.Optional(
            CONF_OUTSIDE_TEMP_SOURCE, default=DEFAULT_OUTSIDE_TEMP_SOURCE
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[m.value for m in OutsideTempSource],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="outside_temp_source",
            )
        ),
        vol.Optional(
            CONF_PRESENCE_ENTITY, default=vol.UNDEFINED
        ): presence_like_selector(),
        **_condition_template_schema(
            CONF_PRESENCE_TEMPLATE, CONF_PRESENCE_TEMPLATE_MODE
        ),
        vol.Optional(CONF_TEMP_LOW, default="21"): _threshold_selector(),
        vol.Optional(CONF_TEMP_HIGH, default="25"): _threshold_selector(),
        vol.Optional(CONF_OUTSIDE_THRESHOLD, default="25"): _threshold_selector(),
        vol.Optional(CONF_TRANSPARENT_BLIND, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_WINTER_CLOSE_INSULATION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR, default=False
        ): selector.BooleanSelector(),
        # Extreme-heat mode (issue #766): a number-or-template threshold with no
        # default (blank = feature off) plus a clearable hold position.
        vol.Optional(CONF_TEMP_EXTREME_HEAT): _threshold_selector(),
        vol.Optional(CONF_EXTREME_HEAT_POSITION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_TRACKING_SEASONS, default=DEFAULT_TRACKING_SEASONS
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[s.value for s in TrackingSeason],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key="tracking_seasons",
            )
        ),
        # Temperature smoothing controls (issue #917). Optional per-crossing
        # hysteresis release edges (blank = off) accept a number or template like
        # the activate thresholds; the hold-time debounces the aggregate season
        # decision. Mirrors the cloud smoothing schema.
        vol.Optional(CONF_TEMP_LOW_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(CONF_TEMP_HIGH_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(CONF_OUTSIDE_THRESHOLD_RELEASE): _threshold_selector(),
        vol.Optional(CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD): _threshold_selector(),
        vol.Optional(
            CONF_CLIMATE_TEMP_HOLD_TIME,
            default=DEFAULT_CLIMATE_TEMP_HOLD_TIME,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=3600,
                step=30,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="s",
            )
        ),
    }
    return vol.Schema(schema)


def behavior_schema(options: dict | None = None) -> vol.Schema:
    """Behavior schema (L2b: timing & thresholds).

    Converts the formerly static ``BEHAVIOR_SCHEMA`` in ``config_flow`` into a
    per-call builder. Profile-owned timing/gate fields
    (``CONF_SUNSET_TIME_ENTITY``, ``CONF_SUNRISE_TIME_ENTITY``,
    ``CONF_DAYTIME_GATE_SENSORS``, ``CONF_DAYTIME_GATE_TEMPLATE``,
    ``CONF_DAYTIME_GATE_TEMPLATE_MODE``) are rendered for linked covers too under
    the inherit/override model (pre-filled with the inherited value). Per-cover
    fields (``CONF_SUNSET_OFFSET``, ``CONF_SUNRISE_OFFSET``, ``CONF_INVERSE_STATE``,
    ``CONF_POSITION_TOLERANCE``, ``CONF_ENABLE_POSITION_MATCHING``) are always
    rendered.
    """
    schema: dict = {
        vol.Optional(CONF_SUNSET_TIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_SUNRISE_TIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
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
        vol.Optional(CONF_DAYTIME_GATE_SENSORS, default=[]): binary_on_selector(
            multiple=True
        ),
        vol.Optional(CONF_DAYTIME_GATE_TEMPLATE): selector.TemplateSelector(),
        vol.Optional(
            CONF_DAYTIME_GATE_TEMPLATE_MODE, default=DEFAULT_TEMPLATE_COMBINE_MODE
        ): _template_combine_mode_selector(),
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
    }
    return vol.Schema(schema)


def glare_zones_schema(
    options: dict | None = None, hass: HomeAssistant | None = None
) -> vol.Schema:
    """Glare-zones schema: name + x/y/radius/z for 4 zone slots (locale-aware)."""
    opts = options or {}
    schema_dict: dict = {}
    for n in GLARE_ZONE_SLOT_NUMBERS:
        schema_dict.update(_glare_zone_slot_fields(GLARE_ZONE_SLOTS[n], opts, hass))
    return vol.Schema(schema_dict)


def _glare_zone_slot_fields(
    keys: Mapping[str, str], opts: dict, hass: HomeAssistant | None
) -> dict:
    """Build one glare-zone slot's schema markers, keyed by *keys*.

    ``keys`` is a ``GLARE_ZONE_SLOTS[n]`` mapping for the 4-zone form, or
    ``GLARE_ZONE_FORM_KEYS`` for the generic single-slot page (issue #945).
    Defaults are seeded (locale-aware) from ``opts`` under the *same* key set,
    so the single-slot caller passes a slot-remapped ``opts``.
    """

    def _default(sub: str, canonical_fallback: float) -> float:
        canonical = float(opts.get(keys[sub], canonical_fallback))
        return length_default(canonical, hass)

    schema_dict: dict = {}
    schema_dict[vol.Optional(keys["name"], default=opts.get(keys["name"], ""))] = (
        selector.TextSelector()
    )
    schema_dict[vol.Optional(keys["x"], default=_default("x", 0.0))] = length_selector(
        hass,
        min_m=-5.0,
        max_m=5.0,
        metric_step=0.05,
        mode=selector.NumberSelectorMode.SLIDER,
    )
    schema_dict[vol.Optional(keys["y"], default=_default("y", 1.0))] = length_selector(
        hass,
        min_m=0.0,
        max_m=10.0,
        metric_step=0.05,
        mode=selector.NumberSelectorMode.SLIDER,
    )
    schema_dict[vol.Optional(keys["radius"], default=_default("radius", 0.3))] = (
        length_selector(
            hass,
            min_m=0.1,
            max_m=2.0,
            metric_step=0.05,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )
    schema_dict[
        vol.Optional(keys["z"], default=_default("z", DEFAULT_GLARE_ZONE_Z))
    ] = length_selector(
        hass,
        min_m=0.0,
        max_m=3.0,
        metric_step=0.05,
        mode=selector.NumberSelectorMode.SLIDER,
    )
    return schema_dict


def glare_zone_slot_schema(
    slot_n: int, options: dict | None = None, hass: HomeAssistant | None = None
) -> vol.Schema:
    """Single-slot glare-zone schema for the per-slot options page (issue #945).

    Renders slot *slot_n* under the generic ``GLARE_ZONE_FORM_KEYS`` (one
    translation block). Defaults are seeded from the slot's stored metres values
    remapped onto the generic keys, so the baked slider defaults are correct even
    before ``add_suggested_values_to_schema`` overlays locale-converted values.
    """
    stored = options or {}
    slot_keys = GLARE_ZONE_SLOTS[slot_n]
    remapped = {
        GLARE_ZONE_FORM_KEYS[sub]: stored[wire]
        for sub, wire in slot_keys.items()
        if wire in stored
    }
    return vol.Schema(_glare_zone_slot_fields(GLARE_ZONE_FORM_KEYS, remapped, hass))


def glare_zone_length_keys() -> tuple[str, ...]:
    """Return the 16 metres-stored option keys for the 4 glare-zone slots."""
    return tuple(
        f"glare_zone_{i}_{axis}"
        for i in GLARE_ZONE_SLOT_NUMBERS
        for axis in ("x", "y", "radius", "z")
    )
