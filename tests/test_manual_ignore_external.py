"""manual_ignore_external option — suppresses non-ACP manual-override detection.

When CONF_MANUAL_IGNORE_EXTERNAL is on, the coordinator must:
- Skip the user-context fast path in async_handle_cover_state_change
- Skip the numeric-diff path in async_handle_cover_state_change
- Skip user `cover.stop_cover` detection in async_check_cover_service_call

The pre-emptive engagement path inside async_apply_user_position
(manager.mark_user_command) is unaffected — proxy and set_position service
commands continue to engage manual override.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_state_change_data(
    entity_id: str,
    *,
    new_state_value: str = "open",
    user_id: str | None = None,
    context_id: str = "ctx-test-123",
    old_state_value: str = "closed",
):
    data = MagicMock()
    data.entity_id = entity_id
    data.old_state = MagicMock()
    data.old_state.state = old_state_value
    data.new_state = MagicMock()
    data.new_state.state = new_state_value
    data.new_state.attributes = {}
    ctx = MagicMock()
    ctx.id = context_id
    ctx.user_id = user_id
    data.new_state.context = ctx
    data.new_state.last_updated = "2026-05-10T19:00:00+00:00"
    return data


def _make_coordinator(
    *,
    manual_ignore_external: bool,
    entity_id: str = "cover.test",
    target: int | None = 0,
    target_just_reached: set | None = None,
    acp_position_contexts: set[str] | None = None,
):
    coordinator = MagicMock()
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.manual_ignore_external = manual_ignore_external
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
    coordinator.manual_threshold = None
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
    coordinator._grace_mgr = MagicMock()
    coordinator._grace_mgr.is_in_command_grace_period = MagicMock(return_value=False)
    return coordinator


# ---------------------------------------------------------------------------
# async_handle_cover_state_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ignore_external_skips_user_context_fast_path():
    """Option ON: external user-context state change → no override engagement."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(manual_ignore_external=True, entity_id=entity_id)
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id="holly", context_id="ctx-h-1"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_user_initiated_state_change.assert_not_called()
    coordinator.manager.handle_state_change.assert_not_called()
    coordinator._cmd_svc.discard_target.assert_not_called()


@pytest.mark.asyncio
async def test_ignore_external_skips_numeric_diff_path():
    """Option ON: external automation move (no user_id) → no override engagement."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(
        manual_ignore_external=True, entity_id=entity_id, target=50
    )
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id=None, context_id="ctx-auto-1"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_state_change.assert_not_called()
    coordinator.manager.handle_user_initiated_state_change.assert_not_called()


@pytest.mark.asyncio
async def test_ignore_external_clears_target_just_reached_housekeeping():
    """Option ON: still drain _target_just_reached so future events aren't masked."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(
        manual_ignore_external=True,
        entity_id=entity_id,
        target_just_reached={entity_id, "cover.other"},
    )
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id="holly", context_id="ctx-h-2"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    assert entity_id not in coordinator._target_just_reached
    assert "cover.other" in coordinator._target_just_reached  # other entities untouched


@pytest.mark.asyncio
async def test_default_off_still_engages_override_for_user_context():
    """Option OFF (default): existing user-context fast path still fires."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(manual_ignore_external=False, entity_id=entity_id)
    coordinator.manager.is_cover_manual = MagicMock(side_effect=[False, True])
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id="holly", context_id="ctx-h-3"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_user_initiated_state_change.assert_called_once()


@pytest.mark.asyncio
async def test_default_off_still_engages_override_for_numeric_diff():
    """Option OFF (default): numeric-diff path still fires for non-user events."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(
        manual_ignore_external=False, entity_id=entity_id, target=50
    )
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id=None, context_id="ctx-auto-2"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    coordinator.manager.handle_state_change.assert_called_once()


# ---------------------------------------------------------------------------
# async_check_cover_service_call (stop_cover detection)
# ---------------------------------------------------------------------------


def _make_stop_event(entity_id: str, *, context_id: str = "ctx-user-stop"):
    event = MagicMock()
    event.data = {
        "domain": "cover",
        "service": "stop_cover",
        "service_data": {"entity_id": entity_id},
    }
    event.context = MagicMock()
    event.context.id = context_id
    return event


def _make_stop_coordinator(*, manual_ignore_external: bool, entity_id: str):
    coordinator = MagicMock()
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.manual_ignore_external = manual_ignore_external
    coordinator.entities = [entity_id]
    coordinator.logger = MagicMock()
    coordinator._manual_gate_closed_log = MagicMock()
    cmd_svc = MagicMock()
    cmd_svc.was_acp_stop_context = MagicMock(return_value=False)
    cmd_svc.is_waiting_for_target = MagicMock(return_value=False)
    cmd_svc.set_target = MagicMock()
    cmd_svc.discard_target = MagicMock()
    coordinator._cmd_svc = cmd_svc
    coordinator.manager = MagicMock()
    coordinator.manager.is_cover_manual = MagicMock(return_value=False)
    coordinator.manager.handle_stop_service_call = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {"my_position_value": 50}
    return coordinator


@pytest.mark.asyncio
async def test_ignore_external_skips_stop_cover_detection():
    """Option ON: external cover.stop_cover → no override engagement."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_stop_coordinator(
        manual_ignore_external=True, entity_id=entity_id
    )
    event = _make_stop_event(entity_id)

    await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
        coordinator, event
    )

    coordinator.manager.handle_stop_service_call.assert_not_called()
    coordinator._cmd_svc.set_target.assert_not_called()


@pytest.mark.asyncio
async def test_default_off_still_detects_stop_cover():
    """Option OFF (default): external cover.stop_cover still engages override."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_stop_coordinator(
        manual_ignore_external=False, entity_id=entity_id
    )
    event = _make_stop_event(entity_id)

    await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
        coordinator, event
    )

    coordinator.manager.handle_stop_service_call.assert_called_once()


# ---------------------------------------------------------------------------
# manual_toggle precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_toggle_off_wins_over_ignore_external():
    """manual_toggle=False short-circuits before manual_ignore_external is consulted."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(manual_ignore_external=True, entity_id=entity_id)
    coordinator.manual_toggle = False
    coordinator._pending_cover_events = [
        _make_state_change_data(
            entity_id, new_state_value="open", user_id="holly", context_id="ctx-h-4"
        )
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 0)

    # Both manual_toggle gate AND ignore_external would suppress; the gate fires first
    coordinator._manual_gate_closed_log.assert_called_once()
    coordinator.manager.handle_user_initiated_state_change.assert_not_called()
    coordinator.manager.handle_state_change.assert_not_called()
