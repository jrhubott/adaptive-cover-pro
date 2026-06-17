"""Proxy cover dynamic reload tests (Phase E Step 19)."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


async def _setup(
    hass,
    *,
    proxy_enabled: bool,
    entry_id: str,
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.living_room"]
    opts[CONF_ENABLE_PROXY_COVER] = proxy_enabled

    hass.states.async_set(
        "cover.living_room",
        "open",
        {"current_position": 50, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Reload Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Reload Cover",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _proxy_states(hass, entry_id: str) -> list[str]:
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    return [
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry_id
        and e.unique_id.startswith(f"{entry_id}_proxy_")
        and hass.states.get(e.entity_id) is not None
    ]


async def test_enabling_proxy_via_options_creates_entities_on_reload(hass) -> None:
    """Flipping the option on then reloading creates the proxy entity."""
    entry = await _setup(hass, proxy_enabled=False, entry_id="proxy_reload_on")
    assert _proxy_states(hass, entry.entry_id) == []

    # Flip the option on; the update-listener triggers reload.
    new_options = dict(entry.options)
    new_options[CONF_ENABLE_PROXY_COVER] = True
    with _patch_coordinator_refresh():
        hass.config_entries.async_update_entry(entry, options=new_options)
        await hass.async_block_till_done()

    proxies = _proxy_states(hass, entry.entry_id)
    assert len(proxies) == 1


async def test_disabling_proxy_via_options_removes_entities_on_reload(hass) -> None:
    """Flipping the option off then reloading retires the proxy state.

    Per HA convention, the entity-registry entry is intentionally left
    behind. The platform reload causes the live state to flip to
    ``unavailable`` (the platform no longer publishes it).
    """
    entry = await _setup(hass, proxy_enabled=True, entry_id="proxy_reload_off")
    proxies = _proxy_states(hass, entry.entry_id)
    assert proxies, "proxy missing before disable"
    proxy_eid = proxies[0]
    # Live state is published before disable
    assert hass.states.get(proxy_eid).state != "unavailable"

    new_options = dict(entry.options)
    new_options[CONF_ENABLE_PROXY_COVER] = False
    with _patch_coordinator_refresh():
        hass.config_entries.async_update_entry(entry, options=new_options)
        await hass.async_block_till_done()

    # After reload with proxy off, the platform no longer publishes the entity.
    state = hass.states.get(proxy_eid)
    assert (
        state is None or state.state == "unavailable"
    ), f"expected proxy retired (None/unavailable); got {state}"
