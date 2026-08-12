"""Companion Lovelace card presence check in the options flow (issue #1168).

Three concerns:
- ``companion_card.async_get_card_status`` answers "is the card here, and how"
  from HACS when HACS is running, falling back to a registered Lovelace
  resource so a hand-installed card is never reported as missing. It duck-types
  every HACS read — HACS is itself a custom integration with unversioned
  internals, and none of it may break the options flow.
- ``card`` is offered in the cover options menu and NEVER in the profile,
  group, or queue menus.
- ``async_step_card`` routes to one of five leaf screens, each of which owns a
  full translation block and receives only bare data through
  ``description_placeholders`` — URLs (hassfest forbids literal URLs in the
  translation strings) and bare version numbers, never an assembled English
  sentence that would render inside a German or French body. Every leaf
  returns to ``init`` on submit.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.companion_card import (
    CARD_ADD_URL,
    CARD_FULL_NAME,
    CARD_HACS_CATEGORY,
    CARD_JS_FILENAME,
    HACS_DOMAIN,
    CardStatus,
    async_get_card_status,
)
from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration

_CARD_STATUS_SEAM = (
    "custom_components.adaptive_cover_pro.config_flow.async_get_card_status"
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


def _cover_flow(hass, entry_id: str = "cover_1") -> OptionsFlowHandler:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_ENTITIES: []},
        entry_id=entry_id,
        title="Kitchen Blind",
    )
    entry.add_to_hass(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.context = {}
    # HA's OptionsFlow.config_entry property resolves via self.handler (entry_id).
    flow.handler = entry.entry_id
    return flow


def _virtual_flow(hass, sensor_type, entry_id: str) -> OptionsFlowHandler:
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


def _repo(
    *,
    installed: bool = True,
    installed_version: str | None = "v2.17.0",
    available_version: str | None = "v2.17.0",
):
    """Build a duck-typed stand-in for a HACS ``HacsRepository``."""
    return SimpleNamespace(
        data=SimpleNamespace(
            installed=installed,
            installed_version=installed_version,
            available_version=available_version,
        )
    )


def _hacs(*, repo=None, stage: str = "running", disabled: bool = False):
    """Build a duck-typed stand-in for the ``HacsBase`` at ``hass.data["hacs"]``."""
    return SimpleNamespace(
        stage=stage,
        system=SimpleNamespace(disabled=disabled),
        repositories=SimpleNamespace(get_by_full_name=lambda _name: repo),
    )


def _install_hacs(hass, hacs) -> None:
    hass.data[HACS_DOMAIN] = hacs


def _install_resource(hass, url: str) -> None:
    hass.data["lovelace"] = SimpleNamespace(
        resources=SimpleNamespace(async_items=lambda: [{"url": url, "type": "module"}])
    )


def _en_menu_labels() -> dict:
    return json.loads(_EN_JSON.read_text(encoding="utf-8"))["options"]["step"]["init"][
        "menu_options"
    ]


def _en_step(step_id: str) -> dict:
    return json.loads(_EN_JSON.read_text(encoding="utf-8"))["options"]["step"][step_id]


# ---------------------------------------------------------------------------
# Detection — companion_card.async_get_card_status
# ---------------------------------------------------------------------------


def test_no_hacs_and_no_resource_reports_nothing(hass: HomeAssistant) -> None:
    status = async_get_card_status(hass)
    assert status == CardStatus()
    assert status.hacs_present is False
    assert status.installed is False
    assert status.detected_via is None


def test_hacs_with_downloaded_repo_reports_installed(hass: HomeAssistant) -> None:
    _install_hacs(hass, _hacs(repo=_repo(installed_version="v2.17.0")))
    status = async_get_card_status(hass)
    assert status.hacs_present is True
    assert status.hacs_ready is True
    assert status.installed is True
    assert status.installed_version == "v2.17.0"
    assert status.detected_via == "hacs"


def test_hacs_running_but_repo_unknown_reports_not_installed(
    hass: HomeAssistant,
) -> None:
    _install_hacs(hass, _hacs(repo=None))
    status = async_get_card_status(hass)
    assert status.hacs_present is True
    assert status.hacs_ready is True
    assert status.installed is False
    assert status.detected_via is None


def test_hacs_repo_added_but_not_downloaded_reports_not_installed(
    hass: HomeAssistant,
) -> None:
    _install_hacs(hass, _hacs(repo=_repo(installed=False, installed_version=None)))
    status = async_get_card_status(hass)
    assert status.installed is False
    assert status.installed_version is None


def test_pending_update_surfaces_available_version(hass: HomeAssistant) -> None:
    _install_hacs(
        hass,
        _hacs(repo=_repo(installed_version="v2.16.0", available_version="v2.17.0")),
    )
    status = async_get_card_status(hass)
    assert status.installed_version == "v2.16.0"
    assert status.available_version == "v2.17.0"


@pytest.mark.parametrize(
    "kwargs",
    [{"stage": "startup"}, {"stage": "setup"}, {"disabled": True}],
    ids=["startup", "setup", "disabled"],
)
def test_hacs_not_ready_is_present_but_not_queried(hass: HomeAssistant, kwargs) -> None:
    """A rate-limited or still-starting HACS serves a stale/empty repo list.

    It must read as *present* (so the flow offers the one-click add rather than
    manual instructions) but never as an authoritative "not installed".
    """
    _install_hacs(hass, _hacs(repo=_repo(), **kwargs))
    status = async_get_card_status(hass)
    assert status.hacs_present is True
    assert status.hacs_ready is False
    assert status.installed is False


@pytest.mark.parametrize(
    "url",
    [
        f"/hacsfiles/adaptive-cover-pro-card/{CARD_JS_FILENAME}?v=2.17.0",
        f"/local/community/adaptive-cover-pro-card/{CARD_JS_FILENAME}",
    ],
    ids=["hacsfiles", "local"],
)
def test_manual_install_detected_from_lovelace_resource(
    hass: HomeAssistant, url: str
) -> None:
    _install_resource(hass, url)
    status = async_get_card_status(hass)
    assert status.installed is True
    assert status.detected_via == "resource"
    assert status.hacs_present is False


def test_resource_version_stamp_is_lifted_when_present(hass: HomeAssistant) -> None:
    _install_resource(hass, f"/hacsfiles/x/{CARD_JS_FILENAME}?v=2.17.0")
    assert async_get_card_status(hass).installed_version == "2.17.0"


def test_unrelated_resource_is_not_mistaken_for_the_card(hass: HomeAssistant) -> None:
    _install_resource(hass, "/hacsfiles/button-card/button-card.js")
    assert async_get_card_status(hass).installed is False


def test_hacs_wins_over_resource_when_both_present(hass: HomeAssistant) -> None:
    _install_hacs(hass, _hacs(repo=_repo(installed_version="v2.17.0")))
    _install_resource(hass, f"/hacsfiles/x/{CARD_JS_FILENAME}?v=1.0.0")
    status = async_get_card_status(hass)
    assert status.detected_via == "hacs"
    assert status.installed_version == "v2.17.0"


def test_repo_is_looked_up_by_full_name(hass: HomeAssistant) -> None:
    """``get_by_full_name`` is the lookup — ``is_registered`` is case-broken upstream."""
    seen: list[str] = []

    def _lookup(name):
        seen.append(name)
        return _repo()

    _install_hacs(
        hass,
        SimpleNamespace(
            stage="running",
            system=SimpleNamespace(disabled=False),
            repositories=SimpleNamespace(get_by_full_name=_lookup),
        ),
    )
    async_get_card_status(hass)
    assert seen == [CARD_FULL_NAME]


# --- Resilience: HACS internals are unversioned and must never break the flow


def test_exploding_hacs_object_degrades_to_not_installed(hass: HomeAssistant) -> None:
    class _Exploding:
        @property
        def stage(self):
            raise RuntimeError("HACS changed shape under us")

    _install_hacs(hass, _Exploding())
    status = async_get_card_status(hass)
    assert status.hacs_present is True
    assert status.installed is False


def test_exploding_repositories_lookup_degrades(hass: HomeAssistant) -> None:
    def _boom(_name):
        raise AttributeError("no such attribute")

    _install_hacs(
        hass,
        SimpleNamespace(
            stage="running",
            system=SimpleNamespace(disabled=False),
            repositories=SimpleNamespace(get_by_full_name=_boom),
        ),
    )
    assert async_get_card_status(hass).installed is False


def test_hacs_object_missing_expected_attributes_degrades(hass: HomeAssistant) -> None:
    _install_hacs(hass, SimpleNamespace())
    status = async_get_card_status(hass)
    assert status.hacs_present is True
    assert status.hacs_ready is False


def test_exploding_lovelace_resources_degrade(hass: HomeAssistant) -> None:
    def _boom():
        raise RuntimeError("resources not loaded yet")

    hass.data["lovelace"] = SimpleNamespace(
        resources=SimpleNamespace(async_items=_boom)
    )
    assert async_get_card_status(hass).installed is False


def test_lovelace_without_resources_degrades(hass: HomeAssistant) -> None:
    hass.data["lovelace"] = SimpleNamespace()
    assert async_get_card_status(hass).installed is False


# ---------------------------------------------------------------------------
# The My Home Assistant add link
# ---------------------------------------------------------------------------


def test_add_url_is_a_my_hacs_repository_redirect() -> None:
    assert CARD_ADD_URL.startswith(
        "https://my.home-assistant.io/redirect/hacs_repository/?"
    )


def test_add_url_carries_owner_repository_and_category() -> None:
    """All three params are load-bearing.

    HACS's repository dashboard only offers the "Add custom repository" confirm
    dialog for an unknown repo when ``category`` is present; without it the user
    gets "Repository not found".
    """
    owner, _, repo = CARD_FULL_NAME.partition("/")
    assert f"owner={owner}" in CARD_ADD_URL
    assert f"repository={repo}" in CARD_ADD_URL
    assert f"category={CARD_HACS_CATEGORY}" in CARD_ADD_URL


def test_hacs_category_is_plugin_not_lovelace() -> None:
    """``HacsCategory`` has no ``lovelace`` member; ``plugin`` is the wire value."""
    assert CARD_HACS_CATEGORY == "plugin"


# ---------------------------------------------------------------------------
# Menu wiring — card is cover-only, and always present
# ---------------------------------------------------------------------------


async def test_cover_init_menu_contains_card(hass: HomeAssistant) -> None:
    result = await _cover_flow(hass).async_step_init()
    assert result["type"] == "menu"
    assert isinstance(result["menu_options"], list)
    assert "card" in result["menu_options"]


async def test_card_row_is_present_even_when_card_is_installed(
    hass: HomeAssistant,
) -> None:
    _install_hacs(hass, _hacs(repo=_repo()))
    result = await _cover_flow(hass).async_step_init()
    assert "card" in result["menu_options"]


async def test_card_sits_between_sync_and_summary(hass: HomeAssistant) -> None:
    menu = (await _cover_flow(hass).async_step_init())["menu_options"]
    assert menu.index("sync") < menu.index("card") < menu.index("summary")


@pytest.mark.parametrize(
    ("sensor_type", "entry_id"),
    [
        (CoverType.BUILDING_PROFILE, "profile_1"),
        (CoverType.GROUP, "group_1"),
        (CoverType.COMMAND_QUEUE, "queue_1"),
    ],
    ids=["profile", "group", "queue"],
)
async def test_virtual_entry_menus_omit_card(
    hass: HomeAssistant, sensor_type, entry_id: str
) -> None:
    result = await _virtual_flow(hass, sensor_type, entry_id).async_step_init()
    assert result["type"] == "menu"
    assert "card" not in result["menu_options"]


def test_card_menu_entry_has_an_en_json_label() -> None:
    assert "card" in _en_menu_labels()


# ---------------------------------------------------------------------------
# async_step_card — routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_step"),
    [
        (CardStatus(installed=True, detected_via="resource"), "card_installed"),
        (
            CardStatus(
                installed=True, installed_version="v2.17.0", detected_via="hacs"
            ),
            "card_installed_version",
        ),
        (
            CardStatus(
                installed=True,
                installed_version="v2.16.0",
                available_version="v2.17.0",
                detected_via="hacs",
            ),
            "card_installed_update",
        ),
        (CardStatus(hacs_present=True, hacs_ready=True), "card_add"),
        (CardStatus(), "card_manual"),
    ],
    ids=[
        "installed-no-version",
        "installed",
        "update-available",
        "hacs-no-card",
        "no-hacs",
    ],
)
async def test_card_routes_on_status(
    hass: HomeAssistant, status: CardStatus, expected_step: str
) -> None:
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["type"] == "form"
    assert result["step_id"] == expected_step


async def test_hacs_install_without_a_version_does_not_claim_a_resource(
    hass: HomeAssistant,
) -> None:
    """HACS reports a null ``installed_version`` for a branch-tracked repo.

    That reaches the same versionless leaf a hand-registered resource does, so
    the screen must not name a detection method the router never checked. It
    asserts only what is certain: installed, version unknown.
    """
    status = CardStatus(
        hacs_present=True,
        hacs_ready=True,
        installed=True,
        installed_version=None,
        available_version="v2.17.0",
        detected_via="hacs",
    )
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == "card_installed"
    description = _en_step("card_installed")["description"]
    assert "dashboard resource" not in description
    assert "could not be determined" in description


async def test_versionless_leaf_names_no_detection_method(hass: HomeAssistant) -> None:
    """The same leaf serves a resource install and a versionless HACS install."""
    for detected_via in ("resource", "hacs"):
        status = CardStatus(installed=True, detected_via=detected_via)
        with patch(_CARD_STATUS_SEAM, return_value=status):
            result = await _cover_flow(hass).async_step_card()
        assert result["step_id"] == "card_installed"


async def test_matching_installed_and_available_is_not_an_update(
    hass: HomeAssistant,
) -> None:
    status = CardStatus(
        hacs_present=True,
        hacs_ready=True,
        installed=True,
        installed_version="v2.17.0",
        available_version="v2.17.0",
        detected_via="hacs",
    )
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == "card_installed_version"


async def test_hacs_present_but_not_ready_offers_the_add_screen(
    hass: HomeAssistant,
) -> None:
    status = CardStatus(hacs_present=True, hacs_ready=False)
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == "card_add"


async def test_manual_install_routes_to_installed_without_hacs(
    hass: HomeAssistant,
) -> None:
    status = CardStatus(installed=True, detected_via="resource")
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == "card_installed"


# ---------------------------------------------------------------------------
# The three leaf screens
# ---------------------------------------------------------------------------


async def test_add_screen_supplies_the_install_url(hass: HomeAssistant) -> None:
    status = CardStatus(hacs_present=True, hacs_ready=True)
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["description_placeholders"]["install_url"] == CARD_ADD_URL


async def test_manual_screen_supplies_hacs_and_download_urls(
    hass: HomeAssistant,
) -> None:
    with patch(_CARD_STATUS_SEAM, return_value=CardStatus()):
        result = await _cover_flow(hass).async_step_card()
    placeholders = result["description_placeholders"]
    assert placeholders["hacs_url"].startswith("https://")
    assert placeholders["download_url"].startswith("https://")


async def test_installed_screen_reports_the_version(hass: HomeAssistant) -> None:
    status = CardStatus(
        hacs_present=True,
        hacs_ready=True,
        installed=True,
        installed_version="v2.17.0",
        available_version="v2.17.0",
        detected_via="hacs",
    )
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["description_placeholders"]["installed_version"] == "v2.17.0"


async def test_update_screen_names_both_versions(hass: HomeAssistant) -> None:
    status = CardStatus(
        hacs_present=True,
        hacs_ready=True,
        installed=True,
        installed_version="v2.16.0",
        available_version="v2.17.0",
        detected_via="hacs",
    )
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    placeholders = result["description_placeholders"]
    assert placeholders["installed_version"] == "v2.16.0"
    assert placeholders["available_version"] == "v2.17.0"


_LEAF_STATUS = {
    "card_installed": CardStatus(installed=True, detected_via="resource"),
    "card_installed_version": CardStatus(
        installed=True, installed_version="v2.17.0", detected_via="hacs"
    ),
    "card_installed_update": CardStatus(
        installed=True,
        installed_version="v2.16.0",
        available_version="v2.17.0",
        detected_via="hacs",
    ),
    "card_add": CardStatus(hacs_present=True, hacs_ready=True),
    "card_manual": CardStatus(),
}

_MISSING = object()


async def test_no_leaf_assembles_user_visible_prose_in_python(
    hass: HomeAssistant,
) -> None:
    """Every non-URL placeholder is a verbatim ``CardStatus`` field.

    A Python-built sentence would render untranslated inside an otherwise
    German or French screen, which is the whole reason each state has its own
    translation block instead of one block with a ``{version_line}`` hole.

    Asserting equality with the source field rather than merely "contains no
    space" is what keeps this from passing on ``⬆️v2.17.0`` or a single
    English word.
    """
    checked = 0
    for status in _LEAF_STATUS.values():
        with patch(_CARD_STATUS_SEAM, return_value=status):
            result = await _cover_flow(hass).async_step_card()
        for name, value in result["description_placeholders"].items():
            if name == "learn_more" or name.endswith("_url"):
                continue
            # Sentinel rather than a bare getattr: a placeholder renamed to
            # something CardStatus has no field for must fail as this
            # assertion, not as an AttributeError raised before it.
            assert value == getattr(status, name, _MISSING), (
                f"{result['step_id']}/{name} is not the bare CardStatus field: "
                f"{value!r} != {getattr(status, name, _MISSING)!r}"
            )
            checked += 1
    # Guard the guard: three data placeholders exist across the five leaves
    # (installed_version twice, available_version once). A refactor that
    # renames them must not silently turn this test into a no-op.
    assert (
        checked == 3
    ), f"expected 3 data placeholders across the leaves, saw {checked}"


@pytest.mark.parametrize(
    "step_id",
    ["card_installed", "card_installed_version"],
    ids=["versionless", "version"],
)
@pytest.mark.parametrize("language", ["en", "de", "fr"], ids=lambda s: s)
def test_installed_screens_never_mention_hacs(step_id: str, language: str) -> None:
    """Both are reachable with HACS absent, where ``card_manual`` says so.

    Telling a HACS-less install to "open HACS" is the same fault as claiming a
    detection method the router never checked: the screen asserting more than
    the state it was routed on.
    """
    path = _EN_JSON.parent / f"{language}.json"
    block = json.loads(path.read_text(encoding="utf-8"))["options"]["step"][step_id]
    assert "HACS" not in block["description"]


def test_version_screen_does_not_claim_to_be_up_to_date() -> None:
    """It is reached when ``available_version`` is unknown, not just equal.

    ``async_step_card`` routes here on ``installed_version`` alone once the
    update branch declines, so an install whose available version was never
    reported would be told it is current on no evidence.
    """
    description = _en_step("card_installed_version")["description"]
    assert "up to date" not in description


async def test_unknown_available_version_is_not_reported_as_current(
    hass: HomeAssistant,
) -> None:
    status = CardStatus(
        installed=True,
        installed_version="2.10.0",
        available_version=None,
        detected_via="resource",
    )
    with patch(_CARD_STATUS_SEAM, return_value=status):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == "card_installed_version"
    assert result["description_placeholders"]["installed_version"] == "2.10.0"


@pytest.mark.parametrize("step_id", sorted(_LEAF_STATUS), ids=lambda s: s)
async def test_every_leaf_has_an_empty_schema_and_a_learn_more(
    hass: HomeAssistant, step_id: str
) -> None:
    with patch(_CARD_STATUS_SEAM, return_value=_LEAF_STATUS[step_id]):
        result = await _cover_flow(hass).async_step_card()
    assert result["step_id"] == step_id
    assert result["data_schema"]({}) == {}
    assert result["description_placeholders"]["learn_more"].startswith("https://")


@pytest.mark.parametrize("step_id", sorted(_LEAF_STATUS), ids=lambda s: s)
async def test_every_leaf_returns_to_the_menu_on_submit(
    hass: HomeAssistant, step_id: str
) -> None:
    flow = _cover_flow(hass)
    result = await getattr(flow, f"async_step_{step_id}")({})
    assert result["type"] == "menu"
    assert result["step_id"] == "init"


@pytest.mark.parametrize(
    "step_id",
    ["card_installed_version", "card_installed_update"],
    ids=["version", "update"],
)
async def test_version_leaves_reroute_when_reached_without_a_status(
    hass: HomeAssistant, step_id: str
) -> None:
    """Reached with no ``status``, a version leaf re-enters through the router.

    Rendering anyway would put an empty string in the version placeholder and
    show "on version ****" to the user.
    """
    _install_hacs(hass, _hacs(repo=_repo(installed_version="v2.17.0")))
    result = await getattr(_cover_flow(hass), f"async_step_{step_id}")()
    assert result["step_id"] == "card_installed_version"
    assert result["description_placeholders"]["installed_version"] == "v2.17.0"


# ---------------------------------------------------------------------------
# Translation blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step_id", sorted(_LEAF_STATUS), ids=lambda s: s)
def test_every_leaf_has_a_translation_block(step_id: str) -> None:
    block = _en_step(step_id)
    assert block["title"]
    assert block["description"]


@pytest.mark.parametrize(
    ("step_id", "placeholders"),
    [
        ("card_installed", {"learn_more"}),
        ("card_installed_version", {"installed_version", "learn_more"}),
        (
            "card_installed_update",
            {"installed_version", "available_version", "learn_more"},
        ),
        ("card_add", {"install_url", "learn_more"}),
        ("card_manual", {"hacs_url", "download_url", "learn_more"}),
    ],
    ids=["installed", "version", "update", "add", "manual"],
)
def test_description_placeholders_match_the_translation(
    step_id: str, placeholders: set[str]
) -> None:
    """Every ``{name}`` in the string must be supplied, or HA drops the key."""
    import string

    description = _en_step(step_id)["description"]
    used = {
        field
        for _, field, _, _ in string.Formatter().parse(description)
        if field is not None
    }
    assert used == placeholders
