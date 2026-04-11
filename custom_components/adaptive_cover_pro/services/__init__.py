"""Services for Adaptive Cover Pro integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .export_service import EXPORT_CONFIG_SCHEMA, async_handle_export

_LOGGER = logging.getLogger(__name__)


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
    all_coordinators: dict = hass.data.get(DOMAIN, {})

    entity_ids: list[str] = list(call.data.get("entity_id", []))
    device_ids: list[str] = list(call.data.get("device_id", []))
    area_ids: list[str] = list(call.data.get("area_id", []))

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
            _LOGGER.debug(
                "integration_service: entity %s is not managed by any ACP instance — skipping",
                eid,
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

    async def handle_integration_enable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord in targets:
            coord.enabled_toggle = True
            coord.logger.debug("integration_enable service: enabled")

    async def handle_integration_disable(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        for coord, entity_filter in targets.items():
            # Stop in-flight moves first (before gate closes)
            await coord._cmd_svc.stop_in_flight(  # noqa: SLF001
                entities=entity_filter
            )
            coord._cancel_motion_timeout()  # noqa: SLF001
            coord._cancel_weather_timeout()  # noqa: SLF001
            coord._cmd_svc.clear_non_safety_targets()  # noqa: SLF001
            coord._cmd_svc._safety_targets.clear()  # noqa: SLF001
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
            coord._cmd_svc._safety_targets.clear()  # noqa: SLF001
            coord.enabled_toggle = False
            coord.logger.debug("emergency_stop service: stopped and disabled")

    hass.services.async_register(DOMAIN, "integration_enable", handle_integration_enable)
    hass.services.async_register(DOMAIN, "integration_disable", handle_integration_disable)
    hass.services.async_register(DOMAIN, "emergency_stop", handle_emergency_stop)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove integration services when the last config entry is unloaded."""
    if hass.data.get(DOMAIN):
        return  # Other entries still active
    hass.services.async_remove(DOMAIN, "export_config")
    hass.services.async_remove(DOMAIN, "integration_enable")
    hass.services.async_remove(DOMAIN, "integration_disable")
    hass.services.async_remove(DOMAIN, "emergency_stop")
