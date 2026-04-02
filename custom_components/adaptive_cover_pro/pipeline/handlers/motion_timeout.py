"""Motion timeout handler — return default when no occupancy detected."""

from __future__ import annotations

from ...enums import ControlMethod
from ...position_utils import PositionConverter
from ..handler import OverrideHandler
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
        if not snapshot.motion_timeout_active:
            return None
        position = PositionConverter.apply_limits(
            int(round(snapshot.cover.default)),
            snapshot.config.min_pos,
            snapshot.config.max_pos,
            snapshot.config.min_pos_sun_only,
            snapshot.config.max_pos_sun_only,
            snapshot.cover.direct_sun_valid,
        )
        return PipelineResult(
            position=position,
            control_method=ControlMethod.MOTION,
            reason=f"motion timeout active — default position {position}%",
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when motion timeout is not active."""
        return "motion timeout not active"
