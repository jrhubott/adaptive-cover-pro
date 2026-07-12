"""Tests for i18n of the configuration summary (issue #258).

The configuration summary is translated to the flow user's language. English
output must stay byte-identical to the pre-i18n strings — those regression
locks live in ``tests/test_config_flow_summary.py``. This file covers the new
machinery: the ``labels`` override param on ``_build_config_summary``, the
shared ``_load_summary_labels`` helper, per-user-language selection, and
placeholder parity between en/de/fr.

The translated label bundles live in the integration's ``summary_i18n/``
directory (``en.json`` / ``de.json`` / ``fr.json``) rather than under
``translations/`` — hassfest rejects a custom ``config_summary`` top-level
category in the HA translation schema, so the data is loaded directly.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.config_flow import (
    _SUMMARY_LABELS_EN,
    _build_config_summary,
    _load_summary_labels,
    _load_summary_labels_sync,
    _resolve_summary_language,
)
from custom_components.adaptive_cover_pro.const import (
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types._summary_labels import (
    AXIS_LABELS_EN,
    COVER_TYPE_LABELS_EN,
    GEOMETRY_LABELS_EN,
)

pytestmark = pytest.mark.unit

SUMMARY_I18N_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "summary_i18n"
)


# ---------------------------------------------------------------------------
# Step 2: labels override param is honored, templated fields still fill
# ---------------------------------------------------------------------------


def test_labels_override_text_appears_and_template_fills() -> None:
    """A non-default labels dict overrides text AND a templated line still
    fills its format fields.
    """
    overrides = {
        "headers.your_cover": "MEINE BESCHATTUNG",
        "rules.custom": (
            "CUSTOM #{slot} if {trigger} on -> {target}{cp_min}{tilt_note}{safety}"
        ),
        "custom.trigger_sensors": "any of {n} sensors",
    }
    labels = {**_SUMMARY_LABELS_EN, **overrides}
    config = {
        "custom_position_sensors_5": ["binary_sensor.a", "binary_sensor.b"],
        "custom_position_5": 80,
        "custom_position_priority_5": 100,
    }
    summary = _build_config_summary(config, CoverType.BLIND, labels=labels)

    # Overridden header text appears.
    assert "MEINE BESCHATTUNG" in summary
    # Templated custom-position line filled its fields from config.
    assert "CUSTOM #5 if any of 2 sensors on -> 80%" in summary


# ---------------------------------------------------------------------------
# Step 3: _load_summary_labels_sync — bundle overlay + English fallback
# ---------------------------------------------------------------------------


def test_load_summary_labels_en_returns_english_defaults() -> None:
    """``en`` needs no file read — the code-owned English dict is the source."""
    assert _load_summary_labels_sync("en") == _SUMMARY_LABELS_EN


def test_load_summary_labels_overlays_translated_bundle() -> None:
    """A translated bundle (de) overrides the English defaults key-for-key, and
    keys absent from the bundle fall back to English.
    """
    de_bundle = _config_summary_flat(_load_json("de.json"))
    labels = _load_summary_labels_sync("de")

    # Every translated key overrides the English default with the bundle value.
    assert de_bundle, "de.json bundle must not be empty"
    for key, de_value in de_bundle.items():
        assert labels[key] == de_value
    # Keys not present in the bundle still resolve to their English default.
    for key, en_value in _SUMMARY_LABELS_EN.items():
        if key not in de_bundle:
            assert labels[key] == en_value


def test_load_summary_labels_missing_language_falls_back_to_english() -> None:
    """An unknown language (no bundle file) yields the English defaults."""
    assert _load_summary_labels_sync("zz") == _SUMMARY_LABELS_EN


async def test_load_summary_labels_async_uses_passed_language() -> None:
    """The async helper passes the per-user language through to the loader and
    offloads the read to the executor.
    """

    class _FakeHass:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def async_add_executor_job(self, func, *args):
            self.calls.append(args)
            return func(*args)

    hass = _FakeHass()
    labels = await _load_summary_labels(hass, "fr")

    # The work was offloaded with the per-user language, not a system language.
    assert hass.calls == [("fr",)]
    # The result is the French bundle overlaid on English.
    assert labels == _load_summary_labels_sync("fr")


# ---------------------------------------------------------------------------
# _resolve_summary_language — issue #905: HA never populates
# context["language"] for config/options flows, so the summary always
# rendered in English regardless of the HA instance language. These tests
# cover the helper's three-way fallback: context -> hass.config.language ->
# "en".
# ---------------------------------------------------------------------------


class _FakeHassConfig:
    def __init__(self, language: str | None) -> None:
        self.language = language


class _FakeHassWithConfig:
    def __init__(self, language: str | None) -> None:
        self.config = _FakeHassConfig(language)


def test_resolve_summary_language_prefers_context_language() -> None:
    """When ``context`` carries a language, it wins over ``hass.config.language``."""
    hass = _FakeHassWithConfig("de")
    assert _resolve_summary_language(hass, {"language": "fr"}) == "fr"


def test_resolve_summary_language_falls_back_to_hass_config_language_when_context_has_none() -> (
    None
):
    """An empty context (HA's actual behavior for config/options flows) falls
    back to the HA instance language — this is the issue #905 bug-fix
    assertion.
    """
    hass = _FakeHassWithConfig("fr")
    assert _resolve_summary_language(hass, {}) == "fr"


def test_resolve_summary_language_falls_back_to_english_when_nothing_available() -> (
    None
):
    """When both context and ``hass.config.language`` are empty/falsy, the
    English default wins.
    """
    hass = _FakeHassWithConfig(None)
    assert _resolve_summary_language(hass, {}) == "en"


# ---------------------------------------------------------------------------
# Step 8: placeholder parity — every label key has identical {field} set
# across en/de/fr, else HA silently drops the translated key.
# ---------------------------------------------------------------------------


def _placeholder_fields(template: str) -> set[str]:
    """Return the set of named ``{field}`` placeholders in a format template,
    normalizing literal ``{{`` / ``}}`` braces away first.
    """
    # Remove escaped literal braces so they don't parse as fields.
    stripped = template.replace("{{", "").replace("}}", "")
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(stripped)
        if field_name
    }


def _config_summary_flat(data: dict) -> dict[str, str]:
    """Return a summary-label bundle flattened to dotted keys."""
    out: dict[str, str] = {}

    def _walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(node, str):
            out[prefix] = node

    _walk(data, "")
    return out


def test_summary_i18n_key_parity_de_fr() -> None:
    """de/fr bundles must expose the IDENTICAL key set as en — else a summary
    line silently falls back to English.
    """
    en = _config_summary_flat(_load_json("en.json"))
    assert en, "en.json bundle must not be empty"
    for lang in ("de", "fr"):
        target = _config_summary_flat(_load_json(f"{lang}.json"))
        assert set(target) == set(en), (
            f"{lang}.json key-set differs from en.json:\n"
            f"  missing: {sorted(set(en) - set(target))[:10]}\n"
            f"  extra:   {sorted(set(target) - set(en))[:10]}"
        )


def test_summary_i18n_en_matches_code_defaults() -> None:
    """The shipped ``summary_i18n/en.json`` must be byte-identical (flattened)
    to the union of the code-owned English label dicts: ``_SUMMARY_LABELS_EN``
    (config-flow summary) plus the policy-owned ``COVER_TYPE_LABELS_EN`` and
    ``GEOMETRY_LABELS_EN``. The English runtime output is driven by those code
    dicts; the bundle exists as the translation source + drift guard.
    """
    en = _config_summary_flat(_load_json("en.json"))
    assert en == {
        **_SUMMARY_LABELS_EN,
        **COVER_TYPE_LABELS_EN,
        **GEOMETRY_LABELS_EN,
        **AXIS_LABELS_EN,
    }


def test_config_summary_placeholder_parity_de_fr() -> None:
    """For every label key, de/fr must expose the IDENTICAL set of {field}
    placeholders as en — else HA silently drops the translated key.
    """
    en = _config_summary_flat(_load_json("en.json"))
    assert en, "en.json bundle must not be empty"
    for lang in ("de", "fr"):
        target = _config_summary_flat(_load_json(f"{lang}.json"))
        for key, en_value in en.items():
            assert key in target, f"{lang}.json missing label key {key!r}"
            en_fields = _placeholder_fields(en_value)
            tgt_fields = _placeholder_fields(target[key])
            assert (
                en_fields == tgt_fields
            ), f"{lang}.json[{key}] placeholder set {tgt_fields} != en {en_fields}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict:
    with (SUMMARY_I18N_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)
