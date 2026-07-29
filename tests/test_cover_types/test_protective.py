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


def _mode2_tilt_cover() -> AdaptiveTiltCover:
    """Build a MODE2 slat engine — pivot at the horizontal 50 % (issue #1104)."""
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=180, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(slat_distance=0.02, depth=0.03, mode="mode2"),
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
