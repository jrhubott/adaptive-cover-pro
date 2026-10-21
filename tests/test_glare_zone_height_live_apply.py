"""A glare zone's height is applied in place, without reloading the entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import _RUNTIME_APPLICABLE_OPTIONS
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    GLARE_ZONE_SLOT_NUMBERS,
    GLARE_ZONE_SLOTS,
    CoverType,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    apply_options_patch,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

_COVER = "cover.study_blind"
_Z_KEY = GLARE_ZONE_SLOTS[1]["z"]


@pytest.mark.unit
def test_every_zone_height_is_runtime_applicable():
    """Each slot's ``z`` is applied in place; name/x/y/radius still reload."""
    for slot in GLARE_ZONE_SLOT_NUMBERS:
        keys = GLARE_ZONE_SLOTS[slot]
        assert keys["z"] in _RUNTIME_APPLICABLE_OPTIONS
        for layout in ("name", "x", "y", "radius"):
            assert keys[layout] not in _RUNTIME_APPLICABLE_OPTIONS


@pytest.mark.integration
async def test_a_height_write_refreshes_without_reloading(hass: HomeAssistant):
    """Changing a zone's height refreshes the coordinator; the entry is not reloaded."""
    hass.states.async_set(_COVER, "open", {"current_position": 40})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Glare", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            **VERTICAL_OPTIONS,
            CONF_ENTITIES: [_COVER],
            CONF_ENABLE_GLARE_ZONES: True,
            GLARE_ZONE_SLOTS[1]["name"]: "Desk",
            _Z_KEY: 0.8,
        },
        entry_id="gz_height_live",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # The baseline the first real refresh takes (setup patches the refresh out).
    coordinator._cached_options = dict(entry.options)

    with (
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload,
        patch.object(coordinator, "async_refresh", AsyncMock()) as refresh,
    ):
        await apply_options_patch(hass, coordinator, {_Z_KEY: 1.2})
        await hass.async_block_till_done()

    reload.assert_not_awaited()
    refresh.assert_awaited_once()
    assert entry.state is ConfigEntryState.LOADED
    assert coordinator._cached_options[_Z_KEY] == 1.2
