"""Geometric calculation utilities for Adaptive Cover Pro."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from .const import (
    EDGE_CASE_LOW_ELEVATION,
    SAFETY_MARGIN_GAMMA_MAX,
    SAFETY_MARGIN_GAMMA_THRESHOLD,
    SAFETY_MARGIN_HIGH_ELEV_MAX,
    SAFETY_MARGIN_HIGH_ELEV_THRESHOLD,
    SAFETY_MARGIN_LOW_ELEV_MAX,
    SAFETY_MARGIN_LOW_ELEV_THRESHOLD,
)


@lru_cache(maxsize=512)
def _safety_margin(gamma: float, sol_elev: float) -> float:
    """Compute the safety-margin multiplier — pure, process-wide memoised.

    Pulled out of ``SafetyMarginCalculator.calculate`` so the result is shared
    across every cover (and every config entry) at the same sun angles within a
    cycle. Pure ``float -> float``, no rounding (rounding would change numeric
    output); ``maxsize`` bounds memory. Assumes finite inputs (elevation 0-90,
    gamma -180..180) — NaN would defeat caching but never reaches here.
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


@lru_cache(maxsize=512)
def _edge_case(
    sol_elev: float, gamma: float, distance: float, h_win: float
) -> tuple[bool, float]:
    """Compute the low-sun edge-case fallback — pure, memoised.

    Companion to :func:`_safety_margin`; see it for the caching rationale.

    Only the very-low-elevation guard remains (issue #600). The former
    extreme-gamma and very-high-elevation branches were removed: the
    projection in ``AdaptiveVerticalCover.calculate_position`` now carries its
    own numerical guards — ``MIN_COS_GAMMA_CLAMP`` on the ``cos(gamma)``
    divisor, ``MIN_TAN_ELEVATION_CLAMP`` on the sill division, and the
    ``effective_distance < 0 → 0`` clamp (#358/#559) — so those two branches
    either duplicated the normal path (very high elevation) or contradicted it
    (extreme gamma forced fully-closed where the grazing geometry is open,
    the root cause of #598). The low-elevation floor is retained as a
    deliberate policy: a sun on the horizon should drive full coverage, and
    the normal path does not enforce that for a zero-sill window.

    ``gamma``, ``distance`` and ``h_win`` are unused now but kept on the
    signature so the cached call site and ``EdgeCaseHandler.check_and_handle``
    contract are unchanged.
    """
    # Very low elevation: sun on the horizon — full coverage (position 0 = closed).
    if sol_elev < EDGE_CASE_LOW_ELEVATION:
        return (True, 0.0)

    return (False, 0.0)


class SafetyMarginCalculator:
    """Calculates angle-dependent safety margins for sun blocking accuracy."""

    @staticmethod
    def calculate(gamma: float, sol_elev: float) -> float:
        """Calculate safety margin multiplier (≥1.0).

        Increases blind extension at extreme angles to ensure effective sun blocking:
        - Gamma margin: increases at extreme horizontal angles
        - Elevation margin: increases at very low or high angles

        Delegates to the module-level cached :func:`_safety_margin` so the result
        is shared across all instances at the same sun angles.

        Args:
            gamma: Surface solar azimuth in degrees (-180 to 180)
            sol_elev: Sun elevation angle in degrees (0-90)

        Returns:
            Safety margin multiplier (1.0 to 1.45)

        """
        return _safety_margin(gamma, sol_elev)


class EdgeCaseHandler:
    """Handles extreme angle edge cases with safe fallback positions."""

    @staticmethod
    def check_and_handle(
        sol_elev: float, gamma: float, distance: float, h_win: float
    ) -> tuple[bool, float]:
        """Check for edge cases and return fallback position if needed.

        Provides robust behavior at edge cases where standard geometric
        calculations become unstable or inaccurate. Delegates to the
        module-level cached :func:`_edge_case`.

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
        return _edge_case(sol_elev, gamma, distance, h_win)
