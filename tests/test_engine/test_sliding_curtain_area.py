"""Tests for the sliding-curtain continuous shade-area model (#829, Part 2).

The engine projects a two-point floor shade area onto the window plane to get
the along-wall interval the fabric must cover, then maps that interval to an
open percentage per slide direction. These tests drive the geometry directly.

``gamma = (win_azi − sol_azi + 180) % 360 − 180``; with ``win_azi = 180`` a
``sol_azi`` of ``180 − gamma`` realises exactly the requested surface-solar
azimuth.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import SlidingCurtainConfig
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveSlidingCurtainCover,
)
from tests.cover_helpers import make_cover_config

_WIN_AZI = 180.0


def _safe_sun_data() -> MagicMock:
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    now = datetime.now(UTC)
    sun_data.sunset.return_value = now + timedelta(hours=6)
    sun_data.sunrise.return_value = now - timedelta(hours=6)
    return sun_data


def _curtain(
    *,
    gamma: float,
    sc_config: SlidingCurtainConfig | None,
    sol_elev: float = 45.0,
    fov_left: int = 90,
    fov_right: int = 90,
) -> AdaptiveSlidingCurtainCover:
    return AdaptiveSlidingCurtainCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=_safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI, fov_left=fov_left, fov_right=fov_right
        ),
        sc_config=sc_config,
    )


def _sc(
    *,
    width: float,
    p1: tuple[float, float],
    p2: tuple[float, float],
    direction: str = "bi_part",
) -> SlidingCurtainConfig:
    return SlidingCurtainConfig(
        enabled=True,
        slide_direction=direction,
        window_width=width,
        point1_x=p1[0],
        point1_y=p1[1],
        point2_x=p2[0],
        point2_y=p2[1],
    )


# ---------------------------------------------------------------------------
# _covered_interval — projection + clamp + guards
# ---------------------------------------------------------------------------


def test_straight_ahead_gamma_zero_is_point_x():
    """gamma=0: each point projects to its own x (no along-wall shift)."""
    sc = _sc(width=2.0, p1=(-0.3, 3.0), p2=(0.4, 5.0))
    a, b = _curtain(gamma=0.0, sc_config=sc)._covered_interval()
    assert a == pytest.approx(-0.3)
    assert b == pytest.approx(0.4)


def test_point_order_does_not_matter():
    """The interval is [min, max] regardless of which point is p1/p2."""
    sc = _sc(width=2.0, p1=(0.4, 5.0), p2=(-0.3, 3.0))
    a, b = _curtain(gamma=0.0, sc_config=sc)._covered_interval()
    assert a == pytest.approx(-0.3)
    assert b == pytest.approx(0.4)


def test_signed_gamma_shifts_interval():
    sc = _sc(width=8.0, p1=(0.0, 2.0), p2=(0.0, 4.0))
    a, b = _curtain(gamma=30.0, sc_config=sc)._covered_interval()
    t = math.tan(math.radians(30.0))
    assert a == pytest.approx(2.0 * t)
    assert b == pytest.approx(4.0 * t)


def test_interval_entirely_off_window_returns_none():
    """Both points project past the same window edge → naturally unshaded."""
    sc = _sc(width=2.0, p1=(0.0, 3.0), p2=(0.0, 5.0))  # xw ≈ 3, 5 at gamma 45
    assert _curtain(gamma=45.0, sc_config=sc)._covered_interval() is None


def test_partial_off_window_clamps_to_half():
    """One point off the left edge → interval clamps to [-half, ...]."""
    sc = _sc(width=2.0, p1=(-3.0, 3.0), p2=(0.5, 3.0))
    a, b = _curtain(gamma=0.0, sc_config=sc)._covered_interval()
    assert a == pytest.approx(-1.0)  # clamped to -half
    assert b == pytest.approx(0.5)


def test_zero_depth_point_returns_none():
    sc = _sc(width=2.0, p1=(0.5, 0.0), p2=(0.5, 3.0))
    assert _curtain(gamma=0.0, sc_config=sc)._covered_interval() is None


def test_negative_depth_point_returns_none():
    sc = _sc(width=2.0, p1=(0.5, -1.0), p2=(0.5, 3.0))
    assert _curtain(gamma=0.0, sc_config=sc)._covered_interval() is None


def test_covered_interval_none_config_returns_none():
    assert _curtain(gamma=0.0, sc_config=None)._covered_interval() is None


def test_covered_interval_zero_width_returns_none():
    sc = _sc(width=0.0, p1=(0.0, 3.0), p2=(0.2, 3.0))
    assert _curtain(gamma=0.0, sc_config=sc)._covered_interval() is None


# ---------------------------------------------------------------------------
# _position_for_interval — per-direction mapping (0 = closed, 100 = open)
# gamma=0 so each point projects to its own x; width=2 → half=1.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "p1", "p2", "expected"),
    [
        # Interval on the right half [0.4, 0.8], width 2 (half 1).
        # LEFT: openness = (half - b)/W = (1 - 0.8)/2 = 10%.
        ("left", (0.4, 3.0), (0.8, 3.0), 10.0),
        # RIGHT: openness = (half + a)/W = (1 + 0.4)/2 = 70%.
        ("right", (0.4, 3.0), (0.8, 3.0), 70.0),
        # BI_PART: d_near = a = 0.4 → 200*0.4/2 = 40%.
        ("bi_part", (0.4, 3.0), (0.8, 3.0), 40.0),
        # Interval on the left half [-0.8, -0.4].
        # LEFT: (1 - (-0.4))/2 = 70%.
        ("left", (-0.8, 3.0), (-0.4, 3.0), 70.0),
        # RIGHT: (1 + (-0.8))/2 = 10%.
        ("right", (-0.8, 3.0), (-0.4, 3.0), 10.0),
        # BI_PART: d_near = -b = 0.4 → 40%.
        ("bi_part", (-0.8, 3.0), (-0.4, 3.0), 40.0),
    ],
)
def test_percentage_per_direction(direction, p1, p2, expected):
    sc = _sc(width=2.0, p1=p1, p2=p2, direction=direction)
    curtain = _curtain(gamma=0.0, sc_config=sc)
    assert curtain.calculate_percentage() == pytest.approx(expected)
    assert curtain.calculate_position() == pytest.approx(expected)


@pytest.mark.parametrize("direction", ["left", "right", "bi_part"])
def test_full_width_interval_is_fully_closed(direction):
    """An interval spanning the whole opening → 0% (fully drawn) every direction."""
    sc = _sc(width=2.0, p1=(-1.0, 3.0), p2=(1.0, 3.0), direction=direction)
    assert _curtain(gamma=0.0, sc_config=sc).calculate_percentage() == pytest.approx(
        0.0
    )


def test_bi_part_straddling_centre_is_fully_closed():
    """A span crossing the centre exposes the middle unless fully closed."""
    sc = _sc(width=2.0, p1=(-0.3, 3.0), p2=(0.6, 3.0), direction="bi_part")
    assert _curtain(gamma=0.0, sc_config=sc).calculate_percentage() == pytest.approx(
        0.0
    )


def test_partial_off_window_clamps_percentage():
    """Left direction, interval clamped to [-1, 0.5] → (1 - 0.5)/2 = 25%."""
    sc = _sc(width=2.0, p1=(-3.0, 3.0), p2=(0.5, 3.0), direction="left")
    assert _curtain(gamma=0.0, sc_config=sc).calculate_percentage() == pytest.approx(
        25.0
    )


# ---------------------------------------------------------------------------
# _solve — top-level dispatch + edge cases
# ---------------------------------------------------------------------------


def test_no_area_falls_back_to_binary_endpoint():
    """sc_config disabled → Part 1 binary: closed under direct sun."""
    sc = SlidingCurtainConfig(enabled=False, window_width=2.0)
    curtain = _curtain(gamma=0.0, sc_config=sc)
    assert curtain.direct_sun_valid is True
    assert curtain.calculate_percentage() == 0  # POSITION_CLOSED


def test_sc_config_none_falls_back_to_binary_endpoint():
    curtain = _curtain(gamma=0.0, sc_config=None)
    assert curtain.calculate_percentage() == 0  # POSITION_CLOSED, direct sun


def test_not_direct_sun_valid_opens_fully():
    """Area configured but sun out of FOV → fully open (100)."""
    sc = _sc(width=2.0, p1=(0.4, 3.0), p2=(0.8, 3.0), direction="bi_part")
    curtain = _curtain(gamma=120.0, sc_config=sc)  # outside 90° FOV
    assert curtain.direct_sun_valid is False
    assert curtain.calculate_percentage() == 100  # POSITION_OPEN


def test_zero_depth_point_opens_fully():
    sc = _sc(width=2.0, p1=(0.5, 0.0), p2=(0.5, 3.0), direction="left")
    assert _curtain(gamma=0.0, sc_config=sc).calculate_percentage() == 100


def test_interval_off_window_opens_fully():
    sc = _sc(width=2.0, p1=(0.0, 3.0), p2=(0.0, 5.0), direction="left")
    assert _curtain(gamma=45.0, sc_config=sc).calculate_percentage() == 100
