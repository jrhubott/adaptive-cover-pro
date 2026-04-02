"""Tests for glare zone data model and geometry."""

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
    VerticalConfig,
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
