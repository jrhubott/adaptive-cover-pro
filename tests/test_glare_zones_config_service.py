"""Tests for GlareZonesConfig building from config entry options.

Units: window_width and glare zone coordinates are metres.
"""

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_types import GlareZonesConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_WINDOW_DEPTH,
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
            CONF_WINDOW_WIDTH: 2.0,
            "glare_zone_1_name": "",
        }
        result = svc.get_glare_zones_config(options)
        assert result is None

    def test_builds_single_zone(self):
        """Builds a GlareZonesConfig with one zone."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 1.5,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 0.5,
            "glare_zone_1_y": 2.0,
            "glare_zone_1_radius": 0.3,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert isinstance(result, GlareZonesConfig)
        assert result.window_width == 1.5
        assert len(result.zones) == 1
        assert result.zones[0].name == "Desk"
        assert result.zones[0].x == 0.5
        assert result.zones[0].y == 2.0
        assert result.zones[0].radius == 0.3

    def test_skips_blank_zone_names(self):
        """Zones with blank names are skipped; named ones are included."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 2.0,
            "glare_zone_1_name": "Table",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 1.5,
            "glare_zone_1_radius": 0.6,
            "glare_zone_2_name": "",
            "glare_zone_3_name": "Bed",
            "glare_zone_3_x": -0.8,
            "glare_zone_3_y": 3.0,
            "glare_zone_3_radius": 0.5,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert len(result.zones) == 2
        names = [z.name for z in result.zones]
        assert "Table" in names
        assert "Bed" in names

    def test_window_width_defaults_to_one_metre(self):
        """window_width defaults to 1.0 m when not set."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 1.0,
            "glare_zone_1_radius": 0.2,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert result.window_width == 1.0


class TestGetVerticalData:
    """Test ConfigurationService.get_vertical_data — issue #174 regression."""

    def test_window_depth_none_defaults_to_zero(self):
        """window_depth=None (horizontal cover config) must not crash.

        config_flow stores CONF_WINDOW_DEPTH: None for horizontal covers because
        the horizontal schema never collects it. options.get(key, default) ignores
        the default when the key is present with value None, so we must use `or 0.0`.
        """
        svc = _make_service()
        options = {CONF_WINDOW_DEPTH: None}
        result = svc.get_vertical_data(options)
        assert result.window_depth == 0.0

    def test_window_depth_absent_defaults_to_zero(self):
        """window_depth absent from options defaults to 0.0."""
        svc = _make_service()
        result = svc.get_vertical_data({})
        assert result.window_depth == 0.0

    def test_window_depth_zero_is_preserved(self):
        """window_depth=0.0 is preserved (not treated as falsy to 0.0)."""
        svc = _make_service()
        result = svc.get_vertical_data({CONF_WINDOW_DEPTH: 0.0})
        assert result.window_depth == 0.0

    def test_window_depth_nonzero_is_preserved(self):
        """window_depth=0.3 is passed through correctly."""
        svc = _make_service()
        result = svc.get_vertical_data({CONF_WINDOW_DEPTH: 0.3})
        assert result.window_depth == 0.3
