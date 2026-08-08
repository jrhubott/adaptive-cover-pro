"""Unit tests for the climate-mode rule tables and shared predicates."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ClimateStrategy
from custom_components.adaptive_cover_pro.pipeline.handlers.climate_modes import (
    NORMAL_WITH_PRESENCE,
    NORMAL_WITHOUT_PRESENCE,
    TILT_WITH_PRESENCE,
    TILT_WITHOUT_PRESENCE,
    _TOP_OVERRIDES,
    ClimateContext,
    ClimateRule,
    _default,
    evaluate_rules,
    match_rule,
)

ALL_TABLES = [
    NORMAL_WITH_PRESENCE,
    NORMAL_WITHOUT_PRESENCE,
    TILT_WITH_PRESENCE,
    TILT_WITHOUT_PRESENCE,
]


def _ctx(
    *,
    valid=True,
    is_winter=False,
    is_summer=False,
    lux=False,
    irradiance=False,
    is_sunny=True,
    winter_close_insulation=False,
    transparent_blind=False,
    default_position=50,
    is_extreme_heat=False,
):
    """Build a ClimateContext over a normal (non-tilt) cover with a mock policy."""
    policy = MagicMock()
    policy.position_for_intent.side_effect = lambda sun_through: (
        "intent_open" if sun_through else "intent_block"
    )
    data = SimpleNamespace(
        is_winter=is_winter,
        is_summer=is_summer,
        lux=lux,
        irradiance=irradiance,
        is_sunny=is_sunny,
        winter_close_insulation=winter_close_insulation,
        transparent_blind=transparent_blind,
        policy=policy,
        is_extreme_heat=is_extreme_heat,
        extreme_heat_position=None,
    )
    cover = SimpleNamespace(valid=valid, mode=None)
    return ClimateContext(
        data=data,
        cover=cover,
        default_position=default_position,
        solar_position=lambda: "solar",
    )


# --- predicates ------------------------------------------------------------


def test_is_low_light_any_signal():
    assert _ctx(lux=True).is_low_light
    assert _ctx(irradiance=True).is_low_light
    assert _ctx(is_sunny=False).is_low_light
    assert not _ctx().is_low_light


def test_is_winter_insulation_requires_both():
    assert _ctx(is_winter=True, winter_close_insulation=True).is_winter_insulation
    assert not _ctx(is_winter=True, winter_close_insulation=False).is_winter_insulation
    assert not _ctx(is_winter=False, winter_close_insulation=True).is_winter_insulation


# --- evaluate_rules mechanics ---------------------------------------------


def test_evaluate_rules_first_match_wins():
    rules = (
        ClimateRule(lambda c: False, ClimateStrategy.WINTER_HEATING, lambda c: 1),
        ClimateRule(lambda c: True, ClimateStrategy.LOW_LIGHT, lambda c: 2),
        ClimateRule(lambda c: True, ClimateStrategy.GLARE_CONTROL, lambda c: 3),
    )
    assert evaluate_rules(rules, _ctx()) == (ClimateStrategy.LOW_LIGHT, 2)


def test_evaluate_rules_raises_without_catch_all():
    rules = (ClimateRule(lambda c: False, ClimateStrategy.LOW_LIGHT, lambda c: 1),)
    with pytest.raises(RuntimeError):
        evaluate_rules(rules, _ctx())


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_table_ends_with_catch_all(table):
    assert table[-1].predicate(_ctx()) is True


# --- position provenance: which branch resolved FROM the default (#1214) --


def test_match_rule_returns_the_first_matching_rule():
    """``match_rule`` is the primitive ``evaluate_rules`` adapts."""
    rules = (
        ClimateRule(lambda c: False, ClimateStrategy.WINTER_HEATING, _default),
        ClimateRule(lambda c: True, ClimateStrategy.LOW_LIGHT, lambda c: 2),
    )
    assert match_rule(rules, _ctx()).strategy is ClimateStrategy.LOW_LIGHT


def test_match_rule_raises_without_catch_all():
    rules = (ClimateRule(lambda c: False, ClimateStrategy.LOW_LIGHT, _default),)
    with pytest.raises(RuntimeError):
        match_rule(rules, _ctx())


def test_resolves_default_position_keys_on_the_position_fn_not_its_value():
    """Provenance, not value equality.

    A rule that merely *returns* the default position's number is not
    resolving from the default. Issue #1214 round 1 compared values and got
    it wrong in both directions — false negatives once a limit clamp moved
    the answer, false positives when a configured override happened to
    coincide with the default.
    """
    coincidence = ClimateRule(
        lambda c: True, ClimateStrategy.LOW_LIGHT, lambda c: c.default_position
    )
    assert coincidence.position(_ctx(default_position=42)) == 42
    assert coincidence.resolves_default_position is False

    assert (
        ClimateRule(
            lambda c: True, ClimateStrategy.LOW_LIGHT, _default
        ).resolves_default_position
        is True
    )


@pytest.mark.parametrize(
    ("table", "ctx", "strategy", "from_default"),
    [
        # NORMAL tables answer LOW_LIGHT with _default — these earn the tilt.
        (NORMAL_WITH_PRESENCE, _ctx(lux=True), ClimateStrategy.LOW_LIGHT, True),
        (
            NORMAL_WITHOUT_PRESENCE,
            _ctx(valid=True, lux=True),
            ClimateStrategy.LOW_LIGHT,
            True,
        ),
        (
            NORMAL_WITHOUT_PRESENCE,
            _ctx(valid=False),
            ClimateStrategy.LOW_LIGHT,
            True,
        ),
        # The season-scope gate answers with _default in every table that has one.
        (
            NORMAL_WITH_PRESENCE,
            replace(_ctx(), tracking_seasons=frozenset()),
            ClimateStrategy.TRACKING_SEASON_GATE,
            True,
        ),
        (
            TILT_WITH_PRESENCE,
            replace(_ctx(), tracking_seasons=frozenset()),
            ClimateStrategy.TRACKING_SEASON_GATE,
            True,
        ),
        (
            TILT_WITHOUT_PRESENCE,
            replace(_ctx(valid=True), tracking_seasons=frozenset()),
            ClimateStrategy.TRACKING_SEASON_GATE,
            True,
        ),
        # TILT tables answer LOW_LIGHT with _solar — same DEFAULT label, a
        # solar-tracked position, so no default tilt.
        (TILT_WITH_PRESENCE, _ctx(lux=True), ClimateStrategy.LOW_LIGHT, False),
        (
            TILT_WITHOUT_PRESENCE,
            _ctx(valid=True, lux=True),
            ClimateStrategy.LOW_LIGHT,
            False,
        ),
        # Every non-default branch stays False.
        (
            NORMAL_WITH_PRESENCE,
            _ctx(is_winter=True, valid=True),
            ClimateStrategy.WINTER_HEATING,
            False,
        ),
        (
            NORMAL_WITH_PRESENCE,
            _ctx(is_winter=True, valid=False, winter_close_insulation=True),
            ClimateStrategy.WINTER_INSULATION,
            False,
        ),
        (NORMAL_WITH_PRESENCE, _ctx(), ClimateStrategy.GLARE_CONTROL, False),
        (
            NORMAL_WITH_PRESENCE,
            _ctx(is_extreme_heat=True),
            ClimateStrategy.EXTREME_HEAT,
            False,
        ),
    ],
)
def test_table_branch_position_provenance(table, ctx, strategy, from_default):
    """Pin which branch of each table resolves from the configured default.

    ``ClimateHandler`` reads exactly this to decide whether a
    ``ControlMethod.DEFAULT`` winner has earned the effective default/sunset
    tilt (issue #1214).
    """
    rule = match_rule(table, ctx)
    assert rule.strategy is strategy
    assert rule.resolves_default_position is from_default


# --- shared top-override band (issue #766 seam) ---------------------------


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_table_prepends_top_overrides_band(table):
    """Each table is assembled as ``(*_TOP_OVERRIDES, <body>)``.

    The band is a single, table-agnostic tuple prepended verbatim to every
    router.  Pinning the prefix here means a future override added to the band
    lands uniformly across all four tables and can never silently drop from one.
    """
    assert table[: len(_TOP_OVERRIDES)] == _TOP_OVERRIDES


# --- NORMAL_WITH_PRESENCE rows --------------------------------------------


def test_nwp_winter_heating():
    s, p = evaluate_rules(NORMAL_WITH_PRESENCE, _ctx(is_winter=True, valid=True))
    assert s == ClimateStrategy.WINTER_HEATING
    assert p == "intent_open"


def test_nwp_winter_insulation_when_not_valid():
    s, p = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(is_winter=True, valid=False, winter_close_insulation=True),
    )
    assert s == ClimateStrategy.WINTER_INSULATION
    assert p == 0


def test_nwp_low_light_returns_default():
    s, p = evaluate_rules(NORMAL_WITH_PRESENCE, _ctx(lux=True, default_position=42))
    assert s == ClimateStrategy.LOW_LIGHT
    assert p == 42


def test_nwp_summer_cooling_requires_transparent_and_valid():
    s, p = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(is_summer=True, transparent_blind=True, valid=True),
    )
    assert s == ClimateStrategy.SUMMER_COOLING
    assert p == "intent_block"


def test_nwp_glare_defers_to_none():
    # sunny, not winter/summer, valid → falls through to glare-control / defer
    s, p = evaluate_rules(NORMAL_WITH_PRESENCE, _ctx())
    assert s == ClimateStrategy.GLARE_CONTROL
    assert p is None


# --- NORMAL_WITHOUT_PRESENCE rows -----------------------------------------


def test_nwop_low_light_first_inside_valid():
    s, p = evaluate_rules(
        NORMAL_WITHOUT_PRESENCE, _ctx(valid=True, lux=True, default_position=30)
    )
    assert s == ClimateStrategy.LOW_LIGHT
    assert p == 30


def test_nwop_summer_then_winter_order():
    s, _ = evaluate_rules(NORMAL_WITHOUT_PRESENCE, _ctx(valid=True, is_summer=True))
    assert s == ClimateStrategy.SUMMER_COOLING
    s, _ = evaluate_rules(NORMAL_WITHOUT_PRESENCE, _ctx(valid=True, is_winter=True))
    assert s == ClimateStrategy.WINTER_HEATING


def test_nwop_fallthrough_low_light_default():
    # not valid, not winter → final catch-all LOW_LIGHT/default
    s, p = evaluate_rules(
        NORMAL_WITHOUT_PRESENCE, _ctx(valid=False, default_position=55)
    )
    assert s == ClimateStrategy.LOW_LIGHT
    assert p == 55
