"""Tests for GlareZoneHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone import (
    GlareZoneHandler,
)
from tests.test_pipeline.conftest import make_snapshot


def test_glare_zone_control_method_exists() -> None:
    """GLARE_ZONE must be a valid ControlMethod value."""
    assert ControlMethod.GLARE_ZONE == "glare_zone"


def _make_vertical_cover(
    distance: float = 3.0,
    gamma: float = 0.0,
    direct_sun_valid: bool = True,
    calculate_percentage_return: float = 60.0,
):
    """Build a mock AdaptiveVerticalCover for GlareZoneHandler tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.distance = distance
    cover.gamma = gamma
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.config = MagicMock()
    cover.config.min_pos = None
    cover.config.max_pos = None
    cover.config.min_pos_sun_only = False
    cover.config.max_pos_sun_only = False
    return cover


def _make_glare_config(
    zones=None,
    window_width: float = 120.0,
) -> GlareZonesConfig:
    if zones is None:
        zones = [GlareZone(name="desk", x=0.0, y=400.0, radius=30.0)]
    return GlareZonesConfig(zones=zones, window_width=window_width)


class TestGlareZoneHandlerGating:
    handler = GlareZoneHandler()

    def test_returns_none_for_awning_cover(self) -> None:
        """GlareZoneHandler only applies to vertical covers."""
        snap = make_snapshot(cover_type="cover_awning")
        assert self.handler.evaluate(snap) is None

    def test_returns_none_for_tilt_cover(self) -> None:
        snap = make_snapshot(cover_type="cover_tilt")
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_glare_zones(self) -> None:
        snap = make_snapshot(cover_type="cover_blind", glare_zones=None)
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_active_zones(self) -> None:
        snap = make_snapshot(
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names=set(),  # no zones active
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_sun_not_valid(self) -> None:
        """Returns None when sun is not in FOV (no need to protect zones)."""
        cover = _make_vertical_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZoneHandlerLogic:
    handler = GlareZoneHandler()

    def test_returns_glare_zone_control_method_when_active(self) -> None:
        """When glare zone produces stricter position, return GLARE_ZONE method."""
        cover = _make_vertical_cover(
            distance=1.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=40.0,
        )
        # Zone at y=400cm (4m), much farther than base distance of 1.0m
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=400.0, radius=30.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE

    def test_falls_through_when_base_distance_wins(self) -> None:
        """Returns None when base distance is farther than all glare zones."""
        cover = _make_vertical_cover(
            distance=10.0,  # base distance 10m >> zone distance
            gamma=0.0,
            direct_sun_valid=True,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=10.0)],  # 0.9m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        # Base distance wins → fall through to SolarHandler
        assert result is None

    def test_inactive_zone_is_ignored(self) -> None:
        """Zone not in active_zone_names is skipped."""
        cover = _make_vertical_cover(distance=1.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = _make_glare_config()  # zone named "desk"
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"other_zone"},  # desk not active
        )
        result = self.handler.evaluate(snap)
        assert result is None

    def test_priority_is_45(self) -> None:
        assert GlareZoneHandler.priority == 45

    def test_name(self) -> None:
        assert GlareZoneHandler.name == "glare_zone"
