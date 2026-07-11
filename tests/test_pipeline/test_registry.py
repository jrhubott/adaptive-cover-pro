"""Tests for the pipeline registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    CustomPositionHandler,
    DefaultHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Safety slot (priority 100) — the migrated force override (issue #563).
_SAFETY_SENSOR = "binary_sensor.s"


def _safety_handler(position: int = 0) -> CustomPositionHandler:
    """Slot-5 CustomPositionHandler at safety priority 100."""
    return CustomPositionHandler(
        slot=5, position=position, priority=CUSTOM_POSITION_SAFETY_PRIORITY
    )


def _safety_state(
    position: int = 0, *, is_on: bool = True
) -> CustomPositionSensorState:
    """Slot-5 sensor state matching ``_safety_handler``."""
    return CustomPositionSensorState(
        entity_ids=(_SAFETY_SENSOR,),
        is_on=is_on,
        position=position,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        min_mode=False,
        use_my=False,
        slot=5,
        active_entity_ids=(_SAFETY_SENSOR,) if is_on else (),
    )


def _all_handlers(safety_position: int = 0) -> list:
    """Full handler set with the safety slot at the given target position."""
    return [
        _safety_handler(safety_position),
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
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    config.min_pos_sun_tracking = None
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
        transparent_blind=True,
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
    snap = make_snapshot(default_position=int(25.0))
    result = registry.evaluate(snap)
    assert result.position == 25
    assert result.control_method == ControlMethod.DEFAULT


def test_priority_ordering() -> None:
    """Higher-priority handler wins when both match."""
    # Both the safety slot (100) and Default (0) match; the safety slot wins.
    registry = PipelineRegistry([DefaultHandler(), _safety_handler(10)])
    snap = make_snapshot(custom_position_sensors=[_safety_state(10)])
    result = registry.evaluate(snap)
    assert result.position == 10
    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.is_safety is True


def test_handlers_sorted_by_priority_descending() -> None:
    """Registry sorts handlers internally so insertion order doesn't matter."""
    # Provide handlers in reverse priority order.
    registry = PipelineRegistry([DefaultHandler(), SolarHandler(), _safety_handler(5)])
    snap = make_snapshot(custom_position_sensors=[_safety_state(5)])
    result = registry.evaluate(snap)
    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.is_safety is True


def test_decision_trace_records_all() -> None:
    """Trace includes the winning handler plus all evaluated handlers."""
    registry = PipelineRegistry(_all_handlers(15))
    snap = make_snapshot(custom_position_sensors=[_safety_state(15)])
    result = registry.evaluate(snap)
    # All 6 handlers should appear in the trace.
    assert len(result.decision_trace) == 6
    # First step is the winner.
    assert result.decision_trace[0].handler == "custom_position_5"
    assert result.decision_trace[0].matched is True
    # All subsequent steps should not be matched.
    for step in result.decision_trace[1:]:
        assert step.matched is False
        # Handlers that evaluated but were outprioritized get a descriptive reason.
        assert step.reason != "skipped (higher priority matched)"


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
    assert "outside acceptance angle" in result.decision_trace[0].reason.lower()
    # Default matches.
    assert result.decision_trace[1].handler == "default"
    assert result.decision_trace[1].matched is True


# ---------------------------------------------------------------------------
# Full pipeline scenario tests (all 6 handlers registered)
# ---------------------------------------------------------------------------


def test_full_pipeline_safety_slot_wins() -> None:
    """The priority-100 safety slot beats all other conditions."""
    registry = PipelineRegistry(_all_handlers(0))
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=60.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_climate_options_summer(),
        manual_override_active=True,
        motion_timeout_active=True,
        custom_position_sensors=[_safety_state(0)],
    )
    result = registry.evaluate(snap)
    assert result.position == 0
    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.is_safety is True


def test_full_pipeline_manual_override_beats_motion_timeout() -> None:
    """Manual override (priority 80) beats motion timeout (priority 75)."""
    registry = PipelineRegistry(_all_handlers())
    snap = make_snapshot(
        calculate_percentage_return=50.0,
        default_position=int(20.0),
        motion_timeout_active=True,
        manual_override_active=True,
        motion_control_enabled=True,
    )
    result = registry.evaluate(snap)
    assert result.control_method == ControlMethod.MANUAL


def test_full_pipeline_climate_summer() -> None:
    """Climate summer wins over solar when both are active."""
    registry = PipelineRegistry(_all_handlers())
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
    registry = PipelineRegistry(_all_handlers())
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
    registry = PipelineRegistry(_all_handlers())
    snap = make_snapshot(
        calculate_percentage_return=65.0,
        direct_sun_valid=True,
    )
    result = registry.evaluate(snap)
    assert result.position == 65
    assert result.control_method == ControlMethod.SOLAR


def test_full_pipeline_default_fallback() -> None:
    """Default wins when sun is not in FOV and no overrides are active."""
    registry = PipelineRegistry(_all_handlers())
    snap = make_snapshot(
        calculate_percentage_return=65.0,
        default_position=int(10.0),
        direct_sun_valid=False,
    )
    result = registry.evaluate(snap)
    assert result.position == 10
    assert result.control_method == ControlMethod.DEFAULT


def test_result_carries_full_trace_through_registry() -> None:
    """The PipelineResult returned by registry has the complete trace attached."""
    registry = PipelineRegistry(_all_handlers())
    snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=55.0)
    result = registry.evaluate(snap)
    # 6 handlers registered — trace must have 6 entries.
    assert len(result.decision_trace) == 6
    # Solar matched — exactly one handler is marked as the winner.
    winning = [s for s in result.decision_trace if s.matched]
    non_winning = [s for s in result.decision_trace if not s.matched]
    assert len(winning) == 1
    assert winning[0].handler == "solar"
    assert len(non_winning) == 5


# ---------------------------------------------------------------------------
# Climate data propagation tests (issue #182)
# ---------------------------------------------------------------------------


def _make_custom_position_handler() -> CustomPositionHandler:
    """CustomPositionHandler for slot 1 at the default priority."""
    return CustomPositionHandler(
        slot=1,
        position=50,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
    )


def _custom_state() -> CustomPositionSensorState:
    """Slot-1 sensor state matching ``_make_custom_position_handler``."""
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.custom",),
        is_on=True,
        position=50,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=False,
        use_my=False,
        slot=1,
        active_entity_ids=("binary_sensor.custom",),
    )


def test_climate_data_populated_when_custom_position_wins() -> None:
    """Climate data is available on the result even when CustomPositionHandler wins."""
    handlers = [
        _make_custom_position_handler(),
        ClimateHandler(),
        SolarHandler(),
        DefaultHandler(),
    ]
    registry = PipelineRegistry(handlers)
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=60.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_climate_options_summer(),
        custom_position_sensors=[_custom_state()],
    )
    result = registry.evaluate(snap)
    # Custom position wins for position.
    assert result.position == 50
    # But climate data is still populated from the climate handler.
    assert result.climate_data is not None
    assert result.climate_data.is_summer is True
    assert result.climate_strategy is not None


def test_climate_data_populated_when_safety_slot_wins() -> None:
    """Climate data is available on the result even when the safety slot wins."""
    registry = PipelineRegistry(_all_handlers())
    cover = _make_climate_cover(direct_sun_valid=True, calculate_percentage_return=60.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_climate_options_summer(),
        custom_position_sensors=[_safety_state(0)],
    )
    result = registry.evaluate(snap)
    assert result.position == 0
    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.is_safety is True
    assert result.climate_data is not None
    assert result.climate_data.is_summer is True


def test_climate_data_none_when_climate_mode_disabled() -> None:
    """climate_data remains None when climate mode is not enabled."""
    registry = PipelineRegistry(_all_handlers())
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=55.0,
        climate_mode_enabled=False,
    )
    result = registry.evaluate(snap)
    assert result.climate_data is None
    assert result.climate_strategy is None


def test_climate_handler_wins_data_from_winner() -> None:
    """When ClimateHandler wins, climate_data comes from the winner directly."""
    registry = PipelineRegistry(_all_handlers())
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
    assert result.climate_data is not None
    assert result.climate_data.is_winter is True


def test_outprioritized_handler_trace_has_descriptive_reason() -> None:
    """Handlers that evaluated but lost get an 'outprioritized by' trace reason."""
    handlers = [
        _make_custom_position_handler(),
        SolarHandler(),
        DefaultHandler(),
    ]
    registry = PipelineRegistry(handlers)
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=70.0,
        custom_position_sensors=[_custom_state()],
    )
    result = registry.evaluate(snap)
    # All handlers evaluated — 3 entries in trace.
    assert len(result.decision_trace) == 3
    winner_step = result.decision_trace[0]
    assert winner_step.matched is True
    # SolarHandler evaluated and got a result but was outprioritized.
    solar_step = next(s for s in result.decision_trace if s.handler == "solar")
    assert solar_step.matched is False
    assert "outprioritized" in solar_step.reason
    # DefaultHandler evaluated and got a result but was outprioritized.
    default_step = next(s for s in result.decision_trace if s.handler == "default")
    assert default_step.matched is False
    assert "outprioritized" in default_step.reason
