"""Setup-path and unload-path behaviour for the virtual Cover Group entry type.

A ``cover_group`` config entry orchestrates member covers (issue #790). Its
policy sets ``is_orchestrator = True``: ``async_setup_entry`` must branch to
a ``GroupCoordinator`` — never the sun/geometry coordinator — and forward
only the group platform set. ``async_unload_entry`` must symmetrically
unload the same platform set (the #712/#714 load/unload-symmetry lesson).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import (
    GROUP_PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


def _group_entry(entry_id: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Living Room", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={},
        entry_id=entry_id,
        title="Living Room",
    )


def test_group_platforms_contents() -> None:
    """The group forwards exactly its own platform set — no number/binary."""
    assert [
        Platform.SENSOR,
        Platform.SWITCH,
        Platform.BUTTON,
        Platform.SELECT,
        Platform.COVER,
    ] == GROUP_PLATFORMS


async def test_group_entry_builds_group_coordinator(hass) -> None:
    """Group setup builds a GroupCoordinator, not the sun/geometry coordinator."""
    entry = _group_entry("group_01")
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.adaptive_cover_pro.AdaptiveDataUpdateCoordinator"
        ) as mock_cover_coord,
        patch(
            "custom_components.adaptive_cover_pro.GroupCoordinator"
        ) as mock_group_coord,
        patch.object(hass.config_entries, "async_forward_entry_setups") as mock_forward,
    ):
        mock_group_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        result = await async_setup_entry(hass, entry)

    assert result is True
    mock_cover_coord.assert_not_called()
    mock_group_coord.assert_called_once_with(hass, entry)
    assert entry.runtime_data is mock_group_coord.return_value
    mock_forward.assert_called_once_with(entry, GROUP_PLATFORMS)


async def test_group_entry_unloads_group_platforms(hass) -> None:
    """Group unload must unload GROUP_PLATFORMS — not the cover PLATFORMS list."""
    entry = _group_entry("group_02")
    entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms", return_value=True
        ) as mock_unload_platforms,
        patch("custom_components.adaptive_cover_pro.async_unload_services"),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_unload_platforms.assert_called_once_with(entry, GROUP_PLATFORMS)


async def test_group_options_update_reloads_entry(hass) -> None:
    """Editing the group's options (membership) reloads the entry."""
    entry = _group_entry("group_03")
    entry.add_to_hass(hass)

    listeners: list = []
    entry.add_update_listener = MagicMock(
        side_effect=lambda listener: listeners.append(listener) or (lambda: None)
    )

    with (
        patch("custom_components.adaptive_cover_pro.GroupCoordinator") as mock_coord,
        patch.object(hass.config_entries, "async_forward_entry_setups"),
    ):
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        await async_setup_entry(hass, entry)

    assert listeners, "group setup must register an options update listener"
    with patch.object(hass.config_entries, "async_reload") as mock_reload:
        await listeners[0](hass, entry)
    mock_reload.assert_called_once_with(entry.entry_id)
