"""Horizontal awning (in/out) cover calculation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy import cos, sin, tan
from numpy import radians as rad

from ...config_types import HorizontalConfig
from ...const import (
    AWNING_SHADE_MODE_AREA,
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
)
from ...position_utils import PositionConverter
from .vertical import AdaptiveVerticalCover

# --- Numeric guards (file-local) ---
# |cos(gamma)| at or below this treats the sun as being in / behind the window
# plane (|gamma| ≥ 90°): no direct sun reaches the glass, so the overhang
# retracts fully. cos(radians(90)) is +6e-17, comfortably below this bound.
COS_GAMMA_RETRACT_THRESHOLD = 1e-9
# Denominator floor for the overhang projection. In the fixed-awning domain
# (awn_angle ∈ [0, 45°], sun above horizon and in front of the plane) the
# denominator is strictly positive; this only guards a degenerate build.
DENOM_NEAR_ZERO_THRESHOLD = 1e-9


@dataclass
class AdaptiveHorizontalCover(AdaptiveVerticalCover):
    """Calculate state for Horizontal blinds."""

    horiz_config: HorizontalConfig = None  # type: ignore[assignment]

    @property
    def awn_length(self) -> float:
        """Get awning length from horiz_config."""
        return self.horiz_config.awn_length

    @property
    def awn_angle(self) -> float:
        """Get awning angle from horiz_config."""
        return self.horiz_config.awn_angle

    def _build_horizontal_trace(
        self,
        *,
        awn_angle: float,
        shade_mode: str,
        cos_gamma: float,
        profile_angle: float,
        denom: float,
        length: float,
        result: float,
        clamped_to_awn_length: bool,
    ) -> dict:
        """Assemble the raw horizontal solar-calculation trace (issue #682, #1025).

        Single source for the area-mode, retracted, and normal/clip returns so
        the key set never drifts between them. Raw native floats — rounding
        happens at the ``DiagnosticsBuilder`` presentation boundary.
        """
        return {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: PositionConverter.to_percentage(
                result, self.awn_length
            ),
            "awn_angle_deg": float(awn_angle),
            "shade_mode": str(shade_mode),
            "cos_gamma": float(cos_gamma),
            "profile_angle_deg": float(profile_angle),
            "denom": float(denom),
            "length_m": float(length),
            "clamped_to_awn_length": bool(clamped_to_awn_length),
        }

    def calculate_position(self) -> float:
        """Calculate awning extension length (metres) from the sun geometry.

        Two shade strategies (issue #1025):

        * **window** (default): project the horizontal overhang needed to shade
          the window glass. With awning tilt ``β`` the extension is
          ``L = h_win / (sin β + cos β · tan α / cos γ) − distance``; at ``β = 0``
          this reduces to ``L = h_win · cos γ / tan α − distance``. The
          projection decays smoothly to 0 as ``|γ| → 90°`` and stays 0 for
          ``|γ| ≥ 90°`` (sun behind the window plane), so there is no
          discontinuity — the issue #1025 defect. It intentionally ignores
          ``sill_height`` / ``window_depth`` / the safety margin (they belonged
          to the old, incorrect vertical-blind coupling) but keeps ``distance``.
        * **area**: keep the awning fully extended across the whole field of
          view (patio / area shading). ``calculate_position`` is only ever
          invoked when the sun is in-FOV, so returning full extension means
          "extended whenever the sun is in front, default otherwise" — and it
          must stay extended past ``|γ| = 90°`` (a wide-FOV patio awning).

        Returns:
            Awning extension length in metres, clipped to ``[0, awn_length]``.

        """
        awn_length = self.awn_length
        awn_angle = self.awn_angle
        shade_mode = self.horiz_config.shade_mode

        # --- Area mode: fully extended whenever the sun is in-FOV. -----------
        if shade_mode == AWNING_SHADE_MODE_AREA:
            self.logger.debug(
                "Horizontal calc (area): elev=%.1f°, gamma=%.1f° → full extension",
                self.sol_elev,
                self.gamma,
            )
            self._last_calc_details = self._build_horizontal_trace(
                awn_angle=awn_angle,
                shade_mode=shade_mode,
                cos_gamma=float(cos(rad(self.gamma))),
                profile_angle=0.0,
                denom=0.0,
                length=awn_length,
                result=awn_length,
                clamped_to_awn_length=False,
            )
            return awn_length

        # --- Window mode: correct overhang projection. ----------------------
        cos_gamma = float(cos(rad(self.gamma)))
        if self.sol_elev <= 0 or cos_gamma <= COS_GAMMA_RETRACT_THRESHOLD:
            # Sun below the horizon OR behind the window plane (|gamma| ≥ 90°).
            length = 0.0
            profile_angle = 0.0
            denom = 0.0
            result = 0.0
        else:
            profile = float(tan(rad(self.sol_elev))) / cos_gamma
            denom = float(sin(rad(awn_angle)) + cos(rad(awn_angle)) * profile)
            if denom <= DENOM_NEAR_ZERO_THRESHOLD:
                length = 0.0
            else:
                length = self.h_win / denom - self.distance
            profile_angle = math.degrees(math.atan(profile))
            result = float(np.clip(length, 0.0, awn_length))

        self.logger.debug(
            "Horizontal calc (window): elev=%.1f°, gamma=%.1f°, awn_angle=%s°, "
            "cos_gamma=%.4f, length=%.3f, result=%.3f",
            self.sol_elev,
            self.gamma,
            awn_angle,
            cos_gamma,
            length,
            result,
        )
        self._last_calc_details = self._build_horizontal_trace(
            awn_angle=awn_angle,
            shade_mode=shade_mode,
            cos_gamma=cos_gamma,
            profile_angle=profile_angle,
            denom=denom,
            length=length,
            result=result,
            clamped_to_awn_length=bool(length > awn_length),
        )
        return result

    def calculate_percentage(self) -> float:
        """Convert awning extension to percentage for Home Assistant.

        Converts calculated awning length (meters) to percentage (0-100) for
        cover entity position attribute.

        Returns:
            Position as percentage (0-100).

        """
        return PositionConverter.to_percentage(
            self.calculate_position(), self.awn_length
        )

    def calculate_raw_percentage(self) -> float:
        """Unrounded geometry fraction for directional rounding (issue #978).

        Bypasses the ``round()`` inside ``PositionConverter.to_percentage`` so
        that :func:`pipeline.helpers.solar_position_from_geometry` can apply
        ``floor()`` / ``ceil()`` / ``round()`` as configured.
        """
        return (float(self.calculate_position()) / self.awn_length) * 100.0
