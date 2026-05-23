"""Tests for custom_position_active_slot and custom_position_minimum_mode in the Decision Trace sensor attributes.

These tests verify that the sensor-layer correctly exposes the new PipelineResult
fields using the existing `if result.X is not None` conditional pattern so that
custom_position_minimum_mode=False is not suppressed by a truthiness check.
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
    custom_position_active_slot: int | None = None,
    custom_position_minimum_mode: bool | None = None,
) -> PipelineResult:
    """Build a CUSTOM_POSITION PipelineResult with the given diagnostic fields."""
    return PipelineResult(
        position=50,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom position #1 active [bypasses automatic control]",
        custom_position_active_slot=custom_position_active_slot,
        custom_position_minimum_mode=custom_position_minimum_mode,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_custom_position_active_slot_and_minimum_mode_true_present_in_attrs() -> None:
    """Custom wins with custom_position_active_slot=1, custom_position_minimum_mode=True → both attrs present."""
    result = _make_custom_result(
        custom_position_active_slot=1, custom_position_minimum_mode=True
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "custom_position_active_slot" in attrs
    assert attrs["custom_position_active_slot"] == 1
    assert "custom_position_minimum_mode" in attrs
    assert attrs["custom_position_minimum_mode"] is True


def test_custom_position_minimum_mode_false_present_in_attrs_not_suppressed() -> None:
    """Custom wins, custom_position_minimum_mode=False → attr is present and is exactly False.

    This is the motivating case from issue #421: the floor is configured but
    the solar position already exceeds it, so the floor is not constraining.
    The sensor must emit custom_position_minimum_mode=False using `is not None`, NOT truthiness,
    so a False value is not silently dropped.
    """
    result = _make_custom_result(
        custom_position_active_slot=2, custom_position_minimum_mode=False
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "custom_position_minimum_mode" in attrs
    assert attrs["custom_position_minimum_mode"] is False


def test_custom_position_fields_absent_when_non_custom_wins() -> None:
    """Non-custom handler wins (custom_position_active_slot=None, custom_position_minimum_mode=None) → neither attr emitted."""
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "custom_position_active_slot" not in attrs
    assert "custom_position_minimum_mode" not in attrs
