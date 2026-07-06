"""Characterization tests for the shared window-plane projection helper (#829).

``ray_x_at_window_plane`` is extracted from
``vertical.glare_zone_effective_distance`` (the ``x_at_window = nearest_x +
nearest_y * tan(gamma)`` line) so the glare-zone projection and the
sliding-curtain shade-area projection share ONE formula. These triples pin the
formula independently of either caller.
"""

from __future__ import annotations

import math

import pytest

from custom_components.adaptive_cover_pro.engine.sun_geometry import (
    ray_x_at_window_plane,
)

# ---------------------------------------------------------------------------
# gamma = 0 → the ray is perpendicular, so it crosses the window plane at the
# floor point's own x (no along-wall shift). Exact, no tan rounding.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x_floor", "y_floor", "expected"),
    [
        (0.5, 3.0, 0.5),
        (-2.0, 10.0, -2.0),
        (0.0, 3.0, 0.0),
    ],
)
def test_gamma_zero_returns_x_floor_exactly(x_floor, y_floor, expected):
    assert ray_x_at_window_plane(x_floor, y_floor, 0.0) == expected


# ---------------------------------------------------------------------------
# Positive gamma shifts the entry point in +x; negative gamma in −x. Hand-worked
# against math.tan so the sign convention is pinned (matches vertical.py).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x_floor", "y_floor", "gamma", "expected"),
    [
        (0.0, 2.0, 45.0, 2.0 * math.tan(math.radians(45.0))),
        (0.0, 4.0, -45.0, 4.0 * math.tan(math.radians(-45.0))),
        (1.0, 2.0, -45.0, 1.0 + 2.0 * math.tan(math.radians(-45.0))),
        (0.5, 2.0, 30.0, 0.5 + 2.0 * math.tan(math.radians(30.0))),
        (-0.3, 4.0, 35.0, -0.3 + 4.0 * math.tan(math.radians(35.0))),
    ],
)
def test_signed_gamma_projection(x_floor, y_floor, gamma, expected):
    assert ray_x_at_window_plane(x_floor, y_floor, gamma) == pytest.approx(expected)


def test_matches_vertical_glare_zone_projection() -> None:
    """The helper reproduces the projection inlined in glare_zone_effective_distance."""
    # nearest_x / nearest_y as computed inside glare_zone_effective_distance for a
    # zone facing the sun; the helper must reproduce its x_at_window term.
    nearest_x, nearest_y, gamma = 0.42, 3.6, 28.0
    expected = nearest_x + nearest_y * math.tan(math.radians(gamma))
    assert ray_x_at_window_plane(nearest_x, nearest_y, gamma) == expected
