"""Manual override handler — pause automatic control after user move."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
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
                reason_payload = Reason(
                    ReasonCode.MANUAL_HOLDING_SOLAR,
                    {"held": held_position, "position": position},
                )
            else:
                reason_payload = Reason(
                    ReasonCode.MANUAL_SOLAR_ONLY, {"position": position}
                )
        else:
            position = compute_default_position(snapshot)
            pos_label = Reason(
                ReasonCode.FRAGMENT_SUNSET_POSITION
                if snapshot.is_sunset_active
                else ReasonCode.FRAGMENT_DEFAULT_POSITION
            )
            if held_position is not None:
                reason_payload = Reason(
                    ReasonCode.MANUAL_HOLDING_LABEL,
                    {
                        "held": held_position,
                        "pos_label": pos_label,
                        "position": position,
                    },
                )
            else:
                reason_payload = Reason(
                    ReasonCode.MANUAL_LABEL_ONLY,
                    {"pos_label": pos_label, "position": position},
                )

        return PipelineResult(
            position=position,
            control_method=ControlMethod.MANUAL,
            reason_payload=reason_payload,
            raw_calculated_position=compute_raw_calculated_position(snapshot),
            held_position=held_position,
            # When the cover's physical position is known, genuinely hold there:
            # ``position`` stays the would-be shadow for diagnostics, but
            # skip_command suppresses the dispatch so we don't drive the cover to
            # the default it merely shadows (issue #809).  When held_position is
            # None (no feedback) fall through without holding — parity with
            # motion_timeout's ``current_cover_position is not None`` guard.
            skip_command=held_position is not None,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:  # noqa: ARG002
        """Reason when manual override is not active."""
        return Reason(ReasonCode.SKIP_MANUAL_NOT_ACTIVE)
