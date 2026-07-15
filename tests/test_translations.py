"""Tests for translation files — structural parity with en.json + content hygiene.

The integration ships English, German, and French. `en.json` is the single
source of truth. DE/FR must match en.json exactly for every section including
`services`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
)

SHIPPED_LANGUAGES = {"en", "de", "fr"}

EN_ONLY_SECTIONS: tuple[str, ...] = ()

TRANSLATION_FILES = sorted(TRANSLATIONS_DIR.glob("*.json"))
LANGUAGE_CODES = [f.stem for f in TRANSLATION_FILES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    """Load a JSON file and return the parsed dict."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(d: object, prefix: str = "") -> set[str]:
    """Recursively flatten a nested dict to a set of dot-delimited leaf key paths."""
    keys: set[str] = set()
    if isinstance(d, dict):
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= _flatten(v, full_key)
            else:
                keys.add(full_key)
    return keys


def _all_leaf_values(d: object) -> list[str]:
    """Recursively collect all string leaf values from a nested dict."""
    values: list[str] = []
    if isinstance(d, dict):
        for v in d.values():
            values.extend(_all_leaf_values(v))
    elif isinstance(d, list):
        for item in d:
            values.extend(_all_leaf_values(item))
    elif isinstance(d, str):
        values.append(d)
    return values


def _strip_en_only(keys: set[str]) -> set[str]:
    """Drop any dotpath that belongs to an EN_ONLY_SECTIONS subtree."""
    return {
        k
        for k in keys
        if not any(
            k == section or k.startswith(f"{section}.") for section in EN_ONLY_SECTIONS
        )
    }


# ---------------------------------------------------------------------------
# File set
# ---------------------------------------------------------------------------


def test_shipped_translation_files_exist() -> None:
    """Exactly en, de, fr are present in translations/."""
    actual = {f.stem for f in TRANSLATION_FILES}
    assert actual == SHIPPED_LANGUAGES, (
        f"Translation file mismatch. "
        f"Missing: {SHIPPED_LANGUAGES - actual}, "
        f"Extra: {actual - SHIPPED_LANGUAGES}"
    )


# ---------------------------------------------------------------------------
# All files are valid JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_translation_file_valid_json(lang_file: Path) -> None:
    """Each translation file must be valid JSON."""
    data = _load(lang_file)
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


def test_en_json_has_expected_top_level_sections() -> None:
    """English must contain the standard HA sections plus services."""
    en_data = _load(TRANSLATIONS_DIR / "en.json")
    for section in (
        "title",
        "config",
        "options",
        "selector",
        "entity",
        "services",
    ):
        assert section in en_data, f"en.json missing '{section}' section"


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_all_translations_have_title_and_config(lang_file: Path) -> None:
    """Every file must have at minimum `title` and `config`."""
    data = _load(lang_file)
    assert "title" in data, f"{lang_file.name} missing 'title'"
    assert "config" in data, f"{lang_file.name} missing 'config'"


# ---------------------------------------------------------------------------
# Structural parity — every non-en file has the same keys as en.json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang_file",
    [f for f in TRANSLATION_FILES if f.stem != "en"],
    ids=[f.stem for f in TRANSLATION_FILES if f.stem != "en"],
)
def test_key_structure_matches_en(lang_file: Path) -> None:
    """Non-en files must have exactly the same leaf keys as en.json minus EN_ONLY_SECTIONS."""
    en_keys = _strip_en_only(_flatten(_load(TRANSLATIONS_DIR / "en.json")))
    target_keys = _flatten(_load(lang_file))

    missing = en_keys - target_keys
    extra = target_keys - en_keys

    assert not missing and not extra, (
        f"{lang_file.name} key-set differs from en.json:\n"
        f"  Missing ({len(missing)}): {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}\n"
        f"  Extra   ({len(extra)}): {sorted(extra)[:10]}{'...' if len(extra) > 10 else ''}"
    )


# ---------------------------------------------------------------------------
# Content hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_icon_mdi_prefix_in_values(lang_file: Path) -> None:
    """Translation values must not contain `mdi:` icon references."""
    values = _all_leaf_values(_load(lang_file))
    for value in values:
        assert (
            "mdi:" not in value
        ), f"{lang_file.name}: 'mdi:' icon reference found in value: {value!r}"


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_empty_string_values(lang_file: Path) -> None:
    """No translation value should be an empty string."""
    values = _all_leaf_values(_load(lang_file))
    for value in values:
        assert (
            value != ""
        ), f"{lang_file.name}: empty string found among translation values"


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_invisible_unicode_chars(lang_file: Path) -> None:
    """Translation values must not contain zero-width joiners or similar invisible chars."""
    invisible = {
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\ufeff",  # BOM
        "\u00ad",  # soft hyphen
    }
    values = _all_leaf_values(_load(lang_file))
    for value in values:
        for char in invisible:
            assert char not in value, (
                f"{lang_file.name}: invisible Unicode char U+{ord(char):04X} "
                f"found in value: {value!r}"
            )


# ---------------------------------------------------------------------------
# Issue #211 Option 2 — blind_spot labels are acceptance-edge-relative, not
# azimuth-relative. Issue #604 renamed the "FOV" frame to "acceptance edge".
# ---------------------------------------------------------------------------


def test_en_blind_spot_labels_name_window_normal_frame() -> None:
    """EN labels for the gamma edges must name the window-normal frame (#247)."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("options", "config"):
        bs = en[step_key]["step"]["blind_spot"]["data"]
        assert "window normal" in bs["blind_spot_left_gamma"].lower(), (
            f"{step_key}.blind_spot.data.blind_spot_left_gamma label must name the "
            "window-normal frame"
        )
        assert "window normal" in bs["blind_spot_right_gamma"].lower(), (
            f"{step_key}.blind_spot.data.blind_spot_right_gamma label must name the "
            "window-normal frame"
        )


# ---------------------------------------------------------------------------
# Issue #604 — "Field of View" renamed to "Sun acceptance angle" (SAA).
# Display strings only; option keys (fov_left/fov_right) are unchanged.
# ---------------------------------------------------------------------------


def test_en_sun_acceptance_angle_labels() -> None:
    """The fov_left/fov_right/fov_compute labels use 'Sun acceptance angle' (#604)."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("options", "config"):
        data = en[step_key]["step"]["geometry"]["data"]
        assert (
            data["fov_left"] == "Sun acceptance angle — left"
        ), f"{step_key}.sun.data.fov_left must read 'Sun acceptance angle — left'"
        assert (
            data["fov_right"] == "Sun acceptance angle — right"
        ), f"{step_key}.sun.data.fov_right must read 'Sun acceptance angle — right'"
        assert (
            "sun acceptance angle" in data["fov_compute"].lower()
        ), f"{step_key}.sun.data.fov_compute must name the sun acceptance angle"


def test_en_has_no_field_of_view_wording() -> None:
    """No user-facing en.json value may still say 'field of view' or the bare 'FOV' badge (#604)."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    offenders = [
        v
        for v in _all_leaf_values(en)
        if "field of view" in v.lower()
        or "field-of-view" in v.lower()
        or "FOV" in v
        or "fov_left" in v
        or "fov_right" in v
    ]
    assert not offenders, (
        "en.json still contains 'Field of View' / 'FOV' wording after the #604 "
        "rename:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# Issue #938 — the FR label for the sun-acceptance-angle concept is
# "angle d'ouverture solaire", NOT the anglicism "angle d'acceptance solaire"
# and NOT the pre-rename "champ de vision".
# ---------------------------------------------------------------------------


def test_fr_sun_acceptance_angle_uses_ouverture_labels() -> None:
    """FR fov_left/fov_right/fov_compute labels use 'ouverture solaire' (#938)."""
    fr = _load(TRANSLATIONS_DIR / "fr.json")
    for step_key in ("options", "config"):
        data = fr[step_key]["step"]["geometry"]["data"]
        assert (
            data["fov_left"] == "Angle d'ouverture solaire — gauche"
        ), f"{step_key}.geometry.data.fov_left must read 'Angle d'ouverture solaire — gauche'"
        assert (
            data["fov_right"] == "Angle d'ouverture solaire — droite"
        ), f"{step_key}.geometry.data.fov_right must read 'Angle d'ouverture solaire — droite'"
        assert (
            "ouverture solaire" in data["fov_compute"].lower()
        ), f"{step_key}.geometry.data.fov_compute must name the angle d'ouverture solaire"


def test_fr_has_no_acceptance_anglicism_or_champ_de_vision() -> None:
    """No FR value may use the anglicism 'd'acceptance' or the pre-rename 'champ de vision' (#938)."""
    summary_i18n_dir = TRANSLATIONS_DIR.parent / "summary_i18n"
    fr_files = [TRANSLATIONS_DIR / "fr.json", summary_i18n_dir / "fr.json"]
    offenders: list[str] = []
    for path in fr_files:
        data = _load(path)
        offenders += [
            f"{path.parent.name}/{path.name}: {v}"
            for v in _all_leaf_values(data)
            if "d'acceptance" in v.lower() or "champ de vision" in v.lower()
        ]
    assert not offenders, (
        "FR strings still contain the 'd'acceptance' anglicism or 'champ de vision' "
        "after the #938 rename to 'ouverture':\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_enforce_delta_at_endpoints_strings_present() -> None:
    """en.json carries the label + description on both config and options steps (#679)."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("config", "options"):
        pos = en[step_key]["step"]["position"]
        assert (
            "enforce_delta_at_endpoints" in pos["data"]
        ), f"{step_key}.position.data missing enforce_delta_at_endpoints label"
        assert "enforce_delta_at_endpoints" in pos["data_description"], (
            f"{step_key}.position.data_description missing "
            "enforce_delta_at_endpoints"
        )
        assert pos["data"]["enforce_delta_at_endpoints"].strip()
        assert pos["data_description"]["enforce_delta_at_endpoints"].strip()


def test_en_blind_spot_descriptions_do_not_mention_window_azimuth() -> None:
    """Helper text must not contradict services.yaml by saying 'from window azimuth'."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("options", "config"):
        dd = en[step_key]["step"]["blind_spot"]["data_description"]
        for key in ("blind_spot_left_gamma", "blind_spot_right_gamma"):
            assert "window azimuth" not in dd[key].lower(), (
                f"{step_key}.blind_spot.data_description.{key} still references "
                f"'window azimuth' — issue #247 requires window-normal framing"
            )


# ---------------------------------------------------------------------------
# Event buffer label honesty + transit_timeout step placement
# ---------------------------------------------------------------------------


def test_debug_event_buffer_label_not_manual_override_specific() -> None:
    """The event buffer is shared across all pipeline subsystems — label must not claim it is manual-override-specific."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    debug = en["options"]["step"]["debug"]
    label = debug["data"]["debug_event_buffer_size"]
    desc = debug["data_description"]["debug_event_buffer_size"]
    assert (
        "manual override" not in label.lower()
    ), f"Buffer is shared across handlers; label is misleading: {label!r}"
    desc_lower = desc.lower()
    consumers_mentioned = sum(
        kw in desc_lower
        for kw in (
            "manual override",
            "motion",
            "weather",
            "pipeline",
            "cover command",
            "time window",
        )
    )
    assert (
        consumers_mentioned >= 2
    ), f"Description should reference multiple consumers; got: {desc!r}"


def test_transit_timeout_on_manual_override_step_not_debug() -> None:
    """transit_timeout belongs on the manual_override step, not debug."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    mo = en["options"]["step"]["manual_override"]
    assert "transit_timeout" in mo.get(
        "data", {}
    ), "transit_timeout must be labelled on the manual_override step"
    assert "transit_timeout" in mo.get(
        "data_description", {}
    ), "transit_timeout must have a data_description on the manual_override step"
    debug = en["options"]["step"]["debug"]
    assert "transit_timeout" not in debug.get(
        "data", {}
    ), "transit_timeout must NOT appear on the debug step"


def test_venetian_mode_in_en_geometry_translations() -> None:
    """venetian_mode must be labelled in the geometry step of both config and options flows."""
    en = _load(TRANSLATIONS_DIR / "en.json")

    cfg_geom = en["config"]["step"]["geometry"]
    assert "venetian_mode" in cfg_geom.get(
        "data", {}
    ), "venetian_mode label missing from config.step.geometry.data"
    assert "venetian_mode" in cfg_geom.get(
        "data_description", {}
    ), "venetian_mode description missing from config.step.geometry.data_description"

    opt_geom = en["options"]["step"]["geometry"]
    assert "venetian_mode" in opt_geom.get(
        "data", {}
    ), "venetian_mode label missing from options.step.geometry.data"
    assert "venetian_mode" in opt_geom.get(
        "data_description", {}
    ), "venetian_mode description missing from options.step.geometry.data_description"


def test_priority_field_documents_all_three_gates() -> None:
    """The slot-1 priority description names all three bypassed gates (#711).

    A safety-priority slot bypasses the automatic-control toggle, manual
    override, and the start/end time window — the long description on both the
    config and options flows must spell out all three so the footgun is
    discoverable from the field help.
    """
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("config", "options"):
        dd = en[step_key]["step"]["custom_position"]["data_description"]
        desc = dd["custom_position_priority_1"]
        for phrase in ("automatic-control toggle", "manual override", "time window"):
            assert phrase in desc, (
                f"{step_key}.custom_position.data_description."
                f"custom_position_priority_1 missing {phrase!r}"
            )


# ---------------------------------------------------------------------------
# Issue #457 — FR cloud_suppression decision trace label must use action-oriented phrasing
# ---------------------------------------------------------------------------


def test_fr_cloud_suppression_decision_trace_state() -> None:
    """FR decision_trace.state.cloud_suppression must use action-oriented phrasing, not 'Suppression de nuages'."""
    fr = _load(TRANSLATIONS_DIR / "fr.json")
    value = fr["entity"]["sensor"]["decision_trace"]["state"]["cloud_suppression"]
    assert value != "Suppression de nuages", (
        "Reverted to misleading phrasing — must say 'Désactivation par temps nuageux' "
        "(reads as 'removing the clouds' to a French native speaker; see issue #457)"
    )
    assert (
        "nuageux" in value.lower()
    ), f"FR cloud_suppression state label should reference cloudy weather; got: {value!r}"


# ---------------------------------------------------------------------------
# Issue #564 — geometry description must not contain per-cover-type field enumeration
# ---------------------------------------------------------------------------


def test_geometry_description_no_field_enumeration() -> None:
    """The geometry step description must NOT contain the per-cover-type field enumeration.

    The second paragraph ("**Window height** and **shaded area** are required for vertical
    blinds and awnings...") is redundant because the form already hides irrelevant fields.
    It must be absent from both config.step.geometry.description and
    options.step.geometry.description in en.json (issue #564).
    """
    en = _load(TRANSLATIONS_DIR / "en.json")

    cfg_desc = en["config"]["step"]["geometry"]["description"]
    opt_desc = en["options"]["step"]["geometry"]["description"]

    enumeration_marker = "required for vertical"

    assert enumeration_marker not in cfg_desc, (
        f"config.step.geometry.description still contains per-cover-type enumeration "
        f"(found {enumeration_marker!r}). Remove the second paragraph (issue #564)."
    )
    assert enumeration_marker not in opt_desc, (
        f"options.step.geometry.description still contains per-cover-type enumeration "
        f"(found {enumeration_marker!r}). Remove the second paragraph (issue #564)."
    )


# ---------------------------------------------------------------------------
# Issue #733 — duplicate_configure translation keys must match schema field names
# ---------------------------------------------------------------------------


def test_duplicate_configure_translation_keys_match_schema() -> None:
    """duplicate_configure.data keys must match the actual schema field names.

    CONF_ENTITIES = 'group', CONF_AZIMUTH = 'set_azimuth'. If these don't match,
    HA renders the raw key as the label (regression guard for issue #733).
    """
    en = _load(TRANSLATIONS_DIR / "en.json")
    data = en["config"]["step"]["duplicate_configure"]["data"]
    assert (
        "group" in data
    ), "duplicate_configure.data must have key 'group' (CONF_ENTITIES = 'group')"
    assert (
        "set_azimuth" in data
    ), "duplicate_configure.data must have key 'set_azimuth' (CONF_AZIMUTH = 'set_azimuth')"
    assert (
        "entities" not in data
    ), "Wrong key 'entities' in duplicate_configure.data — must be 'group' (CONF_ENTITIES)"
    assert (
        "azimuth" not in data
    ), "Wrong key 'azimuth' in duplicate_configure.data — must be 'set_azimuth' (CONF_AZIMUTH)"


# ---------------------------------------------------------------------------
# Issue #738 — non-EN translation files must not contain untranslated English
# ---------------------------------------------------------------------------


def test_no_untranslated_learn_more() -> None:
    """Non-EN translation files must not contain the English '[Learn more]' string."""
    non_en_files = [f for f in TRANSLATION_FILES if f.stem != "en"]
    for lang_file in non_en_files:
        values = _all_leaf_values(_load(lang_file))
        offending = [v for v in values if "[Learn more]" in v]
        assert not offending, (
            f"{lang_file.name}: {len(offending)} value(s) contain untranslated "
            f"'[Learn more]' — translate to the target language. "
            f"First offender: {offending[0]!r}"
        )


# ---------------------------------------------------------------------------
# Issue #793 — service field descriptions must be ICU-safe (no unescaped braces)
# ---------------------------------------------------------------------------

# HA renders `services` translation text through the frontend's IntlMessageFormat
# (ICU) parser, which reads `{...}` as a placeholder. A literal Jinja example like
# `{{ (now()).isoformat() }}` triggers "Translation error: MALFORMED_ARGUMENT" and
# blanks the whole description. Literal braces must be escaped ICU-style with ASCII
# single quotes: `'{{'` renders `{{`, `'}}'` renders `}}`.

_ICU_SPECIAL = "{}#|"


def _has_unescaped_brace(text: str) -> bool:
    """Return True if ``text`` contains a `{`/`}` outside ICU apostrophe quoting.

    Mirrors intl-messageformat: a `'` only opens a literal-quote span when the
    next char is an ICU special char (`{ } # |`) or another `'`; a lone `'`
    (e.g. French "l'heure") is a literal apostrophe and does not start quoting.
    """
    i, n = 0, len(text)
    in_quote = False
    while i < n:
        c = text[i]
        if c == "'":
            nxt = text[i + 1] if i + 1 < n else ""
            if in_quote:
                if nxt == "'":  # '' -> literal apostrophe
                    i += 2
                    continue
                in_quote = False
                i += 1
                continue
            # not currently quoting
            if nxt in _ICU_SPECIAL:
                in_quote = True
                i += 1
                continue
            if nxt == "'":  # '' -> literal apostrophe
                i += 2
                continue
            i += 1  # lone apostrophe, literal
            continue
        if not in_quote and c in "{}":
            return True
        i += 1
    return False


def _service_field_descriptions(data: dict) -> list[tuple[str, str]]:
    """Yield (dotpath, description) for every services.*.fields.*.description."""
    out: list[tuple[str, str]] = []
    for svc_name, svc in data.get("services", {}).items():
        for field_name, field in svc.get("fields", {}).items():
            desc = field.get("description")
            if isinstance(desc, str):
                out.append(
                    (f"services.{svc_name}.fields.{field_name}.description", desc)
                )
    return out


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_service_field_descriptions_icu_safe(lang_file: Path) -> None:
    """Service field descriptions must not carry an unescaped ICU brace (#793)."""
    offending = [
        path
        for path, desc in _service_field_descriptions(_load(lang_file))
        if _has_unescaped_brace(desc)
    ]
    assert not offending, (
        f"{lang_file.name}: {len(offending)} service field description(s) contain "
        f"an unescaped '{{'/'}}' — escape literal braces ICU-style as '{{{{' … '}}}}' "
        f"so the frontend does not raise MALFORMED_ARGUMENT. Offenders: {offending}"
    )
