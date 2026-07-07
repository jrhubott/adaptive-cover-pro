"""Typed configuration dataclasses for cover calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    BLIND_SPOT_SLOT_NUMBERS,
    BLIND_SPOT_SLOTS,
    DEFAULT_BLIND_SPOT_ELEVATION_MODE,
    DEFAULT_MOTION_TEMPLATE_MODE,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    DEFAULT_WEATHER_ENABLED,
    BlindSpot,
    TiltMode,
)


def _make_blind_spot(
    left: Any,
    right: Any,
    elevation: Any,
    elevation_mode: Any = None,
) -> BlindSpot | None:
    """Build a :class:`BlindSpot` from a slot's values, or ``None`` if inactive.

    A slot is *active* only when its left AND right are both set. This is the
    single place a ``BlindSpot`` is assembled from raw slot values — both the
    live slot-1 derivation (``CoverConfig.blind_spots``) and the slot-2+ builder
    (``_extra_blind_spots_from``) delegate here. ``elevation_mode`` falls back to
    the default ("below") when absent (issue #702).
    """
    if left is None or right is None:
        return None
    return BlindSpot(
        left=int(left),
        right=int(right),
        elevation=None if elevation is None else int(elevation),
        elevation_mode=elevation_mode or DEFAULT_BLIND_SPOT_ELEVATION_MODE,
    )


def _extra_blind_spots_from(get: Any, *, enabled: bool) -> tuple[BlindSpot, ...]:
    """Build blind-spot slots 2..N from an option accessor.

    Slot 1 is intentionally excluded: it derives *live* from the flat
    ``blind_spot_*`` fields on :class:`CoverConfig` (so post-construction
    mutation of those fields is reflected). The whole feature is gated by the
    master ``enabled`` flag.
    """
    if not enabled:
        return ()
    spots: list[BlindSpot] = []
    for n in BLIND_SPOT_SLOT_NUMBERS:
        if n == 1:
            continue
        keys = BLIND_SPOT_SLOTS[n]
        bs = _make_blind_spot(
            get(keys["left"]),
            get(keys["right"]),
            get(keys["elevation"]),
            get(keys["elevation_mode"]),
        )
        if bs is not None:
            spots.append(bs)
    return tuple(spots)


def _num_or(value: Any, default: float) -> float:
    """Return *value* as a float, or *default* when it isn't numeric.

    Threshold options in ``config_fields.TEMPLATABLE_KEYS`` may hold an
    unrendered Jinja2 template string here — ``from_options`` runs at
    setup/attach time on the raw options, before the per-cycle
    ``TemplateResolver`` substitutes a number (issue #577). Falling back to the
    default keeps the typed snapshot numeric until the first resolved cycle.
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


@dataclass
class GlareZone:
    """A single glare protection zone.

    Coordinates are relative to the window centre projected onto the floor:
      x = along the wall (positive = right when facing window from inside), metres
      y = into the room (perpendicular to window), metres — must be positive
      radius = zone radius, metres
      z = height of the protected target above the floor, metres — 0 (default) protects
          a floor disk; >0 protects a point at that height (e.g. eye level, tabletop).
    """

    name: str
    x: float
    y: float
    radius: float
    z: float = 0.0


@dataclass
class GlareZonesConfig:
    """All glare zone configuration for a vertical cover."""

    zones: list[GlareZone]
    window_width: float  # metres — used to check if a sun ray can reach a zone


@dataclass
class CoverConfig:
    """Common configuration for all cover types."""

    win_azi: int
    fov_left: int
    fov_right: int
    h_def: int
    sunset_pos: int | None
    sunset_off: int
    sunrise_off: int
    max_pos: int
    min_pos: int
    max_pos_sun_only: bool  # enable_max_position
    min_pos_sun_only: bool  # enable_min_position
    blind_spot_left: int | None
    blind_spot_right: int | None
    blind_spot_elevation: int | None
    blind_spot_on: bool
    min_elevation: int | None
    max_elevation: int | None
    min_pos_sun_tracking: int | None = (
        None  # separate floor for sun-tracking only; None = use min_pos
    )
    # Slot-1 elevation mode (issue #702): "below" (default) blocks low sun,
    # "above" blocks high sun. Flat like the other slot-1 fields so the live
    # ``blind_spots`` property reflects post-construction mutation.
    blind_spot_elevation_mode: str = DEFAULT_BLIND_SPOT_ELEVATION_MODE
    # Blind-spot slots 2..N (issue #701). Slot 1 is NOT stored here — it derives
    # live from the flat ``blind_spot_*`` fields above via the ``blind_spots``
    # property, so post-construction mutation of those fields is reflected.
    extra_blind_spots: tuple[BlindSpot, ...] = ()

    @property
    def blind_spots(self) -> tuple[BlindSpot, ...]:
        """Active blind-spot wedges: slot 1 (live, from flat fields) then 2..N.

        Sun inside ANY of these is treated as blocked. Returns ``()`` when the
        master enable is off or no slot is active. Computed on every access so
        it always reflects the current flat slot-1 field values.
        """
        if not self.blind_spot_on:
            return ()
        spots: list[BlindSpot] = []
        slot1 = _make_blind_spot(
            self.blind_spot_left,
            self.blind_spot_right,
            self.blind_spot_elevation,
            self.blind_spot_elevation_mode,
        )
        if slot1 is not None:
            spots.append(slot1)
        spots.extend(self.extra_blind_spots)
        return tuple(spots)

    @classmethod
    def from_options(cls, options: dict) -> CoverConfig:
        """Build a CoverConfig from a raw options/config dict (CONF_* keys)."""
        from .const import (
            CONF_AZIMUTH,
            CONF_BLIND_SPOT_ELEVATION,
            CONF_BLIND_SPOT_ELEVATION_MODE,
            CONF_BLIND_SPOT_LEFT,
            CONF_BLIND_SPOT_RIGHT,
            CONF_DEFAULT_HEIGHT,
            CONF_ENABLE_BLIND_SPOT,
            CONF_ENABLE_MAX_POSITION,
            CONF_ENABLE_MIN_POSITION,
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_MAX_ELEVATION,
            CONF_MAX_POSITION,
            CONF_MIN_ELEVATION,
            CONF_MIN_POSITION,
            CONF_MIN_POSITION_SUN_TRACKING,
            CONF_SUNRISE_OFFSET,
            CONF_SUNSET_OFFSET,
            CONF_SUNSET_POS,
            DEFAULT_FOV_LEFT,
            DEFAULT_FOV_RIGHT,
        )

        return cls(
            win_azi=options.get(CONF_AZIMUTH) or 180,
            fov_left=(
                options[CONF_FOV_LEFT]
                if options.get(CONF_FOV_LEFT) is not None
                else DEFAULT_FOV_LEFT
            ),
            fov_right=(
                options[CONF_FOV_RIGHT]
                if options.get(CONF_FOV_RIGHT) is not None
                else DEFAULT_FOV_RIGHT
            ),
            h_def=options.get(CONF_DEFAULT_HEIGHT) or 0,
            sunset_pos=options.get(CONF_SUNSET_POS),
            sunset_off=options.get(CONF_SUNSET_OFFSET) or 0,
            sunrise_off=options.get(
                CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET)
            )
            or 0,
            max_pos=(  # no `or` — 0 ("always closed", #806) must survive, not fall back to 100
                options[CONF_MAX_POSITION]
                if options.get(CONF_MAX_POSITION) is not None
                else 100
            ),
            min_pos=options.get(CONF_MIN_POSITION) or 0,
            max_pos_sun_only=options.get(CONF_ENABLE_MAX_POSITION, False),
            min_pos_sun_only=options.get(CONF_ENABLE_MIN_POSITION, False),
            min_pos_sun_tracking=(  # no `or` — preserves None vs 0; int() coerces HA float from NumberSelector
                int(options[CONF_MIN_POSITION_SUN_TRACKING])
                if options.get(CONF_MIN_POSITION_SUN_TRACKING) is not None
                else None
            ),
            blind_spot_left=options.get(CONF_BLIND_SPOT_LEFT),
            blind_spot_right=options.get(CONF_BLIND_SPOT_RIGHT),
            blind_spot_elevation=options.get(CONF_BLIND_SPOT_ELEVATION),
            blind_spot_elevation_mode=options.get(
                CONF_BLIND_SPOT_ELEVATION_MODE, DEFAULT_BLIND_SPOT_ELEVATION_MODE
            ),
            blind_spot_on=options.get(CONF_ENABLE_BLIND_SPOT, False),
            extra_blind_spots=_extra_blind_spots_from(
                options.get, enabled=options.get(CONF_ENABLE_BLIND_SPOT, False)
            ),
            min_elevation=options.get(CONF_MIN_ELEVATION, None),
            max_elevation=options.get(CONF_MAX_ELEVATION, None),
        )


@dataclass
class VerticalConfig:
    """Configuration specific to vertical blinds."""

    distance: float
    h_win: float
    window_depth: float = 0.0
    sill_height: float = 0.0
    glare_zones: GlareZonesConfig | None = None


@dataclass
class HorizontalConfig:
    """Configuration specific to horizontal awnings."""

    awn_length: float = 2.0
    awn_angle: float = 0.0


@dataclass
class OscillatingConfig:
    """Configuration specific to oscillating (drop-arm) awnings (#412).

    The arm of length ``arm_length`` sweeps from ``min_angle`` (closed) to
    ``max_angle`` (fully open); the fabric angle is therefore a function of the
    open percentage rather than a fixed value. ``housing_offset`` is the pivot
    height above the window top. ``pivot_offset`` is the horizontal distance from
    the arm pivot / fabric plane to the window glass (the arm/housing standoff
    plus any window inset); it lowers the dropped fabric's shadow on the pane at
    low sun. Default 0.0 → flush mount (no-op).
    """

    arm_length: float = 0.8
    min_angle: float = 0.0
    max_angle: float = 175.0
    housing_offset: float = 0.0
    pivot_offset: float = 0.0

    @classmethod
    def from_options(cls, options: dict) -> OscillatingConfig:
        """Build from a config-entry options dict, applying defaults."""
        from .const import (
            CONF_ARM_LENGTH,
            CONF_AWNING_HOUSING_OFFSET,
            CONF_AWNING_MAX_ANGLE,
            CONF_AWNING_MIN_ANGLE,
            CONF_AWNING_PIVOT_OFFSET,
            DEFAULT_ARM_LENGTH,
            DEFAULT_AWNING_HOUSING_OFFSET,
            DEFAULT_AWNING_MAX_ANGLE,
            DEFAULT_AWNING_MIN_ANGLE,
            DEFAULT_AWNING_PIVOT_OFFSET,
        )

        return cls(
            arm_length=options.get(CONF_ARM_LENGTH) or DEFAULT_ARM_LENGTH,
            min_angle=options.get(CONF_AWNING_MIN_ANGLE, DEFAULT_AWNING_MIN_ANGLE),
            max_angle=options.get(CONF_AWNING_MAX_ANGLE, DEFAULT_AWNING_MAX_ANGLE),
            housing_offset=options.get(CONF_AWNING_HOUSING_OFFSET)
            or DEFAULT_AWNING_HOUSING_OFFSET,
            pivot_offset=options.get(CONF_AWNING_PIVOT_OFFSET)
            or DEFAULT_AWNING_PIVOT_OFFSET,
        )


@dataclass
class RoofWindowConfig:
    """Configuration specific to roof / skylight windows (#212).

    A roof window is a vertical-style blind travelling down-slope across
    pitched glass. It reuses the vertical window geometry (distance, height,
    depth, sill — carried in a sibling ``VerticalConfig``) and adds the glass
    pitch and the along-slope roof height above the window.

    ``roof_pitch`` is measured FROM HORIZONTAL: ``0`` = flat skylight,
    ``90`` = vertical window (reproduces the vertical engine exactly).
    ``roof_height_above`` enables the ridge occlusion gate when > 0; ``0``
    (the default) disables it (e.g. a window sitting at the ridge).
    """

    roof_pitch: float = 40.0
    roof_height_above: float = 0.0

    @classmethod
    def from_options(cls, options: dict) -> RoofWindowConfig:
        """Build from a config-entry options dict, applying defaults."""
        from .const import (
            CONF_ROOF_HEIGHT_ABOVE,
            CONF_ROOF_PITCH,
            DEFAULT_ROOF_HEIGHT_ABOVE,
            DEFAULT_ROOF_PITCH,
        )

        pitch = options.get(CONF_ROOF_PITCH)
        height_above = options.get(CONF_ROOF_HEIGHT_ABOVE)
        return cls(
            roof_pitch=float(pitch) if pitch is not None else float(DEFAULT_ROOF_PITCH),
            roof_height_above=(
                float(height_above)
                if height_above is not None
                else float(DEFAULT_ROOF_HEIGHT_ABOVE)
            ),
        )


@dataclass
class SlidingCurtainConfig:
    """Configuration specific to horizontal sliding curtains (#829, Part 2).

    The two shade-area points are floor Cartesian coordinates in the SAME frame
    as :class:`GlareZone`: ``x`` along the wall (signed, positive = right facing
    the window from inside), ``y`` depth into the room (> 0). Projecting both
    points to the window plane gives the along-wall interval the fabric must
    cover; ``slide_direction`` maps that interval to an open percentage.

    When ``is_area_configured`` is False (the master toggle is off, or no window
    width is set) the engine falls back to the Part 1 binary open/close.
    """

    enabled: bool = False
    slide_direction: str = "bi_part"
    window_width: float = 0.0
    point1_x: float = 0.0
    point1_y: float = 0.0
    point2_x: float = 0.0
    point2_y: float = 0.0

    @property
    def is_area_configured(self) -> bool:
        """Whether the continuous two-point shade-area model should be used."""
        return self.enabled and self.window_width > 0

    @classmethod
    def from_options(cls, options: dict) -> SlidingCurtainConfig:
        """Build from a config-entry options dict, applying defaults."""
        from .const import (
            CONF_SLIDING_ENABLE_SHADE_AREA,
            CONF_SLIDING_POINT1_X,
            CONF_SLIDING_POINT1_Y,
            CONF_SLIDING_POINT2_X,
            CONF_SLIDING_POINT2_Y,
            CONF_SLIDING_SLIDE_DIRECTION,
            CONF_WINDOW_WIDTH,
            DEFAULT_SLIDING_ENABLE_SHADE_AREA,
            DEFAULT_SLIDING_POINT_X,
            DEFAULT_SLIDING_POINT_Y,
            DEFAULT_SLIDING_SLIDE_DIRECTION,
        )

        return cls(
            enabled=options.get(
                CONF_SLIDING_ENABLE_SHADE_AREA, DEFAULT_SLIDING_ENABLE_SHADE_AREA
            ),
            slide_direction=options.get(
                CONF_SLIDING_SLIDE_DIRECTION, DEFAULT_SLIDING_SLIDE_DIRECTION
            ),
            window_width=float(options.get(CONF_WINDOW_WIDTH) or 0.0),
            point1_x=float(options.get(CONF_SLIDING_POINT1_X, DEFAULT_SLIDING_POINT_X)),
            point1_y=float(options.get(CONF_SLIDING_POINT1_Y, DEFAULT_SLIDING_POINT_Y)),
            point2_x=float(options.get(CONF_SLIDING_POINT2_X, DEFAULT_SLIDING_POINT_X)),
            point2_y=float(options.get(CONF_SLIDING_POINT2_Y, DEFAULT_SLIDING_POINT_Y)),
        )


@dataclass
class LouveredRoofConfig:
    """Configuration specific to louvered (lamella) roofs (#830).

    A louvered roof rotates slats around a horizontal axis lying in a horizontal
    (or pitched) roof plane — a bioclimatic pergola / "Lamellendach". It reuses
    the venetian slat geometry (depth, spacing, mode — carried in a sibling
    ``TiltConfig``) and adds only the roof-plane pitch.

    ``roof_pitch`` is measured FROM HORIZONTAL: ``0`` = flat roof (the default),
    ``90`` = vertical plane (reduces to the venetian/tilt profile angle exactly).

    ``max_slat_angle`` optionally overrides the tilt mode's 90°/180° ceiling for
    a pergola drive whose mechanical travel is neither (e.g. 0–160°). ``0`` (the
    default sentinel) keeps the mode's max; a nonzero value becomes BOTH the
    clamp ceiling and the tilt%→angle denominator.
    """

    roof_pitch: float = 0.0
    max_slat_angle: float = 0.0

    @classmethod
    def from_options(cls, options: dict) -> LouveredRoofConfig:
        """Build from a config-entry options dict, applying defaults."""
        from .const import (
            CONF_MAX_SLAT_ANGLE,
            CONF_ROOF_PITCH,
            DEFAULT_LOUVERED_ROOF_PITCH,
            DEFAULT_MAX_SLAT_ANGLE,
        )

        pitch = options.get(CONF_ROOF_PITCH)
        max_slat = options.get(CONF_MAX_SLAT_ANGLE)
        return cls(
            roof_pitch=(
                float(pitch)
                if pitch is not None
                else float(DEFAULT_LOUVERED_ROOF_PITCH)
            ),
            max_slat_angle=(
                float(max_slat)
                if max_slat is not None
                else float(DEFAULT_MAX_SLAT_ANGLE)
            ),
        )


@dataclass
class TiltConfig:
    """Configuration specific to tilt/venetian blinds."""

    slat_distance: float
    depth: float
    mode: TiltMode | str
    # Endpoint calibration for the specify-angles mode. Defaults mirror
    # const.DEFAULT_TILT_ANGLE_0/100 (0°..180°, the full raw slat range) so a
    # TiltConfig built without explicit endpoints matches the config path.
    angle_0: float = 0.0
    angle_100: float = 180.0
    max_tilt: int = 100
    min_tilt: int = 0
    # When True, the corresponding tilt limit is only enforced during active sun
    # tracking (mirrors enable_min/max_position). False (default) = always
    # enforce. Issue #503/#629.
    min_tilt_sun_only: bool = False
    max_tilt_sun_only: bool = False
    # Configurable venetian tilt safety margin (issue #783): 0.0 (default) is a
    # provable no-op; 1.0 applies the full angle-dependent geometry margin in the
    # slat-closing direction. Scales the automatic ``SafetyMarginCalculator``
    # factor — see ``engine/covers/tilt.py``.
    safety_margin: float = 0.0


# ---------------------------------------------------------------------------
# Operational runtime config — read once per coordinator update cycle.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VenetianSlice:
    """Venetian-specific runtime options."""

    post_settle_hold_seconds: float
    # How the post-settle wait is performed (issue #801): ``fixed_delay``
    # (default) always sleeps ``post_settle_hold_seconds``; ``entity_state``
    # polls ``cover.state`` and proceeds as soon as the carriage is no longer
    # opening/closing, falling back to the fixed sleep on timeout.
    post_settle_mode: str
    tilt_skip_above: int
    # Above-threshold tilt behaviour (issue #748): ``neutral`` (default) sends a
    # benign POSITION_OPEN tilt to overwrite the actuator cache; ``suppress``
    # emits no tilt at all so coupled-axis covers stay on the open endpoint.
    tilt_skip_mode: str
    venetian_mode: str
    # Accumulated commanded tilt-% change that triggers a mechanical drift
    # reset (issue #663). 0 disables. Consumed by ``DualAxisSequencer`` via a
    # live ``get_tilt_reset_threshold`` lambda threaded through ``attach()``.
    tilt_reset_threshold: int
    # Direction the drift-reset drives the slats before re-sending the target
    # (issue #686): ``open`` (default, back-compat) or ``close``. Consumed by
    # ``DualAxisSequencer`` via a live ``get_tilt_reset_direction`` lambda.
    tilt_reset_direction: str
    # Scope that decides which tilt commands accumulate drift (issue #808):
    # ``all_tilt_commands`` (default, back-compat) counts every tilt send;
    # ``sun_tracking_only`` restricts the accumulator to solar-tracking commands
    # (winning ``ControlMethod == SOLAR``). Consumed by ``VenetianPolicy`` via a
    # live ``get_tilt_reset_scope`` lambda threaded through ``attach()``.
    tilt_reset_scope: str
    # Width (seconds) of the publish-lag suppression window anchored to the
    # cover's ``moving → settled`` transition (issue #33 Phase 5). Used by
    # ``DualAxisSequencer.is_in_suppression_with_cap`` for the tilt axis and
    # by ``VenetianPolicy.primary_axis_suppression`` for the position axis,
    # so slow-bus actuators (Somfy IO via Tahoma, KNX, Fibaro/Shelly republish)
    # whose late ``current_position`` / ``current_tilt_position`` publishes
    # land tens of seconds after physical settle no longer trip false
    # manual-override events.
    backrotate_publish_lag_seconds: float


# Sub-dataclasses group fields by manager so each manager's ``update_config``
# can take a typed slice instead of a fan of kwargs. The slices live below;
# the top-level ``RuntimeConfig`` aggregates them plus the bare flags the
# coordinator itself owns.


@dataclass(frozen=True, slots=True)
class TimeWindowSlice:
    """Inputs for ``TimeWindowManager.update_config``."""

    start_time: Any  # str | dict | None — whatever HA's time selector emits
    start_time_entity: str | None
    end_time: Any
    end_time_entity: str | None
    # Daytime gate (issue #632): a binary-entity list and/or a Jinja condition
    # template that, when configured, OWNS the day/night boundary. Empty / None =
    # unconfigured → astronomical fallback (zero regression).
    gate_sensors: list[str] = field(default_factory=list)
    gate_template: str | None = None
    gate_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE


@dataclass(frozen=True, slots=True)
class MotionSlice:
    """Inputs for ``MotionManager.update_config``."""

    sensors: list[str]
    timeout_seconds: int
    media_players: list[str]
    template: str | None = None
    template_mode: str = DEFAULT_MOTION_TEMPLATE_MODE


@dataclass(frozen=True, slots=True)
class WeatherSlice:
    """Inputs for ``WeatherManager.update_config``."""

    wind_speed_sensor: str | None
    wind_direction_sensor: str | None
    wind_speed_threshold: float
    wind_direction_tolerance: int
    win_azi: int
    rain_sensor: str | None
    rain_threshold: float
    is_raining_sensor: str | None
    is_windy_sensor: str | None
    severe_sensors: list[str]
    timeout_seconds: int
    is_raining_template: str | None = None
    is_raining_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
    is_windy_template: str | None = None
    is_windy_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
    # Master on/off toggle for the whole weather override (issue #719). When
    # False the manager ignores every configured sensor/template. Defaults OFF
    # for new covers; pre-existing covers are migrated to True (v3.5 → v3.6).
    enabled: bool = DEFAULT_WEATHER_ENABLED


@dataclass(frozen=True, slots=True)
class TrackingSlice:
    """Coordinator-side per-cycle thresholds and interpolation series."""

    min_change: int
    time_threshold: int
    position_tolerance: int
    manual_threshold: int | None
    interp_start: Any
    interp_end: Any
    interp_list: Any
    interp_list_new: Any
    # Opt-in sun-tracking movement minimization (quantize to N coverage levels).
    minimize_movements: bool = False
    max_coverage_steps: int = 1
    # When True, the reconciliation pass resends until the cover reaches target.
    # When False (default), command once and let a settle past tolerance become
    # a manual override (issue #591).
    enable_position_matching: bool = False
    # When True, the position/tilt delta gate is also enforced for the 0 and
    # 100 endpoints (issue #679). Default False preserves issue #629's
    # always-send-to-0/100 guarantee.
    enforce_delta_at_endpoints: bool = False
    # When True (default, issue #697), a final target of 100 fires
    # cover.open_cover and 0 fires cover.close_cover on position-capable covers
    # instead of set_cover_position(100/0). Falls back to set_cover_position
    # when the cover lacks open/close; never applies to a tilt-only axis.
    endpoint_use_open_close: bool = True


@dataclass(frozen=True, slots=True)
class ManualOverrideSlice:
    """Manual-override-related runtime fields."""

    reset: bool
    duration: dict
    ignore_external: bool
    # Input binary sensors whose off→on edge engages manual override on every
    # cover in the instance (issue #688). Empty = feature off.
    input_entities: list[str]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """All the option reads currently performed in ``_update_options``.

    Built once per call from a raw options dict. The defaults in
    ``from_options`` are the *only* place each ``DEFAULT_*`` constant is
    referenced for these fields — eliminating the 'parameter defaults are
    constants in disguise' drift risk called out in CODING_GUIDELINES.md.
    """

    entities: list[str]
    open_close_threshold: int
    event_buffer_size: int
    tracking: TrackingSlice
    manual_override: ManualOverrideSlice
    time_window: TimeWindowSlice
    motion: MotionSlice
    weather: WeatherSlice
    venetian: VenetianSlice

    @classmethod
    def from_options(cls, options: dict) -> RuntimeConfig:
        """Read every field once from a raw options dict.

        Constant defaults for each option live in ``const.py`` — referenced
        here, not redeclared, so a single source of truth governs both this
        loader and any other consumer.
        """
        from .const import (
            CONF_AZIMUTH,
            CONF_DAYTIME_GATE_SENSORS,
            CONF_DAYTIME_GATE_TEMPLATE,
            CONF_DAYTIME_GATE_TEMPLATE_MODE,
            CONF_DEBUG_EVENT_BUFFER_SIZE,
            CONF_DELTA_POSITION,
            CONF_DELTA_TIME,
            CONF_ENABLE_POSITION_MATCHING,
            CONF_END_ENTITY,
            CONF_END_TIME,
            CONF_ENDPOINT_USE_OPEN_CLOSE,
            CONF_ENFORCE_DELTA_AT_ENDPOINTS,
            CONF_ENTITIES,
            CONF_INTERP_END,
            CONF_INTERP_LIST,
            CONF_INTERP_LIST_NEW,
            CONF_INTERP_START,
            CONF_MANUAL_IGNORE_EXTERNAL,
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
            CONF_MANUAL_OVERRIDE_RESET,
            CONF_MANUAL_THRESHOLD,
            CONF_MAX_COVERAGE_STEPS,
            CONF_MINIMIZE_MOVEMENTS,
            CONF_MOTION_MEDIA_PLAYERS,
            CONF_MOTION_SENSORS,
            CONF_MOTION_TEMPLATE,
            CONF_MOTION_TEMPLATE_MODE,
            CONF_MOTION_TIMEOUT,
            CONF_OPEN_CLOSE_THRESHOLD,
            CONF_POSITION_TOLERANCE,
            CONF_START_ENTITY,
            CONF_START_TIME,
            CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
            CONF_VENETIAN_MODE,
            CONF_VENETIAN_POST_SETTLE_HOLD,
            CONF_VENETIAN_POST_SETTLE_MODE,
            CONF_VENETIAN_TILT_RESET_DIRECTION,
            CONF_VENETIAN_TILT_RESET_SCOPE,
            CONF_VENETIAN_TILT_RESET_THRESHOLD,
            CONF_VENETIAN_TILT_SKIP_ABOVE,
            CONF_VENETIAN_TILT_SKIP_MODE,
            CONF_WEATHER_ENABLED,
            CONF_WEATHER_IS_RAINING_SENSOR,
            CONF_WEATHER_IS_RAINING_TEMPLATE,
            CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
            CONF_WEATHER_IS_WINDY_SENSOR,
            CONF_WEATHER_IS_WINDY_TEMPLATE,
            CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
            CONF_WEATHER_RAIN_SENSOR,
            CONF_WEATHER_RAIN_THRESHOLD,
            CONF_WEATHER_SEVERE_SENSORS,
            CONF_WEATHER_TIMEOUT,
            CONF_WEATHER_WIND_DIRECTION_SENSOR,
            CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
            CONF_WEATHER_WIND_SPEED_SENSOR,
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
            DEFAULT_ENABLE_POSITION_MATCHING,
            DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
            DEFAULT_ENFORCE_DELTA_AT_ENDPOINTS,
            DEFAULT_MAX_COVERAGE_STEPS,
            DEFAULT_MINIMIZE_MOVEMENTS,
            DEFAULT_MOTION_TIMEOUT,
            DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
            DEFAULT_VENETIAN_MODE,
            DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
            DEFAULT_VENETIAN_POST_SETTLE_MODE,
            DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
            DEFAULT_VENETIAN_TILT_RESET_SCOPE,
            DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
            DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
            DEFAULT_VENETIAN_TILT_SKIP_MODE,
            DEFAULT_WEATHER_ENABLED,
            DEFAULT_WEATHER_RAIN_THRESHOLD,
            DEFAULT_WEATHER_TIMEOUT,
            DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
            DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
            POSITION_TOLERANCE_PERCENT,
        )

        return cls(
            entities=options.get(CONF_ENTITIES, []),
            open_close_threshold=options.get(CONF_OPEN_CLOSE_THRESHOLD, 50),
            event_buffer_size=options.get(
                CONF_DEBUG_EVENT_BUFFER_SIZE, DEFAULT_DEBUG_EVENT_BUFFER_SIZE
            ),
            tracking=TrackingSlice(
                min_change=options.get(CONF_DELTA_POSITION) or 1,
                time_threshold=options.get(CONF_DELTA_TIME) or 2,
                position_tolerance=options.get(CONF_POSITION_TOLERANCE)
                or POSITION_TOLERANCE_PERCENT,
                manual_threshold=options.get(CONF_MANUAL_THRESHOLD),
                interp_start=options.get(CONF_INTERP_START),
                interp_end=options.get(CONF_INTERP_END),
                interp_list=options.get(CONF_INTERP_LIST),
                interp_list_new=options.get(CONF_INTERP_LIST_NEW),
                minimize_movements=options.get(
                    CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS
                ),
                max_coverage_steps=int(
                    options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
                ),
                enable_position_matching=options.get(
                    CONF_ENABLE_POSITION_MATCHING, DEFAULT_ENABLE_POSITION_MATCHING
                ),
                enforce_delta_at_endpoints=options.get(
                    CONF_ENFORCE_DELTA_AT_ENDPOINTS,
                    DEFAULT_ENFORCE_DELTA_AT_ENDPOINTS,
                ),
                endpoint_use_open_close=options.get(
                    CONF_ENDPOINT_USE_OPEN_CLOSE,
                    DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
                ),
            ),
            manual_override=ManualOverrideSlice(
                reset=options.get(CONF_MANUAL_OVERRIDE_RESET, False),
                duration=options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2},
                ignore_external=options.get(CONF_MANUAL_IGNORE_EXTERNAL, False),
                input_entities=options.get(CONF_MANUAL_OVERRIDE_INPUT_ENTITIES, []),
            ),
            time_window=TimeWindowSlice(
                start_time=options.get(CONF_START_TIME),
                start_time_entity=options.get(CONF_START_ENTITY),
                end_time=options.get(CONF_END_TIME),
                end_time_entity=options.get(CONF_END_ENTITY),
                gate_sensors=options.get(CONF_DAYTIME_GATE_SENSORS, []),
                gate_template=options.get(CONF_DAYTIME_GATE_TEMPLATE),
                gate_template_mode=options.get(
                    CONF_DAYTIME_GATE_TEMPLATE_MODE, DEFAULT_TEMPLATE_COMBINE_MODE
                ),
            ),
            motion=MotionSlice(
                sensors=options.get(CONF_MOTION_SENSORS, []),
                timeout_seconds=options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                media_players=options.get(CONF_MOTION_MEDIA_PLAYERS, []),
                template=options.get(CONF_MOTION_TEMPLATE),
                template_mode=options.get(
                    CONF_MOTION_TEMPLATE_MODE, DEFAULT_MOTION_TEMPLATE_MODE
                ),
            ),
            weather=WeatherSlice(
                wind_speed_sensor=options.get(CONF_WEATHER_WIND_SPEED_SENSOR),
                wind_direction_sensor=options.get(CONF_WEATHER_WIND_DIRECTION_SENSOR),
                wind_speed_threshold=_num_or(
                    options.get(
                        CONF_WEATHER_WIND_SPEED_THRESHOLD,
                        DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
                    ),
                    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
                ),
                wind_direction_tolerance=_num_or(
                    options.get(
                        CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
                        DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
                    ),
                    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
                ),
                win_azi=options.get(CONF_AZIMUTH, 180),
                rain_sensor=options.get(CONF_WEATHER_RAIN_SENSOR),
                rain_threshold=_num_or(
                    options.get(
                        CONF_WEATHER_RAIN_THRESHOLD, DEFAULT_WEATHER_RAIN_THRESHOLD
                    ),
                    DEFAULT_WEATHER_RAIN_THRESHOLD,
                ),
                is_raining_sensor=options.get(CONF_WEATHER_IS_RAINING_SENSOR),
                is_windy_sensor=options.get(CONF_WEATHER_IS_WINDY_SENSOR),
                is_raining_template=options.get(CONF_WEATHER_IS_RAINING_TEMPLATE),
                is_raining_template_mode=options.get(
                    CONF_WEATHER_IS_RAINING_TEMPLATE_MODE, DEFAULT_TEMPLATE_COMBINE_MODE
                ),
                is_windy_template=options.get(CONF_WEATHER_IS_WINDY_TEMPLATE),
                is_windy_template_mode=options.get(
                    CONF_WEATHER_IS_WINDY_TEMPLATE_MODE, DEFAULT_TEMPLATE_COMBINE_MODE
                ),
                severe_sensors=options.get(CONF_WEATHER_SEVERE_SENSORS, []),
                timeout_seconds=options.get(
                    CONF_WEATHER_TIMEOUT, DEFAULT_WEATHER_TIMEOUT
                ),
                enabled=options.get(CONF_WEATHER_ENABLED, DEFAULT_WEATHER_ENABLED),
            ),
            venetian=VenetianSlice(
                post_settle_hold_seconds=options.get(
                    CONF_VENETIAN_POST_SETTLE_HOLD,
                    DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
                ),
                post_settle_mode=options.get(
                    CONF_VENETIAN_POST_SETTLE_MODE,
                    DEFAULT_VENETIAN_POST_SETTLE_MODE,
                ),
                tilt_skip_above=options.get(
                    CONF_VENETIAN_TILT_SKIP_ABOVE, DEFAULT_VENETIAN_TILT_SKIP_ABOVE
                ),
                tilt_skip_mode=options.get(
                    CONF_VENETIAN_TILT_SKIP_MODE, DEFAULT_VENETIAN_TILT_SKIP_MODE
                ),
                venetian_mode=options.get(CONF_VENETIAN_MODE, DEFAULT_VENETIAN_MODE),
                tilt_reset_threshold=options.get(
                    CONF_VENETIAN_TILT_RESET_THRESHOLD,
                    DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
                ),
                tilt_reset_direction=options.get(
                    CONF_VENETIAN_TILT_RESET_DIRECTION,
                    DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
                ),
                tilt_reset_scope=options.get(
                    CONF_VENETIAN_TILT_RESET_SCOPE,
                    DEFAULT_VENETIAN_TILT_RESET_SCOPE,
                ),
                backrotate_publish_lag_seconds=options.get(
                    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
                    DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
                ),
            ),
        )
