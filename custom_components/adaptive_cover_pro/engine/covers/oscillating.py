"""Oscillating (drop-arm / pivoting) awning cover calculation (#412, #586).

A drop-arm awning pivots above the window top; as the arm sweeps from folded-up
(θ=min_angle) through horizontal (θ≈90°) to straight-down (θ=max_angle) the
fabric *lip* descends down the window face. The shading-relevant quantity at low
sun is how far that lip drops, not merely how far it reaches horizontally.

The earlier model (#412) credited only the horizontal reach
(``reach = arm·sin θ``), which peaks at θ=90° = exactly 50% of a 0–180° sweep,
so the awning never commanded more than 50% no matter how low the sun got — the
lip's vertical drop past horizontal was ignored. Issue #586 replaces that with a
**vertical-drop (lip-height) shade model**:

1. Pivot sits at ``pivot_y = h_win + housing_offset`` above the window bottom.
2. At arm angle θ the lip tip is at horizontal reach ``R(θ) = arm·sin θ`` and
   height ``lip_y(θ) = pivot_y + arm·cos θ`` (θ=0 → lip up the wall, θ=90° →
   lip at the pivot, θ=180° → lip a full arm below the pivot).
3. A sun ray grazing the lip drops back toward the wall at the same
   foreshortened slope the vertical engine uses (``tan(elev)/cos(gamma)``). The
   pivot/fabric plane can stand off the glass by a horizontal **pivot offset**
   ``po`` (the arm/housing standoff plus any window inset), so the lip's true
   horizontal distance from the pane is ``po + R(θ)`` and the shadow top on the
   window face is ``shadow_top(θ) = lip_y(θ) − (po + R(θ))·tan(elev)/cos(gamma)``.
   ``po=0`` (flush mount) is a no-op that reproduces the earlier model exactly.
4. The solver scans the sweep and returns the smallest θ whose shadow_top
   reaches the **protected boundary** — the exposed-glass height the inherited
   vertical sill/depth/distance solve leaves uncovered (so window_depth,
   sill_height and distance still influence the result). If no angle reaches the
   boundary it fails open (max coverage), driving position → 100% at very low
   sun. θ>90° (pos>50%) is now reachable by construction.

**Monotone-coverage contract.** The raw lip-tip shadow is non-physically
non-monotone in θ across the descending half of the sweep (its foreshortened
drop peaks near θ≈90° while the lip keeps falling, producing a false mid-sweep
optimum, then rising again toward θ=180°). A real awning never shades *less* as
it extends further, so the solver operates on the **running floor** of the lip
shadow (``_monotone_coverage_floor``) — the deepest shadow reached *up to* each
angle. This makes both the reach search (first θ at the boundary) and the
fail-open argmin land at the full-extension angle when the boundary is
unreachable, instead of a spurious mid-sweep angle.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, sin, tan
from numpy import radians as rad

from ...config_types import OscillatingConfig
from ...const import (
    OSCILLATING_ARC_SCAN_SAMPLES,
    OSCILLATING_PROTECTED_BOUNDARY_DEFAULT,
)
from .horizontal import AdaptiveHorizontalCover
from .vertical import MIN_COS_GAMMA_CLAMP, AdaptiveVerticalCover

# Tolerance (metres) for treating two coverage-floor heights as equal when the
# fail-open branch selects the deepest-coverage angle. Once the running floor
# plateaus, any angle within this band shades equally, so the awning is driven
# to the largest (most-extended) such angle.
_COVERAGE_PLATEAU_EPS = 1e-6


def _foreshortened_drop_per_reach(sol_elev: float, gamma: float) -> float:
    """Vertical drop of a sun ray per metre of horizontal lip reach.

    A ray grazing the lip descends toward the wall at ``tan(elev)`` per metre of
    in-room depth; the surface-azimuth foreshortening divides by ``cos(gamma)``.
    Shares the ``MIN_COS_GAMMA_CLAMP`` guard with the vertical engine so a
    near-90° gamma cannot divide by zero (single source of truth for the
    foreshortening factor used on the window face).
    """
    cos_gamma = float(cos(rad(gamma)))
    cos_gamma_clamped = max(abs(cos_gamma), MIN_COS_GAMMA_CLAMP) * (
        1 if cos_gamma >= 0 else -1
    )
    return float(tan(rad(sol_elev))) / cos_gamma_clamped


def _lip_shadow_top(
    theta_deg: float,
    *,
    arm_length: float,
    pivot_y: float,
    sol_elev: float,
    gamma: float,
    pivot_offset: float = 0.0,
) -> float:
    """Window-face height of the lip's shadow top at arm angle ``theta_deg``.

    ``pivot_y`` is measured in the window-bottom=0 datum. ``pivot_offset`` is the
    horizontal standoff of the pivot/fabric plane from the glass; it adds to the
    lip's projected reach so the shadow drops lower on the pane (``0.0`` =
    flush, a no-op). Lower return values mean the lip shades further down the
    face (more coverage).
    """
    theta = rad(theta_deg)
    reach = pivot_offset + arm_length * float(sin(theta))
    lip_y = pivot_y + arm_length * float(cos(theta))
    return lip_y - reach * _foreshortened_drop_per_reach(sol_elev, gamma)


def _monotone_coverage_floor(shadow_tops: np.ndarray) -> np.ndarray:
    """Return the running floor (cumulative minimum) of the lip-shadow array.

    The raw lip shadow is non-monotone in θ (a false mid-sweep optimum, then
    rising again toward full extension). A physical awning never shades *less*
    as it extends further, so the coverage the solver credits at angle ``i`` is
    the deepest shadow reached up to and including ``i`` — i.e. the cumulative
    minimum. This is non-increasing by construction, so the first crossing of
    the protected boundary and the global argmin both land at full extension
    when the boundary is otherwise unreachable.
    """
    return np.minimum.accumulate(shadow_tops)


@dataclass
class AdaptiveOscillatingCover(AdaptiveHorizontalCover):
    """Calculate state for oscillating (drop-arm) awnings via the lip-drop model."""

    osc_config: OscillatingConfig = None  # type: ignore[assignment]

    @property
    def awn_length(self) -> float:
        """Max horizontal reach scale = the arm length."""
        return self.osc_config.arm_length

    @property
    def awn_angle(self) -> float:
        """Flat fabric for any inherited reach projection (unused by the solver)."""
        return 0.0

    def _protected_boundary(self) -> float:
        """Window-face height the lip must shade down to (window-bottom datum).

        Derived from the inherited vertical sill/depth/distance solve so that
        window_depth, sill_height and distance stay load-bearing: the vertical
        engine returns the blind height (from the bottom) that must be covered
        to stop sun reaching the protected zone — i.e. the awning lip only needs
        to shade the face DOWN TO that height, not necessarily the window
        bottom. When the vertical solve leaves the whole face exposed it returns
        0.0, which equals OSCILLATING_PROTECTED_BOUNDARY_DEFAULT (the fallback).
        """
        boundary = AdaptiveVerticalCover.calculate_position(self)
        # The vertical solve already clamps to [0, h_win]; default is the floor.
        return max(boundary, OSCILLATING_PROTECTED_BOUNDARY_DEFAULT)

    def calculate_percentage(self) -> float:
        """Solve the arm-sweep arc for the lip-drop shade objective (#586).

        Scans θ ∈ [min_angle, max_angle] and returns the smallest angle whose
        lip shadow reaches the protected boundary, mapped to an open percentage.
        Fails open (max coverage) when the boundary is unreachable.
        """
        arm = self.osc_config.arm_length
        lo = float(self.osc_config.min_angle)
        hi = float(self.osc_config.max_angle)
        if arm <= 0 or hi <= lo:
            return 0.0

        pivot_y = self.h_win + self.osc_config.housing_offset
        boundary = self._protected_boundary()

        thetas = np.linspace(lo, hi, OSCILLATING_ARC_SCAN_SAMPLES)
        shadow_tops = np.array(
            [
                _lip_shadow_top(
                    t,
                    arm_length=arm,
                    pivot_y=pivot_y,
                    sol_elev=self.sol_elev,
                    gamma=self.gamma,
                    pivot_offset=self.osc_config.pivot_offset,
                )
                for t in thetas
            ]
        )
        # Credit the deepest shadow reached so far (monotone-coverage contract):
        # a physical awning never shades less as it extends, so collapse the raw
        # non-monotone lip shadow to its running floor before solving.
        coverage = _monotone_coverage_floor(shadow_tops)

        reaches = np.flatnonzero(coverage <= boundary)
        if reaches.size:
            # Smallest angle that shades down to the boundary (full shade).
            theta = float(thetas[reaches[0]])
        else:
            # Unreachable → maximise coverage (fail open toward pos→100%). The
            # monotone floor plateaus once the deepest shadow is reached; further
            # extension never *reduces* coverage, so pick the LARGEST angle that
            # still achieves the floor minimum (full extension), not the first.
            floor_min = float(coverage[-1])
            best = np.flatnonzero(coverage <= floor_min + _COVERAGE_PLATEAU_EPS)
            theta = float(thetas[best[-1]])

        self.logger.debug(
            "Oscillating calc: elev=%.1f°, gamma=%.1f°, pivot_y=%.3f, "
            "boundary=%.3f, theta=%.2f°",
            self.sol_elev,
            self.gamma,
            pivot_y,
            boundary,
            theta,
        )
        pos = (theta - lo) / (hi - lo) * 100.0
        return float(np.clip(pos, 0, 100))
