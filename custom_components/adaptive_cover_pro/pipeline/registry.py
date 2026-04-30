"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

import datetime as dt

from ..diagnostics.event_buffer import EventBuffer
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

        result = PipelineResult(
            position=winner.position,
            control_method=winner.control_method,
            reason=winner.reason,
            decision_trace=trace,
            tilt=merged.get("tilt", winner.tilt),  # type: ignore[arg-type]
            raw_calculated_position=winner.raw_calculated_position,
            climate_state=merged.get("climate_state", winner.climate_state),  # type: ignore[arg-type]
            climate_strategy=merged.get("climate_strategy", winner.climate_strategy),  # type: ignore[arg-type]
            climate_data=merged.get("climate_data", winner.climate_data),
            bypass_auto_control=winner.bypass_auto_control,
            # Propagate sunset-window flags from the snapshot.
            # NOTE: configured_default and configured_sunset_pos are
            # intentionally left at their defaults (0 / None) here.
            # The coordinator annotates them via dataclasses.replace()
            # after evaluation so they never appear in the snapshot
            # that handlers can read.
            default_position=snapshot.default_position,
            is_sunset_active=snapshot.is_sunset_active,
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
                    "is_sunset_active": result.is_sunset_active,
                }
            )
        return result
