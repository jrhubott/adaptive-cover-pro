"""Tests for the ``fov_from_reveal`` derivation helper (issue #565).

The Measurements FOV mode derives a symmetric half-angle from the window
opening width and the reveal (recess) depth in front of the cover. The helper
is the single source of that arctan — both the config-flow save path and the
summary display call it.
"""

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_FOV_LEFT,
    DEFAULT_FOV_LEFT,
    OPTION_RANGES,
)
from custom_components.adaptive_cover_pro.engine.sun_geometry import fov_from_reveal


def test_width_2_depth_1_is_64_degrees():
    # atan(2/1) = atan(2) ≈ 63.43° → ceil → 64 (conservative: never round down)
    assert fov_from_reveal(2.0, 1.0) == 64


def test_width_2_depth_half_is_76_degrees():
    # atan(2/0.5) = atan(4) ≈ 75.96° → ceil → 76
    assert fov_from_reveal(2.0, 0.5) == 76


def test_zero_depth_is_full_hemisphere_default():
    assert fov_from_reveal(2.0, 0.0) == DEFAULT_FOV_LEFT
    assert fov_from_reveal(2.0, 0.0) == 90


def test_zero_width_is_full_hemisphere_default():
    assert fov_from_reveal(0.0, 1.0) == DEFAULT_FOV_LEFT


def test_negative_inputs_fall_back_to_default():
    assert fov_from_reveal(-1.0, 1.0) == DEFAULT_FOV_LEFT
    assert fov_from_reveal(2.0, -1.0) == DEFAULT_FOV_LEFT


def test_result_clamped_to_fov_range():
    lo, hi = OPTION_RANGES[CONF_FOV_LEFT]
    # Very wide, very shallow → angle approaches 90, never exceeds the range.
    assert fov_from_reveal(50.0, 0.01) <= hi
    assert fov_from_reveal(50.0, 0.01) >= lo


def test_returns_int():
    assert isinstance(fov_from_reveal(1.2, 0.5), int)


@pytest.mark.parametrize(
    ("width", "depth", "expected"),
    [
        (1.0, 1.0, 45),  # atan(1.0) = 45.00° → ceil → 45 (exact integer, unchanged)
        (3.0, 1.0, 72),  # atan(3.0) ≈ 71.57° → ceil → 72
        (1.2, 0.5, 68),  # atan(2.4) ≈ 67.38° → ceil → 68 (the summary example)
    ],
)
def test_known_angles(width, depth, expected):
    assert fov_from_reveal(width, depth) == expected


def test_real_world_reveal_0_84_x_0_25_is_74_degrees():
    """Reporter's window: 0.84 m wide, 0.25 m deep reveal.

    Ground truth: sun first reaches cover at ~228° for a 300° normal → 72° off-normal.
    arctan(0.84 / 0.25) = arctan(3.36) ≈ 73.43° → ceil → 74 (conservative).
    Regression guard for issue #565 (buggy /2 formula returned 59).
    """
    assert fov_from_reveal(0.84, 0.25) == 74
