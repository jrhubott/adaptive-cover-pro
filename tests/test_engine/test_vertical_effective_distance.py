# tests/test_engine/test_vertical_effective_distance.py
"""Tests for AdaptiveVerticalCover effective_distance_override."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    AdaptiveVerticalCover,
    glare_zone_effective_distance,  # public name
)
from custom_components.adaptive_cover_pro.config_types import (
    CoverConfig,
    VerticalConfig,
)


def _make_cover(distance: float = 3.0, h_win: float = 2.2) -> AdaptiveVerticalCover:
    """Build a minimal AdaptiveVerticalCover for testing."""
    config = CoverConfig(
        win_azi=180,
        fov_left=90,
        fov_right=90,
        h_def=0,
        sunset_pos=None,
        sunset_off=0,
        sunrise_off=0,
        max_pos=100,
        min_pos=0,
        max_pos_sun_only=False,
        min_pos_sun_only=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
    )
    vert_config = VerticalConfig(distance=distance, h_win=h_win)
    sun_data = MagicMock()
    sun_data.sunset_time = None
    sun_data.sunrise_time = None
    return AdaptiveVerticalCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=30.0,
        sun_data=sun_data,
        config=config,
        vert_config=vert_config,
    )


def test_glare_zone_effective_distance_is_public() -> None:
    """glare_zone_effective_distance must be importable without underscore."""
    assert callable(glare_zone_effective_distance)


def test_calculate_percentage_accepts_effective_distance_override() -> None:
    """calculate_percentage must accept optional effective_distance_override kwarg."""
    cover = _make_cover(distance=3.0)
    # Should not raise
    result = cover.calculate_percentage(effective_distance_override=5.0)
    assert isinstance(result, (int, float))


def test_effective_distance_override_increases_position() -> None:
    """A larger effective_distance_override should produce a higher blind position."""
    cover = _make_cover(distance=3.0)
    base = cover.calculate_percentage()
    with_override = cover.calculate_percentage(effective_distance_override=5.0)
    assert with_override >= base


def test_glare_zone_loop_removed_from_calculate_position() -> None:
    """active_zone_names on cover no longer affects calculate_position result."""
    from custom_components.adaptive_cover_pro.config_types import (
        GlareZone,
        GlareZonesConfig,
    )

    cover = _make_cover(distance=3.0)
    zone = GlareZone(name="desk", x=0.0, y=400.0, radius=30.0)
    glare_cfg = GlareZonesConfig(zones=[zone], window_width=120.0)
    cover.vert_config = dataclasses.replace(cover.vert_config, glare_zones=glare_cfg)
    cover.active_zone_names = {"desk"}
    result_with_zone = cover.calculate_percentage()
    cover.active_zone_names = set()
    result_without_zone = cover.calculate_percentage()
    # After refactoring, loop is removed so results are identical
    assert result_with_zone == result_without_zone
