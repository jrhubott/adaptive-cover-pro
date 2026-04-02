"""Tests for the pipeline registry."""

from __future__ import annotations

from unittest.mock import MagicMock

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
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_HANDLERS = [
    ForceOverrideHandler(),
    MotionTimeoutHandler(),
    ManualOverrideHandler(),
    ClimateHandler(),
    SolarHandler(),
    DefaultHandler(),
]


def _make_climate_cover(
    *, direct_sun_valid: bool = True, calculate_percentage_return: float = 50.0
) -> MagicMock:
    """Build a mock cover suitable for ClimateHandler (needs .valid and .logger)."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.default = 0.0
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


def _summer_readings() -> ClimateReadings:
    """ClimateReadings that trigger summer mode (inside temp > temp_high)."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=30.0,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _winter_readings() -> ClimateReadings:
    """ClimateReadings that trigger winter mode (inside temp < temp_low)."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=10.0,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _climate_options_summer() -> ClimateOptions:
    """ClimateOptions with thresholds that make 30°C trigger summer."""
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=False,
        winter_close_insulation=False,
    )


def _climate_options_winter() -> ClimateOptions:
    """ClimateOptions with thresholds that make 10°C trigger winter."""
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=False,
        winter_close_insulation=False,
    )


# ---------------------------------------------------------------------------
# Registry infrastructure tests
# ---------------------------------------------------------------------------


def test_empty_registry_raises() -> None:
    """RuntimeError is raised when no handlers are registered."""
    registry = PipelineRegistry([])
    with pytest.raises(RuntimeError):
        registry.evaluate(make_snapshot())


def test_single_handler_always_matches() -> None:
    """DefaultHandler alone produces a valid result."""
    registry = PipelineRegistry([DefaultHandler()])
    snap = make_snapshot(cover_default=25.0)
    result = registry.evaluate(snap)
    assert result.position == 25
    assert result.control_method == ControlMethod.DEFAULT


def test_priority_ordering() -> None:
    """Higher-priority handler wins when both match."""
    # Both ForceOverride (100) and Default (0) match; force should win.
    registry = PipelineRegistry([DefaultHandler(), ForceOverrideHandler()])
    snap = make_snapshot(
        force_override_sensors={"binary_sensor.s": True},
        force_override_position=10,
    )
    result = registry.evaluate(snap)
    assert result.position == 10
    assert result.control_method == ControlMethod.FORCE


def test_handlers_sorted_by_priority_descending() -> None:
    """Registry sorts handlers internally so insertion order doesn't matter."""
    # Provide handlers in reverse priority order.
    registry = PipelineRegistry(
        [DefaultHandler(), SolarHandler(), ForceOverrideHandler()]
    )
    snap = make_snapshot(
        force_override_sensors={"binary_sensor.s": True},
        force_override_position=5,
    )
    result = registry.evaluate(snap)
    assert result.control_method == ControlMethod.FORCE


def test_decision_trace_records_all() -> None:
    """Trace includes the winning handler plus all skipped handlers."""
    registry = PipelineRegistry(ALL_HANDLERS)
    snap = make_snapshot(
        force_override_sensors={"binary_sensor.s": True},
        force_override_position=15,
    )
    result = registry.evaluate(snap)
    # All 6 handlers should appear in the trace.
    assert len(result.decision_trace) == 6
    # First step is the winner.
    assert result.decision_trace[0].handler == "force_override"
    assert result.decision_trace[0].matched is True
    # All subsequent steps should be skipped (not matched).
    for step in result.decision_trace[1:]:
        assert step.matched is False
        assert step.reason == "skipped (higher priority matched)"


def test_decision_trace_non_matching_handlers_record_skip_reason() -> None:
    """Non-matching handlers record their describe_skip() reason, not 'skipped'."""
    # Only DefaultHandler + SolarHandler; sun not valid → default wins.
    registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
    snap = make_snapshot(direct_sun_valid=False, default_position=30)
    result = registry.evaluate(snap)
    assert len(result.decision_trace) == 2
    # SolarHandler doesn't match — reason comes from describe_skip().
    assert result.decision_trace[0].handler == "solar"
    assert result.decision_trace[0].matched is False
    assert "not" in result.decision_trace[0].reason.lower()
    # Default matches.
    assert result.decision_trace[1].handler == "default"
    assert result.decision_trace[1].matched is True


# ---------------------------------------------------------------------------
# Full pipeline scenario tests (all 6 handlers registered)
# ---------------------------------------------------------------------------


def test_full_pipeline_force_wins() -> None:
    """Force override beats all other conditions."""
    registry = PipelineRegistry(ALL_HANDLERS)
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=60.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_climate_options_summer(),
        manual_override_active=True,
        motion_timeout_active=True,
        force_override_sensors={"binary_sensor.s": True},
        force_override_position=0,
    )
    result = registry.evaluate(snap)
    assert result.position == 0
    assert result.control_method == ControlMethod.FORCE


def test_full_pipeline_motion_timeout_beats_manual() -> None:
    """Motion timeout (priority 80) beats manual override (priority 70)."""
    registry = PipelineRegistry(ALL_HANDLERS)
    snap = make_snapshot(
        calculate_percentage_return=50.0,
        cover_default=20.0,
        motion_timeout_active=True,
        manual_override_active=True,
    )
    result = registry.evaluate(snap)
    assert result.position == 20
    assert result.control_method == ControlMethod.MOTION


def test_full_pipeline_climate_summer() -> None:
    """Climate summer wins over solar when both are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=50.0)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_climate_options_summer(),
        direct_sun_valid=True,
    )
    result = registry.evaluate(snap)
    assert result.control_method == ControlMethod.SUMMER


def test_full_pipeline_climate_winter() -> None:
    """Climate winter wins over solar when both are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=50.0)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_winter_readings(),
        climate_options=_climate_options_winter(),
        direct_sun_valid=True,
    )
    result = registry.evaluate(snap)
    assert result.control_method == ControlMethod.WINTER


def test_full_pipeline_solar_default() -> None:
    """Solar wins when sun is in FOV and no overrides are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    snap = make_snapshot(
        calculate_percentage_return=65.0,
        direct_sun_valid=True,
    )
    result = registry.evaluate(snap)
    assert result.position == 65
    assert result.control_method == ControlMethod.SOLAR


def test_full_pipeline_default_fallback() -> None:
    """Default wins when sun is not in FOV and no overrides are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    snap = make_snapshot(
        calculate_percentage_return=65.0,
        cover_default=10.0,
        direct_sun_valid=False,
    )
    result = registry.evaluate(snap)
    assert result.position == 10
    assert result.control_method == ControlMethod.DEFAULT


def test_result_carries_full_trace_through_registry() -> None:
    """The PipelineResult returned by registry has the complete trace attached."""
    registry = PipelineRegistry(ALL_HANDLERS)
    snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=55.0)
    result = registry.evaluate(snap)
    # 6 handlers registered — trace must have 6 entries.
    assert len(result.decision_trace) == 6
    # Solar matched — the rest are skipped.
    winning = [s for s in result.decision_trace if s.matched]
    skipped = [s for s in result.decision_trace if not s.matched]
    assert len(winning) == 1
    assert winning[0].handler == "solar"
    assert len(skipped) == 5
