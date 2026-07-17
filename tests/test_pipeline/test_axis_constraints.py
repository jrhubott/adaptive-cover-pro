"""Unit tests for the unified axis-constraint model (issue #943).

``pipeline/axis_constraints.py`` is the generalization of two single-purpose
composition passes: ``floors.py`` (position-min) and ``tilt_axis.py``
(tilt-fixed). These tests pin the model itself — gather parity with what those
two modules produce today, plus the new bounds they never supported.

The behavioral contract for the *registry composition* built on top of this
lives in ``test_floor_composition.py`` (untouched) and
``test_axis_constraint_composition.py``.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import AxisConstraintMode
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
)
from custom_components.adaptive_cover_pro.pipeline.axis_constraints import (
    AxisConstraint,
    clamp_to_bounds,
    compose_bounds,
    gather_axis_constraints,
    resolve_fixed,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    derive_axis_mode,
)

from tests.test_pipeline.conftest import make_snapshot


def _snapshot(*, sensors: list[CustomPositionSensorState], **kwargs):
    """Snapshot carrying only what the gather pass reads."""
    return make_snapshot(custom_position_sensors=sensors, **kwargs)


def _slot(
    slot: int,
    *,
    is_on: bool = True,
    position: int | None = None,
    priority: int = 77,
    min_mode: bool = False,
    use_my: bool = False,
    tilt: int | None = None,
    tilt_only: bool = False,
    position_max: int | None = None,
    tilt_min: int | None = None,
    tilt_max: int | None = None,
    sensor_name: str | None = None,
) -> CustomPositionSensorState:
    """Build a slot state. The per-axis modes derive themselves."""
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.slot{slot}",),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        tilt=tilt,
        tilt_only=tilt_only,
        sensor_name=sensor_name,
        slot=slot,
        position_max=position_max,
        tilt_min=tilt_min,
        tilt_max=tilt_max,
    )


def _on(constraints, axis, kind=None):
    """Filter constraints by axis (and optionally kind)."""
    return [
        c for c in constraints if c.axis == axis and (kind is None or c.kind is kind)
    ]


# ---------------------------------------------------------------------------
# clamp_to_bounds — the one clamp formula
# ---------------------------------------------------------------------------


class TestClampToBounds:
    """``max(min(value, high), low)`` — the single clamp used everywhere."""

    def test_value_inside_bounds_unchanged(self) -> None:
        """A value already within the bounds passes through."""
        assert clamp_to_bounds(50, 20, 80) == 50

    def test_value_below_low_raised(self) -> None:
        """Below the floor → raised to the floor."""
        assert clamp_to_bounds(10, 20, 80) == 20

    def test_value_above_high_lowered(self) -> None:
        """Above the ceiling → lowered to the ceiling."""
        assert clamp_to_bounds(90, 20, 80) == 80

    def test_no_bounds_is_identity(self) -> None:
        """Both bounds absent → the value is untouched."""
        assert clamp_to_bounds(50, None, None) == 50

    def test_only_low(self) -> None:
        """A lone floor still raises."""
        assert clamp_to_bounds(10, 40, None) == 40

    def test_only_high(self) -> None:
        """A lone ceiling still lowers."""
        assert clamp_to_bounds(90, None, 60) == 60

    def test_floor_wins_when_low_above_high(self) -> None:
        """Conflicting bounds resolve deterministically: the floor wins.

        ``max(min(v, high), low)`` applies the ceiling first and the floor
        last, so a floor above the ceiling is the value that survives.
        """
        assert clamp_to_bounds(50, 60, 40) == 60

    def test_zero_low_is_honored(self) -> None:
        """0 is a real bound, not "unset" — must not be truthiness-tested."""
        assert clamp_to_bounds(-5, 0, 100) == 0

    def test_zero_high_is_honored(self) -> None:
        """A ceiling of 0 (fully closed) clamps everything down to 0."""
        assert clamp_to_bounds(80, None, 0) == 0


# ---------------------------------------------------------------------------
# compose_bounds — max-of-mins / min-of-maxes, one formula per axis
# ---------------------------------------------------------------------------


class TestComposeBounds:
    """Composition of many constraints into one (low, high) pair."""

    def test_empty_is_unbounded(self) -> None:
        """No constraints → no bounds."""
        assert compose_bounds([], AXIS_NAME_POSITION) == (None, None)

    def test_single_min(self) -> None:
        """One floor composes to itself."""
        cs = [
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MIN, 40, None, "s", "l", 77, 1
            )
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (40, None)

    def test_two_mins_pick_max(self) -> None:
        """Issue #496's max-of-floors — now the generic rule."""
        cs = [
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MIN, 40, None, "a", "a", 77, 1
            ),
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MIN, 70, None, "b", "b", 10, 2
            ),
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (70, None)

    def test_two_maxes_pick_min(self) -> None:
        """The single mirror of max-of-mins: the most restrictive ceiling."""
        cs = [
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MAX, None, 60, "a", "a", 77, 1
            ),
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MAX, None, 30, "b", "b", 99, 2
            ),
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (None, 30)

    def test_range_contributes_both_bounds(self) -> None:
        """A RANGE constraint carries a low and a high at once."""
        cs = [
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.RANGE, 30, 70, "a", "a", 77, 1
            )
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (30, 70)

    def test_min_and_max_from_different_slots_compose(self) -> None:
        """Two slots, one bound each, compose into a range."""
        cs = [
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MIN, 30, None, "a", "a", 77, 1
            ),
            AxisConstraint(
                AXIS_NAME_POSITION, AxisConstraintMode.MAX, None, 70, "b", "b", 77, 2
            ),
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (30, 70)

    def test_other_axis_ignored(self) -> None:
        """Composition is per-axis — a tilt bound never leaks into position."""
        cs = [
            AxisConstraint(
                AXIS_NAME_TILT, AxisConstraintMode.MIN, 50, None, "a", "a", 77, 1
            )
        ]
        assert compose_bounds(cs, AXIS_NAME_POSITION) == (None, None)
        assert compose_bounds(cs, AXIS_NAME_TILT) == (50, None)

    def test_fixed_contributes_no_bounds(self) -> None:
        """FIXED is resolved by priority, not composed into bounds."""
        cs = [
            AxisConstraint(
                AXIS_NAME_TILT, AxisConstraintMode.FIXED, 50, 50, "a", "a", 77, 1
            )
        ]
        assert compose_bounds(cs, AXIS_NAME_TILT) == (None, None)

    def test_tilt_axis_uses_the_same_formula(self) -> None:
        """The rule is per-kind, not per-axis: tilt mins also take the max."""
        cs = [
            AxisConstraint(
                AXIS_NAME_TILT, AxisConstraintMode.MIN, 30, None, "a", "a", 77, 1
            ),
            AxisConstraint(
                AXIS_NAME_TILT, AxisConstraintMode.MIN, 50, None, "b", "b", 77, 2
            ),
        ]
        assert compose_bounds(cs, AXIS_NAME_TILT) == (50, None)


# ---------------------------------------------------------------------------
# resolve_fixed — highest-priority wins (today's resolve_tilt_axis rule)
# ---------------------------------------------------------------------------


class TestResolveFixed:
    """FIXED resolution stays highest-priority-wins, first-in-order on a tie."""

    def test_none_when_no_fixed(self) -> None:
        """No FIXED constraint → None."""
        assert resolve_fixed([], AXIS_NAME_TILT) is None

    def test_single_fixed(self) -> None:
        """One FIXED constraint wins by default."""
        c = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.FIXED, 50, 50, "a", "a", 77, 1
        )
        assert resolve_fixed([c], AXIS_NAME_TILT) is c

    def test_highest_priority_wins(self) -> None:
        """Priority 90 beats priority 77 — parity with resolve_tilt_axis."""
        low = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.FIXED, 20, 20, "a", "a", 77, 1
        )
        high = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.FIXED, 80, 80, "b", "b", 90, 2
        )
        assert resolve_fixed([low, high], AXIS_NAME_TILT) is high

    def test_tie_resolves_to_first_in_order(self) -> None:
        """Equal priority → the first in snapshot order, as today."""
        first = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.FIXED, 20, 20, "a", "a", 77, 1
        )
        second = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.FIXED, 80, 80, "b", "b", 77, 2
        )
        assert resolve_fixed([first, second], AXIS_NAME_TILT) is first

    def test_min_constraints_are_not_fixed_winners(self) -> None:
        """Only FIXED participates in fixed resolution."""
        c = AxisConstraint(
            AXIS_NAME_TILT, AxisConstraintMode.MIN, 50, None, "a", "a", 99, 1
        )
        assert resolve_fixed([c], AXIS_NAME_TILT) is None


# ---------------------------------------------------------------------------
# gather_axis_constraints — parity with floors.py / tilt_axis.py + new bounds
# ---------------------------------------------------------------------------


class TestGatherPositionMinParity:
    """Position MIN constraints must reproduce gather_active_floors exactly."""

    def test_min_mode_slot_emits_position_min(self) -> None:
        """A min_mode slot contributes a position floor."""
        snap = _snapshot(sensors=[_slot(1, position=60, min_mode=True)])
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)
        assert len(cs) == 1
        assert cs[0].kind is AxisConstraintMode.MIN
        assert cs[0].low == 60

    def test_min_mode_slot_carries_source_label_priority_slot(self) -> None:
        """Trace-facing metadata matches what FloorClampInfo carries today."""
        snap = _snapshot(
            sensors=[
                _slot(3, position=60, min_mode=True, priority=42, sensor_name="Desk")
            ]
        )
        c = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)[0]
        assert c.source == "custom_position_3"
        assert c.label == "Desk"
        assert c.priority == 42
        assert c.slot == 3

    def test_inactive_slot_contributes_nothing(self) -> None:
        """An off trigger emits no constraint."""
        snap = _snapshot(sensors=[_slot(1, position=60, min_mode=True, is_on=False)])
        assert gather_axis_constraints(snap) == []

    def test_use_my_floor_excluded(self) -> None:
        """The My path is hardware-pinned — it never contributes a floor."""
        snap = _snapshot(sensors=[_slot(1, position=60, min_mode=True, use_my=True)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_POSITION) == []

    def test_exact_position_slot_contributes_no_constraint(self) -> None:
        """A FIXED-position slot claims via its handler, not via composition."""
        snap = _snapshot(sensors=[_slot(1, position=60)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_POSITION) == []

    def test_weather_min_mode_emits_position_min(self) -> None:
        """The weather override floor rides the same model."""
        snap = _snapshot(
            sensors=[],
            weather_override_active=True,
            weather_override_min_mode=True,
            weather_override_position=70,
        )
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)
        assert len(cs) == 1
        assert cs[0].source == "weather"
        assert cs[0].label == "weather override"
        assert cs[0].low == 70

    def test_weather_without_min_mode_emits_nothing(self) -> None:
        """An exact weather override claims via its handler."""
        snap = _snapshot(
            sensors=[],
            weather_override_active=True,
            weather_override_min_mode=False,
            weather_override_position=70,
        )
        assert gather_axis_constraints(snap) == []

    def test_custom_slots_precede_weather(self) -> None:
        """Order is custom slots (snapshot order) then weather — as today."""
        snap = _snapshot(
            sensors=[_slot(1, position=60, min_mode=True)],
            weather_override_active=True,
            weather_override_min_mode=True,
            weather_override_position=70,
        )
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)
        assert [c.source for c in cs] == ["custom_position_1", "weather"]


class TestGatherTiltFixedParity:
    """Tilt FIXED constraints must reproduce gather_tilt_only_contributions."""

    def test_tilt_only_slot_emits_tilt_fixed(self) -> None:
        """A tilt-only slot contributes a FIXED tilt claim."""
        snap = _snapshot(sensors=[_slot(1, tilt=50, tilt_only=True)])
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_TILT)
        assert len(cs) == 1
        assert cs[0].kind is AxisConstraintMode.FIXED
        assert cs[0].low == 50
        assert cs[0].high == 50

    def test_tilt_only_without_tilt_emits_nothing(self) -> None:
        """A tilt-only slot with no slat angle contributes nothing — parity."""
        snap = _snapshot(sensors=[_slot(1, position=30, tilt_only=True)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_TILT) == []

    def test_tilt_only_slot_makes_no_position_claim(self) -> None:
        """tilt_only defers the position axis entirely."""
        snap = _snapshot(sensors=[_slot(1, position=30, tilt=50, tilt_only=True)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_POSITION) == []

    def test_tilt_fixed_carries_slot_metadata(self) -> None:
        """Source/label/slot mirror TiltAxisContribution."""
        snap = _snapshot(
            sensors=[_slot(2, tilt=50, tilt_only=True, sensor_name="Blind")]
        )
        c = _on(gather_axis_constraints(snap), AXIS_NAME_TILT)[0]
        assert c.source == "custom_position_2"
        assert c.label == "Blind"
        assert c.slot == 2


class TestGatherNewConstraints:
    """The constraints #943 adds — none of which existed before."""

    def test_position_max_emits_position_max(self) -> None:
        """A position ceiling."""
        snap = _snapshot(sensors=[_slot(1, position_max=60)])
        c = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)[0]
        assert c.kind is AxisConstraintMode.MAX
        assert c.low is None
        assert c.high == 60

    def test_position_range_is_one_constraint_with_both_bounds(self) -> None:
        """min_mode + position_max is a single RANGE constraint."""
        snap = _snapshot(
            sensors=[_slot(1, position=30, min_mode=True, position_max=70)]
        )
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_POSITION)
        assert len(cs) == 1
        assert cs[0].kind is AxisConstraintMode.RANGE
        assert (cs[0].low, cs[0].high) == (30, 70)

    def test_tilt_min_emits_tilt_min(self) -> None:
        """The reporter's ask: a tilt floor."""
        snap = _snapshot(sensors=[_slot(1, position=30, tilt_min=50)])
        c = _on(gather_axis_constraints(snap), AXIS_NAME_TILT)[0]
        assert c.kind is AxisConstraintMode.MIN
        assert c.low == 50
        assert c.high is None

    def test_tilt_max_emits_tilt_max(self) -> None:
        """A tilt ceiling."""
        snap = _snapshot(sensors=[_slot(1, position=30, tilt_max=60)])
        c = _on(gather_axis_constraints(snap), AXIS_NAME_TILT)[0]
        assert c.kind is AxisConstraintMode.MAX
        assert c.high == 60

    def test_tilt_range_is_one_constraint_with_both_bounds(self) -> None:
        """Both tilt bounds compose into a single RANGE constraint."""
        snap = _snapshot(sensors=[_slot(1, position=30, tilt_min=40, tilt_max=80)])
        cs = _on(gather_axis_constraints(snap), AXIS_NAME_TILT)
        assert len(cs) == 1
        assert cs[0].kind is AxisConstraintMode.RANGE
        assert (cs[0].low, cs[0].high) == (40, 80)

    def test_one_slot_can_constrain_both_axes(self) -> None:
        """A slot with a position floor and a tilt floor emits two constraints."""
        snap = _snapshot(sensors=[_slot(1, position=30, min_mode=True, tilt_min=50)])
        cs = gather_axis_constraints(snap)
        assert len(cs) == 2
        assert {c.axis for c in cs} == {AXIS_NAME_POSITION, AXIS_NAME_TILT}

    def test_tilt_bound_slot_is_not_a_position_claim(self) -> None:
        """A trigger + tilt_min slot must not force a position."""
        snap = _snapshot(sensors=[_slot(1, tilt_min=50)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_POSITION) == []

    def test_constraints_use_the_shared_axis_vocabulary(self) -> None:
        """Axis keys come from cover_types.base — no per-type strings."""
        snap = _snapshot(sensors=[_slot(1, position=30, min_mode=True, tilt_min=50)])
        for c in gather_axis_constraints(snap):
            assert c.axis in (AXIS_NAME_POSITION, AXIS_NAME_TILT)


class TestDeriveAxisMode:
    """Precedence between an exact value and the bounds on one axis.

    FIXED outranks MAX (audit finding 5): a slot that names a position keeps
    its claim, and a lone ``position_max`` is ignored unless the slot is also
    in minimum mode. This mirrors the tilt axis, where a FIXED (``tilt_only``)
    claim has always won over the bounds on the same axis.
    """

    def test_low_and_high_is_range(self) -> None:
        """Both bounds → RANGE."""
        assert derive_axis_mode(fixed=None, low=30, high=70) is AxisConstraintMode.RANGE

    def test_low_only_is_min(self) -> None:
        """A lone floor → MIN."""
        assert derive_axis_mode(fixed=None, low=30, high=None) is AxisConstraintMode.MIN

    def test_high_only_is_max(self) -> None:
        """A lone ceiling with no exact value → MAX."""
        assert derive_axis_mode(fixed=None, low=None, high=70) is AxisConstraintMode.MAX

    def test_fixed_only_is_fixed(self) -> None:
        """An exact value with no bounds → FIXED."""
        assert derive_axis_mode(fixed=50, low=None, high=None) is (
            AxisConstraintMode.FIXED
        )

    def test_fixed_outranks_high(self) -> None:
        """An exact value beats a ceiling — the stored value is the target."""
        assert derive_axis_mode(fixed=70, low=None, high=50) is (
            AxisConstraintMode.FIXED
        )

    def test_low_outranks_fixed(self) -> None:
        """A floor still wins: the stored position *is* the floor (min_mode)."""
        assert derive_axis_mode(fixed=50, low=30, high=None) is AxisConstraintMode.MIN

    def test_nothing_is_none(self) -> None:
        """No claim at all → NONE."""
        assert derive_axis_mode(fixed=None, low=None, high=None) is (
            AxisConstraintMode.NONE
        )

    def test_zero_fixed_is_a_claim(self) -> None:
        """0 is a real value, not "unset"."""
        assert derive_axis_mode(fixed=0, low=None, high=None) is (
            AxisConstraintMode.FIXED
        )


class TestPositionModeFixedNormalizesTheCeiling:
    """A FIXED position slot contributes no ceiling (audit finding 5)."""

    def test_position_with_a_ceiling_derives_fixed(self) -> None:
        """Position 70 + position_max 50, no min_mode → FIXED."""
        state = _slot(1, position=70, position_max=50)
        assert state.position_mode is AxisConstraintMode.FIXED

    def test_fixed_position_slot_emits_no_position_constraint(self) -> None:
        """The ceiling is normalized off — nothing composes it onto the winner."""
        snap = _snapshot(sensors=[_slot(1, position=70, position_max=50)])
        assert _on(gather_axis_constraints(snap), AXIS_NAME_POSITION) == []

    def test_min_mode_with_a_ceiling_still_derives_range(self) -> None:
        """The RANGE cell is untouched — a ceiling needs a floor to apply."""
        state = _slot(1, position=30, min_mode=True, position_max=70)
        assert state.position_mode is AxisConstraintMode.RANGE

    def test_ceiling_without_a_position_still_derives_max(self) -> None:
        """A constraint-only ceiling slot is unaffected."""
        assert _slot(1, position_max=60).position_mode is AxisConstraintMode.MAX


class TestGatherIsPure:
    """The gather pass is a pure read of the snapshot."""

    def test_returns_frozen_constraints(self) -> None:
        """AxisConstraint is immutable."""
        snap = _snapshot(sensors=[_slot(1, position=60, min_mode=True)])
        c = gather_axis_constraints(snap)[0]
        with pytest.raises(AttributeError):
            c.low = 99  # type: ignore[misc]
