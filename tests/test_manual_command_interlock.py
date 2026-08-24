"""ACP's own user commands get the interlock, not just external ones (#1138).

`await_dispatch_clearance` answers "may ACP drive this entity right now?" and
withholds when a coupled entity is in the way. On a Model C day/night shade that
is the honest answer for a SINGLE-rail user command — the partner rail really is
standing where the user's target is — but the gate can only ever say no. Left
there the command is withheld, latched, and never happens: no movement, no
error, nothing above DEBUG.

Automatic control never lands there (it resolves both rails from one logical
position and dispatches them together, so the blocker is always already on its
way out) and an EXTERNAL command doesn't either (`_plan_external_interlock`
observes and corrects it). Only ACP's own user seams were left with a gate and
no remedy. These tests lock the closure of that gap, and the three defects the
closure itself went through:

* the correction is asked BEFORE the dispatch, so a blocked command does not
  first burn the gate's clearance budget waiting on a rail nobody is moving;
* "should I skip the correction?" is asked as *does the leader's in-flight
  target clear me?*, never as *is the leader busy?* — on an actuator that
  publishes nothing mid-travel the latter is true for most of every move and
  strands the command between a correction that declines and a gate that
  refuses;
* an observed dispatch failure, and a dry run, must not read as a rail in
  flight.

The hardware these were found on (ZVIDAR WB04V, zwave_js) publishes NO transit
state and NO intermediate position for a partial `set_cover_position` move — the
recorder shows the leading rail at 60, then 20 s later at 34, nothing between —
so every test here scripts a rail that reports nothing while it travels. That is
the case the observation-based signals cannot serve.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.adaptive_cover_pro.const import MANUAL_INTERLOCK_REASON
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from tests.ha_helpers import wire_entry_task_factory
from tests.test_day_night_rail_sequencing import (
    _BOTTOM,
    _MIDDLE,
    _SEQ,
    _patch_caps,
    _rail_context,
    _rail_harness,
    _user_seam_coordinator,
)

# A leading rail that is silent for its whole traverse: `_park` values are
# consumed in order and the last repeats, so a script of N stationary readings
# followed by a cleared one models "reports nothing, then arrives".
_SILENT = "open"


def _coord(cmd_svc, policy, ctx, *, dry_run=False, unavailable=()):
    """Build a coordinator over the REAL interlock seam and a REAL Model C policy.

    Only the collaborators the interlock does not reason about are stubbed. The
    planner, the executor, the command service and the travel gate are all
    production code, because the whole point of the seam is that those four
    agree with each other.
    """
    policy._sequencer._get_state = lambda _eid: _SILENT  # noqa: SLF001
    policy._get_booked_target = lambda eid: (  # noqa: SLF001
        None
        if dry_run
        else (cmd_svc.get_target(eid) if cmd_svc.is_waiting_for_target(eid) else None)
    )
    # Signal for the #1154 clamp: the raw, unconditional target ACP last
    # commanded a rail to, independent of whether it is still "in flight"
    # (unlike ``_get_booked_target`` above). Mirrors the production wiring in
    # ``coordinator.py``'s ``attach()`` call.
    policy._get_command_target = lambda eid: cmd_svc.get_target(eid)  # noqa: SLF001
    coord = MagicMock()
    coord.logger = MagicMock()
    coord._policy = policy
    coord._cmd_svc = cmd_svc
    coord.manual_ignore_external = False
    coord.manager = MagicMock()
    coord.config_entry.options = {}
    coord._event_buffer = MagicMock()
    coord._build_position_context = lambda _e, _o, **_kw: ctx  # noqa: ARG005
    cmd_svc.is_entity_unavailable = lambda eid: eid in unavailable
    coord._external_interlock_tasks = {}
    wire_entry_task_factory(coord)
    for name in (
        "_interlock_user_command",
        "_execute_external_interlock",
        "_start_interlock_task",
    ):
        setattr(
            coord,
            name,
            types.MethodType(getattr(AdaptiveDataUpdateCoordinator, name), coord),
        )
    # ``_interlock_pair_key`` is a ``staticmethod``: binding it like the
    # instance methods above (``.__get__``-style, via ``types.MethodType``)
    # would pass ``coord`` as an implicit first argument, shadowing the real
    # ``plan`` argument. A direct attribute assignment keeps it a plain
    # one-argument callable.
    coord._interlock_pair_key = AdaptiveDataUpdateCoordinator._interlock_pair_key
    return coord


def _sends(events: list[str]) -> list[str]:
    return [e for e in events if e.startswith("send:")]


def _harness(monkeypatch, *, script, position, concurrent=None):
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    return _rail_harness(
        script=script, position=position, blend=None, concurrent=concurrent
    )


# ---------------------------------------------------------------------------
# The gap itself: a blocked single-rail user command completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lowering_the_middle_rail_past_the_bottom_rail_moves_both(
    monkeypatch,
) -> None:
    """The reported symptom: middle rail below the bottom rail did nothing.

    Bottom rail parked at 100 (stacked at the head), user drags the middle rail
    to 40. The middle rail cannot go there — the bottom rail is in the way — so
    the gate withholds it. The correction sends the bottom rail out first, then
    re-issues the user's own target behind it.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    assert outcome == ("sent", "set_cover_position")
    assert _sends(events) == [f"send:{_BOTTOM}", f"send:{_MIDDLE}"]


@pytest.mark.asyncio
async def test_raising_the_bottom_rail_past_the_middle_rail_moves_both(
    monkeypatch,
) -> None:
    """The mirror direction (#1118): raising, the middle rail leads."""
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [20], _MIDDLE: [20] * 4 + [83]}, position=83
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        outcome = await coord._interlock_user_command(_BOTTOM, 83, dispatch_token=False)

    assert outcome is not None
    assert _sends(events) == [f"send:{_MIDDLE}", f"send:{_BOTTOM}"]


@pytest.mark.asyncio
async def test_an_unblocked_command_is_left_for_the_normal_dispatch(
    monkeypatch,
) -> None:
    """No plan means the gate would have let it through — don't touch it."""
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [20], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    assert outcome is None
    assert _sends(events) == []


@pytest.mark.asyncio
async def test_the_correction_is_attributed_to_the_manual_origin(
    monkeypatch,
) -> None:
    """The timeline must say WHICH kind of command was corrected."""
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    reasons = {
        call.args[0]["reason"]
        for call in coord._event_buffer.record.call_args_list
        if "reason" in call.args[0]
    }
    assert reasons == {MANUAL_INTERLOCK_REASON}


# ---------------------------------------------------------------------------
# Asked BEFORE the dispatch — the command never enters the clearance budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_corrected_command_records_no_deferred_skip(monkeypatch) -> None:
    """Asking first is what keeps the user's command off the skip record.

    Measured on hardware, asking AFTER cost 60 s (a start-confirmation window
    plus the remainder of the full-clearance fallback) watching a rail nobody
    had told to move, and then recorded a ``policy_deferred`` skip for a command
    that was about to succeed.
    """
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    assert cmd_svc.last_skipped_action.get("reason") != "policy_deferred"


# ---------------------------------------------------------------------------
# "Does the leader's booked target clear me?" — never "is the leader busy?"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_in_flight_target_does_not_suppress_the_correction(
    monkeypatch,
) -> None:
    """The live failure: both rails latched in flight and every command vanished.

    The middle rail carried a booked 61 while the user asked the bottom rail for
    83. Suppressing on "the middle rail is busy" left the correction declining
    (it believed the blocker was moving) and the gate refusing (61 does not
    clear 83) — the command was dropped by both halves, with no skip logged.

    The value assertion below is the #1154 clamp's clearance-gating branch,
    exercised: MIDDLE's own recorded 61 does NOT clear 83 (it is the stale
    target this whole test is about), so the correction must fall through to
    the follower's ``wire_target`` (83), exactly as before the clamp existed.
    A naive clamp that unconditionally preferred MIDDLE's own recorded target
    over ``wire_target`` — with no clearance check — would send 61 here and
    silently reintroduce the #1151 stall this test guards against.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [59], _MIDDLE: [69] * 4 + [83]}, position=83
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))
    cmd_svc.set_target(_MIDDLE, 61)  # does NOT clear 83
    cmd_svc.set_waiting(_MIDDLE, True)

    payloads: list[tuple[str, int | None]] = []
    hass = cmd_svc._hass  # noqa: SLF001
    inner = hass.services.async_call.side_effect

    async def _record(domain, service, data, context=None):
        await inner(domain, service, data, context=context)
        payloads.append((data["entity_id"], data.get("position")))

    hass.services.async_call = AsyncMock(side_effect=_record)

    with _patch_caps():
        outcome = await coord._interlock_user_command(_BOTTOM, 83, dispatch_token=False)

    assert outcome is not None
    assert _sends(events) == [f"send:{_MIDDLE}", f"send:{_BOTTOM}"]
    assert (_MIDDLE, 83) in payloads, payloads
    assert (_MIDDLE, 61) not in payloads, payloads


@pytest.mark.asyncio
async def test_a_clearing_in_flight_target_is_left_to_the_gate(monkeypatch) -> None:
    """A fan-out seam's leader is booked below its follower by the no-pass clamp.

    Correcting there would command the leading rail to the FOLLOWER's target and
    undo the user's own fan-out, so the follower must fall through to the gate —
    which is exactly where it belongs, because that leader really is clearing.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))
    cmd_svc.set_target(_BOTTOM, 30)  # clears the middle rail's 40
    cmd_svc.set_waiting(_BOTTOM, True)

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    assert outcome is None
    assert _sends(events) == []


# ---------------------------------------------------------------------------
# A rail is only "in flight" if a command actually reached it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_observed_service_failure_clears_the_in_flight_flag(
    monkeypatch,
) -> None:
    """A failed dispatch must not read as a rail on its way.

    The target and ``waiting`` are booked BEFORE the service call, because a
    command normally IS in flight by then. This one demonstrably is not, and
    leaving the flag set claims a motion nobody can observe ending — the
    transit-timeout backstop would hold it for ~45 s, during which a coupled
    cover type reads it as "the partner rail is moving" and releases a follower
    into a rail that never moved. The TARGET stays so reconciliation resends it.
    """
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100], _MIDDLE: [100]}, position=40
    )
    ctx = _rail_context(policy)
    cmd_svc._hass.services.async_call = AsyncMock(  # noqa: SLF001
        side_effect=HomeAssistantError("node is dead")
    )

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 40, "solar", ctx)

    assert outcome == ("skipped", "service_call_failed")
    assert cmd_svc.is_waiting_for_target(_BOTTOM) is False
    assert cmd_svc.get_target(_BOTTOM) == 40


@pytest.mark.asyncio
async def test_a_dry_run_target_is_not_evidence_of_motion(monkeypatch) -> None:
    """Dry run books a target so the simulated diagnostics look real.

    Which is precisely why an accessor whose contract is "a partner entity is
    physically travelling" must not read it: in a dry run nothing was sent, so
    there is no motion to be evidence OF, and the follower stays gated.
    """
    # concurrent=True is load-bearing: with the option OFF (the default) the
    # gate never builds ``_start_confirmation``, so ``_booked_clears`` is never
    # consulted and this test would pass on the live reading alone — proving
    # nothing about the dry-run guard it is named for.
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch,
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=40,
        concurrent=True,
    )
    ctx = _rail_context(policy)
    _coord(cmd_svc, policy, ctx, dry_run=True)
    cmd_svc.set_target(_BOTTOM, 40)
    cmd_svc.set_waiting(_BOTTOM, True)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 40, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")


@pytest.mark.asyncio
async def test_a_kept_target_after_a_failed_dispatch_is_not_overwritten(
    monkeypatch,
) -> None:
    """The reported failure (#1154): a correction must not clobber a kept target.

    The bottom rail's own dispatch to 40 failed with a ``HomeAssistantError``.
    PR #1152's handler clears ``waiting`` but deliberately keeps the booked
    target at 40 — a legitimate, still-clearing target, not evidence of
    anything in flight. A SECOND command then lands on the middle rail (target
    70) and triggers a correction on the bottom rail; that correction must read
    the bottom rail's own kept 40, not overwrite it with the middle rail's own
    target, because 40 already clears 70 (and 70 does not need to "clear"
    itself).
    """
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=70
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))
    cmd_svc.set_target(_BOTTOM, 40)
    cmd_svc.set_waiting(_BOTTOM, False)

    payloads: list[tuple[str, int | None]] = []
    hass = cmd_svc._hass  # noqa: SLF001
    inner = hass.services.async_call.side_effect

    async def _record(domain, service, data, context=None):
        await inner(domain, service, data, context=context)
        payloads.append((data["entity_id"], data.get("position")))

    hass.services.async_call = AsyncMock(side_effect=_record)

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 70, dispatch_token=False)

    assert outcome is not None
    assert (_BOTTOM, 40) in payloads, payloads
    assert (_BOTTOM, 70) not in payloads, payloads
    assert cmd_svc.get_target(_BOTTOM) == 40


@pytest.mark.asyncio
async def test_a_waiting_kept_target_after_a_failed_dispatch_declines_the_correction(
    monkeypatch,
) -> None:
    """The control for the test above — already passes today, and must keep passing.

    Same numbers, but ``waiting`` was never cleared: the bottom rail is
    genuinely in flight to 40, which clears the middle rail's 70, so
    ``_booked_clears`` suppresses the correction outright and the gate is left
    to do its job. This documents the existing-good path and guards
    ``_booked_clears``'s own ``waiting`` gate (``get_booked_target``) against a
    future regression from the #1154 clamp reading a DIFFERENT, unconditional
    accessor.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=70
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))
    cmd_svc.set_target(_BOTTOM, 40)
    cmd_svc.set_waiting(_BOTTOM, True)

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 70, dispatch_token=False)

    assert outcome is None
    assert _sends(events) == []


# ---------------------------------------------------------------------------
# The correction must be worth making
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unavailable_target_does_not_move_the_partner_rail(
    monkeypatch,
) -> None:
    """A doomed command must not spend a partner move on itself.

    ``apply_position``'s first gate is ``cover_unavailable``. Correcting for a
    follower that gate will refuse leaves the user with a rail they never
    touched moving, the one they did touch still parked, and both in manual
    override for the whole window.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy), unavailable={_MIDDLE})

    with _patch_caps():
        outcome = await coord._interlock_user_command(_MIDDLE, 40, dispatch_token=False)

    assert outcome is None
    assert _sends(events) == []


# ---------------------------------------------------------------------------
# The caller's own manual-override contract survives the correction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("force", "expected_marks"),
    [
        pytest.param(False, 2, id="default-engages-both-rails"),
        pytest.param(True, 0, id="force-skips-engagement"),
    ],
)
async def test_force_governs_override_engagement_through_the_correction(
    monkeypatch, force: bool, expected_marks: int
) -> None:
    """``force=True`` documents that it skips override engagement.

    The correction must not smuggle it back in for BOTH rails. The answer is
    threaded from the caller rather than re-derived from
    ``manual_ignore_external``, which governs whether an EXTERNAL touch takes
    ownership and has no authority over ACP's own seams.
    """
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _coord(cmd_svc, policy, _rail_context(policy))

    with _patch_caps():
        await coord._interlock_user_command(
            _MIDDLE, 40, dispatch_token=False, force=force
        )

    assert coord.manager.mark_user_command.call_count == expected_marks


# ---------------------------------------------------------------------------
# The shared inequality
# ---------------------------------------------------------------------------


def test_rail_blocks_is_the_complement_of_the_gates_release_test() -> None:
    """One inequality, read from both ends, in both directions.

    ``_rail_blocks`` replaced four separate restatements of this comparison.
    Drift between them is what let the planner and the gate disagree about a
    rail neither of them would move, so the equivalence is pinned rather than
    left to the call sites.
    """
    from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
        DayNightShadePolicy,
    )

    policy = DayNightShadePolicy()
    policy._position_tolerance = 2

    # Middle rail asking: the bottom rail blocks while it is still ABOVE the
    # middle rail's target, tolerance inclusive.
    assert policy._rail_blocks(100, 40, False, is_middle=True) is True
    assert policy._rail_blocks(43, 40, False, is_middle=True) is True
    assert policy._rail_blocks(42, 40, False, is_middle=True) is False
    assert policy._rail_blocks(20, 40, False, is_middle=True) is False

    # Bottom rail asking: the middle rail blocks while it is still BELOW.
    assert policy._rail_blocks(20, 83, False, is_middle=False) is True
    assert policy._rail_blocks(80, 83, False, is_middle=False) is True
    assert policy._rail_blocks(81, 83, False, is_middle=False) is False
    assert policy._rail_blocks(100, 83, False, is_middle=False) is False

    # Inverse state flips the frame, and therefore the verdict, on both branches
    # — the #993 bug class is a sign error here, not a missing case.
    assert policy._rail_blocks(0, 40, True, is_middle=True) is True
    assert policy._rail_blocks(80, 40, True, is_middle=True) is False


@pytest.mark.asyncio
async def test_the_user_seam_returns_the_corrections_own_outcome(monkeypatch) -> None:
    """One command per user action, whichever path completed it.

    The executor re-issues the user's target itself, through the same gate, so
    ``async_apply_user_position`` must hand that outcome back rather than
    dispatch a second time behind the correction.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 6 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _user_seam_coordinator(
        cmd_svc, policy, entities=[_BOTTOM, _MIDDLE], monkeypatch=monkeypatch
    )
    coord.manual_ignore_external = False
    coord._event_buffer = MagicMock()
    cmd_svc.is_entity_unavailable = lambda _eid: False

    with _patch_caps():
        outcome = await coord.async_apply_user_position(_MIDDLE, 40, trigger="set_axes")

    assert outcome == ("sent", "set_cover_position")
    # Exactly one command each — the correction's, not one from each path.
    assert _sends(events) == [f"send:{_BOTTOM}", f"send:{_MIDDLE}"]


@pytest.mark.asyncio
async def test_a_second_user_command_supersedes_the_correction_still_in_flight(
    monkeypatch,
) -> None:
    """Two drags on the same rail must not race two live corrections (#1156).

    Before the pair-keyed registry, the user seam awaited its correction
    inline and registered nothing, so a second overlapping drag ran its own
    independent correction: two brackets open on the same rail pair, and the
    stale first drag's target could still land on the motor. Registering the
    task the same way the external path does lets the second drag cancel the
    first's still-in-flight correction outright, so exactly one bracket is
    ever open and only the live drag's target is ever dispatched.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 6 + [70], _MIDDLE: [100]}, position=40
    )
    coord = _user_seam_coordinator(
        cmd_svc, policy, entities=[_BOTTOM, _MIDDLE], monkeypatch=monkeypatch
    )
    coord.manual_ignore_external = False
    coord._event_buffer = MagicMock()
    cmd_svc.is_entity_unavailable = lambda _eid: False

    with _patch_caps():
        first, second = await asyncio.gather(
            coord.async_apply_user_position(_MIDDLE, 40, trigger="set_axes"),
            coord.async_apply_user_position(_MIDDLE, 70, trigger="set_axes"),
        )

    assert first == ("skipped", "interlock_superseded")
    assert second == ("sent", "set_cover_position")
    # Both writes land under the same pair key, so the dict never grows past
    # one live entry — the second write replaces the first's rather than
    # adding beside it.
    assert set(coord._external_interlock_tasks) == {frozenset({_BOTTOM, _MIDDLE})}


@pytest.mark.asyncio
async def test_an_outer_cancellation_is_not_swallowed_as_a_supersede(
    monkeypatch,
) -> None:
    """Entry unload / HA shutdown must not read as an ordinary supersede (#1156).

    ``task.cancelled()`` is true both when a LATER command supersedes this one
    and when the SERVICE-CALL task itself is cancelled from outside (an entry
    unload, HA shutdown). Only the first case may return a normal
    ``("skipped", ...)`` tuple; the second must propagate ``CancelledError``
    untouched — swallowing it would make an unload look like an ordinary skip
    instead of a torn-down integration. The discriminator is
    ``asyncio.current_task().cancelling()``: non-zero only when the awaiting
    task (this one) was itself asked to cancel.
    """
    cmd_svc, policy, _rails, _events = _harness(
        monkeypatch, script={_BOTTOM: [100] * 4 + [40], _MIDDLE: [100]}, position=40
    )
    coord = _user_seam_coordinator(
        cmd_svc, policy, entities=[_BOTTOM, _MIDDLE], monkeypatch=monkeypatch
    )
    coord.manual_ignore_external = False
    coord._event_buffer = MagicMock()
    cmd_svc.is_entity_unavailable = lambda _eid: False

    # Hang the FOLLOWER's own re-dispatch in the clearance gate deterministically
    # — no wall-clock race — while leaving the LEADING rail's gate real, since
    # the leading rail of any plan is always clear by construction.
    real_gate = policy.await_dispatch_clearance
    parked = asyncio.Event()

    async def _gate(entity_id, **kwargs):
        if entity_id == _MIDDLE:
            parked.set()
            await asyncio.sleep(3600)
        return await real_gate(entity_id, **kwargs)

    policy.await_dispatch_clearance = _gate

    with _patch_caps():
        outer = asyncio.create_task(
            coord.async_apply_user_position(_MIDDLE, 40, trigger="set_axes")
        )
        await parked.wait()
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer


@pytest.mark.asyncio
async def test_signal_zero_releases_the_follower_on_silent_hardware(
    monkeypatch,
) -> None:
    """Concurrency ON: ACP's own booked target is the only usable start signal.

    The leading rail here never reports anything — its state stays ``open`` and
    its position never leaves 100 — so the transit-state signal and the
    position-delta signal are both structurally dead. Without signal 0 the
    follower waits out the whole clearance budget and is withheld; with it, the
    fact that ACP commanded the leader to a clearing target IS the evidence.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch,
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=40,
        concurrent=True,
    )
    ctx = _rail_context(policy)
    _coord(cmd_svc, policy, ctx)
    cmd_svc.set_target(_BOTTOM, 40)  # clears the middle rail's own 40
    cmd_svc.set_waiting(_BOTTOM, True)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 40, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_MIDDLE}" in _sends(events)


@pytest.mark.asyncio
async def test_without_signal_zero_the_same_silent_move_is_withheld(
    monkeypatch,
) -> None:
    """The control for the test above: no accessor, same silent rail, no release.

    Pins that signal 0 is what does the work rather than some other disjunct
    quietly firing — an assertion the positive test alone cannot make.
    """
    cmd_svc, policy, _rails, events = _harness(
        monkeypatch,
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=40,
        concurrent=True,
    )
    ctx = _rail_context(policy)
    _coord(cmd_svc, policy, ctx)
    policy._get_booked_target = None
    cmd_svc.set_target(_BOTTOM, 40)
    cmd_svc.set_waiting(_BOTTOM, True)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 40, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in _sends(events)
