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
from math import atan, cos, degrees, radians, sin, tan

from ...config_types import RoofWindowConfig
from .vertical import AdaptiveVerticalCover

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
        beta = radians(self.roof_pitch)
        elev = radians(self.sol_elev)
        cos_dazi = cos(radians(self.gamma))
        return sin(beta) * cos(elev) * cos_dazi + cos(beta) * sin(elev)

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
        # Vertical foreshortening f = tan(elev)/cos(gamma): the slope ratio at β=90°.
        f = float(tan(radians(self.sol_elev))) / cos_gamma_clamped
        if self.roof_pitch == VERTICAL_GLASS_PITCH_DEG:
            self._roof_slope_ratio = f
            return base_height, cos_gamma, cos_gamma_clamped, path_length

        beta = radians(self.roof_pitch)
        sin_b = sin(beta)
        cos_b = cos(beta)
        numerator = -cos_b + sin_b * f
        denominator = sin_b + cos_b * f
        if abs(denominator) < MIN_SLOPE_DENOMINATOR:
            denominator = (
                MIN_SLOPE_DENOMINATOR if denominator >= 0 else -MIN_SLOPE_DENOMINATOR
            )
        slope_ratio = numerator / denominator
        self._roof_slope_ratio = slope_ratio
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
