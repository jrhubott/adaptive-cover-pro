#!/usr/bin/env python3
"""Fail if the integration references a deprecated Home Assistant helper.

Home Assistant marks helpers scheduled for removal with ``@deprecated_function``
/ ``@deprecated_class``. Calling one emits a WARNING *log* record — not a Python
``warnings`` warning, so ``pytest -W error`` never catches it — and, for custom
integrations, asks the user to file a bug. Issue #815 was exactly this:
``state/sun_provider.py`` called the deprecated
``homeassistant.helpers.sun.get_astral_location`` on every update cycle,
spamming a user's log with 461 warnings.

This is a standalone CI check (wired in
``.github/workflows/deprecated-ha-helpers.yml``) rather than a unit test, on
purpose:

* It is independent of the installed HA version. The pinned test HA
  (``pytest-homeassistant-custom-component``) may predate a given deprecation,
  which would let a runtime check pass green while the offending call is present
  — this static scan bites regardless.
* It keeps the deprecation gate out of the unit-test suite, so a flagged
  deprecation shows up as its own CI task instead of a failing unit test.

It parses each source file with the ``ast`` module, so a denylisted name that
appears only in a docstring or comment does not trip it (the #815 fix keeps
``get_astral_location`` in an explanatory docstring).

Run locally::

    python scripts/check_deprecated_ha_helpers.py

Extend the denylist as HA announces removals in the developer blog's deprecation
notices — record the successor API and the removal version so the failure
message tells a maintainer exactly what to switch to.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Deprecated Home Assistant helper symbols the integration must not reference.
#   symbol -> (recommended replacement, breaks_in_ha_version)
DEPRECATED_HA_HELPERS: dict[str, tuple[str, str]] = {
    "get_astral_location": (
        "homeassistant.helpers.sun.get_astral_observer",
        "2027.7",
    ),
}

SOURCE_ROOT = (
    Path(__file__).resolve().parent.parent / "custom_components" / "adaptive_cover_pro"
)


def _referenced_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def find_offenders(root: Path) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = _referenced_names(tree)
        offenders.extend(
            (str(path.relative_to(root)), symbol)
            for symbol in DEPRECATED_HA_HELPERS
            if symbol in used
        )
    return offenders


def main() -> int:
    offenders = find_offenders(SOURCE_ROOT)
    if not offenders:
        print("OK: no deprecated Home Assistant helper references found.")
        return 0
    print("Deprecated Home Assistant helper reference(s) found:")
    for rel, symbol in offenders:
        replacement, breaks_in = DEPRECATED_HA_HELPERS[symbol]
        print(f"  {rel}: {symbol} -> {replacement} (removed in HA {breaks_in})")
    print(
        "\nReplace each call with its documented successor. Edit the denylist in "
        "scripts/check_deprecated_ha_helpers.py to add newly deprecated symbols."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
