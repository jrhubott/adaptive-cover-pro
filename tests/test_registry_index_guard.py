"""Static backstop: nothing here may treat a registry's items as a mapping.

Home Assistant's device and entity registries expose their contents through
``registry.devices`` / ``registry.entities``, which are ``BaseRegistryItems``
(a ``UserDict`` subclass, ``homeassistant/helpers/registry.py``).  Treating
those as plain mappings — ``.values()``, ``.items()``, ``.keys()``, ``.get()``
or ``registry.devices[device_id]`` — is deprecated by HA and **stops working in
Home Assistant 2027.9.0** (issue #1339).  HA's ``helpers/frame.py`` reports the
usage today and removes the mapping surface at that version.

This test parses every ``.py`` file under ``custom_components/adaptive_cover_pro``
**and under ``tests/``** with the ``ast`` module, enumerates each such access,
and compares the discovered ``<repo/relative/path.py>::<enclosing_function>``
names against a hard-coded exemption list.  That list is empty: issue #1339
converted the last four production sites, issue #1373 converted the test-side
ones, and nothing in the repo may reintroduce the pattern.

Why ``tests/`` is in scope (issue #1373)
------------------------------------------
Production was cleared by #1339, so the only place the deprecated surface could
still hide was the test suite — and it did, in a dozen files.  The consequences
there are worse, not better, than in production:

- HA's deprecation reporter grades by *frame*.  A custom integration gets
  ``ReportBehavior.LOG``; a test frame has no custom integration on the stack,
  so ``helpers/frame.py`` classifies it as core and raises ``RuntimeError``
  instead.  The suite breaks **before** production does, not after.
- A test that reaches for a registry internal pins an HA implementation detail
  as though it were a contract.  When HA reimplements it, the test fails for a
  reason that has nothing to do with this integration — the exact churn #1373
  was filed to stop.

Two matchers, two failure modes
---------------------------------
1. **Mapping access** — ``<registry>.devices.values()`` and friends.  The
   deprecated read, in production or in a test.
2. **Internal access** — any *other* attribute on a registry-items container:
   ``registry.devices.get_devices_for_area_id``, ``.get_entry``, ``.data``.
   These are not deprecated, they are simply not public.  Mocking one is how a
   test comes to depend on the current body of a public helper rather than on
   the helper itself: ``dr.async_entries_for_area`` is a one-line wrapper over
   ``devices.get_devices_for_area_id`` *today*, so patching the inner call
   works right up until HA rewrites the wrapper — and then silently stops
   intercepting, leaving the mock returning an empty list rather than raising.

One method banned outright (issue #1369)
-----------------------------------------
Separately from the mapping rules above, ``<registry>.async_get_device(...)``
may not be called from anywhere in the repo.  HA 2026.8 reports it with
``core_behavior=ERROR`` **and** ``core_integration_behavior=ERROR`` and removes
it in 2027.8.0 — production gets a log line, a test frame gets a
``RuntimeError`` — and its v3 replacement ``async_get_device_by_identifier``
does not exist on the HA 2026.3 floor ``hacs.json`` declares.  There is no
spelling that works on both, so the lookup itself is the thing to avoid:
``state/device_link.scan_own_devices`` partitions
``dr.async_entries_for_config_entry`` and matches identifiers in Python, which
is neither deprecated nor version-specific.

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
- ``registry.async_get_entity_id(domain, platform, unique_id)`` for the
  unique-id index.

In a test, prefer the real thing over a mock of any of the above: the
``device_registry``, ``entity_registry`` and ``area_registry`` fixtures
``pytest_homeassistant_custom_component`` ships are real registries, so they
track whatever HA does to the internals.  When a mock really is the right tool,
spec it (``MagicMock(spec=dr.DeviceRegistry)``) and patch the *public* helper.

⚠️  ``dr.async_entries_for_area`` / ``er.async_entries_for_area`` apply **no**
``disabled_by`` filter, but ``er.async_entries_for_device`` defaults to
``include_disabled_entities=False`` and silently drops disabled entities.  The
two accessors are asymmetric — pass the kwarg explicitly when a full-mapping
scan is what you are replacing.

Why bare iteration is not flagged (and still not used in production)
----------------------------------------------------------------------
``for device in registry.devices:`` is sanctioned by HA and is deliberately not
flagged by either matcher.  Production still avoids it: ``UserDict.__iter__``
yields *keys*, so every hit needs a follow-up ``registry.async_get(key)`` — an
O(n) scan plus O(n) redundant lookups, strictly worse than the O(1)-per-hit
indexed accessors above.

Why the container must sit on something registry-shaped
---------------------------------------------------------
The scan flags ``<receiver>.devices`` / ``<receiver>.entities`` only when the
receiver itself looks like a registry: a name or attribute ending in ``reg`` /
``registry`` (``dev_reg``, ``ent_reg``, ``self._registry``) or in the mock
spellings ``reg_mock`` / ``registry_mock`` (``dev_reg_mock``), or a call to
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
- Aliasing, against **either** matcher: ``items = reg.entities`` followed by
  ``items.values()`` or ``items.get_entry(...)`` — the scan has no dataflow,
  only shapes, so once the container is bound to a plain local it is invisible.
- A registry reached through a subscript or an unconventionally named local
  (``hass.data[SOMETHING].devices.values()``, ``r.devices.values()``).
- **A patch target written as a string** —
  ``patch("homeassistant.helpers.device_registry.DeviceRegistryItems.get_devices_for_area_id")``
  reaches the same internal, but it is an ``ast.Constant``: there is no
  attribute access to match on.  Matching strings was considered and rejected
  as unfixably leaky — this module's own docstring quotes
  ``dev_reg.devices.values()`` as prose, and so does every docstring
  explaining the rule, so a string scan would flag the guard that defines it.
  Patching an HA-internal *path* is rarer than mocking an attribute and is a
  deliberate act rather than a copy-paste, which is why the honest gap is
  preferable to a matcher that cries wolf.

(``reg.entities.data.values()`` used to be listed here.  The internal-access
matcher closes it: ``.data`` is flagged as an internal attribute before the
``.values()`` ever matters.)

Closing the rest needs type inference and is not worth it: the realistic
regression is someone copy-pasting the old ``dev_reg.devices.values()`` line,
which the scan does catch.  HA's own runtime deprecation warning stays the
backstop for the remainder.

How to respond when this test fails
-------------------------------------
1. Find the reported ``path.py::function``.  It contains a new
   ``registry.devices``/``registry.entities`` access.
2. **Mapping access** — replace it with the matching indexed accessor from the
   list above.  If you need every entity for a device, remember the
   ``include_disabled_entities`` asymmetry.
3. **Internal access** — in production, call the public helper that wraps it.
   In a test, patch the public helper instead of its innards, or drop the mock
   for the real registry fixture.
4. Only if no accessor fits, add the ``path.py::function`` string to
   ``MAPPING_SCAN_EXEMPTIONS`` below **with a comment saying why** — and know
   that the exemption expires when HA 2027.9.0 removes the mapping surface.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Callable, Iterator

import pytest

# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------

# Every ``<repo/relative/path.py>::<enclosing_function>`` in the repo that is
# allowed to access ``registry.devices`` / ``registry.entities`` as a mapping,
# or to reach into one of its internals.  Empty since issue #1339 converted the
# last four production sites and #1373 converted the test-side ones.  The
# constant stays so a future author who genuinely needs an exception has a
# documented place to justify it — one entry, one comment saying why — instead
# of deleting the guard.  Any exemption expires when HA 2027.9.0 removes the
# mapping surface.
MAPPING_SCAN_EXEMPTIONS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent

_INTEGRATION_ROOT = _REPO_ROOT / "custom_components" / "adaptive_cover_pro"

# Production *and* the test suite — see "Why ``tests/`` is in scope" above.
# Hit keys are relative to the repo root, so the two are unambiguous.
_SCAN_ROOTS = (_INTEGRATION_ROOT, _REPO_ROOT / "tests")

# ``UserDict`` methods that HA's deprecation covers.  Bare iteration is absent
# deliberately — see the module docstring.
_MAPPING_METHODS = frozenset({"values", "items", "keys", "get"})

# The registry container attributes themselves.
_REGISTRY_ITEMS_ATTRS = frozenset({"devices", "entities"})

# What a registry is called when it is a local, an argument or an attribute:
# ``dev_reg``, ``ent_reg``, ``device_reg``, ``registry``, ``self._registry`` —
# plus the two test spellings, ``dev_reg_mock`` / ``device_registry_mock``.
_REGISTRY_NAME_SUFFIXES = ("reg", "registry", "reg_mock", "registry_mock")

# ...and how one is produced inline, where there is no name to match on:
# ``dr.async_get(hass).devices`` / ``er.async_get(hass).entities``.
_REGISTRY_GETTER = "async_get"

# The device-registry area hop, as ``(module alias, function)``.  ``dr`` is
# HA's universal alias for ``homeassistant.helpers.device_registry`` and the
# only spelling this repo uses.  The entity-registry twin
# (``er.async_entries_for_area``) is a different hop with a different owner —
# ``group_coordinator`` resolves area → *entities* — and is out of scope here.
_AREA_DEVICE_HOP = ("dr", "async_entries_for_area")

# The device-registry identifier lookup, banned outright — see
# ``test_no_deprecated_device_identifier_lookups``.  HA 2026.8 reports it with
# ERROR behaviour for core *and* core-integration frames (i.e. ``RuntimeError``
# from a test) and removes it in 2027.8.0, while its v3 replacement
# ``async_get_device_by_identifier`` does not exist on this integration's HA
# 2026.3 floor — so there is no spelling of it that works on both (issue #1369).
_DEPRECATED_DEVICE_LOOKUP = "async_get_device"

# The one function allowed to perform that hop — see
# ``test_area_hop_lives_only_in_area_resolver``.
_AREA_HOP_HOME = (
    "custom_components/adaptive_cover_pro/state/area_resolver.py::area_device_ids"
)


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


def _is_internal_access(node: ast.AST) -> bool:
    """Report whether node reaches into — or stubs out — a registry-items container.

    Two shapes, both of which pin an HA implementation detail:

    - **Reading an internal.** An attribute on ``<registry>.devices`` /
      ``.entities`` that is not one of the deprecated mapping methods:
      ``get_devices_for_area_id``, ``get_entry``, ``data``.
      ``_is_mapping_access`` owns the deprecated names, so the two matchers
      partition the surface rather than overlapping on it.
    - **Replacing the container.** An assignment *to* it —
      ``dev_reg_mock.devices = MagicMock(spec=[...])``.  A test that stubs the
      container has pinned the registry's internal structure without ever
      naming one of its attributes, so the read half above never sees it.
      This is the shape #1345 left behind in ``test_global_services`` and
      #1373 removed.

    Bare iteration (``for device in reg.devices``) names no attribute and
    assigns nothing, so it stays sanctioned.
    """
    if isinstance(node, ast.Assign):
        return any(_is_registry_items(target) for target in node.targets)
    return (
        isinstance(node, ast.Attribute)
        and node.attr not in _MAPPING_METHODS
        and _is_registry_items(node.value)
    )


def _is_deprecated_device_lookup(node: ast.AST) -> bool:
    """Report whether node calls ``<registry>.async_get_device(...)``.

    Matched on the method name alone, with no receiver narrowing: unlike
    ``devices``/``entities``, ``async_get_device`` is not a word anything in
    this repo owns, so there is nothing to collide with and a narrowing would
    only add a way to miss the call.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _DEPRECATED_DEVICE_LOOKUP
    )


def _is_area_device_hop(node: ast.AST) -> bool:
    """Report whether node is a ``dr.async_entries_for_area(...)`` call."""
    module, function = _AREA_DEVICE_HOP
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == function
        and isinstance(func.value, ast.Name)
        and func.value.id == module
    )


def _walk_sources(
    roots: tuple[pathlib.Path, ...],
) -> Iterator[tuple[str, ast.Module]]:
    """Yield ``(repo-relative path, parsed tree)`` for every ``.py`` under roots.

    The single file-walking implementation in this module: every scan below
    goes through it, so widening or narrowing the sweep is a one-line change
    in one place.
    """
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(_REPO_ROOT).as_posix(), ast.parse(path.read_text())


def _find_sites(
    roots: tuple[pathlib.Path, ...],
    predicate: Callable[[ast.AST], bool],
) -> list[str]:
    """Return ``<repo/relative/path.py>::<function>`` for every matching node."""
    return [
        f"{relative}::{_enclosing_function(node, tree)}"
        for relative, tree in _walk_sources(roots)
        for node in ast.walk(tree)
        if predicate(node)
    ]


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def _matches(source: str) -> bool:
    """Whether the mapping matcher flags ``source``, parsed as an expression."""
    return _is_mapping_access(ast.parse(source, mode="eval").body)


def _matches_internal(source: str) -> bool:
    """Whether the internal-access matcher flags any node in ``source``.

    Parsed as a module rather than an expression: the flagged shapes nest
    inside calls and assignments, and the sanctioned ``for d in reg.devices:``
    shape is a statement, so this one has to walk.
    """
    return any(_is_internal_access(node) for node in ast.walk(ast.parse(source)))


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
        "dev_reg_mock.devices.values()",
        "ent_reg_mock.entities.get(entity_id)",
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
    ("source", "flagged"),
    [
        ("registry.devices.get_devices_for_area_id(area_id)", True),
        ("dev_reg_mock.devices.get_devices_for_area_id.side_effect = fn", True),
        ("ent_reg.entities.data", True),
        # Stubbing the container wholesale names no internal attribute, so the
        # read half of the matcher cannot see it.
        ('dev_reg_mock.devices = MagicMock(spec=["get_devices_for_area_id"])', True),
        ("ent_reg.entities = {}", True),
        # ACP's own ``entities`` list — the receiver is not registry-shaped.
        ("coordinator.entities.append(entity_id)", False),
        ("coordinator.entities = []", False),
        # A bare local, not a registry-items container.
        ("devices.get_devices_for_area_id(area_id)", False),
        # Bare iteration is sanctioned by HA and names no attribute.
        ("for device in dev_reg.devices: pass", False),
    ],
)
def test_matcher_flags_internal_accessors_on_registry_items(source, flagged):
    """Reaching past the public helper into the registry's own index is a hit.

    This is the half of the guard that catches a *test* pinning an HA
    implementation detail — ``registry.devices.get_devices_for_area_id`` is
    what ``dr.async_entries_for_area`` happens to call today, and mocking it
    silently stops intercepting the day HA rewrites the wrapper (issue #1373).
    """
    assert _matches_internal(source) is flagged


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
    ],
)
def test_matcher_misses_these_and_the_docstring_says_so(source):
    """Pin the accepted blind spots so the module docstring stays honest.

    Each of these is a real registry mapping read the scan cannot see — an
    unrecognised receiver, an alias, ``__contains__`` or a builtin.  Listed in
    the docstring's "What this guard does NOT catch"; if you ever close one,
    delete its line from both.
    """
    assert not _matches(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("dr.async_entries_for_area(dev_reg, area_id)", True),
        # The entity-registry twin is a different hop with a different owner.
        ("er.async_entries_for_area(ent_reg, area_id)", False),
    ],
)
def test_area_hop_matcher_is_the_device_registry_one(source, flagged):
    """Only the area → *devices* hop is unified; area → entities is separate."""
    assert _is_area_device_hop(ast.parse(source, mode="eval").body) is flagged


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("device_reg.async_get_device(identifiers={(DOMAIN, entry_id)})", True),
        ("dr.async_get(hass).async_get_device(identifiers=ids)", True),
        ("registry.async_get_device(connections=connections)", True),
        # The per-id accessor is a different method and stays sanctioned.
        ("dev_reg.async_get(device_id)", False),
        ("dr.async_entries_for_config_entry(dev_reg, entry_id)", False),
        ("er.async_entries_for_device(ent_reg, device_id)", False),
    ],
)
def test_device_identifier_lookup_matcher(source, flagged):
    """Only ``async_get_device`` is flagged — not the id accessor beside it.

    The two read almost identically at a glance, which is exactly why the
    matcher's edges are pinned: a narrowing that accidentally covered
    ``async_get`` would fail the whole suite, and one that covered neither would
    pass forever while the real call crept back.
    """
    node = ast.parse(source, mode="eval").body
    assert _is_deprecated_device_lookup(node) is flagged


@pytest.mark.unit
def test_no_deprecated_device_identifier_lookups():
    """``async_get_device(identifiers=...)`` may not be called from anywhere.

    HA 2026.8 reports it with ``core_behavior=ERROR`` *and*
    ``core_integration_behavior=ERROR`` and removes it in 2027.8.0: production
    gets a log line, but a **test** frame — which has no custom integration on
    the stack — gets a ``RuntimeError``, so the suite breaks before the
    integration does.  Its v3 replacement, ``async_get_device_by_identifier``,
    does not exist on the HA 2026.3 floor ``hacs.json`` declares, so there is no
    version-neutral spelling of the identifier lookup at all.

    Use ``scan_own_devices`` in ``state/device_link.py`` instead: it partitions
    ``dr.async_entries_for_config_entry`` and matches identifiers in Python,
    which is neither deprecated nor version-specific.  The repo had exactly one
    call site (``__init__.py``) and issue #1369 removed it, so this guard costs
    nothing to keep at zero.
    """
    sites = sorted(set(_find_sites(_SCAN_ROOTS, _is_deprecated_device_lookup)))

    assert sites == [], (
        f"device_registry.async_get_device is called from {sites}.\n"
        "It is deprecated from HA 2026.8 (RuntimeError from a test frame) and "
        "removed in 2027.8.0, and its replacement does not exist on this "
        "integration's HA floor. Resolve the device through "
        "state/device_link.scan_own_devices instead."
    )


@pytest.mark.unit
def test_no_registry_mapping_scans_outside_the_exemption_list():
    """No code here may read ``registry.devices``/``.entities`` as a mapping.

    If this test fails, a new ``.values()``/``.items()``/``.keys()``/``.get()``
    call, a ``[...]`` subscript, or a reach into a registry internal was added
    against a registry-items container.  Follow the instructions in the module
    docstring to resolve the failure.
    """
    discovered = set(_find_sites(_SCAN_ROOTS, _is_mapping_access))
    internal = sorted(set(_find_sites(_SCAN_ROOTS, _is_internal_access)))

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
    if internal:
        messages.append(
            f"Registry internals read directly: {internal}.\n"
            "These are HA implementation details, not API. In production call "
            "the public helper that wraps them (dr.async_entries_for_area / "
            "dr.async_entries_for_config_entry / er.async_entries_for_*). In a "
            "test, patch that public helper instead of its innards, or use the "
            "real device_registry / entity_registry / area_registry fixtures."
        )

    assert not messages, "\n\n".join(messages)


@pytest.mark.unit
def test_area_hop_lives_only_in_area_resolver():
    """``area_device_ids`` is the one place production expands an area to devices.

    ``services/__init__.py`` and ``services/group_service.py`` each used to
    carry their own copy of the expansion — nine identical lines, comment
    included — which is how the same HA change breaks three call sites at once
    (issue #1373).  ``state/area_resolver`` owns the device<->area registry hop
    in both directions; every other caller delegates to it.
    """
    sites = sorted(set(_find_sites((_INTEGRATION_ROOT,), _is_area_device_hop)))

    assert sites == [_AREA_HOP_HOME], (
        f"dr.async_entries_for_area is called from {sites}.\n"
        "Only state/area_resolver.py::area_device_ids may perform the area → "
        "devices hop; call area_device_ids(hass, area_id) instead."
    )
