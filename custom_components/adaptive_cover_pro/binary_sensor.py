"""Binary Sensor platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENABLE_GLARE_ZONES, CONF_SENSOR_TYPE, DOMAIN
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Adaptive Cover Pro binary sensor platform."""
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []

    binary_sensor = AdaptiveCoverBinarySensor(
        config_entry,
        config_entry.entry_id,
        "Sun Infront",
        False,
        "sun_motion",
        BinarySensorDeviceClass.MOTION,
        coordinator,
    )
    manual_override = AdaptiveCoverBinarySensor(
        config_entry,
        config_entry.entry_id,
        "Manual Override",
        False,
        "manual_override",
        BinarySensorDeviceClass.RUNNING,
        coordinator,
    )
    entities.extend([binary_sensor, manual_override])

    # Diagnostic binary sensor (always enabled)
    position_mismatch = AdaptiveCoverPositionMismatchSensor(
        config_entry,
        config_entry.entry_id,
        coordinator,
    )
    entities.append(position_mismatch)

    # Glare active sensor — only created when glare zones are configured
    if (
        config_entry.options.get(CONF_ENABLE_GLARE_ZONES)
        and config_entry.data.get(CONF_SENSOR_TYPE) == "cover_blind"
    ):
        glare_active_sensor = AdaptiveCoverBinarySensor(
            config_entry,
            config_entry.entry_id,
            "Glare Active",
            False,
            "glare_active",
            BinarySensorDeviceClass.RUNNING,
            coordinator,
        )
        entities.append(glare_active_sensor)

    async_add_entities(entities)


class AdaptiveCoverBinarySensor(AdaptiveCoverBaseEntity, BinarySensorEntity):
    """representation of a Adaptive Cover Pro binary sensor."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        unique_id: str,
        binary_name: str,
        state: bool,
        key: str,
        device_class: BinarySensorDeviceClass,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(
            unique_id,
            coordinator.hass,
            config_entry,
            coordinator,
        )
        self._key = key
        self._attr_translation_key = key
        self._binary_name = binary_name
        self._attr_unique_id = f"{unique_id}_{key}"
        self._state = state
        self._attr_device_class = device_class

    @property
    def name(self):
        """Name of the entity."""
        return self._binary_name

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self.coordinator.data.states[self._key]

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:  # noqa: D102
        if self._key == "manual_override":
            return {"manual_controlled": self.coordinator.data.states["manual_list"]}


class AdaptiveCoverPositionMismatchSensor(AdaptiveCoverBaseEntity, BinarySensorEntity):
    """Binary sensor indicating if position doesn't match calculated value."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False  # P1 sensor
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "position_mismatch"

    def __init__(
        self,
        config_entry: ConfigEntry,
        unique_id: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(
            unique_id,
            coordinator.hass,
            config_entry,
            coordinator,
        )
        self._attr_unique_id = f"{unique_id}_position_mismatch"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return "Position Mismatch"

    @property
    def is_on(self) -> bool:
        """Return True if position mismatch detected."""
        # Check if any entity has a position mismatch between target and actual
        for entity_id in self.coordinator.entities:
            target = self.coordinator._cmd_svc.get_target(entity_id)  # noqa: SLF001
            if target is None:
                continue  # No command sent yet

            actual = self.coordinator._get_current_position(entity_id)
            if actual is None:
                continue

            delta = abs(target - actual)
            if delta > self.coordinator._cmd_svc._position_tolerance:
                return True

        return False

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional attributes."""
        tolerance = self.coordinator._cmd_svc._position_tolerance
        attrs: dict[str, Any] = {
            "tolerance": tolerance,
        }

        # Add per-entity details
        entity_details: dict[str, dict[str, Any]] = {}
        for entity_id in self.coordinator.entities:
            diag = self.coordinator._cmd_svc.get_diagnostics(entity_id)
            if diag["target"] is not None and diag["actual"] is not None:
                delta = abs(diag["target"] - diag["actual"])
                entity_details[entity_id] = {
                    "target_position": diag["target"],
                    "actual_position": diag["actual"],
                    "position_delta": delta,
                    "mismatch": delta > tolerance,
                    "retry_count": diag["retry_count"],
                }

        if entity_details:
            attrs["entities"] = entity_details

        return attrs
