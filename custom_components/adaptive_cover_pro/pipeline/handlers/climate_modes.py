"""Declarative climate-mode rule tables.

The four climate routers (normal/tilt × presence/no-presence) used to repeat the
same season-condition expressions (low-light, winter-insulation, winter-heating,
summer) in slightly different orders. This module factors the shared predicate
vocabulary into one place (`ClimateContext` properties) and expresses each router
as an ordered list of `ClimateRule`s evaluated first-match-wins. `ClimateCoverState`
builds a context and delegates to `evaluate_rules`, preserving the exact branch
order, `ClimateStrategy` labels, and position outputs of the original code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...const import (
    CLIMATE_DEFAULT_TILT_ANGLE,
    CLIMATE_SUMMER_TILT_ANGLE,
    POSITION_CLOSED,
    ClimateStrategy,
)
from ...cover_types import TiltPolicy

# ---------------------------------------------------------------------------
# Context: shared predicate vocabulary + the data each position fn may need
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClimateContext:
    """Everything the climate rules read, with the season predicates centralised.

    ``data`` is the ``ClimateCoverData``; ``cover`` the engine cover object;
    ``solar_position`` is the bound ``ClimateCoverState._solar_position``.
    ``gamma_deg``/``beta_deg`` are precomputed tilt geometry (0.0 for non-tilt
    covers, which never consult the tilt position fns).
    """

    data: Any
    cover: Any
    default_position: int
    solar_position: Callable[[], int]
    gamma_deg: float = 0.0
    beta_deg: float = 0.0

    # --- shared season predicates (single source of truth) ---
    @property
    def cover_valid(self) -> bool:
        """Whether the cover's geometry/sun calc is currently valid."""
        return bool(self.cover.valid)

    @property
    def is_winter(self) -> bool:
        """Whether the climate data reports a winter (heating) state."""
        return bool(self.data.is_winter)

    @property
    def is_summer(self) -> bool:
        """Whether the climate data reports a summer (cooling) state."""
        return bool(self.data.is_summer)

    @property
    def is_low_light(self) -> bool:
        """Whether lux/irradiance/no-sun indicates there's no real sun to manage."""
        return bool(self.data.lux or self.data.irradiance or not self.data.is_sunny)

    @property
    def is_winter_insulation(self) -> bool:
        """Whether it's winter and the user opted to close for heat retention."""
        return bool(self.data.is_winter and self.data.winter_close_insulation)

    @property
    def is_tilt_mode2(self) -> bool:
        """Whether the tilt cover runs in MODE2 (slat opens toward the sun)."""
        return bool(TiltPolicy.is_mode2(self.cover.mode))


# ---------------------------------------------------------------------------
# Position functions (what each matched rule returns)
# ---------------------------------------------------------------------------


def _default(ctx: ClimateContext) -> int:
    return ctx.default_position


def _closed(ctx: ClimateContext) -> int:  # noqa: ARG001
    # 0 is correct for both blinds (lowered) and awnings (retracted).
    return POSITION_CLOSED


def _solar(ctx: ClimateContext) -> int:
    return ctx.solar_position()


def _defer(ctx: ClimateContext) -> None:  # noqa: ARG001
    # Normal GLARE_CONTROL: pipeline falls through to GlareZone/Solar.
    return None


def _intent_sun_through(ctx: ClimateContext) -> int:
    return ctx.data.policy.position_for_intent(sun_through=True)


def _intent_block_sun(ctx: ClimateContext) -> int:
    return ctx.data.policy.position_for_intent(sun_through=False)


def _tilt_summer(ctx: ClimateContext) -> int:
    return TiltPolicy.climate_tilt_percentage(
        angle_deg=CLIMATE_SUMMER_TILT_ANGLE,
        mode=ctx.cover.mode,
        gamma_deg=ctx.gamma_deg,
    )


def _tilt_default(ctx: ClimateContext) -> int:
    return TiltPolicy.climate_tilt_percentage(
        angle_deg=CLIMATE_DEFAULT_TILT_ANGLE,
        mode=ctx.cover.mode,
        gamma_deg=ctx.gamma_deg,
    )


def _tilt_winter_mode2(ctx: ClimateContext) -> int:
    # MODE2 winter heating opens the slat toward the sun; passing gamma_deg=0.0
    # preserves the historical positive-hemisphere answer.
    return TiltPolicy.climate_tilt_percentage(
        angle_deg=ctx.beta_deg,
        mode=ctx.cover.mode,
        gamma_deg=0.0,
        sun_through=True,
    )


# ---------------------------------------------------------------------------
# Rule + evaluator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClimateRule:
    """One climate branch: when ``predicate`` holds, claim ``strategy`` + ``position``."""

    predicate: Callable[[ClimateContext], bool]
    strategy: ClimateStrategy
    position: Callable[[ClimateContext], int | None]


def evaluate_rules(
    rules: tuple[ClimateRule, ...], ctx: ClimateContext
) -> tuple[ClimateStrategy, int | None]:
    """Return (strategy, position) for the first matching rule.

    Every table ends with an always-true catch-all, so a match is guaranteed.
    """
    for rule in rules:
        if rule.predicate(ctx):
            return rule.strategy, rule.position(ctx)
    raise RuntimeError("climate rule table exhausted without a catch-all")


_ALWAYS: Callable[[ClimateContext], bool] = lambda _ctx: True  # noqa: E731

# ---------------------------------------------------------------------------
# The four rule tables — branch order matches the original routers verbatim
# ---------------------------------------------------------------------------

# normal_with_presence: winter-heating → winter-insulation → low-light →
# summer-cooling → glare(defer/None).
NORMAL_WITH_PRESENCE: tuple[ClimateRule, ...] = (
    ClimateRule(
        lambda c: c.is_winter and c.cover_valid,
        ClimateStrategy.WINTER_HEATING,
        _intent_sun_through,
    ),
    ClimateRule(
        lambda c: c.is_winter_insulation,
        ClimateStrategy.WINTER_INSULATION,
        _closed,
    ),
    ClimateRule(
        lambda c: c.is_low_light,
        ClimateStrategy.LOW_LIGHT,
        _default,
    ),
    ClimateRule(
        lambda c: c.is_summer and c.data.transparent_blind and c.cover_valid,
        ClimateStrategy.SUMMER_COOLING,
        _intent_block_sun,
    ),
    ClimateRule(_ALWAYS, ClimateStrategy.GLARE_CONTROL, _defer),
)

# normal_without_presence: inside cover.valid → low-light → summer → winter;
# then winter-insulation; else low-light(default). Each valid-block rule carries
# the cover_valid guard so the flat order matches the nested original.
NORMAL_WITHOUT_PRESENCE: tuple[ClimateRule, ...] = (
    ClimateRule(
        lambda c: c.cover_valid and c.is_low_light,
        ClimateStrategy.LOW_LIGHT,
        _default,
    ),
    ClimateRule(
        lambda c: c.cover_valid and c.is_summer,
        ClimateStrategy.SUMMER_COOLING,
        _intent_block_sun,
    ),
    ClimateRule(
        lambda c: c.cover_valid and c.is_winter,
        ClimateStrategy.WINTER_HEATING,
        _intent_sun_through,
    ),
    ClimateRule(
        lambda c: c.is_winter_insulation,
        ClimateStrategy.WINTER_INSULATION,
        _closed,
    ),
    ClimateRule(_ALWAYS, ClimateStrategy.LOW_LIGHT, _default),
)

# tilt_with_presence: inside cover.valid (and only when NOT both-seasons, the
# original's defensive `if is_summer and is_winter: pass`) → winter → low-light →
# summer; then winter-insulation; else glare(tilt default). Seasons are mutually
# exclusive in practice; the not-both guards preserve the misconfig fall-through.
TILT_WITH_PRESENCE: tuple[ClimateRule, ...] = (
    ClimateRule(
        lambda c: c.cover_valid and c.is_winter and not c.is_summer,
        ClimateStrategy.WINTER_HEATING,
        _solar,
    ),
    ClimateRule(
        lambda c: c.cover_valid
        and not (c.is_summer and c.is_winter)
        and c.is_low_light,
        ClimateStrategy.LOW_LIGHT,
        _solar,
    ),
    ClimateRule(
        lambda c: c.cover_valid and c.is_summer and not c.is_winter,
        ClimateStrategy.SUMMER_COOLING,
        _tilt_summer,
    ),
    ClimateRule(
        lambda c: c.is_winter_insulation,
        ClimateStrategy.WINTER_INSULATION,
        _closed,
    ),
    ClimateRule(_ALWAYS, ClimateStrategy.GLARE_CONTROL, _tilt_default),
)

# tilt_without_presence: inside cover.valid → low-light → summer(closed) →
# winter+mode2 → glare(tilt default, the valid-block catch-all); then
# winter-insulation; else glare(solar).
TILT_WITHOUT_PRESENCE: tuple[ClimateRule, ...] = (
    ClimateRule(
        lambda c: c.cover_valid and c.is_low_light,
        ClimateStrategy.LOW_LIGHT,
        _solar,
    ),
    ClimateRule(
        lambda c: c.cover_valid and c.is_summer,
        ClimateStrategy.SUMMER_COOLING,
        _closed,
    ),
    ClimateRule(
        lambda c: c.cover_valid and c.is_winter and c.is_tilt_mode2,
        ClimateStrategy.WINTER_HEATING,
        _tilt_winter_mode2,
    ),
    ClimateRule(
        lambda c: c.cover_valid,
        ClimateStrategy.GLARE_CONTROL,
        _tilt_default,
    ),
    ClimateRule(
        lambda c: c.is_winter_insulation,
        ClimateStrategy.WINTER_INSULATION,
        _closed,
    ),
    ClimateRule(_ALWAYS, ClimateStrategy.GLARE_CONTROL, _solar),
)
