"""Transient opening/closing indicator for no-feedback covers.

Somfy-RTS-style open/close-only covers report no position and no
``opening``/``closing`` state, so the companion card can't show motion. During
ACP's ~45s transit-timeout window (``PerEntityState.waiting``), surface a
synthetic travel direction (``opening``/``closing``) so the card can render
"Opening…/Closing…". Confined to open/close-only covers via the caps gate;
position-reporting covers animate via % and must always report ``None``.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from tests.test_assumed_position_surface import _setup_open_close_cover

pytestmark = pytest.mark.integration

# Somfy-RTS-style capability mask: OPEN(1) | CLOSE(2) | STOP(8) — no SET_POSITION(4).
_OPEN_CLOSE_STOP = 1 | 2 | 8

_OPEN_CLOSE_CAPS = {
    "has_set_position": False,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}
_POSITION_CAPS = {
    "has_set_position": True,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}


# ------------------------------------------------------------------ #
# Unit-level fixtures (direct CoverCommandService construction)
# ------------------------------------------------------------------ #


@pytest.fixture
def svc():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return CoverCommandService(
        hass=h,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


# ------------------------------------------------------------------ #
# Step 1 — _set_transit_direction_if_blind
# ------------------------------------------------------------------ #


def test_set_transit_direction_closing(svc):
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=50, prior_position=100, caps=_OPEN_CLOSE_CAPS
    )
    assert svc.get_transit_direction("cover.x") == "closing"


def test_set_transit_direction_opening(svc):
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=50, prior_position=0, caps=_OPEN_CLOSE_CAPS
    )
    assert svc.get_transit_direction("cover.x") == "opening"


def test_set_transit_direction_equal_is_none(svc):
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=50, prior_position=50, caps=_OPEN_CLOSE_CAPS
    )
    assert svc.get_transit_direction("cover.x") is None


def test_set_transit_direction_position_capable_cleared(svc):
    # Seed a stale direction, then a position-capable cover must clear it.
    svc.state("cover.x").transit_direction = "opening"
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=50, prior_position=100, caps=_POSITION_CAPS
    )
    assert svc.get_transit_direction("cover.x") is None


def test_set_transit_direction_none_target_cleared(svc):
    svc.state("cover.x").transit_direction = "opening"
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=None, prior_position=100, caps=_OPEN_CLOSE_CAPS
    )
    assert svc.get_transit_direction("cover.x") is None


def test_set_transit_direction_none_prior_cleared(svc):
    svc.state("cover.x").transit_direction = "closing"
    svc._set_transit_direction_if_blind(
        "cover.x", routed_target=50, prior_position=None, caps=_OPEN_CLOSE_CAPS
    )
    assert svc.get_transit_direction("cover.x") is None


# ------------------------------------------------------------------ #
# Step 2 — transit_states() gated on waiting
# ------------------------------------------------------------------ #


def test_transit_states_only_when_waiting(svc):
    s = svc.state("cover.x")
    s.transit_direction = "opening"
    s.waiting = True
    assert svc.transit_states() == {"cover.x": "opening"}


def test_transit_states_empty_when_not_waiting(svc):
    s = svc.state("cover.x")
    s.transit_direction = "opening"
    s.waiting = False
    assert svc.transit_states() == {}


# ------------------------------------------------------------------ #
# Step 5 — transit clears when the waiting window times out
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_transit_cleared_on_wait_timeout(svc):
    now = dt.datetime.now(dt.UTC)
    s = svc.state("cover.x")
    s.target = 50
    s.waiting = True
    s.transit_direction = "closing"
    s.sent_at = now - dt.timedelta(seconds=200)  # well past the 45s timeout
    s.last_progress_at = None

    # Isolate the timeout-clear branch from the position-read machinery.
    svc._get_current_position = MagicMock(return_value=None)
    svc._is_cover_in_transit = MagicMock(return_value=False)

    await svc.run_reconciliation_pass(now)

    assert svc.is_waiting_for_target("cover.x") is False
    assert svc.get_transit_direction("cover.x") is None
    assert svc.transit_states() == {}


# ------------------------------------------------------------------ #
# Step 3 — async_apply_user_stop gives the My move a transit window
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_user_stop_sets_transit_closing(hass: HomeAssistant) -> None:
    """A card stop → My on an idle open cover (100) with My=50 shows 'closing'."""
    coordinator = await _setup_open_close_cover(hass, my_position=50)

    # Idle, reporting "open" (prior position 100). Not mid ACP-move.
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )

    coordinator._cmd_svc._dry_run = True
    await coordinator.async_apply_user_stop("cover.test_blind", trigger="stop")
    coordinator._cmd_svc._dry_run = False

    svc = coordinator._cmd_svc
    assert svc.get_transit_direction("cover.test_blind") == "closing"
    assert svc.is_waiting_for_target("cover.test_blind") is True
    assert svc.transit_states()["cover.test_blind"] == "closing"


# ------------------------------------------------------------------ #
# Step 4 — ACP open/close command sets the transit direction
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_prepare_service_call_open_sets_opening(hass: HomeAssistant) -> None:
    coordinator = await _setup_open_close_cover(hass, my_position=None)
    hass.states.async_set(
        "cover.test_blind",
        "closed",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )
    svc = coordinator._cmd_svc
    # Command fully open → open_cover; prior=0 (closed) → opening.
    svc._prepare_service_call("cover.test_blind", 100)
    assert svc.get_transit_direction("cover.test_blind") == "opening"


@pytest.mark.asyncio
async def test_prepare_service_call_close_sets_closing(hass: HomeAssistant) -> None:
    coordinator = await _setup_open_close_cover(hass, my_position=None)
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )
    svc = coordinator._cmd_svc
    # Command fully closed → close_cover; prior=100 (open) → closing.
    svc._prepare_service_call("cover.test_blind", 0)
    assert svc.get_transit_direction("cover.test_blind") == "closing"


# ------------------------------------------------------------------ #
# Step 6 — sensor Cover_Position surfaces transit_states while in transit
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_sensor_exposes_transit_states_in_transit(hass: HomeAssistant) -> None:
    from custom_components.adaptive_cover_pro.sensor import _cover_position_attrs

    coordinator = await _setup_open_close_cover(hass, my_position=50)
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )
    coordinator._cmd_svc._dry_run = True
    await coordinator.async_apply_user_stop("cover.test_blind", trigger="stop")
    coordinator._cmd_svc._dry_run = False
    await coordinator.async_refresh()

    sensor = MagicMock()
    sensor.coordinator = coordinator
    sensor.data = coordinator.data
    attrs = _cover_position_attrs(sensor)
    assert attrs["transit_states"]["cover.test_blind"] == "closing"


@pytest.mark.asyncio
async def test_sensor_omits_transit_states_when_empty(hass: HomeAssistant) -> None:
    from custom_components.adaptive_cover_pro.sensor import _cover_position_attrs

    coordinator = await _setup_open_close_cover(hass, my_position=50)
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": _OPEN_CLOSE_STOP, "assumed_state": True},
    )
    await coordinator.async_refresh()

    sensor = MagicMock()
    sensor.coordinator = coordinator
    sensor.data = coordinator.data
    attrs = _cover_position_attrs(sensor)
    assert "transit_states" not in attrs
