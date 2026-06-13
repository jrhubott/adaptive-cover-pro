"""Pipeline data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..const import ClimateStrategy, ControlMethod

if TYPE_CHECKING:
    from ..config_types import CoverConfig, GlareZonesConfig
    from ..cover_types.base import CoverTypePolicy
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..state.climate_provider import ClimateReadings


# ---------------------------------------------------------------------------
# New snapshot — raw state for self-contained plugin handlers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClimateOptions:
    """Climate configuration thresholds for the ClimateHandler."""

    temp_low: float | None
    temp_high: float | None
    temp_switch: bool  # True = use outside temp; False = use inside temp
    transparent_blind: bool
    temp_summer_outside: float | None
    cloud_suppression_enabled: bool
    winter_close_insulation: bool
    cloudy_position: int | None = None


@dataclass(frozen=True, slots=True)
class CustomPositionSensorState:
    """Per-slot trigger reading carried in the pipeline snapshot.

    One instance per configured custom position slot.  Built once per update
    cycle by ``SnapshotBuilder.read_custom_position_sensors()`` and consumed
    by the matching ``CustomPositionHandler`` instance via slot lookup.
    """

    # All trigger sensors bound to the slot (OR logic, issue #563). May be
    # empty for a template-only slot.
    entity_ids: tuple[str, ...]
    # Slot activation: OR across the sensors, folded with the optional
    # condition template via templates.combine_with_mode() at snapshot time.
    is_on: bool
    position: int
    priority: int
    min_mode: bool
    use_my: bool
    tilt: int | None = None
    # When True, the slot fixes only the slat angle (tilt) and does NOT claim
    # the position axis (issue #514). The handler defers (returns None) from
    # evaluate(); the registry's tilt-axis pass overlays this slot's tilt onto
    # whichever handler wins position. Mutually exclusive with min_mode / use_my
    # (normalized in snapshot_builder — tilt_only wins).
    tilt_only: bool = False
    # Human label of the first active (else first) bound sensor (its
    # friendly_name attribute), surfaced so downstream diagnostics can show
    # e.g. "Custom · Table extension" instead of just "Custom #1". None when
    # no sensor is loaded / has a friendly_name (e.g. template-only slot).
    sensor_name: str | None = None
    # Real 1-5 slot number this state was built from. The snapshot's sensor list
    # is compacted (gaps skipped), so the list index does NOT recover the slot;
    # carry it explicitly so the floor trace can label the correct
    # custom_position_N handler (issue #496). 0 = unset.
    slot: int = 0
    # Sensors currently "on" — drives reason strings (mirrors the old force
    # override's multi-sensor reason format).
    active_entity_ids: tuple[str, ...] = ()
    # Rendered condition-template result. None = no template configured.
    template_active: bool | None = None


@dataclass(frozen=True)
class PipelineSnapshot:
    """Raw state passed to all pipeline handlers.

    Handlers read from this snapshot, compute their own conditions, and
    compute their own positions. No pre-computed decisions live here.
    """

    # Shared calculation engine (sun geometry + cover position math)
    cover: AdaptiveGeneralCover

    # Cover configuration
    config: CoverConfig
    cover_type: str  # "cover_blind" / "cover_awning" / "cover_tilt"

    # Effective default position — the single source of truth for all handlers.
    # Computed by compute_effective_default() before the pipeline runs:
    #   - equals sunset_pos when current time is in the astronomical sunset window
    #   - equals h_def at all other times
    # Handlers MUST use this field; accessing snapshot.cover.default is incorrect
    # and will raise AttributeError (the property has been intentionally removed).
    #
    # NOTE: The raw config values (h_def, sunset_pos) are intentionally NOT
    # exposed on this snapshot.  There is no way for a handler to reconstruct
    # a different default without going through compute_effective_default().
    # The raw values are only available on PipelineResult (written by the
    # coordinator *after* evaluation) so they appear in diagnostics without
    # being visible to handler logic.
    default_position: int

    # True when default_position == sunset_pos (astronomical sunset window active).
    # Handlers may read this to label reason strings; they must not use it to
    # derive a different position.
    is_sunset_active: bool

    # Climate readings (raw sensor values — None if not configured)
    climate_readings: ClimateReadings | None
    climate_mode_enabled: bool
    climate_options: ClimateOptions | None

    # Manager states (inherently stateful; managers track across update cycles)
    manual_override_active: bool
    motion_timeout_active: bool

    # Weather override state (from WeatherManager)
    weather_override_active: bool
    weather_override_position: int

    # Glare zones (vertical covers only — None for awning/tilt)
    glare_zones: GlareZonesConfig | None
    active_zone_names: frozenset[str]

    # When True (default), weather override sends commands even if automatic_control is OFF.
    # Users can disable this if they want weather override to respect the auto-control toggle.
    weather_bypass_auto_control: bool = True

    # When False, sun-tracking is disabled (CONF_ENABLE_SUN_TRACKING=False).
    # compute_raw_calculated_position() must skip the solar branch so that
    # min-mode floors are measured against what the pipeline would actually
    # command (the default position), not a solar geometry result that will
    # never be applied.  Defaults to True for backward compatibility (#264).
    enable_sun_tracking: bool = True

    # Minimum position mode: when True, the configured position acts as a floor —
    # the handler returns max(configured, raw_calculated) instead of always returning configured.
    weather_override_min_mode: bool = False

    # True when current time is within the configured start/end operational window.
    # Handlers that should only run during the active window (e.g. SolarHandler,
    # GlareZoneHandler) check this field and return None when it is False.
    # Defaults to True so that handlers which don't check it are unaffected and
    # existing tests that construct PipelineSnapshot without this field continue
    # to pass.
    in_time_window: bool = True

    # True when the Motion Control switch is enabled.  MotionTimeoutHandler
    # checks this field and passes through (returns None) when it is False,
    # allowing lower-priority handlers to run as if motion timeout is inactive.
    # Defaults to True for backward compatibility.
    motion_control_enabled: bool = True

    # Custom position sensor states — one CustomPositionSensorState per configured
    # slot.  The pipeline creates a separate CustomPositionHandler instance per
    # slot, each carrying its own priority, so the PipelineRegistry sorts them
    # correctly relative to all other handlers.  The handler matches its sensor
    # by looking up entity_id in this list.
    # Defaults to empty list (feature disabled / not configured).
    custom_position_sensors: list[CustomPositionSensorState] = field(
        default_factory=list
    )

    # Somfy "My" position support.
    # my_position_value: the position (1–99 %) the user programmed on the motor remote.
    #   None = feature disabled for this cover.
    # sunset_use_my: when True, the sunset/end_time return path triggers My instead of
    #   the normal open/close threshold fallback (for non-position-capable covers).
    my_position_value: int | None = None
    sunset_use_my: bool = False

    # Explicit tilt for venetian covers. None = use solar-computed tilt.
    default_tilt: int | None = None  # tilt when no active handler fires
    sunset_tilt: int | None = (
        None  # tilt during sunset window; falls back to default_tilt
    )

    # Motion timeout mode:
    #   "return_to_default" (default) — handler sends the configured default position
    #   "hold_position" — handler emits skip_command=True so the cover stays put while
    #     the sun is active; falls through to default when sun leaves FOV or window closes.
    motion_timeout_mode: str = "return_to_default"

    # Mean of current entity positions (int-rounded). None when no entity reports a
    # numeric position. Read by MotionTimeoutHandler in hold_position mode only.
    current_cover_position: int | None = None

    # The CoverTypePolicy chosen at coordinator startup. Handlers should consult
    # this for cover-type-aware decisions (axis routing, intent → position
    # mapping, glare-zone gating) instead of branching on ``cover_type``.
    # Defaults to ``None`` so test fixtures that build snapshots directly keep
    # working; runtime always populates it via ``coordinator._build_snapshot``.
    policy: CoverTypePolicy | None = None

    # Sun-tracking movement minimization (opt-in). When True, the solar branch
    # quantizes the calculated position into ``max_coverage_steps`` evenly-spaced
    # coverage levels, rounding toward full coverage so protection is never
    # reduced. ``max_coverage_steps == 1`` snaps straight to full coverage while
    # the sun is in the FOV. Defaults preserve the un-quantized behavior.
    minimize_movements: bool = False
    max_coverage_steps: int = 1

    # Whether the sun-tracking 1 % floor applies this cycle (issue #569). The
    # solar branch and the glare-zone handler floor the geometric position at
    # ``SOLAR_TRACKING_FLOOR_PCT`` so open/close-only covers never fully retract
    # while the sun is in the FOV. The snapshot builder sets this False only
    # when *every* bound entity supports set_position (conservative
    # mixed-instance rollup) so positionable covers reach a true 0 %. Defaults
    # to True so the floor stays in effect for snapshots that don't set it.
    solar_floor_active: bool = True


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionStep:
    """Record of one handler's evaluation."""

    handler: str
    matched: bool
    reason: str
    position: int | None
    tilt: int | None = None


@dataclass(frozen=True)
class PipelineResult:
    """Output of the override pipeline."""

    position: int
    control_method: ControlMethod
    reason: str
    decision_trace: list[DecisionStep] = field(default_factory=list)
    tilt: int | None = None

    # Raw geometric position before post-processing (interpolation/inverse_state).
    # Set by SolarHandler when direct sun is valid, otherwise equals the effective
    # default position.  Used by diagnostics to show the pure calculation result.
    raw_calculated_position: int = 0

    # Sunset context — written by the coordinator via dataclasses.replace() after
    # pipeline evaluation, NOT sourced from the handler snapshot.  This keeps
    # the raw config values out of handler logic while still surfacing them in
    # diagnostics and the Decision Trace sensor.
    default_position: int = 0
    is_sunset_active: bool = False
    configured_default: int = 0  # raw h_def from user config
    configured_sunset_pos: int | None = None  # raw sunset_pos (None = not configured)
    configured_cloudy_pos: int | None = (
        None  # raw cloudy_position (None = not configured)
    )

    # Optional climate diagnostics set by ClimateHandler
    climate_state: int | None = None
    climate_strategy: ClimateStrategy | None = None
    climate_data: Any = None  # ClimateCoverData | None — avoids circular import

    # When True, this result is applied even when automatic_control is OFF.
    # Set by safety/override handlers (WeatherOverrideHandler,
    # CustomPositionHandler) so that wind/rain/forced protection still works
    # when the user has paused normal sun-tracking automation.
    bypass_auto_control: bool = False

    # When True, this result carries full safety semantics: the coordinator
    # sends it outside the start/end time window and bypasses the
    # delta-position/delta-time gates. Set by WeatherOverrideHandler and by
    # CustomPositionHandler when the slot's priority is at or above
    # CUSTOM_POSITION_SAFETY_PRIORITY (100) — the migrated force-override
    # behavior (issue #563).
    is_safety: bool = False

    # When True, the registry's floor-clamp composition pass raised this
    # winner's position to a user-configured floor. The coordinator's `state`
    # property treats the position as already in cover-position space and
    # skips interpolation / inverse-state remapping (issue #469).
    floor_clamp_applied: bool = False

    # When True, the registry's tilt-axis pass overlaid a per-slot tilt-only
    # contribution onto this winner (issue #514). VenetianPolicy reads this in
    # post_pipeline_resolve to suppress the global VENETIAN_MODE_TILT_ONLY
    # carriage-close for the cycle so the position pipeline genuinely drives
    # the carriage. Cover-type-agnostic — set by the registry, acted on only
    # inside cover_types/.
    tilt_only_contribution_active: bool = False

    # When True, the coordinator should route this command through
    # CoverCommandService.send_my_position() on non-position-capable covers
    # (cover.stop_cover while stationary → triggers the Somfy "My" hardware preset).
    # Position-capable covers gracefully fall through to set_cover_position(position).
    use_my_position: bool = False

    # When True, the coordinator must NOT issue a cover command this cycle.
    # Used by hold-mode handlers (e.g. MotionTimeoutHandler with hold_position) to
    # record the decision in diagnostics while leaving the cover physically untouched.
    skip_command: bool = False

    # Physical position the cover is currently held at during manual override.
    # Set by ManualOverrideHandler to snapshot.current_cover_position so that
    # the "Target Position" sensor shows where the cover actually sits rather
    # than the solar-handler value the override is shadowing.
    # None when override is inactive, when current position is unknown, or for
    # all other handlers.  Consumers must use explicit `is not None` checks
    # because 0% (fully closed) is a valid held position.
    held_position: int | None = None

    # Custom position slot diagnostics — populated only when CustomPositionHandler wins.
    # custom_position_active_slot: 1-based slot number of the winning custom position handler; None otherwise.
    # custom_position_minimum_mode: True when min_mode=True and the floor raises position above raw (floor is
    #   actively constraining); False when min_mode=True and raw >= configured floor (floor is a
    #   no-op); None when min_mode=False (exact mode) or on the use_my path, or when any
    #   non-custom handler wins.
    custom_position_active_slot: int | None = None
    custom_position_minimum_mode: bool | None = None
    # Human label of the winning slot's bound sensor (its friendly_name).
    # None when the sensor isn't loaded, has no friendly_name, or when any
    # non-custom handler wins.
    custom_position_active_slot_name: str | None = None
