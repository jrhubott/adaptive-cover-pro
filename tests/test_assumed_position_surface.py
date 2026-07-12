"""End-to-end: assumed My position surfaces on the display for RTS covers (#888).

An open/close-only cover (Somfy RTS: OPEN|CLOSE|STOP, no SET_POSITION) reports
no numeric position, so the reported-position surfaces render ``—``. After ACP
drives it to its hardware "My" preset, both display surfaces — the cover-position
sensor's ``actual_positions`` (from ``snapshot.cover_positions``) and the
diagnostics ``current_position`` — must fall back to ``my_position_value``.

Also covers invalidation (Step 7): the assumed value is dropped on override
reset, on a real numeric state change, and on a native-position command.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.const import (
    CONF_MY_POSITION_VALUE,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration

# Somfy-RTS-style capability mask: OPEN(1) | CLOSE(2) | STOP(8) — no SET_POSITION(4).
_OPEN_CLOSE_STOP = 1 | 2 | 8


async def _setup_open_close_cover(
    hass: HomeAssistant, *, my_position: int | None = 50, entry_id: str = "assumed_01"
):
    """Set up one open/close-only (assumed-state) blind with an unknown position."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    hass.states.async_set(
        "cover.test_blind",
        "unknown",
        {"supported_features": _OPEN_CLOSE_STOP},
    )

    opts = dict(VERTICAL_OPTIONS)
    if my_position is not None:
        opts[CONF_MY_POSITION_VALUE] = my_position

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Assumed", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Assumed",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


# ---------------------------------------------------------------------------
# Step 5 — both display surfaces fall back to the assumed My value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actual_positions_shows_my_after_acp_move(hass: HomeAssistant) -> None:
    """After an ACP My move, diagnostics + the snapshot report my_position_value."""
    coordinator = await _setup_open_close_cover(hass, my_position=50)

    # ACP drives the cover to My. dry_run keeps the real cover.stop_cover service
    # (unregistered in this bare hass) from firing while still recording state.
    coordinator._cmd_svc._dry_run = True
    await coordinator._cmd_svc.send_my_position("cover.test_blind", 50)
    coordinator._cmd_svc._dry_run = False

    # Diagnostics surface (build_diagnostic_data → read_positions).
    diag = coordinator.build_diagnostic_data()
    assert diag["covers"]["cover.test_blind"]["current_position"] == 50

    # Sensor surface: snapshot.cover_positions, rebuilt on the next update cycle.
    await coordinator.async_refresh()
    assert coordinator._snapshot.cover_positions["cover.test_blind"] == 50


@pytest.mark.asyncio
async def test_actual_positions_shows_my_after_user_stop(
    hass: HomeAssistant,
) -> None:
    """Issue #888 follow-up: the card's stop service surfaces My on the display.

    An assumed-state open/close-only cover reporting "open" — after the ACP
    `stop` service (``async_apply_user_stop``) lands it on its hardware My
    preset — must show ``my_position_value`` on both display surfaces, exactly
    like the external stop→My path.
    """
    coordinator = await _setup_open_close_cover(hass, my_position=50)

    # The cover reports "open" with assumed_state=True (last-command direction).
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )

    # The card stop button routes here. dry_run keeps the (unregistered)
    # cover.stop_cover service from firing while still recording state.
    coordinator._cmd_svc._dry_run = True
    await coordinator.async_apply_user_stop("cover.test_blind", trigger="stop")
    coordinator._cmd_svc._dry_run = False

    # Diagnostics surface (build_diagnostic_data → read_positions).
    diag = coordinator.build_diagnostic_data()
    assert diag["covers"]["cover.test_blind"]["current_position"] == 50

    # Sensor surface: snapshot.cover_positions, rebuilt on the next update cycle.
    await coordinator.async_refresh()
    assert coordinator._snapshot.cover_positions["cover.test_blind"] == 50


@pytest.mark.asyncio
async def test_actual_positions_shows_my_after_user_stop_while_waiting(
    hass: HomeAssistant,
) -> None:
    """Issue #888 follow-up: a user stop mid ACP-move must still surface My.

    Live Deck scenario: ACP was solar-tracking and had just sent ``close_cover``,
    leaving ``waiting=True`` with the stale endpoint (0) stashed as the assumed
    value. Pressing the card's stop button engages the override unconditionally,
    but the My assumed record used to be gated behind ``not was_waiting`` — so
    every reported surface kept showing the stale 0 instead of My. A Somfy-RTS
    stop physically lands on My even mid-move, so the display must show My.
    """
    coordinator = await _setup_open_close_cover(hass, my_position=50)

    # The cover reports "closed" with assumed_state=True (last close command).
    hass.states.async_set(
        "cover.test_blind",
        "closed",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )

    # Simulate ACP mid its-own close move: waiting + the stale close endpoint (0)
    # already recorded as the assumed value, exactly as a routine close_cover left it.
    coordinator._cmd_svc.set_waiting("cover.test_blind", True)
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 0)

    # The card stop button routes here while ACP is still mid-move.
    coordinator._cmd_svc._dry_run = True
    await coordinator.async_apply_user_stop("cover.test_blind", trigger="stop")
    coordinator._cmd_svc._dry_run = False

    # The assumed store must now hold My, not the stale 0.
    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") == 50

    # Diagnostics surface (build_diagnostic_data → read_positions).
    diag = coordinator.build_diagnostic_data()
    assert diag["covers"]["cover.test_blind"]["current_position"] == 50

    # Sensor surface: snapshot.cover_positions, rebuilt on the next update cycle.
    await coordinator.async_refresh()
    assert coordinator._snapshot.cover_positions["cover.test_blind"] == 50


# ---------------------------------------------------------------------------
# Step 7 — invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assumed_cleared_on_override_reset(hass: HomeAssistant) -> None:
    """Resetting the manual override drops the assumed display position."""
    coordinator = await _setup_open_close_cover(hass, my_position=50)
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    coordinator.manager.reset("cover.test_blind")

    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") is None


@pytest.mark.asyncio
async def test_assumed_cleared_on_real_state_change(hass: HomeAssistant) -> None:
    """A real numeric position read invalidates the stale assumed value.

    Uses a read that matches ``our_state`` so no manual override engages — this
    isolates the explicit invalidation, rather than the incidental
    ``discard_target`` that a detected override would trigger.
    """
    coordinator = await _setup_open_close_cover(hass, my_position=50)
    coordinator.manager.add_covers(["cover.test_blind"])
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    # A state-change event carrying a genuine open read (100), matching the
    # expected position so the position-delta detector marks no override.
    new_state = MagicMock()
    new_state.state = "open"
    new_state.attributes = {}
    new_state.context = None
    new_state.last_updated = None
    event = MagicMock()
    event.entity_id = "cover.test_blind"
    event.new_state = new_state
    event.old_state = None

    coordinator.manager.handle_state_change(
        event,
        100,
        coordinator._policy,
        False,
        lambda _e: False,
        5,
    )

    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") is None


@pytest.mark.asyncio
async def test_assumed_survives_same_state_re_report(hass: HomeAssistant) -> None:
    """A same-value re-report ("open"→"open") must NOT wipe the assumed value.

    Issue #888 follow-up: an assumed-state Somfy-RTS cover reports HA state
    "open" (the last-command direction). Any incidental state re-report of the
    same value must leave the display-only assumed My value intact — otherwise
    the card snaps back off My.
    """
    coordinator = await _setup_open_close_cover(hass, my_position=50)
    coordinator.manager.add_covers(["cover.test_blind"])
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    old_state = MagicMock()
    old_state.state = "open"
    old_state.attributes = {"assumed_state": True}
    new_state = MagicMock()
    new_state.state = "open"
    new_state.attributes = {"assumed_state": True}
    new_state.context = None
    new_state.last_updated = None
    event = MagicMock()
    event.entity_id = "cover.test_blind"
    event.new_state = new_state
    event.old_state = old_state

    # our_state matches the raw open read (100) so no manual override engages.
    coordinator.manager.handle_state_change(
        event,
        100,
        coordinator._policy,
        False,
        lambda _e: False,
        5,
    )

    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") == 50


@pytest.mark.asyncio
async def test_assumed_cleared_on_open_close_transition(hass: HomeAssistant) -> None:
    """A genuine endpoint transition ("open"→"closed") still clears the assumed value."""
    coordinator = await _setup_open_close_cover(hass, my_position=50)
    coordinator.manager.add_covers(["cover.test_blind"])
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    old_state = MagicMock()
    old_state.state = "open"
    old_state.attributes = {"assumed_state": True}
    new_state = MagicMock()
    new_state.state = "closed"
    new_state.attributes = {"assumed_state": True}
    new_state.context = None
    new_state.last_updated = None
    event = MagicMock()
    event.entity_id = "cover.test_blind"
    event.new_state = new_state
    event.old_state = old_state

    coordinator.manager.handle_state_change(
        event,
        0,
        coordinator._policy,
        False,
        lambda _e: False,
        5,
    )

    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") is None


@pytest.mark.asyncio
async def test_actual_positions_shows_my_for_assumed_state_cover_reporting_open(
    hass: HomeAssistant,
) -> None:
    """The live Deck scenario: an assumed-state cover reporting "open" shows My.

    The most common assumed-state cover (Somfy RTS) reports HA state "open"
    (assumed_state=True) after a stop→My, which get_open_close_state would map
    to 100. With a recorded assumed My value, both display surfaces must show
    50, not the open-derived 100.
    """
    coordinator = await _setup_open_close_cover(hass, my_position=50)

    # The cover reports "open" with assumed_state=True (last-command direction).
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    # Diagnostics surface (build_diagnostic_data → read_positions).
    diag = coordinator.build_diagnostic_data()
    assert diag["covers"]["cover.test_blind"]["current_position"] == 50

    # Sensor surface: snapshot.cover_positions, rebuilt on the next update cycle.
    await coordinator.async_refresh()
    assert coordinator._snapshot.cover_positions["cover.test_blind"] == 50


@pytest.mark.asyncio
async def test_assumed_cleared_on_native_position_command(
    hass: HomeAssistant,
) -> None:
    """A native set_position command clears any stale assumed value."""
    coordinator = await _setup_open_close_cover(hass, my_position=50)
    coordinator._cmd_svc.record_assumed_position("cover.test_blind", 50)

    # The cover now reports a native position axis (SET_POSITION added).
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"current_position": 40, "supported_features": _OPEN_CLOSE_STOP | 4},
    )
    coordinator._cmd_svc._dry_run = True
    ctx = coordinator._build_position_context(
        "cover.test_blind", dict(VERTICAL_OPTIONS), force=True
    )
    await coordinator._cmd_svc.apply_position("cover.test_blind", 30, "solar", ctx)

    assert coordinator._cmd_svc.get_assumed_position("cover.test_blind") is None
