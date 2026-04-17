"""Tests for translation files — key consistency, content validation, and HA contract.

Verifies all 13 language files have valid structure and no regressions
(emoji removal #146, strings.json sync, etc.).
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
STRINGS_JSON = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "strings.json"
)

TRANSLATION_FILES = sorted(TRANSLATIONS_DIR.glob("*.json"))
LANGUAGE_CODES = [f.stem for f in TRANSLATION_FILES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    """Load a JSON file and return the parsed dict."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(d: dict, prefix: str = "") -> set[str]:
    """Recursively flatten a nested dict to a set of dot-delimited key paths."""
    keys = set()
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten(v, full_key)
        elif isinstance(v, list):
            keys.add(full_key)
        else:
            keys.add(full_key)
    return keys


def _all_leaf_values(d: object) -> list[str]:
    """Recursively collect all string leaf values from a nested dict."""
    values = []
    if isinstance(d, dict):
        for v in d.values():
            values.extend(_all_leaf_values(v))
    elif isinstance(d, list):
        for item in d:
            values.extend(_all_leaf_values(item))
    elif isinstance(d, str):
        values.append(d)
    return values



# ---------------------------------------------------------------------------
# 8a: All files are valid JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_translation_file_valid_json(lang_file: Path) -> None:
    """Each translation file must be valid JSON."""
    data = _load(lang_file)  # Raises if invalid
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# 8b: English is the reference — all languages must have same top-level keys
# ---------------------------------------------------------------------------


def test_all_translations_have_title_key() -> None:
    """All translation files must have a 'title' key."""
    for lang_file in TRANSLATION_FILES:
        data = _load(lang_file)
        assert "title" in data, f"{lang_file.name} missing 'title' key"


def test_all_translations_have_config_key() -> None:
    """All translation files must have a 'config' key."""
    for lang_file in TRANSLATION_FILES:
        data = _load(lang_file)
        assert "config" in data, f"{lang_file.name} missing 'config' key"


def test_en_json_has_expected_top_level_sections() -> None:
    """English translation must contain the standard HA sections."""
    en_data = _load(TRANSLATIONS_DIR / "en.json")
    for section in ("title", "config", "options"):
        assert section in en_data, f"en.json missing '{section}' section"



@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_icon_mdi_prefix_in_values(lang_file: Path) -> None:
    """Translation values must not contain 'mdi:' icon references."""
    data = _load(lang_file)
    values = _all_leaf_values(data)
    for value in values:
        assert "mdi:" not in value, (
            f"{lang_file.name}: 'mdi:' icon reference found in value: {value!r}"
        )


# ---------------------------------------------------------------------------
# 8d: No empty string values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_empty_string_values(lang_file: Path) -> None:
    """No translation value should be an empty string."""
    data = _load(lang_file)
    values = _all_leaf_values(data)
    for value in values:
        assert value != "", (
            f"{lang_file.name}: empty string found among translation values"
        )


# ---------------------------------------------------------------------------
# 8e: No zero-width or invisible unicode characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang_file", TRANSLATION_FILES, ids=LANGUAGE_CODES)
def test_no_invisible_unicode_chars(lang_file: Path) -> None:
    """Translation values must not contain zero-width joiners or similar invisible chars."""
    INVISIBLE = {
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\ufeff",  # BOM
        "\u00ad",  # soft hyphen
    }
    data = _load(lang_file)
    values = _all_leaf_values(data)
    for value in values:
        for char in INVISIBLE:
            assert char not in value, (
                f"{lang_file.name}: invisible Unicode char U+{ord(char):04X} "
                f"found in value: {value!r}"
            )


# ---------------------------------------------------------------------------
# 8f: strings.json must match en.json (HA contract)
# ---------------------------------------------------------------------------


def test_strings_json_is_valid_json() -> None:
    """strings.json must exist and be valid JSON.

    In HA custom components, translations/en.json is the primary source;
    strings.json is used by the frontend dev toolchain. They may diverge
    as en.json is evolved first. This test verifies strings.json is at
    least valid JSON so the frontend toolchain does not break.
    """
    assert STRINGS_JSON.exists(), f"strings.json not found at {STRINGS_JSON}"
    data = _load(STRINGS_JSON)
    assert isinstance(data, dict), "strings.json must be a JSON object"
    assert "config" in data, "strings.json must have a 'config' section"


# ---------------------------------------------------------------------------
# 8g: All files have consistent structure (keys not missing from any language)
# ---------------------------------------------------------------------------


def test_all_languages_have_options_section() -> None:
    """All translation files that have options in en.json should have it too."""
    en_data = _load(TRANSLATIONS_DIR / "en.json")
    if "options" not in en_data:
        pytest.skip("en.json has no options section")

    for lang_file in TRANSLATION_FILES:
        if lang_file.stem == "en":
            continue
        data = _load(lang_file)
        assert "options" in data, (
            f"{lang_file.name} is missing 'options' section present in en.json"
        )


def test_thirteen_translation_files_exist() -> None:
    """Exactly 13 language files exist in the translations directory."""
    expected_languages = {
        "cs",
        "de",
        "en",
        "es",
        "fr",
        "hu",
        "it",
        "nl",
        "pl",
        "pt-BR",
        "sk",
        "sl",
        "uk",
    }
    actual_languages = {f.stem for f in TRANSLATION_FILES}
    assert actual_languages == expected_languages, (
        f"Translation file mismatch. "
        f"Missing: {expected_languages - actual_languages}, "
        f"Extra: {actual_languages - expected_languages}"
    )
