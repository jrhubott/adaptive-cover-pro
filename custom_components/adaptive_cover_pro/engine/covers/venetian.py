"""Dual-axis calculation for venetian blinds with both position and tilt."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...config_types import CoverConfig, TiltConfig, VerticalConfig
from ...position_utils import PositionConverter
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
            # The dual-axis path applies the tilt-axis limits itself at
            # ``_clamp_tilt`` (below), so the sub-engine must not also clamp in
            # ``calculate_percentage`` — a double proportional remap would
            # otherwise compress the band twice (issue #964).
            apply_tilt_axis_limits=False,
        )

    def calculate_dual(self) -> DualAxisResult:
        """Calculate both vertical position and tilt angle.

        When tilt geometry is undefined (e.g. sun nearly perpendicular to
        slat plane), falls back to the configured default position (h_def).

        Returns:
            DualAxisResult with position (0-100) and tilt (0-100)

        """
        position = math.floor(self._vertical.calculate_raw_percentage())
        return DualAxisResult(position=position, tilt=self._compute_tilt())

    def tilt_for_position(self, position: int) -> int:
        """Return the engine-derived tilt for a position resolved upstream.

        The pipeline picks the position (solar / climate / overrides /
        sunset / default).  This call exists so the coordinator can keep
        position decision-making in the pipeline and ask the engine only
        for the matching slat angle.  ``position`` is unused for the slat
        math itself — slat angle is a function of sun geometry — but the
        argument keeps the call site self-documenting.
        """
        return self._compute_tilt()

    def _clamp_tilt(self, value: int) -> int:
        """Clamp a tilt value to the configured ``[min_tilt, max_tilt]`` range.

        Applied to every engine-derived tilt — including the NaN fallback — so
        ``min_tilt`` is a true floor, not just "applied when geometry resolves".

        Delegates to the shared :meth:`PositionConverter.apply_tilt_limits`
        primitive with ``sun_valid=True`` (this is the sun-tracking engine
        path), so the clamp policy lives in exactly one place shared with the
        DefaultHandler default-tilt clamp (#503). With ``sun_valid=True`` the
        limits always apply regardless of the ``*_sun_only`` toggles, preserving
        the original unconditional ``max(min, min(v, max))`` behavior.

        ``cfg.tilt_transform`` selects clamp (default, unchanged) vs the
        proportional remap into ``[min_tilt, max_tilt]`` (#957). This is the only
        seam that opts into the proportional transform; the default-tilt path
        stays on clamp.
        """
        cfg = self._tilt.tilt_config
        return PositionConverter.apply_tilt_limits(
            value,
            cfg.min_tilt,
            cfg.max_tilt,
            cfg.min_tilt_sun_only,
            cfg.max_tilt_sun_only,
            sun_valid=True,
            transform=cfg.tilt_transform,
        )

    def _compute_tilt(self) -> int:
        try:
            raw_tilt = self._tilt.calculate_percentage()
        except (ValueError, ZeroDivisionError):
            return self._tilt.config.h_def
        if math.isnan(raw_tilt):
            return self._clamp_tilt(0)
        return self._clamp_tilt(round(raw_tilt))

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front of window."""
        return self._vertical.direct_sun_valid
