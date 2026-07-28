"""Horizontal awning (in/out) cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import sin
from numpy import radians as rad

from ...config_types import HorizontalConfig
from ...const import (
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
)
from ...position_utils import PositionConverter
from .vertical import AdaptiveVerticalCover

# --- Numeric guards (file-local) ---
# Threshold below which sin(c_angle) is treated as zero to avoid division
# by near-zero. Hit when sun elevation + awning angle ≈ 90°.
SIN_NEAR_ZERO_THRESHOLD = 1e-6


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
        a_angle: float,
        c_angle: float,
        vertical_position: float,
        sin_c: float,
        sin_c_near_zero: bool,
        length: float,
        result: float,
        clamped_to_awn_length: bool,
    ) -> dict:
        """Assemble the raw horizontal solar-calculation trace (issue #682).

        Set AFTER the ``super().calculate_position()`` call so the awning trace
        overwrites the vertical trace that the super sets on ``_last_calc_details``
        (the latent overwrite bug noted in issue #682). Single source for both the
        sin_c guard return and the normal/clip return. Raw native floats — rounding
        happens at the ``DiagnosticsBuilder`` presentation boundary.
        """
        return {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: PositionConverter.to_percentage(
                result, self.awn_length
            ),
            "awn_angle_deg": float(awn_angle),
            "a_angle_deg": float(a_angle),
            "c_angle_deg": float(c_angle),
            "vertical_position_m": float(vertical_position),
            "sin_c": float(sin_c),
            "sin_c_near_zero": bool(sin_c_near_zero),
            "length_m": float(length),
            "clamped_to_awn_length": bool(clamped_to_awn_length),
        }

    def calculate_position(self) -> float:
        """Calculate awning extension length using trigonometric projection.

        Converts vertical blind height to horizontal awning length using the law
        of sines based on sun elevation and awning mounting angle.

        Calculation:
        1. Get vertical blind position that would block sun
        2. Convert to gap above blind: h_win - vertical_position
        3. Project to awning length using triangle geometry:
           length = gap × sin(sun_angle) / sin(awning_closure_angle)

        Returns:
            Awning extension length in meters, saturated at awn_length.
            When the geometric solution exceeds full extension, the awning
            is reported fully extended (100%) rather than overflowing.

        """
        awn_angle = 90 - self.awn_angle
        a_angle = 90 - self.sol_elev
        c_angle = 180 - awn_angle - a_angle

        vertical_position = super().calculate_position()

        # Guard: c_angle near zero → sin(c_angle) ≈ 0 → division by zero.
        # Return full awning extension as a safe fallback.
        sin_c = float(sin(rad(c_angle)))
        if abs(sin_c) < SIN_NEAR_ZERO_THRESHOLD:
            self.logger.debug(
                "Horizontal calc: c_angle=%.2f° near zero — returning full extension",
                c_angle,
            )
            self._last_calc_details = self._build_horizontal_trace(
                awn_angle=awn_angle,
                a_angle=a_angle,
                c_angle=c_angle,
                vertical_position=vertical_position,
                sin_c=sin_c,
                sin_c_near_zero=True,
                length=self.awn_length,
                result=self.awn_length,
                clamped_to_awn_length=False,
            )
            return self.awn_length

        length = float(((self.h_win - vertical_position) * sin(rad(a_angle))) / sin_c)
        self.logger.debug(
            "Horizontal calc: elev=%.1f°, gamma=%.1f°, awn_angle=%s°, "
            "vertical_pos=%.3f, length=%.3f",
            self.sol_elev,
            self.gamma,
            self.awn_angle,
            vertical_position,
            length,
        )
        result = float(np.clip(length, 0, self.awn_length))
        self._last_calc_details = self._build_horizontal_trace(
            awn_angle=awn_angle,
            a_angle=a_angle,
            c_angle=c_angle,
            vertical_position=vertical_position,
            sin_c=sin_c,
            sin_c_near_zero=False,
            length=length,
            result=result,
            clamped_to_awn_length=bool(length > self.awn_length),
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
