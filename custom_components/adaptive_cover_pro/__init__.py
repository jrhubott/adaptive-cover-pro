"""The Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_state_change_event,
    async_track_template_result,
)
from homeassistant.helpers.template import Template

from .const import (
    BLIND_SPOT_SLOTS,
    DEFAULT_AWNING_SHADE_MODE,
    GLARE_ZONE_SLOT_NUMBERS,
    CONF_AWNING_SHADE_MODE,
    CONF_BUILDING_PROFILE_ID,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_COMMAND_QUEUE_GAP,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_SUN_TRACKING_GATE_SENSORS,
    CONF_SUN_TRACKING_GATE_TEMPLATE,
    CONF_DEFAULT_HEIGHT,
    CONF_DEVICE_ID,
    CONF_ENABLE_MY_POSITION_ENTITIES,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_IRRADIANCE_ENTITY,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_MANUAL_OVERRIDE_INPUT_TEMPLATE,
    CONF_MOTION_TEMPLATE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_TEMP_ENTITY,
    CONF_TILT_SAFETY_MARGIN,
    CONF_TRAVEL_TIME_CALIBRATION,
    CONF_VENETIAN_TILT_SAFETY_MARGIN,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_RAINING_TEMPLATE,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_SEVERE_TEMPLATE,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WINDOW_WIDTH,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_COMMAND_QUEUE_GAP,
    DIAG_CACHE_KEY,
    DOMAIN,
    POSITION_CLOSED,
    TIME_STRING_RE,
    _LOGGER,
    blind_spot_legacy_to_gamma,
    clamp_gamma_pair,
    resolve_fov_left,
    resolve_fov_right,
)
from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator
from .cover_types import get_policy
from .managers.cover_command.queue import get_command_queue, normalize_queue_name
from .group_coordinator import GroupCoordinator
from .helpers import (
    copy_legacy_slot_sensors_to_list,
    custom_position_slot_sensors,
    manual_override_input_entities,
    motion_entities,
    normalize_time_string,
)
from .profile_link import _copy_profile_to_cover, _covers_linked_to
from .templates import (
    build_acp_template_variables,
    is_template_string,
    uses_acp_namespace,
)
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


#: Seconds. The floor on how often one tracked template that references the
#: ``acp`` namespace may drive a coordinator refresh (issue #1159).
ACP_NAMESPACE_REFRESH_COOLDOWN: float = 1.0


def _coalesce_namespace_refreshes(
    hass: HomeAssistant,
    entry: AdaptiveConfigEntry,
    action: Callable,
    description: str,
) -> Callable:
    """Wrap a self-referencing template's tracker action in a leading throttle.

    **Do not "simplify" this back to ``TrackTemplate.rate_limit``.** That field
    cannot do this job: HA's ``_rate_limit_for_event``
    (``homeassistant/helpers/event.py``) starts with *"Specifically referenced
    entities are excluded from the rate limit"* and returns ``None`` whenever
    the entity that changed is in ``RenderInfo.entities`` — and
    ``is_state(acp.control_status, …)`` puts exactly that entity_id there. A
    ``rate_limit`` on these trackers is dead weight; six flips inside one second
    still invoke the action six times.

    So the guard sits one step later, on the action the tracker invokes — which
    is where the unbounded work actually is, because every one of these actions
    ends in ``coordinator.async_refresh()`` and the coordinator has no
    debouncer. HA's :class:`~homeassistant.helpers.debounce.Debouncer` with
    ``immediate=True`` is a leading-edge throttle: the first flip after a quiet
    ``ACP_NAMESPACE_REFRESH_COOLDOWN`` runs straight away, so a self-reference
    keeps the same sensor-grade immediacy as any other template; flips inside
    that window collapse into a single **trailing** run once it closes, so
    nothing is lost and the ``template → refresh → ACP entity writes → template``
    cycle settles to one refresh per second instead of spinning.

    Only namespace templates are wrapped. A plain template cannot be driven by
    ACP's own output, and its unthrottled immediacy is the contract shipped
    across #577/#563/#639/#632/#974.

    Both tracker arguments are dropped rather than queued for replay. Every one
    of the five actions this wraps ignores ``event`` and ``updates`` and re-reads
    live state instead — the tracked result only signals *that* a template
    changed — so a coalesced run has nothing to carry forward and calls the
    action with the same empty signal a fresh render would give it.
    """

    async def _run_action() -> None:
        await action(None, [])

    debouncer = Debouncer(
        hass,
        _LOGGER,
        cooldown=ACP_NAMESPACE_REFRESH_COOLDOWN,
        immediate=True,
        function=_run_action,
    )
    # Cancels the pending trailing timer, so an unload can never leave one
    # scheduled against a torn-down coordinator. Guarded by
    # ``test_unload_cancels_the_coalescer_pending_trailing_run`` — the
    # integration-marked suite tolerates lingering timers, so nothing else
    # would notice this line going missing.
    entry.async_on_unload(debouncer.async_shutdown)
    _LOGGER.debug(
        "%s references the acp namespace — coalescing its refreshes to %ss",
        description,
        ACP_NAMESPACE_REFRESH_COOLDOWN,
    )

    async def _coalesced(_event, _updates) -> None:
        await debouncer.async_call()

    return _coalesced


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

    Every tracker gets the same ``acp`` render context the managers use (#1159),
    built from the one factory so the tracker's render and the cycle render can
    never disagree. A template that actually *uses* the namespace additionally
    gets its refreshes coalesced — see :func:`_coalesce_namespace_refreshes` for
    why the guard lives on the action and not on ``TrackTemplate.rate_limit``.
    """
    if not is_template_string(template_str):
        return
    if uses_acp_namespace(template_str):
        action = _coalesce_namespace_refreshes(hass, entry, action, description)
    try:
        _track_info = async_track_template_result(
            hass,
            [
                TrackTemplate(
                    Template(template_str, hass),
                    build_acp_template_variables(hass, entry.entry_id),
                )
            ],
            action,
        )
    except (TemplateError, ValueError) as err:
        _LOGGER.warning(
            "%s failed to register (%r): %s", description, template_str, err
        )
    else:
        entry.async_on_unload(_track_info.async_remove)


def _register_option_template_trackers(
    hass: HomeAssistant,
    entry: AdaptiveConfigEntry,
    coordinator: AdaptiveDataUpdateCoordinator,
) -> None:
    """Register every option-template result tracker for this entry.

    Called *after* ``async_forward_entry_setups`` on purpose (issue #1159). HA
    blocks entry setup on entity addition, so by this point this instance's own
    entities have entity-registry rows and a template written against the
    ``acp`` namespace resolves to a real entity_id on its **first** render —
    the render whose ``RenderInfo`` fixes the tracker's listener set for the
    life of the setup (HA ``helpers/event.py``). Registering before platform
    forwarding left a self-reference with no listeners at all on a first-ever
    setup, until the next reload.

    Teardown is unchanged: each ``_register_template_tracker`` call wires its
    own ``entry.async_on_unload``.
    """
    # The optional manual-override input template (issue #974). The template
    # counterpart to the input sensors: tracking the rendered result engages
    # manual override the instant the template flips truthy, with sensor-grade
    # immediacy and no polling.
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_MANUAL_OVERRIDE_INPUT_TEMPLATE),
        coordinator.async_check_manual_override_input_template_change,
        "Manual override input template",
    )

    # The optional occupancy template (issue #577 follow-up). Tracking the
    # rendered result means the cover reacts the instant the template flips
    # truthy — same immediacy as a motion sensor, no polling. Re-registered on
    # every reload (options changes trigger a full reload).
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_MOTION_TEMPLATE),
        coordinator.async_check_motion_template_change,
        "Motion occupancy template",
    )

    # Each custom-position slot's optional condition template (issue #563).
    # Same pattern as the occupancy template above: tracking the rendered
    # result gives sensor-grade immediacy when a template flips.
    for _slot_keys in CUSTOM_POSITION_SLOTS.values():
        _register_template_tracker(
            hass,
            entry,
            entry.options.get(_slot_keys["template"]),
            coordinator.async_check_custom_position_template_change,
            "Custom position template",
        )

    # The optional is-raining / is-windy / severe condition templates (issue
    # #639). Tracking the rendered result lets a template-only weather override
    # engage and react the instant the template flips, with no companion binary
    # sensor.
    for _weather_template in [
        entry.options.get(CONF_WEATHER_IS_RAINING_TEMPLATE),
        entry.options.get(CONF_WEATHER_IS_WINDY_TEMPLATE),
        entry.options.get(CONF_WEATHER_SEVERE_TEMPLATE),
    ]:
        _register_template_tracker(
            hass,
            entry,
            _weather_template,
            coordinator.async_check_weather_template_change,
            "Weather condition template",
        )

    # The optional daytime-gate template (issue #632). Tracking the rendered
    # result gives the gate the same sensor-grade immediacy as the occupancy and
    # weather templates — the cover repositions the instant the template flips
    # dark, with no polling.
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_DAYTIME_GATE_TEMPLATE),
        coordinator.async_check_daytime_gate_template_change,
        "Daytime gate template",
    )

    # The optional sun-tracking gate template (issue #1167). Same immediacy
    # contract as the daytime gate above.
    _register_template_tracker(
        hass,
        entry,
        entry.options.get(CONF_SUN_TRACKING_GATE_TEMPLATE),
        coordinator.async_check_sun_tracking_gate_template_change,
        "Sun tracking gate template",
    )


def _command_queue_for_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Return the shared queue a Command Queue entry owns, or ``None``.

    Identity is the entry's CREATION-TIME name (``entry.data["name"]``),
    normalized — never its ``entry_id``. That is what makes a name usable
    before, and after, any entry exists to own it: a cover naming a queue
    nobody created still serializes at the default gap, and deleting the entry
    reverts its members to that default rather than breaking them.
    """
    name = normalize_queue_name(entry.data.get("name"))
    if not name:
        return None
    return get_command_queue(hass, name)


def _command_queue_gap(entry: ConfigEntry) -> float:
    """Return the gap this queue entry configures, defaulted from one constant."""
    return entry.options.get(CONF_COMMAND_QUEUE_GAP, DEFAULT_COMMAND_QUEUE_GAP)


def _setup_command_queue_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Bind a Command Queue entry's gap onto the shared queue (issue #1189).

    No platforms, no coordinator: the entry exists only to own a gap and give
    the queue a place in the UI. It attaches to the queue like any member, so a
    queue whose covers are all unloaded still survives while its own entry is
    loaded — and disappears when nothing references it at all.
    """
    queue = _command_queue_for_entry(hass, entry)
    if queue is None:
        return
    queue.attach()
    queue.set_gap(_command_queue_gap(entry))
    entry.async_on_unload(entry.add_update_listener(_async_command_queue_update))

    def _release() -> None:
        # Reverting the gap before detaching matters: members may still hold
        # references, and they must fall back to the default rather than keep
        # the gap of an entry that is gone.
        queue.set_gap(None)
        queue.detach()

    entry.async_on_unload(_release)


async def _async_command_queue_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-apply a Command Queue entry's gap in place — members are NOT reloaded.

    Members read ``queue.gap_seconds`` live at release time rather than copying
    it at setup, so editing the gap takes effect on the very next transmission
    without tearing down every cover on the queue. Reloading them would be the
    obvious implementation and the wrong one: on the 16-instance install this
    feature was built for it would restart sixteen coordinators to change one
    number.
    """
    queue = _command_queue_for_entry(hass, entry)
    if queue is not None:
        queue.set_gap(_command_queue_gap(entry))


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
        # Two virtual entry types share ``controls_cover == False``, and they
        # want opposite things from setup, so the second capability flag splits
        # them (never the cover-type string).
        if policy.is_command_queue:
            _setup_command_queue_entry(hass, entry)
            return True
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

    # Add sun-tracking gate sensors (issue #1167) for the same reason: flipping
    # the gate closed must suppress solar on the next cycle, not whenever some
    # other entity happens to change.
    _entities.extend(entry.options.get(CONF_SUN_TRACKING_GATE_SENSORS, []))

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

    # Register cleanup for the health-check debounce timers (issues #786, #975).
    # Their shutdown lives only inside coordinator.async_shutdown, which is not
    # wired to unload, so an unhealthy condition (e.g. sun.sun missing) would
    # otherwise leak a 900s TimeoutController task across every reload/unload.
    entry.async_on_unload(coordinator._sensor_health.shutdown)
    entry.async_on_unload(coordinator._repair.shutdown)

    # Register cleanup for the periodic position-forecast recompute timer
    # (scheduled in async_config_entry_first_refresh — see issue #437). Wrap
    # in a closure because the unsub handle isn't created until after this
    # registration runs.
    def _cancel_forecast_timer() -> None:
        if coordinator._forecast_unsub is not None:
            coordinator._forecast_unsub()
            coordinator._forecast_unsub = None

    entry.async_on_unload(_cancel_forecast_timer)

    # Prime the instance-language reason-template overlay once (issue #882),
    # mirroring the summary_i18n priming: resolve the language the same way
    # (the HA instance language, #905 semantics) and offload the bundle file
    # read to the executor so no JSON I/O runs on the event loop. The resolved
    # mapping is cached on the coordinator, threaded into the DiagnosticContext,
    # and read by sensor.py to localize decision-trace reason strings.
    from .reason_i18n import async_prime as _async_prime_reason_labels

    coordinator._reason_labels = await _async_prime_reason_labels(
        hass, hass.config.language or "en"
    )

    # Store coordinator before platform setup so sensor async_added_to_hass can
    # access it during RestoreEntity rehydration (must run before first_refresh).
    entry.runtime_data = coordinator

    # Prune entity registry orphans left over from past unique_id renames.
    # Runs before platform setup so orphans are removed before new entities register.
    await async_prune_legacy_entities(hass, entry)
    await async_prune_legacy_sensor_entities(hass, entry)
    await async_prune_legacy_sensor_entities_v2(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Option-template result trackers register here, after platform forwarding,
    # so this instance's own entities are already in the entity registry and an
    # ``acp`` self-reference resolves on the first render — the render that
    # fixes the tracker's listener set (issue #1159).
    _register_option_template_trackers(hass, entry, coordinator)

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

    A deleted Command Queue entry gets NO sweep at all (issue #1189): its
    members reference it by name, not by ``entry_id``, and a dangling name is a
    working configuration — the covers keep serializing against each other at
    the default gap. Stripping the assignment would silently un-serialize a
    radio the user is still sharing, which is the original bug.
    """
    hass.data.get(DIAG_CACHE_KEY, {}).pop(entry.entry_id, None)
    policy = get_policy(entry.data.get(CONF_SENSOR_TYPE))
    if policy.controls_cover or policy.is_command_queue:
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
    for i in GLARE_ZONE_SLOT_NUMBERS
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


def _seed_signed_gamma_blind_spots(options: dict) -> bool:
    """Seed signed-gamma blind-spot keys from the legacy edges (issue #247).

    For every slot whose BOTH legacy FOV-relative edges are present, compute the
    signed-gamma pair via the shared ``blind_spot_legacy_to_gamma`` helper (so
    migration and the runtime fallback in ``CoverConfig.from_options`` can never
    diverge) and ``setdefault`` it into the new keys, clamped to the signed
    bounds ``[-fov_right, fov_left]`` / ``[-fov_left, fov_right]``.

    Additive on purpose: the legacy ``blind_spot_*`` keys are left untouched so a
    rollback to the previous integration version keeps reading its exact config
    (old code ignores the unknown gamma keys). Returns True when any key seeded.
    """
    fov_left = resolve_fov_left(options)
    fov_right = resolve_fov_right(options)
    seeded = False
    for keys in BLIND_SPOT_SLOTS.values():
        old_left = options.get(keys["left"])
        old_right = options.get(keys["right"])
        if old_left is None or old_right is None:
            continue  # slot inactive on the legacy path — nothing to convert
        new_left, new_right = blind_spot_legacy_to_gamma(fov_left, old_left, old_right)
        new_left, new_right = clamp_gamma_pair(new_left, new_right, fov_left, fov_right)
        if keys["left_gamma"] not in options:
            options[keys["left_gamma"]] = new_left
            seeded = True
        if keys["right_gamma"] not in options:
            options[keys["right_gamma"]] = new_right
            seeded = True
    return seeded


def _repair_malformed_times(options: dict) -> list[str]:
    """Rewrite non-canonical start/end times to ``HH:MM:SS`` (issue #1049).

    A value that parses is canonicalised. A value that does **not** parse —
    ``"25:00:00"``, ``"garbage"`` — is dropped rather than left alone: the old
    ``set_options`` regex accepted shape-valid impossible clock times and import
    validated no time at all, so both are reachable in stored options, and
    ``get_datetime_from_str`` runs ``dateutil.parser.parse`` on them with no
    guard — raising on every coordinator cycle. Dropping the key is the #492
    "no time set" state, which is what an unusable window bound already means.

    Returns a description of each change, for the migration log. See the
    v3.11 → v3.12 block in ``async_migrate_entry`` for why this one migration
    rewrites in place and why that stays rollback-safe.
    """
    changes: list[str] = []
    for key in (CONF_START_TIME, CONF_END_TIME):
        if key not in options:
            continue
        original = options[key]
        if original is None or TIME_STRING_RE.match(str(original)):
            continue  # absent-equivalent or already canonical
        canonical = normalize_time_string(original)
        if TIME_STRING_RE.match(str(canonical)):
            options[key] = canonical
            changes.append(f"{key}: {original!r} → {canonical!r}")
        else:
            del options[key]
            changes.append(f"{key}: {original!r} → unset (unparsable)")
    return changes


def _seed_default_position(sensor_type: str | None, options: dict) -> bool:
    """Seed default_percentage from the policy's no-coverage endpoint (issue #1126).

    The minimal create wizard (#945 Part 2) has no position step, so an entry
    created since then never got ``default_percentage`` written — every
    runtime read then falls back to a hard-coded 0, driving the cover fully
    closed until a user opens Options -> Position and saves. Gated on
    ``controls_cover and not is_orchestrator`` — the same gate the create
    finalizer uses — because a Building Profile or Group policy has no axes
    and ``position_for_intent`` would raise ``IndexError``.

    ``setdefault``-shaped: a no-op when the key is already present. Returns
    whether the key was seeded, for the migration log.

    ``sensor_type`` can be ``None``, ``""``, or any string that was never
    registered — a malformed or pre-#1126-window entry, or simply a value
    this migration has no opinion on. ``get_policy`` raises ``ValueError``
    for all of those; that must not propagate out of this function (and
    therefore out of ``async_migrate_entry``), or it parks the whole entry in
    ``ConfigEntryState.MIGRATION_ERROR`` and discards every other repair in
    the same migration cascade.
    """
    if CONF_DEFAULT_HEIGHT in options:
        return False
    try:
        policy = get_policy(sensor_type)
    except ValueError:
        return False
    if not (policy.controls_cover and not policy.is_orchestrator):
        return False
    options[CONF_DEFAULT_HEIGHT] = policy.position_for_intent(sun_through=True)
    return True


def _seed_default_position_and_log(entry: ConfigEntry, options: dict) -> None:
    """Seed default_percentage and log when it was actually seeded (issue #1126).

    Wraps ``_seed_default_position`` so the v3.12 → v3.13 block in
    ``async_migrate_entry`` stays a single call — matching every sibling
    repair's shape — instead of adding its own conditional to an already-long
    linear migration cascade. Unlike a silent ``setdefault``, it must leave a
    log line pointing at the entry, its cover type, and the value seeded —
    the same pattern every other gated repair in the cascade already follows.

    The pre-fix runtime fallback for a key-less entry was a hard-coded
    literal 0 (``POSITION_CLOSED``). For most types (blind/tilt/venetian,
    ``open_blocks_sun=False``) the seeded no-coverage endpoint is 100, so this
    genuinely is the riskiest repair in the cascade — it moves an
    already-bitten cover from effectively fully closed to fully open. For the
    ``open_blocks_sun=True`` types (awning, oscillating awning) the
    no-coverage endpoint IS 0 — identical to the pre-fix fallback — so the
    key gets written but nothing actually moves. The message distinguishes
    the two rather than asserting "was silently fully closed" for a cover
    that never moved.
    """
    if not _seed_default_position(entry.data.get(CONF_SENSOR_TYPE), options):
        return
    seeded = options.get(CONF_DEFAULT_HEIGHT)
    outcome = (
        "was silently kept fully closed until this migration ran"
        if seeded != POSITION_CLOSED
        else "matches the pre-fix runtime fallback, so this cover did not move"
    )
    _LOGGER.info(
        "Seeded default position of %s (%s) to %s%% — %s",
        entry.data.get("name", entry.entry_id),
        entry.data.get(CONF_SENSOR_TYPE),
        seeded,
        outcome,
    )


def _advance_noop_minor(version: int, minor: int, target: int) -> int:
    """Advance a stale minor past an ADDITIVE option that needs no seeding.

    Several schema additions store a key whose ABSENCE already reads as the
    default, so there is nothing for migration to write — but the minor still
    has to advance, or HA sees the entry as stale and re-runs the whole cascade
    on every restart. Four blocks in ``async_migrate_entry`` are exactly that
    bump and nothing else (v3.4→v3.5, v3.6→v3.7, v3.8→v3.9, v3.13→v3.14), so
    the condition-and-assignment is stated once here and called four times
    rather than copied (CODING_GUIDELINES.md "No Code Duplication"). Each call
    site keeps its own comment explaining which option it is advancing past.

    Returns the new minor, unchanged when the entry is already at or past
    ``target`` — which is what makes every call idempotent.
    """
    return target if version == 3 and minor < target else minor


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
    new_minor = _advance_noop_minor(new_version, new_minor, 5)

    # v3.5 → v3.6: enable the weather override by default for every pre-existing
    # entry so upgrading covers keep firing weather safety overrides (issue
    # #719). New installs default OFF via the config-flow schema. Additive +
    # rollback-safe: the key is only filled when absent.
    if new_version == 3 and new_minor < 6:
        new_options.setdefault(CONF_WEATHER_ENABLED, True)
        new_minor = 6

    # v3.6 → v3.7: added the additive outside_temp_source option (issue #547).
    # An absent key already reads as "live" (the default), so nothing needs
    # seeding — this is a no-op minor bump kept only to advance entries sitting
    # at minor 6 to 7 so they stop re-triggering migration every restart.
    new_minor = _advance_noop_minor(new_version, new_minor, 7)

    # v3.7 → v3.8: convert legacy FOV-relative blind-spot edges to signed gamma
    # from the window normal (issue #247). Additive + rollback-safe: the new
    # ``blind_spot_*_gamma`` keys are setdefault-seeded from the untouched legacy
    # edges via the shared conversion helper; a rollback keeps reading the legacy
    # keys. New installs write the gamma keys directly via the config flow.
    if new_version == 3 and new_minor < 8:
        if _seed_signed_gamma_blind_spots(new_options):
            _LOGGER.info(
                "Migrated blind-spot edges of %s to signed gamma from the window normal",
                entry.data.get("name", entry.entry_id),
            )
        new_minor = 8

    # v3.8 → v3.9: added the additive per-slot axis-constraint options —
    # custom_position_position_max_N / _tilt_min_N / _tilt_max_N (issue #943).
    # An absent key already reads as "constraint off", so nothing needs seeding
    # — this is a no-op minor bump kept only to advance entries sitting at minor
    # 8 to 9 so they stop re-triggering migration every restart (the v3.6 → v3.7
    # precedent). Rollback-safe: min_mode / tilt_only remain the stored wire
    # format and are untouched, so an older build finds its config exactly as it
    # left it and simply ignores the new keys.
    new_minor = _advance_noop_minor(new_version, new_minor, 9)

    # v3.9 → v3.10: the tilt safety margin was renamed from the venetian-prefixed
    # key to the neutral CONF_TILT_SAFETY_MARGIN now that tilt-only and
    # louvered-roof covers share it (issue #964). Additive + rollback-safe: copy
    # the legacy value into the new key when present; the old key is left
    # untouched so an older build still reads its exact config, and the
    # configuration-service read falls back to the old key regardless.
    if new_version == 3 and new_minor < 10:
        if CONF_VENETIAN_TILT_SAFETY_MARGIN in new_options:
            new_options.setdefault(
                CONF_TILT_SAFETY_MARGIN,
                new_options[CONF_VENETIAN_TILT_SAFETY_MARGIN],
            )
        new_minor = 10

    # v3.10 → v3.11: added the awning shade-mode option (issue #1025). Seed the
    # window-glass default ONLY for fixed-awning entries — detected by the
    # awning-only geometry key CONF_LENGTH_AWNING, mirroring the key-presence gate
    # of the v3.9→v3.10 block — so non-awning entries stay untouched. Additive +
    # rollback-safe: an absent key already reads as "window" via the configuration
    # service, and an older build simply ignores the key. setdefault-only.
    if new_version == 3 and new_minor < 11:
        if CONF_LENGTH_AWNING in new_options:
            new_options.setdefault(CONF_AWNING_SHADE_MODE, DEFAULT_AWNING_SHADE_MODE)
        new_minor = 11

    # v3.11 → v3.12: repair start_time/end_time already stored in a non-canonical
    # shape (issue #1049). Validating the write paths stops new bad values but
    # leaves an already-bitten entry with e.g. "00:00", which fails every literal
    # BLANK_TIME comparison while TimeWindowManager resolves it to tomorrow's
    # midnight — the until_window_end deadline recedes a day at every local
    # midnight and the override never expires.
    #
    # This is the rare migration that rewrites an existing key rather than only
    # seeding one, so the rollback contract (CLAUDE.md § "Rollback-Safe Config
    # Migrations") deserves an explicit answer: the rewrite is a repair *into*
    # the format every release — old and new — already expects. An older build
    # reading the canonical "00:00:00" applies the unset semantics it always
    # intended; before the repair it read a phantom configured window end. So a
    # rollback is strictly better off, never worse, and no key is renamed.
    # A value no parser can rescue is dropped instead — see
    # ``_repair_malformed_times`` for why leaving it is not the safe option.
    if new_version == 3 and new_minor < 12:
        if repaired := _repair_malformed_times(new_options):
            _LOGGER.info(
                "Repaired malformed time options of %s (%s)",
                entry.data.get("name", entry.entry_id),
                ", ".join(repaired),
            )
        new_minor = 12

    # v3.12 → v3.13: seed default_percentage for entries the minimal create
    # wizard left key-less (issue #1126). Backfill the policy's no-coverage
    # endpoint (100 for most cover types, 0 for the polarity-flipped awning
    # types) so an already-bitten entry is repaired on upgrade instead of
    # staying fully closed forever. See ``_seed_default_position`` for the
    # additive/setdefault-shaped, gated details.
    if new_version == 3 and new_minor < 13:
        _seed_default_position_and_log(entry, new_options)
        new_minor = 13

    # v3.13 → v3.14: added the additive day_night_concurrent_rail_travel option
    # (issue #1140). An absent key reads as the default, so nothing needs
    # seeding; this is a no-op minor bump kept only to advance entries sitting
    # at minor 13 to 14 so they stop re-triggering migration every restart (the
    # v3.6 → v3.7 precedent). Rollback-safe: an older build finds every key
    # exactly as it left it and ignores the one it doesn't know.
    #
    # That default has since flipped to OFF, and this block is deliberately
    # still a no-op. Seeding the old ON for existing entries would preserve
    # concurrent travel exactly where it is most likely to be wrong — installs
    # that never chose it and whose hardware nobody has checked. Leaving the key
    # absent moves them onto the conservative behaviour, which is the point of
    # the flip. Anyone who set it explicitly keeps their choice either way.
    new_minor = _advance_noop_minor(new_version, new_minor, 14)

    # v3.14 → v3.15: added the additive day_night_external_command_interlock
    # option (issue #1138). An absent key already reads as "on" — the default —
    # so nothing needs seeding; this is a no-op minor bump kept only to advance
    # entries sitting at minor 14 to 15 so they stop re-triggering migration
    # every restart (the v3.13 → v3.14 precedent). Rollback-safe: an older build
    # finds every key exactly as it left it and ignores the one it doesn't know.
    new_minor = _advance_noop_minor(new_version, new_minor, 15)

    # v3.15 → v3.16: added the additive sun-tracking gate options —
    # sun_tracking_gate_sensors / _template / _template_mode (issue #1167). An
    # absent key already reads as "no gate configured", which resolves to the
    # master toggle alone, so nothing needs seeding; this is a no-op minor bump
    # kept only to advance entries sitting at minor 15 to 16 so they stop
    # re-triggering migration every restart (the v3.14 → v3.15 precedent).
    # Rollback-safe: an older build finds every key exactly as it left it and
    # ignores the three it does not know, reverting to the plain bool toggle.
    new_minor = _advance_noop_minor(new_version, new_minor, 16)

    # v3.16 → v3.17: added the additive named command-queue options —
    # command_queue on covers, command_queue_gap on the new Command Queue entry
    # type (issue #1189). An absent key already reads as "no queue", which is
    # exactly the dispatch behaviour every existing install has today, so
    # nothing needs seeding; this is a no-op minor bump kept only to advance
    # entries sitting at minor 16 to 17 so they stop re-triggering migration
    # every restart (the v3.15 → v3.16 precedent). Rollback-safe: an older build
    # finds every key exactly as it left it and ignores the one it does not
    # know, reverting to unserialized dispatch.
    new_minor = _advance_noop_minor(new_version, new_minor, 17)

    # v3.17 → v3.18: added the additive per-slot outside-window constraint flag
    # custom_position_outside_window_N (issue #943 item B). An absent key already
    # reads as "this slot's constraints stop at the clock window", which is
    # exactly what every existing install does today, so nothing needs seeding;
    # this is a no-op minor bump kept only to advance entries sitting at minor 17
    # to 18 so they stop re-triggering migration every restart (the v3.16 → v3.17
    # precedent). Rollback-safe: an older build finds every key exactly as it
    # left it and ignores the ten it does not know, so those slots simply stop
    # clamping at night again.
    new_minor = _advance_noop_minor(new_version, new_minor, 18)

    # v3.18 → v3.19: added the additive optional solar options — the
    # transmittance description solar_properties_enabled / solar_cover_side /
    # solar_cover_shade / solar_g_total / solar_g_glazing (issue #1236) and the
    # estimated-solar-gain inputs glass_area / irradiance_plane (issue #1237).
    # An absent solar_properties_enabled already reads as "feature off", which
    # is exactly what every existing install does today, so nothing needs
    # seeding; this is a no-op minor bump kept only to advance entries sitting
    # at minor 18 to 19 so they stop re-triggering migration every restart (the
    # v3.17 → v3.18 precedent). Rollback-safe: an older build finds every key
    # exactly as it left it and ignores the ones it does not know, so the
    # diagnostics block and the gain sensor simply disappear again.
    new_minor = _advance_noop_minor(new_version, new_minor, 19)

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
    # Derived measurement data, not a behaviour switch. It MUST be in this map:
    # a calibration run rewrites it mid-pass, and a reload there would tear down
    # the run that produced it.
    CONF_TRAVEL_TIME_CALIBRATION: "async_apply_travel_calibration_update",
}


def _changed_option_keys(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> set[str]:
    """Keys whose value differs between two option mappings."""
    return {
        key
        for key in current.keys() | previous.keys()
        if current.get(key) != previous.get(key)
    }


def options_write_reloads(entry: ConfigEntry, new_options: Mapping[str, Any]) -> bool:
    """Whether writing *new_options* onto *entry* will reload it.

    The single statement of ``_async_update_listener``'s reload rule.
    ``config_flow`` has to ask this question *before* it writes — a cover-type
    switch changes ``entry.data``, which the listener does not look at, so it
    needs its own reload, but only when the options write is not about to
    produce one anyway (issue #1132). Restating the rule there instead of
    sharing it is how the two answers drift apart into a double reload.

    Note the answer is *not* "did the options change": a delta confined to
    ``_RUNTIME_APPLICABLE_OPTIONS`` is applied in place and never reloads.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        # Nothing is set up, so no update listener is registered to react.
        return False
    previous = getattr(coordinator, "_cached_options", None)
    if previous is None:
        return True
    changed = _changed_option_keys(previous, new_options)
    return bool(changed) and not changed.issubset(_RUNTIME_APPLICABLE_OPTIONS)


async def _async_profile_propagate(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Propagate a Building Profile's sensor changes to its linked covers.

    Registered as the update listener for virtual ``building_profile`` entries
    (which build no coordinator). Re-copies the profile's non-empty shared-sensor
    subset into every linked cover via the shared copier — the ``async_update_entry``
    it performs fires each cover's self-reload listener, so linked covers pick up
    the changed sensor IDs immediately.

    Copy-only: a blank profile key is left alone, because from here a field the
    user just cleared looks exactly like one they never filled in. Removing a
    cleared key from the linked covers happens at save time instead, where the
    transition is still visible — ``propagate_profile_clears`` (issue #1085).
    """
    # Guard: only profiles propagate. A real cover reaching here would be a
    # wiring bug — its own listener handles reloads — and so would a Command
    # Queue, which shares ``controls_cover == False`` but owns a gap, not a set
    # of shared sensors, and has its own listener (issue #1189).
    policy = get_policy(entry.data.get(CONF_SENSOR_TYPE))
    if policy.controls_cover or policy.is_command_queue:
        return
    for cover in _covers_linked_to(hass, entry):
        _copy_profile_to_cover(hass, entry, cover)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    if options_write_reloads(entry, entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    coordinator = entry.runtime_data
    previous_options = coordinator._cached_options
    if previous_options is None:
        return
    changed_keys = _changed_option_keys(previous_options, entry.options)
    for apply_name in {_RUNTIME_APPLICABLE_OPTIONS[key] for key in changed_keys}:
        await getattr(coordinator, apply_name)()
