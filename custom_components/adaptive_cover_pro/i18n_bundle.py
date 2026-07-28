"""Shared i18n-bundle helpers (pure, stdlib only).

Both the config/options ``summary_i18n/`` bundle (issue #258) and the runtime
``reason_i18n/`` bundle (issue #882) ship translated templates as nested JSON
trees and resolve them by overlaying a language bundle onto the code-owned
English defaults. This module holds the flatten / load-overlay / merge logic
so it lives in exactly one place (CODING_GUIDELINES § No Duplication) — both
``config_flow._load_summary_labels_sync`` and ``reason_i18n.load_reason_labels``
delegate here.

No Home Assistant imports: the helpers take a directory + language and return
plain dicts, safe to import from any layer.
"""

from __future__ import annotations

import json
from pathlib import Path


def flatten_bundle(node: object, prefix: str = "") -> dict[str, str]:
    """Flatten a nested label tree to dotted keys (``{"a": {"b": "x"}}`` → ``{"a.b": "x"}``)."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten_bundle(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, str):
        out[prefix] = node
    return out


def load_bundle_overlay(directory: Path | str, language: str) -> dict[str, str]:
    """Return the flattened ``<directory>/<language>.json`` overlay.

    ``en`` (the code-owned source of truth) and any missing or malformed file
    yield an empty overlay — the English defaults then apply unchanged.
    """
    if not language or language == "en":
        return {}
    path = Path(directory) / f"{language}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return flatten_bundle(data)


def merge_labels(defaults: dict[str, str], overlay: dict[str, str]) -> dict[str, str]:
    """Overlay the translated bundle onto the English defaults, per key."""
    return {**defaults, **overlay}
