"""Cover-group domain services (issue #790, Phase 4).

Six thin services over the ``GroupCoordinator``'s public methods so
automations can drive groups: activate/release scenes, set a group
position/tilt, lock/unlock, clear member overrides, and bulk-toggle
automation. Target resolution mirrors the cover services' rules but is
capability-split: these services act ONLY on group coordinators
(``is_orchestrator``), never on cover coordinators — and vice versa.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from voluptuous.validators import Coerce, Range

from ..const import DOMAIN, GROUP_SCENE_SELECT_AUTO, GroupScene

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..group_coordinator import GroupCoordinator

_LOGGER = logging.getLogger(__name__)

GROUP_SERVICE_NAMES: tuple[str, ...] = (
    "group_activate_scene",
    "group_set_position",
    "group_lock",
    "group_unlock",
    "group_clear_overrides",
    "group_set_automation",
)

_SCENE_CHOICES: tuple[str, ...] = (
    GROUP_SCENE_SELECT_AUTO,
    *(str(scene) for scene in GroupScene),
)

# Plain schemas (NOT make_entity_service_schema): the target block is
# optional — an untargeted call fans out to every loaded group, matching
# integration_enable/disable. extra=ALLOW_EXTRA admits the target keys.
GROUP_ACTIVATE_SCENE_SCHEMA = vol.Schema(
    {vol.Required("scene"): vol.In(_SCENE_CHOICES)}, extra=vol.ALLOW_EXTRA
)
GROUP_SET_POSITION_SCHEMA = vol.Schema(
    {
        vol.Required("position"): vol.All(Coerce(int), Range(min=0, max=100)),
        vol.Optional("tilt"): vol.All(Coerce(int), Range(min=0, max=100)),
    },
    extra=vol.ALLOW_EXTRA,
)
GROUP_SET_AUTOMATION_SCHEMA = vol.Schema(
    {vol.Required("enabled"): bool}, extra=vol.ALLOW_EXTRA
)
GROUP_NO_FIELDS_SCHEMA = vol.Schema({}, extra=vol.ALLOW_EXTRA)


def group_coordinators(hass: HomeAssistant) -> dict[str, GroupCoordinator]:
    """Map entry_id → coordinator for every loaded cover-group entry.

    Discriminates on the coordinator type (mirror of
    ``services.cover_coordinators``) — the two service families split the
    ``loaded_coordinators`` pool exactly in two.
    """
    from ..group_coordinator import GroupCoordinator  # noqa: PLC0415
    from . import loaded_coordinators  # noqa: PLC0415

    return {
        entry_id: coordinator
        for entry_id, coordinator in loaded_coordinators(hass).items()
        if isinstance(coordinator, GroupCoordinator)
    }


def resolve_group_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[GroupCoordinator]:
    """Resolve a group service call's target block to group coordinators.

    Mirrors ``_resolve_targets``' rules on the group side: no target → every
    loaded group; entity/device targets resolve through the registries to
    their owning config entry. Non-group targets are silently skipped.
    """
    groups = group_coordinators(hass)

    entity_ids: list[str] = cv.ensure_list(call.data.get("entity_id"))
    device_ids: list[str] = cv.ensure_list(call.data.get("device_id"))
    area_ids: list[str] = cv.ensure_list(call.data.get("area_id"))

    if not entity_ids and not device_ids and not area_ids:
        return list(groups.values())

    if area_ids:
        dev_reg = dr.async_get(hass)
        for area_id in area_ids:
            device_ids.extend(
                device.id
                for device in dev_reg.devices.values()
                if device.area_id == area_id
            )

    resolved: dict[str, GroupCoordinator] = {}
    if device_ids:
        dev_reg = dr.async_get(hass)
        for device_id in device_ids:
            device = dev_reg.async_get(device_id)
            if device is None:
                continue
            for entry_id in device.config_entries:
                if entry_id in groups:
                    resolved[entry_id] = groups[entry_id]

    ent_reg = er.async_get(hass)
    for entity_id in entity_ids:
        reg_entry = ent_reg.async_get(entity_id)
        if reg_entry is not None and reg_entry.config_entry_id in groups:
            resolved[reg_entry.config_entry_id] = groups[reg_entry.config_entry_id]
        else:
            _LOGGER.debug(
                "group_service: entity %s is not owned by any cover group — skipping",
                entity_id,
            )

    if not resolved:
        _LOGGER.warning(
            "group_service: target %s/%s/%s resolved to no cover groups — nothing done",
            entity_ids,
            device_ids,
            area_ids,
        )
    return list(resolved.values())


async def async_handle_group_activate_scene(call: ServiceCall) -> None:
    """Activate a scene on the targeted groups — or release it via 'auto'."""
    scene_value: str = call.data["scene"]
    for group in resolve_group_targets(call.hass, call):
        if scene_value == GROUP_SCENE_SELECT_AUTO:
            await group.async_clear_scene()
        else:
            await group.async_activate_scene(GroupScene(scene_value))


async def async_handle_group_set_position(call: ServiceCall) -> None:
    """Fan a user position (and optional tilt) out through the group."""
    position: int = call.data["position"]
    tilt: int | None = call.data.get("tilt")
    for group in resolve_group_targets(call.hass, call):
        await group.async_set_position(position)
        if tilt is not None:
            await group.async_set_tilt(tilt)


async def async_handle_group_lock(call: ServiceCall) -> None:
    """Push the group lock on the targeted groups."""
    for group in resolve_group_targets(call.hass, call):
        await group.async_set_lock(True)


async def async_handle_group_unlock(call: ServiceCall) -> None:
    """Release the group lock on the targeted groups."""
    for group in resolve_group_targets(call.hass, call):
        await group.async_set_lock(False)


async def async_handle_group_clear_overrides(call: ServiceCall) -> None:
    """Clear member manual overrides on the targeted groups."""
    for group in resolve_group_targets(call.hass, call):
        await group.async_clear_overrides()


async def async_handle_group_set_automation(call: ServiceCall) -> None:
    """Bulk-toggle member automation on the targeted groups."""
    enabled: bool = call.data["enabled"]
    for group in resolve_group_targets(call.hass, call):
        await group.async_set_automation(enabled)


def register_group_services(hass: HomeAssistant) -> None:
    """Register the six group services (called from async_setup_services)."""
    hass.services.async_register(
        DOMAIN,
        "group_activate_scene",
        async_handle_group_activate_scene,
        schema=GROUP_ACTIVATE_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "group_set_position",
        async_handle_group_set_position,
        schema=GROUP_SET_POSITION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, "group_lock", async_handle_group_lock, schema=GROUP_NO_FIELDS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "group_unlock", async_handle_group_unlock, schema=GROUP_NO_FIELDS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "group_clear_overrides",
        async_handle_group_clear_overrides,
        schema=GROUP_NO_FIELDS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "group_set_automation",
        async_handle_group_set_automation,
        schema=GROUP_SET_AUTOMATION_SCHEMA,
    )
