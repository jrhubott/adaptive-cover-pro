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

Why the container must sit on something registry-shaped
---------------------------------------------------------
The scan flags ``<receiver>.devices`` / ``<receiver>.entities`` only when the
receiver itself looks like a registry: a name or attribute ending in ``reg`` /
``registry`` (``dev_reg``, ``ent_reg``, ``self._registry``), or a call to
``async_get`` (``er.async_get(hass).entities``).  Both narrowings matter.
Dropping the receiver test entirely would flag a bare ``devices[key] = ...``
local dict — ``config_flow.py`` builds two of those.  Accepting *any*
attribute would flag this integration's own ``coordinator.entities``, a plain
``list[str]`` read in a dozen places (``sensor.py``, ``binary_sensor.py``,
``services/``, ``building_overview.py``'s ``record.entities``): none of them
subscript or ``.get()`` it today, but the first one that does would fail this
test for no reason and push the author into a bogus exemption.

What this guard does NOT catch
--------------------------------
It is a cheap syntactic backstop, not a type checker.  Deliberately outside
its reach, in rough order of likelihood:

- ``device_id in dev_reg.devices`` — ``__contains__`` is a mapping read too.
- ``len(reg.devices)`` and ``dict(reg.devices)`` — any coercion or builtin
  that consumes the mapping without naming one of its methods.
- ``reg.entities.data.values()`` — the receiver attribute is ``data``, so the
  ``devices``/``entities`` test never sees it.
- Aliasing: ``items = reg.entities`` followed by ``items.values()`` — the
  scan has no dataflow, only shapes.
- A registry reached through a subscript or an unconventionally named local
  (``hass.data[SOMETHING].devices.values()``, ``r.devices.values()``).

Closing these needs type inference and is not worth it: the realistic
regression is someone copy-pasting the old ``dev_reg.devices.values()`` line,
which the scan does catch.  HA's own runtime deprecation warning stays the
backstop for the rest.

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

# What a registry is called when it is a local, an argument or an attribute:
# ``dev_reg``, ``ent_reg``, ``device_reg``, ``registry``, ``self._registry``.
_REGISTRY_NAME_SUFFIXES = ("reg", "registry")

# ...and how one is produced inline, where there is no name to match on:
# ``dr.async_get(hass).devices`` / ``er.async_get(hass).entities``.
_REGISTRY_GETTER = "async_get"


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


def _is_registry_receiver(node: ast.AST) -> bool:
    """Report whether node evaluates to something shaped like a registry.

    Name/attribute suffix, or an inline ``async_get(hass)`` call.  A positive
    test rather than a denylist of known-innocent receivers: it cannot go
    stale as this integration grows new ``*.entities`` attributes of its own,
    and every realistic way a registry is written here matches it.
    """
    if isinstance(node, ast.Name):
        return node.id.lower().endswith(_REGISTRY_NAME_SUFFIXES)
    if isinstance(node, ast.Attribute):
        return node.attr.lower().endswith(_REGISTRY_NAME_SUFFIXES)
    if isinstance(node, ast.Call):
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == _REGISTRY_GETTER
    return False


def _is_registry_items(node: ast.AST) -> bool:
    """Report whether node is ``<registry>.devices`` / ``<registry>.entities``.

    Requiring an attribute access on a registry-shaped receiver — rather than
    accepting any ``devices``/``entities`` — is what keeps both a plain local
    dict and this integration's own ``coordinator.entities`` list out of the
    scan.  See the module docstring.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _REGISTRY_ITEMS_ATTRS
        and _is_registry_receiver(node.value)
    )


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
# The tests
# ---------------------------------------------------------------------------


def _matches(source: str) -> bool:
    """Whether the matcher flags ``source``, parsed as a single expression."""
    return _is_mapping_access(ast.parse(source, mode="eval").body)


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "dev_reg.devices.values()",
        "ent_reg.entities.values()",
        "device_reg.devices.items()",
        "registry.entities.keys()",
        "self._registry.devices.get(device_id)",
        "DEVICE_REG.devices[device_id]",
        "er.async_get(hass).entities.values()",
        "dr.async_get(self.hass).devices[device_id]",
    ],
)
def test_matcher_flags_every_way_a_registry_scan_is_written(source):
    """Every shape a real registry mapping scan takes here must be flagged.

    The receiver narrowing this matcher does is only as good as the names it
    recognises — pin them, so a future tightening cannot quietly turn the
    whole guard into a no-op that passes forever.
    """
    assert _matches(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "self.coordinator.entities[0]",
        "coord.entities[0]",
        "record.entities[0]",
        "self.entities[0]",
        "devices[device_id]",
    ],
)
def test_matcher_ignores_containers_that_are_not_registries(source):
    """ACP's own ``entities`` lists and local dicts must never be flagged.

    ``coordinator.entities`` is a ``list[str]`` read across a dozen modules; a
    false positive here would fail this guard for no reason and pressure the
    next author into a bogus exemption.
    """
    assert not _matches(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "hass.data[DOMAIN].devices.values()",
        "r.devices.values()",
        "items.values()",
        "device_id in dev_reg.devices",
        "len(dev_reg.devices)",
        "dict(dev_reg.devices)",
        "ent_reg.entities.data.values()",
    ],
)
def test_matcher_misses_these_and_the_docstring_says_so(source):
    """Pin the accepted blind spots so the module docstring stays honest.

    Each of these is a real registry mapping read the scan cannot see — an
    unrecognised receiver, an alias, ``__contains__``, a builtin, or the
    underlying ``.data`` dict.  Listed in the docstring's "What this guard
    does NOT catch"; if you ever close one, delete its line from both.
    """
    assert not _matches(source)


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
