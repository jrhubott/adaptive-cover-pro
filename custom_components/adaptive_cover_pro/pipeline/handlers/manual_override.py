"""Manual override handler — pause automatic control after user move."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult


class ManualOverrideHandler(OverrideHandler):
    """Preserve the current (calculated) position while manual override is active.

    Priority 70 — lower than force/motion, higher than climate/solar.
    When the user manually moves the cover, automatic control is paused
    and the last calculated position is kept rather than fighting the user.
    """

    name = "manual_override"
    priority = 70

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return calculated position when manual override is active."""
        if not ctx.manual_override_active:
            return None
        return PipelineResult(
            position=ctx.calculated_position,
            control_method=ControlMethod.MANUAL,
            reason=(
                f"manual override active — holding position {ctx.calculated_position}%"
            ),
            decision_trace=[],
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when manual override is not active."""
        return "manual override not active"
