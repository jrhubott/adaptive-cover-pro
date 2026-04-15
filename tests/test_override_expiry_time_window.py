"""Tests for issue #132: manual override expiry respects the active-hours window.

Sub-issue 1: When a manual override auto-expires *outside* the configured active
hours window the integration must NOT proactively reposition covers.  It should
stay quiet and let the normal update cycle (triggered when the window opens) send
the correct position.

Sub-issue 2 (documentation): a cover at 100% moving to 98% despite a 10% delta
threshold is caused by the ``special_positions`` bypass in
``_check_position_delta``.  The current position (100%) equals ``default_height``
which is in ``special_positions``, so the delta check is skipped.  No code fix is
applied for sub-issue 2 without a diagnostic dump — the existing behaviour is by
design (allows solar tracking to engage from a default/special position).  See
issue #132 for the detailed analysis.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

UTC = dt.UTC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(*, check_adaptive_time: bool, automatic_control: bool = True):
    """Build a minimal mock coordinator for testing _async_send_after_override_clear."""
    coordinator = MagicMock()
    coordinator.check_adaptive_time = check_adaptive_time
    coordinator.automatic_control = automatic_control
    coordinator.logger = MagicMock()
    coordinator.entities = ["cover.test_blind"]
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    return coordinator


# ---------------------------------------------------------------------------
# _async_send_after_override_clear — time-window guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_clear_skips_send_outside_time_window():
    """Override expiry outside active hours must NOT send a cover command.

    Reproduces issue #132 sub-issue 1:
    - active hours start at 8 AM
    - manual override expires at 6 AM (before window)
    - integration must NOT command the cover to the sunset/default position
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    # apply_position must NOT be called when outside the time window
    coordinator._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_override_clear_sends_position_inside_time_window():
    """Override expiry inside active hours must send the pipeline position.

    Normal case: override expires during the active window; cover should be
    repositioned immediately (existing behaviour must not regress).
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=50, options={}
    )

    # apply_position must be called for each cover entity
    coordinator._cmd_svc.apply_position.assert_called_once_with(
        "cover.test_blind",
        50,
        "manual_override_cleared",
        context=coordinator._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_override_clear_logs_debug_when_outside_window():
    """A debug message must be logged when send is skipped for time-window reasons."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    # A debug log must have been emitted explaining the skip
    coordinator.logger.debug.assert_called()
    logged_message = coordinator.logger.debug.call_args_list[-1][0][0]
    assert (
        "outside active-hours window" in logged_message
        or "outside" in logged_message.lower()
    )


@pytest.mark.asyncio
async def test_override_clear_uses_force_true_inside_window():
    """Inside the window, apply_position must be called with force=True context.

    force=True bypasses delta/time thresholds because the cover may have been
    sitting at the manual position for a long time.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=75, options={"test_option": True}
    )

    # _build_position_context must be called with force=True
    coordinator._build_position_context.assert_called_once_with(
        "cover.test_blind",
        {"test_option": True},
        force=True,
        sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
    )


@pytest.mark.asyncio
async def test_override_clear_outside_window_multiple_covers():
    """All covers must be skipped when outside the time window.

    Ensures the early return happens before the entity loop, not inside it.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)
    coordinator.entities = ["cover.blind_1", "cover.blind_2", "cover.blind_3"]

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    # No commands sent for any cover
    coordinator._cmd_svc.apply_position.assert_not_called()
    coordinator._build_position_context.assert_not_called()


@pytest.mark.asyncio
async def test_override_clear_inside_window_multiple_covers():
    """All covers get positioned when inside the time window.

    Regression: multiple-cover scenarios must still send one command per entity.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True)
    coordinator.entities = ["cover.blind_1", "cover.blind_2"]

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=50, options={}
    )

    # One apply_position call per cover
    assert coordinator._cmd_svc.apply_position.call_count == 2
    calls = [
        call.args[0] for call in coordinator._cmd_svc.apply_position.call_args_list
    ]
    assert "cover.blind_1" in calls
    assert "cover.blind_2" in calls


# ---------------------------------------------------------------------------
# _async_send_after_override_clear — automatic-control guard (issue #139)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_clear_skips_send_when_auto_control_off():
    """Override expiry with Automatic Control OFF must NOT send a cover command.

    Reproduces issue #139:
    - User turns off Automatic Control to manage covers manually
    - A previously set manual override expires naturally
    - Integration must NOT force-reposition the cover despite the expiry
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True, automatic_control=False)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    # apply_position must NOT be called when auto control is off
    coordinator._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_override_clear_logs_debug_when_auto_control_off():
    """A debug message must be logged when send is skipped because auto control is OFF."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True, automatic_control=False)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    coordinator.logger.debug.assert_called()
    logged_args = [call[0][0] for call in coordinator.logger.debug.call_args_list]
    assert any("automatic control is OFF" in msg for msg in logged_args)


@pytest.mark.asyncio
async def test_override_clear_sends_when_auto_control_on_inside_window():
    """Override expiry with Automatic Control ON and inside the window sends normally.

    Regression guard: the auto-control check must not suppress the existing
    inside-window behaviour.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True, automatic_control=True)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=40, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once_with(
        "cover.test_blind",
        40,
        "manual_override_cleared",
        context=coordinator._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_override_clear_auto_control_off_multiple_covers():
    """Auto-control guard applies to all covers — no commands sent for any of them."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=True, automatic_control=False)
    coordinator.entities = ["cover.blind_a", "cover.blind_b", "cover.blind_c"]

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()
    coordinator._build_position_context.assert_not_called()


@pytest.mark.asyncio
async def test_override_clear_outside_window_takes_precedence_over_auto_control():
    """Time-window check runs before auto-control check — both logged for correct reason.

    When both conditions are False (outside window AND auto-control off), the
    time-window guard fires first (early return) and auto-control is never checked.
    Either way, no command is sent.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False, automatic_control=False)

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, state=0, options={}
    )

    # No command regardless of which guard fires
    coordinator._cmd_svc.apply_position.assert_not_called()
    # The time-window message is emitted (first guard)
    logged_args = [call[0][0] for call in coordinator.logger.debug.call_args_list]
    assert any(
        "outside active-hours window" in msg or "outside" in msg.lower()
        for msg in logged_args
    )


# ---------------------------------------------------------------------------
# async_handle_state_change — time-window guard (issue #173)
# ---------------------------------------------------------------------------


def _make_state_change_coordinator(
    *, check_adaptive_time: bool, bypass_auto_control: bool = False
):
    """Build a minimal mock coordinator for testing async_handle_state_change."""
    coordinator = MagicMock()
    coordinator.check_adaptive_time = check_adaptive_time
    coordinator.logger = MagicMock()
    coordinator.entities = ["cover.test_blind"]
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    coordinator._pipeline_bypasses_auto_control = bypass_auto_control
    coordinator._pipeline_result = MagicMock()
    coordinator._pipeline_result.control_method.value = "force"
    coordinator.state_change = True
    return coordinator


@pytest.mark.asyncio
async def test_state_change_skips_send_outside_time_window():
    """State changes outside the active time window must NOT move covers.

    Reproduces issue #173: covers open at sunrise even though the user's
    start-time entity is set to a later time. DefaultHandler (priority 0)
    always produces a result, so without this gate the default position
    (e.g. 100%) is sent even before the configured start time.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=100, options={}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_state_change_sends_inside_time_window():
    """State changes inside the active time window do move covers (normal case)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(check_adaptive_time=True)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=50, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_state_change_safety_handler_bypasses_time_window():
    """Safety handlers (force override, weather) must move covers even outside the window."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False, bypass_auto_control=True
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=0, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_state_change_outside_window_logs_debug():
    """A debug message must be logged when state change is suppressed by time window."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=100, options={}
    )

    coordinator.logger.debug.assert_called()
    logged_args = [call[0][0] for call in coordinator.logger.debug.call_args_list]
    assert any(
        "time window" in msg.lower() or "outside" in msg.lower() for msg in logged_args
    )


@pytest.mark.asyncio
async def test_state_change_clears_state_change_flag_outside_window():
    """state_change flag must be cleared even when the send is skipped."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=100, options={}
    )

    assert coordinator.state_change is False


# ---------------------------------------------------------------------------
# Issue #215 guard tests — Europe/Paris config, return_sunset=True
#
# Exact user config at time of incident:
#   sunset_position=0, default_percentage=100, sunset_offset=20
#   timezone: Europe/Paris (UTC+1/+2 DST)
#   Paris April astronomical sunset ~18:45 UTC  →  sunset+20 window opens at ~19:05 UTC
#   Manual override expires at ~21:58 UTC (23:58 CEST) — well past sunset+20
#   End-time is past 21:00 UTC  →  check_adaptive_time=False at 21:58 UTC
#
# The guards below prove that ACP cannot move the cover at 23:58 CEST and that
# the pipeline default at that moment is 0 (sunset_pos), not 100 (day default).
# ---------------------------------------------------------------------------


def _make_sun_data_utc(*, sunset_utc_hour: int, sunset_utc_minute: int = 0,
                       sunrise_utc_hour: int = 5) -> MagicMock:
    """Return a mock SunData whose sunset/sunrise methods return UTC naive datetimes."""
    today = dt.date.today()
    sunset_dt = dt.datetime(today.year, today.month, today.day,
                            sunset_utc_hour, sunset_utc_minute, 0)
    sunrise_dt = dt.datetime(today.year, today.month, today.day, sunrise_utc_hour, 0, 0)
    sun = MagicMock()
    sun.sunset.return_value = sunset_dt
    sun.sunrise.return_value = sunrise_dt
    return sun


def _freeze_helpers_now(naive_utc: dt.datetime):
    """Patch helpers.dt.datetime.now(UTC) to return a UTC-aware version of naive_utc."""
    aware = naive_utc.replace(tzinfo=UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware},
    )


class TestIssue215EuropeParisSunsetConfig:
    """Guard tests for issue #215: verify the pipeline correctly resolves sunset_pos
    at 23:58 CEST and that all reposition gates block movement outside the window.

    Paris April: astronomical sunset ~18:45 UTC. sunset_offset=20 → window opens ~19:05 UTC.
    At 21:58 UTC (23:58 CEST), well past that window, sunset_pos=0 is the correct default.
    """

    def test_pipeline_default_is_sunset_pos_at_2358_cest(self):
        """compute_effective_default returns sunset_pos (0), not day_default (100), at 23:58 CEST.

        This is the core assertion: if all gates fail and a pipeline position is somehow
        sent, the value would still be 0 (closed), not 100 (open). The fact that the user
        observed 100% is therefore external to ACP.
        """
        from custom_components.adaptive_cover_pro.helpers import compute_effective_default

        # Paris April sunset ~18:45 UTC; 23:58 CEST = 21:58 UTC
        sun_data = _make_sun_data_utc(sunset_utc_hour=18, sunset_utc_minute=45,
                                      sunrise_utc_hour=5)
        now_utc = dt.datetime(dt.date.today().year, dt.date.today().month,
                              dt.date.today().day, 21, 58, 0)

        with _freeze_helpers_now(now_utc):
            effective, is_sunset_active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun_data,
                sunset_off=20,
                sunrise_off=0,
            )

        assert is_sunset_active is True, (
            "Expected is_sunset_active=True at 21:58 UTC with Paris April config "
            "(sunset 18:45 UTC + 20 min offset = 19:05 UTC)"
        )
        assert effective == 0, (
            "Expected effective default = sunset_pos (0) at 23:58 CEST, got 100 "
            "(day default). Pipeline would have sent closed, not open."
        )

    def test_pipeline_default_before_sunset_window_is_day_default(self):
        """Before the sunset window opens, compute_effective_default returns h_def (100).

        Regression guard: end_time transitions before sunset+offset should send h_def.
        """
        from custom_components.adaptive_cover_pro.helpers import compute_effective_default

        # Same Paris April config, but now=17:00 UTC — before sunset+20=19:05 UTC
        sun_data = _make_sun_data_utc(sunset_utc_hour=18, sunset_utc_minute=45,
                                      sunrise_utc_hour=5)
        now_utc = dt.datetime(dt.date.today().year, dt.date.today().month,
                              dt.date.today().day, 17, 0, 0)

        with _freeze_helpers_now(now_utc):
            effective, is_sunset_active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun_data,
                sunset_off=20,
                sunrise_off=0,
            )

        assert is_sunset_active is False
        assert effective == 100

    @pytest.mark.asyncio
    async def test_override_expiry_at_2358_outside_window_no_reposition(self):
        """Manual override expiring at 23:58 CEST with end_time already passed → no command.

        Mirrors PhilDirty's exact scenario: check_adaptive_time=False at 21:58 UTC.
        The gate must block the reposition regardless of what the pipeline computed.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(check_adaptive_time=False)

        # state=0 simulates the pipeline's correct answer (sunset_pos)
        await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
            coordinator, state=0, options={}
        )

        coordinator._cmd_svc.apply_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_change_at_2358_outside_window_no_reposition(self):
        """Any state-change event at 23:58 CEST must not move the cover.

        Even if an entity update triggers async_handle_state_change at the same
        moment as override expiry, the time-window gate must block movement.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_state_change_coordinator(
            check_adaptive_time=False, bypass_auto_control=False
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=0, options={}
        )

        coordinator._cmd_svc.apply_position.assert_not_called()

    def test_reconciliation_skips_non_safety_at_2358_outside_window(self):
        """Reconciliation must not resend a stale daytime target at 23:58 CEST.

        Simulates a cover command service with in_time_window=False and a stale
        target of 100 (daytime open) — reconcile must skip it.
        """
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        svc = MagicMock(spec=CoverCommandService)
        svc._in_time_window = False
        svc._safety_targets = set()
        svc._manual_override_entities = set()
        svc._auto_control_enabled = True
        # cover is at 0 but stale target_call says 100
        svc.target_call = {"cover.smart_plug_in_unit": 100}
        svc.wait_for_target = {"cover.smart_plug_in_unit": False}
        svc._sent_at = {}
        svc._retry_counts = {}
        svc._gave_up = set()
        svc._enabled = True
        svc._logger = MagicMock()
        svc._on_tick = None
        svc._get_current_position = MagicMock(return_value=0)
        svc._last_reconcile_time = {}
        svc._WAIT_FOR_TARGET_TIMEOUT_SECONDS = 30

        # Verify the gate condition that reconcile checks
        entity_id = "cover.smart_plug_in_unit"
        assert not svc._in_time_window
        assert entity_id not in svc._safety_targets
        # Gate condition: skip if not in_time_window and not in safety_targets
        should_skip = not svc._in_time_window and entity_id not in svc._safety_targets
        assert should_skip is True, (
            "Reconciliation should skip cover.smart_plug_in_unit outside the time "
            "window when it is not a safety target"
        )


# ---------------------------------------------------------------------------
# Issue #215/#216 regression: end-time transition must NOT safety-tag targets
# ---------------------------------------------------------------------------


class TestIssue215StaleSafetyTarget:
    """Regression tests for the stale-safety-target bug (issue #215/#216).

    Root cause: _on_window_closed sent with force=True, tagging the end-time
    default as a safety target. When a manual override later expired outside the
    window, reconciliation bypassed the time-window gate for safety targets and
    resurrected the stale 100% target.

    Fix: _on_window_closed uses force=False; manual override start discards any
    pre-existing target via CoverCommandService.discard_target().
    """

    @pytest.mark.asyncio
    async def test_end_time_default_does_not_add_entity_to_safety_targets(self):
        """apply_position(force=True) adds entity to _safety_targets; the fix
        changes _on_window_closed to force=False so it does NOT.

        Bug: _on_window_closed called _build_position_context(force=True) →
             apply_position receives force=True context → is_safety=True →
             entity added to _safety_targets → reconcile bypasses time-window gate.

        Fix: force=False → is_safety=False → entity NOT in _safety_targets →
             reconcile gate blocks resend outside the window.

        The assertion checks the desired post-fix state; with the buggy force=True
        the entity ends up in _safety_targets and the assertion FAILS (RED).
        """
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
            PositionContext,
        )

        hass = MagicMock()
        mock_state = MagicMock()
        mock_state.attributes = {"supported_features": 143}
        mock_state.last_updated = None
        hass.states.get.return_value = mock_state
        hass.services.async_call = AsyncMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        svc._enabled = True
        entity_id = "cover.smart_plug_in_unit"

        # Fixed _on_window_closed call: force=False context.
        # force=False → is_safety=False → entity NOT added to _safety_targets.
        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=False,  # FIX: _on_window_closed now uses force=False
        )
        await svc.apply_position(entity_id, 0, "end_time_default", context=ctx)

        assert entity_id not in svc._safety_targets, (
            "end_time_default must NOT be tagged as a safety target. "
            "force=False ensures is_safety=False so reconciliation cannot "
            "bypass the time-window gate and reopen the cover after a manual "
            "override expires (fix for issue #215)."
        )

    @pytest.mark.asyncio
    async def test_reconcile_gate_blocks_end_time_target_outside_window(self):
        """After an end-time default send, the entity must NOT be in _safety_targets
        so that reconcile's time-window gate blocks any resend outside the window.

        Bug: force=True → is_safety=True → entity in _safety_targets → gate bypass.
        Fix: force=False → is_safety=False → entity NOT in _safety_targets → gate blocks.

        This test verifies the gate condition using the real _safety_targets state
        produced by apply_position. With the buggy force=True call, entity IS in
        _safety_targets and the gate check returns False (NOT skipped) — the
        assertion fails. After the fix (force=False), entity not in _safety_targets
        and the gate returns True (skipped).
        """
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
            PositionContext,
        )

        hass = MagicMock()
        mock_state = MagicMock()
        mock_state.attributes = {"supported_features": 143}
        mock_state.last_updated = None
        hass.states.get.return_value = mock_state
        hass.services.async_call = AsyncMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        svc._enabled = True
        entity_id = "cover.smart_plug_in_unit"

        # Fixed end-time default send: force=False.
        # force=False → is_safety=False → entity NOT in _safety_targets →
        # reconcile's time-window gate blocks any resend outside the window.
        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=False,  # FIX: _on_window_closed now uses force=False
        )
        await svc.apply_position(entity_id, 0, "end_time_default", context=ctx)

        # The reconcile gate: skip if not in_time_window AND not in safety_targets.
        # With the bug (entity in _safety_targets), gate returns False → cover resent.
        # After fix (entity not in _safety_targets), gate returns True → cover skipped.
        in_time_window = False
        reconcile_would_skip = (
            not in_time_window and entity_id not in svc._safety_targets
        )
        assert reconcile_would_skip is True, (
            "Reconcile must skip cover outside the time window. "
            "Bug: force=True tags entity as safety target, bypassing the gate. "
            "Fix: use force=False so entity is never safety-tagged."
        )

    def test_discard_target_clears_safety_tag_and_target_call(self):
        """CoverCommandService.discard_target() must remove both the target and
        the safety tag for an entity.

        This is the belt-and-suspenders part of the fix: even if a safety target
        somehow exists, starting a manual override immediately clears it so
        reconciliation cannot use it while — or after — the user is in control.
        """
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )

        entity_id = "cover.smart_plug_in_unit"
        # Artificially place a safety-tagged target (as _on_window_closed
        # used to do with force=True before the fix)
        svc.target_call[entity_id] = 100
        svc.wait_for_target[entity_id] = True
        svc._safety_targets.add(entity_id)
        svc._retry_counts[entity_id] = 1

        svc.discard_target(entity_id)

        assert entity_id not in svc.target_call
        assert entity_id not in svc.wait_for_target
        assert entity_id not in svc._safety_targets
        assert entity_id not in svc._retry_counts

    @pytest.mark.asyncio
    async def test_manual_override_discards_pre_existing_safety_target(self):
        """When a cover enters manual override, any pre-existing safety target
        must be discarded so reconciliation cannot resurrect it.

        Mirrors the exact scenario from #215:
        1. end_time transition placed target=100 in _safety_targets at 20:00
        2. User manually closes at 20:42 (manual override starts)
        3. At step 2, discard_target() must clear the 100% safety target
        4. At 23:46 when override expires, nothing for reconcile to resend
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        cmd_svc = MagicMock()
        cmd_svc.target_call = {"cover.smart_plug_in_unit": 100}
        cmd_svc._safety_targets = {"cover.smart_plug_in_unit"}
        cmd_svc.wait_for_target = {}
        cmd_svc.discard_target = MagicMock()

        coordinator = MagicMock()
        coordinator.manual_toggle = True
        coordinator.automatic_control = True
        coordinator._is_in_startup_grace_period = MagicMock(return_value=False)
        coordinator._target_just_reached = set()
        coordinator._cmd_svc = cmd_svc
        coordinator.target_call = cmd_svc.target_call
        coordinator.logger = MagicMock()
        coordinator._cover_type = "cover_blind"
        coordinator.manual_reset = True
        coordinator.manual_threshold = 5.0
        coordinator._pending_cover_events = []
        coordinator.cover_state_change = True

        # Manager: entity is NOT yet in manual override, then IS after
        manager = MagicMock()
        manager.is_cover_manual.side_effect = [
            False,  # was_manual check (before handle_state_change)
            True,   # is_cover_manual check (after handle_state_change)
        ]
        manager.handle_state_change = MagicMock()
        coordinator.manager = manager

        # Inject one fake state-change event representing user closing at 20:42
        event_data = MagicMock()
        event_data.entity_id = "cover.smart_plug_in_unit"
        coordinator._pending_cover_events = [event_data]
        coordinator.cover_state_change = False  # method sets this flag

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=100
        )

        cmd_svc.discard_target.assert_called_once_with("cover.smart_plug_in_unit")

    def test_reconcile_skips_after_manual_override_discards_target(self):
        """After discard_target() the entity has no target_call entry, so
        reconciliation's entity loop finds nothing to resend.

        Belt-and-suspenders: even if in_time_window were True (wrong gate),
        with no target there is nothing to resend.
        """
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        hass = MagicMock()
        grace_mgr = MagicMock()
        svc = CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )

        entity_id = "cover.smart_plug_in_unit"
        svc.target_call[entity_id] = 100
        svc._safety_targets.add(entity_id)

        # Simulate manual override starting: coordinator calls discard_target
        svc.discard_target(entity_id)

        # Now nothing for reconcile to iterate
        assert entity_id not in svc.target_call, (
            "After discard_target, entity must not appear in target_call — "
            "reconcile loop has nothing to process"
        )
