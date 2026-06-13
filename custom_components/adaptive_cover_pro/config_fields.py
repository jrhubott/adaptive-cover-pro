"""Single-source field declarations for Adaptive Cover Pro configuration.

This module is the **one** place a config field is declared. From each
:class:`FieldSpec` the rest of the package derives, with no hand-maintained
parallel maps:

* the config-flow voluptuous marker + selector (``FieldSpec.to_marker``),
* the numeric bounds map ``OPTION_RANGES`` (``const`` re-exports it),
* the per-option default (``option_default`` — consumed by
  ``config_types.RuntimeConfig`` and the marker defaults),
* the validator kind (``services/options_service`` builds ``FIELD_VALIDATORS``
  from these),
* section membership (``COMMON_SECTIONS`` / ``section_keys``).

Design notes
------------
* **Static sections** (sun tracking, position, automation, manual override,
  force override, custom position, motion, interpolation, debug) are generated
  from ``FieldSpec`` directly.
* **Dynamic sections** (weather override, light/cloud, temperature climate,
  geometry, glare zones) build their selectors from a bound sensor's
  ``unit_of_measurement`` or the user's locale. Those keep dedicated builder
  functions (see ``config_dynamic.py`` / the cover-type policies) but still
  declare a ``FieldSpec`` here — with ``make_selector=None`` — so their bounds,
  defaults and validators stay single-sourced.

Dependency rule: this module imports only ``const`` (plain string/scalar
constants) and ``unit_system`` (neutral). It must never import ``config_flow``
or ``cover_types`` — those import *this*.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from . import const
from .const import (
    CONF_ARM_LENGTH,
    CONF_AWNING_ANGLE,
    CONF_AWNING_HOUSING_OFFSET,
    CONF_AWNING_MAX_ANGLE,
    CONF_AWNING_MIN_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEBUG_CATEGORIES,
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_DRY_RUN,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_MY_POSITION_ENTITIES,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_COMPUTE,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
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
    CONF_MAX_TILT,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_MIN_TILT,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_MOTION_MEDIA_PLAYERS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TEMPLATE,
    CONF_MOTION_TEMPLATE_MODE,
    CONF_MOTION_TIMEOUT,
    CONF_MOTION_TIMEOUT_MODE,
    CONF_MY_POSITION_VALUE,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_POSITION_TOLERANCE,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_SILL_HEIGHT,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TILT,
    CONF_SUNSET_TIME_ENTITY,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSIT_TIMEOUT,
    CONF_TRANSPARENT_BLIND,
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    CONF_VENETIAN_MODE,
    CONF_VENETIAN_POST_SETTLE_HOLD,
    CONF_VENETIAN_TILT_SKIP_ABOVE,
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
    CUSTOM_POSITION_SLOTS,
    DEBUG_CATEGORIES_ALL,
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_ENABLE_MY_POSITION_ENTITIES,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MINIMIZE_MOVEMENTS,
    DEFAULT_MOTION_TEMPLATE_MODE,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_MOTION_TIMEOUT_MODE,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WINDOW_AZIMUTH,
    MAX_DEBUG_EVENT_BUFFER_SIZE,
    MAX_TRANSIT_TIMEOUT,
    MIN_TRANSIT_TIMEOUT,
    MOTION_TIMEOUT_MODE_HOLD,
    MOTION_TIMEOUT_MODE_RETURN,
)

# =============================================================================
# Section identifiers
# =============================================================================
# Section names match the config-flow step ids and the options-menu keys. The
# order here is the canonical common-section order used by the options menu and
# the full setup flow (geometry / glare_zones are inserted per cover type).

SECTION_GEOMETRY = "geometry"
SECTION_SUN_TRACKING = "sun_tracking"
SECTION_POSITION = "position"
SECTION_INTERP = "interp"
SECTION_BLIND_SPOT = "blind_spot"
SECTION_GLARE_ZONES = "glare_zones"
SECTION_AUTOMATION = "automation"
SECTION_LIGHT_CLOUD = "light_cloud"
SECTION_TEMPERATURE_CLIMATE = "temperature_climate"
SECTION_FORCE_OVERRIDE = "force_override"
SECTION_WEATHER_OVERRIDE = "weather_override"
SECTION_MANUAL_OVERRIDE = "manual_override"
SECTION_CUSTOM_POSITION = "custom_position"
SECTION_MOTION_OVERRIDE = "motion_override"
SECTION_DEBUG = "debug"


# =============================================================================
# Selector primitives (relocated from config_flow; neutral, no flow imports)
# =============================================================================

_BINARY_ON_DOMAINS = ["binary_sensor", "input_boolean", "switch", "schedule"]
_PRESENCE_LIKE_DOMAINS = _BINARY_ON_DOMAINS + ["device_tracker", "person", "zone"]
_NUMERIC_DOMAINS = ["sensor", "input_number", "number"]


def binary_on_selector(*, multiple: bool = False) -> selector.EntitySelector:
    """Return a single or multi-pick selector for on/off entities."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_BINARY_ON_DOMAINS, multiple=multiple)
    )


def presence_like_selector(*, multiple: bool = False) -> selector.EntitySelector:
    """Return a selector for presence-shaped entities (motion, occupancy, presence)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_PRESENCE_LIKE_DOMAINS, multiple=multiple)
    )


def media_player_selector(*, multiple: bool = False) -> selector.EntitySelector:
    """Return a selector for media_player entities (occupancy via playback)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["media_player"], multiple=multiple)
    )


def numeric_selector(
    *, device_class: str | None = None, multiple: bool = False
) -> selector.EntitySelector:
    """Return a selector for numeric-state entities, optionally device_class-filtered."""
    if device_class is not None:
        return selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=_NUMERIC_DOMAINS, device_class=device_class
            )
        )
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=_NUMERIC_DOMAINS, multiple=multiple)
    )


def position_slider() -> selector.NumberSelector:
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


def priority_slider() -> selector.NumberSelector:
    """Return a number selector for pipeline priority (1-100; 100 = safety)."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=100,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _number(
    *,
    minimum: float,
    maximum: float,
    step: float | None = None,
    mode: selector.NumberSelectorMode = selector.NumberSelectorMode.SLIDER,
    unit: str | None = None,
) -> Callable[[HomeAssistant | None, dict], selector.NumberSelector]:
    """Return a factory producing a NumberSelector with the given config."""
    cfg: dict[str, Any] = {"min": minimum, "max": maximum, "mode": mode}
    if step is not None:
        cfg["step"] = step
    if unit is not None:
        cfg["unit_of_measurement"] = unit

    def _make(_hass: HomeAssistant | None, _options: dict) -> selector.NumberSelector:
        return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))

    return _make


def _const(make: Callable[[], Any]) -> Callable[[HomeAssistant | None, dict], Any]:
    """Adapt a no-arg selector factory to the (hass, options) signature."""

    def _make(_hass: HomeAssistant | None, _options: dict) -> Any:
        return make()

    return _make


def _bool() -> Callable[[HomeAssistant | None, dict], selector.BooleanSelector]:
    return _const(selector.BooleanSelector)


def _entity(
    *domains: str,
) -> Callable[[HomeAssistant | None, dict], selector.EntitySelector]:
    def _make(_hass: HomeAssistant | None, _options: dict) -> selector.EntitySelector:
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=list(domains))
        )

    return _make


def _time() -> Callable[[HomeAssistant | None, dict], selector.TimeSelector]:
    return _const(selector.TimeSelector)


def _select(
    *options: str,
    multiple: bool = False,
    mode: selector.SelectSelectorMode | None = None,
    translation_key: str | None = None,
    sort: bool | None = None,
    custom_value: bool | None = None,
) -> Callable[[HomeAssistant | None, dict], selector.SelectSelector]:
    cfg: dict[str, Any] = {"options": list(options), "multiple": multiple}
    if mode is not None:
        cfg["mode"] = mode
    if translation_key is not None:
        cfg["translation_key"] = translation_key
    if sort is not None:
        cfg["sort"] = sort
    if custom_value is not None:
        cfg["custom_value"] = custom_value

    def _make(_hass: HomeAssistant | None, _options: dict) -> selector.SelectSelector:
        return selector.SelectSelector(selector.SelectSelectorConfig(**cfg))

    return _make


# Sentinel meaning "no marker default" — voluptuous omits the key when cleared.
_UNSET = object()


# =============================================================================
# FieldSpec
# =============================================================================


class ValidatorKind(Enum):
    """How ``services/options_service`` should validate this field's value."""

    RANGE = "range"  # numeric, bounded by ``rng``
    BOOL = "bool"
    ENTITY = "entity"
    ENTITIES = "entities"
    SELECT = "select"
    DURATION = "duration"
    TIME = "time"
    NONE = "none"  # free-form / list — no validator entry


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One configuration field, declared once.

    ``make_selector`` is ``None`` for fields whose selector is built by a
    dynamic section builder (sensor-unit/locale aware); those still carry
    ``rng``/``default``/``validator`` so the derived maps stay single-sourced.
    """

    key: str
    section: str
    validator: ValidatorKind = ValidatorKind.NONE
    rng: tuple[float, float] | None = None
    default: Any = _UNSET
    required: bool = False
    # Whether a cleared value must be stripped from options (the historical
    # ``_*_OPTIONAL_KEYS`` lists — issue #323/#392/#439).
    clearable: bool = False
    select_options: tuple[str, ...] | None = None
    make_selector: Callable[[HomeAssistant | None, dict], Any] | None = None

    def marker(self) -> vol.Marker:
        """Return the voluptuous marker (Required/Optional with default)."""
        cls = vol.Required if self.required else vol.Optional
        if self.default is _UNSET:
            return cls(self.key)
        return cls(self.key, default=self.default)

    def to_marker(
        self, hass: HomeAssistant | None, options: dict | None
    ) -> tuple[vol.Marker, Any]:
        """Return ``(marker, selector)`` for this field.

        Raises if called on a dynamic field (``make_selector is None``); those
        are emitted by their section builder, not here.
        """
        if self.make_selector is None:
            msg = f"FieldSpec {self.key!r} has no make_selector (dynamic field)"
            raise ValueError(msg)
        return self.marker(), self.make_selector(hass, options or {})


# =============================================================================
# Field registry
# =============================================================================
# Declared per section. Order within a section is the config-flow form order.


def _spec(*specs: FieldSpec) -> list[FieldSpec]:
    return list(specs)


_SUN_TRACKING_SPECS = _spec(
    FieldSpec(
        CONF_ENABLE_SUN_TRACKING,
        SECTION_SUN_TRACKING,
        ValidatorKind.BOOL,
        default=True,
        required=True,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_AZIMUTH,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_AZIMUTH,
        default=DEFAULT_WINDOW_AZIMUTH,
        required=True,
        make_selector=_number(minimum=0, maximum=359, unit="°"),
    ),
    # "Generate FOV from measurements" button (#565). Vertical-blind only —
    # BlindPolicy advertises it as a sun-tracking extra; awning/tilt never render
    # it. A transient toggle: ticking it fills fov_left/right from the window
    # width + reveal depth on submit, then re-renders un-ticked. Never persisted.
    FieldSpec(
        CONF_FOV_COMPUTE,
        SECTION_SUN_TRACKING,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_FOV_LEFT,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_FOV,
        default=90,
        required=True,
        make_selector=_number(minimum=0, maximum=180, step=1, unit="°"),
    ),
    FieldSpec(
        CONF_FOV_RIGHT,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_FOV,
        default=90,
        required=True,
        make_selector=_number(minimum=0, maximum=180, step=1, unit="°"),
    ),
    FieldSpec(
        CONF_MIN_ELEVATION,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_ELEVATION,
        make_selector=_number(minimum=0, maximum=90, step=1, unit="°"),
    ),
    FieldSpec(
        CONF_MAX_ELEVATION,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_ELEVATION,
        make_selector=_number(minimum=0, maximum=90, step=1, unit="°"),
    ),
    # CONF_DISTANCE is length/locale-aware → dynamic builder owns the selector,
    # but the spec records its range + canonical default-in-metres role.
    FieldSpec(
        CONF_DISTANCE,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_DISTANCE,
        required=True,
    ),
    FieldSpec(
        CONF_ENABLE_BLIND_SPOT,
        SECTION_SUN_TRACKING,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MINIMIZE_MOVEMENTS,
        SECTION_SUN_TRACKING,
        ValidatorKind.BOOL,
        default=DEFAULT_MINIMIZE_MOVEMENTS,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MAX_COVERAGE_STEPS,
        SECTION_SUN_TRACKING,
        ValidatorKind.RANGE,
        rng=const._RANGE_MAX_COVERAGE_STEPS,
        default=DEFAULT_MAX_COVERAGE_STEPS,
        make_selector=_number(minimum=1, maximum=10, step=1),
    ),
)


_POSITION_SPECS = _spec(
    FieldSpec(
        CONF_DEFAULT_HEIGHT,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_DEFAULT_HEIGHT,
        default=60,
        required=True,
        make_selector=_number(minimum=0, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_ENABLE_MAX_POSITION,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MAX_POSITION,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_MAX_POSITION,
        default=100,
        make_selector=_number(minimum=1, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_ENABLE_MIN_POSITION,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MIN_POSITION,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_MIN_POSITION,
        default=0,
        make_selector=_number(minimum=0, maximum=99, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_MIN_POSITION_SUN_TRACKING,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_MIN_POSITION,
        clearable=True,
        make_selector=_number(minimum=0, maximum=99, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_SUNSET_TIME_ENTITY,
        SECTION_POSITION,
        ValidatorKind.ENTITY,
        clearable=True,
        make_selector=_entity("sensor", "input_datetime"),
    ),
    FieldSpec(
        CONF_SUNRISE_TIME_ENTITY,
        SECTION_POSITION,
        ValidatorKind.ENTITY,
        clearable=True,
        make_selector=_entity("sensor", "input_datetime"),
    ),
    FieldSpec(
        CONF_SUNSET_POS,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_SUNSET_POS,
        clearable=True,
        make_selector=_number(minimum=0, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_SUNSET_OFFSET,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_OFFSET_MINUTES,
        default=0,
        make_selector=_number(
            minimum=-120,
            maximum=120,
            mode=selector.NumberSelectorMode.BOX,
            unit="minutes",
        ),
    ),
    FieldSpec(
        CONF_SUNRISE_OFFSET,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_OFFSET_MINUTES,
        default=0,
        make_selector=_number(
            minimum=-120,
            maximum=120,
            mode=selector.NumberSelectorMode.BOX,
            unit="minutes",
        ),
    ),
    FieldSpec(
        CONF_RETURN_SUNSET,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_ENABLE_MY_POSITION_ENTITIES,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=DEFAULT_ENABLE_MY_POSITION_ENTITIES,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MY_POSITION_VALUE,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_MY_POSITION,
        clearable=True,
        make_selector=_number(minimum=1, maximum=99, unit="%"),
    ),
    FieldSpec(
        CONF_SUNSET_USE_MY,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_OPEN_CLOSE_THRESHOLD,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_OPEN_CLOSE_THRESHOLD,
        default=50,
        make_selector=_number(minimum=1, maximum=99, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_INVERSE_STATE,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_INTERP,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
)


_INTERP_SPECS = _spec(
    FieldSpec(
        CONF_INTERP_START,
        SECTION_INTERP,
        ValidatorKind.RANGE,
        rng=const._RANGE_INTERP_VALUE,
        clearable=True,
        make_selector=_number(minimum=0, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_INTERP_END,
        SECTION_INTERP,
        ValidatorKind.RANGE,
        rng=const._RANGE_INTERP_VALUE,
        clearable=True,
        make_selector=_number(minimum=0, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_INTERP_LIST,
        SECTION_INTERP,
        ValidatorKind.NONE,
        default=[],
        make_selector=_select("0", "50", "100", multiple=True, custom_value=True),
    ),
    FieldSpec(
        CONF_INTERP_LIST_NEW,
        SECTION_INTERP,
        ValidatorKind.NONE,
        default=[],
        make_selector=_select("0", "50", "100", multiple=True, custom_value=True),
    ),
)


_AUTOMATION_SPECS = _spec(
    FieldSpec(
        CONF_DELTA_POSITION,
        SECTION_AUTOMATION,
        ValidatorKind.RANGE,
        rng=const._RANGE_DELTA_POSITION,
        default=2,
        required=True,
        make_selector=_number(minimum=1, maximum=90, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_POSITION_TOLERANCE,
        SECTION_POSITION,
        ValidatorKind.RANGE,
        rng=const._RANGE_POSITION_TOLERANCE,
        default=3,
        make_selector=_number(minimum=0, maximum=20, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_ENABLE_POSITION_MATCHING,
        SECTION_POSITION,
        ValidatorKind.BOOL,
        default=const.DEFAULT_ENABLE_POSITION_MATCHING,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_DELTA_TIME,
        SECTION_AUTOMATION,
        ValidatorKind.RANGE,
        rng=const._RANGE_DELTA_TIME,
        default=2,
        make_selector=_number(
            minimum=2, maximum=60, mode=selector.NumberSelectorMode.BOX, unit="minutes"
        ),
    ),
    FieldSpec(
        CONF_START_ENTITY,
        SECTION_AUTOMATION,
        ValidatorKind.ENTITY,
        make_selector=_entity("sensor", "input_datetime"),
    ),
    FieldSpec(
        CONF_START_TIME,
        SECTION_AUTOMATION,
        ValidatorKind.TIME,
        make_selector=_time(),
    ),
    FieldSpec(
        CONF_END_ENTITY,
        SECTION_AUTOMATION,
        ValidatorKind.ENTITY,
        make_selector=_entity("sensor", "input_datetime"),
    ),
    FieldSpec(
        CONF_END_TIME,
        SECTION_AUTOMATION,
        ValidatorKind.TIME,
        make_selector=_time(),
    ),
)


_MANUAL_OVERRIDE_SPECS = _spec(
    FieldSpec(
        CONF_MANUAL_OVERRIDE_DURATION,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.DURATION,
        default={"hours": 2},
        make_selector=_const(selector.DurationSelector),
    ),
    FieldSpec(
        CONF_MANUAL_OVERRIDE_RESET,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MANUAL_THRESHOLD,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_MANUAL_THRESHOLD,
        make_selector=_number(minimum=0, maximum=99, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_MANUAL_IGNORE_INTERMEDIATE,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_MANUAL_IGNORE_EXTERNAL,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    # Legacy quirk: transit_timeout is configurable in the flow but is NOT in
    # OPTION_RANGES and is NOT a runtime-mutable service field. Its selector
    # bounds come straight from the MIN/MAX constants. Keep rng=None /
    # validator=NONE so the derived OPTION_RANGES + FIELD_VALIDATORS match the
    # historical behaviour exactly.
    FieldSpec(
        CONF_TRANSIT_TIMEOUT,
        SECTION_MANUAL_OVERRIDE,
        ValidatorKind.NONE,
        default=DEFAULT_TRANSIT_TIMEOUT_SECONDS,
        make_selector=_number(
            minimum=MIN_TRANSIT_TIMEOUT,
            maximum=MAX_TRANSIT_TIMEOUT,
            step=5,
            unit="seconds",
        ),
    ),
)


_FORCE_OVERRIDE_SPECS = _spec(
    FieldSpec(
        CONF_FORCE_OVERRIDE_SENSORS,
        SECTION_FORCE_OVERRIDE,
        ValidatorKind.ENTITIES,
        default=[],
        make_selector=_const(lambda: binary_on_selector(multiple=True)),
    ),
    FieldSpec(
        CONF_FORCE_OVERRIDE_POSITION,
        SECTION_FORCE_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_FORCE_POSITION,
        default=0,
        make_selector=_number(minimum=0, maximum=100, step=1, unit="%"),
    ),
    FieldSpec(
        CONF_FORCE_OVERRIDE_MIN_MODE,
        SECTION_FORCE_OVERRIDE,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
)


_MOTION_OVERRIDE_SPECS = _spec(
    FieldSpec(
        CONF_MOTION_SENSORS,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.ENTITIES,
        default=[],
        make_selector=_const(lambda: presence_like_selector(multiple=True)),
    ),
    FieldSpec(
        CONF_MOTION_MEDIA_PLAYERS,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.ENTITIES,
        default=[],
        make_selector=_const(lambda: media_player_selector(multiple=True)),
    ),
    FieldSpec(
        CONF_MOTION_TEMPLATE,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.NONE,
        clearable=True,
        make_selector=_const(lambda: selector.TemplateSelector()),
    ),
    # Shared ``template_combine_mode`` translation key (not field-specific) so any
    # future template field reusing TemplateCombineMode shows the same OR/AND labels.
    FieldSpec(
        CONF_MOTION_TEMPLATE_MODE,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.SELECT,
        default=DEFAULT_MOTION_TEMPLATE_MODE,
        select_options=tuple(m.value for m in const.TemplateCombineMode),
        make_selector=_select(
            *[m.value for m in const.TemplateCombineMode],
            mode=selector.SelectSelectorMode.LIST,
            translation_key="template_combine_mode",
        ),
    ),
    FieldSpec(
        CONF_MOTION_TIMEOUT,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_MOTION_TIMEOUT,
        default=DEFAULT_MOTION_TIMEOUT,
        make_selector=_number(minimum=30, maximum=3600, step=30, unit="seconds"),
    ),
    FieldSpec(
        CONF_MOTION_TIMEOUT_MODE,
        SECTION_MOTION_OVERRIDE,
        ValidatorKind.SELECT,
        default=DEFAULT_MOTION_TIMEOUT_MODE,
        select_options=(MOTION_TIMEOUT_MODE_RETURN, MOTION_TIMEOUT_MODE_HOLD),
        make_selector=_select(
            MOTION_TIMEOUT_MODE_RETURN,
            MOTION_TIMEOUT_MODE_HOLD,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="motion_timeout_mode",
        ),
    ),
)


_DEBUG_SPECS = _spec(
    FieldSpec(
        CONF_DRY_RUN,
        SECTION_DEBUG,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_DEBUG_MODE,
        SECTION_DEBUG,
        ValidatorKind.BOOL,
        default=False,
        make_selector=_bool(),
    ),
    FieldSpec(
        CONF_DEBUG_CATEGORIES,
        SECTION_DEBUG,
        ValidatorKind.NONE,
        default=[],
        select_options=tuple(DEBUG_CATEGORIES_ALL),
        make_selector=_select(
            *DEBUG_CATEGORIES_ALL,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="debug_categories",
        ),
    ),
    FieldSpec(
        CONF_DEBUG_EVENT_BUFFER_SIZE,
        SECTION_DEBUG,
        ValidatorKind.NONE,
        default=DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
        make_selector=_number(minimum=10, maximum=MAX_DEBUG_EVENT_BUFFER_SIZE, step=10),
    ),
)


# --- Custom position: slot-based; base fields + venetian-only tilt fields ---


def _custom_position_base_specs() -> list[FieldSpec]:
    """Per-slot base custom-position fields (no tilt)."""
    specs: list[FieldSpec] = []
    for slot in CUSTOM_POSITION_SLOTS.values():
        # Legacy single-sensor key: still settable (rollback mirror target) but
        # superseded by the `sensors` list in the config-flow schema.
        specs.append(
            FieldSpec(
                slot["sensor"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.ENTITY,
                clearable=True,
                make_selector=_const(binary_on_selector),
            )
        )
        specs.append(
            FieldSpec(
                slot["sensors"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.ENTITIES,
                default=[],
                make_selector=_const(lambda: binary_on_selector(multiple=True)),
            )
        )
        specs.append(
            FieldSpec(
                slot["template"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.NONE,
                clearable=True,
                make_selector=_const(lambda: selector.TemplateSelector()),
            )
        )
        specs.append(
            FieldSpec(
                slot["template_mode"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.SELECT,
                default=DEFAULT_TEMPLATE_COMBINE_MODE,
                select_options=tuple(m.value for m in const.TemplateCombineMode),
                make_selector=_select(
                    *[m.value for m in const.TemplateCombineMode],
                    mode=selector.SelectSelectorMode.LIST,
                    translation_key="template_combine_mode",
                ),
            )
        )
        specs.append(
            FieldSpec(
                slot["position"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.RANGE,
                rng=const._RANGE_CUSTOM_POSITION,
                clearable=True,
                make_selector=_const(position_slider),
            )
        )
        specs.append(
            FieldSpec(
                slot["priority"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.RANGE,
                rng=const._RANGE_CUSTOM_PRIORITY,
                clearable=True,
                make_selector=_const(priority_slider),
            )
        )
        specs.append(
            FieldSpec(
                slot["min_mode"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.BOOL,
                default=False,
                make_selector=_bool(),
            )
        )
        specs.append(
            FieldSpec(
                slot["use_my"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.BOOL,
                default=False,
                make_selector=_bool(),
            )
        )
    return specs


def _custom_position_tilt_specs() -> list[FieldSpec]:
    """Venetian-only per-slot tilt fields + global default/sunset tilt."""
    specs: list[FieldSpec] = []
    for slot in CUSTOM_POSITION_SLOTS.values():
        specs.append(
            FieldSpec(
                slot["tilt"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.RANGE,
                rng=const._RANGE_TILT,
                clearable=True,
                make_selector=_const(position_slider),
            )
        )
        specs.append(
            FieldSpec(
                slot["tilt_only"],
                SECTION_CUSTOM_POSITION,
                ValidatorKind.BOOL,
                default=False,
                make_selector=_bool(),
            )
        )
    specs.append(
        FieldSpec(
            CONF_DEFAULT_TILT,
            SECTION_CUSTOM_POSITION,
            ValidatorKind.RANGE,
            rng=const._RANGE_TILT,
            clearable=True,
            make_selector=_const(position_slider),
        )
    )
    specs.append(
        FieldSpec(
            CONF_SUNSET_TILT,
            SECTION_CUSTOM_POSITION,
            ValidatorKind.RANGE,
            rng=const._RANGE_TILT,
            clearable=True,
            make_selector=_const(position_slider),
        )
    )
    return specs


def custom_position_schema(*, include_tilt: bool = False) -> vol.Schema:
    """Build the custom-position section schema (slot-interleaved).

    Per-slot sensors/template/template_mode/position/priority/min_mode/use_my,
    with tilt/tilt_only interleaved per slot when *include_tilt* (venetian),
    then global default/sunset tilt at the end. The legacy single-sensor key
    is deliberately absent — it lives on only as a rollback mirror written at
    save time (issue #563).
    """
    schema: dict = {}
    for slot in CUSTOM_POSITION_SLOTS.values():
        schema[vol.Optional(slot["sensors"], default=[])] = binary_on_selector(
            multiple=True
        )
        schema[vol.Optional(slot["template"])] = selector.TemplateSelector()
        schema[
            vol.Optional(slot["template_mode"], default=DEFAULT_TEMPLATE_COMBINE_MODE)
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[m.value for m in const.TemplateCombineMode],
                mode=selector.SelectSelectorMode.LIST,
                translation_key="template_combine_mode",
            )
        )
        schema[vol.Optional(slot["position"])] = position_slider()
        schema[vol.Optional(slot["priority"])] = priority_slider()
        schema[vol.Optional(slot["min_mode"], default=False)] = (
            selector.BooleanSelector()
        )
        schema[vol.Optional(slot["use_my"], default=False)] = selector.BooleanSelector()
        if include_tilt:
            schema[vol.Optional(slot["tilt"])] = position_slider()
            schema[vol.Optional(slot["tilt_only"], default=False)] = (
                selector.BooleanSelector()
            )
    if include_tilt:
        schema[vol.Optional(CONF_DEFAULT_TILT)] = position_slider()
        schema[vol.Optional(CONF_SUNSET_TILT)] = position_slider()
    return vol.Schema(schema)


# Glare-zones enable toggle — appended to the sun-tracking section for cover
# types that support glare zones (blind). Config-flow-only (no validator / no
# range), matching the legacy behaviour.
_GLARE_TOGGLE_SPECS = _spec(
    FieldSpec(
        CONF_ENABLE_GLARE_ZONES,
        SECTION_GLARE_ZONES,
        ValidatorKind.NONE,
        default=False,
        make_selector=_bool(),
    ),
)


# --- Dynamic-section fields: spec metadata only (selector via builder) ---
# Weather override, light/cloud, temperature climate. Their selectors depend on
# a bound sensor's unit; the section builders (config_dynamic.py) emit the
# markers. Here we record range/default/validator so the derived maps are
# single-sourced.

_WEATHER_OVERRIDE_SPECS = _spec(
    FieldSpec(
        CONF_WEATHER_BYPASS_AUTO_CONTROL,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.BOOL,
        default=True,
    ),
    FieldSpec(
        CONF_WEATHER_WIND_SPEED_SENSOR,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_WIND_DIRECTION_SENSOR,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_RAIN_SENSOR,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_IS_RAINING_SENSOR,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_IS_WINDY_SENSOR,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_SEVERE_SENSORS,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.ENTITIES,
        default=[],
    ),
    FieldSpec(
        CONF_WEATHER_WIND_SPEED_THRESHOLD,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_WEATHER_WIND_SPEED,
        default=DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    ),
    FieldSpec(
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_WEATHER_WIND_DIRECTION_TOLERANCE,
        default=DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    ),
    FieldSpec(
        CONF_WEATHER_RAIN_THRESHOLD,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_WEATHER_RAIN,
        default=DEFAULT_WEATHER_RAIN_THRESHOLD,
    ),
    FieldSpec(
        CONF_WEATHER_OVERRIDE_POSITION,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_WEATHER_OVERRIDE_POSITION,
        default=0,
    ),
    FieldSpec(
        CONF_WEATHER_OVERRIDE_MIN_MODE,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.BOOL,
        default=False,
    ),
    FieldSpec(
        CONF_WEATHER_TIMEOUT,
        SECTION_WEATHER_OVERRIDE,
        ValidatorKind.RANGE,
        rng=const._RANGE_WEATHER_TIMEOUT,
        default=DEFAULT_WEATHER_TIMEOUT,
    ),
)

_LIGHT_CLOUD_SPECS = _spec(
    FieldSpec(
        CONF_CLOUD_SUPPRESSION, SECTION_LIGHT_CLOUD, ValidatorKind.BOOL, default=False
    ),
    FieldSpec(
        CONF_CLOUDY_POSITION, SECTION_LIGHT_CLOUD, ValidatorKind.NONE, clearable=True
    ),
    FieldSpec(
        CONF_WEATHER_ENTITY, SECTION_LIGHT_CLOUD, ValidatorKind.ENTITY, clearable=True
    ),
    FieldSpec(
        CONF_IS_SUNNY_SENSOR, SECTION_LIGHT_CLOUD, ValidatorKind.ENTITY, clearable=True
    ),
    FieldSpec(
        CONF_LUX_ENTITY, SECTION_LIGHT_CLOUD, ValidatorKind.ENTITY, clearable=True
    ),
    FieldSpec(
        CONF_IRRADIANCE_ENTITY,
        SECTION_LIGHT_CLOUD,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_CLOUD_COVERAGE_ENTITY,
        SECTION_LIGHT_CLOUD,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_WEATHER_STATE,
        SECTION_LIGHT_CLOUD,
        ValidatorKind.NONE,
        default=["sunny", "partlycloudy", "cloudy", "clear"],
    ),
    FieldSpec(
        CONF_LUX_THRESHOLD, SECTION_LIGHT_CLOUD, ValidatorKind.NONE, default=1000
    ),
    FieldSpec(
        CONF_IRRADIANCE_THRESHOLD, SECTION_LIGHT_CLOUD, ValidatorKind.NONE, default=300
    ),
    FieldSpec(
        CONF_CLOUD_COVERAGE_THRESHOLD,
        SECTION_LIGHT_CLOUD,
        ValidatorKind.NONE,
        default=DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    ),
)

_TEMPERATURE_CLIMATE_SPECS = _spec(
    FieldSpec(
        CONF_CLIMATE_MODE,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.BOOL,
        default=False,
    ),
    FieldSpec(
        CONF_TEMP_ENTITY,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_OUTSIDETEMP_ENTITY,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_PRESENCE_ENTITY,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.ENTITY,
        clearable=True,
    ),
    FieldSpec(
        CONF_TEMP_LOW,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.RANGE,
        rng=const._RANGE_TEMPERATURE,
        default=21,
    ),
    FieldSpec(
        CONF_TEMP_HIGH,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.RANGE,
        rng=const._RANGE_TEMPERATURE,
        default=25,
    ),
    FieldSpec(
        CONF_OUTSIDE_THRESHOLD,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.RANGE,
        rng=const._RANGE_OUTSIDE_THRESHOLD,
        default=25,
    ),
    FieldSpec(
        CONF_TRANSPARENT_BLIND,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.BOOL,
        default=False,
    ),
    FieldSpec(
        CONF_WINTER_CLOSE_INSULATION,
        SECTION_TEMPERATURE_CLIMATE,
        ValidatorKind.BOOL,
        default=False,
    ),
)


# --- Geometry / venetian / glare-zone fields: spec metadata only ---
# Their selectors are length/locale aware and per cover type; the cover-type
# policies' geometry builders emit the markers. Specs here keep ranges/validators
# single-sourced. Blind-spot numeric fields live in the blind_spot section.

_BLIND_SPOT_SPECS = _spec(
    FieldSpec(
        CONF_BLIND_SPOT_LEFT,
        SECTION_BLIND_SPOT,
        ValidatorKind.RANGE,
        rng=const._RANGE_BLIND_SPOT_LEFT,
    ),
    FieldSpec(
        CONF_BLIND_SPOT_RIGHT,
        SECTION_BLIND_SPOT,
        ValidatorKind.RANGE,
        rng=const._RANGE_BLIND_SPOT_RIGHT,
    ),
    FieldSpec(
        CONF_BLIND_SPOT_ELEVATION,
        SECTION_BLIND_SPOT,
        ValidatorKind.RANGE,
        rng=const._RANGE_BLIND_SPOT_ELEVATION,
    ),
)

# Geometry specs are owned per cover type, but the numeric metadata is declared
# here so OPTION_RANGES/FIELD_VALIDATORS cover them. They carry section=geometry.
_GEOMETRY_SPECS = _spec(
    FieldSpec(
        CONF_HEIGHT_WIN,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_HEIGHT_WIN,
    ),
    FieldSpec(
        CONF_WINDOW_WIDTH,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_WINDOW_WIDTH,
    ),
    FieldSpec(
        CONF_WINDOW_DEPTH,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_WINDOW_DEPTH,
    ),
    FieldSpec(
        CONF_SILL_HEIGHT,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_SILL_HEIGHT,
    ),
    FieldSpec(
        CONF_LENGTH_AWNING,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_LENGTH_AWNING,
    ),
    FieldSpec(
        CONF_AWNING_ANGLE,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_AWNING_ANGLE,
    ),
    # Oscillating (drop-arm) awning geometry (#412).
    FieldSpec(
        CONF_ARM_LENGTH,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_ARM_LENGTH,
    ),
    FieldSpec(
        CONF_AWNING_MIN_ANGLE,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_AWNING_SWEEP_ANGLE,
    ),
    FieldSpec(
        CONF_AWNING_MAX_ANGLE,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_AWNING_SWEEP_ANGLE,
    ),
    FieldSpec(
        CONF_AWNING_HOUSING_OFFSET,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_AWNING_HOUSING_OFFSET,
    ),
    FieldSpec(
        CONF_TILT_DEPTH,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_TILT_DEPTH,
    ),
    FieldSpec(
        CONF_TILT_DISTANCE,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_TILT_DISTANCE,
    ),
    FieldSpec(
        CONF_TILT_MODE,
        SECTION_GEOMETRY,
        ValidatorKind.SELECT,
        select_options=("mode1", "mode2"),
    ),
    FieldSpec(
        CONF_MAX_TILT, SECTION_GEOMETRY, ValidatorKind.RANGE, rng=const._RANGE_MAX_TILT
    ),
    FieldSpec(
        CONF_MIN_TILT, SECTION_GEOMETRY, ValidatorKind.RANGE, rng=const._RANGE_MIN_TILT
    ),
    FieldSpec(
        CONF_VENETIAN_POST_SETTLE_HOLD,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_VENETIAN_POST_SETTLE_HOLD,
    ),
    FieldSpec(
        CONF_VENETIAN_TILT_SKIP_ABOVE,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_VENETIAN_TILT_SKIP_ABOVE,
    ),
    FieldSpec(
        CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
        SECTION_GEOMETRY,
        ValidatorKind.RANGE,
        rng=const._RANGE_VENETIAN_BACKROTATE_PUBLISH_LAG,
    ),
    FieldSpec(
        CONF_VENETIAN_MODE,
        SECTION_GEOMETRY,
        ValidatorKind.SELECT,
        select_options=const.VENETIAN_MODES,
    ),
)

# Glare-zone per-zone fields (vertical-only). 4 zones × x/y/radius/z.
_GLARE_ZONE_SPECS = _spec(
    *[
        FieldSpec(
            f"glare_zone_{i}_{axis}",
            SECTION_GLARE_ZONES,
            ValidatorKind.RANGE,
            rng=rng,
        )
        for i in range(1, 5)
        for axis, rng in (
            ("x", const._RANGE_GLARE_ZONE_X),
            ("y", const._RANGE_GLARE_ZONE_Y),
            ("radius", const._RANGE_GLARE_ZONE_RADIUS),
            ("z", const._RANGE_GLARE_ZONE_Z),
        )
    ]
)


# =============================================================================
# Registry assembly
# =============================================================================

_ALL_SPEC_GROUPS: tuple[list[FieldSpec], ...] = (
    _GEOMETRY_SPECS,
    _SUN_TRACKING_SPECS,
    _POSITION_SPECS,
    _INTERP_SPECS,
    _BLIND_SPOT_SPECS,
    _GLARE_TOGGLE_SPECS,
    _GLARE_ZONE_SPECS,
    _AUTOMATION_SPECS,
    _LIGHT_CLOUD_SPECS,
    _TEMPERATURE_CLIMATE_SPECS,
    _FORCE_OVERRIDE_SPECS,
    _WEATHER_OVERRIDE_SPECS,
    _MANUAL_OVERRIDE_SPECS,
    _custom_position_base_specs(),
    _custom_position_tilt_specs(),
    _MOTION_OVERRIDE_SPECS,
    _DEBUG_SPECS,
)


def _build_registry() -> dict[str, FieldSpec]:
    reg: dict[str, FieldSpec] = {}
    for group in _ALL_SPEC_GROUPS:
        for spec in group:
            if spec.key in reg:
                msg = f"Duplicate FieldSpec for key {spec.key!r}"
                raise ValueError(msg)
            reg[spec.key] = spec
    return reg


#: The single registry of every configuration field, keyed by CONF_* value.
FIELD_SPECS: dict[str, FieldSpec] = _build_registry()


# =============================================================================
# Derived maps (single-sourced from FIELD_SPECS)
# =============================================================================


def _build_option_ranges() -> dict[str, tuple[float, float]]:
    return {s.key: s.rng for s in FIELD_SPECS.values() if s.rng is not None}


#: ``{key: (min, max)}`` for every numeric option. ``const.OPTION_RANGES`` is
#: a re-export of this.
OPTION_RANGES: dict[str, tuple[float, float]] = _build_option_ranges()


#: Threshold fields that accept a Home Assistant Jinja2 template (rendered to a
#: number once per coordinator cycle) in place of a fixed value (issue #577).
#: Single source consumed by the config-flow selector builder
#: (``config_dynamic``), the service validators (``services.options_service``),
#: and the runtime resolver (``templates.TemplateResolver``). All are values
#: compared against live readings each cycle, so a dynamic value is meaningful.
TEMPLATABLE_KEYS: frozenset[str] = frozenset(
    {
        CONF_LUX_THRESHOLD,
        CONF_IRRADIANCE_THRESHOLD,
        CONF_CLOUD_COVERAGE_THRESHOLD,
        CONF_TEMP_LOW,
        CONF_TEMP_HIGH,
        CONF_OUTSIDE_THRESHOLD,
        CONF_WEATHER_WIND_SPEED_THRESHOLD,
        CONF_WEATHER_RAIN_THRESHOLD,
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    }
)


def option_default(key: str, fallback: Any = None) -> Any:
    """Return the declared default for *key*, or *fallback* if none/unknown."""
    spec = FIELD_SPECS.get(key)
    if spec is None or spec.default is _UNSET:
        return fallback
    return spec.default


def section_keys(section: str) -> tuple[str, ...]:
    """Return the ordered CONF_* keys declared for *section*."""
    return tuple(s.key for s in FIELD_SPECS.values() if s.section == section)


#: Default ordered list of the common sections (geometry / glare_zones inserted
#: per cover type by the policy). Order follows the legacy options menu so the
#: assembled menu stays familiar (gated sections like interp/blind_spot are
#: filtered by their enable toggle in the menu builder, but kept here so
#: ``live_option_keys`` covers them for validation).
COMMON_SECTION_ORDER: tuple[str, ...] = (
    SECTION_SUN_TRACKING,
    SECTION_POSITION,
    SECTION_INTERP,
    SECTION_BLIND_SPOT,
    SECTION_AUTOMATION,
    SECTION_LIGHT_CLOUD,
    SECTION_TEMPERATURE_CLIMATE,
    SECTION_FORCE_OVERRIDE,
    SECTION_WEATHER_OVERRIDE,
    SECTION_MANUAL_OVERRIDE,
    SECTION_CUSTOM_POSITION,
    SECTION_MOTION_OVERRIDE,
    SECTION_DEBUG,
)

#: Venetian-only custom-position tilt keys (per-slot tilt/tilt_only + global
#: default/sunset tilt). Declared here so the venetian policy can advertise
#: them as its custom-position extras.
CUSTOM_POSITION_TILT_KEYS: tuple[str, ...] = tuple(
    k
    for slot in CUSTOM_POSITION_SLOTS.values()
    for k in (slot["tilt"], slot["tilt_only"])
) + (CONF_DEFAULT_TILT, CONF_SUNSET_TILT)
