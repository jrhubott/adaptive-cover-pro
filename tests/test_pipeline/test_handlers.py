"""Tests for individual override handlers."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    ForceOverrideHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
)

from tests.test_pipeline.conftest import make_ctx, make_snapshot


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

    def test_matches_when_active(self) -> None:
        """Return result with FORCE method when force override is active."""
        ctx = make_ctx(force_override_active=True, force_override_position=5)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 5
        assert result.control_method == ControlMethod.FORCE

    def test_returns_none_when_inactive(self) -> None:
        """Return None when force override is not active."""
        ctx = make_ctx(force_override_active=False)
        assert self.handler.evaluate(ctx) is None

    def test_uses_force_override_position(self) -> None:
        """Use the force_override_position value from context."""
        ctx = make_ctx(force_override_active=True, force_override_position=75)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 75

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip returns a non-empty string mentioning force."""
        ctx = make_ctx(force_override_active=False)
        reason = self.handler.describe_skip(ctx)
        assert isinstance(reason, str)
        assert len(reason) > 0
        assert "force" in reason.lower()

    def test_priority_is_100(self) -> None:
        """Priority should be 100 (highest)."""
        assert ForceOverrideHandler.priority == 100

    def test_name(self) -> None:
        """Handler name should be 'force_override'."""
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
        snap = make_snapshot(motion_timeout_active=False)
        reason = self.handler.describe_skip(snap)
        assert "motion" in reason.lower()

    def test_priority_is_80(self) -> None:
        assert MotionTimeoutHandler.priority == 80

    def test_name(self) -> None:
        assert MotionTimeoutHandler.name == "motion_timeout"


# ---------------------------------------------------------------------------
# ManualOverrideHandler
# ---------------------------------------------------------------------------


class TestManualOverrideHandler:
    """Tests for ManualOverrideHandler."""

    handler = ManualOverrideHandler()

    def test_returns_none_when_inactive(self) -> None:
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
        snap = make_snapshot(manual_override_active=False)
        reason = self.handler.describe_skip(snap)
        assert "manual" in reason.lower()

    def test_priority_is_70(self) -> None:
        assert ManualOverrideHandler.priority == 70

    def test_name(self) -> None:
        assert ManualOverrideHandler.name == "manual_override"


# ---------------------------------------------------------------------------
# ClimateHandler
# ---------------------------------------------------------------------------


class TestClimateHandler:
    """Tests for ClimateHandler."""

    handler = ClimateHandler()

    def test_returns_none_when_climate_disabled(self) -> None:
        """Return None when climate mode is not enabled."""
        ctx = make_ctx(climate_mode_enabled=False, climate_position=80)
        assert self.handler.evaluate(ctx) is None

    def test_returns_none_when_climate_position_none(self) -> None:
        """Return None when climate mode is enabled but position is unavailable."""
        ctx = make_ctx(climate_mode_enabled=True, climate_position=None)
        assert self.handler.evaluate(ctx) is None

    def test_summer_strategy(self) -> None:
        """Return SUMMER control method when climate_is_summer is True."""
        ctx = make_ctx(
            climate_mode_enabled=True,
            climate_position=100,
            climate_is_summer=True,
            climate_is_winter=False,
        )
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 100
        assert result.control_method == ControlMethod.SUMMER

    def test_winter_strategy(self) -> None:
        """Return WINTER control method when climate_is_winter is True."""
        ctx = make_ctx(
            climate_mode_enabled=True,
            climate_position=0,
            climate_is_summer=False,
            climate_is_winter=True,
        )
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 0
        assert result.control_method == ControlMethod.WINTER

    def test_intermediate_strategy_uses_solar_method(self) -> None:
        """Neither summer nor winter → glare control → SOLAR method."""
        ctx = make_ctx(
            climate_mode_enabled=True,
            climate_position=55,
            climate_is_summer=False,
            climate_is_winter=False,
        )
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 55
        assert result.control_method == ControlMethod.SOLAR

    def test_describe_skip_climate_disabled(self) -> None:
        """describe_skip mentions climate not enabled when mode is off."""
        ctx = make_ctx(climate_mode_enabled=False)
        reason = self.handler.describe_skip(ctx)
        assert "climate" in reason.lower()
        assert "not" in reason.lower()

    def test_describe_skip_climate_position_none(self) -> None:
        """describe_skip returns meaningful string when position is unavailable."""
        ctx = make_ctx(climate_mode_enabled=True, climate_position=None)
        reason = self.handler.describe_skip(ctx)
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_priority_is_50(self) -> None:
        """Priority should be 50."""
        assert ClimateHandler.priority == 50

    def test_name(self) -> None:
        """Handler name should be 'climate'."""
        assert ClimateHandler.name == "climate"

    def test_summer_beats_winter_flag(self) -> None:
        """When both summer and winter flags are set, summer takes precedence."""
        ctx = make_ctx(
            climate_mode_enabled=True,
            climate_position=100,
            climate_is_summer=True,
            climate_is_winter=True,
        )
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER


# ---------------------------------------------------------------------------
# SolarHandler
# ---------------------------------------------------------------------------


class TestSolarHandler:
    """Tests for SolarHandler."""

    handler = SolarHandler()

    def test_matches_when_sun_valid(self) -> None:
        """Return result with SOLAR method when direct sun is valid."""
        ctx = make_ctx(direct_sun_valid=True, calculated_position=60)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 60
        assert result.control_method == ControlMethod.SOLAR

    def test_returns_none_when_sun_invalid(self) -> None:
        """Return None when sun is not in the FOV."""
        ctx = make_ctx(direct_sun_valid=False)
        assert self.handler.evaluate(ctx) is None

    def test_uses_calculated_position(self) -> None:
        """Use the calculated_position value from context."""
        ctx = make_ctx(direct_sun_valid=True, calculated_position=88)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 88

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip mentions sun or FOV when sun is not valid."""
        ctx = make_ctx(direct_sun_valid=False)
        reason = self.handler.describe_skip(ctx)
        assert isinstance(reason, str)
        assert len(reason) > 0
        # Should mention sun or FOV
        assert any(word in reason.lower() for word in ("sun", "fov", "elevation"))

    def test_priority_is_40(self) -> None:
        """Priority should be 40."""
        assert SolarHandler.priority == 40

    def test_name(self) -> None:
        """Handler name should be 'solar'."""
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
        assert DefaultHandler.priority == 0

    def test_name(self) -> None:
        assert DefaultHandler.name == "default"

    def test_describe_skip_returns_string(self) -> None:
        snap = make_snapshot()
        reason = self.handler.describe_skip(snap)
        assert isinstance(reason, str)
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# Handler result structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "ctx_kwargs"),
    [
        (
            ForceOverrideHandler(),
            {"force_override_active": True, "force_override_position": 10},
        ),
        (
            MotionTimeoutHandler(),
            {"motion_timeout_active": True, "default_position": 15},
        ),
        (
            ManualOverrideHandler(),
            {"manual_override_active": True, "calculated_position": 30},
        ),
        (
            ClimateHandler(),
            {
                "climate_mode_enabled": True,
                "climate_position": 50,
                "climate_is_summer": True,
            },
        ),
        (SolarHandler(), {"direct_sun_valid": True, "calculated_position": 70}),
        (DefaultHandler(), {"default_position": 5}),
    ],
)
def test_handler_result_has_non_empty_reason(handler, ctx_kwargs) -> None:
    """Every matching handler must provide a non-empty reason string."""
    ctx = make_ctx(**ctx_kwargs)
    result = handler.evaluate(ctx)
    assert result is not None
    assert isinstance(result.reason, str)
    assert len(result.reason) > 0


@pytest.mark.parametrize(
    ("handler", "ctx_kwargs"),
    [
        (
            ForceOverrideHandler(),
            {"force_override_active": True, "force_override_position": 10},
        ),
        (MotionTimeoutHandler(), {"motion_timeout_active": True}),
        (ManualOverrideHandler(), {"manual_override_active": True}),
        (
            ClimateHandler(),
            {
                "climate_mode_enabled": True,
                "climate_position": 50,
            },
        ),
        (SolarHandler(), {"direct_sun_valid": True}),
        (DefaultHandler(), {}),
    ],
)
def test_handler_result_has_valid_control_method(handler, ctx_kwargs) -> None:
    """Every matching handler must return a valid ControlMethod."""
    ctx = make_ctx(**ctx_kwargs)
    result = handler.evaluate(ctx)
    assert result is not None
    assert isinstance(result.control_method, ControlMethod)
