"""Config-flow surfaces for the Cover Group virtual entry type (issue #790).

Three surfaces:
- Creating a ``cover_group`` entry is its own top-level menu option whose
  combined form collects the name plus the two membership rosters (ACP
  members as config entries, generic covers as entities) on one page.
- A group's options flow shows a small group menu, never the cover menu.
- Membership is sanitized on save: duplicates dropped, the group itself
  cannot be a member, and a generic entity owned by a selected ACP member
  is dropped in favour of the entry.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.config_flow import (
    ConfigFlowHandler,
    OptionsFlowHandler,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DELTA_POSITION,
    CONF_ENTITIES,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_MOTION_SENSORS,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.profile_link import _cover_entries

pytestmark = pytest.mark.integration


def _schema_keys(schema):
    return {str(marker.schema) for marker in schema.schema}


def _select_options(schema, key):
    for marker, sel in schema.schema.items():
        if str(marker.schema) == key:
            return sel.config["options"]
    raise AssertionError(f"{key} not in schema")


def _add_cover(hass, entry_id: str, cover_type=CoverType.BLIND, entities=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: cover_type},
        options={CONF_ENTITIES: entities or []},
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


def _add_group(hass, entry_id: str, member_entries=None, member_covers=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: member_entries or [],
            CONF_MEMBER_COVERS: member_covers or [],
        },
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


async def test_user_menu_offers_create_group(hass: HomeAssistant) -> None:
    """Creating a group is a top-level create choice beside the profile."""
    handler = ConfigFlowHandler()
    handler.hass = hass

    result = await handler.async_step_user()

    assert result["type"] == "menu"
    assert "create_group" in result["menu_options"]


async def test_create_group_combined_form_and_entry(hass: HomeAssistant) -> None:
    """One form: name + the two membership selectors; finalizes a GROUP entry
    without injecting cover-automation defaults (delta, motion, ...).
    """
    _add_cover(hass, "cover_a", entities=["cover.a"])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create_group"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "create_group"
    from custom_components.adaptive_cover_pro.const import CONF_GROUP_AREA

    assert _schema_keys(result["data_schema"]) == {
        "name",
        CONF_MEMBER_ENTRIES,
        CONF_MEMBER_COVERS,
        CONF_GROUP_AREA,
    }
    # The ACP-member selector lists existing cover entries.
    values = {
        o["value"] for o in _select_options(result["data_schema"], CONF_MEMBER_ENTRIES)
    }
    assert values == {"cover_a"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Living Room",
            CONF_MEMBER_ENTRIES: ["cover_a"],
            CONF_MEMBER_COVERS: ["cover.generic"],
        },
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.GROUP
    assert entry.options[CONF_MEMBER_ENTRIES] == ["cover_a"]
    assert entry.options[CONF_MEMBER_COVERS] == ["cover.generic"]
    # A group is not a geometry cover — no cover-automation defaults leak in.
    assert CONF_DELTA_POSITION not in entry.options
    assert CONF_MOTION_SENSORS not in entry.options


async def test_create_group_drops_covers_owned_by_selected_members(
    hass: HomeAssistant,
) -> None:
    """A generic entity owned by a selected ACP member is dropped (prefer the
    entry — its pipeline must orchestrate, not be double-commanded), and
    duplicate ids are deduped.
    """
    _add_cover(hass, "cover_a", entities=["cover.a"])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create_group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "G",
            CONF_MEMBER_ENTRIES: ["cover_a", "cover_a"],
            CONF_MEMBER_COVERS: ["cover.a", "cover.generic", "cover.generic"],
        },
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.options[CONF_MEMBER_ENTRIES] == ["cover_a"]
    assert entry.options[CONF_MEMBER_COVERS] == ["cover.generic"]


async def test_group_options_flow_shows_group_menu(hass: HomeAssistant) -> None:
    """Configure on a group shows the group menu, never the cover categories."""
    group = _add_group(hass, "group_1")

    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_init()

    assert result["type"] == "menu"
    assert result["menu_options"] == [
        "group_membership",
        "group_arbitration",
        "group_entities",
        "summary",
        "done",
    ]


async def test_group_membership_step_excludes_self_and_other_groups(
    hass: HomeAssistant,
) -> None:
    """The ACP-member selector lists real covers only — never the group itself
    or another group entry.
    """
    _add_cover(hass, "cover_a")
    _add_group(hass, "group_other")
    group = _add_group(hass, "group_1")

    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_group_membership()

    assert result["type"] == "form"
    values = {
        o["value"] for o in _select_options(result["data_schema"], CONF_MEMBER_ENTRIES)
    }
    assert values == {"cover_a"}


async def test_group_membership_step_saves_sanitized(hass: HomeAssistant) -> None:
    """Submitting membership sanitizes the rosters and saves the options."""
    _add_cover(hass, "cover_a", entities=["cover.a"])
    group = _add_group(hass, "group_1")

    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_group_membership(
        {
            CONF_MEMBER_ENTRIES: ["cover_a", "group_1"],
            CONF_MEMBER_COVERS: ["cover.a", "cover.generic"],
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_MEMBER_ENTRIES] == ["cover_a"]
    assert result["data"][CONF_MEMBER_COVERS] == ["cover.generic"]


async def test_cover_entries_excludes_groups(hass: HomeAssistant) -> None:
    """_cover_entries means real covers: groups are excluded, so duplicate
    sources and profile-linkable cover lists never contain a group.
    """
    _add_cover(hass, "cover_a")
    _add_group(hass, "group_1")

    entries = _cover_entries(hass)

    assert [e.entry_id for e in entries] == ["cover_a"]


# ---------------------------------------------------------------------------
# Phase 2 — arbitration step (stagger + per-member opt-out)
# ---------------------------------------------------------------------------


async def test_group_arbitration_step_saves_stagger_and_opt_out(
    hass: HomeAssistant,
) -> None:
    """One page: stagger slider + one opt-out multi-select per ACP member."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_MEMBER_OPT_OUT,
        CONF_GROUP_STAGGER_DELAY,
        GroupScene,
        OPT_OUT_ALL_SCENES,
    )

    _add_cover(hass, "cover_a", entities=["cover.a"])
    _add_cover(hass, "cover_b", entities=["cover.b"])
    group = _add_group(hass, "group_1", member_entries=["cover_a", "cover_b"])

    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_group_arbitration()
    assert result["type"] == "form"
    keys = _schema_keys(result["data_schema"])
    assert keys == {CONF_GROUP_STAGGER_DELAY, "scene_opt_outs"}
    # One multi-select: per member, the all-scenes sentinel plus every scene,
    # encoded as "member|scene" with a human label naming the member.
    opts = _select_options(result["data_schema"], "scene_opt_outs")
    values = [o["value"] for o in opts]
    assert values == [
        *(f"cover_a|{v}" for v in (OPT_OUT_ALL_SCENES, *(str(s) for s in GroupScene))),
        *(f"cover_b|{v}" for v in (OPT_OUT_ALL_SCENES, *(str(s) for s in GroupScene))),
    ]
    assert all("cover_a" in o["label"] for o in opts[:4])

    result = await flow.async_step_group_arbitration(
        {
            CONF_GROUP_STAGGER_DELAY: 2.5,
            "scene_opt_outs": [f"cover_a|{GroupScene.PRIVACY}"],
        }
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_GROUP_STAGGER_DELAY] == 2.5
    # Only members with opt-outs are stored.
    assert result["data"][CONF_GROUP_MEMBER_OPT_OUT] == {
        "cover_a": [str(GroupScene.PRIVACY)]
    }


async def test_membership_save_prunes_opt_out_of_removed_members(
    hass: HomeAssistant,
) -> None:
    """Removing a member from the roster drops its opt-out entry too."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_MEMBER_OPT_OUT,
        GroupScene,
    )

    _add_cover(hass, "cover_a", entities=["cover.a"])
    _add_cover(hass, "cover_b", entities=["cover.b"])
    group = _add_group(hass, "group_1", member_entries=["cover_a", "cover_b"])
    hass.config_entries.async_update_entry(
        group,
        options={
            **group.options,
            CONF_GROUP_MEMBER_OPT_OUT: {
                "cover_a": [str(GroupScene.PRIVACY)],
                "cover_b": [str(GroupScene.ALL_OPEN)],
            },
        },
    )

    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_group_membership(
        {CONF_MEMBER_ENTRIES: ["cover_a"], CONF_MEMBER_COVERS: []}
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_GROUP_MEMBER_OPT_OUT] == {
        "cover_a": [str(GroupScene.PRIVACY)]
    }


async def test_stagger_registered_in_option_ranges() -> None:
    """The stagger range feeds OPTION_RANGES (validators + selectors)."""
    from custom_components.adaptive_cover_pro.const import (
        _RANGE_GROUP_STAGGER,
        CONF_GROUP_STAGGER_DELAY,
        OPTION_RANGES,
    )

    assert OPTION_RANGES[CONF_GROUP_STAGGER_DELAY] == _RANGE_GROUP_STAGGER


# ---------------------------------------------------------------------------
# Phase 3 — entity-exposure toggles
# ---------------------------------------------------------------------------


async def test_group_entities_step_saves_toggles(hass: HomeAssistant) -> None:
    from custom_components.adaptive_cover_pro.const import (
        CONF_GROUP_ENABLE_CLIMATE_SENSOR,
        CONF_GROUP_ENABLE_COVER_ENTITY,
        CONF_GROUP_ENABLE_POSITION_SENSOR,
        CONF_GROUP_ENABLE_STATE_SENSOR,
        CONF_GROUP_ENABLE_WHO_WON_SENSOR,
    )

    group = _add_group(hass, "group_1")
    flow = OptionsFlowHandler(group)
    flow.hass = hass

    result = await flow.async_step_group_entities()
    assert result["type"] == "form"
    assert _schema_keys(result["data_schema"]) == {
        CONF_GROUP_ENABLE_COVER_ENTITY,
        CONF_GROUP_ENABLE_POSITION_SENSOR,
        CONF_GROUP_ENABLE_STATE_SENSOR,
        CONF_GROUP_ENABLE_CLIMATE_SENSOR,
        CONF_GROUP_ENABLE_WHO_WON_SENSOR,
    }

    result = await flow.async_step_group_entities(
        {
            CONF_GROUP_ENABLE_COVER_ENTITY: True,
            CONF_GROUP_ENABLE_POSITION_SENSOR: True,
            CONF_GROUP_ENABLE_STATE_SENSOR: False,
            CONF_GROUP_ENABLE_CLIMATE_SENSOR: True,
            CONF_GROUP_ENABLE_WHO_WON_SENSOR: False,
        }
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_GROUP_ENABLE_COVER_ENTITY] is True
    assert result["data"][CONF_GROUP_ENABLE_STATE_SENSOR] is False


# ---------------------------------------------------------------------------
# Phase 4 — area membership picker
# ---------------------------------------------------------------------------


async def test_create_group_offers_and_stores_area(hass: HomeAssistant) -> None:
    from custom_components.adaptive_cover_pro.const import CONF_GROUP_AREA

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create_group"}
    )
    assert CONF_GROUP_AREA in _schema_keys(result["data_schema"])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Area Group", CONF_GROUP_AREA: "living_room"},
    )
    assert result["type"] == "create_entry"
    assert result["result"].options[CONF_GROUP_AREA] == "living_room"


async def test_membership_step_updates_and_clears_area(hass: HomeAssistant) -> None:
    from custom_components.adaptive_cover_pro.const import CONF_GROUP_AREA

    group = _add_group(hass, "group_1")
    hass.config_entries.async_update_entry(
        group, options={**group.options, CONF_GROUP_AREA: "old_area"}
    )

    flow = OptionsFlowHandler(group)
    flow.hass = hass
    result = await flow.async_step_group_membership(
        {CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: [], CONF_GROUP_AREA: "new_area"}
    )
    assert result["data"][CONF_GROUP_AREA] == "new_area"

    # Clearing the picker removes the area source entirely.
    flow2 = OptionsFlowHandler(hass.config_entries.async_get_entry("group_1"))
    flow2.hass = hass
    result = await flow2.async_step_group_membership(
        {CONF_MEMBER_ENTRIES: [], CONF_MEMBER_COVERS: []}
    )
    assert CONF_GROUP_AREA not in result["data"]
