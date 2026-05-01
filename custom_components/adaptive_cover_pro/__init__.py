"""The Adaptive Cover Pro integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_track_state_change_event,
)

from .const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_DEVICE_ID,
    CONF_END_ENTITY,
    CONF_ENTITIES,
    CONF_START_ENTITY,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_MOTION_SENSORS,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WINDOW_WIDTH,
    CUSTOM_POSITION_SLOTS,
    DOMAIN,
    _LOGGER,
)
from .coordinator import AdaptiveDataUpdateCoordinator
from .migrations import async_prune_legacy_entities
from .services import async_setup_services, async_unload_services

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BINARY_SENSOR, Platform.BUTTON]
CONF_SUN = ["sun.sun"]


async def async_initialize_integration(
    hass: HomeAssistant,
    config_entry: ConfigEntry | None = None,
) -> bool:
    """Initialize the integration."""

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Adaptive Cover Pro from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    await async_setup_services(hass)

    coordinator = AdaptiveDataUpdateCoordinator(hass)
    # Detect reload vs. cold HA boot so first-refresh can suppress non-safety
    # positioning commands when the user just saved options mid-day.
    coordinator._is_reload = hass.is_running
    _temp_entity = entry.options.get(CONF_TEMP_ENTITY)
    _presence_entity = entry.options.get(CONF_PRESENCE_ENTITY)
    _weather_entity = entry.options.get(CONF_WEATHER_ENTITY)
    _cover_entities = entry.options.get(CONF_ENTITIES, [])
    _start_time_entity = entry.options.get(CONF_START_ENTITY)
    _end_time_entity = entry.options.get(CONF_END_ENTITY)
    _force_override_sensors = entry.options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
    _motion_sensors = entry.options.get(CONF_MOTION_SENSORS, [])
    _cloud_coverage_entity = entry.options.get(CONF_CLOUD_COVERAGE_ENTITY)
    _lux_entity = entry.options.get(CONF_LUX_ENTITY)
    _irradiance_entity = entry.options.get(CONF_IRRADIANCE_ENTITY)
    _outside_temp_entity = entry.options.get(CONF_OUTSIDETEMP_ENTITY)
    _entities = ["sun.sun"]
    for entity in [
        _temp_entity,
        _presence_entity,
        _weather_entity,
        _start_time_entity,
        _end_time_entity,
        _cloud_coverage_entity,
        _lux_entity,
        _irradiance_entity,
        _outside_temp_entity,
    ]:
        if entity is not None:
            _entities.append(entity)

    # Add force override sensors to tracked entities
    if _force_override_sensors:
        _entities.extend(_force_override_sensors)

    # Add custom position sensors to tracked entities so the pipeline
    # re-evaluates immediately when a sensor turns on or off, rather
    # than waiting for the next periodic refresh or another entity change.
    for _slot_keys in CUSTOM_POSITION_SLOTS.values():
        _sensor = entry.options.get(_slot_keys["sensor"])
        if _sensor:
            _entities.append(_sensor)

    _LOGGER.debug("Setting up entry %s", entry.data.get("name"))

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            _entities,
            coordinator.async_check_entity_state_change,
        )
    )

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            _cover_entities,
            coordinator.async_check_cover_state_change,
        )
    )

    # Detect user-initiated cover.stop_cover for manual override on non-position-
    # capable covers (e.g. Somfy RTS) where pressing STOP triggers the "My"
    # preset but never reports a new position via state change.
    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_CALL_SERVICE,
            coordinator.async_check_cover_service_call,
        )
    )

    # Register motion sensor listeners separately (need custom handler for debouncing)
    if _motion_sensors:
        entry.async_on_unload(
            async_track_state_change_event(
                hass,
                _motion_sensors,
                coordinator.async_check_motion_state_change,
            )
        )

    # Register weather sensor listeners separately (need custom handler for clear-delay)
    _weather_sensor_ids: list[str] = []
    for _key in [
        CONF_WEATHER_WIND_SPEED_SENSOR,
        CONF_WEATHER_WIND_DIRECTION_SENSOR,
        CONF_WEATHER_RAIN_SENSOR,
        CONF_WEATHER_IS_RAINING_SENSOR,
        CONF_WEATHER_IS_WINDY_SENSOR,
    ]:
        _val = entry.options.get(_key)
        if _val:
            _weather_sensor_ids.append(_val)
    _weather_sensor_ids.extend(entry.options.get(CONF_WEATHER_SEVERE_SENSORS, []))

    if _weather_sensor_ids:
        entry.async_on_unload(
            async_track_state_change_event(
                hass,
                _weather_sensor_ids,
                coordinator.async_check_weather_state_change,
            )
        )

    # Register cleanup for cover command service reconciliation timer
    entry.async_on_unload(coordinator._cmd_svc.stop)

    # Store coordinator before platform setup so sensor async_added_to_hass can
    # access it during RestoreEntity rehydration (must run before first_refresh).
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Prune entity registry orphans left over from the v2.14.3 unique_id rename.
    # Runs before platform setup so orphans are removed before new entities register.
    await async_prune_legacy_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # First refresh runs after platform setup so that RestoreEntity hooks in
    # async_added_to_hass have already repopulated the manual-override manager
    # state before async_handle_first_refresh issues positioning commands.
    await coordinator.async_config_entry_first_refresh()
    coordinator._check_initial_motion_state()

    device_reg = dr.async_get(hass)

    if entry.options.get(CONF_DEVICE_ID):
        # Device association is active — remove the old standalone virtual device so it
        # doesn't appear as an orphaned entry under the integration.
        old_device = device_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if old_device:
            _LOGGER.debug(
                "Removing orphaned standalone device %s after device association",
                old_device.id,
            )
            device_reg.async_remove_device(old_device.id)
    else:
        # No device association — remove our config entry from any physical device that
        # still has it (left over from a previous association that was cleared).
        for device in list(device_reg.devices.values()):
            if (
                entry.entry_id in device.config_entries
                and (DOMAIN, entry.entry_id) not in device.identifiers
            ):
                _LOGGER.debug(
                    "Removing stale config entry link from physical device %s",
                    device.id,
                )
                device_reg.async_update_device(
                    device.id, remove_config_entry_id=entry.entry_id
                )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        await async_unload_services(hass)

    return unload_ok


# Fields that moved from centimetres to metres in config-entry version 2.
# Every legitimate cm value in the v1 UI was ≥ 10, so a stored value > 5
# is treated as cm and divided by 100. Values ≤ 5 are assumed to already be
# metres (hand-edited or re-migrated) and are left as-is — this keeps the
# migration idempotent.
_CM_TO_M_SENTINEL = 5.0
_GLARE_ZONE_DIMENSION_KEYS = tuple(
    f"glare_zone_{i}_{suffix}" for i in range(1, 5) for suffix in ("x", "y", "radius")
)


def _migrate_cm_to_m(value: float | int | None) -> float | None:
    """Convert a cm value to metres if it's large enough to be cm; else pass through."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value  # type: ignore[return-value]
    if abs(numeric) <= _CM_TO_M_SENTINEL:
        return numeric  # already metres (or effectively zero) — leave alone
    return round(numeric / 100.0, 2)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current schema version."""
    if entry.version >= 2:
        return True

    new_options = dict(entry.options)
    changed: list[str] = []

    for key in (CONF_WINDOW_WIDTH, *_GLARE_ZONE_DIMENSION_KEYS):
        if key not in new_options:
            continue
        original = new_options[key]
        migrated = _migrate_cm_to_m(original)
        if migrated != original:
            new_options[key] = migrated
            changed.append(key)

    if changed:
        _LOGGER.info(
            "Migrated %s from cm to metres (%s)",
            entry.data.get("name", entry.entry_id),
            ", ".join(changed),
        )

    hass.config_entries.async_update_entry(entry, options=new_options, version=2)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
