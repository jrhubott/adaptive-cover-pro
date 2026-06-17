"""Proxy cover entity setup tests (Phase D Step 12)."""

from __future__ import annotations


import pytest
from homeassistant.util import slugify

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


async def _setup_with_proxy(
    hass,
    *,
    enabled: bool,
    sources: list[str] | None = None,
    entry_id: str = "proxy_setup_01",
    name: str = "Adaptive Cover",
):
    """Set up an ACP entry with proxy toggle in a known state."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    opts = dict(VERTICAL_OPTIONS)
    if sources is not None:
        opts[CONF_ENTITIES] = sources
    opts[CONF_ENABLE_PROXY_COVER] = enabled

    # Seed the source cover state(s)
    for src in opts[CONF_ENTITIES]:
        hass.states.async_set(
            src, "open", {"current_position": 100, "supported_features": 143}
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _proxy_states(hass, entry_id: str):
    """Find all proxy cover state objects belonging to ``entry_id``."""
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    proxy_eids = {
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry_id
        and e.unique_id.startswith(f"{entry_id}_proxy_")
    }
    return [hass.states.get(eid) for eid in proxy_eids if hass.states.get(eid)]


async def test_no_proxy_entities_when_flag_disabled(hass) -> None:
    """No proxy entity is created while CONF_ENABLE_PROXY_COVER is False."""
    entry = await _setup_with_proxy(hass, enabled=False, entry_id="proxy_off")
    assert _proxy_states(hass, entry.entry_id) == []


async def test_one_proxy_entity_per_physical_cover_when_enabled(hass) -> None:
    """One proxy is created per source in CONF_ENTITIES."""
    sources = ["cover.living_room", "cover.bedroom"]
    entry = await _setup_with_proxy(
        hass, enabled=True, sources=sources, entry_id="proxy_two"
    )
    proxies = _proxy_states(hass, entry.entry_id)
    assert len(proxies) == 2


async def test_proxy_unique_id_format(hass) -> None:
    """Unique ID follows ``{entry_id}_proxy_{slugify(source)}``."""
    from homeassistant.helpers import entity_registry as er

    sources = ["cover.living_room"]
    entry = await _setup_with_proxy(
        hass, enabled=True, sources=sources, entry_id="proxy_uid_01"
    )
    reg = er.async_get(hass)
    expected = f"{entry.entry_id}_proxy_{slugify('cover.living_room')}"
    matches = [e for e in reg.entities.values() if e.unique_id == expected]
    assert matches, f"unique_id {expected!r} not found in registry"
    assert matches[0].entity_id.startswith("cover.")


async def test_proxy_name_single_cover(hass) -> None:
    """Single-cover proxy name is ``f'{title} Managed'``."""
    sources = ["cover.living_room"]
    entry = await _setup_with_proxy(
        hass,
        enabled=True,
        sources=sources,
        entry_id="proxy_name_single",
        name="Living Blinds",
    )
    proxies = _proxy_states(hass, entry.entry_id)
    assert len(proxies) == 1
    # Friendly name is on the state object
    assert proxies[0].attributes.get("friendly_name") == "Living Blinds Managed"


async def test_proxy_name_multiple_covers_includes_friendly_name(hass) -> None:
    """Multi-cover proxy name includes the source friendly name."""
    sources = ["cover.living_room", "cover.bedroom"]
    # Source friendly names are inferred from entity_id when no friendly_name attr is set
    entry = await _setup_with_proxy(
        hass,
        enabled=True,
        sources=sources,
        entry_id="proxy_name_multi",
        name="Whole House",
    )
    proxies = _proxy_states(hass, entry.entry_id)
    assert len(proxies) == 2
    names = {p.attributes.get("friendly_name") for p in proxies}
    # Each name starts with the base title and includes the source label
    assert all(n.startswith("Whole House Managed (") for n in names), names
    # Both source segments are present
    joined = " | ".join(names)
    assert "living_room" in joined.replace(" ", "_") or "Living" in joined
    assert "bedroom" in joined.replace(" ", "_") or "Bedroom" in joined


async def test_proxy_device_info_matches_acp_entry_device(hass) -> None:
    """DeviceInfo identifier ``(DOMAIN, entry.entry_id)`` matches the entry's virtual device."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    sources = ["cover.living_room"]
    entry = await _setup_with_proxy(
        hass, enabled=True, sources=sources, entry_id="proxy_dev_01"
    )
    e_reg = er.async_get(hass)
    d_reg = dr.async_get(hass)
    # Look up an ACP sensor created by the same entry to find the virtual device
    own_entities = [
        ent for ent in e_reg.entities.values() if ent.config_entry_id == entry.entry_id
    ]
    assert own_entities, "no entities registered for entry"
    devices = {ent.device_id for ent in own_entities if ent.device_id}
    assert (
        len(devices) == 1
    ), f"expected a single virtual device shared by all ACP entities; got {devices}"
    device = d_reg.async_get(next(iter(devices)))
    assert (DOMAIN, entry.entry_id) in device.identifiers
