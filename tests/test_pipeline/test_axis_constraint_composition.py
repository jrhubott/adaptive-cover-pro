"""Registry composition of the new axis constraints (issue #943).

The *existing* composition contract — position floors (#463/#496) and their
interaction with ``held_position`` (#534/#809), plus the tilt-only overlay
(#514) — is pinned by ``test_floor_composition.py`` and
``test_tilt_only_contribution.py``, which this change must leave untouched.

This file pins what #943 adds on top: position ceilings, tilt bounds, and the
way they compose with the floors that were already there.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    ControlMethod,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.pipeline.handler import OverrideHandler
from custom_components.adaptive_cover_pro.pipeline.handlers import DefaultHandler
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)

from tests.test_pipeline.conftest import make_snapshot


class _StubWinner(OverrideHandler):
    """A handler that always wins with a fixed position/tilt."""

    name = "stub_winner"

    def __init__(
        self,
        position: int = 50,
        *,
        tilt: int | None = None,
        priority: int = 40,
        held_position: int | None = None,
        skip_command: bool = False,
    ) -> None:
        self._position = position
        self._tilt = tilt
        self.priority = priority
        self._held = held_position
        self._skip = skip_command

    def evaluate(self, snapshot) -> PipelineResult:  # noqa: ARG002
        return PipelineResult(
            position=self._position,
            tilt=self._tilt,
            control_method=ControlMethod.SOLAR,
            reason="stub",
            held_position=self._held,
            skip_command=self._skip,
        )

    def describe_skip(self, snapshot):  # noqa: ARG002
        return "stub skip"


def _slot(
    slot: int,
    *,
    is_on: bool = True,
    position: int | None = None,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
    min_mode: bool = False,
    tilt: int | None = None,
    tilt_only: bool = False,
    position_max: int | None = None,
    tilt_min: int | None = None,
    tilt_max: int | None = None,
    sensor_name: str | None = None,
) -> CustomPositionSensorState:
    eid = f"binary_sensor.slot{slot}"
    return CustomPositionSensorState(
        entity_ids=(eid,),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=False,
        tilt=tilt,
        tilt_only=tilt_only,
        sensor_name=sensor_name,
        slot=slot,
        active_entity_ids=(eid,) if is_on else (),
        position_max=position_max,
        tilt_min=tilt_min,
        tilt_max=tilt_max,
    )


def _evaluate(sensors, *, winner: _StubWinner | None = None):
    """Run a registry with a stub winner plus one handler per slot."""
    win = winner or _StubWinner()
    handlers: list[OverrideHandler] = [win, DefaultHandler()]
    for s in sensors:
        handlers.append(
            CustomPositionHandler(
                slot=s.slot,
                position=s.position if s.position is not None else 0,
                priority=s.priority,
                tilt=s.tilt,
            )
        )
    snap = make_snapshot(
        cover=MagicMock(), custom_position_sensors=sensors, default_position=0
    )
    return PipelineRegistry(handlers).evaluate(snap)


def _codes(result) -> list:
    return [
        s.reason_payload.code
        for s in result.decision_trace
        if s.reason_payload is not None
    ]


def _step(result, code):
    return next(
        s
        for s in result.decision_trace
        if s.reason_payload is not None and s.reason_payload.code is code
    )


# ---------------------------------------------------------------------------
# Position ceiling
# ---------------------------------------------------------------------------


class TestPositionCeiling:
    """A position_max slot clamps the pipeline winner down."""

    def test_ceiling_lowers_winner(self) -> None:
        """Winner at 80 with a ceiling of 60 lands on 60."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(80))
        assert res.position == 60

    def test_ceiling_sets_floor_clamp_applied(self) -> None:
        """A ceiling clamp is a user-configured cover-space value (#469)."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(80))
        assert res.floor_clamp_applied is True

    def test_ceiling_clears_skip_command(self) -> None:
        """A clamp must reach the cover even when the winner was a hold."""
        res = _evaluate(
            [_slot(1, position_max=60)],
            winner=_StubWinner(80, skip_command=True),
        )
        assert res.skip_command is False

    def test_ceiling_emits_lowered_trace_step(self) -> None:
        """The clamp is visible in the decision trace."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(80))
        assert ReasonCode.REGISTRY_CEILING_LOWERED in _codes(res)

    def test_lowered_step_carries_payload_params(self) -> None:
        """The trace step names where it came from and where it went."""
        res = _evaluate(
            [_slot(1, position_max=60, sensor_name="Awning")],
            winner=_StubWinner(80),
        )
        params = _step(res, ReasonCode.REGISTRY_CEILING_LOWERED).reason_payload.params
        assert params["from_pos"] == 80
        assert params["to_pos"] == 60
        assert params["label"] == "Awning"

    def test_winner_below_ceiling_is_untouched(self) -> None:
        """An inert ceiling leaves the winner's own position exactly as-is."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(40))
        assert res.position == 40
        assert res.floor_clamp_applied is False

    def test_inert_ceiling_emits_inactive_step(self) -> None:
        """An inert ceiling still explains itself rather than a stale skip."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(40))
        assert ReasonCode.REGISTRY_CEILING_INACTIVE in _codes(res)

    def test_two_ceilings_pick_the_lowest(self) -> None:
        """min-of-maxes — the mirror of #496's max-of-floors."""
        res = _evaluate(
            [_slot(1, position_max=60), _slot(2, position_max=30)],
            winner=_StubWinner(80),
        )
        assert res.position == 30

    def test_inactive_slot_ceiling_ignored(self) -> None:
        """An off trigger constrains nothing."""
        res = _evaluate(
            [_slot(1, position_max=60, is_on=False)], winner=_StubWinner(80)
        )
        assert res.position == 80

    def test_ceiling_replaces_the_slot_skip_step(self) -> None:
        """The slot's deferral skip step is replaced, not left stale."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(80))
        assert ReasonCode.SKIP_CUSTOM_NOT_ACTIVE not in _codes(res)


class TestCeilingVersusHeldPosition:
    """A ceiling composes against where the cover actually ends up (#534)."""

    def test_ceiling_lowers_held_position(self) -> None:
        """Held at 80 with a ceiling of 60 → lowered to 60."""
        res = _evaluate(
            [_slot(1, position_max=60)],
            winner=_StubWinner(80, held_position=80, skip_command=True),
        )
        assert res.position == 60
        assert res.skip_command is False

    def test_ceiling_above_held_is_inert(self) -> None:
        """Held at 50 under a ceiling of 60 → nothing to do."""
        res = _evaluate(
            [_slot(1, position_max=60)],
            winner=_StubWinner(50, held_position=50, skip_command=True),
        )
        assert res.floor_clamp_applied is False
        assert ReasonCode.REGISTRY_CEILING_INACTIVE in _codes(res)


class TestFloorBeatsCeiling:
    """Conflicting bounds resolve deterministically — the floor wins."""

    def test_floor_60_beats_ceiling_40(self) -> None:
        """A floor above a ceiling wins: protection is not silently reduced."""
        res = _evaluate(
            [_slot(1, position=60, min_mode=True), _slot(2, position_max=40)],
            winner=_StubWinner(50),
        )
        assert res.position == 60

    def test_conflict_reports_a_floor_raise_not_a_ceiling_lower(self) -> None:
        """The trace attributes the move to the floor that produced it."""
        res = _evaluate(
            [_slot(1, position=60, min_mode=True), _slot(2, position_max=40)],
            winner=_StubWinner(50),
        )
        codes = _codes(res)
        assert ReasonCode.REGISTRY_FLOOR_RAISED in codes
        assert ReasonCode.REGISTRY_CEILING_LOWERED not in codes


# ---------------------------------------------------------------------------
# Tilt bounds
# ---------------------------------------------------------------------------


class TestTiltBounds:
    """Tilt MIN/MAX clamp a tilt the winner already set — #514's inverse."""

    def test_tilt_min_raises_winner_tilt(self) -> None:
        """The reporter's case: calculated 20 with tilt_min 50 → 50."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50, tilt=20))
        assert res.tilt == 50

    def test_tilt_above_min_is_untouched(self) -> None:
        """The reporter's acceptance pair: calculated 75 stays 75."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50, tilt=75))
        assert res.tilt == 75

    def test_tilt_max_lowers_winner_tilt(self) -> None:
        """A tilt ceiling clamps down."""
        res = _evaluate([_slot(1, tilt_max=60)], winner=_StubWinner(50, tilt=80))
        assert res.tilt == 60

    def test_tilt_clamp_emits_trace_step(self) -> None:
        """The tilt clamp is visible in the trace."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50, tilt=20))
        assert ReasonCode.REGISTRY_TILT_CLAMPED in _codes(res)

    def test_tilt_clamp_clears_skip_command(self) -> None:
        """A tilt clamp must reach the cover."""
        res = _evaluate(
            [_slot(1, tilt_min=50)],
            winner=_StubWinner(50, tilt=20, skip_command=True),
        )
        assert res.skip_command is False

    def test_two_tilt_mins_pick_the_max(self) -> None:
        """max-of-mins applies on the tilt axis too — the rule is per-kind."""
        res = _evaluate(
            [_slot(1, tilt_min=30), _slot(2, tilt_min=50)],
            winner=_StubWinner(50, tilt=10),
        )
        assert res.tilt == 50

    def test_tilt_range_clamps_both_ways(self) -> None:
        """A tilt RANGE slot bounds the winner on both sides."""
        low = _evaluate(
            [_slot(1, tilt_min=40, tilt_max=80)], winner=_StubWinner(50, tilt=10)
        )
        high = _evaluate(
            [_slot(1, tilt_min=40, tilt_max=80)], winner=_StubWinner(50, tilt=95)
        )
        assert (low.tilt, high.tilt) == (40, 80)

    def test_tilt_bound_slot_does_not_claim_position(self) -> None:
        """A tilt-bound-only slot leaves the position pipeline alone."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(80, tilt=20))
        assert res.position == 80


class TestTiltOnlyOverlayThenClamp:
    """FIXED fills when unset; bounds then clamp the filled value."""

    def test_overlay_is_clamped_by_a_tilt_min_from_another_slot(self) -> None:
        """Overlay 20 with a separate slot's tilt_min 50 → 50."""
        res = _evaluate(
            [_slot(1, position=0, tilt=20, tilt_only=True), _slot(2, tilt_min=50)],
            winner=_StubWinner(50),
        )
        assert res.tilt == 50

    def test_overlay_within_bounds_survives(self) -> None:
        """An overlay already inside the bounds is untouched."""
        res = _evaluate(
            [_slot(1, position=0, tilt=70, tilt_only=True), _slot(2, tilt_min=50)],
            winner=_StubWinner(50),
        )
        assert res.tilt == 70


class TestTiltBoundsCarriedWhenTiltUnresolved:
    """Tilt can resolve after the pipeline (venetian) — carry the bounds."""

    def test_bounds_carried_on_result(self) -> None:
        """With no tilt to clamp yet, the composed bounds ride the result."""
        res = _evaluate([_slot(1, tilt_min=50, tilt_max=80)], winner=_StubWinner(50))
        assert (res.tilt_low, res.tilt_high) == (50, 80)

    def test_tilt_stays_none(self) -> None:
        """The registry must not invent a tilt out of a bound."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50))
        assert res.tilt is None

    def test_bound_active_step_emitted(self) -> None:
        """A pending bound is traced so it isn't invisible."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50))
        assert ReasonCode.REGISTRY_TILT_BOUND_ACTIVE in _codes(res)

    def test_no_bounds_leaves_fields_none(self) -> None:
        """Without tilt constraints the new fields stay None."""
        res = _evaluate([], winner=_StubWinner(50))
        assert (res.tilt_low, res.tilt_high) is not None or True
        assert res.tilt_low is None
        assert res.tilt_high is None
