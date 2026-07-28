"""Tests for SlidingCurtainConfig.from_options (#829, Part 2).

Pins the CONF_* → field mapping, the defaults (bi-part slide direction, disabled
shade area), and the ``is_area_configured`` predicate that decides binary
(Part 1) vs continuous (Part 2) behaviour.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.config_types import SlidingCurtainConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_SLIDING_ENABLE_SHADE_AREA,
    CONF_SLIDING_POINT1_X,
    CONF_SLIDING_POINT1_Y,
    CONF_SLIDING_POINT2_X,
    CONF_SLIDING_POINT2_Y,
    CONF_SLIDING_SLIDE_DIRECTION,
    CONF_WINDOW_WIDTH,
    DEFAULT_SLIDING_SLIDE_DIRECTION,
    SlideDirection,
)


def test_defaults_when_empty_options():
    cfg = SlidingCurtainConfig.from_options({})
    assert cfg.enabled is False
    assert cfg.slide_direction == DEFAULT_SLIDING_SLIDE_DIRECTION
    assert cfg.slide_direction == SlideDirection.BI_PART.value
    assert cfg.window_width == 0.0
    assert cfg.point1_x == 0.0
    assert cfg.point1_y == 0.0
    assert cfg.point2_x == 0.0
    assert cfg.point2_y == 0.0


def test_maps_all_keys():
    options = {
        CONF_SLIDING_ENABLE_SHADE_AREA: True,
        CONF_SLIDING_SLIDE_DIRECTION: SlideDirection.LEFT.value,
        CONF_WINDOW_WIDTH: 2.4,
        CONF_SLIDING_POINT1_X: -0.5,
        CONF_SLIDING_POINT1_Y: 3.0,
        CONF_SLIDING_POINT2_X: 0.8,
        CONF_SLIDING_POINT2_Y: 4.5,
    }
    cfg = SlidingCurtainConfig.from_options(options)
    assert cfg.enabled is True
    assert cfg.slide_direction == SlideDirection.LEFT.value
    assert cfg.window_width == 2.4
    assert cfg.point1_x == -0.5
    assert cfg.point1_y == 3.0
    assert cfg.point2_x == 0.8
    assert cfg.point2_y == 4.5


def test_coerces_ha_float_strings_and_ints():
    options = {
        CONF_SLIDING_ENABLE_SHADE_AREA: True,
        CONF_WINDOW_WIDTH: 2,
        CONF_SLIDING_POINT1_X: "-1.5",
        CONF_SLIDING_POINT1_Y: "2",
    }
    cfg = SlidingCurtainConfig.from_options(options)
    assert cfg.window_width == 2.0
    assert cfg.point1_x == -1.5
    assert cfg.point1_y == 2.0


def test_is_area_configured_requires_enable_and_width():
    base = {
        CONF_WINDOW_WIDTH: 2.0,
        CONF_SLIDING_POINT1_Y: 3.0,
        CONF_SLIDING_POINT2_Y: 3.0,
    }
    # Disabled → not configured regardless of points.
    assert (
        SlidingCurtainConfig.from_options(
            {**base, CONF_SLIDING_ENABLE_SHADE_AREA: False}
        ).is_area_configured
        is False
    )
    # Enabled with a real width → configured.
    assert (
        SlidingCurtainConfig.from_options(
            {**base, CONF_SLIDING_ENABLE_SHADE_AREA: True}
        ).is_area_configured
        is True
    )


def test_is_area_configured_false_without_window_width():
    cfg = SlidingCurtainConfig.from_options({CONF_SLIDING_ENABLE_SHADE_AREA: True})
    assert cfg.is_area_configured is False
