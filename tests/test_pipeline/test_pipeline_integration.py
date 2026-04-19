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
from unittest.mock import MagicMock, patch

from custom_components.adaptive_cover_pro.config_types import GlareZone, GlareZonesConfig
from custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone import (
    GlareZoneHandler,
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
        transparent_blind=True,  # triggers SUMMER_COOLING close — climate wins directly
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

    def test_registry_climate_data_populated_when_non_climate_handler_wins(
        self,
    ) -> None:
        """climate_data is populated even when a non-climate handler wins (#182)."""
        snap = make_snapshot(
            manual_override_active=True,
            climate_mode_enabled=True,
            climate_readings=_climate_readings_summer(),
            climate_options=_climate_options_summer(),
        )
        result = self.registry.evaluate(snap)
        assert result.control_method.name == "MANUAL"
        assert result.climate_data is not None
        assert result.climate_data.is_summer is True

    def test_registry_climate_data_populated_when_climate_defers_glare_control(
        self,
    ) -> None:
        """climate_data must be present when ClimateHandler returns None for GLARE_CONTROL.

        Regression for Issue #240: intermediate temp + presence + sunny causes ClimateHandler
        to defer (returns None) so SolarHandler wins.  Before the fix, climate_data was lost
        because the merge loop only iterated matching results.  After the fix,
        ClimateHandler.contribute() surfaces climate_data regardless.
        """
        from unittest.mock import MagicMock

        cover = MagicMock()
        cover.direct_sun_valid = True
        cover.valid = True
        cover.calculate_percentage = MagicMock(return_value=55.0)
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
            climate_readings=_climate_readings_intermediate(),
            climate_options=_climate_options_intermediate(),
            direct_sun_valid=True,
            calculate_percentage_return=55.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR, (
            "ClimateHandler should defer (GLARE_CONTROL) so SolarHandler wins"
        )
        assert result.climate_data is not None, (
            "REGRESSION (Issue #240): climate_data was dropped when ClimateHandler "
            "returned None for the GLARE_CONTROL defer path"
        )
        assert isinstance(result.climate_data, ClimateCoverData)
        assert result.climate_data.is_presence is True
        assert result.climate_data.is_sunny is True

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
            custom_position_sensors=[
                ("binary_sensor.scene", True, 55, 77, False, False)
            ],
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
            custom_position_sensors=[
                ("binary_sensor.scene", True, 55, 77, False, False)
            ],
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.MANUAL

    def test_custom_position_beats_solar(self) -> None:
        """CUSTOM_POSITION fires before solar tracking when a sensor is active."""
        # Build registry with the matching position for this test
        registry_33 = _make_registry(custom_position=33)
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", True, 33, 77, False, False)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=80.0,
        )
        result = registry_33.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 33

    def test_solar_fires_when_custom_sensors_all_off(self) -> None:
        """Solar handler wins when custom sensors are configured but all off."""
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", False, 33, 77, False, False)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=72.0,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR

    def test_default_fires_when_no_custom_sensors_and_no_sun(self) -> None:
        """Default handler wins when custom sensors are off and sun not in FOV."""
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", False, 50, 77, False, False)
            ],
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
                CustomPositionHandler(
                    slot=1, entity_id="binary_sensor.scene", position=30, priority=95
                ),
                WeatherOverrideHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", True, 30, 95, False, False)
            ],
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
                CustomPositionHandler(
                    slot=1, entity_id="binary_sensor.scene", position=80, priority=35
                ),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.scene", True, 80, 35, False, False)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR

    def test_two_custom_slots_higher_priority_wins(self) -> None:
        """When two custom slots are active, the higher-priority slot wins."""
        registry = PipelineRegistry(
            [
                CustomPositionHandler(
                    slot=1, entity_id="binary_sensor.slot1", position=20, priority=85
                ),
                CustomPositionHandler(
                    slot=2, entity_id="binary_sensor.slot2", position=60, priority=70
                ),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.slot1", True, 20, 85, False, False),
                ("binary_sensor.slot2", True, 60, 70, False, False),
            ],
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 20  # slot1 at priority 85 wins over slot2 at 70

    def test_two_custom_slots_only_lower_active(self) -> None:
        """When the higher-priority slot is off, the lower-priority slot wins."""
        registry = PipelineRegistry(
            [
                CustomPositionHandler(
                    slot=1, entity_id="binary_sensor.slot1", position=20, priority=85
                ),
                CustomPositionHandler(
                    slot=2, entity_id="binary_sensor.slot2", position=60, priority=70
                ),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                ("binary_sensor.slot1", False, 20, 85, False, False),
                ("binary_sensor.slot2", True, 60, 70, False, False),
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
                CustomPositionHandler(
                    slot=1, entity_id="binary_sensor.scene", position=45, priority=77
                ),
                MotionTimeoutHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        # Manual active → custom should NOT fire
        snap_manual = make_snapshot(
            manual_override_active=True,
            custom_position_sensors=[
                ("binary_sensor.scene", True, 45, 77, False, False)
            ],
        )
        result = registry.evaluate(snap_manual)
        assert result.control_method == ControlMethod.MANUAL

        # Motion timeout active, no manual → custom SHOULD fire
        snap_motion = make_snapshot(
            manual_override_active=False,
            motion_timeout_active=True,
            custom_position_sensors=[
                ("binary_sensor.scene", True, 45, 77, False, False)
            ],
            default_position=10,
        )
        result = registry.evaluate(snap_motion)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 45


class TestClimateStrategyEndToEnd:
    """End-to-end pipeline tests verifying climate strategies fire correctly.

    These tests exist to catch the regression from Issue #134 where temperature
    and presence were silently not read, causing climate mode to always fall
    through to GLARE_CONTROL regardless of temperature configuration.

    Each test builds a full PipelineSnapshot with ClimateReadings that have
    real temperature values and verifies the correct strategy/position is produced.
    """

    registry = _make_climate_registry()

    # ------------------------------------------------------------------
    # Summer strategy
    # ------------------------------------------------------------------

    def test_summer_strategy_closes_blind_when_no_presence(self) -> None:
        """No-presence summer: temperature above temp_high + no one home → position 0 (closed).

        Without occupants and in summer, the blind closes fully for energy savings.
        REGRESSION guard (Issue #134): if inside_temperature is None (not wired in
        coordinator), is_summer is always False and summer cooling never fires.
        """
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=30.0,  # above temp_high=26
            is_presence=False,  # no one home → no-presence path → close
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,  # no outside threshold → outside_high=True
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SUMMER, (
            "REGRESSION (Issue #134): summer strategy did not fire — "
            "check that temp_entity is forwarded to ClimateProvider.read()"
        )
        assert result.position == 0

    def test_summer_strategy_with_presence_tracks_solar(self) -> None:
        """Presence + summer + opaque blind → climate defers, SolarHandler tracks sun.

        When occupants are present and the blind is opaque, ClimateHandler defers
        (GLARE_CONTROL strategy returns None) so SolarHandler wins at priority 40.
        The cover solar-tracks rather than closing fully — same position outcome as
        before, but now cleanly owned by SolarHandler.
        """
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=30.0,  # above temp_high=26
            is_presence=True,  # someone home
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,  # opaque blind → climate defers
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = self.registry.evaluate(snap)
        # SolarHandler wins (climate deferred) — SOLAR control method, solar-tracked position
        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 60  # solar-tracked, not closed

    def test_summer_strategy_with_presence_and_transparent_blind_closes(self) -> None:
        """Presence + summer + transparent blind → closes fully (SUMMER_COOLING).

        Transparent blinds block heat without blocking light, so closing fully
        is appropriate even with occupants present.
        """
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=30.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=True,  # transparent → close fully with presence
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SUMMER
        assert result.position == 0

    def test_summer_strategy_requires_temperature_above_threshold(self) -> None:
        """Temperature at threshold (not above) should NOT trigger summer."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=26.0,  # exactly at temp_high — not above
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
            calculate_percentage_return=55.0,
        )
        result = self.registry.evaluate(snap)
        # 26.0 is not > 26.0, so is_summer is False → falls to glare control (SOLAR)
        assert result.control_method != ControlMethod.SUMMER

    # ------------------------------------------------------------------
    # Winter strategy
    # ------------------------------------------------------------------

    def test_winter_strategy_opens_blind(self) -> None:
        """Temperature below temp_low triggers WINTER strategy → position 100 (open)."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=15.0,  # below temp_low=18
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.WINTER, (
            "REGRESSION (Issue #134): winter strategy did not fire — "
            "check that temp_entity is forwarded to ClimateProvider.read()"
        )
        assert result.position == 100

    def test_winter_strategy_with_sun_not_in_fov_opens_blind(self) -> None:
        """Winter strategy opens blind even when sun is not directly in FOV."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=10.0,  # very cold
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=False,  # sun not in FOV
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.WINTER
        assert result.position == 100

    # ------------------------------------------------------------------
    # Null temperature degrades gracefully (documents Issue #134 pre-fix behavior)
    # ------------------------------------------------------------------

    def test_null_temperature_falls_through_to_glare_control(self) -> None:
        """When temperature is None, is_winter and is_summer are both False.

        Climate mode stays active but the strategy degrades to GLARE_CONTROL
        (solar tracking) — this is the documented pre-fix symptom of Issue #134
        and the expected graceful fallback when sensors are unavailable.
        """
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=None,  # sensor unavailable / not wired
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
            calculate_percentage_return=55.0,
        )
        result = self.registry.evaluate(snap)
        # Not summer, not winter → climate defers (GLARE_CONTROL), SolarHandler wins
        assert result.control_method == ControlMethod.SOLAR, (
            "With null temperature, climate defers to SolarHandler (SOLAR). "
            "If this changes, update this test and the Issue #134 regression notes."
        )

    # ------------------------------------------------------------------
    # Outside temperature path (temp_switch=True)
    # ------------------------------------------------------------------

    def test_outside_temp_used_when_temp_switch_enabled(self) -> None:
        """When temp_switch=True, outside_temperature drives summer/winter."""
        readings = ClimateReadings(
            outside_temperature=32.0,  # hot outside → summer
            inside_temperature=22.0,  # inside is comfortable — ignored when temp_switch=True
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=True,  # use outside temp
            transparent_blind=True,  # SUMMER_COOLING path — climate closes to 0%
            temp_summer_outside=None,  # no secondary outside gate
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SUMMER, (
            "REGRESSION (Issue #134): outside temp path broken — check "
            "CONF_OUTSIDETEMP_ENTITY is forwarded as outside_entity."
        )
        assert result.position == 0

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    def test_absence_triggers_summer_cooling_when_hot(self) -> None:
        """No presence + hot → summer cooling closes blind."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=30.0,
            is_presence=False,  # no one home
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.SUMMER
        assert result.position == 0

    def test_absence_triggers_winter_heating_when_cold(self) -> None:
        """No presence + cold → winter heating opens blind."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=12.0,
            is_presence=False,  # no one home
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
        )
        result = self.registry.evaluate(snap)
        assert result.control_method == ControlMethod.WINTER
        assert result.position == 100

    def test_assumed_presence_when_is_presence_true(self) -> None:
        """is_presence=True uses the with-presence path (solar tracking in glare zone)."""
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,  # between temp_low and temp_high
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = self.registry.evaluate(snap)
        # Between thresholds with presence → GLARE_CONTROL (solar tracking)
        assert result.control_method == ControlMethod.SOLAR

    # ------------------------------------------------------------------
    # climate_data propagated on result
    # ------------------------------------------------------------------

    def test_climate_data_on_result_has_correct_temperatures(self) -> None:
        """Pipeline result carries climate_data with the values from ClimateReadings."""
        from unittest.mock import MagicMock

        cover = MagicMock()
        cover.direct_sun_valid = True
        cover.valid = True
        cover.calculate_percentage = MagicMock(return_value=50.0)
        cover.logger = MagicMock()
        config = MagicMock()
        config.min_pos = None
        config.max_pos = None
        config.min_pos_sun_only = False
        config.max_pos_sun_only = False
        cover.config = config

        readings = ClimateReadings(
            outside_temperature=25.0,
            inside_temperature=30.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=True,  # SUMMER_COOLING → climate wins, climate_data on result
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
        )
        result = self.registry.evaluate(snap)
        assert result.climate_data is not None
        assert result.climate_data.inside_temperature == 30.0, (
            "REGRESSION (Issue #134): climate_data.inside_temperature is None — "
            "temp_entity is not being forwarded to ClimateProvider.read()"
        )
        assert result.climate_data.outside_temperature == 25.0, (
            "REGRESSION (Issue #134): climate_data.outside_temperature is None — "
            "outside_entity is not being forwarded to ClimateProvider.read()"
        )


def _climate_readings_intermediate() -> ClimateReadings:
    """Intermediate temperature — ClimateHandler defers (returns None) for GLARE_CONTROL."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=22.0,  # between temp_low=18 and temp_high=26
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _climate_options_intermediate() -> ClimateOptions:
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=False,
        winter_close_insulation=False,
    )


def _make_glare_climate_cover(calculate_percentage_return: float = 91.0):
    """Mock vertical cover satisfying both GlareZoneHandler and ClimateHandler."""
    cover = MagicMock()
    cover.direct_sun_valid = True
    cover.valid = True
    cover.distance = 3.0
    cover.gamma = 0.0
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


class TestGlareZoneVsClimatePriority:
    """Regression for issue #231 — glare zone wins because climate defers GLARE_CONTROL."""

    def _make_registry(self) -> PipelineRegistry:
        return PipelineRegistry(
            [
                ForceOverrideHandler(),
                ManualOverrideHandler(),
                MotionTimeoutHandler(),
                CloudSuppressionHandler(),
                GlareZoneHandler(),
                ClimateHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )

    def test_glare_zone_beats_climate_glare_control(self) -> None:
        """Glare zone wins when climate mode defers in the intermediate-season case.

        REGRESSION (Issue #231): ClimateHandler previously computed solar position
        directly for GLARE_CONTROL and outprioritized GlareZoneHandler (priority 45).
        Fix: ClimateHandler returns None for GLARE_CONTROL, allowing GlareZoneHandler
        to fire naturally at priority 45 (below ClimateHandler at 50).
        """
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="tv", x=0.0, y=1.5, radius=0.3)],
            window_width=1.2,
        )
        snap = make_snapshot(
            cover=_make_glare_climate_cover(),
            cover_type="cover_blind",
            climate_mode_enabled=True,
            climate_readings=_climate_readings_intermediate(),
            climate_options=_climate_options_intermediate(),
            direct_sun_valid=True,
            glare_zones=glare_cfg,
            active_zone_names={"tv"},
        )
        # effective distance 1.0m < base_distance 3.0m → zone demands more coverage
        with patch(
            "custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone.glare_zone_effective_distance",
            return_value=1.0,
        ):
            result = self._make_registry().evaluate(snap)

        assert result.control_method == ControlMethod.GLARE_ZONE, (
            "REGRESSION (Issue #231): GlareZone must win when climate defers "
            "the glare-control case to lower-priority handlers."
        )

    def test_solar_wins_when_no_glare_zones_active(self) -> None:
        """Solar wins when climate defers and no glare zones are configured."""
        snap = make_snapshot(
            cover=_make_glare_climate_cover(),
            cover_type="cover_blind",
            climate_mode_enabled=True,
            climate_readings=_climate_readings_intermediate(),
            climate_options=_climate_options_intermediate(),
            direct_sun_valid=True,
            glare_zones=None,
            active_zone_names=set(),
        )
        result = self._make_registry().evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR

    def test_solar_wins_when_zone_beyond_base_distance(self) -> None:
        """Solar wins when climate defers and the glare zone is already shaded."""
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="tv", x=0.0, y=1.5, radius=0.3)],
            window_width=1.2,
        )
        snap = make_snapshot(
            cover=_make_glare_climate_cover(),
            cover_type="cover_blind",
            climate_mode_enabled=True,
            climate_readings=_climate_readings_intermediate(),
            climate_options=_climate_options_intermediate(),
            direct_sun_valid=True,
            glare_zones=glare_cfg,
            active_zone_names={"tv"},
        )
        # effective distance 5.0m >= base_distance 3.0m → zone already shaded, fall through
        with patch(
            "custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone.glare_zone_effective_distance",
            return_value=5.0,
        ):
            result = self._make_registry().evaluate(snap)

        assert result.control_method != ControlMethod.GLARE_ZONE

    def test_climate_wins_directly_when_no_presence(self) -> None:
        """Climate wins directly (no defer) when nobody is home — never reaches glare/solar."""
        no_presence_readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,
            is_presence=False,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = make_snapshot(
            cover=_make_glare_climate_cover(),
            cover_type="cover_blind",
            climate_mode_enabled=True,
            climate_readings=no_presence_readings,
            climate_options=_climate_options_intermediate(),
            direct_sun_valid=True,
            glare_zones=None,
            active_zone_names=set(),
        )
        result = self._make_registry().evaluate(snap)
        # normal_without_presence returns default — climate wins and populates climate_data
        assert result.climate_data is not None  # climate won (not glare/solar which have no climate_data)
        assert result.control_method != ControlMethod.GLARE_ZONE
