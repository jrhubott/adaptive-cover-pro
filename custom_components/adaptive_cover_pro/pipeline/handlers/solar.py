"""Solar handler — sun-based position when direct sun is valid."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult


class SolarHandler(OverrideHandler):
    """Return the sun-calculated position when direct sun is in the FOV.

    Priority 40 — lower than climate, higher than default.
    Activates when the sun is within the cover's field of view and within
    configured elevation limits.
    """

    name = "solar"
    priority = 40

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return calculated position when direct sun is valid."""
        if not ctx.direct_sun_valid:
            return None
        return PipelineResult(
            position=ctx.calculated_position,
            control_method=ControlMethod.SOLAR,
            reason=f"sun in FOV — position {ctx.calculated_position}%",
            decision_trace=[],
        )

    def describe_skip(self, ctx: PipelineContext) -> str:  # noqa: ARG002
        """Reason when solar handler does not match."""
        return "sun not in FOV or outside elevation limits"
