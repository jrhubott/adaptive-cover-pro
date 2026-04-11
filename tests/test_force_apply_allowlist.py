"""Static backstop: allowlist of coordinator sites that call _build_position_context(force=True).

This test parses coordinator.py with the ``ast`` module and enumerates every
call to ``_build_position_context`` that passes a *literal* ``force=True``
keyword argument.  It compares the discovered enclosing-function names against
a hard-coded allowlist.

Why literal True specifically?
-------------------------------
Calls like ``_build_position_context(cover, options, force=use_force)`` or
``force=is_safety`` are variable expressions — they are controlled by upstream
logic that *could* be True or False depending on runtime conditions.  Those
paths already have their own gating.

Calls with a literal ``force=True`` are unconditional; the context will always
bypass all CoverCommandService gate checks (auto_control, delta, time_delta,
etc.).  Every such site must either:

a) Pre-gate on ``self.automatic_control`` (non-safety paths), or
b) Be a declared safety bypass (ForceOverrideHandler, WeatherOverrideHandler).

In both cases, a human reviewer must acknowledge the site by adding it to the
allowlist here AND adding a row to ``test_auto_control_gate_matrix.py``.

How to respond when this test fails
-------------------------------------
1. Identify the new ``force=True`` call site in ``coordinator.py``.
2. Decide: is this a safety bypass (force/weather override) or a non-safety path?
   - **Non-safety:** Add an ``automatic_control`` early-return guard *before* the
     ``_build_position_context`` call (see ``_async_send_after_override_clear`` or
     ``_on_window_closed`` for the pattern).
   - **Safety bypass:** Document why ``automatic_control=OFF`` must not block this
     path.
3. Add the enclosing function name to ``ALLOWED_LITERAL_FORCE_TRUE_SITES`` below.
4. Add a row to ``CONTROL_GATE_MATRIX`` in ``test_auto_control_gate_matrix.py``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

# Every function in coordinator.py that contains a literal force=True call to
# _build_position_context.  Update this list when adding a new such call site,
# following the instructions in the module docstring.
ALLOWED_LITERAL_FORCE_TRUE_SITES: frozenset[str] = frozenset(
    {
        # Manual-override expiry path. Gated by automatic_control before the call.
        # See coordinator.py::_async_send_after_override_clear, line ~1140.
        "_async_send_after_override_clear",
        # End-of-time-window return-to-default path. Gated by automatic_control
        # before the call (v2.15.3 fix). See coordinator.py::_on_window_closed.
        "_on_window_closed",
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

_COORDINATOR_PATH = (
    pathlib.Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "coordinator.py"
)


def _enclosing_function(node: ast.AST, tree: ast.Module) -> str | None:
    """Return the name of the innermost function/async-function containing node.

    Walks the full tree and builds a parent map (there is no parent pointer in
    the standard ast node).  The innermost enclosing FunctionDef or
    AsyncFunctionDef wins (handles nested closures like _on_window_closed).
    """
    parent: dict[ast.AST, ast.AST] = {}
    for n in ast.walk(tree):
        for child in ast.iter_child_nodes(n):
            parent[id(child)] = n

    current = parent.get(id(node))
    innermost = None
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            innermost = current.name
            break
        current = parent.get(id(current))
    return innermost


def _find_literal_force_true_sites(source: str) -> list[tuple[str | None, int]]:
    """Return (enclosing_function, lineno) for each _build_position_context(force=True) call."""
    tree = ast.parse(source)
    hits: list[tuple[str | None, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Match self._build_position_context(...)
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "_build_position_context"
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            continue

        # Check for a literal force=True keyword argument
        has_literal_force_true = any(
            kw.arg == "force"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if not has_literal_force_true:
            continue

        hits.append((_enclosing_function(node, tree), node.lineno))

    return hits


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_literal_force_true_sites_are_allowlisted():
    """Every coordinator site with a literal force=True must be in the allowlist.

    If this test fails, a new ``self._build_position_context(..., force=True)``
    call was added to coordinator.py without registering it here.  Follow the
    instructions in the module docstring to resolve the failure.
    """
    source = _COORDINATOR_PATH.read_text()
    sites = _find_literal_force_true_sites(source)

    discovered_functions = {fn for fn, _ in sites}
    unknown = discovered_functions - ALLOWED_LITERAL_FORCE_TRUE_SITES
    missing = ALLOWED_LITERAL_FORCE_TRUE_SITES - discovered_functions

    messages = []
    if unknown:
        messages.append(
            f"New force=True call sites discovered in coordinator.py (not in allowlist): "
            f"{sorted(unknown)}.\n"
            "Add these function names to ALLOWED_LITERAL_FORCE_TRUE_SITES in this file\n"
            "AND add a row to CONTROL_GATE_MATRIX in test_auto_control_gate_matrix.py."
        )
    if missing:
        messages.append(
            f"Allowlisted sites no longer found in coordinator.py: {sorted(missing)}.\n"
            "Remove the stale entries from ALLOWED_LITERAL_FORCE_TRUE_SITES."
        )

    assert not messages, "\n\n".join(messages)
