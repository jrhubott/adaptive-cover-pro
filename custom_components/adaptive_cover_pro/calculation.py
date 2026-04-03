"""Cover state and climate orchestration classes.

Geometry classes have moved to engine/covers/:
  AdaptiveGeneralCover  → engine/covers/base.py
  AdaptiveVerticalCover → engine/covers/vertical.py
  AdaptiveHorizontalCover → engine/covers/horizontal.py
  AdaptiveTiltCover     → engine/covers/tilt.py

Re-exported here for backward compatibility with existing consumers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine.covers import (
    AdaptiveGeneralCover,
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
)
from .position_utils import PositionConverter

__all__ = [
    "AdaptiveGeneralCover",
    "AdaptiveHorizontalCover",
    "AdaptiveTiltCover",
    "AdaptiveVerticalCover",
    "NormalCoverState",
]


@dataclass
class NormalCoverState:
    """Compute state for normal operation."""

    cover: AdaptiveGeneralCover

    def get_state(self) -> int:
        """Calculate cover position using basic sun-tracking logic.

        Simple strategy for normal mode (no climate awareness):
        - If sun directly in front: Use calculated position to block glare
        - Otherwise: Use default position

        Applies configured min/max position limits before returning.

        Returns:
            Cover position as percentage (0-100).

        """
        self.cover.logger.debug("Determining normal position")
        dsv = self.cover.direct_sun_valid
        self.cover.logger.debug(
            "Sun directly in front of window & before sunset + offset? %s", dsv
        )
        if dsv:
            state = self.cover.calculate_percentage()
            # When sun is in the window, position must be at least 1% to prevent
            # open/close-only covers from closing while sun is still in the FOV.
            state = max(state, 1)
            self.cover.logger.debug(
                "Yes sun in window: using calculated percentage (%s)", state
            )
        else:
            state = self.cover.default
            self.cover.logger.debug("No sun in window: using default value (%s)", state)

        # Apply position limits using utility
        return PositionConverter.apply_limits(
            int(state),
            self.cover.config.min_pos,
            self.cover.config.max_pos,
            self.cover.config.min_pos_sun_only,
            self.cover.config.max_pos_sun_only,
            dsv,
        )
