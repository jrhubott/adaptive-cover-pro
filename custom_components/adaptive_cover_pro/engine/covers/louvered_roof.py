"""Louvered / lamella roof (horizontal-plane slat) calculation (#830).

A louvered roof ("Lamellendach" / bioclimatic pergola) rotates slats around a
horizontal axis lying in a horizontal or pitched roof plane, and reports through
``set_cover_tilt_position`` — a single tilt axis, exactly like ``cover_tilt``.
Geometrically it is the cross-product of two shipped engines:

* the venetian slat cut-off solver (``AdaptiveTiltCover`` — this class's parent,
  reused unchanged via ``slat_cutoff_angle``), and
* the pitched-plane sun geometry of the roof window (``roof_cos_aoi`` /
  ``roof_effective_gamma`` / ``roof_slope_ratio``).

Only three things change relative to the vertical-facade venetian case:

* the profile angle ``beta`` is taken relative to the roof plane —
  ``beta = arctan|roof_slope_ratio(gamma, elev, roof_pitch)|`` (the vertical
  case is the ``roof_pitch = 90°`` reduction);
* the illumination gate requires the sun to strike the working face
  (``roof_cos_aoi > 0``) rather than a bare above-horizon test;
* the FOV gate is measured in the tilted roof plane
  (``roof_effective_gamma``), mirroring the roof window.

Pitch convention (``roof_pitch``, FROM HORIZONTAL):

* ``roof_pitch = 90`` → vertical plane → ``beta`` equals the venetian/tilt
  profile angle (a true superset).
* ``roof_pitch = 0`` (default) → flat roof → for aligned sun ``abs(beta)`` is the
  COMPLEMENT of the vertical case, ``90° − elev`` (the reference plane is the
  horizontal, not the vertical facade), and the AOI gate is azimuth-independent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...config_types import LouveredRoofConfig
from .roof_window import (
    TRACE_KEY_COS_AOI,
    TRACE_KEY_ROOF_PITCH_DEG,
    TRACE_KEY_SLOPE_RATIO,
    VERTICAL_GLASS_PITCH_DEG,
    roof_cos_aoi,
    roof_effective_gamma,
    roof_slope_ratio,
)
from .tilt import AdaptiveTiltCover

# Decision-trace key: which side of the slat axis the sun is on. ``True`` = far
# side (``cos(gamma) < 0``), where the cut-off angle is realized as ``180° − θ``.
TRACE_KEY_FAR_SIDE_BRANCH = "louvered_far_side_branch"


@dataclass
class AdaptiveLouveredRoofCover(AdaptiveTiltCover):
    """Calculate slat tilt for a louvered roof (slats in a horizontal/pitched plane)."""

    roof_config: LouveredRoofConfig = None  # type: ignore[assignment]

    @property
    def roof_pitch(self) -> float:
        """Roof-plane pitch from horizontal in degrees (0=flat, 90=vertical)."""
        return self.roof_config.roof_pitch

    def _cos_aoi(self) -> float:
        """Cosine of the angle of incidence on the roof plane (``s·n``).

        Positive → the sun strikes the working (outer) face of the slats. At
        ``roof_pitch = 0`` this is ``sin(elev)`` (azimuth-independent); at
        ``roof_pitch = 90`` it is ``cos(elev)·cos(gamma)`` (the vertical case).
        """
        return float(roof_cos_aoi(self.gamma, self.sol_elev, self.roof_pitch))

    @property
    def beta(self) -> float:
        """Profile angle of the sun relative to the roof plane (radians).

        Overrides the vertical-facade profile angle with the roof-plane slope
        ratio. The magnitude is taken here; the near/far sign is re-applied in
        ``_resolve_slat_angle`` (far-side sun realizes the flipped face,
        ``180° − θ``). The slat cut-off solver squares/uses ``tan(beta)`` and the
        AOI gate handles the sun-behind-face case separately.
        """
        return float(
            np.arctan(abs(roof_slope_ratio(self.gamma, self.sol_elev, self.roof_pitch)))
        )

    def _is_far_side(self) -> bool:
        """Whether the sun is on the far side of the slat axis (``cos(gamma) < 0``).

        The magnitude profile angle ``beta`` discards the near/far sign, so the
        raw cut-off is identical either side of the axis. On a flat roof the AOI
        gate is azimuth-independent, so far-side (evening) sun is tracked and the
        cut-off must be realized on the flipped face. ``cos(gamma) < 0`` gives the
        correct near/far split on a flat roof and — unlike ``sign(slope_ratio)`` —
        never fires at vertical pitch, where lit sun is always near side and the
        venetian anchor must stay byte-for-byte.
        """
        return bool(float(np.cos(np.radians(self.gamma))) < 0)

    def _resolve_slat_angle(self, cutoff_angle: float) -> float:
        """Realize the far-side cut-off as the flipped face past the 90° turnover."""
        if self._is_far_side():
            return 180.0 - cutoff_angle
        return cutoff_angle

    def _effective_max_degrees(self) -> int:
        """Honour a configured physical ``max_slat_angle`` over the tilt-mode max.

        ``0`` (the sentinel default) falls back to the mode's 90°/180°; a nonzero
        value becomes BOTH the clamp ceiling and the tilt%→angle denominator.
        """
        m = self.roof_config.max_slat_angle
        return int(m) if m else self._max_degrees()

    @property
    def valid_elevation(self) -> bool:
        """Keep the elevation bounds and additionally require sun on the face.

        Composes the inherited min/max-elevation gate with the tilted-plane AOI
        illumination test (``cos(AOI) > 0``), mirroring
        :class:`AdaptiveRoofWindowCover`.
        """
        return bool(super().valid_elevation and self._cos_aoi() > 0)

    @property
    def fov_angle(self) -> float:
        """FOV azimuth measured in the tilted roof plane (#830, mirrors #212).

        At a vertical plane (``roof_pitch = 90``) this is the raw horizontal
        gamma (bit-for-bit vertical anchor); below vertical it is the
        elevation-dependent in-plane azimuth, so the FOV "breathes" with sun
        height.
        """
        if self.roof_pitch == VERTICAL_GLASS_PITCH_DEG:
            return super().fov_angle
        return float(roof_effective_gamma(self.gamma, self.sol_elev, self.roof_pitch))

    def calculate_position(self) -> float:
        """Venetian slat solve on the roof plane, then surface roof trace keys."""
        result = super().calculate_position()
        self._last_calc_details = {
            **self._last_calc_details,
            TRACE_KEY_ROOF_PITCH_DEG: float(self.roof_pitch),
            TRACE_KEY_COS_AOI: self._cos_aoi(),
            TRACE_KEY_SLOPE_RATIO: float(
                roof_slope_ratio(self.gamma, self.sol_elev, self.roof_pitch)
            ),
            TRACE_KEY_FAR_SIDE_BRANCH: self._is_far_side(),
        }
        return result
