"""Cover-group domain services (issue #790, Phase 4).

Six thin services over the GroupCoordinator's public methods, plus the
resolution split: cover services must never touch a group coordinator
(pre-existing crash: integration_disable with no target dereferenced
cover-only attributes on the group), and group services must never touch a
cover coordinator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
    GroupScene,
)
from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator
from custom_components.adaptive_cover_pro.services import (
    _resolve_targets,
    async_setup_services,
)
from custom_components.adaptive_cover_pro.services.group_service import (
    resolve_group_targets,
)

pytestmark = pytest.mark.integration


def _add_loaded_entry(hass, entry_id, sensor_type, coordinator, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: sensor_type},
        options=options or {},
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    entry.mock_state(
        hass,
        __import__(
            "homeassistant.config_entries", fromlist=["ConfigEntryState"]
        ).ConfigEntryState.LOADED,
    )
    entry.runtime_data = coordinator
    return entry


def _mock_group_coordinator() -> MagicMock:
    coord = MagicMock(spec=GroupCoordinator)
    for name in (
        "async_activate_scene",
        "async_clear_scene",
        "async_set_position",
        "async_set_tilt",
        "async_set_lock",
        "async_clear_overrides",
        "async_set_automation",
    ):
        setattr(coord, name, AsyncMock())
    return coord


@pytest.fixture
def loaded_pair(hass):
    """One loaded cover entry and one loaded group entry."""
    cover_coord = MagicMock()
    cover_coord.entities = ["cover.blind1"]
    _add_loaded_entry(
        hass,
        "cover_entry",
        CoverType.BLIND,
        cover_coord,
        options={CONF_ENTITIES: ["cover.blind1"]},
    )
    group_coord = _mock_group_coordinator()
    _add_loaded_entry(
        hass,
        "group_entry",
        CoverType.GROUP,
        group_coord,
        options={CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: []},
    )
    return cover_coord, group_coord


async def test_cover_target_resolution_excludes_groups(hass, loaded_pair) -> None:
    """Cover services must never fan out to a group coordinator.

    Pins the pre-existing crash: an untargeted integration_disable walked
    every loaded coordinator and dereferenced cover-only attributes.
    """
    cover_coord, group_coord = loaded_pair

    call = MagicMock()
    call.data = {}
    targets = _resolve_targets(hass, call)

    assert cover_coord in targets
    assert group_coord not in targets


async def test_group_target_resolution_no_target_all_groups(hass, loaded_pair) -> None:
    cover_coord, group_coord = loaded_pair

    call = MagicMock()
    call.data = {}
    groups = resolve_group_targets(hass, call)

    assert groups == [group_coord]


async def test_group_target_resolution_by_entity(hass, loaded_pair) -> None:
    """An entity registered to the group entry resolves to that group only."""
    from homeassistant.helpers import entity_registry as er

    _, group_coord = loaded_pair
    other_group = _mock_group_coordinator()
    _add_loaded_entry(hass, "group_other", CoverType.GROUP, other_group)

    reg = er.async_get(hass)
    reg.async_get_or_create(
        "select",
        DOMAIN,
        "group_entry_group_scene_select",
        suggested_object_id="living_scene",
        config_entry=hass.config_entries.async_get_entry("group_entry"),
    )

    call = MagicMock()
    call.data = {"entity_id": ["select.living_scene"]}
    groups = resolve_group_targets(hass, call)

    assert groups == [group_coord]


async def test_group_services_drive_coordinator_methods(hass, loaded_pair) -> None:
    _, group_coord = loaded_pair
    await async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        "group_activate_scene",
        {"scene": str(GroupScene.PRIVACY)},
        blocking=True,
    )
    group_coord.async_activate_scene.assert_awaited_once_with(GroupScene.PRIVACY)

    await hass.services.async_call(
        DOMAIN, "group_activate_scene", {"scene": "auto"}, blocking=True
    )
    group_coord.async_clear_scene.assert_awaited_once()

    await hass.services.async_call(
        DOMAIN, "group_set_position", {"position": 40, "tilt": 20}, blocking=True
    )
    group_coord.async_set_position.assert_awaited_once_with(40)
    group_coord.async_set_tilt.assert_awaited_once_with(20)

    await hass.services.async_call(DOMAIN, "group_lock", {}, blocking=True)
    group_coord.async_set_lock.assert_awaited_once_with(True)
    await hass.services.async_call(DOMAIN, "group_unlock", {}, blocking=True)
    group_coord.async_set_lock.assert_awaited_with(False)

    await hass.services.async_call(DOMAIN, "group_clear_overrides", {}, blocking=True)
    group_coord.async_clear_overrides.assert_awaited_once()

    await hass.services.async_call(
        DOMAIN, "group_set_automation", {"enabled": False}, blocking=True
    )
    group_coord.async_set_automation.assert_awaited_once_with(False)


async def test_group_set_position_without_tilt_skips_tilt(hass, loaded_pair) -> None:
    _, group_coord = loaded_pair
    await async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN, "group_set_position", {"position": 55}, blocking=True
    )

    group_coord.async_set_position.assert_awaited_once_with(55)
    group_coord.async_set_tilt.assert_not_awaited()


async def test_group_activate_scene_rejects_unknown_scene(hass, loaded_pair) -> None:
    import voluptuous as vol

    await async_setup_services(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "group_activate_scene", {"scene": "party"}, blocking=True
        )


async def test_group_target_resolution_by_device_and_area(hass, loaded_pair) -> None:
    """Device and area targets resolve through the registries to the group."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr

    _, group_coord = loaded_pair
    area = ar.async_get(hass).async_get_or_create("Service Area")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="group_entry",
        identifiers={(DOMAIN, "group_entry")},
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)

    call = MagicMock()
    call.data = {"device_id": [device.id]}
    assert resolve_group_targets(hass, call) == [group_coord]

    call = MagicMock()
    call.data = {"area_id": [area.id]}
    assert resolve_group_targets(hass, call) == [group_coord]


async def test_group_target_resolution_skips_foreign_targets(hass, loaded_pair) -> None:
    """A cover-entry entity and an unknown entity resolve to no groups."""
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "cover_entry_sensor",
        suggested_object_id="blind_sensor",
        config_entry=hass.config_entries.async_get_entry("cover_entry"),
    )

    call = MagicMock()
    call.data = {"entity_id": ["sensor.blind_sensor", "cover.nonexistent"]}
    assert resolve_group_targets(hass, call) == []


async def test_group_diagnostics_unloaded_coordinator(hass) -> None:
    """A group entry with no loaded coordinator reports unavailable, no crash."""
    from custom_components.adaptive_cover_pro.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={},
        entry_id="group_unloaded",
        title="G",
    )
    entry.add_to_hass(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["group"]["status"] == "unavailable"


async def test_group_target_resolution_unknown_device(hass, loaded_pair) -> None:
    """A vanished device id is skipped without error."""
    call = MagicMock()
    call.data = {"device_id": ["no_such_device"]}
    assert resolve_group_targets(hass, call) == []
