"""Number platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENTITIES,
    CONF_MY_POSITION_VALUE,
    CONF_SENSOR_TYPE,
    DOMAIN,
    _RANGE_MY_POSITION,
)
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverBaseEntity
from .services.options_service import apply_options_patch, validate_options_patch


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    numbers = []

    entities = config_entry.options.get(CONF_ENTITIES, [])
    if len(entities) >= 1:
        numbers = [
            AdaptiveCoverMyPositionNumber(
                config_entry.entry_id, hass, config_entry, coordinator
            )
        ]

    async_add_entities(numbers)


class AdaptiveCoverMyPositionNumber(AdaptiveCoverBaseEntity, NumberEntity):
    """Number entity for configuring the My Position value."""

    _attr_translation_key = "my_position_value"
    _attr_native_min_value = _RANGE_MY_POSITION[0]
    _attr_native_max_value = _RANGE_MY_POSITION[1]
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._attr_unique_id = f"{entry_id}_my_position_value"
        self._entities = config_entry.options.get(CONF_ENTITIES, [])

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new My Position value to config_entry.options."""
        patch = {CONF_MY_POSITION_VALUE: int(value)}
        sensor_type = self.config_entry.data.get(CONF_SENSOR_TYPE)
        validate_options_patch(patch, dict(self.config_entry.options), sensor_type)
        await apply_options_patch(self.hass, self.coordinator, patch)
