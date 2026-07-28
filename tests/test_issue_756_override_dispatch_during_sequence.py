"""Issue #756 — override dispatch during a long in-flight venetian sequence.

A higher-priority ``custom_position`` override wins the pipeline immediately,
but its command was never dispatched at the moment it won because dispatch was
keyed on the transient ``state_change`` edge. A long-blocking venetian
settle/tilt sequence holding the coordinator's single in-flight update cycle
clobbered that edge, stranding the override for ~3 min until an unrelated
tracked-entity change re-triggered the dispatch path.

The fix makes dispatch robust to a lost ``state_change`` edge by dispatching on
a *resolved-target change* between cycles: the coordinator compares the resolved
``(control_method, state, tilt, is_safety, bypass, skip, floor_clamp)``
signature against the last-dispatched one and routes through the existing
``async_handle_state_change`` path when it differs — even when ``state_change``
is False.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# _dispatch_for_cycle — the single dispatch authority
# ---------------------------------------------------------------------------


def _make_dispatch_coordinator(
    *,
    state_change: bool,
    last_sig: tuple | None,
    current_sig: tuple | None,
    auto_expired: bool = False,
    has_pending: bool = False,
    entities: list[str] | None = None,
):
    """Build a minimal mock coordinator for _dispatch_for_cycle tests."""
    coordinator = MagicMock()
    coordinator.entities = entities if entities is not None else ["cover.bedroom"]
    coordinator.state_change = state_change
    coordinator._last_dispatched_target_sig = last_sig
    coordinator._resolved_target_signature = MagicMock(return_value=current_sig)
    coordinator.async_handle_state_change = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock()
    coordinator._policy = MagicMock()
    coordinator._policy.has_pending_secondary_axis = MagicMock(return_value=has_pending)
    return coordinator


@pytest.mark.asyncio
async def test_override_dispatches_when_target_changed_despite_lost_state_change_edge():
    """The headline #756 race: state_change edge lost, but the resolved target
    changed since the last dispatch, so the override must still be sent.
    """
    prior_sig = ("solar", 50, None, False, False, False, False)
    winner_sig = ("custom_position", 0, 0, False, True, False, False)
    coordinator = _make_dispatch_coordinator(
        state_change=False,
        last_sig=prior_sig,
        current_sig=winner_sig,
    )

    await AdaptiveDataUpdateCoordinator._dispatch_for_cycle(
        coordinator,
        0,
        {},
        auto_expired=False,
        custom_position_released_entities=set(),
        safety_release=False,
        template_release=False,
    )

    coordinator.async_handle_state_change.assert_awaited_once()
    _, kwargs = coordinator.async_handle_state_change.call_args
    assert kwargs.get("target_changed") is True
    coordinator._async_send_after_override_clear.assert_not_awaited()
    # Nothing pending → the new target signature is recorded as last-dispatched.
    assert coordinator._last_dispatched_target_sig == winner_sig


@pytest.mark.asyncio
async def test_no_dispatch_when_target_unchanged_and_no_state_change():
    """Steady state: same resolved target, no state_change → no dispatch."""
    sig = ("custom_position", 0, 0, False, True, False, False)
    coordinator = _make_dispatch_coordinator(
        state_change=False,
        last_sig=sig,
        current_sig=sig,
    )

    await AdaptiveDataUpdateCoordinator._dispatch_for_cycle(
        coordinator,
        0,
        {},
        auto_expired=False,
        custom_position_released_entities=set(),
        safety_release=False,
        template_release=False,
    )

    coordinator.async_handle_state_change.assert_not_awaited()
    coordinator._async_send_after_override_clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_change_branch_still_dispatches_and_records_sig():
    """A normal tracked-entity change routes through the state_change branch and
    records the resolved target as last-dispatched.
    """
    sig = ("solar", 50, None, False, False, False, False)
    coordinator = _make_dispatch_coordinator(
        state_change=True,
        last_sig=None,
        current_sig=sig,
    )

    await AdaptiveDataUpdateCoordinator._dispatch_for_cycle(
        coordinator,
        50,
        {},
        auto_expired=False,
        custom_position_released_entities=set(),
        safety_release=False,
        template_release=False,
    )

    coordinator.async_handle_state_change.assert_awaited_once()
    # target_changed is False here (no prior sig to compare against).
    _, kwargs = coordinator.async_handle_state_change.call_args
    assert kwargs.get("target_changed") is False
    assert coordinator._last_dispatched_target_sig == sig


@pytest.mark.asyncio
async def test_auto_expired_branch_unchanged():
    """When a manual override just expired (and no state_change), the existing
    after-override-clear path runs — not async_handle_state_change.
    """
    sig = ("solar", 50, None, False, False, False, False)
    coordinator = _make_dispatch_coordinator(
        state_change=False,
        last_sig=sig,
        current_sig=sig,  # unchanged → target_changed False
        auto_expired=True,
    )

    await AdaptiveDataUpdateCoordinator._dispatch_for_cycle(
        coordinator,
        50,
        {},
        auto_expired=True,
        custom_position_released_entities=set(),
        safety_release=False,
        template_release=False,
    )

    coordinator._async_send_after_override_clear.assert_awaited_once()
    coordinator.async_handle_state_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_secondary_axis_defers_signature_recording():
    """While a venetian tilt is still pending, the last-dispatched signature is
    NOT recorded, so the next cycle re-evaluates target_changed and gets another
    chance to flush the deferred tilt.
    """
    prior_sig = ("solar", 50, None, False, False, False, False)
    winner_sig = ("custom_position", 0, 0, False, True, False, False)
    coordinator = _make_dispatch_coordinator(
        state_change=False,
        last_sig=prior_sig,
        current_sig=winner_sig,
        has_pending=True,
    )

    await AdaptiveDataUpdateCoordinator._dispatch_for_cycle(
        coordinator,
        0,
        {},
        auto_expired=False,
        custom_position_released_entities=set(),
        safety_release=False,
        template_release=False,
    )

    coordinator.async_handle_state_change.assert_awaited_once()
    # Pending tilt → signature stays at the prior value so target_changed fires
    # again next cycle.
    assert coordinator._last_dispatched_target_sig == prior_sig


# ---------------------------------------------------------------------------
# _resolved_target_signature
# ---------------------------------------------------------------------------


def test_resolved_target_signature_none_without_pipeline_result():
    coordinator = MagicMock()
    coordinator._pipeline_result = None
    assert AdaptiveDataUpdateCoordinator._resolved_target_signature(coordinator) is None


def test_resolved_target_signature_tuple_content():
    coordinator = MagicMock()
    coordinator.state = 0
    coordinator._pipeline_result = PipelineResult(
        position=0,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom",
        tilt=0,
        is_safety=False,
        bypass_auto_control=True,
        skip_command=False,
        floor_clamp_applied=False,
    )

    sig = AdaptiveDataUpdateCoordinator._resolved_target_signature(coordinator)

    assert sig == (
        ControlMethod.CUSTOM_POSITION.value,
        0,  # self.state
        0,  # tilt
        False,  # is_safety
        True,  # bypass_auto_control
        False,  # skip_command
        False,  # floor_clamp_applied
    )


# ---------------------------------------------------------------------------
# async_handle_state_change — target_changed force + reason
# ---------------------------------------------------------------------------


def _make_state_change_coordinator(*, pipeline_result: PipelineResult):
    coordinator = MagicMock()
    coordinator.entities = ["cover.bedroom"]
    coordinator.logger = MagicMock()
    coordinator.state_change = False
    coordinator._pipeline_result = pipeline_result
    coordinator._pipeline_bypasses_auto_control = pipeline_result.bypass_auto_control
    coordinator._pipeline_is_safety_handler = pipeline_result.is_safety
    coordinator.clock_window_open = True
    coordinator._last_state_change_entity = None
    coordinator._custom_position_template_trigger = False
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._dispatch_to_cover = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_target_changed_forces_dispatch_and_sets_reason():
    """target_changed=True (with no other force driver) must bypass the
    delta/time gates (force=True) and label the trace 'target_changed'.
    """
    result = PipelineResult(
        position=0,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom",
        tilt=0,
        bypass_auto_control=True,
    )
    coordinator = _make_state_change_coordinator(pipeline_result=result)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, 0, {}, target_changed=True
    )

    coordinator._build_position_context.assert_called_once()
    _, kwargs = coordinator._build_position_context.call_args
    assert kwargs["force"] is True
    coordinator._dispatch_to_cover.assert_awaited_once()
    dispatch_args = coordinator._dispatch_to_cover.call_args.args
    assert dispatch_args[2] == "target_changed"


# ---------------------------------------------------------------------------
# _schedule_refresh_after — deferred-tilt wake (issue #756)
# ---------------------------------------------------------------------------


def _coord_for_wake():
    coord = MagicMock()
    coord.hass = MagicMock()
    coord._refresh_after_unsub = None
    return coord


def test_schedule_refresh_after_schedules_single_wake():
    coord = _coord_for_wake()
    cancel = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=cancel,
    ) as m:
        AdaptiveDataUpdateCoordinator._schedule_refresh_after(coord, 42.0)
    m.assert_called_once()
    assert m.call_args.args[0] is coord.hass
    assert m.call_args.args[1] == 42.0
    assert coord._refresh_after_unsub is cancel


def test_schedule_refresh_after_clamps_nonpositive_to_zero():
    coord = _coord_for_wake()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ) as m:
        AdaptiveDataUpdateCoordinator._schedule_refresh_after(coord, 0)
    assert m.call_args.args[1] == 0


def test_schedule_refresh_after_cancels_previous():
    coord = _coord_for_wake()
    previous = MagicMock()
    coord._refresh_after_unsub = previous
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        AdaptiveDataUpdateCoordinator._schedule_refresh_after(coord, 10.0)
    previous.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_after_due_callback_requests_refresh():
    coord = _coord_for_wake()
    coord._refresh_after_unsub = MagicMock()
    coord.async_request_refresh = AsyncMock()
    await AdaptiveDataUpdateCoordinator._on_refresh_after_due(coord, None)
    assert coord._refresh_after_unsub is None
    coord.async_request_refresh.assert_awaited_once()
