"""Setup-path and unload-path behaviour for the virtual Building Profile entry type.

A ``cover_building_profile`` config entry holds shared building-level sensor
IDs and registers no platforms. ``async_setup_entry`` must short-circuit
before constructing the coordinator or forwarding any platform, while still
registering an update listener so future commits can propagate profile
changes to linked covers. ``async_unload_entry`` must symmetrically skip
``async_unload_platforms`` for the same virtual entry types.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


async def test_building_profile_entry_skips_coordinator(hass) -> None:
    """Profile setup returns True without coordinator/platforms; adds a listener."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Building", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_01",
        title="Building",
    )
    entry.add_to_hass(hass)

    add_listener = MagicMock(return_value=lambda: None)
    entry.add_update_listener = add_listener

    with (
        patch(
            "custom_components.adaptive_cover_pro.AdaptiveDataUpdateCoordinator"
        ) as mock_coord,
        patch.object(hass.config_entries, "async_forward_entry_setups") as mock_forward,
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    mock_coord.assert_not_called()
    mock_forward.assert_not_called()
    add_listener.assert_called_once()


async def test_building_profile_entry_unloads_without_platforms(hass) -> None:
    """Profile unload must NOT call async_unload_platforms (entry loaded no platforms)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Building", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_02",
        title="Building",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms", return_value=True
        ) as mock_unload_platforms,
        patch("custom_components.adaptive_cover_pro.async_unload_services"),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_unload_platforms.assert_not_called()


async def test_real_cover_entry_unloads_platforms(hass) -> None:
    """Cover entries (controls_cover=True) must call async_unload_platforms with PLATFORMS."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Blind", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={},
        entry_id="cover_01",
        title="My Blind",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms", return_value=True
        ) as mock_unload_platforms,
        patch("custom_components.adaptive_cover_pro.async_unload_services"),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_unload_platforms.assert_called_once_with(entry, PLATFORMS)
