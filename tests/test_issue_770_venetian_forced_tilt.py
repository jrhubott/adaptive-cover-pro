"""Issue #770 — venetian forced-transition tilt bypass.

On a handler transition that releases a custom position back to solar, the
coordinator forces the **position** command (``force=True``, reason
``custom_position_released``) so it bypasses the time/position delta gates. The
**tilt** axis is driven separately by ``VenetianPolicy.maybe_update_tilt_only``,
invoked from the ``same_position`` skip branch. Before this fix that method
ignored the ``context`` it was handed and, while the prior sequence's 90 s
back-rotate suppression window was still open, **deferred** the tilt (issue #756)
instead of sending the new handler's full target.

The fix: when the call is a forced handler transition (``context.force``) AND the
carriage is not physically mid-travel, bypass the suppression *deferral* and send
the tilt immediately via ``update_tilt_only(..., force=True)``. The #756 deferral
stays the default for non-forced (routine tracking) cycles, and a forced
transition still defers while the carriage is actively moving.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import VenetianPolicy

pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")

_ENTITY = "cover.bedroom"


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def schedule_refresh_after():
    return MagicMock()


def _make_policy(hass, schedule_refresh_after, *, get_state=None):
    p = VenetianPolicy()
    attach_kwargs = {
        "hass": hass,
        "logger": MagicMock(),
        "grace_mgr": MagicMock(),
        "get_current_position": lambda eid: 0,
        "set_commanded_position": MagicMock(),
        "position_tolerance": 5,
        "is_dry_run": lambda: True,
        "schedule_refresh_after": schedule_refresh_after,
    }
    if get_state is not None:
        attach_kwargs["get_state"] = get_state
    p.attach(**attach_kwargs)
    return p


@pytest.fixture
def policy(hass, schedule_refresh_after):
    return _make_policy(hass, schedule_refresh_after)


@pytest.mark.asyncio
async def test_forced_transition_bypasses_suppression_deferral(
    policy, schedule_refresh_after
):
    """A forced handler transition sends the full new tilt immediately even
    inside the back-rotate suppression window, instead of deferring it (#756).
    """
    policy._last_tilt = 100
    # Open the back-rotate suppression window (fresh position command stamp).
    policy._sequencer.stamp_position_command(_ENTITY)
    assert policy._sequencer.is_in_suppression(_ENTITY)

    send_spy = AsyncMock()
    record_spy = MagicMock()
    policy._sequencer.update_tilt_only = send_spy
    policy._sequencer.record_pending_tilt = record_spy

    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=True),
        reason="custom_position_released",
    )

    send_spy.assert_awaited_once()
    assert send_spy.await_args.kwargs["force"] is True
    assert send_spy.await_args.kwargs["tilt_target"] == 100
    record_spy.assert_not_called()
    schedule_refresh_after.assert_not_called()


@pytest.mark.asyncio
async def test_non_forced_cycle_still_defers(policy, schedule_refresh_after):
    """A routine (non-forced) tracking cycle inside suppression still defers the
    tilt (issue #756 regression guard): pending is set and a refresh scheduled.
    """
    policy._last_tilt = 100
    policy._sequencer.stamp_position_command(_ENTITY)
    assert policy._sequencer.is_in_suppression(_ENTITY)

    send_spy = AsyncMock()
    policy._sequencer.update_tilt_only = send_spy

    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=False),
        reason="solar",
    )

    send_spy.assert_not_awaited()
    assert policy.has_pending_secondary_axis(_ENTITY) is True
    schedule_refresh_after.assert_called_once()


@pytest.mark.asyncio
async def test_forced_transition_defers_while_carriage_moving(
    hass, schedule_refresh_after
):
    """A forced transition still defers while the carriage is physically
    mid-travel — tier (a) of the suppression cap must hold even under force.
    """
    policy = _make_policy(hass, schedule_refresh_after, get_state=lambda eid: "closing")
    policy._last_tilt = 100
    policy._sequencer.stamp_position_command(_ENTITY)
    assert policy._sequencer.is_carriage_moving(_ENTITY) is True

    send_spy = AsyncMock()
    policy._sequencer.update_tilt_only = send_spy

    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=True),
        reason="custom_position_released",
    )

    send_spy.assert_not_awaited()
    assert policy.has_pending_secondary_axis(_ENTITY) is True
    schedule_refresh_after.assert_called_once()
