"""Typed configuration dataclasses for cover calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import TiltMode


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

    @classmethod
    def from_options(cls, options: dict) -> CoverConfig:
        """Build a CoverConfig from a raw options/config dict (CONF_* keys)."""
        from .const import (
            CONF_AZIMUTH,
            CONF_BLIND_SPOT_ELEVATION,
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
            max_pos=options.get(CONF_MAX_POSITION) or 100,
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
            blind_spot_on=options.get(CONF_ENABLE_BLIND_SPOT, False),
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
class TiltConfig:
    """Configuration specific to tilt/venetian blinds."""

    slat_distance: float
    depth: float
    mode: TiltMode | str
    max_tilt: int = 100
    min_tilt: int = 0


# ---------------------------------------------------------------------------
# Operational runtime config — read once per coordinator update cycle.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VenetianSlice:
    """Venetian-specific runtime options."""

    post_settle_hold_seconds: float
    tilt_skip_above: int
    venetian_mode: str
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


@dataclass(frozen=True, slots=True)
class MotionSlice:
    """Inputs for ``MotionManager.update_config``."""

    sensors: list[str]
    timeout_seconds: int


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


@dataclass(frozen=True, slots=True)
class ManualOverrideSlice:
    """Manual-override-related runtime fields."""

    reset: bool
    duration: dict
    ignore_external: bool


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
            CONF_DEBUG_EVENT_BUFFER_SIZE,
            CONF_DELTA_POSITION,
            CONF_DELTA_TIME,
            CONF_END_ENTITY,
            CONF_END_TIME,
            CONF_ENTITIES,
            CONF_INTERP_END,
            CONF_INTERP_LIST,
            CONF_INTERP_LIST_NEW,
            CONF_INTERP_START,
            CONF_MANUAL_IGNORE_EXTERNAL,
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_MANUAL_OVERRIDE_RESET,
            CONF_MANUAL_THRESHOLD,
            CONF_MOTION_SENSORS,
            CONF_MOTION_TIMEOUT,
            CONF_OPEN_CLOSE_THRESHOLD,
            CONF_POSITION_TOLERANCE,
            CONF_START_ENTITY,
            CONF_START_TIME,
            CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
            CONF_VENETIAN_MODE,
            CONF_VENETIAN_POST_SETTLE_HOLD,
            CONF_VENETIAN_TILT_SKIP_ABOVE,
            CONF_WEATHER_IS_RAINING_SENSOR,
            CONF_WEATHER_IS_WINDY_SENSOR,
            CONF_WEATHER_RAIN_SENSOR,
            CONF_WEATHER_RAIN_THRESHOLD,
            CONF_WEATHER_SEVERE_SENSORS,
            CONF_WEATHER_TIMEOUT,
            CONF_WEATHER_WIND_DIRECTION_SENSOR,
            CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
            CONF_WEATHER_WIND_SPEED_SENSOR,
            CONF_WEATHER_WIND_SPEED_THRESHOLD,
            DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
            DEFAULT_MOTION_TIMEOUT,
            DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
            DEFAULT_VENETIAN_MODE,
            DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
            DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
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
            ),
            manual_override=ManualOverrideSlice(
                reset=options.get(CONF_MANUAL_OVERRIDE_RESET, False),
                duration=options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2},
                ignore_external=options.get(CONF_MANUAL_IGNORE_EXTERNAL, False),
            ),
            time_window=TimeWindowSlice(
                start_time=options.get(CONF_START_TIME),
                start_time_entity=options.get(CONF_START_ENTITY),
                end_time=options.get(CONF_END_TIME),
                end_time_entity=options.get(CONF_END_ENTITY),
            ),
            motion=MotionSlice(
                sensors=options.get(CONF_MOTION_SENSORS, []),
                timeout_seconds=options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
            ),
            weather=WeatherSlice(
                wind_speed_sensor=options.get(CONF_WEATHER_WIND_SPEED_SENSOR),
                wind_direction_sensor=options.get(CONF_WEATHER_WIND_DIRECTION_SENSOR),
                wind_speed_threshold=options.get(
                    CONF_WEATHER_WIND_SPEED_THRESHOLD,
                    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
                ),
                wind_direction_tolerance=options.get(
                    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
                    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
                ),
                win_azi=options.get(CONF_AZIMUTH, 180),
                rain_sensor=options.get(CONF_WEATHER_RAIN_SENSOR),
                rain_threshold=options.get(
                    CONF_WEATHER_RAIN_THRESHOLD, DEFAULT_WEATHER_RAIN_THRESHOLD
                ),
                is_raining_sensor=options.get(CONF_WEATHER_IS_RAINING_SENSOR),
                is_windy_sensor=options.get(CONF_WEATHER_IS_WINDY_SENSOR),
                severe_sensors=options.get(CONF_WEATHER_SEVERE_SENSORS, []),
                timeout_seconds=options.get(
                    CONF_WEATHER_TIMEOUT, DEFAULT_WEATHER_TIMEOUT
                ),
            ),
            venetian=VenetianSlice(
                post_settle_hold_seconds=options.get(
                    CONF_VENETIAN_POST_SETTLE_HOLD,
                    DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
                ),
                tilt_skip_above=options.get(
                    CONF_VENETIAN_TILT_SKIP_ABOVE, DEFAULT_VENETIAN_TILT_SKIP_ABOVE
                ),
                venetian_mode=options.get(CONF_VENETIAN_MODE, DEFAULT_VENETIAN_MODE),
                backrotate_publish_lag_seconds=options.get(
                    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
                    DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
                ),
            ),
        )
