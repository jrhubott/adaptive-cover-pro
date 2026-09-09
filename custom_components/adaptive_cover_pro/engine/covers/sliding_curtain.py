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
from ...const import (
    POSITION_CLOSED,
    POSITION_OPEN,
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
    SlideDirection,
)
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

    @staticmethod
    def _empty_interval_trace(status: str) -> dict:
        """Return the stable trace shape for an interval not yet projected."""
        return {
            "projected_point1_x_m": None,
            "projected_point2_x_m": None,
            "raw_interval_start_m": None,
            "raw_interval_end_m": None,
            "covered_interval_start_m": None,
            "covered_interval_end_m": None,
            "covered_width_m": None,
            "interval_status": status,
        }

    def _build_trace(
        self,
        *,
        calculation_mode: str,
        result: float,
        interval_trace: dict,
        position_basis: float | None = None,
        unclamped_position_pct: float | None = None,
    ) -> dict:
        """Assemble the raw sliding-curtain solar-calculation trace.

        The key set is shared by binary and shade-area paths so the live
        ``solar_calculation`` sensor and diagnostics download always expose a
        predictable shape. Values stay unrounded here; ``DiagnosticsBuilder``
        applies presentation rounding at the boundary.
        """
        sc = self.sc_config
        trace = {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: float(result),
            "calculation_mode": calculation_mode,
            "direct_sun_valid": bool(self.direct_sun_valid),
            "slide_direction": None if sc is None else str(sc.slide_direction),
            "window_width_m": None if sc is None else float(sc.window_width),
            "shade_point1_x_m": None if sc is None else float(sc.point1_x),
            "shade_point1_y_m": None if sc is None else float(sc.point1_y),
            "shade_point2_x_m": None if sc is None else float(sc.point2_x),
            "shade_point2_y_m": None if sc is None else float(sc.point2_y),
            "position_basis_m": position_basis,
            "unclamped_position_pct": unclamped_position_pct,
        }
        trace.update(interval_trace)
        return trace

    def _endpoint(self) -> int:
        """Return the fully-closed endpoint under direct sun, else fully-open.

        ``POSITION_CLOSED`` (drawn across the window) blocks the sun;
        ``POSITION_OPEN`` (retracted) lets it through. The Part 1 fallback when
        no shade area is configured.
        """
        return POSITION_CLOSED if self.direct_sun_valid else POSITION_OPEN

    def _covered_interval_with_trace(
        self,
    ) -> tuple[tuple[float, float] | None, dict]:
        """Return the covered interval and its raw projection trace.

        Projects both shade-area points onto the window plane at the current
        ``gamma`` and clamps the resulting span to the window half-width. Returns
        ``None`` — meaning the shade target is naturally unshaded, so the curtain
        should stay open — when either point sits on/behind the window wall
        (``y <= 0``), the window is degenerate, or the raw span falls entirely
        outside the opening.
        """
        sc = self.sc_config
        if sc is None:
            return None, self._empty_interval_trace("no_config")
        half = sc.window_width / 2.0
        if half <= 0:
            return None, self._empty_interval_trace("invalid_window_width")
        if sc.point1_y <= 0 or sc.point2_y <= 0:
            return None, self._empty_interval_trace("invalid_shade_depth")

        xw1 = ray_x_at_window_plane(sc.point1_x, sc.point1_y, self.gamma)
        xw2 = ray_x_at_window_plane(sc.point2_x, sc.point2_y, self.gamma)
        raw_a, raw_b = min(xw1, xw2), max(xw1, xw2)
        trace = self._empty_interval_trace("covered")
        trace.update(
            {
                "projected_point1_x_m": float(xw1),
                "projected_point2_x_m": float(xw2),
                "raw_interval_start_m": float(raw_a),
                "raw_interval_end_m": float(raw_b),
            }
        )

        # Entire span past one edge → the ray never enters the opening.
        if raw_b < -half or raw_a > half:
            trace["interval_status"] = "outside_window"
            return None, trace

        a = min(max(raw_a, -half), half)
        b = min(max(raw_b, -half), half)
        trace.update(
            {
                "covered_interval_start_m": float(a),
                "covered_interval_end_m": float(b),
                "covered_width_m": float(b - a),
            }
        )
        return (a, b), trace

    def _covered_interval(self) -> tuple[float, float] | None:
        """Along-wall interval (metres) the fabric must cover for this sun angle."""
        interval, _trace = self._covered_interval_with_trace()
        return interval

    def _position_components(self, a: float, b: float) -> tuple[float, float, float]:
        """Return clamped percentage, width basis, and raw percentage."""
        sc = self.sc_config
        assert sc is not None  # guarded by caller
        width = sc.window_width
        half = width / 2.0

        if sc.slide_direction == SlideDirection.LEFT:
            position_basis = half - b
        elif sc.slide_direction == SlideDirection.RIGHT:
            position_basis = half + a
        else:  # BI_PART (default)
            position_basis = 2.0 * max(0.0, a, -b)

        unclamped_pct = 100.0 * position_basis / width
        return min(max(unclamped_pct, 0.0), 100.0), position_basis, unclamped_pct

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
        pct, _position_basis, _unclamped_pct = self._position_components(a, b)
        return pct

    def _solve(self) -> float:
        """Resolve the target (0–100) shared by position and percentage.

        1. No shade area configured → Part 1 binary endpoint.
        2. Shade area configured but sun not directly in the window → fully open.
        3. Otherwise project the shade area to a covered interval and close just
           enough for the configured slide direction.
        """
        sc = self.sc_config
        if sc is None or not sc.is_area_configured:
            result = self._endpoint()
            self._last_calc_details = self._build_trace(
                calculation_mode="binary",
                result=result,
                interval_trace=self._empty_interval_trace("not_applicable"),
            )
            return result
        if not self.direct_sun_valid:
            result = POSITION_OPEN
            self._last_calc_details = self._build_trace(
                calculation_mode="shade_area",
                result=result,
                interval_trace=self._empty_interval_trace("sun_not_direct"),
            )
            return result
        interval, interval_trace = self._covered_interval_with_trace()
        if interval is None:
            result = POSITION_OPEN
            self._last_calc_details = self._build_trace(
                calculation_mode="shade_area",
                result=result,
                interval_trace=interval_trace,
            )
            return result
        result, position_basis, unclamped_pct = self._position_components(*interval)
        self._last_calc_details = self._build_trace(
            calculation_mode="shade_area",
            result=result,
            interval_trace=interval_trace,
            position_basis=position_basis,
            unclamped_position_pct=unclamped_pct,
        )
        return result

    def calculate_position(self) -> float:
        """Target position (0–100); continuous when a shade area is configured."""
        return self._solve()

    def calculate_percentage(self) -> int:
        """Percentage target; mirrors :meth:`calculate_position`."""
        return self._solve()
