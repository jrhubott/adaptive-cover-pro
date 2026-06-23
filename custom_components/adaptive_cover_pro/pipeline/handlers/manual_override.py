"""Manual override handler — pause automatic control after user move."""

from __future__ import annotations

from ...const import ControlMethod
from ..handler import OverrideHandler
from ..helpers import (
    compute_default_position,
    compute_raw_calculated_position,
    compute_solar_position,
)
from ..types import PipelineResult, PipelineSnapshot


class ManualOverrideHandler(OverrideHandler):
    """Preserve the sun-tracking position while manual override is active.

    Priority 80 — lower than force/weather, higher than motion/climate/solar.
    When the user manually moves the cover, automatic control is paused.
    The handler computes what the solar position would be (or default if
    sun not in FOV) to avoid fighting the user.
    """

    name = "manual_override"
    priority = 80

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return computed position when manual override is active."""
        if not snapshot.manual_override_active:
            return None

        # The cover's actual physical position — may be None if the cover entity
        # has not reported a numeric position yet.  Used to populate held_position
        # so the "Target Position" sensor shows where the cover physically sits
        # rather than the solar value the override is shadowing.
        held_position: int | None = snapshot.current_cover_position

        if snapshot.cover.direct_sun_valid:
            position = compute_solar_position(snapshot)
            if held_position is not None:
                reason = (
                    f"manual override active — holding {held_position}%"
                    f" (solar would-be {position}%)"
                )
            else:
                reason = f"manual override active — solar would-be {position}%"
        else:
            position = compute_default_position(snapshot)
            pos_label = (
                "sunset position" if snapshot.is_sunset_active else "default position"
            )
            if held_position is not None:
                reason = (
                    f"manual override active — holding {held_position}%"
                    f" ({pos_label} would be {position}%)"
                )
            else:
                reason = f"manual override active — {pos_label} {position}%"

        return PipelineResult(
            position=position,
            control_method=ControlMethod.MANUAL,
            reason=reason,
            raw_calculated_position=compute_raw_calculated_position(snapshot),
            held_position=held_position,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when manual override is not active."""
        return "manual override not active"
