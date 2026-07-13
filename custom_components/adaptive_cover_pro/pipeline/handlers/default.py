"""Default handler — always matches as the final fallback."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...position_utils import PositionConverter
from ...reason_i18n import Reason
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
        # Sunset tilt (and its default_tilt fallback) is a deliberate carve-out
        # and stays UNclamped, mirroring the sunset *position* bypass (#128).
        # Only the non-sunset default_tilt honors the global min_tilt/max_tilt
        # clamp (#503) — exactly as default *position* is clamped. tilt is None
        # for non-venetian covers, so the clamp is a natural no-op there (no
        # cover-type string branch needed).
        tilt: int | None
        if snapshot.is_sunset_active:
            tilt = (
                snapshot.sunset_tilt
                if snapshot.sunset_tilt is not None
                else snapshot.default_tilt
            )
        else:
            tilt = snapshot.default_tilt
            if tilt is not None:
                tilt = PositionConverter.apply_tilt_limits(
                    tilt,
                    snapshot.min_tilt,
                    snapshot.max_tilt,
                    snapshot.min_tilt_sun_only,
                    snapshot.max_tilt_sun_only,
                    sun_valid=False,
                )
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
                reason_payload=Reason(
                    ReasonCode.DEFAULT_SUNSET_USE_MY, {"position": pos}
                ),
                raw_calculated_position=position,
            )
        pos_label = Reason(
            ReasonCode.FRAGMENT_SUNSET_POSITION
            if snapshot.is_sunset_active
            else ReasonCode.FRAGMENT_DEFAULT_POSITION
        )
        return PipelineResult(
            position=position,
            tilt=tilt,
            control_method=ControlMethod.DEFAULT,
            reason_payload=Reason(
                ReasonCode.DEFAULT_NO_CONDITION,
                {"pos_label": pos_label, "position": position},
            ),
            raw_calculated_position=position,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:  # noqa: ARG002
        """DefaultHandler always matches — this should never be called."""
        return Reason(ReasonCode.SKIP_ALWAYS_MATCHES)
