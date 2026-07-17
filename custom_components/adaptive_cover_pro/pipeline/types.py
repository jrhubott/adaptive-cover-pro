"""Pipeline data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..const import (
    DEFAULT_TRACKING_SEASONS,
    AxisConstraintMode,
    ClimateStrategy,
    ControlMethod,
    GroupIntentKind,
    GroupScene,
)
from ..reason_i18n import Reason, render_en

if TYPE_CHECKING:
    from ..config_types import CoverConfig, GlareZonesConfig
    from ..cover_types.base import CoverTypePolicy
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..state.climate_provider import ClimateReadings


@dataclass(frozen=True, slots=True)
class GroupIntent:
    """A cover-group's live claim on a member cover (issue #790, Phase 2).

    Pushed by a ``GroupCoordinator`` via the member's ``set_group_intent``;
    the member folds its highest-priority live intent into each snapshot,
    where ``GroupSceneHandler`` / ``GroupLockHandler`` read it. ``scene`` is
    only meaningful for ``kind == SCENE`` — the handler resolves it through
    the member's own policy, never an absolute shared position.
    """

    kind: GroupIntentKind
    scene: GroupScene | None
    priority: int
    group_id: str


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
    summer_close_bypass_sun_floor: bool = False
    cloudy_position: int | None = None
    # Extreme-heat mode (issue #766). ``temp_extreme_heat`` None = feature off.
    # ``extreme_heat_position`` None = use DEFAULT_EXTREME_HEAT_POSITION; an
    # explicit 0 is honored (distinguished with ``is not None``).
    temp_extreme_heat: float | None = None
    extreme_heat_position: int | None = None
    # Seasons in which glare tracking is permitted; defaults to all seasons
    # (unchanged behaviour). See ``ClimateContext.is_tracking_season_blocked``.
    tracking_seasons: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_TRACKING_SEASONS)
    )


@dataclass(frozen=True, slots=True)
class ClimateTempFlags:
    """Smoothed temperature-season crossings from ClimateSmoothingManager (#917).

    Mirrors ``cloud_suppression_active`` but carries the FOUR resolved crossings
    climate mode needs (a single OR-bool cannot represent a multi-way season
    classifier). Threaded onto the snapshot and consumed by ``ClimateCoverData``,
    whose season properties prefer these flags over the raw single-crossing when
    present. ``is_summer`` is composed downstream from ``summer_warm`` AND
    ``outside_high`` — the manager smooths crossings, not seasons.
    """

    winter: bool
    summer_warm: bool
    outside_high: bool
    extreme_heat: bool


def derive_axis_mode(
    *, fixed: int | None, low: int | None, high: int | None
) -> AxisConstraintMode:
    """Resolve one axis's :class:`AxisConstraintMode` from its raw claims.

    Precedence, most to least specific:

    * a floor (``low``) present: it *pairs* with a ceiling into ``RANGE``, else
      it is a ``MIN``. A floor always wins over a bare exact value because a
      slot that names a floor (``min_mode``) stores its position *as* the floor.
    * an exact value (``fixed``): ``FIXED``. This **outranks a lone ceiling** —
      a slot with an explicit position keeps its fixed claim, and a
      ``position_max`` is honored only alongside ``min_mode`` (as the ceiling of
      a ``RANGE``). This mirrors the tilt axis, where a FIXED (``tilt_only``)
      claim has always won over the bounds on the same axis, and keeps the
      config summary's ``→ 70%`` honest instead of quietly capping it (audit
      finding 5).
    * a lone ceiling (``high``): ``MAX``.
    * nothing: ``NONE``.

    Every pre-#943 config yields ``FIXED`` / ``MIN`` / ``NONE`` only, so their
    outcomes are unchanged. The single source of the precedence, for both axes;
    callers pass the claims already normalized for cross-axis conflicts (see
    :attr:`CustomPositionSensorState.position_mode`).
    """
    if low is not None and high is not None:
        return AxisConstraintMode.RANGE
    if low is not None:
        return AxisConstraintMode.MIN
    if fixed is not None:
        return AxisConstraintMode.FIXED
    if high is not None:
        return AxisConstraintMode.MAX
    return AxisConstraintMode.NONE


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
    # The slot's position claim, in pre-inversion canonical space. ``None`` =
    # the slot makes no position claim — a constraint-only slot (e.g. trigger →
    # minimum tilt) added by issue #943. 0 is a valid position (fully closed),
    # so consumers must test ``is None``, never truthiness.
    position: int | None
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
    # Optional user-configured label for this slot (issue #867). When set,
    # overrides sensor_name everywhere the slot's label is surfaced (reason
    # string, decision_trace attribute, floor/tilt-axis traces, card
    # snapshot). None = no name configured (default; byte-identical to
    # pre-#867 behavior).
    custom_name: str | None = None

    # --- Axis constraints (issue #943) -------------------------------------
    # Optional per-axis bounds that clamp whatever the pipeline resolves while
    # this slot's trigger is active. None = the bound is off. Values are in the
    # same pre-inversion canonical space as ``position`` / ``tilt``.
    #
    # ``position_max`` is normalized off on the ``use_my`` path (hardware-pinned)
    # and by ``tilt_only``; ``tilt_min`` / ``tilt_max`` are normalized off by
    # ``tilt_only`` (a FIXED tilt claim wins over bounds on the same axis).
    position_max: int | None = None
    tilt_min: int | None = None
    tilt_max: int | None = None

    @property
    def position_mode(self) -> AxisConstraintMode:
        """This slot's derived claim on the position axis (issue #943).

        A *property*, not a stored field: the mode is a pure function of the
        wire format (``min_mode`` / ``tilt_only`` + the numeric keys), and the
        wire format is what rollback safety pins. Deriving here rather than in
        the snapshot builder means every construction path — the builder, the
        card, and every test that builds a state by hand — agrees, with no way
        for a stored copy to drift from the flags it was derived from.

        ``tilt_only`` wins the whole slot: it fixes the slat angle and lets the
        position pipeline drive the carriage, so it claims nothing here.
        """
        if self.tilt_only:
            return AxisConstraintMode.NONE
        return derive_axis_mode(
            fixed=None if self.min_mode else self.position,
            low=self.position if self.min_mode else None,
            high=self.position_max,
        )

    @property
    def tilt_mode(self) -> AxisConstraintMode:
        """This slot's derived claim on the tilt axis (issue #943).

        ``tilt_only`` is an exact (``FIXED``) tilt claim and wins over the
        bounds — mirroring the precedence it already has over ``min_mode`` /
        ``use_my``. A tilt-only slot with no configured slat angle claims
        nothing (pre-#943 behavior, preserved).
        """
        if self.tilt_only:
            return (
                AxisConstraintMode.FIXED
                if self.tilt is not None
                else AxisConstraintMode.NONE
            )
        return derive_axis_mode(fixed=None, low=self.tilt_min, high=self.tilt_max)

    @property
    def slot_name(self) -> str | None:
        """Label for the card/decision-trace attribute — None-able.

        The configured ``custom_name`` wins when set; otherwise falls back to
        ``sensor_name`` (today's behavior — None for an unnamed template-only
        slot, preserving the exact pre-#867 attribute value).
        """
        return self.custom_name or self.sensor_name

    @property
    def display_label(self) -> str:
        """Always-a-string label for trace lines (floors.py / tilt_axis.py).

        Falls back to the first bound entity_id, then the literal
        ``"template"`` when no sensor is bound — the single source of truth
        for the label expression previously duplicated across call sites.
        """
        return self.slot_name or (self.entity_ids[0] if self.entity_ids else "template")


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

    # Global tilt clamps (issue #503). The DefaultHandler clamps its non-sunset
    # default_tilt to [min_tilt, max_tilt]; sunset_tilt and custom-position tilt
    # are deliberate carve-outs and are never clamped. The *_sun_only toggles
    # mirror enable_min/max_position: False (default) = always enforce, True =
    # only during sun tracking. Defaults are no-ops (0 / 100 / False) so
    # snapshots that don't set them behave exactly as before.
    min_tilt: int = 0
    max_tilt: int = 100
    min_tilt_sun_only: bool = False
    max_tilt_sun_only: bool = False

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

    # The highest-priority live cover-group intent targeting this member, or
    # None when no group claims it (issue #790, Phase 2). Read by
    # GroupSceneHandler / GroupLockHandler; absence of an intent IS
    # non-membership — the handlers defer without any membership lookup.
    group_intent: GroupIntent | None = None

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

    # Anticipatory-solar look-ahead horizon, in minutes (issue #616). Equals
    # CONF_DELTA_TIME — the "Minimum interval between position changes" the
    # send-gate throttles on. When > 0 the solar branch
    # (:func:`pipeline.helpers.anticipated_solar_position`) samples future sun
    # positions across ``(now, now + time_threshold_minutes]`` and commands the
    # most-protective one, so coverage holds until the next allowed move. ``0``
    # disables anticipation (identical-to-today live solar behaviour) and keeps
    # the no-hass snapshot paths safe. Defaults to ``0`` so snapshots that don't
    # set it behave exactly as before.
    time_threshold_minutes: int = 0

    # Resolved cloud-suppression decision from CloudSuppressionManager (issue
    # #864). Mirrors ``weather_override_active``: the manager owns the hysteresis
    # latch + hold-time debounce and hands the pure handler a single bool. The
    # CloudSuppressionHandler gates on this AFTER its FOV / time-window guards,
    # so the manager never keeps suppression asserted once the sun leaves the
    # window FOV (#417). Defaults False so snapshots that don't set it (and older
    # installs with the smoothing feature absent) behave exactly as before.
    cloud_suppression_active: bool = False

    # Smoothed temperature-season crossings from ClimateSmoothingManager (issue
    # #917). None = smoothing off / not threaded → ClimateCoverData falls back to
    # the raw single-crossing, so pre-#917 installs and every direct-constructor
    # test are byte-identical. When present, each flag wins over the raw
    # comparison for its crossing.
    climate_temp_flags: ClimateTempFlags | None = None


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionStep:
    """Record of one handler's evaluation."""

    handler: str
    matched: bool
    # Canonical English reason string. When ``reason_payload`` is provided and
    # ``reason`` is left empty, ``__post_init__`` derives ``reason`` from it via
    # ``render_en`` so the byte-identical EN prose is always available; passing
    # an explicit ``reason`` (the legacy path) keeps it verbatim.
    reason: str = ""
    position: int | None = None
    tilt: int | None = None
    # Evaluation priority of the handler that produced this step (higher wins).
    # Surfaced in diagnostics so a re-ordered chain is visible for debugging.
    # None for synthetic steps (e.g. floor_clamp) that aren't a real handler.
    priority: int | None = None
    # Physical position the cover is held at during a manual override step.
    # Set by PipelineRegistry only for the manual_override winning step
    # (propagated from PipelineResult.held_position). None for all other
    # handlers and all other steps. Consumers must use explicit is-not-None
    # checks because 0% (fully closed) is a valid held position.
    held_position: int | None = None
    # Stable reason code + params (issue #882). Localized by the Lovelace card
    # and used to render the ``reason`` string above in the user's language.
    # None on legacy steps that still carry only an English ``reason`` string.
    reason_payload: Reason | None = None

    def __post_init__(self) -> None:
        """Derive the English ``reason`` from ``reason_payload`` when unset."""
        if self.reason_payload is not None and not self.reason:
            object.__setattr__(self, "reason", render_en(self.reason_payload))


@dataclass(frozen=True)
class PipelineResult:
    """Output of the override pipeline."""

    position: int
    control_method: ControlMethod
    # Canonical English reason string. When ``reason_payload`` is provided and
    # ``reason`` is left empty, ``__post_init__`` derives ``reason`` from it via
    # ``render_en``; an explicit ``reason`` (the legacy path) is kept verbatim.
    reason: str = ""
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

    # When True, the registry's axis-constraint composition pass clamped this
    # winner's position to a user-configured bound — a floor raise (issue #463)
    # or, since issue #943, a ceiling lower. The coordinator's `state` property
    # treats the position as already in cover-position space and skips
    # interpolation / inverse-state remapping (issue #469); that holds for
    # either bound, since both are values the user typed in cover space.
    floor_clamp_applied: bool = False

    # Composed tilt bounds that could not be applied during evaluation because
    # the winner had no tilt to clamp yet (issue #943). Tilt can resolve *after*
    # the pipeline — the venetian engine fills it in ``post_pipeline_resolve`` —
    # so the bounds ride the result and that policy applies them via the shared
    # ``axis_constraints.clamp_to_bounds``. None = unbounded on that side. When
    # the registry could clamp the tilt itself, it already did and these stay
    # None. ``tilt_bound_label`` names the slot(s) the bounds came from so the
    # deferred clamp's trace step reads the same as an in-registry one.
    tilt_low: int | None = None
    tilt_high: int | None = None
    tilt_bound_label: str | None = None

    # When True, the registry's tilt-axis pass overlaid a per-slot tilt-only
    # contribution onto this winner (issue #514). VenetianPolicy reads this in
    # post_pipeline_resolve to suppress the global VENETIAN_MODE_TILT_ONLY
    # carriage-close for the cycle so the position pipeline genuinely drives
    # the carriage. Cover-type-agnostic — set by the registry, acted on only
    # inside cover_types/.
    tilt_only_contribution_active: bool = False

    # 1-based slot number of the tilt-only contribution that was *applied*
    # (overlaid its slat angle onto the position winner). Set by the registry
    # only when the overlay actually took effect (winner's own tilt was None);
    # None when no tilt-only slot fired or when it was deferred because the
    # winner already set tilt. Surfaced in the Control Status string (#667).
    tilt_only_slot: int | None = None

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

    # Stable reason code + params (issue #882). The winning handler's structured
    # reason; the registry propagates it onto the winner's DecisionStep so the
    # Lovelace card can localize it. None on legacy results that still carry only
    # an English ``reason`` string (handlers migrate in later dispatches).
    reason_payload: Reason | None = None

    def __post_init__(self) -> None:
        """Derive the English ``reason`` from ``reason_payload`` when unset."""
        if self.reason_payload is not None and not self.reason:
            object.__setattr__(self, "reason", render_en(self.reason_payload))
