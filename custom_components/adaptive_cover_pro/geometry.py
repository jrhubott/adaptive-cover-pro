"""Geometric calculation utilities for Adaptive Cover Pro."""

from __future__ import annotations

import numpy as np

from .const import (
    EDGE_CASE_EXTREME_GAMMA,
    EDGE_CASE_HIGH_ELEVATION,
    EDGE_CASE_LOW_ELEVATION,
    SAFETY_MARGIN_GAMMA_MAX,
    SAFETY_MARGIN_GAMMA_THRESHOLD,
    SAFETY_MARGIN_HIGH_ELEV_MAX,
    SAFETY_MARGIN_HIGH_ELEV_THRESHOLD,
    SAFETY_MARGIN_LOW_ELEV_MAX,
    SAFETY_MARGIN_LOW_ELEV_THRESHOLD,
)


class SafetyMarginCalculator:
    """Calculates angle-dependent safety margins for sun blocking accuracy."""

    @staticmethod
    def calculate(gamma: float, sol_elev: float) -> float:
        """Calculate safety margin multiplier (≥1.0).

        Increases blind extension at extreme angles to ensure effective sun blocking:
        - Gamma margin: increases at extreme horizontal angles
        - Elevation margin: increases at very low or high angles

        Args:
            gamma: Surface solar azimuth in degrees (-180 to 180)
            sol_elev: Sun elevation angle in degrees (0-90)

        Returns:
            Safety margin multiplier (1.0 to 1.45)

        """
        margin = 1.0

        # Gamma margin: increases at extreme horizontal angles
        gamma_abs = abs(gamma)
        if gamma_abs > SAFETY_MARGIN_GAMMA_THRESHOLD:
            # Normalized transition: 0 at threshold, 1 at 90°
            t = (gamma_abs - SAFETY_MARGIN_GAMMA_THRESHOLD) / (
                90 - SAFETY_MARGIN_GAMMA_THRESHOLD
            )
            t = float(np.clip(t, 0, 1))
            smooth_t = t * t * (3 - 2 * t)  # Smoothstep interpolation
            margin += SAFETY_MARGIN_GAMMA_MAX * smooth_t

        # Elevation margin: increases at very low/high angles
        if sol_elev < SAFETY_MARGIN_LOW_ELEV_THRESHOLD:
            t = (
                SAFETY_MARGIN_LOW_ELEV_THRESHOLD - sol_elev
            ) / SAFETY_MARGIN_LOW_ELEV_THRESHOLD
            margin += SAFETY_MARGIN_LOW_ELEV_MAX * float(np.clip(t, 0, 1))
        elif sol_elev > SAFETY_MARGIN_HIGH_ELEV_THRESHOLD:
            t = (sol_elev - SAFETY_MARGIN_HIGH_ELEV_THRESHOLD) / (
                90 - SAFETY_MARGIN_HIGH_ELEV_THRESHOLD
            )
            margin += SAFETY_MARGIN_HIGH_ELEV_MAX * float(np.clip(t, 0, 1))

        return float(margin)


class EdgeCaseHandler:
    """Handles extreme angle edge cases with safe fallback positions."""

    @staticmethod
    def check_and_handle(
        sol_elev: float, gamma: float, distance: float, h_win: float
    ) -> tuple[bool, float]:
        """Check for edge cases and return fallback position if needed.

        Provides robust behavior at edge cases where standard geometric
        calculations become unstable or inaccurate.

        Args:
            sol_elev: Sun elevation angle in degrees (0-90)
            gamma: Surface solar azimuth in degrees (-180 to 180)
            distance: Distance from window to shaded area (meters)
            h_win: Window height (meters)

        Returns:
            Tuple of (is_edge_case: bool, position: float)
            - is_edge_case: True if edge case detected
            - position: Safe fallback position (only valid if is_edge_case=True)

        """
        # Very low elevation: sun nearly horizontal, full coverage safest
        if sol_elev < EDGE_CASE_LOW_ELEVATION:
            return (True, h_win)

        # Extreme gamma: sun perpendicular to window, full coverage
        if abs(gamma) > EDGE_CASE_EXTREME_GAMMA:
            return (True, h_win)

        # Very high elevation: sun nearly overhead, use simplified calculation
        if sol_elev > EDGE_CASE_HIGH_ELEVATION:
            from numpy import radians as rad
            from numpy import tan

            simple_height = distance * tan(rad(sol_elev))
            return (True, float(np.clip(simple_height, 0, h_win)))

        return (False, 0.0)
