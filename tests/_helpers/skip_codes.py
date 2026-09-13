"""Canonical ``last_skipped_action`` reason-code sets, derived where possible.

Shared by ``tests/test_skip_reason_guard.py`` (exhaustiveness: every reason
literal that can reach ``CoverCommandService.record_skipped_action`` must be
accounted for here) and ``tests/test_spec_translation_keys.py`` (translation
completeness: every accounted-for reason must have a translated state in
every shipped language).

Three canons, because ``last_skipped_action.reason`` values reach the wire
through three different shapes:

- ``EXPECTED_SKIP_CODES``: reason codes passed to ``self._skip(entity,
  "code", ...)`` inside ``managers/cover_command/__init__.py``.
  ``test_skip_reason_guard.py`` cross-checks this set against the actual
  ``_skip(`` call sites in that file.
- ``EXTRA_RECORD_SKIPPED_ACTION_REASONS``: the small number of reason
  literals that reach ``record_skipped_action()`` OUTSIDE ``_skip()`` via a
  hand-maintained, non-``_HOLD_SKIP_LABEL`` path — today just
  ``"preempted_by_handler"`` (passed directly by
  ``CoverCommandService.record_preempted_skip``) and the ``"hold"`` fallback
  literal in coordinator.py's
  ``_HOLD_SKIP_LABEL.get(result.control_method, "hold")``.
  ``emitted_record_skipped_action_reasons()`` below is the actual source of
  truth this is checked against — see its docstring.
- The idle state (``last_skipped_action`` has no record yet) — ``sensor.py``
  's ``_last_skipped_value`` returns a literal directly, never going through
  ``record_skipped_action`` at all — derived via :func:`idle_last_skipped_value`
  by calling the real function rather than hand-typing its return value.

History: a first version of ``emitted_record_skipped_action_reasons`` was a
regex scan for a quoted-literal second positional argument. It missed a
reason passed as a bare module-level constant name (coordinator.py's
``_MANUAL_OVERRIDE_SKIP_LABEL``, since a ``Name`` node is never a quoted
literal), and would have missed a keyword ``reason=...`` argument, a dotted
callee, or an f-string too. Replaced with the AST-based scan below (issue
#1353 audit round 2).
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Canonical set of skip reason codes passed to `_skip()` in
# managers/cover_command/__init__.py.  Update this (and CODING_GUIDELINES.md's
# `last_skipped_action` Dict Structure section) whenever a code is added or
# removed there — test_skip_reason_guard.py enforces parity.
EXPECTED_SKIP_CODES: frozenset[str] = frozenset(
    {
        "integration_disabled",
        "auto_control_off",
        "same_position",
        "delta_too_small",
        "time_delta_too_small",
        "manual_override",
        "no_capable_service",
        "dry_run",
        "service_call_failed",
        "cover_unavailable",
        "policy_deferred",
        "calibration_in_progress",
        "superseded_in_queue",
    }
)

# The hand-maintained half of "everything record_skipped_action() can emit
# outside _skip()" — the other half, _HOLD_SKIP_LABEL's values, is read live
# off the dict itself rather than retyped here (see
# emitted_record_skipped_action_reasons and its use in test_skip_reason_guard
# and test_spec_translation_keys). Checked bidirectionally against the AST
# scan by test_skip_reason_guard.py: a reason emitted-but-not-listed here
# fails, and a reason listed here but no longer emitted anywhere also fails.
EXTRA_RECORD_SKIPPED_ACTION_REASONS: frozenset[str] = frozenset(
    {"preempted_by_handler", "hold"}
)


def idle_last_skipped_value() -> str:
    """Return the real idle-state literal ``sensor._last_skipped_value`` emits.

    Calls the actual function against a stub whose diagnostics are present
    but carry no ``last_skipped_action`` record, rather than hand-typing
    ``"no_action_skipped"`` a second time, so a future rename is caught here
    automatically instead of drifting out of sync. (A falsy ``diagnostics``
    — ``None`` or ``{}`` — takes an earlier ``return None`` branch in
    ``_last_skipped_value`` meant for "no diagnostics computed yet", not the
    idle state this helper wants.)
    """
    from custom_components.adaptive_cover_pro.sensor import _last_skipped_value

    stub = SimpleNamespace(data=SimpleNamespace(diagnostics={"unrelated": True}))
    return _last_skipped_value(stub)


# ---------------------------------------------------------------------------
# AST scan: every reason that can reach record_skipped_action()
# ---------------------------------------------------------------------------

_ADAPTIVE_COVER_PRO_DIR = (
    Path(__file__).parent.parent.parent / "custom_components" / "adaptive_cover_pro"
)

# (module import path, path to its source file) for every file that may call
# record_skipped_action(). Add an entry here if a new one starts calling it.
_SCANNED_MODULES: tuple[tuple[str, Path], ...] = (
    (
        "custom_components.adaptive_cover_pro.coordinator",
        _ADAPTIVE_COVER_PRO_DIR / "coordinator.py",
    ),
    (
        "custom_components.adaptive_cover_pro.managers.cover_command",
        _ADAPTIVE_COVER_PRO_DIR / "managers" / "cover_command" / "__init__.py",
    ),
)

# (enclosing function name, parameter name) pairs whose value, when forwarded
# straight through as a nested record_skipped_action() call's reason
# argument, is NOT a new literal to account for here — it is resolved at
# another call site this same scan already visits, or by a sibling guard:
#
# - ("_skip", "reason"): CoverCommandService._skip(entity_id, reason,
#   position, ...) forwards its own `reason` parameter to
#   `self._diag.record_skipped_action(entity_id, reason, position, ...)`.
#   Every value it can carry is a literal at one of its OWN call sites
#   (`self._skip(entity, "code", ...)`), which EXPECTED_SKIP_CODES and
#   test_skip_reason_guard.test_no_undocumented_skip_codes_in_source already
#   enumerate exhaustively.
# - ("record_skipped_action", "reason"): CoverCommandService
#   .record_skipped_action(entity, reason, state, ...) forwards its own
#   `reason` parameter to `self._diag.record_skipped_action(...)`. Every
#   value it can carry is a literal/constant/dynamic-lookup at one of ITS
#   OWN call sites (coordinator.py), which this same scan visits directly.
_KNOWN_FORWARDED_PARAMS: frozenset[tuple[str, str]] = frozenset(
    {
        ("_skip", "reason"),
        ("record_skipped_action", "reason"),
    }
)

_CONSTANT_NAME_RE = re.compile(r"^_*[A-Z][A-Z0-9_]*$")


def _callee_name(func: ast.expr) -> str | None:
    """Return the bare name of a call target: ``foo`` or ``obj.attr.foo`` → ``foo``."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _reason_arg(call: ast.Call) -> ast.expr | None:
    """Return the AST node for a ``record_skipped_action(...)`` call's reason arg.

    Positional index 1 (``entity, reason, state, ...``) for every real
    signature in the codebase (``CoverCommandService._skip``,
    ``CoverCommandService.record_skipped_action``,
    ``DiagnosticsRecorder.record_skipped_action``); falls back to a keyword
    ``reason=...`` so a future call written that way is still recognized.
    """
    for kw in call.keywords:
        if kw.arg == "reason":
            return kw.value
    if len(call.args) >= 2:
        return call.args[1]
    return None


class _UnresolvedReasonError(AssertionError):
    """Raised when a record_skipped_action() reason argument is an unrecognized shape."""


class _RecordSkippedActionVisitor(ast.NodeVisitor):
    """Collect every reason string that can reach ``record_skipped_action()``.

    Walks one module's AST, tracking the innermost enclosing function so a
    ``Name`` reason argument can be checked against
    :data:`_KNOWN_FORWARDED_PARAMS` (by (function name, param name)) and, for
    the ``_HOLD_SKIP_LABEL``-style dynamic pattern, searched for its
    assignment within that same function.
    """

    def __init__(self, path: Path, module: Any) -> None:
        self.path = path
        self.module = module
        self.found: set[str] = set()
        self._func_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _callee_name(node.func) == "record_skipped_action":
            self._handle_call(node)
        self.generic_visit(node)

    def _handle_call(self, node: ast.Call) -> None:
        reason_node = _reason_arg(node)
        if reason_node is None:
            raise _UnresolvedReasonError(
                f"{self.path}:{node.lineno}: record_skipped_action() call has "
                "no resolvable reason argument (positional index 1 or keyword "
                "reason=). Update tests/_helpers/skip_codes.py's scan if the "
                "signature changed."
            )
        enclosing = self._func_stack[-1] if self._func_stack else None
        self.found |= self._resolve(reason_node, enclosing)

    def _resolve(
        self,
        node: ast.expr,
        enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None,
    ) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}

        if isinstance(node, ast.Name):
            func_name = enclosing.name if enclosing is not None else None
            if (func_name, node.id) in _KNOWN_FORWARDED_PARAMS:
                return set()

            constant = self._resolve_str_constant(node.id)
            if constant is not None:
                return {constant}

            dynamic = self._resolve_dynamic_assignment(node.id, enclosing)
            if dynamic is not None:
                return dynamic

        raise _UnresolvedReasonError(
            f"{self.path}:{node.lineno}: record_skipped_action() reason "
            f"argument ({ast.dump(node)}) is not a recognized shape — a "
            "string literal, a known forwarded parameter "
            "(_KNOWN_FORWARDED_PARAMS), a module-level string constant, or "
            'the `_HOLD_SKIP_LABEL.get(..., "fallback")` dynamic-lookup '
            "pattern. Teach tests/_helpers/skip_codes.py's scan the new "
            "shape, or rewrite the call to use a recognized one."
        )

    def _resolve_str_constant(self, name: str) -> str | None:
        """``getattr(self.module, name)`` if *name* looks like a str constant."""
        if not _CONSTANT_NAME_RE.match(name):
            return None
        value = getattr(self.module, name, None)
        return value if isinstance(value, str) else None

    def _resolve_dict_constant(self, name: str) -> dict | None:
        """``getattr(self.module, name)`` if *name* looks like a dict constant."""
        if not _CONSTANT_NAME_RE.match(name):
            return None
        value = getattr(self.module, name, None)
        return value if isinstance(value, dict) else None

    def _resolve_dynamic_assignment(
        self, name: str, enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None
    ) -> set[str] | None:
        """Resolve ``name = SOME_DICT.get(<key-expr>, "fallback")`` within *enclosing*.

        The only dynamic pattern in the codebase today is coordinator.py's
        ``label = _HOLD_SKIP_LABEL.get(result.control_method, "hold")``. The
        fallback literal is read from the AST (never hand-typed), and the
        dict's possible values are read from the live dict object — so a
        value added to or removed from ``_HOLD_SKIP_LABEL``, or a changed
        fallback, is picked up automatically without touching this file.
        """
        if enclosing is None:
            return None
        for stmt in ast.walk(enclosing):
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == name
                for target in stmt.targets
            ):
                continue
            value = stmt.value
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "get"
                and isinstance(value.func.value, ast.Name)
                and len(value.args) == 2
                and isinstance(value.args[1], ast.Constant)
                and isinstance(value.args[1].value, str)
            ):
                continue
            dict_obj = self._resolve_dict_constant(value.func.value.id)
            if dict_obj is None:
                continue
            fallback = value.args[1].value
            return set(dict_obj.values()) | {fallback}
        return None


def emitted_record_skipped_action_reasons() -> frozenset[str]:
    """Every reason string that can reach ``record_skipped_action()``, by AST scan.

    Walks ``coordinator.py`` and ``managers/cover_command/__init__.py``,
    finds every ``Call`` whose callee's bare name is
    ``record_skipped_action``, and resolves its reason argument per
    ``_RecordSkippedActionVisitor._resolve``. Raises ``_UnresolvedReasonError``
    (an ``AssertionError`` subclass, so it surfaces as a normal test failure)
    naming the file:line if a call's reason argument is a shape the scan
    doesn't recognize — a new unresolvable shape must fail loudly here rather
    than silently contribute nothing.

    This is the single source of truth ``test_skip_reason_guard.py`` and
    ``test_spec_translation_keys.py`` both build on, replacing an earlier
    regex scan that only matched a quoted-literal second positional argument
    and therefore missed a reason passed as a bare constant name (see the
    module docstring).
    """
    found: set[str] = set()
    for module_name, path in _SCANNED_MODULES:
        module = importlib.import_module(module_name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _RecordSkippedActionVisitor(path, module)
        visitor.visit(tree)
        found |= visitor.found
    return frozenset(found)
