"""Tests for issue #271: slow covers trigger false manual override at sunset.

Root cause: the transit-timeout backstop in process_entity_state_change() compared
elapsed time against TRANSIT_TIMEOUT_SECONDS (45s) from the command send time (_sent_at).
A slow cover that takes >45s clears wait_for_target while still physically in transit.
The next intermediate position report then flows through manual-override detection,
exceeds manual_threshold, and is misidentified as a user move.

Fix:
1. Direction-aware backstop — the timeout resets whenever the cover makes forward
   progress toward target (new_distance < old_distance). Stalled / no-report covers
   still get cleared after TRANSIT_TIMEOUT_SECONDS with no progress; slow-but-moving
   covers get an extended window proportional to when they last moved.
2. User-configurable transit timeout (CONF_TRANSIT_TIMEOUT) so users with large /
   slow shades can set a longer window rather than relying on the 45s default.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors test_issue_172_false_manual_override helpers, extended)
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
    """Build a minimal coordinator mock for process_entity_state_change tests.

    Args:
        entity_id: The cover entity being tracked.
        target_position: The position the integration commanded.
        current_position: The position the cover is now reporting.
        old_position: The position the cover had before this event.
        grace_expired: Whether the command grace period has expired.
        ignore_intermediate: Whether to ignore opening/closing states.
        new_state_str: The HA state string for the cover.
        sent_seconds_ago: How many seconds ago the command was sent.
        last_progress_seconds_ago: If set, pre-populate _last_progress_at as if
            forward progress was recorded this many seconds ago. None = no progress
            recorded yet (elapsed is measured from sent_at).
        transit_timeout_seconds: The configured transit timeout to apply.

    """
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
    cmd_svc._sent_at = {
        entity_id: now - dt.timedelta(seconds=sent_seconds_ago)
    }
    cmd_svc._wait_for_target_timeout_seconds = transit_timeout_seconds

    # Track last progress per entity so record_progress / _transit_elapsed work together
    last_progress_at: dict[str, dt.datetime] = {}
    if last_progress_seconds_ago is not None:
        last_progress_at[entity_id] = now - dt.timedelta(seconds=last_progress_seconds_ago)

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
# Progress-aware backstop
# ===========================================================================


class TestProgressAwareBackstop:
    """The transit backstop must use time-since-last-progress, not time-since-sent."""

    def test_backstop_does_not_fire_while_cover_is_still_making_progress(self) -> None:
        """Slow cover with progress 20s ago must not be cleared at 50s from sent_at.

        Real scenario: shades take 90s to close.  By t=50s (>45s from command)
        the cover has been reporting progress every ~10s.  The last progress was
        20s ago.  elapsed_since_progress = 20s < 45s — backstop must NOT fire.
        """
        entity_id = "cover.slow_shade"
        # Command sent 50s ago, but cover made progress only 20s ago
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,  # moving toward 0 — new_dist=50 < old_dist=60
            new_state_str="closing",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=20.0,
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True, (
            "wait_for_target must stay True when cover made progress 20s ago "
            "(even though 50s have elapsed since the command was sent)"
        )

    def test_backstop_fires_when_no_progress_for_configured_timeout(self) -> None:
        """Cover with no progress recorded for > timeout seconds must be cleared.

        If a cover never reports intermediate positions, the reference falls
        back to sent_at, preserving the original hard-timeout behavior.
        """
        entity_id = "cover.stalled_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=80,
            old_position=80,  # same position — no progress
            new_state_str="closing",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=None,  # no progress recorded
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False, (
            "wait_for_target must be cleared when no progress for > 45s"
        )

    def test_slow_cover_still_moving_at_50s_does_not_trigger_false_override(
        self,
    ) -> None:
        """The canonical issue #271 scenario.

        Command sent 50s ago (>45s timeout).  The cover has been making
        progress — last progress recorded 20s ago.  Current event shows
        the cover still moving toward target.  wait_for_target must stay
        True, preventing the intermediate position from being misidentified
        as a user-initiated manual override.
        """
        entity_id = "cover.slow_sunset_shade"
        # Target=0 (sunset close), cover was at 60%, now at 50% — still closing
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,
            new_state_str="closing",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=20.0,
        )
        _call(coord)
        # wait_for_target must stay True — no manual override check reaches handle_state_change
        assert coord._cmd_svc.wait_for_target[entity_id] is True, (
            "wait_for_target must stay True for a slow cover that is still "
            "making progress — this is the false-override scenario from issue #271"
        )
        # record_progress must have been called to extend the window further
        coord._cmd_svc.record_progress.assert_called_once()

    def test_cover_never_reports_intermediate_positions_still_clears_after_timeout(
        self,
    ) -> None:
        """Cover that never reports mid-transit position still gets cleared after timeout.

        Some covers jump directly from starting position to final position without
        intermediate reports.  No progress_at is ever recorded, so
        _transit_elapsed_without_progress falls back to sent_at.  After timeout
        seconds, the backstop fires as before — the safety net is preserved.
        """
        entity_id = "cover.no_report_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=80,
            old_position=80,  # position unchanged — no intermediate reports
            new_state_str="closing",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=None,  # never reported progress
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False, (
            "Hard timeout safety net must still work for covers without intermediate reports"
        )

    def test_configurable_timeout_extends_window(self) -> None:
        """When transit_timeout is set to 120s, a 50s old command is not timed out."""
        entity_id = "cover.slow_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=50,
            old_position=60,
            new_state_str="closing",
            sent_seconds_ago=50.0,
            last_progress_seconds_ago=None,  # no progress, falls back to sent_at
            transit_timeout_seconds=120,
        )
        _call(coord)
        # 50s elapsed < 120s timeout → must NOT fire
        assert coord._cmd_svc.wait_for_target[entity_id] is True, (
            "With transit_timeout=120s, a 50s old command must not trigger the backstop"
        )


# ===========================================================================
# Configurable timeout constant and config
# ===========================================================================


class TestConfigurableTransitTimeout:
    """CONF_TRANSIT_TIMEOUT must exist and be read by the coordinator."""

    def test_conf_transit_timeout_constant_exists(self) -> None:
        from custom_components.adaptive_cover_pro.const import CONF_TRANSIT_TIMEOUT

        assert CONF_TRANSIT_TIMEOUT == "transit_timeout"

    def test_default_transit_timeout_constant_is_45_seconds(self) -> None:
        from custom_components.adaptive_cover_pro.const import (
            DEFAULT_TRANSIT_TIMEOUT_SECONDS,
        )

        assert DEFAULT_TRANSIT_TIMEOUT_SECONDS == 45

    def test_legacy_transit_timeout_alias_still_works(self) -> None:
        """TRANSIT_TIMEOUT_SECONDS alias must remain for backward compatibility."""
        from custom_components.adaptive_cover_pro.const import TRANSIT_TIMEOUT_SECONDS

        assert TRANSIT_TIMEOUT_SECONDS == 45

    def test_cover_command_service_accepts_transit_timeout_param(self) -> None:
        """CoverCommandService must accept and store transit_timeout_seconds."""
        from unittest.mock import MagicMock

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
            transit_timeout_seconds=120,
        )
        assert svc._wait_for_target_timeout_seconds == 120

    def test_cover_command_service_defaults_to_45_seconds(self) -> None:
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        assert svc._wait_for_target_timeout_seconds == 45

    def test_transit_elapsed_without_progress_returns_elapsed_from_sent_at(
        self,
    ) -> None:
        """When no progress recorded, elapsed is measured from sent_at."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        sent_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=30)
        svc._sent_at[entity_id] = sent_at

        now = dt.datetime.now(dt.UTC)
        elapsed = svc._transit_elapsed_without_progress(entity_id, now)
        assert elapsed is not None
        assert 29 < elapsed < 32  # ~30 seconds

    def test_transit_elapsed_without_progress_uses_last_progress_when_set(
        self,
    ) -> None:
        """When progress was recorded recently, elapsed is measured from last_progress_at."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        # Command sent 50s ago
        svc._sent_at[entity_id] = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=50)
        # But progress recorded only 10s ago
        now = dt.datetime.now(dt.UTC)
        svc.record_progress(entity_id, now - dt.timedelta(seconds=10))

        elapsed = svc._transit_elapsed_without_progress(entity_id, now)
        assert elapsed is not None
        assert 9 < elapsed < 12  # ~10 seconds (not 50)

    def test_record_progress_updates_last_progress_at(self) -> None:
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        now = dt.datetime.now(dt.UTC)
        svc.record_progress(entity_id, now)
        assert svc._last_progress_at[entity_id] == now

    def test_apply_position_resets_last_progress_at(self) -> None:
        """Sending a new command must clear _last_progress_at so the new transit starts fresh."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        hass.services.call = MagicMock(return_value=None)
        hass.states.get = MagicMock(return_value=MagicMock(state="closed"))
        grace_mgr = MagicMock()
        grace_mgr.is_in_command_grace_period = MagicMock(return_value=False)
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        now = dt.datetime.now(dt.UTC)
        svc.record_progress(entity_id, now - dt.timedelta(seconds=30))
        assert entity_id in svc._last_progress_at

        # Directly reset as apply_position would
        svc._last_progress_at.pop(entity_id, None)
        assert entity_id not in svc._last_progress_at


# ===========================================================================
# Reconciliation backstop in CoverCommandService
# ===========================================================================


class TestReconciliationBackstop:
    """The 30s reconciliation backstop must also use progress-aware timeout."""

    @pytest.mark.asyncio
    async def test_reconcile_backstop_uses_configurable_timeout(self) -> None:
        """Reconciliation must use _wait_for_target_timeout_seconds (not hard-coded 30s)."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        hass.async_create_task = MagicMock()
        hass.loop = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
            transit_timeout_seconds=120,
        )
        now = dt.datetime.now(dt.UTC)
        svc.wait_for_target[entity_id] = True
        svc.target_call[entity_id] = 0
        svc._sent_at[entity_id] = now - dt.timedelta(seconds=50)

        # 50s elapsed < 120s configured timeout → reconcile must NOT clear wait_for_target
        await svc._reconcile(now)
        assert svc.wait_for_target.get(entity_id) is True, (
            "Reconcile backstop must respect the configured transit timeout of 120s "
            "— 50s elapsed should not clear wait_for_target"
        )

    @pytest.mark.asyncio
    async def test_reconcile_backstop_fires_after_configured_timeout(self) -> None:
        """Reconciliation must clear wait_for_target after configured timeout elapses."""
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        entity_id = "cover.test"
        hass = MagicMock()
        # Return None from states.get so _get_current_position returns None and
        # reconcile skips the position-match step (we only care the backstop fires)
        hass.states.get = MagicMock(return_value=None)
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
            transit_timeout_seconds=45,
        )
        now = dt.datetime.now(dt.UTC)
        svc.wait_for_target[entity_id] = True
        svc.target_call[entity_id] = 0
        svc._sent_at[entity_id] = now - dt.timedelta(seconds=50)
        svc._enabled = True

        await svc._reconcile(now)
        assert svc.wait_for_target.get(entity_id) is False, (
            "Reconcile backstop must clear wait_for_target after 50s > 45s configured timeout"
        )
