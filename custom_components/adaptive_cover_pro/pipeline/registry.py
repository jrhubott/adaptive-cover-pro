"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Mapping

from ..const import AxisConstraintMode, ReasonCode
from ..cover_types.base import AXIS_NAME_POSITION, AXIS_NAME_TILT
from ..diagnostics.event_buffer import EventBuffer
from ..position_utils import flip_if
from ..reason_i18n import Reason, render_en
from .axis_constraints import (
    bound_label,
    bounding_constraint,
    clamp_to_bounds,
    compose_bounds,
    constraint_label,
    outranking,
    partition_axis_constraints,
    tilt_clamp_step,
)
from .handler import OverrideHandler
from .tilt_axis import resolve_tilt_axis_from
from .types import (
    DecisionStep,
    HoldClampVerdict,
    PipelineResult,
    PipelineSnapshot,
    PositionAxisJudgment,
)

_LOGGER = logging.getLogger(__name__)


def _axis_fragment(axis: str) -> Reason:
    """Name an axis for a trace reason — the one mapping, used by two steps."""
    return Reason(
        ReasonCode.FRAGMENT_AXIS_POSITION
        if axis == AXIS_NAME_POSITION
        else ReasonCode.FRAGMENT_AXIS_TILT
    )


def _window_dropped_steps(dropped: list) -> list[DecisionStep]:
    """Explain every active claim the closed clock window did not admit.

    A dropped claim's handler DEFERRED, so the deferral sweep has already taken
    its ``describe_skip`` entry — without a step of its own the slot vanishes
    from the trace entirely and the user is left guessing why their bound did
    nothing at 03:00. Distinct from ``floor_inactive`` / ``tilt_bound_inactive``
    on purpose: those claim the winner was already on the satisfied side, which
    is not what happened here.
    """
    return [
        DecisionStep(
            handler=c.source,
            matched=False,
            reason_payload=Reason(
                ReasonCode.REGISTRY_CONSTRAINT_OUTSIDE_WINDOW,
                {
                    "low_label": bound_label(c.low),
                    "high_label": bound_label(c.high),
                    "label": c.label,
                    "axis": _axis_fragment(c.axis),
                },
            ),
            position=None,
        )
        for c in dropped
    ]


def _normalize_reason(value: str | Reason) -> tuple[str, Reason | None]:
    """Split a str-or-:class:`Reason` into (english text, payload-or-None).

    Handlers migrate their ``describe_skip`` / ``reason`` emitters to stable
    :class:`Reason` codes one batch at a time (issue #882). Until every emitter
    is migrated the registry must accept both: a ``Reason`` yields its
    English rendering plus the payload (for the card); a legacy ``str`` passes
    through unchanged with no payload.
    """
    if isinstance(value, Reason):
        return render_en(value), value
    return value, None


def _drop_trace_steps(
    trace: list[DecisionStep], sources: set[str]
) -> list[DecisionStep]:
    """Remove the *deferral* trace steps whose handler is one of ``sources``.

    Both the floor pass and the tilt-axis pass re-emit fresh trace steps for
    handlers that *deferred* (returned None) so the registry can replace the
    handler's unhelpful ``describe_skip`` entry. They share this removal step
    so the dedup logic lives in one place (CODING_GUIDELINES § No Duplication).

    A ``matched=True`` step is never dropped: since #943 a slot can win the
    pipeline (an exact position or ``use_my``) *and* contribute a bound on the
    other axis, so its own source appears in ``sources`` even though it did not
    defer. Sweeping it out left the winner unnamed in the trace (audit finding
    1); the matched guard keeps the winner's step while still replacing every
    deferral skip.
    """
    return [step for step in trace if step.matched or step.handler not in sources]


def _split_bounds_against_hold(
    constraints: list, winner, holder_priority: int
) -> tuple[list, list]:
    """Split constraints into those allowed to move this winner, and those not.

    A winner carrying ``held_position`` is not proposing a computed target — it
    is holding the cover where something authoritative already put it (a manual
    move, a group lock). A bound may move that cover only when it outranks the
    handler doing the holding, which is the same question
    ``Coordinator._clamp_to_active_floor`` asks about the user's command at the
    moment it is issued (#472). Asking it in only one of the two places was the
    reported defect: the manual close dispatched correctly, and this pass raised
    it straight back on the next cycle (#1170).

    ⚠️ ``held_position`` is the marker, not "is this a hold". ``motion_timeout``
    also holds a cover in place but publishes the position through
    ``PipelineResult.position`` and leaves ``held_position`` unset, so it is NOT
    gated here and a low-priority bound still clamps a motion hold. That is
    unchanged, pre-existing behaviour, called out because the asymmetry is
    invisible from this function.

    Applies to **both axes**: a tilt bound that does not outrank the holder
    forces a dispatch just as surely as a position floor does, and a rule that
    protected one axis and not the other would be the original defect again.
    ``FIXED`` tilt-only claims are exempt at the call site — they fill an unset
    tilt (#514) rather than moving a held one.

    An ordinary winner (``held_position is None``) is unaffected — its position
    is a value this pipeline computed, and clamping it regardless of priority is
    #463's whole point, so every constraint is returned as binding.

    The second element is the bounded claims that yielded, for the trace.
    Membership is tested by identity, matching :func:`_iter_nonbinding_bounds`:
    two slots can carry equal bounds and each must stay individually visible.
    """
    if winner.held_position is None:
        return constraints, []
    binding = outranking(constraints, holder_priority)
    binding_ids = {id(c) for c in binding}
    yielded = [
        c
        for c in constraints
        if id(c) not in binding_ids and c.kind is not AxisConstraintMode.FIXED
    ]
    return binding, yielded


def _policy_for(snapshot: PipelineSnapshot):
    """Resolve this snapshot's policy — one site, one local import.

    ``snapshot.policy or get_policy(snapshot.cover_type)`` is the same
    resolution the glare-zone, group-scene and climate handlers already use.

    ⚠️ The fallback builds the right policy CLASS but a **default-constructed**
    instance — one that has never been handed ``sync_runtime_options``. For most
    registered policies that costs nothing: they answer
    ``entities_move_independently`` from the class-derived base predicate (or
    from a constant override, as venetian does) and inherit the base
    ``hold_reference_position``, so a fresh instance gives exactly the answer the
    live one would. That is what makes the fallback safe for them.

    The hazard's boundary is the two policies that answer from INSTANCE state
    since #1179 — ``DayNightShadePolicy`` (its cached control model) and
    ``DualPanelPolicy`` (whether a front panel is designated). A fresh instance
    holds their ``__init__`` defaults, so a policy-less day/night snapshot
    answers as Model A (independent) and a policy-less dual panel as front-less
    (independent), where the old class-derived predicate said coupled for either.
    Hand a real Model C entry a policy-less snapshot and its rails get per-cover
    verdicts judged on raw wire values, which is the double-remap #1174's gate
    exists to prevent.

    Unreachable in production — ``snapshot_builder`` puts the coordinator's
    live, already-synced policy on every snapshot it builds, and
    ``tests/test_pipeline/conftest.make_snapshot`` documents the same hazard for
    hand-built ones. The fresh-instance answers are pinned by
    ``test_a_policy_less_snapshot_answers_from_a_default_constructed_policy``
    so they cannot drift silently.

    Local import: ``cover_types`` pulls in policies that import from
    ``pipeline``, so a module-level import here would close the cycle — the same
    reason ``axis_constraints.gather_axis_constraints`` imports its handler
    locally.
    """
    from ..cover_types import get_policy

    return snapshot.policy or get_policy(snapshot.cover_type)


def _judges_per_cover(snapshot: PipelineSnapshot) -> bool:
    """Whether this instance's covers may be judged one at a time (#1174).

    Delegated whole to ``CoverTypePolicy.entities_move_independently`` — the
    registry asks the polymorphic hook and never inspects a cover-type string
    (CODING_GUIDELINES § "Cover Type Abstraction").
    """
    return _policy_for(snapshot).entities_move_independently()


def _hold_reference_position(snapshot: PipelineSnapshot) -> int | None:
    """Ask the policy for the coupled instance's own LOGICAL position (#1179).

    The second half of the same polymorphic question: a cover type whose
    entities are ONE geometry has no per-cover answer, but it does have a
    position of its own, and only the policy can reduce the raw per-entity reads
    back to it. ``None`` means it has no single answer this cycle, which keeps
    the legacy summary mean.
    """
    return _policy_for(snapshot).hold_reference_position(
        snapshot.cover_positions or {},
        inverted=snapshot.position_axis_inverted,
    )


def _judge_position_axis(
    winner,
    snapshot: PipelineSnapshot,
    floor_pos: int | None,
    ceiling_pos: int | None,
) -> PositionAxisJudgment:
    """Resolve what the composed position bounds do to this cycle's winner.

    The single site that asks "where will the cover actually end up, and did a
    composed bound move it?" — for both kinds of winner, so the two answers
    cannot drift apart.

    **A computed winner** proposes ``winner.position``, already in the logical
    frame, and is clamped unconditionally (#463). One position, no verdicts:
    unchanged from before #1174, and the only path a non-hold winner takes.

    **A hold winner** keeps physical positions rather than proposing a computed
    one, and on a multi-cover instance ``winner.held_position`` is the group's
    *mean* — nobody's position. Judging the bounds against that mean dragged
    compliant covers to a floor they already satisfied and hid a lone violator
    behind its compliant siblings (#1174). So each cover is judged on its own
    position and carries its own resolved target, because one shared number
    cannot serve them all: a floor and a ceiling that both outrank the holder
    bind different covers in opposite directions, and ``clamp_to_bounds``
    applies the floor last, so the shared ``final_pos`` can only ever name one
    of the two edges.

    **Judged set** (holds only) — three cases, in order:

    1. ``snapshot.cover_positions`` when it carries entries AND this cover
       type's entities move independently. A cover with an unknown position
       falls back to the summary ``winner.held_position``, which is today's
       judgment kept exactly for the covers where per-entity data is missing.
       ``verdicts`` is populated: #1174's path, unchanged.
    2. Coupled, and the policy names a reference (#1179) — ONE entry carrying
       that one number, which is the whole instance's own position. ``verdicts``
       stays ``None``: the clamped value rides ``PipelineResult.position``
       through the shared dispatch route a coupled type already uses, so
       ``post_pipeline_resolve`` and ``resolve_entity_target`` re-expand it
       exactly once — exactly as they do for a computed winner.
    3. Everything else — a single pseudo-entry carrying
       ``winner.held_position``, reproducing the legacy singular comparison
       byte-for-byte. ``verdicts`` is ``None`` so every pre-#1174 consumer stays
       on the singular ``skip_command`` route.

    **The independence gate** is the policy's own
    ``entities_move_independently`` (``cover_types/base.py``), never a
    cover-type test here — CODING_GUIDELINES § "Cover Type Abstraction". A type
    that remaps a shared position per entity, rewrites it after the pipeline, or
    orders physically coupled entities has no per-cover question to answer: its
    entities express ONE geometry, and judging them apart both double-applies
    the remap and bypasses the rewrite. What that geometry's own position IS is
    the second half of the same polymorphic question, and
    ``CoverTypePolicy.hold_reference_position`` answers it; ``None`` there keeps
    the pre-#1174 summary mean.

    **Frames.** Held positions are RAW cover-frame reads and the bounds are
    logical user values, so each is converted before comparing — the #1036
    conversion, now applied per cover instead of once to the mean. A policy's
    reference arrives ALREADY LOGICAL: the reduction is the inverse of the whole
    dispatch chain, wire decoding included, so the policy un-flips and decodes
    it itself and this function must not flip it a second time. Every ``target``
    is on the logical side of that conversion, matching
    ``PipelineResult.position``, so the coordinator maps it to the wire through
    the same ``_to_cover_frame`` seam it already uses for the shared value.

    For a hold, ``effective_winner_pos`` names the *violating* cover — the
    lowest judged position when a floor raised, the highest when a ceiling
    lowered — so the clamp step's ``from_pos`` reads as a real cover's position
    rather than a mean nobody sits at. ``raise_from`` / ``lower_from`` keep both
    of those, because a floor and a ceiling can bind different covers in the
    same cycle and each clamp step then owes its own honest starting point.
    """
    if winner.held_position is None:
        final = clamp_to_bounds(winner.position, floor_pos, ceiling_pos)
        raised = final > winner.position
        lowered = final < winner.position
        return PositionAxisJudgment(
            effective_winner_pos=winner.position,
            final_pos=final,
            raised=raised,
            lowered=lowered,
            raise_from=winner.position if raised else None,
            lower_from=winner.position if lowered else None,
            verdicts=None,
        )
    per_entity = snapshot.cover_positions or None
    reference: int | None = None
    if per_entity is not None and not _judges_per_cover(snapshot):
        # One geometry, one position: ask the policy for this instance's own
        # position in the frame PipelineResult.position speaks (#1179). None
        # means the policy has no single answer, which keeps the legacy mean.
        reference = _hold_reference_position(snapshot)
        per_entity = None
    # (entity_id, raw read for the verdict, LOGICAL position to judge).
    entries: list[tuple[str | None, int | None, int]]
    if per_entity is None and reference is not None:
        # Already LOGICAL — the policy un-flipped and decoded it itself, so the
        # #1036 conversion below must NOT run a second time on this value.
        entries = [(None, None, reference)]
    else:
        # #1174's per-entity set, or the legacy single pseudo-entry on the mean.
        # One conversion site for both, because they are the same conversion.
        judged: Mapping[str | None, int | None] = (
            per_entity if per_entity is not None else {None: winner.held_position}
        )
        entries = []
        for entity_id, position in judged.items():
            raw = position if position is not None else winner.held_position
            entries.append(
                (entity_id, raw, flip_if(raw, inverted=snapshot.position_axis_inverted))
            )
    verdicts: dict[str, HoldClampVerdict] = {}
    effective_positions: list[int] = []
    raised = False
    lowered = False
    for entity_id, raw, eff in entries:
        # This cover's own answer, and the only one that can be right for it:
        # the edge that binds IT when a bound does, and where it already sits
        # when none does — which is what a command forced by the other axis
        # has to carry so the hold survives it.
        fin = clamp_to_bounds(eff, floor_pos, ceiling_pos)
        effective_positions.append(eff)
        raised = raised or fin > eff
        lowered = lowered or fin < eff
        if entity_id is not None:
            verdicts[entity_id] = HoldClampVerdict(
                held_position=raw, released=fin != eff, target=fin
            )
    if raised:
        effective_winner_pos = min(effective_positions)
    elif lowered:
        effective_winner_pos = max(effective_positions)
    else:
        # Nothing clamped, so this is what ``_release_hold_for_tilt_clamp``
        # writes into ``result.position`` when the TILT axis forces a dispatch
        # and the position axis stayed inert. For a coupled type that must be
        # the policy's own abstract reference, not the raw read it decoded it
        # from: the Model B fabric-change path re-folds this value with a new
        # blend, and folding a wire a second time halves the coverage.
        effective_winner_pos = (
            reference
            if reference is not None
            else flip_if(winner.held_position, inverted=snapshot.position_axis_inverted)
        )
    return PositionAxisJudgment(
        effective_winner_pos=effective_winner_pos,
        final_pos=clamp_to_bounds(effective_winner_pos, floor_pos, ceiling_pos),
        raised=raised,
        lowered=lowered,
        raise_from=min(effective_positions) if raised else None,
        lower_from=max(effective_positions) if lowered else None,
        verdicts=verdicts if per_entity is not None else None,
    )


def _command_every_held_cover(
    verdicts: Mapping[str, HoldClampVerdict] | None,
) -> Mapping[str, HoldClampVerdict] | None:
    """Release every verdict, each to the target it already resolved (#1174).

    A tilt clamp is a command, not a no-op (#1170 audit): it clears
    ``skip_command`` for the whole group because the slats have to reach the
    hardware, and a cover the position axis never released still has to receive
    that command. What it must NOT receive is the position axis's clamp target
    — a cover held above a floor is not a reason to close it 40 points, and
    that is exactly what a shared value does here. Each verdict already carries
    its own target: the bound edge for a cover a bound moved, and its own
    position for one nothing moved, which the same-position gate turns into a
    pure secondary-axis service call (#954).

    ``None`` (a computed winner, or a snapshot with no per-entity positions)
    passes straight through — those cycles are already on the singular path
    ``_release_hold_for_tilt_clamp`` cleared.
    """
    if verdicts is None:
        return None
    return {
        entity_id: dataclasses.replace(verdict, released=True)
        for entity_id, verdict in verdicts.items()
    }


def _release_hold_for_tilt_clamp(
    winner,
    trace: list[DecisionStep],
    *,
    position_clamped: bool,
    effective_winner_pos: int,
):
    """Let a tilt clamp command the cover without misplacing it.

    A tilt clamp is a command, not a no-op, so it clears ``skip_command`` the
    same way a position clamp does — but the position that then goes out has to
    be where the cover actually IS. When the winner is a hold, ``position`` is
    the shadow value it merely reports (``manual_override``'s "solar would-be"),
    and the position axis only overwrites it when something clamped there. A
    tilt bound forcing the dispatch while the position axis stayed inert — or
    yielded to the hold (#1170) — would otherwise send that shadow and move a
    cover the user had just placed by hand.

    Since #943 item B the "hold" may be a PSEUDO one
    (:func:`_as_outside_window_pseudo_hold`): outside the clock window a
    computed winner is judged as if it held the cover's current read, precisely
    so this function's rule applies to it. The shadow it then suppresses is the
    DEFAULT winner's own position — ``default_percentage`` / ``sunset_position``
    — which is the value issues #215/#216/#223 are about.
    """
    winner = dataclasses.replace(winner, skip_command=False)
    if position_clamped or winner.held_position is None:
        return winner
    if effective_winner_pos == winner.position:
        # ``group_lock`` sets ``position`` FROM the held read, so the two agree
        # by construction and "held at 27% (not the would-be 27%)" is noise.
        # Same guard ``diagnostics/builder.py`` puts on BUILDER_MANUAL_DIVERGENCE,
        # and it also silences a manual hold sitting on its own shadow.
        return dataclasses.replace(winner, position=effective_winner_pos)
    # Appended here rather than returned, so the caller stays one statement —
    # the dispatch and its explanation are the same event.
    trace.append(
        DecisionStep(
            handler="hold_position_carried",
            matched=True,
            reason_payload=Reason(
                ReasonCode.REGISTRY_HOLD_POSITION_CARRIED,
                {"position": effective_winner_pos, "shadow_pos": winner.position},
            ),
            position=effective_winner_pos,
        )
    )
    return dataclasses.replace(winner, position=effective_winner_pos)


def _as_outside_window_pseudo_hold(
    winner, snapshot: PipelineSnapshot, constraints: list
):
    """Judge a computed winner as if it were holding the cover's current read.

    Returns ``(winner, active)`` — the winner to compose against, and whether
    the pseudo-hold engaged.

    **Why a hold and not a lifted gate** (issue #943 item B). Dispatch is
    whole-target: outside the user's clock window the winner is normally
    ``DefaultHandler`` at ``default_percentage`` / ``sunset_position``, and
    letting an admitted tilt clamp carry that position to the hardware would
    fling the cover open at 06:00 — issues #215/#216/#223, exactly. The
    codebase already owns the right mechanism for "get the constrained axis to
    the hardware without moving anything else": :func:`_judge_position_axis`'s
    hold path plus :func:`_release_hold_for_tilt_clamp`, both keyed on
    ``held_position``. Borrowing it is what makes the new capability a
    restatement of an existing rule rather than a second one.

    The holder priority stays ``winning_handler.priority`` at the call site,
    which has a consequence worth stating plainly. A REAL hold (manual override
    at 80, a group lock at 100) never reaches here at all — ``held_position`` is
    already set — and still beats a 77 slot through the unchanged
    :func:`outranking` gate. But a pseudo-hold inherits its winner's priority as
    if that winner were holding the cover, and :func:`outranking` is
    strictly-greater. Against ``DefaultHandler`` at 0 that is inert: every
    constraint outranks it. Against a **FIXED custom-position slot** winning at
    the default 77, a second slot's opted-in bound at 77 does NOT outrank it and
    is filed as ``yielded_to_hold`` — so on that configuration the opt-in is
    inert and the user sees a ``bound_yielded_to_hold`` trace step naming a
    holder that is not holding anything.

    That is today's behaviour and it is pinned by
    ``tests/test_pipeline/test_outside_window_constraints.py``
    ``::test_pseudo_hold_ties_are_judged_against_the_winners_own_priority``.
    Whether it is the RIGHT behaviour is a separate question left open: the
    pseudo-hold is scaffolding, nobody is holding the cover, and #463's rule for
    a computed winner is that composition is priority-independent — which argues
    for handing this call site a below-everything holder priority rather than
    the winner's own. That is a design change, so it is not made here.

    ``held_position`` is stripped back off before the result is returned: it is
    a user-facing field (the Target Position sensor, the Model B stash replay in
    ``DayNightShadePolicy``) and a DEFAULT result must not start claiming to
    hold anything.

    Withheld when no cover reports a position: there is then no safe value to
    pin the unconstrained axes to, and the only number left to send would be the
    winner's own — which is the whole hazard.

    **Withheld for a safety winner**, which is clause (a) of the same invariant
    the rest of this function serves. Suppressing the winner's own position is
    only ever right for a result that has no licence to move a cover out here;
    a weather retraction and a priority-100 slot both do (#563), and their own
    values are exactly what must reach the hardware. Applied to one, the
    pseudo-hold would post the cover's current read in place of the storm
    position — and, because the bounds would then be judged against that read
    rather than the winner, a ceiling that really binds the retraction would
    stop binding it too. Declining leaves such a winner on the pre-item-B path,
    where :func:`_judge_position_axis` clamps ``winner.position`` itself and
    :func:`_release_hold_for_tilt_clamp` returns early on the absent
    ``held_position`` — i.e. byte-identical to the clock-open cycle, which is
    what "acts outside the window" has always meant. It also keeps
    ``is_safety`` and ``outside_window_constraint_active`` from ever being
    co-written, preserving the separation #1226/#1165 established.
    """
    if (
        snapshot.clock_window_open
        or not constraints
        or winner.held_position is not None
        or winner.is_safety
    ):
        return winner, False
    if snapshot.current_cover_position is None:
        _LOGGER.debug(
            "Outside-window constraint active but no cover position is "
            "readable — withholding admission for this cycle"
        )
        return winner, False
    return (
        dataclasses.replace(winner, held_position=snapshot.current_cover_position),
        True,
    )


def _clamp_step(
    constraints: list,
    *,
    low: bool,
    from_pos: int,
    to_pos: int,
    code: ReasonCode,
    extra: dict | None = None,
):
    """Build one matched position-clamp step, plus the bound that produced it.

    ``low`` selects the direction — the floor edge (True) or the ceiling edge
    (False) — and drives BOTH the ``bounding_constraint`` lookup and the step's
    handler name, so the trace can never credit one slot while the inactive
    sweep treats a different one as binding. Returning the constraint alongside
    the step is what lets a two-sided clamp (#1174: a floor and a ceiling
    binding different covers) hand the sweep both bounds without restating the
    lookup (CODING_GUIDELINES § No Duplication).
    """
    binding = bounding_constraint(constraints, AXIS_NAME_POSITION, to_pos, low=low)
    label = (
        binding.label
        if binding is not None
        else constraint_label(constraints, AXIS_NAME_POSITION)
    )
    # Both endpoints are logical now that the judged positions are converted
    # up front, so "floor raised from X% to Y%" no longer mixes a motor reading
    # with a user-typed bound (#1036).
    params: dict = {"from_pos": from_pos, "to_pos": to_pos, "label": label}
    if extra:
        params.update(extra)
    step = DecisionStep(
        handler="floor_clamp" if low else "ceiling_clamp",
        matched=True,
        reason_payload=Reason(code, params),
        position=to_pos,
    )
    return step, binding


def _position_clamp_steps(
    constraints: list,
    judgment: PositionAxisJudgment,
    *,
    floor_pos: int | None,
    ceiling_pos: int | None,
    floor_wins: bool,
    two_sided: bool,
) -> tuple[list[DecisionStep], list]:
    """Emit the matched position-clamp step(s), plus the bound(s) behind them.

    One step for the ordinary single-direction clamp, and TWO when a floor and
    a ceiling each bound a different cover in the same cycle (#1174) — a shape
    only per-cover judging can produce, and one the shared ``final_pos`` cannot
    describe on its own. Returns an empty pair when nothing clamped.

    The bounds come back with the steps because the inactive-bound sweep has to
    skip exactly the ones that bound; resolving them twice is how a bound ends
    up both credited with a clamp and reported as having done nothing.
    """
    if two_sided:
        floor_step, floor_binding = _clamp_step(
            constraints,
            low=True,
            from_pos=judgment.raise_from,
            to_pos=floor_pos,
            code=ReasonCode.REGISTRY_FLOOR_RAISED,
        )
        ceiling_step, ceiling_binding = _clamp_step(
            constraints,
            low=False,
            from_pos=judgment.lower_from,
            to_pos=ceiling_pos,
            code=ReasonCode.REGISTRY_CEILING_LOWERED,
        )
        return [floor_step, ceiling_step], [floor_binding, ceiling_binding]
    if not (judgment.raised or judgment.lowered):
        return [], []
    extra: dict | None = None
    if not floor_wins:
        code = ReasonCode.REGISTRY_CEILING_LOWERED
    elif judgment.raised:
        code = ReasonCode.REGISTRY_FLOOR_RAISED
    else:
        # The floor won a conflict but the winner started above it: the net
        # move is a lowering, so "floor raised from 80% to 60%" would
        # contradict itself. A floor-wins step names the floor as the
        # determining bound without implying a direction (audit finding C).
        code = ReasonCode.REGISTRY_FLOOR_OVERRIDES_CEILING
        extra = {"ceiling_pos": ceiling_pos}
    step, binding = _clamp_step(
        constraints,
        low=floor_wins,
        from_pos=judgment.effective_winner_pos,
        to_pos=judgment.final_pos,
        code=code,
        extra=extra,
    )
    return [step], [binding]


def _iter_nonbinding_bounds(constraints: list, axis: str, bindings):
    """Yield the active bounded constraints on ``axis`` that did not bind.

    Shared by the position and tilt inactive-step passes so the axis filter,
    the FIXED skip, and the ``bindings``-by-identity skip live in one place
    (CODING_GUIDELINES § No Duplication). Skipping by *identity* rather than by
    value keeps a losing tie-bound visible — two slots at the same floor no
    longer collapse into one trace entry (audit finding 4b).

    ``bindings`` is a collection because the position axis can have TWO bounds
    bind in one cycle once covers are judged individually (#1174) — a floor on
    one cover and a ceiling on another. Either of them left out of this set
    would be re-reported as having done nothing, which is the opposite of true.
    """
    for c in constraints:
        if c.axis != axis or c.kind is AxisConstraintMode.FIXED:
            continue
        if any(c is b for b in bindings):
            continue  # this bound *did* bind — already emitted as a clamp
        yield c


def _inactive_position_steps(
    constraints: list,
    *,
    winner_pos: int,
    final_pos: int,
    floor_wins: bool,
    bindings,
) -> list[DecisionStep]:
    """Explain every position bound that was active but did not bind.

    A constraint whose handler deferred would otherwise be left with an
    unhelpful ``describe_skip`` step (the registry drops those). Give each
    non-binding bound a step saying it was evaluated and why it did nothing.

    ``bindings`` holds the constraints that produced this cycle's clamps (empty
    when nothing clamped, two when a floor and a ceiling bound different
    covers); each is skipped, since it was already emitted as a matched clamp
    step.

    A ceiling the floor beat is reported as *overridden*, not *inactive*: when
    the floor won the conflict (``floor_wins``) it sits above that ceiling, so
    the ceiling was outranked, not idle — the honest wording says so (audit
    findings 7 / C).

    An inactive ceiling reports the *resolved* position, not the winner's: a
    ceiling out-composed by a lower one sits above the winner (winner 80,
    ceilings 40 + 70 → resolved 40), so 'winner 80% below ceiling 70%' is a
    lie. The resolved position is always at or below every non-binding ceiling
    (it is at or below the lowest, binding one), so it reads truthfully in both
    the out-composed case and the classic winner-already-below case where the
    resolved position equals the winner's (audit finding ii).
    """
    steps: list[DecisionStep] = []
    for c in _iter_nonbinding_bounds(constraints, AXIS_NAME_POSITION, bindings):
        if c.low is not None:
            steps.append(
                DecisionStep(
                    handler=c.source,
                    matched=False,
                    reason_payload=Reason(
                        ReasonCode.REGISTRY_FLOOR_INACTIVE,
                        {"floor_pos": c.low, "winner_pos": winner_pos},
                    ),
                    position=c.low,
                )
            )
        if c.high is not None:
            if floor_wins and c.high < final_pos:
                code, params = (
                    ReasonCode.REGISTRY_CEILING_OVERRIDDEN,
                    {"ceiling_pos": c.high, "to_pos": final_pos},
                )
            else:
                code, params = (
                    ReasonCode.REGISTRY_CEILING_INACTIVE,
                    {"ceiling_pos": c.high, "to_pos": final_pos},
                )
            steps.append(
                DecisionStep(
                    handler=c.source,
                    matched=False,
                    reason_payload=Reason(code, params),
                    position=c.high,
                )
            )
    return steps


def _inactive_tilt_steps(
    constraints: list, *, final_tilt: int, binding
) -> list[DecisionStep]:
    """Explain every tilt bound that was active but did not bind.

    The tilt-axis analog of :func:`_inactive_position_steps`: a tilt bound that
    the resolved tilt already satisfied, or one out-composed by a stricter
    bound, would otherwise be dropped with the deferral sweep and vanish from
    the trace (audit findings A / B). ``binding`` is the one bound that actually
    clamped (None when the tilt was already within every bound); it is skipped
    by identity, exactly as the position pass skips its binding constraint.

    ``final_tilt`` is the tilt *after* the axis clamp (equal to the pre-clamp
    tilt when nothing clamped). A stricter same-direction bound may have moved
    the tilt, so an out-composed bound must report the value the cover actually
    settled on — reporting the pre-clamp tilt renders a false 'already within'
    claim when the tilt was really clamped elsewhere (audit finding i).
    """
    steps: list[DecisionStep] = []
    for c in _iter_nonbinding_bounds(constraints, AXIS_NAME_TILT, (binding,)):
        steps.append(
            DecisionStep(
                handler=c.source,
                matched=False,
                reason_payload=Reason(
                    ReasonCode.REGISTRY_TILT_BOUND_INACTIVE,
                    {
                        "low_label": bound_label(c.low),
                        "high_label": bound_label(c.high),
                        "label": c.label,
                        "tilt": final_tilt,
                    },
                ),
                position=None,
                tilt=None,
            )
        )
    return steps


def _tilt_to_clamp(
    tilt_overlay: int | None,
    winner_tilt: int | None,
) -> int | None:
    """Return the tilt a bound should clamp, in precedence order.

    The FIXED overlay (issue #514) we just filled wins; otherwise the
    winner's own tilt. There is no third, merged-from-a-loser source: a
    handler the pipeline outprioritized never contributes a tilt (issue
    #1153) — ``_MERGEABLE`` no longer includes ``"tilt"``, so nothing else
    could have set one by this point. Returns None when nothing has set a
    tilt yet (the venetian engine will, post-pipeline).
    """
    if tilt_overlay is not None:
        return tilt_overlay
    return winner_tilt


class PipelineRegistry:
    """Evaluates a set of :class:`OverrideHandler` instances in priority order."""

    def __init__(
        self,
        handlers: list[OverrideHandler],
        *,
        event_buffer: EventBuffer | None = None,
    ) -> None:
        """Initialise and sort handlers by priority descending."""
        self._handlers: list[OverrideHandler] = sorted(
            handlers, key=lambda h: h.priority, reverse=True
        )
        self._event_buffer = event_buffer

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult:
        """Evaluate all handlers and return the highest-priority matching result.

        Every handler is evaluated regardless of priority so that optional data
        fields (e.g. climate_data) are populated even when a higher-priority
        handler wins the position.  The final PipelineResult carries the
        winner's position/control_method/reason plus a field-level merge of
        optional data from lower-priority handlers.

        Builds a full decision_trace of every handler evaluated.

        Raises:
            RuntimeError: if no handler matches (DefaultHandler must always match).

        """
        evaluated: list[tuple[OverrideHandler, PipelineResult | None]] = []
        for handler in self._handlers:
            evaluated.append((handler, handler.evaluate(snapshot)))

        matches = [(h, r) for h, r in evaluated if r is not None]

        if not matches:
            raise RuntimeError(  # pragma: no cover
                "Pipeline exhausted with no handler matching. "
                "Ensure a DefaultHandler (priority=0, always matches) is registered."
            )

        winning_handler, winner = matches[0]

        # Build decision trace.  The winning handler is marked matched=True.
        # Handlers that evaluated and produced a result but were outprioritized
        # are marked matched=False with an explanatory reason.  Handlers that
        # returned None get their own describe_skip() reason.
        trace: list[DecisionStep] = []
        for handler, result in evaluated:
            if result is not None:
                if handler is winning_handler:
                    trace.append(
                        DecisionStep(
                            handler=handler.name,
                            matched=True,
                            reason=result.reason,
                            reason_payload=result.reason_payload,
                            position=result.position,
                            held_position=result.held_position,
                            priority=handler.priority,
                        )
                    )
                else:
                    trace.append(
                        DecisionStep(
                            handler=handler.name,
                            matched=False,
                            reason_payload=Reason(
                                ReasonCode.REGISTRY_OUTPRIORITIZED,
                                {"handler": winning_handler.name},
                            ),
                            position=result.position,
                            priority=handler.priority,
                        )
                    )
            else:
                skip_text, skip_payload = _normalize_reason(
                    handler.describe_skip(snapshot)
                )
                trace.append(
                    DecisionStep(
                        handler=handler.name,
                        matched=False,
                        reason=skip_text,
                        reason_payload=skip_payload,
                        position=None,
                        priority=handler.priority,
                    )
                )

        # Field-level merge: fill None optional fields on the winner's result.
        # Two sources, tried in order:
        #   1. Lower-priority handlers that also matched (existing behaviour).
        #   2. Every handler's contribute() output — handlers that returned None
        #      from evaluate() (e.g. ClimateHandler deferring GLARE_CONTROL) can
        #      still surface metadata this way (Issue #240).
        # Winner's non-None values are never overwritten.
        #
        # ``tilt`` is deliberately NOT in this tuple (issue #1153). Unlike
        # climate_state/climate_strategy/climate_data, tilt is an actuation
        # output, not diagnostic metadata — a handler the pipeline explicitly
        # outprioritized must never drive the tilt axis. Tilt is owned by
        # exactly two sources: the winner's own ``PipelineResult.tilt``, and
        # the tilt-axis pass below (the #514 FIXED overlay, still
        # priority-blind, and the #943 bounds composition, which since #1170
        # yields to a higher-priority hold; see ``_tilt_to_clamp``).
        _MERGEABLE = ("climate_state", "climate_strategy", "climate_data")
        contributions: list[dict[str, object]] = [
            h.contribute(snapshot) for h, _ in evaluated
        ]
        merged: dict[str, object] = {}
        for field_name in _MERGEABLE:
            if getattr(winner, field_name) is None:
                for _, other in matches[1:]:
                    val = getattr(other, field_name)
                    if val is not None:
                        merged[field_name] = val
                        break
                else:
                    for contrib in contributions:
                        val = contrib.get(field_name)
                        if val is not None:
                            merged[field_name] = val
                            break

        # ── Axis-constraint composition (issues #463 / #514 / #943) ─────────
        # Custom-position slots and the weather override contribute *constraints*
        # — per-axis claims that clamp the winner regardless of priority, unless
        # the winner is holding a physical position (``_split_bounds_against_hold``,
        # which governs both axes).
        # The handlers themselves defer (return None); the registry composes
        # here so the arithmetic lives in exactly one place
        # (pipeline/axis_constraints.py). One gather serves both axes; the rules
        # differ by constraint *kind*, not by axis.
        #
        # Outside the user's start/end CLOCK window the gather keeps only the
        # claims ``_window_eligible`` admits (#943 item B); ``window_dropped``
        # holds the rest purely so the trace can say they were active and
        # deliberately not applied. With the clock open the split is inert and
        # ``constraints`` is byte-identical to the pre-item-B gather.
        constraints, window_dropped = partition_axis_constraints(snapshot)
        # Unconditional: with the clock open ``window_dropped`` is empty and
        # this is an identity. A dropped source's ``describe_skip`` step is
        # swept so the explicit "not applied outside the window" step replaces
        # it rather than sitting beside it and contradicting it.
        trace = _drop_trace_steps(trace, {c.source for c in window_dropped})
        # Built here, appended AFTER both axis sweeps — the same reason
        # ``yielded_steps`` below is. Those sweeps key on the SOURCE of a kept
        # claim and spare only ``matched=True`` steps, so a slot with one kept
        # claim and one dropped claim would have its replacement swept straight
        # back out and be left with no trace entry at all.
        #
        # No slot can be in that shape today: every claim of a slot carries the
        # same ``priority`` and ``outside_window``, so eligibility can only
        # differ by ``kind is FIXED``, the only FIXED claim this gather emits is
        # a tilt one, and a FIXED tilt requires ``tilt_only`` — which makes
        # ``position_mode`` NONE and leaves the slot with nothing else to keep.
        # Deferring the append costs nothing and means the explanation does not
        # depend on that chain staying true.
        window_dropped_steps = _window_dropped_steps(window_dropped)

        # Outside-window pseudo-hold (issue #943 item B) — see
        # ``_as_outside_window_pseudo_hold``.
        winner, outside_window_active = _as_outside_window_pseudo_hold(
            winner, snapshot, constraints
        )

        # Bounds allowed to move THIS winner. Identical to ``constraints``
        # unless the winner is holding a physical position, in which case a
        # bound must outrank the holder (#1170). Consumed by both axes; the
        # FIXED tilt overlay below deliberately reads the unfiltered list.
        bound_constraints, yielded_to_hold = _split_bounds_against_hold(
            constraints, winner, winning_handler.priority
        )

        # --- Position axis: bounded kinds ---
        floor_pos, ceiling_pos = compose_bounds(bound_constraints, AXIS_NAME_POSITION)
        # The position the bounds act on: where the cover will actually end
        # up.  manual_override holds the cover at held_position (its physical
        # position), not winner.position (the theoretical default it shadows),
        # so a bound must clamp against held_position when present (#534).
        # ``group_lock`` sets it too; every other handler leaves it None, so
        # this preserves the existing behaviour exactly for computed winners.
        #
        # ONE evaluation per cycle still produces ONE shared result, and every
        # singular field below keeps the value it always had. What
        # ``_judge_position_axis`` adds is a per-cover verdict for a hold
        # (#1174) — whether each bound cover is moved at all, and to where —
        # because ``held_position`` is the instance MEAN, and no single number
        # can stand in for the group once a floor and a ceiling bind different
        # covers in opposite directions. It also owns the #1036 cover→logical
        # frame conversion, now applied per cover rather than once to the mean.
        # A computed winner keeps its own position and no verdicts, exactly as
        # before.
        #
        # A floor "raises" when it lifts a cover above where it would actually
        # end up.  Key on the *effective* position — NOT on
        # ``clamped_position != winner.position`` — so the raise still fires on
        # the alignment edge where the floor equals the would-be ``position`` but
        # exceeds the held one (a manual-override hold with position==floor but
        # held below it must still be lifted; issue #809).  Because
        # ``clamp_to_bounds`` applies the floor last, a floor above a ceiling
        # still reads as a raise — the deliberate conflict rule.
        judgment = _judge_position_axis(winner, snapshot, floor_pos, ceiling_pos)
        effective_winner_pos = judgment.effective_winner_pos
        final_pos = judgment.final_pos
        raised = judgment.raised
        lowered = judgment.lowered
        hold_clamp_verdicts = judgment.verdicts
        position_clamped = raised or lowered
        # In a floor/ceiling conflict ``clamp_to_bounds`` applies the floor last,
        # so the floor always determines the final value (== floor_pos) no matter
        # where the winner started. Detect the conflict explicitly rather than
        # inferring the direction from ``final`` vs ``effective``: when the winner
        # sat *above* the floor the net move is a lowering, yet the floor — not
        # the ceiling — is what bound (audit finding C). Outside a conflict,
        # ``floor_wins`` collapses to the old ``floor_raised`` predicate.
        floor_conflict = (
            floor_pos is not None
            and ceiling_pos is not None
            and floor_pos > ceiling_pos
        )
        floor_wins = floor_conflict or raised
        # Unchanged position keeps the winner's own value (today's behaviour:
        # an inert bound must not overwrite ``position`` with ``held_position``).
        clamped_position = final_pos if position_clamped else winner.position
        # Two bounds can bind in ONE cycle once each cover is judged on its own
        # position (#1174): a floor lifting the low cover while a ceiling lowers
        # the high one. ``final_pos`` is a single number and ``clamp_to_bounds``
        # applies the floor last, so the shared field can only ever name the
        # floor's edge — leaving the ceiling to fall through to the "did
        # nothing" sweep and be reported as inactive while it was busy moving a
        # cover to 70. So each side that really bound states itself. Excluded in
        # a floor/ceiling CONFLICT, where the floor alone produced both
        # directions and the ceiling bound nobody.
        two_sided = raised and lowered and not floor_conflict
        # The constraints that actually bound — resolved back from the composed
        # values so the trace credits the slots that produced the moves, not the
        # join of every active bound (audit finding 4a).
        clamp_steps, position_bindings = _position_clamp_steps(
            bound_constraints,
            judgment,
            floor_pos=floor_pos,
            ceiling_pos=ceiling_pos,
            floor_wins=floor_wins,
            two_sided=two_sided,
        )
        trace.extend(clamp_steps)
        # Replace the deferral steps of the position-bound sources — those
        # steps came from the deferral path and carry an unhelpful describe_skip
        # reason. Only position sources are swept here so a tilt-only / tilt-
        # bound slot's step is left for the tilt pass to handle (audit finding
        # 4c); the matched winner's step is never dropped (finding 1).
        trace = _drop_trace_steps(
            trace,
            {c.source for c in constraints if c.axis == AXIS_NAME_POSITION},
        )
        trace.extend(
            _inactive_position_steps(
                bound_constraints,
                winner_pos=effective_winner_pos,
                final_pos=final_pos,
                floor_wins=floor_wins,
                bindings=position_bindings,
            )
        )
        # A yielded bound must NOT fall through to the inactive steps above:
        # "floor 40% inactive (winner 27% above floor)" is simply false — the
        # bound would have bound, and priority is the only reason it did not.
        # It still needs a step of its own, because the deferral sweep already
        # removed the handler's ``describe_skip`` entry (#1170).
        #
        # Built here, appended AFTER the tilt pass: a slot carrying both a
        # position floor and a tilt bound is in the tilt sweep's source set, so
        # appending now would have this step dropped again a few lines below and
        # leave that slot with no trace entry at all.
        yielded_steps = [
            DecisionStep(
                handler=c.source,
                matched=False,
                reason_payload=Reason(
                    ReasonCode.REGISTRY_BOUND_YIELDED_TO_HOLD,
                    {
                        "low_label": bound_label(c.low),
                        "high_label": bound_label(c.high),
                        "label": c.label,
                        "priority": c.priority,
                        "holder": winning_handler.name,
                        "holder_priority": winning_handler.priority,
                        # A slot can bound BOTH axes and yield on both; without
                        # the axis the two steps render identically (#1170).
                        "axis": _axis_fragment(c.axis),
                    },
                ),
                # Same column ``_inactive_position_steps`` fills for the same
                # kind of claim — a yielded floor must not blank the card's
                # position where an inert one shows its value. Tilt-axis claims
                # leave it None, as the tilt pass does. A RANGE claim shows its
                # floor; both edges are spelled out in the reason text, which is
                # where ``_inactive_position_steps`` splits into two steps and
                # this one deliberately does not.
                position=(
                    (c.low if c.low is not None else c.high)
                    if c.axis == AXIS_NAME_POSITION
                    else None
                ),
            )
            for c in yielded_to_hold
        ]

        # --- Tilt axis ---
        # FIXED (tilt-only, issue #514) fills the tilt when the winner left it
        # unset; the bounded kinds (#943) clamp the tilt once something has set
        # it. Both rules are the kind's rule, applied here on the tilt axis
        # exactly as they are on the position axis above.
        tilt_contribution = resolve_tilt_axis_from(constraints)
        tilt_overlay: int | None = None
        tilt_only_active = False
        # Slot number of the tilt-only contribution that was actually applied —
        # surfaced in the Control Status string (#667). Stays None when the
        # contribution is deferred (winner already set tilt).
        tilt_only_slot_applied: int | None = None
        if tilt_contribution is not None:
            tilt_only_active = True
            # Replace any trace step for the contributing slot — it came from the
            # handler's deferral path and carries an unhelpful describe_skip
            # reason (mirrors the floor pass's step-replacement).
            trace = _drop_trace_steps(trace, {tilt_contribution.source})
            if winner.tilt is None:
                tilt_overlay = tilt_contribution.tilt
                tilt_only_slot_applied = tilt_contribution.slot
                trace.append(
                    DecisionStep(
                        handler=tilt_contribution.source,
                        matched=True,
                        reason_payload=Reason(
                            ReasonCode.REGISTRY_TILT_APPLIED,
                            {
                                "tilt": tilt_contribution.tilt,
                                "label": tilt_contribution.label,
                                "handler": winning_handler.name,
                            },
                        ),
                        position=None,
                        tilt=tilt_contribution.tilt,
                    )
                )
            else:
                trace.append(
                    DecisionStep(
                        handler=tilt_contribution.source,
                        matched=False,
                        reason_payload=Reason(
                            ReasonCode.REGISTRY_TILT_DEFERRED,
                            {
                                "tilt": tilt_contribution.tilt,
                                "handler": winning_handler.name,
                                "winner_tilt": winner.tilt,
                            },
                        ),
                        position=None,
                        tilt=tilt_contribution.tilt,
                    )
                )

        # Bounded tilt constraints (issue #943). Unlike the FIXED overlay above,
        # a bound clamps a tilt that is already set — the exact case the
        # fill-when-unset branch skips.
        tilt_low, tilt_high = compose_bounds(bound_constraints, AXIS_NAME_TILT)
        # Replace the deferral skips of the bounded tilt sources — they are
        # re-explained by the clamp / bound-active step below (a losing FIXED
        # tilt-only slot is deliberately left out of this set so its step
        # survives; audit finding 4c). Matched winner steps are protected.
        trace = _drop_trace_steps(
            trace,
            {
                c.source
                # UNFILTERED, mirroring the position sweep: a slot whose bound
                # yielded to a hold still deferred, so its ``describe_skip``
                # step must go — otherwise a stale "not active" entry survives
                # beside the yielded step and contradicts it (#1170).
                for c in constraints
                if c.axis == AXIS_NAME_TILT and c.kind is not AxisConstraintMode.FIXED
            },
        )
        # Past every sweep now — safe to state why a position bound yielded
        # even when the same slot also bounds the tilt (#1170), and why an
        # ineligible claim was not applied at all (#943 item B).
        trace.extend(window_dropped_steps)
        trace.extend(yielded_steps)
        # The tilt to clamp is, in precedence order: the FIXED overlay we just
        # filled, or the winner's own tilt (see ``_tilt_to_clamp``). #943's
        # audit finding 2 — a configured minimum silently violated by a tilt
        # that reached the result via merge from a lower-priority handler —
        # no longer applies: issue #1153 removed that merge path entirely, so
        # there is nothing left for a bound to miss.
        resolved_tilt = _tilt_to_clamp(tilt_overlay, winner.tilt)
        tilt_clamped = False
        carried_low: int | None = None
        carried_high: int | None = None
        carried_label: str | None = None
        if tilt_low is not None or tilt_high is not None:
            tilt_label = constraint_label(bound_constraints, AXIS_NAME_TILT)
            if resolved_tilt is not None:
                bounded_tilt = clamp_to_bounds(resolved_tilt, tilt_low, tilt_high)
                tilt_binding = None
                if bounded_tilt != resolved_tilt:
                    tilt_clamped = True
                    tilt_binding = bounding_constraint(
                        bound_constraints,
                        AXIS_NAME_TILT,
                        bounded_tilt,
                        low=bounded_tilt > resolved_tilt,
                    )
                    trace.append(
                        tilt_clamp_step(
                            from_tilt=resolved_tilt,
                            to_tilt=bounded_tilt,
                            label=(
                                tilt_binding.label
                                if tilt_binding is not None
                                else tilt_label
                            ),
                            source="tilt_clamp",
                        )
                    )
                    if tilt_overlay is not None:
                        tilt_overlay = bounded_tilt
                    else:
                        merged["tilt"] = bounded_tilt
                # Every active tilt bound that did not bind still explains itself
                # — the tilt-axis analog of the position inactive steps. Covers a
                # bound the resolved tilt already satisfied (nothing clamped) and
                # one out-composed by a stricter bound; ``tilt_binding`` (the one
                # that did clamp, or None) is skipped by identity so it is not
                # both clamped and inactive (audit findings A / B).
                trace.extend(
                    _inactive_tilt_steps(
                        bound_constraints,
                        final_tilt=bounded_tilt,
                        binding=tilt_binding,
                    )
                )
            elif outside_window_active:
                # Nothing resolved a tilt AND the clock window is closed, so
                # carrying the bounds is not an option: ``PipelineSnapshot``
                # holds no tilt reads, and ``VenetianPolicy.post_pipeline_resolve``
                # DROPS carried bounds on its engine-suppressed branch — which
                # is exactly the branch a DEFAULT winner takes. So the registry
                # resolves the tilt itself, to the bound's OWN edge and never to
                # any handler's tilt (#1153). The floor edge wins a two-sided
                # bound for the same reason ``clamp_to_bounds`` applies the
                # floor last: a floor is the protection commitment.
                #
                # Setting ``merged["tilt"]`` mirrors the #514 overlay's
                # transport, so the policy's "handler tilt honored" path carries
                # it and the engine-suppressed branch is never reached with live
                # bounds. Treated as a tilt clamp because it is one: the axis
                # forced a dispatch and the position must be carried, not
                # invented.
                enforced_tilt = tilt_low if tilt_low is not None else tilt_high
                tilt_clamped = True
                merged["tilt"] = enforced_tilt
                trace.append(
                    DecisionStep(
                        handler="tilt_clamp",
                        matched=True,
                        reason_payload=Reason(
                            ReasonCode.REGISTRY_TILT_BOUND_ENFORCED,
                            {
                                "low_label": bound_label(tilt_low),
                                "high_label": bound_label(tilt_high),
                                "label": tilt_label,
                                "tilt": enforced_tilt,
                            },
                        ),
                        position=None,
                        tilt=enforced_tilt,
                    )
                )
            else:
                # Nothing has resolved a tilt yet. It still can: the venetian
                # engine fills tilt after the pipeline, in
                # ``VenetianPolicy.post_pipeline_resolve``. Carry the bounds so
                # that policy can apply them through the same shared clamp.
                carried_low, carried_high = tilt_low, tilt_high
                carried_label = tilt_label
                trace.append(
                    DecisionStep(
                        handler="tilt_clamp",
                        matched=False,
                        reason_payload=Reason(
                            ReasonCode.REGISTRY_TILT_BOUND_ACTIVE,
                            {
                                "low_label": bound_label(tilt_low),
                                "high_label": bound_label(tilt_high),
                                "label": tilt_label,
                            },
                        ),
                        position=None,
                    )
                )

        # Propagate sunset-window flags from the snapshot.
        # NOTE: configured_default and configured_sunset_pos are
        # intentionally left at their defaults (0 / None) here.
        # The coordinator annotates them via dataclasses.replace()
        # after evaluation so they never appear in the snapshot
        # that handlers can read.
        if position_clamped:
            # A position clamp must reach the cover even when the winner is a
            # hold (manual-override / motion): clear skip_command so the composed
            # result is dispatched, not suppressed (issue #809 / #534).
            # position_constraint_applied records only that a user-configured
            # bound clamped this winner — a floor raise (#463) or a ceiling
            # lower (#943) — which is what drives that forced dispatch and the
            # reason labelling. It makes NO claim about the value's frame: the
            # composed position is logical, exactly like the winner's own was
            # (#1036).
            winner = dataclasses.replace(
                winner,
                position=clamped_position,
                position_constraint_applied=True,
                skip_command=False,
            )
        if tilt_clamped:
            winner = _release_hold_for_tilt_clamp(
                winner,
                trace,
                position_clamped=position_clamped,
                effective_winner_pos=effective_winner_pos,
            )
            # The tilt clamp commands the whole group, so every verdict says so
            # — each still to its OWN target, which is the only thing that
            # keeps a cover the position axis never released where the user put
            # it (#1174).
            hold_clamp_verdicts = _command_every_held_cover(hold_clamp_verdicts)
        # The tilt-axis overlay (issue #514) is how a FIXED contribution
        # reaches the result's tilt field. It can never collide with the
        # bound-clamp write above (the ``else: merged["tilt"] = bounded_tilt``
        # branch only fires when ``tilt_overlay`` is None) — this is simply
        # the transport for a (possibly bound-clamped) tilt-only contribution.
        if tilt_overlay is not None:
            merged["tilt"] = tilt_overlay
        # ── Outside-window admission (issue #943 item B) ────────────────────
        # Set ONLY when the clock window is closed AND an eligible constraint
        # actually bound something this cycle. Deliberately NOT ``is_safety``:
        # that flag's one-writer lifetime (#1226/#1165) stays untouched, and a
        # constraint dispatch must not inherit safety's other licences (auto
        # control off, surviving ``clear_non_safety_targets``).
        #
        # Gating on the CLOSED window is what keeps the end-of-window sweep
        # semantics intact (#215/#216): an in-window booking never carries the
        # flag, so nothing new survives ``clear_non_safety_targets()``.
        outside_window_admitted = outside_window_active and (
            position_clamped or tilt_clamped
        )
        if outside_window_active:
            # The pseudo-hold was scaffolding for the position axis; strip it
            # before the result leaves. ``held_position`` is user-facing (the
            # Target Position sensor, the Model B stash replay in
            # ``DayNightShadePolicy``) and a DEFAULT result must not start
            # claiming to hold anything.
            winner = dataclasses.replace(winner, held_position=None)
            if outside_window_admitted:
                # ── Axis scoping ─────────────────────────────────────────────
                # An admitted cycle dispatches, so every field it carries
                # reaches hardware — and the invariant allows only two kinds of
                # value out here: what an eligible constraint composed, and
                # where the cover already is. The position axis gets the second
                # from the pseudo-hold. These two fields have no such fallback,
                # so they are emptied instead:
                #
                # * ``tilt`` — ``PipelineSnapshot`` carries ``cover_positions``
                #   but no tilt reads, so "where the slats already are" is not
                #   knowable. ``merged`` holds a tilt only when the composition
                #   above wrote one (the bound clamp, the resolved bound edge,
                #   or the #514 FIXED overlay — every one of them an eligible
                #   constraint's value), so its ABSENCE is exactly the case
                #   where the result would otherwise fall through to the
                #   winner's own ``default_tilt`` / ``sunset_tilt``. None sends
                #   nothing on that axis: ``position_context_overrides`` omits
                #   the key, ``PositionContext.tilt`` stays None, and
                #   ``maybe_update_tilt_only``'s guard never opens — the slats
                #   stay where the user left them.
                # * ``use_my_position`` — the winner's own routing. It would
                #   land a cover without ``set_cover_position`` on its hardware
                #   My preset instead of the bound edge just resolved.
                #
                # A safety winner never reaches here — it is clause (a), and its
                # own values are precisely what must go out.
                merged.setdefault("tilt", None)
                winner = dataclasses.replace(winner, use_my_position=False)
            else:
                # Nothing bound ⇒ nothing to dispatch ⇒ the pseudo-hold is
                # discarded whole, verdicts included: a DEFAULT winner must not
                # leave the registry carrying per-cover "held, not released"
                # verdicts nobody asked for. The result is then byte-identical
                # to its pre-item-B self, diagnostics included.
                hold_clamp_verdicts = None
        result = dataclasses.replace(
            winner,
            decision_trace=trace,
            outside_window_constraint_active=outside_window_admitted,
            # Per-cover dispatch verdicts for a hold (#1174) — which covers are
            # commanded and where each one goes. None for a computed winner and
            # for a snapshot without per-entity positions, both of which keep
            # that cycle on the singular path.
            hold_clamp_verdicts=hold_clamp_verdicts,
            default_position=snapshot.default_position,
            is_sunset_active=snapshot.is_sunset_active,
            tilt_only_contribution_active=tilt_only_active,
            tilt_only_slot=tilt_only_slot_applied,
            tilt_low=carried_low,
            tilt_high=carried_high,
            tilt_bound_label=carried_label,
            **merged,
        )
        if self._event_buffer is not None:
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "pipeline_evaluated",
                    "entity_id": "",
                    "winning_handler": winning_handler.name,
                    "winning_priority": winning_handler.priority,
                    "control_method": (
                        result.control_method.value
                        if hasattr(result.control_method, "value")
                        else str(result.control_method)
                    ),
                    "position": result.position,
                    "reason": result.reason,
                    "bypass_auto_control": result.bypass_auto_control,
                    "position_constraint_applied": result.position_constraint_applied,
                    "outside_window_constraint_active": (
                        result.outside_window_constraint_active
                    ),
                    "is_sunset_active": result.is_sunset_active,
                }
            )
        return result
