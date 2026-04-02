"""Tests for the cloud suppression / glare control suppression feature (Issue #65).

Covers:
- _is_cloud_suppression_active() logic with all WeatherReading combinations
- Pipeline integration — cloud suppression overrides solar handler
- Priority ordering — cloud suppression defers to higher-priority handlers
"""

from __future__ import annotations


from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    ForceOverrideHandler,
    ManualOverrideHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_weather_readings(
    *,
    is_sunny: bool = True,
    lux_below_threshold: bool = False,
    irradiance_below_threshold: bool = False,
    cloud_coverage_above_threshold: bool = False,
) -> ClimateReadings:
    """Build ClimateReadings with the weather/lux/irradiance/cloud-coverage fields set."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
    )


def _is_cloud_suppression_active(
    cloud_suppression_enabled: bool,
    weather_readings: ClimateReadings | None,
) -> bool:
    """Replicate coordinator._is_cloud_suppression_active() logic for unit testing."""
    if not cloud_suppression_enabled:
        return False
    if weather_readings is None:
        return False
    return bool(
        not weather_readings.is_sunny
        or weather_readings.lux_below_threshold
        or weather_readings.irradiance_below_threshold
        or weather_readings.cloud_coverage_above_threshold
    )


# ---------------------------------------------------------------------------
# _is_cloud_suppression_active logic
# ---------------------------------------------------------------------------


class TestIsCloudSuppressionActive:
    """Unit tests for the coordinator._is_cloud_suppression_active() logic."""

    # -- Feature disabled --

    def test_inactive_when_toggle_off_and_not_sunny(self) -> None:
        """Toggle off → inactive even when weather says no sun."""
        readings = make_weather_readings(is_sunny=False)
        assert _is_cloud_suppression_active(False, readings) is False

    def test_inactive_when_toggle_off_and_lux_low(self) -> None:
        """Toggle off → inactive even when lux below threshold."""
        readings = make_weather_readings(lux_below_threshold=True)
        assert _is_cloud_suppression_active(False, readings) is False

    def test_inactive_when_toggle_off_and_irradiance_low(self) -> None:
        """Toggle off → inactive even when irradiance below threshold."""
        readings = make_weather_readings(irradiance_below_threshold=True)
        assert _is_cloud_suppression_active(False, readings) is False

    # -- No readings --

    def test_inactive_when_readings_none(self) -> None:
        """No readings → inactive regardless of toggle."""
        assert _is_cloud_suppression_active(True, None) is False

    # -- Weather conditions --

    def test_active_when_weather_not_sunny(self) -> None:
        """Weather not sunny activates cloud suppression."""
        readings = make_weather_readings(is_sunny=False)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_weather_sunny(self) -> None:
        """Weather sunny → inactive (all other sensors at default)."""
        readings = make_weather_readings(is_sunny=True)
        assert _is_cloud_suppression_active(True, readings) is False

    # -- Lux conditions --

    def test_active_when_lux_below_threshold(self) -> None:
        """Lux below threshold activates cloud suppression."""
        readings = make_weather_readings(lux_below_threshold=True)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_lux_above_threshold(self) -> None:
        """Lux above threshold (lux_below_threshold=False) → inactive."""
        readings = make_weather_readings(lux_below_threshold=False)
        assert _is_cloud_suppression_active(True, readings) is False

    # -- Irradiance conditions --

    def test_active_when_irradiance_below_threshold(self) -> None:
        """Irradiance below threshold activates cloud suppression."""
        readings = make_weather_readings(irradiance_below_threshold=True)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_irradiance_above_threshold(self) -> None:
        """Irradiance above threshold → inactive."""
        readings = make_weather_readings(irradiance_below_threshold=False)
        assert _is_cloud_suppression_active(True, readings) is False

    # -- OR logic: any condition is sufficient --

    def test_active_when_weather_not_sunny_regardless_of_lux(self) -> None:
        """Not sunny activates even when lux and irradiance are above threshold."""
        readings = make_weather_readings(
            is_sunny=False, lux_below_threshold=False, irradiance_below_threshold=False
        )
        assert _is_cloud_suppression_active(True, readings) is True

    def test_active_when_lux_low_regardless_of_weather(self) -> None:
        """Lux below threshold activates even when weather says sunny."""
        readings = make_weather_readings(is_sunny=True, lux_below_threshold=True)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_active_when_irradiance_low_regardless_of_weather(self) -> None:
        """Irradiance below threshold activates even when weather says sunny."""
        readings = make_weather_readings(is_sunny=True, irradiance_below_threshold=True)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_active_when_all_conditions_indicate_low_light(self) -> None:
        """All three low-light conditions → active."""
        readings = make_weather_readings(
            is_sunny=False, lux_below_threshold=True, irradiance_below_threshold=True
        )
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_all_conditions_indicate_sun(self) -> None:
        """All three sunny conditions → inactive."""
        readings = make_weather_readings(
            is_sunny=True, lux_below_threshold=False, irradiance_below_threshold=False
        )
        assert _is_cloud_suppression_active(True, readings) is False

    # -- Cloud coverage conditions --

    def test_active_when_cloud_coverage_above_threshold(self) -> None:
        """Cloud coverage above threshold activates cloud suppression."""
        readings = make_weather_readings(cloud_coverage_above_threshold=True)
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_cloud_coverage_below_threshold(self) -> None:
        """Cloud coverage below threshold (cloud_coverage_above_threshold=False) → inactive."""
        readings = make_weather_readings(cloud_coverage_above_threshold=False)
        assert _is_cloud_suppression_active(True, readings) is False

    def test_active_when_cloud_coverage_above_regardless_of_weather(self) -> None:
        """Cloud coverage above threshold activates even when weather says sunny."""
        readings = make_weather_readings(
            is_sunny=True, cloud_coverage_above_threshold=True
        )
        assert _is_cloud_suppression_active(True, readings) is True

    def test_active_when_all_four_conditions_indicate_low_light(self) -> None:
        """All four low-light conditions → active."""
        readings = make_weather_readings(
            is_sunny=False,
            lux_below_threshold=True,
            irradiance_below_threshold=True,
            cloud_coverage_above_threshold=True,
        )
        assert _is_cloud_suppression_active(True, readings) is True

    def test_inactive_when_all_four_conditions_indicate_sun(self) -> None:
        """All four sunny conditions → inactive."""
        readings = make_weather_readings(
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        assert _is_cloud_suppression_active(True, readings) is False

    def test_cloud_coverage_disabled_by_toggle(self) -> None:
        """Toggle off → inactive even when cloud coverage is above threshold."""
        readings = make_weather_readings(cloud_coverage_above_threshold=True)
        assert _is_cloud_suppression_active(False, readings) is False


# ---------------------------------------------------------------------------
# Pipeline integration — cloud suppression vs solar handler
# ---------------------------------------------------------------------------


class TestCloudSuppressionPipelineIntegration:
    """Test that cloud suppression correctly overrides solar in the pipeline."""

    def _make_registry(self) -> PipelineRegistry:
        return PipelineRegistry(
            [
                ForceOverrideHandler(),
                ManualOverrideHandler(),
                CloudSuppressionHandler(),
                ClimateHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )

    def test_cloud_suppression_overrides_solar_handler(self) -> None:
        """Cloud suppression (priority 60) fires before solar (priority 40)."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            direct_sun_valid=True,  # solar would normally fire
            calculate_percentage_return=30,
            default_position=80,
            climate_readings=make_weather_readings(is_sunny=False),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 80

    def test_solar_fires_when_cloud_suppression_inactive(self) -> None:
        """When cloud suppression is off, solar handler controls position."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            direct_sun_valid=True,
            calculate_percentage_return=35,
            default_position=80,
            climate_readings=make_weather_readings(is_sunny=True),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=False,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 35

    def test_default_when_both_cloud_and_solar_inactive(self) -> None:
        """Default handler takes over when cloud suppression and solar are both off."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            direct_sun_valid=False,
            cover_default=60,
            default_position=60,
            climate_readings=make_weather_readings(is_sunny=True),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=False,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 60

    def test_cloud_suppression_uses_default_position(self) -> None:
        """Cloud suppression returns the default position, not the calculated one."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            direct_sun_valid=True,
            calculate_percentage_return=10,
            default_position=50,
            climate_readings=make_weather_readings(is_sunny=False),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.position == 50

    def test_cloud_suppression_defers_to_force_override(self) -> None:
        """Force override (priority 100) beats cloud suppression (priority 60)."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.test": True},
            force_override_position=0,
            default_position=80,
            climate_readings=make_weather_readings(is_sunny=False),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.FORCE

    def test_cloud_suppression_defers_to_manual_override(self) -> None:
        """Manual override (priority 70) beats cloud suppression (priority 60)."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            manual_override_active=True,
            calculate_percentage_return=40,
            default_position=80,
            climate_readings=make_weather_readings(is_sunny=False),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.MANUAL

    def test_cloud_suppression_overrides_climate(self) -> None:
        """Cloud suppression (priority 60) fires before climate handler (priority 50)."""
        registry = self._make_registry()
        snapshot = make_snapshot(
            climate_mode_enabled=True,
            default_position=70,
            climate_readings=make_weather_readings(is_sunny=False),
            climate_options=ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
            ),
        )
        result = registry.evaluate(snapshot)
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 70
