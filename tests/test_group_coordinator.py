"""Behaviour of the ``GroupCoordinator`` (issue #790, Phase 1).

Covers the three fan-out operations (scene activation, bulk automation,
bulk override clear), the mid-reload null-guard on member resolution, and
the position/state aggregates the group sensors read.
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
    POSITION_CLOSED,
    POSITION_OPEN,
    CoverType,
    GroupScene,
    GroupState,
)
from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator

pytestmark = pytest.mark.integration

BLIND_ENTITY = "cover.blind1"
AWNING_ENTITY = "cover.awning1"
GENERIC_ENTITY = "cover.generic1"


def _member_entry(
    hass, entry_id: str, cover_type: CoverType, entities: list[str]
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: cover_type},
        options={CONF_ENTITIES: entities},
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


def _mock_member_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))
    coord.async_reset_manual_overrides = AsyncMock(return_value=[])
    coord.async_refresh = AsyncMock()
    return coord


@pytest.fixture
def group_setup(hass):
    """Build a group with a blind member, an awning member, and one generic cover."""
    blind_entry = _member_entry(hass, "member_blind", CoverType.BLIND, [BLIND_ENTITY])
    awning_entry = _member_entry(
        hass, "member_awning", CoverType.AWNING, [AWNING_ENTITY]
    )
    blind_coord = _mock_member_coordinator()
    awning_coord = _mock_member_coordinator()
    blind_entry.runtime_data = blind_coord
    awning_entry.runtime_data = awning_coord

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Living Room", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["member_blind", "member_awning"],
            CONF_MEMBER_COVERS: [GENERIC_ENTITY],
        },
        entry_id="group_01",
        title="Living Room",
    )
    group_entry.add_to_hass(hass)

    coordinator = GroupCoordinator(hass, group_entry)
    # Adopt-mode command service is real by default; tests that exercise the
    # adopt fan-out replace it with a mock to observe the calls.
    coordinator._cmd_svc = MagicMock(
        apply_position=AsyncMock(return_value=("sent", "")), stop=MagicMock()
    )
    return coordinator, blind_coord, awning_coord


async def test_member_resolution_skips_unset_runtime_data(hass) -> None:
    """A member whose entry is mid-reload (runtime_data unset) is skipped."""
    ok_entry = _member_entry(hass, "member_ok", CoverType.BLIND, [BLIND_ENTITY])
    ok_coord = _mock_member_coordinator()
    ok_entry.runtime_data = ok_coord
    # mid-reload member: entry exists but runtime_data never set
    _member_entry(hass, "member_reloading", CoverType.BLIND, ["cover.x"])
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            # includes a removed entry id too — must also be skipped
            CONF_MEMBER_ENTRIES: ["member_ok", "member_reloading", "member_gone"],
            CONF_MEMBER_COVERS: [],
        },
        entry_id="group_02",
        title="G",
    )
    group_entry.add_to_hass(hass)

    coordinator = GroupCoordinator(hass, group_entry)
    resolved = coordinator.resolved_members()

    assert [entry.entry_id for entry, _ in resolved] == ["member_ok"]
    assert resolved[0][1] is ok_coord


async def test_activate_scene_resolves_target_per_member_policy(group_setup) -> None:
    """PRIVACY resolves through each member's own policy: blind 0, awning 100."""
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_activate_scene(GroupScene.PRIVACY)

    blind_coord.async_apply_user_position.assert_awaited_once_with(
        BLIND_ENTITY,
        POSITION_CLOSED,
        trigger="group_scene_privacy",
    )
    awning_coord.async_apply_user_position.assert_awaited_once_with(
        AWNING_ENTITY,
        POSITION_OPEN,
        trigger="group_scene_privacy",
    )


async def test_activate_scene_adopt_commands_generic_covers(group_setup) -> None:
    """Generic ``cover.*`` members are commanded through the group's own service."""
    coordinator, _, _ = group_setup

    await coordinator.async_activate_scene(GroupScene.ALL_CLOSED)

    coordinator._cmd_svc.apply_position.assert_awaited_once()
    args, kwargs = coordinator._cmd_svc.apply_position.await_args
    assert args[0] == GENERIC_ENTITY
    assert args[1] == POSITION_CLOSED
    context = kwargs.get("context") or args[3]
    assert context.force is True
    assert context.auto_control is True


async def test_activate_scene_records_active_scene(group_setup) -> None:
    """The last activated scene is recorded for the select/sensor entities."""
    coordinator, _, _ = group_setup
    assert coordinator.active_scene is None

    await coordinator.async_activate_scene(GroupScene.ALL_OPEN)

    assert coordinator.active_scene is GroupScene.ALL_OPEN


async def test_set_automation_flips_member_toggles(group_setup) -> None:
    """Bulk automation off sets each member's automatic_control and refreshes."""
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_set_automation(False)

    for member in (blind_coord, awning_coord):
        assert member.automatic_control is False
        member.async_refresh.assert_awaited_once()

    await coordinator.async_set_automation(True)
    assert blind_coord.automatic_control is True


async def test_clear_overrides_delegates_to_members(group_setup) -> None:
    """Bulk clear rides each member's shared reset path."""
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_clear_overrides()

    blind_coord.async_reset_manual_overrides.assert_awaited_once_with(
        trigger="group_clear_overrides"
    )
    awning_coord.async_reset_manual_overrides.assert_awaited_once_with(
        trigger="group_clear_overrides"
    )


async def test_member_cover_entities_union(group_setup) -> None:
    """ACP members' controlled covers + generic covers, in roster order."""
    coordinator, _, _ = group_setup
    assert coordinator.member_cover_entities() == [
        BLIND_ENTITY,
        AWNING_ENTITY,
        GENERIC_ENTITY,
    ]


@pytest.mark.parametrize(
    ("positions", "expected_state", "expected_position"),
    [
        (
            {BLIND_ENTITY: 100, AWNING_ENTITY: 100, GENERIC_ENTITY: 100},
            GroupState.OPEN,
            100,
        ),
        ({BLIND_ENTITY: 0, AWNING_ENTITY: 0, GENERIC_ENTITY: 0}, GroupState.CLOSED, 0),
        (
            {BLIND_ENTITY: 100, AWNING_ENTITY: 0, GENERIC_ENTITY: 50},
            GroupState.MIXED,
            50,
        ),
        ({}, GroupState.UNKNOWN, None),
    ],
)
async def test_aggregates(
    hass, group_setup, positions, expected_state, expected_position
) -> None:
    """Aggregate = average of readable member positions + state classification."""
    coordinator, _, _ = group_setup
    for entity_id, pos in positions.items():
        hass.states.async_set(entity_id, "open", {"current_position": pos})

    aggregates = await coordinator._async_update_data()

    assert aggregates.state is expected_state
    assert aggregates.position == expected_position
    if positions:
        assert aggregates.member_positions == positions


async def test_member_cover_entities_skips_removed_entries(hass) -> None:
    """A roster id whose entry was removed contributes no entities anywhere."""
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["member_gone"],
            CONF_MEMBER_COVERS: [GENERIC_ENTITY],
        },
        entry_id="group_03",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)

    assert coordinator.member_cover_entities() == [GENERIC_ENTITY]
    aggregates = await coordinator._async_update_data()
    assert list(aggregates.member_positions) == [GENERIC_ENTITY]


async def test_member_state_change_triggers_refresh(hass, group_setup) -> None:
    """A member cover state change schedules an aggregate refresh."""
    coordinator, _, _ = group_setup

    await coordinator._async_setup()
    assert coordinator._unsub_state is not None

    coordinator.async_request_refresh = AsyncMock()
    hass.states.async_set(BLIND_ENTITY, "open", {"current_position": 50})
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_awaited()

    await coordinator.async_shutdown()
    assert coordinator._unsub_state is None
    coordinator._cmd_svc.stop.assert_called_once()
