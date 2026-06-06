"""Tests for the My-preset entities toggle (button + number gate).

The config-flow option `CONF_ENABLE_MY_POSITION_ENTITIES` controls whether the
"Managed My Position" button and "Managed My Position Value" number entity are
created. Default is `False` for new installs; the v2 → v3 migration sets it to
`True` for every pre-existing entry so the upgrade is invisible.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_my_position_button_not_created_when_toggle_default_false():
    """With no toggle set, button.async_setup_entry must NOT create the My Position button."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverButton,
        AdaptiveCoverMyPositionButton,
        async_setup_entry,
    )
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES, DOMAIN

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    # No CONF_ENABLE_MY_POSITION_ENTITIES key — gate must default to False
    config_entry.options = {CONF_ENTITIES: ["cover.foo"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    added: list = []

    def capture(entities, **kwargs):
        added.extend(entities)

    await async_setup_entry(hass, config_entry, capture)

    my_pos = [e for e in added if isinstance(e, AdaptiveCoverMyPositionButton)]
    reset = [e for e in added if isinstance(e, AdaptiveCoverButton)]
    assert (
        len(my_pos) == 0
    ), "My Position button must not be created when toggle defaults to False"
    assert (
        len(reset) == 1
    ), "Reset Manual Override button must remain always-on regardless of toggle"
    assert len(added) == 1


@pytest.mark.asyncio
async def test_my_position_number_not_created_when_toggle_default_false():
    """With no toggle set, number.async_setup_entry must NOT create the value entity."""
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES, DOMAIN
    from custom_components.adaptive_cover_pro.number import async_setup_entry

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    # No CONF_ENABLE_MY_POSITION_ENTITIES key — gate must default to False
    config_entry.options = {CONF_ENTITIES: ["cover.foo"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    added: list = []

    def capture(entities, **kwargs):
        added.extend(entities)

    await async_setup_entry(hass, config_entry, capture)

    assert (
        len(added) == 0
    ), "My Position Value number entity must not be created when toggle defaults to False"


@pytest.mark.asyncio
async def test_both_entities_created_when_toggle_true():
    """When the toggle is True, both the My Position button and number entity must be created."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverButton,
        AdaptiveCoverMyPositionButton,
        async_setup_entry as button_setup,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
        CONF_ENTITIES,
        DOMAIN,
    )
    from custom_components.adaptive_cover_pro.number import (
        AdaptiveCoverMyPositionNumber,
        async_setup_entry as number_setup,
    )

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {
        CONF_ENTITIES: ["cover.foo"],
        CONF_ENABLE_MY_POSITION_ENTITIES: True,
    }
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    button_added: list = []
    number_added: list = []

    def capture_buttons(entities, **kwargs):
        button_added.extend(entities)

    def capture_numbers(entities, **kwargs):
        number_added.extend(entities)

    await button_setup(hass, config_entry, capture_buttons)
    await number_setup(hass, config_entry, capture_numbers)

    assert any(isinstance(e, AdaptiveCoverButton) for e in button_added)
    assert any(isinstance(e, AdaptiveCoverMyPositionButton) for e in button_added)
    assert len(number_added) == 1
    assert isinstance(number_added[0], AdaptiveCoverMyPositionNumber)
