"""Cloud suppression handler — use default position when no real direct sun."""

from __future__ import annotations

from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult
from ...enums import ControlMethod


class CloudSuppressionHandler(OverrideHandler):
    """Uses default position when weather/lux/irradiance indicate no real direct sun.

    Priority 60: between manual_override (70) and climate (50).
    Activated when the 'Suppress glare control in low light' option is enabled
    and conditions indicate no direct sun (weather not sunny, lux below
    threshold, or irradiance below threshold).
    """

    name = "cloud_suppression"
    priority = 60

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return default position when no direct sun is detected."""
        if not ctx.cloud_suppression_active:
            return None
        return PipelineResult(
            position=ctx.default_position,
            control_method=ControlMethod.CLOUD,
            reason=f"Cloud/low-light suppression — no direct sun detected → default {ctx.default_position}%",
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when cloud suppression is not active."""
        return "cloud suppression inactive (direct sun present or feature disabled)"
