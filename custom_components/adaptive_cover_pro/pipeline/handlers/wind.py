"""Wind override handler — safety retraction when wind exceeds threshold."""

from __future__ import annotations

from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult
from ...enums import ControlMethod


class WindOverrideHandler(OverrideHandler):
    """Retracts covers when wind speed exceeds threshold.

    Priority 90: between force_override (100) and motion_timeout (80).
    Currently a stub — will be fully implemented when wind sensor
    configuration is added (Issue #28).
    """

    name = "wind"
    priority = 90

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return retract position when wind speed exceeds threshold."""
        if not ctx.wind_active:
            return None
        return PipelineResult(
            position=ctx.wind_retract_position,
            control_method=ControlMethod.WIND,
            reason=f"Wind override active → {ctx.wind_retract_position}%",
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when wind override is not active."""
        return "wind override not active"
