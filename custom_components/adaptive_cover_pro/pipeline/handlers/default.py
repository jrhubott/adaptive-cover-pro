"""Default handler — always matches as the final fallback."""

from __future__ import annotations

from ...enums import ControlMethod
from ...position_utils import PositionConverter
from ..handler import OverrideHandler
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
        position = PositionConverter.apply_limits(
            snapshot.default_position,
            snapshot.config.min_pos,
            snapshot.config.max_pos,
            snapshot.config.min_pos_sun_only,
            snapshot.config.max_pos_sun_only,
            snapshot.cover.direct_sun_valid,
        )
        return PipelineResult(
            position=position,
            control_method=ControlMethod.DEFAULT,
            reason=f"no active condition — default position {position}%",
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """DefaultHandler always matches — this should never be called."""
        return "always matches"
