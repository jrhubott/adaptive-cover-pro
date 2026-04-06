"""Integration tests for inverse state through the coordinator state property.

Tests the full chain: pipeline position → coordinator.state property
→ inverse_state / interpolation applied → correct final position.

Covers:
- Step 15: Solar position inverted (30% → 70%) when inverse_state=True
- Step 16: Force override bypasses inverse state (safety handlers are exempt)
- Step 17: Open/close-only cover: threshold checked after inversion
- Step 18: Interpolation + inverse_state → inverse NOT applied (conflict guard)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
    inverse_state,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(
    *,
    position: int,
    control_method: ControlMethod = ControlMethod.SOLAR,
    bypass_auto_control: bool = False,
) -> PipelineResult:
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass_auto_control,
    )


def _make_coordinator(
    *,
    pipeline_result: PipelineResult,
    inverse_state_enabled: bool = False,
    use_interpolation: bool = False,
    start_value=None,
    end_value=None,
    normal_list=None,
    new_list=None,
) -> MagicMock:
    """Build a minimal mock coordinator with the attributes used by `state` property."""
    coordinator = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coordinator._pipeline_result = pipeline_result
    coordinator._pipeline_bypasses_auto_control = pipeline_result.bypass_auto_control
    coordinator._use_interpolation = use_interpolation
    coordinator._inverse_state = inverse_state_enabled
    coordinator.start_value = start_value
    coordinator.end_value = end_value
    coordinator.normal_list = normal_list
    coordinator.new_list = new_list
    coordinator.logger = MagicMock()
    return coordinator


# ---------------------------------------------------------------------------
# Step 15: Solar position inverted before command
# ---------------------------------------------------------------------------


class TestSolarPositionInverted:
    """When inverse_state=True, the pipeline position is inverted (100 - pos)."""

    def test_solar_30_becomes_70(self):
        """Pipeline returns 30% → coordinator.state returns 70% with inverse_state=True."""
        result = _make_pipeline_result(position=30, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 70

    def test_solar_70_becomes_30(self):
        """Pipeline returns 70% → coordinator.state returns 30% with inverse_state=True."""
        result = _make_pipeline_result(position=70, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 30

    def test_solar_50_stays_50(self):
        """Pipeline returns 50% → coordinator.state returns 50% (midpoint unchanged)."""
        result = _make_pipeline_result(position=50, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 50

    def test_no_inversion_without_inverse_state_flag(self):
        """Without inverse_state=True, the pipeline position is returned as-is."""
        result = _make_pipeline_result(position=30, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(
            pipeline_result=result, inverse_state_enabled=False
        )

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 30

    def test_default_position_also_inverted(self):
        """DEFAULT handler position is also inverted when inverse_state=True."""
        result = _make_pipeline_result(
            position=20, control_method=ControlMethod.DEFAULT
        )
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 80

    def test_inverse_state_function_formula(self):
        """inverse_state() = 100 - position for all valid positions."""
        for pos in [0, 10, 25, 40, 50, 60, 75, 90, 100]:
            assert inverse_state(pos) == 100 - pos


# ---------------------------------------------------------------------------
# Step 16: Force override bypasses inverse state
# ---------------------------------------------------------------------------


class TestForceOverrideBypassesInverseState:
    """Safety handlers (force/weather) bypass inverse state via bypass_auto_control."""

    def test_force_override_position_not_inverted(self):
        """ForceOverrideHandler result is returned as-is even with inverse_state=True.

        Safety handlers set bypass_auto_control=True, causing coordinator.state
        to return the pipeline position directly without post-processing transforms.
        """
        result = _make_pipeline_result(
            position=75,
            control_method=ControlMethod.FORCE,
            bypass_auto_control=True,
        )
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # 75% NOT inverted to 25% — safety bypasses all transforms
        assert state == 75

    def test_weather_override_position_not_inverted(self):
        """WeatherOverrideHandler result is also returned as-is."""
        result = _make_pipeline_result(
            position=0,
            control_method=ControlMethod.WEATHER,
            bypass_auto_control=True,
        )
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # 0% NOT inverted to 100% — safety bypasses transforms
        assert state == 0

    def test_force_zero_percent_stays_zero(self):
        """Force override to 0% stays 0% (not inverted to 100%)."""
        result = _make_pipeline_result(
            position=0,
            control_method=ControlMethod.FORCE,
            bypass_auto_control=True,
        )
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 0

    def test_non_safety_manual_position_IS_inverted(self):
        """ManualOverrideHandler does NOT set bypass_auto_control, so it IS inverted."""
        result = _make_pipeline_result(
            position=30,
            control_method=ControlMethod.MANUAL,
            bypass_auto_control=False,  # ← not a safety handler
        )
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # Manual override position IS inverted (30 → 70)
        assert state == 70


# ---------------------------------------------------------------------------
# Step 17: Open/close-only covers with inverse state
# ---------------------------------------------------------------------------


class TestOpenCloseOnlyCoverWithInverseState:
    """For open/close-only covers, the threshold is checked AFTER inversion.

    The correct order is: calculate → invert → compare to threshold.
    This is enforced by the coordinator passing inverse_state into PositionContext
    and CoverCommandService._prepare_service_call applying it.

    These tests verify the coordinator correctly exposes the inverted state.
    The threshold application itself is tested in test_inverse_state.py.
    """

    def test_position_below_50_inverted_above_50_would_open(self):
        """Position 30% inverted to 70% — above threshold 50% → cover opens.

        This simulates: sun says 30% (mostly closed), but inverse_state=True
        means the COVER convention is reversed. Inverted = 70%, which is above
        the 50% open/close threshold → the physical cover opens.
        """
        result = _make_pipeline_result(position=30, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        # coordinator.state shows the inverted value that will be used
        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 70  # Inverted; CoverCommandService compares 70 > 50 → open

    def test_position_above_50_inverted_below_50_would_close(self):
        """Position 80% inverted to 20% — below threshold 50% → cover closes."""
        result = _make_pipeline_result(position=80, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(pipeline_result=result, inverse_state_enabled=True)

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        assert state == 20  # Inverted; CoverCommandService compares 20 < 50 → close


# ---------------------------------------------------------------------------
# Step 18: Interpolation + inverse state — inverse NOT applied
# ---------------------------------------------------------------------------


class TestInterpolationAndInverseStateConflict:
    """When both interpolation and inverse_state are configured, inverse_state is
    skipped and a warning is logged.  Interpolation takes precedence."""

    def test_interpolation_takes_precedence_over_inverse_state(self):
        """With both enabled, interpolation runs but inverse state does NOT apply."""
        # Pipeline returns 50%, interpolation remaps 0-100 → 20-80
        # So interpolated position = 50 maps to 50 (midpoint stays midpoint)
        result = _make_pipeline_result(position=50, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(
            pipeline_result=result,
            inverse_state_enabled=True,
            use_interpolation=True,
            start_value=20,
            end_value=80,
        )

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # Interpolated: interp(50, [0,100], [20,80]) = 50
        # Inverse NOT applied (interpolation takes precedence)
        assert state == 50  # Not 50 (inverse of 50 is still 50, but test the non-midpoint case)

    def test_interpolation_remaps_without_inversion(self):
        """Pipeline 0% → interpolation maps to 20%, inverse NOT applied (would give 80%)."""
        result = _make_pipeline_result(position=0, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(
            pipeline_result=result,
            inverse_state_enabled=True,
            use_interpolation=True,
            start_value=20,
            end_value=80,
        )

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # Interpolated: interp(0, [0,100], [20,80]) = 20
        # Inverse NOT applied (20 → 80 would be wrong)
        assert state == 20  # Interpolated, NOT inverted

    def test_inverse_applied_without_interpolation(self):
        """Without interpolation, inverse_state IS applied."""
        result = _make_pipeline_result(position=0, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(
            pipeline_result=result,
            inverse_state_enabled=True,
            use_interpolation=False,  # ← no interpolation
        )

        state = AdaptiveDataUpdateCoordinator.state.fget(coord)

        # No interpolation → inverse state applies: 0 → 100
        assert state == 100

    def test_conflict_warning_logged(self):
        """A warning is logged when both inverse_state and interpolation are enabled."""
        result = _make_pipeline_result(position=30, control_method=ControlMethod.SOLAR)
        coord = _make_coordinator(
            pipeline_result=result,
            inverse_state_enabled=True,
            use_interpolation=True,
            start_value=0,
            end_value=100,
        )

        AdaptiveDataUpdateCoordinator.state.fget(coord)

        # Warning must be logged about the conflict
        coord.logger.info.assert_called()
        logged = [call[0][0] for call in coord.logger.info.call_args_list]
        assert any("inverse" in msg.lower() and "interpolation" in msg.lower() for msg in logged)
