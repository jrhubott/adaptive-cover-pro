"""Tests for individual override handlers."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    ForceOverrideHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)

from tests.test_pipeline.conftest import make_snapshot


# ---------------------------------------------------------------------------
# PipelineResult.skip_command
# ---------------------------------------------------------------------------


class TestPipelineResultSkipCommand:
    """PipelineResult carries a skip_command flag for hold-mode handlers."""

    def test_skip_command_defaults_false(self) -> None:
        """skip_command is False by default — no behavior change for existing results."""
        from custom_components.adaptive_cover_pro.enums import ControlMethod

        r = PipelineResult(
            position=42, control_method=ControlMethod.DEFAULT, reason="x"
        )
        assert r.skip_command is False

    def test_skip_command_can_be_set_true(self) -> None:
        """skip_command=True can be constructed for hold-mode results."""
        from custom_components.adaptive_cover_pro.enums import ControlMethod

        r = PipelineResult(
            position=42,
            control_method=ControlMethod.MOTION,
            reason="hold",
            skip_command=True,
        )
        assert r.skip_command is True


# ---------------------------------------------------------------------------
# PipelineSnapshot new fields — motion_timeout_mode / current_cover_position
# ---------------------------------------------------------------------------


class TestPipelineSnapshotNewFields:
    """PipelineSnapshot carries motion_timeout_mode and current_cover_position."""

    def test_motion_timeout_mode_defaults_return_to_default(self) -> None:
        snap = make_snapshot()
        assert snap.motion_timeout_mode == "return_to_default"

    def test_motion_timeout_mode_accepts_hold_position(self) -> None:
        snap = make_snapshot(motion_timeout_mode="hold_position")
        assert snap.motion_timeout_mode == "hold_position"

    def test_current_cover_position_defaults_none(self) -> None:
        snap = make_snapshot()
        assert snap.current_cover_position is None

    def test_current_cover_position_accepts_integer(self) -> None:
        snap = make_snapshot(current_cover_position=42)
        assert snap.current_cover_position == 42


def test_pipeline_snapshot_is_importable() -> None:
    """PipelineSnapshot and ClimateOptions must be importable from pipeline.types."""
    from custom_components.adaptive_cover_pro.pipeline.types import (
        ClimateOptions,
        PipelineSnapshot,
    )

    assert ClimateOptions is not None
    assert PipelineSnapshot is not None


# ---------------------------------------------------------------------------
# ForceOverrideHandler
# ---------------------------------------------------------------------------


class TestForceOverrideHandler:
    """Tests for ForceOverrideHandler."""

    handler = ForceOverrideHandler()

    def test_returns_none_when_no_sensors(self) -> None:
        """Return None when no force override sensors are configured."""
        snap = make_snapshot(force_override_sensors={})
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_all_sensors_off(self) -> None:
        """Return None when all sensors are off."""
        snap = make_snapshot(
            force_override_sensors={
                "binary_sensor.wind": False,
                "binary_sensor.rain": False,
            }
        )
        assert self.handler.evaluate(snap) is None

    def test_matches_when_any_sensor_on(self) -> None:
        """Return FORCE result when any sensor is on."""
        snap = make_snapshot(
            force_override_sensors={
                "binary_sensor.wind": False,
                "binary_sensor.rain": True,
            },
            force_override_position=5,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 5
        assert result.control_method == ControlMethod.FORCE

    def test_matches_when_single_sensor_on(self) -> None:
        """Return FORCE result when exactly one sensor is on."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.alert": True},
            force_override_position=75,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 75

    def test_uses_force_override_position(self) -> None:
        """Position comes from force_override_position."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=10,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 10

    def test_describe_skip_mentions_force(self) -> None:
        """describe_skip mentions 'force' when skipped."""
        snap = make_snapshot(force_override_sensors={})
        reason = self.handler.describe_skip(snap)
        assert "force" in reason.lower()

    def test_priority_is_100(self) -> None:
        """ForceOverrideHandler has priority 100 (highest)."""
        assert ForceOverrideHandler.priority == 100

    def test_name(self) -> None:
        """ForceOverrideHandler name is 'force_override'."""
        assert ForceOverrideHandler.name == "force_override"


class TestForceOverrideHandlerMinMode:
    """Tests for ForceOverrideHandler minimum position mode."""

    handler = ForceOverrideHandler()

    def test_min_mode_off_uses_exact_position(self) -> None:
        """With min_mode off, position is always the configured value (default behavior)."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=False,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_higher_uses_calculated(self) -> None:
        """With min_mode on, if calculated position > floor, use calculated."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 50

    def test_min_mode_on_calculated_lower_uses_floor(self) -> None:
        """With min_mode on, if calculated position < floor, use the floor."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=10.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_equal_uses_floor(self) -> None:
        """With min_mode on, if calculated equals floor, position equals floor."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=30.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_reason_mentions_minimum_mode(self) -> None:
        """With min_mode on, reason string mentions minimum mode."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "minimum mode" in result.reason

    def test_min_mode_control_method_still_force(self) -> None:
        """ControlMethod remains FORCE regardless of min_mode."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=30,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=70.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.FORCE


# ---------------------------------------------------------------------------
# Min-mode with sun tracking disabled — regression tests for issue #264
# ---------------------------------------------------------------------------


class TestForceOverrideHandlerMinModeWithSunTrackingOff:
    """Force override min-mode floors correctly when sun tracking is disabled.

    Regression tests for #264: with tracking off and default=100, a
    configured floor of 80 must yield max(80, 100)=100, not max(80, 29)=80.
    """

    handler = ForceOverrideHandler()

    def test_min_mode_uses_default_not_solar_when_tracking_off(self) -> None:
        """Min-mode floor measured against default position, not solar, when tracking off."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=80,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 100

    def test_min_mode_floor_still_enforced_when_default_below_floor(self) -> None:
        """Floor is enforced when default position is below the configured floor."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=80,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=50,
            enable_sun_tracking=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 80

    def test_min_mode_sun_tracking_enabled_unchanged_regression(self) -> None:
        """Regression guard: solar floor semantics unchanged when tracking is on."""
        snap = make_snapshot(
            force_override_sensors={"binary_sensor.s": True},
            force_override_position=80,
            force_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=True,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 80  # max(80, 29) = 80 — solar floor applies


class TestWeatherOverrideHandlerMinModeWithSunTrackingOff:
    """Weather override min-mode floors correctly when sun tracking is disabled."""

    handler = WeatherOverrideHandler()

    def test_min_mode_uses_default_not_solar_when_tracking_off(self) -> None:
        """Min-mode floor measured against default position, not solar, when tracking off."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=80,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 100

    def test_min_mode_sun_tracking_enabled_unchanged_regression(self) -> None:
        """Regression guard: solar floor semantics unchanged when tracking is on."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=80,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=True,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 80  # max(80, 29) = 80 — solar floor applies


class TestCustomPositionHandlerMinModeWithSunTrackingOff:
    """Custom position min-mode floors correctly when sun tracking is disabled."""

    def _make_handler(self) -> CustomPositionHandler:
        return CustomPositionHandler(
            slot=1, entity_id="binary_sensor.cp1", position=80, priority=77
        )

    def test_min_mode_uses_default_not_solar_when_tracking_off(self) -> None:
        """Min-mode floor measured against default position, not solar, when tracking off."""
        handler = self._make_handler()
        snap = make_snapshot(
            custom_position_sensors=[
                CustomPositionSensorState(
                    entity_id="binary_sensor.cp1",
                    is_on=True,
                    position=80,
                    priority=77,
                    min_mode=True,
                    use_my=False,
                )
            ],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=False,
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert result.position == 100

    def test_min_mode_sun_tracking_enabled_unchanged_regression(self) -> None:
        """Regression guard: solar floor semantics unchanged when tracking is on."""
        handler = self._make_handler()
        snap = make_snapshot(
            custom_position_sensors=[
                CustomPositionSensorState(
                    entity_id="binary_sensor.cp1",
                    is_on=True,
                    position=80,
                    priority=77,
                    min_mode=True,
                    use_my=False,
                )
            ],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=True,
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert result.position == 80  # max(80, 29) = 80 — solar floor applies


# ---------------------------------------------------------------------------
# MotionTimeoutHandler
# ---------------------------------------------------------------------------


class TestMotionTimeoutHandler:
    """Tests for MotionTimeoutHandler."""

    handler = MotionTimeoutHandler()

    def test_matches_when_active(self) -> None:
        """Return MOTION method when motion timeout is active."""
        snap = make_snapshot(motion_timeout_active=True, default_position=int(20.0))
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.MOTION

    def test_returns_none_when_inactive(self) -> None:
        """Return None when motion timeout is not active."""
        snap = make_snapshot(motion_timeout_active=False)
        assert self.handler.evaluate(snap) is None

    def test_uses_snapshot_default_position(self) -> None:
        """Return position from snapshot.default_position (not cover.default) when timeout active."""
        snap = make_snapshot(motion_timeout_active=True, default_position=int(33.0))
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 33

    def test_returns_none_when_motion_control_disabled(self) -> None:
        """Return None when motion_control_enabled is False even if timeout is active."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_control_enabled=False,
            default_position=20,
        )
        assert self.handler.evaluate(snap) is None

    def test_matches_when_enabled_and_active(self) -> None:
        """Return MOTION result when motion_control_enabled is True and timeout is active."""
        snap = make_snapshot(
            motion_timeout_active=True, motion_control_enabled=True, default_position=20
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.MOTION

    def test_describe_skip_motion_control_disabled(self) -> None:
        """describe_skip returns 'motion control disabled' when switch is off."""
        snap = make_snapshot(motion_timeout_active=True, motion_control_enabled=False)
        assert self.handler.describe_skip(snap) == "motion control disabled"

    def test_describe_skip_timeout_not_active(self) -> None:
        """describe_skip returns timeout-not-active message when enabled but no timeout."""
        snap = make_snapshot(motion_timeout_active=False, motion_control_enabled=True)
        reason = self.handler.describe_skip(snap)
        assert "motion" in reason.lower()
        assert "disabled" not in reason.lower()

    def test_priority_is_75(self) -> None:
        """MotionTimeoutHandler has priority 75."""
        assert MotionTimeoutHandler.priority == 75

    def test_name(self) -> None:
        """MotionTimeoutHandler name is 'motion_timeout'."""
        assert MotionTimeoutHandler.name == "motion_timeout"


class TestMotionTimeoutHandlerHoldMode:
    """Tests for MotionTimeoutHandler hold_position mode."""

    handler = MotionTimeoutHandler()

    def test_hold_fires_when_sun_active(self) -> None:
        """hold_position + in_time_window + direct_sun_valid → skip_command=True at current pos."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_timeout_mode="hold_position",
            in_time_window=True,
            direct_sun_valid=True,
            current_cover_position=42,
            default_position=10,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.skip_command is True
        assert result.position == 42
        assert result.control_method == ControlMethod.MOTION
        assert "hold" in result.reason.lower()

    def test_hold_exits_when_sun_not_valid(self) -> None:
        """hold_position + direct_sun_valid=False → fall through to default position."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_timeout_mode="hold_position",
            in_time_window=True,
            direct_sun_valid=False,
            current_cover_position=42,
            default_position=10,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.skip_command is False
        assert result.position == 10

    def test_hold_exits_outside_time_window(self) -> None:
        """hold_position + in_time_window=False → fall through to default position."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_timeout_mode="hold_position",
            in_time_window=False,
            direct_sun_valid=True,
            current_cover_position=42,
            default_position=10,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.skip_command is False
        assert result.position == 10

    def test_hold_falls_back_when_position_unknown(self) -> None:
        """hold_position + current_cover_position=None → fall through to default (safe fallback)."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_timeout_mode="hold_position",
            in_time_window=True,
            direct_sun_valid=True,
            current_cover_position=None,
            default_position=10,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.skip_command is False
        assert result.position == 10

    def test_return_to_default_mode_unchanged(self) -> None:
        """return_to_default mode is completely unchanged (regression guard)."""
        snap = make_snapshot(
            motion_timeout_active=True,
            motion_timeout_mode="return_to_default",
            in_time_window=True,
            direct_sun_valid=True,
            current_cover_position=42,
            default_position=20,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.skip_command is False
        assert result.position == 20
        assert result.control_method == ControlMethod.MOTION


# ---------------------------------------------------------------------------
# ManualOverrideHandler
# ---------------------------------------------------------------------------


class TestManualOverrideHandler:
    """Tests for ManualOverrideHandler."""

    handler = ManualOverrideHandler()

    def test_returns_none_when_inactive(self) -> None:
        """Return None when manual override not active."""
        snap = make_snapshot(manual_override_active=False)
        assert self.handler.evaluate(snap) is None

    def test_matches_when_active_sun_valid(self) -> None:
        """When manual override active + sun valid, return solar position."""
        snap = make_snapshot(
            manual_override_active=True,
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 60
        assert result.control_method == ControlMethod.MANUAL

    def test_matches_when_active_sun_invalid(self) -> None:
        """When manual override active + sun not valid, return default."""
        snap = make_snapshot(
            manual_override_active=True,
            direct_sun_valid=False,
            default_position=int(25.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.MANUAL

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip mentions 'manual' when skipped."""
        snap = make_snapshot(manual_override_active=False)
        reason = self.handler.describe_skip(snap)
        assert "manual" in reason.lower()

    def test_priority_is_80(self) -> None:
        """ManualOverrideHandler has priority 80."""
        assert ManualOverrideHandler.priority == 80

    def test_name(self) -> None:
        """ManualOverrideHandler name is 'manual_override'."""
        assert ManualOverrideHandler.name == "manual_override"


# ---------------------------------------------------------------------------
# ClimateHandler
# ---------------------------------------------------------------------------


class TestClimateHandler:
    """Tests for ClimateHandler — basic gating."""

    handler = ClimateHandler()

    def test_returns_none_when_climate_disabled(self) -> None:
        """Climate disabled → return None."""
        snap = make_snapshot(climate_mode_enabled=False)
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_readings(self) -> None:
        """No climate readings → return None."""
        snap = make_snapshot(climate_mode_enabled=True, climate_readings=None)
        assert self.handler.evaluate(snap) is None

    def test_priority_is_50(self) -> None:
        """ClimateHandler has priority 50."""
        assert ClimateHandler.priority == 50

    def test_name(self) -> None:
        """ClimateHandler name is 'climate'."""
        assert ClimateHandler.name == "climate"


# ---------------------------------------------------------------------------
# SolarHandler
# ---------------------------------------------------------------------------


class TestSolarHandler:
    """Tests for SolarHandler."""

    handler = SolarHandler()

    def test_matches_when_sun_valid(self) -> None:
        """Sun valid → return SOLAR method."""
        snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=60.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SOLAR

    def test_returns_none_when_sun_invalid(self) -> None:
        """Sun invalid → return None."""
        snap = make_snapshot(direct_sun_valid=False)
        assert self.handler.evaluate(snap) is None

    def test_priority_is_40(self) -> None:
        """SolarHandler has priority 40."""
        assert SolarHandler.priority == 40

    def test_name(self) -> None:
        """SolarHandler name is 'solar'."""
        assert SolarHandler.name == "solar"


# ---------------------------------------------------------------------------
# DefaultHandler
# ---------------------------------------------------------------------------


class TestDefaultHandler:
    """Tests for DefaultHandler."""

    handler = DefaultHandler()

    def test_always_matches(self) -> None:
        """DefaultHandler must return a result for any snapshot."""
        snap = make_snapshot()
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_returns_default_position(self) -> None:
        """Return the default_position with DEFAULT method."""
        snap = make_snapshot(default_position=42)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 42
        assert result.control_method == ControlMethod.DEFAULT

    def test_zero_default_position(self) -> None:
        """Handle default_position=0 correctly (falsy value check)."""
        snap = make_snapshot(default_position=0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 0

    def test_priority_is_0(self) -> None:
        """DefaultHandler has priority 0 (lowest)."""
        assert DefaultHandler.priority == 0

    def test_name(self) -> None:
        """DefaultHandler name is 'default'."""
        assert DefaultHandler.name == "default"

    def test_describe_skip_returns_string(self) -> None:
        """describe_skip returns meaningful string."""
        snap = make_snapshot()
        reason = self.handler.describe_skip(snap)
        assert isinstance(reason, str)
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# Handler result structure
# (Parametrized integration tests removed — will be replaced in Task 16)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# contribute() default contract
# ---------------------------------------------------------------------------


class TestOverrideHandlerContributeDefault:
    """Every OverrideHandler exposes contribute(); default returns {}."""

    def test_default_contribute_is_empty_dict(self) -> None:
        """OverrideHandler.contribute() default returns {} — handlers opt in by overriding."""
        from custom_components.adaptive_cover_pro.pipeline.handler import (
            OverrideHandler,
        )

        class _Dummy(OverrideHandler):
            name = "dummy"
            priority = 0

            def evaluate(self, snapshot):
                return None

        snap = make_snapshot()
        assert _Dummy().contribute(snap) == {}

    def test_non_climate_handlers_return_empty_by_default(self) -> None:
        """Unmodified handlers return {} from contribute() — no accidental merges."""
        snap = make_snapshot()
        for handler in [
            ForceOverrideHandler(),
            ManualOverrideHandler(),
            MotionTimeoutHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]:
            assert (
                handler.contribute(snap) == {}
            ), f"{handler.__class__.__name__}.contribute() should return {{}}"
