"""Integration tests for cross-handler priority interactions.

Tests complex scenarios where multiple override systems are simultaneously active
and verifies the highest-priority handler wins with the correct position.

Covers:
- Step 44: Weather override + motion timeout (weather 90 > motion 75)
- Step 45: Weather override + manual override (weather 90 > manual 80)
- Step 46: Force override + weather (force 100 > weather 90)
- Step 47: Motion timeout fires when weather inactive
- Step 48: Custom position between manual and motion (default priority 77)
- Step 49: Cloud suppression + climate mode (cloud 60 > climate 50)
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.force_override import (
    ForceOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_registry() -> PipelineRegistry:
    """Full registry with all handlers in correct priority order."""
    return PipelineRegistry(
        [
            ForceOverrideHandler(),
            WeatherOverrideHandler(),
            ManualOverrideHandler(),
            CustomPositionHandler(
                slot=1, entity_id="binary_sensor.scene", position=55, priority=77
            ),
            MotionTimeoutHandler(),
            CloudSuppressionHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _summer_readings() -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=30.0,  # above temp_high=26
        is_presence=False,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _summer_options() -> ClimateOptions:
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=False,
        winter_close_insulation=False,
    )


def _cloudy_readings() -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=False,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _cloud_options() -> ClimateOptions:
    return ClimateOptions(
        temp_low=None,
        temp_high=None,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=True,
        winter_close_insulation=False,
    )


# ---------------------------------------------------------------------------
# Step 44: Weather override + motion timeout
# ---------------------------------------------------------------------------


class TestWeatherOverridePlusMotionTimeout:
    """Weather (90) beats motion timeout (75) when both are active."""

    def test_weather_wins_over_motion_timeout(self):
        """WeatherOverrideHandler fires before MotionTimeoutHandler."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            motion_timeout_active=True,
            default_position=50,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.WEATHER
        assert result.position == 0

    def test_weather_bypass_auto_control_set(self):
        """Weather override result has bypass_auto_control=True."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)

        assert result.bypass_auto_control is True

    def test_motion_fires_when_weather_inactive(self):
        """Motion timeout fires when weather override is NOT active."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=False,
            motion_timeout_active=True,
            default_position=30,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 30

    def test_decision_trace_shows_weather_over_motion(self):
        """In the decision trace, weather matched=True; motion matched=False."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)

        trace = {s.handler: s.matched for s in result.decision_trace}
        assert trace.get("weather") is True
        assert trace.get("motion_timeout") is False


# ---------------------------------------------------------------------------
# Step 45: Weather override + manual override
# ---------------------------------------------------------------------------


class TestWeatherOverridePlusManualOverride:
    """Weather (90) beats manual override (80) when both are active."""

    def test_weather_wins_over_manual_override(self):
        """WeatherOverrideHandler fires before ManualOverrideHandler."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            manual_override_active=True,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.WEATHER
        assert result.position == 0

    def test_manual_fires_when_weather_clears(self):
        """After weather clears, manual override takes effect."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=False,
            manual_override_active=True,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MANUAL

    def test_decision_trace_weather_then_manual(self):
        """Trace shows weather matched, manual and below did not."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            manual_override_active=True,
        )
        result = registry.evaluate(snap)

        trace = {s.handler: s.matched for s in result.decision_trace}
        assert trace.get("weather") is True
        assert trace.get("manual_override") is False
        assert trace.get("motion_timeout") is False


# ---------------------------------------------------------------------------
# Step 46: Force override + weather
# ---------------------------------------------------------------------------


class TestForceOverridePlusWeather:
    """Force (100) beats weather (90) when both are active."""

    def test_force_wins_over_weather(self):
        """ForceOverrideHandler fires before WeatherOverrideHandler."""
        registry = _full_registry()
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.wind_sensor": True},
            force_override_position=75,
            weather_override_active=True,
            weather_override_position=0,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.FORCE
        assert result.position == 75

    def test_force_override_always_bypasses_auto_control(self):
        """Force override result has bypass_auto_control=True."""
        registry = _full_registry()
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.emergency": True},
            force_override_position=0,
            weather_override_active=True,
            weather_override_position=0,
        )
        result = registry.evaluate(snap)

        assert result.bypass_auto_control is True

    def test_weather_fires_when_force_clears(self):
        """When force override sensor turns off, weather takes over."""
        registry = _full_registry()
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.wind_sensor": False},  # off
            weather_override_active=True,
            weather_override_position=0,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.WEATHER

    def test_all_three_safety_active_force_wins(self):
        """Force + weather both active + motion → force still wins."""
        registry = _full_registry()
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.emergency": True},
            force_override_position=50,
            weather_override_active=True,
            weather_override_position=0,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.FORCE
        assert result.position == 50


# ---------------------------------------------------------------------------
# Step 47: Motion timeout fires when weather is inactive
# ---------------------------------------------------------------------------


class TestMotionTimeoutWithoutWeather:
    """When weather is not active, motion timeout correctly takes effect."""

    def test_motion_timeout_fires_without_weather(self):
        """MotionTimeoutHandler wins when no weather or higher-priority overrides."""
        registry = _full_registry()
        snap = make_snapshot(
            weather_override_active=False,
            manual_override_active=False,
            motion_timeout_active=True,
            direct_sun_valid=True,
            default_position=20,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 20

    def test_motion_timeout_returns_default_not_solar(self):
        """Motion timeout returns snapshot.default_position, not the solar position."""
        registry = _full_registry()
        snap = make_snapshot(
            motion_timeout_active=True,
            direct_sun_valid=True,
            calculate_percentage_return=80.0,  # solar would be 80
            default_position=10,  # but motion uses 10
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 10  # default, not 80


# ---------------------------------------------------------------------------
# Step 48: Custom position between manual and motion
# ---------------------------------------------------------------------------


class TestCustomPositionPriorityInteractions:
    """Custom position (default priority 77) sits between manual (80) and motion (75)."""

    def test_custom_beats_motion_timeout(self):
        """Custom position (77) fires before motion timeout (75)."""
        registry = _full_registry()
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", True, 55, 77, False, False)
            ],
            motion_timeout_active=True,
            default_position=10,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 55

    def test_manual_override_beats_custom_position(self):
        """Manual override (80) fires before custom position (77)."""
        registry = _full_registry()
        snap = make_snapshot(
            manual_override_active=True,
            custom_position_sensors=[
                ("binary_sensor.scene", True, 55, 77, False, False)
            ],
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MANUAL

    def test_custom_inactive_allows_motion_timeout(self):
        """When custom sensor is off and motion timeout active, motion wins."""
        registry = _full_registry()
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", False, 55, 77, False, False)
            ],  # off
            motion_timeout_active=True,
            default_position=10,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 10

    def test_custom_beats_solar(self):
        """Custom position (77) also beats solar (40) when active."""
        registry = _full_registry()
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", True, 55, 77, False, False)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=90.0,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 55


# ---------------------------------------------------------------------------
# Step 49: Cloud suppression + climate mode
# ---------------------------------------------------------------------------


class TestCloudSuppressionPlusClimateMode:
    """Cloud suppression (60) fires before climate handler (50)."""

    def test_cloud_suppression_beats_climate_handler(self):
        """CloudSuppressionHandler fires before ClimateHandler when not sunny."""
        registry = _full_registry()
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_cloudy_readings(),
            climate_options=_cloud_options(),
            direct_sun_valid=True,
            default_position=30,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 30  # default (cloud suppression returns default)

    def test_climate_fires_when_cloud_suppression_disabled(self):
        """ClimateHandler fires when cloud suppression is disabled even if not sunny."""
        from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions

        options_no_cloud = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,  # disabled
            winter_close_insulation=False,
        )
        registry = _full_registry()
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_summer_readings(),
            climate_options=options_no_cloud,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)

        # With cloud suppression disabled, ClimateHandler evaluates
        # Summer cooling (no presence, hot) → position 0
        assert result.control_method == ControlMethod.SUMMER
        assert result.position == 0

    def test_cloud_suppression_position_is_default_not_climate(self):
        """Cloud suppression returns default_position, not the climate strategy position."""
        registry = _full_registry()
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_cloudy_readings(),
            climate_options=_cloud_options(),
            direct_sun_valid=True,
            default_position=40,  # cloud suppression uses this
            calculate_percentage_return=90.0,  # solar would be 90, climate would vary
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 40  # default, not 0 (summer) or 90 (solar)

    def test_decision_trace_cloud_before_climate(self):
        """In trace: cloud_suppression matched=True; climate matched=False."""
        registry = _full_registry()
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_cloudy_readings(),
            climate_options=_cloud_options(),
            direct_sun_valid=True,
            default_position=30,
        )
        result = registry.evaluate(snap)

        trace = {s.handler: s.matched for s in result.decision_trace}
        assert trace.get("cloud_suppression") is True
        assert trace.get("climate") is False
