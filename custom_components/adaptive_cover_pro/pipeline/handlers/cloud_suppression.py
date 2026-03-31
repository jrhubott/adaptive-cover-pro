"""Cloud suppression handler — use default position when cloud coverage is high."""

from __future__ import annotations

from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult
from ...enums import ControlMethod


class CloudSuppressionHandler(OverrideHandler):
    """Uses default position when cloud coverage suppresses solar radiation.

    Priority 60: between manual_override (70) and climate (50).
    Currently a stub — will be fully implemented when cloud/lux
    suppression configuration is added (Issue #31).
    """

    name = "cloud_suppression"
    priority = 60

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return default position when cloud coverage suppresses solar radiation."""
        if not ctx.cloud_suppression_active:
            return None
        return PipelineResult(
            position=ctx.default_position,
            control_method=ControlMethod.CLOUD,
            reason=f"Cloud suppression active → default {ctx.default_position}%",
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when cloud suppression is not active."""
        return "cloud suppression not active"
