"""Tests for glare zone data model and geometry."""

import pytest
from math import cos, radians, sin, tan

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
    VerticalConfig,
)
from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    _glare_zone_effective_distance,
)


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
