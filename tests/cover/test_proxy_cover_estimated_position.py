"""The proxy cover animates from the travel-time model while a move is in flight.

Travel plans on their own only reach the companion card. The proxy is what makes
*any* HA cover card show movement, because it is an ordinary cover entity — so
while its source has a live plan it publishes the modelled position instead of
the source's last (stale, or absent) reading.

That needs a republish tick, since nothing else would prompt a redraw between
the command and the arrival. The tick's lifetime is the load-bearing part: it
must exist exactly while something is moving, and never at idle.
"""

from __future__ import annotations

import datetime as dt

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_INVERSE_STATE,
    CONF_SENSOR_TYPE,
    CONF_TRAVEL_TIME_CALIBRATION,
    DOMAIN,
    TRAVEL_CALIBRATION_SOURCE_MEASURED,
    CoverType,
)
from custom_components.adaptive_cover_pro.managers.cover_command.state_store import (
    TravelPlan,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration

_SOURCE = "cover.living_room"
_T0 = dt.datetime(2026, 8, 2, 12, 0, 0, tzinfo=dt.UTC)

_CALIBRATION = {
    _SOURCE: {
        "full_travel_seconds": 40.0,
        "open_seconds": 40.0,
        "close_seconds": 40.0,
        "start_delay_seconds": 0.0,
        "calibrated_at": _T0.isoformat(),
        "source": TRAVEL_CALIBRATION_SOURCE_MEASURED,
    }
}


async def _setup(hass, *, entry_id: str, extra_options: dict | None = None):
    """Set up one proxied cover and return ``(coordinator, proxy_entity_id)``."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = [_SOURCE]
    opts[CONF_ENABLE_PROXY_COVER] = True
    opts[CONF_TRAVEL_TIME_CALIBRATION] = _CALIBRATION
    if extra_options:
        opts.update(extra_options)

    hass.states.async_set(
        _SOURCE, "open", {"current_position": 0, "supported_features": 143}
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Ramp Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Ramp Cover",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )
    return entry.runtime_data, proxy_eid


def _start_ramp(
    coordinator, *, from_position: int, to_position: int, elapsed: float = 20.0
) -> None:
    """Put a live travel plan on the source, ``elapsed`` seconds in."""
    svc = coordinator._cmd_svc
    state = svc.state(_SOURCE)
    state.waiting = True
    state.travel_plan = TravelPlan(
        from_position=from_position,
        to_position=to_position,
        started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=elapsed),
        start_delay_seconds=0.0,
        duration_seconds=40.0,
    )


async def test_proxy_reports_the_modelled_position_mid_move(hass) -> None:
    """Half way through a 40 s sweep to 100, the proxy shows roughly half open.

    The source is still publishing 0 — that is the whole problem the ramp
    solves.
    """
    coordinator, proxy_eid = await _setup(hass, entry_id="ramp_mid")
    _start_ramp(coordinator, from_position=0, to_position=100)

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes["current_position"] == pytest.approx(
        50, abs=2
    )


async def test_proxy_falls_back_to_the_source_reading_when_idle(hass) -> None:
    coordinator, proxy_eid = await _setup(hass, entry_id="ramp_idle")

    hass.states.async_set(
        _SOURCE, "open", {"current_position": 37, "supported_features": 143}
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes["current_position"] == 37


async def test_estimate_is_un_inverted_like_any_other_reading(hass) -> None:
    """The plan's endpoints are in the source's frame, so the estimate is too.

    Deliberately sampled a quarter of the way through rather than half: at the
    midpoint the flip is the identity, and a test that cannot fail if the
    un-inversion is dropped is not testing it.
    """
    coordinator, proxy_eid = await _setup(
        hass, entry_id="ramp_inverse", extra_options={CONF_INVERSE_STATE: True}
    )
    _start_ramp(coordinator, from_position=0, to_position=100, elapsed=10.0)

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    # Wire 25 → logical 75 on an inverse install.
    assert hass.states.get(proxy_eid).attributes["current_position"] == pytest.approx(
        75, abs=2
    )


async def test_estimate_is_published_raw_on_an_upright_install(hass) -> None:
    """The same quarter-way sample, un-flipped — the control for the test above."""
    coordinator, proxy_eid = await _setup(hass, entry_id="ramp_upright")
    _start_ramp(coordinator, from_position=0, to_position=100, elapsed=10.0)

    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes["current_position"] == pytest.approx(
        25, abs=2
    )


async def test_tick_runs_only_while_a_plan_is_live(hass) -> None:
    """No timer at idle, a timer during a move, and no timer after it lands.

    A tick left running is a state write per second per instance, forever.
    """
    coordinator, _ = await _setup(hass, entry_id="ramp_tick")
    assert coordinator._travel_tick_unsub is None

    _start_ramp(coordinator, from_position=0, to_position=100)
    coordinator._sync_travel_tick()
    assert coordinator._travel_tick_unsub is not None

    coordinator._cmd_svc._clear_waiting(coordinator._cmd_svc.state(_SOURCE))
    coordinator._sync_travel_tick()
    assert coordinator._travel_tick_unsub is None


async def test_tick_stops_itself_once_the_last_plan_clears(hass) -> None:
    """The tick is self-limiting — nothing else has to remember to cancel it."""
    coordinator, _ = await _setup(hass, entry_id="ramp_tick_self")
    _start_ramp(coordinator, from_position=0, to_position=100)
    coordinator._sync_travel_tick()

    coordinator._cmd_svc._clear_waiting(coordinator._cmd_svc.state(_SOURCE))
    coordinator._travel_tick(dt.datetime.now(dt.UTC))

    assert coordinator._travel_tick_unsub is None


async def test_tick_is_cancelled_on_unload(hass) -> None:
    coordinator, _ = await _setup(hass, entry_id="ramp_tick_unload")
    _start_ramp(coordinator, from_position=0, to_position=100)
    coordinator._sync_travel_tick()
    assert coordinator._travel_tick_unsub is not None

    await coordinator.async_shutdown()

    assert coordinator._travel_tick_unsub is None
