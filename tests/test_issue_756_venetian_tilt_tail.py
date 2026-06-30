"""Issue #756 — venetian deferred-tilt tail.

When the override resolves with the carriage already at target (same_position)
but the slat tilt differs, ``maybe_update_tilt_only`` early-returns while the
back-rotate suppression window from the prior solar sequence is still open.
Previously the tilt was simply dropped until the next unrelated state change.

The fix RECORDS the deferred tilt and schedules a single refresh at suppression
expiry so the tilt fires promptly. ``has_pending_secondary_axis`` reports True
while the tilt is pending; the pending state clears once the tilt actually
sends.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    VENETIAN_TILT_SUPPRESSION_SECONDS,
)
from custom_components.adaptive_cover_pro.cover_types import (
    BlindPolicy,
    VenetianPolicy,
)

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


@pytest.fixture
def policy(hass, schedule_refresh_after):
    p = VenetianPolicy()
    p.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda eid: 0,
        set_commanded_position=MagicMock(),
        position_tolerance=5,
        is_dry_run=lambda: True,
        schedule_refresh_after=schedule_refresh_after,
    )
    return p


def test_base_policy_has_no_pending_secondary_axis():
    """Single-axis covers never carry a deferred secondary axis (Liskov default)."""
    assert BlindPolicy().has_pending_secondary_axis(_ENTITY) is False


def test_unattached_venetian_has_no_pending_secondary_axis():
    """Before attach() wires a sequencer, the venetian reports no pending axis."""
    assert VenetianPolicy().has_pending_secondary_axis(_ENTITY) is False


@pytest.mark.asyncio
async def test_tilt_deferred_and_refresh_scheduled_during_suppression(
    policy, schedule_refresh_after
):
    """A tilt-only update that lands inside the suppression window is recorded as
    pending and a refresh is scheduled at suppression expiry.
    """
    policy._last_tilt = 0
    # Open the back-rotate suppression window (fresh position command stamp).
    policy._sequencer.stamp_position_command(_ENTITY)
    assert policy._sequencer.is_in_suppression(_ENTITY)

    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=False),
        reason="custom_position",
    )

    # The tilt is queued, not sent.
    assert policy.has_pending_secondary_axis(_ENTITY) is True
    schedule_refresh_after.assert_called_once()
    secs = schedule_refresh_after.call_args.args[0]
    assert 0 < secs <= VENETIAN_TILT_SUPPRESSION_SECONDS


@pytest.mark.asyncio
async def test_pending_tilt_fires_and_clears_after_suppression_expires(
    policy, schedule_refresh_after
):
    """Once the suppression window has elapsed, the next tilt-only attempt sends
    the tilt and clears the pending state.
    """
    policy._last_tilt = 0
    policy._sequencer.stamp_position_command(_ENTITY)

    # First attempt defers (still suppressed).
    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=False),
        reason="custom_position",
    )
    assert policy.has_pending_secondary_axis(_ENTITY) is True

    # Backdate the suppression stamp so the window has closed.
    policy._sequencer._suppression_at[_ENTITY] = dt.datetime.now(dt.UTC) - dt.timedelta(
        seconds=VENETIAN_TILT_SUPPRESSION_SECONDS + 5
    )
    assert not policy._sequencer.is_in_suppression(_ENTITY)

    await policy.maybe_update_tilt_only(
        _ENTITY,
        current_position=0,
        context=SimpleNamespace(force=False),
        reason="custom_position",
    )

    # Tilt sent → pending cleared.
    assert policy.has_pending_secondary_axis(_ENTITY) is False


def test_suppression_remaining_seconds_none_without_stamp(policy):
    assert policy._sequencer.suppression_remaining_seconds(_ENTITY) is None
