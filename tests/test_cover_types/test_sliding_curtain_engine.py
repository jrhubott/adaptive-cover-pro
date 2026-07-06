"""Tests for the sliding-curtain calc engine (#829, Part 1).

The engine is deliberately binary: the fabric slides fully across the window
opening when the sun would strike the shade target (``direct_sun_valid``) and
retracts fully otherwise. ``calculate_position`` and ``calculate_percentage``
therefore return only the two canonical endpoints ``POSITION_CLOSED`` (0, drawn
across = blocks sun) and ``POSITION_OPEN`` (100, retracted). Part 2 will grow a
continuous width-fraction model; Part 1 pins the open/close semantics.

``gamma = (win_azi − sol_azi + 180) % 360 − 180``; with ``win_azi = 180`` a
``sol_azi`` of ``180 − gamma`` realises exactly the requested surface-solar
azimuth, so each case drives a real engine while pinning gamma precisely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import POSITION_CLOSED, POSITION_OPEN
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveSlidingCurtainCover,
)
from tests.cover_helpers import make_cover_config

_WIN_AZI = 180.0


def _safe_sun_data() -> MagicMock:
    """Sun data with far-off sunset/sunrise → ``sunset_valid`` is False."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    now = datetime.now(UTC)
    sun_data.sunset.return_value = now + timedelta(hours=6)
    sun_data.sunrise.return_value = now - timedelta(hours=6)
    return sun_data


def _past_sunset_sun_data() -> MagicMock:
    """Sun data whose sunset already passed → inside the sunset-offset window."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    now = datetime.now(UTC)
    sun_data.sunset.return_value = now - timedelta(hours=1)
    sun_data.sunrise.return_value = now - timedelta(hours=12)
    return sun_data


def _curtain(
    *,
    sol_elev: float,
    gamma: float,
    fov_left: int = 45,
    fov_right: int = 45,
    sun_data: MagicMock | None = None,
) -> AdaptiveSlidingCurtainCover:
    return AdaptiveSlidingCurtainCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=sun_data if sun_data is not None else _safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI, fov_left=fov_left, fov_right=fov_right
        ),
    )


# ---------------------------------------------------------------------------
# (a) Sun in front, above the horizon → fully closed (blocks sun)
# ---------------------------------------------------------------------------


def test_sun_in_fov_and_up_closes_fully():
    curtain = _curtain(sol_elev=45.0, gamma=0.0)
    assert curtain.direct_sun_valid is True
    assert curtain.calculate_percentage() == POSITION_CLOSED
    assert curtain.calculate_position() == POSITION_CLOSED


# ---------------------------------------------------------------------------
# (b) Azimuth outside the FOV → fully open (retracted)
# ---------------------------------------------------------------------------


def test_azimuth_out_of_fov_opens_fully():
    curtain = _curtain(sol_elev=45.0, gamma=100.0)
    assert curtain.direct_sun_valid is False
    assert curtain.calculate_percentage() == POSITION_OPEN
    assert curtain.calculate_position() == POSITION_OPEN


# ---------------------------------------------------------------------------
# (c) Sun below the horizon → fully open
# ---------------------------------------------------------------------------


def test_low_elevation_opens_fully():
    curtain = _curtain(sol_elev=-5.0, gamma=0.0)
    assert curtain.direct_sun_valid is False
    assert curtain.calculate_percentage() == POSITION_OPEN
    assert curtain.calculate_position() == POSITION_OPEN


# ---------------------------------------------------------------------------
# (d) Inside the sunset-offset window → fully open even with sun in front
# ---------------------------------------------------------------------------


def test_inside_sunset_window_opens_fully():
    curtain = _curtain(sol_elev=45.0, gamma=0.0, sun_data=_past_sunset_sun_data())
    assert curtain.sunset_valid is True
    assert curtain.direct_sun_valid is False
    assert curtain.calculate_percentage() == POSITION_OPEN
    assert curtain.calculate_position() == POSITION_OPEN


# ---------------------------------------------------------------------------
# Binary invariant — never anything but the two endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gamma", [-100.0, -30.0, 0.0, 30.0, 100.0])
@pytest.mark.parametrize("elev", [-5.0, 10.0, 45.0, 80.0])
def test_output_is_always_an_endpoint(elev, gamma):
    curtain = _curtain(sol_elev=elev, gamma=gamma)
    assert curtain.calculate_percentage() in (POSITION_CLOSED, POSITION_OPEN)
    assert curtain.calculate_position() in (POSITION_CLOSED, POSITION_OPEN)
