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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    coordinator._cmd_svc.apply_position = AsyncMock(return_value=("sent", "set_cover_position"))
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
        "cover.test_blind", 50, "manual_override_cleared", context=coordinator._build_position_context.return_value
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
    assert "outside active-hours window" in logged_message or "outside" in logged_message.lower()


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
    calls = [call.args[0] for call in coordinator._cmd_svc.apply_position.call_args_list]
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
        "cover.test_blind", 40, "manual_override_cleared",
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
    assert any("outside active-hours window" in msg or "outside" in msg.lower() for msg in logged_args)
