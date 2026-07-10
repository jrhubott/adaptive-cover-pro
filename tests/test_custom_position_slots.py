"""Tests for CUSTOM_POSITION_SLOT_NUMBERS expansion — issue #703 (5 → 10 slots).

RED step: these tests fail before const.py is changed (slot count is 5).
GREEN step: they pass after CUSTOM_POSITION_SLOT_NUMBERS is extended to 1..10.
"""

from __future__ import annotations

import custom_components.adaptive_cover_pro.const as const
from custom_components.adaptive_cover_pro.pipeline.handlers import build_handlers
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)

# Full set of sub-keys that every slot dict must carry.
_REQUIRED_KEYS = frozenset(
    {
        "name",
        "sensor",
        "sensors",
        "template",
        "template_mode",
        "position",
        "priority",
        "min_mode",
        "use_my",
        "tilt",
        "tilt_only",
        "enabled",
    }
)


# ---------------------------------------------------------------------------
# Slot count / constant structure
# ---------------------------------------------------------------------------


class TestSlotCount:
    """CUSTOM_POSITION_SLOT_NUMBERS must contain exactly 10 entries (1–10)."""

    def test_slot_numbers_count_is_10(self) -> None:
        """Issue #703: slot count raised from 5 to 10."""
        assert len(const.CUSTOM_POSITION_SLOT_NUMBERS) == 10

    def test_slot_numbers_contiguous_1_to_10(self) -> None:
        """Slots must be the exact range 1–10, no gaps."""
        assert set(const.CUSTOM_POSITION_SLOT_NUMBERS) == set(range(1, 11))

    def test_custom_position_slots_dict_has_keys_1_to_10(self) -> None:
        """CUSTOM_POSITION_SLOTS derived dict must cover all 10 slots."""
        assert set(const.CUSTOM_POSITION_SLOTS.keys()) == set(range(1, 11))

    def test_slot_9_has_full_key_set(self) -> None:
        """Slot 9 must have every expected sub-key."""
        assert set(const.CUSTOM_POSITION_SLOTS[9].keys()) == _REQUIRED_KEYS

    def test_slot_10_has_full_key_set(self) -> None:
        """Slot 10 must have every expected sub-key."""
        assert set(const.CUSTOM_POSITION_SLOTS[10].keys()) == _REQUIRED_KEYS

    def test_slot_9_wire_keys_contain_slot_number(self) -> None:
        """Wire keys for slot 9 must embed '_9'."""
        keys = const.CUSTOM_POSITION_SLOTS[9]
        assert keys["sensors"] == "custom_position_sensors_9"
        assert keys["position"] == "custom_position_9"
        assert keys["priority"] == "custom_position_priority_9"

    def test_slot_10_wire_keys_contain_slot_number(self) -> None:
        """Wire keys for slot 10 must embed '_10'."""
        keys = const.CUSTOM_POSITION_SLOTS[10]
        assert keys["sensors"] == "custom_position_sensors_10"
        assert keys["position"] == "custom_position_10"
        assert keys["priority"] == "custom_position_priority_10"


# ---------------------------------------------------------------------------
# build_handlers produces handlers for new slots
# ---------------------------------------------------------------------------


class TestBuildHandlersNewSlots:
    """build_handlers must create handlers for slots 6–10 when configured."""

    def test_slot_9_handler_created_when_configured(self) -> None:
        """Configuring slot 9 yields a handler named custom_position_9."""
        options = {
            "custom_position_sensors_9": ["binary_sensor.away"],
            "custom_position_9": 25,
            "custom_position_priority_9": 77,
        }
        handlers = build_handlers(options)
        names = [h.name for h in handlers]
        assert "custom_position_9" in names

    def test_slot_9_handler_is_custom_position_handler(self) -> None:
        """The handler produced for slot 9 is a CustomPositionHandler instance."""
        options = {
            "custom_position_sensors_9": ["binary_sensor.scene"],
            "custom_position_9": 60,
        }
        handlers = build_handlers(options)
        cp_handlers = [h for h in handlers if isinstance(h, CustomPositionHandler)]
        slots = {h._slot for h in cp_handlers}
        assert 9 in slots

    def test_slot_9_handler_has_correct_position(self) -> None:
        """The slot-9 handler carries the configured position value."""
        options = {
            "custom_position_sensors_9": ["binary_sensor.wind"],
            "custom_position_9": 42,
        }
        handlers = build_handlers(options)
        cp9 = next(
            (
                h
                for h in handlers
                if isinstance(h, CustomPositionHandler) and h._slot == 9
            ),
            None,
        )
        assert cp9 is not None
        assert cp9._position == 42

    def test_slot_10_handler_created_when_configured(self) -> None:
        """Configuring slot 10 yields a handler named custom_position_10."""
        options = {
            "custom_position_sensors_10": ["binary_sensor.cold"],
            "custom_position_10": 80,
        }
        handlers = build_handlers(options)
        names = [h.name for h in handlers]
        assert "custom_position_10" in names

    def test_slot_5_still_works_after_slot_count_increase(self) -> None:
        """Adding slots 6–10 must not break the existing slot 5."""
        options = {
            "custom_position_sensors_5": ["binary_sensor.rain"],
            "custom_position_5": 90,
            "custom_position_priority_5": 100,
        }
        handlers = build_handlers(options)
        names = [h.name for h in handlers]
        assert "custom_position_5" in names

    def test_slots_6_to_10_unconfigured_produce_no_handlers(self) -> None:
        """With only slot 1 configured, slots 6–10 produce no handlers."""
        options = {
            "custom_position_sensors_1": ["binary_sensor.morning"],
            "custom_position_1": 50,
        }
        handlers = build_handlers(options)
        cp_handlers = [h for h in handlers if isinstance(h, CustomPositionHandler)]
        slots = {h._slot for h in cp_handlers}
        assert slots == {1}
        assert not any(s > 5 for s in slots)
