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
    for section in ("title", "config", "options", "selector", "entity", "services"):
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
# Issue #211 Option 2 — blind_spot labels are FOV-relative, not azimuth-relative
# ---------------------------------------------------------------------------


def test_en_blind_spot_labels_name_fov_frame() -> None:
    """EN labels for blind_spot_left/right must name the FOV reference frame."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("options", "config"):
        bs = en[step_key]["step"]["blind_spot"]["data"]
        assert (
            "FOV" in bs["blind_spot_left"]
        ), f"{step_key}.blind_spot.data.blind_spot_left label must mention 'FOV'"
        assert (
            "FOV" in bs["blind_spot_right"]
        ), f"{step_key}.blind_spot.data.blind_spot_right label must mention 'FOV'"


def test_en_blind_spot_descriptions_do_not_mention_window_azimuth() -> None:
    """Helper text must not contradict services.yaml by saying 'from window azimuth'."""
    en = _load(TRANSLATIONS_DIR / "en.json")
    for step_key in ("options", "config"):
        dd = en[step_key]["step"]["blind_spot"]["data_description"]
        for key in ("blind_spot_left", "blind_spot_right"):
            assert "window azimuth" not in dd[key].lower(), (
                f"{step_key}.blind_spot.data_description.{key} still references "
                f"'window azimuth' — Option 2 requires FOV-left-edge framing"
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
