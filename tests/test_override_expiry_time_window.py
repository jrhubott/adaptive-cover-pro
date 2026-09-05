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

import dataclasses
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

from custom_components.adaptive_cover_pro.cover_types import get_policy

UTC = dt.UTC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(
    *,
    check_adaptive_time: bool,
    automatic_control: bool = True,
    clock_window_open: bool | None = None,
    acts_outside_clock_window: bool = False,
):
    """Build a minimal mock coordinator for testing _async_force_send_pipeline_position.

    ``clock_window_open`` defaults to mirror ``check_adaptive_time`` because every
    scenario in this file models "outside the window" as a genuinely closed clock
    (after end_time / before start_time). The gate-dark case (clock open, gate
    dark) is exercised explicitly by passing ``clock_window_open=True`` (#656).

    ``acts_outside_clock_window`` is the #943-item-B admission — "may this
    result reach the hardware outside the clock?". It must be pinned explicitly
    because a bare ``MagicMock`` attribute is truthy, which would silently open
    every window guard in this file.
    """
    coordinator = MagicMock()
    coordinator.check_adaptive_time = check_adaptive_time
    coordinator.clock_window_open = (
        check_adaptive_time if clock_window_open is None else clock_window_open
    )
    coordinator._pipeline_acts_outside_clock_window = acts_outside_clock_window
    # No result computed yet — the default for every scenario in this file. It
    # is pinned for the same reason as the flag above: the force-send path reads
    # ``_pipeline_result.outside_window_constraint_active`` to decide whether to
    # route per cover, and a bare ``MagicMock`` attribute is truthy.
    coordinator._pipeline_result = None
    coordinator.automatic_control = automatic_control
    coordinator.logger = MagicMock()
    coordinator.entities = ["cover.test_blind"]
    # Real policy: the dispatch loops ask it for the entity order (#1115).
    coordinator._policy = get_policy("cover_blind")
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    # The per-entity dispatch seam is identity for a blind (no rail remap).
    coordinator._entity_target = lambda cover, state: state
    return coordinator


# ---------------------------------------------------------------------------
# _async_force_send_pipeline_position — time-window guard
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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
async def test_override_clear_sends_when_gate_dark_but_clock_open():
    """Override clear at night (gate dark, clock still open) must dispatch the night position.

    Issue #656: with a degenerate clock (start/end = 00:00) the user's clock
    window stays open all night; is_active is False purely because the daytime
    gate reads dark. The pipeline computes the correct night/default position
    (here 0), and clearing the override must send it — not leave the cover where
    the user last parked it.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(
        check_adaptive_time=False,  # gate dark → is_active False
        clock_window_open=True,  # but the clock is still open
        automatic_control=True,
    )

    result = await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=0, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once_with(
        "cover.test_blind",
        0,
        "manual_override_cleared",
        context=coordinator._build_position_context.return_value,
    )
    assert result == {"cover.test_blind"}


@pytest.mark.asyncio
async def test_override_clear_logs_debug_when_outside_window():
    """A debug message must be logged when send is skipped for time-window reasons."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=75, options={"test_option": True}
    )

    # _build_position_context must be called with force=True but NOT is_safety
    # (override clear bypasses gates but is not a safety-critical target)
    coordinator._build_position_context.assert_called_once_with(
        "cover.test_blind",
        {"test_option": True},
        force=True,
        # Default for this caller — the auto-control bypass is opt-in and only
        # the Apply Calculated Position button (#1045) requests it.
        bypass_auto_control=False,
        sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
    )
    ctx = coordinator._build_position_context.call_args
    assert not ctx.kwargs.get("is_safety", False), (
        "_async_force_send_pipeline_position must NOT pass is_safety=True — "
        "override-clear targets are NOT safety targets and must not persist "
        "across window boundaries (fix for issue #223)"
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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
# _async_force_send_pipeline_position — automatic-control guard (issue #139)
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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
    *,
    check_adaptive_time: bool,
    bypass_auto_control: bool = False,
    clock_window_open: bool | None = None,
    acts_outside_clock_window: bool | None = None,
):
    """Build a minimal mock coordinator for testing async_handle_state_change.

    ``clock_window_open`` defaults to mirror ``check_adaptive_time`` (genuinely
    closed clock). Pass ``clock_window_open=True`` to model the gate-dark case
    where the clock is open but the daytime gate reads dark (#656).

    ``acts_outside_clock_window`` is the #943-item-B admission. It defaults to
    ``bypass_auto_control`` because that is what this fixture already uses as
    its ``is_safety`` stand-in, and on the real coordinator the predicate is
    ``is_safety OR constraint_admitted`` — a safety result always acts outside
    the clock. It is pinned explicitly (never left as a bare ``MagicMock``
    attribute, which is truthy) so it cannot silently open the window guard
    these tests exist to hold shut.
    """
    coordinator = MagicMock()
    coordinator.check_adaptive_time = check_adaptive_time
    coordinator.clock_window_open = (
        check_adaptive_time if clock_window_open is None else clock_window_open
    )
    coordinator._pipeline_acts_outside_clock_window = (
        bypass_auto_control
        if acts_outside_clock_window is None
        else acts_outside_clock_window
    )
    coordinator.logger = MagicMock()
    coordinator.entities = ["cover.test_blind"]
    # Real policy: the dispatch loops ask it for the entity order (#1115).
    coordinator._policy = get_policy("cover_blind")
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    coordinator._pipeline_bypasses_auto_control = bypass_auto_control
    coordinator._pipeline_is_safety_handler = bypass_auto_control
    coordinator._pipeline_result = MagicMock()
    coordinator._pipeline_result.skip_command = False
    coordinator._pipeline_result.control_method.value = "force"
    coordinator.state_change = True

    async def _dispatch_side_effect(cover, state, reason, ctx):
        return await coordinator._cmd_svc.apply_position(
            cover, state, reason, context=ctx
        )

    coordinator._dispatch_to_cover = AsyncMock(side_effect=_dispatch_side_effect)
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
async def test_state_change_dispatches_when_gate_dark_but_clock_open():
    """A normal-update state change at night (gate dark, clock open) must dispatch.

    Issue #656: the reporter's custom-position handler wins the pipeline but the
    L1646 guard suppressed dispatch because is_active was False (gate dark). With
    the clock window still open, the computed night/custom position must be sent.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,  # gate dark → is_active False
        clock_window_open=True,  # but the clock is still open
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=50, options={}
    )

    # The L1646 early-return must NOT be taken: a command is dispatched.
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
# Issue #943 item B — an opted-in constraint acts outside the CLOCK window
#
# The gap these pin is narrow on purpose: the admission is granted by the
# registry only when an eligible bound actually clamped something, and the
# value the guard then lets through is the pseudo-hold's — the cover's own
# read on every axis the bound did not constrain. A plain DEFAULT winner is
# still blocked (``test_state_change_skips_send_outside_time_window``).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_change_sends_admitted_constraint_outside_clock_window():
    """The primary gap: an admitted constraint dispatches before start time."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_constraint_release_outside_window_stays_hands_off():
    """When the trigger releases overnight ACP goes quiet — no return-to-default.

    Returning the cover to a default at 03:00 is the #173 defect; the cover
    stays where the clamp left it and the licence is revoked so reconciliation
    cannot resurrect the target either.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=False,
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=100, options={}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()
    coordinator._cmd_svc.clear_outside_window_targets.assert_called_once_with()


@pytest.mark.asyncio
async def test_closed_clock_cycle_with_nothing_admitted_revokes_stale_safety_verdicts():
    """Issue #1311: a safety booking made before the clock closed must not
    stay licensed once nothing is admitted out here.

    This branch returns before apply_position, so neither is_safety writer
    (_prepare_service_call, _record_safety_verdict) ever runs — the licence
    would otherwise survive until an end-of-window send that CONF_RETURN_SUNSET
    OFF never produces, and reconciliation step 4 resends it all night.

    The revoke must run BEFORE clear_outside_window_targets: a row holding both
    flags is spared by that sweeper while it still reads as safety, and its
    outside-window licence is admitted by step 4 just as readily.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=False,
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=100, options={}
    )

    coordinator._cmd_svc.revoke_safety_verdicts.assert_called_once_with()
    coordinator._cmd_svc.apply_position.assert_not_called()

    names = [c[0] for c in coordinator._cmd_svc.mock_calls]
    assert names.index("revoke_safety_verdicts") < names.index(
        "clear_outside_window_targets"
    )


@pytest.mark.asyncio
async def test_live_safety_result_outside_the_clock_window_keeps_its_verdict():
    """The revoke is gated at the call site, not inside the method.

    With weather_outside_window ON the handler still wins at 03:00, so
    acts_outside_clock_window is True, this branch never runs, and the blanket
    revoke cannot reach a licence that is still being earned. The gate is sound
    because the pipeline result is per-instance: one live safety verdict
    anywhere holds the branch shut for every entity.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=0, options={}
    )

    coordinator._cmd_svc.revoke_safety_verdicts.assert_not_called()


@pytest.mark.asyncio
async def test_safety_release_edge_outside_the_clock_window_keeps_its_verdict():
    """The safety-release edge is exempt too, for a different reason.

    ``safety_release=True`` is the cycle a priority-100 slot stood down on. It
    is deliberately let past the closed-clock guard so the release itself can
    be dispatched, which means ``apply_position`` runs and writes this cycle's
    verdict for every entity by the ordinary route. The blanket sweep has no
    work to do here and must not pre-empt that per-entity write.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=False,
    )

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=0, options={}, safety_release=True
    )

    coordinator._cmd_svc.revoke_safety_verdicts.assert_not_called()
    # Both halves of the exemption, not just the visible one. The sweep is
    # skipped BECAUSE dispatch happens and writes each entity's verdict by the
    # ordinary route; a future change that returned before apply_position would
    # otherwise leave this test green while the licence leaked.
    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_force_send_admits_constraint_outside_clock_window():
    """The force-send / Apply-Calculated path honours the same admission."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=40, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_force_send_outside_time_window_revokes_stale_safety_verdicts():
    """Issue #1313: force-send's closed-clock exit must revoke stale safety
    verdicts too, not just async_handle_state_change's (#1311/#1312).

    Reachable via _dispatch_for_cycle's auto_expired branch: a manual-override
    auto-expiry cycle outside the clock window, with no tracked-entity
    state-change edge this cycle, takes THIS exit instead of
    async_handle_state_change's. A safety booking made by an earlier cycle
    would otherwise ride forward unrevoked past this exit specifically —
    reconciliation step 4 admits it because acts_outside_clock_window is the
    OR of is_safety and outside_window_constraint.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=0, options={}
    )

    coordinator._cmd_svc.revoke_safety_verdicts.assert_called_once_with()
    coordinator._cmd_svc.clear_outside_window_targets.assert_called_once_with()
    coordinator._cmd_svc.apply_position.assert_not_called()

    names = [c[0] for c in coordinator._cmd_svc.mock_calls]
    assert names.index("revoke_safety_verdicts") < names.index(
        "clear_outside_window_targets"
    )


@pytest.mark.asyncio
async def test_force_send_admitted_outside_clock_window_keeps_its_verdict():
    """The revoke is gated at the call site, not inside the method.

    With acts_outside_clock_window True the guard's admission condition is
    satisfied, so this closed-clock exit never runs and the blanket revoke
    cannot reach a licence that is still being earned — parity with
    test_live_safety_result_outside_the_clock_window_keeps_its_verdict.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=40, options={}
    )

    coordinator._cmd_svc.revoke_safety_verdicts.assert_not_called()


@pytest.mark.asyncio
async def test_first_refresh_sends_admitted_constraint_outside_window():
    """A restart at 03:00 re-establishes an admitted clamp instead of idling."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )
    coordinator.manager.is_cover_manual = MagicMock(return_value=False)
    coordinator._is_reload = False
    coordinator.first_refresh = True

    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
        coordinator, state=40, options={}
    )

    coordinator._cmd_svc.apply_position.assert_called_once()


@pytest.mark.asyncio
async def test_first_refresh_still_blocked_without_admission():
    """Positive control: no admission at 03:00 still means no startup move."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_state_change_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=False,
    )
    coordinator.manager.is_cover_manual = MagicMock(return_value=False)
    coordinator._is_reload = False
    coordinator.first_refresh = True

    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
        coordinator, state=100, options={}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()


def _make_pseudo_hold_coordinator(*, positions: dict[str, int], mean: int):
    """Build a force-send coordinator carrying the outside-window pseudo-hold.

    Outside the clock window the registry judges a computed winner as if it held
    each cover's current read, so ``PipelineResult.position`` is the instance
    MEAN and the per-cover answers live in ``hold_clamp_verdicts`` (#943 item B
    borrowing #1174's machinery).
    """
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import (
        HoldClampVerdict,
        PipelineResult,
    )

    coordinator = _make_coordinator(
        check_adaptive_time=False,
        acts_outside_clock_window=True,
    )
    coordinator.entities = list(positions)
    coordinator._inverse_state = False
    coordinator.position_axis_inverted = False
    coordinator._to_cover_frame = lambda position: position
    coordinator._pipeline_result = PipelineResult(
        position=mean,
        control_method=ControlMethod.DEFAULT,
        outside_window_constraint_active=True,
        hold_clamp_verdicts={
            entity: HoldClampVerdict(held_position=pos, released=True, target=pos)
            for entity, pos in positions.items()
        },
    )
    coordinator._dispatch_to_cover = (
        AdaptiveDataUpdateCoordinator._dispatch_to_cover.__get__(coordinator)
    )
    # The real frame split too — a verdict's target is a bound edge or a raw
    # read, and only the first is calibrated. Uncalibrated here, so both
    # branches agree; binding it keeps the seam under test rather than a mock.
    coordinator._verdict_dispatch_target = (
        AdaptiveDataUpdateCoordinator._verdict_dispatch_target.__get__(coordinator)
    )
    return coordinator


@pytest.mark.asyncio
async def test_force_send_outside_window_honors_per_cover_verdicts():
    """An admitted force-send must not blast the instance mean at every cover.

    #943 item B: outside the clock window the registry converts a computed
    winner into a pseudo-hold, so ``PipelineResult.position`` — and therefore
    ``coordinator.state`` — is the arithmetic MEAN of the instance, while each
    cover's real answer sits in ``hold_clamp_verdicts``. Two covers at 80 and
    10 with a tilt bound binding would both be driven ~35 points at 03:00.

    ``honor_holds`` cannot rescue this: ``_INSTANCE_MEAN_POSITION_HOLDS`` is
    keyed on ``ControlMethod`` and the pseudo-hold's winner is ``DEFAULT``, and
    both callers that reach here outside the window (the manual-override
    auto-expiry branch and ``async_reset_manual_overrides``) pass
    ``honor_holds=False`` anyway.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_pseudo_hold_coordinator(
        positions={"cover.high": 80, "cover.low": 10}, mean=45
    )

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=45, options={}
    )

    # Each cover stays where it already is; only the constrained axis moves.
    assert [
        (call.args[0], call.args[1])
        for call in coordinator._cmd_svc.apply_position.call_args_list
    ] == [("cover.high", 80), ("cover.low", 10)]


@pytest.mark.asyncio
async def test_force_send_outside_window_skips_covers_the_bound_left_alone():
    """A cover the bound never moved gets a hold-skip record, not a command.

    The position-axis-only case: the pseudo-hold's verdicts say "released" for
    the cover a ceiling lowered and "held" for the one already compliant. The
    held cover must not receive the mean.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import HoldClampVerdict

    coordinator = _make_pseudo_hold_coordinator(
        positions={"cover.high": 80, "cover.low": 10}, mean=45
    )
    coordinator._pipeline_result = dataclasses.replace(
        coordinator._pipeline_result,
        hold_clamp_verdicts={
            # A ceiling of 30 lowered the high cover and left the low one alone.
            "cover.high": HoldClampVerdict(held_position=80, released=True, target=30),
            "cover.low": HoldClampVerdict(held_position=10, released=False, target=10),
        },
    )

    sent = await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=45, options={}
    )

    assert [
        (call.args[0], call.args[1])
        for call in coordinator._cmd_svc.apply_position.call_args_list
    ] == [("cover.high", 30)]
    assert sent == {"cover.high"}
    assert coordinator._cmd_svc.record_skipped_action.call_args.args[0] == "cover.low"


@pytest.mark.asyncio
async def test_force_send_in_window_still_sends_the_shared_state():
    """Byte-identical in-window control: no verdict routing while the clock is open.

    ``outside_window_constraint_active`` is only ever set on a closed-clock
    cycle, so an in-window force-send keeps the direct ``apply_position`` path
    and the shared ``state`` even with per-cover verdicts present.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_pseudo_hold_coordinator(
        positions={"cover.high": 80, "cover.low": 10}, mean=45
    )
    coordinator.clock_window_open = True
    coordinator.check_adaptive_time = True
    coordinator._pipeline_acts_outside_clock_window = False
    coordinator._pipeline_result = dataclasses.replace(
        coordinator._pipeline_result, outside_window_constraint_active=False
    )

    await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state=45, options={}
    )

    assert [
        (call.args[0], call.args[1])
        for call in coordinator._cmd_svc.apply_position.call_args_list
    ] == [("cover.high", 45), ("cover.low", 45)]


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


def _make_sun_data_utc(
    *, sunset_utc_hour: int, sunset_utc_minute: int = 0, sunrise_utc_hour: int = 5
) -> MagicMock:
    """Return a mock SunData whose sunset/sunrise methods return UTC naive datetimes."""
    today = dt.date.today()
    sunset_dt = dt.datetime(
        today.year, today.month, today.day, sunset_utc_hour, sunset_utc_minute, 0
    )
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
        from custom_components.adaptive_cover_pro.helpers import (
            compute_effective_default,
        )

        # Paris April sunset ~18:45 UTC; 23:58 CEST = 21:58 UTC
        sun_data = _make_sun_data_utc(
            sunset_utc_hour=18, sunset_utc_minute=45, sunrise_utc_hour=5
        )
        now_utc = dt.datetime(
            dt.date.today().year, dt.date.today().month, dt.date.today().day, 21, 58, 0
        )

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
        from custom_components.adaptive_cover_pro.helpers import (
            compute_effective_default,
        )

        # Same Paris April config, but now=17:00 UTC — before sunset+20=19:05 UTC
        sun_data = _make_sun_data_utc(
            sunset_utc_hour=18, sunset_utc_minute=45, sunrise_utc_hour=5
        )
        now_utc = dt.datetime(
            dt.date.today().year, dt.date.today().month, dt.date.today().day, 17, 0, 0
        )

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
        await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
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

        svc = CoverCommandService(
            hass=MagicMock(),
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=MagicMock(),
        )
        svc._in_time_window = False
        # cover is at 0 but stale target_call says 100
        svc.set_target("cover.smart_plug_in_unit", 100)
        svc.set_waiting("cover.smart_plug_in_unit", False)

        # Verify the gate condition that reconcile checks
        entity_id = "cover.smart_plug_in_unit"
        assert not svc._in_time_window
        assert not svc.is_safety_target(entity_id)
        # Gate condition: skip if not in_time_window and not in safety_targets
        should_skip = not svc._in_time_window and not svc.is_safety_target(entity_id)
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

        assert not svc.is_safety_target(entity_id), (
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
        reconcile_would_skip = not in_time_window and not svc.is_safety_target(
            entity_id
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
        svc.set_target(entity_id, 100)
        svc.set_waiting(entity_id, True)
        svc.state(entity_id).is_safety = True
        svc.state(entity_id).retry_count = 1

        svc.discard_target(entity_id)

        assert not svc.has_target(entity_id)
        assert not svc.is_waiting_for_target(entity_id)
        assert not svc.is_safety_target(entity_id)
        assert svc.state(entity_id).retry_count == 0

    @pytest.mark.asyncio
    async def test_manual_override_discards_pre_existing_safety_target(self):
        """When a cover enters manual override, any pre-existing safety target
        must be discarded so reconciliation cannot resurrect it.

        Mirrors the exact scenario from #215:
        1. end_time transition placed target=100 in _safety_targets at 20:00
        2. User manually closes at 20:42 (manual override starts)
        3. At step 2, discard_target() must clear the 100% safety target
        4. At 23:46 when override expires, nothing for reconcile to resend

        The discard now lives in the manager's ``on_engaged`` edge callback
        (wired by the coordinator to ``cmd_svc.discard_target``) rather than in
        the coordinator's state-change loop, so this verifies the relocated
        seam directly on a real engine.
        """
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        cmd_svc = MagicMock()
        cmd_svc.discard_target = MagicMock()

        entity_id = "cover.smart_plug_in_unit"
        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 2},
            logger=MagicMock(),
            on_engaged=cmd_svc.discard_target,
        )
        manager.add_covers([entity_id])

        # Policy reports the user-moved position (0) against the commanded
        # safety target (100): a delta well past the threshold.
        policy = MagicMock()
        policy.read_axis_value.return_value = 0
        policy.primary_axis_suppression.return_value = False

        event_data = MagicMock()
        event_data.entity_id = entity_id
        event_data.old_state = MagicMock()
        new_state = MagicMock()
        new_state.state = "open"
        new_state.attributes = {}
        new_state.context = None
        new_state.last_updated = "2026-05-10T20:42:00+00:00"
        event_data.new_state = new_state

        manager.handle_state_change(
            event_data,
            100,  # commanded safety target
            policy,
            True,  # allow_reset
            lambda _e: False,  # is_waiting
            5,  # manual_threshold
            is_in_command_grace=lambda _e: False,
            is_in_transit=lambda _e: False,
        )

        assert manager.is_cover_manual(entity_id)
        cmd_svc.discard_target.assert_called_once_with(entity_id)

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
        svc.set_target(entity_id, 100)
        svc.state(entity_id).is_safety = True

        # Simulate manual override starting: coordinator calls discard_target
        svc.discard_target(entity_id)

        # Now nothing for reconcile to iterate
        assert not svc.has_target(entity_id), (
            "After discard_target, entity must not appear in target_call — "
            "reconcile loop has nothing to process"
        )


# ---------------------------------------------------------------------------
# Issue #223: override-clear must NOT safety-tag the target
# ---------------------------------------------------------------------------


class TestIssue223OverrideClearSafetyTag:
    """Regression tests for issue #223.

    Root cause: _async_force_send_pipeline_position called apply_position with
    force=True (to bypass delta gates), but force=True was conflated with
    is_safety=True, adding the entity to _safety_targets.  When the window
    later closed, clear_non_safety_targets() preserved the entity and
    reconciliation bypassed the time-window guard, resending the stale target.

    Fix: decouple force (bypass gates) from is_safety (safety target
    classification) by adding an explicit is_safety field to PositionContext.
    _async_force_send_pipeline_position still uses force=True but is_safety
    defaults to False, so the target is cleaned up normally when the window
    closes.
    """

    def _make_svc(self):
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        hass = MagicMock()
        mock_state = MagicMock()
        mock_state.attributes = {"supported_features": 143}
        mock_state.last_updated = None
        hass.states.get.return_value = mock_state
        hass.services.async_call = AsyncMock()
        grace_mgr = MagicMock()
        return CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )

    @pytest.mark.asyncio
    async def test_override_clear_does_not_safety_tag_entity(self):
        """Override-clear apply_position must NOT add entity to _safety_targets.

        force=True is still used to bypass delta/time gates, but is_safety must
        remain False so the target does not persist across window boundaries.
        """
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            PositionContext,
        )

        svc = self._make_svc()
        svc._enabled = True
        entity_id = "cover.bedroom_blind"

        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=True,
            is_safety=False,  # override clear: bypass gates but NOT safety
        )
        await svc.apply_position(entity_id, 55, "manual_override_cleared", context=ctx)

        assert not svc.is_safety_target(entity_id), (
            "Override-clear target must NOT be tagged as a safety target. "
            "is_safety=False ensures the target is cleaned up when the window "
            "closes (fix for issue #223)."
        )

    @pytest.mark.asyncio
    async def test_override_clear_target_cleared_by_window_close(self):
        """After override-clear, clear_non_safety_targets() must remove the target.

        If the entity was incorrectly safety-tagged, clear_non_safety_targets()
        would preserve it and reconciliation would resend it outside the window.
        """
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            PositionContext,
        )

        svc = self._make_svc()
        svc._enabled = True
        entity_id = "cover.bedroom_blind"

        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=True,
            is_safety=False,
        )
        await svc.apply_position(entity_id, 55, "manual_override_cleared", context=ctx)

        # Simulate window closing
        svc.clear_non_safety_targets()

        assert not svc.has_target(entity_id), (
            "After window close, override-clear target must be removed — "
            "it was not safety-tagged so clear_non_safety_targets() must clear it."
        )

    @pytest.mark.asyncio
    async def test_safety_handler_still_tags_entity(self):
        """Safety handlers (force override, weather) must still tag entities.

        Verifies that the fix does not accidentally break genuine safety targets.
        """
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            PositionContext,
        )

        svc = self._make_svc()
        svc._enabled = True
        entity_id = "cover.bedroom_blind"

        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=True,
            is_safety=True,  # genuine safety handler
        )
        await svc.apply_position(entity_id, 0, "force_override", context=ctx)

        assert svc.is_safety_target(entity_id), (
            "Safety handler target must be tagged — reconciliation needs it to "
            "persist across window boundaries."
        )

    @pytest.mark.asyncio
    async def test_reconcile_skips_override_clear_target_outside_window(self):
        """Full scenario: override clears → window closes → reconcile must skip.

        This is the exact bug from issue #223: the cover should NOT be resent
        after the window closes if the override expired during the window.
        """
        import logging

        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
            PositionContext,
        )

        hass = MagicMock()
        mock_state = MagicMock()
        mock_state.attributes = {"supported_features": 143, "current_position": 55}
        mock_state.last_updated = None
        mock_state.state = "open"
        hass.states.get.return_value = mock_state
        hass.services.async_call = AsyncMock()

        svc = CoverCommandService(
            hass=hass,
            logger=logging.getLogger("test"),
            cover_type="cover_blind",
            grace_mgr=MagicMock(),
        )
        svc._enabled = True
        entity_id = "cover.bedroom_blind"

        # Step 1: Override clears inside window → apply_position(force=True, is_safety=False)
        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=5,
            time_threshold=2,
            special_positions=[0, 100],
            force=True,
            is_safety=False,
        )
        await svc.apply_position(entity_id, 55, "manual_override_cleared", context=ctx)
        svc.set_waiting(entity_id, False)  # cover reached position

        # Step 2: Window closes → clear_non_safety_targets removes the target
        svc.in_time_window = False
        svc.clear_non_safety_targets()

        # Reset the mock so we only track calls made BY reconciliation
        hass.services.async_call.reset_mock()

        # Step 3: Reconciliation — must NOT resend (target was cleared)
        await svc.run_reconciliation_pass(dt.datetime.now(UTC))

        (
            hass.services.async_call.assert_not_called(),
            (
                "Reconciliation must not resend the override-clear target outside the "
                "time window — the entity was not safety-tagged and was cleaned up "
                "when the window closed (fix for issue #223)."
            ),
        )


# ---------------------------------------------------------------------------
# Sun-mode expiry driven through the real coordinator guards (issue #1051)
# ---------------------------------------------------------------------------


def _sun_data(
    *,
    sunrise=dt.datetime(2026, 7, 2, 4, 0),
    sunset=dt.datetime(2026, 7, 2, 21, 0),
    next_sunrise=dt.datetime(2026, 7, 3, 4, 1),
    next_sunset=dt.datetime(2026, 7, 3, 20, 59),
):
    """Return a mid-latitude summer day: sunrise 04:00, sunset 21:00 UTC."""
    sun_data = MagicMock()
    sun_data.sunrise.return_value = sunrise.replace(tzinfo=UTC)
    sun_data.sunset.return_value = sunset.replace(tzinfo=UTC)
    sun_data.next_sunrise.return_value = next_sunrise.replace(tzinfo=UTC)
    sun_data.next_sunset.return_value = next_sunset.replace(tzinfo=UTC)
    return sun_data


def _sunrise_mode_coordinator(**kwargs):
    """Coordinator with the REAL resolver, manager, and force-send path wired.

    Everything between "the resolver picks a sunrise deadline" and "the guard
    decides whether to reposition" is production code here: only the HA reads
    (sun data, cover command service, position context) are stubbed.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_DURATION_MODE,
        MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE,
    )
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    coord = _make_coordinator(**kwargs)
    coord.config_entry.options = {
        CONF_MANUAL_OVERRIDE_DURATION_MODE: (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
        )
    }
    coord.manual_override_duration_mode = MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
    # No sunrise-time entity configured — the resolver falls through to astral.
    coord.hass.states.get.return_value = None
    coord._cover_data = MagicMock(sun_data=_sun_data())
    coord._time_mgr.end_time = None
    coord._resolve_override_deadline = (
        AdaptiveDataUpdateCoordinator._resolve_override_deadline.__get__(coord)
    )
    coord._async_force_send_pipeline_position = (
        AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position.__get__(coord)
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(), reset_duration={"hours": 2}, logger=MagicMock()
    )
    manager.add_covers(["cover.test_blind"])
    manager.set_deadline_resolver(coord._resolve_override_deadline)
    coord.manager = manager
    return coord


def _engage_override_at_dusk(coord):
    """Engage a manual override at 22:00 — the close-the-blind-at-dusk case."""
    coord.manager.set_last_updated(
        "cover.test_blind",
        MagicMock(last_updated=dt.datetime(2026, 7, 2, 22, 0, tzinfo=UTC)),
        allow_reset=True,
    )


class TestSunModeExpiryThroughCoordinatorGuards:
    """A sun-anchored expiry lands on the guards #215/#223 were written for.

    Issue #1051 item 3. Everything else in this file drives
    ``_async_force_send_pipeline_position`` directly; nothing walked the whole
    path — resolver resolves a sunrise deadline, ``reset_if_needed`` returns the
    entity, the guards suppress the reposition. The sun modes relocate expiry
    onto exactly the dawn/dusk moments those issues were about, so a
    ``until_sunrise`` deadline fires inside #215's literal timeline.
    """

    @pytest.mark.asyncio
    async def test_sunrise_expiry_outside_window_does_not_reposition(self):
        """Expiry at sunrise, hours before the window opens → stay put."""
        coord = _sunrise_mode_coordinator(check_adaptive_time=False)
        _engage_override_at_dusk(coord)

        # 02:00 — the fixed 2 h duration would have expired at midnight, and
        # #215's "reopening at 2 a.m." is exactly this moment. The sun deadline
        # holds, which is what proves the resolver set it and not the duration.
        with freeze_time("2026-07-03 02:00:00"):
            assert coord.manager.reset_if_needed() == set()
        assert coord.manager.is_cover_manual("cover.test_blind") is True

        # Just past the resolved sunrise (04:01) the override clears.
        with freeze_time("2026-07-03 04:01:01"):
            expired = coord.manager.reset_if_needed()
        assert expired == {"cover.test_blind"}

        sent = await coord._async_force_send_pipeline_position(60, {})

        assert sent == set()
        coord._cmd_svc.apply_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sunrise_expiry_with_auto_control_off_does_not_reposition(self):
        """Same expiry inside the window, automatic control OFF → stay put."""
        coord = _sunrise_mode_coordinator(
            check_adaptive_time=True,
            clock_window_open=True,
            automatic_control=False,
        )
        _engage_override_at_dusk(coord)

        with freeze_time("2026-07-03 04:01:01"):
            expired = coord.manager.reset_if_needed()
        assert expired == {"cover.test_blind"}

        sent = await coord._async_force_send_pipeline_position(60, {})

        assert sent == set()
        coord._cmd_svc.apply_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sunrise_expiry_inside_window_does_reposition(self):
        """Positive control: with both guards open the same expiry DOES send.

        Without this the two guard tests above would pass against an inert
        harness that never dispatches for any reason.
        """
        coord = _sunrise_mode_coordinator(check_adaptive_time=True)
        _engage_override_at_dusk(coord)

        with freeze_time("2026-07-03 04:01:01"):
            expired = coord.manager.reset_if_needed()
        assert expired == {"cover.test_blind"}

        sent = await coord._async_force_send_pipeline_position(60, {})

        assert sent == {"cover.test_blind"}
        coord._cmd_svc.apply_position.assert_awaited_once()
