"""Tests for active_slot and floor_binding in the Decision Trace sensor attributes.

These tests verify that the sensor-layer correctly exposes the new PipelineResult
fields using the existing `if result.X is not None` conditional pattern so that
floor_binding=False is not suppressed by a truthiness check.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, SensorType
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.sensor import AdaptiveCoverDecisionTraceSensor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_custom_pos_trace_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: SensorType.BLIND}
    entry.options = {}
    return entry


def _make_coordinator(pipeline_result: PipelineResult | None = None):
    coord = MagicMock()
    coord.data = None
    coord._pipeline_result = pipeline_result
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    coord.check_adaptive_time = True
    return coord


def _make_sensor(
    pipeline_result: PipelineResult | None = None,
) -> AdaptiveCoverDecisionTraceSensor:
    return AdaptiveCoverDecisionTraceSensor(
        "test_custom_pos_trace_entry",
        _make_hass(),
        _make_config_entry(),
        "Test",
        _make_coordinator(pipeline_result),
    )


def _make_custom_result(
    *,
    active_slot: int | None = None,
    floor_binding: bool | None = None,
) -> PipelineResult:
    """Build a CUSTOM_POSITION PipelineResult with the given diagnostic fields."""
    return PipelineResult(
        position=50,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom position #1 active [bypasses automatic control]",
        active_slot=active_slot,
        floor_binding=floor_binding,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_active_slot_and_floor_binding_true_present_in_attrs() -> None:
    """Custom wins with active_slot=1, floor_binding=True → both attrs present."""
    result = _make_custom_result(active_slot=1, floor_binding=True)
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "active_slot" in attrs
    assert attrs["active_slot"] == 1
    assert "floor_binding" in attrs
    assert attrs["floor_binding"] is True


def test_floor_binding_false_present_in_attrs_not_suppressed() -> None:
    """Custom wins, floor_binding=False → attr is present and is exactly False.

    This is the motivating case from issue #421: the floor is configured but
    the solar position already exceeds it, so the floor is not constraining.
    The sensor must emit floor_binding=False using `is not None`, NOT truthiness,
    so a False value is not silently dropped.
    """
    result = _make_custom_result(active_slot=2, floor_binding=False)
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "floor_binding" in attrs
    assert attrs["floor_binding"] is False


def test_active_slot_and_floor_binding_absent_when_non_custom_wins() -> None:
    """Non-custom handler wins (active_slot=None, floor_binding=None) → neither attr emitted."""
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "active_slot" not in attrs
    assert "floor_binding" not in attrs
