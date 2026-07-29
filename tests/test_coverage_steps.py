"""Tests for sun-tracking coverage-step quantization (movement minimization)."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.position_utils import PositionConverter

quantize = PositionConverter.quantize_to_coverage_steps


# ---------------------------------------------------------------------------
# full_coverage_at_zero=True (vertical blind / tilt / venetian — 0% = closed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        (100, 100),  # no coverage needed → stays fully open
        (99, 0),  # any coverage demand → full coverage
        (60, 0),
        (40, 0),
        (1, 0),
        (0, 0),  # already full coverage
    ],
)
def test_n1_blind_snaps_to_full_coverage(percentage, expected):
    assert quantize(percentage, 1, full_coverage_at_zero=True) == expected


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        (100, 100),  # open
        (70, 50),  # 0.30 coverage → rounds up to 0.50 level → 50%
        (50, 50),  # exactly on a level
        (40, 0),  # 0.60 coverage → rounds up to 1.0 → full
        (10, 0),
        (0, 0),
    ],
)
def test_n2_blind_levels(percentage, expected):
    assert quantize(percentage, 2, full_coverage_at_zero=True) == expected


def test_n3_blind_rounds_toward_coverage():
    # coverage 0.10 → ceil(0.30)/3 = 1/3 → position 67
    assert quantize(90, 3, full_coverage_at_zero=True) == 67
    # coverage 0.50 → ceil(1.5)/3 = 2/3 → position 33
    assert quantize(50, 3, full_coverage_at_zero=True) == 33


# ---------------------------------------------------------------------------
# full_coverage_at_zero=False (awning — 100% = fully extended = max blocking)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        (0, 0),  # retracted → no coverage
        (1, 100),  # any extension demand → full extension
        (60, 100),
        (100, 100),
    ],
)
def test_n1_awning_snaps_to_full_extension(percentage, expected):
    assert quantize(percentage, 1, full_coverage_at_zero=False) == expected


def test_n2_awning_rounds_toward_extension():
    # coverage 0.30 → 0.50 level → 50% extended
    assert quantize(30, 2, full_coverage_at_zero=False) == 50
    # coverage 0.60 → 1.0 → fully extended
    assert quantize(60, 2, full_coverage_at_zero=False) == 100
    assert quantize(0, 2, full_coverage_at_zero=False) == 0


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pct", range(0, 101, 5))
@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_never_reduces_coverage_blind(pct, n):
    """Quantized blind position is always <= input (more closed = more coverage)."""
    assert quantize(pct, n, full_coverage_at_zero=True) <= pct


@pytest.mark.parametrize("pct", range(0, 101, 5))
@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_never_reduces_coverage_awning(pct, n):
    """Quantized awning position is always >= input (more extended = more coverage)."""
    assert quantize(pct, n, full_coverage_at_zero=False) >= pct


def test_zero_steps_is_noop():
    assert quantize(37, 0, full_coverage_at_zero=True) == 37


# ---------------------------------------------------------------------------
# Bi-directional axis (issue #1104) — coverage is measured from a mid-scale
# pivot, one set of levels per side, and quantization never crosses the pivot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percentage", "n", "expected"),
    [
        # 60 % is 10 of the 50 points above the pivot → 0.2 coverage → the
        # half-step at 75 %. The old whole-range scale said 50 % — horizontal.
        (60, 2, 75),
        # One level per side is "fully closed on THIS side", not on either.
        (60, 1, 100),
        (51, 1, 100),
        # Below the pivot the sides mirror: 40 % is 10 of 50 → half-step at 25 %.
        (40, 2, 25),
        (49, 1, 0),
    ],
)
def test_mode2_pivot_quantizes_per_side(percentage, n, expected):
    assert quantize(percentage, n, full_coverage_at_zero=True, pivot=50.0) == expected


@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_on_the_pivot_stays_on_the_pivot(n):
    """Zero coverage demand on either side rounds up to the zero-th level."""
    assert quantize(50, n, full_coverage_at_zero=True, pivot=50.0) == 50


@pytest.mark.parametrize("percentage", range(0, 101))
@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_quantization_never_crosses_the_pivot(percentage, n):
    """The commanded slat stays on the side of horizontal the demand is on."""
    result = quantize(percentage, n, full_coverage_at_zero=True, pivot=50.0)
    if percentage > 50:
        assert result >= 50
    else:
        assert result <= 50


@pytest.mark.parametrize("percentage", range(0, 101))
@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_quantization_never_reduces_coverage(percentage, n):
    """Distance from the pivot — the coverage measure — never shrinks."""
    result = quantize(percentage, n, full_coverage_at_zero=True, pivot=50.0)
    assert abs(result - 50) >= abs(percentage - 50)


# A louvered roof with ``max_slat_angle=140`` puts horizontal at 90/140 → the
# pivot is fractional and off-centre, which is why it is an engine-supplied
# value rather than a constant.
_FRACTIONAL_PIVOT = 90.0 / 140.0 * 100.0  # 64.2857…


def test_fractional_pivot_upper_side_closes_upward():
    # 65 % is just above the pivot → one level → the top of the upper side.
    assert quantize(65, 1, full_coverage_at_zero=True, pivot=_FRACTIONAL_PIVOT) == 100


def test_fractional_pivot_lower_side_closes_downward():
    # 60 % is below the pivot → one level → the bottom of the lower side.
    assert quantize(60, 1, full_coverage_at_zero=True, pivot=_FRACTIONAL_PIVOT) == 0


@pytest.mark.parametrize("percentage", [0, 30, 60, 64, 65, 70, 90, 100])
@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_fractional_pivot_stays_on_its_side(percentage, n):
    result = quantize(
        percentage, n, full_coverage_at_zero=True, pivot=_FRACTIONAL_PIVOT
    )
    if percentage > _FRACTIONAL_PIVOT:
        assert result >= _FRACTIONAL_PIVOT
    else:
        assert result <= _FRACTIONAL_PIVOT


def test_degenerate_boundary_pivot_has_no_side_to_quantize_into():
    """A 0.0 pivot leaves the "below" side zero-width; 0 % is its only position."""
    assert quantize(0, 2, full_coverage_at_zero=False, pivot=0.0) == 0


# ``max_coverage_steps`` is bounded to 1–10 by ``const.OPTION_RANGES``.
@pytest.mark.parametrize("percentage", range(0, 101))
@pytest.mark.parametrize("n", range(1, 11))
def test_monotonic_pivot_reduces_to_the_boolean_path(percentage, n):
    """The generalization subsumes the monotonic axes rather than replacing them.

    A monotonic axis is a bi-directional one whose pivot sits on a boundary, so
    stating the pivot explicitly must be indistinguishable from not stating it —
    that equality is what keeps every blind / awning / MODE1-tilt caller byte-
    identical across this change (issue #978's guarantee).
    """
    assert quantize(percentage, n, True, pivot=100.0) == quantize(percentage, n, True)
    assert quantize(percentage, n, False, pivot=0.0) == quantize(percentage, n, False)


# ---------------------------------------------------------------------------
# Off-travel pivot (issue #1104 audit) — horizontal is not always reachable
# ---------------------------------------------------------------------------
# The pivot is ``TILT_HORIZONTAL_DEG``'s image under the engine's own
# angle→percentage map, and nothing constrains that map's range to 0–100. Two
# shipped configurations put horizontal off the travel entirely:
#
# * a louvered roof with ``max_slat_angle`` below 90° — the field is a plain
#   0–180 number box, so 45° gives 90/45 → 200 % and 60° gives 150 %;
# * a ``specify_angles`` calibration whose endpoints both sit on ONE side of
#   horizontal — the config flow only enforces ``angle_0 < angle_100``, so
#   0°→0 % / 45°→100 % gives 200 % and 120°/180° gives −50 %.
#
# A slat that never passes through horizontal is monotonic over its whole
# travel, which is exactly what ``pivot is None`` means — anchoring the level
# arithmetic on an unreachable point instead turns a zero-coverage demand into
# a full-scale move.
_OFF_TRAVEL_PIVOTS = [200.0, 150.0, 100.5, -0.5, -50.0]


@pytest.mark.parametrize("pivot", _OFF_TRAVEL_PIVOTS)
@pytest.mark.parametrize("percentage", range(0, 101, 5))
@pytest.mark.parametrize("n", range(1, 11))
@pytest.mark.parametrize("full_coverage_at_zero", [True, False])
def test_off_travel_pivot_reduces_to_the_boolean_path(
    pivot, percentage, n, full_coverage_at_zero
):
    """A pivot off the travel is the monotonic case, restated less precisely."""
    assert quantize(percentage, n, full_coverage_at_zero, pivot=pivot) == quantize(
        percentage, n, full_coverage_at_zero
    )


def test_off_travel_pivot_leaves_a_zero_coverage_demand_alone():
    """The headline case: 100 % is the most-open slat a 0–45° scale can reach.

    With horizontal at an unreachable 200 %, measuring the demand as distance
    from it makes the fully-open slat read as HALF the extended span — one
    level of coverage — and the single-level quantiser answers "fully closed".
    A demand that asks for no coverage must be left where it is.
    """
    assert quantize(100, 1, full_coverage_at_zero=True, pivot=200.0) == 100


def test_negative_pivot_does_not_invert_the_single_level():
    """Mirror image below the travel: 0 % must not be read as half-covered."""
    assert quantize(0, 1, full_coverage_at_zero=True, pivot=-50.0) == 0


# ---------------------------------------------------------------------------
# Reachable travel (issue #1104 audit) — the ladder spans the band, not the scale
# ---------------------------------------------------------------------------
# A tilt axis carries its own ``[min_tilt, max_tilt]`` band, applied BEFORE this
# quantiser and by nothing after it. Anchoring the levels on the pivot and
# spanning them to the far end of the 0–100 scale therefore makes the top level
# unreachable-by-configuration: on a ``max_tilt`` of 60 the single level above a
# 50 % pivot is 100 %, forty points past the cap the user set.

_BANDED_LADDER = [
    # 51 % is 1 of the 10 points between the pivot and a 60 % cap: a tenth of
    # the coverage available on that side, not a fiftieth of the scale.
    (51, 1, 60),
    (51, 2, 55),
    (51, 3, 53),
    # A demand already at the cap is the top level at every step count.
    (60, 1, 60),
    (60, 3, 60),
    # Below the pivot this band's edge IS the end of the travel, so the lower
    # side is unchanged by the bounds.
    (40, 1, 0),
    (40, 2, 25),
]


@pytest.mark.parametrize(("percentage", "n", "expected"), _BANDED_LADDER)
def test_bounded_levels_span_the_band(percentage, n, expected):
    assert (
        quantize(
            percentage, n, full_coverage_at_zero=True, pivot=50.0, bounds=(0.0, 60.0)
        )
        == expected
    )


@pytest.mark.parametrize(
    ("lo", "hi"), [(0, 100), (0, 60), (0, 55), (45, 100), (45, 55), (20, 60), (25, 75)]
)
@pytest.mark.parametrize("n", range(1, 11))
def test_bounded_result_never_leaves_the_band(lo, hi, n):
    """An in-band demand can only become an in-band command, and never opens up."""
    for percentage in range(lo, hi + 1):
        result = quantize(
            percentage,
            n,
            full_coverage_at_zero=True,
            pivot=50.0,
            bounds=(float(lo), float(hi)),
        )
        assert lo <= result <= hi
        assert abs(result - 50.0) >= abs(percentage - 50.0)


@pytest.mark.parametrize("full_coverage_at_zero", [True, False])
def test_the_whole_travel_is_the_default_bound(full_coverage_at_zero):
    """Stating the full travel explicitly must be indistinguishable from silence.

    Every caller without a band — and every axis whose band is the default
    0/100 — has to keep the behaviour issue #1104 shipped, so the bounds are an
    added constraint on the ladder, never a reshaping of it.
    """
    for percentage in range(0, 101):
        for n in range(1, 11):
            assert quantize(
                percentage, n, full_coverage_at_zero, pivot=50.0, bounds=(0.0, 100.0)
            ) == quantize(percentage, n, full_coverage_at_zero, pivot=50.0)


@pytest.mark.parametrize("pivot", [None, *_OFF_TRAVEL_PIVOTS])
@pytest.mark.parametrize("full_coverage_at_zero", [True, False])
def test_bounds_are_ignored_without_an_anchorable_pivot(pivot, full_coverage_at_zero):
    """No pivot, no ladder to constrain — the monotonic path is bit-identical.

    The band matters here only because the levels are ANCHORED on the pivot.
    Where they are not — a monotonic axis, or one whose pivot the travel never
    reaches — the boolean path answers, exactly as it did before bounds existed.
    Swept past both ends of the travel and past both ends of the step range so
    the ``n_steps < 1`` short-circuit is included.
    """
    for percentage in range(-5, 106):
        for n in range(0, 12):
            assert quantize(
                percentage, n, full_coverage_at_zero, pivot=pivot, bounds=(45.0, 55.0)
            ) == quantize(percentage, n, full_coverage_at_zero)


def test_a_band_pinned_to_one_point_has_nothing_to_quantize_into():
    """``min_tilt == max_tilt`` leaves the side zero-width; the demand stands."""
    assert (
        quantize(70, 3, full_coverage_at_zero=True, pivot=50.0, bounds=(70.0, 70.0))
        == 70
    )


def test_a_demand_already_past_the_band_is_not_opened_up():
    """Defensive: the quantiser adds coverage or nothing, never subtracts it.

    Both call sites band the value before handing it over, so this is not a
    reachable state — but "never reduce coverage" is the quantiser's whole
    contract, and it must not become conditional on the caller getting the
    ordering right.
    """
    assert (
        quantize(80, 1, full_coverage_at_zero=True, pivot=50.0, bounds=(0.0, 60.0))
        == 80
    )


# ---------------------------------------------------------------------------
# Live path — compute_solar_position applies quantization on the solar branch
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from custom_components.adaptive_cover_pro.pipeline.helpers import (  # noqa: E402
    compute_solar_position,
)
from tests.cover_helpers import attach_coverage_rounding  # noqa: E402


def _snapshot(*, calc_pct, minimize, steps, open_blocks_sun=False):
    config = SimpleNamespace(
        min_pos=None,
        max_pos=None,
        min_pos_sun_only=False,
        max_pos_sun_only=False,
        min_pos_sun_tracking=None,
    )
    policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=open_blocks_sun)])
    return SimpleNamespace(
        cover=attach_coverage_rounding(
            SimpleNamespace(
                direct_sun_valid=True,
                calculate_percentage=lambda: int(round(calc_pct)),
                calculate_raw_percentage=lambda: calc_pct,
            )
        ),
        config=config,
        policy=policy,
        minimize_movements=minimize,
        max_coverage_steps=steps,
    )


def test_solar_position_unquantized_when_disabled():
    snap = _snapshot(calc_pct=65.0, minimize=False, steps=1)
    assert compute_solar_position(snap) == 65


def test_solar_position_n1_blind_snaps_closed():
    # Blind: open_blocks_sun=False → full coverage at 0; floored to 1.
    snap = _snapshot(calc_pct=65.0, minimize=True, steps=1)
    assert compute_solar_position(snap) == 1


def test_solar_position_n2_blind_steps():
    snap = _snapshot(calc_pct=70.0, minimize=True, steps=2)
    assert compute_solar_position(snap) == 50


def test_solar_position_n1_awning_snaps_extended():
    # Awning: open_blocks_sun=True → full coverage at 100.
    snap = _snapshot(calc_pct=40.0, minimize=True, steps=1, open_blocks_sun=True)
    assert compute_solar_position(snap) == 100
