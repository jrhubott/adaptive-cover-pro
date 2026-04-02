"""Tests for GlareZonesConfig building from config entry options."""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import GlareZone, GlareZonesConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_WINDOW_WIDTH,
)
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)


def _make_service():
    """Create a ConfigurationService with mocked HA dependencies."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Cover"}
    logger = MagicMock()
    return ConfigurationService(
        hass=hass,
        config_entry=config_entry,
        logger=logger,
        cover_type="cover_blind",
        temp_toggle=False,
        lux_toggle=False,
        irradiance_toggle=False,
    )


class TestGetGlareZonesConfig:
    """Test ConfigurationService.get_glare_zones_config."""

    def test_returns_none_when_disabled(self):
        """Returns None when CONF_ENABLE_GLARE_ZONES is False."""
        svc = _make_service()
        options = {CONF_ENABLE_GLARE_ZONES: False}
        result = svc.get_glare_zones_config(options)
        assert result is None

    def test_returns_none_when_missing(self):
        """Returns None when CONF_ENABLE_GLARE_ZONES not in options."""
        svc = _make_service()
        result = svc.get_glare_zones_config({})
        assert result is None

    def test_returns_none_when_no_named_zones(self):
        """Returns None when all zone names are blank."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 200.0,
            "glare_zone_1_name": "",
        }
        result = svc.get_glare_zones_config(options)
        assert result is None

    def test_builds_single_zone(self):
        """Builds a GlareZonesConfig with one zone."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 150.0,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 50.0,
            "glare_zone_1_y": 200.0,
            "glare_zone_1_radius": 30.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert isinstance(result, GlareZonesConfig)
        assert result.window_width == 150.0
        assert len(result.zones) == 1
        assert result.zones[0].name == "Desk"
        assert result.zones[0].x == 50.0
        assert result.zones[0].y == 200.0
        assert result.zones[0].radius == 30.0

    def test_skips_blank_zone_names(self):
        """Zones with blank names are skipped; named ones are included."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 200.0,
            "glare_zone_1_name": "Table",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 150.0,
            "glare_zone_1_radius": 60.0,
            "glare_zone_2_name": "",
            "glare_zone_3_name": "Bed",
            "glare_zone_3_x": -80.0,
            "glare_zone_3_y": 300.0,
            "glare_zone_3_radius": 50.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert len(result.zones) == 2
        names = [z.name for z in result.zones]
        assert "Table" in names
        assert "Bed" in names

    def test_window_width_defaults_to_100(self):
        """window_width defaults to 100cm when not set."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 100.0,
            "glare_zone_1_radius": 20.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert result.window_width == 100.0
