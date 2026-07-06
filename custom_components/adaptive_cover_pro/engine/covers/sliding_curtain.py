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

from ...config_types import SlidingCurtainConfig
from ...const import POSITION_CLOSED, POSITION_OPEN, SlideDirection
from ..sun_geometry import ray_x_at_window_plane
from .base import AdaptiveGeneralCover


@dataclass
class AdaptiveSlidingCurtainCover(AdaptiveGeneralCover):
    """Calculate state for horizontally-sliding curtains.

    Without a configured shade area (``sc_config`` is ``None`` or its
    ``is_area_configured`` is False) this is the Part 1 binary cover: fully
    drawn under direct sun, fully retracted otherwise. With a two-point shade
    area it closes a continuous fraction just wide enough to keep that floor
    interval in shadow (Part 2).
    """

    sc_config: SlidingCurtainConfig | None = None

    def _endpoint(self) -> int:
        """Return the fully-closed endpoint under direct sun, else fully-open.

        ``POSITION_CLOSED`` (drawn across the window) blocks the sun;
        ``POSITION_OPEN`` (retracted) lets it through. The Part 1 fallback when
        no shade area is configured.
        """
        return POSITION_CLOSED if self.direct_sun_valid else POSITION_OPEN

    def _covered_interval(self) -> tuple[float, float] | None:
        """Along-wall interval (metres) the fabric must cover for this sun angle.

        Projects both shade-area points onto the window plane at the current
        ``gamma`` and clamps the resulting span to the window half-width. Returns
        ``None`` — meaning the shade target is naturally unshaded, so the curtain
        should stay open — when either point sits on/behind the window wall
        (``y <= 0``), the window is degenerate, or the raw span falls entirely
        outside the opening.
        """
        sc = self.sc_config
        if sc is None:
            return None
        half = sc.window_width / 2.0
        if half <= 0:
            return None
        if sc.point1_y <= 0 or sc.point2_y <= 0:
            return None

        xw1 = ray_x_at_window_plane(sc.point1_x, sc.point1_y, self.gamma)
        xw2 = ray_x_at_window_plane(sc.point2_x, sc.point2_y, self.gamma)
        raw_a, raw_b = min(xw1, xw2), max(xw1, xw2)

        # Entire span past one edge → the ray never enters the opening.
        if raw_b < -half or raw_a > half:
            return None

        a = min(max(raw_a, -half), half)
        b = min(max(raw_b, -half), half)
        return a, b

    def _position_for_interval(self, a: float, b: float) -> float:
        """Map a covered along-wall interval to an open percentage (0=closed).

        ``0`` = fully drawn across (blocks the sun); ``100`` = fully retracted.
        The mapping depends on where the fabric is anchored:

        * ``LEFT`` — anchored at the left edge, closes rightward; the right end
          ``b`` is binding, so openness is the fraction still uncovered to its
          right.
        * ``RIGHT`` — anchored at the right edge, closes leftward; the left end
          ``a`` is binding (mirror image).
        * ``BI_PART`` — parts from the centre; the central gap may only grow up
          to the interval edge nearest the centre, so a span straddling the
          centre forces full closure.
        """
        sc = self.sc_config
        assert sc is not None  # guarded by caller
        width = sc.window_width
        half = width / 2.0

        if sc.slide_direction == SlideDirection.LEFT:
            pct = 100.0 * (half - b) / width
        elif sc.slide_direction == SlideDirection.RIGHT:
            pct = 100.0 * (half + a) / width
        else:  # BI_PART (default)
            d_near = max(0.0, a, -b)
            pct = 200.0 * d_near / width

        return min(max(pct, 0.0), 100.0)

    def _solve(self) -> float:
        """Resolve the target (0–100) shared by position and percentage.

        1. No shade area configured → Part 1 binary endpoint.
        2. Shade area configured but sun not directly in the window → fully open.
        3. Otherwise project the shade area to a covered interval and close just
           enough for the configured slide direction.
        """
        sc = self.sc_config
        if sc is None or not sc.is_area_configured:
            return self._endpoint()
        if not self.direct_sun_valid:
            return POSITION_OPEN
        interval = self._covered_interval()
        if interval is None:
            return POSITION_OPEN
        return self._position_for_interval(*interval)

    def calculate_position(self) -> float:
        """Target position (0–100); continuous when a shade area is configured."""
        return self._solve()

    def calculate_percentage(self) -> int:
        """Percentage target; mirrors :meth:`calculate_position`."""
        return self._solve()
