"""Tests for issue #518: false manual override for optimistic covers during transit.

Root cause: optimistic covers (firmware reports the commanded target position first,
then the real intermediate position as the motor travels) defeat the forward-progress
guard in state_classifier.py.

When old_position == target (optimistic report) and the next position is a real
intermediate (e.g. 48 vs target 20), the progress check computes:
  old_distance = |20 - 20| = 0
  new_distance = |48 - 20| = 28
  new_distance < old_distance  →  False  (28 < 0)

This is misread as drift-away (the #285 manual branch), clears wait_for_target
mid-transit. The next position event trips delta >= 3% → false manual override.

Real diagnostics (v2.25.0, 14:37:09):
  cover_command_sent  position=20  (P40 solar; cover was at 75)
  manual_override_rejected_wait_for_target  our_state=20  (grace active)
  grace_period_expired  (5s; cover STILL traveling 75→20)
  transit_cleared  position=48  old_position=20  target=20  cover_state="open"
  manual_override_set  our_state=20  new_position=48  delta 28.0% >= 3%  ← FALSE

Fix: before the direction/progress block, add an optimistic-target guard that
detects the old_position == target signature and restarts the grace period
(mirroring the #186 step-motor-pause pattern).
"""

from __future__ import annotations

import datetime as dt

import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers — mirror the test_issue_285 harness exactly
# ---------------------------------------------------------------------------


def _make_state_change_data(
    entity_id: str,
    new_position: int,
    old_position: int = 0,
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
    event.old_state.state = old_state_str if old_state_str is not None else new_state_str
    event.old_state.attributes = {"current_position": old_position}
    return event


def _make_coordinator(
    entity_id: str,
    target_position: int,
    current_position: int,
    old_position: int = 0,
    *,
    grace_expired: bool = True,
    ignore_intermediate: bool = False,
    new_state_str: str = "open",
    old_state_str: str | None = None,
    sent_seconds_ago: float = 5.1,
    last_progress_seconds_ago: float | None = None,
    transit_timeout_seconds: int = 45,
    event_buffer=None,
):
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    coordinator = MagicMock()
    coordinator.state_change_data = _make_state_change_data(
        entity_id, current_position, old_position, new_state_str, old_state_str
    )
    coordinator.ignore_intermediate_states = ignore_intermediate
    coordinator._target_just_reached = set()

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    if not grace_expired:
        grace_mgr._command_timestamps[entity_id] = dt.datetime.now().timestamp()
    coordinator._grace_mgr = grace_mgr

    cmd_svc = CoverCommandService(
        hass=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=5,
        transit_timeout_seconds=transit_timeout_seconds,
        event_buffer=event_buffer,
    )
    cmd_svc.set_target(entity_id, target_position)
    cmd_svc.set_waiting(entity_id, True)

    now = dt.datetime.now(dt.UTC)
    cmd_svc.state(entity_id).sent_at = now - dt.timedelta(seconds=sent_seconds_ago)

    if last_progress_seconds_ago is not None:
        cmd_svc.state(entity_id).last_progress_at = now - dt.timedelta(
            seconds=last_progress_seconds_ago
        )

    # Wrap record_progress so tests can assert it was called.
    cmd_svc.record_progress = MagicMock(wraps=cmd_svc.record_progress)

    cmd_svc.get_cover_capabilities = lambda eid: {"has_set_position": True}

    def _read_position(eid, caps, state_obj):
        if state_obj is coordinator.state_change_data.new_state:
            return current_position
        if state_obj is coordinator.state_change_data.old_state:
            return old_position
        return current_position

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

    return coordinator


def _call(coordinator):
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    AdaptiveDataUpdateCoordinator.process_entity_state_change(coordinator)


# ===========================================================================
# Issue #518: optimistic covers — firmware reports commanded target first,
# then real intermediate positions as the motor travels.
# ===========================================================================


class TestOptimisticTargetReplay:
    """Optimistic covers must not trigger false manual overrides during transit."""

    @pytest.mark.asyncio
    async def test_optimistic_target_replay_keeps_wait_for_target(self) -> None:
        """Exact reproduction of the real diagnostics sequence (issue #518).

        Timeline (reproduced from v2.25.0 event_buffer):
          cover_command_sent  position=20  (cover was at 75)
          grace_period_expired  (~5.1s; cover still traveling 75→20)
          old_position=20 (optimistic firmware report == target)
          new_position=48  (real intermediate on the way from 75 to 20)
          cover_state="open" throughout (never emits opening/closing)

        Expected: wait_for_target must remain True — the cover is still
        mid-transit.  Today it is incorrectly cleared because the progress
        check sees old_distance=0, new_distance=28 → not forward progress →
        treats it as drift-away and clears the flag.
        """
        entity_id = "cover.office_roller_shutter_switch_2"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=48,  # real intermediate (traveling 75→20)
            old_position=20,  # optimistic firmware report == target
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=5.1,  # grace just expired (grace=5s)
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True, (
            "wait_for_target must remain True: the cover firmware reported the "
            "commanded target optimistically, then updated to the real intermediate "
            "position. This is transit, not a manual override."
        )
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_optimistic_target_replay_records_buffer_event(self) -> None:
        """A transit_optimistic_target_replay event must be recorded in the buffer."""
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )

        entity_id = "cover.office_roller_shutter_switch_2"
        buf = EventBuffer(maxlen=50)
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=48,
            old_position=20,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=5.1,
            transit_timeout_seconds=45,
            event_buffer=buf,
        )
        # Wire the event_buffer into the classifier via cmd_svc
        from custom_components.adaptive_cover_pro.managers.cover_command.state_classifier import (
            StateClassifier,
        )

        coord._cmd_svc._state_classifier = StateClassifier(
            coord._cmd_svc,
            event_buffer=buf,
            debug_log=lambda cat, msg, *args: None,
        )

        _call(coord)

        events = [e["event"] for e in buf._buf]
        assert "transit_optimistic_target_replay" in events, (
            f"Expected transit_optimistic_target_replay event in buffer. "
            f"Got: {events}"
        )
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_optimistic_target_replay_restarts_grace(self) -> None:
        """Grace period must be restarted so subsequent intermediate positions are suppressed."""
        entity_id = "cover.office_roller_shutter_switch_2"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=48,
            old_position=20,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=5.1,
            transit_timeout_seconds=45,
        )
        _call(coord)
        # Grace must have been restarted — the cover is now in grace period
        assert coord._grace_mgr.is_in_command_grace_period(entity_id), (
            "Grace period must be restarted after detecting optimistic-target replay "
            "so that subsequent intermediate positions are suppressed"
        )
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_optimistic_target_replay_resets_progress_clock(self) -> None:
        """record_progress must be called to extend the backstop window."""
        entity_id = "cover.office_roller_shutter_switch_2"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=48,
            old_position=20,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=5.1,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.record_progress.called, (
            "record_progress must be called when the optimistic-target guard fires "
            "so the transit-timeout backstop window is reset"
        )
        coord._grace_mgr.cancel_all()


# ===========================================================================
# Regression — true drift-away (#285) must still be detected
# ===========================================================================


class TestRegressionTrueDriftAway:
    """Genuine drift-away (old_position != target) must still clear wait_for_target."""

    def test_true_drift_away_still_clears_wait_for_target(self) -> None:
        """Cover moving away from target must still be treated as manual override.

        old_position=60, target=0, new_position=70: cover moving AWAY.
        old_position (60) != target (0) — the optimistic guard must not fire.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=70,
            old_position=60,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=10.0,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "Cover moving away from target (60→70, target=0) must clear "
            "wait_for_target — genuine manual move (#285 regression guard)"
        )

    def test_cover_at_target_legitimately_drifts_away(self) -> None:
        """Cover that genuinely settled at target then was moved manually.

        old_position=20 (== target 20), but sent_at was >45s ago (backstop).
        The guard must not protect this — backstop should clear it.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=48,
            old_position=20,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=50.0,  # > 45s transit_timeout → backstop fires
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "Optimistic guard must NOT fire when transit_elapsed_without_progress "
            "> transit_timeout_seconds — backstop clears wait_for_target at 45s"
        )


# ===========================================================================
# Regression — existing #285/#271/#172/#186 scenarios must remain unchanged
# ===========================================================================


class TestRegressionExistingScenarios:
    """All pre-existing behavior must be unchanged after the #518 fix."""

    def test_forward_progress_open_state_keeps_wait_for_target(self) -> None:
        """Open-state cover making genuine forward progress must stay True (#285)."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=10.0,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True

    def test_stalled_beyond_timeout_clears_wait_for_target(self) -> None:
        """Open-state cover with no progress > timeout must be cleared (#271)."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=80,
            old_position=80,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=50.0,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False

    def test_open_from_open_same_position_clears_wait_for_target(self) -> None:
        """Cover stuck at same position with open→open clears wait_for_target (#172)."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=51,
            new_state_str="open",
            old_state_str="open",
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False
