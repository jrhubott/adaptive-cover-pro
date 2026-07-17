"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

import dataclasses
import datetime as dt

from ..const import AxisConstraintMode, ReasonCode
from ..cover_types.base import AXIS_NAME_POSITION, AXIS_NAME_TILT
from ..diagnostics.event_buffer import EventBuffer
from ..reason_i18n import Reason, render_en
from .axis_constraints import (
    bound_label,
    clamp_to_bounds,
    compose_bounds,
    constraint_label,
    gather_axis_constraints,
    tilt_clamp_step,
)
from .handler import OverrideHandler
from .tilt_axis import resolve_tilt_axis_from
from .types import DecisionStep, PipelineResult, PipelineSnapshot


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
    """Remove trace steps whose handler is one of ``sources``.

    Both the floor pass and the tilt-axis pass re-emit fresh trace steps for
    handlers that *deferred* (returned None) so the registry can replace the
    handler's unhelpful ``describe_skip`` entry. They share this removal step
    so the dedup logic lives in one place (CODING_GUIDELINES § No Duplication).
    """
    return [step for step in trace if step.handler not in sources]


def _inactive_position_steps(
    constraints: list,
    *,
    winner_pos: int,
    bound_pos: int | None,
    floor_raised: bool,
) -> list[DecisionStep]:
    """Explain every position bound that was active but did not bind.

    A constraint whose handler deferred would otherwise be left with an
    unhelpful ``describe_skip`` step (the registry drops those). Give each
    non-binding bound a step saying it was evaluated and why it did nothing —
    the floor/ceiling pair mirror each other exactly.

    ``bound_pos`` is the value that *did* bind this cycle (None when nothing
    clamped); the bound that produced it is skipped, since it was already
    emitted as the matched clamp step.
    """
    steps: list[DecisionStep] = []
    for c in constraints:
        if c.axis != AXIS_NAME_POSITION or c.kind is AxisConstraintMode.FIXED:
            continue
        bound_value = c.low if floor_raised else c.high
        if bound_pos is not None and bound_value == bound_pos:
            continue  # this bound *did* bind — already emitted as the clamp
        for value, code, key in (
            (c.low, ReasonCode.REGISTRY_FLOOR_INACTIVE, "floor_pos"),
            (c.high, ReasonCode.REGISTRY_CEILING_INACTIVE, "ceiling_pos"),
        ):
            if value is None:
                continue
            steps.append(
                DecisionStep(
                    handler=c.source,
                    matched=False,
                    reason_payload=Reason(code, {key: value, "winner_pos": winner_pos}),
                    position=value,
                )
            )
    return steps


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
        _MERGEABLE = ("climate_state", "climate_strategy", "climate_data", "tilt")
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
        # — per-axis claims that must clamp the winner regardless of priority.
        # The handlers themselves defer (return None); the registry composes
        # here so the arithmetic lives in exactly one place
        # (pipeline/axis_constraints.py). One gather serves both axes; the rules
        # differ by constraint *kind*, not by axis.
        constraints = gather_axis_constraints(snapshot)

        # --- Position axis: bounded kinds, always-clamp ---
        floor_pos, ceiling_pos = compose_bounds(constraints, AXIS_NAME_POSITION)
        # The position the bounds act on: where the cover will actually end
        # up.  manual_override holds the cover at held_position (its physical
        # position), not winner.position (the theoretical default it shadows),
        # so a bound must clamp against held_position when present (#534).
        # Every other handler leaves held_position=None, so this preserves the
        # existing behaviour exactly.
        effective_winner_pos = (
            winner.held_position
            if winner.held_position is not None
            else winner.position
        )
        final_pos = clamp_to_bounds(effective_winner_pos, floor_pos, ceiling_pos)
        # A floor "raises" when it lifts the cover above where it would actually
        # end up.  Key on the *effective* position — NOT on
        # ``clamped_position != winner.position`` — so the raise still fires on
        # the alignment edge where the floor equals the would-be ``position`` but
        # exceeds the held one (a manual-override hold with position==floor but
        # held below it must still be lifted; issue #809).  Because
        # ``clamp_to_bounds`` applies the floor last, a floor above a ceiling
        # still reads as a raise — the deliberate conflict rule.
        floor_raised = final_pos > effective_winner_pos
        ceiling_lowered = final_pos < effective_winner_pos
        position_clamped = floor_raised or ceiling_lowered
        # Unchanged position keeps the winner's own value (today's behaviour:
        # an inert bound must not overwrite ``position`` with ``held_position``).
        clamped_position = final_pos if position_clamped else winner.position
        if position_clamped:
            code, source = (
                (ReasonCode.REGISTRY_FLOOR_RAISED, "floor_clamp")
                if floor_raised
                else (ReasonCode.REGISTRY_CEILING_LOWERED, "ceiling_clamp")
            )
            trace.append(
                DecisionStep(
                    handler=source,
                    matched=True,
                    reason_payload=Reason(
                        code,
                        {
                            "from_pos": effective_winner_pos,
                            "to_pos": final_pos,
                            "label": constraint_label(constraints, AXIS_NAME_POSITION),
                        },
                    ),
                    position=final_pos,
                )
            )
        # Replace any existing trace step whose handler matches a constraint's
        # source — those steps came from the deferral path and carry an
        # unhelpful describe_skip reason.  We give them a fresh entry that
        # explains the constraint was active but did not bind.
        trace = _drop_trace_steps(trace, {c.source for c in constraints})
        trace.extend(
            _inactive_position_steps(
                constraints,
                winner_pos=effective_winner_pos,
                bound_pos=final_pos if position_clamped else None,
                floor_raised=floor_raised,
            )
        )

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
        # fill-when-unset branch skips. The tilt to clamp is the winner's own,
        # or the overlay we just filled in.
        tilt_low, tilt_high = compose_bounds(constraints, AXIS_NAME_TILT)
        resolved_tilt = winner.tilt if tilt_overlay is None else tilt_overlay
        tilt_clamped = False
        carried_low: int | None = None
        carried_high: int | None = None
        carried_label: str | None = None
        if tilt_low is not None or tilt_high is not None:
            tilt_label = constraint_label(constraints, AXIS_NAME_TILT)
            if resolved_tilt is not None:
                bounded_tilt = clamp_to_bounds(resolved_tilt, tilt_low, tilt_high)
                if bounded_tilt != resolved_tilt:
                    tilt_clamped = True
                    trace.append(
                        tilt_clamp_step(
                            from_tilt=resolved_tilt,
                            to_tilt=bounded_tilt,
                            label=tilt_label,
                            source="tilt_clamp",
                        )
                    )
                    if tilt_overlay is not None:
                        tilt_overlay = bounded_tilt
                    else:
                        merged["tilt"] = bounded_tilt
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
            # floor_clamp_applied marks the position as already in cover space
            # so the coordinator skips interpolation (#469) — true of a ceiling
            # lower for the same reason it is true of a floor raise.
            winner = dataclasses.replace(
                winner,
                position=clamped_position,
                floor_clamp_applied=True,
                skip_command=False,
            )
        if tilt_clamped:
            # Same rule on the tilt axis: a clamp is a command, not a no-op.
            winner = dataclasses.replace(winner, skip_command=False)
        # The dedicated tilt-axis overlay (issue #514) wins the tilt field over
        # the generic _MERGEABLE tilt fill — both only fire when the winner's
        # own tilt is None, but a tilt-only contribution is explicit user intent.
        if tilt_overlay is not None:
            merged["tilt"] = tilt_overlay
        result = dataclasses.replace(
            winner,
            decision_trace=trace,
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
                    "floor_clamp_applied": result.floor_clamp_applied,
                    "is_sunset_active": result.is_sunset_active,
                }
            )
        return result
