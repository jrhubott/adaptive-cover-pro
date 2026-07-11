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
