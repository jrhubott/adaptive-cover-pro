"""Solar handler — sun-based position when direct sun is valid."""

from __future__ import annotations

from ...enums import ControlMethod
from ...position_utils import PositionConverter
from ..handler import OverrideHandler
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

        state = int(round(snapshot.cover.calculate_percentage()))
        # Prevent open/close-only covers from closing while sun is in FOV
        state = max(state, 1)
        position = PositionConverter.apply_limits(
            state,
            snapshot.config.min_pos,
            snapshot.config.max_pos,
            snapshot.config.min_pos_sun_only,
            snapshot.config.max_pos_sun_only,
            True,  # sun is valid when this handler fires
        )
        return PipelineResult(
            position=position,
            control_method=ControlMethod.SOLAR,
            reason=f"sun in FOV — position {position}%",
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when solar handler does not match."""
        return "sun not in FOV or outside elevation limits"
