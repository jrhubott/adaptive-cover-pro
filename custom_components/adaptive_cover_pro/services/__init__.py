"""Services for Adaptive Cover Pro integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator import AdaptiveDataUpdateCoordinator

from ..const import DOMAIN
from .diagnostics_service import GET_DIAGNOSTICS_SCHEMA, async_handle_get_diagnostics
from .export_service import EXPORT_CONFIG_SCHEMA, async_handle_export
from .options_service import OPTIONS_SERVICE_NAMES, register_options_services
from .set_position_service import SET_POSITION_SCHEMA, async_handle_set_position
from .set_tilt_service import SET_TILT_SCHEMA, async_handle_set_tilt
from .stop_service import async_handle_stop

_LOGGER = logging.getLogger(__name__)


def loaded_coordinators(
    hass: HomeAssistant,
) -> dict[str, AdaptiveDataUpdateCoordinator]:
    """Map entry_id → coordinator for every loaded ACP config entry.

    Replaces the legacy ``hass.data[DOMAIN]`` registry: each loaded entry now
    stores its coordinator on ``entry.runtime_data``.
    """
    return {
        entry.entry_id: entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    }


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
    """
    all_coordinators = loaded_coordinators(hass)

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
            schema=GET_DIAGNOSTICS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def handle_integration_enable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord in targets:
            coord.enabled_toggle = True
            coord.logger.debug("integration_enable service: enabled")

    async def handle_integration_disable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord, entity_filter in targets.items():
            # Stop in-flight moves first (before gate closes)
            await coord._cmd_svc.stop_in_flight(entities=entity_filter)  # noqa: SLF001
            coord._cancel_motion_timeout()  # noqa: SLF001
            coord._cancel_weather_timeout()  # noqa: SLF001
            coord._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
            coord._cmd_svc.clear_safety_targets()  # noqa: SLF001
            coord.enabled_toggle = False
            coord.logger.debug("integration_disable service: disabled")

    async def handle_emergency_stop(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord, entity_filter in targets.items():
            # Blanket stop: all configured covers (not just wait_for_target)
            entity_ids = (
                list(entity_filter) if entity_filter is not None else coord.entities
            )
            await coord._cmd_svc.stop_all(entity_ids)  # noqa: SLF001
            # Then run full integration_disable cleanup
            coord._cancel_motion_timeout()  # noqa: SLF001
            coord._cancel_weather_timeout()  # noqa: SLF001
            coord._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
            coord._cmd_svc.clear_safety_targets()  # noqa: SLF001
            coord.enabled_toggle = False
            coord.logger.debug("emergency_stop service: stopped and disabled")

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
    hass.services.async_register(DOMAIN, "stop", async_handle_stop)

    register_options_services(hass)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove integration services when the last config entry is unloaded.

    The unloading entry is already ``UNLOAD_IN_PROGRESS`` (not ``LOADED``) when
    this runs, so ``loaded_coordinators`` counts only the entries that remain.
    """
    if loaded_coordinators(hass):
        return  # Other entries still active
    hass.services.async_remove(DOMAIN, "export_config")
    hass.services.async_remove(DOMAIN, "get_diagnostics")
    hass.services.async_remove(DOMAIN, "integration_enable")
    hass.services.async_remove(DOMAIN, "integration_disable")
    hass.services.async_remove(DOMAIN, "emergency_stop")
    hass.services.async_remove(DOMAIN, "set_position")
    hass.services.async_remove(DOMAIN, "set_tilt")
    hass.services.async_remove(DOMAIN, "stop")
    for name in OPTIONS_SERVICE_NAMES:
        hass.services.async_remove(DOMAIN, name)
