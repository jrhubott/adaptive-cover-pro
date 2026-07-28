"""Tests for individual override handlers."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
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
        from custom_components.adaptive_cover_pro.const import ControlMethod

        r = PipelineResult(
            position=42, control_method=ControlMethod.DEFAULT, reason="x"
        )
        assert r.skip_command is False

    def test_skip_command_can_be_set_true(self) -> None:
        """skip_command=True can be constructed for hold-mode results."""
        from custom_components.adaptive_cover_pro.const import ControlMethod

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
# Safety custom-position slot — the migrated force override (issue #563)
# ---------------------------------------------------------------------------


def _safety_handler(position: int = 90) -> CustomPositionHandler:
    """Priority-100 custom-position handler (slot 5 by convention)."""
    return CustomPositionHandler(
        slot=5, position=position, priority=CUSTOM_POSITION_SAFETY_PRIORITY
    )


def _safety_state(
    sensors: dict[str, bool],
    position: int = 90,
    *,
    min_mode: bool = False,
) -> CustomPositionSensorState:
    """Build the slot-5 sensor state from an {entity_id: is_on} map (OR logic)."""
    active = tuple(eid for eid, on in sensors.items() if on)
    return CustomPositionSensorState(
        entity_ids=tuple(sensors),
        is_on=bool(active),
        position=position,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        min_mode=min_mode,
        use_my=False,
        slot=5,
        active_entity_ids=active,
    )


class TestSafetyCustomPositionHandler:
    """Behavioral parity with the deleted ForceOverrideHandler."""

    handler = _safety_handler(position=90)

    def test_returns_none_when_no_slot_configured(self) -> None:
        """Return None when the safety slot is not in the snapshot."""
        snap = make_snapshot(custom_position_sensors=[])
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_all_sensors_off(self) -> None:
        """Return None when all bound sensors are off."""
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state(
                    {"binary_sensor.wind": False, "binary_sensor.rain": False}
                )
            ]
        )
        assert self.handler.evaluate(snap) is None

    def test_matches_when_any_sensor_on(self) -> None:
        """Multi-sensor OR: any sensor on activates the slot."""
        handler = _safety_handler(position=5)
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state(
                    {"binary_sensor.wind": False, "binary_sensor.rain": True},
                    position=5,
                )
            ]
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert result.position == 5
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.is_safety is True
        assert result.bypass_auto_control is True

    def test_matches_when_single_sensor_on(self) -> None:
        """A single active sensor activates the slot."""
        handler = _safety_handler(position=75)
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.alert": True}, position=75)
            ]
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert result.position == 75

    def test_reason_mentions_active_sensors(self) -> None:
        """Reason string lists the active sensors (force-override parity)."""
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.wind": False, "binary_sensor.rain": True})
            ]
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "binary_sensor.rain" in result.reason
        assert "binary_sensor.wind" not in result.reason

    def test_describe_skip_mentions_slot(self) -> None:
        """describe_skip names the slot when skipped."""
        snap = make_snapshot(custom_position_sensors=[])
        reason = self.handler.describe_skip(snap)
        assert "#5" in reason
        assert "not active" in reason

    def test_priority_is_100(self) -> None:
        """Safety slot has priority 100 (highest)."""
        assert self.handler.priority == CUSTOM_POSITION_SAFETY_PRIORITY == 100

    def test_name(self) -> None:
        """Slot-5 handler name is 'custom_position_5'."""
        assert self.handler.name == "custom_position_5"


class TestSafetyCustomPositionHandlerMinMode:
    """The safety slot defers in min_mode; the registry composes the floor.

    See ``tests/test_pipeline/test_floor_composition.py`` for the end-to-end
    floor-clamp composition tests.
    """

    handler = _safety_handler(position=30)

    def test_min_mode_off_uses_exact_position(self) -> None:
        """With min_mode off, position is always the configured value (default behavior)."""
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.s": True}, position=30)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_defers(self) -> None:
        """With min_mode on, evaluate() returns None — the registry composes
        the floor as a post-decision clamp.
        """
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.s": True}, position=30, min_mode=True)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is None


# ---------------------------------------------------------------------------
# Min-mode with sun tracking disabled — regression tests for issue #264
# ---------------------------------------------------------------------------


class TestSafetyCustomPositionHandlerMinModeWithSunTrackingOff:
    """The safety slot defers in min_mode regardless of sun-tracking toggle.

    Issue #264 (floor measured against default rather than solar when tracking
    off) is now handled by the registry's floor-composition pass, which clamps
    whichever lower-priority handler actually wins — including the default
    fallback when tracking is off.
    """

    handler = _safety_handler(position=80)

    def test_min_mode_defers_when_tracking_off(self) -> None:
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.s": True}, position=80, min_mode=True)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None

    def test_min_mode_defers_when_tracking_on(self) -> None:
        snap = make_snapshot(
            custom_position_sensors=[
                _safety_state({"binary_sensor.s": True}, position=80, min_mode=True)
            ],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=True,
        )
        result = self.handler.evaluate(snap)
        assert result is None


class TestWeatherOverrideHandlerMinModeWithSunTrackingOff:
    """WeatherOverrideHandler defers in min_mode regardless of sun-tracking toggle."""

    handler = WeatherOverrideHandler()

    def test_min_mode_defers_when_tracking_off(self) -> None:
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
        assert result is None

    def test_min_mode_defers_when_tracking_on(self) -> None:
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
        assert result is None


class TestCustomPositionHandlerMinModeWithSunTrackingOff:
    """CustomPositionHandler defers in min_mode regardless of sun-tracking toggle."""

    def _make_handler(self) -> CustomPositionHandler:
        return CustomPositionHandler(
            slot=1,
            position=80,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        )

    @staticmethod
    def _min_mode_state() -> CustomPositionSensorState:
        return CustomPositionSensorState(
            entity_ids=("binary_sensor.cp1",),
            is_on=True,
            position=80,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=True,
            use_my=False,
            slot=1,
            active_entity_ids=("binary_sensor.cp1",),
        )

    def test_min_mode_defers_when_tracking_off(self) -> None:
        handler = self._make_handler()
        snap = make_snapshot(
            custom_position_sensors=[self._min_mode_state()],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=False,
        )
        result = handler.evaluate(snap)
        assert result is None

    def test_min_mode_defers_when_tracking_on(self) -> None:
        handler = self._make_handler()
        snap = make_snapshot(
            custom_position_sensors=[self._min_mode_state()],
            direct_sun_valid=True,
            calculate_percentage_return=29.0,
            default_position=100,
            enable_sun_tracking=True,
        )
        result = handler.evaluate(snap)
        assert result is None


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

    def test_tilt_none_when_not_configured(self) -> None:
        """result.tilt is None when neither default_tilt nor sunset_tilt is set."""
        snap = make_snapshot()
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt is None

    def test_default_tilt_stamped(self) -> None:
        """default_tilt=25 stamps result.tilt=25 on the non-sunset path."""
        snap = make_snapshot(default_tilt=25)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 25

    def test_sunset_tilt_stamped_when_sunset_active(self) -> None:
        """sunset_tilt=80 stamps result.tilt=80 when is_sunset_active=True."""
        snap = make_snapshot(is_sunset_active=True, sunset_tilt=80)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 80

    def test_sunset_tilt_falls_back_to_default_tilt_when_none(self) -> None:
        """When sunset_tilt is None and sunset active, falls back to default_tilt."""
        snap = make_snapshot(is_sunset_active=True, default_tilt=40)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 40

    def test_default_tilt_not_used_when_sunset_tilt_set(self) -> None:
        """sunset_tilt takes precedence over default_tilt when sunset is active."""
        snap = make_snapshot(is_sunset_active=True, sunset_tilt=90, default_tilt=20)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 90

    def test_sunset_tilt_ignored_when_not_sunset(self) -> None:
        """sunset_tilt is not used when is_sunset_active=False; default_tilt applies."""
        snap = make_snapshot(is_sunset_active=False, sunset_tilt=80, default_tilt=20)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 20

    # -- default_tilt clamp against min_tilt/max_tilt (issue #503) ----------

    def test_default_tilt_clamped_by_max_tilt(self) -> None:
        """default_tilt above max_tilt is clamped down to max_tilt (non-sunset)."""
        snap = make_snapshot(default_tilt=80, max_tilt=60)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 60

    def test_default_tilt_clamped_by_min_tilt(self) -> None:
        """default_tilt below min_tilt is raised up to min_tilt (non-sunset)."""
        snap = make_snapshot(default_tilt=5, min_tilt=20)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 20

    def test_default_tilt_unchanged_when_within_limits(self) -> None:
        """default_tilt within [min_tilt, max_tilt] is stamped verbatim."""
        snap = make_snapshot(default_tilt=50, min_tilt=10, max_tilt=90)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 50

    def test_default_tilt_not_clamped_when_max_sun_only(self) -> None:
        """max_tilt_sun_only=True: max cap does not apply on the sun-invalid default path."""
        snap = make_snapshot(default_tilt=80, max_tilt=60, max_tilt_sun_only=True)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 80

    def test_default_tilt_not_clamped_when_min_sun_only(self) -> None:
        """min_tilt_sun_only=True: min floor does not apply on the sun-invalid default path."""
        snap = make_snapshot(default_tilt=5, min_tilt=20, min_tilt_sun_only=True)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 5

    def test_sunset_tilt_not_clamped(self) -> None:
        """sunset_tilt is a deliberate carve-out — never clamped (issue #128)."""
        snap = make_snapshot(is_sunset_active=True, sunset_tilt=80, max_tilt=60)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 80

    def test_sunset_default_tilt_fallback_not_clamped(self) -> None:
        """When sunset active with no sunset_tilt, the default_tilt fallback stays unclamped."""
        snap = make_snapshot(is_sunset_active=True, default_tilt=80, max_tilt=60)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.tilt == 80


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
            _safety_handler(),
            WeatherOverrideHandler(),
            ManualOverrideHandler(),
            MotionTimeoutHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]:
            assert (
                handler.contribute(snap) == {}
            ), f"{handler.__class__.__name__}.contribute() should return {{}}"
