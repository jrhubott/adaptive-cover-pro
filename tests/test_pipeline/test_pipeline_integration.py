"""End-to-end pipeline integration tests verifying priority ordering."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.force_override import (
    ForceOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
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


# Entity ID used by the default custom position handler in integration tests.
_CUSTOM_SENSOR = "binary_sensor.scene"


def _make_registry(
    custom_entity: str = _CUSTOM_SENSOR,
    custom_position: int = 55,
    custom_priority: int = 77,
) -> PipelineRegistry:
    """Build a test registry with one CustomPositionHandler slot."""
    return PipelineRegistry(
        [
            ForceOverrideHandler(),
            ManualOverrideHandler(),
            CustomPositionHandler(
                slot=1,
                entity_id=custom_entity,
                position=custom_position,
                priority=custom_priority,
            ),
            MotionTimeoutHandler(),
            CloudSuppressionHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _make_climate_registry() -> PipelineRegistry:
    """Registry that includes the ClimateHandler for climate-specific tests."""
    return PipelineRegistry(
        [
            ForceOverrideHandler(),
            MotionTimeoutHandler(),
            ManualOverrideHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _climate_readings_summer() -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=30.0,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _climate_options_summer() -> ClimateOptions:
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
        winter_close_insulation=False,
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
            default_position=int(10.0),
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
            default_position=int(30.0),
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
            "manual_override",
            "custom_position_1",  # per-instance name includes slot number
            "motion_timeout",
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


class TestClimateDataPropagation:
    """Verify climate_data flows from ClimateHandler through the registry."""

    registry = _make_climate_registry()

    def test_registry_copies_climate_data_when_climate_wins(self) -> None:
        """Registry result carries climate_data when ClimateHandler is the winner."""
        from unittest.mock import MagicMock

        cover = MagicMock()
        cover.direct_sun_valid = True
        cover.valid = True
        cover.calculate_percentage = MagicMock(return_value=60.0)
        cover.logger = MagicMock()
        config = MagicMock()
        config.min_pos = None
        config.max_pos = None
        config.min_pos_sun_only = False
        config.max_pos_sun_only = False
        cover.config = config

        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_climate_readings_summer(),
            climate_options=_climate_options_summer(),
        )
        result = self.registry.evaluate(snap)
        assert result.control_method.name == "SUMMER"
        assert result.climate_data is not None
        assert isinstance(result.climate_data, ClimateCoverData)
        assert result.climate_data.is_summer is True

    def test_registry_climate_data_none_when_non_climate_handler_wins(self) -> None:
        """climate_data is None on registry result when a non-climate handler wins."""
        snap = make_snapshot(
            manual_override_active=True,
            climate_mode_enabled=True,
            climate_readings=_climate_readings_summer(),
            climate_options=_climate_options_summer(),
        )
        result = self.registry.evaluate(snap)
        assert result.control_method.name == "MANUAL"
        assert result.climate_data is None

    def test_registry_copies_tilt_from_winning_handler(self) -> None:
        """Registry result copies tilt field from the winning handler's result."""
        from unittest.mock import patch
        from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
            SolarHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
        from custom_components.adaptive_cover_pro.enums import ControlMethod as CM

        # Patch SolarHandler to return a result with tilt=45
        with patch.object(
            SolarHandler,
            "evaluate",
            return_value=PipelineResult(
                position=50,
                control_method=CM.SOLAR,
                reason="test",
                tilt=45,
            ),
        ):
            snap = make_snapshot(direct_sun_valid=True)
            result = self.registry.evaluate(snap)
        assert result.tilt == 45


class TestCustomPositionPriority:
    """Verify custom_position sits at priority 77: below manual (80), above motion (75)."""

    def setup_method(self) -> None:
        """Create a fresh registry for each test."""
        self.registry = _make_registry()

    def test_custom_position_beats_motion_timeout(self) -> None:
        """CUSTOM_POSITION fires instead of motion timeout when a sensor is active."""
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", True, 55, 77)],
            motion_timeout_active=True,
            default_position=10,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 55

    def test_manual_override_beats_custom_position(self) -> None:
        """MANUAL fires before custom_position when manual override is active."""
        snap = make_snapshot(
            manual_override_active=True,
            custom_position_sensors=[("binary_sensor.scene", True, 55, 77)],
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.MANUAL

    def test_custom_position_beats_solar(self) -> None:
        """CUSTOM_POSITION fires before solar tracking when a sensor is active."""
        # Build registry with the matching position for this test
        registry_33 = _make_registry(custom_position=33)
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", True, 33, 77)],
            direct_sun_valid=True,
            calculate_percentage_return=80.0,
        )
        result = registry_33.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 33

    def test_solar_fires_when_custom_sensors_all_off(self) -> None:
        """Solar handler wins when custom sensors are configured but all off."""
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", False, 33, 77)],
            direct_sun_valid=True,
            calculate_percentage_return=72.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR

    def test_default_fires_when_no_custom_sensors_and_no_sun(self) -> None:
        """Default handler wins when custom sensors are off and sun not in FOV."""
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", False, 50, 77)],
            direct_sun_valid=False,
            default_position=20,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.DEFAULT


class TestCustomPositionConfigurablePriority:
    """Verify that custom position priority controls evaluation order."""

    def test_high_priority_custom_beats_weather_override(self) -> None:
        """Custom slot at priority 95 fires before weather override (90)."""
        from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
            WeatherOverrideHandler,
        )

        registry = PipelineRegistry(
            [
                CustomPositionHandler(slot=1, entity_id="binary_sensor.scene", position=30, priority=95),
                WeatherOverrideHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", True, 30, 95)],
            weather_override_active=True,
            weather_override_position=0,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 30

    def test_low_priority_custom_loses_to_solar(self) -> None:
        """Custom slot at priority 35 (below solar 40) does not fire when sun is valid."""
        registry = PipelineRegistry(
            [
                CustomPositionHandler(slot=1, entity_id="binary_sensor.scene", position=80, priority=35),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[("binary_sensor.scene", True, 80, 35)],
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR

    def test_two_custom_slots_higher_priority_wins(self) -> None:
        """When two custom slots are active, the higher-priority slot wins."""
        registry = PipelineRegistry(
            [
                CustomPositionHandler(slot=1, entity_id="binary_sensor.slot1", position=20, priority=85),
                CustomPositionHandler(slot=2, entity_id="binary_sensor.slot2", position=60, priority=70),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.slot1", True, 20, 85),
                ("binary_sensor.slot2", True, 60, 70),
            ],
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 20  # slot1 at priority 85 wins over slot2 at 70

    def test_two_custom_slots_only_lower_active(self) -> None:
        """When the higher-priority slot is off, the lower-priority slot wins."""
        registry = PipelineRegistry(
            [
                CustomPositionHandler(slot=1, entity_id="binary_sensor.slot1", position=20, priority=85),
                CustomPositionHandler(slot=2, entity_id="binary_sensor.slot2", position=60, priority=70),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.slot1", False, 20, 85),
                ("binary_sensor.slot2", True, 60, 70),
            ],
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 60  # slot2 wins since slot1 is off

    def test_backward_compat_default_priority_between_manual_and_motion(self) -> None:
        """Default priority 77 preserves original behavior: below manual (80), above motion (75)."""
        registry = PipelineRegistry(
            [
                ManualOverrideHandler(),
                CustomPositionHandler(slot=1, entity_id="binary_sensor.scene", position=45, priority=77),
                MotionTimeoutHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        # Manual active → custom should NOT fire
        snap_manual = make_snapshot(
            manual_override_active=True,
            custom_position_sensors=[("binary_sensor.scene", True, 45, 77)],
        )
        result = registry.evaluate(snap_manual)
        assert result.control_method == ControlMethod.MANUAL

        # Motion timeout active, no manual → custom SHOULD fire
        snap_motion = make_snapshot(
            manual_override_active=False,
            motion_timeout_active=True,
            custom_position_sensors=[("binary_sensor.scene", True, 45, 77)],
            default_position=10,
        )
        result = registry.evaluate(snap_motion)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 45
