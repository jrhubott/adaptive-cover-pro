"""Regression tests for issue #266: sunset position not sent when end_time < sunset+offset.

When the user's end_time fires before the astronomical sunset window opens,
_on_window_closed sends the daytime default (is_sunset_active=False at that moment).
Later, when is_sunset_active flips True, _check_sunset_window_transition must detect
the transition and dispatch the sunset position.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_SUNSET_POS
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coord(
    *,
    track_end_time: bool = True,
    automatic_control: bool = True,
    sunset_pos: int | None = 0,
    inverse_state: bool = False,
    n_entities: int = 1,
) -> AdaptiveDataUpdateCoordinator:
    """Minimal coordinator fixture for _check_sunset_window_transition tests."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = automatic_control
    coord._track_end_time = track_end_time
    coord._inverse_state = inverse_state
    coord._prev_sunset_active: bool | None = None

    entities = [MagicMock() for _ in range(n_entities)]
    coord.entities = entities

    options = {}
    if sunset_pos is not None:
        options[CONF_SUNSET_POS] = sunset_pos
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._cmd_svc = cmd_svc

    coord.async_refresh = AsyncMock()

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    coord.manager = manager

    def _fake_build_ctx(entity, options, *, force=False, is_safety=False, **_):
        return PositionContext(
            auto_control=automatic_control,
            manual_override=False,
            sun_just_appeared=False,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
        )

    coord._build_position_context = _fake_build_ctx

    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
    coord._event_buffer = EventBuffer(maxlen=50)

    return coord


def _seed_sunset_state(coord, *, prev: bool | None, current_is_sunset: bool, pos: int = 0):
    """Seed _prev_sunset_active and mock _compute_current_effective_default."""
    coord._prev_sunset_active = prev
    coord._compute_current_effective_default = MagicMock(return_value=(pos, current_is_sunset))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sunset_window_opens_after_end_time_dispatches_sunset_pos():
    """Core regression: transition False→True dispatches sunset_pos to all covers."""
    coord = _make_coord(sunset_pos=0, n_entities=2)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True, pos=0)

    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 2
    for call in coord._cmd_svc.apply_position.call_args_list:
        trigger = call.args[2] if len(call.args) > 2 else call.kwargs.get("trigger", "")
        assert trigger == "sunset_window_opened", f"Expected trigger 'sunset_window_opened', got {trigger!r}"
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_sunset_already_active_in_previous_cycle():
    """No dispatch when is_sunset_active was already True (no transition)."""
    coord = _make_coord(sunset_pos=0)
    _seed_sunset_state(coord, prev=True, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_return_sunset_disabled():
    """No dispatch when return_sunset (track_end_time) is False."""
    coord = _make_coord(track_end_time=False, sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_automatic_control_off():
    """No dispatch when automatic_control is False (non-bypass gate)."""
    coord = _make_coord(automatic_control=False, sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_sunset_pos_not_configured():
    """No dispatch when sunset_pos is not configured (None)."""
    coord = _make_coord(sunset_pos=None)
    # _compute_current_effective_default won't be called because we return early
    coord._prev_sunset_active = False
    # No sunset_pos in options → return early before checking transition

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_covers_with_active_manual_override():
    """Cover with active manual override is skipped; others proceed."""
    coord = _make_coord(sunset_pos=0, n_entities=2)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    # First entity has manual override active
    def _manual_for_first(entity):
        return entity is coord.entities[0]

    coord.manager.is_cover_manual.side_effect = _manual_for_first

    await coord._check_sunset_window_transition()

    # Only second entity gets a command
    assert coord._cmd_svc.apply_position.call_count == 1
    called_entity = coord._cmd_svc.apply_position.call_args.args[0]
    assert called_entity is coord.entities[1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_transition_detected_only_once_per_day():
    """apply_position called exactly once even when invoked multiple times with is_sunset=True."""
    coord = _make_coord(sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()
    # Second call: _prev_sunset_active is now True, is_sunset still True → no transition
    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_prev_sunset_active_initial_none_does_not_dispatch():
    """On HA restart mid-sunset (_prev_sunset_active=None), no spurious dispatch.

    Mirrors the _last_sun_validity_state=None pattern: first call initializes
    state without dispatching, so covers aren't blindly repositioned on startup.
    """
    coord = _make_coord(sunset_pos=0)
    # _prev_sunset_active starts as None (fresh coordinator)
    assert coord._prev_sunset_active is None
    coord._compute_current_effective_default = MagicMock(return_value=(0, True))

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()
    # State should be initialized
    assert coord._prev_sunset_active is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_inverse_state_position_is_inverted():
    """With inverse_state=True, sunset_pos=0 is sent as 100."""
    coord = _make_coord(sunset_pos=0, inverse_state=True)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True, pos=0)

    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 1
    sent_pos = coord._cmd_svc.apply_position.call_args.args[1]
    assert sent_pos == 100


@pytest.mark.asyncio
@pytest.mark.unit
async def test_end_time_after_sunset_does_not_double_dispatch():
    """When end_time fires after sunset is already active, no follow-up dispatch.

    If end_time > sunset+offset, _on_window_closed already sent sunset_pos.
    _prev_sunset_active should be True when _check_sunset_window_transition runs,
    so the transition guard prevents a second send.
    """
    coord = _make_coord(sunset_pos=0)
    # Simulate: sunset window was already active when the last update ran
    _seed_sunset_state(coord, prev=True, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()
