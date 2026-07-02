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
    CONF_CLOUD_SUPPRESSION,
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_SUN_TRACKING,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
)
from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator
from .cover_types import get_policy
from .entity_base import AdaptiveCoverBaseEntity
from .helpers import motion_entities
from .services.options_service import apply_options_patch, validate_options_patch


@dataclass(frozen=True, slots=True)
class _SwitchSpec:
    """Spec for a single AdaptiveCoverSwitch instance.

    Locked unique_id contract: `switch_name` becomes the suffix in
    `f"{entry_id}_{switch_name}"`. **Do not change any switch_name string** —
    those are user-installed registry keys. Translation/internal lookups use
    `key`. The two are intentionally different (e.g. `Manual Override` vs
    `manual_toggle`) and the test in
    `tests/test_switch_actions.py:204` pins this asymmetry.

    ``option_key`` marks a switch backed by ``config_entry.options`` instead of
    a coordinator attribute. The coordinator's update listener maps the
    persisted change to its runtime action (see ``_RUNTIME_APPLICABLE_OPTIONS``
    in ``__init__``), so the switch only persists — it never rebuilds directly.
    """

    switch_name: str  # → unique_id suffix; LOCKED
    key: str  # translation_key; legacy switches also reuse it as the coordinator attr
    initial_state: bool
    enabled_default: bool = True
    display_name: str | None = None
    enabled_when: Callable[[ConfigEntry], bool] = field(default=lambda _: True)
    option_key: str | None = None


def _has_climate_mode(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(CONF_CLIMATE_MODE))


def _has_climate_temp_source(entry: ConfigEntry) -> bool:
    if not _has_climate_mode(entry):
        return False
    return bool(
        entry.options.get(CONF_WEATHER_ENTITY)
        or entry.options.get(CONF_OUTSIDETEMP_ENTITY)
    )


def _has_lux_feature(entry: ConfigEntry) -> bool:
    """Lux switch shown when lux entity configured AND either climate mode or cloud suppression is on."""
    return bool(entry.options.get(CONF_LUX_ENTITY)) and (
        _has_climate_mode(entry) or bool(entry.options.get(CONF_CLOUD_SUPPRESSION))
    )


def _has_irradiance_feature(entry: ConfigEntry) -> bool:
    """Irradiance switch shown when irradiance entity configured AND either climate mode or cloud suppression is on."""
    return bool(entry.options.get(CONF_IRRADIANCE_ENTITY)) and (
        _has_climate_mode(entry) or bool(entry.options.get(CONF_CLOUD_SUPPRESSION))
    )


def _supports_return_to_default_switch(entry: ConfigEntry) -> bool:
    """Whether the "Return to default when disabled" switch applies to this cover.

    The answer is a per-cover-type semantic, owned by the ``CoverTypePolicy``.
    """
    return get_policy(
        entry.data.get(CONF_SENSOR_TYPE)
    ).supports_return_to_default_switch


def _has_motion_sensors(entry: ConfigEntry) -> bool:
    return bool(motion_entities(entry.options))


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
        switch_name="Sun Tracking",
        key="sun_tracking",
        initial_state=True,
        option_key=CONF_ENABLE_SUN_TRACKING,
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
        enabled_when=_supports_return_to_default_switch,
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
        enabled_when=_has_lux_feature,
    ),
    _SwitchSpec(
        switch_name="Irradiance",
        key="irradiance_toggle",
        initial_state=True,
        enabled_default=False,
        enabled_when=_has_irradiance_feature,
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
    from .cover_types import POLICY_REGISTRY, get_policy

    sensor_type = entry.data.get(CONF_SENSOR_TYPE)
    if sensor_type not in POLICY_REGISTRY:
        return []
    if not get_policy(sensor_type).supports_glare_zones:
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
    config_entry: AdaptiveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the demo switch platform."""
    coordinator: AdaptiveDataUpdateCoordinator = config_entry.runtime_data

    # Cover groups expose the bulk automation switch, never the cover set.
    if get_policy(config_entry.data.get(CONF_SENSOR_TYPE)).is_orchestrator:
        from .group_entities import build_group_switches

        async_add_entities(
            build_group_switches(config_entry.entry_id, hass, config_entry, coordinator)
        )
        return

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
            (
                bool(config_entry.options.get(spec.option_key, spec.initial_state))
                if spec.option_key
                else spec.initial_state
            ),
            spec.key,
            enabled_default=spec.enabled_default,
            display_name=spec.display_name,
            option_key=spec.option_key,
        )
        for spec in specs
    )


class AdaptiveCoverSwitch(AdaptiveCoverBaseEntity, SwitchEntity, RestoreEntity):
    """Representation of a adaptive cover switch."""

    _option_key: str | None = None

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
        option_key: str | None = None,
    ) -> None:
        """Initialize the switch."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._state: bool | None = None
        self._key = key
        self._attr_translation_key = key
        self._switch_name = display_name or switch_name
        self._attr_device_class = device_class
        self._initial_state = initial_state
        self._option_key = option_key
        self._attr_unique_id = f"{entry_id}_{switch_name}"
        self._attr_entity_registry_enabled_default = enabled_default

        self.coordinator.logger.debug("Setup switch")

    @property
    def name(self):
        """Name of the entity."""
        return self._switch_name

    @property
    def is_on(self) -> bool | None:
        """Return the switch state.

        Option-backed switches read config_entry.options so service/options-flow
        changes are reflected without RestoreEntity state drift.
        """
        if self._option_key is not None:
            return bool(
                self.config_entry.options.get(
                    self._option_key,
                    self._initial_state,
                )
            )
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        self.coordinator.logger.debug("Turning on")
        if self._option_key is not None:
            await self._async_set_option(True)
            return
        self._attr_is_on = True
        setattr(self.coordinator, self._key, True)
        if self._key == "automatic_control" and kwargs.get("added") is not True:
            # Issue #33 defense-in-depth: invalidate the venetian sequencer's
            # stored tilt targets so the very next cycle resolves the
            # min-delta gate's anchor from live actuator state rather than a
            # potentially stale cached target. No-op on non-venetian policies
            # (their _policy has no sequencer attribute or it's None).
            sequencer = getattr(self.coordinator._policy, "sequencer", None)
            if sequencer is not None:
                sequencer.clear_tilt_targets()
            # Issue #352: signal state_change so the upcoming refresh routes
            # through async_handle_state_change with the post-pipeline result.
            # The previous design dispatched coordinator.state BEFORE refresh,
            # sending the stale prior-cycle DefaultHandler value (e.g. 100 from
            # an out-of-window cycle) and then waiting up to a minute for the
            # periodic window-open transition to dispatch the correct solar
            # value. Routing through state_change keeps dispatch in one place
            # (the coordinator), aligned with weather/motion/window-open
            # callers that already use this idiom.
            self.coordinator.state_change = True
        await self.coordinator.async_refresh()
        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        self.coordinator.logger.debug("Turning off")
        if self._option_key is not None:
            await self._async_set_option(False)
            return
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
        await super().async_added_to_hass()

        if self._option_key is not None:
            # Option-backed switches derive is_on from config_entry.options, so
            # there is no RestoreEntity state to reconcile — persisted options
            # win. The coordinator listener (registered via super) applies any
            # runtime change.
            return

        last_state = await self.async_get_last_state()
        self.coordinator.logger.debug("%s: last state is %s", self._name, last_state)
        if (last_state is None and self._initial_state) or (
            last_state is not None and last_state.state == STATE_ON
        ):
            await self.async_turn_on(added=True)
        else:
            await self.async_turn_off(added=True)

    async def _async_set_option(self, value: bool) -> None:
        """Persist the option through the shared services path.

        The coordinator's update listener (``_async_update_listener``) maps the
        persisted change to its runtime action, so there is a single rebuild
        path whether the option is changed here, by a service, or in the
        options flow. The immediate ``schedule_update_ha_state`` redraws this
        switch from the freshly-persisted option without waiting for the
        listener-driven refresh.
        """
        assert self._option_key is not None
        patch = {self._option_key: value}
        sensor_type = self.config_entry.data.get(CONF_SENSOR_TYPE)
        validate_options_patch(patch, dict(self.config_entry.options), sensor_type)
        await apply_options_patch(self.hass, self.coordinator, patch)
        self.schedule_update_ha_state()
