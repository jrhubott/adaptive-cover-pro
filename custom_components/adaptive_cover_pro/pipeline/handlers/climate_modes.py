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
    DEFAULT_EXTREME_HEAT_POSITION,
    DEFAULT_TRACKING_SEASONS,
    POSITION_CLOSED,
    ClimateStrategy,
    TrackingSeason,
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
    ``tracking_seasons`` mirrors the per-instance season-scope option: the set
    of seasons in which glare tracking is permitted (defaults to all).
    """

    data: Any
    cover: Any
    default_position: int
    solar_position: Callable[[], int]
    gamma_deg: float = 0.0
    beta_deg: float = 0.0
    tracking_seasons: frozenset[str] = frozenset(DEFAULT_TRACKING_SEASONS)

    # --- shared season predicates (single source of truth) ---
    @property
    def cover_valid(self) -> bool:
        """Whether the cover's geometry/sun calc is currently valid."""
        return bool(self.cover.valid)

    @property
    def current_season(self) -> TrackingSeason:
        """The active season for the season-scope gate.

        Summer takes precedence over winter (mirrors ``ClimateHandler.evaluate``);
        in practice the two are mutually exclusive.  Anything that is neither is
        ``INTERMEDIATE``.
        """
        if self.is_summer:
            return TrackingSeason.SUMMER
        if self.is_winter:
            return TrackingSeason.WINTER
        return TrackingSeason.INTERMEDIATE

    @property
    def is_tracking_season_blocked(self) -> bool:
        """True when glare tracking is not permitted in the current season.

        This is the single season-scope predicate consulted by every rule
        table.  When it fires, the glare fall-through is replaced by the cover's
        default position.  It only gates the glare-tracking branch — the
        dedicated winter (heating/insulation) and summer (cooling) strategies
        are evaluated first and run regardless of the selected seasons.
        """
        return self.current_season.value not in self.tracking_seasons

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

    @property
    def is_extreme_heat(self) -> bool:
        """Whether the outside temperature has crossed the extreme-heat threshold."""
        return bool(self.data.is_extreme_heat)


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


def _extreme_heat(ctx: ClimateContext) -> int:
    # The configured hold, or fully closed when unset. ``is not None`` keeps an
    # explicit 0 % (fully closed) distinct from "unset" (issue #766). A raw int
    # flows through apply_snapshot_limits + inverse-state exactly like _closed —
    # no cover-type branching here; TiltPolicy interprets it as a slat angle.
    pos = ctx.data.extreme_heat_position
    return pos if pos is not None else DEFAULT_EXTREME_HEAT_POSITION


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

# Season-scope gate: when the current season is not in the permitted set the
# glare fall-through is replaced by the default position. Inserted immediately
# before each table's glare-tracking branch so the dedicated winter/summer
# climate strategies (evaluated earlier) always win regardless of the selected
# seasons. NORMAL_WITHOUT_PRESENCE never reaches glare tracking, so it carries
# no gate (see its comment). The predicate lives on ClimateContext so all
# tables share one source of truth.
_SEASON_GATE = ClimateRule(
    lambda c: c.is_tracking_season_blocked,
    ClimateStrategy.TRACKING_SEASON_GATE,
    _default,
)
# TILT_WITHOUT_PRESENCE places winter-insulation and an invalid-cover solar
# fall-through *after* its glare branch, so its gate must keep the cover_valid
# guard to avoid pre-empting them when the cover geometry is invalid.
_SEASON_GATE_VALID = ClimateRule(
    lambda c: c.cover_valid and c.is_tracking_season_blocked,
    ClimateStrategy.TRACKING_SEASON_GATE,
    _default,
)

# ---------------------------------------------------------------------------
# Shared, table-agnostic override band (issue #766)
# ---------------------------------------------------------------------------
# ``_TOP_OVERRIDES`` is prepended verbatim to the TOP of every rule table, so a
# forced hold declared here pre-empts EVERY season strategy (including winter
# heating) uniformly across all four routers. It is a plain
# ``tuple[ClimateRule, ...]`` built from the same ``ClimateRule`` seam the
# tables use — no new representation. When the extreme-heat predicate is False
# (feature off / threshold not crossed) the member never matches and table
# outcomes are byte-for-byte identical to the pre-#766 behavior.
#
# Future consumers (e.g. PR #548's season gate) add their own entry — the band
# is the shared, table-agnostic insertion point.
_TOP_OVERRIDES: tuple[ClimateRule, ...] = (
    ClimateRule(
        lambda c: c.is_extreme_heat,
        ClimateStrategy.EXTREME_HEAT,
        _extreme_heat,
    ),
)

# ---------------------------------------------------------------------------
# The four rule tables — branch order matches the original routers verbatim
# ---------------------------------------------------------------------------

# normal_with_presence: winter-heating → winter-insulation → low-light →
# summer-cooling → season-gate → glare(defer/None). The gate sits just before
# the glare fall-through: low-light / summer-cooling are honest climate
# strategies that should label as themselves, so only the would-be glare
# tracking is gated by season.
NORMAL_WITH_PRESENCE: tuple[ClimateRule, ...] = (
    *_TOP_OVERRIDES,
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
    _SEASON_GATE,
    ClimateRule(_ALWAYS, ClimateStrategy.GLARE_CONTROL, _defer),
)

# normal_without_presence: inside cover.valid → low-light → summer → winter;
# then winter-insulation; else low-light(default). Each valid-block rule carries
# the cover_valid guard so the flat order matches the nested original.
# No season gate: this table never defers to glare tracking — its catch-all
# already returns the default position — so there is nothing for the gate to
# suppress (it would only relabel an honest LOW_LIGHT default).
NORMAL_WITHOUT_PRESENCE: tuple[ClimateRule, ...] = (
    *_TOP_OVERRIDES,
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
# summer; then winter-insulation; season-gate; else glare(tilt default). Seasons
# are mutually exclusive in practice; the not-both guards preserve the misconfig
# fall-through. The gate sits just before the glare(tilt default) catch-all so a
# deselected season returns the cover's default position instead of the
# sun-aware default tilt angle.
TILT_WITH_PRESENCE: tuple[ClimateRule, ...] = (
    *_TOP_OVERRIDES,
    ClimateRule(
        lambda c: c.cover_valid and c.is_winter and not c.is_summer,
        ClimateStrategy.WINTER_HEATING,
        _solar,
    ),
    ClimateRule(
        lambda c: (
            c.cover_valid and not (c.is_summer and c.is_winter) and c.is_low_light
        ),
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
    _SEASON_GATE,
    ClimateRule(_ALWAYS, ClimateStrategy.GLARE_CONTROL, _tilt_default),
)

# tilt_without_presence: inside cover.valid → low-light → summer(closed) →
# winter+mode2 → season-gate(valid) → glare(tilt default, the valid-block
# catch-all); then winter-insulation; else glare(solar). The gate carries the
# cover_valid guard (the _SEASON_GATE_VALID variant) because winter-insulation
# and the invalid-cover solar fall-through sit *after* the glare branch — an
# unguarded gate would pre-empt them for invalid covers.
TILT_WITHOUT_PRESENCE: tuple[ClimateRule, ...] = (
    *_TOP_OVERRIDES,
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
    _SEASON_GATE_VALID,
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
