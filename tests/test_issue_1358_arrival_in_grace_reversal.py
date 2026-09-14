"""End-to-end regression test for issue #1358.

Root cause: ``StateClassifier.classify`` returns unconditionally while the
entity is in the 5s command grace window, BEFORE ``check_target_reached`` is
ever consulted. On a cover whose full 0->100 travel completes in well under
``COMMAND_GRACE_PERIOD_SECONDS`` (the reporter's Tuya roller shutter streams
~35 intermediate positions across ~0.87s of travel), the arrival event
ALWAYS lands inside grace and the classifier's own tolerance check is never
run — ``wait_for_target`` can then only ever be cleared by the 45s transit
timeout.

Left uncorrected, the eventual user reversal's first sample —
``old_position == target, position != target`` — is byte-identical to the
#518 optimistic-firmware-echo signature, so ``_check_optimistic_guard`` fires
and swallows the reversal as ``transit_optimistic_target_replay`` instead of
engaging the manual override.

This test drives the real two-event sequence end-to-end through
``AdaptiveDataUpdateCoordinator.process_entity_state_change`` (mirroring the
``tests/test_issue_285_open_state_cover_false_override.py`` harness) with
``cmd_svc._position_tolerance = 3`` (the reporter's effective threshold) and
a dispatch origin of 0:

  event 1: old=95 -> new=100, target=100, grace ACTIVE (arrival inside grace)
  event 2: old=100 -> new=95, target=100, grace EXPIRED (the user's reversal)

After event 2, ``wait_for_target`` must be False and the buffer must NOT
contain ``transit_optimistic_target_replay`` — the arrival in event 1 must
have already cleared ``wait_for_target``, so event 2 never even reaches the
#518 guard (which is gated on ``is_waiting_for_target``).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — a persistent-state variant of the test_issue_285 harness: a
# SINGLE coordinator/cmd_svc is reused across two events (rather than one
# fresh pair per call) so wait_for_target, target, and position_at_send carry
# forward from event 1 into event 2, exactly like a real cover session.
# ---------------------------------------------------------------------------


def _make_state_change_data(
    entity_id: str,
    new_position: int,
    old_position: int,
    new_state_str: str = "open",
    old_state_str: str | None = None,
):
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = new_state_str
    event.new_state.attributes = {"current_position": new_position}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.old_state = MagicMock()
    event.old_state.state = (
        old_state_str if old_state_str is not None else new_state_str
    )
    event.old_state.attributes = {"current_position": old_position}
    return event


def _make_coordinator(entity_id: str, target_position: int):
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    coordinator = MagicMock()
    coordinator.ignore_intermediate_states = False
    coordinator._target_just_reached = set()

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    coordinator._grace_mgr = grace_mgr

    event_buffer = EventBuffer(maxlen=50)

    cmd_svc = CoverCommandService(
        hass=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=5,
        transit_timeout_seconds=45,
        event_buffer=event_buffer,
    )
    # The reporter's effective threshold (manual_threshold=None floors to
    # POSITION_TOLERANCE_PERCENT=3), applied directly since the constructor's
    # default differs.
    cmd_svc._position_tolerance = 3
    cmd_svc.set_target(entity_id, target_position)
    cmd_svc.set_waiting(entity_id, True)
    cmd_svc.state(entity_id).position_at_send = 0
    cmd_svc.state(entity_id).sent_at = dt.datetime.now(dt.UTC)

    cmd_svc.get_cover_capabilities = lambda eid: {"has_set_position": True}

    def _read_position(eid, caps, state_obj):
        return state_obj.attributes.get("current_position")

    cmd_svc.read_position_with_capabilities = _read_position
    coordinator._cmd_svc = cmd_svc

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator._is_in_grace_period = lambda eid: (
        AdaptiveDataUpdateCoordinator._is_in_grace_period(coordinator, eid)
    )
    coordinator._start_grace_period = lambda eid: (
        AdaptiveDataUpdateCoordinator._start_grace_period(coordinator, eid)
    )

    return coordinator, event_buffer


def _apply_event(
    coordinator,
    entity_id: str,
    *,
    new_position: int,
    old_position: int,
    grace_expired: bool,
    sent_seconds_ago: float | None = None,
    new_state_str: str = "open",
    old_state_str: str | None = None,
):
    coordinator.state_change_data = _make_state_change_data(
        entity_id, new_position, old_position, new_state_str, old_state_str
    )
    grace_mgr = coordinator._grace_mgr
    if grace_expired:
        grace_mgr._command_timestamps.pop(entity_id, None)
    else:
        grace_mgr._command_timestamps[entity_id] = dt.datetime.now().timestamp()
    if sent_seconds_ago is not None:
        now = dt.datetime.now(dt.UTC)
        coordinator._cmd_svc.state(entity_id).sent_at = now - dt.timedelta(
            seconds=sent_seconds_ago
        )

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    AdaptiveDataUpdateCoordinator.process_entity_state_change(coordinator)


@pytest.mark.unit
def test_reversal_after_fast_arrival_engages_manual_override() -> None:
    """Fast arrival inside grace, then a reversal, must engage manual override.

    Reproduces the reporter's Tuya roller shutter shape: a full travel
    completes well under COMMAND_GRACE_PERIOD_SECONDS, so the arrival lands
    inside grace. If that arrival doesn't clear wait_for_target, the user's
    subsequent reversal reads as a #518 optimistic-echo and is swallowed.
    """
    entity_id = "cover.shutters_office_lenka_curtain"
    coordinator, event_buffer = _make_coordinator(entity_id, target_position=100)

    # Event 1: the cover arrives at its target (100) while grace is still
    # active — the dispatched command's grace window has not yet expired,
    # but the cover already reported an intermediate (95) on the way there.
    _apply_event(
        coordinator,
        entity_id,
        new_position=100,
        old_position=95,
        grace_expired=False,
    )
    assert coordinator._cmd_svc.is_waiting_for_target(entity_id) is False, (
        "A verified arrival (95 -> 100, target 100) must clear "
        "wait_for_target even while the command grace period is still "
        "active — otherwise only the 45s transit timeout can ever clear it."
    )

    # Event 2: the user reverses the cover. Grace from the original command
    # has now expired (sent_seconds_ago simulates elapsed wall-clock time).
    _apply_event(
        coordinator,
        entity_id,
        new_position=95,
        old_position=100,
        grace_expired=True,
        sent_seconds_ago=14.5,
    )

    assert coordinator._cmd_svc.is_waiting_for_target(entity_id) is False
    buffer_events = [e["event"] for e in event_buffer.snapshot()]
    assert "transit_optimistic_target_replay" not in buffer_events, (
        "The reversal must not be misread as a #518 optimistic-target-replay "
        "echo. wait_for_target was already cleared by the in-grace arrival in "
        "event 1, so event 2 must never even reach the #518 guard (which "
        "only runs while is_waiting_for_target is True)."
    )
