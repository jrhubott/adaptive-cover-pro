"""Tests for i18n of the configuration summary (issue #258).

The configuration summary is translated to the flow user's language. English
output must stay byte-identical to the pre-i18n strings — those regression
locks live in ``tests/test_config_flow_summary.py``. This file covers the new
machinery: the ``labels`` override param on ``_build_config_summary``, the
shared ``_load_summary_labels`` helper, per-user-language selection, and
placeholder parity between en/de/fr.
"""

from __future__ import annotations

import json
import string
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_flow import (
    _SUMMARY_LABELS_EN,
    _build_config_summary,
    _load_summary_labels,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.unit

TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
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
        "rules.force": ("FORCE if {n} {sensor_word} on -> {force_pos}%{min_mode}"),
    }
    labels = {**_SUMMARY_LABELS_EN, **overrides}
    config = {
        CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.a", "binary_sensor.b"],
        CONF_FORCE_OVERRIDE_POSITION: 80,
    }
    summary = _build_config_summary(config, CoverType.BLIND, labels=labels)

    # Overridden header text appears.
    assert "MEINE BESCHATTUNG" in summary
    # Templated force line filled its fields from config.
    assert "FORCE if 2 sensors on -> 80%" in summary


# ---------------------------------------------------------------------------
# Step 3: _load_summary_labels — prefix strip + English-default fallback
# ---------------------------------------------------------------------------


async def test_load_summary_labels_strips_prefix_and_falls_back() -> None:
    """The loaded bundle's keys are stripped of the component/category prefix,
    overlaid onto the English defaults; unreturned keys keep their English
    default value.
    """
    prefix = f"component.{DOMAIN}.config_summary."
    fake_translations = {
        f"{prefix}headers.your_cover": "Ihre Beschattung",
    }

    hass = MagicMock()
    with _patch_async_get_translations(fake_translations) as mock_get:
        labels = await _load_summary_labels(hass, "de")

    # Returned key is stripped of prefix and overrides the English default.
    assert labels["headers.your_cover"] == "Ihre Beschattung"
    # A key NOT returned by the bundle falls back to the English default.
    assert labels["rules.force"] == _SUMMARY_LABELS_EN["rules.force"]
    # Helper requested the config_summary category for this integration.
    _, kwargs = mock_get.call_args
    assert kwargs["category"] == "config_summary"
    assert kwargs["integrations"] == [DOMAIN]


# ---------------------------------------------------------------------------
# Step 5: per-user language is used, never hass.config.language
# ---------------------------------------------------------------------------


async def test_load_summary_labels_uses_passed_language() -> None:
    """The helper passes through the language argument it is given (the
    per-user flow language), not the system language.
    """
    hass = MagicMock()
    # Make hass.config.language a sentinel that must NOT be consulted.
    hass.config.language = "SYSTEM_LANG_MUST_NOT_BE_USED"

    with _patch_async_get_translations({}) as mock_get:
        await _load_summary_labels(hass, "fr")

    args, kwargs = mock_get.call_args
    passed = list(args) + list(kwargs.values())
    assert "fr" in passed
    assert "SYSTEM_LANG_MUST_NOT_BE_USED" not in passed


# ---------------------------------------------------------------------------
# Step 8: placeholder parity — every config_summary key has identical {field}
# set across en/de/fr. EXPECTED TO FAIL until DE/FR sync (Phase 5b).
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
    """Return the config_summary subtree flattened to dotted keys."""
    out: dict[str, str] = {}

    def _walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(node, str):
            out[prefix] = node

    _walk(data.get("config_summary", {}), "")
    return out


def test_config_summary_placeholder_parity_de_fr() -> None:
    """For every config_summary.* key, de/fr must expose the IDENTICAL set of
    {field} placeholders as en — else HA silently drops the translated key.
    """
    en = _config_summary_flat(_load_json("en.json"))
    assert en, "en.json must have a config_summary namespace"
    for lang in ("de", "fr"):
        target = _config_summary_flat(_load_json(f"{lang}.json"))
        for key, en_value in en.items():
            assert key in target, f"{lang}.json missing config_summary key {key!r}"
            en_fields = _placeholder_fields(en_value)
            tgt_fields = _placeholder_fields(target[key])
            assert (
                en_fields == tgt_fields
            ), f"{lang}.json[{key}] placeholder set {tgt_fields} != en {en_fields}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict:
    with (TRANSLATIONS_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


class _patch_async_get_translations:  # noqa: N801
    """Patch the ``async_get_translations`` symbol used by config_flow with an
    AsyncMock returning ``return_value``.
    """

    def __init__(self, return_value: dict) -> None:
        self._mock = AsyncMock(return_value=return_value)
        self._mp = pytest.MonkeyPatch()

    def __enter__(self) -> AsyncMock:
        import custom_components.adaptive_cover_pro.config_flow as cf

        self._mp.setattr(cf, "async_get_translations", self._mock)
        return self._mock

    def __exit__(self, *exc) -> bool:
        self._mp.undo()
        return False
