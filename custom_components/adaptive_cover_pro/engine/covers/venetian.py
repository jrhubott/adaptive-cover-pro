"""Dual-axis calculation for venetian blinds with both position and tilt."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...config_types import CoverConfig, TiltConfig, VerticalConfig
from ...sun import SunData
from .tilt import AdaptiveTiltCover
from .vertical import AdaptiveVerticalCover


@dataclass(frozen=True)
class DualAxisResult:
    """Result of a dual-axis calculation."""

    position: int  # 0-100 vertical position percentage
    tilt: int  # 0-100 tilt angle percentage


class VenetianCoverCalculation:
    """Dual-axis calculation composing vertical position + slat tilt.

    For covers that expose both position and tilt on a single HA entity
    (e.g., KNX venetian blinds). Composes existing VerticalCover and
    TiltCover calculations.

    Not yet wired into coordinator/config_flow — this is the calculation
    engine ready for when Issue #33's config UI is implemented.
    """

    def __init__(
        self,
        config: CoverConfig,
        vert_config: VerticalConfig,
        tilt_config: TiltConfig,
        sun_data: SunData,
        sol_azi: float,
        sol_elev: float,
        logger,
    ) -> None:
        """Initialise both the vertical and tilt sub-calculators."""
        self._vertical = AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=vert_config,
        )
        self._tilt = AdaptiveTiltCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=tilt_config,
        )

    def calculate_dual(self) -> DualAxisResult:
        """Calculate both vertical position and tilt angle.

        When tilt geometry is undefined (e.g. sun nearly perpendicular to
        slat plane), falls back to the configured default position (h_def).

        Returns:
            DualAxisResult with position (0-100) and tilt (0-100)

        """
        position = round(self._vertical.calculate_percentage())
        try:
            raw_tilt = self._tilt.calculate_percentage()
            tilt = 0 if math.isnan(raw_tilt) else round(raw_tilt)
        except (ValueError, ZeroDivisionError):
            tilt = self._tilt.config.h_def
        return DualAxisResult(position=position, tilt=tilt)

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front of window."""
        return self._vertical.direct_sun_valid
