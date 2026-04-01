"""Climate handler — temperature/season-aware position control."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineContext, PipelineResult


class ClimateHandler(OverrideHandler):
    """Return the climate-calculated position when climate mode is enabled.

    Priority 50 — lower than override handlers, higher than solar/default.
    The control method is set based on the climate season:
    - SUMMER when over the high-temp threshold (heat blocking)
    - WINTER when under the low-temp threshold (solar heat gain)
    - SOLAR for all other climate-mode states (glare control)
    """

    name = "climate"
    priority = 50

    def evaluate(self, ctx: PipelineContext) -> PipelineResult | None:
        """Return climate position when climate mode is active and has a value."""
        if not ctx.climate_mode_enabled:
            return None
        if ctx.climate_position is None:
            return None

        if ctx.climate_is_summer:
            method = ControlMethod.SUMMER
            season = "summer"
        elif ctx.climate_is_winter:
            method = ControlMethod.WINTER
            season = "winter"
        else:
            method = ControlMethod.SOLAR
            season = "glare control"

        return PipelineResult(
            position=ctx.climate_position,
            control_method=method,
            reason=(
                f"climate mode active ({season}) — position {ctx.climate_position}%"
            ),
            decision_trace=[],
        )

    def describe_skip(self, ctx: PipelineContext) -> str:
        """Reason when climate handler does not match."""
        if not ctx.climate_mode_enabled:
            return "climate mode not enabled"
        return "climate position unavailable"
