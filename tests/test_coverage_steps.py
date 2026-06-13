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
# Live path — compute_solar_position applies quantization on the solar branch
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from custom_components.adaptive_cover_pro.pipeline.helpers import (  # noqa: E402
    compute_solar_position,
)


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
        cover=SimpleNamespace(
            direct_sun_valid=True, calculate_percentage=lambda: calc_pct
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
