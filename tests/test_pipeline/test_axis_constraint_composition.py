"""Registry composition of the new axis constraints (issue #943).

The *existing* composition contract — position floors (#463/#496) and their
interaction with ``held_position`` (#534/#809), plus the tilt-only overlay
(#514) — is pinned by ``test_floor_composition.py`` and
``test_tilt_only_contribution.py``, which this change must leave untouched.

This file pins what #943 adds on top: position ceilings, tilt bounds, and the
way they compose with the floors that were already there.
"""

from __future__ import annotations

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


class _StubTiltContributor(OverrideHandler):
    """A lower-priority handler that matches and supplies only a tilt.

    Reproduces the ``_MERGEABLE`` fill path: the winner leaves ``tilt`` unset
    and this handler's tilt is merged onto the result.
    """

    name = "stub_tilt"

    def __init__(self, tilt: int, *, priority: int = 50) -> None:
        self._tilt = tilt
        self.priority = priority

    def evaluate(self, snapshot) -> PipelineResult:  # noqa: ARG002
        return PipelineResult(
            position=99,
            tilt=self._tilt,
            control_method=ControlMethod.SOLAR,
            reason="stub tilt",
        )

    def describe_skip(self, snapshot):  # noqa: ARG002
        return "stub tilt skip"


def _evaluate(
    sensors,
    *,
    winner: _StubWinner | None = None,
    extra: list[OverrideHandler] | None = None,
):
    """Run a registry with a stub winner plus one handler per slot."""
    win = winner or _StubWinner()
    handlers: list[OverrideHandler] = [win, DefaultHandler(), *(extra or [])]
    for s in sensors:
        handlers.append(
            CustomPositionHandler(
                slot=s.slot,
                position=s.position if s.position is not None else 0,
                priority=s.priority,
                tilt=s.tilt,
            )
        )
    snap = make_snapshot(custom_position_sensors=sensors, default_position=0)
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


# ---------------------------------------------------------------------------
# Pre-merge audit regressions (issue #943)
# ---------------------------------------------------------------------------


class TestWinnerTraceStepSurvivesItsOwnConstraint:
    """A slot can win the pipeline *and* carry a tilt bound (audit finding 1).

    Sweeping every constraint source out of the trace left the winner unnamed:
    the card showed a move with no matched step explaining it.
    """

    def _res(self):
        return _evaluate(
            [_slot(1, position=40, tilt_min=50)],
            winner=_StubWinner(80, priority=10),
        )

    def test_slot_still_wins_the_position(self) -> None:
        """The fixed claim is unaffected — this is a trace bug, not a value bug."""
        assert self._res().position == 40

    def test_winner_keeps_its_matched_trace_step(self) -> None:
        """The handler that won must be named in the trace."""
        matched = [
            s.handler for s in self._res().decision_trace if s.matched and s.position
        ]
        assert "custom_position_1" in matched

    def test_trace_has_exactly_one_matched_position_step(self) -> None:
        """No clamp fires here, so the winner is the only matched position step."""
        res = self._res()
        matched = [
            s for s in res.decision_trace if s.matched and s.handler != "tilt_clamp"
        ]
        assert [s.handler for s in matched] == ["custom_position_1"]


class TestMergedTiltIsClamped:
    """A tilt reaching the result via the _MERGEABLE fill must be clamped.

    Audit finding 2 — the headline feature failing at its own job: the registry
    clamped only ``winner.tilt`` / the FIXED overlay, so a tilt merged from a
    lower-priority handler (or a ``contribute()``) sailed past the bound.
    """

    def _res(self, tilt_min=50):
        return _evaluate(
            [_slot(3, tilt_min=tilt_min)],
            winner=_StubWinner(70, priority=80),
            extra=[_StubTiltContributor(30, priority=50)],
        )

    def test_merged_tilt_is_raised_to_the_bound(self) -> None:
        """Merged tilt 30 under a minimum of 50 → 50."""
        assert self._res().tilt == 50

    def test_bounds_are_not_also_carried(self) -> None:
        """Once a tilt is clamped there is exactly one clamp site — not two.

        Carrying the bounds as well would let the venetian policy clamp the
        already-clamped tilt a second time.
        """
        res = self._res()
        assert (res.tilt_low, res.tilt_high) == (None, None)

    def test_merged_tilt_within_bounds_survives(self) -> None:
        """A merged tilt already inside the bound is untouched."""
        assert self._res(tilt_min=20).tilt == 30

    def test_clamped_merge_emits_the_trace_step(self) -> None:
        """The clamp is visible rather than a silent value change."""
        assert ReasonCode.REGISTRY_TILT_CLAMPED in _codes(self._res())


class TestBindingBoundIsNamed:
    """Trace steps name the bound that actually bound (audit finding 4a)."""

    def test_floor_raised_names_only_the_binding_floor(self) -> None:
        """Two floors, one binds — the raise must not credit the other."""
        res = _evaluate(
            [
                _slot(1, position=40, min_mode=True, sensor_name="Sensor 1"),
                _slot(2, position=60, min_mode=True, sensor_name="Sensor 2"),
            ],
            winner=_StubWinner(10),
        )
        params = _step(res, ReasonCode.REGISTRY_FLOOR_RAISED).reason_payload.params
        assert params["label"] == "Sensor 2"

    def test_ceiling_lowered_names_only_the_binding_ceiling(self) -> None:
        """The mirror: the lowest ceiling binds and is the one credited."""
        res = _evaluate(
            [
                _slot(1, position_max=60, sensor_name="Sensor 1"),
                _slot(2, position_max=30, sensor_name="Sensor 2"),
            ],
            winner=_StubWinner(90),
        )
        params = _step(res, ReasonCode.REGISTRY_CEILING_LOWERED).reason_payload.params
        assert params["label"] == "Sensor 2"

    def test_tilt_clamp_names_only_the_binding_tilt_bound(self) -> None:
        """Same rule on the tilt axis."""
        res = _evaluate(
            [
                _slot(1, tilt_min=30, sensor_name="Sensor 1"),
                _slot(2, tilt_min=50, sensor_name="Sensor 2"),
            ],
            winner=_StubWinner(50, tilt=10),
        )
        params = _step(res, ReasonCode.REGISTRY_TILT_CLAMPED).reason_payload.params
        assert params["label"] == "Sensor 2"


class TestTiedFloorsBothTraced:
    """Tie floors: the losing slot keeps an inactive step (audit finding 4b)."""

    def test_losing_tied_floor_emits_an_inactive_step(self) -> None:
        """Equal floors resolve to the first — the second must still explain itself."""
        res = _evaluate(
            [
                _slot(1, position=60, min_mode=True),
                _slot(2, position=60, min_mode=True),
            ],
            winner=_StubWinner(10),
        )
        inactive = [
            s.handler
            for s in res.decision_trace
            if s.reason_payload is not None
            and s.reason_payload.code is ReasonCode.REGISTRY_FLOOR_INACTIVE
        ]
        assert inactive == ["custom_position_2"]


class TestLosingTiltOnlySlotKeepsAStep:
    """A deferred tilt-only loser is not swept out of the trace (finding 4c)."""

    def test_losing_tilt_only_slot_is_still_named(self) -> None:
        """Slot 2 loses the FIXED tilt resolution but must stay visible."""
        res = _evaluate(
            [
                _slot(1, tilt=20, tilt_only=True, priority=90),
                _slot(2, tilt=70, tilt_only=True, priority=50),
            ],
            winner=_StubWinner(50),
        )
        assert "custom_position_2" in [s.handler for s in res.decision_trace]


class TestCeilingOverriddenByFloor:
    """The floor-beats-ceiling conflict reads honestly (audit finding 7)."""

    def _res(self):
        return _evaluate(
            [
                _slot(1, position=60, min_mode=True, sensor_name="Floor"),
                _slot(2, position_max=40, sensor_name="Ceiling"),
            ],
            winner=_StubWinner(50),
        )

    def test_overridden_ceiling_is_not_reported_as_inactive(self) -> None:
        """The cover ended up *above* the ceiling — 'inactive' is a lie."""
        assert ReasonCode.REGISTRY_CEILING_INACTIVE not in _codes(self._res())

    def test_overridden_ceiling_emits_the_overridden_step(self) -> None:
        """Say the floor overrode it, which is what actually happened."""
        assert ReasonCode.REGISTRY_CEILING_OVERRIDDEN in _codes(self._res())

    def test_overridden_step_names_the_final_position(self) -> None:
        """The payload carries the ceiling and where the cover really went."""
        params = _step(
            self._res(), ReasonCode.REGISTRY_CEILING_OVERRIDDEN
        ).reason_payload.params
        assert params["ceiling_pos"] == 40
        assert params["to_pos"] == 60

    def test_inert_ceiling_still_reads_as_inactive(self) -> None:
        """Without a conflict the ceiling keeps today's inactive wording."""
        res = _evaluate([_slot(1, position_max=60)], winner=_StubWinner(40))
        assert ReasonCode.REGISTRY_CEILING_INACTIVE in _codes(res)


# ---------------------------------------------------------------------------
# Second-round pre-merge audit regressions (issue #943)
# ---------------------------------------------------------------------------


class TestLosingPositionSlotWithTiltBoundStaysVisible:
    """Finding A: a slot that loses the position axis but carries a tilt bound.

    Slot 2 names an exact position that loses on priority (a matched=False
    ``OUTPRIORITIZED`` step) while also contributing a tilt minimum. The tilt
    pass's ``_drop_trace_steps`` swept the slot's step away with nothing
    re-emitted, so an active, constraining slot vanished from the trace.
    """

    def _res(self):
        return _evaluate(
            [_slot(2, position=25, tilt_min=10, priority=50)],
            winner=_StubWinner(80, tilt=60, priority=80),
        )

    def test_no_value_changes(self) -> None:
        """This is a trace bug: winner keeps position 80 and tilt 60."""
        res = self._res()
        assert res.position == 80
        assert res.tilt == 60

    def test_losing_slot_is_named_in_the_trace(self) -> None:
        """custom_position_2 was active and constrained — it must appear."""
        assert "custom_position_2" in [s.handler for s in self._res().decision_trace]

    def test_losing_slot_gets_a_tilt_bound_inactive_step(self) -> None:
        """Its non-binding tilt bound explains itself with the inactive step."""
        assert ReasonCode.REGISTRY_TILT_BOUND_INACTIVE in _codes(self._res())


class TestTiltBoundInactiveStep:
    """Finding B: the tilt axis gains the position axis's inactive analog."""

    def test_within_bound_emits_inactive_step(self) -> None:
        """Winner tilt 75 already satisfies tilt_min 50 — the bound stays visible."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50, tilt=75))
        assert res.tilt == 75  # value unchanged
        assert ReasonCode.REGISTRY_TILT_BOUND_INACTIVE in _codes(res)

    def test_inactive_step_carries_payload_params(self) -> None:
        """The step names the bound and the tilt that was already within it."""
        res = _evaluate(
            [_slot(1, tilt_min=50, sensor_name="Door")],
            winner=_StubWinner(50, tilt=75),
        )
        params = _step(
            res, ReasonCode.REGISTRY_TILT_BOUND_INACTIVE
        ).reason_payload.params
        assert params["low_label"] == "50%"
        assert params["high_label"] == "—"
        assert params["label"] == "Door"
        assert params["tilt"] == 75

    def test_out_composed_bound_emits_inactive_step(self) -> None:
        """A tilt_min out-composed by a stricter one still explains itself."""
        res = _evaluate(
            [
                _slot(1, tilt_min=30, sensor_name="Sensor 1"),
                _slot(2, tilt_min=50, sensor_name="Sensor 2"),
            ],
            winner=_StubWinner(50, tilt=10),
        )
        assert res.tilt == 50  # value unchanged (max-of-mins)
        inactive = [
            s.handler
            for s in res.decision_trace
            if s.reason_payload is not None
            and s.reason_payload.code is ReasonCode.REGISTRY_TILT_BOUND_INACTIVE
        ]
        assert inactive == ["custom_position_1"]

    def test_binding_bound_is_not_also_inactive(self) -> None:
        """The bound that actually clamped gets the clamp step, not an inactive one."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50, tilt=10))
        assert ReasonCode.REGISTRY_TILT_CLAMPED in _codes(res)
        assert ReasonCode.REGISTRY_TILT_BOUND_INACTIVE not in _codes(res)

    def test_carried_bound_is_not_reported_inactive(self) -> None:
        """A pending bound (no tilt resolved yet) stays 'active', never 'inactive'."""
        res = _evaluate([_slot(1, tilt_min=50)], winner=_StubWinner(50))
        assert ReasonCode.REGISTRY_TILT_BOUND_ACTIVE in _codes(res)
        assert ReasonCode.REGISTRY_TILT_BOUND_INACTIVE not in _codes(res)


class TestFloorBeatsCeilingWinnerAboveFloor:
    """Finding C: floor-beats-ceiling with the winner above the floor.

    Floor 60, ceiling 40, winner 80 → position 60 (floor wins). The winner
    started above the floor, so the net move is a lowering — but the floor,
    not the ceiling, determined the final value. The trace must say so.
    """

    def _res(self):
        return _evaluate(
            [
                _slot(1, position=60, min_mode=True, sensor_name="Floor"),
                _slot(2, position_max=40, sensor_name="Ceiling"),
            ],
            winner=_StubWinner(80),
        )

    def test_position_is_the_floor(self) -> None:
        """Value unchanged from the floor-wins rule."""
        assert self._res().position == 60

    def test_move_is_attributed_to_the_floor(self) -> None:
        """A floor-wins step names the floor; it is not a ceiling lower."""
        codes = _codes(self._res())
        assert ReasonCode.REGISTRY_FLOOR_OVERRIDES_CEILING in codes
        assert ReasonCode.REGISTRY_CEILING_LOWERED not in codes

    def test_floor_step_names_only_the_floor_not_a_joined_label(self) -> None:
        """The joined-label bug must not resurface — the label is just the floor."""
        params = _step(
            self._res(), ReasonCode.REGISTRY_FLOOR_OVERRIDES_CEILING
        ).reason_payload.params
        assert params["label"] == "Floor"
        assert params["to_pos"] == 60
        assert params["ceiling_pos"] == 40
        assert params["from_pos"] == 80

    def test_floor_is_not_reported_inactive(self) -> None:
        """The floor produced the value — it cannot also be 'inactive'."""
        assert ReasonCode.REGISTRY_FLOOR_INACTIVE not in _codes(self._res())

    def test_beaten_ceiling_reads_overridden_not_inactive(self) -> None:
        """The cover ended up above the ceiling — 'below ceiling' would be a lie."""
        codes = _codes(self._res())
        assert ReasonCode.REGISTRY_CEILING_OVERRIDDEN in codes
        assert ReasonCode.REGISTRY_CEILING_INACTIVE not in codes

    def test_overridden_ceiling_names_the_final_position(self) -> None:
        params = _step(
            self._res(), ReasonCode.REGISTRY_CEILING_OVERRIDDEN
        ).reason_payload.params
        assert params["ceiling_pos"] == 40
        assert params["to_pos"] == 60


class TestFixedPositionOutranksCeiling:
    """An explicit position keeps its claim; position_max needs min_mode (#5)."""

    def test_fixed_position_wins_over_a_stored_position_max(self) -> None:
        """Slot names 70 with a stale ceiling of 50 → the slot claims 70."""
        res = _evaluate(
            [_slot(1, position=70, position_max=50)],
            winner=_StubWinner(20, priority=10),
        )
        assert res.position == 70

    def test_min_mode_still_pairs_with_position_max_as_a_range(self) -> None:
        """The RANGE cell is unaffected — the ceiling applies alongside a floor."""
        res = _evaluate(
            [_slot(1, position=30, min_mode=True, position_max=50)],
            winner=_StubWinner(90),
        )
        assert res.position == 50


# ---------------------------------------------------------------------------
# Third-round audit: inactive-step payloads must reflect the FINAL value
# ---------------------------------------------------------------------------


class TestOutComposedTiltBoundInactivePayload:
    """Finding i: an out-composed tilt bound's inactive step must report the
    FINAL (post-clamp) tilt, not the pre-clamp winner tilt.

    Slot 1 tilt_min 30, slot 2 tilt_min 50, winner tilt 10 → final tilt 50
    (max-of-mins; slot 2 binds). Slot 1's non-binding inactive step must say the
    resolved tilt is 50 — which genuinely *is* within [30, —]. The pre-clamp 10
    is a false 'already within' claim: 10 is below 30 and is not the final tilt.
    """

    def _res(self):
        return _evaluate(
            [
                _slot(1, tilt_min=30, sensor_name="Sensor 1"),
                _slot(2, tilt_min=50, sensor_name="Sensor 2"),
            ],
            winner=_StubWinner(50, tilt=10),
        )

    def test_value_unchanged(self) -> None:
        """This is a trace bug: the final tilt is still 50."""
        assert self._res().tilt == 50

    def test_inactive_step_reports_the_final_tilt(self) -> None:
        """The non-binding bound's payload carries the resolved tilt, not 10."""
        step = _step(self._res(), ReasonCode.REGISTRY_TILT_BOUND_INACTIVE)
        assert step.reason_payload.params["label"] == "Sensor 1"
        assert step.reason_payload.params["tilt"] == 50

    def test_stale_number_variant_reports_the_clamped_tilt(self) -> None:
        """A tilt_max clamps 75→40; the inactive tilt_min must report 40, not 75."""
        res = _evaluate(
            [
                _slot(1, tilt_min=20, sensor_name="Min"),
                _slot(2, tilt_max=40, sensor_name="Max"),
            ],
            winner=_StubWinner(50, tilt=75),
        )
        assert res.tilt == 40  # value unchanged
        step = _step(res, ReasonCode.REGISTRY_TILT_BOUND_INACTIVE)
        assert step.reason_payload.params["label"] == "Min"
        assert step.reason_payload.params["tilt"] == 40


class TestOutComposedCeilingInactivePayload:
    """Finding ii: a ceiling ABOVE the winner, out-composed by a lower ceiling,
    must not claim the winner is 'below' it.

    Ceilings 40 and 70, winner 80 → final 40 (min-of-maxes; C40 binds). C70's
    inactive step must read truthfully relative to the resolved position (40 is
    at or below 70) — not 'winner 80% below ceiling 70%', which is a lie: the
    winner (80) sits *above* the 70 ceiling, and 80 is not the resolved value.
    """

    def _res(self):
        return _evaluate(
            [
                _slot(1, position_max=40, sensor_name="Ceiling 40"),
                _slot(2, position_max=70, sensor_name="Ceiling 70"),
            ],
            winner=_StubWinner(80),
        )

    def test_value_unchanged(self) -> None:
        """This is a trace bug: the cover still resolves to 40."""
        assert self._res().position == 40

    def test_inactive_ceiling_reports_the_resolved_position(self) -> None:
        """The payload carries the resolved position (40), not the winner (80)."""
        params = _step(
            self._res(), ReasonCode.REGISTRY_CEILING_INACTIVE
        ).reason_payload.params
        assert params["ceiling_pos"] == 70
        assert params["to_pos"] == 40
        assert "winner_pos" not in params

    def test_inactive_ceiling_text_is_truthful(self) -> None:
        """The rendered text never claims the above-ceiling winner is below it."""
        text = _step(self._res(), ReasonCode.REGISTRY_CEILING_INACTIVE).reason
        assert "80" not in text
        assert "40" in text
