"""Tests for the `enabled_handlers` attribute on the decision_trace sensor.

The card's decision-strip reads this attribute as the sole source of truth for
which pipeline handlers to render. Handlers not in the list are hidden by the
card. The attribute is configuration-driven — it answers "is this handler
configured to ever fire?", not "did it fire this tick?".
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_SUPPRESSION,
    CONF_CUSTOM_POSITION_1,
    CONF_CUSTOM_POSITION_SENSOR_1,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_SUN_TRACKING,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
    SensorType,
)
from custom_components.adaptive_cover_pro.sensor import AdaptiveCoverDecisionTraceSensor


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry(options: dict | None = None):
    entry = MagicMock()
    entry.entry_id = "test_enabled_handlers_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: SensorType.BLIND}
    entry.options = options or {}
    return entry


def _make_coordinator():
    coord = MagicMock()
    coord.data = None
    coord._pipeline_result = None
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    coord.check_adaptive_time = True
    return coord


def _make_sensor(options: dict | None = None) -> AdaptiveCoverDecisionTraceSensor:
    return AdaptiveCoverDecisionTraceSensor(
        "test_enabled_handlers_entry",
        _make_hass(),
        _make_config_entry(options),
        "Test",
        _make_coordinator(),
    )


def _enabled(options: dict | None = None) -> set[str]:
    sensor = _make_sensor(options)
    attrs = sensor.extra_state_attributes or {}
    return set(attrs.get("enabled_handlers", []))


# ---------------------------------------------------------------------------
# Always-on handlers
# ---------------------------------------------------------------------------


def test_enabled_handlers_attribute_present():
    """Even with empty options, the attribute is emitted."""
    sensor = _make_sensor({})
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "enabled_handlers" in attrs
    assert isinstance(attrs["enabled_handlers"], list)


def test_manual_and_default_always_enabled():
    enabled = _enabled({})
    assert "manual" in enabled
    assert "default" in enabled


def test_solar_enabled_by_default():
    """CONF_ENABLE_SUN_TRACKING defaults to True — solar should be enabled."""
    enabled = _enabled({})
    assert "solar" in enabled


def test_solar_disabled_when_sun_tracking_off():
    enabled = _enabled({CONF_ENABLE_SUN_TRACKING: False})
    assert "solar" not in enabled


# ---------------------------------------------------------------------------
# Configuration-gated handlers
# ---------------------------------------------------------------------------


def test_force_disabled_when_no_sensors_configured():
    enabled = _enabled({})
    assert "force" not in enabled


def test_force_enabled_when_sensors_configured():
    enabled = _enabled({CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.x"]})
    assert "force" in enabled


def test_force_disabled_when_empty_list():
    enabled = _enabled({CONF_FORCE_OVERRIDE_SENSORS: []})
    assert "force" not in enabled


def test_motion_disabled_when_no_sensors_configured():
    enabled = _enabled({})
    assert "motion" not in enabled


def test_motion_enabled_when_sensors_configured():
    enabled = _enabled({CONF_MOTION_SENSORS: ["binary_sensor.m"]})
    assert "motion" in enabled


def test_climate_disabled_by_default():
    enabled = _enabled({})
    assert "climate" not in enabled


def test_climate_enabled_when_climate_mode_on():
    enabled = _enabled({CONF_CLIMATE_MODE: True})
    assert "climate" in enabled


def test_weather_disabled_when_no_weather_config():
    enabled = _enabled({})
    assert "weather" not in enabled


def test_weather_enabled_when_weather_entity_set():
    enabled = _enabled({CONF_WEATHER_ENTITY: "weather.home"})
    assert "weather" in enabled


def test_glare_zone_disabled_by_default():
    enabled = _enabled({})
    assert "glare_zone" not in enabled


def test_glare_zone_enabled_when_flag_on():
    enabled = _enabled({CONF_ENABLE_GLARE_ZONES: True})
    assert "glare_zone" in enabled


def test_cloud_disabled_when_only_flag_on():
    """Cloud requires BOTH suppression flag AND coverage entity."""
    enabled = _enabled({CONF_CLOUD_SUPPRESSION: True})
    assert "cloud" not in enabled


def test_cloud_disabled_when_only_entity_set():
    enabled = _enabled({CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud"})
    assert "cloud" not in enabled


def test_cloud_enabled_when_both_set():
    enabled = _enabled(
        {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud"}
    )
    assert "cloud" in enabled


def test_custom_position_disabled_by_default():
    enabled = _enabled({})
    assert "custom_position" not in enabled


def test_custom_position_enabled_when_pair_configured():
    enabled = _enabled(
        {CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.cp1", CONF_CUSTOM_POSITION_1: 50}
    )
    assert "custom_position" in enabled


def test_custom_position_disabled_when_only_sensor_set():
    enabled = _enabled({CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.cp1"})
    assert "custom_position" not in enabled


# ---------------------------------------------------------------------------
# Composition / completeness
# ---------------------------------------------------------------------------


def test_full_configuration_enables_everything():
    enabled = _enabled(
        {
            CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.x"],
            CONF_MOTION_SENSORS: ["binary_sensor.m"],
            CONF_CLIMATE_MODE: True,
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
            CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.cp1",
            CONF_CUSTOM_POSITION_1: 50,
            CONF_ENABLE_SUN_TRACKING: True,
        }
    )
    assert enabled == {
        "force",
        "weather",
        "manual",
        "custom_position",
        "motion",
        "cloud",
        "climate",
        "glare_zone",
        "solar",
        "default",
    }


def test_minimal_configuration_enables_only_always_on():
    enabled = _enabled({CONF_ENABLE_SUN_TRACKING: False})
    assert enabled == {"manual", "default"}


def test_handler_names_match_card_normalized_form():
    """Card uses 'force', 'manual', 'motion', 'cloud' — not the pipeline names
    'force_override', 'manual_override', 'motion_timeout', 'cloud_suppression'.
    """
    enabled = _enabled(
        {
            CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.x"],
            CONF_MOTION_SENSORS: ["binary_sensor.m"],
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        }
    )
    assert "force_override" not in enabled
    assert "manual_override" not in enabled
    assert "motion_timeout" not in enabled
    assert "cloud_suppression" not in enabled
    assert "force" in enabled
    assert "manual" in enabled
    assert "motion" in enabled
    assert "cloud" in enabled
