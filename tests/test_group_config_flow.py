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
    assert _schema_keys(result["data_schema"]) == {
        "name",
        CONF_MEMBER_ENTRIES,
        CONF_MEMBER_COVERS,
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
    assert result["menu_options"] == ["group_membership", "summary", "done"]


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
