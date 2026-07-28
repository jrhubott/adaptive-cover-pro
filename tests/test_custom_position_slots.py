"""Tests for CUSTOM_POSITION_SLOT_NUMBERS expansion — issue #703 (5 → 10 slots).

RED step: these tests fail before const.py is changed (slot count is 5).
GREEN step: they pass after CUSTOM_POSITION_SLOT_NUMBERS is extended to 1..10.

Also covers the per-slot axis-constraint vocabulary added by issue #943
(``position_max`` / ``tilt_min`` / ``tilt_max``).
"""

from __future__ import annotations

import custom_components.adaptive_cover_pro.const as const
from custom_components.adaptive_cover_pro.helpers import custom_position_slot_configured
from custom_components.adaptive_cover_pro.pipeline.handlers import build_handlers
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    FIELD_VALIDATORS,
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
        "position_max",
        "tilt_min",
        "tilt_max",
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


# ---------------------------------------------------------------------------
# Axis-constraint vocabulary — issue #943
# ---------------------------------------------------------------------------


class TestAxisConstraintSlotKeys:
    """Per-slot wire keys for position max / tilt min / tilt max."""

    def test_slot_1_carries_position_max_wire_key(self) -> None:
        """Slot 1 exposes the suffixed position-max wire key."""
        assert (
            const.CUSTOM_POSITION_SLOTS[1]["position_max"]
            == "custom_position_position_max_1"
        )

    def test_slot_1_carries_tilt_min_wire_key(self) -> None:
        """Slot 1 exposes the suffixed tilt-min wire key."""
        assert (
            const.CUSTOM_POSITION_SLOTS[1]["tilt_min"] == "custom_position_tilt_min_1"
        )

    def test_slot_1_carries_tilt_max_wire_key(self) -> None:
        """Slot 1 exposes the suffixed tilt-max wire key."""
        assert (
            const.CUSTOM_POSITION_SLOTS[1]["tilt_max"] == "custom_position_tilt_max_1"
        )

    def test_slot_10_constraint_wire_keys_embed_slot_number(self) -> None:
        """The constraint keys are suffixed per slot like every other sub-key."""
        keys = const.CUSTOM_POSITION_SLOTS[10]
        assert keys["position_max"] == "custom_position_position_max_10"
        assert keys["tilt_min"] == "custom_position_tilt_min_10"
        assert keys["tilt_max"] == "custom_position_tilt_max_10"

    def test_form_keys_carry_generic_constraint_keys(self) -> None:
        """The single-slot options page renders the un-suffixed generic keys."""
        assert (
            const.CUSTOM_POSITION_FORM_KEYS["position_max"]
            == "custom_position_position_max"
        )
        assert const.CUSTOM_POSITION_FORM_KEYS["tilt_min"] == "custom_position_tilt_min"
        assert const.CUSTOM_POSITION_FORM_KEYS["tilt_max"] == "custom_position_tilt_max"


class TestAxisConstraintOptionRanges:
    """OPTION_RANGES derives from the FieldSpec registry — no hand-written bounds."""

    def test_position_max_range_is_percent(self) -> None:
        """Position max shares the custom-position 0–100 percent range."""
        assert const.OPTION_RANGES["custom_position_position_max_1"] == (0, 100)

    def test_tilt_min_range_is_percent(self) -> None:
        """Tilt min shares the tilt 0–100 percent range."""
        assert const.OPTION_RANGES["custom_position_tilt_min_1"] == (0, 100)

    def test_tilt_max_range_is_percent(self) -> None:
        """Tilt max shares the tilt 0–100 percent range."""
        assert const.OPTION_RANGES["custom_position_tilt_max_3"] == (0, 100)

    def test_every_slot_has_constraint_ranges(self) -> None:
        """All 10 slots register ranges for all three constraint keys."""
        for n in const.CUSTOM_POSITION_SLOT_NUMBERS:
            keys = const.CUSTOM_POSITION_SLOTS[n]
            for sub in ("position_max", "tilt_min", "tilt_max"):
                assert keys[sub] in const.OPTION_RANGES


class TestAxisConstraintFieldValidators:
    """The set_option service must accept the new keys."""

    def test_position_max_validator_registered(self) -> None:
        """A validator exists for the position-max key."""
        assert "custom_position_position_max_1" in FIELD_VALIDATORS

    def test_tilt_min_validator_registered(self) -> None:
        """A validator exists for the tilt-min key."""
        assert "custom_position_tilt_min_1" in FIELD_VALIDATORS

    def test_tilt_max_validator_registered(self) -> None:
        """A validator exists for the tilt-max key."""
        assert "custom_position_tilt_max_1" in FIELD_VALIDATORS

    def test_position_max_validator_accepts_in_range(self) -> None:
        """A 0–100 value validates."""
        assert FIELD_VALIDATORS["custom_position_position_max_1"](60) == 60

    def test_tilt_min_validator_rejects_out_of_range(self) -> None:
        """A value above 100 is rejected."""
        import voluptuous as vol
        import pytest

        with pytest.raises(vol.Invalid):
            FIELD_VALIDATORS["custom_position_tilt_min_1"](150)


# ---------------------------------------------------------------------------
# Slot participation — a constraint-only slot is configured (issue #943)
# ---------------------------------------------------------------------------


class TestSlotConfiguredWithConstraintsOnly:
    """A slot with a trigger and only a constraint (no position) participates."""

    def test_trigger_plus_tilt_min_is_configured(self) -> None:
        """The reporter's flagship config: contact sensor → minimum tilt only."""
        keys = const.CUSTOM_POSITION_SLOTS[1]
        options = {
            keys["sensors"]: ["binary_sensor.door"],
            keys["tilt_min"]: 50,
        }
        assert custom_position_slot_configured(options, keys) is True

    def test_trigger_plus_tilt_max_is_configured(self) -> None:
        """A tilt-max-only slot participates."""
        keys = const.CUSTOM_POSITION_SLOTS[2]
        options = {
            keys["sensors"]: ["binary_sensor.door"],
            keys["tilt_max"]: 40,
        }
        assert custom_position_slot_configured(options, keys) is True

    def test_trigger_plus_position_max_is_configured(self) -> None:
        """A position-max-only slot participates."""
        keys = const.CUSTOM_POSITION_SLOTS[3]
        options = {
            keys["sensors"]: ["binary_sensor.door"],
            keys["position_max"]: 60,
        }
        assert custom_position_slot_configured(options, keys) is True

    def test_trigger_without_position_or_constraints_is_not_configured(self) -> None:
        """A bare trigger still contributes nothing — unchanged behavior."""
        keys = const.CUSTOM_POSITION_SLOTS[4]
        options = {keys["sensors"]: ["binary_sensor.door"]}
        assert custom_position_slot_configured(options, keys) is False

    def test_constraint_without_trigger_is_not_configured(self) -> None:
        """A constraint with no trigger cannot activate — unchanged gate."""
        keys = const.CUSTOM_POSITION_SLOTS[5]
        options = {keys["tilt_min"]: 50}
        assert custom_position_slot_configured(options, keys) is False

    def test_trigger_plus_position_still_configured(self) -> None:
        """Legacy exact-position slots are unaffected."""
        keys = const.CUSTOM_POSITION_SLOTS[6]
        options = {
            keys["sensors"]: ["binary_sensor.door"],
            keys["position"]: 30,
        }
        assert custom_position_slot_configured(options, keys) is True


class TestBuildHandlersConstraintOnlySlot:
    """build_handlers must tolerate a slot with no position claim."""

    def test_constraint_only_slot_builds_handler(self) -> None:
        """A tilt-min-only slot still needs its handler (it emits the trace step)."""
        options = {
            "custom_position_sensors_7": ["binary_sensor.door"],
            "custom_position_tilt_min_7": 50,
        }
        handlers = build_handlers(options)
        assert "custom_position_7" in [h.name for h in handlers]

    def test_constraint_only_slot_carries_no_position_sentinel(self) -> None:
        """No stored position must stay None, not become a phantom 0.

        Audit finding 3: the 0 sentinel *is* readable — the ``use_my`` path
        bypasses the deferral — and a phantom 0 fully closes the cover.
        """
        options = {
            "custom_position_sensors_7": ["binary_sensor.door"],
            "custom_position_tilt_min_7": 50,
        }
        handler = next(
            h for h in build_handlers(options) if h.name == "custom_position_7"
        )
        assert handler._position is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Card / sensor slot snapshot — issue #943
# ---------------------------------------------------------------------------


class TestSlotSnapshotSurfacesConstraints:
    """The companion card's slot rows carry the axis constraints."""

    @staticmethod
    def _row(options, slot=1):
        from unittest.mock import MagicMock

        from custom_components.adaptive_cover_pro.sensor import (
            _build_custom_position_slots_snapshot,
        )

        hass = MagicMock()
        hass.states.get.return_value = None
        rows = _build_custom_position_slots_snapshot(options, hass)
        return next(r for r in rows if r["slot"] == slot)

    def test_constraints_surfaced(self) -> None:
        """A configured slot reports its three constraint values."""
        row = self._row(
            {
                "custom_position_sensors_1": ["binary_sensor.door"],
                "custom_position_1": 30,
                "custom_position_position_max_1": 60,
                "custom_position_tilt_min_1": 50,
                "custom_position_tilt_max_1": 90,
            }
        )
        assert row["position_max"] == 60
        assert row["tilt_min"] == 50
        assert row["tilt_max"] == 90

    def test_absent_constraints_are_none(self) -> None:
        """A legacy slot reports None for each constraint."""
        row = self._row(
            {
                "custom_position_sensors_1": ["binary_sensor.door"],
                "custom_position_1": 30,
            }
        )
        assert row["position_max"] is None
        assert row["tilt_min"] is None
        assert row["tilt_max"] is None

    def test_constraint_only_slot_reports_null_position(self) -> None:
        """A slot with no position claim must not crash the snapshot."""
        row = self._row(
            {
                "custom_position_sensors_1": ["binary_sensor.door"],
                "custom_position_tilt_min_1": 50,
            }
        )
        assert row["position"] is None
        assert row["tilt_min"] == 50
        assert row["enabled"] is True
