"""Shared i18n-bundle parity-lock helpers (issue #970).

``reason_i18n/``, ``summary_i18n/``, and ``troubleshoot_i18n/`` each ship a
nested-JSON label tree per language and hold three near-identical parity locks:

* the flattened ``en.json`` must byte-match the code-owned English defaults,
* every non-English bundle must expose the identical key set as ``en.json``, and
* every key's ``{field}`` placeholders (field name + format spec + conversion)
  must match ``en.json`` — else Home Assistant silently drops the translated key.

Per CODING_GUIDELINES § No Duplication + "cross-file test helpers live in
``tests/_helpers/``", those three copies collapse to the functions below,
parametrized over a bundle directory and the code-owned English defaults.
"""

from __future__ import annotations

import json
import string
from collections.abc import Mapping
from pathlib import Path


def flatten(data: object) -> dict[str, str]:
    """Flatten a nested label tree to dotted keys (``{"a": {"b": "x"}}`` -> ``{"a.b": "x"}``)."""
    out: dict[str, str] = {}

    def _walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{prefix}.{key}" if prefix else key)
        elif isinstance(node, str):
            out[prefix] = node

    _walk(data, "")
    return out


def load_bundle(bundle_dir: Path | str, name: str) -> dict:
    """Load one ``<bundle_dir>/<name>`` JSON file."""
    with (Path(bundle_dir) / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def placeholders(template: str) -> set[tuple[str, str | None, str | None]]:
    """Return the ``(field, format_spec, conversion)`` placeholder tuples.

    Including the format spec (e.g. ``.2f``) catches a translation that drops a
    numeric format, not just a renamed field. Literal ``{{``/``}}`` are stripped
    first so they don't parse as fields.
    """
    stripped = template.replace("{{", "").replace("}}", "")
    return {
        (field, spec, conv)
        for _, field, spec, conv in string.Formatter().parse(stripped)
        if field
    }


def assert_en_matches_defaults(
    bundle_dir: Path | str, code_defaults: Mapping[str, str]
) -> None:
    """Assert flattened ``en.json`` is byte-identical to ``code_defaults``."""
    en = flatten(load_bundle(bundle_dir, "en.json"))
    assert en == dict(code_defaults), (
        "en.json differs from the code-owned English defaults:\n"
        f"  only in en.json: {sorted(set(en) - set(code_defaults))[:10]}\n"
        f"  only in code:    {sorted(set(code_defaults) - set(en))[:10]}"
    )


def assert_key_parity(
    bundle_dir: Path | str, langs: tuple[str, ...] = ("de", "fr")
) -> None:
    """Assert each ``<lang>.json`` exposes the identical key set as ``en.json``."""
    en = flatten(load_bundle(bundle_dir, "en.json"))
    assert en, "en.json must not be empty"
    for lang in langs:
        target = flatten(load_bundle(bundle_dir, f"{lang}.json"))
        assert set(target) == set(en), (
            f"{lang}.json key-set differs from en.json:\n"
            f"  missing: {sorted(set(en) - set(target))[:10]}\n"
            f"  extra:   {sorted(set(target) - set(en))[:10]}"
        )


def assert_placeholder_parity(
    bundle_dir: Path | str, langs: tuple[str, ...] = ("de", "fr")
) -> None:
    """Assert each key's placeholder set matches ``en.json`` across ``langs``."""
    en = flatten(load_bundle(bundle_dir, "en.json"))
    assert en, "en.json must not be empty"
    for lang in langs:
        target = flatten(load_bundle(bundle_dir, f"{lang}.json"))
        for key, en_value in en.items():
            assert key in target, f"{lang}.json missing key {key!r}"
            assert placeholders(en_value) == placeholders(target[key]), (
                f"{lang}.json[{key}] placeholders {placeholders(target[key])} "
                f"!= en {placeholders(en_value)}"
            )
