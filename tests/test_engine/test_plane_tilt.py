"""``AdaptiveGeneralCover.plane_tilt_deg`` — the working plane's tilt (#1237).

The isotropic sky/ground transposition needs β, the plane's tilt FROM
HORIZONTAL: 90° for glass in a wall, the roof pitch for a skylight or a
louvered roof. ``cos_aoi`` already answers "how obliquely does the beam strike
this plane?" polymorphically; β is the other half of the same geometry and gets
the same treatment — one property on the base, two overrides, no cover-type
string anywhere.

⚠️ It is a PROPERTY, never a trace key. ``tests/test_solar_calc_trace.py``
asserts exact ``_last_calc_details`` key sets per cover type, and a new key
there breaks the ``solar_calculation`` sensor's contract and the companion
card's payload. The last test in this file guards that directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import (
    LouveredRoofConfig,
    RoofWindowConfig,
)
from custom_components.adaptive_cover_pro.const import VERTICAL_GLASS_PITCH_DEG
from custom_components.adaptive_cover_pro.engine.covers.louvered_roof import (
    AdaptiveLouveredRoofCover,
)
from custom_components.adaptive_cover_pro.engine.covers.roof_window import (
    AdaptiveRoofWindowCover,
)
from tests.cover_helpers import (
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
    make_cover_config,
    make_daytime_sun_data,
    make_tilt_config,
    make_vertical_config,
)

pytestmark = pytest.mark.unit


_COMMON = {
    "sol_azi": 180.0,
    "sol_elev": 45.0,
    "sunset_pos": 0,
    "sunset_off": 0,
    "sunrise_off": 0,
    "fov_left": 45,
    "fov_right": 45,
    "win_azi": 180,
    "h_def": 50,
    "max_pos": 100,
    "min_pos": 0,
    "max_pos_bool": False,
    "min_pos_bool": False,
    "blind_spot_left": None,
    "blind_spot_right": None,
    "blind_spot_elevation": None,
    "blind_spot_on": False,
    "min_elevation": None,
    "max_elevation": None,
}


def _vertical():
    return build_vertical_cover(
        logger=MagicMock(),
        sun_data=make_daytime_sun_data(),
        distance=0.5,
        h_win=2.0,
        **_COMMON,
    )


def _horizontal():
    return build_horizontal_cover(
        logger=MagicMock(),
        sun_data=make_daytime_sun_data(),
        distance=0.5,
        h_win=2.0,
        awn_length=2.0,
        awn_angle=0,
        **_COMMON,
    )


def _tilt():
    return build_tilt_cover(
        logger=MagicMock(),
        sun_data=make_daytime_sun_data(),
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
        **_COMMON,
    )


def _roof_window(pitch: float):
    return AdaptiveRoofWindowCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=make_daytime_sun_data(),
        config=make_cover_config(win_azi=180.0, fov_left=90, fov_right=90),
        vert_config=make_vertical_config(distance=1.0, h_win=2.0),
        roof_config=RoofWindowConfig(roof_pitch=pitch, roof_height_above=0.0),
    )


def _louvered_roof(pitch: float):
    return AdaptiveLouveredRoofCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=180.0, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(slat_distance=0.02, depth=0.03, mode="mode2"),
        roof_config=LouveredRoofConfig(roof_pitch=pitch, max_slat_angle=0.0),
    )


# ---------------------------------------------------------------------------
# Vertical-plane engines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "factory"),
    [("vertical", _vertical), ("horizontal", _horizontal), ("tilt", _tilt)],
)
def test_wall_mounted_engines_report_vertical_glass(name, factory):
    assert factory().plane_tilt_deg == pytest.approx(VERTICAL_GLASS_PITCH_DEG)


def test_the_vertical_constant_is_ninety_degrees():
    """β is measured FROM HORIZONTAL, so vertical glass is 90, not 0."""
    assert VERTICAL_GLASS_PITCH_DEG == 90.0


def test_the_engine_constant_is_the_const_module_one():
    """One constant, three consumers — no second 90.0 to drift (#1237)."""
    from custom_components.adaptive_cover_pro.engine.covers import roof_window

    assert roof_window.VERTICAL_GLASS_PITCH_DEG is VERTICAL_GLASS_PITCH_DEG


# ---------------------------------------------------------------------------
# Pitched-plane engines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pitch", [0.0, 15.0, 35.0, 90.0])
def test_roof_window_reports_its_roof_pitch(pitch):
    assert _roof_window(pitch).plane_tilt_deg == pytest.approx(pitch)


@pytest.mark.parametrize("pitch", [0.0, 20.0, 45.0, 90.0])
def test_louvered_roof_reports_its_roof_pitch(pitch):
    assert _louvered_roof(pitch).plane_tilt_deg == pytest.approx(pitch)


def test_a_vertical_roof_window_agrees_with_the_wall_engines():
    """β = 90 is the documented reduction to the vertical case."""
    assert _roof_window(VERTICAL_GLASS_PITCH_DEG).plane_tilt_deg == pytest.approx(
        _vertical().plane_tilt_deg
    )


# ---------------------------------------------------------------------------
# #682 guard — the property must never reach the trace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "factory"),
    [
        ("vertical", _vertical),
        ("horizontal", _horizontal),
        ("tilt", _tilt),
        ("roof_window", lambda: _roof_window(35.0)),
        ("louvered_roof", lambda: _louvered_roof(20.0)),
    ],
)
def test_plane_tilt_never_enters_the_calc_trace(name, factory):
    cover = factory()
    cover.calculate_position()
    assert "plane_tilt_deg" not in cover._last_calc_details
