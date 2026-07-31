"""Options-flow "Change Cover Type" step (issue #1132).

``CONF_SENSOR_TYPE`` lives in ``config_entry.data`` and, before this feature, no
post-creation write path touched ``data`` — so a cover's type could only change
by deleting and re-adding the entry, which re-mints every ACP entity's
``unique_id`` (``entity_base.py``: ``f"{entry_id}_{suffix}"``) and breaks every
dashboard and automation referencing them. These tests pin the new options-flow
step that rewrites ``entry.data[CONF_SENSOR_TYPE]`` in place.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.helpers import bound_cover_capabilities
from custom_components.adaptive_cover_pro.migrations import (
    _PRUNE_SENSORS_V1_FLAG,
    _PRUNE_SENSORS_V2_FLAG,
    _PRUNE_V1_FLAG,
)

from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

# ---------------------------------------------------------------------------
# Step 2 — shared cap-map builder
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_bound_cover_capabilities_maps_each_entity(hass: HomeAssistant) -> None:
    """Each bound entity maps to its feature dict; unreadable ones map to None."""
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.const import STATE_UNAVAILABLE

    hass.states.async_set(
        "cover.a",
        "open",
        {"supported_features": int(CoverEntityFeature.SET_POSITION)},
    )
    hass.states.async_set("cover.b", STATE_UNAVAILABLE, {})

    known = bound_cover_capabilities(hass, {CONF_ENTITIES: ["cover.a", "cover.b"]})

    assert set(known) == {"cover.a", "cover.b"}
    assert known["cover.a"]["has_set_position"] is True
    assert known["cover.a"]["has_set_tilt_position"] is False
    assert known["cover.b"] is None


@pytest.mark.integration
async def test_bound_cover_capabilities_empty_without_hass_or_entities(
    hass: HomeAssistant,
) -> None:
    """No hass or no bound entities → an empty map, never a crash."""
    assert bound_cover_capabilities(None, {CONF_ENTITIES: ["cover.a"]}) == {}
    assert bound_cover_capabilities(hass, {}) == {}
    assert bound_cover_capabilities(hass, {CONF_ENTITIES: []}) == {}


# ---------------------------------------------------------------------------
# Shared fixtures / helpers for the options-flow tests
# ---------------------------------------------------------------------------


def _position_only_cover(hass: HomeAssistant, entity_id: str) -> None:
    """Register a cover that supports set_position but not set_tilt_position."""
    from homeassistant.components.cover import CoverEntityFeature

    features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    hass.states.async_set(
        entity_id,
        "open",
        {"current_position": 100, "supported_features": int(features)},
    )


async def _setup_entry(
    hass: HomeAssistant,
    *,
    cover_type: str = CoverType.BLIND,
    entities: list[str] | None = None,
    extra_options: dict | None = None,
    entry_id: str = "change_type_01",
) -> MockConfigEntry:
    """Register + set up an entry whose covers are position-capable."""
    import datetime

    entities = entities or ["cover.test_blind"]
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "azimuth": 180.0,
            "elevation": 45.0,
            "rising": True,
            "next_rising": datetime.datetime.now(datetime.UTC).isoformat(),
            "next_setting": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )
    for eid in entities:
        _position_only_cover(hass, eid)

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENTITIES] = list(entities)
    # Model an entry that has already completed its one-time orphan-prune
    # migrations. Without these, the prune passes write ``entry.options`` DURING
    # setup — after the coordinator snapshotted ``_cached_options`` — leaving the
    # cache permanently stale, so the update listener sees a non-empty diff and
    # reloads on the next ``async_update_entry`` no matter what changed. That is
    # pre-existing first-boot behaviour unrelated to the cover-type switch; a
    # settled entry is what these tests need to measure.
    options[_PRUNE_V1_FLAG] = True
    options[_PRUNE_SENSORS_V1_FLAG] = True
    options[_PRUNE_SENSORS_V2_FLAG] = True
    options.update(extra_options or {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Switchable", CONF_SENSOR_TYPE: cover_type},
        options=options,
        entry_id=entry_id,
        title="Switchable",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _en_json() -> dict:
    """Load the English translation bundle."""
    import json
    import os

    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "adaptive_cover_pro",
        "translations",
        "en.json",
    )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Step 3 — menu entry + label
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_change_cover_type_in_cover_menu(hass: HomeAssistant) -> None:
    """A real cover's options menu offers the change-cover-type row."""
    entry = await _setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "menu"
    menu = result["menu_options"]
    assert "change_cover_type" in menu
    # Layer 1 placement: right after "Covers & Device", before geometry.
    assert (
        menu.index("cover_entities")
        < menu.index("change_cover_type")
        < menu.index("geometry")
    )


@pytest.mark.integration
async def test_change_cover_type_absent_from_profile_and_group_menus(
    hass: HomeAssistant,
) -> None:
    """Virtual entry types (profile, group) never offer a cover-type switch."""
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    for cover_type, entry_id in (
        (CoverType.BUILDING_PROFILE, "virtual_profile"),
        (CoverType.GROUP, "virtual_group"),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "Virtual", CONF_SENSOR_TYPE: cover_type},
            options={},
            entry_id=entry_id,
            title="Virtual",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        result = await flow.async_step_init()
        assert "change_cover_type" not in result["menu_options"], cover_type


def test_change_cover_type_has_menu_label() -> None:
    """The new menu entry needs an en.json label or it renders as a blank row."""
    labels = _en_json()["options"]["step"]["init"]["menu_options"]
    assert labels.get("change_cover_type", "").strip()


# ---------------------------------------------------------------------------
# Step 4 — the picker
# ---------------------------------------------------------------------------


def _type_selector(result: dict):
    """Return the cover-type SelectSelector rendered by the picker step."""
    for marker, value in result["data_schema"].schema.items():
        if str(marker) == CONF_SENSOR_TYPE:
            return value
    raise AssertionError(
        f"no {CONF_SENSOR_TYPE} field in {result['data_schema'].schema}"
    )


async def _open_picker(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Walk the options flow from the menu into the change-cover-type picker."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_cover_type"}
    )


@pytest.mark.integration
async def test_picker_offers_capability_compatible_types_only(
    hass: HomeAssistant,
) -> None:
    """Only types whose entity filter the bound covers satisfy are offered."""
    entry = await _setup_entry(hass)

    result = await _open_picker(hass, entry)

    assert result["type"] == "form"
    assert result["step_id"] == "change_cover_type"
    offered = _type_selector(result).config["options"]
    # Position-capable, no tilt: every plain-domain and position-filtered type.
    assert CoverType.DAY_NIGHT_SHADE in offered
    assert CoverType.DUAL_PANEL in offered
    assert CoverType.AWNING in offered
    # Tilt-filtered types cannot take a position-only cover.
    assert CoverType.TILT not in offered
    assert CoverType.VENETIAN not in offered
    assert CoverType.LOUVERED_ROOF not in offered
    # Never offer the type it already is.
    assert CoverType.BLIND not in offered


@pytest.mark.integration
async def test_picker_excludes_virtual_types(hass: HomeAssistant) -> None:
    """Building Profile / Group are virtual entry types, never switch targets."""
    entry = await _setup_entry(hass, entry_id="change_type_virtual")

    offered = _type_selector(await _open_picker(hass, entry)).config["options"]

    assert CoverType.BUILDING_PROFILE not in offered
    assert CoverType.GROUP not in offered


@pytest.mark.integration
async def test_picker_lists_blocked_types_with_reason(hass: HomeAssistant) -> None:
    """Filtered-out types are explained, not silently absent."""
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    entry = await _setup_entry(hass, entry_id="change_type_blocked")

    result = await _open_picker(hass, entry)

    blocked_text = result["description_placeholders"]["blocked_types"]
    assert get_policy(CoverType.VENETIAN).display_label() in blocked_text
    assert "set_tilt_position" in blocked_text
    # The current type names itself in the description.
    assert (
        get_policy(CoverType.BLIND).display_label()
        in result["description_placeholders"]["current_type"]
    )


@pytest.mark.integration
async def test_picker_uses_mode_translation_key(hass: HomeAssistant) -> None:
    """Reuse the ten already-translated selector.mode labels — no new strings."""
    entry = await _setup_entry(hass, entry_id="change_type_i18n")

    selector_ = _type_selector(await _open_picker(hass, entry))

    assert selector_.config["translation_key"] == "mode"
    mode_options = _en_json()["selector"]["mode"]["options"]
    assert set(selector_.config["options"]).issubset(mode_options)


@pytest.mark.integration
async def test_picker_aborts_when_nothing_eligible(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No destination type → an explained abort, not an empty dropdown."""
    from custom_components.adaptive_cover_pro import config_flow as cf

    entry = await _setup_entry(hass, entry_id="change_type_abort")
    monkeypatch.setattr(cf, "SENSOR_TYPE_MENU", [CoverType.BLIND])

    result = await _open_picker(hass, entry)

    assert result["type"] == "abort"
    assert result["reason"] == "no_eligible_cover_types"
    assert _en_json()["options"]["abort"]["no_eligible_cover_types"].strip()


# ---------------------------------------------------------------------------
# Step 5 — the confirm screen
# ---------------------------------------------------------------------------


async def _pick(hass: HomeAssistant, entry: MockConfigEntry, new_type: str) -> dict:
    """Open the picker and submit *new_type*, landing on the confirm screen."""
    picker = await _open_picker(hass, entry)
    return await hass.config_entries.options.async_configure(
        picker["flow_id"], {CONF_SENSOR_TYPE: new_type}
    )


@pytest.mark.integration
async def test_confirm_step_shown_after_pick(hass: HomeAssistant) -> None:
    """Picking a destination type lands on an explicit confirm screen."""
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    entry = await _setup_entry(hass, entry_id="confirm_shown")

    result = await _pick(hass, entry, CoverType.AWNING)

    assert result["type"] == "form"
    assert result["step_id"] == "change_cover_type_confirm"
    assert "confirm" in {str(m) for m in result["data_schema"].schema}
    placeholders = result["description_placeholders"]
    assert placeholders["from_type"] == get_policy(CoverType.BLIND).display_label()
    assert placeholders["to_type"] == get_policy(CoverType.AWNING).display_label()
    # Nothing is written until the user confirms.
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND


@pytest.mark.integration
async def test_confirm_lists_stranded_options(hass: HomeAssistant) -> None:
    """Options only the outgoing type reads are named — non-blocking."""
    entry = await _setup_entry(
        hass,
        entry_id="confirm_stranded",
        extra_options={
            "enable_glare_zones": True,
            "glare_zone_1_name": "Sofa",
        },
    )

    result = await _pick(hass, entry, CoverType.AWNING)

    stranded = result["description_placeholders"]["stranded_options"]
    assert "enable_glare_zones" in stranded
    assert "glare_zone_1_name" in stranded


@pytest.mark.integration
async def test_confirm_shows_capability_notes(hass: HomeAssistant) -> None:
    """Capability advice is evaluated against the DESTINATION type."""
    entry = await _setup_entry(hass, entry_id="confirm_caps")

    result = await _pick(hass, entry, CoverType.DAY_NIGHT_SHADE)

    notes = result["description_placeholders"]["capability_notes"]
    assert "set_tilt_position" in notes


@pytest.mark.integration
async def test_declining_confirm_leaves_type_unchanged(hass: HomeAssistant) -> None:
    """Unticking confirm returns to the menu with nothing written."""
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = await _setup_entry(hass, entry_id="confirm_declined")
    confirmed = await _pick(hass, entry, CoverType.AWNING)

    result = await hass.config_entries.options.async_configure(
        confirmed["flow_id"], {"confirm": False}
    )

    assert result["type"] == "menu"
    assert result["step_id"] == "init"
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    flow = next(
        f
        for f in hass.config_entries.options.async_progress()
        if f["flow_id"] == confirmed["flow_id"]
    )
    handler = hass.config_entries.options._progress[flow["flow_id"]]
    assert isinstance(handler, OptionsFlowHandler)
    assert handler._cover_type_changed is False


# ---------------------------------------------------------------------------
# Step 6 — the entry.data write and in-memory propagation
# ---------------------------------------------------------------------------


async def _confirm(hass: HomeAssistant, entry: MockConfigEntry, new_type: str) -> dict:
    """Pick *new_type* and tick the confirm box."""
    confirm = await _pick(hass, entry, new_type)
    return await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )


@pytest.mark.integration
async def test_confirm_writes_entry_data(hass: HomeAssistant) -> None:
    """Confirming rewrites entry.data in place and leaves options untouched."""
    entry = await _setup_entry(hass, entry_id="write_data")
    options_before = dict(entry.options)

    await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert entry.data["name"] == "Switchable"
    assert dict(entry.options) == options_before


@pytest.mark.integration
async def test_confirm_routes_into_geometry_with_new_type_schema(
    hass: HomeAssistant,
) -> None:
    """The follow-on step is the existing geometry form, rendered for the new type."""
    entry = await _setup_entry(hass, entry_id="route_geometry")

    result = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)

    assert result["type"] == "form"
    assert result["step_id"] == "geometry"
    markers = {str(m) for m in result["data_schema"].schema}
    assert "day_night_control_model" in markers
    assert "day_night_middle_rail_entity" in markers


@pytest.mark.integration
async def test_confirm_alone_does_not_reload(hass: HomeAssistant) -> None:
    """The coordinator keeps running the OLD policy until Save & Close.

    A reload at confirm time would bring the coordinator up on the new type with
    none of its geometry collected yet — for Day/Night that means defaulting to
    Model A on position-only hardware and driving both rails together.
    """
    entry = await _setup_entry(hass, entry_id="no_reload_yet")

    with (
        patch.object(hass.config_entries, "async_schedule_reload") as sched,
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        await hass.async_block_till_done()

    sched.assert_not_called()
    reload.assert_not_called()


@pytest.mark.integration
async def test_menu_after_switch_reflects_new_type(hass: HomeAssistant) -> None:
    """Back at the menu, every gate reads the incoming type's policy."""
    entry = await _setup_entry(
        hass,
        entry_id="menu_after_switch",
        extra_options={"enable_glare_zones": True},
    )
    menu_before = await hass.config_entries.options.async_init(entry.entry_id)
    assert "glare_zones" in menu_before["menu_options"]
    hass.config_entries.options.async_abort(menu_before["flow_id"])

    geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
    menu = await hass.config_entries.options.async_configure(geometry["flow_id"], {})

    assert menu["type"] == "menu"
    assert menu["step_id"] == "init"
    # Day/Night has no glare zones — the row is gone even though the option is on.
    assert "glare_zones" not in menu["menu_options"]


# ---------------------------------------------------------------------------
# Step 7 — exactly one reload, at Save & Close
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_done_schedules_reload_after_type_change(hass: HomeAssistant) -> None:
    """Save & Close applies the switch with exactly one scheduled reload."""
    entry = await _setup_entry(hass, entry_id="reload_on_done")

    with patch.object(hass.config_entries, "async_schedule_reload") as sched:
        geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        menu = await hass.config_entries.options.async_configure(
            geometry["flow_id"], {}
        )
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    sched.assert_called_once_with(entry.entry_id)


@pytest.mark.integration
async def test_done_does_not_schedule_reload_without_type_change(
    hass: HomeAssistant,
) -> None:
    """An ordinary Save & Close does not gain a reload it never had."""
    entry = await _setup_entry(hass, entry_id="reload_no_change")

    with patch.object(hass.config_entries, "async_schedule_reload") as sched:
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    sched.assert_not_called()


@pytest.mark.integration
async def test_reload_scheduled_even_when_options_unchanged(
    hass: HomeAssistant,
) -> None:
    """The reload must be explicit, not left to the options-write listener.

    If the geometry form comes back byte-identical, ``_async_update_listener``
    sees an empty diff and reloads nothing — the coordinator would keep running
    the OLD policy until the next HA restart.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = await _setup_entry(hass, entry_id="reload_options_same")

    async def _straight_back_to_menu(self, user_input=None):
        return await self.async_step_init()

    with (
        patch.object(hass.config_entries, "async_schedule_reload") as sched,
        patch.object(OptionsFlowHandler, "async_step_geometry", _straight_back_to_menu),
    ):
        options_before = dict(entry.options)
        menu = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        assert menu["type"] == "menu"
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert dict(entry.options) == options_before
    sched.assert_called_once_with(entry.entry_id)


# ---------------------------------------------------------------------------
# Step 8 — entity-ID preservation, end to end (the acceptance test)
# ---------------------------------------------------------------------------


def _registry_snapshot(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Map ``unique_id → entity_id`` for every registry row owned by *entry*."""
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    return {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }


@pytest.mark.integration
async def test_entity_ids_survive_a_cover_type_switch(hass: HomeAssistant) -> None:
    """The whole point of the feature: no entity is re-minted by a type switch."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
        CONF_DUAL_PANEL_FRONT_ENTITY,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    entry = await _setup_entry(
        hass,
        cover_type=CoverType.DUAL_PANEL,
        entities=["cover.panel_front", "cover.panel_back"],
        extra_options={
            CONF_DUAL_PANEL_FRONT_ENTITY: "cover.panel_front",
            CONF_DUAL_PANEL_BLACKOUT_TRIGGERS: ["night"],
        },
        entry_id="e2e_switch",
    )
    await hass.async_block_till_done()

    before = _registry_snapshot(hass, entry)
    assert before, "no entities registered for the entry"
    tilt_uid = f"{entry.entry_id}_Cover_Tilt"
    rtd_uid = f"{entry.entry_id}_Return to default when disabled"
    assert tilt_uid not in before  # dual panel is single-axis
    assert rtd_uid in before  # dual panel offers return-to-default

    geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
    menu = await hass.config_entries.options.async_configure(geometry["flow_id"], {})
    await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    after = _registry_snapshot(hass, entry)

    shared = set(before) & set(after)
    assert shared
    for uid in shared:
        assert after[uid] == before[uid], f"{uid} was re-minted"
    assert not [e for e in after.values() if e.endswith("_2")]

    # Day/Night exposes the dual-axis Target Tilt sensor; dual panel does not.
    assert tilt_uid in after
    # The dual-panel-only switch row is preserved, not deleted — deleting would
    # release the entity_id and re-mint it as `_2` if the user switches back.
    assert rtd_uid in after
    assert after[rtd_uid] == before[rtd_uid]

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert (
        entry.runtime_data._policy.cover_type
        == get_policy(CoverType.DAY_NIGHT_SHADE).cover_type
    )


def test_config_entry_version_unchanged() -> None:
    """No new option key → no migration, no version bump (rollback safety)."""
    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    assert ConfigFlowHandler.VERSION == 3
    assert ConfigFlowHandler.MINOR_VERSION == 13
