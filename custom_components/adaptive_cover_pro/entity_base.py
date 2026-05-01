"""Base entity classes for Adaptive Cover Pro integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_SENSOR_TYPE, DOMAIN
from .enums import CoverType

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import AdaptiveDataUpdateCoordinator


class AdaptiveCoverBaseEntity(CoordinatorEntity["AdaptiveDataUpdateCoordinator"]):
    """Base class for all Adaptive Cover Pro entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self.hass = hass
        self.config_entry = config_entry
        self.coordinator = coordinator
        self._name = config_entry.data["name"]
        self._cover_type = config_entry.data[CONF_SENSOR_TYPE]
        self._device_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        linked_device_id = self.config_entry.options.get(CONF_DEVICE_ID)
        if linked_device_id:
            device_reg = dr.async_get(self.hass)
            device_entry = device_reg.async_get(linked_device_id)
            if device_entry:
                return DeviceInfo(
                    identifiers=device_entry.identifiers,
                    connections=device_entry.connections,
                )

        type_display = self._get_type_display_name(self._cover_type)
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._device_id)},
            name=self._name,
            manufacturer="BasHeijermans",
            model=f"Adaptive {type_display} Cover",
            configuration_url="https://github.com/jrhubott/adaptive-cover-pro",
        )

    @staticmethod
    def _get_type_display_name(cover_type: str | CoverType) -> str:
        """Get display name for cover type.

        Delegates to `CoverType.display_name`. Accepts either the enum or its
        underlying string value. Raises `ValueError` for unrecognised values
        rather than silently returning "Unknown" — a new enum member needs an
        intentional add to `display_name`.
        """
        return CoverType(cover_type).display_name

    @property
    def available(self) -> bool:
        """Return False until coordinator.data is populated.

        All Adaptive Cover Pro entities read from coordinator.data in their
        state properties. Home Assistant's entity-add path calls
        async_write_ha_state() as soon as the entity is added to the platform,
        which races with async_config_entry_first_refresh(). If the write
        happens first, every state property raises AttributeError and HA
        drops the entity from the registry for the whole session. Returning
        False here tells HA to use STATE_UNAVAILABLE and skip state reads
        until the coordinator has real data.

        This is the single point of enforcement — do not duplicate this
        guard in subclasses.
        """
        if self.coordinator.data is None:
            return False
        return super().available

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self.async_write_ha_state()


class AdaptiveCoverSensorBase(AdaptiveCoverBaseEntity):
    """Base class for Adaptive Cover sensors."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        unique_id_suffix: str,
        icon: str | None = None,
    ) -> None:
        """Initialize sensor."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"
        if icon:
            self._attr_icon = icon

    @property
    def data(self):
        """Return coordinator data for convenience."""
        return self.coordinator.data


class AdaptiveCoverDiagnosticSensorBase(AdaptiveCoverSensorBase):
    """Base class for diagnostic sensors."""

    _attr_entity_registry_enabled_default = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        unique_id_suffix: str,
        icon: str,
        unit: str | None = None,
        state_class=None,
    ) -> None:
        """Initialize diagnostic sensor."""
        super().__init__(
            entry_id, hass, config_entry, coordinator, unique_id_suffix, icon
        )
        if unit:
            self._attr_native_unit_of_measurement = unit
        if state_class:
            self._attr_state_class = state_class
