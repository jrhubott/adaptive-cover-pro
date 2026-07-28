"""Tests for CustomPositionHandler (per-instance model)."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)
from custom_components.adaptive_cover_pro.reason_i18n import render_en

from .conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTITY = "binary_sensor.scene_a"
_DEFAULT_PRIORITY = DEFAULT_CUSTOM_POSITION_PRIORITY


def _handler(
    slot: int = 1,
    position: int = 50,
    priority: int = _DEFAULT_PRIORITY,
    tilt: int | None = None,
) -> CustomPositionHandler:
    """Create a CustomPositionHandler with sensible defaults."""
    return CustomPositionHandler(
        slot=slot,
        position=position,
        priority=priority,
        tilt=tilt,
    )


def _snapshot_with(
    entity_id: str,
    is_on: bool,
    position: int = 50,
    priority: int = _DEFAULT_PRIORITY,
    slot: int = 1,
):
    """Build a snapshot with a single custom position sensor entry."""
    return make_snapshot(
        custom_position_sensors=[
            _make_state(entity_id, is_on, position, priority, False, False, slot=slot)
        ]
    )


def _make_state(
    entity_id: str,
    is_on: bool,
    position: int,
    priority: int,
    min_mode: bool,
    use_my: bool,
    *,
    slot: int = 1,
    custom_name: str | None = None,
) -> CustomPositionSensorState:
    """Compact constructor for test sensor states."""
    return CustomPositionSensorState(
        entity_ids=(entity_id,),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        slot=slot,
        active_entity_ids=(entity_id,) if is_on else (),
        custom_name=custom_name,
    )


# ---------------------------------------------------------------------------
# Handler metadata
# ---------------------------------------------------------------------------


class TestHandlerMetadata:
    """Verify static handler properties."""

    def test_name_includes_slot(self) -> None:
        """Name must be 'custom_position_<slot>'."""
        assert _handler(slot=1).name == "custom_position_1"
        assert _handler(slot=2).name == "custom_position_2"
        assert _handler(slot=5).name == "custom_position_5"

    def test_default_priority(self) -> None:
        """Default priority must be 77."""
        assert _handler().priority == _DEFAULT_PRIORITY

    def test_custom_priority_stored(self) -> None:
        """Priority passed to constructor is stored correctly."""
        assert _handler(priority=95).priority == 95
        assert _handler(priority=35).priority == 35
        assert _handler(priority=1).priority == 1

    def test_priority_range_high(self) -> None:
        """Priority 100 is accepted (safety — the migrated force override)."""
        h = _handler(priority=100)
        assert h.priority == 100

    def test_priority_range_low(self) -> None:
        """Priority 1 is accepted (just above default handler 0)."""
        h = _handler(priority=1)
        assert h.priority == 1


# ---------------------------------------------------------------------------
# Evaluate — slot not in snapshot
# ---------------------------------------------------------------------------


class TestEvaluateNoMatchingSlot:
    """Handler passes through when its slot is not in the snapshot."""

    def test_returns_none_when_empty_list(self) -> None:
        snapshot = make_snapshot(custom_position_sensors=[])
        assert _handler().evaluate(snapshot) is None

    def test_returns_none_when_slot_absent(self) -> None:
        """Different slot in snapshot — handler's slot not present."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state("binary_sensor.other", True, 50, 77, False, False, slot=2)
            ]
        )
        assert _handler(slot=1).evaluate(snapshot) is None

    def test_describe_skip_mentions_slot(self) -> None:
        snapshot = make_snapshot(custom_position_sensors=[])
        skip = render_en(_handler(slot=2).describe_skip(snapshot))
        assert "#2" in skip
        assert "not active" in skip

    def test_describe_skip_payload_names_slot(self) -> None:
        snapshot = make_snapshot(custom_position_sensors=[])
        payload = _handler(slot=2).describe_skip(snapshot)
        assert payload.code == ReasonCode.SKIP_CUSTOM_NOT_ACTIVE
        assert payload.params["slot"] == 2


# ---------------------------------------------------------------------------
# Evaluate — sensor present but off
# ---------------------------------------------------------------------------


class TestEvaluateSensorOff:
    """Handler passes through when its slot is present but off."""

    def test_returns_none_when_off(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=False)
        assert _handler().evaluate(snapshot) is None


# ---------------------------------------------------------------------------
# Evaluate — sensor present and on
# ---------------------------------------------------------------------------


class TestEvaluateSensorOn:
    """Handler returns the configured position when its slot is on."""

    def test_returns_configured_position(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=45)
        result = _handler(position=45).evaluate(snapshot)
        assert result is not None
        assert result.position == 45

    def test_control_method_is_custom_position(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=30)
        result = _handler(position=30).evaluate(snapshot)
        assert result is not None
        assert result.control_method == ControlMethod.CUSTOM_POSITION

    def test_reason_contains_slot_entity_and_position(self) -> None:
        snapshot = _snapshot_with(
            "binary_sensor.morning", is_on=True, position=70, slot=2
        )
        result = _handler(slot=2, position=70).evaluate(snapshot)
        assert result is not None
        assert "#2" in result.reason
        assert "binary_sensor.morning" in result.reason
        assert "70%" in result.reason

    def test_position_zero_valid(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=0)
        result = _handler(position=0).evaluate(snapshot)
        assert result is not None
        assert result.position == 0

    def test_position_one_hundred_valid(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=100)
        result = _handler(position=100).evaluate(snapshot)
        assert result is not None
        assert result.position == 100

    def test_custom_position_tilt_not_clamped_by_max_tilt(self) -> None:
        """Custom-position tilt is a deliberate carve-out — max_tilt never clamps it (issue #503/#515)."""
        snapshot = make_snapshot(
            max_tilt=85,
            custom_position_sensors=[
                _make_state(_ENTITY, True, 50, _DEFAULT_PRIORITY, False, False)
            ],
        )
        result = _handler(position=50, tilt=100).evaluate(snapshot)
        assert result is not None
        assert result.tilt == 100


# ---------------------------------------------------------------------------
# Multi-sensor / template trigger reasons (issue #563)
# ---------------------------------------------------------------------------


class TestTriggerReason:
    """Reason strings describe what activated the slot."""

    def test_reason_lists_all_active_sensors(self) -> None:
        """Multi-sensor slot: reason joins every active sensor, comma-separated."""
        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.wind", "binary_sensor.rain", "binary_sensor.x"),
            is_on=True,
            position=90,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=("binary_sensor.wind", "binary_sensor.rain"),
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=90).evaluate(snapshot)
        assert result is not None
        assert "binary_sensor.wind, binary_sensor.rain" in result.reason
        assert "binary_sensor.x" not in result.reason

    def test_reason_says_template_for_template_only_trigger(self) -> None:
        """Template-only slot: reason says 'template' (no sensors bound)."""
        state = CustomPositionSensorState(
            entity_ids=(),
            is_on=True,
            position=40,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=(),
            template_active=True,
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=40).evaluate(snapshot)
        assert result is not None
        assert "template" in result.reason

    def test_reason_lists_sensors_and_template_together(self) -> None:
        """Active sensors plus an active template both appear in the reason."""
        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.wind",),
            is_on=True,
            position=40,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=("binary_sensor.wind",),
            template_active=True,
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=40).evaluate(snapshot)
        assert result is not None
        assert "binary_sensor.wind, template" in result.reason


# ---------------------------------------------------------------------------
# reason_payload — stable code + params (issue #882)
# ---------------------------------------------------------------------------


class TestReasonPayload:
    """The migrated handler emits a stable Reason payload on every path."""

    def test_slot_head_position_payload(self) -> None:
        """An unnamed slot on the exact-position path nests a head_slot fragment."""
        snapshot = _snapshot_with(
            "binary_sensor.morning", is_on=True, position=70, slot=2
        )
        result = _handler(slot=2, position=70).evaluate(snapshot)
        assert result is not None
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.CUSTOM_POSITION
        assert result.reason_payload.params["position"] == 70
        assert result.reason_payload.params["bypass_note"] == ""
        head = result.reason_payload.params["head"]
        assert head.code == ReasonCode.CUSTOM_HEAD_SLOT
        assert head.params["slot"] == 2
        # The trigger is a tuple of the active entity ids (scalars).
        assert list(head.params["trigger"]) == ["binary_sensor.morning"]

    def test_named_head_payload(self) -> None:
        """A named slot nests a head_named fragment carrying the raw name."""
        state = _make_state(
            "binary_sensor.movie",
            True,
            40,
            _DEFAULT_PRIORITY,
            False,
            False,
            slot=3,
            custom_name="Movie night",
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(slot=3, position=40).evaluate(snapshot)
        assert result is not None
        assert result.reason_payload is not None
        head = result.reason_payload.params["head"]
        assert head.code == ReasonCode.CUSTOM_HEAD_NAMED
        assert head.params["name"] == "Movie night"

    def test_use_my_path_payload(self) -> None:
        """The use-My path emits a custom.use_my payload."""
        state = _make_state(_ENTITY, True, 50, _DEFAULT_PRIORITY, False, True)
        snapshot = make_snapshot(custom_position_sensors=[state], my_position_value=30)
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.CUSTOM_USE_MY
        assert result.reason_payload.params["position"] == 30

    def test_bypass_note_fragment_on_safety_slot(self) -> None:
        """A safety-priority slot sets bypass_note to a bypass fragment."""
        state = _make_state(
            _ENTITY, True, 50, CUSTOM_POSITION_SAFETY_PRIORITY, False, False
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(
            position=50, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert result.reason_payload is not None
        bypass = result.reason_payload.params["bypass_note"]
        assert bypass.code == ReasonCode.FRAGMENT_BYPASS_NOTE

    def test_trigger_tuple_includes_template_fragment(self) -> None:
        """A sensor + template trigger nests the entity id and a template fragment."""
        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.wind",),
            is_on=True,
            position=40,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=("binary_sensor.wind",),
            template_active=True,
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=40).evaluate(snapshot)
        assert result is not None
        head = result.reason_payload.params["head"]
        assert head.code == ReasonCode.CUSTOM_HEAD_SLOT
        trigger = list(head.params["trigger"])
        assert trigger[0] == "binary_sensor.wind"
        assert trigger[1].code == ReasonCode.FRAGMENT_TRIGGER_TEMPLATE

    def test_trigger_fallback_fragment_when_empty(self) -> None:
        """A slot with no active sensors and no template uses the fallback fragment."""
        state = CustomPositionSensorState(
            entity_ids=(),
            is_on=True,
            position=40,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=(),
            template_active=False,
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=40).evaluate(snapshot)
        assert result is not None
        head = result.reason_payload.params["head"]
        trigger = head.params["trigger"]
        assert trigger.code == ReasonCode.FRAGMENT_TRIGGER_FALLBACK


# ---------------------------------------------------------------------------
# is_safety — priority-100 slots carry force-override safety semantics
# ---------------------------------------------------------------------------


class TestIsSafety:
    """Slots at CUSTOM_POSITION_SAFETY_PRIORITY (100) set is_safety=True."""

    def test_safety_priority_sets_is_safety_true(self) -> None:
        snapshot = _snapshot_with(
            _ENTITY, is_on=True, position=90, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        )
        result = _handler(
            position=90, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert result.is_safety is True

    def test_below_safety_priority_sets_is_safety_false(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=90, priority=99)
        result = _handler(position=90, priority=99).evaluate(snapshot)
        assert result is not None
        assert result.is_safety is False

    def test_default_priority_sets_is_safety_false(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=90)
        result = _handler(position=90).evaluate(snapshot)
        assert result is not None
        assert result.is_safety is False

    def test_safety_priority_use_my_path_sets_is_safety_true(self) -> None:
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, 50, CUSTOM_POSITION_SAFETY_PRIORITY, False, True
                )
            ],
            my_position_value=30,
        )
        result = _handler(
            position=50, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert result.use_my_position is True
        assert result.is_safety is True


# ---------------------------------------------------------------------------
# Per-instance isolation — each handler only checks its own slot
# ---------------------------------------------------------------------------


class TestPerInstanceIsolation:
    """Each handler instance only evaluates its own slot."""

    def test_handler_ignores_other_slots(self) -> None:
        """Handler for slot 1 is not triggered by slot 2 being on."""
        sensors = [
            _make_state("binary_sensor.slot1", False, 30, 77, False, False, slot=1),
            _make_state("binary_sensor.slot2", True, 70, 77, False, False, slot=2),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)

        h1 = _handler(slot=1, position=30)
        h2 = _handler(slot=2, position=70)

        # h1 should NOT fire even though slot2 is on
        assert h1.evaluate(snapshot) is None
        # h2 SHOULD fire
        result = h2.evaluate(snapshot)
        assert result is not None
        assert result.position == 70

    def test_both_on_both_handlers_fire(self) -> None:
        """When both slots are on, both handlers independently return results."""
        sensors = [
            _make_state("binary_sensor.slot1", True, 30, 95, False, False, slot=1),
            _make_state("binary_sensor.slot2", True, 70, 60, False, False, slot=2),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)

        h1 = _handler(slot=1, position=30, priority=95)
        h2 = _handler(slot=2, position=70, priority=60)

        r1 = h1.evaluate(snapshot)
        r2 = h2.evaluate(snapshot)
        assert r1 is not None and r1.position == 30
        assert r2 is not None and r2.position == 70


# ---------------------------------------------------------------------------
# Priority affects pipeline ordering (not evaluate() itself)
# ---------------------------------------------------------------------------


class TestPriorityAttribute:
    """Priority is a plain attribute read by PipelineRegistry for sorting."""

    def test_high_priority_handler_registered(self) -> None:
        """High-priority custom handler has priority above manual override (80)."""
        h = _handler(priority=95)
        assert h.priority > 80  # beats manual override

    def test_low_priority_handler_registered(self) -> None:
        """Low-priority custom handler has priority below solar (40)."""
        h = _handler(priority=35)
        assert h.priority < 40

    def test_default_priority_between_manual_and_motion(self) -> None:
        """Default priority 77 sits between manual override (80) and motion timeout (75)."""
        h = _handler()
        assert 75 < h.priority < 80


# ---------------------------------------------------------------------------
# raw_calculated_position
# ---------------------------------------------------------------------------


class TestRawCalculatedPosition:
    """raw_calculated_position is populated on a match."""

    def test_raw_calculated_position_set(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=55)
        result = _handler(position=55).evaluate(snapshot)
        assert result is not None
        assert result.raw_calculated_position is not None


# ---------------------------------------------------------------------------
# Minimum position mode
# ---------------------------------------------------------------------------


class TestMinimumPositionMode:
    """Tests for CustomPositionHandler minimum position mode.

    Floor-mode composition lives in the registry post-decision pass (see
    ``tests/test_pipeline/test_floor_composition.py``). At the handler
    level, all min_mode does is *defer* — evaluate() returns None so the
    pipeline can pick a genuine lower-priority winner.
    """

    def _snapshot_min_mode(
        self,
        *,
        position: int,
        min_mode: bool,
        is_on: bool = True,
        calculate_percentage_return: float = 50.0,
        direct_sun_valid: bool = True,
    ):
        """Build a snapshot for min_mode tests."""
        return make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, is_on, position, _DEFAULT_PRIORITY, min_mode, False
                )
            ],
            direct_sun_valid=direct_sun_valid,
            calculate_percentage_return=calculate_percentage_return,
        )

    def test_min_mode_off_uses_exact_position(self) -> None:
        """With min_mode off, position is always the configured value (default behavior)."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=False, calculate_percentage_return=50.0
        )
        result = _handler(position=30).evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_defers(self) -> None:
        """With min_mode on, the handler defers (returns None) — the floor is
        composed by the registry, not produced by the handler.
        """
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=50.0
        )
        result = _handler(position=30).evaluate(snap)
        assert result is None

    def test_min_mode_off_reason_no_minimum_mode_mention(self) -> None:
        """With min_mode off, reason string does not mention minimum mode."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=False, calculate_percentage_return=50.0
        )
        result = _handler(position=30).evaluate(snap)
        assert result is not None
        assert "minimum mode" not in result.reason


# ---------------------------------------------------------------------------
# bypass_auto_control — gated on safety priority (issue #767)
# ---------------------------------------------------------------------------


class TestBypassAutoControl:
    """CustomPositionHandler gates bypass_auto_control on safety priority (#767).

    Only a priority-100 (safety) slot bypasses Automatic Control; a
    default-priority (77) slot respects the switch like any other handler.
    """

    def test_no_bypass_normal_path_non_safety(self) -> None:
        """A default-priority (77) slot does NOT bypass automatic control."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(_ENTITY, True, 50, _DEFAULT_PRIORITY, False, False)
            ]
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is False

    def test_bypass_set_normal_path_safety(self) -> None:
        """A safety-priority (100) slot bypasses automatic control."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, 50, CUSTOM_POSITION_SAFETY_PRIORITY, False, False
                )
            ]
        )
        result = _handler(
            position=50, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is True

    def test_bypass_flag_min_mode_defers(self) -> None:
        """Min-mode handler defers (returns None) — bypass is moot since no
        result is produced. The floor-clamp composition pass in the registry
        carries the winner forward via the lower-priority handler.
        """
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(_ENTITY, True, 30, _DEFAULT_PRIORITY, True, False)
            ],
            calculate_percentage_return=50.0,
        )
        result = _handler(position=30).evaluate(snapshot)
        assert result is None

    def test_no_bypass_use_my_path_non_safety(self) -> None:
        """The use-My path of a default-priority slot does NOT bypass automatic control."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(_ENTITY, True, 50, _DEFAULT_PRIORITY, False, True)
            ],
            my_position_value=30,
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.use_my_position is True
        assert result.bypass_auto_control is False

    def test_bypass_set_use_my_path_safety(self) -> None:
        """The use-My path of a safety-priority slot bypasses automatic control."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, 50, CUSTOM_POSITION_SAFETY_PRIORITY, False, True
                )
            ],
            my_position_value=30,
        )
        result = _handler(
            position=50, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert result.use_my_position is True
        assert result.bypass_auto_control is True

    def test_reason_omits_bypass_text_non_safety(self) -> None:
        """A default-priority slot reason omits the bypass annotation."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(_ENTITY, True, 50, _DEFAULT_PRIORITY, False, False)
            ]
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" not in result.reason

    def test_reason_includes_bypass_text_safety(self) -> None:
        """A safety-priority slot reason includes '[bypasses automatic control]'."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, 50, CUSTOM_POSITION_SAFETY_PRIORITY, False, False
                )
            ]
        )
        result = _handler(
            position=50, priority=CUSTOM_POSITION_SAFETY_PRIORITY
        ).evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_no_bypass_when_sensor_off(self) -> None:
        """Handler returns None (not a bypass result) when sensor is off."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, False, 50, 77, False, False)]
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is None


# ---------------------------------------------------------------------------
# CustomPositionSensorState.tilt field
# ---------------------------------------------------------------------------


class TestCustomPositionSensorStateTilt:
    """CustomPositionSensorState must carry an optional tilt value."""

    def test_default_tilt_is_none(self) -> None:
        """Tilt defaults to None when not supplied."""
        state = CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
        )
        assert state.tilt is None

    def test_tilt_can_be_set_to_int(self) -> None:
        """Tilt accepts an integer value."""
        state = CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=35,
        )
        assert state.tilt == 35

    def test_tilt_zero_accepted(self) -> None:
        """tilt=0 is a valid value."""
        state = CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=0,
        )
        assert state.tilt == 0

    def test_tilt_hundred_accepted(self) -> None:
        """tilt=100 is a valid value."""
        state = CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=100,
        )
        assert state.tilt == 100


# ---------------------------------------------------------------------------
# Handler stamps tilt on PipelineResult (Steps 4-5)
# ---------------------------------------------------------------------------


class TestHandlerTilt:
    """CustomPositionHandler must stamp tilt=self._tilt on every result path."""

    def test_tilt_none_by_default_normal_path(self) -> None:
        """Handler with no tilt configured stamps tilt=None on normal path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, False)]
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.tilt is None

    def test_tilt_stamped_on_normal_path(self) -> None:
        """Handler with tilt=35 stamps result.tilt=35 on normal path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, False)]
        )
        result = _handler(position=50, tilt=35).evaluate(snapshot)
        assert result is not None
        assert result.tilt == 35

    def test_tilt_zero_stamped(self) -> None:
        """tilt=0 is stamped on result (not treated as falsy None)."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, False)]
        )
        result = _handler(position=50, tilt=0).evaluate(snapshot)
        assert result is not None
        assert result.tilt == 0

    def test_tilt_stamped_on_use_my_path(self) -> None:
        """Handler with tilt=80 stamps result.tilt=80 on use-My path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, True)],
            my_position_value=30,
        )
        result = _handler(position=50, tilt=80).evaluate(snapshot)
        assert result is not None
        assert result.tilt == 80

    def test_tilt_none_on_use_my_path_when_not_set(self) -> None:
        """Handler with no tilt stamps None on use-My path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, True)],
            my_position_value=30,
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.tilt is None

    def test_min_mode_defers_so_no_tilt_emitted(self) -> None:
        """Min-mode handler defers — tilt would be applied by the lower-priority
        winner; this handler emits no result of its own.
        """
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 30, 77, True, False)],
            calculate_percentage_return=50.0,
        )
        result = _handler(position=30, tilt=60).evaluate(snapshot)
        assert result is None


# ---------------------------------------------------------------------------
# custom_position_active_slot
# ---------------------------------------------------------------------------


class TestCustomPositionActiveSlot:
    """custom_position_active_slot is populated with the slot number when the handler fires."""

    @pytest.mark.parametrize("slot", [1, 2, 3, 4, 5])
    def test_custom_position_active_slot_matches_handler_slot(self, slot: int) -> None:
        """custom_position_active_slot == slot for each of the 5 possible slot numbers."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, 50, _DEFAULT_PRIORITY, False, False, slot=slot
                )
            ]
        )
        result = _handler(slot=slot, position=50).evaluate(snapshot)
        assert result is not None
        assert result.custom_position_active_slot == slot

    def test_custom_position_active_slot_none_on_default_pipeline_result(self) -> None:
        """A non-custom PipelineResult has custom_position_active_slot defaulting to None."""
        from custom_components.adaptive_cover_pro.const import ControlMethod
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

        result = PipelineResult(
            position=50, control_method=ControlMethod.SOLAR, reason="solar"
        )
        assert result.custom_position_active_slot is None


# ---------------------------------------------------------------------------
# custom_position_minimum_mode
# ---------------------------------------------------------------------------


class TestCustomPositionMinimumMode:
    """custom_position_minimum_mode field on PipelineResult.

    With floor-mode composition moved to the registry, the handler no longer
    emits a PipelineResult when min_mode is active — it defers. The remaining
    on-handler invariants: the field is None on exact-position results, on the
    use-My path, and on non-custom results.
    """

    def _snap(
        self,
        *,
        position: int,
        min_mode: bool,
        calculate_percentage_return: float,
        use_my: bool = False,
        my_position_value: int | None = None,
    ):
        return make_snapshot(
            custom_position_sensors=[
                _make_state(
                    _ENTITY, True, position, _DEFAULT_PRIORITY, min_mode, use_my
                )
            ],
            direct_sun_valid=True,
            calculate_percentage_return=calculate_percentage_return,
            my_position_value=my_position_value,
        )

    def test_min_mode_defers(self) -> None:
        """min_mode=True (without use_my) → evaluate() returns None."""
        snap = self._snap(position=50, min_mode=True, calculate_percentage_return=20.0)
        result = _handler(position=50).evaluate(snap)
        assert result is None

    def test_custom_position_minimum_mode_none_when_exact_mode(self) -> None:
        """min_mode=False → custom_position_minimum_mode is None."""
        snap = self._snap(position=50, min_mode=False, calculate_percentage_return=70.0)
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.custom_position_minimum_mode is None

    def test_custom_position_minimum_mode_none_on_use_my_path(self) -> None:
        """use_my=True bypasses min_mode → custom_position_minimum_mode is None."""
        snap = self._snap(
            position=50,
            min_mode=True,
            calculate_percentage_return=20.0,
            use_my=True,
            my_position_value=60,
        )
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.custom_position_minimum_mode is None

    def test_both_fields_none_on_non_custom_result(self) -> None:
        """A plain PipelineResult has both custom_position_active_slot and custom_position_minimum_mode as None."""
        from custom_components.adaptive_cover_pro.const import ControlMethod
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

        result = PipelineResult(
            position=50, control_method=ControlMethod.SOLAR, reason="solar"
        )
        assert result.custom_position_active_slot is None
        assert result.custom_position_minimum_mode is None


# ---------------------------------------------------------------------------
# custom_position_active_slot_name — sensor friendly-name propagation
# ---------------------------------------------------------------------------


class TestCustomPositionActiveSlotName:
    """The handler propagates the bound sensor's friendly_name onto the result.

    Lets downstream diagnostics (decision_trace, companion card badge) show
    "Custom · Table extension" instead of just "Custom #1".
    """

    @staticmethod
    def _state_with_name(name: str | None) -> CustomPositionSensorState:
        return CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            sensor_name=name,
            slot=1,
            active_entity_ids=(_ENTITY,),
        )

    def test_name_propagates_on_normal_path(self) -> None:
        snap = make_snapshot(
            custom_position_sensors=[self._state_with_name("Table extension")]
        )
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.custom_position_active_slot_name == "Table extension"

    def test_name_propagates_on_use_my_path(self) -> None:
        state = CustomPositionSensorState(
            entity_ids=(_ENTITY,),
            is_on=True,
            position=50,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=True,
            sensor_name="My preset",
            slot=1,
            active_entity_ids=(_ENTITY,),
        )
        snap = make_snapshot(
            custom_position_sensors=[state],
            my_position_value=30,
        )
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.use_my_position is True
        assert result.custom_position_active_slot_name == "My preset"

    def test_name_is_none_when_sensor_name_unset(self) -> None:
        snap = make_snapshot(custom_position_sensors=[self._state_with_name(None)])
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.custom_position_active_slot_name is None

    def test_field_defaults_to_none_on_plain_result(self) -> None:
        """A non-custom PipelineResult has custom_position_active_slot_name=None."""
        from custom_components.adaptive_cover_pro.const import ControlMethod
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

        result = PipelineResult(
            position=50, control_method=ControlMethod.SOLAR, reason="solar"
        )
        assert result.custom_position_active_slot_name is None


# ---------------------------------------------------------------------------
# Per-slot custom name (issue #867) — overrides the label everywhere.
# ---------------------------------------------------------------------------


class TestCustomPositionCustomName:
    """An optional per-slot ``name`` overrides the reason/label, else falls
    back to today's exact behavior (byte-identical when unset).
    """

    def test_reason_uses_custom_name_when_set(self) -> None:
        """A named slot's reason starts with '<name> active' and still shows the position."""
        state = _make_state(
            "binary_sensor.movie",
            True,
            40,
            _DEFAULT_PRIORITY,
            False,
            False,
            slot=3,
            custom_name="Movie night",
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(slot=3, position=40).evaluate(snapshot)
        assert result is not None
        assert result.reason.startswith("Movie night active")
        assert "40%" in result.reason
        assert result.custom_position_active_slot_name == "Movie night"

    def test_reason_falls_back_when_name_absent(self) -> None:
        """An unnamed slot keeps today's '#N (entity) — position X%' reason exactly."""
        snapshot = _snapshot_with(
            "binary_sensor.morning", is_on=True, position=70, slot=2
        )
        result = _handler(slot=2, position=70).evaluate(snapshot)
        assert result is not None
        assert "#2" in result.reason
        assert "binary_sensor.morning" in result.reason
        assert "70%" in result.reason
        assert result.custom_position_active_slot_name is None

    def test_template_only_named_slot_surfaces_name_on_attribute(self) -> None:
        """A named template-only slot surfaces its name as the active-slot-name
        attribute — today (unnamed) that attribute would be None.
        """
        state = CustomPositionSensorState(
            entity_ids=(),
            is_on=True,
            position=40,
            priority=_DEFAULT_PRIORITY,
            min_mode=False,
            use_my=False,
            slot=1,
            active_entity_ids=(),
            template_active=True,
            custom_name="Away mode",
        )
        snapshot = make_snapshot(custom_position_sensors=[state])
        result = _handler(position=40).evaluate(snapshot)
        assert result is not None
        assert result.custom_position_active_slot_name == "Away mode"
        assert result.reason.startswith("Away mode active")

    def test_named_use_my_path_uses_name_in_reason(self) -> None:
        """A named slot on the use-My path also uses '<name> active' in the reason."""
        state = _make_state(
            _ENTITY,
            True,
            50,
            _DEFAULT_PRIORITY,
            False,
            True,
            custom_name="Nap time",
        )
        snapshot = make_snapshot(
            custom_position_sensors=[state],
            my_position_value=30,
        )
        result = _handler(position=50).evaluate(snapshot)
        assert result is not None
        assert result.use_my_position is True
        assert result.reason.startswith("Nap time active")
        assert result.custom_position_active_slot_name == "Nap time"


# ---------------------------------------------------------------------------
# Axis-constraint deferral — issue #943
#
# The deferral generalizes from "min_mode and not use_my" to "position_mode is
# not FIXED and not use_my". Every pre-#943 config keeps its exact outcome; the
# new constraint modes defer for the same reason min_mode always has — the
# registry composes them onto whichever handler actually wins.
# ---------------------------------------------------------------------------


def _constraint_state(
    *,
    position: int | None = None,
    min_mode: bool = False,
    use_my: bool = False,
    position_max: int | None = None,
    tilt_min: int | None = None,
    tilt_max: int | None = None,
    tilt: int | None = None,
    tilt_only: bool = False,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(_ENTITY,),
        is_on=True,
        position=position,
        priority=_DEFAULT_PRIORITY,
        min_mode=min_mode,
        use_my=use_my,
        tilt=tilt,
        tilt_only=tilt_only,
        slot=1,
        active_entity_ids=(_ENTITY,),
        position_max=position_max,
        tilt_min=tilt_min,
        tilt_max=tilt_max,
    )


class TestConstraintModeDeferral:
    """Non-FIXED position modes defer so the registry can compose them."""

    def test_max_mode_slot_defers(self) -> None:
        """A position-ceiling slot must not claim an exact position."""
        snap = make_snapshot(
            custom_position_sensors=[_constraint_state(position_max=60)]
        )
        assert _handler().evaluate(snap) is None

    def test_range_mode_slot_defers(self) -> None:
        """A floor+ceiling slot defers, exactly as a floor-only slot does."""
        snap = make_snapshot(
            custom_position_sensors=[
                _constraint_state(position=30, min_mode=True, position_max=70)
            ]
        )
        assert _handler().evaluate(snap) is None

    def test_none_mode_slot_defers(self) -> None:
        """A tilt-bound-only slot makes no position claim at all."""
        snap = make_snapshot(custom_position_sensors=[_constraint_state(tilt_min=50)])
        assert _handler().evaluate(snap) is None

    def test_fixed_mode_slot_still_claims(self) -> None:
        """The exact-position path is unchanged."""
        snap = make_snapshot(custom_position_sensors=[_constraint_state(position=50)])
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.position == 50

    def test_min_mode_slot_still_defers(self) -> None:
        """Parity: today's floor-mode deferral is unchanged."""
        snap = make_snapshot(
            custom_position_sensors=[_constraint_state(position=60, min_mode=True)]
        )
        assert _handler().evaluate(snap) is None

    def test_use_my_with_min_mode_still_claims(self) -> None:
        """Parity: the My path ignores floor semantics and claims (unchanged)."""
        snap = make_snapshot(
            custom_position_sensors=[
                _constraint_state(position=60, min_mode=True, use_my=True)
            ],
            my_position_value=42,
        )
        result = _handler().evaluate(snap)
        assert result is not None
        assert result.use_my_position is True
        assert result.position == 42

    def test_tilt_bounds_do_not_stop_a_fixed_position_claim(self) -> None:
        """A slot may fix a position and bound the tilt at the same time."""
        snap = make_snapshot(
            custom_position_sensors=[_constraint_state(position=50, tilt_min=50)]
        )
        result = _handler(position=50).evaluate(snap)
        assert result is not None
        assert result.position == 50


class TestUseMyWithoutAPosition:
    """``use_my`` bypasses the deferral — it must not claim a phantom 0.

    Audit finding 3: a slot with a trigger, ``use_my`` on, no stored position
    and a cover with no My value fell through to the handler's position
    sentinel and silently closed the cover.
    """

    def _snap(self, *, my_position_value=None, position=None):
        return make_snapshot(
            custom_position_sensors=[
                _constraint_state(position=position, use_my=True, tilt_min=50)
            ],
            my_position_value=my_position_value,
        )

    def test_no_my_value_and_no_position_defers(self) -> None:
        """Nothing to claim → defer, rather than close the cover."""
        assert _handler(position=None).evaluate(self._snap()) is None

    def test_no_my_value_falls_back_to_a_stored_position(self) -> None:
        """Parity: with a position configured the fallback is unchanged."""
        result = _handler(position=45).evaluate(self._snap(position=45))
        assert result is not None
        assert result.position == 45

    def test_my_value_still_claims_without_a_stored_position(self) -> None:
        """The My path itself is unaffected by the missing position."""
        result = _handler(position=None).evaluate(self._snap(my_position_value=42))
        assert result is not None
        assert result.position == 42
        assert result.use_my_position is True
