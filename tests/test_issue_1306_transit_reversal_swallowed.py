"""Tests for issue #1306: user reversal during transit is never detected as manual.

Root cause: TWO independent suppression arms in ``StateClassifier.classify``
both judge causation from position numbers alone, and both match the exact
event a mid-transit user reversal produces:

- The step-motor pause guard (issue #186) restarts the command grace period
  whenever a cover transitions from a moving state ("opening"/"closing") to a
  settled state at a position that is not the target — without checking
  whether the event shows any progress TOWARD the target. A cover that a user
  physically reverses (wall switch / remote) reports exactly that signature:
  ``opening`` → ``closed`` with zero (or negative) progress.
- The unreacted-command guard (issue #1139) keeps ``wait_for_target`` whenever
  ``old_position == position == position_at_send`` — true for a reversal on a
  cover that publishes no intermediate positions, because
  ``position_at_send`` is stamped at dispatch and the single reported event
  never moves off it.

Either arm returning early leaves ``wait_for_target`` alive, the
manual-override detector never runs, ACP re-sends the command, and the loop
repeats — the override can structurally never engage (reported with a Velux
roof window via KLF-200, which publishes no intermediate positions: every
cycle is ``opening``(0) → 45 s → ``closed``(0)).

Fix: a single ``reversal_after_ack`` predicate — the cover acked the command
by entering transit and then left transit without getting any closer to the
target — is consulted by BOTH arms. The signature both canonical #186
scenarios share (46→51 toward 100; 50→40 toward 20) always advances, so arm 1
still fires for them; every #1139 scenario never acks with a transit state at
all, so arm 2 still fires for those. No-progress and moved-away reversal
events fall through both arms to the existing direction/progress branches,
which clear ``wait_for_target`` so the manual-override detector runs on the
same event.

Credit: @litithio (PR #1239) found the mechanism and wrote the first correct
half of the fix — the #186 arm's progress term. This file extends his tests
with the ``position_at_send`` dimension his fix did not cover and an
end-to-end assertion that the override actually engages.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.test_issue_285_open_state_cover_false_override import (
    _call,
    _make_coordinator,
)

# ===========================================================================
# Issue #1306: reversal during transit must clear wait_for_target
# ===========================================================================


class TestTransitReversalDetected:
    """A user-reversed cover must not be mistaken for a step-motor pause."""

    @pytest.mark.asyncio
    async def test_no_progress_reversal_clears_wait_for_target(self) -> None:
        """Velux/KLF-200 signature: opening(0) → closed(0), target 100.

        The cover publishes no intermediate positions. ACP commands open
        (target 100), the cover acks "opening" at position 0, the user presses
        the physical close button mid-transit, and 45 s later the cover
        reports "closed" still at position 0. Zero progress toward the target
        was ever observed — this is not a motor-pulse pause, and the event
        must fall through so the manual-override detector runs on it.
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        # Production always stamps position_at_send on dispatch (see
        # TestTransitReversalDetectedWithDispatchOrigin below); the cover
        # acked at 0 and never moved, so that is the realistic dispatch
        # origin here too — the equal-distance arm of the shared predicate
        # needs it to tell this reversal apart from a same-position pause.
        coord._cmd_svc.state(entity_id).position_at_send = 0
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "opening→closed with zero progress toward target must clear "
            "wait_for_target so manual-override detection can run"
        )

    @pytest.mark.asyncio
    async def test_no_progress_reversal_does_not_restart_grace(self) -> None:
        """The reversal event must not restart the command grace period.

        A restarted grace period swallows the follow-up state report of the
        same reversal (observed live: "open" and "closed" arriving 4 ms apart,
        the second rejected because the first restarted grace).
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        # See test_no_progress_reversal_clears_wait_for_target above: the
        # realistic dispatch origin for this signature is 0.
        coord._cmd_svc.state(entity_id).position_at_send = 0
        _call(coord)
        assert not coord._grace_mgr.is_in_command_grace_period(
            entity_id
        ), "a no-progress reversal must not restart the grace period"
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_moved_away_reversal_clears_wait_for_target(self) -> None:
        """opening(7) → closed(0), target 100: moved away from target.

        The cover had opened slightly (7 %) before the user reversed it back
        to fully closed. Distance to target grew (93 → 100) — unambiguously
        not ACP's own transit.
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=7,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert (
            coord._cmd_svc.is_waiting_for_target(entity_id) is False
        ), "opening→closed moving away from target must clear wait_for_target"

    @pytest.mark.asyncio
    async def test_closing_command_reversed_to_open_clears_wait_for_target(
        self,
    ) -> None:
        """Mirror case: ACP commands close (target 0), user reverses to open."""
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            new_state_str="open",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "closing"
        # Realistic dispatch origin for this mirror signature: acked at 100,
        # never moved (see test_no_progress_reversal_clears_wait_for_target).
        coord._cmd_svc.state(entity_id).position_at_send = 100
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "closing→open with zero progress toward target must clear "
            "wait_for_target"
        )


# ===========================================================================
# Issue #1306: the realistic dimension @litithio's arm-1-only fix misses —
# ``position_at_send`` is stamped on EVERY production dispatch
# (``_prepare_service_call``, ``send_my_position``), but none of the tests
# above ever populate it. With it populated, the reversal event also matches
# the #1139 unreacted-command arm (``old_position == position ==
# position_at_send``), which keeps ``wait_for_target`` even once arm 1 is
# defused. These tests fail on a tree carrying only the #186 progress term.
# ===========================================================================


def _make_coordinator_with_dispatch_origin(
    entity_id: str,
    target_position: int,
    current_position: int,
    old_position: int,
    *,
    position_at_send: int | None,
    old_state_str: str,
    new_state_str: str,
    sent_seconds_ago: float,
    transit_timeout_seconds: int,
):
    """Extend ``_make_coordinator`` with the dispatch-origin stamp it omits.

    ``_make_coordinator`` (imported from ``test_issue_285_...``) leaves
    ``PerEntityState.position_at_send`` at its default of ``None`` — which is
    exactly why PR #1239's tests are green while the #1139 arm still swallows
    the reversal in production, where every dispatch stamps it.
    """
    coord = _make_coordinator(
        entity_id,
        target_position=target_position,
        current_position=current_position,
        old_position=old_position,
        new_state_str=new_state_str,
        sent_seconds_ago=sent_seconds_ago,
        transit_timeout_seconds=transit_timeout_seconds,
    )
    coord.state_change_data.old_state.state = old_state_str
    coord._cmd_svc.state(entity_id).position_at_send = position_at_send
    return coord


class TestTransitReversalDetectedWithDispatchOrigin:
    """The reversal must clear wait_for_target even when position_at_send is set."""

    @pytest.mark.asyncio
    async def test_no_progress_reversal_clears_wait_for_target_with_dispatch_origin(
        self,
    ) -> None:
        """Velux/KLF-200 signature, with position_at_send stamped as production does.

        target 100, opening(0) → closed(0), position_at_send=0 — the #1139
        unreacted-command arm's exact match condition
        (``old_position == position == position_at_send``). The arm-1-only
        fix does not touch this arm, so it keeps wait_for_target alive.
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator_with_dispatch_origin(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            position_at_send=0,
            old_state_str="opening",
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "opening→closed with zero progress and a matching dispatch "
            "origin must still clear wait_for_target so manual-override "
            "detection can run"
        )

    @pytest.mark.asyncio
    async def test_no_progress_reversal_does_not_restart_grace_with_dispatch_origin(
        self,
    ) -> None:
        """The reversal event must not restart the command grace period either."""
        entity_id = "cover.roof_window"
        coord = _make_coordinator_with_dispatch_origin(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            position_at_send=0,
            old_state_str="opening",
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        _call(coord)
        assert not coord._grace_mgr.is_in_command_grace_period(
            entity_id
        ), "a no-progress reversal must not restart the grace period"
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_closing_command_reversed_to_open_clears_wait_for_target_with_dispatch_origin(
        self,
    ) -> None:
        """Mirror case: ACP commands close (target 0), user reverses to open."""
        entity_id = "cover.roof_window"
        coord = _make_coordinator_with_dispatch_origin(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            old_state_str="closing",
            new_state_str="open",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "closing→open with zero progress and a matching dispatch origin "
            "must still clear wait_for_target"
        )


# ===========================================================================
# Issue #1306: the override must actually ENGAGE end to end.
#
# Every reversal assertion above (and every one PR #1239 shipped) stops at
# ``wait_for_target is False``. The user's actual report is "the binary
# sensor stayed off" — clearing wait_for_target is necessary but not
# sufficient if a downstream gate still rejects the same event. This drives
# the full classify -> handle_state_change sequence (the same two calls
# coordinator.py's ``process_entity_state_change`` and
# ``async_handle_cover_state_change`` make on a drained event, per
# coordinator.py:1225-1231 and :3866) and asserts the manager actually marks
# the cover manual, mirroring
# ``test_issue_1139_unreacted_command.py::TestEndToEndNoFalseOverride``.
# ===========================================================================


class TestEndToEndOverrideEngages:
    """The reversal must actually engage manual override, not just clear the gate."""

    def test_reversal_engages_manual_override_end_to_end(self) -> None:
        from custom_components.adaptive_cover_pro.cover_types import get_policy
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        entity_id = "cover.roof_window"
        buf = EventBuffer(maxlen=50)
        coord = _make_coordinator_with_dispatch_origin(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            position_at_send=0,
            old_state_str="opening",
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )

        # Step 1: classify the state change (StateClassifier), exactly as
        # coordinator.process_entity_state_change() does on the incoming event.
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "wait_for_target must be clear before the override detector runs "
            "on this event"
        )

        # Step 2: the coordinator would now call manager.handle_state_change
        # on the same drained event (coordinator.py:3866), with
        # wait_for_target re-read at drain time.
        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 2},
            logger=MagicMock(),
            event_buffer=buf,
        )
        manager.add_covers([entity_id])

        manager.handle_state_change(
            coord.state_change_data,
            our_state=100,
            policy=get_policy("cover_blind"),
            allow_reset=False,
            is_waiting=coord._cmd_svc.is_waiting_for_target,
            manual_threshold=3,
        )

        assert manager.is_cover_manual(entity_id) is True, (
            "a user reversal that clears wait_for_target must engage manual "
            "override — this is the user-visible symptom in issue #1306: "
            "the binary_sensor stayed off"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "manual_override_set" in events, f"Got: {events}"
        assert (
            "manual_override_rejected_wait_for_target" not in events
        ), f"the wait_for_target gate must not reject this event. Got: {events}"
        coord._grace_mgr.cancel_all()


# ===========================================================================
# Regression guards — the #186 pause guard must keep protecting real pauses
# ===========================================================================


class TestStepMotorPauseStillProtected:
    """Canonical #186 scenarios (pause WITH progress) must keep restarting grace."""

    @pytest.mark.asyncio
    async def test_pause_with_progress_still_restarts_grace(self) -> None:
        """opening(46) → open(51), target 100: genuine motor-pulse pause."""
        entity_id = "cover.step_motor_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=46,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_pause_with_unknown_old_position_still_restarts_grace(self) -> None:
        """Old position unreadable → cannot judge direction → keep today's behaviour."""
        entity_id = "cover.step_motor_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=None,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_equal_distance_pause_away_from_dispatch_origin_restarts_grace(
        self,
    ) -> None:
        """opening(45) → open(45), target 100, position_at_send=20.

        The cover has moved from its dispatch origin (20) to 45 and then
        paused. Because a cover that only publishes a position when it
        changes reports the same number on both the last transit report and
        the settle, old_distance == new_distance here (55 == 55) — the
        reversal predicate's equal-distance case. But the cover has
        demonstrably moved since dispatch (45 != 20): this is a genuine
        #186 step-motor pause, not the zero-movement reversal the equality
        case exists to catch, and arm 1 must fire and restart grace.

        An unconditional ``>=`` treats this identically to a true reversal
        and swallows the pause guard — the MUST-FIX regression this test
        locks.
        """
        entity_id = "cover.step_motor_shade"
        coord = _make_coordinator_with_dispatch_origin(
            entity_id,
            target_position=100,
            current_position=45,
            old_position=45,
            position_at_send=20,
            old_state_str="opening",
            new_state_str="open",
            sent_seconds_ago=10.0,
            transit_timeout_seconds=150,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True, (
            "an equal-distance pause that has moved away from its dispatch "
            "origin is a genuine step-motor pause, not a reversal — "
            "wait_for_target must stay True"
        )
        assert coord._grace_mgr.is_in_command_grace_period(
            entity_id
        ), "arm 1 must restart the command grace period for this pause"
        coord._grace_mgr.cancel_all()
