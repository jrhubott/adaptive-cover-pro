"""Tests for AdaptiveCoverMyPositionButton (button platform, issue #409)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Step 8 — My Position button created when entities configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_my_position_button_created_when_entities_configured():
    """async_setup_entry must yield exactly one AdaptiveCoverMyPositionButton."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
        async_setup_entry,
    )
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES, DOMAIN

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {CONF_ENTITIES: ["cover.test1"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    added = []

    def capture(entities, **kwargs):
        added.extend(entities)

    await async_setup_entry(hass, config_entry, capture)

    my_pos_buttons = [e for e in added if isinstance(e, AdaptiveCoverMyPositionButton)]
    assert len(my_pos_buttons) == 1


# ---------------------------------------------------------------------------
# Step 9 — async_press calls async_apply_user_position for each entity
# ---------------------------------------------------------------------------


def _make_my_position_button(*, options=None, entities=None):
    """Return a minimal AdaptiveCoverMyPositionButton without HA infrastructure."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENTITIES,
        CONF_MY_POSITION_VALUE,
    )

    if entities is None:
        entities = ["cover.test1", "cover.test2"]
    if options is None:
        options = {CONF_MY_POSITION_VALUE: 55, CONF_ENTITIES: entities}

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = options
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    coordinator.async_apply_user_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    button = AdaptiveCoverMyPositionButton.__new__(AdaptiveCoverMyPositionButton)
    button.coordinator = coordinator
    button.config_entry = config_entry
    button._entities = entities
    return button


@pytest.mark.asyncio
async def test_press_calls_async_apply_user_position():
    """async_press must call async_apply_user_position for each entity."""
    button = _make_my_position_button()

    await button.async_press()

    assert button.coordinator.async_apply_user_position.call_count == 2
    for call in button.coordinator.async_apply_user_position.call_args_list:
        assert call.args[1] == 55
        assert call.kwargs.get("trigger") == "my_position_recall"
        assert call.kwargs.get("force") is False


# ---------------------------------------------------------------------------
# Step 10 — Warn-and-skip when my_position_value not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_warn_and_skip_when_my_position_not_configured():
    """async_press must skip all covers when my_position_value is not set."""
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES

    options = {CONF_ENTITIES: ["cover.test1", "cover.test2"]}
    button = _make_my_position_button(
        options=options, entities=["cover.test1", "cover.test2"]
    )

    await button.async_press()

    button.coordinator.async_apply_user_position.assert_not_called()


# ---------------------------------------------------------------------------
# Step 11 — Preempted-skip return does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_records_preempted_skip_when_force_override_active():
    """async_press must not raise when coordinator returns preempted_by_force_override."""
    button = _make_my_position_button()
    button.coordinator.async_apply_user_position = AsyncMock(
        return_value=("skipped", "preempted_by_force_override")
    )

    # Must not raise
    await button.async_press()
