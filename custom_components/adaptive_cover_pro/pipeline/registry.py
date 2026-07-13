"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

import dataclasses
import datetime as dt

from ..const import ReasonCode
from ..diagnostics.event_buffer import EventBuffer
from ..reason_i18n import Reason, render_en
from .floors import effective_floor, gather_active_floors
from .handler import OverrideHandler
from .tilt_axis import resolve_tilt_axis
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

        # Floor-mode composition (issue #463).  Custom-position slots,
        # weather override, and force override in min_mode each contribute
        # a "floor" — a minimum position that must clamp the winner regardless
        # of priority.  The handlers themselves defer (return None) in floor
        # mode; the registry composes the effective floor here so the
        # arithmetic lives in exactly one place (pipeline/floors.py).
        active_floors = gather_active_floors(snapshot)
        floor_pos, floor_info = effective_floor(active_floors)
        # The position the floor must raise: where the cover will actually end
        # up.  manual_override holds the cover at held_position (its physical
        # position), not winner.position (the theoretical default it shadows),
        # so the floor must clamp against held_position when present (#534).
        # Every other handler leaves held_position=None, so this preserves the
        # existing behaviour exactly.
        effective_winner_pos = (
            winner.held_position
            if winner.held_position is not None
            else winner.position
        )
        # A floor "raises" when it sits above where the cover will actually end
        # up (its effective position).  Key on this — NOT on
        # ``clamped_position != winner.position`` — so the raise still fires on
        # the alignment edge where the floor equals the would-be ``position`` but
        # exceeds the held one (a manual-override hold with position==floor but
        # held below it must still be lifted; issue #809).
        floor_raised = floor_info is not None and floor_pos > effective_winner_pos
        clamped_position = floor_pos if floor_raised else winner.position
        if floor_raised:
            trace.append(
                DecisionStep(
                    handler="floor_clamp",
                    matched=True,
                    reason_payload=Reason(
                        ReasonCode.REGISTRY_FLOOR_RAISED,
                        {
                            "from_pos": effective_winner_pos,
                            "to_pos": floor_pos,
                            "label": floor_info.label,
                        },
                    ),
                    position=floor_pos,
                )
            )
        # Replace any existing trace step whose handler matches an active
        # floor's source — those steps came from the deferral path and
        # carry an unhelpful describe_skip reason.  We give them a fresh
        # entry that explains the floor was active but did not win.
        floor_sources = {info.source for info in active_floors}
        trace = _drop_trace_steps(trace, floor_sources)
        for info in active_floors:
            if floor_raised and info is floor_info:
                continue  # this floor *did* win — already emitted as floor_clamp
            trace.append(
                DecisionStep(
                    handler=info.source,
                    matched=False,
                    reason_payload=Reason(
                        ReasonCode.REGISTRY_FLOOR_INACTIVE,
                        {
                            "floor_pos": info.position,
                            "winner_pos": effective_winner_pos,
                        },
                    ),
                    position=info.position,
                )
            )

        # Tilt-axis overlay (issue #514).  A per-slot "tilt-only" custom
        # position fixes the slat angle without claiming position — the slot's
        # handler defers (returns None) and this pass overlays its tilt onto
        # whichever handler won position.  Fill-when-unset: the overlay only
        # applies when the winner's own tilt is None, so a position-winner that
        # already set an explicit tilt keeps it (decision Q1b).  Resolution is
        # cover-type-agnostic and lives in pipeline/tilt_axis.py.
        tilt_contribution = resolve_tilt_axis(snapshot)
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

        # Propagate sunset-window flags from the snapshot.
        # NOTE: configured_default and configured_sunset_pos are
        # intentionally left at their defaults (0 / None) here.
        # The coordinator annotates them via dataclasses.replace()
        # after evaluation so they never appear in the snapshot
        # that handlers can read.
        if floor_raised:
            # A floor raise must reach the cover even when the winner is a hold
            # (manual-override / motion): clear skip_command so the composed
            # result is dispatched, not suppressed (issue #809 / #534).
            winner = dataclasses.replace(
                winner,
                position=clamped_position,
                floor_clamp_applied=True,
                skip_command=False,
            )
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
