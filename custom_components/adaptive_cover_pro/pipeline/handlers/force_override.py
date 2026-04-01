"""Force override handler — highest priority safety override."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult


class ForceOverrideHandler(OverrideHandler):
    """Return the force-override position when a safety sensor is active.

    Priority 100 — evaluated before all other handlers.
    """

    name = "force_override"
    priority = 100

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return override position when force override is active."""
        if not ctx.force_override_active:
            return None
        return PipelineResult(
            position=ctx.force_override_position,
            control_method=ControlMethod.FORCE,
            reason=f"force override active — position {ctx.force_override_position}%",
            decision_trace=[],
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when force override is not active."""
        return "force override not active"
