"""Roof / skylight window (down-slope blind across pitched glass) calculation.

A roof window is controlled like a vertical blind — a single position axis whose
fabric travels *down the slope* across the pane — but the sun geometry differs:
the glass is tilted by ``roof_pitch`` degrees from horizontal, so both the
"is the sun on the outer face?" test and the "how far down the slope must the
blind travel?" projection are taken against the tilted plane rather than a
vertical wall.

The engine subclasses :class:`AdaptiveVerticalCover` and reuses its edge-case
handling, window-depth/sill effective-distance pipeline, ``cos(gamma)`` clamp,
safety margin, and final clamp to the travel length. Only the projection and the
illumination / ridge gates change.

Geometry (vectors in a frame whose ``d̂_h`` is the down-slope *horizontal*
direction the window faces and ``ẑ`` is up):

* outward normal      ``n = sinβ·d̂_h + cosβ·ẑ``
* up-slope tangent    ``t = −cosβ·d̂_h + sinβ·ẑ``
* sun direction       ``s`` with ``cos Δazi = cos(gamma)`` (Δazi = sol_azi − win_azi)

so ``s·n = sinβ·cos(elev)·cos Δazi + cosβ·sin(elev) = cos(AOI)`` and
``s·t = −cosβ·cos(elev)·cos Δazi + sinβ·sin(elev)``. The down-slope shadow is
``L_slope = effective_distance · (s·t)/(s·n)``.

Pitch convention (``roof_pitch`` β, FROM HORIZONTAL):

* ``β = 90`` → vertical glass → reproduces :class:`AdaptiveVerticalCover`
  bit-for-bit (the regression anchor; handled by an early return).
* ``β = 0``  → flat skylight → illuminated whenever the sun is above the
  horizon, azimuth-independent, no ``cos(gamma) → 0`` singularity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import atan, cos, degrees, radians, sin, tan

import numpy as np

from ...config_types import RoofWindowConfig
from .vertical import MIN_COS_GAMMA_CLAMP, AdaptiveVerticalCover

# --- Numeric guards (file-local) ---
# Pitch (from horizontal) at which the glass is vertical and the geometry
# collapses to the vertical engine exactly.
VERTICAL_GLASS_PITCH_DEG = 90.0
# Minimum |slope denominator| (s·n scaled by cos elev·cos Δazi) before the
# projection divides. The denominator is the cosine of the angle of incidence
# normalised by the FOV foreshortening; it is strictly positive across the
# illuminated region, so this guard only protects the degenerate grazing limit.
MIN_SLOPE_DENOMINATOR = 1e-9

# --- Diagnostic trace keys (surfaced in ``_last_calc_details``) ---
TRACE_KEY_ROOF_PITCH_DEG = "roof_pitch_deg"
TRACE_KEY_COS_AOI = "cos_aoi"
TRACE_KEY_SLOPE_RATIO = "slope_ratio"
TRACE_KEY_RIDGE_GATE_ENABLED = "ridge_gate_enabled"
TRACE_KEY_RIDGE_GATE_OCCLUDED = "ridge_gate_occluded"


# ---------------------------------------------------------------------------
# Pure tilted-plane geometry (single source of truth for scalar + vector paths)
# ---------------------------------------------------------------------------
# These accept floats OR pandas/numpy arrays (numpy ufuncs broadcast either),
# so the scalar gate methods and the vectorized per-day predicted window (#729)
# share ONE copy of the cos(AOI) / effective-gamma trig.


def roof_cos_aoi(gamma, elev, pitch):
    """Cosine of the angle of incidence on the tilted glass (``s·n``).

    ``cos(AOI) = sinβ·cos(elev)·cos(gamma) + cosβ·sin(elev)``. Positive → the
    sun strikes the outer face. Scalar or array inputs.
    """
    beta = np.radians(pitch)
    e = np.radians(elev)
    g = np.radians(gamma)
    return np.sin(beta) * np.cos(e) * np.cos(g) + np.cos(beta) * np.sin(e)


def roof_effective_gamma(gamma, elev, pitch):
    """In-plane (tilted-glass) FOV azimuth ``atan2(cos(elev)·sin(gamma), cos(AOI))``.

    Reduces to the horizontal gamma at β=90° and widens with decreasing pitch.
    Scalar or array inputs (the vectorized form feeds the predicted-window gate).
    """
    e = np.radians(elev)
    g = np.radians(gamma)
    return np.degrees(
        np.arctan2(np.cos(e) * np.sin(g), roof_cos_aoi(gamma, elev, pitch))
    )


def roof_slope_ratio(gamma: float, elev: float, pitch: float) -> float:
    """Down-slope shadow ratio ``(s·t)/(s·n)`` on a plane pitched ``pitch`` from horizontal.

    Single source of truth for the slope-projection math shared by the
    roof-window drop projection (``_project_drop``) and the louvered-roof
    profile angle (``beta = arctan|slope_ratio|``).

    Factoring the numerator and denominator by ``cos(elev)·cos Δazi`` expresses
    the ratio through the vertical foreshortening ``f = tan(elev)/cos(gamma)``::

        (s·t)/(s·n) = (−cosβ + sinβ·f) / (sinβ + cosβ·f)

    which collapses to ``f`` at ``β = 90°`` (vertical plane — the roof-window
    bit-for-bit regression anchor) and to ``−1/f = −cot(elev)`` at ``β = 0°``,
    ``gamma = 0`` (flat plane, aligned sun). ``cos(gamma)`` is clamped exactly as
    the vertical engine clamps it (``MIN_COS_GAMMA_CLAMP``) so the roof-window
    projection stays byte-for-byte; the tiny ``β`` denominator is clamped by
    ``MIN_SLOPE_DENOMINATOR`` at the grazing limit.
    """
    cos_gamma = float(cos(radians(gamma)))
    cos_gamma_clamped = max(abs(cos_gamma), MIN_COS_GAMMA_CLAMP) * (
        1 if cos_gamma >= 0 else -1
    )
    f = float(tan(radians(elev))) / cos_gamma_clamped
    if pitch == VERTICAL_GLASS_PITCH_DEG:
        return f
    beta = radians(pitch)
    sin_b = sin(beta)
    cos_b = cos(beta)
    numerator = -cos_b + sin_b * f
    denominator = sin_b + cos_b * f
    if abs(denominator) < MIN_SLOPE_DENOMINATOR:
        denominator = (
            MIN_SLOPE_DENOMINATOR if denominator >= 0 else -MIN_SLOPE_DENOMINATOR
        )
    return numerator / denominator


@dataclass
class AdaptiveRoofWindowCover(AdaptiveVerticalCover):
    """Calculate state for roof / skylight windows (pitched glass)."""

    roof_config: RoofWindowConfig = None  # type: ignore[assignment]

    @property
    def roof_pitch(self) -> float:
        """Glass pitch from horizontal in degrees (0=flat, 90=vertical)."""
        return self.roof_config.roof_pitch

    @property
    def roof_height_above(self) -> float:
        """Along-slope roof above the window in metres (0 disables ridge gate)."""
        return self.roof_config.roof_height_above

    # ------------------------------------------------------------------
    # Sun-on-glass geometry
    # ------------------------------------------------------------------

    def _cos_aoi(self) -> float:
        """Cosine of the angle of incidence on the tilted glass plane (``s·n``).

        ``cos(AOI) = sinβ·cos(elev)·cos Δazi + cosβ·sin(elev)`` with
        ``cos Δazi = cos(gamma)``. Positive → the sun strikes the outer face.
        At β=90° this is ``cos(elev)·cos(gamma)`` (the vertical case); at β=0°
        it is ``sin(elev)`` (a flat skylight, azimuth-independent).
        """
        return float(roof_cos_aoi(self.gamma, self.sol_elev, self.roof_pitch))

    def _effective_gamma(self) -> float:
        """FOV azimuth measured in the tilted glass plane (#212).

        Scalar wrapper over the shared :func:`roof_effective_gamma` helper:
        atan2(cos(elev)*sin(gamma), cos(AOI)). Reduces to the horizontal gamma at
        beta=90 and widens with decreasing pitch, so the user FOV bounds the
        in-plane sideways angle, not the raw horizontal azimuth. The position
        projection still uses the real gamma; only acceptance moves to the tilted
        plane. effective_gamma is elevation-dependent (the FOV breathes with sun
        height), which is correct for a tilted plane.
        """
        return float(roof_effective_gamma(self.gamma, self.sol_elev, self.roof_pitch))

    @property
    def fov_angle(self) -> float:
        """Roof-window FOV gate angle: the tilted-plane azimuth (#212).

        At vertical glass this is bit-for-bit the horizontal gamma (the
        regression anchor); below vertical it is the elevation-dependent
        in-plane azimuth, so the FOV "breathes" with sun height.
        """
        if self.roof_pitch == VERTICAL_GLASS_PITCH_DEG:
            return super().fov_angle  # bit-for-bit vertical anchor
        return self._effective_gamma()

    def solar_times_with_position(
        self,
    ) -> tuple[
        tuple[datetime, float, float] | None,
        tuple[datetime, float, float] | None,
    ]:
        """Per-day predicted sun window with the tilted-plane FOV gate (#729).

        The base delegates to ``SunGeometry`` with the raw-azimuth vertical gate.
        A pitched roof "sees" a wider azimuth swath, so the predicted window must
        use the same in-plane effective gamma the live ``fov_angle`` gate uses —
        otherwise the predicted sun-leave time lags the live ``direct_sun_valid``
        by 1–2 h. At vertical glass (β=90°) there is no widening, so delegate with
        the ``None`` (vertical) gate to preserve the bit-for-bit anchor.
        """
        pitch = self.roof_pitch
        if pitch == VERTICAL_GLASS_PITCH_DEG:
            return self.solar.solar_times_with_position()
        return self.solar.solar_times_with_position(
            fov_angle_series=lambda gamma, elev: roof_effective_gamma(
                gamma, elev, pitch
            )
        )

    def _is_sun_behind_ridge(self) -> bool:
        """Whether the roof above the window occludes the sun (ridge gate, #212).

        Active only up-dip (``cos Δazi < 0`` — sun on the ridge side). The roof
        above the window subtends ``θ_R = atan(tanβ · (−cos Δazi))``; a sun
        lower than that is hidden behind the roof. ``roof_height_above = 0``
        (no roof above, e.g. a window at the ridge) disables the gate.
        """
        if self.roof_height_above <= 0:
            return False
        cos_dazi = cos(radians(self.gamma))
        if cos_dazi >= 0:  # down-dip — the ridge never occludes
            return False
        theta_r = degrees(atan(tan(radians(self.roof_pitch)) * (-cos_dazi)))
        return self.sol_elev < theta_r

    # ------------------------------------------------------------------
    # Gate overrides (compose AOI + ridge into the inherited gate chain)
    # ------------------------------------------------------------------

    @property
    def valid_elevation(self) -> bool:
        """Replace the bare above-horizon test with the tilted-plane AOI gate.

        Keeps the inherited min/max-elevation bounds (``super().valid_elevation``)
        and additionally requires the sun to strike the outer glass face
        (``cos(AOI) > 0``). The azimuth FOV gate still applies in ``valid``.
        """
        return bool(super().valid_elevation and self._cos_aoi() > 0)

    @property
    def direct_sun_valid(self) -> bool:
        """Direct sun also requires the roof above the window not to occlude it.

        The ridge horizon ``θ_R`` coincides with the angle of incidence going to
        zero on the up-dip side, so on its own this gate is subsumed by the AOI
        illumination test in ``valid_elevation``. It is kept as an explicit,
        independently-traced gate (per the #212 design — "composed into
        ``direct_sun_valid`` like the blind spot") so the decision trace can
        attribute an up-dip miss to the roof above the window.
        """
        return bool(super().direct_sun_valid and not self._is_sun_behind_ridge())

    # ------------------------------------------------------------------
    # Slope projection (reuses the vertical effective-distance pipeline)
    # ------------------------------------------------------------------

    def _project_drop(
        self, effective_distance: float
    ) -> tuple[float, float, float, float]:
        """Re-project the protected distance onto the pitched glass slope.

        Returns ``(base_height, cos_gamma, cos_gamma_clamped, path_length)`` so
        the inherited ``calculate_position`` trace stays populated. At β=90° this
        returns the vertical drop unchanged (bit-for-bit regression anchor).

        For β<90° the down-slope shadow is ``effective_distance · (s·t)/(s·n)``.
        Factoring numerator and denominator by ``cos(elev)·cos Δazi`` expresses
        the ratio through the vertical foreshortening ``f = tan(elev)/cos(gamma)``
        already computed for the vertical case::

            (s·t)/(s·n) = (−cosβ + sinβ·f) / (sinβ + cosβ·f)

        which collapses to ``f`` at β=90°. The magnitude is taken (the sign only
        encodes up- vs down-slope direction) and the surrounding
        ``calculate_position`` clamps it to ``[0, h_win]``.
        """
        base_height, cos_gamma, cos_gamma_clamped, path_length = super()._project_drop(
            effective_distance
        )
        # Slope ratio (s·t)/(s·n) via the shared helper — at β=90° it is the
        # vertical foreshortening f = tan(elev)/cos(gamma) and the vertical drop
        # is returned unchanged (bit-for-bit anchor).
        slope_ratio = roof_slope_ratio(self.gamma, self.sol_elev, self.roof_pitch)
        self._roof_slope_ratio = slope_ratio
        if self.roof_pitch == VERTICAL_GLASS_PITCH_DEG:
            return base_height, cos_gamma, cos_gamma_clamped, path_length

        slope_drop = abs(effective_distance * slope_ratio)
        return slope_drop, cos_gamma, cos_gamma_clamped, path_length

    def calculate_position(
        self, effective_distance_override: float | None = None
    ) -> float:
        """Vertical solve on the pitched plane, then surface roof trace keys."""
        result = super().calculate_position(effective_distance_override)
        self._last_calc_details = {
            **self._last_calc_details,
            TRACE_KEY_ROOF_PITCH_DEG: float(self.roof_pitch),
            TRACE_KEY_COS_AOI: float(self._cos_aoi()),
            TRACE_KEY_SLOPE_RATIO: float(getattr(self, "_roof_slope_ratio", 0.0)),
            TRACE_KEY_RIDGE_GATE_ENABLED: bool(self.roof_height_above > 0),
            TRACE_KEY_RIDGE_GATE_OCCLUDED: bool(self._is_sun_behind_ridge()),
        }
        return result
