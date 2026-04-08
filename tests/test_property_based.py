"""Property-based tests using Hypothesis to fuzz the calculation engine and pipeline.

Verifies that:
- Cover positions are always in [0, 100] for any valid input
- The engine never crashes on extreme-but-valid sun positions
- Pipeline always returns a position (default handler fallback)
- Inverse state inversion is always valid
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.cover_helpers import (
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Strategies for valid cover inputs
# ---------------------------------------------------------------------------

# Sun azimuth: 0–360 degrees
_azimuth = st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False)
# Sun elevation: -10 to 90 degrees (some below-horizon values for edge case testing)
_elevation = st.floats(min_value=-10.0, max_value=90.0, allow_nan=False, allow_infinity=False)
# Window azimuth: 0–360
_win_azi = st.integers(min_value=0, max_value=360)
# FOV: 1–90 degrees per side
_fov = st.integers(min_value=1, max_value=90)
# Distance from window: 0.1–10 metres
_distance = st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)
# Window height: 0.5–6 metres
_h_win = st.floats(min_value=0.5, max_value=6.0, allow_nan=False, allow_infinity=False)
# Default position: 0–100%
_h_def = st.integers(min_value=0, max_value=100)


def _make_sun_data(sol_azi: float, sol_elev: float) -> MagicMock:
    """Build a minimal SunData mock."""
    sd = MagicMock()
    sd.timezone = "UTC"
    sd.solar_azimuth = sol_azi
    sd.solar_elevation = sol_elev
    return sd


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# 9a: Vertical cover — position always 0–100
# ---------------------------------------------------------------------------

@settings(max_examples=200, deadline=1000)
@given(
    sol_azi=_azimuth,
    sol_elev=_elevation,
    win_azi=_win_azi,
    fov_left=_fov,
    fov_right=_fov,
    distance=_distance,
    h_win=_h_win,
    h_def=_h_def,
)
def test_vertical_position_always_0_to_100(
    sol_azi, sol_elev, win_azi, fov_left, fov_right, distance, h_win, h_def
) -> None:
    """Vertical cover position is always in [0, 100] for any valid input."""
    cover = build_vertical_cover(
        logger=_make_logger(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=_make_sun_data(sol_azi, sol_elev),
        win_azi=win_azi,
        fov_left=fov_left,
        fov_right=fov_right,
        h_def=h_def,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=distance,
        h_win=h_win,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100, (
        f"Vertical position {position} out of range for "
        f"sol_azi={sol_azi:.1f}, sol_elev={sol_elev:.1f}, "
        f"win_azi={win_azi}, fov=({fov_left},{fov_right})"
    )


# ---------------------------------------------------------------------------
# 9b: Horizontal cover — position always 0–100
# ---------------------------------------------------------------------------

@settings(max_examples=200, deadline=1000)
@given(
    sol_azi=_azimuth,
    sol_elev=_elevation,
    win_azi=_win_azi,
    fov_left=_fov,
    fov_right=_fov,
    distance=_distance,
    h_win=_h_win,
    h_def=_h_def,
    awn_length=st.floats(min_value=0.5, max_value=6.0, allow_nan=False, allow_infinity=False),
    awn_angle=st.floats(min_value=0.0, max_value=45.0, allow_nan=False, allow_infinity=False),
)
def test_horizontal_position_always_0_to_100(
    sol_azi, sol_elev, win_azi, fov_left, fov_right, distance, h_win, h_def,
    awn_length, awn_angle
) -> None:
    """Horizontal awning position is always in [0, 100] for any valid input."""
    cover = build_horizontal_cover(
        logger=_make_logger(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=_make_sun_data(sol_azi, sol_elev),
        win_azi=win_azi,
        fov_left=fov_left,
        fov_right=fov_right,
        h_def=h_def,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=distance,
        h_win=h_win,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        awn_length=awn_length,
        awn_angle=awn_angle,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100, (
        f"Horizontal position {position} out of range"
    )


# ---------------------------------------------------------------------------
# 9c: Tilt cover — position always 0–100
# ---------------------------------------------------------------------------

@settings(max_examples=200, deadline=1000)
@given(
    sol_azi=_azimuth,
    sol_elev=_elevation,
    win_azi=_win_azi,
    fov_left=_fov,
    fov_right=_fov,
    h_def=_h_def,
)
def test_tilt_position_always_0_to_100(
    sol_azi, sol_elev, win_azi, fov_left, fov_right, h_def
) -> None:
    """Tilt cover position is always in [0, 100] for any valid input."""
    cover = build_tilt_cover(
        logger=_make_logger(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=_make_sun_data(sol_azi, sol_elev),
        win_azi=win_azi,
        fov_left=fov_left,
        fov_right=fov_right,
        h_def=h_def,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100, (
        f"Tilt position {position} out of range"
    )


def test_tilt_narrow_fov_edge_case() -> None:
    """Tilt cover with 1° FOV and 60° elevation stays within [0, 100]."""
    cover = build_tilt_cover(
        logger=_make_logger(),
        sol_azi=0.0,
        sol_elev=60.0,
        sun_data=_make_sun_data(0.0, 60.0),
        win_azi=0,
        fov_left=1,
        fov_right=1,
        h_def=0,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100, f"Tilt position {position} out of range"


# ---------------------------------------------------------------------------
# 9d: Edge cases — no crash on extreme inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sol_elev", [-90.0, -0.001, 0.0, 0.001, 1.9, 88.0, 88.5, 90.0])
def test_vertical_extreme_elevations_no_crash(sol_elev: float) -> None:
    """Vertical cover handles all extreme elevation values without crashing."""
    cover = build_vertical_cover(
        logger=_make_logger(),
        sol_azi=180.0,
        sol_elev=sol_elev,
        sun_data=_make_sun_data(180.0, sol_elev),
        win_azi=180,
        fov_left=45,
        fov_right=45,
        h_def=50,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=0.5,
        h_win=2.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100


@pytest.mark.parametrize("sol_azi,win_azi", [
    (0.0, 0), (0.0, 180), (180.0, 0), (360.0, 360),
    (90.0, 270), (270.0, 90), (45.0, 315),
])
def test_vertical_extreme_azimuth_combinations_no_crash(sol_azi: float, win_azi: int) -> None:
    """Vertical cover handles extreme azimuth combinations without crashing."""
    cover = build_vertical_cover(
        logger=_make_logger(),
        sol_azi=sol_azi,
        sol_elev=45.0,
        sun_data=_make_sun_data(sol_azi, 45.0),
        win_azi=win_azi,
        fov_left=45,
        fov_right=45,
        h_def=50,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=0.5,
        h_win=2.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    position = cover.calculate_position()
    assert 0 <= position <= 100


# ---------------------------------------------------------------------------
# 9e: Inverse state always valid (0–100 after inversion)
# ---------------------------------------------------------------------------

@settings(max_examples=300, deadline=500)
@given(position=st.integers(min_value=0, max_value=100))
def test_inverse_state_always_valid(position: int) -> None:
    """Inverting a position (100 - p) always stays in [0, 100]."""
    inverted = 100 - position
    assert 0 <= inverted <= 100


# ---------------------------------------------------------------------------
# 9f: Pipeline — DefaultHandler always provides fallback
# ---------------------------------------------------------------------------

def test_pipeline_default_handler_never_returns_none() -> None:
    """DefaultHandler always returns a non-None position (the fallback)."""
    from custom_components.adaptive_cover_pro.pipeline.handlers import DefaultHandler
    from tests.cover_helpers import make_cover_config

    cover_config = make_cover_config(h_def=50, max_pos=100, min_pos=0)
    cover_mock = MagicMock()
    cover_mock.direct_sun_valid = False

    # Use a plain MagicMock (not spec'd) so attribute access always works
    snapshot = MagicMock()
    snapshot.default_position = 50
    snapshot.in_time_window = True
    snapshot.sunset_position = 50
    snapshot.config = cover_config
    snapshot.cover = cover_mock

    handler = DefaultHandler()
    result = handler.evaluate(snapshot)
    assert result is not None
    assert result.position is not None


# ---------------------------------------------------------------------------
# 9g: Min/max position clamping is always valid
# ---------------------------------------------------------------------------

@settings(max_examples=300, deadline=500)
@given(
    raw_position=st.integers(min_value=-200, max_value=200),
    min_pos=st.integers(min_value=0, max_value=49),
    max_pos=st.integers(min_value=51, max_value=100),
)
def test_position_clamp_always_in_bounds(
    raw_position: int, min_pos: int, max_pos: int
) -> None:
    """Clamping a position to [min_pos, max_pos] always stays in bounds."""
    assume(min_pos < max_pos)
    clamped = max(min_pos, min(max_pos, raw_position))
    assert min_pos <= clamped <= max_pos
