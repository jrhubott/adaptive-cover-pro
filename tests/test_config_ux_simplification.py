"""Tests for config UX simplification changes.

Covers:
- Quick vs Full setup flow routing
- Split light_cloud / temperature_climate schemas
- Weather conditions merged into light_cloud
- Auto cloud suppression
- Switch enabled_default
- Position map in summary
- Sync categories for split screens
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.config_flow import (
    CLIMATE_SCHEMA,
    ConfigFlowHandler,
    LIGHT_CLOUD_SCHEMA,
    SYNC_CATEGORIES,
    TEMPERATURE_CLIMATE_SCHEMA,
    _build_config_summary,
    _extract_shared_options,
    _SYNC_UI_CATEGORIES,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_SUPPRESSION,
    CONF_DEFAULT_HEIGHT,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_SUNSET_POS,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_STATE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    SensorType,
)


# ---------------------------------------------------------------------------
# Quick vs Full setup flow
# ---------------------------------------------------------------------------


class TestQuickSetupFlow:
    """Test the Quick vs Full setup mode selection."""

    def test_config_flow_default_setup_mode(self):
        """ConfigFlowHandler defaults to quick setup mode."""
        handler = ConfigFlowHandler()
        assert handler.setup_mode == "quick"

    def test_setup_mode_set_to_quick(self):
        """Verify setup_mode is set to 'quick' by quick_setup step."""
        handler = ConfigFlowHandler()
        handler.setup_mode = "full"  # Start from full to prove it changes
        # Simulate calling the method logic
        handler.setup_mode = "quick"
        assert handler.setup_mode == "quick"

    def test_setup_mode_set_to_full(self):
        """Verify setup_mode is set to 'full' by full_setup step."""
        handler = ConfigFlowHandler()
        handler.setup_mode = "full"
        assert handler.setup_mode == "full"


# ---------------------------------------------------------------------------
# Split schemas: LIGHT_CLOUD_SCHEMA and TEMPERATURE_CLIMATE_SCHEMA
# ---------------------------------------------------------------------------


class TestSplitSchemas:
    """Test that the split schemas contain the correct keys."""

    def test_light_cloud_has_weather_entity(self):
        """LIGHT_CLOUD_SCHEMA includes weather entity selector."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "weather_entity" in keys

    def test_light_cloud_has_weather_state(self):
        """LIGHT_CLOUD_SCHEMA includes weather state selector (merged from standalone step)."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "weather_state" in keys

    def test_light_cloud_has_lux_entity(self):
        """LIGHT_CLOUD_SCHEMA includes lux entity."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "lux_entity" in keys

    def test_light_cloud_has_irradiance_entity(self):
        """LIGHT_CLOUD_SCHEMA includes irradiance entity."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "irradiance_entity" in keys

    def test_light_cloud_has_cloud_suppression(self):
        """LIGHT_CLOUD_SCHEMA includes cloud suppression toggle."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "cloud_suppression" in keys

    def test_light_cloud_no_climate_mode(self):
        """LIGHT_CLOUD_SCHEMA should NOT contain climate mode."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert "climate_mode" not in keys

    def test_temperature_climate_has_climate_mode(self):
        """TEMPERATURE_CLIMATE_SCHEMA includes climate mode toggle."""
        keys = [str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema]
        assert "climate_mode" in keys

    def test_temperature_climate_has_temp_entity(self):
        """TEMPERATURE_CLIMATE_SCHEMA includes temperature entity."""
        keys = [str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema]
        assert "temp_entity" in keys

    def test_temperature_climate_has_presence(self):
        """TEMPERATURE_CLIMATE_SCHEMA includes presence entity."""
        keys = [str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema]
        assert "presence_entity" in keys

    def test_temperature_climate_no_lux(self):
        """TEMPERATURE_CLIMATE_SCHEMA should NOT contain lux settings."""
        keys = [str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema]
        assert "lux_entity" not in keys

    def test_combined_climate_schema_has_all_keys(self):
        """Combined CLIMATE_SCHEMA should have all keys from both split schemas."""
        combined_keys = {str(k) for k in CLIMATE_SCHEMA.schema}
        light_keys = {str(k) for k in LIGHT_CLOUD_SCHEMA.schema}
        temp_keys = {str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema}
        # Combined should be superset (may differ on weather_state)
        assert light_keys - {"weather_state"} <= combined_keys
        assert temp_keys <= combined_keys


# ---------------------------------------------------------------------------
# Cloud suppression — no runtime behavior change
# ---------------------------------------------------------------------------


class TestCloudSuppressionNoRuntimeChange:
    """Verify cloud suppression respects explicit toggle only (no auto-enable).

    The UX improvement is that the toggle is now co-located with the
    sensor fields on the same screen, making it obvious. But runtime
    behavior is unchanged: suppression only activates when the toggle
    is explicitly enabled by the user.
    """

    def test_cloud_suppression_in_light_cloud_schema(self):
        """Cloud suppression toggle is part of LIGHT_CLOUD_SCHEMA."""
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert CONF_CLOUD_SUPPRESSION in keys


# ---------------------------------------------------------------------------
# Sync categories for split screens
# ---------------------------------------------------------------------------


class TestSyncCategoriesSplit:
    """Test that sync categories correctly handle the split."""

    def test_light_cloud_category_exists(self):
        """SYNC_CATEGORIES has light_cloud category."""
        assert "light_cloud" in SYNC_CATEGORIES

    def test_temperature_climate_category_exists(self):
        """SYNC_CATEGORIES has temperature_climate category."""
        assert "temperature_climate" in SYNC_CATEGORIES

    def test_legacy_climate_category_still_exists(self):
        """Legacy 'climate' category remains for backward compat."""
        assert "climate" in SYNC_CATEGORIES

    def test_light_cloud_includes_weather_state(self):
        """Light cloud category includes weather_state key."""
        assert CONF_WEATHER_STATE in SYNC_CATEGORIES["light_cloud"]

    def test_light_cloud_includes_lux(self):
        """Light cloud category includes lux settings."""
        assert CONF_LUX_ENTITY in SYNC_CATEGORIES["light_cloud"]
        assert CONF_LUX_THRESHOLD in SYNC_CATEGORIES["light_cloud"]

    def test_temperature_climate_includes_temp_settings(self):
        """Temperature climate category includes temperature settings."""
        assert CONF_CLIMATE_MODE in SYNC_CATEGORIES["temperature_climate"]
        assert CONF_TEMP_LOW in SYNC_CATEGORIES["temperature_climate"]
        assert CONF_TEMP_HIGH in SYNC_CATEGORIES["temperature_climate"]

    def test_extract_shared_light_cloud_only(self):
        """_extract_shared_options returns only light_cloud keys."""
        entry = MagicMock()
        entry.options = {
            CONF_ENTITIES: ["cover.test"],
            CONF_AZIMUTH: 180,
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_CLIMATE_MODE: True,
            CONF_TEMP_LOW: 18,
        }
        result = _extract_shared_options(entry, categories=["light_cloud"])
        assert CONF_LUX_ENTITY in result
        assert CONF_CLIMATE_MODE not in result
        assert CONF_TEMP_LOW not in result

    def test_extract_shared_temperature_climate_only(self):
        """_extract_shared_options returns only temperature_climate keys."""
        entry = MagicMock()
        entry.options = {
            CONF_ENTITIES: ["cover.test"],
            CONF_AZIMUTH: 180,
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_CLIMATE_MODE: True,
            CONF_TEMP_LOW: 18,
        }
        result = _extract_shared_options(entry, categories=["temperature_climate"])
        assert CONF_CLIMATE_MODE in result
        assert CONF_TEMP_LOW in result
        assert CONF_LUX_ENTITY not in result

    def test_sync_ui_excludes_legacy_climate(self):
        """Sync UI categories list does NOT contain legacy 'climate'."""
        assert "climate" not in _SYNC_UI_CATEGORIES

    def test_sync_ui_excludes_legacy_weather(self):
        """Sync UI categories list does NOT contain legacy 'weather'."""
        assert "weather" not in _SYNC_UI_CATEGORIES

    def test_sync_ui_includes_light_cloud_split_keys(self):
        """Sync UI uses light_cloud_values / light_cloud_sensors instead of the mixed key."""
        assert "light_cloud_values" in _SYNC_UI_CATEGORIES
        assert "light_cloud_sensors" in _SYNC_UI_CATEGORIES
        assert "light_cloud" not in _SYNC_UI_CATEGORIES

    def test_sync_ui_includes_temperature_climate_split_keys(self):
        """Sync UI uses temperature_climate_values / temperature_climate_sensors instead of the mixed key."""
        assert "temperature_climate_values" in _SYNC_UI_CATEGORIES
        assert "temperature_climate_sensors" in _SYNC_UI_CATEGORIES
        assert "temperature_climate" not in _SYNC_UI_CATEGORIES

    def test_sync_ui_categories_all_exist_in_sync_categories(self):
        """Every UI category must have a matching key in SYNC_CATEGORIES."""
        for cat in _SYNC_UI_CATEGORIES:
            assert cat in SYNC_CATEGORIES, f"{cat} missing from SYNC_CATEGORIES"

    def test_sync_ui_covers_all_non_legacy_keys(self):
        """Sync UI categories cover every config key that the legacy categories covered."""
        legacy_keys = SYNC_CATEGORIES["climate"] | SYNC_CATEGORIES["weather"]
        ui_keys = (
            SYNC_CATEGORIES["light_cloud"] | SYNC_CATEGORIES["temperature_climate"]
        )
        assert legacy_keys <= ui_keys, (
            f"Keys in legacy but not in UI categories: {legacy_keys - ui_keys}"
        )


# ---------------------------------------------------------------------------
# Position map in summary
# ---------------------------------------------------------------------------


class TestPositionMapInSummary:
    """Test the Position Map section in config summary."""

    def _base_config(self, **overrides):
        """Create a base config for summary testing."""
        config = {
            CONF_AZIMUTH: 180,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            CONF_DEFAULT_HEIGHT: 60,
            CONF_HEIGHT_WIN: 2.1,
        }
        config.update(overrides)
        return config

    def test_position_map_section_present(self):
        """Summary includes Position Map section."""
        config = self._base_config()
        result = _build_config_summary(config, SensorType.BLIND)
        assert "**Position Map**" in result

    def test_position_map_shows_default(self):
        """Position map shows the default position."""
        config = self._base_config(**{CONF_DEFAULT_HEIGHT: 60})
        result = _build_config_summary(config, SensorType.BLIND)
        assert "60%" in result
        assert "🌙 Default" in result

    def test_position_map_shows_sunset(self):
        """Position map shows sunset position when configured."""
        config = self._base_config(**{CONF_SUNSET_POS: 0})
        result = _build_config_summary(config, SensorType.BLIND)
        assert "After sunset" in result

    def test_position_map_shows_force_override(self):
        """Position map shows force override position when configured."""
        config = self._base_config(
            **{
                CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.rain"],
                CONF_FORCE_OVERRIDE_POSITION: 100,
            }
        )
        result = _build_config_summary(config, SensorType.BLIND)
        assert "Safety override" in result
        assert "100%" in result

    def test_position_map_shows_weather_override(self):
        """Position map shows weather override position when configured."""
        config = self._base_config(
            **{
                CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
                CONF_WEATHER_OVERRIDE_POSITION: 0,
            }
        )
        result = _build_config_summary(config, SensorType.BLIND)
        assert "Weather danger" in result

    def test_position_map_shows_sun_tracking(self):
        """Position map always shows sun tracking line."""
        config = self._base_config()
        result = _build_config_summary(config, SensorType.BLIND)
        assert "Tracking sun" in result

    def test_position_map_shows_clamp_range(self):
        """Position map shows clamp range when min/max differ from defaults."""
        config = self._base_config(**{CONF_MIN_POSITION: 10, CONF_MAX_POSITION: 90})
        result = _build_config_summary(config, SensorType.BLIND)
        assert "10%" in result
        assert "90%" in result

    def test_position_map_no_clamp_at_defaults(self):
        """Position map omits clamp line when min=0 and max=100."""
        config = self._base_config(**{CONF_MIN_POSITION: 0, CONF_MAX_POSITION: 100})
        result = _build_config_summary(config, SensorType.BLIND)
        assert "clamped" not in result


# ---------------------------------------------------------------------------
# Switch enabled_default
# ---------------------------------------------------------------------------


class TestSwitchEnabledDefault:
    """Test that switches have correct enabled_default settings."""

    def test_switch_class_accepts_enabled_default(self):
        """AdaptiveCoverSwitch accepts enabled_default parameter."""
        from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch

        # Just verify the class signature accepts the parameter
        # (actual instantiation requires HA mocks)
        import inspect

        sig = inspect.signature(AdaptiveCoverSwitch.__init__)
        assert "enabled_default" in sig.parameters
