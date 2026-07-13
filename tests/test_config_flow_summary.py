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
    CONF_BUILDING_PROFILE_ID,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DAYTIME_GATE_TEMPLATE_MODE,
    CONF_ENABLE_SUN_TRACKING,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_USE_MY,
    CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
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
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
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
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TEMPLATE,
    CONF_MOTION_TIMEOUT,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_SILL_HEIGHT,
    CONF_START_TIME,
    CONF_START_ENTITY,
    CONF_END_ENTITY,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_VENETIAN_TILT_SKIP_ABOVE,
    DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_RAINING_TEMPLATE,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_TRANSIT_TIMEOUT,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    CoverType,
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
            # Safety-priority slot 5 — the merged force override (issue #563)
            "custom_position_sensors_5": ["binary_sensor.wind_alert"],
            "custom_position_5": 100,
            "custom_position_priority_5": 100,
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
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Vertical Blind" in summary


def test_summary_shows_awning_type():
    """Awning type label appears in Your Cover section."""
    summary = _build_config_summary({}, CoverType.AWNING)
    assert "Horizontal Awning" in summary


def test_summary_shows_tilt_type():
    """Tilt type label appears in Your Cover section."""
    summary = _build_config_summary({}, CoverType.TILT)
    assert "Venetian" in summary or "Tilt" in summary


def test_summary_no_type_graceful():
    """None sensor type does not crash."""
    summary = _build_config_summary({}, None)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_empty_config_returns_string():
    """Empty config returns a non-empty string without crashing."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_dry_run_banner_shown_when_enabled():
    """Dry-run banner is surfaced (and first) when dry_run is on."""
    from custom_components.adaptive_cover_pro.const import CONF_DRY_RUN

    summary = _build_config_summary({CONF_DRY_RUN: True}, CoverType.BLIND)
    assert "Dry-run mode is ON" in summary
    assert "covers will NOT move" in summary
    # Must lead the summary, above the Your Cover section.
    assert summary.index("Dry-run mode is ON") < summary.index("**Your Cover**")


def test_dry_run_banner_absent_when_disabled():
    """No dry-run banner when the flag is off or unset."""
    from custom_components.adaptive_cover_pro.const import CONF_DRY_RUN

    assert "Dry-run mode" not in _build_config_summary(
        {CONF_DRY_RUN: False}, CoverType.BLIND
    )
    assert "Dry-run mode" not in _build_config_summary({}, CoverType.BLIND)


def test_summary_shows_position_matching_on_when_enabled():
    """Enabling matching surfaces the 'on' line in the summary (issue #591)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_POSITION_MATCHING,
    )

    summary = _build_config_summary(
        {CONF_ENABLE_POSITION_MATCHING: True}, CoverType.BLIND
    )
    assert "Position matching on" in summary
    assert "Position matching off" not in summary


def test_summary_shows_position_matching_off_by_default():
    """Default/unset → matching is off, summary shows the 'off' line (issue #591)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_POSITION_MATCHING,
    )

    for cfg in ({CONF_ENABLE_POSITION_MATCHING: False}, {}):
        summary = _build_config_summary(cfg, CoverType.BLIND)
        assert "Position matching off" in summary
        assert "Position matching on" not in summary


def test_entity_included_in_your_cover():
    """Cover entity ID appears in the Your Cover line."""
    cfg = {CONF_ENTITIES: ["cover.living_room"]}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "cover.living_room" in summary


def test_minimal_vertical_contains_key_fields():
    """Minimal vertical config shows entity, dimensions, and azimuth."""
    cfg = _minimal_vertical()
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.AWNING)
    assert "3.0m awning" in summary
    assert "15°" in summary


def test_geometry_tilt_shows_tilt_fields():
    """Tilt slat dimensions appear."""
    cfg = {CONF_TILT_DEPTH: 3.0, CONF_TILT_DISTANCE: 4.0, CONF_TILT_MODE: "mode1"}
    summary = _build_config_summary(cfg, CoverType.TILT)
    assert "slat depth 3.0cm" in summary
    assert "spacing 4.0cm" in summary
    assert "mode1" in summary


def test_geometry_louvered_roof_shows_slat_and_pitch_fields():
    """Louvered roof renders the shared slat block plus the roof-plane pitch."""
    from custom_components.adaptive_cover_pro.const import CONF_ROOF_PITCH

    cfg = {
        CONF_TILT_DEPTH: 3.0,
        CONF_TILT_DISTANCE: 2.0,
        CONF_TILT_MODE: "mode2",
        CONF_ROOF_PITCH: 15,
    }
    summary = _build_config_summary(cfg, CoverType.LOUVERED_ROOF)
    assert "slat depth 3.0cm" in summary
    assert "spacing 2.0cm" in summary
    assert "roof pitch 15° from horizontal" in summary
    assert "Louvered Roof" in summary


def test_geometry_venetian_shows_retract_threshold_default():
    """Venetian summary includes the upper retract threshold at the default value."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert f"skip tilt when position > {DEFAULT_VENETIAN_TILT_SKIP_ABOVE}%" in summary


def test_geometry_venetian_shows_retract_threshold_custom():
    """Venetian summary reflects a custom upper threshold."""
    cfg = {CONF_VENETIAN_TILT_SKIP_ABOVE: 80}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "skip tilt when position > 80%" in summary


def test_geometry_venetian_skip_suppress_line_hidden_in_neutral_mode():
    """The suppress-mode line is absent under the default neutral skip mode."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert "suppress tilt at/above" not in summary


def test_geometry_venetian_skip_suppress_line_shown_in_suppress_mode():
    """Suppress mode renders an extra 'suppress tilt at/above N%' line."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SKIP_ABOVE,
        CONF_VENETIAN_TILT_SKIP_MODE,
        VENETIAN_TILT_SKIP_SUPPRESS,
    )

    cfg = {
        CONF_VENETIAN_TILT_SKIP_MODE: VENETIAN_TILT_SKIP_SUPPRESS,
        CONF_VENETIAN_TILT_SKIP_ABOVE: 90,
    }
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "suppress tilt at/above 90% (no neutral send)" in summary


def test_geometry_venetian_shows_max_tilt_default():
    """Venetian summary includes max tilt at the default value (100%)."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert "max tilt 100%" in summary


def test_geometry_venetian_shows_max_tilt_custom():
    """Venetian summary reflects a custom max_tilt value."""
    from custom_components.adaptive_cover_pro.const import CONF_MAX_TILT

    cfg = {CONF_MAX_TILT: 70}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "max tilt 70%" in summary


def test_geometry_venetian_shows_min_tilt_default():
    """Venetian summary includes min tilt at the default value (0%)."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert "min tilt 0%" in summary


def test_geometry_venetian_shows_min_tilt_custom():
    """Venetian summary reflects a custom min_tilt value."""
    from custom_components.adaptive_cover_pro.const import CONF_MIN_TILT

    cfg = {CONF_MIN_TILT: 15}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "min tilt 15%" in summary


def test_geometry_venetian_shows_post_settle_hold_default():
    """Venetian summary includes post-settle hold at the default value (3.0 s)."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert "post-settle hold 3.0s" in summary


def test_geometry_venetian_shows_post_settle_hold_custom():
    """Venetian summary reflects a custom post_settle_hold value."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_HOLD,
    )

    cfg = {CONF_VENETIAN_POST_SETTLE_HOLD: 5.5}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "post-settle hold 5.5s" in summary


def test_geometry_venetian_shows_backrotate_lag_default():
    """Venetian summary includes back-rotate publish lag at the default (45.0 s)."""
    summary = _build_config_summary({}, CoverType.VENETIAN)
    assert "back-rotate publish lag 45.0s" in summary


def test_geometry_venetian_shows_backrotate_lag_custom():
    """Venetian summary reflects a custom back-rotate publish lag value."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    )

    cfg = {CONF_VENETIAN_BACKROTATE_PUBLISH_LAG: 60.0}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "back-rotate publish lag 60.0s" in summary


def test_geometry_venetian_drift_reset_shows_default_direction():
    """Drift-reset summary names the default (open) direction when enabled."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_RESET_THRESHOLD,
    )

    cfg = {CONF_VENETIAN_TILT_RESET_THRESHOLD: 300}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "drift-reset every 300% accumulated tilt (via open)" in summary


def test_geometry_venetian_drift_reset_shows_close_direction():
    """Drift-reset summary reflects a configured close direction."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_RESET_DIRECTION,
        CONF_VENETIAN_TILT_RESET_THRESHOLD,
        VENETIAN_TILT_RESET_CLOSE,
    )

    cfg = {
        CONF_VENETIAN_TILT_RESET_THRESHOLD: 300,
        CONF_VENETIAN_TILT_RESET_DIRECTION: VENETIAN_TILT_RESET_CLOSE,
    }
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "(via close)" in summary


def test_geometry_oscillating_awning_shows_housing_offset():
    """Oscillating-awning summary renders the housing offset when configured."""
    from custom_components.adaptive_cover_pro.const import CONF_AWNING_HOUSING_OFFSET

    cfg = {CONF_AWNING_HOUSING_OFFSET: 0.25}
    summary = _build_config_summary(cfg, CoverType.OSCILLATING_AWNING)
    assert "0.25m housing offset" in summary


def test_geometry_oscillating_awning_housing_offset_omitted_when_unset():
    """Housing offset line is absent when the field is not configured."""
    summary = _build_config_summary({}, CoverType.OSCILLATING_AWNING)
    assert "housing offset" not in summary


def test_geometry_roof_window_shows_pitch():
    """Roof-window summary renders the glass pitch (#212)."""
    from custom_components.adaptive_cover_pro.const import CONF_ROOF_PITCH

    cfg = {CONF_HEIGHT_WIN: 1.2, CONF_DISTANCE: 0.4, CONF_ROOF_PITCH: 35}
    summary = _build_config_summary(cfg, CoverType.ROOF_WINDOW)
    assert "roof pitch 35° from horizontal" in summary


def test_geometry_roof_window_shows_ridge_height_when_set():
    """Roof-window summary renders the roof-above-window ridge height when > 0."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    cfg = {CONF_ROOF_PITCH: 40, CONF_ROOF_HEIGHT_ABOVE: 1.5}
    summary = _build_config_summary(cfg, CoverType.ROOF_WINDOW)
    assert "1.5m roof above window" in summary


def test_geometry_roof_window_ridge_height_omitted_when_zero():
    """Ridge-height line is absent when roof_height_above is 0 (gate disabled)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    cfg = {CONF_ROOF_PITCH: 40, CONF_ROOF_HEIGHT_ABOVE: 0.0}
    summary = _build_config_summary(cfg, CoverType.ROOF_WINDOW)
    assert "roof above window" not in summary


# ---------------------------------------------------------------------------
# Section 2: How It Decides — Sun Tracking
# ---------------------------------------------------------------------------


def test_sun_tracking_always_present():
    """☀️ solar tracking bullet always appears."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Tracks the sun" in summary


def test_sun_tracking_fov_shown():
    """Azimuth and FOV are embedded in the solar tracking bullet."""
    cfg = {CONF_AZIMUTH: 200, CONF_FOV_LEFT: 80, CONF_FOV_RIGHT: 70}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "200°" in summary
    assert "80°" in summary
    assert "70°" in summary


def test_sun_tracking_optional_elevation_omitted():
    """Elevation limits are absent when not configured."""
    cfg = {CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "above" not in summary
    assert "below" not in summary


def test_sun_tracking_elevation_shown_when_set():
    """Elevation limits appear when configured."""
    cfg = {CONF_AZIMUTH: 180, CONF_MIN_ELEVATION: 5, CONF_MAX_ELEVATION: 70}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "above 5°" in summary
    assert "below 70°" in summary


def test_minimize_movements_omitted_when_disabled():
    """No minimize-movements line when the feature is off (default)."""
    summary = _build_config_summary({CONF_AZIMUTH: 180}, CoverType.BLIND)
    assert "Minimize movements" not in summary


def test_minimize_movements_single_step():
    """N=1 surfaces the 'straight to full coverage' wording."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MAX_COVERAGE_STEPS,
        CONF_MINIMIZE_MOVEMENTS,
    )

    cfg = {
        CONF_AZIMUTH: 180,
        CONF_MINIMIZE_MOVEMENTS: True,
        CONF_MAX_COVERAGE_STEPS: 1,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Minimize movements" in summary
    assert "full coverage" in summary
    assert "1 step" in summary


def test_minimize_movements_multi_step():
    """N>1 surfaces the step count."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MAX_COVERAGE_STEPS,
        CONF_MINIMIZE_MOVEMENTS,
    )

    cfg = {
        CONF_AZIMUTH: 180,
        CONF_MINIMIZE_MOVEMENTS: True,
        CONF_MAX_COVERAGE_STEPS: 3,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Minimize movements" in summary
    assert "up to 3 steps" in summary


def test_minimize_movements_absent_when_sun_tracking_disabled():
    """The line lives under the ☀️ tracking branch; gone when tracking is off."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_SUN_TRACKING,
        CONF_MINIMIZE_MOVEMENTS,
    )

    cfg = {
        CONF_AZIMUTH: 180,
        CONF_ENABLE_SUN_TRACKING: False,
        CONF_MINIMIZE_MOVEMENTS: True,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Minimize movements" not in summary


# ---------------------------------------------------------------------------
# Section 2: Timing
# ---------------------------------------------------------------------------


def test_automation_times_shown():
    """Start and end times appear in the timing bullet."""
    cfg = {CONF_START_TIME: "07:00", CONF_END_TIME: "21:00"}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.sunrise_time" in summary
    assert "sensor.sunset_time" in summary
    assert "from 00:00:00" not in summary
    assert "until 00:00:00" not in summary


def test_blank_start_end_time_not_shown_as_literal():
    """Blank-sentinel start/end times must not render as 'from 00:00:00'.

    Regression for issue #492: a cleared TimeSelector coerces to the blank
    sentinel '00:00:00'. The summary must treat it as unset (sunrise/sunset)
    rather than printing a literal midnight window.
    """
    from custom_components.adaptive_cover_pro.const import BLANK_TIME

    cfg = {CONF_START_TIME: BLANK_TIME, CONF_END_TIME: BLANK_TIME}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "from 00:00:00" not in summary
    assert "until 00:00:00" not in summary
    assert "Active during daylight" in summary


def test_explicit_start_time_used_when_no_entity():
    """Static time is used when no entity is configured."""
    cfg = {CONF_START_TIME: "07:00", CONF_END_TIME: "21:00"}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.sunrise_time" in summary
    assert "until 21:30" in summary


def test_sunset_position_shown():
    """Sunset/end-of-day position appears in timing bullet."""
    cfg = {CONF_SUNSET_POS: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sunset" in summary.lower() or "end time" in summary.lower()


def test_sunrise_position_shown_when_sunset_pos_configured():
    """After sunrise line appears when sunset_pos is configured."""
    cfg = {CONF_SUNSET_POS: 80, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunrise" in summary
    assert "tracking resumes" in summary


def test_sunrise_position_not_shown_without_sunset_pos():
    """After sunrise line absent when no sunset_pos is configured."""
    cfg = {CONF_DEFAULT_HEIGHT: 60}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "tracking resumes" not in summary
    assert "After sunrise" not in summary


def test_sunrise_offset_positive_shown():
    """Positive sunrise offset shown as (+N min)."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: 60, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunrise (+60 min)" in summary


def test_sunrise_offset_negative_shown():
    """Negative sunrise offset shown as (-N min)."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: -30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunrise (-30 min)" in summary


def test_sunrise_offset_zero_omitted():
    """Zero sunrise offset shows no parenthetical annotation."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNRISE_OFFSET: 0, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunrise →" in summary
    assert "(+" not in summary.split("After sunrise")[1].split("\n")[0]


def test_sunset_offset_positive_shown():
    """Positive sunset offset shown as (+N min) on the sunset line."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: 30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunset (+30 min)" in summary


def test_sunset_offset_negative_shown():
    """Negative sunset offset shown as (-N min) on the sunset line."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: -30, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunset (-30 min)" in summary


def test_sunset_offset_zero_omitted():
    """Zero sunset offset shows no parenthetical annotation."""
    cfg = {CONF_SUNSET_POS: 80, CONF_SUNSET_OFFSET: 0, CONF_DEFAULT_HEIGHT: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "After sunset →" in summary
    assert "(+" not in summary.split("After sunset")[1].split("\n")[0]


def test_sunrise_shows_default_position():
    """After sunrise line shows the default position percentage."""
    cfg = {CONF_SUNSET_POS: 80, CONF_DEFAULT_HEIGHT: 45}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Blind spot" not in summary


def test_blind_spot_shown_when_enabled():
    """Blind spot bullet shows the degree range."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        CONF_BLIND_SPOT_ELEVATION: 40,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Blind spot" in summary
    assert "10°" in summary
    assert "20°" in summary
    assert "40°" in summary
    assert "acceptance edge" in summary
    # Single slot renders exactly one blind-spot line.
    assert summary.count("Blind spot") == 1


def test_blind_spot_below_mode_wording():
    """A 'below' (default) elevation slot reads 'up to {elev}°' (#702)."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        CONF_BLIND_SPOT_ELEVATION: 40,
        "blind_spot_elevation_mode": "below",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "up to 40°" in summary
    assert "above 40°" not in summary


def test_blind_spot_above_mode_wording():
    """An 'above' elevation slot reads 'above {elev}°' (#702)."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        CONF_BLIND_SPOT_ELEVATION: 40,
        "blind_spot_elevation_mode": "above",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "above 40°" in summary
    assert "up to 40°" not in summary


def test_blind_spot_multiple_slots_render_one_line_each():
    """Two configured slots produce two blind-spot summary lines (#701)."""
    cfg = {
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: 10,
        CONF_BLIND_SPOT_RIGHT: 20,
        "blind_spot_left_2": 40,
        "blind_spot_right_2": 60,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert summary.count("Blind spot") == 2
    assert "10°" in summary
    assert "60°" in summary


# ---------------------------------------------------------------------------
# Section 2: Glare Zones
# ---------------------------------------------------------------------------


def test_glare_zones_hidden_when_disabled():
    """Glare zone bullet absent when not enabled."""
    cfg = {CONF_ENABLE_GLARE_ZONES: False, CONF_WINDOW_WIDTH: 1.5}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Glare zones" not in summary


def test_glare_zones_shown_when_enabled():
    """Glare zone bullet appears with zone names and window width."""
    cfg = {
        CONF_ENABLE_GLARE_ZONES: True,
        CONF_WINDOW_WIDTH: 1.5,
        "glare_zone_1_name": "Desk",
        "glare_zone_2_name": "",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Glare zones" in summary
    assert "1.50m" in summary
    assert "Desk" in summary


def test_glare_zones_not_shown_for_awning():
    """Glare zone bullet absent for awning type."""
    cfg = {CONF_ENABLE_GLARE_ZONES: True, CONF_WINDOW_WIDTH: 1.0}
    summary = _build_config_summary(cfg, CoverType.AWNING)
    assert "Glare zones" not in summary


def test_glare_zones_summary_omits_z_when_all_zones_floor_level():
    """No 'Z height' tag when every named zone has Z=0."""
    cfg = {
        CONF_ENABLE_GLARE_ZONES: True,
        CONF_WINDOW_WIDTH: 1.5,
        "glare_zone_1_name": "Desk",
        "glare_zone_1_z": 0.0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Z height" not in summary


def test_glare_zones_summary_shows_z_when_any_zone_above_floor():
    """'Z height' tag surfaces when at least one named zone has Z > 0."""
    cfg = {
        CONF_ENABLE_GLARE_ZONES: True,
        CONF_WINDOW_WIDTH: 1.5,
        "glare_zone_1_name": "Eye",
        "glare_zone_1_z": 1.1,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Z height" in summary
    assert "1.10m" in summary


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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Climate mode" in summary
    assert "16" in summary
    assert "24" in summary
    assert "sensor.temp" in summary


def test_climate_line_shows_area_resolved_sensor():
    """With no explicit temp sensor and auto-resolve on, the climate line notes
    the sensor is taken from the cover's area (issue #786).
    """
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_LOW: 16,
        CONF_TEMP_HIGH: 24,
        # No CONF_TEMP_ENTITY; auto-resolve defaults on.
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "area temperature sensor" in summary
    assert "using sensor." not in summary


def test_climate_line_area_resolve_suppressed_when_explicit_sensor():
    """An explicit temp sensor takes precedence — no area note."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_ENTITY: "sensor.explicit",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.explicit" in summary
    assert "area temperature sensor" not in summary


def test_climate_line_no_area_note_when_auto_resolve_disabled():
    """Auto-resolve off and no explicit sensor → no area note."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_AUTO_RESOLVE_TEMP_FROM_AREA: False,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "area temperature sensor" not in summary


def test_climate_weather_entity_shown():
    """Weather entity appears in climate bullet."""
    cfg = {CONF_CLIMATE_MODE: True, CONF_WEATHER_ENTITY: "weather.home"}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "500 lx" in summary
    assert "150 W/m²" in summary


def test_climate_cloud_coverage_shown():
    """Cloud coverage threshold appears in cloud suppression bullet."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        CONF_CLOUD_COVERAGE_THRESHOLD: 60,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "60%" in summary
    assert "Cloud suppression" in summary


def test_cloud_suppression_hold_time_shown_when_positive():
    """A non-zero hold-time renders a smoothing-hold suffix (issue #864)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_SUPPRESSION_HOLD_TIME,
    )

    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_CLOUD_SUPPRESSION_HOLD_TIME: 120,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "smoothing hold 120s" in summary


def test_cloud_suppression_hold_time_hidden_when_zero():
    """A zero hold-time (default) renders no smoothing-hold suffix (issue #864)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_SUPPRESSION_HOLD_TIME,
    )

    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_CLOUD_SUPPRESSION_HOLD_TIME: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "smoothing hold" not in summary


def test_cloud_suppression_release_band_shown_when_set():
    """Configured release thresholds render a hysteresis release detail (#864)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD,
        CONF_LUX_RELEASE_THRESHOLD,
    )

    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_LUX_RELEASE_THRESHOLD: 8000,
        CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        CONF_CLOUD_COVERAGE_THRESHOLD: 75,
        CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD: 50,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "8000" in summary
    assert "50" in summary
    # The release detail is labelled as a release/hysteresis clause.
    assert "release" in summary.lower()


def test_light_sensors_without_suppression_noted():
    """Light sensors configured but suppression off shows informational note."""
    cfg = {CONF_LUX_ENTITY: "sensor.lux", CONF_CLOUD_SUPPRESSION: False}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "lux" in summary
    assert "cloud suppression is off" in summary


def test_is_sunny_sensor_shown_with_suppression():
    """is_sunny_sensor appears in cloud suppression bullet when suppression on (issue #363)."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_IS_SUNNY_SENSOR: "binary_sensor.sun_on_window",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is_sunny=binary_sensor.sun_on_window" in summary


def test_is_sunny_template_shows_placeholder():
    """is_sunny template-only shows the [template] marker (issue #639)."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_IS_SUNNY_TEMPLATE: "{{ is_state('sun.sun', 'above_horizon') }}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is_sunny=[template]" in summary


def test_is_sunny_sensor_takes_priority_over_template_in_summary():
    """When both are set, the entity id is shown (sensor is the explicit pick)."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_IS_SUNNY_SENSOR: "binary_sensor.sun_on_window",
        CONF_IS_SUNNY_TEMPLATE: "{{ true }}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is_sunny=binary_sensor.sun_on_window" in summary


def test_presence_template_shows_placeholder():
    """Presence template-only renders the [template] marker on the climate line."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_PRESENCE_TEMPLATE: "{{ is_state('input_boolean.home', 'on') }}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "presence: [template]" in summary


def test_weather_is_raining_template_engages_section():
    """A template-only is-raining override appears in the weather section (#639)."""
    cfg = {
        CONF_WEATHER_IS_RAINING_TEMPLATE: "{{ states('sensor.rain_rate')|float > 0 }}",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is-raining" in summary


def test_weather_is_windy_template_engages_section():
    """A template-only is-windy override appears in the weather section (#639)."""
    cfg = {
        CONF_WEATHER_IS_WINDY_TEMPLATE: "{{ states('sensor.gust')|float > 30 }}",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is-windy" in summary


def test_is_sunny_sensor_without_suppression_noted():
    """is_sunny_sensor configured but suppression off shows informational note."""
    cfg = {
        CONF_IS_SUNNY_SENSOR: "binary_sensor.sun_on_window",
        CONF_CLOUD_SUPPRESSION: False,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "binary_sensor.sun_on_window" in summary
    assert "cloud suppression is off" in summary


# ---------------------------------------------------------------------------
# Section 2: Manual Override
# ---------------------------------------------------------------------------


def test_manual_override_always_present():
    """Manual override bullet always appears (it's always active)."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Manual override" in summary


def test_manual_override_duration_shown():
    """Override duration dict appears formatted in the manual override bullet (issue #148)."""
    cfg = {CONF_MANUAL_OVERRIDE_DURATION: {"hours": 5, "minutes": 0, "seconds": 0}}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "5 h" in summary
    # Raw dict must NOT appear
    assert "{'hours'" not in summary
    assert "hours" not in summary or "5 h" in summary


def test_transit_timeout_non_default_shown_in_manual_override_section():
    """Non-default transit_timeout appears in the manual override line, not Position Limits."""
    non_default = DEFAULT_TRANSIT_TIMEOUT_SECONDS + 30
    cfg = {CONF_TRANSIT_TIMEOUT: non_default}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    lines = summary.splitlines()
    mo_line = next((ln for ln in lines if "Manual override" in ln), None)
    assert mo_line is not None, "Manual override line missing from summary"
    assert (
        f"transit timeout: {non_default}s" in mo_line
    ), f"Expected 'transit timeout: {non_default}s' in manual override line; got: {mo_line!r}"
    # Must NOT appear under Position Limits
    in_position_limits = False
    in_pl_section = False
    for line in lines:
        if "**Position Limits**" in line:
            in_pl_section = True
        elif line.startswith("**") and in_pl_section:
            in_pl_section = False
        if in_pl_section and "transit timeout" in line.lower():
            in_position_limits = True
    assert (
        not in_position_limits
    ), "transit_timeout must not appear under Position Limits"


def test_transit_timeout_default_not_shown():
    """Default transit_timeout is not surfaced in the summary."""
    cfg = {CONF_TRANSIT_TIMEOUT: DEFAULT_TRANSIT_TIMEOUT_SECONDS}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "transit timeout" not in summary.lower()


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
        summary = _build_config_summary(cfg, CoverType.BLIND)
        assert "{'hours'" not in summary
        assert "{" not in summary or "pauses for 3 h 30 min" in summary


def test_manual_override_reset_shown():
    """Reset-on-new-command flag appears."""
    cfg = {CONF_MANUAL_OVERRIDE_RESET: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "resets on next move" in summary


def test_manual_override_input_entities_shown():
    """Configured input-override sensors render in the manual override section (#688)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    )

    cfg = {
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES: [
            "binary_sensor.a",
            "binary_sensor.b",
        ]
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    mo_line = next((ln for ln in summary.splitlines() if "Manual override" in ln), None)
    assert mo_line is not None
    assert "input-sensor override: 2 sensor(s)" in mo_line


def test_manual_override_input_entities_omitted_when_unset():
    """No input-override sensors → no input-sensor line in the summary."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "input-sensor override" not in summary


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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "🚶 Occupancy" in summary
    assert "300s" in summary
    assert "60%" in summary


def test_motion_section_hidden_when_no_sensors():
    """Motion bullet absent when no motion sensors configured."""
    cfg = {CONF_MOTION_SENSORS: []}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "🚶 Occupancy" not in summary


# ---------------------------------------------------------------------------
# Section 2: Weather Override
# ---------------------------------------------------------------------------


def test_weather_override_section_hidden_when_no_sensors():
    """Weather safety bullet absent when no weather sensors configured."""
    cfg = {}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Weather safety" not in summary


def test_weather_override_wind_sensor_shown():
    """Wind threshold appears in weather safety bullet."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_TIMEOUT: 120,
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "rain > 5.0" in summary


def test_weather_override_binary_sensors_shown():
    """is-raining, is-windy, and severe sensor count appear."""
    cfg = {
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.rain",
        CONF_WEATHER_IS_WINDY_SENSOR: "binary_sensor.wind",
        CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.hail"],
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is-raining" in summary
    assert "is-windy" in summary
    assert "severe weather" in summary


# --- Master toggle warning (issue #719) ---

_WX_DISABLED_PHRASE = "turned OFF — weather overrides are ignored"


def test_weather_override_disabled_with_sensors_shows_warning():
    """Sensors configured + master toggle OFF → warning, no normal retract line."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_OVERRIDE_POSITION: 0,
        CONF_WEATHER_ENABLED: False,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert _WX_DISABLED_PHRASE in summary
    # The normal "retract to" rule line must NOT render when disabled.
    assert "retract to" not in summary


def test_weather_override_enabled_with_sensors_shows_rule_not_warning():
    """Sensors configured + master toggle ON → normal line, no warning."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 60,
        CONF_WEATHER_OVERRIDE_POSITION: 0,
        CONF_WEATHER_ENABLED: True,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "retract to" in summary
    assert _WX_DISABLED_PHRASE not in summary


def test_weather_override_no_sensors_shows_neither_warning_nor_rule():
    """No weather sources → neither the warning nor the normal rule line."""
    summary = _build_config_summary({CONF_WEATHER_ENABLED: False}, CoverType.BLIND)
    assert _WX_DISABLED_PHRASE not in summary
    assert "Weather safety" not in summary


# ---------------------------------------------------------------------------
# Section 2: Force Override
# ---------------------------------------------------------------------------


def test_force_override_section_hidden_when_no_sensors():
    """Force override bullet absent when no force sensors configured."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Force override" not in summary


def test_safety_priority_slot_shown_with_position():
    """A priority-100 slot renders with multi-sensor trigger, position, safety note."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.wind", "binary_sensor.hail"],
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Custom #5" in summary
    assert "any of 2 sensors" in summary
    assert "100%" in summary
    assert "safety: acts outside the time window" in summary
    assert "[100]" in summary


# ---------------------------------------------------------------------------
# Section 2: Safety-priority bypass warning (issue #711)
# ---------------------------------------------------------------------------

_SAFETY_BYPASS_PHRASE = (
    "automatic-control toggle, manual override, and the start/end time window"
)


def test_safety_slot_with_sensor_emits_bypass_warning():
    """A sensor-bound priority-100 slot surfaces the ⚠️ bypass warning (#711)."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.outside_temp_threshold"],
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "⚠️ Custom #5 is at safety priority (100)" in summary
    assert _SAFETY_BYPASS_PHRASE in summary


def test_safety_slot_template_only_emits_bypass_warning():
    """A template-only priority-100 slot (no sensor) still warns (broadened #711)."""
    cfg = {
        "custom_position_template_5": "{{ states('sensor.x')|float > 18 }}",
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "⚠️ Custom #5 is at safety priority (100)" in summary
    assert _SAFETY_BYPASS_PHRASE in summary


def test_safety_slot_no_trigger_emits_no_warning():
    """A priority-100 slot with no sensor and no template is inert → no warning."""
    cfg = {
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is at safety priority" not in summary


def test_non_safety_slot_emits_no_bypass_warning():
    """A sensor-bound slot below safety priority does not warn."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.outside_temp_threshold"],
        "custom_position_5": 100,
        "custom_position_priority_5": 77,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "is at safety priority" not in summary


def test_safety_bypass_warning_uses_constant():
    """The warning fills {safety} from CUSTOM_POSITION_SAFETY_PRIORITY, not a literal."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.outside_temp_threshold"],
        "custom_position_5": 100,
        "custom_position_priority_5": CUSTOM_POSITION_SAFETY_PRIORITY,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert f"safety priority ({CUSTOM_POSITION_SAFETY_PRIORITY})" in summary


# ---------------------------------------------------------------------------
# Section 3: Position Limits
# ---------------------------------------------------------------------------


def test_position_limits_section_present_with_values():
    """Position Limits section appears when min/max/default are set."""
    cfg = {CONF_MIN_POSITION: 5, CONF_MAX_POSITION: 90, CONF_DEFAULT_HEIGHT: 60}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "during sun tracking only" in summary


def test_position_inverse_state_shown():
    """Inverse state appears in Position Limits."""
    cfg = {CONF_INVERSE_STATE: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Inverse state" in summary


def test_position_inverse_state_hidden_when_false():
    """Inverse state absent when disabled."""
    cfg = {CONF_INVERSE_STATE: False}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Inverse state" not in summary


def test_position_tolerance_shown_when_configured():
    """Position tolerance appears in Position Limits when set."""
    from custom_components.adaptive_cover_pro.const import CONF_POSITION_TOLERANCE

    cfg = {CONF_DEFAULT_HEIGHT: 60, CONF_POSITION_TOLERANCE: 5}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Position tolerance: 5%" in summary


def test_position_tolerance_hidden_when_unset():
    """Position tolerance line is absent when the field is not configured."""
    cfg = {CONF_DEFAULT_HEIGHT: 60}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Position tolerance" not in summary


def test_position_interp_shown():
    """Position calibration flag appears in Position Limits."""
    cfg = {CONF_INTERP: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Position calibration" in summary


def test_delta_position_and_time_shown():
    """Delta position and time appear in Position Limits."""
    cfg = {CONF_DELTA_POSITION: 3, CONF_DELTA_TIME: 5}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "3%" in summary
    assert "5 min" in summary


def test_summary_shows_sun_tracking_min_when_set():
    """Sun-tracking min line appears in Position Limits when min_position_sun_tracking is set."""
    cfg = {
        CONF_MIN_POSITION: 0,
        CONF_MIN_POSITION_SUN_TRACKING: 15,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Sun-tracking min: 15%" in summary


def test_summary_sun_tracking_min_absent_when_not_set():
    """Sun-tracking min line does not appear when min_position_sun_tracking is not set."""
    cfg = {CONF_MIN_POSITION: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Sun-tracking min" not in summary


def test_summary_warns_when_sun_tracking_min_below_min_position():
    """Footgun warning appears when sun_tracking_min < min_position (always-on floor dominates)."""
    cfg = {
        CONF_MIN_POSITION: 20,
        CONF_MIN_POSITION_SUN_TRACKING: 10,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "⚠️" in summary
    assert "10%" in summary
    assert "20%" in summary


def test_summary_no_warning_when_sun_tracking_min_above_min_position():
    """No footgun warning when sun_tracking_min >= min_position (no conflict)."""
    cfg = {
        CONF_MIN_POSITION: 10,
        CONF_MIN_POSITION_SUN_TRACKING: 15,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    # No footgun warning (sun_tracking_min is above min_pos, so it's effective)
    # There may be other ⚠️ in summary, but not for this specific scenario
    assert "sun-tracking floor will be raised" not in summary


# ---------------------------------------------------------------------------
# Section 4: Decision Priority chain
# ---------------------------------------------------------------------------


def test_priority_section_always_present():
    """Decision Priority section always appears."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Decision Priority" in summary


def test_priority_always_on_handlers_active():
    """Manual, Solar, and Default are always shown as ✅."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "✅Manual" in summary
    assert "✅Solar" in summary
    assert "✅Default" in summary


def test_priority_safety_slot_in_chain():
    """A priority-100 slot appears at the head of the priority chain."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.wind"],
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Custom#5(100)" in summary


def test_priority_custom_slot_named_shown_in_decision_priority():
    """A named custom slot renders its name, not Custom#N (issue #910)."""
    cfg = {
        "custom_position_sensors_2": ["binary_sensor.protection_solaire_canicule"],
        "custom_position_2": 10,
        "custom_position_priority_2": 75,
        "custom_position_name_2": "Canicule",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Canicule(75)" in summary
    assert "Custom#2" not in summary


def test_priority_no_force_entry_remains():
    """The standalone Force chain entry is gone (merged into custom slots, #563)."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "Force" not in summary
    assert "Custom#" not in summary


def test_priority_weather_override_active_with_sensors():
    """Weather Override shows ✅ when sensors are configured."""
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Weather" in summary


def test_priority_weather_override_not_configured():
    """Weather Override shows ❌ when no weather sensors are set."""
    summary = _build_config_summary({}, CoverType.BLIND)
    assert "❌Weather" in summary


def test_priority_motion_timeout_active():
    """Motion Timeout shows ✅ when sensors are configured."""
    cfg = {CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_DEFAULT_HEIGHT: 45}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Motion" in summary


def test_priority_cloud_suppression_active():
    """Cloud Suppression shows ✅ when enabled."""
    cfg = {CONF_CLOUD_SUPPRESSION: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Cloud" in summary


def test_priority_climate_active():
    """Climate shows ✅ when climate mode is on."""
    cfg = {CONF_CLIMATE_MODE: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Climate" in summary


def test_priority_glare_zone_active_for_vertical():
    """Glare Zone shows ✅ for vertical blind when enabled."""
    cfg = {CONF_ENABLE_GLARE_ZONES: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "✅Glare" in summary


def test_priority_glare_zone_hidden_for_awning():
    """Glare Zone entry is omitted entirely for awning covers."""
    summary = _build_config_summary({CONF_ENABLE_GLARE_ZONES: True}, CoverType.AWNING)
    assert "Glare" not in summary


def test_priority_glare_zone_hidden_for_tilt():
    """Glare Zone entry is omitted entirely for tilt covers."""
    summary = _build_config_summary({}, CoverType.TILT)
    assert "Glare" not in summary


def test_priority_default_position_reflected():
    """Default handler shows the configured default height in the narrative."""
    cfg = {CONF_DEFAULT_HEIGHT: 75}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "75%" in summary


def test_priority_all_nine_handlers_full_config():
    """Full config shows all nine handlers as ✅ in the priority chain."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, CoverType.BLIND)
    for token in [
        "✅Custom#5(100)",
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
    summary = _build_config_summary(cfg, CoverType.BLIND)

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
    assert "🚶 Occupancy" in summary
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
    # Safety-priority custom position (merged force override)
    assert "Custom #5" in summary
    assert "safety: acts outside the time window" in summary
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Sun tracking disabled" in summary
    assert "Tracks the sun" not in summary


def test_sun_tracking_enabled_shows_tracking_message():
    """When enable_sun_tracking is True (default), summary says it tracks the sun."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True, CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Tracks the sun" in summary
    assert "Sun tracking disabled" not in summary


def test_sun_tracking_default_enabled_shows_tracking_message():
    """When enable_sun_tracking is absent (defaults to True), summary shows tracking."""
    cfg = {CONF_AZIMUTH: 180}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Tracks the sun" in summary
    assert "Sun tracking disabled" not in summary


def test_glare_zones_shown_when_sun_tracking_disabled():
    """Glare zones remain in summary when sun tracking is off (issue #238)."""
    cfg = {
        CONF_ENABLE_SUN_TRACKING: False,
        CONF_ENABLE_GLARE_ZONES: True,
        "glare_zone_1_name": "Desk",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Glare" in summary
    assert "Desk" in summary


def test_sun_tracking_disabled_priority_chain_shows_solar_inactive():
    """Priority chain marks Solar as inactive when sun tracking is off."""
    cfg = {CONF_ENABLE_SUN_TRACKING: False, CONF_ENABLE_GLARE_ZONES: False}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "❌Solar" in summary


def test_priority_chain_glare_independent_of_sun_tracking():
    """Priority chain shows Glare as active even with sun tracking off, when glare zones enabled."""
    cfg = {CONF_ENABLE_SUN_TRACKING: False, CONF_ENABLE_GLARE_ZONES: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "My (50%)" in summary
    # Raw percent should not appear as the custom target
    assert "→ 40%" not in summary


def test_summary_falls_back_when_my_value_unset():
    """Shows fallback text and warning when use_my is True but My Preset Value is not set."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 30
    cfg[CONF_SUNSET_USE_MY] = True
    # CONF_MY_POSITION_VALUE intentionally absent
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "My (not set → 30%)" in summary
    assert "⚠️" in summary
    assert "My Preset Value is not set" in summary


def test_summary_hides_my_info_when_unused():
    """No My preset lines appear when no use_my flags are set and value is absent."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 30
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "🎛️ Somfy My preset" not in summary
    assert "⚠️ Somfy My preset" not in summary
    assert "My (" not in summary


def test_summary_shows_my_info_line_when_value_set_globally():
    """Info line appears showing the configured My value even if no use_my flags are enabled."""
    cfg = _minimal_vertical()
    cfg[CONF_MY_POSITION_VALUE] = 50
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "🎛️ Somfy My preset: 50%" in summary


def test_summary_shows_my_position_entities_enabled():
    """Summary must show enabled/disabled status for the My-preset entities toggle."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    cfg = _minimal_vertical()
    cfg[CONF_MY_POSITION_VALUE] = 50
    cfg[CONF_ENABLE_MY_POSITION_ENTITIES] = True
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "My-preset entities: enabled" in summary


def test_summary_shows_my_position_entities_disabled_by_default():
    """When the toggle is off (default), summary should show disabled."""
    cfg = _minimal_vertical()
    # No CONF_ENABLE_MY_POSITION_ENTITIES key — default False
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "My-preset entities: disabled" in summary


def test_summary_warns_when_toggle_on_with_blank_value():
    """Toggle on but my_position_value unset must emit a ⚠️ warning."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    cfg = _minimal_vertical()
    cfg[CONF_ENABLE_MY_POSITION_ENTITIES] = True
    # CONF_MY_POSITION_VALUE intentionally absent
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "⚠️" in summary
    assert "My Preset Value is not set" in summary


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
    summary = _build_config_summary(cfg, CoverType.BLIND, hass)
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
    summary = _build_config_summary(cfg, CoverType.BLIND, hass)
    assert "Cover Warnings" in summary
    assert "open/close-only" in summary


def test_capabilities_section_absent_without_hass():
    """_build_cover_capabilities_text returns empty string when hass is not passed."""
    cfg = {CONF_ENTITIES: ["cover.living_room"]}
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND)
    assert result == ""


def test_capabilities_section_absent_without_entities():
    """_build_cover_capabilities_text returns empty string when entities list is empty."""
    hass = _make_hass({})
    result = _build_cover_capabilities_text({CONF_ENTITIES: []}, CoverType.BLIND, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
    assert "open/close-only" in result
    assert "threshold compare" in result


def test_capabilities_section_unavailable_entity():
    """Unavailable entity renders ⚠️ not-ready warning."""
    hass = _make_hass(
        {"cover.blind": {"state": "unavailable", "supported_features": 0}}
    )
    # check_cover_features returns None for unavailable state
    cfg = {CONF_ENTITIES: ["cover.blind"]}
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.TILT, hass)
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
    result = _build_cover_capabilities_text(cfg, CoverType.BLIND, hass)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "today" not in summary


def test_sun_tracking_row_shows_solar_window():
    """Sun tracking line includes today's solar control window when sun_times provided."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True}
    summary = _build_config_summary(cfg, CoverType.BLIND, sun_times=_sun_times())
    assert "(today: sun in window 07:14 → 18:30)" in summary
    # Sun tracking rule line still carries its existing "Tracks the sun" phrasing
    assert "Tracks the sun" in summary


def test_sun_tracking_row_no_window_when_solar_times_none():
    """Sun tracking line shows 'does not enter window' when solar_start/solar_end are None."""
    cfg = {CONF_ENABLE_SUN_TRACKING: True}
    summary = _build_config_summary(
        cfg, CoverType.BLIND, sun_times=_sun_times(solar_start=None, solar_end=None)
    )
    assert "(today: sun does not enter window)" in summary


def test_after_sunset_row_shows_effective_sunset():
    """After sunset sub-bullet includes effective sunset time when sunset_pos configured."""
    cfg = {CONF_SUNSET_POS: 30}
    summary = _build_config_summary(
        cfg, CoverType.BLIND, sun_times=_sun_times(sunset_eff=(19, 45))
    )
    assert "🌅 After sunset (today ~19:45) → 30%" in summary


def test_after_sunrise_row_shows_when_sunset_pos_configured():
    """After sunrise sub-bullet shows today's effective sunrise time when sunset_pos configured."""
    cfg = {CONF_SUNSET_POS: 30}
    summary = _build_config_summary(
        cfg, CoverType.BLIND, sun_times=_sun_times(sunrise_eff=(6, 42))
    )
    assert "🌄 After sunrise (today ~06:42) → 0%" in summary


def test_after_sunrise_row_absent_without_sunset_pos():
    """After sunrise sub-bullet does not appear when sunset_pos is not configured."""
    cfg = {}
    summary = _build_config_summary(cfg, CoverType.BLIND, sun_times=_sun_times())
    assert "🌄 After sunrise" not in summary


def test_default_row_always_present():
    """Default fallback line always renders under How It Decides."""
    for cfg in [{}, {CONF_SUNSET_POS: 30}, {CONF_ENABLE_SUN_TRACKING: False}]:
        summary = _build_config_summary(cfg, CoverType.BLIND)
        assert "🌙 Default (no rule matches) → 0%" in summary


def test_sunset_today_and_offset_merged_in_one_parenthetical():
    """When both today's time and an offset are present, they share one parenthetical."""
    cfg = {CONF_SUNSET_POS: 30, CONF_SUNSET_OFFSET: 30}
    summary = _build_config_summary(
        cfg, CoverType.BLIND, sun_times=_sun_times(sunset_eff=(20, 15))
    )
    assert "🌅 After sunset (today ~20:15, +30 min) → 30%" in summary


# ---------------------------------------------------------------------------
# Position Map section is gone — every place it lived is now the HID chain
# ---------------------------------------------------------------------------


def test_position_map_section_absent():
    """Position Map header never renders — its content moved to How It Decides."""
    cfg = _full_vertical()
    summary = _build_config_summary(cfg, CoverType.BLIND, sun_times=_sun_times())
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    for badge in (
        "[100]",
        "[90]",
        "[80]",
        "[77]",
        "[75]",
        "[60]",
        "[50]",
        "[45]",
        "[40]",
        "[0]",
    ):
        assert badge in summary, f"expected {badge} badge on some rule"


def test_priority_badge_default_zero():
    """Default fallback line shows the [0] badge."""
    summary = _build_config_summary({}, CoverType.BLIND)
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
    """CONF_RETURN_SUNSET toggles a '🔚 Move to default position at end time: on' line."""
    from custom_components.adaptive_cover_pro.const import CONF_RETURN_SUNSET

    cfg = {CONF_SUNSET_POS: 30, CONF_RETURN_SUNSET: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Move to default position at end time: on" in summary


def test_return_sunset_line_absent_when_false():
    """CONF_RETURN_SUNSET=False omits the 🔚 line."""
    from custom_components.adaptive_cover_pro.const import CONF_RETURN_SUNSET

    cfg = {CONF_SUNSET_POS: 30, CONF_RETURN_SUNSET: False}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Move to default position at end time" not in summary


def test_end_of_window_line_rendered_with_return_on():
    """Issue #625: eow position + return_sunset on + end time → line, no footgun."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_END_OF_WINDOW_POS,
        CONF_RETURN_SUNSET,
    )

    cfg = {
        CONF_SUNSET_POS: 30,
        CONF_END_OF_WINDOW_POS: 0,
        CONF_RETURN_SUNSET: True,
        CONF_END_TIME: "19:30",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "End-of-window position" in summary
    assert "will not be applied" not in summary


def test_end_of_window_footgun_when_return_off():
    """Issue #625: eow position set but return_sunset off → footgun warning."""
    from custom_components.adaptive_cover_pro.const import CONF_END_OF_WINDOW_POS

    cfg = {CONF_SUNSET_POS: 30, CONF_END_OF_WINDOW_POS: 0}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "End-of-window position" in summary
    assert "will not be applied" in summary


def test_end_of_window_line_renders_without_sunset_pos():
    """Issue #625: eow set with NO sunset_position → line still renders.

    Structural guard for the historic ``if sunset_pos is not None`` gate.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_END_OF_WINDOW_POS,
        CONF_RETURN_SUNSET,
    )

    cfg = {
        CONF_END_OF_WINDOW_POS: 0,
        CONF_RETURN_SUNSET: True,
        CONF_END_TIME: "19:30",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "End-of-window position" in summary


def test_end_of_window_absent_when_unset():
    """Issue #625: no eow option → no end-of-window line."""
    cfg = {CONF_SUNSET_POS: 30}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "End-of-window position" not in summary


def test_manual_ignore_intermediate_shown():
    """CONF_MANUAL_IGNORE_INTERMEDIATE adds 'ignores intermediate positions' annotation."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_IGNORE_INTERMEDIATE,
    )

    cfg = {CONF_MANUAL_IGNORE_INTERMEDIATE: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    # "(as minimum)" belongs on the weather safety line, not the force line
    wx_line = next(ln for ln in summary.splitlines() if "Weather safety" in ln)
    assert "(as minimum)" in wx_line


def test_safety_slot_min_mode_shown():
    """Min mode on a priority-100 slot renders '(as minimum)' on its line."""
    cfg = {
        "custom_position_sensors_5": ["binary_sensor.safety"],
        "custom_position_5": 100,
        "custom_position_priority_5": 100,
        "custom_position_min_mode_5": True,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    slot_line = next(ln for ln in summary.splitlines() if "Custom #5" in ln)
    assert "(as minimum)" in slot_line


def test_custom_position_min_mode_shown():
    """custom_position_min_mode_N renders '(as minimum)' on the custom slot line."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.movie",
        "custom_position_1": 40,
        "custom_position_priority_1": 77,
        "custom_position_min_mode_1": True,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    custom_line = next(ln for ln in summary.splitlines() if "Custom #1" in ln)
    assert "(as minimum)" in custom_line


def test_custom_position_bypass_annotation_shown():
    """Custom slot line notes that it bypasses delta gates and auto-control."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.movie",
        "custom_position_1": 40,
        "custom_position_priority_1": 77,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    custom_line = next(ln for ln in summary.splitlines() if "Custom #1" in ln)
    assert "bypasses delta" in custom_line


def test_custom_position_tilt_only_summary_line():
    """A tilt-only slot renders a 'tilt only' note describing the slat-fix mode."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.glare",
        "custom_position_1": 80,
        "custom_position_priority_1": 77,
        "custom_position_tilt_1": 30,
        "custom_position_tilt_only_1": True,
    }
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    custom_line = next(ln for ln in summary.splitlines() if "Custom #1" in ln)
    assert "tilt only" in custom_line.lower()
    assert "30%" in custom_line


def test_custom_position_tilt_only_mutual_exclusion_warning():
    """tilt_only + min_mode (or use_my) produces a config warning."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.glare",
        "custom_position_1": 80,
        "custom_position_priority_1": 77,
        "custom_position_tilt_1": 30,
        "custom_position_tilt_only_1": True,
        "custom_position_min_mode_1": True,
    }
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "⚠️" in summary
    assert "tilt only" in summary.lower()
    assert "#1" in summary


def test_custom_position_tilt_only_no_warning_when_alone():
    """tilt_only alone (no min_mode/use_my conflict) produces no warning."""
    cfg = {
        "custom_position_sensor_1": "binary_sensor.glare",
        "custom_position_1": 80,
        "custom_position_priority_1": 77,
        "custom_position_tilt_1": 30,
        "custom_position_tilt_only_1": True,
    }
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    warning_lines = [
        ln for ln in summary.splitlines() if "⚠️" in ln and "tilt only" in ln.lower()
    ]
    assert warning_lines == []


def test_weather_state_list_in_cloud_line():
    """CONF_WEATHER_STATE list renders as 'weather in {state, state}' on the cloud line."""
    from custom_components.adaptive_cover_pro.const import CONF_WEATHER_STATE

    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_WEATHER_ENTITY: "weather.home",
        CONF_WEATHER_STATE: ["cloudy", "rainy"],
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "weather in {cloudy, rainy}" in summary


def test_outside_threshold_shown_on_climate_line():
    """CONF_OUTSIDE_THRESHOLD annotates the outside temp entity on the climate line."""
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor",
        CONF_OUTSIDE_THRESHOLD: 28,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.outdoor > 28°C" in summary


def test_transparent_blind_note_on_climate_line():
    """CONF_TRANSPARENT_BLIND adds a 'transparent blind' note on climate line."""
    from custom_components.adaptive_cover_pro.const import CONF_TRANSPARENT_BLIND

    cfg = {CONF_CLIMATE_MODE: True, CONF_TRANSPARENT_BLIND: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    climate_line = next(ln for ln in summary.splitlines() if "Climate mode" in ln)
    assert "transparent blind" in climate_line


def test_winter_close_insulation_note_on_climate_line():
    """CONF_WINTER_CLOSE_INSULATION adds a 'closes fully in winter' note."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WINTER_CLOSE_INSULATION,
    )

    cfg = {CONF_CLIMATE_MODE: True, CONF_WINTER_CLOSE_INSULATION: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "closes fully in winter for insulation" in summary


def test_summer_close_bypass_sun_floor_note_on_climate_line():
    """CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR adds a 'closes fully in summer' note (#689)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    )

    cfg = {CONF_CLIMATE_MODE: True, CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    climate_line = next(ln for ln in summary.splitlines() if "Climate mode" in ln)
    assert "closes fully in summer heat" in climate_line


def test_extreme_heat_note_on_climate_line():
    """CONF_TEMP_EXTREME_HEAT adds an all-day hold note with position + threshold (#766)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_EXTREME_HEAT_POSITION,
        CONF_TEMP_EXTREME_HEAT,
    )

    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_EXTREME_HEAT: 35,
        CONF_EXTREME_HEAT_POSITION: 20,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    climate_line = next(ln for ln in summary.splitlines() if "Climate mode" in ln)
    assert "holds 20% all day above 35°C outside" in climate_line


def test_extreme_heat_note_defaults_hold_to_closed_when_position_unset():
    """With only the threshold set, the note shows the fully-closed default hold (#766)."""
    from custom_components.adaptive_cover_pro.const import CONF_TEMP_EXTREME_HEAT

    cfg = {CONF_CLIMATE_MODE: True, CONF_TEMP_EXTREME_HEAT: 35}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    climate_line = next(ln for ln in summary.splitlines() if "Climate mode" in ln)
    assert "holds 0% all day above 35°C outside" in climate_line


def test_extreme_heat_absent_when_threshold_unset():
    """No extreme-heat note appears when the threshold is not configured (#766)."""
    cfg = {CONF_CLIMATE_MODE: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "all day above" not in summary


def test_open_close_threshold_in_position_limits():
    """CONF_OPEN_CLOSE_THRESHOLD renders under Position Limits."""
    from custom_components.adaptive_cover_pro.const import CONF_OPEN_CLOSE_THRESHOLD

    cfg = {CONF_MIN_POSITION: 0, CONF_OPEN_CLOSE_THRESHOLD: 50}
    summary = _build_config_summary(cfg, CoverType.BLIND)
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
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Calibration 10→90" in summary


def test_interp_without_start_end_falls_back():
    """CONF_INTERP=True without start/end falls back to the plain 'on' note."""
    cfg = {CONF_MIN_POSITION: 0, CONF_INTERP: True}
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Position calibration on" in summary


def test_min_only_qualifier_is_min_specific():
    """When only enable_min_position is True, qualifier reads 'min during sun tracking only'."""
    cfg = {
        CONF_MIN_POSITION: 10,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MIN_POSITION: True,
        CONF_ENABLE_MAX_POSITION: False,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "(min during sun tracking only)" in summary


def test_max_only_qualifier_is_max_specific():
    """When only enable_max_position is True, qualifier reads 'max during sun tracking only'."""
    cfg = {
        CONF_MIN_POSITION: 10,
        CONF_MAX_POSITION: 90,
        CONF_ENABLE_MIN_POSITION: False,
        CONF_ENABLE_MAX_POSITION: True,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
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


# ---------------------------------------------------------------------------
# cloudy_position summary (Issue #311)
# ---------------------------------------------------------------------------


def test_cloud_suppression_shows_cloudy_position_when_set():
    """When cloudy_position is configured and suppression is on, summary shows 'cloudy position N%'."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
        CONF_CLOUDY_POSITION: 25,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "cloudy position 25%" in summary
    assert "default (" not in summary.split("Cloud suppression")[1].split("\n")[0]


def test_cloud_suppression_shows_default_when_cloudy_position_not_set():
    """When cloudy_position is absent, summary shows 'default (N%)' as before."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: 500,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "default (" in summary
    assert "cloudy position" not in summary


def test_cloudy_position_zero_is_shown_not_skipped():
    """CONF_CLOUDY_POSITION=0 renders as '0%', not treated as unset."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_CLOUDY_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "cloudy position 0%" in summary


def test_cloudy_position_set_without_suppression_shows_warning():
    """⚠️ warning when cloudy_position is set but cloud suppression is disabled."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: False,
        CONF_CLOUDY_POSITION: 25,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "⚠️" in summary
    assert "cloud suppression" in summary.lower()


def test_cloudy_position_no_warning_when_suppression_on():
    """No ⚠️ warning for cloudy_position when cloud suppression is enabled."""
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_CLOUDY_POSITION: 25,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    cloud_line = next(
        (ln for ln in summary.splitlines() if "Cloud suppression" in ln), ""
    )
    assert "⚠️" not in cloud_line


# ---------------------------------------------------------------------------
# Tilt MODE2 + min_position footgun warning (issue #373)
# ---------------------------------------------------------------------------


def _mode2_warning_markers_present(summary: str) -> bool:
    """Look for the MODE2-min-position footgun warning by its diagnostic markers."""
    lower = summary.lower()
    return (
        "⚠️" in summary
        and "mode2" in lower
        and "min position" in lower
        and "open" in lower
    )


def test_tilt_mode2_with_high_min_position_shows_warning():
    """MODE2 + min_position ≥ 50 surfaces the footgun (issue #373)."""
    cfg = {CONF_TILT_MODE: "mode2", CONF_MIN_POSITION: 50}
    summary = _build_config_summary(cfg, CoverType.TILT)
    assert _mode2_warning_markers_present(
        summary
    ), f"Expected MODE2-min-pos warning markers in summary, got:\n{summary}"


def test_tilt_mode1_with_high_min_position_no_warning():
    """MODE1 + min_position ≥ 50 must NOT surface the MODE2-specific warning."""
    cfg = {CONF_TILT_MODE: "mode1", CONF_MIN_POSITION: 50}
    summary = _build_config_summary(cfg, CoverType.TILT)
    assert not _mode2_warning_markers_present(
        summary
    ), f"MODE1 must not trigger MODE2 warning, got:\n{summary}"


def test_tilt_mode2_min_position_zero_no_warning():
    """MODE2 + min_position 0 leaves the open band alone, no warning."""
    cfg = {CONF_TILT_MODE: "mode2", CONF_MIN_POSITION: 0}
    summary = _build_config_summary(cfg, CoverType.TILT)
    assert not _mode2_warning_markers_present(
        summary
    ), f"MODE2 + min_pos 0 must not warn, got:\n{summary}"


def test_venetian_mode2_with_high_min_position_shows_warning():
    """Venetian MODE2 + min_position ≥ 50 surfaces the same footgun."""
    cfg = {CONF_TILT_MODE: "mode2", CONF_MIN_POSITION: 50}
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert _mode2_warning_markers_present(
        summary
    ), f"Expected MODE2-min-pos warning for venetian, got:\n{summary}"


# ---------------------------------------------------------------------------
# Motion timeout mode (issue #333)
# ---------------------------------------------------------------------------


def test_motion_summary_default_mode_says_return_to_default():
    """Default mode shows 'return to default' in motion bullet."""
    cfg = {
        CONF_MOTION_SENSORS: ["binary_sensor.motion"],
        CONF_MOTION_TIMEOUT: 120,
        CONF_DEFAULT_HEIGHT: 45,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    motion_line = next((ln for ln in summary.splitlines() if "🚶 Occupancy" in ln), "")
    assert "return to default" in motion_line.lower()


def test_motion_summary_template_only():
    """An occupancy-template-only config still shows the motion rule (#577 f/u)."""
    cfg = {
        CONF_MOTION_TEMPLATE: "{{ is_state('input_boolean.guest', 'on') }}",
        CONF_MOTION_TIMEOUT: 120,
        CONF_DEFAULT_HEIGHT: 45,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    motion_line = next((ln for ln in summary.splitlines() if "🚶 Occupancy" in ln), "")
    assert "occupancy template" in motion_line


def test_motion_summary_sensors_plus_template():
    """Sensors and a template are both listed as occupancy sources."""
    cfg = {
        CONF_MOTION_SENSORS: ["binary_sensor.motion"],
        CONF_MOTION_TEMPLATE: "{{ true }}",
        CONF_MOTION_TIMEOUT: 120,
        CONF_DEFAULT_HEIGHT: 45,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    motion_line = next((ln for ln in summary.splitlines() if "🚶 Occupancy" in ln), "")
    assert "1 source" in motion_line
    assert "occupancy template" in motion_line


def test_motion_summary_hold_mode_says_hold_position():
    """hold_position mode shows a hold description in motion bullet."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_TIMEOUT_MODE

    cfg = {
        CONF_MOTION_SENSORS: ["binary_sensor.motion"],
        CONF_MOTION_TIMEOUT: 120,
        CONF_DEFAULT_HEIGHT: 45,
        CONF_MOTION_TIMEOUT_MODE: "hold_position",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    motion_line = next((ln for ln in summary.splitlines() if "🚶 Occupancy" in ln), "")
    assert "hold" in motion_line.lower()


def test_motion_summary_hold_mode_no_sensors_shows_warning():
    """hold_position mode with no sensors shows a ⚠️ warning."""
    from custom_components.adaptive_cover_pro.const import CONF_MOTION_TIMEOUT_MODE

    cfg = {
        CONF_MOTION_SENSORS: [],
        CONF_MOTION_TIMEOUT_MODE: "hold_position",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert (
        "⚠️" in summary
        and "hold_position" in summary.lower()
        or ("hold_position" in summary or "hold position" in summary.lower())
    )


# ---------------------------------------------------------------------------
# Proxy cover summary line + min-mode warning
# ---------------------------------------------------------------------------


def test_config_summary_shows_proxy_cover_disabled_by_default():
    """Default-off proxy cover is reflected in the summary."""
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_PROXY_COVER

    cfg = _minimal_vertical()
    cfg[CONF_ENABLE_PROXY_COVER] = False
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Proxy cover" in summary
    proxy_line = next(ln for ln in summary.splitlines() if "Proxy cover" in ln)
    assert "disabled" in proxy_line.lower()


def test_config_summary_shows_proxy_cover_enabled():
    """Enabled proxy cover is reflected in the summary."""
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_PROXY_COVER

    cfg = _minimal_vertical()
    cfg[CONF_ENABLE_PROXY_COVER] = True
    # A min-mode slot is configured so no warning fires.
    cfg["custom_position_sensor_1"] = "binary_sensor.evening"
    cfg["custom_position_1"] = 60
    cfg["custom_position_min_mode_1"] = True
    summary = _build_config_summary(cfg, CoverType.BLIND)
    proxy_line = next(ln for ln in summary.splitlines() if "Proxy cover" in ln)
    assert "enabled" in proxy_line.lower()


def test_config_summary_warns_when_proxy_enabled_without_min_mode_slot():
    """Proxy enabled with no min-mode slots → ⚠ warning line."""
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_PROXY_COVER

    cfg = _minimal_vertical()
    cfg[CONF_ENABLE_PROXY_COVER] = True
    # A custom slot is configured but min_mode=False.
    cfg["custom_position_sensor_1"] = "binary_sensor.evening"
    cfg["custom_position_1"] = 60
    cfg["custom_position_min_mode_1"] = False
    summary = _build_config_summary(cfg, CoverType.BLIND)
    proxy_block = [ln for ln in summary.splitlines() if "proxy" in ln.lower()]
    assert any(
        "⚠" in ln for ln in proxy_block
    ), f"expected ⚠ warning near proxy line; got: {proxy_block}"


# ---------------------------------------------------------------------------
# Tilt in config summary (Step 14)
# ---------------------------------------------------------------------------


def test_summary_shows_default_tilt_when_set():
    """CONF_DEFAULT_TILT appears in summary when set for venetian cover."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_TILT

    cfg = _minimal_vertical()
    cfg[CONF_DEFAULT_TILT] = 40
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "40" in summary, "default_tilt value should appear in summary"
    assert any(
        "tilt" in line.lower() and "40" in line for line in summary.splitlines()
    ), "Expected a tilt line with value 40 in summary"


def test_summary_shows_sunset_tilt_when_set():
    """CONF_SUNSET_TILT appears in summary when set for venetian cover."""
    from custom_components.adaptive_cover_pro.const import CONF_SUNSET_TILT

    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_TILT] = 80
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "80" in summary, "sunset_tilt value should appear in summary"
    assert any(
        "tilt" in line.lower() and "80" in line for line in summary.splitlines()
    ), "Expected a tilt line with value 80 in summary"


def test_summary_shows_custom_slot_tilt_when_set():
    """Per-slot tilt value appears in summary when set for venetian custom position."""
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS

    cfg = _minimal_vertical()
    tilt_key = CUSTOM_POSITION_SLOTS[1]["tilt"]
    cfg["custom_position_sensor_1"] = "binary_sensor.evening"
    cfg["custom_position_1"] = 50
    cfg[tilt_key] = 35
    summary = _build_config_summary(cfg, CoverType.VENETIAN)
    assert "35" in summary, "custom slot tilt value should appear in summary"
    assert any(
        "tilt" in line.lower() and "35" in line for line in summary.splitlines()
    ), "Expected a tilt line with value 35 in summary"


# ---------------------------------------------------------------------------
# POSITION_SCHEMA accepts new entity fields (Step 7)
# ---------------------------------------------------------------------------


def test_position_schema_accepts_sunset_time_entity():
    """BEHAVIOR_SCHEMA accepts sunset_time_entity key (moved L2a→L2b, #613)."""
    from custom_components.adaptive_cover_pro.config_flow import BEHAVIOR_SCHEMA

    result = BEHAVIOR_SCHEMA({CONF_SUNSET_TIME_ENTITY: "sensor.sun2_dusk"})
    assert result[CONF_SUNSET_TIME_ENTITY] == "sensor.sun2_dusk"


def test_position_schema_accepts_sunrise_time_entity():
    """BEHAVIOR_SCHEMA accepts sunrise_time_entity key (moved L2a→L2b, #613)."""
    from custom_components.adaptive_cover_pro.config_flow import BEHAVIOR_SCHEMA

    result = BEHAVIOR_SCHEMA({CONF_SUNRISE_TIME_ENTITY: "sensor.sun2_dawn"})
    assert result[CONF_SUNRISE_TIME_ENTITY] == "sensor.sun2_dawn"


# ---------------------------------------------------------------------------
# _build_config_summary: entity ID shown when configured (Step 8)
# ---------------------------------------------------------------------------


def test_summary_shows_sunset_entity_when_configured():
    """Summary includes the sunset entity ID when CONF_SUNSET_TIME_ENTITY is set."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 0
    cfg[CONF_SUNSET_OFFSET] = 30
    cfg[CONF_SUNSET_TIME_ENTITY] = "sensor.sun2_dusk"
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.sun2_dusk" in summary


def test_summary_shows_sunrise_entity_when_configured():
    """Summary includes the sunrise entity ID when CONF_SUNRISE_TIME_ENTITY is set."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 0
    cfg[CONF_SUNRISE_OFFSET] = 30
    cfg[CONF_SUNRISE_TIME_ENTITY] = "sensor.sun2_dawn"
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "sensor.sun2_dawn" in summary


def test_summary_without_entities_uses_offset_annotation():
    """When no entity override is set, the existing offset annotation still appears."""
    cfg = _minimal_vertical()
    cfg[CONF_SUNSET_POS] = 0
    cfg[CONF_SUNSET_OFFSET] = 30
    summary = _build_config_summary(cfg, CoverType.BLIND)
    # Offset annotation should still appear in some form
    assert "+30" in summary or "30 min" in summary
    # The entity IDs should NOT appear
    assert "sun2" not in summary


# ---------------------------------------------------------------------------
# FOV summary (#565)
# ---------------------------------------------------------------------------


def test_summary_omits_computed_fov_line():
    """The FOV is stored as concrete angles, so the summary shows no computed line.

    The "Generate FOV from measurements" button writes ``fov_left``/``fov_right``
    on submit, so the summary's sun-tracking block reflects the actual angles —
    there is no separate "Computed FOV ≈ …" geometry line any more (#565).
    """
    cfg = _minimal_vertical()
    cfg[CONF_WINDOW_WIDTH] = 1.2
    cfg[CONF_WINDOW_DEPTH] = 0.5
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "Computed FOV" not in summary


# ---------------------------------------------------------------------------
# Jinja2 template placeholder tests (issue #577)
# ---------------------------------------------------------------------------


def test_cloud_irradiance_template_shows_placeholder():
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_IRRADIANCE_ENTITY: "sensor.irr",
        CONF_IRRADIANCE_THRESHOLD: "{% set s = states('sensor.season') %}{{ 300 if s == 'winter' else 500 }}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_cloud_lux_template_shows_placeholder():
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_LUX_THRESHOLD: "{% if true %}300{% endif %}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_cloud_coverage_template_shows_placeholder():
    cfg = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
        CONF_CLOUD_COVERAGE_THRESHOLD: "{% if true %}60{% endif %}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_weather_wind_template_shows_placeholder():
    cfg = {
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: "{% if true %}50{% endif %}",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_weather_rain_template_shows_placeholder():
    cfg = {
        CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
        CONF_WEATHER_RAIN_THRESHOLD: "{% if true %}2.0{% endif %}",
        CONF_WEATHER_OVERRIDE_POSITION: 0,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_climate_temp_low_template_shows_placeholder():
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_LOW: "{% if true %}16{% endif %}",
        CONF_TEMP_HIGH: 24,
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_climate_temp_high_template_shows_placeholder():
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_TEMP_LOW: 16,
        CONF_TEMP_HIGH: "{% if true %}24{% endif %}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


def test_climate_outside_threshold_template_shows_placeholder():
    cfg = {
        CONF_CLIMATE_MODE: True,
        CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor",
        CONF_OUTSIDE_THRESHOLD: "{% if true %}28{% endif %}",
    }
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "[template]" in summary
    assert "{%" not in summary


# ---------------------------------------------------------------------------
# Daytime gate (issue #632) — summary line + offset-ignored warning
# ---------------------------------------------------------------------------


def test_summary_shows_daytime_gate_line_for_sensor():
    cfg = _minimal_vertical()
    cfg[CONF_DAYTIME_GATE_SENSORS] = ["binary_sensor.bright"]
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "daytime gate" in summary.lower()
    assert "binary_sensor.bright" in summary


def test_summary_shows_daytime_gate_line_for_template():
    cfg = _minimal_vertical()
    cfg[CONF_DAYTIME_GATE_TEMPLATE] = "{{ is_state('sun.sun', 'above_horizon') }}"
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "daytime gate" in summary.lower()


def test_summary_gate_absent_when_unconfigured():
    summary = _build_config_summary(_minimal_vertical(), CoverType.BLIND)
    assert "daytime gate" not in summary.lower()


def test_summary_gate_warns_offsets_ignored_when_offset_set():
    cfg = _minimal_vertical()
    cfg[CONF_DAYTIME_GATE_SENSORS] = ["binary_sensor.bright"]
    cfg[CONF_SUNSET_OFFSET] = 30
    cfg[CONF_SUNRISE_OFFSET] = 60
    summary = _build_config_summary(cfg, CoverType.BLIND)
    # A ⚠️ must surface the footgun: offsets are no-ops under a gate.
    assert "⚠️" in summary
    assert "offset" in summary.lower()


def test_summary_gate_no_offset_warning_when_offsets_zero():
    cfg = _minimal_vertical()
    cfg[CONF_DAYTIME_GATE_SENSORS] = ["binary_sensor.bright"]
    # No offsets configured → no footgun → no offset warning, but still gate line.
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "daytime gate" in summary.lower()
    assert "offset" not in summary.lower()


def test_summary_gate_template_mode_and():
    cfg = _minimal_vertical()
    cfg[CONF_DAYTIME_GATE_SENSORS] = ["binary_sensor.bright"]
    cfg[CONF_DAYTIME_GATE_TEMPLATE] = "{{ true }}"
    cfg[CONF_DAYTIME_GATE_TEMPLATE_MODE] = "and"
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert "daytime gate" in summary.lower()


# ---------------------------------------------------------------------------
# Section: Building Profile link in summary (issue #720)
# ---------------------------------------------------------------------------


def test_summary_building_profile_line_when_linked() -> None:
    """_build_config_summary shows the linked profile title when linked."""

    class _FakeEntry:
        def __init__(self, title: str) -> None:
            self.title = title

    class _FakeStates:
        """Minimal hass.states stub so _check_cover_capabilities doesn't crash."""

        def get(self, entity_id: str):  # noqa: D102
            return None

    class _FakeConfigEntries:
        def __init__(self, entries: dict) -> None:
            self._entries = entries

        def async_get_entry(self, entry_id: str):  # noqa: D102
            return self._entries.get(entry_id)

    class _FakeHass:
        def __init__(self, entries: dict) -> None:
            self.config_entries = _FakeConfigEntries(entries)
            self.states = _FakeStates()

    hass = _FakeHass({"profile_1": _FakeEntry("Main House")})
    cfg = _minimal_vertical()
    cfg[CONF_BUILDING_PROFILE_ID] = "profile_1"
    summary = _build_config_summary(cfg, CoverType.BLIND, hass=hass)

    assert (
        "building profile" in summary.lower()
    ), "Summary must mention 'building profile' for a linked cover"
    assert "Main House" in summary, "Summary must include the linked profile's title"


def test_summary_building_profile_line_absent_when_unlinked() -> None:
    """_build_config_summary has no profile line when CONF_BUILDING_PROFILE_ID is absent."""
    cfg = _minimal_vertical()
    summary = _build_config_summary(cfg, CoverType.BLIND)
    assert (
        "building profile" not in summary.lower()
    ), "Summary must not mention 'building profile' for an unlinked cover"


# ---------------------------------------------------------------------------
# Cover Group summary (issue #790)
# ---------------------------------------------------------------------------


def test_summary_group_shows_header_and_member_counts():
    """A group renders its own compact summary, never the cover chain."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
    )

    summary = _build_config_summary(
        {
            CONF_MEMBER_ENTRIES: ["entry_a", "entry_b"],
            CONF_MEMBER_COVERS: ["cover.generic"],
        },
        CoverType.GROUP,
    )

    assert "**Cover Group**" in summary
    assert "2 ACP member(s)" in summary
    assert "1 generic cover(s)" in summary
    assert "cover.generic" in summary
    # None of the cover-chain sections appear for a group.
    assert "How It Decides" not in summary


def test_summary_group_empty_roster_warns():
    """An empty group is called out — it will not command anything."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
    )

    summary = _build_config_summary(
        {CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: []},
        CoverType.GROUP,
    )

    assert "**Cover Group**" in summary
    assert "no members" in summary


def test_summary_group_shows_arbitration_lines():
    """Phase 2: scene/lock priorities and the stagger, from the imported consts."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_STAGGER_DELAY,
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
        CUSTOM_POSITION_SAFETY_PRIORITY,
        GROUP_SCENE_PRIORITY,
    )

    summary = _build_config_summary(
        {
            CONF_MEMBER_ENTRIES: ["entry_a"],
            CONF_MEMBER_COVERS: [],
            CONF_GROUP_STAGGER_DELAY: 2.5,
        },
        CoverType.GROUP,
    )

    assert f"priority {GROUP_SCENE_PRIORITY}" in summary
    assert f"priority {CUSTOM_POSITION_SAFETY_PRIORITY}" in summary
    assert "2.5" in summary


def test_summary_group_omits_stagger_line_when_zero():
    from custom_components.adaptive_cover_pro.const import (
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
    )

    summary = _build_config_summary(
        {CONF_MEMBER_ENTRIES: ["entry_a"], CONF_MEMBER_COVERS: []},
        CoverType.GROUP,
    )

    assert "apart" not in summary


def test_summary_group_shows_cover_entity_line_when_enabled():
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_ENABLE_COVER_ENTITY,
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
    )

    base = {CONF_MEMBER_ENTRIES: ["entry_a"], CONF_MEMBER_COVERS: []}
    without = _build_config_summary(
        {**base, CONF_GROUP_ENABLE_COVER_ENTITY: False}, CoverType.GROUP
    )
    assert "Aggregate cover entity" not in without

    with_cover = _build_config_summary(
        {**base, CONF_GROUP_ENABLE_COVER_ENTITY: True}, CoverType.GROUP
    )
    assert "Aggregate cover entity" in with_cover

    # Default (key absent) now enables the cover, so the line appears.
    by_default = _build_config_summary(base, CoverType.GROUP)
    assert "Aggregate cover entity" in by_default


def test_summary_group_shows_area_line_when_configured():
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_AREA,
        CONF_MEMBER_COVERS,
        CONF_MEMBER_ENTRIES,
    )

    base = {CONF_MEMBER_ENTRIES: ["entry_a"], CONF_MEMBER_COVERS: []}
    without = _build_config_summary(base, CoverType.GROUP)
    assert "area" not in without.lower()

    with_area = _build_config_summary(
        {**base, CONF_GROUP_AREA: "living_room"}, CoverType.GROUP
    )
    assert "living_room" in with_area
