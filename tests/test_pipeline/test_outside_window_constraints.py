"""Outside-the-clock-window axis constraints (issue #943 item B).

A Custom Position slot can opt in — per slot — to keeping its **min/max
constraint** binding after the user's start/end clock window has closed. The
capability is deliberately narrow, and the invariant it must never break is:

    Outside the user's start/end clock window, a cover moves only for
    (a) a safety result (``is_safety`` — weather, or a priority-100 slot), or
    (b) an opted-in slot's active min/max constraint, and then only to satisfy
    that constraint: the constrained axis receives the composed bound edge,
    every other axis receives the cover's current read. A non-safety winner's
    own values (default, sunset, solar, climate — position or tilt) never reach
    hardware outside the clock window.

The last sentence is what separates this from the #215/#216/#223 defect class,
and it is why the registry converts a computed winner into a *pseudo-hold*
rather than simply lifting a dispatch gate.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    AxisConstraintMode,
    ControlMethod,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
)
from custom_components.adaptive_cover_pro.pipeline.axis_constraints import (
    gather_axis_constraints,
    may_act_outside_clock_window,
    window_ineligible_constraints,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    CustomPositionHandler,
    DefaultHandler,
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)

from tests.test_pipeline.conftest import make_snapshot


def _slot(
    slot: int = 1,
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
    outside_window: bool = False,
) -> CustomPositionSensorState:
    """Build a slot state; the per-axis modes derive themselves."""
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.slot{slot}",),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        tilt=tilt,
        tilt_only=tilt_only,
        slot=slot,
        position_max=position_max,
        tilt_min=tilt_min,
        tilt_max=tilt_max,
        outside_window=outside_window,
    )


def _snapshot(*, sensors=None, clock_open=True, **kwargs):
    return make_snapshot(
        custom_position_sensors=list(sensors or []),
        clock_window_open=clock_open,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Gather filter (step 3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gather_drops_unflagged_constraints_when_clock_closed():
    """A slot that did not opt in stops contributing once the clock closes."""
    snap = _snapshot(sensors=[_slot(tilt_min=50)], clock_open=False)
    assert gather_axis_constraints(snap) == []
    dropped = window_ineligible_constraints(snap)
    assert [c.axis for c in dropped] == [AXIS_NAME_TILT]


@pytest.mark.unit
def test_gather_keeps_flagged_min_max_claims_when_clock_closed():
    """Every bounded kind of an opted-in slot survives the closed clock."""
    snap = _snapshot(
        sensors=[
            _slot(
                position=30,
                min_mode=True,
                position_max=70,
                tilt_min=50,
                tilt_max=90,
                outside_window=True,
            )
        ],
        clock_open=False,
    )
    kept = gather_axis_constraints(snap)
    assert {c.axis for c in kept} == {AXIS_NAME_POSITION, AXIS_NAME_TILT}
    assert all(c.kind is AxisConstraintMode.RANGE for c in kept)
    assert window_ineligible_constraints(snap) == []


@pytest.mark.unit
def test_gather_drops_fixed_claims_of_flagged_slot_when_clock_closed():
    """The flag never lets a slot DRIVE a value outside the window.

    An exact position and a real fixed slat angle are FIXED claims: admitting
    them would be #215/#216/#223 re-armed by a checkbox. Only the slot's
    bounded claims survive.
    """
    snap = _snapshot(
        sensors=[
            _slot(position=30, outside_window=True),
            _slot(2, tilt=20, tilt_only=True, outside_window=True),
        ],
        clock_open=False,
    )
    assert gather_axis_constraints(snap) == []
    dropped = window_ineligible_constraints(snap)
    assert {c.kind for c in dropped} == {AxisConstraintMode.FIXED}


@pytest.mark.unit
def test_gather_keeps_weather_floor_and_safety_slots_when_clock_closed():
    """Weather and priority-100 slots were already window-blind — unchanged."""
    snap = _snapshot(
        sensors=[
            _slot(
                position=40,
                min_mode=True,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
            )
        ],
        clock_open=False,
        weather_override_active=True,
        weather_override_min_mode=True,
        weather_override_position=60,
    )
    kept = gather_axis_constraints(snap)
    assert sorted(c.source for c in kept) == ["custom_position_1", "weather"]
    assert window_ineligible_constraints(snap) == []


@pytest.mark.unit
def test_gather_window_open_is_byte_identical_to_today():
    """With the clock open the filter is inert, whatever the flags say."""
    sensors = [
        _slot(position=30, min_mode=True, position_max=70, tilt_min=50),
        _slot(2, tilt=20, tilt_only=True),
        _slot(3, position=10, outside_window=True),
    ]
    opened = gather_axis_constraints(_snapshot(sensors=sensors, clock_open=True))
    unflagged = [
        _slot(position=30, min_mode=True, position_max=70, tilt_min=50),
        _slot(2, tilt=20, tilt_only=True),
        _slot(3, position=10),
    ]
    baseline = gather_axis_constraints(_snapshot(sensors=unflagged, clock_open=True))
    assert opened == baseline
    assert window_ineligible_constraints(_snapshot(sensors=sensors)) == []


@pytest.mark.unit
def test_user_move_clamp_ignores_unflagged_floor_at_night_but_honors_weather():
    """The #472 user-move clamp inherits the filter through the gather.

    Deliberate behaviour change: a non-opted-in ``min_mode`` floor stops
    clamping a user's service-call position at 02:00, which moves the clamp
    *toward* the #215/#216 invariant. The weather floor is unconditionally
    window-eligible and keeps clamping.
    """
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        gather_active_floors,
    )

    night = _snapshot(
        sensors=[_slot(position=40, min_mode=True)],
        clock_open=False,
        weather_override_active=True,
        weather_override_min_mode=True,
        weather_override_position=60,
    )
    assert [f.source for f in gather_active_floors(night)] == ["weather"]

    day = _snapshot(
        sensors=[_slot(position=40, min_mode=True)],
        clock_open=True,
        weather_override_active=True,
        weather_override_min_mode=True,
        weather_override_position=60,
    )
    assert [f.source for f in gather_active_floors(day)] == [
        "custom_position_1",
        "weather",
    ]


# ---------------------------------------------------------------------------
# Admission flag + shared predicate (step 4)
# ---------------------------------------------------------------------------


def _registry() -> PipelineRegistry:
    """Build a registry carrying nothing but the always-matching DefaultHandler."""
    return PipelineRegistry([DefaultHandler()])


def _evaluate(*, sensors, clock_open, **kwargs) -> PipelineResult:
    snap = _snapshot(
        sensors=sensors,
        clock_open=clock_open,
        policy=get_policy(kwargs.pop("cover_type", "cover_blind")),
        **kwargs,
    )
    return _registry().evaluate(snap)


@pytest.mark.unit
def test_result_admitted_when_flagged_tilt_bound_clamps_resolved_tilt():
    """A bound that actually moved something admits the cycle."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=10,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    assert result.outside_window_constraint_active is True
    assert result.acts_outside_clock_window is True
    assert result.is_safety is False
    assert result.tilt == 50


@pytest.mark.unit
def test_result_not_admitted_when_bound_already_satisfied():
    """A compliant cover is not a reason to command anything."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=60,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    assert result.outside_window_constraint_active is False
    assert result.acts_outside_clock_window is False
    assert result.hold_clamp_verdicts is None


@pytest.mark.unit
def test_result_never_admitted_with_clock_open():
    """In-window bookings never carry the flag — #215/#216 sweeps untouched."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=True,
        default_position=100,
        default_tilt=10,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    assert result.tilt == 50
    assert result.outside_window_constraint_active is False
    assert result.acts_outside_clock_window is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("is_safety", "admitted", "expected"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_may_act_outside_clock_window_is_the_only_or(is_safety, admitted, expected):
    """One helper answers "may this reach the cover outside the clock?".

    Both the pipeline result and the per-entity command record expose it
    through that helper, so the four dispatch guards cannot drift apart.
    """
    from custom_components.adaptive_cover_pro.managers.cover_command.state_store import (
        PerEntityState,
    )

    assert (
        may_act_outside_clock_window(is_safety=is_safety, constraint_admitted=admitted)
        is expected
    )
    result = PipelineResult(
        position=0,
        control_method=ControlMethod.DEFAULT,
        is_safety=is_safety,
        outside_window_constraint_active=admitted,
    )
    assert result.acts_outside_clock_window is expected
    entity = PerEntityState(
        target=50, is_safety=is_safety, outside_window_constraint=admitted
    )
    assert entity.acts_outside_clock_window is expected


# ---------------------------------------------------------------------------
# Pseudo-hold + tilt edge resolution (step 5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_winner_position_never_dispatched_outside_window():
    """The DEFAULT winner's own 100% must never ride an admitted tilt clamp."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=10,
        current_cover_position=40,
        cover_positions={"cover.a": 40, "cover.b": 25},
    )
    assert result.control_method is ControlMethod.DEFAULT
    assert result.tilt == 50
    assert result.position == 40
    assert result.skip_command is False
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    # Every cover is commanded (the slats have to reach the hardware) and each
    # one to its OWN read, so no carriage moves.
    assert verdicts["cover.a"].released is True
    assert verdicts["cover.a"].target == 40
    assert verdicts["cover.b"].released is True
    assert verdicts["cover.b"].target == 25


@pytest.mark.unit
def test_registry_resolves_tilt_edge_when_winner_has_no_tilt():
    """With no resolved tilt the registry pins the bound's own edge.

    ``PipelineSnapshot`` carries no tilt reads, and
    ``VenetianPolicy.post_pipeline_resolve`` drops carried bounds on its
    engine-suppressed branch — which is the branch a DEFAULT winner takes. So
    the edge has to be resolved here, and it is the constraint's own claim,
    never the winner's.
    """
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=None,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
        cover_type="cover_venetian",
    )
    assert result.tilt == 50
    assert result.tilt_low is None
    assert result.tilt_high is None
    assert result.outside_window_constraint_active is True
    assert result.position == 40


@pytest.mark.unit
def test_flagged_position_max_clamps_only_violating_covers():
    """A ceiling moves the cover that violates it and nobody else.

    ``default_tilt`` is set deliberately: the position axis is the only bound
    one here, so the DEFAULT winner's own slat angle must not ride along on the
    dispatch the ceiling forced (the tilt half of the same invariant).
    """
    result = _evaluate(
        sensors=[_slot(position_max=30, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=0,
        current_cover_position=45,
        cover_positions={"cover.high": 80, "cover.low": 10},
    )
    assert result.outside_window_constraint_active is True
    assert result.tilt is None
    verdicts = result.hold_clamp_verdicts
    assert verdicts["cover.high"].released is True
    assert verdicts["cover.high"].target == 30
    assert verdicts["cover.low"].released is False


@pytest.mark.unit
def test_admission_withheld_without_position_reads():
    """With no "where the cover is" there is nothing safe to pin the other axis to."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=10,
        current_cover_position=None,
        cover_positions=None,
    )
    assert result.outside_window_constraint_active is False
    assert result.acts_outside_clock_window is False


@pytest.mark.unit
def test_ow_constraint_defers_to_manual_hold_outside_window():
    """A real hold at 80 still outranks a 77 slot — ``outranking`` unchanged."""
    from custom_components.adaptive_cover_pro.pipeline.handlers import (
        ManualOverrideHandler,
    )

    snap = _snapshot(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        policy=get_policy("cover_blind"),
        manual_override_active=True,
        default_position=100,
        default_tilt=10,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    result = PipelineRegistry([ManualOverrideHandler(), DefaultHandler()]).evaluate(
        snap
    )
    assert result.control_method is ControlMethod.MANUAL
    assert result.outside_window_constraint_active is False
    assert any(
        step.reason_payload is not None
        and step.reason_payload.code is ReasonCode.REGISTRY_BOUND_YIELDED_TO_HOLD
        for step in result.decision_trace
    )


@pytest.mark.unit
def test_edge_resolution_never_uses_an_outprioritized_handlers_tilt():
    """The resolved edge is the bound's, not some losing handler's tilt (#1153)."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50, tilt_max=70, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=None,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    # The floor edge wins the edge resolution: a floor is the protection
    # commitment, exactly as ``clamp_to_bounds`` applies it last.
    assert result.tilt == 50


@pytest.mark.unit
def test_dropped_constraint_leaves_an_explicit_trace_step():
    """A non-admitted bound says so rather than silently vanishing."""
    result = _evaluate(
        sensors=[_slot(tilt_min=50)],
        clock_open=False,
        default_position=100,
        default_tilt=10,
        current_cover_position=40,
        cover_positions={"cover.a": 40},
    )
    # The DEFAULT winner keeps its own tilt: the dropped bound clamped nothing.
    assert result.tilt == 10
    assert result.outside_window_constraint_active is False
    steps = [
        s
        for s in result.decision_trace
        if s.reason_payload is not None
        and s.reason_payload.code is ReasonCode.REGISTRY_CONSTRAINT_OUTSIDE_WINDOW
    ]
    assert len(steps) == 1
    assert steps[0].matched is False
    assert steps[0].handler == "custom_position_1"


# ---------------------------------------------------------------------------
# Axis scoping: an unbound axis carries nothing of the winner's
# ---------------------------------------------------------------------------
#
# The pseudo-hold pins the POSITION axis to the cover's own read. Tilt has no
# equivalent read to pin to — ``PipelineSnapshot`` carries ``cover_positions``
# only — so the tilt axis of an admitted cycle carries a value the constraint
# composition produced, or nothing at all. ``use_my_position`` is the same
# question asked about the routing: an admitted cycle sends the composed edge,
# never the hardware My preset the winner would have used.


@pytest.mark.unit
def test_position_bound_never_carries_the_winners_own_tilt():
    """A position ceiling admits the cycle; the DEFAULT's slats stay put.

    The reporter's own ``sunset_tilt`` is 0. Without this rule a window contact
    configured with nothing but a *position* ceiling drives the carriage to the
    bound edge (correct) and the slats to 0 (never asked for) at 03:00 — the
    #215/#216/#223 defect class on the tilt axis.
    """
    result = _evaluate(
        sensors=[_slot(position_max=30, outside_window=True)],
        clock_open=False,
        default_position=100,
        default_tilt=0,
        current_cover_position=80,
        cover_positions={"cover.a": 80},
        cover_type="cover_venetian",
    )
    assert result.outside_window_constraint_active is True
    assert result.position == 30
    assert result.skip_command is False
    assert result.tilt is None
    # The seam that decides whether a tilt command is issued at all:
    # ``PositionContext.tilt`` stays unset, so ``maybe_update_tilt_only``'s
    # ``context.tilt is not None`` guard never opens.
    assert "tilt" not in get_policy("cover_venetian").position_context_overrides(result)


@pytest.mark.unit
def test_flagged_tilt_min_still_reaches_the_slats_outside_window():
    """The reporter's actual case, end to end — ``tilt_min`` must still bind.

    Suppressing the *unbound* axis must not suppress the bound one: a slot that
    asks for slats at 50 gets slats at 50, and the carriage stays where the
    cover already is.
    """
    result = _evaluate(
        sensors=[_slot(tilt_min=50, outside_window=True)],
        clock_open=False,
        default_position=100,
        is_sunset_active=True,
        sunset_tilt=0,
        current_cover_position=80,
        cover_positions={"cover.a": 80},
        cover_type="cover_venetian",
    )
    assert result.outside_window_constraint_active is True
    assert result.tilt == 50
    assert result.position == 80
    overrides = get_policy("cover_venetian").position_context_overrides(result)
    assert overrides["tilt"] == 50


@pytest.mark.unit
def test_use_my_winner_does_not_route_to_the_my_preset_outside_window():
    """An admitted cycle sends the composed edge, not the hardware My preset.

    "Use My at sunset" makes the DEFAULT winner's *position* the cover's stored
    My value and flips ``use_my_position``, which routes a cover without
    ``set_cover_position`` through ``stop_cover``. Outside the window that flag
    is the winner's own value by another name, and it would land the cover on
    its preset instead of the bound edge the ceiling just resolved.
    """
    result = _evaluate(
        sensors=[_slot(position_max=30, outside_window=True)],
        clock_open=False,
        default_position=100,
        is_sunset_active=True,
        sunset_use_my=True,
        my_position_value=65,
        current_cover_position=80,
        cover_positions={"cover.a": 80},
    )
    assert result.outside_window_constraint_active is True
    assert result.position == 30
    assert result.use_my_position is False


@pytest.mark.unit
def test_axis_scoping_is_inert_with_the_clock_open():
    """In-window results keep the winner's tilt and My routing untouched."""
    result = _evaluate(
        sensors=[_slot(position_max=30, outside_window=True)],
        clock_open=True,
        default_position=100,
        default_tilt=0,
        is_sunset_active=True,
        sunset_use_my=True,
        my_position_value=65,
        current_cover_position=80,
        cover_positions={"cover.a": 80},
        cover_type="cover_venetian",
    )
    assert result.outside_window_constraint_active is False
    assert result.tilt == 0
    assert result.use_my_position is True


# ---------------------------------------------------------------------------
# Clause (a): a safety winner already owns the night
# ---------------------------------------------------------------------------
#
# The pseudo-hold exists to suppress a NON-safety winner's own position, which
# has no licence to reach hardware outside the window. A safety result does
# have that licence — weather, or a priority-100 slot (#563) — and its own
# values are precisely what must be dispatched. Applying the pseudo-hold to one
# replaces the storm position with the cover's current read.


def _safety_slot_registry() -> PipelineRegistry:
    """Build a priority-100 slot naming an exact position, plus DEFAULT."""
    return PipelineRegistry(
        [
            CustomPositionHandler(1, 0, CUSTOM_POSITION_SAFETY_PRIORITY),
            DefaultHandler(),
        ]
    )


@pytest.mark.unit
def test_weather_safety_winner_keeps_its_own_position_outside_window():
    """A storm retraction goes where WEATHER says, not to a bound's edge."""
    kwargs = {
        "sensors": [
            _slot(
                position=40,
                min_mode=True,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
            )
        ],
        "policy": get_policy("cover_blind"),
        "weather_override_active": True,
        "weather_override_position": 90,
        "default_position": 100,
        "current_cover_position": 10,
        "cover_positions": {"cover.a": 10},
    }
    registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
    closed = registry.evaluate(_snapshot(clock_open=False, **kwargs))
    opened = registry.evaluate(_snapshot(clock_open=True, **kwargs))

    assert closed.control_method is ControlMethod.WEATHER
    assert closed.is_safety is True
    # The floor at 40 does not bind a retraction to 90, in or out of the window.
    assert closed.position == opened.position == 90
    assert closed.acts_outside_clock_window is True
    assert closed.hold_clamp_verdicts is None


@pytest.mark.unit
def test_safety_slot_fixed_position_still_dispatched_outside_window():
    """A priority-100 storm-close still closes — #563's promise, unchanged.

    The slot names position 0 *and* carries a ``tilt_min``. The tilt bound must
    not turn the close into "stay where you are with the slats at 50".
    """
    kwargs = {
        "sensors": [
            _slot(
                position=0,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
                tilt_min=50,
            )
        ],
        "policy": get_policy("cover_blind"),
        "default_position": 100,
        "current_cover_position": 80,
        "cover_positions": {"cover.a": 80},
    }
    closed = _safety_slot_registry().evaluate(_snapshot(clock_open=False, **kwargs))
    opened = _safety_slot_registry().evaluate(_snapshot(clock_open=True, **kwargs))

    assert closed.control_method is ControlMethod.CUSTOM_POSITION
    assert closed.is_safety is True
    # A safety winner is window-invariant: what it sends at 02:00 is exactly
    # what it sends at noon, tilt bound included.
    assert closed.position == opened.position == 0
    assert closed.tilt == opened.tilt
    assert closed.tilt_low == opened.tilt_low == 50
    assert closed.hold_clamp_verdicts is None


@pytest.mark.unit
def test_eligible_bound_still_clamps_a_safety_winner_outside_window():
    """Declining the pseudo-hold must not stop a bound from BOUNDING.

    The defect is that a safety winner's position gets *replaced* by the
    current read, not that it gets clamped. A ceiling that really binds the
    retraction still binds it, exactly as it does with the clock open.
    """
    kwargs = {
        "sensors": [_slot(position_max=70, priority=CUSTOM_POSITION_SAFETY_PRIORITY)],
        "policy": get_policy("cover_blind"),
        "weather_override_active": True,
        "weather_override_position": 90,
        "default_position": 100,
        "current_cover_position": 10,
        "cover_positions": {"cover.a": 10},
    }
    registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
    closed = registry.evaluate(_snapshot(clock_open=False, **kwargs))
    opened = registry.evaluate(_snapshot(clock_open=True, **kwargs))

    assert closed.position == opened.position == 70
    assert closed.position_constraint_applied is True
    assert closed.is_safety is True


@pytest.mark.unit
def test_safety_winner_keeps_its_own_tilt_and_my_routing_outside_window():
    """Axis scoping is for clause (b) only — clause (a) is window-invariant.

    A priority-100 slot on the hardware My preset, carrying its own slat angle,
    under a second slot's opted-in ceiling. The ceiling still bounds it, and
    everything else it sends at 02:00 is byte-identical to noon.
    """
    kwargs = {
        "sensors": [
            _slot(use_my=True, priority=CUSTOM_POSITION_SAFETY_PRIORITY),
            _slot(2, position_max=30, outside_window=True),
        ],
        "policy": get_policy("cover_venetian"),
        "default_position": 100,
        "default_tilt": 0,
        "my_position_value": 65,
        "current_cover_position": 80,
        "cover_positions": {"cover.a": 80},
    }
    handlers = [
        CustomPositionHandler(1, None, CUSTOM_POSITION_SAFETY_PRIORITY, 20),
        DefaultHandler(),
    ]
    closed = PipelineRegistry(handlers).evaluate(_snapshot(clock_open=False, **kwargs))
    opened = PipelineRegistry(handlers).evaluate(_snapshot(clock_open=True, **kwargs))

    assert closed.is_safety is True
    assert closed.outside_window_constraint_active is False
    assert closed.tilt == opened.tilt == 20
    assert closed.use_my_position == opened.use_my_position is True
    # The ceiling binds the safety winner in both, exactly as before item B.
    assert closed.position == opened.position == 30


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sensors", "weather"),
    [
        pytest.param(
            [
                _slot(
                    position=40, min_mode=True, priority=CUSTOM_POSITION_SAFETY_PRIORITY
                )
            ],
            True,
            id="safety-floor-under-weather-winner",
        ),
        pytest.param(
            [_slot(tilt_min=50, outside_window=True)],
            True,
            id="flagged-slot-bound-under-weather-winner",
        ),
        pytest.param(
            [_slot(position=0, priority=CUSTOM_POSITION_SAFETY_PRIORITY, tilt_min=50)],
            False,
            id="safety-slot-winner-with-own-tilt-bound",
        ),
    ],
)
def test_safety_result_never_also_claims_a_constraint_admission(sensors, weather):
    """``is_safety`` and the item-B admission are never co-written.

    #1226/#1165 gave ``is_safety`` one writer per entry point and a defined
    end; the constraint admission is a deliberately separate licence. A result
    carrying both means the registry converted a safety winner into a
    pseudo-hold — which is exactly how its own position gets lost.
    """
    handlers = (
        [WeatherOverrideHandler(), DefaultHandler()]
        if weather
        else [
            CustomPositionHandler(1, 0, CUSTOM_POSITION_SAFETY_PRIORITY),
            DefaultHandler(),
        ]
    )
    result = PipelineRegistry(handlers).evaluate(
        _snapshot(
            sensors=sensors,
            clock_open=False,
            policy=get_policy("cover_blind"),
            weather_override_active=weather,
            weather_override_position=90,
            default_position=100,
            current_cover_position=10,
            cover_positions={"cover.a": 10},
        )
    )
    assert result.is_safety is True
    assert result.outside_window_constraint_active is False
    # The licence it does hold is its own, and it is enough.
    assert result.acts_outside_clock_window is True
