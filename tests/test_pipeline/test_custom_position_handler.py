"""Tests for CustomPositionHandler (per-instance model)."""

from __future__ import annotations


from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)

from .conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTITY = "binary_sensor.scene_a"
_DEFAULT_PRIORITY = 77


def _handler(
    slot: int = 1,
    entity_id: str = _ENTITY,
    position: int = 50,
    priority: int = _DEFAULT_PRIORITY,
) -> CustomPositionHandler:
    """Create a CustomPositionHandler with sensible defaults."""
    return CustomPositionHandler(
        slot=slot,
        entity_id=entity_id,
        position=position,
        priority=priority,
    )


def _snapshot_with(
    entity_id: str, is_on: bool, position: int = 50, priority: int = _DEFAULT_PRIORITY
):
    """Build a snapshot with a single custom position sensor entry."""
    return make_snapshot(
        custom_position_sensors=[
            _make_state(entity_id, is_on, position, priority, False, False)
        ]
    )


def _make_state(
    entity_id: str,
    is_on: bool,
    position: int,
    priority: int,
    min_mode: bool,
    use_my: bool,
) -> CustomPositionSensorState:
    """Compact constructor for test sensor states."""
    return CustomPositionSensorState(
        entity_id=entity_id,
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
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
        assert _handler(slot=4).name == "custom_position_4"

    def test_default_priority(self) -> None:
        """Default priority must be 77."""
        assert _handler().priority == _DEFAULT_PRIORITY

    def test_custom_priority_stored(self) -> None:
        """Priority passed to constructor is stored correctly."""
        assert _handler(priority=95).priority == 95
        assert _handler(priority=35).priority == 35
        assert _handler(priority=1).priority == 1

    def test_priority_range_high(self) -> None:
        """Priority 99 is accepted (above all built-in handlers)."""
        h = _handler(priority=99)
        assert h.priority == 99

    def test_priority_range_low(self) -> None:
        """Priority 1 is accepted (just above default handler 0)."""
        h = _handler(priority=1)
        assert h.priority == 1


# ---------------------------------------------------------------------------
# Evaluate — sensor not in snapshot
# ---------------------------------------------------------------------------


class TestEvaluateNoMatchingEntity:
    """Handler passes through when its entity_id is not in the snapshot."""

    def test_returns_none_when_empty_list(self) -> None:
        snapshot = make_snapshot(custom_position_sensors=[])
        assert _handler().evaluate(snapshot) is None

    def test_returns_none_when_entity_absent(self) -> None:
        """Different entity_id in snapshot — handler's sensor not present."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                _make_state("binary_sensor.other", True, 50, 77, False, False)
            ]
        )
        assert _handler(entity_id=_ENTITY).evaluate(snapshot) is None

    def test_describe_skip_mentions_slot_and_entity(self) -> None:
        snapshot = make_snapshot(custom_position_sensors=[])
        skip = _handler(slot=2, entity_id="binary_sensor.blackout").describe_skip(
            snapshot
        )
        assert "#2" in skip
        assert "binary_sensor.blackout" in skip


# ---------------------------------------------------------------------------
# Evaluate — sensor present but off
# ---------------------------------------------------------------------------


class TestEvaluateSensorOff:
    """Handler passes through when its sensor is present but off."""

    def test_returns_none_when_off(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=False)
        assert _handler(entity_id=_ENTITY).evaluate(snapshot) is None


# ---------------------------------------------------------------------------
# Evaluate — sensor present and on
# ---------------------------------------------------------------------------


class TestEvaluateSensorOn:
    """Handler returns the configured position when its sensor is on."""

    def test_returns_configured_position(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=45)
        result = _handler(entity_id=_ENTITY, position=45).evaluate(snapshot)
        assert result is not None
        assert result.position == 45

    def test_control_method_is_custom_position(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=30)
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snapshot)
        assert result is not None
        assert result.control_method == ControlMethod.CUSTOM_POSITION

    def test_reason_contains_slot_entity_and_position(self) -> None:
        snapshot = _snapshot_with("binary_sensor.morning", is_on=True, position=70)
        result = _handler(
            slot=2, entity_id="binary_sensor.morning", position=70
        ).evaluate(snapshot)
        assert result is not None
        assert "#2" in result.reason
        assert "binary_sensor.morning" in result.reason
        assert "70%" in result.reason

    def test_position_zero_valid(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=0)
        result = _handler(entity_id=_ENTITY, position=0).evaluate(snapshot)
        assert result is not None
        assert result.position == 0

    def test_position_one_hundred_valid(self) -> None:
        snapshot = _snapshot_with(_ENTITY, is_on=True, position=100)
        result = _handler(entity_id=_ENTITY, position=100).evaluate(snapshot)
        assert result is not None
        assert result.position == 100


# ---------------------------------------------------------------------------
# Per-instance isolation — each handler only checks its own sensor
# ---------------------------------------------------------------------------


class TestPerInstanceIsolation:
    """Each handler instance only evaluates its own entity_id."""

    def test_handler_ignores_other_sensors(self) -> None:
        """Handler for entity A is not triggered by entity B being on."""
        sensors = [
            _make_state("binary_sensor.slot1", False, 30, 77, False, False),
            _make_state("binary_sensor.slot2", True, 70, 77, False, False),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)

        h1 = _handler(slot=1, entity_id="binary_sensor.slot1", position=30)
        h2 = _handler(slot=2, entity_id="binary_sensor.slot2", position=70)

        # h1 should NOT fire even though slot2 is on
        assert h1.evaluate(snapshot) is None
        # h2 SHOULD fire
        result = h2.evaluate(snapshot)
        assert result is not None
        assert result.position == 70

    def test_both_on_both_handlers_fire(self) -> None:
        """When both sensors are on, both handlers independently return results."""
        sensors = [
            _make_state("binary_sensor.slot1", True, 30, 95, False, False),
            _make_state("binary_sensor.slot2", True, 70, 60, False, False),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)

        h1 = _handler(slot=1, entity_id="binary_sensor.slot1", position=30, priority=95)
        h2 = _handler(slot=2, entity_id="binary_sensor.slot2", position=70, priority=60)

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
        result = _handler(entity_id=_ENTITY, position=55).evaluate(snapshot)
        assert result is not None
        assert result.raw_calculated_position is not None


# ---------------------------------------------------------------------------
# Minimum position mode
# ---------------------------------------------------------------------------


class TestMinimumPositionMode:
    """Tests for CustomPositionHandler minimum position mode."""

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
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_higher_uses_calculated(self) -> None:
        """With min_mode on, if calculated position > floor, use calculated."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=50.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert result.position == 50

    def test_min_mode_on_calculated_lower_uses_floor(self) -> None:
        """With min_mode on, if calculated position < floor, use the floor."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=10.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_equal_uses_floor(self) -> None:
        """With min_mode on, if calculated equals floor, position equals floor."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=30.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_reason_mentions_minimum_mode(self) -> None:
        """With min_mode on, reason string mentions minimum mode."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=50.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert "minimum mode" in result.reason

    def test_min_mode_off_reason_no_minimum_mode_mention(self) -> None:
        """With min_mode off, reason string does not mention minimum mode."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=False, calculate_percentage_return=50.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert "minimum mode" not in result.reason

    def test_min_mode_control_method_still_custom_position(self) -> None:
        """ControlMethod remains CUSTOM_POSITION regardless of min_mode."""
        snap = self._snapshot_min_mode(
            position=30, min_mode=True, calculate_percentage_return=70.0
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CUSTOM_POSITION


# ---------------------------------------------------------------------------
# bypass_auto_control — custom positions always bypass delta gates
# ---------------------------------------------------------------------------


class TestBypassAutoControl:
    """CustomPositionHandler must set bypass_auto_control=True on every active result."""

    def test_bypass_flag_set_normal_path(self) -> None:
        """Normal position path sets bypass_auto_control=True."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, False)]
        )
        result = _handler(entity_id=_ENTITY, position=50).evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is True

    def test_bypass_flag_set_min_mode_path(self) -> None:
        """Min-mode path sets bypass_auto_control=True."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 30, 77, True, False)],
            calculate_percentage_return=50.0,
        )
        result = _handler(entity_id=_ENTITY, position=30).evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is True

    def test_bypass_flag_set_use_my_path(self) -> None:
        """Use-My path sets bypass_auto_control=True."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, True)],
            my_position_value=30,
        )
        result = _handler(entity_id=_ENTITY, position=50).evaluate(snapshot)
        assert result is not None
        assert result.use_my_position is True
        assert result.bypass_auto_control is True

    def test_reason_includes_bypass_text_normal_path(self) -> None:
        """Reason string includes '[bypasses automatic control]' on normal path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, False)]
        )
        result = _handler(entity_id=_ENTITY, position=50).evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_reason_includes_bypass_text_use_my_path(self) -> None:
        """Reason string includes '[bypasses automatic control]' on use-My path."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, True, 50, 77, False, True)],
            my_position_value=30,
        )
        result = _handler(entity_id=_ENTITY, position=50).evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_no_bypass_when_sensor_off(self) -> None:
        """Handler returns None (not a bypass result) when sensor is off."""
        snapshot = make_snapshot(
            custom_position_sensors=[_make_state(_ENTITY, False, 50, 77, False, False)]
        )
        result = _handler(entity_id=_ENTITY, position=50).evaluate(snapshot)
        assert result is None
