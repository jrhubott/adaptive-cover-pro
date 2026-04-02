"""Tests for _build_config_summary() in config_flow.py."""

from __future__ import annotations


from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
from custom_components.adaptive_cover_pro.const import (
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENTITIES,
    CONF_END_TIME,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_PRESENCE_ENTITY,
    CONF_SILL_HEIGHT,
    CONF_START_TIME,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    SensorType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_vertical() -> dict:
    """Minimal config for a vertical blind."""
    return {
        CONF_ENTITIES: ["cover.living_room"],
        CONF_HEIGHT_WIN: 2.1,
        CONF_DISTANCE: 0.5,
        CONF_AZIMUTH: 180,
        CONF_FOV_LEFT: 90,
        CONF_FOV_RIGHT: 90,
        CONF_DEFAULT_HEIGHT: 60,
        CONF_DELTA_POSITION: 2,
        CONF_DELTA_TIME: 2,
    }


def _full_vertical() -> dict:
    """Full vertical blind config with all optional fields."""
    cfg = _minimal_vertical()
    cfg.update(
        {
            CONF_WINDOW_DEPTH: 0.1,
            CONF_SILL_HEIGHT: 0.5,
            CONF_MIN_POSITION: 10,
            CONF_ENABLE_MIN_POSITION: False,
            CONF_MAX_POSITION: 95,
            CONF_ENABLE_MAX_POSITION: True,
            CONF_SUNSET_POS: 0,
            CONF_INVERSE_STATE: True,
            CONF_INTERP: True,
            CONF_MIN_ELEVATION: 5,
            CONF_MAX_ELEVATION: 70,
            CONF_ENABLE_BLIND_SPOT: True,
            CONF_BLIND_SPOT_LEFT: 10,
            CONF_BLIND_SPOT_RIGHT: 20,
            CONF_BLIND_SPOT_ELEVATION: 30,
            CONF_START_TIME: "07:30",
            CONF_END_TIME: "20:00",
            CONF_MANUAL_OVERRIDE_DURATION: 120,
            CONF_MANUAL_THRESHOLD: 5,
            CONF_MANUAL_OVERRIDE_RESET: True,
            CONF_MOTION_SENSORS: ["binary_sensor.motion_1", "binary_sensor.motion_2"],
            CONF_MOTION_TIMEOUT: 300,
            CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.wind_alert"],
            CONF_FORCE_OVERRIDE_POSITION: 100,
            CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind_speed",
            CONF_WEATHER_WIND_SPEED_THRESHOLD: 50,
            CONF_WEATHER_RAIN_SENSOR: "sensor.rain_rate",
            CONF_WEATHER_RAIN_THRESHOLD: 2.0,
            CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.is_raining",
            CONF_WEATHER_IS_WINDY_SENSOR: "binary_sensor.is_windy",
            CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.hail", "binary_sensor.storm"],
            CONF_WEATHER_TIMEOUT: 600,
            CONF_CLIMATE_MODE: True,
            CONF_TEMP_ENTITY: "sensor.indoor_temp",
            CONF_TEMP_LOW: 16,
            CONF_TEMP_HIGH: 24,
            CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor_temp",
            CONF_OUTSIDE_THRESHOLD: 10,
            CONF_PRESENCE_ENTITY: "binary_sensor.presence",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_LUX_THRESHOLD: 1000,
            CONF_IRRADIANCE_ENTITY: "sensor.irradiance",
            CONF_IRRADIANCE_THRESHOLD: 200,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud_coverage",
            CONF_CLOUD_COVERAGE_THRESHOLD: 50,
            CONF_CLOUD_SUPPRESSION: True,
        }
    )
    return cfg


# ---------------------------------------------------------------------------
# Cover type label tests
# ---------------------------------------------------------------------------


def test_summary_shows_vertical_type():
    """Summary shows vertical type."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Vertical Blind" in summary


def test_summary_shows_awning_type():
    """Summary shows awning type."""
    summary = _build_config_summary({}, SensorType.AWNING)
    assert "Horizontal Awning" in summary


def test_summary_shows_tilt_type():
    """Summary shows tilt type."""
    summary = _build_config_summary({}, SensorType.TILT)
    assert "Venetian" in summary or "Tilt" in summary


def test_summary_no_type_graceful():
    """Summary no type graceful."""
    summary = _build_config_summary({}, None)
    # Should not crash and should return something
    assert isinstance(summary, str)


# ---------------------------------------------------------------------------
# Empty / minimal config
# ---------------------------------------------------------------------------


def test_empty_config_returns_string():
    """An empty config must not raise and must return a non-empty string."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_minimal_vertical_contains_key_fields():
    """Minimal vertical contains key fields."""
    cfg = _minimal_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "180°" in summary  # azimuth
    assert "2.1 m" in summary  # window height
    assert "0.5 m" in summary  # distance
    assert "60%" in summary  # default height
    assert "cover.living_room" in summary


# ---------------------------------------------------------------------------
# Section-specific tests — Geometry
# ---------------------------------------------------------------------------


def test_geometry_vertical_optional_fields_omitted_when_zero():
    """window_depth and sill_height of 0 should not appear in summary."""
    cfg = {CONF_HEIGHT_WIN: 2.0, CONF_DISTANCE: 0.5, CONF_WINDOW_DEPTH: 0.0, CONF_SILL_HEIGHT: 0.0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Window Depth" not in summary
    assert "Sill Height" not in summary


def test_geometry_vertical_optional_fields_shown_when_nonzero():
    """Geometry vertical optional fields shown when nonzero."""
    cfg = {CONF_HEIGHT_WIN: 2.0, CONF_DISTANCE: 0.5, CONF_WINDOW_DEPTH: 0.1, CONF_SILL_HEIGHT: 0.5}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Window Depth" in summary
    assert "Sill Height" in summary


def test_geometry_awning_shows_awning_fields():
    """Geometry awning shows awning fields."""
    cfg = {
        CONF_LENGTH_AWNING: 3.0,
        CONF_AWNING_ANGLE: 15,
        CONF_HEIGHT_WIN: 2.0,
        CONF_DISTANCE: 0.5,
    }
    summary = _build_config_summary(cfg, SensorType.AWNING)
    assert "Awning Length" in summary
    assert "3.0 m" in summary
    assert "15°" in summary


def test_geometry_tilt_shows_tilt_fields():
    """Geometry tilt shows tilt fields."""
    cfg = {CONF_TILT_DEPTH: 3.0, CONF_TILT_DISTANCE: 4.0, CONF_TILT_MODE: "mode1"}
    summary = _build_config_summary(cfg, SensorType.TILT)
    assert "Slat Depth" in summary
    assert "Slat Spacing" in summary
    assert "mode1" in summary


# ---------------------------------------------------------------------------
# Glare Zones
# ---------------------------------------------------------------------------


def test_glare_zones_hidden_when_disabled():
    """Glare zones hidden when disabled."""
    cfg = {CONF_ENABLE_GLARE_ZONES: False, CONF_WINDOW_WIDTH: 150}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Glare Zones" not in summary


def test_glare_zones_shown_when_enabled():
    """Glare zones shown when enabled."""
    cfg = {
        CONF_ENABLE_GLARE_ZONES: True,
        CONF_WINDOW_WIDTH: 150,
        "glare_zone_1_name": "Desk",
        "glare_zone_2_name": "",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Glare Zones" in summary
    assert "150 cm" in summary
    assert "Desk" in summary


def test_glare_zones_not_shown_for_awning():
    """Glare zones not shown for awning."""
    cfg = {CONF_ENABLE_GLARE_ZONES: True, CONF_WINDOW_WIDTH: 100}
    summary = _build_config_summary(cfg, SensorType.AWNING)
    assert "Glare Zones" not in summary


# ---------------------------------------------------------------------------
# Sun Tracking
# ---------------------------------------------------------------------------


def test_sun_tracking_fov_shown():
    """Sun tracking fov shown."""
    cfg = {CONF_AZIMUTH: 200, CONF_FOV_LEFT: 80, CONF_FOV_RIGHT: 70}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "200°" in summary
    assert "80°" in summary
    assert "70°" in summary


def test_sun_tracking_optional_elevation_omitted():
    """Sun tracking optional elevation omitted."""
    cfg = {CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Min Elevation" not in summary
    assert "Max Elevation" not in summary


def test_sun_tracking_elevation_shown_when_set():
    """Sun tracking elevation shown when set."""
    cfg = {CONF_AZIMUTH: 180, CONF_MIN_ELEVATION: 5, CONF_MAX_ELEVATION: 70}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Min Elevation" in summary
    assert "Max Elevation" in summary
    assert "5°" in summary
    assert "70°" in summary


# ---------------------------------------------------------------------------
# Position section
# ---------------------------------------------------------------------------


def test_position_min_max_with_qualifier():
    """Position min max with qualifier."""
    cfg = {
        CONF_MIN_POSITION: 5,
        CONF_ENABLE_MIN_POSITION: True,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MAX_POSITION: False,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "5%" in summary
    assert "90%" in summary
    assert "during sun tracking only" in summary  # enable_min is True


def test_position_inverse_state_shown():
    """Position inverse state shown."""
    cfg = {CONF_INVERSE_STATE: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Inverse State" in summary


def test_position_inverse_state_hidden_when_false():
    """Position inverse state hidden when false."""
    cfg = {CONF_INVERSE_STATE: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Inverse State" not in summary


def test_position_interp_shown():
    """Position interp shown."""
    cfg = {CONF_INTERP: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Interpolation" in summary


# ---------------------------------------------------------------------------
# Blind Spot
# ---------------------------------------------------------------------------


def test_blind_spot_hidden_when_disabled():
    """Blind spot hidden when disabled."""
    cfg = {CONF_ENABLE_BLIND_SPOT: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Blind Spot" not in summary


def test_blind_spot_shown_when_enabled():
    """Blind spot shown when enabled."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        CONF_BLIND_SPOT_ELEVATION: 40,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Blind Spot" in summary
    assert "10°" in summary
    assert "20°" in summary
    assert "40°" in summary


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------


def test_automation_times_shown():
    """Automation times shown."""
    cfg = {CONF_START_TIME: "07:00", CONF_END_TIME: "21:00", CONF_DELTA_POSITION: 3, CONF_DELTA_TIME: 5}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "07:00" in summary
    assert "21:00" in summary
    assert "3%" in summary
    assert "5 min" in summary


# ---------------------------------------------------------------------------
# Manual Override
# ---------------------------------------------------------------------------


def test_manual_override_shown():
    """Manual override shown."""
    cfg = {
        CONF_MANUAL_OVERRIDE_DURATION: 60,
        CONF_MANUAL_THRESHOLD: 3,
        CONF_MANUAL_OVERRIDE_RESET: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Manual Override" in summary
    assert "60 min" in summary
    assert "3%" in summary
    assert "Reset on New Command" in summary


# ---------------------------------------------------------------------------
# Motion & Force Overrides
# ---------------------------------------------------------------------------


def test_motion_sensors_count_shown():
    """Motion sensors count shown."""
    cfg = {
        CONF_MOTION_SENSORS: ["binary_sensor.a", "binary_sensor.b"],
        CONF_MOTION_TIMEOUT: 300,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Motion Sensors: 2" in summary
    assert "300 s" in summary


def test_force_override_sensors_shown():
    """Force override sensors shown."""
    cfg = {
        CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.wind"],
        CONF_FORCE_OVERRIDE_POSITION: 100,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Force Override Sensors: 1" in summary
    assert "100%" in summary


def test_overrides_section_hidden_when_empty():
    """Overrides section hidden when empty."""
    cfg = {CONF_MOTION_SENSORS: [], CONF_FORCE_OVERRIDE_SENSORS: []}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Motion & Force Overrides" not in summary


# ---------------------------------------------------------------------------
# Weather Override
# ---------------------------------------------------------------------------


def test_weather_override_section_hidden_when_no_sensors():
    """Weather override section hidden when no sensors."""
    cfg = {}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Weather Override" not in summary


def test_weather_override_wind_sensor_shown():
    """Weather override wind sensor shown."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_TIMEOUT: 120,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Weather Override" in summary
    assert "sensor.wind" in summary
    assert "60 km/h" in summary
    assert "120 s" in summary


def test_weather_override_rain_sensor_shown():
    """Weather override rain sensor shown."""
    cfg = {
        CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
        CONF_WEATHER_RAIN_THRESHOLD: 5.0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Rain Rate Sensor" in summary
    assert "5.0 mm/h" in summary


def test_weather_override_binary_sensors_shown():
    """Weather override binary sensors shown."""
    cfg = {
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.rain",
        CONF_WEATHER_IS_WINDY_SENSOR: "binary_sensor.wind",
        CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.hail"],
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Is-Raining Sensor" in summary
    assert "Is-Windy Sensor" in summary
    assert "Severe Weather Sensors: 1" in summary


# ---------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------


def test_climate_mode_shown():
    """Climate mode shown."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_ENTITY: "sensor.temp",
        CONF_TEMP_LOW: 16,
        CONF_TEMP_HIGH: 24,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Climate Mode: Enabled" in summary
    assert "sensor.temp" in summary
    assert "16°C" in summary
    assert "24°C" in summary


def test_climate_weather_entity_shown():
    """Climate weather entity shown."""
    cfg = {CONF_WEATHER_ENTITY: "weather.home"}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Climate" in summary
    assert "weather.home" in summary


def test_climate_lux_and_irradiance_shown():
    """Climate lux and irradiance shown."""
    cfg = {
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_IRRADIANCE_ENTITY: "sensor.irr",
        CONF_IRRADIANCE_THRESHOLD: 150,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.lux" in summary
    assert "500 lx" in summary
    assert "sensor.irr" in summary
    assert "150 W/m²" in summary


def test_climate_cloud_coverage_shown():
    """Climate cloud coverage shown."""
    cfg = {
        CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        CONF_CLOUD_COVERAGE_THRESHOLD: 60,
        CONF_CLOUD_SUPPRESSION: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.cloud" in summary
    assert "60%" in summary
    assert "Cloud Suppression: Enabled" in summary


# ---------------------------------------------------------------------------
# Full vertical — smoke test
# ---------------------------------------------------------------------------


def test_full_vertical_config_smoke():
    """Full vertical config should produce a summary without errors."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)

    # Cover type
    assert "Vertical Blind" in summary
    # Entity
    assert "cover.living_room" in summary
    # Geometry
    assert "2.1 m" in summary
    assert "0.1 m" in summary  # window_depth
    assert "0.5 m" in summary  # sill_height
    # Sun tracking
    assert "180°" in summary
    # Position
    assert "60%" in summary
    assert "Inverse State" in summary
    assert "Interpolation" in summary
    # Blind spot
    assert "Blind Spot" in summary
    # Automation
    assert "07:30" in summary
    assert "20:00" in summary
    # Manual override
    assert "120 min" in summary
    # Motion & Force
    assert "Motion Sensors: 2" in summary
    assert "Force Override Sensors: 1" in summary
    # Weather override
    assert "Weather Override" in summary
    assert "sensor.wind_speed" in summary
    # Climate
    assert "Climate Mode: Enabled" in summary
    assert "sensor.indoor_temp" in summary
    assert "weather.home" in summary
    assert "sensor.lux" in summary
    assert "sensor.cloud_coverage" in summary
    assert "Cloud Suppression: Enabled" in summary
