"""Switch platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_MOTION_SENSORS,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
)
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the demo switch platform."""
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    enabled_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Integration Enabled",
        True,
        "enabled_toggle",
    )
    manual_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Manual Override",
        True,
        "manual_toggle",
    )
    control_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Automatic Control",
        True,
        "automatic_control",
    )
    climate_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Climate Mode",
        True,
        "switch_mode",
    )
    temp_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Outside Temperature",
        False,
        "temp_toggle",
        enabled_default=False,
    )
    lux_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Lux",
        True,
        "lux_toggle",
        enabled_default=False,
    )
    irradiance_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Irradiance",
        True,
        "irradiance_toggle",
        enabled_default=False,
    )
    return_default_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Return to default when disabled",
        False,
        "return_to_default_toggle",
    )

    motion_control_switch = AdaptiveCoverSwitch(
        config_entry.entry_id,
        hass,
        config_entry,
        coordinator,
        "Motion Control",
        True,
        "motion_control",
    )

    climate_mode = config_entry.options.get(CONF_CLIMATE_MODE)
    weather_entity = config_entry.options.get(CONF_WEATHER_ENTITY)
    sensor_entity = config_entry.options.get(CONF_OUTSIDETEMP_ENTITY)
    lux_entity = config_entry.options.get(CONF_LUX_ENTITY)
    irradiance_entity = config_entry.options.get(CONF_IRRADIANCE_ENTITY)
    sensor_type = config_entry.data.get(CONF_SENSOR_TYPE)
    motion_sensors = config_entry.options.get(CONF_MOTION_SENSORS, [])

    # Always add control and manual switches for all cover types
    switches = [enabled_switch, control_switch, manual_switch]

    # Add return to default switch for vertical and horizontal covers
    if sensor_type in ["cover_awning", "cover_blind"]:
        switches.append(return_default_switch)

    # Motion control switch — only when motion sensors are configured
    if motion_sensors:
        switches.append(motion_control_switch)

    if climate_mode:
        switches.append(climate_switch)
        if weather_entity or sensor_entity:
            switches.append(temp_switch)
        if lux_entity:
            switches.append(lux_switch)
        if irradiance_entity:
            switches.append(irradiance_switch)

    # Glare zone switches — one per configured zone for vertical covers
    if sensor_type == "cover_blind" and config_entry.options.get(
        CONF_ENABLE_GLARE_ZONES
    ):
        zone_counter = 0
        for idx in range(1, 5):  # idx is 1-based (matches config option keys)
            zone_name = config_entry.options.get(f"glare_zone_{idx}_name", "")
            if not zone_name:
                continue
            # Key uses sequential 0-based counter so it matches the compact list
            # that ConfigurationService builds (skipping blank slots). Both the
            # coordinator's enumerate() and this counter advance only for named zones,
            # so glare_zone_0 always refers to the first named zone regardless of
            # which config slot it came from.
            zone_key = f"glare_zone_{zone_counter}"
            zone_counter += 1
            switches.append(
                AdaptiveCoverSwitch(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    coordinator,
                    f"Glare Zone: {zone_name}",
                    True,
                    zone_key,
                )
            )

    async_add_entities(switches)


class AdaptiveCoverSwitch(AdaptiveCoverBaseEntity, SwitchEntity, RestoreEntity):
    """Representation of a adaptive cover switch."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        switch_name: str,
        initial_state: bool,
        key: str,
        device_class: SwitchDeviceClass | None = None,
        *,
        enabled_default: bool = True,
    ) -> None:
        """Initialize the switch."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._state: bool | None = None
        self._key = key
        self._attr_translation_key = key
        self._switch_name = switch_name
        self._attr_device_class = device_class
        self._initial_state = initial_state
        self._attr_unique_id = f"{entry_id}_{switch_name}"
        self._attr_entity_registry_enabled_default = enabled_default

        self.coordinator.logger.debug("Setup switch")

    @property
    def name(self):
        """Name of the entity."""
        return self._switch_name

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        self.coordinator.logger.debug("Turning on")
        self._attr_is_on = True
        setattr(self.coordinator, self._key, True)
        if self._key == "automatic_control" and kwargs.get("added") is not True:
            options = self.coordinator.config_entry.options
            for entity in self.coordinator.entities:
                if (
                    not self.coordinator.manager.is_cover_manual(entity)
                    and self.coordinator.check_adaptive_time
                ):
                    ctx = self.coordinator._build_position_context(
                        entity, options, force=True
                    )
                    await self.coordinator._cmd_svc.apply_position(
                        entity, self.coordinator.state, "auto_control_on", context=ctx
                    )
        await self.coordinator.async_refresh()
        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        self.coordinator.logger.debug("Turning off")
        self._attr_is_on = False
        setattr(self.coordinator, self._key, False)
        if self._key == "enabled_toggle" and kwargs.get("added") is not True:
            # Cancel any deferred motion/weather tasks so they don't fire after
            # the integration is disabled, and clear all reconciliation state so
            # nothing is resent automatically when re-enabling.
            self.coordinator._cancel_motion_timeout()  # noqa: SLF001
            self.coordinator._cancel_weather_timeout()  # noqa: SLF001
            self.coordinator._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
            self.coordinator._cmd_svc._safety_targets.clear()  # noqa: SLF001

        if self._key == "automatic_control" and kwargs.get("added") is not True:
            for entity in self.coordinator.manager.manual_controlled:
                self.coordinator.manager.reset(entity)

            # Return to default position if enabled
            if (
                hasattr(self.coordinator, "return_to_default_toggle")
                and self.coordinator.return_to_default_toggle
            ):
                default_position = self.coordinator.config_entry.options.get(
                    CONF_DEFAULT_HEIGHT, 60
                )
                self.coordinator.logger.debug(
                    "Returning covers to default position: %s", default_position
                )
                options = self.coordinator.config_entry.options
                for entity in self.coordinator.entities:
                    ctx = self.coordinator._build_position_context(
                        entity, options, force=True
                    )
                    await self.coordinator._cmd_svc.apply_position(
                        entity, default_position, "auto_control_off", context=ctx
                    )

        await self.coordinator.async_refresh()
        self.schedule_update_ha_state()

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        last_state = await self.async_get_last_state()
        self.coordinator.logger.debug("%s: last state is %s", self._name, last_state)
        if (last_state is None and self._initial_state) or (
            last_state is not None and last_state.state == STATE_ON
        ):
            await self.async_turn_on(added=True)
        else:
            await self.async_turn_off(added=True)
