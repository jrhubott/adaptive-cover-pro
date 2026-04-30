"""Tests for duplicate and sync cover features."""

import pytest
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_flow import (
    SYNC_CATEGORIES,
    _SYNC_UI_CATEGORIES,
    ConfigFlowHandler,
    _extract_shared_options,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CUSTOM_POSITION_1,
    CONF_CUSTOM_POSITION_MIN_MODE_1,
    CONF_CUSTOM_POSITION_PRIORITY_1,
    CONF_CUSTOM_POSITION_SENSOR_1,
    CONF_DELTA_POSITION,
    CONF_DEVICE_ID,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_HEIGHT_WIN,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MIN_POSITION,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_STATE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
)


def _make_entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.options = options
    return entry


class TestExtractSharedOptions:
    """Tests for _extract_shared_options."""

    def test_excludes_entities(self):
        """Verify CONF_ENTITIES is not present in the returned dict."""
        entry = _make_entry({CONF_ENTITIES: ["cover.test"], CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_ENTITIES not in result

    def test_excludes_azimuth(self):
        """Verify CONF_AZIMUTH is not present in the returned dict."""
        entry = _make_entry({CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_AZIMUTH not in result

    def test_excludes_device_id(self):
        """Verify CONF_DEVICE_ID is not present in the returned dict."""
        entry = _make_entry({CONF_DEVICE_ID: "abc123", CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_DEVICE_ID not in result

    def test_includes_window_dimensions(self):
        """Verify window dimension options are included in the returned dict."""
        entry = _make_entry({CONF_HEIGHT_WIN: 2.1, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_HEIGHT_WIN] == 2.1

    def test_includes_automation_settings(self):
        """Verify automation settings are included in the returned dict."""
        entry = _make_entry({CONF_DELTA_POSITION: 5, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_DELTA_POSITION] == 5

    def test_includes_climate_mode(self):
        """Verify climate mode setting is included in the returned dict."""
        entry = _make_entry({CONF_CLIMATE_MODE: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_CLIMATE_MODE] is True

    def test_includes_position_limits(self):
        """Verify position limit options are included in the returned dict."""
        entry = _make_entry({CONF_MIN_POSITION: 10, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_MIN_POSITION] == 10

    def test_includes_motion_sensors(self):
        """Verify motion sensor options are included in the returned dict."""
        entry = _make_entry(
            {CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_AZIMUTH: 180}
        )
        result = _extract_shared_options(entry)
        assert result[CONF_MOTION_SENSORS] == ["binary_sensor.motion"]

    def test_includes_blind_spot(self):
        """Verify blind spot options are included in the returned dict."""
        entry = _make_entry({CONF_ENABLE_BLIND_SPOT: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_ENABLE_BLIND_SPOT] is True

    def test_empty_options_returns_empty(self):
        """Verify empty options dict returns empty result."""
        entry = _make_entry({})
        result = _extract_shared_options(entry)
        assert result == {}

    def test_only_excluded_fields_returns_empty(self):
        """Verify a dict containing only excluded fields returns empty."""
        entry = _make_entry(
            {
                CONF_ENTITIES: ["cover.test"],
                CONF_AZIMUTH: 180,
                CONF_DEVICE_ID: "abc",
            }
        )
        result = _extract_shared_options(entry)
        assert result == {}

    def test_returns_copy_not_reference(self):
        """Verify the returned dict is a copy, not a reference to entry.options."""
        options = {CONF_HEIGHT_WIN: 2.1}
        entry = _make_entry(options)
        result = _extract_shared_options(entry)
        result[CONF_HEIGHT_WIN] = 99.0
        assert entry.options[CONF_HEIGHT_WIN] == 2.1


class TestSyncCategorySplit:
    """Tests for the values/sensors split within mixed sync categories (issue #125)."""

    # --- light_cloud ---

    def test_light_cloud_values_excludes_entity_keys(self):
        """light_cloud_values must not overwrite per-room sensor assignments."""
        entry = _make_entry(
            {
                CONF_WEATHER_ENTITY: "weather.home",
                CONF_LUX_ENTITY: "sensor.lux",
                CONF_IRRADIANCE_ENTITY: "sensor.irradiance",
                CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
                CONF_WEATHER_STATE: ["sunny"],
                CONF_LUX_THRESHOLD: 500,
                CONF_IRRADIANCE_THRESHOLD: 800,
                CONF_CLOUD_COVERAGE_THRESHOLD: 50,
                CONF_CLOUD_SUPPRESSION: True,
            }
        )
        result = _extract_shared_options(entry, ["light_cloud_values"])
        assert CONF_WEATHER_ENTITY not in result
        assert CONF_LUX_ENTITY not in result
        assert CONF_IRRADIANCE_ENTITY not in result
        assert CONF_CLOUD_COVERAGE_ENTITY not in result
        assert result[CONF_IRRADIANCE_THRESHOLD] == 800
        assert result[CONF_LUX_THRESHOLD] == 500
        assert result[CONF_CLOUD_SUPPRESSION] is True

    def test_light_cloud_sensors_includes_only_entity_keys(self):
        """light_cloud_sensors must return only the entity_id fields."""
        entry = _make_entry(
            {
                CONF_WEATHER_ENTITY: "weather.home",
                CONF_LUX_ENTITY: "sensor.lux",
                CONF_IRRADIANCE_ENTITY: "sensor.irradiance",
                CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
                CONF_IRRADIANCE_THRESHOLD: 800,
                CONF_LUX_THRESHOLD: 500,
            }
        )
        result = _extract_shared_options(entry, ["light_cloud_sensors"])
        assert result[CONF_WEATHER_ENTITY] == "weather.home"
        assert result[CONF_LUX_ENTITY] == "sensor.lux"
        assert result[CONF_IRRADIANCE_ENTITY] == "sensor.irradiance"
        assert result[CONF_CLOUD_COVERAGE_ENTITY] == "sensor.cloud"
        assert CONF_IRRADIANCE_THRESHOLD not in result
        assert CONF_LUX_THRESHOLD not in result

    def test_light_cloud_legacy_key_returns_full_union(self):
        """Selecting old 'light_cloud' category must still return all keys (back-compat)."""
        entry = _make_entry(
            {
                CONF_WEATHER_ENTITY: "weather.home",
                CONF_IRRADIANCE_THRESHOLD: 800,
            }
        )
        result = _extract_shared_options(entry, ["light_cloud"])
        assert CONF_WEATHER_ENTITY in result
        assert CONF_IRRADIANCE_THRESHOLD in result

    # --- temperature_climate ---

    def test_temperature_climate_values_excludes_sensor_entities(self):
        """temperature_climate_values must not overwrite per-room sensor assignments."""
        entry = _make_entry(
            {
                CONF_TEMP_ENTITY: "sensor.bedroom_temp",
                CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp",
                CONF_PRESENCE_ENTITY: "binary_sensor.presence",
                CONF_CLIMATE_MODE: True,
                CONF_TEMP_LOW: 18.0,
                CONF_TEMP_HIGH: 24.0,
                CONF_OUTSIDE_THRESHOLD: 10.0,
                CONF_TRANSPARENT_BLIND: False,
            }
        )
        result = _extract_shared_options(entry, ["temperature_climate_values"])
        assert CONF_TEMP_ENTITY not in result
        assert CONF_OUTSIDETEMP_ENTITY not in result
        assert CONF_PRESENCE_ENTITY not in result
        assert result[CONF_CLIMATE_MODE] is True
        assert result[CONF_TEMP_LOW] == 18.0
        assert result[CONF_TEMP_HIGH] == 24.0

    def test_temperature_climate_sensors_includes_only_entity_keys(self):
        """temperature_climate_sensors must return only the room-specific entity fields."""
        entry = _make_entry(
            {
                CONF_TEMP_ENTITY: "sensor.bedroom_temp",
                CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp",
                CONF_PRESENCE_ENTITY: "binary_sensor.presence",
                CONF_TEMP_LOW: 18.0,
                CONF_TEMP_HIGH: 24.0,
            }
        )
        result = _extract_shared_options(entry, ["temperature_climate_sensors"])
        assert result[CONF_TEMP_ENTITY] == "sensor.bedroom_temp"
        assert result[CONF_OUTSIDETEMP_ENTITY] == "sensor.outside_temp"
        assert result[CONF_PRESENCE_ENTITY] == "binary_sensor.presence"
        assert CONF_TEMP_LOW not in result
        assert CONF_TEMP_HIGH not in result

    def test_temperature_climate_legacy_key_returns_full_union(self):
        """Selecting old 'temperature_climate' must still return all keys (back-compat)."""
        entry = _make_entry({CONF_TEMP_ENTITY: "sensor.temp", CONF_TEMP_LOW: 18.0})
        result = _extract_shared_options(entry, ["temperature_climate"])
        assert CONF_TEMP_ENTITY in result
        assert CONF_TEMP_LOW in result

    # --- motion_override ---

    def test_motion_override_values_excludes_sensor_list(self):
        """motion_override_values must not copy the per-room sensor list."""
        entry = _make_entry(
            {CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_MOTION_TIMEOUT: 300}
        )
        result = _extract_shared_options(entry, ["motion_override_values"])
        assert CONF_MOTION_SENSORS not in result
        assert result[CONF_MOTION_TIMEOUT] == 300

    def test_motion_override_sensors_excludes_timeout(self):
        """motion_override_sensors must return only the sensor list, not the timeout."""
        entry = _make_entry(
            {CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_MOTION_TIMEOUT: 300}
        )
        result = _extract_shared_options(entry, ["motion_override_sensors"])
        assert result[CONF_MOTION_SENSORS] == ["binary_sensor.motion"]
        assert CONF_MOTION_TIMEOUT not in result

    # --- force_override ---

    def test_force_override_values_excludes_sensor_list(self):
        """force_override_values must not copy the sensor list."""
        entry = _make_entry(
            {
                CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.force"],
                CONF_FORCE_OVERRIDE_POSITION: 50,
                CONF_FORCE_OVERRIDE_MIN_MODE: False,
            }
        )
        result = _extract_shared_options(entry, ["force_override_values"])
        assert CONF_FORCE_OVERRIDE_SENSORS not in result
        assert result[CONF_FORCE_OVERRIDE_POSITION] == 50

    def test_force_override_sensors_excludes_values(self):
        """force_override_sensors must return only the sensor list."""
        entry = _make_entry(
            {
                CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.force"],
                CONF_FORCE_OVERRIDE_POSITION: 50,
            }
        )
        result = _extract_shared_options(entry, ["force_override_sensors"])
        assert result[CONF_FORCE_OVERRIDE_SENSORS] == ["binary_sensor.force"]
        assert CONF_FORCE_OVERRIDE_POSITION not in result

    # --- custom_position ---

    def test_custom_position_values_excludes_trigger_sensors(self):
        """custom_position_values must not copy trigger sensor entity IDs."""
        entry = _make_entry(
            {
                CONF_CUSTOM_POSITION_SENSOR_1: "input_boolean.daytime",
                CONF_CUSTOM_POSITION_1: 30,
                CONF_CUSTOM_POSITION_PRIORITY_1: 77,
                CONF_CUSTOM_POSITION_MIN_MODE_1: False,
            }
        )
        result = _extract_shared_options(entry, ["custom_position_values"])
        assert CONF_CUSTOM_POSITION_SENSOR_1 not in result
        assert result[CONF_CUSTOM_POSITION_1] == 30
        assert result[CONF_CUSTOM_POSITION_PRIORITY_1] == 77

    def test_custom_position_sensors_excludes_values(self):
        """custom_position_sensors must return only trigger sensor entity IDs."""
        entry = _make_entry(
            {
                CONF_CUSTOM_POSITION_SENSOR_1: "input_boolean.daytime",
                CONF_CUSTOM_POSITION_1: 30,
                CONF_CUSTOM_POSITION_PRIORITY_1: 77,
            }
        )
        result = _extract_shared_options(entry, ["custom_position_sensors"])
        assert result[CONF_CUSTOM_POSITION_SENSOR_1] == "input_boolean.daytime"
        assert CONF_CUSTOM_POSITION_1 not in result
        assert CONF_CUSTOM_POSITION_PRIORITY_1 not in result

    # --- weather_override ---

    def test_weather_override_values_excludes_sensor_entities(self):
        """weather_override_values must not copy sensor entity assignments."""
        entry = _make_entry(
            {
                CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind_speed",
                CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
                CONF_WEATHER_WIND_SPEED_THRESHOLD: 10.0,
                CONF_WEATHER_RAIN_THRESHOLD: 0.5,
            }
        )
        result = _extract_shared_options(entry, ["weather_override_values"])
        assert CONF_WEATHER_WIND_SPEED_SENSOR not in result
        assert CONF_WEATHER_RAIN_SENSOR not in result
        assert result[CONF_WEATHER_WIND_SPEED_THRESHOLD] == 10.0

    def test_weather_override_sensors_excludes_thresholds(self):
        """weather_override_sensors must return only sensor entity assignments."""
        entry = _make_entry(
            {
                CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind_speed",
                CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
                CONF_WEATHER_WIND_SPEED_THRESHOLD: 10.0,
                CONF_WEATHER_RAIN_THRESHOLD: 0.5,
            }
        )
        result = _extract_shared_options(entry, ["weather_override_sensors"])
        assert result[CONF_WEATHER_WIND_SPEED_SENSOR] == "sensor.wind_speed"
        assert result[CONF_WEATHER_RAIN_SENSOR] == "sensor.rain"
        assert CONF_WEATHER_WIND_SPEED_THRESHOLD not in result

    # --- UI category list ---

    def test_ui_categories_contains_split_keys(self):
        """_SYNC_UI_CATEGORIES must expose all 12 new split sub-category keys."""
        expected_split_keys = {
            "light_cloud_values",
            "light_cloud_sensors",
            "temperature_climate_values",
            "temperature_climate_sensors",
            "motion_override_values",
            "motion_override_sensors",
            "force_override_values",
            "force_override_sensors",
            "custom_position_values",
            "custom_position_sensors",
            "weather_override_values",
            "weather_override_sensors",
        }
        assert expected_split_keys.issubset(set(_SYNC_UI_CATEGORIES))

    def test_ui_categories_excludes_original_mixed_keys(self):
        """Mixed categories must not appear in _SYNC_UI_CATEGORIES; only split keys shown."""
        mixed_keys = {
            "light_cloud",
            "temperature_climate",
            "motion_override",
            "force_override",
            "custom_position",
            "weather_override",
        }
        for key in mixed_keys:
            assert (
                key not in _SYNC_UI_CATEGORIES
            ), f"'{key}' should not be in _SYNC_UI_CATEGORIES — use split *_values/*_sensors keys instead"

    def test_sync_categories_still_has_legacy_mixed_keys(self):
        """SYNC_CATEGORIES must retain the original mixed keys for backward compat."""
        for key in (
            "light_cloud",
            "temperature_climate",
            "motion_override",
            "force_override",
            "custom_position",
            "weather_override",
        ):
            assert (
                key in SYNC_CATEGORIES
            ), f"Legacy key '{key}' missing from SYNC_CATEGORIES"


class TestEnsureUniqueName:
    """Tests for _ensure_unique_name with suffix support."""

    def _make_handler_with_names(self, existing_names: list[str]) -> ConfigFlowHandler:
        """Create a ConfigFlowHandler mock with given existing entry names."""
        handler = ConfigFlowHandler.__new__(ConfigFlowHandler)
        entries = []
        for name in existing_names:
            e = MagicMock()
            e.data = {"name": name}
            entries.append(e)
        handler.hass = MagicMock()
        handler.hass.config_entries.async_entries.return_value = entries
        return handler

    @pytest.mark.asyncio
    async def test_unique_name_returned_unchanged(self):
        """Verify a name with no conflict is returned as-is."""
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Bedroom")
        assert result == "Bedroom"

    @pytest.mark.asyncio
    async def test_default_suffix_is_imported(self):
        """Verify the default suffix is 'Imported' for backward compatibility."""
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Living Room")
        assert result == "Living Room (Imported)"

    @pytest.mark.asyncio
    async def test_copy_suffix(self):
        """Verify 'Copy' suffix is used when explicitly passed."""
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy)"

    @pytest.mark.asyncio
    async def test_copy_suffix_increments(self):
        """Verify suffix increments to 2 when first suffixed name also conflicts."""
        handler = self._make_handler_with_names(["Living Room", "Living Room (Copy)"])
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy 2)"

    @pytest.mark.asyncio
    async def test_copy_suffix_increments_further(self):
        """Verify suffix increments to 3 when Copy and Copy 2 both conflict."""
        handler = self._make_handler_with_names(
            ["Living Room", "Living Room (Copy)", "Living Room (Copy 2)"]
        )
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy 3)"
