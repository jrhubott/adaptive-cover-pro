"""Tests for issue #285: false manual override for covers that stay "open" mid-transit.

Root cause: process_entity_state_change() gates the entire progress-aware transit
backstop on cover_is_transitioning (HA state in "opening"/"closing"). Covers that
stay "open" while current_position updates skip the direction check entirely, causing
wait_for_target to be cleared prematurely. The next position update then triggers a
false manual override.

Fix: lift the direction/progress check outside the cover_is_transitioning gate so it
applies whenever old_position, position, and target are all known.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers (mirrors test_issue_271 helpers)
# ---------------------------------------------------------------------------


def _make_state_change_data(
    entity_id: str,
    new_position: int,
    old_position: int = 0,
    new_state_str: str = "opening",
):
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = new_state_str
    event.new_state.attributes = {"current_position": new_position}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.old_state = MagicMock()
    event.old_state.state = new_state_str
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
    new_state_str: str = "opening",
    sent_seconds_ago: float = 10.0,
    last_progress_seconds_ago: float | None = None,
    transit_timeout_seconds: int = 45,
):
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    coordinator = MagicMock()
    coordinator.state_change_data = _make_state_change_data(
        entity_id, current_position, old_position, new_state_str
    )
    coordinator.ignore_intermediate_states = ignore_intermediate
    coordinator._target_just_reached = set()

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    if not grace_expired:
        grace_mgr._command_timestamps[entity_id] = dt.datetime.now().timestamp()
    coordinator._grace_mgr = grace_mgr

    cmd_svc = MagicMock(spec=CoverCommandService)
    cmd_svc.wait_for_target = {entity_id: True}
    cmd_svc.target_call = {entity_id: target_position}
    cmd_svc._position_tolerance = 5

    now = dt.datetime.now(dt.UTC)
    cmd_svc._sent_at = {entity_id: now - dt.timedelta(seconds=sent_seconds_ago)}
    cmd_svc._wait_for_target_timeout_seconds = transit_timeout_seconds

    last_progress_at: dict[str, dt.datetime] = {}
    if last_progress_seconds_ago is not None:
        last_progress_at[entity_id] = now - dt.timedelta(
            seconds=last_progress_seconds_ago
        )

    def _transit_elapsed(eid: str, now_arg: dt.datetime) -> float | None:
        reference = last_progress_at.get(eid) or cmd_svc._sent_at.get(eid)
        return (now_arg - reference).total_seconds() if reference else None

    def _record_progress(eid: str, now_arg: dt.datetime) -> None:
        last_progress_at[eid] = now_arg

    cmd_svc._transit_elapsed_without_progress = MagicMock(side_effect=_transit_elapsed)
    cmd_svc.record_progress = MagicMock(side_effect=_record_progress)

    def _check_target_reached(eid, pos):
        if pos is None:
            return False
        if abs(pos - cmd_svc.target_call.get(eid, -999)) <= cmd_svc._position_tolerance:
            cmd_svc.wait_for_target[eid] = False
            return True
        return False

    cmd_svc.check_target_reached = MagicMock(side_effect=_check_target_reached)
    cmd_svc.get_cover_capabilities = MagicMock(return_value={"has_set_position": True})

    def _read_position(eid, caps, state_obj):
        if state_obj is coordinator.state_change_data.new_state:
            return current_position
        if state_obj is coordinator.state_change_data.old_state:
            return old_position
        return current_position

    cmd_svc.read_position_with_capabilities = MagicMock(side_effect=_read_position)
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
# Issue #285: covers that stay "open" while moving
# ===========================================================================


class TestOpenStateCoverMakingProgress:
    """Covers that never emit 'opening'/'closing' must not trigger false overrides."""

    def test_open_state_cover_making_progress_keeps_wait_for_target(self) -> None:
        """Cover stays 'open' while current_position moves toward target.

        Real scenario (Issue #285): some covers (French volet-roulant, some Zigbee
        rolling shutters) never emit 'opening'/'closing'. They stay 'open' the whole
        time and just update current_position. A position change from 60→50 toward
        target=0 is integration-initiated transit, not a manual override.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,
            new_state_str="open",
            sent_seconds_ago=10.0,
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True, (
            "wait_for_target must stay True: cover is moving toward target "
            "even though HA state stays 'open' throughout transit"
        )

    def test_open_state_cover_making_progress_records_progress(self) -> None:
        """record_progress must be called when an open-state cover moves toward target."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,
            new_state_str="open",
            sent_seconds_ago=10.0,
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert (
            coord._cmd_svc.record_progress.called
        ), "record_progress must be called so the progress backstop window extends"

    def test_open_state_cover_stalled_beyond_timeout_clears_wait_for_target(
        self,
    ) -> None:
        """Open-state cover with no progress for > timeout must still be cleared.

        The hard-timeout safety net must work even when the cover never emits
        'opening'/'closing'. old_position == current_position means no movement.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=80,
            old_position=80,
            new_state_str="open",
            sent_seconds_ago=50.0,
            transit_timeout_seconds=45,
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert (
            coord._cmd_svc.wait_for_target[entity_id] is False
        ), "Hard timeout backstop must still fire for open-state covers with no progress"

    def test_open_state_cover_drifting_away_from_target_clears_wait_for_target(
        self,
    ) -> None:
        """Open-state cover moving away from target must be detected as manual override.

        Position 60→70 while target=0 means the cover moved away — genuine user action.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=70,
            old_position=60,
            new_state_str="open",
            sent_seconds_ago=10.0,
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert (
            coord._cmd_svc.wait_for_target[entity_id] is False
        ), "Cover moving away from target must clear wait_for_target (genuine manual move)"

    def test_open_state_cover_progress_backstop_resets_window(self) -> None:
        """Open-state cover: progress window extends when moving, even past sent_at timeout.

        sent 50s ago (> 45s default), but last forward progress was only 20s ago.
        The cover is still actively closing — must not be cleared.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=40,
            old_position=50,
            new_state_str="open",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=20.0,
            transit_timeout_seconds=45,
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True, (
            "Progress-aware backstop must work for open-state covers: "
            "20s since last progress < 45s timeout, cover still moving forward"
        )


# ===========================================================================
# Regression guards — existing behaviour must not change
# ===========================================================================


class TestRegressionExistingBehaviour:
    """Existing test scenarios must continue to behave identically after the fix."""

    def test_open_from_open_same_position_still_clears_wait_for_target(self) -> None:
        """Cover stuck at same position with open→open must still clear wait_for_target.

        Issue #172 regression guard. old_position == new_position == 51, state stays
        'open'. No state transition and no movement — genuine stop, not startup delay.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=51,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert (
            coord._cmd_svc.wait_for_target[entity_id] is False
        ), "Stalled open→open (no movement) must clear wait_for_target — Issue #172 guard"

    def test_opening_state_cover_forward_progress_still_works(self) -> None:
        """The original 'opening' code path must still keep wait_for_target=True."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=60,
            old_position=40,
            new_state_str="opening",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert (
            coord._cmd_svc.wait_for_target[entity_id] is True
        ), "opening→opening with forward progress must still keep wait_for_target=True"
