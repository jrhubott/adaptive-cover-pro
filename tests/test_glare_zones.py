"""Tests for glare zone data model and geometry."""

import pytest
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
    VerticalConfig,
)
from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    _glare_zone_effective_distance,
)
from tests.cover_helpers import build_vertical_cover


class TestGlareZoneDataModel:
    """Test GlareZone and GlareZonesConfig dataclasses."""

    def test_glare_zone_fields(self):
        """GlareZone stores name, x, y, radius."""
        zone = GlareZone(name="Desk", x=50.0, y=200.0, radius=30.0)
        assert zone.name == "Desk"
        assert zone.x == 50.0
        assert zone.y == 200.0
        assert zone.radius == 30.0

    def test_glare_zones_config_fields(self):
        """GlareZonesConfig stores zones list and window_width."""
        zone = GlareZone(name="Table", x=0.0, y=150.0, radius=60.0)
        cfg = GlareZonesConfig(zones=[zone], window_width=120.0)
        assert len(cfg.zones) == 1
        assert cfg.window_width == 120.0

    def test_vertical_config_glare_zones_defaults_none(self):
        """VerticalConfig.glare_zones defaults to None."""
        vc = VerticalConfig(distance=0.5, h_win=2.0)
        assert vc.glare_zones is None

    def test_vertical_config_accepts_glare_zones(self):
        """VerticalConfig.glare_zones accepts a GlareZonesConfig."""
        zone = GlareZone(name="Couch", x=-80.0, y=300.0, radius=50.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        vc = VerticalConfig(distance=0.5, h_win=2.0, glare_zones=zones_cfg)
        assert vc.glare_zones is zones_cfg


class TestGlareZoneGeometry:
    """Test _glare_zone_effective_distance."""

    def test_zone_directly_in_front_gamma_zero(self):
        """Zone centred on window normal, gamma=0 → nearest_y = y - radius."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=30.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # nearest_y = 200 - 30*cos(0) = 170 cm → 1.70 m
        assert dist == pytest.approx(1.70, abs=1e-6)

    def test_zone_on_right_side_gamma_zero(self):
        """Zone offset to the right, gamma=0, centred ray still passes through window."""
        zone = GlareZone(name="Z", x=50.0, y=100.0, radius=0.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # x_at_window = 50 + 100*tan(0) = 50 < 150 → reachable; nearest_y = 100
        assert dist == pytest.approx(1.00, abs=1e-6)

    def test_zone_behind_window_wall_returns_none(self):
        """Zone with y ≤ radius (nearest_y ≤ 0) is behind the wall."""
        zone = GlareZone(name="Z", x=0.0, y=20.0, radius=30.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # nearest_y = 20 - 30 = -10 → None
        assert dist is None

    def test_zone_outside_window_width_returns_none(self):
        """Zone whose sun ray enters outside the window frame → None."""
        zone2 = GlareZone(name="Z2", x=200.0, y=100.0, radius=0.0)
        # x_at_window = 200 + 100*tan(0) = 200; window_half_width=50 → outside
        dist = _glare_zone_effective_distance(zone2, gamma=0.0, window_half_width=50.0)
        assert dist is None

    def test_zone_angled_sun_reachable(self):
        """Zone at centre, moderate gamma: check the nearest_y is correct."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=0.0)
        gamma = 30.0
        # nearest_x = 0; nearest_y = 200
        # x_at_window = 0 + 200*tan(30) ≈ 115.47; window_half_width=150 → reachable
        dist = _glare_zone_effective_distance(zone, gamma=gamma, window_half_width=150.0)
        assert dist == pytest.approx(2.00, abs=1e-6)

    def test_zone_outside_window_angle_returns_none(self):
        """Zone at (0, 200), gamma=30°, narrow window (half=50cm) → None."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=0.0)
        # x_at_window ≈ 115.47 > 50 → outside window
        dist = _glare_zone_effective_distance(zone, gamma=30.0, window_half_width=50.0)
        assert dist is None

    def test_returns_metres_not_cm(self):
        """Result is in metres (nearest_y / 100)."""
        zone = GlareZone(name="Z", x=0.0, y=300.0, radius=0.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=200.0)
        assert dist == pytest.approx(3.00, abs=1e-6)

    def test_zone_offset_negative_gamma(self):
        """Zone offset left, sun from right (negative gamma): ray misses narrow window."""
        # Zone at x=-100, y=200, r=0; gamma=-30 (sun from right)
        # nearest_x = -100 + 0*sin(-30) = -100
        # nearest_y = 200 - 0*cos(-30) = 200
        # x_at_window = -100 + 200*tan(-30) ≈ -100 - 115.47 = -215.47
        # abs(-215.47) > 150 → None (ray exits left of window)
        zone = GlareZone(name="Z", x=-100.0, y=200.0, radius=0.0)
        dist = _glare_zone_effective_distance(zone, gamma=-30.0, window_half_width=150.0)
        assert dist is None


class TestGlareZoneCalculation:
    """Test glare zone integration in AdaptiveVerticalCover.calculate_position()."""

    def _make_cover(self, glare_zones=None, active_zone_names=None, **kwargs):
        """Build a vertical cover with optional glare zone config."""
        logger = MagicMock()
        sun_data = MagicMock()
        cover = build_vertical_cover(
            logger=logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=sun_data,
            distance=0.5,
            h_win=2.0,
            glare_zones=glare_zones,
            **kwargs,
        )
        cover.active_zone_names = active_zone_names or set()
        return cover

    def test_no_zones_configured_unchanged(self):
        """With no glare zones, calculate_position() is identical to baseline."""
        cover_no_zones = self._make_cover(glare_zones=None)
        baseline = cover_no_zones.calculate_position()

        cover_empty = self._make_cover(
            glare_zones=GlareZonesConfig(zones=[], window_width=200.0)
        )
        result = cover_empty.calculate_position()
        assert result == pytest.approx(baseline, rel=1e-6)

    def test_active_zone_farther_than_base_extends_position(self):
        """Zone farther than base distance → higher blind position."""
        zone = GlareZone(name="Desk", x=0.0, y=200.0, radius=30.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Desk"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result > baseline

    def test_active_zone_closer_than_base_does_not_reduce(self):
        """Zone closer than base distance → position is still the base (max wins)."""
        zone = GlareZone(name="Near", x=0.0, y=30.0, radius=0.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Near"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)

    def test_inactive_zone_does_not_affect_position(self):
        """Zone present in config but not in active_zone_names → ignored."""
        zone = GlareZone(name="Desk", x=0.0, y=200.0, radius=30.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names=set())

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)

    def test_multiple_zones_max_wins(self):
        """Two active zones: position equals the farther one."""
        zone1 = GlareZone(name="Near", x=0.0, y=80.0, radius=0.0)
        zone2 = GlareZone(name="Far", x=0.0, y=250.0, radius=0.0)
        zones_cfg = GlareZonesConfig(zones=[zone1, zone2], window_width=200.0)
        cover = self._make_cover(
            glare_zones=zones_cfg,
            active_zone_names={"Near", "Far"},
        )
        cover_far_only = self._make_cover(
            glare_zones=GlareZonesConfig(zones=[zone2], window_width=200.0),
            active_zone_names={"Far"},
        )
        result = cover.calculate_position()
        far_result = cover_far_only.calculate_position()
        assert result == pytest.approx(far_result, rel=1e-6)

    def test_zone_unreachable_through_window_falls_back_to_base(self):
        """Zone whose sun ray exits the window frame → treated as if inactive."""
        zone = GlareZone(name="Corner", x=300.0, y=200.0, radius=0.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=10.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Corner"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)
