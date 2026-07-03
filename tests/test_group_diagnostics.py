"""Config-entry diagnostics for cover-group entries (issue #790, Phase 4).

Pins the crash fix: the generic cover path read ``coordinator.data.diagnostics``,
which a ``GroupCoordinator``'s ``GroupAggregates`` does not have — downloading
diagnostics for a group entry raised AttributeError. The group branch returns
a rollup of per-member SUMMARIES (winner + position + climate), never full
member traces (§4 size mitigation).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_GROUP_STAGGER_DELAY,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
    GroupScene,
)
from custom_components.adaptive_cover_pro.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator

pytestmark = pytest.mark.integration


async def test_group_entry_diagnostics_rollup(hass) -> None:
    """A group entry download returns the rollup instead of crashing."""
    member = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "blind", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_ENTITIES: ["cover.blind1"]},
        entry_id="member_blind",
        title="Living Blind",
    )
    member.add_to_hass(hass)
    member_coord = MagicMock()
    member_coord.pipeline_winner_name = "group_scene"
    member_coord.data.diagnostics = {
        "climate_conditions": {"is_summer": True, "is_winter": False}
    }
    member.runtime_data = member_coord

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Living Room", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["member_blind"],
            CONF_MEMBER_COVERS: ["cover.generic1"],
            CONF_GROUP_STAGGER_DELAY: 1.5,
        },
        entry_id="group_01",
        title="Living Room",
    )
    group_entry.add_to_hass(hass)
    coordinator = GroupCoordinator(hass, group_entry)
    group_entry.runtime_data = coordinator
    hass.states.async_set("cover.blind1", "open", {"current_position": 40})
    hass.states.async_set("cover.generic1", "open", {"current_position": 60})
    await coordinator.async_refresh()
    coordinator.active_scene = GroupScene.PRIVACY
    coordinator.group_locked = True

    diagnostics = await async_get_config_entry_diagnostics(hass, group_entry)

    group = diagnostics["group"]
    assert group["active_scene"] == "privacy"
    assert group["group_locked"] is True
    assert group["aggregates"]["position"] == 50
    assert group["aggregates"]["member_positions"] == {
        "cover.blind1": 40,
        "cover.generic1": 60,
    }
    assert group["member_winners"] == {"cover.blind1": "group_scene"}
    assert group["member_climate_modes"] == {"cover.blind1": "summer_mode"}
    assert group["stagger_delay"] == 1.5
    rosters = group["rosters"]
    assert rosters["member_entries"] == [
        {"entry_id": "member_blind", "title": "Living Blind"}
    ]
    assert rosters["member_covers"] == ["cover.generic1"]
    # Envelope fields stay consistent with cover diagnostics.
    assert diagnostics["type"] == "config_entry"
    assert diagnostics["identifier"] == "group_01"


async def test_group_entry_diagnostics_before_first_refresh(hass) -> None:
    """No aggregates yet → still a valid rollup, no crash, no member commands."""
    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: []},
        entry_id="group_02",
        title="G",
    )
    group_entry.add_to_hass(hass)
    group_entry.runtime_data = GroupCoordinator(hass, group_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, group_entry)

    assert diagnostics["group"]["active_scene"] is None
    assert diagnostics["group"]["aggregates"]["position"] is None
