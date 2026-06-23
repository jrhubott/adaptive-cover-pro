"""Targeted coverage tests for proxy cover branches not hit by feature tests."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

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


async def _setup_no_sources(hass) -> None:
    """Proxy enabled, but ``CONF_ENTITIES`` is empty — no proxies are created."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = []
    opts[CONF_ENABLE_PROXY_COVER] = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Empty Sources", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id="proxy_cov_empty",
        title="Empty Sources",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_returns_when_sources_empty(hass) -> None:
    """``async_setup_entry`` with enabled flag + empty entities returns cleanly."""
    from homeassistant.helpers import entity_registry as er

    await _setup_no_sources(hass)
    reg = er.async_get(hass)
    proxies = [
        e
        for e in reg.entities.values()
        if e.unique_id.startswith("proxy_cov_empty_proxy_")
    ]
    assert proxies == []


# ---------------------------------------------------------------------------
# Source-unavailable command guards (exercises every command's early-return)
# ---------------------------------------------------------------------------


async def _setup_unavail_proxy(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.dead_blind"]
    opts[CONF_ENABLE_PROXY_COVER] = True

    hass.states.async_set(
        "cover.dead_blind",
        "open",
        {"current_position": 50, "supported_features": 143 | 128},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Dead", CONF_SENSOR_TYPE: CoverType.TILT},
        options=opts,
        entry_id="proxy_cov_unavail",
        title="Dead",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = entry.runtime_data
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))
    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )

    # Flip the source to unavailable AFTER setup so the proxy is alive but blind.
    hass.states.async_set("cover.dead_blind", "unavailable", {})
    await hass.async_block_till_done()
    return coord, proxy_eid


async def test_open_close_tilt_stop_dropped_when_source_unavailable(
    hass, caplog
) -> None:
    """Every command early-returns when the source is unavailable.

    Call the proxy's command methods directly so HA's "skip unavailable entity"
    framework guard doesn't short-circuit the early-return branch we're trying
    to cover.
    """
    coord, proxy_eid = await _setup_unavail_proxy(hass)
    caplog.set_level(logging.DEBUG, logger="custom_components.adaptive_cover_pro.cover")

    proxy = next(
        e
        for e in hass.data["entity_components"]["cover"].entities
        if e.entity_id == proxy_eid
    )
    await proxy.async_set_cover_position(position=50)
    await proxy.async_open_cover()
    await proxy.async_close_cover()
    await proxy.async_set_cover_tilt_position(tilt_position=40)
    await proxy.async_stop_cover()
    coord.async_apply_user_position.assert_not_called()
    # Debug log must mention "unavailable — dropping"
    assert any(
        "unavailable" in r.getMessage() for r in caplog.records
    ), "expected debug log mentioning unavailable"


# ---------------------------------------------------------------------------
# Tilt-incapable source: set_cover_tilt_position no-ops
# ---------------------------------------------------------------------------


async def test_tilt_command_dropped_when_source_lacks_tilt_capability(hass) -> None:
    """Source supports position but not tilt → set_cover_tilt_position no-ops."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.no_tilt"]
    opts[CONF_ENABLE_PROXY_COVER] = True
    # supported_features = 15 → no SET_TILT_POSITION (128) bit
    hass.states.async_set(
        "cover.no_tilt",
        "open",
        {"current_position": 50, "supported_features": 15},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "No Tilt", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id="proxy_cov_no_tilt",
        title="No Tilt",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = entry.runtime_data
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))
    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )

    # Use the proxy's own method directly — HA's framework would reject the
    # service call up-front because supported_features doesn't include tilt,
    # but the in-class guard still needs coverage for defence-in-depth.
    proxy = next(
        e
        for e in hass.data["entity_components"]["cover"].entities
        if e.entity_id == proxy_eid
    )
    await proxy.async_set_cover_tilt_position(tilt_position=80)
    coord.async_apply_user_position.assert_not_called()


# ---------------------------------------------------------------------------
# Property null-paths: state missing entirely from HA
# ---------------------------------------------------------------------------


async def test_properties_when_source_state_object_missing(hass) -> None:
    """When ``hass.states.get`` returns None, properties degrade safely."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.transient"]
    opts[CONF_ENABLE_PROXY_COVER] = True
    hass.states.async_set(
        "cover.transient",
        "open",
        {"current_position": 50, "supported_features": 143},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Transient", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id="proxy_cov_missing",
        title="Transient",
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
    proxy = next(
        e
        for e in hass.data["entity_components"]["cover"].entities
        if e.entity_id == proxy_eid
    )
    # Remove the source state object entirely
    hass.states.async_remove("cover.transient")
    await hass.async_block_till_done()

    assert proxy.available is False
    assert proxy.current_cover_position is None
    assert proxy.current_cover_tilt_position is None
    assert int(proxy.supported_features) == 0
    assert proxy.is_closed is None


# ---------------------------------------------------------------------------
# Source friendly-label resolution branches
# ---------------------------------------------------------------------------


def test_source_friendly_label_state_friendly_name(hass) -> None:
    """When no registry entry exists but state has friendly_name, use it."""
    from custom_components.adaptive_cover_pro.cover import _source_friendly_label

    hass.states.async_set(
        "cover.unregistered_blind",
        "open",
        {"friendly_name": "Pretty Name"},
    )
    assert _source_friendly_label(hass, "cover.unregistered_blind") == "Pretty Name"


def test_source_friendly_label_falls_back_to_object_id(hass) -> None:
    """No registry, no friendly_name → titlecased object_id."""
    from custom_components.adaptive_cover_pro.cover import _source_friendly_label

    # No state, no entry
    label = _source_friendly_label(hass, "cover.no_such_thing")
    assert label == "No Such Thing"


def test_source_friendly_label_uses_registry_name(hass) -> None:
    """Registry entry with name takes priority over state friendly_name."""
    from custom_components.adaptive_cover_pro.cover import _source_friendly_label
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    reg.async_get_or_create(
        "cover", "test_platform", "uid_xyz", suggested_object_id="reg_blind"
    )
    reg.async_update_entity("cover.reg_blind", name="Registry Name")
    hass.states.async_set(
        "cover.reg_blind", "open", {"friendly_name": "Ignored State Name"}
    )
    assert _source_friendly_label(hass, "cover.reg_blind") == "Registry Name"
