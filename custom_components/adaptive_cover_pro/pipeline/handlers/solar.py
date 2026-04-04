"""Solar handler — sun-based position when direct sun is valid."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_solar_position
from ..types import PipelineResult, PipelineSnapshot


class SolarHandler(OverrideHandler):
    """Return the sun-calculated position when direct sun is in the FOV.

    Priority 40 — lower than climate, higher than default.
    Activates when the sun is within the cover's field of view and within
    configured elevation limits. Computes position from the calculation
    engine and applies configured position limits.
    """

    name = "solar"
    priority = 40

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return calculated position when direct sun is valid."""
        if not snapshot.cover.direct_sun_valid:
            return None

        position = compute_solar_position(snapshot)
        return PipelineResult(
            position=position,
            control_method=ControlMethod.SOLAR,
            reason=f"sun in FOV — position {position}%",
            raw_calculated_position=position,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when solar handler does not match."""
        return "sun not in FOV or outside elevation limits"
