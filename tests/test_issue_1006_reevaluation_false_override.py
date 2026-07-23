"""Issue #1006: false manual override after a handler reevaluation mid-transit.

Scenario (venetian ``position_and_tilt``, WAREMA/KNX):

ACP dispatches ``open_cover`` (+ tilt=70) to a cover. A few tens of
milliseconds later a transiently-unavailable input recovers and ACP
**reevaluates its handler state** — a different handler now wins with a
different position/tilt target — **while the open movement is still
travelling**, and dispatches NO replacement command. Seconds later the KNX
actuator reaches the commanded open end-position (position≈100, tilt≈70).

The manual-override gate must recognise that end-of-travel feedback as ACP's
OWN movement. The defect: the tilt-axis ``SecondaryAxisCheck.expected`` was
sourced from the freshly-reevaluated ``PipelineResult`` (tilt=30) rather than
the tilt ACP actually DISPATCHED (70), so the end-of-travel tilt=70 read a
delta of 40% and tripped a false ``manual_override_set``.

The fix anchors the correlation to the value ACP last DISPATCHED per axis:
the tilt axis reads ``sequencer.last_tilt_target(entity_id)`` (the stored
commanded tilt) instead of the mutable per-cycle pipeline value. The position
axis was already robust — a reevaluation that dispatches nothing never
re-anchors the recorded target (``_prepare_service_call`` is the sole writer,
and it only runs on an actual send) and the venetian post-tilt rebase
early-returns at the endpoint (drift≈0). Test C pins that robustness.

Tests A/B/D drive the post-fix 3-arg ``policy.secondary_axis_check(result,
cmd_svc, entity_id)`` accessor exactly as the coordinator does, so the tilt
expected value is derived, not hand-fed.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

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


def test_tilt_expected_is_dispatched_target_not_reevaluated() -> None:
    """``SecondaryAxisCheck.expected`` binds to the DISPATCHED tilt (70), not 30.

    ACP dispatched tilt=70 (stored in ``_tilt_targets``). A reevaluation then
    recomputed tilt=30 with no replacement send. The check the coordinator
    builds from the reevaluated result must still expect 70 — the tilt ACP is
    actually driving toward — so the end-of-travel tilt=70 is not misread.
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_attached_policy(entity_id)
    # ACP dispatched tilt=70 — the value the actuator is travelling toward.
    policy.sequencer._tilt_targets[entity_id] = 70

    cmd_svc = _make_cmd_svc()
    result = _reevaluated_result(position=30, tilt=30)

    check = policy.secondary_axis_check(result, cmd_svc, entity_id)

    assert check is not None
    assert check.expected == 70, (
        "tilt expected must be the dispatched target (70), not the mid-transit "
        f"reevaluated pipeline value (30); got {check.expected}"
    )


# ---------------------------------------------------------------------------
# Test B — end-to-end tilt: settle at commanded tilt after reevaluation
# ---------------------------------------------------------------------------


def test_endpoint_tilt_arrival_after_reevaluation_is_not_manual() -> None:
    """Actuator settles at the commanded tilt (70) after a reevaluation to 30.

    The position axis reached its recorded target (100). The end-of-travel
    feedback (position=100, tilt=70) must NOT engage manual override — it is
    ACP's own commanded movement completing.
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_attached_policy(entity_id)
    policy.sequencer._tilt_targets[entity_id] = 70

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 100)  # dispatched open → recorded target 100

    result = _reevaluated_result(position=30, tilt=30)

    # Coordinator derivation (post-fix form): tilt expected from dispatched.
    check = policy.secondary_axis_check(result, cmd_svc, entity_id)
    recorded_target = cmd_svc.get_target(entity_id)
    expected_position = recorded_target  # 100

    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=100, tilt=70),
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
        "end-of-travel arrival at the commanded tilt after a reevaluation must "
        "NOT trip manual override"
    )


# ---------------------------------------------------------------------------
# Test C — position axis is robust to a no-dispatch reevaluation
# ---------------------------------------------------------------------------


def test_position_anchor_survives_reevaluation_without_dispatch() -> None:
    """A reevaluation that dispatches nothing must not orphan the position anchor.

    The recorded target is written only by the dispatch chokepoint, so a
    no-dispatch reevaluation leaves it intact (100). The venetian post-tilt
    rebase early-returns at the endpoint (actual≈target → drift≈0), so it does
    not re-anchor either. The endpoint arrival (position=100) is therefore
    correctly recognised as target-reached and stays automatic.
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_attached_policy(entity_id)

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 100)  # dispatched open
    cmd_svc.set_waiting(entity_id, True)

    # Reevaluation recomputes a different target but dispatches nothing — the
    # recorded target must be untouched (no _prepare_service_call ran).
    assert cmd_svc.get_target(entity_id) == 100

    # The real post-tilt rebase at the endpoint: actual == target → early
    # return, target stays 100 (does not re-anchor to a mid value).
    policy.sequencer._get_current_position = lambda _eid: 100
    policy.sequencer._set_commanded_position = lambda eid, pos: cmd_svc.set_target(
        eid, pos
    )
    policy.sequencer._rebase_commanded_position(entity_id, 100)
    assert cmd_svc.get_target(entity_id) == 100

    # Endpoint arrival: the recorded target is reached.
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


def test_combined_position_and_tilt_endpoint_after_reevaluation_is_not_manual() -> None:
    """The full #1006 incident: dispatch open+tilt=70, reevaluate to 30/30, no send.

    Both axes reach their commanded endpoints (position=100, tilt=70). Neither
    axis may misclassify the arrival: the tilt axis compares against the
    dispatched 70 (not the reevaluated 30) and the position axis against the
    intact recorded target (100).
    """
    entity_id = "cover.raffstore_wc"
    policy = _make_attached_policy(entity_id)
    policy.sequencer._tilt_targets[entity_id] = 70  # dispatched tilt

    cmd_svc = _make_cmd_svc()
    cmd_svc.set_target(entity_id, 100)  # dispatched open

    # Mid-transit reevaluation wants a different position AND tilt; sends nothing.
    result = _reevaluated_result(position=30, tilt=30)

    check = policy.secondary_axis_check(result, cmd_svc, entity_id)
    recorded_target = cmd_svc.get_target(entity_id)
    expected_position = recorded_target  # 100

    mgr = _make_manager(entity_id)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=100, tilt=70),
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
    ), "the incident's combined endpoint arrival must NOT trip manual override"
