"""Horizontal awning (in/out) cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

from numpy import sin
from numpy import radians as rad

from ...config_types import HorizontalConfig
from ...position_utils import PositionConverter
from .vertical import AdaptiveVerticalCover


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
            Awning extension length in meters (may exceed awn_length if full
            extension insufficient to block sun).

        """
        awn_angle = 90 - self.awn_angle
        a_angle = 90 - self.sol_elev
        c_angle = 180 - awn_angle - a_angle

        vertical_position = super().calculate_position()
        length = ((self.h_win - vertical_position) * sin(rad(a_angle))) / sin(
            rad(c_angle)
        )
        self.logger.debug(
            "Horizontal calc: elev=%.1f°, gamma=%.1f°, awn_angle=%s°, "
            "vertical_pos=%.3f, length=%.3f",
            self.sol_elev,
            self.gamma,
            self.awn_angle,
            vertical_position,
            length,
        )
        # return np.clip(length, 0, self.awn_length)
        return length

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
