"""Behaviour of the ``GroupCoordinator`` (issue #790, Phase 1).

Covers the three fan-out operations (scene activation, bulk automation,
bulk override clear), the mid-reload null-guard on member resolution, and
the position/state aggregates the group sensors read.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_ENTITIES,
    CONF_GROUP_MEMBER_OPT_OUT,
    CONF_GROUP_STAGGER_DELAY,
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
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
from custom_components.adaptive_cover_pro.pipeline.handlers import GroupLockHandler
from custom_components.adaptive_cover_pro.pipeline.types import GroupIntent
from tests._helpers.group_members import UNCLAIMED_WINNER, RealMemberCoordinator

pytestmark = pytest.mark.integration

BLIND_ENTITY = "cover.blind1"
AWNING_ENTITY = "cover.awning1"
GENERIC_ENTITY = "cover.generic1"


def _member_entry(
    hass,
    entry_id: str,
    cover_type: CoverType,
    entities: list[str],
    extra_options: dict | None = None,
    state: ConfigEntryState = ConfigEntryState.LOADED,
) -> MockConfigEntry:
    """Build a member entry.

    Defaults to ``LOADED`` because that is what a member the group can act on
    actually looks like: ``resolved_members`` skips anything that has not
    finished setup, since HA assigns ``runtime_data`` before platform setup and
    a toggle written to a half-built coordinator is undone when its entities
    restore (issue #1063).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: cover_type},
        options={CONF_ENTITIES: entities, **(extra_options or {})},
        entry_id=entry_id,
        title=entry_id,
        state=state,
    )
    entry.add_to_hass(hass)
    return entry


def _mock_member_coordinator() -> MagicMock:
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord = MagicMock()
    # The group's per-member fan-out reads each member's own ordered dispatch
    # view (#1115), so the mock needs the real default policy the member
    # coordinator would carry — a MagicMock policy iterates as empty.
    coord._policy = get_policy(CoverType.BLIND)
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))
    coord.async_reset_manual_overrides = AsyncMock(return_value=[])
    coord.async_refresh = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def group_setup(hass):
    """Build a group with a blind member, an awning member, and one generic cover.

    Both members carry ``CONF_CLIMATE_MODE`` so they can hold a bulk climate
    command — ``async_set_climate_mode`` skips members that expose no Climate
    Mode switch to persist it (issue #1063).
    """
    blind_entry = _member_entry(
        hass,
        "member_blind",
        CoverType.BLIND,
        [BLIND_ENTITY],
        extra_options={CONF_CLIMATE_MODE: True},
    )
    awning_entry = _member_entry(
        hass,
        "member_awning",
        CoverType.AWNING,
        [AWNING_ENTITY],
        extra_options={CONF_CLIMATE_MODE: True},
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
        member.async_refresh.assert_awaited_once()
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


async def test_set_climate_mode_flips_member_switch_mode(group_setup) -> None:
    """Bulk climate on sets each member's switch_mode and refreshes."""
    coordinator, blind_coord, awning_coord = group_setup

    await coordinator.async_set_climate_mode(True)

    for member in (blind_coord, awning_coord):
        assert member.switch_mode is True
        member.async_refresh.assert_awaited_once()

    await coordinator.async_set_climate_mode(False)
    assert blind_coord.switch_mode is False
    assert awning_coord.switch_mode is False


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
        member.async_refresh.assert_awaited_once()


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
# Issue #1082 — intent pushes must re-evaluate the member immediately
# ---------------------------------------------------------------------------


def _group_with_real_members(hass, entry_id="group_20"):
    """Build a group whose members carry real listener + debouncer plumbing."""
    blind_entry = _member_entry(
        hass, f"{entry_id}_blind", CoverType.BLIND, [BLIND_ENTITY]
    )
    awning_entry = _member_entry(
        hass, f"{entry_id}_awning", CoverType.AWNING, [AWNING_ENTITY]
    )
    blind_coord = RealMemberCoordinator(hass, blind_entry)
    awning_coord = RealMemberCoordinator(hass, awning_entry)
    blind_entry.runtime_data = blind_coord
    awning_entry.runtime_data = awning_coord
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: [f"{entry_id}_blind", f"{entry_id}_awning"],
            CONF_MEMBER_COVERS: [],
        },
        entry_id=entry_id,
        title=entry_id,
    )
    group_entry.add_to_hass(hass)
    return GroupCoordinator(hass, group_entry), blind_coord, awning_coord


async def test_lock_release_inside_debounce_window_refreshes_members(hass) -> None:
    """Releasing the lock seconds after engaging it still re-evaluates members.

    ``async_request_refresh`` is debounced (10s cooldown, ``immediate=True``), so
    a second push inside that window is DEFERRED: the member still reports the
    lock as its winner at the instant the group publishes to its listeners, and
    the who-won sensor latches that stale value (issue #1082). The two toggles
    below are milliseconds apart — trivially inside the cooldown, which is why
    the live report toggled at 19:23:45 and 19:23:47 and stuck.
    """
    coordinator, blind_coord, awning_coord = _group_with_real_members(hass)

    await coordinator.async_set_lock(True)
    assert coordinator.member_winners() == {
        BLIND_ENTITY: GroupLockHandler.name,
        AWNING_ENTITY: GroupLockHandler.name,
    }

    await coordinator.async_set_lock(False)
    assert coordinator.member_winners() == {
        BLIND_ENTITY: UNCLAIMED_WINNER,
        AWNING_ENTITY: UNCLAIMED_WINNER,
    }

    await coordinator.async_shutdown()
    for member in (blind_coord, awning_coord):
        await member.async_shutdown()


@pytest.mark.parametrize(
    "action",
    [
        pytest.param(
            lambda c: c.async_activate_scene(GroupScene.PRIVACY), id="scene_activate"
        ),
        pytest.param(lambda c: c.async_clear_scene(), id="scene_clear"),
        pytest.param(lambda c: c.async_set_lock(True), id="lock_engage"),
        pytest.param(lambda c: c.async_set_lock(False), id="lock_release"),
        pytest.param(lambda c: c.async_shutdown(), id="shutdown"),
    ],
)
async def test_intent_pushes_use_the_non_debounced_refresh(group_setup, action) -> None:
    """Every group→member intent push re-evaluates the member immediately.

    ``async_request_refresh`` would route the push through HA's 10-second
    request debouncer, which is what left the who-won sensor stale (#1082).
    """
    coordinator, blind_coord, awning_coord = group_setup

    await action(coordinator)

    for member in (blind_coord, awning_coord):
        member.async_refresh.assert_awaited()
        member.async_request_refresh.assert_not_awaited()


async def test_lock_release_staggers_but_engage_does_not(hass) -> None:
    """Release moves covers so it staggers; engage holds position so it does not."""
    from custom_components.adaptive_cover_pro import group_coordinator as gc_module

    coordinator, _, _ = _group_with_options(
        hass, {CONF_GROUP_STAGGER_DELAY: 1.5}, entry_id="group_12"
    )

    with pytest.MonkeyPatch.context() as mp:
        sleeper = AsyncMock()
        mp.setattr(gc_module.asyncio, "sleep", sleeper)
        await coordinator.async_set_lock(True)
        assert sleeper.await_count == 0

        await coordinator.async_set_lock(False)

    # 2 ACP members → 1 gap. Generic covers are not touched by the lock.
    assert sleeper.await_count == 1
    sleeper.assert_awaited_with(1.5)


async def test_roster_change_refreshes_aggregates(hass) -> None:
    """A member joining mid-run must also recompute the position aggregates.

    Repainting alone would publish ``member_positions`` for the old roster.
    """
    member_entry = _member_entry(
        hass,
        "joining_member",
        CoverType.BLIND,
        [BLIND_ENTITY],
        state=ConfigEntryState.NOT_LOADED,
    )
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: ["joining_member"], CONF_MEMBER_COVERS: []},
        entry_id="group_join",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)
    await coordinator._async_setup()

    member_coord = RealMemberCoordinator(hass, member_entry)
    member_entry.runtime_data = member_coord
    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        member_entry.mock_state(hass, ConfigEntryState.LOADED)
        await hass.async_block_till_done()

    refresh.assert_awaited()

    await coordinator.async_shutdown()
    await member_coord.async_shutdown()


async def test_unlock_with_active_scene_pushes_once_per_member(group_setup) -> None:
    """Releasing the lock hands each member its FINAL intent in one push.

    Clearing and then re-pushing the scene would make every member evaluate
    once un-scened in between — with immediate refreshes that is a visible jog
    the request debouncer used to swallow (#1082).
    """
    coordinator, blind_coord, awning_coord = group_setup
    await coordinator.async_activate_scene(GroupScene.PRIVACY)
    await coordinator.async_set_lock(True)

    for member in (blind_coord, awning_coord):
        member.reset_mock()
    await coordinator.async_set_lock(False)

    for member in (blind_coord, awning_coord):
        assert member.set_group_intent.call_count == 1
        assert member.async_refresh.await_count == 1
        intent = member.set_group_intent.call_args.args[1]
        assert intent.kind is GroupIntentKind.SCENE
        assert intent.scene is GroupScene.PRIVACY


async def test_unlock_leaves_opted_out_member_unscened(hass) -> None:
    """A member opted out of the active scene ends on no intent, in one push."""
    coordinator, blind_coord, awning_coord = _group_with_options(
        hass, {CONF_GROUP_MEMBER_OPT_OUT: {"group_10_blind": [OPT_OUT_ALL_SCENES]}}
    )
    await coordinator.async_activate_scene(GroupScene.PRIVACY)
    await coordinator.async_set_lock(True)

    for member in (blind_coord, awning_coord):
        member.reset_mock()
    await coordinator.async_set_lock(False)

    assert blind_coord.set_group_intent.call_count == 1
    assert blind_coord.set_group_intent.call_args.args[1] is None
    assert awning_coord.set_group_intent.call_args.args[1].scene is GroupScene.PRIVACY


# ---------------------------------------------------------------------------
# Issue #1082 — member-coordinator subscriptions track the roster's lifecycle
# ---------------------------------------------------------------------------


async def test_member_loaded_after_group_setup_is_subscribed(hass) -> None:
    """A member that finishes setup after the group did still gets subscribed.

    HA loads entries independently, so the group can reach ``_async_setup``
    while a member is still ``NOT_LOADED`` and invisible to ``resolved_members``.
    """
    member_entry = _member_entry(
        hass,
        "late_member",
        CoverType.BLIND,
        [BLIND_ENTITY],
        state=ConfigEntryState.NOT_LOADED,
    )
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: ["late_member"], CONF_MEMBER_COVERS: []},
        entry_id="group_late",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)

    await coordinator._async_setup()
    assert coordinator._member_subs == {}

    member_coord = RealMemberCoordinator(hass, member_entry)
    member_entry.runtime_data = member_coord
    member_entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()

    assert list(coordinator._member_subs) == ["late_member"]

    await coordinator.async_shutdown()
    await member_coord.async_shutdown()


async def test_member_reload_swaps_the_subscription(hass) -> None:
    """A reloaded member keeps its entry id but gets a NEW coordinator object.

    Diffing on the entry id alone would leave the listener on the dead
    coordinator, so the group would stop hearing from the live one.
    """
    member_entry = _member_entry(hass, "member_a", CoverType.BLIND, [BLIND_ENTITY])
    first = RealMemberCoordinator(hass, member_entry)
    member_entry.runtime_data = first
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: ["member_a"], CONF_MEMBER_COVERS: []},
        entry_id="group_reload",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)

    await coordinator._async_setup()
    assert coordinator._member_subs["member_a"].coordinator is first

    member_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert coordinator._member_subs == {}

    second = RealMemberCoordinator(hass, member_entry)
    member_entry.runtime_data = second
    member_entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()

    assert coordinator._member_subs["member_a"].coordinator is second

    await coordinator.async_shutdown()
    for member in (first, second):
        await member.async_shutdown()


async def test_shutdown_unsubscribes_member_listeners(hass) -> None:
    """Teardown releases the member and config-entry subscriptions."""
    coordinator, blind_coord, awning_coord = _group_with_real_members(
        hass, entry_id="group_21"
    )
    await coordinator._async_setup()
    assert len(coordinator._member_subs) == 2
    assert coordinator._unsub_entry_state is not None

    await coordinator.async_shutdown()

    assert coordinator._member_subs == {}
    assert coordinator._unsub_entry_state is None
    for member in (blind_coord, awning_coord):
        assert not member._listeners
        await member.async_shutdown()


async def test_member_subscription_sync_is_idempotent(hass) -> None:
    """Re-syncing an unchanged roster keeps the same subscriptions.

    The config-entry signal fires on every transition of every ACP entry, so a
    re-sync that churned subscriptions would resubscribe constantly and report
    a change that entity listeners would then act on.
    """
    coordinator, blind_coord, awning_coord = _group_with_real_members(
        hass, entry_id="group_22"
    )
    await coordinator._async_setup()
    before = dict(coordinator._member_subs)

    assert coordinator._sync_member_subscriptions() is False
    assert coordinator._member_subs == before

    await coordinator.async_shutdown()
    for member in (blind_coord, awning_coord):
        await member.async_shutdown()


async def test_config_entry_change_ignores_other_domains(hass, group_setup) -> None:
    """A non-ACP entry changing state is not this group's business."""
    coordinator, _, _ = group_setup
    await coordinator._async_setup()

    other = MockConfigEntry(domain="light", entry_id="other_01", title="Other")
    other.add_to_hass(hass)
    with patch.object(coordinator, "_sync_member_subscriptions") as sync:
        coordinator._handle_config_entry_change(None, other)

    sync.assert_not_called()

    await coordinator.async_shutdown()


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


# ---------------------------------------------------------------------------
# Phase 4 — area membership
# ---------------------------------------------------------------------------


def _registry_cover(hass, unique_id, object_id, *, area_id=None, platform="test"):
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    entry = reg.async_get_or_create(
        "cover", platform, unique_id, suggested_object_id=object_id
    )
    if area_id is not None:
        reg.async_update_entity(entry.entity_id, area_id=area_id)
    return entry.entity_id


@pytest.fixture
def area_setup(hass):
    """Build an area with one ACP-controlled cover and one free generic
    cover, plus an out-of-area ACP member for the static roster.
    """
    from homeassistant.helpers import area_registry as ar

    area = ar.async_get(hass).async_get_or_create("Living Room")

    acp_cover = _registry_cover(hass, "acp1", "acp_blind", area_id=area.id)
    generic_cover = _registry_cover(hass, "gen1", "free_cover", area_id=area.id)
    _registry_cover(hass, "elsewhere", "other_room_cover", area_id=None)
    # An ACP-owned proxy entity in the area must never be adopted.
    proxy_cover = _registry_cover(
        hass, "proxy1", "acp_proxy", area_id=area.id, platform=DOMAIN
    )

    area_member = _member_entry(hass, "area_member", CoverType.BLIND, [acp_cover])
    area_member.runtime_data = _mock_member_coordinator()
    static_member = _member_entry(
        hass, "static_member", CoverType.AWNING, ["cover.static1"]
    )
    static_member.runtime_data = _mock_member_coordinator()

    from custom_components.adaptive_cover_pro.const import CONF_GROUP_AREA

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["static_member"],
            CONF_MEMBER_COVERS: ["cover.static_generic"],
            CONF_GROUP_AREA: area.id,
        },
        entry_id="group_area",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)
    return coordinator, acp_cover, generic_cover, proxy_cover


async def test_area_membership_resolves_acp_entries(area_setup) -> None:
    """Static roster ∪ ACP entries with a controlled cover in the area."""
    coordinator, _, _, _ = area_setup
    assert coordinator.member_entry_ids() == ["static_member", "area_member"]


async def test_area_membership_resolves_generic_covers(area_setup) -> None:
    """Area cover entities join the generic roster — except covers already
    controlled by an ACP entry (orchestrate wins) and ACP's own proxy
    entities.
    """
    coordinator, acp_cover, generic_cover, proxy_cover = area_setup

    generic = coordinator.generic_cover_ids()

    assert generic == ["cover.static_generic", generic_cover]
    assert acp_cover not in generic
    assert proxy_cover not in generic


async def test_area_membership_feeds_resolved_members(area_setup) -> None:
    coordinator, _, _, _ = area_setup
    resolved_ids = [entry.entry_id for entry, _ in coordinator.resolved_members()]
    assert resolved_ids == ["static_member", "area_member"]


async def test_area_membership_via_device_area(hass) -> None:
    """An entity with no own area inherits its device's area."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.adaptive_cover_pro.const import CONF_GROUP_AREA

    area = ar.async_get(hass).async_get_or_create("Bedroom")
    helper_entry = MockConfigEntry(domain="test", entry_id="helper")
    helper_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="helper",
        identifiers={("test", "dev1")},
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    reg_entry = er.async_get(hass).async_get_or_create(
        "cover",
        "test",
        "dev_cover",
        suggested_object_id="bed_cover",
        device_id=device.id,
    )

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: [],
            CONF_MEMBER_COVERS: [],
            CONF_GROUP_AREA: area.id,
        },
        entry_id="group_dev_area",
        title="G",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)

    assert coordinator.generic_cover_ids() == [reg_entry.entity_id]


async def test_no_area_behaves_statically(group_setup) -> None:
    """Without an area, the effective rosters equal the stored rosters."""
    coordinator, _, _ = group_setup
    assert coordinator.member_entry_ids() == ["member_blind", "member_awning"]
    assert coordinator.generic_cover_ids() == [GENERIC_ENTITY]


async def test_registry_change_reloads_when_roster_changes(hass, area_setup) -> None:
    """Moving a cover into the area changes the roster → one entry reload."""
    from unittest.mock import patch

    coordinator, _, _, _ = area_setup
    await coordinator._async_setup()

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_mock:
        # A registry event with no roster impact must not reload.
        hass.bus.async_fire(
            "entity_registry_updated",
            {"action": "update", "entity_id": "cover.other_room_cover"},
        )
        await hass.async_block_till_done()
        reload_mock.assert_not_awaited()

        # A new free cover appears in the area → roster changes → reload once.
        from homeassistant.helpers import area_registry as ar

        area = ar.async_get(hass).async_get_or_create("Living Room")
        _registry_cover(hass, "newgen", "new_free_cover", area_id=area.id)
        await hass.async_block_till_done()
        reload_mock.assert_awaited_once_with("group_area")

    await coordinator.async_shutdown()


async def test_registry_listener_absent_without_area(hass, group_setup) -> None:
    """Static-only groups subscribe to no registry events."""
    from unittest.mock import patch

    coordinator, _, _ = group_setup
    await coordinator._async_setup()

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_mock:
        hass.bus.async_fire(
            "entity_registry_updated", {"action": "create", "entity_id": "cover.x"}
        )
        await hass.async_block_till_done()
        reload_mock.assert_not_awaited()

    await coordinator.async_shutdown()


async def test_entity_area_id_unregistered_entity(group_setup) -> None:
    """An entity absent from the registry has no area."""
    coordinator, _, _ = group_setup
    assert coordinator._entity_area_id("cover.not_registered") is None


# ---------------------------------------------------------------------------
# Issue #1027: one group slider value, each member's own dispatch frame
# ---------------------------------------------------------------------------


def _dispatch_member_coordinator(options: dict, entity: str) -> MagicMock:
    """Build a member coord whose real user-position path runs to the dispatch seam."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.types import (
        DecisionStep,
        PipelineResult,
    )
    from tests.ha_helpers import bind_user_position_seam, wire_dispatch_frame
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock()
    coord.entities = [entity]
    coord.config_entry = MagicMock()
    coord.config_entry.options = options
    wire_dispatch_frame(coord, options)
    coord._resolved_options = options
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.build = MagicMock(return_value=make_snapshot())
    # Solar winner at priority 40 — below manual override, so a group drag is
    # not preempted and reaches dispatch.
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        decision_trace=[
            DecisionStep(handler="solar", matched=True, reason="solar", position=50)
        ],
    )
    solar = MagicMock()
    solar.priority = 40
    coord._handler_by_name = {"solar": solar}
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    bind_user_position_seam(coord)
    coord.async_reset_manual_overrides = AsyncMock(return_value=[])
    coord.async_refresh = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def _dispatched_position(coord: MagicMock) -> int:
    coord._cmd_svc.apply_position.assert_awaited_once()
    return coord._cmd_svc.apply_position.await_args.args[1]


def _group_with_dispatch_members(hass, member_options: dict[str, dict]):
    """Build a group whose ACP members each carry their own frame config."""
    entry_ids = []
    coords = {}
    for idx, (entity, options) in enumerate(member_options.items()):
        entry_id = f"frame_member_{idx}"
        # A real member stores its frame config in its OWN entry options, which
        # is where the group's aggregate has to read it from.
        entry = _member_entry(hass, entry_id, CoverType.BLIND, [entity], options)
        coord = _dispatch_member_coordinator(options, entity)
        entry.runtime_data = coord
        entry_ids.append(entry_id)
        coords[entity] = coord

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Frame Group", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: entry_ids,
            CONF_MEMBER_COVERS: [GENERIC_ENTITY],
        },
        entry_id="group_frame",
        title="Frame Group",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)
    coordinator._cmd_svc = MagicMock(
        apply_position=AsyncMock(return_value=("sent", "")), stop=MagicMock()
    )
    return coordinator, coords


async def test_group_member_dispatch_transforms_per_member_config(hass) -> None:
    """One group drag, two members, two frames — each member's own config wins.

    The group slider is a logical value like every other user surface, so an
    ``inverse_state`` member owes its cover the inverted number while a plain
    member sends it through unchanged (#1027). The non-ACP adopt path is
    deliberately untouched and keeps dispatching raw.
    """
    coordinator, coords = _group_with_dispatch_members(
        hass,
        {
            "cover.inverted_member": {CONF_INVERSE_STATE: True},
            "cover.plain_member": {},
        },
    )

    await coordinator.async_set_position(30)

    assert _dispatched_position(coords["cover.inverted_member"]) == 70
    assert _dispatched_position(coords["cover.plain_member"]) == 30
    # Generic (non-ACP) members ride the adopt path and stay raw.
    coordinator._cmd_svc.apply_position.assert_awaited_once()
    assert coordinator._cmd_svc.apply_position.await_args.args[1] == 30


async def test_group_member_dispatch_applies_member_interpolation(hass) -> None:
    """A calibrated member maps the group's linear value onto its motor curve."""
    coordinator, coords = _group_with_dispatch_members(
        hass,
        {
            "cover.calibrated_member": {
                CONF_INTERP: True,
                CONF_INTERP_LIST: [0, 25, 58, 100],
                CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
            },
            "cover.plain_member": {},
        },
    )

    await coordinator.async_set_position(25)

    assert _dispatched_position(coords["cover.calibrated_member"]) == 45
    assert _dispatched_position(coords["cover.plain_member"]) == 25


# ---------------------------------------------------------------------------
# Issue #1027: the group cover entity round-trips its own slider
# ---------------------------------------------------------------------------


def _report_positions(hass, positions: dict[str, int]) -> None:
    """Publish what each member cover was actually driven to."""
    for entity_id, pos in positions.items():
        hass.states.async_set(
            entity_id, "open", {"current_position": pos, "supported_features": 15}
        )


async def test_group_cover_round_trip_with_mixed_frame_members(hass) -> None:
    """Drag the group slider to 30 and the group settles on 30, not 43.

    The group entity is an ordinary HA cover: its slider value is logical, and
    ``async_set_position`` now hands that logical number to each ACP member,
    which re-frames it for its own hardware (#1027). The aggregate therefore
    has to normalise every member's reading back to the logical frame BEFORE
    averaging — a group can mix inverted and plain members, so un-inverting the
    average is not a thing that can work.
    """
    coordinator, coords = _group_with_dispatch_members(
        hass,
        {
            "cover.inverted_member": {CONF_INVERSE_STATE: True},
            "cover.plain_member": {},
        },
    )

    await coordinator.async_set_position(30)

    # Each member is driven in its own frame …
    assert _dispatched_position(coords["cover.inverted_member"]) == 70
    assert _dispatched_position(coords["cover.plain_member"]) == 30
    _report_positions(
        hass,
        {
            "cover.inverted_member": 70,
            "cover.plain_member": 30,
            GENERIC_ENTITY: 30,
        },
    )

    await coordinator.async_refresh()

    # … and the group reads back the one logical number the user asked for.
    assert coordinator.data.position == 30


async def test_group_state_normalises_before_deciding_open(hass) -> None:
    """Everything physically open reads as OPEN even with an inverted member.

    ``GroupState`` is decided from the same member readings as the average, so
    the raw-frame aggregate mislabelled a fully open group as MIXED whenever a
    member ran ``inverse_state``. That drives ``is_closed`` on the group cover
    entity, so it is user-visible, not just cosmetic.
    """
    coordinator, _coords = _group_with_dispatch_members(
        hass,
        {
            "cover.inverted_member": {CONF_INVERSE_STATE: True},
            "cover.plain_member": {},
        },
    )
    _report_positions(
        hass,
        {
            # Inverted hardware reports 0 when it is physically wide open.
            "cover.inverted_member": POSITION_CLOSED,
            "cover.plain_member": 100,
            GENERIC_ENTITY: 100,
        },
    )

    await coordinator.async_refresh()

    assert coordinator.data.position == 100
    assert coordinator.data.state is GroupState.OPEN
