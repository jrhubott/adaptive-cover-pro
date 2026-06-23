"""Tests for AdaptiveCoverMyPositionNumber (number platform, issue #409)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Step 1 — Platform.NUMBER in PLATFORMS
# ---------------------------------------------------------------------------


def test_platform_number_in_platforms():
    """Platform.NUMBER must be listed in the integration's PLATFORMS."""
    from homeassistant.const import Platform

    from custom_components.adaptive_cover_pro import PLATFORMS

    assert Platform.NUMBER in PLATFORMS


# ---------------------------------------------------------------------------
# Step 2 — Module importable with AdaptiveCoverMyPositionNumber
# ---------------------------------------------------------------------------


def test_number_module_importable():
    """number.py must export AdaptiveCoverMyPositionNumber."""
    from custom_components.adaptive_cover_pro import number
    from custom_components.adaptive_cover_pro.number import (
        AdaptiveCoverMyPositionNumber,
    )

    assert hasattr(number, "async_setup_entry")
    assert AdaptiveCoverMyPositionNumber is not None


# ---------------------------------------------------------------------------
# Step 3 — Entity created when len(entities) >= 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_number_entity_created_when_entities_configured():
    """async_setup_entry must yield exactly one AdaptiveCoverMyPositionNumber."""
    from custom_components.adaptive_cover_pro.number import (
        AdaptiveCoverMyPositionNumber,
        async_setup_entry,
    )

    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
        CONF_ENTITIES,
        DOMAIN,
    )

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {
        CONF_ENTITIES: ["cover.test1"],
        CONF_ENABLE_MY_POSITION_ENTITIES: True,
    }
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    added = []

    def capture(entities, **kwargs):
        added.extend(entities)

    await async_setup_entry(hass, config_entry, capture)

    assert len(added) == 1
    assert isinstance(added[0], AdaptiveCoverMyPositionNumber)


# ---------------------------------------------------------------------------
# Helpers shared by Steps 4, 5, 6, 7
# ---------------------------------------------------------------------------


def _make_number_entity():
    """Return a minimal AdaptiveCoverMyPositionNumber without HA infrastructure."""
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES
    from custom_components.adaptive_cover_pro.number import (
        AdaptiveCoverMyPositionNumber,
    )

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {CONF_ENTITIES: ["cover.test1"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass = MagicMock()

    entity = AdaptiveCoverMyPositionNumber.__new__(AdaptiveCoverMyPositionNumber)
    entity.hass = hass
    entity.config_entry = config_entry
    entity.coordinator = coordinator
    return entity


# ---------------------------------------------------------------------------
# Step 4 — Min/max/step from _RANGE_MY_POSITION
# ---------------------------------------------------------------------------


def test_number_entity_range_matches_const():
    """Native min/max must match _RANGE_MY_POSITION; step must be 1."""
    from custom_components.adaptive_cover_pro.const import _RANGE_MY_POSITION
    from homeassistant.const import PERCENTAGE

    entity = _make_number_entity()

    assert entity._attr_native_min_value == _RANGE_MY_POSITION[0]
    assert entity._attr_native_max_value == _RANGE_MY_POSITION[1]
    assert entity._attr_native_step == 1
    assert entity._attr_native_unit_of_measurement == PERCENTAGE


# ---------------------------------------------------------------------------
# Step 5 — entity_category = CONFIG
# ---------------------------------------------------------------------------


def test_number_entity_category_is_config():
    """Entity category must be CONFIG so the entity appears in the config section."""
    from homeassistant.helpers.entity import EntityCategory

    entity = _make_number_entity()

    assert entity._attr_entity_category == EntityCategory.CONFIG


# ---------------------------------------------------------------------------
# Step 6 — async_set_native_value calls validate + apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_native_value_calls_apply_options_patch():
    """async_set_native_value must validate then apply the patch via options-service."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENTITIES,
        CONF_MY_POSITION_VALUE,
        CONF_SENSOR_TYPE,
    )

    entity = _make_number_entity()
    current_options = {CONF_ENTITIES: ["cover.test1"]}
    entity.config_entry.options = current_options
    entity.config_entry.data = {
        "name": "Test Cover",
        CONF_SENSOR_TYPE: "cover_blind",
    }

    with (
        patch(
            "custom_components.adaptive_cover_pro.number.validate_options_patch"
        ) as mock_validate,
        patch(
            "custom_components.adaptive_cover_pro.number.apply_options_patch",
            new_callable=AsyncMock,
        ) as mock_apply,
    ):
        mock_validate.return_value = {CONF_MY_POSITION_VALUE: 42}
        mock_apply.return_value = {}

        await entity.async_set_native_value(42.0)

        expected_patch = {CONF_MY_POSITION_VALUE: 42}
        mock_validate.assert_called_once_with(
            expected_patch, dict(current_options), "cover_blind"
        )
        mock_apply.assert_called_once_with(
            entity.hass, entity.coordinator, expected_patch
        )


# ---------------------------------------------------------------------------
# Step 7 — Cross-field rule fires (real validator, no mocks)
# ---------------------------------------------------------------------------


def test_set_native_value_cross_field_rule_fires():
    """Setting my_position_value=None when sunset_use_my=True must raise ServiceValidationError."""
    from homeassistant.core import ServiceValidationError

    from custom_components.adaptive_cover_pro.const import (
        CONF_MY_POSITION_VALUE,
        CONF_SUNSET_USE_MY,
    )
    from custom_components.adaptive_cover_pro.services.options_service import (
        validate_options_patch,
    )

    with pytest.raises(
        ServiceValidationError, match="sunset_use_my=true requires my_position_value"
    ):
        validate_options_patch(
            {CONF_MY_POSITION_VALUE: None},
            {CONF_SUNSET_USE_MY: True},
            "cover_blind",
        )


# ---------------------------------------------------------------------------
# Step 8 — native_value reads CONF_MY_POSITION_VALUE from options (issue #409)
# ---------------------------------------------------------------------------


def test_native_value_returns_option_value():
    """native_value must return float(CONF_MY_POSITION_VALUE) from config_entry.options."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENTITIES,
        CONF_MY_POSITION_VALUE,
    )

    entity = _make_number_entity()
    entity.config_entry.options = {
        CONF_ENTITIES: ["cover.test1"],
        CONF_MY_POSITION_VALUE: 35,
    }

    assert entity.native_value == 35.0


def test_native_value_returns_none_when_not_set():
    """native_value must be None when CONF_MY_POSITION_VALUE is absent from options."""
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES

    entity = _make_number_entity()
    entity.config_entry.options = {CONF_ENTITIES: ["cover.test1"]}

    assert entity.native_value is None
