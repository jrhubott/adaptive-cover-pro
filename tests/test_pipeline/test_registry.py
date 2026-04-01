"""Tests for the pipeline registry."""

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
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry

from tests.test_pipeline.conftest import make_ctx

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


# ---------------------------------------------------------------------------
# Registry infrastructure tests
# ---------------------------------------------------------------------------


def test_empty_registry_raises() -> None:
    """RuntimeError is raised when no handlers are registered."""
    registry = PipelineRegistry([])
    with pytest.raises(RuntimeError):
        registry.evaluate(make_ctx())


def test_single_handler_always_matches() -> None:
    """DefaultHandler alone produces a valid result."""
    registry = PipelineRegistry([DefaultHandler()])
    ctx = make_ctx(default_position=25)
    result = registry.evaluate(ctx)
    assert result.position == 25
    assert result.control_method == ControlMethod.DEFAULT


def test_priority_ordering() -> None:
    """Higher-priority handler wins when both match."""
    # Both ForceOverride (100) and Default (0) match; force should win.
    registry = PipelineRegistry([DefaultHandler(), ForceOverrideHandler()])
    ctx = make_ctx(force_override_active=True, force_override_position=10)
    result = registry.evaluate(ctx)
    assert result.position == 10
    assert result.control_method == ControlMethod.FORCE


def test_handlers_sorted_by_priority_descending() -> None:
    """Registry sorts handlers internally so insertion order doesn't matter."""
    # Provide handlers in reverse priority order.
    registry = PipelineRegistry(
        [DefaultHandler(), SolarHandler(), ForceOverrideHandler()]
    )
    ctx = make_ctx(force_override_active=True, force_override_position=5)
    result = registry.evaluate(ctx)
    assert result.control_method == ControlMethod.FORCE


def test_decision_trace_records_all() -> None:
    """Trace includes the winning handler plus all skipped handlers."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        force_override_active=True,
        force_override_position=15,
    )
    result = registry.evaluate(ctx)
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
    ctx = make_ctx(direct_sun_valid=False, default_position=30)
    result = registry.evaluate(ctx)
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
    ctx = make_ctx(
        calculated_position=60,
        direct_sun_valid=True,
        climate_mode_enabled=True,
        climate_position=80,
        climate_is_summer=True,
        manual_override_active=True,
        motion_timeout_active=True,
        force_override_active=True,
        force_override_position=0,
    )
    result = registry.evaluate(ctx)
    assert result.position == 0
    assert result.control_method == ControlMethod.FORCE


def test_full_pipeline_motion_timeout_beats_manual() -> None:
    """Motion timeout (priority 80) beats manual override (priority 70)."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        calculated_position=50,
        default_position=20,
        motion_timeout_active=True,
        manual_override_active=True,
    )
    result = registry.evaluate(ctx)
    assert result.position == 20
    assert result.control_method == ControlMethod.MOTION


def test_full_pipeline_climate_summer() -> None:
    """Climate summer wins over solar when both are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        calculated_position=50,
        climate_mode_enabled=True,
        climate_position=100,
        climate_is_summer=True,
        direct_sun_valid=True,
    )
    result = registry.evaluate(ctx)
    assert result.position == 100
    assert result.control_method == ControlMethod.SUMMER


def test_full_pipeline_climate_winter() -> None:
    """Climate winter wins over solar when both are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        calculated_position=50,
        climate_mode_enabled=True,
        climate_position=0,
        climate_is_winter=True,
        direct_sun_valid=True,
    )
    result = registry.evaluate(ctx)
    assert result.position == 0
    assert result.control_method == ControlMethod.WINTER


def test_full_pipeline_solar_default() -> None:
    """Solar wins when sun is in FOV and no overrides are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        calculated_position=65,
        direct_sun_valid=True,
    )
    result = registry.evaluate(ctx)
    assert result.position == 65
    assert result.control_method == ControlMethod.SOLAR


def test_full_pipeline_default_fallback() -> None:
    """Default wins when sun is not in FOV and no overrides are active."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(
        calculated_position=65,
        default_position=10,
        direct_sun_valid=False,
    )
    result = registry.evaluate(ctx)
    assert result.position == 10
    assert result.control_method == ControlMethod.DEFAULT


def test_result_carries_full_trace_through_registry() -> None:
    """The PipelineResult returned by registry has the complete trace attached."""
    registry = PipelineRegistry(ALL_HANDLERS)
    ctx = make_ctx(direct_sun_valid=True, calculated_position=55)
    result = registry.evaluate(ctx)
    # 6 handlers registered — trace must have 6 entries.
    assert len(result.decision_trace) == 6
    # Solar matched — the rest are skipped.
    winning = [s for s in result.decision_trace if s.matched]
    skipped = [s for s in result.decision_trace if not s.matched]
    assert len(winning) == 1
    assert winning[0].handler == "solar"
    assert len(skipped) == 5
