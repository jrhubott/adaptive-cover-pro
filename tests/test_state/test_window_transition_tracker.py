"""Direct tests for :class:`WindowTransitionTracker`.

The pre-existing tests in test_coordinator_coverage, test_issue_266_sunset_transition,
test_event_buffer_recording, and test_auto_control_gate_matrix continue to exercise
the tracker through the coordinator's delegating methods.  These tests drive the
tracker's public surface directly without involving a coordinator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
    WindowTransitionTracker,
)


def _make_tracker(effective_default=(0, False)) -> WindowTransitionTracker:
    return WindowTransitionTracker(
        hass=MagicMock(),
        logger=MagicMock(),
        event_buffer=EventBuffer(maxlen=10),
        effective_default_fn=lambda _opts: effective_default,
    )


def _event_types(buf: EventBuffer) -> list[str]:
    return [e["event"] for e in buf.snapshot()]


# ---------------------------------------------------------------------------
# sun_just_appeared — state machine + event recording
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sun_just_appeared_returns_false_when_cover_data_is_none():
    tracker = _make_tracker()
    assert tracker.sun_just_appeared(None) is False


@pytest.mark.unit
def test_sun_just_appeared_first_call_seeds_state_and_returns_false():
    tracker = _make_tracker()
    cover_data = MagicMock()
    cover_data.direct_sun_valid = True
    assert tracker.sun_just_appeared(cover_data) is False
    assert tracker._last_sun_validity_state is True
    # No transition event recorded on seeding.
    assert _event_types(tracker._event_buffer) == []


@pytest.mark.unit
def test_sun_just_appeared_returns_true_on_false_to_true_transition():
    tracker = _make_tracker()
    tracker._last_sun_validity_state = False
    cover_data = MagicMock()
    cover_data.direct_sun_valid = True
    assert tracker.sun_just_appeared(cover_data) is True
    assert "sun_entered_fov" in _event_types(tracker._event_buffer)


@pytest.mark.unit
def test_sun_just_appeared_returns_false_on_true_to_false_transition():
    tracker = _make_tracker()
    tracker._last_sun_validity_state = True
    cover_data = MagicMock()
    cover_data.direct_sun_valid = False
    assert tracker.sun_just_appeared(cover_data) is False
    assert "sun_left_fov" in _event_types(tracker._event_buffer)


@pytest.mark.unit
def test_sun_just_appeared_no_event_when_state_unchanged():
    tracker = _make_tracker()
    tracker._last_sun_validity_state = True
    cover_data = MagicMock()
    cover_data.direct_sun_valid = True
    assert tracker.sun_just_appeared(cover_data) is False
    assert _event_types(tracker._event_buffer) == []


# ---------------------------------------------------------------------------
# check_sunset_window — gates + dispatch
# ---------------------------------------------------------------------------


def _common_kwargs(
    *, automatic_control=True, sunset_pos_cfg=25, inverse=False, entities=None
):
    """Build the per-call kwargs the tracker needs."""
    apply_position = AsyncMock(return_value=("sent", ""))
    refresh = AsyncMock()
    build_ctx = MagicMock(return_value=MagicMock(name="position_context"))
    return (
        {
            "track_end_time": True,
            "automatic_control": automatic_control,
            "sunset_pos_cfg": sunset_pos_cfg,
            "options": {},
            "inverse_state_enabled": inverse,
            "entities": entities if entities is not None else ["cover.a"],
            "is_cover_manual": lambda _e: False,
            "build_position_context": build_ctx,
            "apply_position": apply_position,
            "refresh": refresh,
        },
        apply_position,
        refresh,
    )


@pytest.mark.asyncio
async def test_check_sunset_window_skips_when_track_end_time_off():
    tracker = _make_tracker(effective_default=(0, True))
    kwargs, apply_position, refresh = _common_kwargs()
    kwargs["track_end_time"] = False
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_check_sunset_window_skips_when_automatic_control_off():
    tracker = _make_tracker(effective_default=(0, True))
    kwargs, apply_position, _ = _common_kwargs(automatic_control=False)
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_check_sunset_window_skips_when_sunset_pos_not_configured():
    tracker = _make_tracker(effective_default=(0, True))
    kwargs, apply_position, _ = _common_kwargs(sunset_pos_cfg=None)
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_check_sunset_window_first_call_seeds_state_without_dispatch():
    """Mirrors the _last_sun_validity_state=None pattern (no spurious restart dispatch)."""
    tracker = _make_tracker(effective_default=(0, True))
    kwargs, apply_position, _ = _common_kwargs()
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_not_called()
    assert tracker._prev_sunset_active is True


@pytest.mark.asyncio
async def test_check_sunset_window_dispatches_on_false_to_true_transition():
    tracker = _make_tracker(effective_default=(0, True))
    tracker._prev_sunset_active = False
    kwargs, apply_position, refresh = _common_kwargs(
        sunset_pos_cfg=25, entities=["cover.a", "cover.b"]
    )
    await tracker.check_sunset_window(**kwargs)
    assert apply_position.call_count == 2
    # Position is the raw sunset_pos when inverse_state is off.
    for call in apply_position.call_args_list:
        assert call.args[1] == 25
    refresh.assert_awaited()
    assert tracker._prev_sunset_active is True


@pytest.mark.asyncio
async def test_check_sunset_window_inverse_state_inverts_position():
    tracker = _make_tracker(effective_default=(0, True))
    tracker._prev_sunset_active = False
    kwargs, apply_position, _ = _common_kwargs(sunset_pos_cfg=0, inverse=True)
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_called_once()
    assert apply_position.call_args.args[1] == 100


@pytest.mark.asyncio
async def test_check_sunset_window_skips_manual_covers():
    tracker = _make_tracker(effective_default=(0, True))
    tracker._prev_sunset_active = False
    kwargs, apply_position, _ = _common_kwargs(entities=["cover.manual", "cover.auto"])
    kwargs["is_cover_manual"] = lambda eid: eid == "cover.manual"
    await tracker.check_sunset_window(**kwargs)
    assert apply_position.call_count == 1
    assert apply_position.call_args.args[0] == "cover.auto"


@pytest.mark.asyncio
async def test_check_sunset_window_no_double_dispatch_when_already_true():
    tracker = _make_tracker(effective_default=(0, True))
    tracker._prev_sunset_active = True  # already in the sunset window
    kwargs, apply_position, _ = _common_kwargs()
    await tracker.check_sunset_window(**kwargs)
    apply_position.assert_not_called()


@pytest.mark.asyncio
async def test_check_sunset_window_records_sunset_window_opened_event():
    tracker = _make_tracker(effective_default=(0, True))
    tracker._prev_sunset_active = False
    kwargs, _, _ = _common_kwargs(sunset_pos_cfg=25, entities=["a", "b", "c"])
    await tracker.check_sunset_window(**kwargs)
    events = tracker._event_buffer.snapshot()
    sunset = next(e for e in events if e["event"] == "sunset_window_opened")
    assert sunset["position"] == 25
    assert sunset["cover_count"] == 3
