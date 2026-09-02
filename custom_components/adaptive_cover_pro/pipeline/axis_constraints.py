"""Unified per-axis constraint composition (issue #943).

A *constraint* is one active override's claim on one axis of the cover. The
handler that owns the claim defers (returns ``None`` from ``evaluate``); the
pipeline resolves normally and this module composes the claims onto whatever
won. Composition onto an *ordinary* winner is priority-independent by
construction — a constraint clamps a computed position regardless of who
computed it (#463).

The one exception is a winner that is **holding** a physical position rather
than proposing a computed one (``held_position`` — manual override, a group
lock). There a constraint must outrank the holder before it may move the cover;
see :func:`outranking` (#1170).

**This module is the generalization of two single-purpose passes.** Before
#943 the same shape was written twice:

* ``floors.py``    — position-min only: max-of-values, always-clamp,
  ``held_position``-aware (issues #463 / #496 / #534 / #809).
* ``tilt_axis.py`` — tilt-fixed only: highest-priority-wins, fill-when-unset
  (issue #514).

Those two rules read like an axis difference but are really a **kind**
difference. A position floor is ``kind=MIN``; a tilt-only slot is
``kind=FIXED``. Once that is named, one gather + one compose serves both axes,
and the new bounds (#943's position-max and tilt-min/max) fall out of the
existing rules rather than needing new ones:

============  =========================================  ===================
kind          resolution                                 application
============  =========================================  ===================
``FIXED``     highest priority wins (ties → first)       fill-when-unset
``MIN``       max of the lows        (#496)              always-clamp
``MAX``       min of the highs       (the #496 mirror)   always-clamp
``RANGE``     both of the above                          always-clamp
============  =========================================  ===================

No existing behavior changes, because a pre-#943 config can only ever produce
position-``MIN`` and tilt-``FIXED`` constraints — exactly the two cells the old
modules implemented. ``floors.py`` and ``tilt_axis.py`` survive as thin
adapters over this module so the coordinator's user-move clamp (#472/#416/#372)
and the registry keep sharing one implementation of the arithmetic.

Everything here is pure: it reads a :class:`PipelineSnapshot` and returns plain
data. Cover-type-agnostic by construction — axes are keyed by the shared
``AXIS_NAME_*`` vocabulary and the pass never asks "is this venetian".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ..const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    AxisConstraintMode,
    ReasonCode,
    custom_position_handler_name,
)
from ..cover_types.base import AXIS_NAME_POSITION, AXIS_NAME_TILT
from ..reason_i18n import Reason
from .types import DecisionStep, PipelineSnapshot, is_bounded_mode


@dataclass(frozen=True, slots=True)
class AxisConstraint:
    """One active override's claim on one axis.

    Replaces ``FloorClampInfo`` (position-min) and ``TiltAxisContribution``
    (tilt-fixed) with a single type covering both.

    Attributes:
        axis:  Which axis this constrains — one of the ``AXIS_NAME_*``
               constants from ``cover_types.base``. A *value*, never a
               cover-type branch: code outside ``cover_types/`` must not ask
               what kind of cover it is looking at.
        kind:  How the claim resolves and applies. See the table in the module
               docstring.
        low:   Floor value (0–100, pre-inversion canonical space), or None.
               For ``FIXED`` this equals ``high`` — the exact value.
        high:  Ceiling value, or None. For ``FIXED`` this equals ``low``.
        source: Stable identifier used as the ``handler`` field in the decision
               trace — e.g. ``"custom_position_1"``, ``"weather"``.
        label: Human-readable name for the trace reason — the bound sensor's
               friendly name, or a fixed string for the weather override.
        priority: The contributing override's pipeline priority. ``FIXED``
               resolution sorts on this, and :func:`outranking` gates on it
               wherever a bound is asked to move a position a handler is
               *holding* (#472/#1170). Composition onto an ordinary computed
               winner ignores it — auto-rule composition stays unconditional
               (#463).
        slot:  1-based custom-position slot number, or 0 for non-slot sources
               (the weather floor). Surfaced in the Control Status string
               (#667).
        outside_window: Whether this claim keeps binding after the user's
               start/end clock window closes (issue #943 item B). For a
               custom-position slot it is the per-slot opt-in; the weather
               floor sets it from ``snapshot.weather_outside_window``, which
               defaults True because weather has always acted out there and
               reads False only when the user scoped the override to their
               operational window (issue #1308). Read ONLY by
               :func:`_window_eligible`, and only for a non-``FIXED`` kind.

    """

    axis: str
    kind: AxisConstraintMode
    low: int | None
    high: int | None
    source: str
    label: str
    priority: int
    slot: int
    outside_window: bool = False

    @property
    def value(self) -> int | None:
        """The exact value of a ``FIXED`` claim (``low`` and ``high`` agree)."""
        return self.low


class _Prioritized(Protocol):
    """Anything carrying a pipeline priority — the only field :func:`outranking` reads."""

    priority: int


def outranking[ClaimT: _Prioritized](
    claims: Iterable[ClaimT], holder_priority: int
) -> list[ClaimT]:
    """Keep only the claims that strictly outrank a handler holding a position.

    **The one place the "may this bound move a held position?" rule is
    stated.** Both seams that can move a position a handler is already holding
    consume it, so they cannot drift apart (CODING_GUIDELINES § No
    Duplication):

    * ``Coordinator._clamp_to_active_floor`` — the user's command, judged
      before dispatch (#472).
    * ``PipelineRegistry.evaluate`` — the same command one cycle later,
      arriving as ``manual_override``'s ``held_position``. Both axes consume
      it: a tilt bound forces a dispatch just as a position floor does (#1170).

    Splitting those two produced the reported defect: the user's close was
    dispatched correctly, then the identical floor raised it straight back
    because only the first seam asked about priority.

    Strictly-greater, matching the pipeline's own tie rule: a bound *equal* to
    the holder does not outrank it. ``holder_priority`` is the holding
    handler's **effective** priority — resolved by ``resolve_handler_priority``
    from the 🔀 Handler Priorities step, never the class default, which ignores
    that setting.

    **A safety claim never yields**, even on a tie. ``CUSTOM_POSITION_SAFETY_PRIORITY``
    is documented to act outside the time window, past the auto-control switch
    and past manual override, and ``GroupLockHandler`` holds at that same 100 —
    so plain strictly-greater would let a room lock suppress a storm or wind
    floor, inverting the tie rule ``const.py`` states (the member's own safety
    slot wins a 100-tie). Built-in handlers cap at 99, so this tie is reachable
    only between a safety slot and a group lock.

    Deliberately duck-typed on ``.priority`` so the registry's
    :class:`AxisConstraint` and the coordinator's
    :class:`~.floors.FloorClampInfo` share one implementation.
    """
    return [
        c
        for c in claims
        if c.priority > holder_priority or c.priority == CUSTOM_POSITION_SAFETY_PRIORITY
    ]


def clamp_to_bounds(value: int, low: int | None, high: int | None) -> int:
    """Clamp *value* into ``[low, high]``; either bound may be absent.

    **The one clamp formula.** Every caller — the registry's position pass, its
    tilt pass, the venetian engine-tilt clamp, and ``floors.effective_floor``'s
    consumers — goes through here, so the arithmetic exists exactly once
    (CODING_GUIDELINES § Single-Source-of-Truth Helpers).

    Order matters: the ceiling applies first and the floor last, so when a
    caller hands in conflicting bounds (``low > high``) **the floor wins**.
    That is the deliberate conflict rule — a floor is a protection commitment
    (keep at least this much cover), and honoring it over a ceiling fails safe.
    It also keeps the position pass's ``final > effective_winner_pos`` predicate
    exactly equivalent to the pre-#943 ``floor_raised``.

    Bounds are tested with ``is None``, never truthiness: 0 is a real bound.
    """
    if high is not None:
        value = min(value, high)
    if low is not None:
        value = max(value, low)
    return value


def compose_bounds(
    constraints: Iterable[AxisConstraint], axis: str
) -> tuple[int | None, int | None]:
    """Compose every bounded constraint on *axis* into one ``(low, high)``.

    ``low`` is the **max of the lows** — issue #496's max-of-floors rule, now
    stated once for every axis. ``high`` is the **min of the highs**, its single
    mirror. Both mean "the most restrictive claim wins", which is what makes
    composition order-independent.

    ``FIXED`` constraints are skipped: they resolve by priority
    (:func:`resolve_fixed`), not by composition. Returns ``(None, None)`` when
    nothing bounds the axis.
    """
    low: int | None = None
    high: int | None = None
    for c in constraints:
        if c.axis != axis or c.kind is AxisConstraintMode.FIXED:
            continue
        if c.low is not None and (low is None or c.low > low):
            low = c.low
        if c.high is not None and (high is None or c.high < high):
            high = c.high
    return low, high


def bounding_constraint(
    constraints: Iterable[AxisConstraint],
    axis: str,
    value: int,
    *,
    low: bool,
) -> AxisConstraint | None:
    """Return the single constraint whose bound actually bound this cycle.

    ``compose_bounds`` collapses many claims into one ``(low, high)`` pair, so
    a clamp knows the value it applied but not *which* claim produced it. This
    walks back: the binding floor is the first ``MIN``/``RANGE`` whose ``low``
    equals ``value`` (``low=True``); the binding ceiling the first
    ``MAX``/``RANGE`` whose ``high`` equals ``value`` (``low=False``). "First"
    matches the tie rule the composition already uses (max-of-mins keeps the
    earliest slot on a tie), so the trace credits exactly one slot — never the
    join of every active bound (audit finding 4a). Returns None when nothing
    matched (an inert axis).
    """
    for c in constraints:
        if c.axis != axis or c.kind is AxisConstraintMode.FIXED:
            continue
        edge = c.low if low else c.high
        if edge is not None and edge == value:
            return c
    return None


def resolve_fixed(
    constraints: Iterable[AxisConstraint], axis: str
) -> AxisConstraint | None:
    """Return the highest-priority ``FIXED`` claim on *axis*, or None.

    Ties resolve to the first in iteration order (snapshot order, which matches
    ``_build_pipeline`` registration order) — byte-identical to the rule
    ``tilt_axis.resolve_tilt_axis`` has used since #514.
    """
    winner: AxisConstraint | None = None
    for c in constraints:
        if c.axis != axis or c.kind is not AxisConstraintMode.FIXED:
            continue
        if winner is None or c.priority > winner.priority:
            winner = c
    return winner


def _bounded(
    axis: str,
    mode: AxisConstraintMode,
    low: int | None,
    high: int | None,
    *,
    source: str,
    label: str,
    priority: int,
    slot: int,
    outside_window: bool = False,
) -> AxisConstraint | None:
    """Build a bounded constraint, or None when the axis makes no claim."""
    if not is_bounded_mode(mode):
        return None
    return AxisConstraint(
        axis=axis,
        kind=mode,
        low=low,
        high=high,
        source=source,
        label=label,
        priority=priority,
        slot=slot,
        outside_window=outside_window,
    )


def _window_eligible(constraint: AxisConstraint) -> bool:
    """Whether this claim still binds once the user's start/end clock closes.

    **The one statement of outside-window eligibility** (issue #943 item B).
    Two ways in, and only two:

    * a claim at ``CUSTOM_POSITION_SAFETY_PRIORITY``. A slot at that priority
      whose result WINS has commanded outside the window since #563. This
      branch is wider than that, and deliberately: a priority-100 slot that
      contributes only a BOUND never produces a result at all, so it never had
      the licence — before item B its ceiling was composed onto a DEFAULT
      winner and then dropped by the dispatch gate. Admitting it here is a new
      capability, granted on the grounds that a slot the user placed at safety
      priority means it, and that a bound can only ever clamp something already
      resolved.
    * a claim whose ``outside_window`` flag is set AND whose kind is bounded —
      an opted-in custom-position slot, and the weather floor, which sets the
      flag from ``snapshot.weather_outside_window`` (default True). Also a
      widening, and also deliberate: a NON-min-mode weather override wins with
      ``is_safety`` and has always acted out here, but the MIN-MODE floor this
      claim represents makes the handler *defer*
      (``WeatherOverrideHandler.evaluate`` returns ``None`` for it), so no
      result ever carried ``is_safety`` and the old dispatch gate blocked it
      exactly like any other bound. A storm floor that stops holding at dusk is
      not what the option promises, so it is admitted — unless the user asked
      for exactly that by scoping weather to their window (#1308), which the
      flag then carries here without this predicate learning a second rule.
      Keying on the field rather than on the source string keeps the handler's
      own ``name`` the single definition of "weather" — nothing here compares
      identifiers.

    ``FIXED`` is excluded on purpose: a slot that DRIVES a value outside the
    window — an exact position, a real fixed slat angle — is the
    #215/#216/#223 defect class re-armed by a checkbox. A bound only ever
    clamps something the pipeline already resolved, which is what keeps the
    blast radius to "satisfy the constraint".

    Nothing here consults the *result*: eligibility is a property of the claim.
    Whether an eligible claim actually earns a dispatch is a separate question,
    answered once per cycle by the registry and carried on
    ``PipelineResult.outside_window_constraint_active``.
    """
    if constraint.priority >= CUSTOM_POSITION_SAFETY_PRIORITY:
        return True
    return constraint.outside_window and constraint.kind is not AxisConstraintMode.FIXED


def may_act_outside_clock_window(*, is_safety: bool, constraint_admitted: bool) -> bool:
    """Whether a target may reach the hardware outside the user's clock window.

    **The one place the outside-window admission rule is stated** — the OR
    itself, so the four dispatch guards cannot drift apart
    (CODING_GUIDELINES § No Duplication). It is deliberately a free function
    over two bools rather than a method, because the two callers hold the
    answer in different shapes and neither can be reached from the other:

    * :attr:`PipelineResult.acts_outside_clock_window` — this cycle's live
      result, read by the three coordinator guards;
    * :attr:`~managers.cover_command.state_store.PerEntityState.acts_outside_clock_window`
      — a per-entity BOOKED verdict, read by reconciliation long after the
      result that produced it is gone.

    ``is_safety`` keeps its own lifetime (#1226/#1165) and is never co-written
    with the constraint admission; this predicate only reads them together.
    """
    return is_safety or constraint_admitted


def partition_axis_constraints(
    snapshot: PipelineSnapshot,
) -> tuple[list[AxisConstraint], list[AxisConstraint]]:
    """Split the snapshot's active constraints into (binding, window-dropped).

    With the clock window open the second list is always empty and the first is
    byte-identical to the pre-#943-item-B gather. With it closed, the split is
    :func:`_window_eligible` applied once — the dropped half exists so the
    decision trace can say a bound was active and deliberately not applied,
    rather than leaving the slot's unhelpful ``describe_skip`` entry standing.
    """
    constraints = _gather_all(snapshot)
    if snapshot.clock_window_open:
        return constraints, []
    kept: list[AxisConstraint] = []
    dropped: list[AxisConstraint] = []
    for constraint in constraints:
        (kept if _window_eligible(constraint) else dropped).append(constraint)
    return kept, dropped


def gather_axis_constraints(snapshot: PipelineSnapshot) -> list[AxisConstraint]:
    """Collect every active axis constraint that binds this cycle.

    The binding half of :func:`partition_axis_constraints` — so the registry's
    composition, ``floors.gather_active_floors`` (and through it the
    coordinator's user-move clamp, #472), and the end-of-window clamp all see
    the same set, and outside-window eligibility is decided in exactly one
    place rather than once per consumer.
    """
    return partition_axis_constraints(snapshot)[0]


def _gather_all(snapshot: PipelineSnapshot) -> list[AxisConstraint]:
    """Collect every active axis constraint the snapshot contributes.

    One pass over the snapshot emits, in this order (which the trace relies on):

      1. Per custom-position slot, in snapshot order (matching
         ``_build_pipeline`` registration order): its position claim then its
         tilt claim. A slot may constrain both axes.
      2. The weather override's min-mode position floor, if any.

    Slot modes are read straight off ``CustomPositionSensorState`` — the
    snapshot builder already derived them at the single normalization site, so
    no boolean precedence is re-litigated here.

    ``use_my`` position claims are excluded: the My path is hardware-pinned and
    never participates in constraint semantics (pre-#943 behavior, preserved).
    """
    # Local import: ``pipeline.handlers`` pulls in cover-type policies, so a
    # module-level import here would form a circular import chain. The class is
    # still the single source of truth for the weather priority — never inline
    # the magic number.
    from .handlers.weather import WeatherOverrideHandler

    constraints: list[AxisConstraint] = []
    for state in snapshot.custom_position_sensors:
        if not state.is_on:
            continue
        source = custom_position_handler_name(state.slot)
        shared = {
            "source": source,
            "label": state.display_label,
            "priority": state.priority,
            "slot": state.slot,
            "outside_window": state.outside_window,
        }

        # --- Position axis ---
        if not state.use_my:
            position_low = (
                state.position
                if state.position_mode
                in (AxisConstraintMode.MIN, AxisConstraintMode.RANGE)
                else None
            )
            pos = _bounded(
                AXIS_NAME_POSITION,
                state.position_mode,
                position_low,
                state.position_max,
                **shared,
            )
            if pos is not None:
                constraints.append(pos)

        # --- Tilt axis ---
        if state.tilt_mode is AxisConstraintMode.FIXED and state.tilt is not None:
            constraints.append(
                AxisConstraint(
                    axis=AXIS_NAME_TILT,
                    kind=AxisConstraintMode.FIXED,
                    low=state.tilt,
                    high=state.tilt,
                    **shared,
                )
            )
        else:
            tilt = _bounded(
                AXIS_NAME_TILT,
                state.tilt_mode,
                state.tilt_min,
                state.tilt_max,
                **shared,
            )
            if tilt is not None:
                constraints.append(tilt)

    if snapshot.weather_override_active and snapshot.weather_override_min_mode:
        constraints.append(
            AxisConstraint(
                axis=AXIS_NAME_POSITION,
                kind=AxisConstraintMode.MIN,
                low=snapshot.weather_override_position,
                high=None,
                # The handler's own ``name`` is the single definition of this
                # identifier; the eligibility predicate keys on the flag below,
                # never on this string.
                source=WeatherOverrideHandler.name,
                label="weather override",
                # The EFFECTIVE priority, resolved at snapshot build from
                # ``weather_priority``. The class default is only the fallback:
                # a user who demotes weather below manual override must not have
                # its floor keep claiming 90 and outranking a hold it was just
                # told to lose to (#1170). None means "no resolution available"
                # — every production snapshot carries one.
                priority=(
                    snapshot.weather_override_priority
                    if snapshot.weather_override_priority is not None
                    else WeatherOverrideHandler.priority
                ),
                slot=0,
                # A storm floor holds overnight unless the user scoped weather
                # to their operational window (#1308). Carried on the claim so
                # :func:`_window_eligible` never has to recognise the weather
                # floor by source — and note the default IS a change for it:
                # the handler defers in min mode, so this claim's cycles never
                # carried ``is_safety`` and the pre-item-B dispatch gate stopped
                # them like any other bound. The non-min-mode retraction is the
                # one that has always acted out here, and it is a winning
                # result, not a claim in this list — it reads the same snapshot
                # field from ``WeatherOverrideHandler.evaluate``, so one option
                # scopes both seats.
                outside_window=snapshot.weather_outside_window,
            )
        )
    return constraints


def bound_label(value: int | None) -> str:
    """Render one side of a bound for a trace reason ('—' when unbounded)."""
    return "—" if value is None else f"{value}%"


def tilt_clamp_step(
    *, from_tilt: int, to_tilt: int, label: str, source: str
) -> DecisionStep:
    """Build the trace step for a tilt clamp.

    Shared by the registry (clamping a tilt the winner already set) and
    ``VenetianPolicy.post_pipeline_resolve`` (clamping the tilt its engine
    resolves after the pipeline). Both clamps are the same event and must read
    the same way in the trace, so the step is built in one place.
    """
    return DecisionStep(
        handler=source,
        matched=True,
        reason_payload=Reason(
            ReasonCode.REGISTRY_TILT_CLAMPED,
            {"from_tilt": from_tilt, "to_tilt": to_tilt, "label": label},
        ),
        position=None,
        tilt=to_tilt,
    )


def constraint_label(constraints: Iterable[AxisConstraint], axis: str) -> str:
    """Name the bounded constraints on *axis* for a trace reason.

    One constraint renders its own label; several render a joined list, so a
    clamp always says which slot(s) produced it.
    """
    labels = [
        c.label
        for c in constraints
        if c.axis == axis and c.kind is not AxisConstraintMode.FIXED
    ]
    # dict.fromkeys de-dupes while preserving order (two bounds, one slot).
    return ", ".join(dict.fromkeys(labels)) or "constraint"
