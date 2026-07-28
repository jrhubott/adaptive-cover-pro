"""Unit tests for the season-scope glare-tracking control (``tracking_seasons``).

``tracking_seasons`` is the set of seasons in which glare tracking may run.
Glare tracking (the GLARE_CONTROL fall-through in each rule table) is gated by a
single predicate, ``ClimateContext.is_tracking_season_blocked``, consulted by all
four climate routers. In any deselected season the glare branch is replaced by
the cover's default position; the dedicated winter (heating/insulation) and
summer (cooling) strategies are evaluated first and run regardless of the set.

Coverage:
  * the season predicate / current_season derivation
  * NORMAL_WITH_PRESENCE — the four original spec cases, generalised
  * TILT_WITH_PRESENCE and TILT_WITHOUT_PRESENCE — the gate now fires here too
    (the bug this change closes), including the cover_valid guard
  * NORMAL_WITHOUT_PRESENCE — still a no-op (never reaches glare tracking)
  * the all-seasons default leaves every table unchanged
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_TRACKING_SEASONS,
    POSITION_CLOSED,
    ClimateStrategy,
    TrackingSeason,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate_modes import (
    NORMAL_WITH_PRESENCE,
    NORMAL_WITHOUT_PRESENCE,
    TILT_WITH_PRESENCE,
    TILT_WITHOUT_PRESENCE,
    ClimateContext,
    evaluate_rules,
)

# Position-fn outputs for the default (mode=None → MODE1) tilt context used below.
# _tilt_default uses CLIMATE_DEFAULT_TILT_ANGLE (80°): round(80 / 90 * 100) == 89.
TILT_DEFAULT_PCT = 89
ALL_SEASONS = frozenset(DEFAULT_TRACKING_SEASONS)
SUMMER_ONLY = frozenset({TrackingSeason.SUMMER.value})


def _ctx(
    *,
    valid: bool = True,
    is_winter: bool = False,
    is_summer: bool = False,
    lux: bool = False,
    irradiance: bool = False,
    is_sunny: bool = True,
    winter_close_insulation: bool = False,
    transparent_blind: bool = False,
    default_position: int = 50,
    tracking_seasons: frozenset[str] | None = None,
    mode=None,
    gamma_deg: float = 0.0,
) -> ClimateContext:
    """Build a ClimateContext usable by every rule table.

    ``tracking_seasons=None`` selects the all-seasons default (unchanged
    behaviour). ``mode``/``gamma_deg`` feed the tilt position functions.
    """
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
        # Extreme-heat mode is off in these tests; the shared _TOP_OVERRIDES
        # band prepended to every table reads this field (issue #766).
        is_extreme_heat=False,
        policy=policy,
    )
    cover = SimpleNamespace(valid=valid, mode=mode)
    return ClimateContext(
        data=data,
        cover=cover,
        default_position=default_position,
        solar_position=lambda: "solar",
        gamma_deg=gamma_deg,
        tracking_seasons=ALL_SEASONS if tracking_seasons is None else tracking_seasons,
    )


# ---------------------------------------------------------------------------
# current_season + is_tracking_season_blocked predicate
# ---------------------------------------------------------------------------


def test_current_season_summer_takes_precedence():
    # Mutually exclusive in practice, but summer wins if both were ever set.
    assert _ctx(is_summer=True, is_winter=True).current_season is TrackingSeason.SUMMER


def test_current_season_winter():
    assert _ctx(is_winter=True).current_season is TrackingSeason.WINTER


def test_current_season_intermediate():
    assert _ctx().current_season is TrackingSeason.INTERMEDIATE


def test_default_all_seasons_never_blocks():
    for is_summer, is_winter in ((True, False), (False, True), (False, False)):
        ctx = _ctx(is_summer=is_summer, is_winter=is_winter)  # tracking_seasons=all
        assert ctx.is_tracking_season_blocked is False


def test_summer_only_blocks_winter_and_intermediate_not_summer():
    assert (
        _ctx(tracking_seasons=SUMMER_ONLY, is_summer=True).is_tracking_season_blocked
        is False
    )
    assert (
        _ctx(tracking_seasons=SUMMER_ONLY, is_winter=True).is_tracking_season_blocked
        is True
    )
    assert _ctx(tracking_seasons=SUMMER_ONLY).is_tracking_season_blocked is True


def test_exclude_intermediate_only():
    seasons = frozenset({TrackingSeason.WINTER.value, TrackingSeason.SUMMER.value})
    assert (
        _ctx(tracking_seasons=seasons).is_tracking_season_blocked is True
    )  # intermediate
    assert (
        _ctx(tracking_seasons=seasons, is_summer=True).is_tracking_season_blocked
        is False
    )
    assert (
        _ctx(tracking_seasons=seasons, is_winter=True).is_tracking_season_blocked
        is False
    )


def test_empty_set_blocks_every_season():
    empty = frozenset()
    for is_summer, is_winter in ((True, False), (False, True), (False, False)):
        ctx = _ctx(tracking_seasons=empty, is_summer=is_summer, is_winter=is_winter)
        assert ctx.is_tracking_season_blocked is True


# ---------------------------------------------------------------------------
# NORMAL_WITH_PRESENCE
# ---------------------------------------------------------------------------


def test_normal_default_intermediate_sunny_defers():
    """All seasons (default): intermediate + sunny + presence → defer to glare."""
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE, _ctx(is_sunny=True, lux=False)
    )
    assert strategy == ClimateStrategy.GLARE_CONTROL
    assert position is None


def test_normal_summer_only_intermediate_sunny_gates_to_default():
    """summer-only: intermediate + sunny → gate → default position."""
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY, is_sunny=True, lux=False, default_position=42
        ),
    )
    assert strategy == ClimateStrategy.TRACKING_SEASON_GATE
    assert position == 42


def test_normal_summer_only_intermediate_lowlight_labels_low_light():
    """summer-only: intermediate low-light → LOW_LIGHT (honest) before the gate.

    Both return the default position; the gate only governs the would-be glare
    branch, so genuine low light is reported as LOW_LIGHT rather than the gate.
    """
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(tracking_seasons=SUMMER_ONLY, lux=True, default_position=30),
    )
    assert strategy == ClimateStrategy.LOW_LIGHT
    assert position == 30


def test_normal_summer_only_summer_defers_to_glare():
    """summer-only: summer + sunny + non-transparent → defer (glare tracking runs)."""
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(tracking_seasons=SUMMER_ONLY, is_summer=True, transparent_blind=False),
    )
    assert strategy == ClimateStrategy.GLARE_CONTROL
    assert position is None


def test_normal_summer_only_summer_transparent_blind_cools():
    strategy, _ = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(tracking_seasons=SUMMER_ONLY, is_summer=True, transparent_blind=True),
    )
    assert strategy == ClimateStrategy.SUMMER_COOLING


def test_normal_summer_only_winter_still_opens():
    """Winter climate action is unaffected by the season scope."""
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(tracking_seasons=SUMMER_ONLY, is_winter=True, valid=True),
    )
    assert strategy == ClimateStrategy.WINTER_HEATING
    assert position == "intent_open"


def test_normal_summer_only_winter_insulation_closes():
    strategy, position = evaluate_rules(
        NORMAL_WITH_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY,
            is_winter=True,
            winter_close_insulation=True,
            valid=False,
        ),
    )
    assert strategy == ClimateStrategy.WINTER_INSULATION
    assert position == POSITION_CLOSED


# ---------------------------------------------------------------------------
# NORMAL_WITHOUT_PRESENCE — still a no-op (no glare-tracking fall-through)
# ---------------------------------------------------------------------------


def test_normal_without_presence_summer_only_intermediate_unchanged():
    """No gate here: intermediate falls through to the LOW_LIGHT default as before."""
    strategy, position = evaluate_rules(
        NORMAL_WITHOUT_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY, is_sunny=True, lux=False, default_position=37
        ),
    )
    assert strategy == ClimateStrategy.LOW_LIGHT
    assert position == 37


# ---------------------------------------------------------------------------
# TILT_WITH_PRESENCE — gate now fires here (previously inert)
# ---------------------------------------------------------------------------


def test_tilt_with_presence_default_intermediate_tracks():
    """All seasons: intermediate glare → the sun-aware default tilt (unchanged)."""
    strategy, position = evaluate_rules(
        TILT_WITH_PRESENCE, _ctx(is_sunny=True, lux=False)
    )
    assert strategy == ClimateStrategy.GLARE_CONTROL
    assert position == TILT_DEFAULT_PCT


def test_tilt_with_presence_summer_only_intermediate_gates_to_default():
    """summer-only: intermediate glare → gate → cover default, NOT the tilt default.

    This is the behaviour change: before, the tilt table never consulted the
    season scope and a tilt user got no effect from the option.
    """
    strategy, position = evaluate_rules(
        TILT_WITH_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY, is_sunny=True, lux=False, default_position=20
        ),
    )
    assert strategy == ClimateStrategy.TRACKING_SEASON_GATE
    assert position == 20
    assert position != TILT_DEFAULT_PCT


def test_tilt_with_presence_summer_only_winter_insulation_before_gate():
    """Winter insulation still wins over the gate (ordered before it)."""
    strategy, position = evaluate_rules(
        TILT_WITH_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY,
            is_winter=True,
            winter_close_insulation=True,
            valid=False,
        ),
    )
    assert strategy == ClimateStrategy.WINTER_INSULATION
    assert position == POSITION_CLOSED


# ---------------------------------------------------------------------------
# TILT_WITHOUT_PRESENCE — gate now fires here, with the cover_valid guard
# ---------------------------------------------------------------------------


def test_tilt_without_presence_default_intermediate_tracks():
    strategy, position = evaluate_rules(
        TILT_WITHOUT_PRESENCE, _ctx(is_sunny=True, lux=False)
    )
    assert strategy == ClimateStrategy.GLARE_CONTROL
    assert position == TILT_DEFAULT_PCT


def test_tilt_without_presence_summer_only_valid_intermediate_gates():
    strategy, position = evaluate_rules(
        TILT_WITHOUT_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY, is_sunny=True, lux=False, default_position=15
        ),
    )
    assert strategy == ClimateStrategy.TRACKING_SEASON_GATE
    assert position == 15
    assert position != TILT_DEFAULT_PCT


def test_tilt_without_presence_guard_preserves_invalid_insulation():
    """Gate's cover_valid guard must not pre-empt winter-insulation for an
    invalid cover (insulation is ordered after the glare branch here).
    """
    strategy, position = evaluate_rules(
        TILT_WITHOUT_PRESENCE,
        _ctx(
            tracking_seasons=SUMMER_ONLY,
            is_winter=True,
            winter_close_insulation=True,
            valid=False,
        ),
    )
    assert strategy == ClimateStrategy.WINTER_INSULATION
    assert position == POSITION_CLOSED


def test_tilt_without_presence_invalid_intermediate_falls_to_solar_not_gate():
    """Invalid intermediate cover skips the guarded gate → solar catch-all."""
    strategy, position = evaluate_rules(
        TILT_WITHOUT_PRESENCE,
        _ctx(tracking_seasons=SUMMER_ONLY, valid=False, is_sunny=True, lux=False),
    )
    assert strategy == ClimateStrategy.GLARE_CONTROL
    assert position == "solar"
