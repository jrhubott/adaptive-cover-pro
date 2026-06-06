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
    DEFAULT_ENABLE_MY_POSITION_ENTITIES,
    DOMAIN,
)
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
        """Handle the button press."""
        reset_entities = []
        for entity in self._entities:
            if self.coordinator.manager.is_cover_manual(entity):
                _LOGGER.debug("Resetting manual override for: %s", entity)
                self.coordinator.manager.reset(entity)
                # Suppress re-detection: cover state events during refresh must
                # not be treated as a new manual override.
                self.coordinator._cmd_svc.set_waiting(entity, True)  # noqa: SLF001
                self.coordinator.cover_state_change = False
                reset_entities.append(entity)
            else:
                _LOGGER.debug(
                    "Resetting manual override for %s is not needed since it is already auto-controlled",
                    entity,
                )

        if not reset_entities:
            return

        # Refresh so the pipeline re-runs without the override active,
        # producing the correct post-override position (climate, solar,
        # default — whichever handler wins now).
        await self.coordinator.async_refresh()

        # Delegate to the shared post-override send path.
        # Time-window and automatic-control gates live there, along with
        # force=True so time_delta/position_delta are bypassed for this
        # intentional user reset.
        sent = await self.coordinator._async_send_after_override_clear(
            self.coordinator.state,
            self.coordinator.config_entry.options,
            entities=reset_entities,
            trigger="manual_reset",
        )

        # Entities not sent to (gated by time window / auto-control, or
        # skipped inside apply_position) must have wait_for_target cleared so
        # later cover state events are not silently swallowed.
        # Entities that were sent already have wait_for_target=True set by
        # apply_position; leave those untouched.
        for entity in reset_entities:
            if entity not in sent:
                _LOGGER.debug(
                    "Manual override reset: no position change sent for %s",
                    entity,
                )
                self.coordinator._cmd_svc.set_waiting(entity, False)  # noqa: SLF001


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
