"""Static backstop: no integration code may treat a registry's items as a mapping.

Home Assistant's device and entity registries expose their contents through
``registry.devices`` / ``registry.entities``, which are ``BaseRegistryItems``
(a ``UserDict`` subclass, ``homeassistant/helpers/registry.py``).  Treating
those as plain mappings — ``.values()``, ``.items()``, ``.keys()``, ``.get()``
or ``registry.devices[device_id]`` — is deprecated by HA and **stops working in
Home Assistant 2027.9.0** (issue #1339).  HA's ``helpers/frame.py`` reports the
usage today and removes the mapping surface at that version.

This test parses every ``.py`` file under ``custom_components/adaptive_cover_pro``
with the ``ast`` module, enumerates each such mapping access, and compares the
discovered ``<relative/path.py>::<enclosing_function>`` names against a
hard-coded exemption list.  That list is empty: issue #1339 converted the last
four sites, and nothing in the integration may reintroduce the pattern.

What to use instead
-------------------
The registries already maintain purpose-built indexes, and HA exposes them
through module-level ``@callback`` helpers that are *not* deprecated:

- ``dr.async_entries_for_area(registry, area_id)``
- ``dr.async_entries_for_config_entry(registry, config_entry_id)``
- ``er.async_entries_for_area(registry, area_id)``
- ``er.async_entries_for_device(registry, device_id, include_disabled_entities=...)``
- ``er.async_entries_for_config_entry(registry, config_entry_id)``
- ``registry.async_get(device_id)`` / ``registry.async_get(entity_id)`` for a
  single known id — this is the sanctioned replacement for
  ``registry.devices.get(...)`` and ``registry.devices[...]``.

⚠️  ``dr.async_entries_for_area`` / ``er.async_entries_for_area`` apply **no**
``disabled_by`` filter, but ``er.async_entries_for_device`` defaults to
``include_disabled_entities=False`` and silently drops disabled entities.  The
two accessors are asymmetric — pass the kwarg explicitly when a full-mapping
scan is what you are replacing.

Why bare iteration is not flagged (and still not used here)
------------------------------------------------------------
``for device in registry.devices:`` is sanctioned by HA and is deliberately not
flagged by this scan.  This repo still avoids it: ``UserDict.__iter__`` yields
*keys*, so every hit needs a follow-up ``registry.async_get(key)`` — an O(n)
scan plus O(n) redundant lookups, strictly worse than the O(1)-per-hit indexed
accessors above.

Why the container must itself be an attribute
-----------------------------------------------
The scan only flags a mapping access whose container is an ``ast.Attribute``
named ``devices``/``entities`` (``some_reg.devices.values()``), never a bare
name (``devices[key] = ...``).  A bare name is a local dict — ``config_flow.py``
builds two of those and they are correctly excluded.

How to respond when this test fails
-------------------------------------
1. Find the reported ``path.py::function``.  It contains a new
   ``registry.devices``/``registry.entities`` mapping access.
2. Replace it with the matching indexed accessor from the list above.  If you
   need every entity for a device, remember the ``include_disabled_entities``
   asymmetry.
3. Only if no accessor fits, add the ``path.py::function`` string to
   ``MAPPING_SCAN_EXEMPTIONS`` below **with a comment saying why** — and know
   that the exemption expires when HA 2027.9.0 removes the mapping surface.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------

# Every ``<relative/path.py>::<enclosing_function>`` in the integration that is
# allowed to access ``registry.devices`` / ``registry.entities`` as a mapping.
# Empty since issue #1339 converted the last four sites.  The constant stays so
# a future author who genuinely needs an exception has a documented place to
# justify it — one entry, one comment saying why — instead of deleting the
# guard.  Any exemption expires when HA 2027.9.0 removes the mapping surface.
MAPPING_SCAN_EXEMPTIONS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

_INTEGRATION_ROOT = (
    pathlib.Path(__file__).parent.parent / "custom_components" / "adaptive_cover_pro"
)

# ``UserDict`` methods that HA's deprecation covers.  Bare iteration is absent
# deliberately — see the module docstring.
_MAPPING_METHODS = frozenset({"values", "items", "keys", "get"})

# The registry container attributes themselves.
_REGISTRY_ITEMS_ATTRS = frozenset({"devices", "entities"})


def _enclosing_function(node: ast.AST, tree: ast.Module) -> str | None:
    """Return the name of the innermost function/async-function containing node.

    Walks the full tree and builds a parent map (there is no parent pointer in
    the standard ast node).  The innermost enclosing FunctionDef or
    AsyncFunctionDef wins (handles nested closures).
    """
    parent: dict[int, ast.AST] = {}
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            parent[id(child)] = n

    current = parent.get(id(node))
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current.name
        current = parent.get(id(current))
    return None


def _is_registry_items(node: ast.AST) -> bool:
    """Report whether node is ``<registry>.devices`` / ``<registry>.entities``.

    Requiring an attribute access — rather than accepting a bare name — is what
    keeps a plain local dict called ``devices`` or ``entities`` out of the scan.
    """
    return isinstance(node, ast.Attribute) and node.attr in _REGISTRY_ITEMS_ATTRS


def _is_mapping_access(node: ast.AST) -> bool:
    """Report whether node reads a registry-items container as a mapping."""
    if isinstance(node, ast.Call):
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr in _MAPPING_METHODS
            and _is_registry_items(func.value)
        )
    if isinstance(node, ast.Subscript):
        return _is_registry_items(node.value)
    return False


def _find_mapping_scan_sites() -> list[str]:
    """Return ``<relative/path.py>::<function>`` for every registry mapping access."""
    hits: list[str] = []

    for path in sorted(_INTEGRATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not _is_mapping_access(node):
                continue
            relative = path.relative_to(_INTEGRATION_ROOT).as_posix()
            hits.append(f"{relative}::{_enclosing_function(node, tree)}")

    return hits


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_registry_mapping_scans_outside_the_exemption_list():
    """No integration code may read ``registry.devices``/``.entities`` as a mapping.

    If this test fails, a new ``.values()``/``.items()``/``.keys()``/``.get()``
    call or a ``[...]`` subscript was added against a registry-items container.
    Follow the instructions in the module docstring to resolve the failure.
    """
    discovered = set(_find_mapping_scan_sites())

    unknown = discovered - MAPPING_SCAN_EXEMPTIONS
    stale = MAPPING_SCAN_EXEMPTIONS - discovered

    messages = []
    if unknown:
        messages.append(
            "New registry mapping scans found in the integration "
            f"(deprecated, removed in HA 2027.9.0): {sorted(unknown)}.\n"
            "Replace each with the matching indexed accessor — see the module "
            "docstring of this file for the list and the disabled-entity caveat."
        )
    if stale:
        messages.append(
            f"Exempted sites no longer scan a registry as a mapping: {sorted(stale)}.\n"
            "Remove the stale entries from MAPPING_SCAN_EXEMPTIONS in this file."
        )

    assert not messages, "\n\n".join(messages)
