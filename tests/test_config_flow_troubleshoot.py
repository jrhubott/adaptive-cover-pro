"""Config-flow surfaces for the read-only Troubleshoot step (issue #970, Phase 1).

Three concerns:
- ``troubleshoot`` is offered in the cover options menu (a list, for client-side
  label translation) and NEVER in the profile or group menus.
- ``async_step_troubleshoot`` runs the triage engine read-only against resolved
  diagnostics, renders the report into ``description_placeholders`` and builds a
  menu of ``[*deduped fix steps, "troubleshoot", "init"]`` — never calling
  ``async_refresh`` (which would run the pipeline and could move a cover).
- Every menu option the step can emit has an ``en.json`` label.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.diagnostics.triage import TRIAGE_RULES

pytestmark = pytest.mark.integration

_SERVICES_COVER_COORDINATORS = (
    "custom_components.adaptive_cover_pro.services.cover_coordinators"
)

_EN_JSON = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
    / "en.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cover_flow(
    hass, options: dict | None = None, entry_id: str = "cover_1"
) -> tuple[OptionsFlowHandler, MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options if options is not None else {CONF_ENTITIES: []},
        entry_id=entry_id,
        title="Kitchen Blind",
    )
    entry.add_to_hass(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.context = {}
    # HA's OptionsFlow.config_entry property resolves via self.handler (entry_id).
    flow.handler = entry.entry_id
    return flow, entry


def _virtual_flow(hass, sensor_type, entry_id: str):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: sensor_type},
        options={},
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.context = {}
    flow.handler = entry.entry_id
    return flow


def _spy_coordinator(diagnostics: dict | None = None):
    coord = MagicMock()
    coord.data.diagnostics = {} if diagnostics is None else diagnostics
    coord.async_refresh = AsyncMock()
    return coord


# ---------------------------------------------------------------------------
# Menu wiring — troubleshoot is cover-only
# ---------------------------------------------------------------------------


async def test_cover_init_menu_contains_troubleshoot_as_list(
    hass: HomeAssistant,
) -> None:
    flow, _ = _cover_flow(hass)
    result = await flow.async_step_init()
    assert result["type"] == "menu"
    assert isinstance(result["menu_options"], list)
    assert "troubleshoot" in result["menu_options"]


async def test_profile_init_menu_omits_troubleshoot(hass: HomeAssistant) -> None:
    flow = _virtual_flow(hass, CoverType.BUILDING_PROFILE, "profile_1")
    result = await flow.async_step_init()
    assert result["type"] == "menu"
    assert "troubleshoot" not in result["menu_options"]


async def test_group_init_menu_omits_troubleshoot(hass: HomeAssistant) -> None:
    flow = _virtual_flow(hass, CoverType.GROUP, "group_1")
    result = await flow.async_step_init()
    assert result["type"] == "menu"
    assert "troubleshoot" not in result["menu_options"]


# ---------------------------------------------------------------------------
# async_step_troubleshoot — the report + menu
# ---------------------------------------------------------------------------


async def test_troubleshoot_returns_menu_with_findings_and_back_options(
    hass: HomeAssistant,
) -> None:
    # Options fire rules 1 (custom_position), 3 (automation), 9 (position) and
    # 10 (position) — proving fix_step order preservation + dedup. Rule 3 needs a
    # genuinely-suspect window (inverted start>end), not the BLANK_TIME sentinel.
    options = {
        CONF_ENTITIES: [],
        "custom_position_sensor_1": "binary_sensor.t",
        "custom_position_1": 5,
        "custom_position_priority_1": CUSTOM_POSITION_SAFETY_PRIORITY,
        "start_time": "21:00:00",
        "end_time": "08:00:00",
        "min_position": 40,
        "enable_min_position": False,
    }
    flow, entry = _cover_flow(hass, options)
    coord = _spy_coordinator()
    with patch(_SERVICES_COVER_COORDINATORS, return_value={entry.entry_id: coord}):
        result = await flow.async_step_troubleshoot()

    assert result["type"] == "menu"
    assert result["menu_options"] == [
        "custom_position",
        "automation",
        "position",
        "troubleshoot",
        "init",
    ]


async def test_troubleshoot_report_contains_finding_text(
    hass: HomeAssistant,
) -> None:
    options = {CONF_ENTITIES: [], "enable_min_position": False, "min_position": 40}
    flow, entry = _cover_flow(hass, options)
    coord = _spy_coordinator()
    with patch(_SERVICES_COVER_COORDINATORS, return_value={entry.entry_id: coord}):
        result = await flow.async_step_troubleshoot()
    report = result["description_placeholders"]["report"]
    assert "Minimum position 40%" in report


async def test_troubleshoot_no_findings_renders_clean_line(
    hass: HomeAssistant,
) -> None:
    flow, entry = _cover_flow(hass, {CONF_ENTITIES: []})
    coord = _spy_coordinator()
    with patch(_SERVICES_COVER_COORDINATORS, return_value={entry.entry_id: coord}):
        result = await flow.async_step_troubleshoot()
    report = result["description_placeholders"]["report"]
    assert report.strip()  # a non-empty "all good" line
    assert result["menu_options"] == ["troubleshoot", "init"]


async def test_troubleshoot_diagnostics_unavailable_is_graceful(
    hass: HomeAssistant,
) -> None:
    # No coordinator registered and no cached snapshot -> source "unavailable".
    flow, entry = _cover_flow(hass, {CONF_ENTITIES: []})
    with patch(_SERVICES_COVER_COORDINATORS, return_value={}):
        result = await flow.async_step_troubleshoot()
    assert result["type"] == "menu"
    report = result["description_placeholders"]["report"]
    assert report.strip()
    assert result["menu_options"] == ["troubleshoot", "init"]


async def test_troubleshoot_unavailable_still_shows_config_findings(
    hass: HomeAssistant,
) -> None:
    # Diagnostics unavailable (no coordinator) but a CONFIG issue exists: the
    # finding is rendered in the report AND its fix route appears in the menu —
    # never a menu route for an unshown finding (issue #970, MINOR 3).
    options = {CONF_ENTITIES: [], "enable_min_position": False, "min_position": 40}
    flow, entry = _cover_flow(hass, options)
    with patch(_SERVICES_COVER_COORDINATORS, return_value={}):
        result = await flow.async_step_troubleshoot()
    report = result["description_placeholders"]["report"]
    assert "Diagnostics aren't available" in report  # the runtime-needs-diag note
    assert "Minimum position 40%" in report  # the CONFIG finding is rendered
    assert result["menu_options"] == ["position", "troubleshoot", "init"]


async def test_troubleshoot_never_calls_async_refresh(
    hass: HomeAssistant,
) -> None:
    flow, entry = _cover_flow(hass, {CONF_ENTITIES: []})
    coord = _spy_coordinator()
    with patch(_SERVICES_COVER_COORDINATORS, return_value={entry.entry_id: coord}):
        await flow.async_step_troubleshoot()
    coord.async_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# en.json label coverage — every emittable menu option has a label
# ---------------------------------------------------------------------------


def test_every_troubleshoot_menu_option_has_en_label() -> None:
    data = json.loads(_EN_JSON.read_text(encoding="utf-8"))
    step = data["options"]["step"]["troubleshoot"]
    assert "title" in step
    assert "{report}" in step["description"]
    labels = step["menu_options"]

    possible = {rule.fix_step for rule in TRIAGE_RULES if rule.fix_step}
    possible |= {"troubleshoot", "init"}
    missing = possible - set(labels)
    assert not missing, f"troubleshoot menu_options missing labels: {sorted(missing)}"


def test_init_menu_has_troubleshoot_label() -> None:
    data = json.loads(_EN_JSON.read_text(encoding="utf-8"))
    assert "troubleshoot" in data["options"]["step"]["init"]["menu_options"]
