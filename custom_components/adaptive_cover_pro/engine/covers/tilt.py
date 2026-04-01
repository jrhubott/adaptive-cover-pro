"""Tilted/venetian slat cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, tan
from numpy import radians as rad

from ...config_types import TiltConfig
from ...enums import TiltMode
from ...position_utils import PositionConverter
from .base import AdaptiveGeneralCover


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

        slat = 2 * np.arctan(
            (
                tan(beta)
                + np.sqrt(
                    (tan(beta) ** 2) - ((self.slat_distance / self.depth) ** 2) + 1
                )
            )
            / (1 + self.slat_distance / self.depth)
        )
        result = np.rad2deg(slat)

        self.logger.debug(
            "Tilt calc: elev=%.1f°, gamma=%.1f°, beta=%.4f rad, slat_angle=%.1f°",
            self.sol_elev,
            self.gamma,
            beta,
            result,
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
