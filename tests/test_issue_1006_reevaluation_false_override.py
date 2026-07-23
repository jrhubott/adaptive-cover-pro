"""Issue #1006: false manual override after a handler reevaluation mid-transit.

Scenario (venetian ``position_and_tilt``, WAREMA/KNX):

A solar cycle dispatches position=60 + tilt=45 to a cover (a producible tuple —
60 is below ``tilt_skip_above=95`` so the tilt is genuinely sent). A few tens of
milliseconds later a transiently-unavailable input recovers and ACP
**reevaluates its handler state** — a different handler now wins with a
different position/tilt target — **while the movement is still travelling**, and
dispatches NO replacement command. Seconds later the actuator settles at the
commanded values (position=60, tilt=45).

The manual-override gate must recognise that settle feedback as ACP's OWN
movement. The defect: the tilt-axis ``SecondaryAxisCheck.expected`` was sourced
from the freshly-reevaluated ``PipelineResult`` (tilt=20) rather than the tilt
ACP actually DISPATCHED (45), so the settle tilt=45 read a large delta and
tripped a false ``manual_override_set``.

The fix anchors the correlation to the value ACP last DISPATCHED per axis, in
one shared rule (:func:`resolve_dispatched_secondary_expected`) that the
venetian tilt axis delegates to: the check's expected reads
``sequencer.last_tilt_target(entity_id)`` (the stored commanded value) instead of
the mutable per-cycle pipeline value. When ACP dispatched no independent tilt
(empty anchor — suppress mode, HA restart, drift-verify pop, Auto-Control
off→on), the rule returns ``None`` and the check yields NO independent tilt
manual-detection rather than falling back to the reevaluated ``result.tilt``.

The position axis was already robust — a reevaluation that dispatches nothing
never re-anchors the recorded target (``_prepare_service_call`` is the sole
writer, and it only runs on an actual send), and the venetian post-tilt rebase
refuses to re-anchor while the carriage is mid-transit (drift beyond
``VENETIAN_REBASE_MAX_DRIFT_PERCENT``). Test C pins that refusal guard.

Tests A/B/D drive the tilt anchor through the real ``update_tilt_only`` dispatch
path and the post-fix 3-arg ``policy.secondary_axis_check(result, cmd_svc,
entity_id)`` accessor exactly as the coordinator does, so the tilt expected
value is derived, not hand-fed. The empty-anchor test covers the no-dispatched
case.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
    VenetianPolicy,
)
from custom_components.adaptive_cover_pro.managers.grace_period import (
    GracePeriodManager,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# The venetian sequencer otherwise waits on real-motor post-tilt delays.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _make_event(entity_id: str, *, position: int | None, tilt: int | None):
    """Build a fake StateChangedData reporting both axes."""
    attrs: dict = {}
    if position is not None:
        attrs["current_position"] = position
    if tilt is not None:
        attrs["current_tilt_position"] = tilt
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "open"
    event.new_state.attributes = attrs
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.old_state = None
    return event


def _make_manager(entity_id: str) -> AdaptiveCoverManager:
    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.add_covers([entity_id])
    return mgr


def _make_attached_policy(entity_id: str) -> VenetianPolicy:
    """Build a VenetianPolicy with a real DualAxisSequencer attached.

    Grace is NOT stamped — the tilt correlation under test is the stored
    dispatched-tilt anchor, not any time-based grace window.
    """
    policy = VenetianPolicy()
    policy.attach(
        hass=MagicMock(),
        logger=MagicMock(),
        grace_mgr=GracePeriodManager(logger=MagicMock()),
        get_current_position=lambda _eid: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "open",
        venetian_mode="position_and_tilt",
    )
    return policy


def _make_cmd_svc():
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    return CoverCommandService(
        hass=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_venetian",
        grace_mgr=grace_mgr,
        position_tolerance=5,
        transit_timeout_seconds=45,
    )


def _reevaluated_result(*, position: int, tilt: int) -> PipelineResult:
    """Return the different position/tilt a mid-transit reevaluation computed."""
    return PipelineResult(
        position=position,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        tilt=tilt,
    )


# ---------------------------------------------------------------------------
# Test A — tilt unit: expected must be the DISPATCHED tilt, not the reeval value
# ---------------------------------------------------------------------------


async def test_tilt_expected_is_dispatched_target_not_reevaluated() -> None:
    """``SecondaryAxisCheck.expected`` binds to the DISPATCHED tilt (45), not 20.

    A solar-tracking cycle dispatches tilt=45 at position=60 through the real
    sequencer path (a producible tuple — 60 is below ``tilt_skip_above=95`` so
    the tilt is not skipped). A reevaluation then recomputes tilt=20 with no
    replacement send. The check the coordinator builds from the reevaluated
    result must still expect 45 — the tilt ACP is actually driving toward — so
    the end-of-travel tilt=45 is not misread.
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_dispatch_venetian_policy()
    # Real solar-cycle dispatch → _tilt_targets populated by production code.
    await _dispatch_tilt(policy, entity_id, tilt=45, position=60)
    assert policy.sequencer.last_tilt_target(entity_id) == 45

    cmd_svc = _make_cmd_svc()
    result = _reevaluated_result(position=30, tilt=20)

    check = policy.secondary_axis_check(result, cmd_svc, entity_id)

    assert check is not None
    assert check.expected == 45, (
        "tilt expected must be the dispatched target (45), not the mid-transit "
        f"reevaluated pipeline value (20); got {check.expected}"
    )


# ---------------------------------------------------------------------------
# Test B — end-to-end tilt: settle at commanded tilt after reevaluation
# ---------------------------------------------------------------------------


async def test_endpoint_tilt_arrival_after_reevaluation_is_not_manual() -> None:
    """Actuator settles at the commanded tilt (45) after a reevaluation to 20.

    A solar cycle dispatched position=60 + tilt=45 (a producible tuple). The
    position axis reached its recorded target (60). The settle feedback
    (position=60, tilt=45) must NOT engage manual override — it is ACP's own
    commanded movement completing.
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_dispatch_venetian_policy()
    await _dispatch_tilt(policy, entity_id, tilt=45, position=60)

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 60)  # solar cycle commanded position 60

    result = _reevaluated_result(position=30, tilt=20)

    # Coordinator derivation (post-fix form): tilt expected from dispatched.
    check = policy.secondary_axis_check(result, cmd_svc, entity_id)
    recorded_target = cmd_svc.get_target(entity_id)
    expected_position = recorded_target  # 60

    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=60, tilt=45),
        our_state=expected_position,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        has_recorded_target=recorded_target is not None,
        secondary_axis_check=check,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert not mgr.is_cover_manual(entity_id), (
        "settle at the commanded tilt after a reevaluation must NOT trip manual "
        "override"
    )


# ---------------------------------------------------------------------------
# Test C — position axis: a MID-TRANSIT venetian rebase must NOT orphan the
# dispatched endpoint. Exercises the real ``VENETIAN_REBASE_MAX_DRIFT_PERCENT``
# refusal branch (not a drift-0 early-return): the one production path that
# could re-anchor the recorded position target without a fresh dispatch.
# ---------------------------------------------------------------------------


def test_mid_transit_rebase_does_not_orphan_dispatched_position_endpoint() -> None:
    """A large-drift post-tilt rebase mid-transit must leave the target intact.

    ACP dispatched ``open_cover`` → recorded target 100. The venetian post-tilt
    rebase (``_rebase_commanded_position``, the sole non-dispatch path that can
    rewrite the recorded position target) fires WHILE the carriage is still far
    from the endpoint (actual=40, drift=60% ≫ ``VENETIAN_REBASE_MAX_DRIFT_PERCENT
    =15``). Its refusal guard must decline to rebase, so the recorded target
    stays 100. When the carriage later reaches the physical endpoint (100), the
    arrival is recognised as target-reached and stays automatic.

    Counterfactual that makes this a real guard: if the refusal branch were
    removed, the target would rebase to 40, and the endpoint arrival at 100
    (delta 60) would be misclassified as a manual move — exactly the #1006
    orphaned-anchor failure, on the position axis.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_REBASE_MAX_DRIFT_PERCENT,
    )

    entity_id = "cover.raffstore_wc"
    policy = _make_attached_policy(entity_id)

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 100)  # dispatched open
    cmd_svc.set_waiting(entity_id, True)

    # Mid-transit: the carriage is at 40, still travelling toward 100.
    mid_transit_actual = 40
    drift = abs(mid_transit_actual - 100)
    assert drift > VENETIAN_REBASE_MAX_DRIFT_PERCENT  # refusal branch is exercised

    policy.sequencer._get_current_position = lambda _eid: mid_transit_actual
    policy.sequencer._set_commanded_position = lambda eid, pos: cmd_svc.set_target(
        eid, pos
    )
    policy.sequencer._rebase_commanded_position(entity_id, 100)

    # Guard held: the recorded target was NOT re-anchored to the mid value.
    assert cmd_svc.get_target(entity_id) == 100

    # Later endpoint arrival: the intact recorded target is reached.
    assert cmd_svc.check_target_reached(entity_id, 100) is True

    recorded_target = cmd_svc.get_target(entity_id)
    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=100, tilt=None),
        our_state=recorded_target,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        has_recorded_target=recorded_target is not None,
        secondary_axis_check=None,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )
    assert not mgr.is_cover_manual(entity_id)


# ---------------------------------------------------------------------------
# Test D — the literal incident: combined position_and_tilt endpoint arrival
# ---------------------------------------------------------------------------


async def test_combined_position_and_tilt_endpoint_after_reevaluation_is_not_manual() -> (
    None
):
    """The full #1006 incident: dispatch position=60+tilt=45, reevaluate to 30/20.

    A solar cycle dispatched a producible tuple (position=60, tilt=45). A
    mid-transit reevaluation recomputes position=30 + tilt=20 and sends nothing.
    Both axes reach their commanded values (position=60, tilt=45). Neither axis
    may misclassify the arrival: the tilt axis compares against the dispatched 45
    (not the reevaluated 20) and the position axis against the intact recorded
    target (60).
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_dispatch_venetian_policy()
    await _dispatch_tilt(policy, entity_id, tilt=45, position=60)  # dispatched tilt

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 60)  # solar cycle commanded position

    # Mid-transit reevaluation wants a different position AND tilt; sends nothing.
    result = _reevaluated_result(position=30, tilt=20)

    check = policy.secondary_axis_check(result, cmd_svc, entity_id)
    recorded_target = cmd_svc.get_target(entity_id)
    expected_position = recorded_target  # 60

    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=60, tilt=45),
        our_state=expected_position,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        has_recorded_target=recorded_target is not None,
        secondary_axis_check=check,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert not mgr.is_cover_manual(
        entity_id
    ), "the incident's combined settle arrival must NOT trip manual override"


# ---------------------------------------------------------------------------
# Real-dispatch helpers: drive the tilt anchor through the public sequencer path
# so the stored ``_tilt_targets`` value is one the real dispatch path actually
# produces, not a hand-poked number.
# ---------------------------------------------------------------------------


def _make_dispatch_hass() -> MagicMock:
    """Return a hass whose ``services.async_call`` is awaitable so a send runs."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


def _make_quiet_grace_mgr() -> MagicMock:
    """Return a grace manager that never spawns a timeout task nor reports grace.

    A real ``GracePeriodManager.start_command_grace_period`` (fired by the
    sequencer on every send) spawns an asyncio timer that outlives the test.
    Mocking it keeps the driven dispatch timer-free, and forcing
    ``is_in_command_grace_period`` to ``False`` stops the suppression callback
    from swallowing the check — so these tests exercise the anchor, not grace.
    """
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = lambda _eid: False
    return grace_mgr


def _make_dispatch_venetian_policy() -> VenetianPolicy:
    """Return a VenetianPolicy whose sequencer can actually dispatch a tilt send.

    Uses an awaitable ``async_call`` and a timer-free grace mock so a real
    ``update_tilt_only`` runs, populating ``_tilt_targets`` through the same code
    a solar-tracking cycle uses (producible anchor values, not hand-poked
    numbers).
    """
    policy = VenetianPolicy()
    policy.attach(
        hass=_make_dispatch_hass(),
        logger=MagicMock(),
        grace_mgr=_make_quiet_grace_mgr(),
        get_current_position=lambda _eid: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "open",
        venetian_mode="position_and_tilt",
    )
    return policy


async def _dispatch_tilt(policy, entity_id: str, *, tilt: int, position: int) -> None:
    """Drive a real tilt send through the public sequencer path.

    Populates ``_tilt_targets[entity_id]`` via the same code a solar-tracking
    cycle uses — the anchor the manual-override check must read.
    """
    await policy.sequencer.update_tilt_only(
        entity_id, tilt_target=tilt, current_position=position, reason="solar"
    )


# ---------------------------------------------------------------------------
# Empty anchor (suppress mode / restart / Auto-Control off→on):
# no dispatched tilt → NO independent tilt manual-detection (not result.tilt)
# ---------------------------------------------------------------------------


def test_empty_tilt_anchor_yields_no_tilt_manual_detection() -> None:
    """No dispatched tilt anchor → the tilt axis must NOT assert an expectation.

    The reporter's WAREMA exterior raffstore runs ``venetian_tilt_skip_mode =
    suppress``: opening above ``tilt_skip_above`` dispatches NO independent tilt,
    so ``last_tilt_target`` is empty (the same state after an HA restart, a
    drift-verify pop, or an Auto-Control off→on ``clear_tilt_targets``). ACP has
    no commanded tilt reference to police here.

    A mid-transit reevaluation computes some ``result.tilt`` (20). The actuator
    then settles at the coupled open endpoint, publishing tilt≈100. Falling back
    to the reevaluated ``result.tilt`` (the pre-fix behaviour) makes |20−100|=80
    read as a manual move. With no dispatched anchor, the secondary-axis check
    must yield NO independent tilt manual-detection instead.
    """
    entity_id = "cover.raffstore_wc_suppress"
    policy = _make_attached_policy(entity_id)
    # No tilt dispatched — anchor is empty.
    assert policy.sequencer.last_tilt_target(entity_id) is None

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 100)  # dispatched open (position axis only)
    result = _reevaluated_result(position=20, tilt=20)

    check = policy.secondary_axis_check(result, cmd_svc, entity_id)
    recorded_target = cmd_svc.get_target(entity_id)

    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        # Coupled open endpoint: position reached (100), tilt follows to ≈100.
        states_data=_make_event(entity_id, position=100, tilt=100),
        our_state=recorded_target,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        has_recorded_target=recorded_target is not None,
        secondary_axis_check=check,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert not mgr.is_cover_manual(entity_id), (
        "with no dispatched tilt anchor, the coupled end-of-travel tilt must NOT "
        "be misclassified as a manual move via the reevaluated result.tilt"
    )
