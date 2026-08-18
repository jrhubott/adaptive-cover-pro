"""Day/Night dual-fabric shade calculation (#993, Model A).

A day/night shade drives both ``set_cover_position`` (bottom rail / total
coverage) and ``set_cover_tilt_position`` on a single HA entity — but the tilt
axis is reinterpreted as the *fabric blend*: the share of the covered band that
is the light-filtering "sheer" fabric versus the opaque "blackout" fabric.

Position rides the normal vertical pipeline (identical to ``cover_blind`` /
``cover_venetian``), so this engine composes :class:`AdaptiveVerticalCover` for
the carriage. The blend is a pure fabric-selection decision — there is NO slat
geometry here, unlike the venetian engine's tilt solve. Fabric selection keys
on the season (``is_summer``) and the two fabric opacities:

* comfortable / winter → sheer (blend 100), letting daylight through while
  filtering glare;
* summer direct sun → escalate to blackout (blend 0) when the sheer fabric is
  too transparent to block the heat (``opacity_sheer < blackout_threshold``);
  the climate bucket forces blackout unconditionally.

Blend polarity (fixed by the policy + a pinned test): ``100`` = all sheer,
``0`` = all blackout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...config_types import CoverConfig, DayNightShadeConfig, VerticalConfig
from ...position_utils import covered_fraction
from ...sun import SunData
from .venetian import DualAxisResult
from .vertical import AdaptiveVerticalCover

# Blend-axis polarity constants (0–100 %). ``result.tilt`` carries the blend.
DAY_NIGHT_SHEER = 100  # all sheer — light-filtering fully deployed
DAY_NIGHT_BLACKOUT = 0  # all blackout — opaque band fully deployed


@dataclass(frozen=True)
class FabricSelection:
    """The chosen fabric plus its light-filtering estimate for one cycle.

    ``blend`` is the value written to the blend axis (``DAY_NIGHT_SHEER`` /
    ``DAY_NIGHT_BLACKOUT``). ``opacity`` is the chosen fabric's light-blocking
    percentage; ``covered_fraction`` is ``(100 - position) / 100`` — the share of
    the window the carriage covers; ``filtering_estimate`` is their product, the
    approximate share of incident light the deployed fabric blocks.
    """

    blend: int
    opacity: int
    covered_fraction: float
    filtering_estimate: float


class DayNightShadeCalculation:
    """Compose the vertical carriage position with a fabric-blend selection.

    The live pipeline resolves position through the handlers and asks this
    engine only for the paired fabric blend via :meth:`select_fabric` (the
    position argument comes from the pipeline). :meth:`calculate_dual` exists
    for engine-level tests and completeness; it drives the vertical calc itself.
    """

    def __init__(
        self,
        *,
        config: CoverConfig,
        vert_config: VerticalConfig,
        day_night_config: DayNightShadeConfig,
        sun_data: SunData,
        sol_azi: float,
        sol_elev: float,
        logger,
    ) -> None:
        """Initialise the vertical sub-calculator + the fabric config."""
        self._vertical = AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=vert_config,
        )
        self._dn = day_night_config

    @property
    def direct_sun_valid(self) -> bool:
        """Whether the sun is directly in front of the window."""
        return self._vertical.direct_sun_valid

    def calculate_position(self) -> int:
        """Return the raw vertical carriage position (0–100)."""
        return math.floor(self._vertical.calculate_raw_percentage())

    def select_fabric(
        self,
        position: int,
        *,
        is_summer: bool,
        allow_sheer_in_summer: bool,
    ) -> FabricSelection:
        """Choose the fabric for ``position`` and compute its filtering estimate.

        ``allow_sheer_in_summer`` distinguishes the two callers: the SOLAR path
        passes ``True`` (keep the sheer fabric in summer unless it is too
        transparent to block the heat — ``opacity_sheer < blackout_threshold``);
        the climate path passes ``False`` (summer forces blackout regardless of
        opacity). Non-summer always keeps sheer.
        """
        blackout = is_summer and not (
            allow_sheer_in_summer
            and self._dn.opacity_sheer >= self._dn.blackout_threshold
        )
        blend = DAY_NIGHT_BLACKOUT if blackout else DAY_NIGHT_SHEER
        opacity = self._dn.opacity_blackout if blackout else self._dn.opacity_sheer
        # The carriage rides the position axis with blind polarity (0 % = fully
        # covered), so the shared primitive answers it — the arithmetic is not
        # re-spelt here (#1236).
        covered = covered_fraction(position, open_blocks_sun=False)
        return FabricSelection(
            blend=blend,
            opacity=opacity,
            covered_fraction=covered,
            filtering_estimate=opacity * covered,
        )

    def calculate_dual(
        self,
        *,
        is_summer: bool = False,
        allow_sheer_in_summer: bool = True,
    ) -> DualAxisResult:
        """Calculate carriage position + fabric blend as a ``DualAxisResult``."""
        position = self.calculate_position()
        selection = self.select_fabric(
            position,
            is_summer=is_summer,
            allow_sheer_in_summer=allow_sheer_in_summer,
        )
        return DualAxisResult(position=position, tilt=selection.blend)
