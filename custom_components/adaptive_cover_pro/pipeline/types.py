"""Pipeline data types."""

from __future__ import annotations

from collections.abc import Mapping
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


def has_fixed_tilt(*, tilt_only: bool, tilt: int | None) -> bool:
    """Whether a slot's ``tilt_only`` flag names an actual FIXED tilt claim.

    ``tilt_only`` conflates two different claims: it *always* disclaims the
    position axis (issue #514 — see :attr:`CustomPositionSensorState.position_mode`,
    which keys on the bare flag and is unaffected by this predicate), but it
    only outranks ``tilt_min``/``tilt_max`` bounds on the tilt axis when it
    also names a slat angle. ``tilt_only=True`` with ``tilt=None`` is a
    *vacuous* FIXED claim — before issue #1215 that vacuous claim still won
    the precedence, silently discarding a configured ``tilt_min``/``tilt_max``
    and leaving the slot completely inert. Callers that need the FIXED-vs-
    bounds precedence (:attr:`CustomPositionSensorState.tilt_mode`, the
    snapshot-builder tilt-bound wipe, and the config-summary rendering
    branch) must test this predicate, not the bare ``tilt_only`` flag.
    """
    return tilt_only and tilt is not None


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
    # Two independent holds can substitute a held value here instead of this
    # cycle's fresh fold result: the whole-slot hold (issue #1005), engaged
    # only when EVERY input is invalid this cycle (``is_valid`` False); and
    # the time-bounded per-input hold (issue #1012), which runs on EITHER
    # input regardless of ``is_valid`` — most visibly while ``is_valid``
    # stays True (the other input is still speaking), but it also runs when
    # both inputs are invalid, in which case the whole-slot hold then
    # overwrites whatever it produced. The per-input window is measured from
    # that input's own FIRST INVALID SIGHTING (cadence-independent — a gap of
    # any length between the input's last-valid reading and when it was first
    # observed bad does not itself eat into the window), for up to
    # CUSTOM_POSITION_INPUT_HOLD_SECONDS past that first invalid sighting.
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
    # This cycle's template opinion — normally the fresh render, but the
    # time-bounded per-input hold (issue #1012) can substitute the template's
    # own last-valid opinion here instead when the template alone failed to
    # render this cycle (for up to CUSTOM_POSITION_INPUT_HOLD_SECONDS). None =
    # no template configured (never held).
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
    # and by ``tilt_only``; ``tilt_min`` / ``tilt_max`` are normalized off only
    # when the slot names an actual fixed slat angle (``has_fixed_tilt``) — a
    # real FIXED tilt claim wins over bounds on the same axis. A bare
    # ``tilt_only`` flag with no configured slat angle is a vacuous claim and
    # leaves ``tilt_min`` / ``tilt_max`` intact (issue #1215).
    position_max: int | None = None
    tilt_min: int | None = None
    tilt_max: int | None = None

    # Whether this cycle's read was usable (issue #1005). True when at least one
    # bound sensor reported a non-invalid state (not unavailable/unknown/missing)
    # OR a condition template rendered an opinion. False = NEITHER input spoke
    # this cycle, in which case ``is_on`` / ``active_entity_ids`` are HELD to the
    # last valid combined read (whole-slot hold, no time bound) so a transient
    # blip does not fire a false release edge. ``True`` does NOT mean every
    # field below is fresh: the time-bounded per-input hold (issue #1012) can
    # still be substituting one input's (sensor or template) own last-valid
    # contribution into ``is_on`` while the *other* input keeps ``is_valid``
    # True this cycle. Default True so every pre-#1005 construction path stays
    # a valid read.
    is_valid: bool = True

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
        Deliberately keyed on the bare flag, unlike :attr:`tilt_mode` (issue
        #1215) — a tilt-only slot disclaims the position axis whether or not
        it also names a slat angle, so this must NOT route through
        :func:`has_fixed_tilt`.
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

        A real FIXED tilt claim (``tilt_only`` *with* a configured slat
        angle) wins over the bounds — mirroring the precedence it already has
        over ``min_mode`` / ``use_my``. A tilt-only slot with no configured
        slat angle makes no FIXED claim (issue #1215): it falls through to
        the same bound derivation as any other slot, so a constraint-only
        "tilt_only + tilt_min" slot still emits its floor instead of going
        silently inert.
        """
        if has_fixed_tilt(tilt_only=self.tilt_only, tilt=self.tilt):
            return AxisConstraintMode.FIXED
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

    # When False, sun-tracking is not live this cycle — either the master
    # toggle is off (CONF_ENABLE_SUN_TRACKING=False) or the sun-tracking gate
    # read closed (issue #1167). compute_raw_calculated_position() must skip the
    # solar branch so that min-mode floors are measured against what the
    # pipeline would actually command (the default position), not a solar
    # geometry result that will never be applied.  Defaults to True for
    # backward compatibility (#264).
    enable_sun_tracking: bool = True
    # Which of the two closed it, so the decision trace can say so. True ONLY
    # when the master toggle is on and a configured gate resolved false —
    # otherwise a user who simply switched sun tracking off would be told a gate
    # they never configured is closed (issue #1167 audit).
    sun_tracking_gate_closed: bool = False

    # Minimum position mode: when True, the configured position acts as a floor —
    # the handler returns max(configured, raw_calculated) instead of always returning configured.
    weather_override_min_mode: bool = False

    # WeatherOverrideHandler's EFFECTIVE priority, resolved from the 🔀 Handler
    # Priorities step at snapshot build. Carried on the snapshot because the
    # weather floor is composed by the pure `axis_constraints` pass, which has no
    # options to resolve from — and since #1170 that priority decides whether the
    # floor may move a position a handler is holding, so the class default is no
    # longer good enough. None = unresolved; the gather falls back to the class
    # default (test snapshots that predate this field).
    weather_override_priority: int | None = None

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

    # Summary of the current entity positions: their int-rounded mean. None when
    # no entity reports a numeric position. Read by MotionTimeoutHandler in
    # hold_position mode, and copied into ``PipelineResult.held_position`` by the
    # manual-override / group-lock holds as a presentation value. It is a
    # *summary*: no per-cover decision consumes it — the registry judges each
    # held cover against its own entry in ``cover_positions`` below (#1174).
    # This is a RAW cover-frame read — see position_axis_inverted below.
    current_cover_position: int | None = None

    # Per-entity RAW cover-frame positions — the same frame and the same source
    # as ``current_cover_position``, which is the summary mean of these. The
    # registry judges a hold's per-cover clamp verdicts against this dict
    # (#1174) — but only for a cover type whose entities move independently; a
    # coupled type's covers are judged as one, off the summary scalar. ``None``
    # in legacy / test snapshots that predate the field: the registry then
    # judges holds on the summary scalar exactly as before.
    cover_positions: Mapping[str, int | None] | None = None

    # Whether the position axis is effectively inverted for this install
    # (inverse-state configured and not suppressed by interpolation, per
    # ``cover_types.base.axis_inverted``). A handler that puts a raw cover read
    # such as ``current_cover_position`` into ``PipelineResult.position`` must
    # convert it to the logical frame first (``position_utils.flip_if``),
    # because ``coordinator.state`` maps every winner through
    # ``_to_cover_frame`` on the way out — no flag exempts one (#1028 / #1036).
    position_axis_inverted: bool = False

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


@dataclass(frozen=True, slots=True)
class HoldClampVerdict:
    """What happens to ONE held cover this cycle — moved, and where to (#1174).

    A hold winner keeps every bound cover where something authoritative already
    put it. Whether that stops being true, and what replaces it, is a question
    per cover and not per instance: ``PipelineResult.held_position`` is the
    group's arithmetic MEAN, so judging it dragged compliant covers to a bound
    they already satisfied and hid a lone violator behind its siblings.

    The two fields answer the two halves of the dispatch question, and no single
    instance-wide number can answer either:

    * a floor and a ceiling that both outrank the holder bind *different* covers
      in *opposite* directions, so the released covers do not share one target;
    * a TILT clamp commands every held cover while the position axis released
      nobody, so "is a command going out" is not "did a bound move me".

    Only built for a cover type whose entities move independently
    (``CoverTypePolicy.entities_move_independently``). That is what makes
    ``target`` safe to hand straight to ``coordinator._entity_target``: a policy
    that would remap it per entity, or rewrite it after the pipeline, produces
    no verdicts at all and keeps its shared-target path — including when it
    names its own position through ``CoverTypePolicy.hold_reference_position``
    (#1179). A coupled type's clamped value is ONE abstract position, and it has
    to ride ``PipelineResult.position`` so ``post_pipeline_resolve`` and
    ``resolve_entity_target`` expand it exactly once; a verdict would bypass the
    first and double-apply the second.
    """

    #: This cover's own position, a RAW cover-frame read (matching the frame
    #: ``PipelineSnapshot.cover_positions`` and the coordinator's held-position
    #: diagnostic extras speak, #1028). Falls back to the summary
    #: ``PipelineResult.held_position`` when this cover reports no position.
    held_position: int | None
    #: True when a command goes out to this cover this cycle: a composed
    #: position bound moved it, or another axis forced a dispatch the whole
    #: group has to carry. False means the hold stands and the coordinator
    #: writes a hold-skip record instead.
    released: bool
    #: Where that command sends this cover, as a LOGICAL (pre-inversion,
    #: pre-interpolation) position — the same frame ``PipelineResult.position``
    #: speaks, so the coordinator maps it through the one ``_to_cover_frame``
    #: seam. Equals the bound edge that bound THIS cover when a position bound
    #: moved it, and this cover's own position when none did — which is what
    #: makes a tilt-forced command a positional no-op. Always resolved, and
    #: read only while ``released``.
    target: int


@dataclass(frozen=True, slots=True)
class PositionAxisJudgment:
    """What the composed position bounds did to this cycle's winner (#1174).

    One return value for :func:`pipeline.registry._judge_position_axis`, which
    answers "where does the winner actually end up, did a bound move it, and —
    for a hold — what happens to each cover individually" in a single pass so
    those answers cannot drift apart.
    """

    #: The position the bounds were judged against, LOGICAL frame. A computed
    #: winner's own ``position``; for a hold, the *violating* cover's position
    #: (lowest when a floor raised, highest when a ceiling lowered) so the
    #: clamp trace step reads as a real cover rather than a mean nobody sits at.
    #: For a coupled hold that judged nothing, the policy's own reference (#1179)
    #: — the value ``_release_hold_for_tilt_clamp`` carries when the other axis
    #: forces a dispatch.
    effective_winner_pos: int
    #: ``effective_winner_pos`` after the composed bounds, i.e. the shared
    #: clamp target the trace and ``PipelineResult.position`` carry.
    final_pos: int
    #: A floor lifted at least one judged position.
    raised: bool
    #: A ceiling lowered at least one judged position.
    lowered: bool
    #: The LOWEST judged position when ``raised``, else ``None`` — the cover the
    #: floor actually lifted, and the honest ``from_pos`` for a floor's clamp
    #: step. Equals ``effective_winner_pos`` unless a ceiling bound a different
    #: cover in the same cycle.
    raise_from: int | None
    #: The HIGHEST judged position when ``lowered``, else ``None`` — the mirror,
    #: and the only starting point a ceiling's own clamp step can honestly name
    #: once a floor has taken ``effective_winner_pos``.
    lower_from: int | None
    #: Per-cover dispatch verdicts, or ``None`` for a computed winner, for a
    #: snapshot carrying no per-entity positions, and for a cover type whose
    #: entities do not move independently — whether that type named its own
    #: reference position (#1179) or fell back to the summary mean. All of those
    #: keep the cycle on the singular pre-#1174 path.
    verdicts: Mapping[str, HoldClampVerdict] | None


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
    # or, since issue #943, a ceiling lower. The flag has exactly three jobs:
    #
    #   1. reason/trace labelling (the `floor_clamp` / `ceiling_clamp` steps),
    #   2. clearing `skip_command` so the clamp still reaches a cover the winner
    #      was merely holding (issues #534 / #809),
    #   3. riding the diagnostics and event-timeline payloads.
    #
    # It makes NO claim about the value's frame: `position` stays logical, and
    # `coordinator.state` interpolates/inverts a clamped winner exactly like any
    # other (issue #1036 removed the #469 carve-out that skipped both).
    position_constraint_applied: bool = False

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
    #
    # On a multi-cover instance this is a SUMMARY — the mean of the covers that
    # reported a position — because the "Target Position" sensor is one entity
    # per config entry and a scalar has to stand for the group. No decision
    # consumes it: the per-cover truth is ``hold_clamp_verdicts`` below (and the
    # sensor's own ``actual_positions`` attribute). It also remains the
    # presence marker the #1170 priority gate keys on.
    held_position: int | None = None

    # Per-cover dispatch verdicts for a hold winner (#1174): the sole authority
    # on which bound covers are commanded this cycle and where each one is sent.
    # Populated when ``held_position is not None``, the snapshot carried
    # per-entity positions, AND this cover type's entities move independently
    # (``CoverTypePolicy.entities_move_independently`` — a type that remaps its
    # entities' targets, rewrites the position after the pipeline, or orders
    # physically coupled rails expresses ONE geometry and has no per-cover
    # question to answer). ``None`` otherwise — for every computed (non-hold)
    # winner, for legacy snapshots without the dict, and for those coupled types
    # — all of which keep the cycle on the singular ``skip_command`` /
    # ``position`` path unchanged.
    #
    # A cover absent from the dict is likewise unjudged and falls back to that
    # singular path. ``position`` stays the shared summary the trace and the
    # singular surfaces carry; where a released cover actually goes is
    # ``HoldClampVerdict.target``, which diverges from it whenever a floor and a
    # ceiling bind different covers.
    hold_clamp_verdicts: Mapping[str, HoldClampVerdict] | None = None

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
