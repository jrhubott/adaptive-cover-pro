"""Default handler — always matches as the final fallback."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult


class DefaultHandler(OverrideHandler):
    """Return the default position as the final fallback.

    Priority 0 — evaluated last, always matches.
    Used when the sun is outside the FOV, outside the time window, or
    no other handler has claimed the position.
    """

    name = "default"
    priority = 0

    def evaluate(self, ctx: PipelineContext) -> PipelineResult:
        """Return the default position as the final fallback."""
        return PipelineResult(
            position=ctx.default_position,
            control_method=ControlMethod.DEFAULT,
            reason=f"no active condition — default position {ctx.default_position}%",
            decision_trace=[],
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """DefaultHandler always matches — this should never be called."""
        return "always matches"
