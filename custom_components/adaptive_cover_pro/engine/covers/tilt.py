"""Tilted/venetian slat cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, tan
from numpy import radians as rad

from ...config_types import TiltConfig
from ...const import (
    TILT_HORIZONTAL_DEG,
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
    TiltMode,
)
from ...geometry import SafetyMarginCalculator
from ...position_utils import PositionConverter
from .base import AdaptiveGeneralCover


def slat_cutoff_angle(
    beta: float, slat_distance: float, depth: float
) -> tuple[float, float, bool]:
    """Solve the venetian slat cut-off angle for a profile angle ``beta``.

    Single source of truth for the MDPI cut-off expression
    (https://www.mdpi.com/1996-1073/13/7/1731) plus its negative-discriminant
    guard, shared by :class:`AdaptiveTiltCover` (vertical-facade profile angle)
    and the louvered-roof engine (pitched-plane profile angle). Only ``beta``
    changes between callers; the slat geometry solve is identical.

    Returns ``(slat_angle_deg, discriminant, negative_discriminant)``:

    * ``negative_discriminant`` is ``True`` when the slat_distance/depth ratio is
      large relative to ``tan(beta)`` (``sqrt`` of a negative). NumPy would
      return ``nan`` silently; the caller returns ``0.0`` (closed) instead, so
      the angle is ``0.0`` in that case.
    * otherwise the angle is ``2·arctan((tan β + √disc)/(1 + ratio))`` in degrees.
    """
    ratio = slat_distance / depth
    discriminant = (tan(beta) ** 2) - (ratio**2) + 1
    if discriminant < 0:
        return 0.0, float(discriminant), True
    slat = 2 * np.arctan((tan(beta) + np.sqrt(discriminant)) / (1 + ratio))
    return float(np.rad2deg(slat)), float(discriminant), False


@dataclass
class AdaptiveTiltCover(AdaptiveGeneralCover):
    """Calculate state for tilted blinds."""

    tilt_config: TiltConfig = None  # type: ignore[assignment]

    @property
    def slat_distance(self) -> float:
        """Get slat distance from tilt_config."""
        return self.tilt_config.slat_distance

    @property
    def depth(self) -> float:
        """Get depth from tilt_config."""
        return self.tilt_config.depth

    @property
    def mode(self) -> TiltMode | str:
        """Get mode from tilt_config."""
        return self.tilt_config.mode

    @property
    def beta(self) -> float:
        """Calculate beta angle (incident angle of sun on slat plane).

        Beta represents the effective sun elevation angle as seen from the slat's
        perspective, accounting for both sun elevation and horizontal angle (gamma).
        Used in slat tilt calculation to block direct sun while maximizing view/light.

        Returns:
            Beta angle in radians.

        """
        beta = np.arctan(tan(rad(self.sol_elev)) / cos(rad(self.gamma)))
        return beta

    def _max_degrees(self) -> int:
        """Resolve max slat degrees for the configured mode (string or enum)."""
        if isinstance(self.mode, TiltMode):
            return self.mode.max_degrees
        return TiltMode(self.mode).max_degrees

    def _build_trace(
        self,
        *,
        beta: float,
        discriminant: float,
        negative_discriminant: bool,
        slat_angle_raw_deg: float | None,
        nan_result: bool,
        max_degrees: int,
        result: float,
        safety_margin: float = 1.0,
    ) -> dict:
        """Assemble the raw tilt solar-calculation trace (issue #682).

        Single source for the negative-discriminant guard, the NaN guard, and the
        normal return path so the key set never drifts. Raw native floats — the
        ``DiagnosticsBuilder`` rounds at the presentation boundary.
        """
        mode_value = self.mode.value if isinstance(self.mode, TiltMode) else self.mode
        return {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: PositionConverter.to_percentage(
                result, max_degrees
            ),
            "beta_rad": float(beta),
            "discriminant": float(discriminant),
            "negative_discriminant": bool(negative_discriminant),
            "slat_angle_raw_deg": (
                None if slat_angle_raw_deg is None else float(slat_angle_raw_deg)
            ),
            "nan_result": bool(nan_result),
            "max_degrees": int(max_degrees),
            "tilt_mode": str(mode_value),
            "safety_margin": float(safety_margin),
        }

    def calculate_position(self) -> float:
        """Calculate optimal slat tilt angle to block direct sun.

        Implements venetian blind optimization algorithm from:
        https://www.mdpi.com/1996-1073/13/7/1731

        Uses slat geometry (depth, spacing) and sun incident angle (beta) to
        calculate the tilt angle that blocks direct solar radiation while
        maximizing view and diffuse light.

        Supports two modes:
        - MODE1 (90°): Single-direction tilt (0° closed → 90° fully open)
        - MODE2 (180°): Bi-directional tilt (0° closed → 90° horizontal → 180° closed)

        Returns:
            Optimal slat tilt angle in degrees (0-90 for MODE1, 0-180 for MODE2).

        """
        beta = self.beta
        max_degrees = self._max_degrees()

        # Guard: discriminant can be negative when slat_distance/depth ratio is
        # large relative to tan(beta), making sqrt of a negative.  NumPy returns
        # nan silently; we return 0.0 (closed) as a safe fallback instead. The
        # cut-off math is shared with the louvered-roof engine via
        # ``slat_cutoff_angle`` (only ``beta`` differs between them).
        result, discriminant, negative_discriminant = slat_cutoff_angle(
            beta, self.slat_distance, self.depth
        )
        if negative_discriminant:
            self.logger.debug(
                "Tilt calc: negative discriminant (%.4f) — returning 0° (closed)",
                float(discriminant),
            )
            self._last_calc_details = self._build_trace(
                beta=beta,
                discriminant=discriminant,
                negative_discriminant=True,
                slat_angle_raw_deg=None,
                nan_result=False,
                max_degrees=max_degrees,
                result=0.0,
            )
            return 0.0

        # Additional nan guard in case of unexpected floating-point edge cases
        if np.isnan(result):
            self.logger.debug(
                "Tilt calc: NaN result (elev=%.1f°, gamma=%.1f°, beta=%.4f) — returning 0°",
                self.sol_elev,
                self.gamma,
                float(beta),
            )
            self._last_calc_details = self._build_trace(
                beta=beta,
                discriminant=discriminant,
                negative_discriminant=False,
                slat_angle_raw_deg=None,
                nan_result=True,
                max_degrees=max_degrees,
                result=0.0,
            )
            return 0.0

        slat_angle_raw_deg = float(result)

        # Configurable safety margin (issue #783): reuse the vertical axis'
        # angle-dependent geometry margin (>=1.0), scaled by the user's
        # ``safety_margin`` (0.0-1.0), applied in the slat-CLOSING direction.
        # Vertical multiplies a drop by the margin; tilt must instead close the
        # slats further, so we scale the closure away from horizontal. At
        # ``safety_margin=0.0`` (or a benign geometry where the geometry margin
        # is 1.0) ``eff_margin`` is exactly 1.0 and the block is skipped — a
        # provable byte-for-byte no-op that preserves the exact grazing angle.
        geo_margin = SafetyMarginCalculator.calculate(self.gamma, self.sol_elev)
        eff_margin = 1.0 + (geo_margin - 1.0) * self.tilt_config.safety_margin
        if eff_margin != 1.0:
            result = TILT_HORIZONTAL_DEG - (TILT_HORIZONTAL_DEG - result) * eff_margin

        result = max(0.0, min(float(max_degrees), float(result)))

        self.logger.debug(
            "Tilt calc: elev=%.1f°, gamma=%.1f°, beta=%.4f rad, slat_angle=%.1f°",
            self.sol_elev,
            self.gamma,
            beta,
            result,
        )
        self._last_calc_details = self._build_trace(
            beta=beta,
            discriminant=discriminant,
            negative_discriminant=False,
            slat_angle_raw_deg=slat_angle_raw_deg,
            nan_result=False,
            max_degrees=max_degrees,
            result=result,
            safety_margin=eff_margin,
        )
        return result

    def calculate_percentage(self) -> float:
        """Convert slat tilt angle to percentage for Home Assistant.

        Converts calculated tilt angle (degrees) to percentage (0-100) for cover
        entity position attribute. Maximum degrees depends on mode:
        - MODE1: 0° (closed) → 90° (fully open) = 0-100%
        - MODE2: 0° (closed) → 180° (closed inverted) = 0-100%

        Returns:
            Position as percentage (0-100).

        """
        # 0 degrees is closed, 90 degrees is open (mode1), 180 degrees is closed (mode2)
        position = self.calculate_position()

        # Handle both string and TiltMode enum for backward compatibility
        if isinstance(self.mode, TiltMode):
            max_degrees = self.mode.max_degrees
        else:
            # Convert string to TiltMode
            mode_enum = TiltMode(self.mode)
            max_degrees = mode_enum.max_degrees

        return PositionConverter.to_percentage(position, max_degrees)
