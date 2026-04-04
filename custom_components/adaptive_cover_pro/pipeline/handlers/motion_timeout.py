"""Motion timeout handler — return default when no occupancy detected."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_default_position, compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class MotionTimeoutHandler(OverrideHandler):
    """Return the default position when the motion timeout is active.

    Priority 80 — lower than force override but higher than manual override.
    When all occupancy sensors have reported no motion for the configured
    timeout duration, automatic sun-tracking is suspended.
    """

    name = "motion_timeout"
    priority = 80

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return default position when motion timeout is active."""
        if not snapshot.motion_control_enabled:
            return None
        if not snapshot.motion_timeout_active:
            return None
        position = compute_default_position(snapshot)
        pos_label = "sunset position" if snapshot.is_sunset_active else "default position"
        return PipelineResult(
            position=position,
            control_method=ControlMethod.MOTION,
            reason=f"motion timeout active — {pos_label} {position}%",
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when motion timeout is not active."""
        if not snapshot.motion_control_enabled:
            return "motion control disabled"
        return "motion timeout not active"
