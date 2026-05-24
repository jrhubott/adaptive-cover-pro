"""Default handler — always matches as the final fallback."""

from __future__ import annotations

from ...const import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_default_position
from ..types import PipelineResult, PipelineSnapshot


class DefaultHandler(OverrideHandler):
    """Return the default position as the final fallback.

    Priority 0 — evaluated last, always matches.
    Used when the sun is outside the FOV, outside the time window, or
    no other handler has claimed the position.
    """

    name = "default"
    priority = 0

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult:
        """Return the default position as the final fallback."""
        position = compute_default_position(snapshot)
        # Resolve tilt: sunset_tilt takes precedence during the sunset window,
        # then fall back to default_tilt. None means the venetian policy will
        # use solar-computed tilt instead.
        tilt: int | None
        if snapshot.is_sunset_active:
            tilt = (
                snapshot.sunset_tilt
                if snapshot.sunset_tilt is not None
                else snapshot.default_tilt
            )
        else:
            tilt = snapshot.default_tilt
        # "Use My at sunset" path: route through the cover's hardware-stored My preset
        # when the sunset window is active and the user has opted in.
        if (
            snapshot.is_sunset_active
            and snapshot.sunset_use_my
            and snapshot.my_position_value is not None
        ):
            pos = snapshot.my_position_value
            return PipelineResult(
                position=pos,
                tilt=tilt,
                use_my_position=True,
                control_method=ControlMethod.DEFAULT,
                reason=f"sunset position — use My position ({pos}%)",
                raw_calculated_position=position,
            )
        pos_label = (
            "sunset position" if snapshot.is_sunset_active else "default position"
        )
        return PipelineResult(
            position=position,
            tilt=tilt,
            control_method=ControlMethod.DEFAULT,
            reason=f"no active condition — {pos_label} {position}%",
            raw_calculated_position=position,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """DefaultHandler always matches — this should never be called."""
        return "always matches"
