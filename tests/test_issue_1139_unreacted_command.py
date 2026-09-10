"""Tests for issue #1139: false manual override when the cover hasn't reacted.

Root cause: in ``StateClassifier.classify``, the ``new_distance == old_distance``
arm only keeps ``wait_for_target`` when the HA state STRING changed (the #172
startup-delay guard). When a cover republishes its *pre-command* position with
an UNCHANGED state string — it has not reacted to the dispatched command at
all — that guard is False and control falls through to the unconditional
``cmd_svc.set_waiting(entity_id, False)``, emitting ``transit_cleared`` while
the cover never moved. The very next event then computes
``|target - reported|`` — the entire commanded travel — as a manual override.

Two field reproductions land on this exact branch, both inside the transit
timeout window (so the #271 backstop never engages):

- ESPSomfy RTS serialized queue: target 70, cover still at 100/"open", flagged
  20.9s after send.
- Single cover, motor actuation lag: target 0, cover still at 100/"open",
  ``transit_cleared`` at +5.96s then ``manual_override_set`` 3ms later.

Fix: stamp ``PerEntityState.position_at_send`` from the ``prior_position``
value already computed at both dispatch chokepoints
(``_prepare_service_call``, ``send_my_position``). A new arm in the
equal-distance block suppresses the clear ONLY when the cover is still
resting at the exact position it occupied at dispatch — discriminated from a
genuine mid-travel stall (e.g. #172's stalled-at-51 fixture), which must
still clear immediately.

Harness cloned from ``tests/test_issue_518_optimistic_target_replay.py``.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.adaptive_cover_pro.managers.manual_override import (
    StateChangeInputs,
)

# ---------------------------------------------------------------------------
# Helpers — cloned from test_issue_518_optimistic_target_replay.py, extended
# with a ``position_at_send`` kwarg.
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
    event.old_state.state = (
        old_state_str if old_state_str is not None else new_state_str
    )
    event.old_state.attributes = {"current_position": old_position}
    return event


def _make_coordinator(
    entity_id: str,
    target_position: int,
    current_position: int,
    old_position: int = 0,
    *,
    position_at_send: int | None = None,
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
    cmd_svc.state(entity_id).position_at_send = position_at_send

    if last_progress_seconds_ago is not None:
        cmd_svc.state(entity_id).last_progress_at = now - dt.timedelta(
            seconds=last_progress_seconds_ago
        )

    # Wrap record_progress so tests can assert it was (not) called.
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
# RED (1)-(2): both field reproductions must keep wait_for_target True.
# ===========================================================================


class TestUnreactedCommandKeepsWaiting:
    """A cover that never reacted to a dispatched command must stay `waiting`."""

    def test_unreacted_command_keeps_wait_for_target(self) -> None:
        """Repro 2 (Discussion #1244): motor actuation lag on a single cover.

        target=0, cover still at 100/"open" (its pre-command position), grace
        expired 6.0s ago, well inside the 20s transit timeout. Must NOT clear.
        """
        entity_id = "cover.single_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True, (
            "Cover that has not reacted to the dispatched command at all "
            "(still resting at its pre-command position) must keep "
            "wait_for_target — this is not a manual override (issue #1139)"
        )
        coord._grace_mgr.cancel_all()

    def test_serialized_backend_queue_wait_keeps_wait_for_target(self) -> None:
        """Repro 1: ESPSomfy RTS serialized command queue.

        target=70, cover still at 100/"open", flagged 20.9s after send, well
        inside the 45s transit timeout. Must NOT clear.
        """
        entity_id = "cover.office_roller_shutter"
        coord = _make_coordinator(
            entity_id,
            target_position=70,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=20.9,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True, (
            "Serialized-backend queue delay must not defeat the unreacted-"
            "command guard while inside the transit timeout (issue #1139)"
        )
        coord._grace_mgr.cancel_all()


# ===========================================================================
# RED (3): the new arm must record a `transit_awaiting_reaction` event, not
# `transit_cleared`.
# ===========================================================================


class TestEventBufferRecording:
    """The new arm must record `transit_awaiting_reaction`, not `transit_cleared`."""

    def test_unreacted_command_records_awaiting_reaction_event(self) -> None:
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )

        entity_id = "cover.single_shade"
        buf = EventBuffer(maxlen=50)
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
            event_buffer=buf,
        )
        _call(coord)

        events = [e["event"] for e in buf.snapshot()]
        assert (
            "transit_awaiting_reaction" in events
        ), f"Expected transit_awaiting_reaction event in buffer. Got: {events}"
        assert (
            "transit_cleared" not in events
        ), f"transit_cleared must NOT fire for an unreacted command. Got: {events}"
        coord._grace_mgr.cancel_all()


# ===========================================================================
# RED (4)-(5): position_at_send must be stamped at the dispatch chokepoint,
# on both the plain set_cover_position route and the endpoint open/close
# routing repro 2 actually used.
# ===========================================================================


class TestPositionAtSendStamping:
    """position_at_send must be stamped at the single _prepare_service_call chokepoint."""

    @pytest.mark.asyncio
    async def test_position_at_send_stamped_on_dispatch(self) -> None:
        """A real set_cover_position dispatch stamps position_at_send."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
            PositionContext,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.states.get = MagicMock(
            return_value=MagicMock(state="open", attributes={"current_position": 100})
        )
        grace_mgr = MagicMock()
        grace_mgr.is_in_command_grace_period = MagicMock(return_value=False)
        # endpoint_use_open_close=False forces the plain set_cover_position
        # route even though target=0 is a mechanical endpoint (issue #697
        # would otherwise route this to close_cover — see the sibling test
        # below for that routing specifically).
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
            endpoint_use_open_close=False,
        )

        with (
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
                return_value={
                    "has_set_position": True,
                    "has_set_tilt_position": False,
                    "has_open": True,
                    "has_close": True,
                },
            ),
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
                return_value=None,
            ),
        ):
            outcome, detail = await svc.apply_position(
                entity_id,
                0,
                "solar",
                context=PositionContext(
                    auto_control=True,
                    manual_override=False,
                    sun_just_appeared=False,
                    min_change=2,
                    time_threshold=0,
                    special_positions=[0, 100],
                ),
            )

        assert outcome == "sent"
        assert detail == "set_cover_position"
        assert svc.state(entity_id).position_at_send == 100, (
            "position_at_send must be stamped with the cover's raw position "
            "at the moment of dispatch (issue #1139)"
        )

    @pytest.mark.asyncio
    async def test_position_at_send_stamped_on_endpoint_open_close_routing(
        self,
    ) -> None:
        """Repro 2's routing: has_set_position=False, close_cover, still stamps."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
            PositionContext,
        )

        entity_id = "cover.somfy_rts"
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.states.get = MagicMock(return_value=MagicMock(state="open", attributes={}))
        grace_mgr = MagicMock()
        grace_mgr.is_in_command_grace_period = MagicMock(return_value=False)
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        caps = {
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        }

        with (
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
                return_value=caps,
            ),
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
                return_value=None,
            ),
        ):
            outcome, detail = await svc.apply_position(
                entity_id,
                0,
                "solar",
                context=PositionContext(
                    auto_control=True,
                    manual_override=False,
                    sun_just_appeared=False,
                    min_change=2,
                    time_threshold=0,
                    special_positions=[0, 100],
                ),
            )

        assert outcome == "sent"
        assert detail == "close_cover"
        assert svc.state(entity_id).position_at_send == 100, (
            "position_at_send must be stamped on the endpoint open/close "
            "routing path too — the single _prepare_service_call chokepoint "
            "covers set_position, open/close, and stop_cover (issue #1139)"
        )


# ===========================================================================
# RED (6): full classify -> handle_state_change sequence must not engage a
# manual override for an unreacted command.
# ===========================================================================


class TestEndToEndNoFalseOverride:
    """Full classify -> handle_state_change sequence must never engage an override."""

    def test_end_to_end_no_manual_override_on_unreacted_command(self) -> None:
        from custom_components.adaptive_cover_pro.cover_types import get_policy
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        entity_id = "cover.single_shade"
        buf = EventBuffer(maxlen=50)
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
        )

        # Step 1: classify the state change (StateClassifier).
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True

        # Step 2: the coordinator would now call manager.handle_state_change on
        # the same drained event, with wait_for_target still gating detection.
        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 2},
            logger=MagicMock(),
            event_buffer=buf,
        )
        manager.add_covers([entity_id])

        manager.handle_state_change(
            coord.state_change_data,
            StateChangeInputs(
                our_state=0,
                policy=get_policy("cover_blind"),
                allow_reset=False,
                is_waiting=coord._cmd_svc.is_waiting_for_target,
                manual_threshold=3,
            ),
        )

        assert manager.is_cover_manual(entity_id) is False, (
            "An unreacted command must never engage manual override end to end "
            "(issue #1139)"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert (
            "manual_override_set" not in events
        ), f"manual_override_set must not fire. Got: {events}"
        coord._grace_mgr.cancel_all()


# ===========================================================================
# NEW BOUND ASSERTIONS (15a)-(15e): guards for the fix itself — the
# suppression must remain bounded by transit_timeout and must not reset
# either of the two clocks that bound it (record_progress / command grace).
# ===========================================================================


class TestSuppressionIsBounded:
    """The guard's suppression must stay bounded by transit_timeout, not become permanent."""

    def test_awaiting_reaction_clears_after_transit_timeout(self) -> None:
        """Same unreacted-command shape, but past the transit timeout: must clear."""
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )

        entity_id = "cover.single_shade"
        buf = EventBuffer(maxlen=50)
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=25.0,
            transit_timeout_seconds=20,
            event_buffer=buf,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "The unreacted-command guard must remain bounded by transit_timeout "
            "— it must not suppress the clear forever"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "transit_timeout_cleared" in events
        coord._grace_mgr.cancel_all()

    def test_awaiting_reaction_does_not_reset_the_progress_clock(self) -> None:
        """The arm must not call record_progress — that would push the bound out."""
        entity_id = "cover.single_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._cmd_svc.record_progress.called is False, (
            "record_progress must NOT be called by the unreacted-command guard "
            "— doing so would reset the transit-timeout backstop clock and make "
            "the suppression unbounded (issue #1139)"
        )
        assert coord._cmd_svc.state(entity_id).last_progress_at is None
        coord._grace_mgr.cancel_all()

    def test_awaiting_reaction_does_not_restart_command_grace(self) -> None:
        """The arm must not restart the command grace period either."""
        entity_id = "cover.single_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id) is False, (
            "The unreacted-command guard must not restart the command grace "
            "period — the transit-timeout backstop is the only clock that may "
            "bound this suppression"
        )
        coord._grace_mgr.cancel_all()

    def test_symmetric_straddle_still_clears(self) -> None:
        """Equal distances from a genuine move (not a dispatch-origin stall) must clear.

        target=50, old=40, current=60: |40-50| == |60-50| == 10, but the cover
        moved from 40 to 60 — a real move, not "never reacted". position_at_send
        (40) matches old_position but NOT the current position, so the new arm
        must not fire.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=50,
            current_position=60,
            old_position=40,
            position_at_send=40,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=10.0,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "A symmetric straddle around the target from a genuine move must "
            "still clear wait_for_target — equal distances alone must not "
            "suppress the clear"
        )
        coord._grace_mgr.cancel_all()

    def test_return_to_origin_after_real_movement_still_clears(self) -> None:
        """Returning to the dispatch-origin position after a real move must still clear.

        target=50, position_at_send=40 (dispatched from 40 toward 50); the
        cover moved to 60 and now reports 40 again: |60-50| == |40-50| == 10,
        an equal-distance straddle, and position (40) happens to equal
        position_at_send (40) too. But old_position (60) does NOT equal
        position (40) — the cover demonstrably moved since dispatch, it did
        not sit resting at its origin. The guard must require old_position ==
        position == position_at_send, not just position == position_at_send,
        or it wrongly suppresses the clear for a cover that already reacted.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=50,
            current_position=40,
            old_position=60,
            position_at_send=40,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=10.0,
            transit_timeout_seconds=45,
        )
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "A cover that moved away from its dispatch origin and back must "
            "still clear wait_for_target — position == position_at_send alone "
            "must not suppress the clear when old_position != position"
        )
        coord._grace_mgr.cancel_all()

    def test_arm_does_not_engage_without_a_live_clock(self) -> None:
        """The arm must not suppress when there is no clock left to bound it.

        Both bounds that make this suppression safe — the in-classifier
        backstop just above and reconciliation step 1 — are gated on
        ``elapsed is not None``. If ``sent_at`` were ever None while
        ``waiting`` and ``position_at_send`` were set, ``elapsed`` would be
        None too (``transit_elapsed_without_progress`` falls back to
        ``last_progress_at or sent_at``), and an ungated arm would suppress
        the clear with no clock left to time it out — unbounded. No live
        path is known to construct this state, but the arm's own
        correctness argument is boundedness, so it must require a live
        clock before it can engage at all.
        """
        entity_id = "cover.single_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            position_at_send=100,
            new_state_str="open",
            old_state_str="open",
            sent_seconds_ago=6.0,
            transit_timeout_seconds=20,
        )
        # Force the shape the audit could not reach through a live path:
        # waiting + position_at_send set, but sent_at (and last_progress_at)
        # cleared, so transit_elapsed_without_progress has no reference and
        # returns None.
        coord._cmd_svc.state(entity_id).sent_at = None
        assert coord._cmd_svc.state(entity_id).last_progress_at is None

        _call(coord)

        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "With no live clock (sent_at and last_progress_at both None), "
            "elapsed is None and the suppression would be unbounded — the "
            "arm must fall through to clear rather than keep waiting "
            "(issue #1139 finding 5)"
        )
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_reconciliation_clears_a_never_reported_unreacted_command(
        self,
    ) -> None:
        """The independent reconciliation-tick bound must still fire with no event.

        Proves the second, event-independent bound (`run_reconciliation_pass`
        step 1) still clears an unreacted command that never generates a
        single state-changed event at all.
        """
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        hass.states.get = MagicMock(return_value=None)
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
            transit_timeout_seconds=20,
        )
        now = dt.datetime.now(dt.UTC)
        svc.set_waiting(entity_id, True)
        svc.set_target(entity_id, 0)
        svc.state(entity_id).sent_at = now - dt.timedelta(seconds=25)
        svc.state(entity_id).position_at_send = 100
        svc._enabled = True

        await svc.run_reconciliation_pass(now)

        assert svc.is_waiting_for_target(entity_id) is False, (
            "Reconciliation's own 60s-tick backstop must clear wait_for_target "
            "for an unreacted command even with no cover event ever firing"
        )
