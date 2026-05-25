"""Manual override detection via HA Context user_id.

Covers the user-context fast-path that handles assumed-state and OPEN/CLOSE-only
covers (e.g. Bond/Olibra Somfy RMS12 — supported_features=11, no SET_POSITION,
no current_position attribute). The position-math path in
``AdaptiveCoverManager.handle_state_change`` is unreliable for these covers
because:

- ``current_position`` may not exist (entity exposes only the open/closed string).
- ``assumed_state=True`` covers report the last commanded value, not the real
  one — so reconciliation can race ahead and overwrite the live state before
  the queued state-change event is drained, masking the user's input.

The fix has three parts: (1) ``helpers.get_open_close_state`` accepts an
optional ``state_obj`` so detection sees the event payload, not the live state.
(2) ``CoverCommandService`` records every position-command HA Context id via
``PositionContextTracker``. (3) The coordinator's
``async_handle_cover_state_change`` fast-paths state changes whose context
carries a non-None ``user_id`` AND whose context.id is not in the position
tracker — those are unambiguously user-initiated and trigger
``handle_user_initiated_state_change`` directly, bypassing position math.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_change_data(
    entity_id: str,
    *,
    new_state_value: str = "open",
    attributes: dict | None = None,
    user_id: str | None = None,
    context_id: str = "ctx-test-123",
    old_state_value: str = "closed",
):
    """Build a state-change event payload with a real-looking new_state.context."""
    data = MagicMock()
    data.entity_id = entity_id
    data.old_state = MagicMock()
    data.old_state.state = old_state_value
    data.new_state = MagicMock()
    data.new_state.state = new_state_value
    data.new_state.attributes = attributes or {}
    ctx = MagicMock()
    ctx.id = context_id
    ctx.user_id = user_id
    data.new_state.context = ctx
    data.new_state.last_updated = "2026-05-10T19:00:00+00:00"
    return data


def _make_coordinator(
    entity_id: str = "cover.test",
    *,
    target: int | None = 0,
    manual_toggle: bool = True,
    automatic_control: bool = True,
    manual_threshold: int | None = None,
    target_just_reached: set | None = None,
    acp_position_contexts: set[str] | None = None,
):
    """Mock coordinator wired to exercise async_handle_cover_state_change."""
    coordinator = MagicMock()
    coordinator.manual_toggle = manual_toggle
    coordinator.automatic_control = automatic_control
    coordinator.manual_ignore_external = False
    cmd_svc = MagicMock()
    cmd_svc.get_target = MagicMock(return_value=target)
    cmd_svc.is_waiting_for_target = MagicMock(return_value=False)
    cmd_svc.discard_target = MagicMock()
    cmd_svc.was_acp_position_context = MagicMock(
        side_effect=lambda cid: cid in (acp_position_contexts or set())
    )
    coordinator._cmd_svc = cmd_svc
    coordinator._cover_type = "cover_awning"
    coordinator.manual_reset = False
    coordinator.manual_threshold = manual_threshold
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.is_cover_manual = MagicMock(return_value=False)
    coordinator.manager.handle_user_initiated_state_change = MagicMock(
        return_value=True
    )
    coordinator.cover_state_change = True
    coordinator._is_in_startup_grace_period = MagicMock(return_value=False)
    coordinator._manual_gate_closed_log = MagicMock()
    coordinator._target_just_reached = (
        target_just_reached if target_just_reached is not None else set()
    )
    coordinator._pending_cover_events = []
    coordinator._pipeline_result = None
    coordinator._policy = MagicMock()
    coordinator._policy.secondary_axis_check = MagicMock(return_value=None)
    return coordinator


# ===========================================================================
# PositionContextTracker
# ===========================================================================


def test_position_context_tracker_records_and_recognises_id():
    from custom_components.adaptive_cover_pro.managers.cover_command.position_context import (
        PositionContextTracker,
    )

    tracker = PositionContextTracker()
    tracker.record("ctx-1")

    assert tracker.was_acp_position_context("ctx-1") is True
    assert tracker.was_acp_position_context("ctx-other") is False
    assert tracker.acp_position_context_count() == 1


def test_position_context_tracker_caps_at_history_size():
    """Older context ids fall off when the deque is full."""
    from custom_components.adaptive_cover_pro.managers.cover_command.position_context import (
        PositionContextTracker,
    )

    tracker = PositionContextTracker()
    cap = PositionContextTracker._CONTEXT_HISTORY_SIZE
    for i in range(cap + 5):
        tracker.record(f"ctx-{i}")

    # First few should have aged out
    assert tracker.was_acp_position_context("ctx-0") is False
    assert tracker.was_acp_position_context("ctx-4") is False
    # Most recent should still be present
    assert tracker.was_acp_position_context(f"ctx-{cap + 4}") is True
    assert tracker.acp_position_context_count() == cap


# ===========================================================================
# helpers.get_open_close_state — state_obj propagation
# ===========================================================================


def test_get_open_close_state_uses_state_obj_when_supplied():
    """The event payload wins over the live registry value when state_obj is passed.

    Critical for the assumed-state cover reconciliation race: by the time the
    queued event is drained, hass.states may already reflect ACP's
    counter-command. The event's new_state must remain the source of truth.
    """
    from custom_components.adaptive_cover_pro.helpers import get_open_close_state

    hass = MagicMock()
    live_state = MagicMock()
    live_state.state = "closed"  # ACP's counter-command
    hass.states.get = MagicMock(return_value=live_state)

    event_state = MagicMock()
    event_state.state = "open"  # what triggered the event

    assert get_open_close_state(hass, "cover.x", state_obj=event_state) == 100
    # And the live-fall-back path still works without state_obj
    assert get_open_close_state(hass, "cover.x") == 0


def test_get_open_close_state_falls_back_when_state_obj_invalid():
    """unknown/unavailable state_obj returns None, doesn't fall back to live."""
    from custom_components.adaptive_cover_pro.helpers import get_open_close_state

    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    bad_state = MagicMock()
    bad_state.state = "unavailable"

    assert get_open_close_state(hass, "cover.x", state_obj=bad_state) is None


# ===========================================================================
# read_axis_value passes state_obj through for OPEN/CLOSE-only covers
# ===========================================================================


def test_read_axis_value_open_close_only_uses_event_state():
    """An awning policy with an OPEN/CLOSE-only cover reads the event payload."""
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    policy = get_policy("cover_awning")
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }

    hass = MagicMock()
    live = MagicMock()
    live.state = "closed"  # post-reconcile state in HA
    hass.states.get = MagicMock(return_value=live)

    event_state = MagicMock()
    event_state.state = "open"  # what the user triggered
    event_state.attributes = {}

    assert policy.read_axis_value(hass, "cover.x", caps, state_obj=event_state) == 100


# ===========================================================================
# AdaptiveCoverManager.handle_user_initiated_state_change
# ===========================================================================


def test_handle_user_initiated_state_change_marks_override():
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    manager.add_covers(["cover.patio_awning"])
    new_state = MagicMock()
    new_state.last_updated = "2026-05-10T19:00:00+00:00"

    handled = manager.handle_user_initiated_state_change(
        "cover.patio_awning",
        new_state,
        allow_reset=False,
        context_user_id="holly",
        context_id="ctx-holly-1",
    )

    assert handled is True
    assert manager.is_cover_manual("cover.patio_awning") is True
    # An event was recorded with the override reason
    events = manager.get_event_buffer()
    assert any(e.get("event") == "manual_override_set" for e in events)
    assert any("user-initiated" in (e.get("reason") or "") for e in events)


def test_handle_user_initiated_state_change_returns_false_for_untracked_cover():
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    new_state = MagicMock()
    new_state.last_updated = "2026-05-10T19:00:00+00:00"

    handled = manager.handle_user_initiated_state_change(
        "cover.not_tracked",
        new_state,
        allow_reset=False,
        context_user_id="holly",
        context_id="ctx-1",
    )

    assert handled is False
    assert manager.is_cover_manual("cover.not_tracked") is False


# ===========================================================================
# Coordinator fast-path
# ===========================================================================


@pytest.mark.asyncio
async def test_user_context_fast_path_marks_override_for_user_event():
    """Holly's dashboard click (user_id set, non-ACP context) → override fired.

    This is the regression test for the patio awning ping-pong: ACP commanded
    close (target=0), Holly clicked Open from the HA dashboard, the resulting
    state-change event must be flagged as manual override even when the
    numeric path's expected==new comparison would say zero delta.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.patio_awning"
    coordinator = _make_coordinator(entity_id=entity_id, target=0)
    # is_cover_manual: False before the fast-path, True after (simulating that
    # handle_user_initiated_state_change marked the cover).
    coordinator.manager.is_cover_manual = MagicMock(side_effect=[False, True])
    event_data = _make_state_change_data(
        entity_id,
        new_state_value="open",
        user_id="holly",
        context_id="ctx-holly-open-1",
    )
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    # Fast-path was taken
    coordinator.manager.handle_user_initiated_state_change.assert_called_once()
    call = coordinator.manager.handle_user_initiated_state_change.call_args
    assert call.args[0] == entity_id
    assert call.kwargs["context_user_id"] == "holly"
    assert call.kwargs["context_id"] == "ctx-holly-open-1"
    # Existing position-math path was NOT called
    coordinator.manager.handle_state_change.assert_not_called()
    # And the latched target was discarded so reconciliation stops fighting
    coordinator._cmd_svc.discard_target.assert_called_once_with(entity_id)


@pytest.mark.asyncio
async def test_user_context_fast_path_does_not_redundantly_discard_target():
    """If the cover is already in manual override, the fast-path doesn't redundantly discard."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.patio_awning"
    coordinator = _make_coordinator(entity_id=entity_id, target=0)
    # is_cover_manual: True before the fast-path (already manual), True after
    coordinator.manager.is_cover_manual = MagicMock(side_effect=[True, True])
    event_data = _make_state_change_data(
        entity_id,
        new_state_value="open",
        user_id="holly",
        context_id="ctx-holly-open-2",
    )
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_user_initiated_state_change.assert_called_once()
    # Already manual, so no redundant target discard
    coordinator._cmd_svc.discard_target.assert_not_called()


@pytest.mark.asyncio
async def test_user_context_fast_path_skipped_for_acp_context():
    """An event whose context.id is in the ACP position tracker falls through."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.patio_awning"
    coordinator = _make_coordinator(
        entity_id=entity_id,
        target=0,
        acp_position_contexts={"ctx-acp-1"},
    )
    event_data = _make_state_change_data(
        entity_id,
        new_state_value="closed",
        user_id="holly",  # even with user_id, ACP context should override
        context_id="ctx-acp-1",
    )
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    # Fast-path NOT taken
    coordinator.manager.handle_user_initiated_state_change.assert_not_called()
    # Position-math path DID run
    coordinator.manager.handle_state_change.assert_called_once()


@pytest.mark.asyncio
async def test_user_context_fast_path_skipped_when_user_id_none():
    """No user_id (system-originated change) → fall through to numeric path."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.patio_awning"
    coordinator = _make_coordinator(entity_id=entity_id, target=0)
    event_data = _make_state_change_data(
        entity_id,
        new_state_value="open",
        user_id=None,
        context_id="ctx-system-1",
    )
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_user_initiated_state_change.assert_not_called()
    coordinator.manager.handle_state_change.assert_called_once()


@pytest.mark.asyncio
async def test_user_context_fast_path_consumes_target_just_reached():
    """When the fast-path fires, any pending target_just_reached for the entity is cleared.

    Prevents a stale flag from masking a subsequent legitimate event.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.patio_awning"
    coordinator = _make_coordinator(
        entity_id=entity_id,
        target=0,
        target_just_reached={entity_id, "cover.other"},
    )
    event_data = _make_state_change_data(
        entity_id,
        new_state_value="open",
        user_id="holly",
        context_id="ctx-holly-1",
    )
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_user_initiated_state_change.assert_called_once()
    # Entity removed from the set, but other entries preserved
    assert entity_id not in coordinator._target_just_reached
    assert "cover.other" in coordinator._target_just_reached
