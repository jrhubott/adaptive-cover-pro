"""Switch platform for the Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class _SwitchSpec:
    """Spec for a single AdaptiveCoverSwitch instance.

    Locked unique_id contract: `switch_name` becomes the suffix in
    `f"{entry_id}_{switch_name}"`. **Do not change any switch_name string** —
    those are user-installed registry keys. Translation/internal lookups use
    `key`. The two are intentionally different (e.g. `Manual Override` vs
    `manual_toggle`) and the test in
    `tests/test_switch_actions.py:204` pins this asymmetry.
    """

    switch_name: str  # → unique_id suffix; LOCKED
    key: str  # translation_key + coordinator attribute name
    initial_state: bool
    enabled_default: bool = True
    display_name: str | None = None
    enabled_when: Callable[[ConfigEntry], bool] = field(default=lambda _: True)


def _has_climate_mode(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(CONF_CLIMATE_MODE))


def _has_climate_temp_source(entry: ConfigEntry) -> bool:
    if not _has_climate_mode(entry):
        return False
    return bool(
        entry.options.get(CONF_WEATHER_ENTITY)
        or entry.options.get(CONF_OUTSIDETEMP_ENTITY)
    )


def _has_climate_lux(entry: ConfigEntry) -> bool:
    return _has_climate_mode(entry) and bool(entry.options.get(CONF_LUX_ENTITY))


def _has_climate_irradiance(entry: ConfigEntry) -> bool:
    return _has_climate_mode(entry) and bool(entry.options.get(CONF_IRRADIANCE_ENTITY))


def _is_vertical_or_horizontal(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_SENSOR_TYPE) in ("cover_awning", "cover_blind")


def _has_motion_sensors(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(CONF_MOTION_SENSORS, []))


# Order matches the pre-refactor instantiation order in async_setup_entry so
# that platform-add ordering (and therefore HA logbook chronology) is
# unchanged.
_SWITCH_SPECS: tuple[_SwitchSpec, ...] = (
    _SwitchSpec(
        switch_name="Integration Enabled",
        key="enabled_toggle",
        initial_state=True,
    ),
    _SwitchSpec(
        switch_name="Automatic Control",
        key="automatic_control",
        initial_state=True,
    ),
    _SwitchSpec(
        switch_name="Manual Override",
        key="manual_toggle",
        initial_state=True,
        display_name="Manual Override Detection",
    ),
    _SwitchSpec(
        switch_name="Return to default when disabled",
        key="return_to_default_toggle",
        initial_state=False,
        enabled_when=_is_vertical_or_horizontal,
    ),
    _SwitchSpec(
        switch_name="Motion Control",
        key="motion_control",
        initial_state=True,
        enabled_when=_has_motion_sensors,
    ),
    _SwitchSpec(
        switch_name="Climate Mode",
        key="switch_mode",
        initial_state=True,
        enabled_when=_has_climate_mode,
    ),
    _SwitchSpec(
        switch_name="Outside Temperature",
        key="temp_toggle",
        initial_state=False,
        enabled_default=False,
        enabled_when=_has_climate_temp_source,
    ),
    _SwitchSpec(
        switch_name="Lux",
        key="lux_toggle",
        initial_state=True,
        enabled_default=False,
        enabled_when=_has_climate_lux,
    ),
    _SwitchSpec(
        switch_name="Irradiance",
        key="irradiance_toggle",
        initial_state=True,
        enabled_default=False,
        enabled_when=_has_climate_irradiance,
    ),
)


def _glare_zone_specs(entry: ConfigEntry) -> list[_SwitchSpec]:
    """Build dynamic glare-zone switch specs from configured zone names.

    Vertical-cover-only feature. The compact 0-based key (`glare_zone_0`,
    `glare_zone_1`, …) advances only for *named* zones, matching the index
    that ConfigurationService uses. The unique_id suffix
    `f"Glare Zone: {zone_name}"` carries the user-provided text and **must
    stay byte-identical** — that user text is the registry key.
    """
    if entry.data.get(CONF_SENSOR_TYPE) != "cover_blind":
        return []
    if not entry.options.get(CONF_ENABLE_GLARE_ZONES):
        return []

    specs: list[_SwitchSpec] = []
    zone_counter = 0
    for idx in range(1, 5):  # idx is 1-based (matches config option keys)
        zone_name = entry.options.get(f"glare_zone_{idx}_name", "")
        if not zone_name:
            continue
        specs.append(
            _SwitchSpec(
                switch_name=f"Glare Zone: {zone_name}",
                key=f"glare_zone_{zone_counter}",
                initial_state=True,
            )
        )
        zone_counter += 1
    return specs


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the demo switch platform."""
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    specs: list[_SwitchSpec] = [
        spec for spec in _SWITCH_SPECS if spec.enabled_when(config_entry)
    ]
    specs.extend(_glare_zone_specs(config_entry))

    async_add_entities(
        AdaptiveCoverSwitch(
            config_entry.entry_id,
            hass,
            config_entry,
            coordinator,
            spec.switch_name,
            spec.initial_state,
            spec.key,
            enabled_default=spec.enabled_default,
            display_name=spec.display_name,
        )
        for spec in specs
    )


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
        display_name: str | None = None,
    ) -> None:
        """Initialize the switch."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._state: bool | None = None
        self._key = key
        self._attr_translation_key = key
        self._switch_name = display_name or switch_name
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
            # Stop any ACP-in-flight cover moves FIRST (before the gate closes),
            # then cancel deferred tasks and clear all reconciliation state so
            # nothing is resent automatically when re-enabling.
            await self.coordinator._cmd_svc.stop_in_flight()  # noqa: SLF001
            self.coordinator._cancel_motion_timeout()  # noqa: SLF001
            self.coordinator._cancel_weather_timeout()  # noqa: SLF001
            self.coordinator._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
            self.coordinator._cmd_svc.clear_safety_targets()  # noqa: SLF001

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
                    # Sanctioned one-shot transition: auto_control was just
                    # toggled OFF; honor the user's "return to default" choice
                    # by bypassing the auto_control gate exactly once.  Without
                    # bypass_auto_control=True the gate (issue #293) would
                    # correctly skip this command.
                    ctx = self.coordinator._build_position_context(
                        entity, options, force=True, bypass_auto_control=True
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
