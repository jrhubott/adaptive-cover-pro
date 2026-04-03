"""Button platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import _LOGGER, CONF_ENTITIES, DOMAIN
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    reset_manual = AdaptiveCoverButton(
        config_entry.entry_id, hass, config_entry, coordinator
    )

    buttons = []

    entities = config_entry.options.get(CONF_ENTITIES, [])
    if len(entities) >= 1:
        buttons = [reset_manual]

    async_add_entities(buttons)


class AdaptiveCoverButton(AdaptiveCoverBaseEntity, ButtonEntity):
    """Representation of a adaptive cover button."""

    _attr_translation_key = "reset_manual_override"

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize the button."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._attr_unique_id = f"{entry_id}_Reset Manual Override"
        self._button_name = "Reset Manual Override"
        self._entities = config_entry.options.get(CONF_ENTITIES, [])

    @property
    def name(self):
        """Name of the entity."""
        return self._button_name

    async def async_press(self) -> None:
        """Handle the button press."""
        reset_entities = []
        for entity in self._entities:
            if self.coordinator.manager.is_cover_manual(entity):
                _LOGGER.debug("Resetting manual override for: %s", entity)

                # Check if delta is sufficient before moving
                target_position = self.coordinator.state
                options = self.coordinator.config_entry.options
                if self.coordinator.check_position_delta(
                    entity, target_position, options
                ):
                    await self.coordinator.async_set_position(entity, target_position)
                    # Wait for cover to reach target, but no longer than 30 seconds
                    deadline = asyncio.get_event_loop().time() + 30
                    while self.coordinator.wait_for_target.get(entity):
                        if asyncio.get_event_loop().time() >= deadline:
                            _LOGGER.debug(
                                "Timed out waiting for %s to reach target position",
                                entity,
                            )
                            break
                        await asyncio.sleep(1)
                else:
                    _LOGGER.debug(
                        "Manual override reset: delta too small for %s, skipping position change",
                        entity,
                    )

                self.coordinator.manager.reset(entity)
                # Suppress re-detection: any cover state events arriving during
                # refresh should not be treated as a new manual override.
                self.coordinator.wait_for_target[entity] = True
                self.coordinator.cover_state_change = False
                reset_entities.append(entity)
            else:
                _LOGGER.debug(
                    "Resetting manual override for %s is not needed since it is already auto-controlled",
                    entity,
                )
        await self.coordinator.async_refresh()
        # Unblock state tracking now that refresh has consumed any pending events.
        for entity in reset_entities:
            self.coordinator.wait_for_target[entity] = False
