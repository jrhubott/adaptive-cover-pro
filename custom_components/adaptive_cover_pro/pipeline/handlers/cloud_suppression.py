"""Cloud suppression handler — use default position when no real direct sun."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineResult, PipelineSnapshot


class CloudSuppressionHandler(OverrideHandler):
    """Uses default position when weather/lux/irradiance indicate no real direct sun.

    Priority 60: between manual_override (70) and climate (50).
    Evaluates ClimateReadings directly from the snapshot:
    - Not sunny (weather state not in sunny_conditions list)
    - OR lux below configured threshold
    - OR solar irradiance below configured threshold
    - OR cloud coverage above configured threshold
    """

    name = "cloud_suppression"
    priority = 60

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return default position when no direct sun is detected."""
        if snapshot.climate_readings is None:
            return None
        if snapshot.climate_options is None:
            return None
        if not snapshot.climate_options.cloud_suppression_enabled:
            return None

        r = snapshot.climate_readings
        suppressed = (
            not r.is_sunny
            or r.lux_below_threshold
            or r.irradiance_below_threshold
            or r.cloud_coverage_above_threshold
        )
        if not suppressed:
            return None

        pos = snapshot.default_position
        pos_label = "sunset position" if snapshot.is_sunset_active else "default position"
        return PipelineResult(
            position=pos,
            control_method=ControlMethod.CLOUD,
            reason=f"cloud/low-light suppression — no direct sun detected → {pos_label} {pos}%",
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when cloud suppression is not active."""
        return "cloud suppression inactive (direct sun present or feature disabled)"
