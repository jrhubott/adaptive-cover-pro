"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

import dataclasses
import datetime as dt

from ..diagnostics.event_buffer import EventBuffer
from .floors import effective_floor, gather_active_floors
from .handler import OverrideHandler
from .types import DecisionStep, PipelineResult, PipelineSnapshot


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
                            position=result.position,
                        )
                    )
                else:
                    trace.append(
                        DecisionStep(
                            handler=handler.name,
                            matched=False,
                            reason=f"outprioritized by {winning_handler.name}",
                            position=result.position,
                        )
                    )
            else:
                trace.append(
                    DecisionStep(
                        handler=handler.name,
                        matched=False,
                        reason=handler.describe_skip(snapshot),
                        position=None,
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
        clamped_position = winner.position
        if floor_info is not None and floor_pos > winner.position:
            clamped_position = floor_pos
            trace.append(
                DecisionStep(
                    handler="floor_clamp",
                    matched=True,
                    reason=(
                        f"floor raised winner from {winner.position}% to "
                        f"{floor_pos}% by {floor_info.label}"
                    ),
                    position=floor_pos,
                )
            )
        # Replace any existing trace step whose handler matches an active
        # floor's source — those steps came from the deferral path and
        # carry an unhelpful describe_skip reason.  We give them a fresh
        # entry that explains the floor was active but did not win.
        floor_sources = {info.source for info in active_floors}
        trace = [step for step in trace if step.handler not in floor_sources]
        for info in active_floors:
            if (
                floor_info is not None
                and info is floor_info
                and floor_pos > winner.position
            ):
                continue  # this floor *did* win — already emitted as floor_clamp
            trace.append(
                DecisionStep(
                    handler=info.source,
                    matched=False,
                    reason=(
                        f"floor {info.position}% inactive "
                        f"(winner {winner.position}% above floor)"
                    ),
                    position=info.position,
                )
            )

        # Propagate sunset-window flags from the snapshot.
        # NOTE: configured_default and configured_sunset_pos are
        # intentionally left at their defaults (0 / None) here.
        # The coordinator annotates them via dataclasses.replace()
        # after evaluation so they never appear in the snapshot
        # that handlers can read.
        if clamped_position != winner.position:
            winner = dataclasses.replace(
                winner,
                position=clamped_position,
                floor_clamp_applied=True,
            )
        result = dataclasses.replace(
            winner,
            decision_trace=trace,
            default_position=snapshot.default_position,
            is_sunset_active=snapshot.is_sunset_active,
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
