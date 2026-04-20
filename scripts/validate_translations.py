#!/usr/bin/env python3
"""Translation validation and status script for Adaptive Cover Pro.

Usage:
    ./scripts/validate_translations.py            # Show status dashboard for all languages
    ./scripts/validate_translations.py de         # Show detailed report for one language
    ./scripts/validate_translations.py --ci       # CI mode: exit 1 if any language has missing/extra keys

Shipped languages: en (source), de, fr. All sections including `services` must be
present in every language file.

STATUS LEGEND:
  ✅ Complete     — key structure matches en.json, no untranslated strings
  🔄 In Progress  — key structure matches, but some values still match English
  ❌ Needs Work   — key structure does not match (missing or extra keys)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
TRANSLATIONS_DIR = (
    REPO_ROOT / "custom_components" / "adaptive_cover_pro" / "translations"
)
EN_FILE = TRANSLATIONS_DIR / "en.json"

LANGUAGES = ["de", "fr"]

EN_ONLY_SECTIONS: tuple[str, ...] = ()

# Values that are intentionally identical in all languages (technical identifiers,
# placeholders, format strings, etc.)
PLACEHOLDER_PATTERN = re.compile(r"^\{[^}]+\}$")  # pure placeholder e.g. {summary}

# "Word N" labels (e.g. "Sensor 1", "Zone 2") are technical labels, not prose.
WORD_NUMBER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\s]* \d+$")

# Dotpath keys whose values are deliberately language-universal (proper nouns,
# single words that are identical in all shipped languages, etc.).
UNIVERSAL_KEYS: set[str] = {
    "title",                                    # product name "Adaptive Cover Pro"
    "config.step.create_new.data.name",         # "Name" — same in DE/FR
    "config.step.duplicate_configure.data.name",  # "Name" — same in DE/FR
    "entity.sensor.decision_trace.state.winter",  # "Winter" — same in DE/FR
}


# ---------------------------------------------------------------------------
# JSON tree helpers
# ---------------------------------------------------------------------------


def flatten(obj: dict | str, prefix: str = "") -> dict[str, str]:
    """Recursively flatten a nested dict into {dotted.key: value} pairs (leaves only)."""
    result: dict[str, str] = {}
    if isinstance(obj, str):
        result[prefix] = obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{prefix}.{k}" if prefix else k
            result.update(flatten(v, new_key))
    # Lists are not used in translation files — skip silently
    return result


def get_keys(obj: dict, prefix: str = "") -> set[str]:
    """Return the set of all dotted key paths (leaves only)."""
    return set(flatten(obj, prefix).keys())


# ---------------------------------------------------------------------------
# Placeholder / untranslated detection
# ---------------------------------------------------------------------------


def is_likely_untranslated(key: str, en_value: str, target_value: str) -> bool:
    """Return True if target_value appears to be the same as English (not yet translated).

    We consider a string 'untranslated' if it is byte-for-byte identical to the
    English source AND is not a pure placeholder or a very short technical string
    that could legitimately be the same in every language (e.g. "OK", "N/A", unit
    labels like "%", "°", "W/m²").
    """
    if en_value != target_value:
        return False  # Already different — translated

    # Keys explicitly declared as language-universal (proper nouns, etc.)
    if key in UNIVERSAL_KEYS:
        return False

    # Pure placeholders are intentionally identical
    if PLACEHOLDER_PATTERN.match(target_value.strip()):
        return False

    # "Word N" labels (e.g. "Sensor 1") are technical identifiers, not prose
    if WORD_NUMBER_PATTERN.match(target_value.strip()):
        return False

    # Very short strings (≤3 chars) may legitimately be the same across languages
    if len(target_value.strip()) <= 3:
        return False

    # Strings that are purely numeric or purely symbols
    if re.match(r"^[\d°%.,\s±+\-*/()]+$", target_value.strip()):
        return False

    return True


# ---------------------------------------------------------------------------
# Per-language analysis
# ---------------------------------------------------------------------------


def _strip_en_only_sections(en_flat: dict[str, str]) -> dict[str, str]:
    """Return en_flat with top-level sections that DE/FR intentionally omit removed."""
    return {
        k: v
        for k, v in en_flat.items()
        if not any(
            k == section or k.startswith(f"{section}.") for section in EN_ONLY_SECTIONS
        )
    }


def analyse_language(lang: str, en_flat_full: dict[str, str]) -> dict:
    """Return analysis dict for a single language."""
    en_flat = _strip_en_only_sections(en_flat_full)
    lang_file = TRANSLATIONS_DIR / f"{lang}.json"

    if not lang_file.exists():
        return {
            "lang": lang,
            "exists": False,
            "missing_keys": list(en_flat.keys()),
            "extra_keys": [],
            "untranslated": [],
            "total_keys": len(en_flat),
            "matched_keys": 0,
            "status": "❌ Missing file",
        }

    try:
        target = json.loads(lang_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "lang": lang,
            "exists": True,
            "error": str(exc),
            "missing_keys": [],
            "extra_keys": [],
            "untranslated": [],
            "total_keys": len(en_flat),
            "matched_keys": 0,
            "status": "❌ Invalid JSON",
        }

    target_flat = flatten(target)
    en_keys = set(en_flat.keys())
    target_keys = set(target_flat.keys())

    missing = sorted(en_keys - target_keys)
    extra = sorted(target_keys - en_keys)

    untranslated = []
    for key in en_keys & target_keys:
        if is_likely_untranslated(key, en_flat[key], target_flat[key]):
            untranslated.append(key)
    untranslated.sort()

    matched = len(en_keys & target_keys)

    if missing or extra:
        status = "❌ Needs Work"
    elif untranslated:
        status = "🔄 In Progress"
    else:
        status = "✅ Complete"

    return {
        "lang": lang,
        "exists": True,
        "missing_keys": missing,
        "extra_keys": extra,
        "untranslated": untranslated,
        "total_keys": len(en_flat),
        "matched_keys": matched,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_dashboard(analyses: list[dict], en_total: int) -> None:
    """Print summary table for all languages."""
    print()
    print("  Adaptive Cover Pro — Translation Status")
    print(f"  English source: {en_total} translatable strings")
    print()
    print(
        f"  {'Language':<10} {'Keys':>9}  {'Missing':>8}  {'Extra':>6}  {'Untranslated':>13}  Status"
    )
    print(f"  {'─' * 10} {'─' * 9}  {'─' * 8}  {'─' * 6}  {'─' * 13}  {'─' * 20}")

    all_done = True
    for a in analyses:
        lang = a["lang"]
        if not a["exists"] or "error" in a:
            print(
                f"  {lang:<10} {'—':>9}  {'—':>8}  {'—':>6}  {'—':>13}  {a['status']}"
            )
            all_done = False
            continue

        keys_str = f"{a['matched_keys']}/{a['total_keys']}"
        missing_str = str(len(a["missing_keys"])) if a["missing_keys"] else "—"
        extra_str = str(len(a["extra_keys"])) if a["extra_keys"] else "—"
        untrans_str = str(len(a["untranslated"])) if a["untranslated"] else "—"
        print(
            f"  {lang:<10} {keys_str:>9}  {missing_str:>8}  {extra_str:>6}  {untrans_str:>13}  {a['status']}"
        )

        if a["status"] != "✅ Complete":
            all_done = False

    print()
    if all_done:
        print("  🎉 All languages complete!")
    else:
        remaining = [a["lang"] for a in analyses if a["status"] != "✅ Complete"]
        print(f"  Languages still needing work: {', '.join(remaining)}")
    print()


def print_detail(a: dict) -> None:
    """Print detailed report for one language."""
    lang = a["lang"]
    print()
    print(f"  Detailed report: {lang}.json")
    print(f"  Status: {a['status']}")
    print()

    if not a.get("exists"):
        print("  ❌ File does not exist.")
        print(f"     Expected: {TRANSLATIONS_DIR / f'{lang}.json'}")
        print()
        return

    if "error" in a:
        print(f"  ❌ JSON parse error: {a['error']}")
        print()
        return

    if a["missing_keys"]:
        print(
            f"  Missing keys ({len(a['missing_keys'])}) — present in en.json but absent here:"
        )
        for k in a["missing_keys"][:50]:
            print(f"    - {k}")
        if len(a["missing_keys"]) > 50:
            print(f"    ... and {len(a['missing_keys']) - 50} more")
        print()

    if a["extra_keys"]:
        print(
            f"  Extra keys ({len(a['extra_keys'])}) — present here but removed from en.json:"
        )
        for k in a["extra_keys"][:50]:
            print(f"    + {k}")
        if len(a["extra_keys"]) > 50:
            print(f"    ... and {len(a['extra_keys']) - 50} more")
        print()

    if a["untranslated"]:
        print(
            f"  Untranslated strings ({len(a['untranslated'])}) — value identical to English:"
        )
        for k in a["untranslated"][:30]:
            print(f"    ~ {k}")
        if len(a["untranslated"]) > 30:
            print(f"    ... and {len(a['untranslated']) - 30} more")
        print()

    if not a["missing_keys"] and not a["extra_keys"] and not a["untranslated"]:
        print("  ✅ All strings present and translated — nothing to do!")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate translation files against en.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "language",
        nargs="?",
        help="Language code for detailed report (e.g. de, fr, es). Omit for dashboard.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit 1 if any language has missing or extra keys.",
    )
    args = parser.parse_args()

    if not EN_FILE.exists():
        print(f"ERROR: English source file not found: {EN_FILE}", file=sys.stderr)
        return 2

    en_data = json.loads(EN_FILE.read_text(encoding="utf-8"))
    en_flat = flatten(en_data)

    if args.language:
        lang = args.language
        if lang not in LANGUAGES:
            print(
                f"ERROR: Unknown language '{lang}'. Valid: {', '.join(LANGUAGES)}",
                file=sys.stderr,
            )
            return 2
        a = analyse_language(lang, en_flat)
        print_detail(a)
        if args.ci and (a["missing_keys"] or a["extra_keys"]):
            return 1
        return 0

    # Dashboard mode
    analyses = [analyse_language(lang, en_flat) for lang in LANGUAGES]
    print_dashboard(analyses, len(_strip_en_only_sections(en_flat)))

    if args.ci:
        has_errors = any(a["missing_keys"] or a["extra_keys"] for a in analyses)
        return 1 if has_errors else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
