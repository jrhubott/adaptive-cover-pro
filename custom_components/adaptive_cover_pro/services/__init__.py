"""Services for Adaptive Cover Pro integration."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator import AdaptiveDataUpdateCoordinator

from ..const import DOMAIN
from ..helpers import usable_coordinator
from .diagnostics_service import async_handle_get_diagnostics
from .group_service import GROUP_SERVICE_NAMES, register_group_services
from .engage_manual_override_service import (
    ENGAGE_MANUAL_OVERRIDE_SCHEMA,
    async_handle_engage_manual_override,
)
from .export_service import (
    EXPORT_ALL_CONFIG_SCHEMA,
    EXPORT_CONFIG_SCHEMA,
    async_handle_export,
    async_handle_export_all,
)
from .import_service import IMPORT_CONFIG_SCHEMA, async_handle_import_config
from .options_service import OPTIONS_SERVICE_NAMES, register_options_services
from .set_axes_service import SET_AXES_SCHEMA, async_handle_set_axes
from .set_position_service import SET_POSITION_SCHEMA, async_handle_set_position
from .set_tilt_service import SET_TILT_SCHEMA, async_handle_set_tilt
from .stop_service import async_handle_stop
from .troubleshoot_service import async_handle_get_troubleshooting

_LOGGER = logging.getLogger(__name__)


def loaded_coordinators(
    hass: HomeAssistant,
) -> dict[str, AdaptiveDataUpdateCoordinator]:
    """Map entry_id → coordinator for every loaded ACP config entry.

    Replaces the legacy ``hass.data[DOMAIN]`` registry: each loaded entry now
    stores its coordinator on ``entry.runtime_data``.

    The LOADED-and-has-a-coordinator predicate lives in
    ``helpers.usable_coordinator`` so this walk and ``GroupCoordinator``'s
    roster walk cannot drift apart.
    """
    return {
        entry.entry_id: coordinator
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (coordinator := usable_coordinator(entry)) is not None
    }


def cover_coordinators(
    hass: HomeAssistant,
) -> dict[str, AdaptiveDataUpdateCoordinator]:
    """``loaded_coordinators`` restricted to real cover coordinators.

    Cover groups also live on ``runtime_data`` but expose none of the cover
    coordinator's surface — a cover service that walked them would crash
    (issue #790 Phase 4). The discriminator is the coordinator type itself
    (not a re-fetched entry) so callers with partially-mocked ``hass`` still
    resolve. Cover services resolve through this; group services use
    ``group_service.group_coordinators``.
    """
    from ..group_coordinator import GroupCoordinator  # noqa: PLC0415

    return {
        entry_id: coordinator
        for entry_id, coordinator in loaded_coordinators(hass).items()
        if not isinstance(coordinator, GroupCoordinator)
    }


@dataclass(frozen=True, slots=True)
class UnresolvedConfigEntry:
    """An explicitly-targeted ACP config entry with no cover coordinator.

    ``name`` is ``entry.data["name"]`` — the same bare name every healthy
    branch reads (``config_entry.data.get("name")`` in both
    ``diagnostics_service.py`` and ``troubleshoot_service.py``), falling back
    to ``entry.title`` only for an entry type that doesn't carry a
    ``data["name"]`` at all. ``entry.title`` is a *different* string by
    construction — ``config_flow.py`` builds every entry with
    ``title=f"{cover_type_label} {name}"`` alongside ``data["name"]=name`` — so
    using ``title`` here would give a degraded entry a different naming scheme
    than a healthy one (issue #1059 audit round 4, N2). ``reason`` is a
    per-case, human-readable explanation of why this id produced no
    coordinator (a cover group, a Building Profile, or an entry that isn't
    currently loaded — issue #1059 audit round 3, defect #1).
    """

    name: str
    reason: str


def _resolve_by_config_entry(
    hass: HomeAssistant, entry_ids: list[str]
) -> tuple[dict[str, AdaptiveDataUpdateCoordinator], dict[str, UnresolvedConfigEntry]]:
    """Resolve explicit config entry IDs to (resolved, unresolved).

    Shared by ``get_diagnostics`` and ``get_troubleshooting`` — both accept an
    explicit ``config_entry_id`` escape hatch that bypasses the standard
    entity/device/area target block. ``resolved`` maps entry_id → coordinator
    for every id that is a real, loaded cover coordinator (resolved through
    :func:`cover_coordinators`, never :func:`loaded_coordinators`, so a cover
    group is never handed to a reader that expects a real cover coordinator's
    ``.data.diagnostics`` shape — a ``GroupCoordinator``'s ``.data`` is
    ``GroupAggregates``, which has no ``diagnostics`` attribute, issue #1059).

    ``unresolved`` maps entry_id → :class:`UnresolvedConfigEntry` for every id
    that IS an ACP config entry but has no cover coordinator to read from — a
    cover group, a Building Profile (a virtual entry that reaches ``LOADED``
    without ever setting ``runtime_data``, so it is absent from
    :func:`loaded_coordinators` too), or an entry that isn't currently
    ``LOADED`` at all (mid-reload, ``SETUP_RETRY``, disabled). Callers degrade
    these to a per-entry error rather than losing the whole response — a
    single targeted instance being unresolvable must not sink an explicit
    multi-entry call (issue #1059 audit round 3, defect #1: an ``else: raise``
    here previously made the whole batch all-or-nothing).

    An id that is not an ACP config entry at all (typo, wrong integration,
    nonexistent) is a different class of mistake with no instance to attach a
    degraded entry to — that still raises ``ServiceValidationError``
    immediately, unchanged from before.
    """
    cover_coords = cover_coordinators(hass)
    all_loaded = loaded_coordinators(hass)
    resolved: dict[str, AdaptiveDataUpdateCoordinator] = {}
    unresolved: dict[str, UnresolvedConfigEntry] = {}
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                f"Config entry '{entry_id}' not found or does not belong to {DOMAIN}"
            )
        coord = cover_coords.get(entry_id)
        if coord is not None:
            resolved[entry_id] = coord
            continue
        if entry_id in all_loaded:
            # Loaded, but excluded from cover_coordinators() → a cover group.
            reason = (
                "This config entry is a cover group, not a single cover "
                "instance — it has no per-instance diagnostics or "
                "troubleshooting data."
            )
        elif entry.state is ConfigEntryState.LOADED:
            # LOADED but never set runtime_data → a virtual Building Profile.
            reason = (
                "This config entry has no cover coordinator — it is likely "
                "a Building Profile (a virtual entry with no cover to "
                "control)."
            )
        else:
            reason = (
                f"This config entry is not currently loaded (state: "
                f"{entry.state.value}) — it may be mid-reload, in a setup "
                "retry, or disabled."
            )
        unresolved[entry_id] = UnresolvedConfigEntry(
            name=entry.data.get("name") or entry.title, reason=reason
        )
    return resolved, unresolved


def _resolve_targets(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict:
    """Resolve a service call's target block to {coordinator: entity_filter_or_None}.

    Returns a dict mapping coordinator objects to either:
    - None  → act on all covers owned by this coordinator
    - set   → act only on the named cover entity_ids within this coordinator

    Resolution rules (unioned if multiple targets provided):
    - No target              → all coordinators, None filter (all their covers)
    - entity_id targets      → walk coordinators; match where entity is in coordinator.entities
    - device_id targets      → map device → config_entry_id → coordinator
    - area_id targets        → expand device_ids via device registry, then device_id rule

    Unmanaged entity_ids (not owned by any ACP coordinator) are silently skipped.
    Cover groups are excluded — group services have their own resolver.
    """
    all_coordinators = cover_coordinators(hass)

    entity_ids: list[str] = cv.ensure_list(call.data.get("entity_id"))
    device_ids: list[str] = cv.ensure_list(call.data.get("device_id"))
    area_ids: list[str] = cv.ensure_list(call.data.get("area_id"))

    # Expand area_ids → device_ids
    if area_ids:
        dev_reg = dr.async_get(hass)
        for area_id in area_ids:
            for device in dev_reg.devices.values():
                if device.area_id == area_id:
                    device_ids.append(device.id)

    # No target at all → all coordinators, no filter
    if not entity_ids and not device_ids and not area_ids:
        return dict.fromkeys(all_coordinators.values())

    result: dict = {}

    # Expand device_ids → config_entry_ids
    if device_ids:
        dev_reg = dr.async_get(hass)
        for device_id in device_ids:
            device = dev_reg.async_get(device_id)
            if device:
                for entry_id in device.config_entries:
                    if entry_id in all_coordinators:
                        coord = all_coordinators[entry_id]
                        result.setdefault(coord, None)

    # Fetch entity registry once for the ACP-owned non-cover entity fallback
    ent_reg = er.async_get(hass)

    # Resolve entity_ids → coordinator + per-entity filter
    for eid in entity_ids:
        owned = False
        for coord in all_coordinators.values():
            if eid in coord.entities:
                owned = True
                existing = result.get(coord)
                if existing is None and coord in result:
                    # Already set to None (all covers) — don't narrow it
                    pass
                else:
                    # Add entity to filter set (or create it)
                    if coord not in result:
                        result[coord] = {eid}
                    elif existing is not None:
                        existing.add(eid)
                    # If existing is None (whole-coordinator), keep it
        if not owned:
            # Fallback: look up the entity registry to find any ACP-owned entity
            # (e.g. diagnostic sensor, switch, button) by its config_entry_id.
            reg_entry = ent_reg.async_get(eid)
            if reg_entry and reg_entry.config_entry_id in all_coordinators:
                coord = all_coordinators[reg_entry.config_entry_id]
                result.setdefault(coord, None)
                owned = True
        if not owned:
            _LOGGER.debug(
                "integration_service: entity %s is not managed by any ACP instance — skipping",
                eid,
            )

    if (entity_ids or device_ids or area_ids) and not result:
        _LOGGER.warning(
            "integration_service: target %s/%s/%s resolved to no ACP instances — nothing updated",
            entity_ids,
            device_ids,
            area_ids,
        )

    return result


# Shared schema for the response-services that each return a per-instance
# payload on demand (``get_diagnostics``, ``get_troubleshooting`` — issue
# #1059): the standard entity/device/area target block plus an explicit
# ``config_entry_id`` escape hatch. Defined once here so neither service
# module hand-duplicates it (CODING_GUIDELINES "No Code Duplication").
TARGET_WITH_CONFIG_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("area_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("config_entry_id"): vol.All(cv.ensure_list, [str]),
    }
)


def _resolve_service_targets(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[dict[str, AdaptiveDataUpdateCoordinator], dict[str, UnresolvedConfigEntry]]:
    """Resolve a response-service call to (resolved, unresolved).

    Shared targeting preamble for ``get_diagnostics`` and
    ``get_troubleshooting`` (issue #1059): an explicit ``config_entry_id``
    list bypasses the standard entity/device/area target block via
    :func:`_resolve_by_config_entry` — the only path that can produce
    ``unresolved`` entries. The standard entity/device/area target block
    (:func:`_resolve_targets`) only ever discovers real, loaded cover
    coordinators, so it always pairs with an empty unresolved map.
    """
    explicit_entry_ids: list[str] = call.data.get("config_entry_id") or []
    if explicit_entry_ids:
        return _resolve_by_config_entry(hass, explicit_entry_ids)
    target_map = _resolve_targets(hass, call)
    return {coord.config_entry.entry_id: coord for coord in target_map}, {}


def _build_response_envelope(entries: dict) -> dict:
    """Build the standard ``{version, generated_at, count, entries}`` envelope.

    Shared response shape for every response-service that returns a
    per-instance payload (``get_diagnostics``, ``get_troubleshooting`` — issue
    #1059), so the envelope is never hand-duplicated a second time.
    """
    return {
        "version": 1,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "count": len(entries),
        "entries": entries,
    }


def _set_enabled(
    coord: AdaptiveDataUpdateCoordinator,
    value: bool,
    service: str,
    outcome: str,
) -> None:
    """Flip a coordinator's kill switch and push the change to its switch entity.

    ``switch.<entry>_integration_enabled`` reads ``coordinator.enabled_toggle``
    (issue #1063), so the coordinator is the single source of truth — but the
    entity still needs a listener notification to re-render. These three
    services deliberately never refresh, and the coordinator sets no
    ``update_interval`` (refreshes are state-change driven), so without the
    notification the switch would keep rendering its old value until some
    unrelated cycle happened to run. That stale render is what ``RestoreEntity``
    would hand back at the next restart, silently re-enabling the integration.
    """
    coord.enabled_toggle = value
    coord.async_update_listeners()
    coord.logger.debug("%s service: %s", service, outcome)


def _stand_down_calibration(coord: AdaptiveDataUpdateCoordinator) -> None:
    """Cancel any travel-time run BEFORE the covers are stopped.

    Ordering is the whole point, which is why this is not folded into
    ``_disable`` (which runs after the caller's stop pass). A calibration run
    drives covers through ``_execute_command``, which bypasses the enabled gate
    — so a run left going would keep issuing commands during and after the stop,
    quietly undoing it.

    ``restore=False``: both callers are asking for the covers to stop moving, and
    sending them home is more movement.
    """
    coord.async_cancel_travel_calibration(restore=False)


def _disable(
    coord: AdaptiveDataUpdateCoordinator,
    service: str,
    outcome: str,
) -> None:
    """Cancel deferred work, drop reconciliation state, and close the gate.

    Shared by ``integration_disable`` and ``emergency_stop`` — the two differ
    only in how they stop in-flight moves (scoped ``stop_in_flight`` versus a
    blanket ``stop_all``), which the caller does first; everything after that
    is identical policy and lives here so the two cannot drift.
    """
    coord._cancel_motion_timeout()  # noqa: SLF001
    coord._cancel_weather_timeout()  # noqa: SLF001
    coord._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
    coord._cmd_svc.revoke_safety_verdicts()  # noqa: SLF001
    _set_enabled(coord, False, service, outcome)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent — safe to call per config entry)."""
    if hass.services.has_service(DOMAIN, "export_config"):
        return
    hass.services.async_register(
        DOMAIN,
        "export_config",
        async_handle_export,
        schema=EXPORT_CONFIG_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    if not hass.services.has_service(DOMAIN, "get_diagnostics"):
        hass.services.async_register(
            DOMAIN,
            "get_diagnostics",
            async_handle_get_diagnostics,
            schema=TARGET_WITH_CONFIG_ENTRY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, "get_troubleshooting"):
        hass.services.async_register(
            DOMAIN,
            "get_troubleshooting",
            async_handle_get_troubleshooting,
            schema=TARGET_WITH_CONFIG_ENTRY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, "export_all_config"):
        hass.services.async_register(
            DOMAIN,
            "export_all_config",
            async_handle_export_all,
            schema=EXPORT_ALL_CONFIG_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, "import_config"):
        hass.services.async_register(
            DOMAIN,
            "import_config",
            async_handle_import_config,
            schema=IMPORT_CONFIG_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def handle_integration_enable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord in targets:
            _set_enabled(coord, True, "integration_enable", "enabled")

    async def handle_integration_disable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord, entity_filter in targets.items():
            _stand_down_calibration(coord)
            # Stop in-flight moves first (before gate closes)
            await coord._cmd_svc.stop_in_flight(entities=entity_filter)  # noqa: SLF001
            _disable(coord, "integration_disable", "disabled")

    async def handle_emergency_stop(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord, entity_filter in targets.items():
            _stand_down_calibration(coord)
            # Blanket stop: all configured covers (not just wait_for_target)
            entity_ids = (
                list(entity_filter) if entity_filter is not None else coord.entities
            )
            await coord._cmd_svc.stop_all(entity_ids)  # noqa: SLF001
            # Then run full integration_disable cleanup
            _disable(coord, "emergency_stop", "stopped and disabled")

    hass.services.async_register(
        DOMAIN, "integration_enable", handle_integration_enable
    )
    hass.services.async_register(
        DOMAIN, "integration_disable", handle_integration_disable
    )
    hass.services.async_register(DOMAIN, "emergency_stop", handle_emergency_stop)
    hass.services.async_register(
        DOMAIN, "set_position", async_handle_set_position, schema=SET_POSITION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "set_tilt", async_handle_set_tilt, schema=SET_TILT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "set_axes", async_handle_set_axes, schema=SET_AXES_SCHEMA
    )
    hass.services.async_register(DOMAIN, "stop", async_handle_stop)
    hass.services.async_register(
        DOMAIN,
        "engage_manual_override",
        async_handle_engage_manual_override,
        schema=ENGAGE_MANUAL_OVERRIDE_SCHEMA,
    )

    register_group_services(hass)
    register_options_services(hass)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove integration services when the last config entry is unloaded.

    The unloading entry is already ``UNLOAD_IN_PROGRESS`` (not ``LOADED``) when
    this runs, so ``loaded_coordinators`` counts only the entries that remain.
    """
    if loaded_coordinators(hass):
        return  # Other entries still active
    hass.services.async_remove(DOMAIN, "export_config")
    hass.services.async_remove(DOMAIN, "export_all_config")
    hass.services.async_remove(DOMAIN, "import_config")
    hass.services.async_remove(DOMAIN, "get_diagnostics")
    hass.services.async_remove(DOMAIN, "get_troubleshooting")
    hass.services.async_remove(DOMAIN, "integration_enable")
    hass.services.async_remove(DOMAIN, "integration_disable")
    hass.services.async_remove(DOMAIN, "emergency_stop")
    hass.services.async_remove(DOMAIN, "set_position")
    hass.services.async_remove(DOMAIN, "set_tilt")
    hass.services.async_remove(DOMAIN, "set_axes")
    hass.services.async_remove(DOMAIN, "stop")
    hass.services.async_remove(DOMAIN, "engage_manual_override")
    for name in GROUP_SERVICE_NAMES:
        hass.services.async_remove(DOMAIN, name)
    for name in OPTIONS_SERVICE_NAMES:
        hass.services.async_remove(DOMAIN, name)
