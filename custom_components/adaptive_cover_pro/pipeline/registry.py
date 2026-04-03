"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

from .handler import OverrideHandler
from .types import DecisionStep, PipelineResult, PipelineSnapshot


class PipelineRegistry:
    """Evaluates a set of :class:`OverrideHandler` instances in priority order."""

    def __init__(self, handlers: list[OverrideHandler]) -> None:
        """Initialise and sort handlers by priority descending."""
        self._handlers: list[OverrideHandler] = sorted(
            handlers, key=lambda h: h.priority, reverse=True
        )

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult:
        """Evaluate all handlers and return the first matching result.

        Builds a full decision_trace of every handler evaluated.

        Raises:
            RuntimeError: if no handler matches (DefaultHandler must always match).

        """
        trace: list[DecisionStep] = []

        for index, handler in enumerate(self._handlers):
            result = handler.evaluate(snapshot)

            if result is not None:
                trace.append(
                    DecisionStep(
                        handler=handler.name,
                        matched=True,
                        reason=result.reason,
                        position=result.position,
                    )
                )
                for skipped in self._handlers[index + 1 :]:
                    trace.append(
                        DecisionStep(
                            handler=skipped.name,
                            matched=False,
                            reason="skipped (higher priority matched)",
                            position=None,
                        )
                    )
                return PipelineResult(
                    position=result.position,
                    control_method=result.control_method,
                    reason=result.reason,
                    decision_trace=trace,
                    climate_state=result.climate_state,
                    climate_strategy=result.climate_strategy,
                )

            trace.append(
                DecisionStep(
                    handler=handler.name,
                    matched=False,
                    reason=handler.describe_skip(snapshot),
                    position=None,
                )
            )

        raise RuntimeError(  # pragma: no cover
            "Pipeline exhausted with no handler matching. "
            "Ensure a DefaultHandler (priority=0, always matches) is registered."
        )
