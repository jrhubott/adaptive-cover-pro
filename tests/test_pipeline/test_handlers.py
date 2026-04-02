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

from tests.test_pipeline.conftest import make_snapshot


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


# ---------------------------------------------------------------------------
# MotionTimeoutHandler
# ---------------------------------------------------------------------------


class TestMotionTimeoutHandler:
    """Tests for MotionTimeoutHandler."""

    handler = MotionTimeoutHandler()

    def test_matches_when_active(self) -> None:
        """Return MOTION method when motion timeout is active."""
        snap = make_snapshot(motion_timeout_active=True, cover_default=20.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.MOTION

    def test_returns_none_when_inactive(self) -> None:
        """Return None when motion timeout is not active."""
        snap = make_snapshot(motion_timeout_active=False)
        assert self.handler.evaluate(snap) is None

    def test_uses_cover_default_position(self) -> None:
        """Return position based on cover.default when timeout active."""
        snap = make_snapshot(motion_timeout_active=True, cover_default=33.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 33

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip mentions 'motion' when skipped."""
        snap = make_snapshot(motion_timeout_active=False)
        reason = self.handler.describe_skip(snap)
        assert "motion" in reason.lower()

    def test_priority_is_80(self) -> None:
        """MotionTimeoutHandler has priority 80."""
        assert MotionTimeoutHandler.priority == 80

    def test_name(self) -> None:
        """MotionTimeoutHandler name is 'motion_timeout'."""
        assert MotionTimeoutHandler.name == "motion_timeout"


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
            cover_default=25.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.MANUAL

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip mentions 'manual' when skipped."""
        snap = make_snapshot(manual_override_active=False)
        reason = self.handler.describe_skip(snap)
        assert "manual" in reason.lower()

    def test_priority_is_70(self) -> None:
        """ManualOverrideHandler has priority 70."""
        assert ManualOverrideHandler.priority == 70

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
        snap = make_snapshot(cover_default=42)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 42
        assert result.control_method == ControlMethod.DEFAULT

    def test_zero_default_position(self) -> None:
        """Handle default_position=0 correctly (falsy value check)."""
        snap = make_snapshot(cover_default=0)
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
