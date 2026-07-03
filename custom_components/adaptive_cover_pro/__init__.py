"""The Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_state_change_event,
    async_track_template_result,
)
from homeassistant.helpers.template import Template

from .const import (
    CONF_BUILDING_PROFILE_ID,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DEVICE_ID,
    CONF_ENABLE_MY_POSITION_ENTITIES,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_ENTITIES,
    CONF_START_ENTITY,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_MOTION_TEMPLATE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_RAINING_TEMPLATE,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WINDOW_WIDTH,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DIAG_CACHE_KEY,
    DOMAIN,
    _LOGGER,
)
from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator
from .cover_types import get_policy
from .group_coordinator import GroupCoordinator
from .helpers import (
    copy_legacy_slot_sensors_to_list,
    custom_position_slot_sensors,
    manual_override_input_entities,
    motion_entities,
)
from .profile_link import _copy_profile_to_cover, _covers_linked_to
from .templates import is_template_string
from .migrations import (
    async_prune_legacy_entities,
    async_prune_legacy_sensor_entities,
    async_prune_legacy_sensor_entities_v2,
)
from .services import async_setup_services, async_unload_services

PLATFORMS = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.NUMBER,
]
# Platform set for cover-group entries (``is_orchestrator = True``). A group
# exposes aggregate sensors, bulk switches, scene buttons, the scene select,
# and the opt-in aggregate cover — no number or binary sensor. Setup and
# unload must use the same list (load/unload symmetry, the #712/#714 lesson).
GROUP_PLATFORMS = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.COVER,
]
CONF_SUN = ["sun.sun"]


async def async_initialize_integration(
    hass: HomeAssistant,
    config_entry: ConfigEntry | None = None,
) -> bool:
    """Initialize the integration."""

    return True


def _register_template_tracker(
    hass: HomeAssistant,
    entry: AdaptiveConfigEntry,
    template_str: str | None,
    action: Callable,
    description: str,
) -> None:
    """Track one rendered template result, wiring teardown to the entry.

    Shared by the occupancy, custom-position, weather, and daytime-gate
    templates (issues #577/#563/#639/#632): tracking the rendered result gives
    a template-only override sensor-grade immediacy — the cover reacts the
    instant the template flips, with no companion binary sensor and no polling.
    Non-templates are skipped; render/parse failures are logged and skipped.
    """
    if not is_template_string(template_str):
        return
    try:
        _track_info = async_track_template_result(
            hass,
            [TrackTemplate(Template(template_str, hass), None)],
            action,
        )
    except (TemplateError, ValueError) as err:
        _LOGGER.warning(
            "%s failed to register (%r): %s", description, template_str, err
        )
    else:
        entry.async_on_unload(_track_info.async_remove)


async def async_setup_entry(hass: HomeAssistant, entry: AdaptiveConfigEntry) -> bool:
    """Set up Adaptive Cover Pro from a config entry."""

    await async_setup_services(hass)

    # Virtual entry types (the Building Profile) hold only shared building-level
    # sensor IDs — they register no platforms and build no coordinator. Filter on
    # the policy capability, never on the cover-type string, so the regression
    # guard stays unambiguous. Register a propagation update-listener so a change
    # to the profile reaches its linked covers, then return without forwarding
    # platforms.
    policy = get_policy(entry.data[CONF_SENSOR_TYPE])
    if not policy.controls_cover:
        entry.async_on_unload(entry.add_update_listener(_async_profile_propagate))
        return True

    # Cover groups (``is_orchestrator = True``) control covers but are not
    # geometry-driven: build the GroupCoordinator and forward only the group
    # platform set — never the sun/geometry coordinator below.
    if policy.is_orchestrator:
        group_coordinator = GroupCoordinator(hass, entry)
        entry.runtime_data = group_coordinator
        await hass.config_entries.async_forward_entry_setups(entry, GROUP_PLATFORMS)
        await group_coordinator.async_config_entry_first_refresh()
        entry.async_on_unload(entry.add_update_listener(_async_group_update_listener))
        return True

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
    _motion_sensors = motion_entities(entry.options)
    _manual_override_input_entities = manual_override_input_entities(entry.options)
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

    # Add custom position sensors to tracked entities so the pipeline
    # re-evaluates immediately when a sensor turns on or off, rather
    # than waiting for the next periodic refresh or another entity change.
    for _slot_keys in CUSTOM_POSITION_SLOTS.values():
        _entities.extend(custom_position_slot_sensors(entry.options, _slot_keys))

    # Add daytime gate sensors (issue #632) so flipping the gate OFF (dark)
    # triggers an immediate positioning cycle — same immediacy as lux/irradiance.
    _entities.extend(entry.options.get(CONF_DAYTIME_GATE_SENSORS, []))

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

    # Register input-sensor manual-override listeners separately (issue #688):
    # an off→on edge on one of these (e.g. a Shelly wall-switch input) engages
    # manual override on every cover in the instance. Dedicated handler, not the
    # motion debounce path.
    if _manual_override_input_entities:
        entry.async_on_unload(
            async_track_state_change_event(
                hass,
                _manual_override_input_entities,
                coordinator.async_check_manual_override_input_change,
            )
        )

    # Register the optional occupancy template (issue #577 follow-up). Tracking
    # the rendered result means the cover reacts the instant the template flips
    # truthy — same immediacy as a motion sensor, no polling. Re-registered on
    # every reload (options changes trigger a full reload).
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_MOTION_TEMPLATE),
        coordinator.async_check_motion_template_change,
        "Motion occupancy template",
    )

    # Register each custom-position slot's optional condition template (issue
    # #563). Same pattern as the occupancy template above: tracking the
    # rendered result gives sensor-grade immediacy when a template flips.
    for _slot_keys in CUSTOM_POSITION_SLOTS.values():
        _register_template_tracker(
            hass,
            entry,
            entry.options.get(_slot_keys["template"]),
            coordinator.async_check_custom_position_template_change,
            "Custom position template",
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

    # Register the optional is-raining / is-windy condition templates (issue
    # #639). Same pattern as the occupancy/custom-position templates above:
    # tracking the rendered result lets a template-only weather override engage
    # and react the instant the template flips, with no companion binary sensor.
    for _weather_template in [
        entry.options.get(CONF_WEATHER_IS_RAINING_TEMPLATE),
        entry.options.get(CONF_WEATHER_IS_WINDY_TEMPLATE),
    ]:
        _register_template_tracker(
            hass,
            entry,
            _weather_template,
            coordinator.async_check_weather_template_change,
            "Weather condition template",
        )

    # Register the optional daytime-gate template (issue #632). Tracking the
    # rendered result gives the gate the same sensor-grade immediacy as the
    # occupancy and weather templates — the cover repositions the instant the
    # template flips dark, with no polling.
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_DAYTIME_GATE_TEMPLATE),
        coordinator.async_check_daytime_gate_template_change,
        "Daytime gate template",
    )

    # Register cleanup for cover command service reconciliation timer
    entry.async_on_unload(coordinator._cmd_svc.stop)

    # Register cleanup for the periodic position-forecast recompute timer
    # (scheduled in async_config_entry_first_refresh — see issue #437). Wrap
    # in a closure because the unsub handle isn't created until after this
    # registration runs.
    def _cancel_forecast_timer() -> None:
        if coordinator._forecast_unsub is not None:
            coordinator._forecast_unsub()
            coordinator._forecast_unsub = None

    entry.async_on_unload(_cancel_forecast_timer)

    # Store coordinator before platform setup so sensor async_added_to_hass can
    # access it during RestoreEntity rehydration (must run before first_refresh).
    entry.runtime_data = coordinator

    # Prune entity registry orphans left over from past unique_id renames.
    # Runs before platform setup so orphans are removed before new entities register.
    await async_prune_legacy_entities(hass, entry)
    await async_prune_legacy_sensor_entities(hass, entry)
    await async_prune_legacy_sensor_entities_v2(hass, entry)

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


async def async_unload_entry(hass: HomeAssistant, entry: AdaptiveConfigEntry) -> bool:
    """Unload a config entry."""
    # Virtual entry types (Building Profile, controls_cover == False) forwarded
    # no platforms in async_setup_entry, so unloading platforms would raise
    # "Config entry was never loaded!". Mirror the setup short-circuit, and
    # unload exactly the platform set setup forwarded (groups use their own).
    policy = get_policy(entry.data[CONF_SENSOR_TYPE])
    if not policy.controls_cover:
        await async_unload_services(hass)
        return True
    platforms = GROUP_PLATFORMS if policy.is_orchestrator else PLATFORMS
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        await async_unload_services(hass)
    return unload_ok


async def _async_group_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a group entry when its options (membership) change.

    The group holds no long-running state worth preserving across an options
    edit, so a full reload is the simplest way to re-resolve the roster and
    rebuild the per-scene entities.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a config entry is removed.

    Q5 active sweep: when a deleted entry is a Building Profile (virtual,
    ``controls_cover == False``), strip the dangling ``CONF_BUILDING_PROFILE_ID``
    from every cover still linked to it so their profile pickers re-expose on the
    next options view. The last-copied sensor IDs are deliberately left in place
    so the covers keep functioning. Removing a real cover does nothing here.

    Also drops the entry's last-good diagnostics snapshot from the hass.data cache
    (written each update cycle, kept across reloads) so it does not leak.
    """
    hass.data.get(DIAG_CACHE_KEY, {}).pop(entry.entry_id, None)
    if get_policy(entry.data.get(CONF_SENSOR_TYPE)).controls_cover:
        return
    for cover in _covers_linked_to(hass, entry):
        hass.config_entries.async_update_entry(
            cover,
            options={
                k: v for k, v in cover.options.items() if k != CONF_BUILDING_PROFILE_ID
            },
        )


# Fields that moved from centimetres to metres in config-entry version 2.
# Every legitimate cm value in the v1 UI was ≥ 10, so a stored value > 5
# is treated as cm and divided by 100. Values ≤ 5 are assumed to already be
# metres (hand-edited or re-migrated) and are left as-is — this keeps the
# migration idempotent.
_CM_TO_M_SENTINEL = 5.0
_GLARE_ZONE_DIMENSION_KEYS = tuple(
    f"glare_zone_{i}_{suffix}"
    for i in range(1, 5)
    for suffix in ("x", "y", "radius", "z")
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


def _merge_force_override_into_slot_5(options: dict) -> bool:
    """Copy legacy force-override config into custom-position slot 5 (issue #563).

    Additive on purpose: the legacy ``force_override_*`` keys are left
    untouched so a rollback to the previous integration version restores the
    exact pre-merge behavior (the old ForceOverrideHandler reads them; slot-5
    keys are invisible to old code, which only iterates slots 1–4).

    Returns True when slot 5 was written.
    """
    sensors = options.get(CONF_FORCE_OVERRIDE_SENSORS) or []
    if not sensors:
        return False  # nothing configured (absent OR empty list) — slot 5 stays free
    slot5 = CUSTOM_POSITION_SLOTS[5]
    options[slot5["sensors"]] = list(sensors)
    options[slot5["position"]] = int(options.get(CONF_FORCE_OVERRIDE_POSITION) or 0)
    options[slot5["priority"]] = CUSTOM_POSITION_SAFETY_PRIORITY
    options[slot5["min_mode"]] = bool(options.get(CONF_FORCE_OVERRIDE_MIN_MODE, False))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current schema version."""
    new_options = dict(entry.options)
    new_version = entry.version
    new_minor = entry.minor_version

    # v1 → v2: convert window/glare-zone dimensions from cm to metres.
    if new_version < 2:
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
        new_version = 2

    # v2 → v3: enable the My-preset entities by default for every pre-existing
    # entry so the upgrade is invisible to users who already rely on the
    # "Managed My Position" button and value entity. New installs created on
    # v3 onwards default to False via the config-flow schema.
    if new_version < 3:
        new_options.setdefault(CONF_ENABLE_MY_POSITION_ENTITIES, True)
        new_version = 3

    # v3.1 → v3.2: merge the standalone force-override feature into
    # custom-position slot 5 at safety priority (issue #563). A MINOR bump on
    # purpose — HA lets older code load entries with a higher minor version,
    # and the copy is additive, so a rollback to the previous release keeps a
    # fully functioning force override.
    if new_version == 3 and new_minor < 2:
        if _merge_force_override_into_slot_5(new_options):
            _LOGGER.info(
                "Migrated force override config of %s into custom-position slot 5",
                entry.data.get("name", entry.entry_id),
            )
        new_minor = 2

    # v3.2 → v3.3: copy each legacy custom_position_sensor_N single-sensor key
    # into the new custom_position_sensors_N list key so pre-multi-sensor entries
    # prefill the options-flow multi-select correctly (issue #563 trailing defect).
    # Additive + rollback-safe: legacy keys are left intact.
    if new_version == 3 and new_minor < 3:
        if copy_legacy_slot_sensors_to_list(new_options):
            _LOGGER.info(
                "Migrated legacy single-sensor keys of %s into list keys",
                entry.data.get("name", entry.entry_id),
            )
        new_minor = 3

    # v3.3 → v3.4: enable position matching by default for every pre-existing
    # entry so upgrading covers keep the old reconcile/chase behavior instead of
    # silently flipping to the new command-once default (issue #591, #606). New
    # installs created on v3.4 onwards default to False via the config-flow
    # schema. Additive + rollback-safe: the key is only filled when absent.
    if new_version == 3 and new_minor < 4:
        new_options.setdefault(CONF_ENABLE_POSITION_MATCHING, True)
        new_minor = 4

    # v3.4 → v3.5: previously seeded the now-removed weather-retraction
    # visibility toggle (CONF_SHOW_WEATHER_RETRACTION). The toggle is gone (the
    # retraction pickers are always shown), so this is a no-op minor bump kept
    # only to advance entries sitting at minor 4 to 5 — without it they would
    # stay below MINOR_VERSION and re-trigger migration every restart.
    if new_version == 3 and new_minor < 5:
        new_minor = 5

    # v3.5 → v3.6: enable the weather override by default for every pre-existing
    # entry so upgrading covers keep firing weather safety overrides (issue
    # #719). New installs default OFF via the config-flow schema. Additive +
    # rollback-safe: the key is only filled when absent.
    if new_version == 3 and new_minor < 6:
        new_options.setdefault(CONF_WEATHER_ENABLED, True)
        new_minor = 6

    hass.config_entries.async_update_entry(
        entry, options=new_options, version=new_version, minor_version=new_minor
    )
    return True


# Option keys that a live coordinator can apply without a full reload, mapped
# to the coordinator coroutine that applies them. When *every* changed option
# key is in this map the listener applies them in place (rebuilds the pipeline,
# no reload); any other changed key forces a full reload so all listeners and
# pipeline handlers pick up the new values. This is the single rebuild path —
# the option-backed switches only persist the value and rely on the listener.
_RUNTIME_APPLICABLE_OPTIONS: dict[str, str] = {
    CONF_ENABLE_SUN_TRACKING: "async_apply_sun_tracking_update",
}


async def _async_profile_propagate(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Propagate a Building Profile's sensor changes to its linked covers.

    Registered as the update listener for virtual ``building_profile`` entries
    (which build no coordinator). Re-copies the profile's non-empty shared-sensor
    subset into every linked cover via the shared copier — the ``async_update_entry``
    it performs fires each cover's self-reload listener, so linked covers pick up
    the changed sensor IDs immediately.
    """
    # Guard: only profiles (virtual, controls_cover == False) propagate. A real
    # cover reaching here would be a wiring bug — its own listener handles reloads.
    if get_policy(entry.data.get(CONF_SENSOR_TYPE)).controls_cover:
        return
    for cover in _covers_linked_to(hass, entry):
        _copy_profile_to_cover(hass, entry, cover)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    coordinator = entry.runtime_data
    previous_options = coordinator._cached_options
    if previous_options is not None:
        current_options = dict(entry.options)
        previous_options = dict(previous_options)
        changed_keys = {
            key
            for key in current_options.keys() | previous_options.keys()
            if current_options.get(key) != previous_options.get(key)
        }

        if not changed_keys:
            return
        if changed_keys.issubset(_RUNTIME_APPLICABLE_OPTIONS):
            for apply_name in {
                _RUNTIME_APPLICABLE_OPTIONS[key] for key in changed_keys
            }:
                await getattr(coordinator, apply_name)()
            return

    await hass.config_entries.async_reload(entry.entry_id)
