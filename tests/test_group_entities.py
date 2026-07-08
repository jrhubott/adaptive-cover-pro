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
from custom_components.adaptive_cover_pro.const import (
    GROUP_SCENE_SELECT_AUTO as SCENE_SELECT_AUTO,
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
        "group_01_group_climate_mode",
        "group_01_group_who_won",
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


@pytest.mark.parametrize(
    "unique_id",
    [
        "group_01_group_state",
        "group_01_group_active_scene",
        "group_01_group_climate_mode",
    ],
)
async def test_group_text_sensors_have_no_unit(hass, group_entry, unique_id) -> None:
    """Text/status group sensors must not set a unit of measurement (issue #843).

    Home Assistant's ``SensorEntity`` raises ``ValueError`` for a non-numeric value
    when ``native_unit_of_measurement is not None`` — and an empty string ``""`` is
    not ``None``. That raise aborts the state write and HA marks the entity
    ``unavailable`` the moment the sensor holds a string (observed live on
    ``sensor.<group>_group_state``). These sensors return strings, so they carry
    no unit.
    """
    entities = {
        e.unique_id: e for e in await _added_entities(sensor, hass, group_entry)
    }
    assert entities[unique_id].native_unit_of_measurement is None


async def test_switch_platform_builds_group_automation_switch(
    hass, group_entry
) -> None:
    entities = await _added_entities(switch, hass, group_entry)
    assert [e.unique_id for e in entities] == [
        "group_01_group_automation",
        "group_01_group_lock",
    ]

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
    assert picker.options == [SCENE_SELECT_AUTO, *(str(scene) for scene in GroupScene)]

    coordinator = group_entry.runtime_data
    assert picker.current_option == SCENE_SELECT_AUTO
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


async def test_group_lock_switch_drives_lock_intent(hass, group_entry) -> None:
    """The lock switch pushes/releases the LOCK intent via the coordinator."""
    coordinator = group_entry.runtime_data
    coordinator.async_set_lock = AsyncMock()
    entities = {
        e.unique_id: e for e in await _added_entities(switch, hass, group_entry)
    }
    lock = entities["group_01_group_lock"]
    lock.async_write_ha_state = MagicMock()

    assert lock.is_on is False  # unlocked by default
    await lock.async_turn_on()
    coordinator.async_set_lock.assert_awaited_once_with(True)
    assert lock.is_on is True

    await lock.async_turn_off()
    coordinator.async_set_lock.assert_awaited_with(False)
    assert lock.is_on is False


async def test_select_auto_clears_scene(hass, group_entry) -> None:
    """Choosing Auto releases the scene claim."""
    coordinator = group_entry.runtime_data
    coordinator.async_clear_scene = AsyncMock()
    coordinator.async_activate_scene = AsyncMock()
    entities = await _added_entities(select, hass, group_entry)
    picker = entities[0]
    picker.async_write_ha_state = MagicMock()

    await picker.async_select_option(SCENE_SELECT_AUTO)

    coordinator.async_clear_scene.assert_awaited_once()
    coordinator.async_activate_scene.assert_not_awaited()


async def test_who_won_sensor_reads_member_winners(hass, group_entry) -> None:
    """State = members currently driven by this group; per-member attributes."""
    coordinator = group_entry.runtime_data
    coordinator.member_winners = MagicMock(
        return_value={
            "cover.blind1": "group_scene",
            "cover.awning1": "weather_override",
            "cover.tilt1": "group_lock",
        }
    )
    entities = {
        e.unique_id: e for e in await _added_entities(sensor, hass, group_entry)
    }
    who_won = entities["group_01_group_who_won"]

    assert who_won.native_value == 2  # blind (scene) + tilt (lock)
    assert who_won.extra_state_attributes["member_winners"] == {
        "cover.blind1": "group_scene",
        "cover.awning1": "weather_override",
        "cover.tilt1": "group_lock",
    }


# ---------------------------------------------------------------------------
# Phase 3 — climate rollup sensor, group cover entity, exposure toggles
# ---------------------------------------------------------------------------


async def test_group_climate_sensor_states(hass, group_entry) -> None:
    """Agree → the mode; disagree → mixed; none reporting → None."""
    coordinator = group_entry.runtime_data
    entities = {
        e.unique_id: e for e in await _added_entities(sensor, hass, group_entry)
    }
    climate = entities["group_01_group_climate_mode"]

    coordinator.member_climate_modes = MagicMock(
        return_value={"cover.a": "summer_mode", "cover.b": "summer_mode"}
    )
    assert climate.native_value == "summer_mode"

    coordinator.member_climate_modes = MagicMock(
        return_value={"cover.a": "summer_mode", "cover.b": "winter_mode"}
    )
    assert climate.native_value == "mixed"

    coordinator.member_climate_modes = MagicMock(
        return_value={"cover.a": None, "cover.b": None}
    )
    assert climate.native_value is None

    coordinator.member_climate_modes = MagicMock(
        return_value={"cover.a": "winter_mode", "cover.b": None}
    )
    assert climate.native_value == "winter_mode"
    assert climate.extra_state_attributes["member_climate_modes"] == {
        "cover.a": "winter_mode",
        "cover.b": None,
    }


async def test_sensor_platform_includes_climate_by_default(hass, group_entry) -> None:
    entities = {e.unique_id for e in await _added_entities(sensor, hass, group_entry)}
    assert "group_01_group_climate_mode" in entities


async def test_sensor_toggles_gate_creation(hass) -> None:
    """Disabled exposure toggles suppress the matching sensors."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_ENABLE_CLIMATE_SENSOR,
        CONF_GROUP_ENABLE_POSITION_SENSOR,
        CONF_GROUP_ENABLE_STATE_SENSOR,
        CONF_GROUP_ENABLE_WHO_WON_SENSOR,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: [],
            CONF_MEMBER_COVERS: ["cover.g1"],
            CONF_GROUP_ENABLE_POSITION_SENSOR: False,
            CONF_GROUP_ENABLE_STATE_SENSOR: False,
            CONF_GROUP_ENABLE_CLIMATE_SENSOR: False,
            CONF_GROUP_ENABLE_WHO_WON_SENSOR: False,
        },
        entry_id="group_05",
        title="G",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = GroupCoordinator(hass, entry)

    entities = {e.unique_id for e in await _added_entities(sensor, hass, entry)}

    # Only the always-on active-scene sensor remains.
    assert entities == {"group_05_group_active_scene"}


async def test_cover_platform_builds_group_cover_only_when_enabled(hass) -> None:
    from custom_components.adaptive_cover_pro import cover
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_ENABLE_COVER_ENTITY,
    )

    # Default: off — no group cover entity.
    entry_off = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: ["cover.g1"]},
        entry_id="group_06",
        title="G",
    )
    entry_off.add_to_hass(hass)
    entry_off.runtime_data = GroupCoordinator(hass, entry_off)
    assert await _added_entities(cover, hass, entry_off) == []

    entry_on = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G2", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: [],
            CONF_MEMBER_COVERS: ["cover.g1"],
            CONF_GROUP_ENABLE_COVER_ENTITY: True,
        },
        entry_id="group_07",
        title="G2",
    )
    entry_on.add_to_hass(hass)
    entry_on.runtime_data = GroupCoordinator(hass, entry_on)
    entities = await _added_entities(cover, hass, entry_on)
    assert [e.unique_id for e in entities] == ["group_07_group_cover"]


async def test_group_cover_reads_aggregates_and_commands(hass, group_entry) -> None:
    from custom_components.adaptive_cover_pro.group_entities import AdaptiveGroupCover

    coordinator = group_entry.runtime_data
    group_cover = AdaptiveGroupCover("group_01", hass, group_entry, coordinator)
    coordinator.async_set_updated_data(
        GroupAggregates(
            position=0, state=GroupState.CLOSED, member_positions={"cover.g1": 0}
        )
    )

    assert group_cover.current_cover_position == 0
    assert group_cover.is_closed is True

    coordinator.async_set_position = AsyncMock()
    coordinator.async_stop = AsyncMock()
    await group_cover.async_set_cover_position(position=70)
    coordinator.async_set_position.assert_awaited_once_with(70)
    await group_cover.async_open_cover()
    coordinator.async_set_position.assert_awaited_with(100)
    await group_cover.async_close_cover()
    coordinator.async_set_position.assert_awaited_with(0)
    await group_cover.async_stop_cover()
    coordinator.async_stop.assert_awaited_once()


async def test_group_cover_tilt_only_when_all_members_tilt(hass) -> None:
    """Tilt features appear only when every member (ACP + generic) tilts."""
    from homeassistant.components.cover import CoverEntityFeature

    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES
    from custom_components.adaptive_cover_pro.group_entities import AdaptiveGroupCover

    # Mixed group: blind member (no tilt axis) → no tilt features.
    blind_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "b", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_ENTITIES: ["cover.b1"]},
        entry_id="m_blind",
        title="b",
    )
    blind_entry.add_to_hass(hass)
    blind_entry.runtime_data = MagicMock()
    mixed = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: ["m_blind"], CONF_MEMBER_COVERS: []},
        entry_id="group_08",
        title="G",
    )
    mixed.add_to_hass(hass)
    mixed_cover = AdaptiveGroupCover(
        "group_08", hass, mixed, GroupCoordinator(hass, mixed)
    )
    assert not mixed_cover.supported_features & CoverEntityFeature.SET_TILT_POSITION

    # All-venetian group + tilt-capable generic → tilt exposed.
    ven_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "v", CONF_SENSOR_TYPE: CoverType.VENETIAN},
        options={CONF_ENTITIES: ["cover.v1"]},
        entry_id="m_ven",
        title="v",
    )
    ven_entry.add_to_hass(hass)
    ven_entry.runtime_data = MagicMock()
    hass.states.async_set(
        "cover.gt1",
        "open",
        {"supported_features": int(CoverEntityFeature.SET_TILT_POSITION)},
    )
    tilty = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G2", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: ["m_ven"], CONF_MEMBER_COVERS: ["cover.gt1"]},
        entry_id="group_09",
        title="G2",
    )
    tilty.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, tilty)
    tilt_cover = AdaptiveGroupCover("group_09", hass, tilty, coordinator)
    assert tilt_cover.supported_features & CoverEntityFeature.SET_TILT_POSITION

    coordinator.async_set_tilt = AsyncMock()
    await tilt_cover.async_set_cover_tilt_position(tilt_position=40)
    coordinator.async_set_tilt.assert_awaited_once_with(40)


async def test_group_cover_edge_cases(hass) -> None:
    """Empty roster → no tilt; unknown aggregates → is_closed None; generic
    cover without tilt bit → no tilt; removed member id skipped.
    """
    from homeassistant.components.cover import CoverEntityFeature

    from custom_components.adaptive_cover_pro.group_entities import AdaptiveGroupCover

    empty = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "E", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["gone_member"],
            CONF_MEMBER_COVERS: ["cover.no_tilt"],
        },
        entry_id="group_20",
        title="E",
    )
    empty.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, empty)
    hass.states.async_set("cover.no_tilt", "open", {"supported_features": 0})
    group_cover = AdaptiveGroupCover("group_20", hass, empty, coordinator)

    # gone_member is skipped; cover.no_tilt lacks the tilt bit → no tilt.
    assert not group_cover.supported_features & CoverEntityFeature.SET_TILT_POSITION

    coordinator.async_set_updated_data(
        GroupAggregates(position=None, state=GroupState.UNKNOWN, member_positions={})
    )
    assert group_cover.is_closed is None

    # A group with no members at all is not tiltable.
    bare = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "B", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: []},
        entry_id="group_21",
        title="B",
    )
    bare.add_to_hass(hass)
    assert GroupCoordinator(hass, bare).all_members_tilt() is False
