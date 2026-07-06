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
    CONF_GROUP_MEMBER_OPT_OUT,
    CONF_GROUP_STAGGER_DELAY,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DOMAIN,
    GROUP_SCENE_PRIORITY,
    OPT_OUT_ALL_SCENES,
    POSITION_CLOSED,
    CoverType,
    GroupIntentKind,
    GroupScene,
    GroupState,
)
from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator
from custom_components.adaptive_cover_pro.pipeline.types import GroupIntent

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
    coord.async_request_refresh = AsyncMock()
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


async def test_activate_scene_pushes_intent_and_refreshes(group_setup) -> None:
    """Phase 2: scenes ride the pipeline — intent push + refresh, never the
    user-position path (which would engage manual override).
    """
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_activate_scene(GroupScene.PRIVACY)

    expected = GroupIntent(
        kind=GroupIntentKind.SCENE,
        scene=GroupScene.PRIVACY,
        priority=GROUP_SCENE_PRIORITY,
        group_id="group_01",
    )
    for member in (blind_coord, awning_coord):
        member.set_group_intent.assert_called_once_with("group_01", expected)
        member.async_request_refresh.assert_awaited_once()
        member.async_apply_user_position.assert_not_awaited()


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


# ---------------------------------------------------------------------------
# Phase 2 — intent arbitration, opt-out, stagger, lock, clear, who-won
# ---------------------------------------------------------------------------


def _group_with_options(hass, extra_options, entry_id="group_10"):
    blind_entry = _member_entry(
        hass, f"{entry_id}_blind", CoverType.BLIND, [BLIND_ENTITY]
    )
    awning_entry = _member_entry(
        hass, f"{entry_id}_awning", CoverType.AWNING, [AWNING_ENTITY]
    )
    blind_coord = _mock_member_coordinator()
    awning_coord = _mock_member_coordinator()
    blind_entry.runtime_data = blind_coord
    awning_entry.runtime_data = awning_coord
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: [f"{entry_id}_blind", f"{entry_id}_awning"],
            CONF_MEMBER_COVERS: [GENERIC_ENTITY],
            **extra_options,
        },
        entry_id=entry_id,
        title=entry_id,
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)
    coordinator._cmd_svc = MagicMock(
        apply_position=AsyncMock(return_value=("sent", "")), stop=MagicMock()
    )
    return coordinator, blind_coord, awning_coord


async def test_opt_out_skips_member_for_that_scene_only(hass) -> None:
    """A member opted out of PRIVACY is skipped for it but included elsewhere."""
    coordinator, blind_coord, awning_coord = _group_with_options(
        hass, {CONF_GROUP_MEMBER_OPT_OUT: {"group_10_blind": [str(GroupScene.PRIVACY)]}}
    )

    await coordinator.async_activate_scene(GroupScene.PRIVACY)
    blind_coord.set_group_intent.assert_not_called()
    awning_coord.set_group_intent.assert_called_once()

    blind_coord.reset_mock()
    awning_coord.reset_mock()
    await coordinator.async_activate_scene(GroupScene.ALL_OPEN)
    blind_coord.set_group_intent.assert_called_once()
    awning_coord.set_group_intent.assert_called_once()


async def test_opt_out_star_skips_member_for_all_scenes(hass) -> None:
    coordinator, blind_coord, _ = _group_with_options(
        hass, {CONF_GROUP_MEMBER_OPT_OUT: {"group_10_blind": [OPT_OUT_ALL_SCENES]}}
    )

    await coordinator.async_activate_scene(GroupScene.ALL_CLOSED)

    blind_coord.set_group_intent.assert_not_called()


async def test_clear_scene_removes_intents(group_setup) -> None:
    """Clearing the scene removes this group's claim and refreshes members."""
    coordinator, blind_coord, awning_coord = group_setup
    await coordinator.async_activate_scene(GroupScene.ALL_OPEN)
    assert coordinator.active_scene is GroupScene.ALL_OPEN

    for member in (blind_coord, awning_coord):
        member.reset_mock()
    await coordinator.async_clear_scene()

    assert coordinator.active_scene is None
    for member in (blind_coord, awning_coord):
        member.set_group_intent.assert_called_once_with("group_01", None)
        member.async_request_refresh.assert_awaited_once()


async def test_lock_pushes_and_clears_lock_intent(group_setup) -> None:
    """The group lock is a LOCK intent at safety priority on every member."""
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_set_lock(True)
    assert coordinator.group_locked is True
    expected = GroupIntent(
        kind=GroupIntentKind.LOCK,
        scene=None,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        group_id="group_01",
    )
    for member in (blind_coord, awning_coord):
        member.set_group_intent.assert_called_once_with("group_01", expected)

    for member in (blind_coord, awning_coord):
        member.reset_mock()
    await coordinator.async_set_lock(False)
    assert coordinator.group_locked is False
    for member in (blind_coord, awning_coord):
        member.set_group_intent.assert_called_once_with("group_01", None)


async def test_lock_ignores_scene_opt_out(hass) -> None:
    """Opt-out is per-scene; the lock is a safety claim on every member."""
    coordinator, blind_coord, _ = _group_with_options(
        hass, {CONF_GROUP_MEMBER_OPT_OUT: {"group_10_blind": [OPT_OUT_ALL_SCENES]}}
    )

    await coordinator.async_set_lock(True)

    blind_coord.set_group_intent.assert_called_once()


async def test_stagger_spaces_member_commands(hass) -> None:
    """With a stagger configured, successive commands are spaced apart."""
    from custom_components.adaptive_cover_pro import group_coordinator as gc_module

    coordinator, _, _ = _group_with_options(
        hass, {CONF_GROUP_STAGGER_DELAY: 1.5}, entry_id="group_11"
    )

    with pytest.MonkeyPatch.context() as mp:
        sleeper = AsyncMock()
        mp.setattr(gc_module.asyncio, "sleep", sleeper)
        await coordinator.async_activate_scene(GroupScene.ALL_OPEN)

    # 2 ACP members + 1 generic = 3 commands → 2 gaps.
    assert sleeper.await_count == 2
    sleeper.assert_awaited_with(1.5)


async def test_no_stagger_no_sleep(group_setup) -> None:
    from custom_components.adaptive_cover_pro import group_coordinator as gc_module

    coordinator, _, _ = group_setup
    with pytest.MonkeyPatch.context() as mp:
        sleeper = AsyncMock()
        mp.setattr(gc_module.asyncio, "sleep", sleeper)
        await coordinator.async_activate_scene(GroupScene.ALL_OPEN)

    sleeper.assert_not_awaited()


async def test_shutdown_clears_group_intents(group_setup) -> None:
    """A group being unloaded must not leave stale intents on members."""
    coordinator, blind_coord, awning_coord = group_setup
    await coordinator.async_activate_scene(GroupScene.PRIVACY)

    await coordinator.async_shutdown()

    for member in (blind_coord, awning_coord):
        assert member.set_group_intent.call_args_list[-1].args == ("group_01", None)


async def test_member_winners_maps_entities_to_pipeline_winner(group_setup) -> None:
    """Who-won: each member cover mapped to its pipeline's winning handler."""
    coordinator, blind_coord, awning_coord = group_setup
    blind_coord.pipeline_winner_name = "group_scene"
    awning_coord.pipeline_winner_name = "weather_override"

    winners = coordinator.member_winners()

    assert winners == {
        BLIND_ENTITY: "group_scene",
        AWNING_ENTITY: "weather_override",
    }


async def test_unlock_repushes_active_scene(group_setup) -> None:
    """Unlocking with a scene active re-pushes the scene, not unmanaged state."""
    coordinator, blind_coord, _ = group_setup
    await coordinator.async_activate_scene(GroupScene.PRIVACY)
    await coordinator.async_set_lock(True)

    blind_coord.reset_mock()
    await coordinator.async_set_lock(False)

    pushed = [
        call.args[1]
        for call in blind_coord.set_group_intent.call_args_list
        if call.args[1] is not None
    ]
    assert pushed and pushed[-1].kind is GroupIntentKind.SCENE
    assert pushed[-1].scene is GroupScene.PRIVACY


# ---------------------------------------------------------------------------
# Phase 3 — climate rollup + cover fan-out
# ---------------------------------------------------------------------------


def _set_member_climate(coord: MagicMock, *, is_summer=False, is_winter=False) -> None:
    coord.data.diagnostics = {
        "climate_conditions": {"is_summer": is_summer, "is_winter": is_winter}
    }


async def test_member_climate_modes_maps_entities(group_setup) -> None:
    """Each ACP member's cover entities map to its climate mode; generic
    covers (no pipeline) are excluded; missing diagnostics → None.
    """
    coordinator, blind_coord, awning_coord = group_setup
    _set_member_climate(blind_coord, is_summer=True)
    awning_coord.data.diagnostics = None  # climate mode off / not yet built

    modes = coordinator.member_climate_modes()

    assert modes == {
        BLIND_ENTITY: "summer_mode",
        AWNING_ENTITY: None,
    }


async def test_member_climate_modes_winter_and_intermediate(group_setup) -> None:
    coordinator, blind_coord, awning_coord = group_setup
    _set_member_climate(blind_coord, is_winter=True)
    _set_member_climate(awning_coord)  # neither flag → intermediate

    modes = coordinator.member_climate_modes()

    assert modes[BLIND_ENTITY] == "winter_mode"
    assert modes[AWNING_ENTITY] == "intermediate"


async def test_set_position_fans_out_user_positions(group_setup) -> None:
    """A group cover drag is a user action: member user-position path +
    adopt-mode command for generic covers.
    """
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_set_position(60)

    blind_coord.async_apply_user_position.assert_awaited_once_with(
        BLIND_ENTITY, 60, trigger="group_cover"
    )
    awning_coord.async_apply_user_position.assert_awaited_once_with(
        AWNING_ENTITY, 60, trigger="group_cover"
    )
    coordinator._cmd_svc.apply_position.assert_awaited_once()
    args, kwargs = coordinator._cmd_svc.apply_position.await_args
    assert args[0] == GENERIC_ENTITY
    assert args[1] == 60


async def test_set_position_staggers_commands(hass) -> None:
    from custom_components.adaptive_cover_pro import group_coordinator as gc_module

    coordinator, _, _ = _group_with_options(
        hass, {CONF_GROUP_STAGGER_DELAY: 2.0}, entry_id="group_12"
    )

    with pytest.MonkeyPatch.context() as mp:
        sleeper = AsyncMock()
        mp.setattr(gc_module.asyncio, "sleep", sleeper)
        await coordinator.async_set_position(50)

    assert sleeper.await_count == 2  # 3 commands → 2 gaps
    sleeper.assert_awaited_with(2.0)


async def test_set_tilt_fans_out_user_tilts(hass, group_setup) -> None:
    """Tilt rides the dedicated tilt path (#684) for ACP members and the
    tilt service for generic covers.
    """
    from pytest_homeassistant_custom_component.common import async_mock_service

    coordinator, blind_coord, awning_coord = group_setup
    blind_coord.async_apply_user_tilt = AsyncMock()
    awning_coord.async_apply_user_tilt = AsyncMock()
    tilt_calls = async_mock_service(hass, "cover", "set_cover_tilt_position")

    await coordinator.async_set_tilt(30)
    await hass.async_block_till_done()

    blind_coord.async_apply_user_tilt.assert_awaited_once_with(
        BLIND_ENTITY, 30, trigger="group_cover_tilt"
    )
    awning_coord.async_apply_user_tilt.assert_awaited_once_with(
        AWNING_ENTITY, 30, trigger="group_cover_tilt"
    )
    assert len(tilt_calls) == 1
    assert tilt_calls[0].data == {
        "entity_id": GENERIC_ENTITY,
        "tilt_position": 30,
    }


async def test_stop_calls_stop_service_per_member_cover(hass, group_setup) -> None:
    from pytest_homeassistant_custom_component.common import async_mock_service

    coordinator, _, _ = group_setup
    stop_calls = async_mock_service(hass, "cover", "stop_cover")

    await coordinator.async_stop()
    await hass.async_block_till_done()

    assert [call.data["entity_id"] for call in stop_calls] == [
        BLIND_ENTITY,
        AWNING_ENTITY,
        GENERIC_ENTITY,
    ]
