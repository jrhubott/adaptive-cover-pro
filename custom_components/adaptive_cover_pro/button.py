"""Button platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

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
                self.coordinator.manager.reset(entity)
                # Suppress re-detection: cover state events during refresh must
                # not be treated as a new manual override.
                self.coordinator.wait_for_target[entity] = True
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

        # Send the fresh pipeline position to every reset cover.
        # Using the standard context means all normal gates apply (auto_control,
        # delta, time threshold) except manual_override which is already cleared.
        # force=True is not needed here because the cover was manually moved
        # away from the calculated position, so the delta will naturally be large.
        options = self.coordinator.config_entry.options
        for entity in reset_entities:
            ctx = self.coordinator._build_position_context(entity, options)
            outcome, _ = await self.coordinator._cmd_svc.apply_position(
                entity, self.coordinator.state, "manual_reset", context=ctx
            )
            if outcome != "sent":
                _LOGGER.debug(
                    "Manual override reset: no position change sent for %s (%s)",
                    entity,
                    outcome,
                )
            # If sent, apply_position already set wait_for_target=True;
            # let normal cover-state-change detection clear it on arrival.
            # If not sent, unblock state tracking now.
            if outcome != "sent":
                self.coordinator.wait_for_target[entity] = False
