"""Group entity surfaces (issue #790, Phase 1).

A group entry exposes aggregate sensors, the bulk automation switch, scene
buttons + clear-overrides button, and the scene-picker select (a NEW
platform). All unique_ids follow the locked ``f"{entry_id}_{suffix}"``
contract, and cover entries must not gain any group entity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import button, select, sensor, switch
from custom_components.adaptive_cover_pro.const import (
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
    GroupScene,
    GroupState,
)
from custom_components.adaptive_cover_pro.group_coordinator import (
    GroupAggregates,
    GroupCoordinator,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def group_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Living Room", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: ["cover.g1"]},
        entry_id="group_01",
        title="Living Room",
    )
    entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, entry)
    entry.runtime_data = coordinator
    return entry


async def _added_entities(platform_module, hass, entry):
    added = []
    await platform_module.async_setup_entry(
        hass, entry, lambda new, **kwargs: added.extend(new)
    )
    return added


async def test_sensor_platform_builds_group_sensors(hass, group_entry) -> None:
    entities = await _added_entities(sensor, hass, group_entry)
    unique_ids = {e.unique_id for e in entities}
    assert unique_ids == {
        "group_01_group_position",
        "group_01_group_state",
        "group_01_group_active_scene",
    }


async def test_group_sensor_values(hass, group_entry) -> None:
    """Sensors render the aggregates and the active scene."""
    coordinator = group_entry.runtime_data
    entities = {
        e.unique_id: e for e in await _added_entities(sensor, hass, group_entry)
    }
    coordinator.active_scene = GroupScene.PRIVACY
    coordinator.async_set_updated_data(
        GroupAggregates(
            position=42,
            state=GroupState.MIXED,
            member_positions={"cover.g1": 42},
        )
    )

    assert entities["group_01_group_position"].native_value == 42
    assert entities["group_01_group_position"].extra_state_attributes[
        "member_positions"
    ] == {"cover.g1": 42}
    assert entities["group_01_group_state"].native_value == GroupState.MIXED
    assert entities["group_01_group_active_scene"].native_value == GroupScene.PRIVACY


async def test_switch_platform_builds_group_automation_switch(
    hass, group_entry
) -> None:
    entities = await _added_entities(switch, hass, group_entry)
    assert [e.unique_id for e in entities] == ["group_01_group_automation"]

    coordinator = group_entry.runtime_data
    coordinator.async_set_automation = AsyncMock()
    switch_entity = entities[0]
    switch_entity.async_write_ha_state = MagicMock()

    assert switch_entity.is_on is True  # default: automation on
    await switch_entity.async_turn_off()
    coordinator.async_set_automation.assert_awaited_once_with(False)
    assert switch_entity.is_on is False
    await switch_entity.async_turn_on()
    assert switch_entity.is_on is True


async def test_button_platform_builds_scene_and_clear_buttons(
    hass, group_entry
) -> None:
    entities = await _added_entities(button, hass, group_entry)
    unique_ids = {e.unique_id for e in entities}
    assert unique_ids == {
        *(f"group_01_group_scene_{scene}" for scene in GroupScene),
        "group_01_group_clear_overrides",
    }

    coordinator = group_entry.runtime_data
    coordinator.async_activate_scene = AsyncMock()
    coordinator.async_clear_overrides = AsyncMock()
    by_id = {e.unique_id: e for e in entities}

    await by_id[f"group_01_group_scene_{GroupScene.ALL_OPEN}"].async_press()
    coordinator.async_activate_scene.assert_awaited_once_with(GroupScene.ALL_OPEN)

    await by_id["group_01_group_clear_overrides"].async_press()
    coordinator.async_clear_overrides.assert_awaited_once()


async def test_select_platform_builds_scene_picker(hass, group_entry) -> None:
    entities = await _added_entities(select, hass, group_entry)
    assert [e.unique_id for e in entities] == ["group_01_group_scene_select"]

    picker = entities[0]
    assert picker.options == [str(scene) for scene in GroupScene]

    coordinator = group_entry.runtime_data
    assert picker.current_option is None
    coordinator.active_scene = GroupScene.ALL_CLOSED
    assert picker.current_option == str(GroupScene.ALL_CLOSED)

    coordinator.async_activate_scene = AsyncMock()
    picker.async_write_ha_state = MagicMock()
    await picker.async_select_option(str(GroupScene.PRIVACY))
    coordinator.async_activate_scene.assert_awaited_once_with(GroupScene.PRIVACY)


async def test_select_platform_yields_nothing_for_cover_entry(hass) -> None:
    """The select platform is group-only — a cover entry produces no entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Blind", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={},
        entry_id="cover_01",
        title="Blind",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()

    entities = await _added_entities(select, hass, entry)
    assert entities == []
