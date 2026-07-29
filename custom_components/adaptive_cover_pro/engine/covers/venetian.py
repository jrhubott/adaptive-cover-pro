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

        Delegates to the tilt engine's own :meth:`AdaptiveTiltCover._limit_tilt`
        band seam rather than rebuilding the argument bundle here. That seam
        owns the bounds, the two ``*_sun_only`` toggles and ``sun_valid=True``
        (the engine path is always sun-tracking, so the limits apply
        unconditionally — the original ``max(min, min(v, max))`` behavior), and
        routes them to the shared :meth:`PositionConverter.apply_tilt_limits`
        primitive the DefaultHandler default-tilt clamp also uses (#503). A
        hand-rolled copy here is a band that only *happens* to match.

        ``cfg.tilt_transform`` selects clamp (default, unchanged) vs the
        proportional remap into ``[min_tilt, max_tilt]`` (#957). This is the only
        seam that opts into the proportional transform; the default-tilt path
        stays on clamp.

        The sub-engine's ``apply_tilt_axis_limits=False`` does not suppress
        this, and must not: that flag answers "does the engine own the band?",
        and clearing it is precisely how this method claims ownership.
        """
        cfg = self._tilt.tilt_config
        return self._tilt._limit_tilt(value, transform=cfg.tilt_transform)

    def _compute_tilt(self) -> int:
        try:
            raw_tilt = self._tilt.calculate_raw_percentage()
        except (ValueError, ZeroDivisionError):
            return self._tilt.config.h_def
        if math.isnan(raw_tilt):
            return self._clamp_tilt(0)
        # Same seam the tilt-only solar branch uses
        # (``pipeline.helpers.solar_position_from_geometry``), so this sub-path
        # can never drift from it again (issue #1090). ``full_coverage_at_zero``
        # is the venetian TILT_AXIS semantic (``open_blocks_sun=False``); the
        # tilt engine refines it into an angle-aware direction, because on the
        # shipped MODE2 scale coverage is not monotonic in the percentage.
        # ``_clamp_tilt`` still runs after, and still on an integer — but not
        # necessarily the SAME integer: above horizontal it now receives the
        # ceil where it used to receive the floor, one point higher, which is
        # precisely the difference this fix exists to make. Both the flat clamp
        # and the proportional remap are monotonic, so a one-point-more-covering
        # input yields a no-less-covering output. The sub-engine is built with
        # ``apply_tilt_axis_limits=False``, so ``round_toward_coverage`` does not
        # re-band on its way out either — ``_clamp_tilt`` stays the single band
        # owner on this path.
        return self._clamp_tilt(
            self._tilt.round_toward_coverage(raw_tilt, full_coverage_at_zero=True)
        )

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front of window."""
        return self._vertical.direct_sun_valid
