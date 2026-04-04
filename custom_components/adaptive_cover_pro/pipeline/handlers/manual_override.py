"""Manual override handler — pause automatic control after user move."""

from __future__ import annotations

from ...enums import ControlMethod
from ...position_utils import PositionConverter
from ..handler import OverrideHandler
from ..types import PipelineResult, PipelineSnapshot


class ManualOverrideHandler(OverrideHandler):
    """Preserve the sun-tracking position while manual override is active.

    Priority 70 — lower than force/motion, higher than climate/solar.
    When the user manually moves the cover, automatic control is paused.
    The handler computes what the solar position would be (or default if
    sun not in FOV) to avoid fighting the user.
    """

    name = "manual_override"
    priority = 70

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return computed position when manual override is active."""
        if not snapshot.manual_override_active:
            return None

        if snapshot.cover.direct_sun_valid:
            state = int(round(snapshot.cover.calculate_percentage()))
            state = max(state, 1)
            position = PositionConverter.apply_limits(
                state,
                snapshot.config.min_pos,
                snapshot.config.max_pos,
                snapshot.config.min_pos_sun_only,
                snapshot.config.max_pos_sun_only,
                True,
            )
        else:
            position = PositionConverter.apply_limits(
                snapshot.default_position,
                snapshot.config.min_pos,
                snapshot.config.max_pos,
                snapshot.config.min_pos_sun_only,
                snapshot.config.max_pos_sun_only,
                False,
            )

        if snapshot.cover.direct_sun_valid:
            reason = f"manual override active — holding solar position {position}%"
        else:
            pos_label = "sunset position" if snapshot.is_sunset_active else "default position"
            reason = f"manual override active — holding {pos_label} {position}%"
        return PipelineResult(
            position=position,
            control_method=ControlMethod.MANUAL,
            reason=reason,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when manual override is not active."""
        return "manual override not active"
