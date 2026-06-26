"""Proxy cover command routing tests (Phase D Step 16)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    TILT_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = pytest.mark.integration


async def _setup_proxy(
    hass,
    *,
    source: str = "cover.living_room",
    cover_type: str = CoverType.BLIND,
    entry_id: str = "proxy_cmd",
    options: dict | None = None,
    state: str = "open",
    attrs: dict | None = None,
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    base = (
        dict(options)
        if options is not None
        else (
            dict(TILT_OPTIONS)
            if cover_type == CoverType.TILT
            else dict(VERTICAL_OPTIONS)
        )
    )
    base[CONF_ENTITIES] = [source]
    base[CONF_ENABLE_PROXY_COVER] = True

    hass.states.async_set(
        source,
        state,
        attrs or {"current_position": 50, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Proxy Cmd", CONF_SENSOR_TYPE: cover_type},
        options=base,
        entry_id=entry_id,
        title="Proxy Cmd",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )
    return entry, coordinator, proxy_eid


async def test_set_cover_position_routes_through_async_apply_user_position(
    hass,
) -> None:
    """A ``cover.set_cover_position`` service call delegates to the helper."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_set")
    coord.async_apply_user_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 42},
        blocking=True,
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 42, trigger="proxy_managed"
    )


async def test_set_cover_position_uses_proxy_managed_trigger(hass) -> None:
    """The trigger label for a managed cover command is ``proxy_managed``."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_trig")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 10},
        blocking=True,
    )
    args, kwargs = coord.async_apply_user_position.await_args
    assert kwargs.get("trigger") == "proxy_managed"


async def test_open_cover_calls_apply_user_position_with_100(hass) -> None:
    """``cover.open_cover`` sends 100 with trigger ``proxy_open``."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_open")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": proxy_eid}, blocking=True
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 100, trigger="proxy_open"
    )


async def test_close_cover_calls_apply_user_position_with_0(hass) -> None:
    """``cover.close_cover`` sends 0 with trigger ``proxy_close`` (clamp applies)."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_close")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": proxy_eid}, blocking=True
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 0, trigger="proxy_close"
    )


async def test_proxy_tilt_routes_to_apply_user_tilt(hass) -> None:
    """Venetian proxy: ``set_cover_tilt_position`` delegates to ``async_apply_user_tilt``.

    Issue #684: the proxy tilt setter must drive the *tilt* axis, not the
    position parameter of ``async_apply_user_position``. The requested tilt
    flows into the dedicated tilt entry point with the ``proxy_tilt`` trigger,
    and the position helper is left untouched.
    """
    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        source="cover.venetian",
        entry_id="proxy_cmd_tilt",
        options=dict(VERTICAL_OPTIONS),
        attrs={
            "current_position": 50,
            "current_tilt_position": 80,
            "supported_features": 143 | 128,
        },
    )
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", ""))
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 10},
        blocking=True,
    )
    coord.async_apply_user_tilt.assert_awaited_once_with(
        "cover.venetian", 10, trigger="proxy_tilt"
    )
    coord.async_apply_user_position.assert_not_awaited()


async def test_proxy_tilt_sends_real_tilt_service_leaves_carriage_unchanged(
    hass,
) -> None:
    """End-to-end: a proxy tilt request lands ``set_cover_tilt_position`` on the source.

    Core of issue #684 — the entry point is NOT mocked. A venetian dual-axis
    source at position 50 / tilt 80 receiving a tilt request of 10 must emit
    exactly one ``cover.set_cover_tilt_position`` (tilt_position == 10) on the
    source and ZERO ``cover.set_cover_position`` calls (carriage unchanged).
    """
    from homeassistant.const import EVENT_CALL_SERVICE

    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        source="cover.venetian",
        entry_id="proxy_cmd_tilt_real",
        options=dict(VERTICAL_OPTIONS),
        attrs={
            "current_position": 50,
            "current_tilt_position": 80,
            "supported_features": 143 | 128,
        },
    )

    tilt_calls: list[dict] = []
    position_calls: list[dict] = []

    def _on_event(event):
        if event.data.get("domain") != "cover":
            return
        data = dict(event.data.get("service_data") or {})
        if data.get("entity_id") != "cover.venetian":
            return
        if event.data.get("service") == "set_cover_tilt_position":
            tilt_calls.append(data)
        elif event.data.get("service") == "set_cover_position":
            position_calls.append(data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _on_event)

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 10},
        blocking=True,
    )
    await hass.async_block_till_done()

    # At least one real tilt service call lands on the source, and every tilt
    # call requests the user value (10) — a static mock source never publishes
    # the new tilt, so the verify path may fire a bounded drift retry of the
    # SAME value (issue #500), which is correct production behavior.
    assert tilt_calls, f"no tilt service call reached the source: {tilt_calls}"
    assert all(c.get("tilt_position") == 10 for c in tilt_calls), tilt_calls
    # Core of #684: the carriage must NOT be commanded.
    assert position_calls == [], f"carriage moved unexpectedly: {position_calls}"


async def test_stop_cover_forwards_directly_no_clamp(hass) -> None:
    """``cover.stop_cover`` forwards to the source service, not through the helper."""
    from homeassistant.const import EVENT_CALL_SERVICE

    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_stop")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    # Track every cover.stop_cover service call via the event bus.
    calls: list[dict] = []

    def _on_event(event):
        if (
            event.data.get("domain") == "cover"
            and event.data.get("service") == "stop_cover"
        ):
            calls.append(dict(event.data.get("service_data") or {}))

    hass.bus.async_listen(EVENT_CALL_SERVICE, _on_event)

    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": proxy_eid}, blocking=True
    )
    await hass.async_block_till_done()
    # The helper must NOT have been called
    coord.async_apply_user_position.assert_not_called()
    # A forwarded stop_cover targets the source entity
    targeted = [c for c in calls if c.get("entity_id") == "cover.living_room"]
    assert targeted, f"no forwarded stop_cover; captured={calls}"


async def test_commands_dropped_when_source_unavailable(hass) -> None:
    """Source unavailable → helper never called, no exception raised."""
    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        entry_id="proxy_cmd_unavail",
        state="unavailable",
        attrs={},
    )
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    # set_position
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 50},
        blocking=True,
    )
    coord.async_apply_user_position.assert_not_called()
