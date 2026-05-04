"""Motion timeout handler — return default when no occupancy detected."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_default_position, compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class MotionTimeoutHandler(OverrideHandler):
    """Return the default position when the motion timeout is active.

    Priority 75 — lower than manual override, higher than climate/solar.
    When all occupancy sensors have reported no motion for the configured
    timeout duration, automatic sun-tracking is suspended.
    """

    name = "motion_timeout"
    priority = 75

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return default position when motion timeout is active."""
        if not snapshot.motion_control_enabled:
            return None
        if not snapshot.motion_timeout_active:
            return None

        # Hold-position mode: freeze the cover at its current physical position
        # while the sun is actively in the FOV. When the sun leaves (or the time
        # window closes), fall through to the return_to_default branch below so
        # the cover returns to default without requiring motion re-detection.
        if (
            snapshot.motion_timeout_mode == "hold_position"
            and snapshot.in_time_window
            and snapshot.cover.direct_sun_valid
            and snapshot.current_cover_position is not None
        ):
            held = snapshot.current_cover_position
            return PipelineResult(
                position=held,
                control_method=ControlMethod.MOTION,
                reason=f"motion timeout — holding position {held}% (sun in FOV)",
                skip_command=True,
                raw_calculated_position=compute_raw_calculated_position(snapshot),
            )

        position = compute_default_position(snapshot)
        pos_label = (
            "sunset position" if snapshot.is_sunset_active else "default position"
        )
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
