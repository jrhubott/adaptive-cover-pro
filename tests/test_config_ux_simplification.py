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


import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.config_flow import (
    LIGHT_CLOUD_SCHEMA,
    POSITION_SCHEMA,
    SYNC_CATEGORIES,
    TEMPERATURE_CLIMATE_SCHEMA,
    WEATHER_OVERRIDE_SCHEMA,
    _build_config_summary,
    _build_custom_position_schema_dict,
    _CUSTOM_POSITION_OPTIONAL_KEYS,
    _extract_shared_options,
    _LIGHT_CLOUD_OPTIONAL_KEYS,
    _POSITION_OPTIONAL_KEYS,
    _SYNC_UI_CATEGORIES,
    _TEMPERATURE_CLIMATE_OPTIONAL_KEYS,
    _WEATHER_OVERRIDE_OPTIONAL_KEYS,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_ENTITIES,
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
    CoverType,
)

# ---------------------------------------------------------------------------
# Split schemas: LIGHT_CLOUD_SCHEMA and TEMPERATURE_CLIMATE_SCHEMA
#
# (The quick/full setup-mode selection was removed with the minimal create
# wizard — #945 Part 2 — so its tests no longer apply. The split schemas below
# are still used by the options flow.)
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

    def test_light_cloud_master_toggle_is_first(self):
        """Master toggle and its companion cloudy_position must render at the top
        of the screen so users see the on/off and target before any sensor field.

        Regression guard for #364 — reporter wasted an hour configuring sensors
        before noticing the master toggle was disabled at the bottom of the screen.
        """
        keys = [str(k) for k in LIGHT_CLOUD_SCHEMA.schema]
        assert keys[0] == CONF_CLOUD_SUPPRESSION
        assert keys[1] == CONF_CLOUDY_POSITION

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

    def test_presence_entity_selector_allows_seven_domains(self):
        """Presence entity selector must accept device_tracker, person, zone,
        binary_sensor, input_boolean, switch, and schedule.

        Regression guard for #313 — PR #287 silently narrowed this list.
        Updated for #318 — switch and schedule added to match motion selector.
        """
        for key, value in TEMPERATURE_CLIMATE_SCHEMA.schema.items():
            if str(key) == "presence_entity":
                domain = value.config["domain"]
                assert set(domain) == {
                    "device_tracker",
                    "person",
                    "zone",
                    "binary_sensor",
                    "input_boolean",
                    "switch",
                    "schedule",
                }
                return
        raise AssertionError("presence_entity not found in schema")

    def test_temperature_climate_no_lux(self):
        """TEMPERATURE_CLIMATE_SCHEMA should NOT contain lux settings."""
        keys = [str(k) for k in TEMPERATURE_CLIMATE_SCHEMA.schema]
        assert "lux_entity" not in keys


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
        assert (
            legacy_keys <= ui_keys
        ), f"Keys in legacy but not in UI categories: {legacy_keys - ui_keys}"


# ---------------------------------------------------------------------------
# Position map in summary
# ---------------------------------------------------------------------------


class TestPositionMapInSummary:
    """Rule targets and positions now live under How It Decides (Position Map removed)."""

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
        """Position Map section removed; How It Decides now carries per-rule targets."""
        config = self._base_config()
        result = _build_config_summary(config, CoverType.BLIND)
        assert "**Position Map**" not in result
        assert "**How It Decides**" in result

    def test_position_map_shows_default(self):
        """Default-fallback line renders under How It Decides."""
        config = self._base_config(**{CONF_DEFAULT_HEIGHT: 60})
        result = _build_config_summary(config, CoverType.BLIND)
        assert "60%" in result
        assert "🌙 Default" in result

    def test_position_map_shows_sunset(self):
        """Sunset row renders under How It Decides when configured."""
        config = self._base_config(**{CONF_SUNSET_POS: 0})
        result = _build_config_summary(config, CoverType.BLIND)
        assert "After sunset" in result

    def test_position_map_shows_safety_custom_position(self):
        """A priority-100 custom slot row renders under How It Decides (#563)."""
        config = self._base_config(
            **{
                "custom_position_sensors_5": ["binary_sensor.rain"],
                "custom_position_5": 100,
                "custom_position_priority_5": 100,
            }
        )
        result = _build_config_summary(config, CoverType.BLIND)
        assert "Custom #5" in result
        assert "100%" in result

    def test_position_map_shows_weather_override(self):
        """Weather safety row renders under How It Decides."""
        config = self._base_config(
            **{
                CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
                CONF_WEATHER_OVERRIDE_POSITION: 0,
            }
        )
        result = _build_config_summary(config, CoverType.BLIND)
        assert "Weather safety" in result

    def test_position_map_shows_sun_tracking(self):
        """Sun tracking row renders under How It Decides."""
        config = self._base_config()
        result = _build_config_summary(config, CoverType.BLIND)
        assert "Tracks the sun" in result

    def test_position_map_shows_clamp_range(self):
        """Position Limits section shows the clamp range when min/max differ from defaults."""
        config = self._base_config(**{CONF_MIN_POSITION: 10, CONF_MAX_POSITION: 90})
        result = _build_config_summary(config, CoverType.BLIND)
        assert "10%" in result
        assert "90%" in result

    def test_position_map_no_clamp_at_defaults(self):
        """Position Limits omits clamp qualifier when min=0 and max=100."""
        config = self._base_config(**{CONF_MIN_POSITION: 0, CONF_MAX_POSITION: 100})
        result = _build_config_summary(config, CoverType.BLIND)
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


# ---------------------------------------------------------------------------
# Selector domain widening (#318)
# ---------------------------------------------------------------------------

_BINARY_ON_EXPECTED = {"binary_sensor", "input_boolean", "switch", "schedule"}
_PRESENCE_LIKE_EXPECTED = _BINARY_ON_EXPECTED | {"device_tracker", "person", "zone"}
_NUMERIC_EXPECTED = {"sensor", "input_number", "number"}


def _domain_for(schema, key_name: str) -> set[str]:
    """Return the domain set for a named key in a vol.Schema."""
    for key, value in schema.schema.items():
        if str(key) == key_name:
            return set(value.config["domain"])
    raise AssertionError(f"{key_name!r} not found in schema")


class TestSelectorDomains:
    """Regression guards for entity selector domain widening (#318)."""

    # --- FORCE_OVERRIDE_SCHEMA ---

    # --- CUSTOM_POSITION_SCHEMA ---

    def test_custom_position_sensors_binary_on_domains(self):
        """All five custom_position_sensors_N multi-selectors accept binary_on domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            CUSTOM_POSITION_SCHEMA,
        )
        from custom_components.adaptive_cover_pro.const import (
            CUSTOM_POSITION_SLOT_NUMBERS,
        )

        for n in CUSTOM_POSITION_SLOT_NUMBERS:
            assert (
                _domain_for(CUSTOM_POSITION_SCHEMA, f"custom_position_sensors_{n}")
                == _BINARY_ON_EXPECTED
            ), f"custom_position_sensors_{n} domain mismatch"

    # --- MOTION_OVERRIDE_SCHEMA ---

    def test_motion_sensors_presence_like_domains(self):
        """motion_sensors accepts device_tracker/person/zone plus binary_on domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            MOTION_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(MOTION_OVERRIDE_SCHEMA, "motion_sensors")
            == _PRESENCE_LIKE_EXPECTED
        )

    def test_motion_media_players_media_player_domain(self):
        """motion_media_players is restricted to the media_player domain."""
        from custom_components.adaptive_cover_pro.config_flow import (
            MOTION_OVERRIDE_SCHEMA,
        )

        assert _domain_for(MOTION_OVERRIDE_SCHEMA, "motion_media_players") == {
            "media_player"
        }

    # --- WEATHER_OVERRIDE_SCHEMA ---

    def test_weather_wind_speed_numeric_domains(self):
        """weather_wind_speed_sensor accepts numeric domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_wind_speed_sensor")
            == _NUMERIC_EXPECTED
        )

    def test_weather_wind_direction_numeric_domains(self):
        """weather_wind_direction_sensor accepts numeric domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_wind_direction_sensor")
            == _NUMERIC_EXPECTED
        )

    def test_weather_rain_sensor_numeric_domains(self):
        """weather_rain_sensor accepts numeric domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_rain_sensor")
            == _NUMERIC_EXPECTED
        )

    def test_weather_is_raining_binary_on_domains(self):
        """weather_is_raining_sensor accepts binary_on domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_is_raining_sensor")
            == _BINARY_ON_EXPECTED
        )

    def test_weather_is_windy_binary_on_domains(self):
        """weather_is_windy_sensor accepts binary_on domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_is_windy_sensor")
            == _BINARY_ON_EXPECTED
        )

    def test_weather_severe_sensors_binary_on_domains(self):
        """weather_severe_sensors accepts binary_on domains."""
        from custom_components.adaptive_cover_pro.config_flow import (
            WEATHER_OVERRIDE_SCHEMA,
        )

        assert (
            _domain_for(WEATHER_OVERRIDE_SCHEMA, "weather_severe_sensors")
            == _BINARY_ON_EXPECTED
        )

    # --- LIGHT_CLOUD_SCHEMA ---

    def test_lux_entity_numeric_domains_with_device_class(self):
        """lux_entity accepts numeric domains and filters by device_class=illuminance."""
        from custom_components.adaptive_cover_pro.config_flow import LIGHT_CLOUD_SCHEMA

        for key, value in LIGHT_CLOUD_SCHEMA.schema.items():
            if str(key) == "lux_entity":
                assert set(value.config["domain"]) == _NUMERIC_EXPECTED
                # EntityFilterSelectorConfig stores device_class as a list
                assert "illuminance" in value.config.get("device_class", [])
                return
        raise AssertionError("lux_entity not found in LIGHT_CLOUD_SCHEMA")

    def test_irradiance_entity_numeric_domains_with_device_class(self):
        """irradiance_entity accepts numeric domains and filters by device_class=irradiance."""
        from custom_components.adaptive_cover_pro.config_flow import LIGHT_CLOUD_SCHEMA

        for key, value in LIGHT_CLOUD_SCHEMA.schema.items():
            if str(key) == "irradiance_entity":
                assert set(value.config["domain"]) == _NUMERIC_EXPECTED
                # EntityFilterSelectorConfig stores device_class as a list
                assert "irradiance" in value.config.get("device_class", [])
                return
        raise AssertionError("irradiance_entity not found in LIGHT_CLOUD_SCHEMA")

    def test_cloud_coverage_entity_numeric_domains(self):
        """cloud_coverage_entity accepts numeric domains."""
        from custom_components.adaptive_cover_pro.config_flow import LIGHT_CLOUD_SCHEMA

        assert (
            _domain_for(LIGHT_CLOUD_SCHEMA, "cloud_coverage_entity")
            == _NUMERIC_EXPECTED
        )

    # --- TEMPERATURE_CLIMATE_SCHEMA ---

    def test_outsidetemp_entity_numeric_domains(self):
        """outside_temp (outsidetemp_entity) accepts numeric domains."""
        assert (
            _domain_for(TEMPERATURE_CLIMATE_SCHEMA, "outside_temp") == _NUMERIC_EXPECTED
        )


# ---------------------------------------------------------------------------
# Schema-walking guard: _*_OPTIONAL_KEYS must exactly match the vol.Optional
# keys with default=vol.UNDEFINED in their paired schema. Catches the #323 /
# #377 bug class (key added to schema with no default but not added to the
# constant — value silently survives a user clear).
# ---------------------------------------------------------------------------


_SCHEMA_OPTIONAL_KEY_PAIRS = [
    ("POSITION", POSITION_SCHEMA, _POSITION_OPTIONAL_KEYS),
    ("WEATHER_OVERRIDE", WEATHER_OVERRIDE_SCHEMA, _WEATHER_OVERRIDE_OPTIONAL_KEYS),
    ("LIGHT_CLOUD", LIGHT_CLOUD_SCHEMA, _LIGHT_CLOUD_OPTIONAL_KEYS),
    (
        "TEMPERATURE_CLIMATE",
        TEMPERATURE_CLIMATE_SCHEMA,
        _TEMPERATURE_CLIMATE_OPTIONAL_KEYS,
    ),
    # CUSTOM_POSITION's constant covers the venetian-augmented variant (with
    # tilt fields), not the bare module-level schema — build that variant for
    # the guard.
    (
        "CUSTOM_POSITION",
        vol.Schema(_build_custom_position_schema_dict(sensor_type="cover_venetian")),
        _CUSTOM_POSITION_OPTIONAL_KEYS,
    ),
]


def _schema_undefined_optional_keys(schema: vol.Schema) -> set[str]:
    """Return string keys of every vol.Optional marker whose default is vol.UNDEFINED.

    Covers both vol.Optional(KEY, default=vol.UNDEFINED) and bare
    vol.Optional(KEY) — the latter's default is also vol.UNDEFINED.
    """
    return {
        str(key)
        for key in schema.schema
        if isinstance(key, vol.Optional) and key.default is vol.UNDEFINED
    }


class TestOptionalKeyConstants:
    """_*_OPTIONAL_KEYS must exactly equal the set of vol.Optional keys whose
    default is vol.UNDEFINED in the paired schema.
    """

    @pytest.mark.parametrize("label,schema,constant", _SCHEMA_OPTIONAL_KEY_PAIRS)
    def test_optional_keys_match_schema(self, label, schema, constant):
        from_schema = _schema_undefined_optional_keys(schema)
        from_constant = set(constant)
        missing = from_schema - from_constant
        extra = from_constant - from_schema
        assert not missing, (
            f"{label}: schema has UNDEFINED-default keys missing from constant: "
            f"{sorted(missing)}"
        )
        assert not extra, (
            f"{label}: constant has keys that are not UNDEFINED in schema "
            f"(stale): {sorted(extra)}"
        )
