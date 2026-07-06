"""Button platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    _LOGGER,
    CONF_ENABLE_MY_POSITION_ENTITIES,
    CONF_ENTITIES,
    CONF_MY_POSITION_VALUE,
    CONF_SENSOR_TYPE,
    DEFAULT_ENABLE_MY_POSITION_ENTITIES,
)
from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AdaptiveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator: AdaptiveDataUpdateCoordinator = config_entry.runtime_data

    # Cover groups expose scene + clear-overrides buttons, never the cover set.
    from .cover_types import get_policy

    if get_policy(config_entry.data.get(CONF_SENSOR_TYPE)).is_orchestrator:
        from .group_entities import build_group_buttons

        async_add_entities(
            build_group_buttons(config_entry.entry_id, hass, config_entry, coordinator)
        )
        return

    buttons: list[ButtonEntity] = []

    entities = config_entry.options.get(CONF_ENTITIES, [])
    if len(entities) >= 1:
        buttons.append(
            AdaptiveCoverButton(config_entry.entry_id, hass, config_entry, coordinator)
        )
        if config_entry.options.get(
            CONF_ENABLE_MY_POSITION_ENTITIES, DEFAULT_ENABLE_MY_POSITION_ENTITIES
        ):
            buttons.append(
                AdaptiveCoverMyPositionButton(
                    config_entry.entry_id, hass, config_entry, coordinator
                )
            )

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
        """Handle the button press.

        The full clear-and-resend sequence lives on the coordinator
        (``async_reset_manual_overrides``) so this button and the cover-group
        bulk clear (issue #790) share one path.
        """
        await self.coordinator.async_reset_manual_overrides(self._entities)


class AdaptiveCoverMyPositionButton(AdaptiveCoverBaseEntity, ButtonEntity):
    """Button that recalls the user's saved My Position preset."""

    _attr_translation_key = "my_position"

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize the button."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._attr_unique_id = f"{entry_id}_my_position"
        self._entities = config_entry.options.get(CONF_ENTITIES, [])

    async def async_press(self) -> None:
        """Send the My Position command to all configured covers."""
        my_position_value = self.config_entry.options.get(CONF_MY_POSITION_VALUE)
        if my_position_value is None:
            _LOGGER.warning(
                "My Position button pressed but my_position_value is not configured"
            )
            return
        for entity_id in self._entities:
            await self.coordinator.async_apply_user_position(
                entity_id,
                int(my_position_value),
                trigger="my_position_recall",
                force=False,
                use_my_position=True,
            )
