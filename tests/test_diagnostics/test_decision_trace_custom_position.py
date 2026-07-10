"""Tests for custom_position_active_slot and custom_position_minimum_mode in the Decision Trace sensor attributes.

These tests verify that the sensor-layer correctly exposes the new PipelineResult
fields using the existing `if result.X is not None` conditional pattern so that
custom_position_minimum_mode=False is not suppressed by a truthiness check.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    CUSTOM_POSITION_SLOT_NUMBERS,
    CoverType,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
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
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
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
    custom_position_active_slot_name: str | None = None,
) -> PipelineResult:
    """Build a CUSTOM_POSITION PipelineResult with the given diagnostic fields."""
    return PipelineResult(
        position=50,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom position #1 active [bypasses automatic control]",
        custom_position_active_slot=custom_position_active_slot,
        custom_position_minimum_mode=custom_position_minimum_mode,
        custom_position_active_slot_name=custom_position_active_slot_name,
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
    assert "custom_position_active_slot_name" not in attrs


def test_custom_position_active_slot_name_present_when_set() -> None:
    """Custom wins with sensor friendly_name 'Table extension' → attr emitted verbatim.

    Surfaces the human label so the companion card can render
    "Custom · Table extension" instead of just "Custom #1".
    """
    result = _make_custom_result(
        custom_position_active_slot=1,
        custom_position_minimum_mode=True,
        custom_position_active_slot_name="Table extension",
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert attrs.get("custom_position_active_slot_name") == "Table extension"


def test_custom_position_active_slot_name_absent_when_none() -> None:
    """Custom wins but no friendly_name resolved → attr not emitted (avoids noise)."""
    result = _make_custom_result(
        custom_position_active_slot=2,
        custom_position_minimum_mode=False,
        custom_position_active_slot_name=None,
    )
    sensor = _make_sensor(result)
    attrs = sensor.extra_state_attributes or {}

    assert "custom_position_active_slot_name" not in attrs


# ---------------------------------------------------------------------------
# custom_position_slots snapshot
# ---------------------------------------------------------------------------


def _make_sensor_with_options(options: dict, states: dict | None = None):
    """Build a sensor whose config entry has the given options + hass.states map."""
    hass = _make_hass()
    if states:
        hass.states.get.side_effect = lambda eid, _states=states: _states.get(eid)

    coord = MagicMock()
    coord.data = None
    coord._pipeline_result = None
    coord.logger = MagicMock()
    coord.hass = hass
    coord.check_adaptive_time = True

    entry = MagicMock()
    entry.entry_id = "snapshot_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = options

    from custom_components.adaptive_cover_pro.sensor import (
        AdaptiveCoverDecisionTraceSensor,
    )

    return AdaptiveCoverDecisionTraceSensor(
        "snapshot_entry", hass, entry, "Test", coord
    )


def test_custom_position_slots_snapshot_lists_configured_slots() -> None:
    """Each configured slot appears in custom_position_slots with its full config."""
    bound = MagicMock()
    bound.attributes = {"friendly_name": "Table extension"}
    options = {
        "custom_position_sensor_1": "binary_sensor.table",
        "custom_position_1": 60,
        "custom_position_priority_1": 80,
        "custom_position_min_mode_1": True,
    }
    sensor = _make_sensor_with_options(options, states={"binary_sensor.table": bound})
    attrs = sensor.extra_state_attributes or {}

    slots = attrs.get("custom_position_slots")
    assert isinstance(slots, list)
    # Snapshot must include every slot so the card can render an even row.
    assert len(slots) == len(CUSTOM_POSITION_SLOT_NUMBERS)

    slot1 = next(s for s in slots if s["slot"] == 1)
    assert slot1["enabled"] is True  # default when key absent
    assert slot1["position"] == 60
    assert slot1["priority"] == 80
    assert slot1["min_mode"] is True
    assert slot1["sensor_name"] == "Table extension"


def test_custom_position_slots_snapshot_reflects_enabled_false() -> None:
    """A slot with custom_position_enabled_N=False reads enabled=False."""
    options = {
        "custom_position_sensor_2": "binary_sensor.x",
        "custom_position_2": 40,
        "custom_position_enabled_2": False,
    }
    sensor = _make_sensor_with_options(options)
    attrs = sensor.extra_state_attributes or {}

    slot2 = next(s for s in attrs["custom_position_slots"] if s["slot"] == 2)
    assert slot2["enabled"] is False


def test_custom_position_slots_snapshot_unconfigured_slot_is_inactive() -> None:
    """Unconfigured slots still appear, marked enabled=False with null sensor."""
    sensor = _make_sensor_with_options({})  # no slots configured
    attrs = sensor.extra_state_attributes or {}

    for s in attrs["custom_position_slots"]:
        assert s["enabled"] is False
        assert s["position"] is None
        assert s["sensor"] is None
        assert s["sensor_name"] is None


def test_custom_position_slots_snapshot_exposes_configured_name() -> None:
    """A slot with a configured name (issue #867) exposes it as custom_name."""
    bound = MagicMock()
    bound.attributes = {"friendly_name": "Table extension"}
    options = {
        "custom_position_sensor_1": "binary_sensor.table",
        "custom_position_1": 60,
        "custom_position_name_1": "Movie night",
    }
    sensor = _make_sensor_with_options(options, states={"binary_sensor.table": bound})
    attrs = sensor.extra_state_attributes or {}

    slot1 = next(s for s in attrs["custom_position_slots"] if s["slot"] == 1)
    assert slot1["custom_name"] == "Movie night"
    # sensor_name stays the raw friendly_name — custom_name is additive.
    assert slot1["sensor_name"] == "Table extension"


def test_custom_position_slots_snapshot_custom_name_none_when_unset() -> None:
    """A slot with no configured name reads custom_name=None."""
    options = {
        "custom_position_sensor_2": "binary_sensor.x",
        "custom_position_2": 40,
    }
    sensor = _make_sensor_with_options(options)
    attrs = sensor.extra_state_attributes or {}

    slot2 = next(s for s in attrs["custom_position_slots"] if s["slot"] == 2)
    assert slot2["custom_name"] is None
