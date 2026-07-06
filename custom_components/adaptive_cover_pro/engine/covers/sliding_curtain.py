"""Horizontal sliding-curtain cover calculation (#829, Part 1).

A sliding curtain draws its fabric sideways across the window opening (from one
edge, or bi-parting from the centre). Part 1 models it as a *binary* cover: the
fabric is fully drawn across the opening whenever direct sun would strike the
shade target — ``direct_sun_valid`` — and fully retracted otherwise. Both
open/close-only hardware and bi-parting vs single-slide leaves are satisfied by
dispatching the same endpoint target to every bound entity.

The illumination gate (FOV azimuth, elevation limits, sunset offset, blind spot)
is inherited unchanged from :class:`AdaptiveGeneralCover`; only the endpoint
mapping is defined here. Part 2 will replace the binary output with a continuous
width-fraction derived from a two-point shade area.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...const import POSITION_CLOSED, POSITION_OPEN
from .base import AdaptiveGeneralCover


@dataclass
class AdaptiveSlidingCurtainCover(AdaptiveGeneralCover):
    """Calculate state for horizontally-sliding curtains (binary open/close)."""

    def _endpoint(self) -> int:
        """Return the fully-closed endpoint under direct sun, else fully-open.

        ``POSITION_CLOSED`` (drawn across the window) blocks the sun;
        ``POSITION_OPEN`` (retracted) lets it through. Both position and
        percentage resolve through this single source of truth.
        """
        return POSITION_CLOSED if self.direct_sun_valid else POSITION_OPEN

    def calculate_position(self) -> float:
        """Binary target: fully closed under direct sun, else fully open."""
        return self._endpoint()

    def calculate_percentage(self) -> int:
        """Binary percentage: mirrors :meth:`calculate_position`."""
        return self._endpoint()
