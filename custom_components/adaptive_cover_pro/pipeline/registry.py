"""Pipeline registry — evaluates handlers in priority order."""

from __future__ import annotations

from .handler import OverrideHandler
from .types import DecisionStep, PipelineContext, PipelineResult


class PipelineRegistry:
    """Evaluates a set of :class:`OverrideHandler` instances in priority order.

    Usage::

        registry = PipelineRegistry([ForceOverrideHandler(), DefaultHandler(), ...])
        result = registry.evaluate(ctx)
    """

    def __init__(self, handlers: list[OverrideHandler]) -> None:
        """Initialise the registry with the given handlers.

        Handlers are sorted by priority (highest first) so that the most
        important conditions are always evaluated before lower-priority ones.
        """
        # Sort descending by priority so highest priority is evaluated first.
        self._handlers: list[OverrideHandler] = sorted(
            handlers, key=lambda h: h.priority, reverse=True
        )

    def evaluate(self, ctx: PipelineContext) -> PipelineResult:
        """Evaluate all handlers and return the first matching result.

        Builds a full :attr:`PipelineResult.decision_trace` of every handler
        evaluated (matched or skipped).

        Raises:
            RuntimeError: if no handler matches (DefaultHandler must always match).

        """
        trace: list[DecisionStep] = []

        for index, handler in enumerate(self._handlers):
            result = handler.evaluate(ctx)

            if result is not None:
                # Record the winning handler.
                trace.append(
                    DecisionStep(
                        handler=handler.name,
                        matched=True,
                        reason=result.reason,
                        position=result.position,
                    )
                )
                # Record all remaining handlers as skipped.
                for skipped in self._handlers[index + 1 :]:
                    trace.append(
                        DecisionStep(
                            handler=skipped.name,
                            matched=False,
                            reason="skipped (higher priority matched)",
                            position=None,
                        )
                    )
                # Return a new result that carries the full trace.
                return PipelineResult(
                    position=result.position,
                    control_method=result.control_method,
                    reason=result.reason,
                    decision_trace=trace,
                )

            # Handler did not match — record why and continue.
            trace.append(
                DecisionStep(
                    handler=handler.name,
                    matched=False,
                    reason=handler.describe_skip(ctx),
                    position=None,
                )
            )

        raise RuntimeError(  # pragma: no cover
            "Pipeline exhausted with no handler matching. "
            "Ensure a DefaultHandler (priority=0, always matches) is registered."
        )
