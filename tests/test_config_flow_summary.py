"""Tests for _build_config_summary() in config_flow.py — narrative format."""

from __future__ import annotations


from custom_components.adaptive_cover_pro.config_flow import (
    _build_config_summary,
    _build_cover_capabilities_text,
    _format_duration,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_ENABLE_SUN_TRACKING,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_USE_MY,
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
    CONF_START_ENTITY,
    CONF_END_ENTITY,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
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
    CONF_WEATHER_OVERRIDE_POSITION,
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
# Fixtures
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
            CONF_SUNSET_OFFSET: 30,
            CONF_SUNRISE_OFFSET: 60,
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
            CONF_WEATHER_OVERRIDE_POSITION: 0,
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
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 1.5,
        }
    )
    return cfg


# ---------------------------------------------------------------------------
# Section 1: Your Cover
# ---------------------------------------------------------------------------


def test_summary_shows_vertical_type():
    """Cover type label appears in Your Cover section."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Vertical Blind" in summary


def test_summary_shows_awning_type():
    """Awning type label appears in Your Cover section."""
    summary = _build_config_summary({}, SensorType.AWNING)
    assert "Horizontal Awning" in summary


def test_summary_shows_tilt_type():
    """Tilt type label appears in Your Cover section."""
    summary = _build_config_summary({}, SensorType.TILT)
    assert "Venetian" in summary or "Tilt" in summary


def test_summary_no_type_graceful():
    """None sensor type does not crash."""
    summary = _build_config_summary({}, None)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_empty_config_returns_string():
    """Empty config returns a non-empty string without crashing."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_entity_included_in_your_cover():
    """Cover entity ID appears in the Your Cover line."""
    cfg = {CONF_ENTITIES: ["cover.living_room"]}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "cover.living_room" in summary


def test_minimal_vertical_contains_key_fields():
    """Minimal vertical config shows entity, dimensions, and azimuth."""
    cfg = _minimal_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "cover.living_room" in summary
    assert "2.1m" in summary
    assert "0.5m" in summary
    assert "180°" in summary
    assert "60%" in summary


def test_geometry_vertical_optional_fields_omitted_when_zero():
    """Window depth and sill height of 0 do not appear."""
    cfg = {
        CONF_HEIGHT_WIN: 2.0,
        CONF_DISTANCE: 0.5,
        CONF_WINDOW_DEPTH: 0.0,
        CONF_SILL_HEIGHT: 0.0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "reveal" not in summary
    assert "sill" not in summary


def test_geometry_vertical_optional_fields_shown_when_nonzero():
    """Window depth and sill height appear when > 0."""
    cfg = {
        CONF_HEIGHT_WIN: 2.0,
        CONF_DISTANCE: 0.5,
        CONF_WINDOW_DEPTH: 0.1,
        CONF_SILL_HEIGHT: 0.5,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "reveal" in summary
    assert "sill" in summary


def test_geometry_awning_shows_awning_fields():
    """Awning dimensions appear in plain-English format."""
    cfg = {
        CONF_LENGTH_AWNING: 3.0,
        CONF_AWNING_ANGLE: 15,
        CONF_HEIGHT_WIN: 2.0,
        CONF_DISTANCE: 0.5,
    }
    summary = _build_config_summary(cfg, SensorType.AWNING)
    assert "3.0m awning" in summary
    assert "15°" in summary


def test_geometry_tilt_shows_tilt_fields():
    """Tilt slat dimensions appear."""
    cfg = {CONF_TILT_DEPTH: 3.0, CONF_TILT_DISTANCE: 4.0, CONF_TILT_MODE: "mode1"}
    summary = _build_config_summary(cfg, SensorType.TILT)
    assert "slat depth 3.0cm" in summary
    assert "spacing 4.0cm" in summary
    assert "mode1" in summary


# ---------------------------------------------------------------------------
# Section 2: How It Decides — Sun Tracking
# ---------------------------------------------------------------------------


def test_sun_tracking_always_present():
    """☀️ solar tracking bullet always appears."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Tracks the sun" in summary


def test_sun_tracking_fov_shown():
    """Azimuth and FOV are embedded in the solar tracking bullet."""
    cfg = {CONF_AZIMUTH: 200, CONF_FOV_LEFT: 80, CONF_FOV_RIGHT: 70}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "200°" in summary
    assert "80°" in summary
    assert "70°" in summary


def test_sun_tracking_optional_elevation_omitted():
    """Elevation limits are absent when not configured."""
    cfg = {CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "above" not in summary
    assert "below" not in summary


def test_sun_tracking_elevation_shown_when_set():
    """Elevation limits appear when configured."""
    cfg = {CONF_AZIMUTH: 180, CONF_MIN_ELEVATION: 5, CONF_MAX_ELEVATION: 70}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "above 5°" in summary
    assert "below 70°" in summary


# ---------------------------------------------------------------------------
# Section 2: Timing
# ---------------------------------------------------------------------------


def test_automation_times_shown():
    """Start and end times appear in the timing bullet."""
    cfg = {CONF_START_TIME: "07:00", CONF_END_TIME: "21:00"}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "07:00" in summary
    assert "21:00" in summary


def test_start_entity_preferred_over_default_start_time():
    """Entity wins when CONF_START_TIME is the schema default '00:00:00'."""
    cfg = {
        CONF_START_TIME: "00:00:00",
        CONF_START_ENTITY: "sensor.sunrise_time",
        CONF_END_TIME: "00:00:00",
        CONF_END_ENTITY: "sensor.sunset_time",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.sunrise_time" in summary
    assert "sensor.sunset_time" in summary
    assert "from 00:00:00" not in summary
    assert "until 00:00:00" not in summary


def test_explicit_start_time_used_when_no_entity():
    """Static time is used when no entity is configured."""
    cfg = {CONF_START_TIME: "07:00", CONF_END_TIME: "21:00"}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "from 07:00" in summary
    assert "until 21:00" in summary


def test_start_entity_preferred_even_with_non_default_start_time():
    """Entity always wins over a non-default static time — mirrors time_window.py precedence."""
    cfg = {
        CONF_START_TIME: "06:30",
        CONF_START_ENTITY: "sensor.dynamic_start",
        CONF_END_TIME: "22:00",
        CONF_END_ENTITY: "sensor.dynamic_end",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.dynamic_start" in summary
    assert "sensor.dynamic_end" in summary
    assert "06:30" not in summary
    assert "22:00" not in summary


def test_mixed_start_entity_with_static_end_time():
    """Start entity + static end time — each branch resolved independently."""
    cfg = {
        CONF_START_TIME: "00:00:00",
        CONF_START_ENTITY: "sensor.sunrise_time",
        CONF_END_TIME: "21:30",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.sunrise_time" in summary
    assert "until 21:30" in summary


def test_sunset_position_shown():
    """Sunset/end-of-day position appears in timing bullet."""
    cfg = {CONF_SUNSET_POS: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sunset" in summary.lower() or "end time" in summary.lower()


def test_sunrise_position_shown_when_sunset_pos_configured():
    """After sunrise line appears when sunset_pos is configured."""
    cfg = {CONF_SUNSET_POS: 80, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunrise" in summary
    assert "tracking resumes" in summary


def test_sunrise_position_not_shown_without_sunset_pos():
    """After sunrise line absent when no sunset_pos is configured."""
    cfg = {CONF_DEFAULT_HEIGHT: 60}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "tracking resumes" not in summary
    assert "After sunrise" not in summary


def test_sunrise_offset_positive_shown():
    """Positive sunrise offset shown as (+N min)."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: 60, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunrise (+60 min)" in summary


def test_sunrise_offset_negative_shown():
    """Negative sunrise offset shown as (-N min)."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: -30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunrise (-30 min)" in summary


def test_sunrise_offset_zero_omitted():
    """Zero sunrise offset shows no parenthetical annotation."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: 0, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunrise →" in summary
    assert "(+" not in summary.split("After sunrise")[1].split("\n")[0]


def test_sunset_offset_positive_shown():
    """Positive sunset offset shown as (+N min) on the sunset line."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: 30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunset (+30 min)" in summary


def test_sunset_offset_negative_shown():
    """Negative sunset offset shown as (-N min) on the sunset line."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: -30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunset (-30 min)" in summary


def test_sunset_offset_zero_omitted():
    """Zero sunset offset shows no parenthetical annotation."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: 0, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunset →" in summary
    assert "(+" not in summary.split("After sunset")[1].split("\n")[0]


def test_sunrise_shows_default_position():
    """After sunrise line shows the default position percentage."""
    cfg = {CONF_SUNSET_POS: 80, CONF_DEFAULT_HEIGHT: 45}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    # The sunrise line should reference the default position (45%)
    sunrise_line = [ln for ln in summary.splitlines() if "After sunrise" in ln]
    assert sunrise_line, "No 'After sunrise' line found"
    assert "45%" in sunrise_line[0]


def test_both_offsets_shown_together():
    """Both sunset and sunrise offsets appear correctly when both are non-zero."""
    cfg = {
        CONF_SUNSET_POS: 80,
        CONF_SUNSET_OFFSET: 30,
        CONF_SUNRISE_OFFSET: 60,
        CONF_DEFAULT_HEIGHT: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "After sunset (+30 min) →" in summary
    assert "After sunrise (+60 min) →" in summary


def test_end_time_and_sunset_pos_with_offsets():
    """Scenario A: end time + sunset_pos + both offsets all render correctly."""
    cfg = {
        CONF_END_TIME: "20:00",
        CONF_SUNSET_POS: 80,
        CONF_SUNSET_OFFSET: 30,
        CONF_SUNRISE_OFFSET: 60,
        CONF_DEFAULT_HEIGHT: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    # End-time transition line
    assert "After end time" in summary
    # Sunset line with offset
    assert "After sunset (+30 min) → 80%" in summary
    # Sunrise line with offset
    assert "After sunrise (+60 min)" in summary
    assert "tracking resumes" in summary


# ---------------------------------------------------------------------------
# Section 2: Blind Spot
# ---------------------------------------------------------------------------


def test_blind_spot_hidden_when_disabled():
    """Blind spot bullet absent when not enabled."""
    cfg = {CONF_ENABLE_BLIND_SPOT: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Blind spot" not in summary


def test_blind_spot_shown_when_enabled():
    """Blind spot bullet shows the degree range."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        CONF_BLIND_SPOT_ELEVATION: 40,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Blind spot" in summary
    assert "10°" in summary
    assert "20°" in summary
    assert "40°" in summary
    assert "FOV left" in summary


# ---------------------------------------------------------------------------
# Section 2: Glare Zones
# ---------------------------------------------------------------------------


def test_glare_zones_hidden_when_disabled():
    """Glare zone bullet absent when not enabled."""
    cfg = {CONF_ENABLE_GLARE_ZONES: False, CONF_WINDOW_WIDTH: 1.5}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Glare zones" not in summary


def test_glare_zones_shown_when_enabled():
    """Glare zone bullet appears with zone names and window width."""
    cfg = {
        CONF_ENABLE_GLARE_ZONES: True,
        CONF_WINDOW_WIDTH: 1.5,
        "glare_zone_1_name": "Desk",
        "glare_zone_2_name": "",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Glare zones" in summary
    assert "1.50m" in summary
    assert "Desk" in summary


def test_glare_zones_not_shown_for_awning():
    """Glare zone bullet absent for awning type."""
    cfg = {CONF_ENABLE_GLARE_ZONES: True, CONF_WINDOW_WIDTH: 1.0}
    summary = _build_config_summary(cfg, SensorType.AWNING)
    assert "Glare zones" not in summary


# ---------------------------------------------------------------------------
# Section 2: Climate
# ---------------------------------------------------------------------------


def test_climate_mode_shown():
    """Climate mode bullet appears with temp range and sensors."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_ENTITY: "sensor.temp",
        CONF_TEMP_LOW: 16,
        CONF_TEMP_HIGH: 24,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Climate mode" in summary
    assert "16" in summary
    assert "24" in summary
    assert "sensor.temp" in summary


def test_climate_weather_entity_shown():
    """Weather entity appears in climate bullet."""
    cfg = {CONF_CLIMATE_MODE: True, CONF_WEATHER_ENTITY: "weather.home"}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "weather.home" in summary


def test_climate_lux_and_irradiance_shown_with_suppression():
    """Lux and irradiance thresholds appear in cloud suppression bullet."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_IRRADIANCE_ENTITY: "sensor.irr",
        CONF_IRRADIANCE_THRESHOLD: 150,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "500 lx" in summary
    assert "150 W/m²" in summary


def test_climate_cloud_coverage_shown():
    """Cloud coverage threshold appears in cloud suppression bullet."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        CONF_CLOUD_COVERAGE_THRESHOLD: 60,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "60%" in summary
    assert "Cloud suppression" in summary


def test_light_sensors_without_suppression_noted():
    """Light sensors configured but suppression off shows informational note."""
    cfg = {CONF_LUX_ENTITY: "sensor.lux", CONF_CLOUD_SUPPRESSION: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "lux" in summary
    assert "cloud suppression is off" in summary


# ---------------------------------------------------------------------------
# Section 2: Manual Override
# ---------------------------------------------------------------------------


def test_manual_override_always_present():
    """Manual override bullet always appears (it's always active)."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Manual override" in summary


def test_manual_override_duration_shown():
    """Override duration dict appears formatted in the manual override bullet (issue #148)."""
    cfg = {CONF_MANUAL_OVERRIDE_DURATION: {"hours": 5, "minutes": 0, "seconds": 0}}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "5 h" in summary
    # Raw dict must NOT appear
    assert "{'hours'" not in summary
    assert "hours" not in summary or "5 h" in summary


# ---------------------------------------------------------------------------
# _format_duration unit tests (issue #148)
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """Unit tests for _format_duration helper."""

    def test_hours_only(self):
        """Full hours, zeroed minutes and seconds."""
        assert _format_duration({"hours": 5, "minutes": 0, "seconds": 0}) == "5 h"

    def test_hours_and_minutes(self):
        """Hours and non-zero minutes combined."""
        assert (
            _format_duration({"hours": 2, "minutes": 15, "seconds": 0}) == "2 h 15 min"
        )

    def test_minutes_only(self):
        """Zero hours, non-zero minutes."""
        assert _format_duration({"hours": 0, "minutes": 30, "seconds": 0}) == "30 min"

    def test_seconds_only(self):
        """All zero except seconds."""
        assert _format_duration({"hours": 0, "minutes": 0, "seconds": 45}) == "45 s"

    def test_all_three_components(self):
        """Hours, minutes, and seconds all non-zero."""
        assert (
            _format_duration({"hours": 1, "minutes": 5, "seconds": 30})
            == "1 h 5 min 30 s"
        )

    def test_all_zero(self):
        """All components zero returns '0 min'."""
        assert _format_duration({"hours": 0, "minutes": 0, "seconds": 0}) == "0 min"

    def test_legacy_int(self):
        """Plain integer (legacy config) treated as minutes."""
        assert _format_duration(120) == "120 min"

    def test_legacy_float(self):
        """Plain float (legacy config) treated as minutes."""
        assert _format_duration(90.0) == "90 min"

    def test_none_returns_empty(self):
        """None input returns empty string."""
        assert _format_duration(None) == ""

    def test_default_ha_two_hours(self):
        """Default HA duration selector value {"hours": 2} (no minutes/seconds key)."""
        assert _format_duration({"hours": 2}) == "2 h"

    def test_no_raw_dict_in_summary(self):
        """Regression: raw dict must not appear anywhere in the config summary."""
        cfg = {CONF_MANUAL_OVERRIDE_DURATION: {"hours": 3, "minutes": 30, "seconds": 0}}
        summary = _build_config_summary(cfg, SensorType.BLIND)
        assert "{'hours'" not in summary
        assert "{" not in summary or "pauses for 3 h 30 min" in summary


def test_manual_override_reset_shown():
    """Reset-on-new-command flag appears."""
    cfg = {CONF_MANUAL_OVERRIDE_RESET: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "resets on next move" in summary


# ---------------------------------------------------------------------------
# Section 2: Motion Timeout
# ---------------------------------------------------------------------------


def test_motion_sensors_count_shown():
    """Motion sensor count and timeout appear in the motion bullet."""
    cfg = {
        CONF_MOTION_SENSORS: ["binary_sensor.a", "binary_sensor.b"],
        CONF_MOTION_TIMEOUT: 300,
        CONF_DEFAULT_HEIGHT: 60,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Motion-based" in summary
    assert "300s" in summary
    assert "60%" in summary


def test_motion_section_hidden_when_no_sensors():
    """Motion bullet absent when no motion sensors configured."""
    cfg = {CONF_MOTION_SENSORS: []}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Motion-based" not in summary


# ---------------------------------------------------------------------------
# Section 2: Weather Override
# ---------------------------------------------------------------------------


def test_weather_override_section_hidden_when_no_sensors():
    """Weather safety bullet absent when no weather sensors configured."""
    cfg = {}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Weather safety" not in summary


def test_weather_override_wind_sensor_shown():
    """Wind threshold appears in weather safety bullet."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_TIMEOUT: 120,
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Weather safety" in summary
    assert "wind > 60" in summary
    assert "120s" in summary


def test_weather_override_rain_sensor_shown():
    """Rain threshold appears in weather safety bullet."""
    cfg = {
        CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
        CONF_WEATHER_RAIN_THRESHOLD: 5.0,
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "rain > 5.0" in summary


def test_weather_override_binary_sensors_shown():
    """is-raining, is-windy, and severe sensor count appear."""
    cfg = {
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.rain",
        CONF_WEATHER_IS_WINDY_SENSOR: "binary_sensor.wind",
        CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.hail"],
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "is-raining" in summary
    assert "is-windy" in summary
    assert "severe weather" in summary


# ---------------------------------------------------------------------------
# Section 2: Force Override
# ---------------------------------------------------------------------------


def test_force_override_section_hidden_when_no_sensors():
    """Force override bullet absent when no force sensors configured."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Force override" not in summary


def test_force_override_shown_with_position():
    """Force override bullet appears with sensor count and position."""
    cfg = {
        CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.wind"],
        CONF_FORCE_OVERRIDE_POSITION: 100,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Force override" in summary
    assert "100%" in summary
    assert "overrides everything else" in summary


# ---------------------------------------------------------------------------
# Section 3: Position Limits
# ---------------------------------------------------------------------------


def test_position_limits_section_present_with_values():
    """Position Limits section appears when min/max/default are set."""
    cfg = {CONF_MIN_POSITION: 5, CONF_MAX_POSITION: 90, CONF_DEFAULT_HEIGHT: 60}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Position Limits" in summary
    assert "5%" in summary
    assert "90%" in summary
    assert "60%" in summary


def test_position_limits_sun_tracking_qualifier():
    """'during sun tracking only' appears when enable_min or enable_max is set."""
    cfg = {
        CONF_MIN_POSITION: 5,
        CONF_ENABLE_MIN_POSITION: True,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MAX_POSITION: False,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "during sun tracking only" in summary


def test_position_inverse_state_shown():
    """Inverse state appears in Position Limits."""
    cfg = {CONF_INVERSE_STATE: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Inverse state" in summary


def test_position_inverse_state_hidden_when_false():
    """Inverse state absent when disabled."""
    cfg = {CONF_INVERSE_STATE: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Inverse state" not in summary


def test_position_interp_shown():
    """Position calibration flag appears in Position Limits."""
    cfg = {CONF_INTERP: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Position calibration" in summary


def test_delta_position_and_time_shown():
    """Delta position and time appear in Position Limits."""
    cfg = {CONF_DELTA_POSITION: 3, CONF_DELTA_TIME: 5}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "3%" in summary
    assert "5 min" in summary


# ---------------------------------------------------------------------------
# Section 4: Decision Priority chain
# ---------------------------------------------------------------------------


def test_priority_section_always_present():
    """Decision Priority section always appears."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "Decision Priority" in summary


def test_priority_always_on_handlers_active():
    """Manual, Solar, and Default are always shown as ✅."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "✅Manual" in summary
    assert "✅Solar" in summary
    assert "✅Default" in summary


def test_priority_force_override_active_with_sensors():
    """Force Override shows ✅ when sensors are configured."""
    cfg = {
        CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.wind"],
        CONF_FORCE_OVERRIDE_POSITION: 100,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Force" in summary


def test_priority_force_override_not_configured():
    """Force Override shows ❌ when no sensors are set."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "❌Force" in summary


def test_priority_weather_override_active_with_sensors():
    """Weather Override shows ✅ when sensors are configured."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Weather" in summary


def test_priority_weather_override_not_configured():
    """Weather Override shows ❌ when no weather sensors are set."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "❌Weather" in summary


def test_priority_motion_timeout_active():
    """Motion Timeout shows ✅ when sensors are configured."""
    cfg = {CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_DEFAULT_HEIGHT: 45}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Motion" in summary


def test_priority_cloud_suppression_active():
    """Cloud Suppression shows ✅ when enabled."""
    cfg = {CONF_CLOUD_SUPPRESSION: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Cloud" in summary


def test_priority_climate_active():
    """Climate shows ✅ when climate mode is on."""
    cfg = {CONF_CLIMATE_MODE: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Climate" in summary


def test_priority_glare_zone_active_for_vertical():
    """Glare Zone shows ✅ for vertical blind when enabled."""
    cfg = {CONF_ENABLE_GLARE_ZONES: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Glare" in summary


def test_priority_glare_zone_hidden_for_awning():
    """Glare Zone entry is omitted entirely for awning covers."""
    summary = _build_config_summary({CONF_ENABLE_GLARE_ZONES: True}, SensorType.AWNING)
    assert "Glare" not in summary


def test_priority_glare_zone_hidden_for_tilt():
    """Glare Zone entry is omitted entirely for tilt covers."""
    summary = _build_config_summary({}, SensorType.TILT)
    assert "Glare" not in summary


def test_priority_default_position_reflected():
    """Default handler shows the configured default height in the narrative."""
    cfg = {CONF_DEFAULT_HEIGHT: 75}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "75%" in summary


def test_priority_all_nine_handlers_full_config():
    """Full config shows all nine handlers as ✅ in the priority chain."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)
    for token in [
        "✅Force",
        "✅Weather",
        "✅Motion",
        "✅Manual",
        "✅Cloud",
        "✅Climate",
        "✅Glare",
        "✅Solar",
        "✅Default",
    ]:
        assert token in summary, f"Expected '{token}' in summary"


# ---------------------------------------------------------------------------
# Full smoke test
# ---------------------------------------------------------------------------


def test_full_vertical_config_smoke():
    """Full vertical config produces a complete summary without errors."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)

    # Section headers
    assert "Your Cover" in summary
    assert "How It Decides" in summary
    assert "Position Limits" in summary
    assert "Decision Priority" in summary

    # Entity
    assert "cover.living_room" in summary
    # Geometry
    assert "2.1m" in summary
    assert "reveal 0.1m" in summary
    assert "sill 0.5m" in summary
    # Sun tracking
    assert "180°" in summary
    # Blind spot
    assert "Blind spot" in summary
    assert "10°" in summary
    # Timing
    assert "07:30" in summary
    assert "20:00" in summary
    assert "After sunrise" in summary
    assert "tracking resumes" in summary
    # Manual override
    assert "120 min" in summary
    # Motion
    assert "Motion-based" in summary
    assert "300s" in summary
    # Weather
    assert "Weather safety" in summary
    assert "wind > 50" in summary
    # Climate
    assert "Climate mode" in summary
    assert "sensor.indoor_temp" in summary
    assert "weather.home" in summary
    # Cloud suppression
    assert "Cloud suppression" in summary
    assert "1000 lx" in summary
    # Force override
    assert "Force override" in summary
    # Position limits
    assert "10%" in summary
    assert "95%" in summary
    assert "Inverse state" in summary
    assert "Position calibration" in summary


# ---------------------------------------------------------------------------
# Sun tracking toggle
# ---------------------------------------------------------------------------


def test_sun_tracking_disabled_shows_disabled_message():
    """When enable_sun_tracking is False, summary says tracking is disabled."""
    cfg = {CONF_ENABLE_SUN_TRACKING: False, CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Sun tracking disabled" in summary
    assert "Tracks the sun" not in summary


def test_sun_tracking_enabled_shows_tracking_message():
    """When enable_sun_tracking is True (default), summary says it tracks the sun."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True, CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Tracks the sun" in summary
    assert "Sun tracking disabled" not in summary


def test_sun_tracking_default_enabled_shows_tracking_message():
    """When enable_sun_tracking is absent (defaults to True), summary shows tracking."""
    cfg = {CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Tracks the sun" in summary
    assert "Sun tracking disabled" not in summary


def test_glare_zones_shown_when_sun_tracking_disabled():
    """Glare zones remain in summary when sun tracking is off (issue #238)."""
    cfg = {
        CONF_ENABLE_SUN_TRACKING: False,
        CONF_ENABLE_GLARE_ZONES: True,
        "glare_zone_1_name": "Desk",
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Glare" in summary
    assert "Desk" in summary


def test_sun_tracking_disabled_priority_chain_shows_solar_inactive():
    """Priority chain marks Solar as inactive when sun tracking is off."""
    cfg = {CONF_ENABLE_SUN_TRACKING: False, CONF_ENABLE_GLARE_ZONES: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "❌Solar" in summary


def test_priority_chain_glare_independent_of_sun_tracking():
    """Priority chain shows Glare as active even with sun tracking off, when glare zones enabled."""
    cfg = {CONF_ENABLE_SUN_TRACKING: False, CONF_ENABLE_GLARE_ZONES: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "✅Glare" in summary
    assert "❌Solar" in summary


# ---------------------------------------------------------------------------
# My Preset (Somfy) support
# ---------------------------------------------------------------------------


def test_summary_shows_my_preset_for_sunset():
    """Sunset target renders as 'My (N%)' when CONF_SUNSET_USE_MY is True and value is set."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 30
    cfg[CONF_MY_POSITION_VALUE] = 50
    cfg[CONF_SUNSET_USE_MY] = True
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "My (50%)" in summary
    # The raw configured percent should not appear as a sunset target
    assert "→ 30%" not in summary


def test_summary_shows_my_preset_for_custom_slot():
    """Custom slot renders 'My (N%)' when use_my flag is set and value is configured."""
    cfg = _minimal_vertical()
    cfg["custom_position_sensor_1"] = "binary_sensor.my_sensor"
    cfg["custom_position_1"] = 40
    cfg["custom_position_use_my_1"] = True
    cfg[CONF_MY_POSITION_VALUE] = 50
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "My (50%)" in summary
    # Raw percent should not appear as the custom target
    assert "→ 40%" not in summary


def test_summary_falls_back_when_my_value_unset():
    """Shows fallback text and warning when use_my is True but My Preset Value is not set."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 30
    cfg[CONF_SUNSET_USE_MY] = True
    # CONF_MY_POSITION_VALUE intentionally absent
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "My (not set → 30%)" in summary
    assert "⚠️" in summary
    assert "My Preset Value is not set" in summary


def test_summary_hides_my_info_when_unused():
    """No My preset lines appear when no use_my flags are set and value is absent."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 30
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "🎛️ Somfy My preset" not in summary
    assert "⚠️ Somfy My preset" not in summary
    assert "My (" not in summary


def test_summary_shows_my_info_line_when_value_set_globally():
    """Info line appears showing the configured My value even if no use_my flags are enabled."""
    cfg = _minimal_vertical()
    cfg[CONF_MY_POSITION_VALUE] = 50
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "🎛️ Somfy My preset: 50%" in summary


# ---------------------------------------------------------------------------
# Section 1b: Cover Capabilities (now shown on Debug & Diagnostics screen)
# ---------------------------------------------------------------------------


def _make_hass(entity_states: dict) -> object:
    """Build a minimal hass stub for cover-capabilities tests.

    entity_states maps entity_id → dict with keys:
      - "state": str (default "open")
      - "supported_features": int (default 0)
      - "assumed_state": bool (default False)
    Returns an object whose .states.get(entity_id) mimics HA state objects.
    """
    from unittest.mock import MagicMock

    def _get_state(entity_id):
        if entity_id not in entity_states:
            return None
        spec = entity_states[entity_id]
        s = MagicMock()
        s.state = spec.get("state", "open")
        attrs = {}
        if "supported_features" in spec:
            attrs["supported_features"] = spec["supported_features"]
        if spec.get("assumed_state"):
            attrs["assumed_state"] = True
        s.attributes = attrs
        return s

    hass = MagicMock()
    hass.states.get.side_effect = _get_state
    return hass


def test_capability_listing_not_in_summary():
    """Full capability listing (entity: set position, open...) is not in _build_config_summary."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
    )
    hass = _make_hass({"cover.living_room": {"supported_features": feats}})
    cfg = {CONF_ENTITIES: ["cover.living_room"]}
    summary = _build_config_summary(cfg, SensorType.BLIND, hass)
    # Full listing section header is not shown — only warnings
    assert "Cover Capabilities" not in summary
    # No warnings for a well-configured cover
    assert "Cover Warnings" not in summary


def test_capability_warnings_appear_in_summary():
    """Actionable capability warnings appear in _build_config_summary."""
    from homeassistant.components.cover import CoverEntityFeature

    # Open/close-only cover — should produce a warning
    feats = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    hass = _make_hass({"cover.blind": {"supported_features": feats}})
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    summary = _build_config_summary(cfg, SensorType.BLIND, hass)
    assert "Cover Warnings" in summary
    assert "open/close-only" in summary


def test_capabilities_section_absent_without_hass():
    """_build_cover_capabilities_text returns empty string when hass is not passed."""
    cfg = {CONF_ENTITIES: ["cover.living_room"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND)
    assert result == ""


def test_capabilities_section_absent_without_entities():
    """_build_cover_capabilities_text returns empty string when entities list is empty."""
    hass = _make_hass({})
    result = _build_cover_capabilities_text({CONF_ENTITIES: []}, SensorType.BLIND, hass)
    assert result == ""


def test_capabilities_section_renders_full_featured_cover():
    """Full-featured cover shows set position, open, close, stop in capabilities."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
    )
    hass = _make_hass({"cover.blind": {"supported_features": feats}})
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "Cover Capabilities" in result
    assert "cover.blind" in result
    assert "set position" in result
    assert "open" in result
    assert "close" in result
    assert "stop" in result


def test_capabilities_section_warns_on_open_close_only():
    """Open/close-only cover renders ⚠️ threshold-compare warning."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    hass = _make_hass({"cover.blind": {"supported_features": feats}})
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "open/close-only" in result
    assert "threshold compare" in result


def test_capabilities_section_unavailable_entity():
    """Unavailable entity renders ⚠️ not-ready warning."""
    hass = _make_hass(
        {"cover.blind": {"state": "unavailable", "supported_features": 0}}
    )
    # check_cover_features returns None for unavailable state
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "not ready" in result


def test_capabilities_section_assumed_state_warning():
    """Cover with assumed_state attribute renders ⚠️ assumed-state warning."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
    )
    hass = _make_hass(
        {"cover.blind": {"supported_features": feats, "assumed_state": True}}
    )
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "assumed_state" in result
    assert "position cannot be read back" in result


def test_capabilities_section_mixed_caps_warning():
    """Two covers with different set_position support renders mixed-capabilities warning."""
    from homeassistant.components.cover import CoverEntityFeature

    hass = _make_hass(
        {
            "cover.a": {
                "supported_features": CoverEntityFeature.SET_POSITION
                | CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
            },
            "cover.b": {
                "supported_features": CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
            },
        }
    )
    cfg = {CONF_ENTITIES: ["cover.a", "cover.b"]}
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "Mixed capabilities" in result
    assert "driven differently" in result


def test_capabilities_section_tilt_type_mismatch():
    """Tilt sensor_type with no set_tilt_position cover renders mismatch warning."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
    )
    hass = _make_hass({"cover.blind": {"supported_features": feats}})
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, SensorType.TILT, hass)
    assert "set_tilt_position" in result
    assert "tilt (venetian)" in result


def test_capabilities_section_position_limits_ignored_warning():
    """Position limits configured + open/close-only cover renders ignored-limits warning."""
    from homeassistant.components.cover import CoverEntityFeature

    feats = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    hass = _make_hass({"cover.blind": {"supported_features": feats}})
    cfg = {
        CONF_ENTITIES: ["cover.blind"],
        CONF_ENABLE_MIN_POSITION: True,
        CONF_MIN_POSITION: 10,
    }
    result = _build_cover_capabilities_text(cfg, SensorType.BLIND, hass)
    assert "Position limits" in result
    assert "limits will be ignored" in result
    assert "cover.blind" in result


# ---------------------------------------------------------------------------
# Section: How It Decides — sun-time annotations (Position Map removed, all
# trigger→target content now lives under How It Decides)
# ---------------------------------------------------------------------------


def _sun_times(
    *,
    sunrise_raw=(6, 30),
    sunset_raw=(19, 45),
    sunrise_eff=None,
    sunset_eff=None,
    solar_start=(7, 14),
    solar_end=(18, 30),
):
    """Build a sun_times dict with HH:MM tuples; None entries pass through."""
    import datetime as dt

    today = dt.date(2026, 4, 18)

    def _dt(hm):
        if hm is None:
            return None
        return dt.datetime(today.year, today.month, today.day, hm[0], hm[1])

    return {
        "sunrise_raw": _dt(sunrise_raw),
        "sunset_raw": _dt(sunset_raw),
        "sunrise_eff": _dt(sunrise_eff) if sunrise_eff else _dt(sunrise_raw),
        "sunset_eff": _dt(sunset_eff) if sunset_eff else _dt(sunset_raw),
        "solar_start": _dt(solar_start),
        "solar_end": _dt(solar_end),
    }


def test_no_times_without_sun_times():
    """Without sun_times no time annotations appear anywhere."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "today" not in summary


def test_sun_tracking_row_shows_solar_window():
    """Sun tracking line includes today's solar control window when sun_times provided."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True}
    summary = _build_config_summary(cfg, SensorType.BLIND, sun_times=_sun_times())
    assert "(today: sun in window 07:14 → 18:30)" in summary
    # Sun tracking rule line still carries its existing "Tracks the sun" phrasing
    assert "Tracks the sun" in summary


def test_sun_tracking_row_no_window_when_solar_times_none():
    """Sun tracking line shows 'does not enter window' when solar_start/solar_end are None."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True}
    summary = _build_config_summary(
        cfg, SensorType.BLIND, sun_times=_sun_times(solar_start=None, solar_end=None)
    )
    assert "(today: sun does not enter window)" in summary


def test_after_sunset_row_shows_effective_sunset():
    """After sunset sub-bullet includes effective sunset time when sunset_pos configured."""
    cfg = {CONF_SUNSET_POS: 30}
    summary = _build_config_summary(
        cfg, SensorType.BLIND, sun_times=_sun_times(sunset_eff=(19, 45))
    )
    assert "🌅 After sunset (today ~19:45) → 30%" in summary


def test_after_sunrise_row_shows_when_sunset_pos_configured():
    """After sunrise sub-bullet shows today's effective sunrise time when sunset_pos configured."""
    cfg = {CONF_SUNSET_POS: 30}
    summary = _build_config_summary(
        cfg, SensorType.BLIND, sun_times=_sun_times(sunrise_eff=(6, 42))
    )
    assert "🌄 After sunrise (today ~06:42) → 0%" in summary


def test_after_sunrise_row_absent_without_sunset_pos():
    """After sunrise sub-bullet does not appear when sunset_pos is not configured."""
    cfg = {}
    summary = _build_config_summary(cfg, SensorType.BLIND, sun_times=_sun_times())
    assert "🌄 After sunrise" not in summary


def test_default_row_always_present():
    """Default fallback line always renders under How It Decides."""
    for cfg in [{}, {CONF_SUNSET_POS: 30}, {CONF_ENABLE_SUN_TRACKING: False}]:
        summary = _build_config_summary(cfg, SensorType.BLIND)
        assert "🌙 Default (no rule matches) → 0%" in summary


def test_sunset_today_and_offset_merged_in_one_parenthetical():
    """When both today's time and an offset are present, they share one parenthetical."""
    cfg = {CONF_SUNSET_POS: 30, CONF_SUNSET_OFFSET: 30}
    summary = _build_config_summary(
        cfg, SensorType.BLIND, sun_times=_sun_times(sunset_eff=(20, 15))
    )
    assert "🌅 After sunset (today ~20:15, +30 min) → 30%" in summary


# ---------------------------------------------------------------------------
# Position Map section is gone — every place it lived is now the HID chain
# ---------------------------------------------------------------------------


def test_position_map_section_absent():
    """Position Map header never renders — its content moved to How It Decides."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, SensorType.BLIND, sun_times=_sun_times())
    assert "**Position Map**" not in summary
    assert "Position Map" not in summary


# ---------------------------------------------------------------------------
# Priority badges — each rule in How It Decides ends with [N]
# ---------------------------------------------------------------------------


def test_priority_badges_on_every_rule():
    """Each HID rule line carries a [N] priority badge; badges match the chain."""
    cfg = _full_vertical()
    cfg["custom_position_sensor_1"] = "binary_sensor.movie"
    cfg["custom_position_1"] = 40
    cfg["custom_position_priority_1"] = 77
    summary = _build_config_summary(cfg, SensorType.BLIND)
    for badge in ("[100]", "[90]", "[80]", "[77]", "[75]", "[60]", "[50]", "[45]", "[40]", "[0]"):
        assert badge in summary, f"expected {badge} badge on some rule"


def test_priority_badge_default_zero():
    """Default fallback line shows the [0] badge."""
    summary = _build_config_summary({}, SensorType.BLIND)
    assert "🌙 Default (no rule matches) → 0%" in summary
    # [0] appears on the same line as the default fallback
    for line in summary.splitlines():
        if "🌙 Default" in line:
            assert "[0]" in line
            break
    else:
        raise AssertionError("No default fallback line found")


# ---------------------------------------------------------------------------
# Newly rendered behavior-affecting options
# ---------------------------------------------------------------------------


def test_return_sunset_line_rendered():
    """CONF_RETURN_SUNSET toggles a '🔚 Return to sunset position at end time: on' line."""
    from custom_components.adaptive_cover_pro.const import CONF_RETURN_SUNSET

    cfg = {CONF_SUNSET_POS: 30, CONF_RETURN_SUNSET: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Return to sunset position at end time: on" in summary


def test_return_sunset_line_absent_when_false():
    """CONF_RETURN_SUNSET=False omits the 🔚 line."""
    from custom_components.adaptive_cover_pro.const import CONF_RETURN_SUNSET

    cfg = {CONF_SUNSET_POS: 30, CONF_RETURN_SUNSET: False}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Return to sunset position at end time" not in summary


def test_manual_ignore_intermediate_shown():
    """CONF_MANUAL_IGNORE_INTERMEDIATE adds 'ignores intermediate positions' annotation."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_IGNORE_INTERMEDIATE,
    )

    cfg = {CONF_MANUAL_IGNORE_INTERMEDIATE: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "ignores intermediate positions" in summary


def test_weather_wind_direction_shown():
    """Wind direction sensor + tolerance render on the weather safety line."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_WIND_DIRECTION_SENSOR,
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    )

    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_WIND_DIRECTION_SENSOR: "sensor.wind_dir",
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE: 45,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "from window ±45°" in summary


def test_weather_bypass_auto_control_warning():
    """CONF_WEATHER_BYPASS_AUTO_CONTROL renders a ⚠️ halt-annotation."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_BYPASS_AUTO_CONTROL,
    )

    cfg = {
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.rain",
        CONF_WEATHER_BYPASS_AUTO_CONTROL: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "halts all automation while triggered" in summary


def test_weather_override_min_mode_shown():
    """CONF_WEATHER_OVERRIDE_MIN_MODE renders '(as minimum)' on the weather line."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_OVERRIDE_MIN_MODE,
    )

    cfg = {
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.rain",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
        CONF_WEATHER_OVERRIDE_MIN_MODE: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    # "(as minimum)" belongs on the weather safety line, not the force line
    wx_line = next(ln for ln in summary.splitlines() if "Weather safety" in ln)
    assert "(as minimum)" in wx_line


def test_force_override_min_mode_shown():
    """CONF_FORCE_OVERRIDE_MIN_MODE renders '(as minimum)' on the force line."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_FORCE_OVERRIDE_MIN_MODE,
    )

    cfg = {
        CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.safety"],
        CONF_FORCE_OVERRIDE_POSITION: 100,
        CONF_FORCE_OVERRIDE_MIN_MODE: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    force_line = next(ln for ln in summary.splitlines() if "Force override" in ln)
    assert "(as minimum)" in force_line


def test_custom_position_min_mode_shown():
    """custom_position_min_mode_N renders '(as minimum)' on the custom slot line."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.movie",
        "custom_position_1": 40,
        "custom_position_priority_1": 77,
        "custom_position_min_mode_1": True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    custom_line = next(ln for ln in summary.splitlines() if "Custom #1" in ln)
    assert "(as minimum)" in custom_line


def test_weather_state_list_in_cloud_line():
    """CONF_WEATHER_STATE list renders as 'weather in {state, state}' on the cloud line."""
    from custom_components.adaptive_cover_pro.const import CONF_WEATHER_STATE

    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_WEATHER_ENTITY: "weather.home",
        CONF_WEATHER_STATE: ["cloudy", "rainy"],
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "weather in {cloudy, rainy}" in summary


def test_outside_threshold_shown_on_climate_line():
    """CONF_OUTSIDE_THRESHOLD annotates the outside temp entity on the climate line."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor",
        CONF_OUTSIDE_THRESHOLD: 28,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "sensor.outdoor > 28°C" in summary


def test_transparent_blind_note_on_climate_line():
    """CONF_TRANSPARENT_BLIND adds a 'transparent blind' note on climate line."""
    from custom_components.adaptive_cover_pro.const import CONF_TRANSPARENT_BLIND

    cfg = {CONF_CLIMATE_MODE: True, CONF_TRANSPARENT_BLIND: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    climate_line = next(ln for ln in summary.splitlines() if "Climate mode" in ln)
    assert "transparent blind" in climate_line


def test_winter_close_insulation_note_on_climate_line():
    """CONF_WINTER_CLOSE_INSULATION adds a 'closes fully in winter' note."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WINTER_CLOSE_INSULATION,
    )

    cfg = {CONF_CLIMATE_MODE: True, CONF_WINTER_CLOSE_INSULATION: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "closes fully in winter for insulation" in summary


def test_open_close_threshold_in_position_limits():
    """CONF_OPEN_CLOSE_THRESHOLD renders under Position Limits."""
    from custom_components.adaptive_cover_pro.const import CONF_OPEN_CLOSE_THRESHOLD

    cfg = {CONF_MIN_POSITION: 0, CONF_OPEN_CLOSE_THRESHOLD: 50}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Open/close threshold: 50%" in summary


def test_interp_start_end_in_position_limits():
    """CONF_INTERP_START and CONF_INTERP_END render as 'Calibration N→M' when set."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_INTERP_END,
        CONF_INTERP_START,
    )

    cfg = {
        CONF_MIN_POSITION: 0,
        CONF_INTERP: True,
        CONF_INTERP_START: 10,
        CONF_INTERP_END: 90,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Calibration 10→90" in summary


def test_interp_without_start_end_falls_back():
    """CONF_INTERP=True without start/end falls back to the plain 'on' note."""
    cfg = {CONF_MIN_POSITION: 0, CONF_INTERP: True}
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "Position calibration on" in summary


def test_min_only_qualifier_is_min_specific():
    """When only enable_min_position is True, qualifier reads 'min during sun tracking only'."""
    cfg = {
        CONF_MIN_POSITION: 10,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MIN_POSITION: True,
        CONF_ENABLE_MAX_POSITION: False,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "(min during sun tracking only)" in summary


def test_max_only_qualifier_is_max_specific():
    """When only enable_max_position is True, qualifier reads 'max during sun tracking only'."""
    cfg = {
        CONF_MIN_POSITION: 10,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MIN_POSITION: False,
        CONF_ENABLE_MAX_POSITION: True,
    }
    summary = _build_config_summary(cfg, SensorType.BLIND)
    assert "(max during sun tracking only)" in summary


async def test_compute_todays_sun_times_returns_expected_shape():
    """_compute_todays_sun_times returns a dict with all expected keys in local TZ."""
    import datetime as dt
    from unittest.mock import MagicMock, patch

    from custom_components.adaptive_cover_pro.config_flow import (
        _compute_todays_sun_times,
    )

    fake_sunrise_utc = dt.datetime(2026, 4, 18, 10, 30, tzinfo=dt.UTC)
    fake_sunset_utc = dt.datetime(2026, 4, 18, 23, 45, tzinfo=dt.UTC)
    fake_solar_start_utc = dt.datetime(2026, 4, 18, 11, 14, tzinfo=dt.UTC)
    fake_solar_end_utc = dt.datetime(2026, 4, 18, 22, 30, tzinfo=dt.UTC)

    sun_data_stub = MagicMock()
    sun_data_stub.sunrise.return_value = fake_sunrise_utc
    sun_data_stub.sunset.return_value = fake_sunset_utc

    geom_stub = MagicMock()
    geom_stub.solar_times.return_value = (fake_solar_start_utc, fake_solar_end_utc)

    hass = MagicMock()
    hass.config.time_zone = "UTC"

    async def _run_executor(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = _run_executor

    with (
        patch(
            "custom_components.adaptive_cover_pro.state.sun_provider."
            "SunProvider.create_sun_data",
            return_value=sun_data_stub,
        ),
        patch(
            "custom_components.adaptive_cover_pro.engine.sun_geometry.SunGeometry",
            return_value=geom_stub,
        ),
    ):
        result = await _compute_todays_sun_times(
            hass, {CONF_SUNRISE_OFFSET: 12, CONF_SUNSET_OFFSET: -12}
        )

    assert result is not None
    assert set(result.keys()) == {
        "sunrise_raw",
        "sunset_raw",
        "sunrise_eff",
        "sunset_eff",
        "solar_start",
        "solar_end",
    }
    # All datetimes returned as naive (tz-stripped after local conversion)
    for value in result.values():
        if value is not None:
            assert value.tzinfo is None
    # Effective = raw + offset
    assert result["sunrise_eff"] - result["sunrise_raw"] == dt.timedelta(minutes=12)
    assert result["sunset_eff"] - result["sunset_raw"] == dt.timedelta(minutes=-12)


async def test_compute_todays_sun_times_returns_none_on_failure():
    """_compute_todays_sun_times returns None when SunProvider raises."""
    from unittest.mock import MagicMock, patch

    from custom_components.adaptive_cover_pro.config_flow import (
        _compute_todays_sun_times,
    )

    hass = MagicMock()
    hass.config.time_zone = "UTC"

    async def _run_executor(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = _run_executor

    with patch(
        "custom_components.adaptive_cover_pro.state.sun_provider."
        "SunProvider.create_sun_data",
        side_effect=RuntimeError("no location"),
    ):
        result = await _compute_todays_sun_times(hass, {})

    assert result is None
