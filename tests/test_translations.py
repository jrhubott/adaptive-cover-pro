"""Tests for translation files — structural parity with en.json + content hygiene.

The integration ships English, German, and French. `en.json` is the single
source of truth. DE/FR must match en.json exactly for every section including
`services`.
"""

from __future__ import annotations

import json
import re
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


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_literal_urls_in_config_and_options_step_strings(lang_file: Path) -> None:
    """config/options step strings must not embed literal URLs — pass them as
    description placeholders instead.

    HA's hassfest rejects a literal ``http(s)://`` URL inside any ``config`` /
    ``options`` step title/description/data string with "the string should not
    contain URLs, please use description placeholders instead". That rule is not
    covered by ``validate_translations.py`` or structural-parity tests, so a
    literal wiki link slips past local CI and only fails in the Hassfest GitHub
    job (this bit issue #945 Part 2's summary pointer). This guard reproduces
    the rule locally: every wiki link must be authored as ``[text]({learn_more})``
    (or similar) with the URL supplied at runtime via ``description_placeholders``.
    """
    data = _load(lang_file)
    offenders: list[str] = []
    for section in ("config", "options"):
        steps = data.get(section, {}).get("step", {})
        for step_id, step in steps.items():
            # _flatten returns leaf dotpaths; re-walk each to read its value.
            for dotpath in _flatten(step):
                node = step
                for part in dotpath.split("."):
                    node = node[part]
                if isinstance(node, str) and ("http://" in node or "https://" in node):
                    offenders.append(f"{section}.step.{step_id}.{dotpath}")
    assert not offenders, (
        f"{lang_file.name}: literal URL(s) in config/options step strings — "
        f"hassfest forbids this. Use a {{learn_more}} placeholder instead:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# Issue #211 Option 2 — blind_spot labels are acceptance-edge-relative, not
# azimuth-relative. Issue #604 renamed the "FOV" frame to "acceptance edge".
# ---------------------------------------------------------------------------


def test_en_blind_spot_labels_name_window_normal_frame() -> None:
    """EN labels for the gamma edges must name the window-normal frame (#247)."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    # Options flow paginates blind spots into per-slot pages (#945); its editable
    # gamma labels live on blind_spot_slot. The create flow keeps the flat form.
    for step_key, step in (("options", "blind_spot_slot"),):
        bs = en[step_key]["step"][step]["data"]
        assert "window normal" in bs["blind_spot_left_gamma"].lower(), (
            f"{step_key}.{step}.data.blind_spot_left_gamma label must name the "
            "window-normal frame"
        )
        assert "window normal" in bs["blind_spot_right_gamma"].lower(), (
            f"{step_key}.{step}.data.blind_spot_right_gamma label must name the "
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
    # Position moved out of the create wizard (#945 Part 2) — options only now.
    for step_key in ("options",):
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
    for step_key, step in (("options", "blind_spot_slot"),):
        dd = en[step_key]["step"][step]["data_description"]
        for key in ("blind_spot_left_gamma", "blind_spot_right_gamma"):
            assert "window azimuth" not in dd[key].lower(), (
                f"{step_key}.{step}.data_description.{key} still references "
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


def test_venetian_select_options_translated_in_en() -> None:
    """The 5 venetian enum fields must carry a selector.<key>.options block whose
    keys exactly match the corresponding const tuple values, or SelectSelector's
    translation_key lookup resolves to nothing and the frontend falls back to raw
    enum values (issue: untranslated venetian select options).
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_MODES,
        VENETIAN_POST_SETTLE_MODES,
        VENETIAN_TILT_RESET_DIRECTIONS,
        VENETIAN_TILT_RESET_SCOPES,
        VENETIAN_TILT_SKIP_MODES,
    )

    en = _load(TRANSLATIONS_DIR / "en.json")
    sel = en["selector"]
    expected = {
        "venetian_mode": set(VENETIAN_MODES),
        "venetian_tilt_reset_direction": set(VENETIAN_TILT_RESET_DIRECTIONS),
        "venetian_tilt_reset_scope": set(VENETIAN_TILT_RESET_SCOPES),
        "venetian_tilt_skip_mode": set(VENETIAN_TILT_SKIP_MODES),
        "venetian_post_settle_mode": set(VENETIAN_POST_SETTLE_MODES),
    }
    for key, option_values in expected.items():
        assert key in sel, f"selector.{key} missing from en.json"
        assert set(sel[key]["options"].keys()) == option_values


def test_priority_field_documents_all_three_gates() -> None:
    """The slot-1 priority description names all three bypassed gates (#711).

    A safety-priority slot bypasses the automatic-control toggle, manual
    override, and the start/end time window — the long description on both the
    config and options flows must spell out all three so the footgun is
    discoverable from the field help.
    """
    en = _load(TRANSLATIONS_DIR / "en.json")
    # Options flow paginates custom positions (#945): the priority help lives on
    # the generic per-slot page key; the create flow keeps the slot-1 suffixed key.
    # custom_position moved out of the create wizard (#945 Part 2); the paged
    # options slot page is the sole home of the priority help now.
    for step_key, step, key in (
        ("options", "custom_position_slot", "custom_position_priority"),
    ):
        dd = en[step_key]["step"][step]["data_description"]
        desc = dd[key]
        for phrase in ("automatic-control toggle", "manual override", "time window"):
            assert (
                phrase in desc
            ), f"{step_key}.{step}.data_description.{key} missing {phrase!r}"


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


def _data_descriptions(bundle: dict) -> list[tuple[str, str]]:
    """Every ``*.data_description.*`` leaf in the bundle, as (dotpath, text)."""
    out: list[tuple[str, str]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, str) and ".data_description." in path:
            out.append((path, node))

    walk(bundle, "")
    return out


def _has_unescaped_double_brace(text: str) -> bool:
    """Return True if ``text`` has a literal ``{{``/``}}`` outside ICU quoting.

    Narrower than :func:`_has_unescaped_brace` on purpose. Config-flow help text
    legitimately carries single-brace ICU arguments the frontend substitutes
    (``{computed_fov}``, ``{wind_unit}``), so flagging every brace here would be
    all false positives. A DOUBLED brace is never a valid ICU argument — it is
    always a literal Jinja example that must be quoted as ``'{{'`` … ``'}}'`` or
    the whole string renders as MALFORMED_ARGUMENT (issue #793).
    """
    i, n = 0, len(text)
    in_quote = False
    while i < n:
        c = text[i]
        if c == "'":
            nxt = text[i + 1] if i + 1 < n else ""
            if in_quote:
                if nxt == "'":
                    i += 2
                    continue
                in_quote = False
                i += 1
                continue
            if nxt in "{}#|":
                in_quote = True
                i += 1
                continue
            if nxt == "'":
                i += 2
                continue
            i += 1
            continue
        if not in_quote and c in "{}" and text[i : i + 2] in ("{{", "}}"):
            return True
        i += 1
    return False


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_data_descriptions_icu_safe(lang_file: Path) -> None:
    """Config-flow help text must not carry an unescaped Jinja ``{{`` (#793/#1167).

    The sibling guard above covers service field descriptions. This one covers
    ``data_description`` leaves, where issue #1167's sun-tracking-gate help text
    shipped a raw ``{{ is_state(...) }}`` example in three steps x three
    languages — nine strings that would each have rendered as
    "Translation error: MALFORMED_ARGUMENT" with the whole description lost.
    """
    offending = [
        path
        for path, desc in _data_descriptions(_load(lang_file))
        if _has_unescaped_double_brace(desc)
    ]
    assert not offending, (
        f"{lang_file.name}: {len(offending)} data_description(s) contain an "
        f"unescaped Jinja '{{{{'/'}}}}' — quote literal braces ICU-style as "
        f"'{{{{' ... '}}}}' so the frontend does not raise MALFORMED_ARGUMENT. "
        f"Offenders: {offending}"
    )


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_get_diagnostics_and_get_troubleshooting_config_entry_id_descriptions_match(
    lang_file: Path,
) -> None:
    """get_diagnostics and get_troubleshooting expose the identical
    config_entry_id escape hatch, so the action picker must show one text for
    it, not two (issue #1059 audit round 3, nit #5).
    """
    services = _load(lang_file)["services"]
    diag_desc = services["get_diagnostics"]["fields"]["config_entry_id"]["description"]
    troubleshoot_desc = services["get_troubleshooting"]["fields"]["config_entry_id"][
        "description"
    ]
    assert diag_desc == troubleshoot_desc, (
        f"{lang_file.name}: get_diagnostics and get_troubleshooting "
        "config_entry_id descriptions must be identical"
    )


@pytest.mark.unit
def test_manual_override_duration_mode_options_translated_in_en():
    """All five duration-mode values need a non-empty English selector label (#1044)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_DURATION_MODE,
        MANUAL_OVERRIDE_DURATION_MODES,
    )

    en = _load(TRANSLATIONS_DIR / "en.json")
    options = en["selector"][CONF_MANUAL_OVERRIDE_DURATION_MODE]["options"]

    assert set(options) == set(MANUAL_OVERRIDE_DURATION_MODES)
    assert all(str(v).strip() for v in options.values())

    step = en["options"]["step"]["manual_override"]
    assert step["data"][CONF_MANUAL_OVERRIDE_DURATION_MODE].strip()
    assert step["data_description"][CONF_MANUAL_OVERRIDE_DURATION_MODE].strip()


# ---------------------------------------------------------------------------
# Issues #1200 / #1201 — the picker dropdown and the screen after it must name
# the same cover type. ``selector.mode.options`` labels the dropdown; the
# confirm step renders ``get_policy(key).display_label(labels)``. Two
# independent bundles name one value, and nothing cross-checked them, so
# ``cover_awning`` shipped in English as "Horizontal blind" on one screen and
# "Horizontal Awning" on the other (#1200), and DE/FR shipped a drop-arm awning
# as "Gelenkarmmarkise"/"Schwenkmarkise" and "Store banne à bras articulé"/
# "Store oscillant" (#1201).
#
# Runs over every shipped language. The rule is deliberately language-neutral:
# #1200's first cut matched on the English head noun (last token), which does
# not survive contact with German compounding ("Doppelpaneel-Beschattung" is
# one token, not two) or French postposed adjectives.
# ---------------------------------------------------------------------------


_PAREN_TAIL = re.compile(r"\s*\(([^()]*)\)\s*$")


def _split_label(label: str) -> tuple[list[str], str | None]:
    """Split a cover-type label into (stem tokens, trailing qualifier).

    Case-folds, then peels off one trailing parenthesised qualifier — the
    picker adds hardware hints the summary label routinely omits ("(dual-axis)",
    "(drop-arm)", "(sheer + blackout)"). Returns ``None`` for the qualifier when
    the label carries none.
    """
    text = label.casefold().strip()
    qualifier: str | None = None
    if match := _PAREN_TAIL.search(text):
        qualifier = " ".join(match.group(1).split())
        text = text[: match.start()]
    return text.split(), qualifier


def _is_prefix(shorter: list[str], longer: list[str]) -> bool:
    """Whether *shorter* is a token-wise prefix of *longer*."""
    return len(shorter) <= len(longer) and longer[: len(shorter)] == shorter


@pytest.mark.parametrize("language", sorted(SHIPPED_LANGUAGES))
def test_selector_mode_labels_name_the_same_cover_type_as_the_policy(
    language: str,
) -> None:
    """Every ``selector.mode`` option names the type its policy names (#1201).

    Two clauses, both language-neutral:

    * **Stem** — after case-folding and peeling one trailing parenthetical, the
      two stems must be equal, or one must be a token-wise prefix of the other
      ("Dual panel" vs "Dual Panel Shade"). No head-noun shortcut: a rule that
      compares only the last token passes "Jalousette" against "Jalousie" in a
      language that welds its nouns together.
    * **Qualifier** — when *both* labels carry a trailing parenthetical they
      must say the same thing. The picker is free to add a hint the summary
      omits; it is not free to call the same hardware "(double axe)" on one
      screen and "(deux axes)" on the next.

    Deliberately no exemption list. Every shipped row satisfies the rule on its
    own wording — a carve-out here is drift with a comment attached.
    """
    from custom_components.adaptive_cover_pro.config_flow import (
        _load_summary_labels_sync,
    )
    from custom_components.adaptive_cover_pro.cover_types import (
        POLICY_REGISTRY,
        get_policy,
    )

    options = _load(TRANSLATIONS_DIR / f"{language}.json")["selector"]["mode"][
        "options"
    ]

    unknown = [key for key in options if key not in POLICY_REGISTRY]
    assert not unknown, (
        "selector.mode.options has keys with no registered policy, so their "
        f"labels are never cross-checked against one: {unknown}"
    )

    # The same resolution the confirm step performs: the language bundle
    # overlaid on the code-owned English defaults, per key.
    labels = _load_summary_labels_sync(language)

    mismatched: list[str] = []
    for key, label in options.items():
        policy_label = get_policy(key).display_label(labels)
        selector_stem, selector_qualifier = _split_label(label)
        policy_stem, policy_qualifier = _split_label(policy_label)

        stem_agrees = _is_prefix(selector_stem, policy_stem) or _is_prefix(
            policy_stem, selector_stem
        )
        qualifier_agrees = (
            selector_qualifier is None
            or policy_qualifier is None
            or selector_qualifier == policy_qualifier
        )
        if not (stem_agrees and qualifier_agrees):
            mismatched.append(
                f"  - {key}: picker says {label!r}, confirm says {policy_label!r}"
            )

    assert not mismatched, (
        f"[{language}] selector.mode and display_label() name the same cover "
        "type differently, so the picker and the screen after it disagree:\n"
        + "\n".join(mismatched)
    )


@pytest.mark.parametrize("language", sorted(SHIPPED_LANGUAGES))
def test_cover_type_labels_are_distinct_within_a_language(language: str) -> None:
    """No two cover types may share a label in either bundle (#1201).

    The parity guard above compares one key's two labels, which says nothing
    about two keys colliding. Reconciling a pair by deleting whatever made it
    specific satisfies parity and still breaks the UI: a first pass at #1201
    settled both German venetian rows on a bare "Jalousie", so tilt-only and
    dual-axis became the same word on the confirm screen and in the config
    summary. The picker is worse — two identical dropdown entries are
    unpickable.
    """
    from custom_components.adaptive_cover_pro.config_flow import (
        _load_summary_labels_sync,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    options = _load(TRANSLATIONS_DIR / f"{language}.json")["selector"]["mode"][
        "options"
    ]
    labels = _load_summary_labels_sync(language)

    for surface, rendered in (
        ("picker (selector.mode.options)", dict(options)),
        (
            "confirm (display_label)",
            {key: get_policy(key).display_label(labels) for key in options},
        ),
    ):
        by_label: dict[str, list[str]] = {}
        for key, text in rendered.items():
            by_label.setdefault(text.casefold(), []).append(key)
        collisions = {text: keys for text, keys in by_label.items() if len(keys) > 1}
        assert not collisions, (
            f"[{language}] {surface} gives one name to several cover types, so "
            f"the user cannot tell them apart: {collisions}"
        )


def test_every_offered_cover_type_has_a_selector_mode_label() -> None:
    """The other direction: no offerable type may be missing its label (#1200).

    ``test_selector_mode_labels_name_the_same_cover_type_as_the_policy`` walks
    the label bundle and checks each key resolves to a policy, which says
    nothing about a policy that ships with no key at all. A new cover type
    registered without one renders its raw ``cover_*`` string in both the
    create flow's dropdown and the change-cover-type picker.

    Scoped to ``SENSOR_TYPE_MENU``, not ``POLICY_REGISTRY``: the menu is what
    both dropdowns are built from — ``CONFIG_SCHEMA`` passes it straight to the
    selector, and ``_eligible_cover_types``/``_offered_cover_types`` iterate it
    — while the registry also holds virtual entry types (Building Profile,
    Group, Command Queue) that no cover-type dropdown ever offers and that
    therefore need no ``selector.mode`` label.
    """
    from custom_components.adaptive_cover_pro.config_flow import SENSOR_TYPE_MENU

    options = _load(TRANSLATIONS_DIR / "en.json")["selector"]["mode"]["options"]

    missing = [key for key in SENSOR_TYPE_MENU if key not in options]
    assert not missing, (
        "cover types the picker offers with no selector.mode.options label, so "
        f"the dropdown renders their raw key: {missing}"
    )


# ---------------------------------------------------------------------------
# Issue #1203 — intra-bundle wording consistency the #1201/#1202 parity guards
# don't reach. Those guards only compare one key's label *across* bundles
# (picker vs. confirm); they say nothing about a single term being reused
# consistently for unrelated keys *within* one language, and never touch
# `services.*` at all.
#
# One table, one assertion body: each row names a bundle + key path and the
# terms that key's value must/must-not contain. A site that shares a term
# with another site (e.g. a picker label and the help text that should echo
# it) gets its own row rather than a shared derivation — cheap to add a row,
# expensive to special-case a shape.
#
# ``same_term_as`` (re-audit follow-up) is the one exception: a row can point
# at another ``(bundle, key_path)`` whose value carries a trailing
# parenthetical — e.g. "Store à projection (bras de projection)" — and demand
# that its own value echo whatever term is *actually* inside that
# parenthetical right now. The term is derived at test time with the same
# ``_PAREN_TAIL`` regex the picker/confirm parity guard uses above, not
# restated as a literal, so renaming the referenced parenthetical (and
# updating only its own row's ``must_contain``) is enough to turn every row
# that points at it red without anyone touching them — the cross-key
# guarantee the original single-purpose test gave for the FR drop-arm term
# before it was folded into this table.
# ---------------------------------------------------------------------------


def _bundle_path(bundle: str) -> Path:
    """Resolve a ``"<dir>/<file>.json"`` shorthand to its path on disk."""
    directory, filename = bundle.split("/", 1)
    if directory == "translations":
        return TRANSLATIONS_DIR / filename
    if directory == "summary_i18n":
        return TRANSLATIONS_DIR.parent / "summary_i18n" / filename
    raise ValueError(f"unknown bundle directory: {directory!r}")


def _resolve(bundle: str, key_path: tuple[str, ...]) -> str:
    """Load *bundle*, walk *key_path*, and return the leaf — which must be a str.

    Centralizing the walk means every caller — a row's own lock and any
    ``same_term_as`` coupling that resolves the referenced key path — gets
    the same guard: a ``key_path`` that stops one segment short lands on a
    ``dict``, and without this check an empty ``must_contain``/
    ``must_not_contain`` (or a coupling term that happens not to appear)
    would pass silently instead of raising, because ``in`` on a dict checks
    keys, not text.
    """
    value: object = _load(_bundle_path(bundle))
    for part in key_path:
        value = value[part]
    location = f"{bundle}:{'.'.join(key_path)}"
    assert isinstance(value, str), f"{location} did not resolve to a string: {value!r}"
    return value


# Each row's ``same_term_as`` (default: ``None``) points at another
# ``(bundle, key_path)`` whose trailing parenthetical holds the term *this*
# row's value must also contain, extracted at test time with ``_PAREN_TAIL``
# — the same regex ``_split_label`` uses above. Only the two FR arm_length
# rows need this today, both pointing at the drop-arm picker; the field costs
# nothing on every other row.
_FR_OSCILLATING_AWNING_PICKER = (
    "translations/fr.json",
    ("selector", "mode", "options", "cover_oscillating_awning"),
)

_WORDING_LOCKS = [
    # -- #1203: cover_blind names a fabric shade, not a slatted Jalousie.
    # "Jalousie" already correctly names cover_tilt/cover_venetian; "Rollo"
    # already names the other position-only cover (cover_day_night_shade).
    pytest.param(
        "translations/de.json",
        ("selector", "mode", "options", "cover_blind"),
        ("Vertikales Rollo",),
        ("Jalousie",),
        None,
        id="de-cover_blind-picker",
    ),
    pytest.param(
        "summary_i18n/de.json",
        ("cover_types", "blind"),
        ("Vertikales Rollo",),
        ("Jalousie",),
        None,
        id="de-cover_blind-confirm",
    ),
    # -- #1203: FR names the oscillating awning's drop arm the same way
    # everywhere. A prior fix landed "bras oscillant" on the picker while the
    # arm_length help text (both steps) still said "bras pivotant" — two
    # words for the same part within one bundle. ``same_term_as`` restores
    # the cross-key coupling the original (pre-table) test had: each
    # arm_length row must echo whatever the picker's parenthetical actually
    # says right now, not just the literal "bras de projection" below.
    pytest.param(
        *_FR_OSCILLATING_AWNING_PICKER,
        ("bras de projection",),
        ("bras oscillant", "bras pivotant"),
        None,
        id="fr-oscillating_awning-picker",
    ),
    pytest.param(
        "translations/fr.json",
        ("config", "step", "geometry", "data_description", "arm_length"),
        ("bras de projection",),
        ("bras oscillant", "bras pivotant"),
        _FR_OSCILLATING_AWNING_PICKER,
        id="fr-arm_length-config",
    ),
    pytest.param(
        "translations/fr.json",
        ("options", "step", "geometry", "data_description", "arm_length"),
        ("bras de projection",),
        ("bras oscillant", "bras pivotant"),
        _FR_OSCILLATING_AWNING_PICKER,
        id="fr-arm_length-options",
    ),
    # -- #1203: FR set_tilt service description matches the picker's "deux
    # axes" wording. PR#1202 moved cover_venetian to "Store vénitien (deux
    # axes)", but its parity guard only walks selector.mode.options vs.
    # display_label() — it never reaches services.*.
    pytest.param(
        "translations/fr.json",
        ("selector", "mode", "options", "cover_venetian"),
        ("(deux axes)",),
        ("à double axe",),
        None,
        id="fr-cover_venetian-picker",
    ),
    pytest.param(
        "translations/fr.json",
        ("services", "set_tilt", "description"),
        ("(deux axes)",),
        ("à double axe",),
        None,
        id="fr-set_tilt-description",
    ),
    # -- #1203: DE arm_length help text names the arm and pivot correctly.
    # #1201 fixed the noun to "Fallarmmarkise" (matching
    # cover_oscillating_awning) but left the surrounding sentence saying
    # "Schwenkarmes" (should be "Fallarmes") and "Wandpivot", which is not a
    # German word ("Wanddrehpunkt" is the trade term).
    pytest.param(
        "translations/de.json",
        ("config", "step", "geometry", "data_description", "arm_length"),
        ("Fallarmes", "Wanddrehpunkt"),
        ("Schwenkarmes", "Wandpivot"),
        None,
        id="de-arm_length-config",
    ),
    pytest.param(
        "translations/de.json",
        ("options", "step", "geometry", "data_description", "arm_length"),
        ("Fallarmes", "Wanddrehpunkt"),
        ("Schwenkarmes", "Wandpivot"),
        None,
        id="de-arm_length-options",
    ),
    # -- #1203 follow-up: DE set_tilt service description matches the
    # picker's "zweiachsig" wording. Same drift class as the FR "deux axes"
    # row above, caught in the same audit pass: the picker (cover_venetian =
    # "Jalousie (zweiachsig)") uses the German term, but the service
    # description still carried the untranslated English qualifier
    # "dual-axis" — services.* is outside every picker/confirm parity guard.
    pytest.param(
        "translations/de.json",
        ("services", "set_tilt", "description"),
        ("zweiachsig",),
        ("dual-axis",),
        None,
        id="de-set_tilt-description",
    ),
]


@pytest.mark.parametrize(
    "bundle, key_path, must_contain, must_not_contain, same_term_as",
    _WORDING_LOCKS,
)
def test_intra_bundle_wording_is_consistent(
    bundle: str,
    key_path: tuple[str, ...],
    must_contain: tuple[str, ...],
    must_not_contain: tuple[str, ...],
    same_term_as: tuple[str, tuple[str, ...]] | None,
) -> None:
    """A single site's value carries the current term, not a drifted one (#1203).

    Each parametrize case is one lock: one bundle, one key path, the term(s)
    it must carry, and the term(s) it must not. Adding a new intra-bundle
    consistency lock costs one row, not a new hand-rolled test.

    A non-``None`` ``same_term_as`` adds a runtime coupling on top: it names
    another ``(bundle, key_path)`` whose value carries a trailing
    parenthetical (peeled with ``_PAREN_TAIL``, same as the picker/confirm
    parity guard above), and whatever term is actually inside it — not this
    row's own ``must_contain`` literal — must also appear in *this* row's
    value. Rename the referenced parenthetical without updating this row and
    it goes red on the coupling, not just on its own (unchanged) literal.
    """
    value = _resolve(bundle, key_path)

    location = f"{bundle}:{'.'.join(key_path)}"
    for term in must_contain:
        assert term in value, f"{location} should contain {term!r}: {value!r}"
    for term in must_not_contain:
        assert term not in value, f"{location} still contains {term!r}: {value!r}"

    if same_term_as is not None:
        source_bundle, source_key_path = same_term_as
        source_value = _resolve(source_bundle, source_key_path)
        source_location = f"{source_bundle}:{'.'.join(source_key_path)}"
        match = _PAREN_TAIL.search(source_value)
        assert match, (
            f"{source_location} has no trailing parenthetical for {location} "
            f"to echo: {source_value!r}"
        )
        term = " ".join(match.group(1).split())
        assert term in value, (
            f"{location} does not echo {source_location}'s parenthetical "
            f"term {term!r}: {value!r}"
        )
