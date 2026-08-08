"""Tests for ``CoverTypePolicy.more_protective_position``.

The polymorphic comparator the anticipation helper (issue #616) folds future
sun-tracked samples through. "More protective" = "blocks more direct sun", which
is cover-type dependent:

  - blind / tilt / venetian (``open_blocks_sun=False``) → lower % = more coverage
  - awning (``open_blocks_sun=True``)                    → higher % = more coverage

The direction lives entirely on ``axes[0].open_blocks_sun`` so no cover-type
string branch or hardcoded min/max leaks outside ``cover_types/``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover
from tests.cover_helpers import make_cover_config, make_tilt_config

# Cover types whose primary axis closes (lower %) to block the sun.
_LOWER_IS_PROTECTIVE = ["cover_blind", "cover_tilt", "cover_venetian"]


def _mode2_tilt_cover(**tilt_overrides) -> AdaptiveTiltCover:
    """Build a MODE2 slat engine — pivot at the horizontal 50 % (issue #1104)."""
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=180, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(
            slat_distance=0.02,
            depth=0.03,
            **{"mode": "mode2", **tilt_overrides},
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("cover_type", _LOWER_IS_PROTECTIVE)
def test_lower_percentage_is_more_protective(cover_type: str) -> None:
    policy = get_policy(cover_type)
    assert policy.more_protective_position(30, 70) == 30
    # Argument order must not matter.
    assert policy.more_protective_position(70, 30) == 30


@pytest.mark.unit
def test_higher_percentage_is_more_protective_for_awning() -> None:
    policy = get_policy("cover_awning")
    assert policy.more_protective_position(30, 70) == 70
    assert policy.more_protective_position(70, 30) == 70


@pytest.mark.unit
@pytest.mark.parametrize(
    "cover_type", ["cover_blind", "cover_awning", "cover_tilt", "cover_venetian"]
)
def test_equal_values_are_idempotent(cover_type: str) -> None:
    assert get_policy(cover_type).more_protective_position(50, 50) == 50


# ---------------------------------------------------------------------------
# Bi-directional axis (issue #1104) — "more protective" is measured as distance
# from the engine's coverage pivot, not as a min/max on the raw percentage.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_same_side_above_pivot_picks_farther_from_horizontal() -> None:
    """The discriminating case the symmetric ``(30, 70)`` pair cannot reach.

    On MODE2, 55 % is 99° (9° off horizontal) and 60 % is 108° (18° off), so the
    bigger percentage is the more closed slat. ``min`` answers 55.
    """
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover()
    assert policy.more_protective_position(55, 60, cover=cover) == 60
    assert policy.more_protective_position(60, 55, cover=cover) == 60


@pytest.mark.unit
def test_same_side_below_pivot_picks_farther_from_horizontal() -> None:
    """Below the pivot the two rules agree — the fix must not disturb that."""
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover()
    assert policy.more_protective_position(45, 40, cover=cover) == 40
    assert policy.more_protective_position(40, 45, cover=cover) == 40


@pytest.mark.unit
def test_symmetric_straddle_tie_breaks_to_axis_rule() -> None:
    """Equally protective in real terms (both 36° off), so the axis rule decides."""
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover()
    assert policy.more_protective_position(30, 70, cover=cover) == 30
    assert policy.more_protective_position(70, 30, cover=cover) == 30


@pytest.mark.unit
def test_symmetric_straddle_really_reaches_the_axis_rule() -> None:
    """Non-vacuity companion to the guard above (#1222 audit).

    That guard names the axis rule but cannot see whether it ran. 30 % and 70 %
    are both 36° off horizontal, so the distance metric is supposed to abstain —
    but it is computed as ``|30/100·180 − 90|`` against ``|70/100·180 − 90|``,
    and the second overshoots 36° by a few ulps. Under an exact ``!=`` the
    metric therefore answers first, and answers 30 for no better reason than
    which way the rounding fell.

    Swapping the axis direction on the SAME engine separates the two branches:
    the tilt and awning policies share this comparator and differ only in the
    axis rule, so a distance metric that decided would hand both the same
    percentage.
    """
    cover = _mode2_tilt_cover()
    assert cover.coverage_distance(30) == pytest.approx(cover.coverage_distance(70))
    assert get_policy("cover_tilt").more_protective_position(30, 70, cover=cover) == 30
    assert (
        get_policy("cover_awning").more_protective_position(30, 70, cover=cover) == 70
    )


@pytest.mark.unit
@pytest.mark.parametrize("low", range(50))
def test_symmetric_mode2_pairs_all_fall_through_to_the_axis_rule(low: int) -> None:
    """Every ``pct`` / ``100 − pct`` pair on MODE2 is a tie (#1222 audit).

    The shipped-default scale is symmetric about horizontal, so the two members
    of each of these 50 pairs are the same number of degrees off it and neither
    blocks more sun. Angle-space distance computes them through different
    arithmetic, though — ``44/100·180`` lands 1.4e-14 below 10.8° while
    ``56/100·180`` lands 1.4e-14 above — so an exact inequality test reads
    floating-point noise as a real difference. 13 of these pairs used to flip
    that way, all of them from the axis rule's answer to its opposite, the worst
    an 82-point swing between two equally protective slats (9 ↔ 91).

    Both axis directions are asserted for the same reason as the case above:
    only the axis rule can distinguish them, so agreement here is proof the tie
    fell through instead of being decided.
    """
    high = 100 - low
    cover = _mode2_tilt_cover()
    tilt, awning = get_policy("cover_tilt"), get_policy("cover_awning")
    assert tilt.more_protective_position(low, high, cover=cover) == low
    assert tilt.more_protective_position(high, low, cover=cover) == low
    assert awning.more_protective_position(low, high, cover=cover) == high
    assert awning.more_protective_position(high, low, cover=cover) == high


@pytest.mark.unit
def test_no_cover_falls_back_to_monotonic_rule() -> None:
    """Callers with no engine in scope (the glare-zone handler) are unchanged."""
    assert get_policy("cover_tilt").more_protective_position(55, 60) == 55


@pytest.mark.unit
def test_pivotless_engine_falls_back() -> None:
    """A monotonic engine reports no pivot, so the axis rule stands for both types."""
    pivotless = MagicMock()
    pivotless.coverage_pivot_percentage.return_value = None
    assert (
        get_policy("cover_tilt").more_protective_position(55, 60, cover=pivotless) == 55
    )
    assert (
        get_policy("cover_awning").more_protective_position(55, 60, cover=pivotless)
        == 60
    )


@pytest.mark.unit
def test_proportional_band_is_ranked_in_command_space() -> None:
    """The #957 band transform does not move the pivot (#1104 audit).

    ``tilt_transform=proportional`` remaps the solar DEMAND into
    ``[min_tilt, max_tilt]``; what comes out is the percentage handed to the
    cover entity, whose own scale is unchanged. So on MODE2 the horizontal slat
    is still at 50 % of the COMMAND, and the two candidates here are 27 % = 48.6°
    (41.4° off horizontal) and 30 % = 54° (36.0° off) — 27 is the more closed
    slat, and the comparator must not be handed a band-remapped pivot that would
    reverse that.
    """
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover(min_tilt=0, max_tilt=50, tilt_transform="proportional")
    assert cover.coverage_pivot_percentage() == 50.0
    assert policy.more_protective_position(27, 30, cover=cover) == 27
    assert policy.more_protective_position(30, 27, cover=cover) == 27


@pytest.mark.unit
@pytest.mark.parametrize(
    ("angle_0", "angle_100", "pivot", "a", "b", "expected"),
    [
        # 0° → 0 %, 45° → 100 %: horizontal is an unreachable 200 %. Every
        # sample is below it, so distance-from-pivot and the axis ``min`` agree
        # — 60 % is 27° (63° off horizontal), 80 % is 36° (54° off).
        (0.0, 45.0, 200.0, 60, 80, 60),
        # 120° → 0 %, 180° → 100 %: horizontal is an unreachable −50 %, and now
        # the two rules DISAGREE. 20 % is 132° (42° off), 40 % is 144° (54° off),
        # so the axis ``min`` would command the more open slat.
        (120.0, 180.0, -50.0, 20, 40, 40),
    ],
)
def test_off_travel_pivot_still_orders_the_comparator(
    angle_0, angle_100, pivot, a, b, expected
) -> None:
    """An unreachable pivot is still a valid ordering reference (#1104 audit).

    The percentage↔angle map is affine, so ``|pct − pivot|`` is proportional to
    ``|angle − 90°|`` whether or not the pivot itself lies on the travel. Only
    the coverage-step quantiser — which anchors levels ON the pivot and spans
    them to the end of the travel — needs the pivot to be reachable, which is
    why it pulls the anchor onto the travel itself instead of the shared engine
    hook doing it for everyone. Both consumers still name the same covering end:
    see ``test_conservative_rounding.py`` ::
    ``test_the_quantiser_and_the_comparator_agree_on_the_covering_end``.
    """
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover(
        mode="specify_angles", angle_0=angle_0, angle_100=angle_100
    )
    assert cover.coverage_pivot_percentage() == pytest.approx(pivot)
    assert policy.more_protective_position(a, b, cover=cover) == expected
    assert policy.more_protective_position(b, a, cover=cover) == expected


@pytest.mark.unit
def test_cross_pivot_ranking_under_asymmetric_midpoint() -> None:
    """A three-point calibration breaks percentage-distance ranking (#1222).

    The reporting KNX venetian runs 0°→0 %, 90°→50 %, 130°→100 %: 1.8 °/% below
    horizontal against 0.8 °/% above it. Percentage distance from the pivot is
    proportional to distance from maximum openness only while the two sides
    share a scale, and here they do not.

    40 % is 72° — 18° of closure — while 65 % is 102°, a mere 12°. The
    percentage metric reads that backwards (15 points from the pivot beats 10)
    and would command the slat that lets MORE direct sun through, on exactly the
    cross-pivot pair the anticipation helper produces when the solve crosses
    horizontal inside a throttle interval.
    """
    policy = get_policy("cover_tilt")
    cover = _mode2_tilt_cover(
        mode="specify_angles", angle_0=0.0, angle_100=130.0, horizontal_percent=50.0
    )
    assert cover.coverage_pivot_percentage() == pytest.approx(50.0)
    # The physics the ranking has to respect.
    assert cover._angle_from_percentage(40) == pytest.approx(72.0)
    assert cover._angle_from_percentage(65) == pytest.approx(102.0)

    assert policy.more_protective_position(40, 65, cover=cover) == 40
    assert policy.more_protective_position(65, 40, cover=cover) == 40
