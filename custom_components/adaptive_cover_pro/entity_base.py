"""Base entity classes for Adaptive Cover Pro integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_SENSOR_TYPE, DOMAIN
from .const import CoverType

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import AdaptiveDataUpdateCoordinator


# Unique marker for "no state has been written yet". A fresh object() never
# compares equal to any render signature, so the first coordinator update
# always writes.
_SENTINEL = object()


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
        # Render signature of the last write; gates redundant async_write_ha_state.
        self._acp_last_write_sig: object = _SENTINEL

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

        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._device_id)},
            name=self._name,
            manufacturer="Jason Rhubottom",
            model=self._get_device_model_name(self._cover_type),
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

    def _get_device_model_name(self, cover_type: str | CoverType) -> str:
        """Return a localized device model label for the configured cover type."""
        language = (getattr(self.hass.config, "language", None) or "en").split("-")[0]
        labels = {
            "de": {
                CoverType.BLIND: "Adaptive Vertikale Beschattung",
                CoverType.AWNING: "Adaptive Horizontale Markise",
                CoverType.TILT: "Adaptive Jalousie (nur Neigung)",
                CoverType.VENETIAN: "Adaptive Jalousie (zwei Achsen)",
                CoverType.OSCILLATING_AWNING: "Adaptive Ausfallmarkise",
                CoverType.ROOF_WINDOW: "Adaptive Dachfenster-Beschattung",
                CoverType.SLIDING_CURTAIN: "Adaptive Schiebegardine",
                CoverType.LOUVERED_ROOF: "Adaptives Lamellendach",
                CoverType.DAY_NIGHT_SHADE: "Adaptives Tag-/Nachtrollo",
                CoverType.DUAL_PANEL: "Adaptiver Doppelpanel-Rollo",
            },
            "fr": {
                CoverType.BLIND: "Store vertical Adaptive",
                CoverType.AWNING: "Store banne horizontal Adaptive",
                CoverType.TILT: "Store vénitien Adaptive (inclinaison seule)",
                CoverType.VENETIAN: "Store vénitien Adaptive (deux axes)",
                CoverType.OSCILLATING_AWNING: "Store à projection Adaptive",
                CoverType.ROOF_WINDOW: "Fenêtre de toit Adaptive",
                CoverType.SLIDING_CURTAIN: "Rideau coulissant Adaptive",
                CoverType.LOUVERED_ROOF: "Toit à lames Adaptive",
                CoverType.DAY_NIGHT_SHADE: "Store jour/nuit Adaptive",
                CoverType.DUAL_PANEL: "Store à double panneau Adaptive",
            },
        }
        cover_type = CoverType(cover_type)
        if language in labels and cover_type in labels[language]:
            return labels[language][cover_type]
        return f"Adaptive {cover_type.display_name} Cover"

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
        """Write HA state only when this entity's render output changed.

        The coordinator notifies every listener on each update cycle, but most
        cycles leave an individual entity's rendered value unchanged (e.g. a sun
        micro-move or a chatty temperature sensor that doesn't shift the computed
        position). Skipping the write in that case avoids a state-machine update,
        a bus event, and a recorder row per entity per cycle.

        Fails open: any error building the signature resets the cache and writes,
        so a comparison bug can never suppress a legitimate state update.
        """
        try:
            sig = self._acp_render_signature()
        except Exception:  # noqa: BLE001 - never let a signature error suppress a write
            self._acp_last_write_sig = _SENTINEL
            self.async_write_ha_state()
            return
        if sig == self._acp_last_write_sig:
            return
        self._acp_last_write_sig = sig
        self.async_write_ha_state()

    def _acp_render_signature(self) -> object:
        """Return a cheap, comparable snapshot of everything HA renders.

        ``self.state`` is resolved by HA's base ``Entity`` subclass for each
        platform (sensor native_value, binary_sensor/switch is_on, cover
        position), so a single signature works uniformly. ``extra_state_attributes``
        is snapshotted via ``repr`` to give a stable token even if a subclass
        reuses a mutable mapping across cycles; the attribute values here are
        plain dicts/lists/scalars/datetimes, so the repr is deterministic and far
        cheaper than a full state write. ``available`` is included so the
        coordinator-data availability flip (see :meth:`available`) forces a write.
        """
        return (
            self.available,
            self.state,
            repr(self.extra_state_attributes),
        )


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
