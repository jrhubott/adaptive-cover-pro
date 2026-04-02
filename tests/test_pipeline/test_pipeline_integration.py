"""End-to-end pipeline integration tests verifying priority ordering."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
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
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot


def _make_registry() -> PipelineRegistry:
    return PipelineRegistry(
        [
            ForceOverrideHandler(),
            MotionTimeoutHandler(),
            ManualOverrideHandler(),
            CloudSuppressionHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _cloudy_readings() -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=False,  # triggers cloud suppression
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
    )


class TestPipelineIntegration:
    """Verify that handlers fire in the correct priority order."""

    registry = _make_registry()

    def test_force_override_beats_everything(self) -> None:
        """FORCE fires even when solar is valid and motion is active."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.alert": True},
            force_override_position=0,
            direct_sun_valid=True,
            motion_timeout_active=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.FORCE
        assert result.position == 0

    def test_motion_timeout_beats_solar(self) -> None:
        """MOTION fires when motion timeout active even with sun in FOV."""
        snap = make_snapshot(
            motion_timeout_active=True,
            direct_sun_valid=True,
            calculate_percentage_return=75.0,
            cover_default=10.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.MOTION

    def test_manual_override_beats_solar(self) -> None:
        """MANUAL fires when manual override active even with sun in FOV."""
        snap = make_snapshot(
            manual_override_active=True,
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.MANUAL

    def test_cloud_suppression_beats_solar(self) -> None:
        """CLOUD fires before solar when suppression enabled and not sunny."""
        snap = make_snapshot(
            direct_sun_valid=True,
            calculate_percentage_return=70.0,
            climate_readings=_cloudy_readings(),
            climate_options=_cloud_options(),
            default_position=15,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 15

    def test_solar_beats_default(self) -> None:
        """SOLAR fires when sun is in FOV and no overrides active."""
        snap = make_snapshot(
            direct_sun_valid=True,
            calculate_percentage_return=55.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 55

    def test_default_fires_when_no_sun(self) -> None:
        """DEFAULT fires when no other handler matches."""
        snap = make_snapshot(
            direct_sun_valid=False,
            cover_default=30.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.DEFAULT

    def test_decision_trace_includes_all_handlers(self) -> None:
        """Decision trace must list every registered handler."""
        snap = make_snapshot()
        result = self.registry.evaluate(snap)
        handler_names = {step.handler for step in result.decision_trace}
        expected = {
            "force_override",
            "motion_timeout",
            "manual_override",
            "cloud_suppression",
            "solar",
            "default",
        }
        assert handler_names == expected

    def test_winning_handler_marked_matched_true(self) -> None:
        """The winning handler in decision trace has matched=True."""
        snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=50.0)
        result = self.registry.evaluate(snap)
        matched = [s for s in result.decision_trace if s.matched]
        assert len(matched) == 1
        assert matched[0].handler == "solar"
