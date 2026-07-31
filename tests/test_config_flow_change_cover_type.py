"""Options-flow "Change Cover Type" step (issue #1132).

``CONF_SENSOR_TYPE`` lives in ``config_entry.data`` and, before this feature, no
post-creation write path touched ``data`` — so a cover's type could only change
by deleting and re-adding the entry, which re-mints every ACP entity's
``unique_id`` (``entity_base.py``: ``f"{entry_id}_{suffix}"``) and breaks every
dashboard and automation referencing them. These tests pin the new options-flow
step that rewrites ``entry.data[CONF_SENSOR_TYPE]`` in place — picked and
confirmed in memory, persisted (with exactly one reload) only at Save & Close.
"""

from __future__ import annotations

import ast
import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_SUN_TRACKING,
    CONF_ENTITIES,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_SENSOR_TYPE,
    CONF_SUNSET_POS,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
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
    # migrations. In production the prunes are harmless: they write
    # ``entry.options`` during setup, and ``_async_update_data`` re-snapshots
    # ``_cached_options`` on the first refresh right afterwards. Here
    # ``_patch_coordinator_refresh`` replaces that first refresh with an
    # ``AsyncMock``, so the snapshot never happens and the cache stays stale for
    # the life of the test — the update listener would then see a non-empty diff
    # and reload on any ``async_update_entry``. Pre-seeding the flags keeps the
    # harness honest about what a settled entry does.
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
async def test_blocked_type_reason_names_set_position_for_day_night(
    hass: HomeAssistant,
) -> None:
    """Day/Night's blocked reason names ``set_position``, not ``set_tilt_position`` (#1137).

    ``_blocked_types_text`` explains a decision ``entities_satisfy_selector``
    already made, so it must read the SAME capability matrix that made that
    decision: ``prospective_capability_warnings``, not
    ``cover_capability_warnings``. Day/Night's ``entity_selector_filter`` only
    requires ``set_position`` (issue #1114/#1117 — every control model needs
    it, only Model A additionally needs tilt), so an ordinary open/close-only
    cover (no ``set_position``, no ``set_tilt_position``) is blocked here
    because it lacks ``set_position`` — not because it lacks tilt. Naming
    ``set_tilt_position`` in this line would blame the wrong axis and
    contradict ``entity_selector_filter``, the same inconsistency the confirm
    screen had before this fix.
    """
    from homeassistant.components.cover import CoverEntityFeature

    from custom_components.adaptive_cover_pro.cover_types import get_policy

    entry = await _setup_entry(hass, entry_id="blocked_open_close_only")
    # Ordinary open/close-only hardware: OPEN | CLOSE | STOP, no SET_POSITION
    # and no SET_TILT_POSITION.
    features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"supported_features": int(features)},
    )

    result = await _open_picker(hass, entry)

    blocked_text = result["description_placeholders"]["blocked_types"]
    day_night_label = get_policy(CoverType.DAY_NIGHT_SHADE).display_label()
    assert day_night_label in blocked_text

    day_night_line = next(
        line for line in blocked_text.splitlines() if day_night_label in line
    )
    assert "set_position" in day_night_line
    assert "set_tilt_position" not in day_night_line


@pytest.mark.integration
async def test_picker_blocked_list_never_renders_a_dangling_heading(
    hass: HomeAssistant,
) -> None:
    """Nothing blocked still needs a placeholder — the heading is unconditional."""
    from homeassistant.components.cover import CoverEntityFeature

    entry = await _setup_entry(hass, entry_id="change_type_nothing_blocked")
    # A cover that can do everything satisfies every policy's entity filter.
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {
            "current_position": 100,
            "current_tilt_position": 100,
            "supported_features": int(
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
                | CoverEntityFeature.SET_POSITION
                | CoverEntityFeature.SET_TILT_POSITION
            ),
        },
    )

    result = await _open_picker(hass, entry)

    assert result["description_placeholders"]["blocked_types"] == "—"


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
    """The confirm screen must not assume a control model before one is picked (#1137).

    The bound covers are position-only — exactly the Model B/C hardware
    #1114/#1117 exist to support. Day/Night's control model is chosen on the
    NEXT screen (geometry), so the confirm screen must not warn about a tilt
    requirement only Model A has; the picker already admitted this type via
    the same all-models intersection ``entity_selector_filter()`` encodes.
    """
    entry = await _setup_entry(hass, entry_id="confirm_caps")

    result = await _pick(hass, entry, CoverType.DAY_NIGHT_SHADE)

    notes = result["description_placeholders"]["capability_notes"]
    assert "set_tilt_position" not in notes


@pytest.mark.integration
async def test_confirm_still_warns_for_tilt_only_destination(
    hass: HomeAssistant,
) -> None:
    """The Day/Night tilt relaxation must not leak to a type that always needs it.

    Venetian genuinely needs tilt on every configuration — #1117's
    intersection filter is specific to Day/Night's multiple control models, so
    Venetian keeps the strict, unconditional both-axes requirement. The
    reporter's position-only covers cannot reach Venetian through the real
    picker at all (Venetian's own ``entity_selector_filter`` blocks them
    there — see ``test_picker_offers_capability_compatible_types_only``), so
    this constructs the flow handler directly and sets the pending type by
    hand, to isolate the confirm screen's capability advice from the picker's
    eligibility gate.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = await _setup_entry(hass, entry_id="confirm_caps_venetian")
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow._pending_cover_type = CoverType.VENETIAN

    result = await flow.async_step_change_cover_type_confirm()

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
# Step 6 — in-memory propagation; nothing is persisted before Save & Close
# ---------------------------------------------------------------------------


async def _confirm(hass: HomeAssistant, entry: MockConfigEntry, new_type: str) -> dict:
    """Pick *new_type* and tick the confirm box."""
    confirm = await _pick(hass, entry, new_type)
    return await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )


def _handler(hass: HomeAssistant, flow_id: str):
    """Return the live OptionsFlowHandler behind *flow_id*."""
    return hass.config_entries.options._progress[flow_id]


@contextlib.contextmanager
def _count_reloads(hass: HomeAssistant):
    """Count reloads from BOTH paths that can trigger one.

    ``async_schedule_reload`` calls ``async_reload`` inside a task and
    ``_async_update_listener`` awaits it directly, so patching the one method
    both go through is the only way to see the total. Patching
    ``async_schedule_reload`` alone counts the explicit path and misses the
    listener entirely — which is exactly how a double reload hides.
    """
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        yield reload


def _reloads(reload_mock, entry: MockConfigEntry) -> int:
    """How many times *entry* specifically was reloaded."""
    return sum(
        1 for call in reload_mock.call_args_list if call.args[0] == entry.entry_id
    )


@pytest.mark.integration
async def test_confirm_switches_the_flow_but_not_the_entry(
    hass: HomeAssistant,
) -> None:
    """Confirming moves the in-memory type only — ``entry.data`` is untouched.

    Persisting at confirm time would leave every non-Save exit (abandon the
    dialog, hit an aborting step) with an entry permanently switched, never
    reloaded, and missing the geometry the user was about to enter.
    """
    entry = await _setup_entry(hass, entry_id="write_data")
    options_before = dict(entry.options)

    result = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.data["name"] == "Switchable"
    assert dict(entry.options) == options_before
    handler = _handler(hass, result["flow_id"])
    assert handler.sensor_type == CoverType.DAY_NIGHT_SHADE
    assert handler._cover_type_changed is True


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
# Step 7 — nothing persists unless the user reaches Save & Close
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_abandoning_after_confirm_leaves_type_unchanged(
    hass: HomeAssistant,
) -> None:
    """Closing the dialog on the geometry step must not leave a switched entry.

    A ``data`` write at confirm time survives an abandoned flow: the entry is
    permanently the new type, never reloaded, and missing every key the geometry
    step was about to collect. On the next HA restart the coordinator comes up
    on a half-configured type that already drives hardware.
    """
    entry = await _setup_entry(hass, entry_id="abandon_after_confirm")
    options_before = dict(entry.options)

    with _count_reloads(hass) as reload:
        geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        assert geometry["step_id"] == "geometry"
        hass.config_entries.options.async_abort(geometry["flow_id"])
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert dict(entry.options) == options_before
    assert _reloads(reload, entry) == 0


@pytest.mark.integration
async def test_confirm_then_aborting_step_leaves_type_unchanged(
    hass: HomeAssistant,
) -> None:
    """An aborting step after confirm ends the flow without ever writing options.

    ``async_step_sync`` filters sibling covers on this instance's own type. Once
    the switch is pending there is no sibling of the new type, so it aborts —
    and ``OptionsFlowManager.async_finish_flow`` returns on an ABORT without
    writing options or running ``_update_options``. Everything the flow did must
    unwind with it.
    """
    entry = await _setup_entry(hass, entry_id="abort_via_sync")
    options_before = dict(entry.options)

    with _count_reloads(hass) as reload:
        geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        menu = await hass.config_entries.options.async_configure(
            geometry["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "sync"}
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "no_covers_to_sync"
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert dict(entry.options) == options_before
    assert _reloads(reload, entry) == 0


@pytest.mark.integration
async def test_data_written_only_at_save_and_close(hass: HomeAssistant) -> None:
    """``entry.data`` flips on Save & Close, together with exactly one reload."""
    entry = await _setup_entry(hass, entry_id="data_at_save")

    with _count_reloads(hass) as reload:
        geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
        menu = await hass.config_entries.options.async_configure(
            geometry["flow_id"], {}
        )
        assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert entry.data["name"] == "Switchable"
    assert _reloads(reload, entry) == 1


@pytest.mark.integration
async def test_confirm_to_save_model_c_no_capability_warning(
    hass: HomeAssistant,
) -> None:
    """End-to-end (#1137): position-only covers, Model C, no false warning.

    Exactly the reporter's Smartwings/ZVIDAR two-motor day/night shade
    (#1114/#1117): two position-only rails, switching to Day/Night and
    picking Model C (``dual_entity``) plus a middle rail on the geometry
    step. The confirm screen must show no tilt warning (the control model
    isn't known yet), and the switch must save cleanly through to
    ``entry.data``.
    """
    entry = await _setup_entry(
        hass,
        entry_id="model_c_e2e",
        entities=["cover.bottom_rail", "cover.middle_rail"],
    )

    confirm = await _pick(hass, entry, CoverType.DAY_NIGHT_SHADE)
    assert (
        "set_tilt_position"
        not in confirm["description_placeholders"]["capability_notes"]
    )

    geometry = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )
    assert geometry["step_id"] == "geometry"

    menu = await hass.config_entries.options.async_configure(
        geometry["flow_id"],
        {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: "cover.middle_rail",
        },
    )
    assert menu["type"] == "menu"

    await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert entry.options[CONF_DAY_NIGHT_CONTROL_MODEL] == DAY_NIGHT_MODEL_DUAL_ENTITY
    assert entry.options[CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY] == "cover.middle_rail"


# ---------------------------------------------------------------------------
# Step 8 — exactly one reload, however the options happen to fall
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_type_change_with_option_edits_reloads_once(
    hass: HomeAssistant,
) -> None:
    """A switch that also edits options reloads once, not twice.

    The incoming type's geometry form writes its own defaults, so the options
    write at Save & Close already reloads the entry through
    ``_async_update_listener``. Scheduling a reload unconditionally on top of it
    takes every entity through unavailable → available twice.
    """
    entry = await _setup_entry(hass, entry_id="reload_on_done")

    with _count_reloads(hass) as reload:
        geometry = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        menu = await hass.config_entries.options.async_configure(
            geometry["flow_id"], {}
        )
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    # The geometry step really did move options — this is the double-reload case.
    assert entry.options["day_night_control_model"]
    assert _reloads(reload, entry) == 1


@pytest.mark.integration
async def test_type_change_with_runtime_only_option_edits_still_reloads_once(
    hass: HomeAssistant,
) -> None:
    """A runtime-applicable-only delta never reloads, so the switch must.

    ``_async_update_listener`` applies a change confined to
    ``_RUNTIME_APPLICABLE_OPTIONS`` in place and returns. "Options changed" is
    therefore not the same question as "the write will reload".
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = await _setup_entry(hass, entry_id="reload_runtime_only")

    async def _runtime_only_edit(self, user_input=None):
        self.options[CONF_ENABLE_SUN_TRACKING] = False
        return await self.async_step_init()

    with (
        _count_reloads(hass) as reload,
        patch.object(OptionsFlowHandler, "async_step_geometry", _runtime_only_edit),
    ):
        menu = await _confirm(hass, entry, CoverType.DAY_NIGHT_SHADE)
        assert menu["type"] == "menu"
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert entry.options[CONF_ENABLE_SUN_TRACKING] is False
    assert _reloads(reload, entry) == 1


@pytest.mark.integration
async def test_done_does_not_reload_without_type_change(
    hass: HomeAssistant,
) -> None:
    """An ordinary Save & Close does not gain a reload it never had."""
    entry = await _setup_entry(hass, entry_id="reload_no_change")

    with _count_reloads(hass) as reload:
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert _reloads(reload, entry) == 0


@pytest.mark.integration
async def test_reload_happens_even_when_options_unchanged(
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
        _count_reloads(hass) as reload,
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
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DAY_NIGHT_SHADE
    assert _reloads(reload, entry) == 1


# ---------------------------------------------------------------------------
# Step 9 — position polarity across the switch
# ---------------------------------------------------------------------------
#
# ``default_percentage`` is stored by every cover type, so it can never show up
# in ``{stranded_options}`` — yet its correct value is the opposite across a
# polarity flip. On a blind 100 % is the no-coverage end; on an awning that end
# is 0 %. Carrying the number over silently turns "stay out of the way" into
# "shade as hard as you can".


async def _polarity_note(
    hass: HomeAssistant, entry: MockConfigEntry, new_type: str
) -> str:
    """Return the confirm screen's polarity disclosure for a switch to *new_type*."""
    confirm = await _pick(hass, entry, new_type)
    note = confirm["description_placeholders"]["position_polarity"]
    hass.config_entries.options.async_abort(confirm["flow_id"])
    return note


async def _switch_and_save(
    hass: HomeAssistant, entry: MockConfigEntry, new_type: str
) -> None:
    """Run the whole switch through to Save & Close."""
    geometry = await _confirm(hass, entry, new_type)
    menu = await hass.config_entries.options.async_configure(geometry["flow_id"], {})
    await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_polarity_flip_reseeds_default_position_blind_to_awning(
    hass: HomeAssistant,
) -> None:
    """Blind → awning re-seeds the default to the awning's no-coverage end."""
    entry = await _setup_entry(hass, entry_id="polarity_to_awning")
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50

    note = await _polarity_note(hass, entry, CoverType.AWNING)
    assert "50" in note
    assert "0" in note

    await _switch_and_save(hass, entry, CoverType.AWNING)

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.AWNING
    assert entry.options[CONF_DEFAULT_HEIGHT] == 0


@pytest.mark.integration
async def test_polarity_flip_reseeds_default_position_awning_to_blind(
    hass: HomeAssistant,
) -> None:
    """Awning → blind re-seeds the other way, to the blind's no-coverage end."""
    entry = await _setup_entry(
        hass,
        cover_type=CoverType.AWNING,
        entry_id="polarity_to_blind",
        extra_options={"length_awning": 2.1, "angle": 0},
    )
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50

    note = await _polarity_note(hass, entry, CoverType.BLIND)
    assert "100" in note

    await _switch_and_save(hass, entry, CoverType.BLIND)

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 100


@pytest.mark.integration
async def test_non_flipping_switch_leaves_default_position_alone(
    hass: HomeAssistant,
) -> None:
    """Blind → dual panel shares a polarity — say nothing, change nothing."""
    entry = await _setup_entry(hass, entry_id="polarity_none")

    note = await _polarity_note(hass, entry, CoverType.DUAL_PANEL)
    assert note == "—"

    await _switch_and_save(hass, entry, CoverType.DUAL_PANEL)

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.DUAL_PANEL
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50


@pytest.mark.integration
async def test_polarity_flip_names_the_positions_it_does_not_change(
    hass: HomeAssistant,
) -> None:
    """Travel limits have no policy answer, so they are disclosed, not rewritten."""
    entry = await _setup_entry(
        hass,
        entry_id="polarity_limits",
        extra_options={
            CONF_MIN_POSITION: 20,
            CONF_MAX_POSITION: 100,
            CONF_SUNSET_POS: 30,
        },
    )

    note = await _polarity_note(hass, entry, CoverType.AWNING)

    assert CONF_MIN_POSITION in note
    assert CONF_SUNSET_POS in note
    # 100 % is "no upper clamp" under either polarity — nothing to disclose.
    assert CONF_MAX_POSITION not in note

    await _switch_and_save(hass, entry, CoverType.AWNING)

    assert entry.options[CONF_MIN_POSITION] == 20
    assert entry.options[CONF_SUNSET_POS] == 30


# ---------------------------------------------------------------------------
# Step 10 — entity-ID preservation, end to end (the acceptance test)
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


# ---------------------------------------------------------------------------
# Step 11 — a pending switch never leaks through a mid-flow entry write
# ---------------------------------------------------------------------------
#
# ``_update_options`` is not the only place this flow writes its own entry:
# ``async_step_sync_confirm`` and ``async_step_building_profile``'s unlink
# branch both persist ``self.options`` mid-dialog, and each write fires the
# update listener (so it can reload). A confirmed-but-unsaved switch must not
# ride along on either — the entry would come back up on the OLD type carrying
# the NEW type's derived values (a blind whose no-sun default is 0 %, i.e.
# fully closed on every fallback).


async def _confirm_and_return_to_menu(
    hass: HomeAssistant, entry: MockConfigEntry, new_type: str
) -> dict:
    """Confirm a switch, submit the incoming type's geometry, land on the menu."""
    geometry = await _confirm(hass, entry, new_type)
    return await hass.config_entries.options.async_configure(geometry["flow_id"], {})


def _add_profile(
    hass: HomeAssistant,
    entry_id: str = "profile_for_link",
    extra_options: dict | None = None,
) -> MockConfigEntry:
    """Register a Building Profile entry (options-only; never set up)."""
    from custom_components.adaptive_cover_pro.const import CONF_TEMP_ENTITY

    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "HQ", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_TEMP_ENTITY: "sensor.outside_temp", **(extra_options or {})},
        entry_id=entry_id,
        title="HQ",
    )
    profile.add_to_hass(hass)
    return profile


def _sync_target_options(result: dict) -> list[dict]:
    """Return the covers the sync step offers as copy targets."""
    for marker, value in result["data_schema"].schema.items():
        if str(marker) == "target_entries":
            return list(value.config["options"])
    raise AssertionError(f"no target_entries field in {result['data_schema'].schema}")


@pytest.mark.integration
async def test_sync_confirm_does_not_persist_a_pending_switch(
    hass: HomeAssistant,
) -> None:
    """Sync saves this cover's settings first — but not a pending switch.

    ``async_step_sync_confirm`` writes ``options=dict(self.options)`` so the
    copy sees the latest values. With a switch pending those options already
    carry the awning re-seed, so the write leaves a BLIND whose no-sun default
    is 0 % — and reloads it straight into that state, and hands that 0 % on to
    every cover it then copies into.
    """
    sibling = await _setup_entry(
        hass,
        entities=["cover.blind_a"],
        extra_options={CONF_DEFAULT_HEIGHT: 90},
        entry_id="sync_sibling_blind",
    )
    entry = await _setup_entry(hass, entry_id="sync_pending_switch")
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    sync = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "sync"}
    )
    confirm = await hass.config_entries.options.async_configure(
        sync["flow_id"],
        {"target_entries": [sibling.entry_id], "sync_categories": ["position"]},
    )
    await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )
    await hass.async_block_till_done()

    # data and options must agree with each other: both still the blind's.
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50
    # The incoming type's geometry is part of the same pending switch.
    assert "length_awning" not in entry.options
    # What the source persisted is what the target receives — the copy reads
    # the source entry back off disk, so a leaked re-seed lands here too.
    assert sibling.options[CONF_DEFAULT_HEIGHT] == 50
    assert "length_awning" not in sibling.options


@pytest.mark.integration
async def test_sync_targets_are_filtered_by_the_stored_type(
    hass: HomeAssistant,
) -> None:
    """A pending switch must not re-aim "Copy to other covers" at the new type.

    The write that precedes the copy rolls the pending switch back out, so what
    ``_extract_shared_options`` reads off the entry is the OUTGOING type's
    values. Offering the incoming type's covers as targets would copy one type's
    positions onto another's — the target filter and the copied values have to
    describe the same cover type, and only the stored one is on both sides.
    """
    awning_sibling = await _setup_entry(
        hass,
        cover_type=CoverType.AWNING,
        entities=["cover.probe_awning"],
        extra_options={"length_awning": 2.1, "angle": 0, CONF_DEFAULT_HEIGHT: 10},
        entry_id="probe_sibling_awning",
    )
    blind_sibling = await _setup_entry(
        hass,
        entities=["cover.probe_blind"],
        extra_options={CONF_DEFAULT_HEIGHT: 90},
        entry_id="probe_sibling_blind",
    )
    entry = await _setup_entry(
        hass,
        extra_options={CONF_DEFAULT_HEIGHT: 80},
        entry_id="probe_sync_source",
    )

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    sync = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "sync"}
    )

    offered = {opt["value"] for opt in _sync_target_options(sync)}
    assert offered == {blind_sibling.entry_id}
    assert awning_sibling.entry_id not in offered


@pytest.mark.integration
async def test_sync_does_not_write_inverted_positions_to_siblings(
    hass: HomeAssistant,
) -> None:
    """The copy must never land a blind's 80 % on an awning as "extended 80 %".

    ``SYNC_CATEGORIES["position"]`` carries every polarity-sensitive key
    (``default_percentage``, ``min_position``, ``max_position``,
    ``sunset_position``, ``my_position_value``), so a target of the wrong type
    gets the whole inverted block — silently, on someone else's cover.
    """
    awning_sibling = await _setup_entry(
        hass,
        cover_type=CoverType.AWNING,
        entities=["cover.inverted_awning"],
        extra_options={"length_awning": 2.1, "angle": 0, CONF_DEFAULT_HEIGHT: 10},
        entry_id="inverted_sibling_awning",
    )
    blind_sibling = await _setup_entry(
        hass,
        entities=["cover.inverted_blind"],
        extra_options={CONF_DEFAULT_HEIGHT: 90},
        entry_id="inverted_sibling_blind",
    )
    entry = await _setup_entry(
        hass,
        extra_options={CONF_DEFAULT_HEIGHT: 80},
        entry_id="inverted_sync_source",
    )

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    sync = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "sync"}
    )
    targets = [opt["value"] for opt in _sync_target_options(sync)]
    confirm = await hass.config_entries.options.async_configure(
        sync["flow_id"],
        {"target_entries": targets, "sync_categories": ["position"]},
    )
    await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )
    await hass.async_block_till_done()

    # The awning is a different cover type — nothing of this blind's belongs
    # in it, least of all a position whose ends mean the opposite.
    assert awning_sibling.options[CONF_DEFAULT_HEIGHT] == 10
    # The same-type sibling gets the source's stored (blind) value.
    assert blind_sibling.options[CONF_DEFAULT_HEIGHT] == 80
    # And the source is untouched: still a blind, still 80.
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 80


@pytest.mark.integration
async def test_profile_unlink_does_not_persist_a_pending_switch(
    hass: HomeAssistant,
) -> None:
    """The Building Profile unlink branch is the same write, narrower gate."""
    from custom_components.adaptive_cover_pro.config_flow import _PROFILE_NONE_SENTINEL
    from custom_components.adaptive_cover_pro.const import CONF_BUILDING_PROFILE_ID

    profile = _add_profile(hass, entry_id="profile_for_unlink")
    entry = await _setup_entry(
        hass,
        entry_id="unlink_pending_switch",
        extra_options={CONF_BUILDING_PROFILE_ID: profile.entry_id},
    )

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "building_profile"}
    )
    await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BUILDING_PROFILE_ID: _PROFILE_NONE_SENTINEL}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50
    assert "length_awning" not in entry.options
    # The unlink itself still happened.
    assert CONF_BUILDING_PROFILE_ID not in entry.options


@pytest.mark.integration
async def test_profile_link_does_not_strand_a_pending_switch(
    hass: HomeAssistant,
) -> None:
    """Linking a profile re-reads the entry — it must not drop the pending switch.

    The link branch replaces ``self.options`` wholesale from the freshly written
    entry, discarding the polarity re-seed and the new type's geometry while the
    switch stays pending. Save & Close then flips ``data`` to the new type with
    options that contradict the disclosure the user just read.
    """
    from custom_components.adaptive_cover_pro.const import CONF_BUILDING_PROFILE_ID

    profile = _add_profile(hass)
    entry = await _setup_entry(hass, entry_id="link_pending_switch")

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "building_profile"}
    )
    back = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BUILDING_PROFILE_ID: profile.entry_id}
    )
    await hass.config_entries.options.async_configure(
        back["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.AWNING
    # The confirm screen promised this re-seed; the switch landed, so must it.
    assert entry.options[CONF_DEFAULT_HEIGHT] == 0
    assert "length_awning" in entry.options
    assert entry.options[CONF_BUILDING_PROFILE_ID] == profile.entry_id


async def _link_profile_after_editing(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    profile: MockConfigEntry,
    edit: dict,
) -> None:
    """Make *edit* in an open dialog, link *profile*, then Save & Close.

    The edit is injected through a patched step rather than a real form so the
    test says "an unsaved edit to this key" without dragging in whichever form
    happens to own the field today.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
    from custom_components.adaptive_cover_pro.const import CONF_BUILDING_PROFILE_ID

    async def _edit(self, user_input=None):
        self.options.update(edit)
        return await self.async_step_init()

    with patch.object(OptionsFlowHandler, "async_step_geometry", _edit):
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        menu = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "geometry"}
        )
        form = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "building_profile"}
        )
        menu = await hass.config_entries.options.async_configure(
            form["flow_id"], {CONF_BUILDING_PROFILE_ID: profile.entry_id}
        )
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()


@pytest.mark.integration
async def test_profile_link_adopts_the_profile_over_an_unsaved_edit(
    hass: HomeAssistant,
) -> None:
    """With no switch pending, linking still means "adopt the profile".

    The keys a profile owns are the whole point of linking, and on disk
    ``_copy_profile_to_cover`` overwrites the cover's stored value with the
    profile's (a cover being linked for the first time has no recorded
    overrides for the copier to spare). Staging that write in the dialog must
    reach the same place, or the outcome of "type a value, link a profile"
    depends on whether the user happened to hit Save in between — inherit if
    they did, a manufactured local override if they did not.

    ``compute_override_keys`` is a pure value-diff at save time, so whatever
    ``self.options`` holds at Save & Close decides inherit-vs-override. Leaving
    the typed-over value there records an override the user never asked for.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_BUILDING_PROFILE_ID,
        CONF_OUTSIDETEMP_ENTITY,
        CONF_PROFILE_SENSOR_OVERRIDES,
    )

    # The profile names the same sensor the cover already has stored, so the
    # copier changes nothing on disk and the "what did the copier change?" diff
    # is empty for this key — the case the delta merge cannot see.
    profile = _add_profile(
        hass,
        entry_id="profile_owns_outside_temp",
        extra_options={CONF_OUTSIDETEMP_ENTITY: "sensor.hq_outside"},
    )
    entry = await _setup_entry(
        hass,
        entry_id="link_adopts_profile_key",
        extra_options={CONF_OUTSIDETEMP_ENTITY: "sensor.hq_outside"},
    )

    await _link_profile_after_editing(
        hass, entry, profile, {CONF_OUTSIDETEMP_ENTITY: "sensor.typed_in_dialog"}
    )

    assert entry.options[CONF_BUILDING_PROFILE_ID] == profile.entry_id
    assert entry.options[CONF_OUTSIDETEMP_ENTITY] == "sensor.hq_outside"
    assert CONF_PROFILE_SENSOR_OVERRIDES not in entry.options


@pytest.mark.integration
async def test_profile_link_keeps_an_unsaved_edit_the_profile_does_not_own(
    hass: HomeAssistant,
) -> None:
    """Adopting is scoped to what the profile actually defines, not a category.

    A blank profile key means "the profile does not define this" — indistinguishable
    from never filled in — so ``merge_profile_into_config`` skips it and the
    cover keeps its own value. An unsaved edit to such a key is the cover's own
    just the same, and linking says nothing about it.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_LUX_ENTITY,
        CONF_OUTSIDETEMP_ENTITY,
        CONF_PROFILE_SENSOR_OVERRIDES,
    )

    profile = _add_profile(
        hass,
        entry_id="profile_leaves_lux_blank",
        extra_options={CONF_OUTSIDETEMP_ENTITY: "sensor.hq_outside"},
    )
    entry = await _setup_entry(hass, entry_id="link_keeps_local_key")

    await _link_profile_after_editing(
        hass, entry, profile, {CONF_LUX_ENTITY: "sensor.my_own_lux"}
    )

    assert entry.options[CONF_LUX_ENTITY] == "sensor.my_own_lux"
    # The profile leaves it blank, so it is "local", never an override.
    assert CONF_PROFILE_SENSOR_OVERRIDES not in entry.options
    # And the key the profile does own was adopted.
    assert entry.options[CONF_OUTSIDETEMP_ENTITY] == "sensor.hq_outside"


@pytest.mark.integration
async def test_mid_flow_write_without_a_pending_switch_is_unchanged(
    hass: HomeAssistant,
) -> None:
    """No pending switch → a mid-flow write still persists every unsaved edit.

    The regression risk of routing the writes through one seam: it must strip
    only what a pending switch put there, never an ordinary edit.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    sibling = await _setup_entry(
        hass, entities=["cover.other_blind"], entry_id="plain_sync_sibling"
    )
    entry = await _setup_entry(hass, entry_id="plain_sync_source")

    async def _edit(self, user_input=None):
        self.options[CONF_DEFAULT_HEIGHT] = 33
        return await self.async_step_init()

    with patch.object(OptionsFlowHandler, "async_step_geometry", _edit):
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        menu = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "geometry"}
        )
        sync = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "sync"}
        )
        confirm = await hass.config_entries.options.async_configure(
            sync["flow_id"],
            {"target_entries": [sibling.entry_id], "sync_categories": ["position"]},
        )
        await hass.config_entries.options.async_configure(
            confirm["flow_id"], {"confirm": True}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 33
    assert sibling.options[CONF_DEFAULT_HEIGHT] == 33


@pytest.mark.integration
async def test_mid_flow_write_keeps_a_position_re_entered_after_the_switch(
    hass: HomeAssistant,
) -> None:
    """A value the user typed after confirming is an edit, not the switch's.

    The rollback restores what the switch put there. Once the user has typed
    over the re-seeded ``default_percentage``, the switch's contribution to that
    key is gone — what is there is an ordinary edit, and the seam promises those
    survive a mid-dialog write like any other.
    """
    from custom_components.adaptive_cover_pro.config_flow import _PROFILE_NONE_SENTINEL
    from custom_components.adaptive_cover_pro.const import CONF_BUILDING_PROFILE_ID

    profile = _add_profile(hass, entry_id="profile_for_reentry")
    entry = await _setup_entry(
        hass,
        entry_id="reentered_default",
        extra_options={CONF_BUILDING_PROFILE_ID: profile.entry_id},
    )
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50

    menu = await _confirm_and_return_to_menu(hass, entry, CoverType.AWNING)
    position = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "position"}
    )
    menu = await hass.config_entries.options.async_configure(
        position["flow_id"], {CONF_DEFAULT_HEIGHT: 30}
    )
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "building_profile"}
    )
    await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BUILDING_PROFILE_ID: _PROFILE_NONE_SENTINEL}
    )
    await hass.async_block_till_done()

    # The switch itself is still pending, and its own contribution still unwinds.
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert "length_awning" not in entry.options
    # But the typed-over position is the user's, not the switch's.
    assert entry.options[CONF_DEFAULT_HEIGHT] == 30


# Both attributes reach the SAME ``ConfigEntry`` object, so a guard that knows
# only one of them can be walked past by spelling the other.
#
# ``_config_entry`` is what ``OptionsFlowHandler.__init__`` stores: HA's
# ``OptionsFlowManager.async_create_flow`` resolves the entry with
# ``hass.config_entries.async_get_known_entry(handler_key)`` and hands that
# object to ``async_get_options_flow(entry)`` (homeassistant/config_entries.py).
# ``config_entry`` is HA's own ``OptionsFlow.config_entry`` property, which is a
# live ``hass.config_entries.async_get_known_entry(self._config_entry_id)``
# lookup with ``_config_entry_id`` returning ``self.handler`` — the same
# ``handler_key``. Both therefore resolve to ``ConfigEntries._entries.data[
# entry_id]``, and ``async_update_entry`` mutates that object in place rather
# than replacing it, so the identity holds for the life of the flow.
#
# Not hypothetical: ``async_step_init`` already reads ``self.config_entry.title``.
_OWN_ENTRY_ATTRS = frozenset({"_config_entry", "config_entry"})


def _called_name(call: ast.Call) -> str | None:
    """Return the bare name of what *call* invokes (``a.b.c()`` → ``"c"``)."""
    return getattr(call.func, "attr", None) or getattr(call.func, "id", None)


def _is_own_entry(node: ast.AST | None, aliases: set[str]) -> bool:
    """Whether *node* evaluates to this flow's own config entry."""
    if isinstance(node, ast.Attribute):
        return (
            node.attr in _OWN_ENTRY_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )
    return isinstance(node, ast.Name) and node.id in aliases


def _own_entry_aliases(func: ast.AST) -> set[str]:
    """Local names bound to this flow's own entry anywhere inside *func*.

    Assigning to a local is the cheapest way to walk past a guard that only
    looks at ``self._config_entry`` (or ``self.config_entry``) literally, so the
    alias set is transitive (``a = self._config_entry`` then ``b = a``) and is
    rebuilt to a fixpoint rather than in source order.
    """
    aliases: set[str] = set()
    grew = True
    while grew:
        grew = False
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr):
                if node.value is None:
                    continue
                targets, value = [node.target], node.value
            else:
                continue
            if not _is_own_entry(value, aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    grew = True
    return aliases


def _writer_closure(calls: dict[str, set[str]]) -> frozenset[str]:
    """Names in *calls* that reach ``async_update_entry``, however many hops.

    ``calls`` maps a function name to the bare names it calls. One hop is not
    enough: ``def _sync_now(entry): _copy_profile_to_cover(hass, p, entry)``
    writes the entry it is handed just as surely as the copier does, so handing
    it this flow's own entry is the same bypass — invisible to a set built only
    from direct callers of ``async_update_entry``.

    Seeded with ``async_update_entry`` itself so direct and indirect writers
    fall out of one rule, then dropped from the result — it is HA's method, not
    a helper of ours anyone could hand an entry to.
    """
    reaching = {"async_update_entry"}
    grew = True
    while grew:
        grew = False
        for name, called in calls.items():
            if name not in reaching and called & reaching:
                reaching.add(name)
                grew = True
    return frozenset(reaching - {"async_update_entry"})


def _entry_writing_helpers() -> frozenset[str]:
    """Every function in the integration that can end up writing a config entry.

    Derived, not listed: a helper added later that writes an entry is picked up
    without anyone remembering to name it here.

    Names are matched bare, so two same-named functions in different modules
    merge into one entry. That over-approximates, which is the safe direction —
    it can make the guard demand a review that was not strictly needed, never
    let an unreviewed write past.
    """
    import pathlib

    from custom_components.adaptive_cover_pro import config_flow as cf

    calls: dict[str, set[str]] = {}
    for path in sorted(pathlib.Path(cf.__file__).parent.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            calls.setdefault(node.name, set()).update(
                name
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and (name := _called_name(call))
            )
    return _writer_closure(calls)


def test_entry_writing_helpers_follow_calls_more_than_one_hop() -> None:
    """The indirect check is only as deep as the set of helpers it is given.

    A one-hop set names the copier but not the wrapper around it, so the
    wrapper becomes a free bypass. The closure is the same fixpoint the alias
    tracker uses, one level up.
    """
    calls = {
        "_copy_profile_to_cover": {"async_update_entry"},
        "_sync_now": {"_copy_profile_to_cover"},
        "_and_its_caller": {"_sync_now"},
        "_unrelated": {"dict"},
    }

    assert _writer_closure(calls) == {
        "_copy_profile_to_cover",
        "_sync_now",
        "_and_its_caller",
    }
    # And the real derivation is wired to it: ``_persist_entry`` writes
    # directly, ``_update_options`` only by calling it.
    assert {"_persist_entry", "_update_options"} <= _entry_writing_helpers()


def _base_names(cls: ast.ClassDef) -> set[str]:
    """Bare names of *cls*'s declared bases (``a.B`` counts as ``B``)."""
    return {
        name
        for base in cls.bases
        if (name := getattr(base, "attr", None) or getattr(base, "id", None))
    }


def _handler_classes(tree: ast.Module, class_name: str) -> list[ast.ClassDef]:
    """*class_name* plus every class in the module that descends from it.

    A method that writes the entry is no less a write for being declared on a
    subclass, so matching one ``ClassDef`` by name leaves a bypass that costs
    one ``class Foo(OptionsFlowHandler):`` to take. The fixpoint over declared
    bases is ten lines and needs no assumption stated in prose — which is the
    argument for deriving it rather than asserting "no subclass exists today"
    and leaving the next author to discover that a correct extension fails a
    guard they then have to edit. Resolution is limited to what the module AST
    shows: a subclass declared in another module is out of reach, which is
    fine — this guard is about *this* module's writes.
    """
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert any(c.name == class_name for c in classes), f"no class {class_name}"

    family = {class_name}
    grew = True
    while grew:
        grew = False
        for cls in classes:
            if cls.name not in family and _base_names(cls) & family:
                family.add(cls.name)
                grew = True
    return [c for c in classes if c.name in family]


def _seam_violations(
    source: str, *, class_name: str, writer_helpers: frozenset[str]
) -> tuple[set[str], set[tuple[str, str]]]:
    """Find every place *class_name* writes its own config entry.

    Returns ``(direct, indirect)``: method names that call
    ``async_update_entry`` on their own entry, and ``(method, helper)`` pairs
    that hand their own entry to something that writes it for them.

    Only methods declared directly on the class (or on a subclass of it — see
    :func:`_handler_classes`) are iterated, so a call inside a nested closure is
    attributed to the method that contains it rather than to the closure's own
    name.
    """
    tree = ast.parse(source)
    methods = [
        node
        for cls in _handler_classes(tree, class_name)
        for node in cls.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]

    direct: set[str] = set()
    indirect: set[tuple[str, str]] = set()
    for method in methods:
        aliases = _own_entry_aliases(method)
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if name == "async_update_entry":
                # ``entry`` is positional-or-keyword on HA's signature.
                entry = (
                    node.args[0]
                    if node.args
                    else next(
                        (kw.value for kw in node.keywords if kw.arg == "entry"), None
                    )
                )
                if _is_own_entry(entry, aliases):
                    direct.add(method.name)
            elif name in writer_helpers and (
                any(_is_own_entry(arg, aliases) for arg in node.args)
                or any(_is_own_entry(kw.value, aliases) for kw in node.keywords)
            ):
                indirect.add((method.name, name))
    return direct, indirect


# Helpers that write a config entry and are handed this flow's own entry. Each
# is listed with the reason it cannot land a pending cover-type switch:
#
# * ``_copy_profile_to_cover`` really does write ``self._config_entry`` — but it
#   builds the new options from ``cover_entry.options``, i.e. what is on disk,
#   never from ``self.options``, which is the only place a pending switch
#   exists. ``async_step_building_profile`` then merges back only the keys the
#   copier changed, so the switch stays pending and is still committed by the
#   seam at Save & Close.
# * ``clear_cover_override`` and ``propagate_profile_clears`` receive
#   ``self._config_entry`` as the *profile* argument and write the covers linked
#   to it. A profile is never linked to itself, so this flow's own entry is not
#   among the entries they write.
_ALLOWED_INDIRECT_WRITES = {
    ("async_step_building_profile", "_copy_profile_to_cover"),
    ("async_step_profile_overrides", "clear_cover_override"),
    ("_propagate_profile_clears", "propagate_profile_clears"),
}


# A class shaped like every way a future edit could write its own entry without
# writing the literal ``async_update_entry(self._config_entry, …)`` the first
# version of this guard looked for.
_SEAM_EVASIONS = """
class OptionsFlowHandler:
    def _persist_entry(self):
        self.hass.config_entries.async_update_entry(self._config_entry, options={})

    def aliased(self):
        entry = self._config_entry
        also = entry
        self.hass.config_entries.async_update_entry(also, options={})

    def keyword(self):
        self.hass.config_entries.async_update_entry(
            entry=self._config_entry, options={}
        )

    def nested(self):
        def _inner():
            self.hass.config_entries.async_update_entry(self._config_entry, options={})

        _inner()

    def ha_property(self):
        self.hass.config_entries.async_update_entry(self.config_entry, options={})

    def aliased_ha_property(self):
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(entry, options={})

    def indirect(self):
        _copy_profile_to_cover(self.hass, profile, self._config_entry)

    def indirect_via_ha_property(self):
        _copy_profile_to_cover(self.hass, profile, self.config_entry)

    def hands_it_over_as_a_non_target(self, target):
        _copy_profile_to_cover(self.hass, self._config_entry, target)

    def writes_someone_else(self, target):
        self.hass.config_entries.async_update_entry(target, options={})


class _LaterSubclass(OptionsFlowHandler):
    def subclass_write(self):
        self.hass.config_entries.async_update_entry(self._config_entry, options={})


class _DeeperStill(_LaterSubclass):
    def deeper_subclass_write(self):
        self.hass.config_entries.async_update_entry(self.config_entry, options={})


class _SomeOtherFlow:
    def unrelated_write(self):
        self.hass.config_entries.async_update_entry(self._config_entry, options={})
"""


def test_seam_guard_sees_through_aliases_keywords_closures_and_helpers() -> None:
    """The guard is only worth having if it cannot be stepped around.

    Assigning the entry to a local, passing it by keyword, writing it from a
    nested closure, or handing it to a helper that writes it are all the same
    bypass. A guard that matches one literal call shape catches none of them.

    Indirect writes are flagged wherever the entry appears in the argument
    list, not just in the position the helper happens to write today — which
    argument is the written one is the helper's business and can change. That
    is what ``_ALLOWED_INDIRECT_WRITES`` is for: each reviewed call site says
    in prose why it is safe, and an unreviewed one fails.

    Subclassing is the same bypass one level up: a method that writes the entry
    is no less a write for being declared on a subclass of the handler, so the
    scan follows ``bases`` rather than matching one class by name. A class that
    does not descend from the handler is nobody's business here.
    """
    direct, indirect = _seam_violations(
        _SEAM_EVASIONS,
        class_name="OptionsFlowHandler",
        writer_helpers=frozenset({"_copy_profile_to_cover"}),
    )

    assert direct == {
        "_persist_entry",
        "aliased",
        "keyword",
        "nested",
        "ha_property",
        "aliased_ha_property",
        "subclass_write",
        "deeper_subclass_write",
    }
    assert "writes_someone_else" not in direct
    assert "unrelated_write" not in direct
    assert indirect == {
        ("indirect", "_copy_profile_to_cover"),
        ("indirect_via_ha_property", "_copy_profile_to_cover"),
        ("hands_it_over_as_a_non_target", "_copy_profile_to_cover"),
    }


def test_options_flow_writes_its_own_entry_through_one_seam() -> None:
    """Every write of this flow's own entry goes through ``_persist_entry``.

    The drift guard for the fix: a future step that persists ``self.options``
    directly would silently reintroduce the half-applied switch.
    """
    import inspect

    from custom_components.adaptive_cover_pro import config_flow as cf

    direct, indirect = _seam_violations(
        inspect.getsource(cf),
        class_name="OptionsFlowHandler",
        writer_helpers=_entry_writing_helpers(),
    )

    assert direct == {
        "_persist_entry"
    }, f"self._config_entry is written outside the seam: {sorted(direct)}"
    assert indirect <= _ALLOWED_INDIRECT_WRITES, (
        "this flow's own entry is handed to an entry-writing helper from a call "
        f"site that has not been reviewed: {sorted(indirect - _ALLOWED_INDIRECT_WRITES)}"
    )
    assert indirect >= _ALLOWED_INDIRECT_WRITES, (
        "reviewed-call-site notes that no longer describe a real call site — a "
        "stale exemption is one a future write can land in without review: "
        f"{sorted(_ALLOWED_INDIRECT_WRITES - indirect)}"
    )


# ---------------------------------------------------------------------------
# Step 12 — a switch round trip inside one dialog is a no-op
# ---------------------------------------------------------------------------


async def _switch_to(
    hass: HomeAssistant, flow_id: str, new_type: str, *, geometry: bool = True
) -> dict:
    """Drive an already-open flow from the menu through a switch to *new_type*."""
    picker = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "change_cover_type"}
    )
    confirm = await hass.config_entries.options.async_configure(
        picker["flow_id"], {CONF_SENSOR_TYPE: new_type}
    )
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {"confirm": True}
    )
    if not geometry:
        return result
    return await hass.config_entries.options.async_configure(result["flow_id"], {})


@pytest.mark.integration
async def test_round_trip_switch_restores_the_original_default(
    hass: HomeAssistant,
) -> None:
    """Blind → awning → blind must land back on the user's stored default.

    Each hop re-seeds against the type it is leaving, so a round trip walks
    50 → 0 → 100 and the original value is gone. The re-seed belongs to the
    pending switch, so unwinding the switch must unwind it too.
    """
    entry = await _setup_entry(hass, entry_id="round_trip_default")

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    menu = await _switch_to(hass, menu["flow_id"], CoverType.AWNING)
    menu = await _switch_to(hass, menu["flow_id"], CoverType.BLIND)
    await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert entry.options[CONF_DEFAULT_HEIGHT] == 50


@pytest.mark.integration
async def test_round_trip_switch_reloads_nothing(hass: HomeAssistant) -> None:
    """Back on the type it started as, Save & Close has nothing to apply.

    Counted across BOTH reload paths, because a round trip can trip either.
    ``_cover_type_changed`` is derived from ``self.sensor_type`` against the
    stored one, so A → B → A must read as "no switch" and skip the ``data``
    write and the explicit reload it schedules; and the re-seed each hop applied
    must unwind, or the leftover ``default_percentage`` delta has the update
    listener reloading on its own.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = await _setup_entry(hass, entry_id="round_trip_reload")
    options_before = dict(entry.options)

    async def _straight_back_to_menu(self, user_input=None):
        return await self.async_step_init()

    with (
        patch.object(OptionsFlowHandler, "async_step_geometry", _straight_back_to_menu),
        _count_reloads(hass) as reload,
    ):
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        menu = await _switch_to(hass, menu["flow_id"], CoverType.AWNING, geometry=False)
        menu = await _switch_to(hass, menu["flow_id"], CoverType.BLIND, geometry=False)
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    assert dict(entry.options) == options_before
    assert _reloads(reload, entry) == 0


# ---------------------------------------------------------------------------
# Step 13 — the polarity screen names every stored position, not four of them
# ---------------------------------------------------------------------------
#
# ``_POLARITY_CARRIED_LINE`` says "these stored positions now mean the
# opposite". Naming three keys out of ten makes it worse than silence: a user
# who checks exactly what they were told to check is left with the rest
# inverted.


@pytest.mark.integration
async def test_polarity_disclosure_names_weather_override_position(
    hass: HomeAssistant,
) -> None:
    """A blind's "raise fully in wind/rain" becomes "extend fully" on an awning."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_OVERRIDE_POSITION,
    )

    entry = await _setup_entry(
        hass,
        entry_id="polarity_weather",
        extra_options={CONF_WEATHER_OVERRIDE_POSITION: 100},
    )

    note = await _polarity_note(hass, entry, CoverType.AWNING)

    assert CONF_WEATHER_OVERRIDE_POSITION in note


@pytest.mark.integration
async def test_polarity_disclosure_names_custom_position_slots(
    hass: HomeAssistant,
) -> None:
    """Custom-position scene values and their ceilings invert too."""
    entry = await _setup_entry(
        hass,
        entry_id="polarity_custom_slots",
        extra_options={
            "custom_position_1": 100,
            "custom_position_position_max_1": 40,
        },
    )

    note = await _polarity_note(hass, entry, CoverType.AWNING)

    assert "custom_position_1" in note
    assert "custom_position_position_max_1" in note


@pytest.mark.integration
async def test_polarity_disclosure_names_every_stored_position(
    hass: HomeAssistant,
) -> None:
    """The completeness assertion, driven off the field registry itself."""
    from custom_components.adaptive_cover_pro.config_fields import (
        PositionRole,
        position_roles,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    disclosed = {
        key
        for key, role in position_roles().items()
        if role in PositionRole.disclosed_roles()
    }
    assert disclosed, "no disclosed primary-axis positions declared"

    # A value that is not the polarity-neutral no-op for any of them.
    stored = dict.fromkeys(disclosed, 40)
    entry = await _setup_entry(
        hass, entry_id="polarity_completeness", extra_options=stored
    )

    note = await _polarity_note(hass, entry, CoverType.AWNING)

    live = get_policy(CoverType.AWNING).live_option_keys()
    missing = sorted(k for k in disclosed if k in live and k not in note)
    assert not missing, f"undisclosed inverting positions: {missing}"


def test_every_percentage_option_declares_a_position_role() -> None:
    """A new 0-100 % option must be classified before it can ship.

    The drift guard: without it the disclosure list is a second hand-maintained
    tuple that falls behind ``const.py`` exactly the way the first one did.
    """
    from custom_components.adaptive_cover_pro.config_fields import position_roles
    from custom_components.adaptive_cover_pro.cover_types import percentage_option_keys

    classified = set(position_roles())
    candidates = percentage_option_keys()

    assert not (candidates - classified), (
        "percentage options with no PositionRole: " f"{sorted(candidates - classified)}"
    )
    assert not (classified - candidates), (
        f"PositionRole declared for non-percentage keys: "
        f"{sorted(classified - candidates)}"
    )


# ---------------------------------------------------------------------------
# Step 14 — the blocked-type reason and the filter must agree on unavailability
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_blocked_type_reason_ignores_unavailable_covers(
    hass: HomeAssistant,
) -> None:
    """An unreadable cover is skipped by the filter, so it must not be blamed.

    ``entities_satisfy_selector`` skips a ``None`` cap entry; the warning
    builder does not, and ``caps_get(None, …)`` is False — so a merely
    unavailable entity gets reported as "does not support set_position".
    """
    from homeassistant.const import STATE_UNAVAILABLE

    entry = await _setup_entry(
        hass,
        entities=["cover.readable", "cover.offline"],
        entry_id="blocked_unavailable",
    )
    hass.states.async_set("cover.offline", STATE_UNAVAILABLE, {})

    result = await _open_picker(hass, entry)

    blocked = result["description_placeholders"]["blocked_types"]
    assert "cover.offline" not in blocked
    assert "cover.readable" in blocked
