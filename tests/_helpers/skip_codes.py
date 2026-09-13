"""Canonical ``last_skipped_action`` reason-code sets, derived by AST scan.

Shared by ``tests/test_skip_reason_guard.py`` (exhaustiveness in both
directions: every reason a scanned call site can actually pass must be
documented, and every documented reason must still be reachable somewhere)
and ``tests/test_spec_translation_keys.py`` (translation completeness:
every accounted-for reason must have a translated state in every shipped
language).

``last_skipped_action.reason`` values reach the wire through three shapes:

- ``EXPECTED_SKIP_CODES``: reason codes passed to ``CoverCommandService
  ._skip(entity, reason, position, ...)`` inside
  ``managers/cover_command/__init__.py``. Cross-checked in both directions
  against :func:`emitted_skip_codes`'s AST scan of ``_skip(`` call sites.
- ``EXTRA_RECORD_SKIPPED_ACTION_REASONS``: the hand-maintained reasons that
  reach ``record_skipped_action()`` OUTSIDE ``_skip()`` through some path
  other than ``_HOLD_SKIP_LABEL`` — today just ``"preempted_by_handler"``
  (``CoverCommandService.record_preempted_skip``) and the ``"hold"``
  fallback literal in coordinator.py's
  ``_HOLD_SKIP_LABEL.get(result.control_method, "hold")``.
  ``_HOLD_SKIP_LABEL``'s own values are read live off the dict rather than
  retyped here. Cross-checked in both directions against
  :func:`emitted_record_skipped_action_reasons`.
- The idle state (no ``last_skipped_action`` record yet) — ``sensor.py``'s
  ``_last_skipped_value`` returns a literal directly, never going through
  ``record_skipped_action`` at all — derived via
  :func:`idle_last_skipped_value` by calling the real function rather than
  hand-typing its return value.

Both AST scans share one ``ast.NodeVisitor`` (``_SkipReasonVisitor``) that
walks coordinator.py and managers/cover_command/__init__.py, resolving
every ``_skip(`` and ``record_skipped_action(`` call's reason argument — a
string literal, a module-level constant referenced by name, or the
``_HOLD_SKIP_LABEL`` dynamic-lookup pattern — and raising loudly on any
other shape (an f-string, an unrecognized variable, a tuple/list-unpacked
binding, etc.) rather than silently contributing nothing.
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
# removed there — test_skip_reason_guard.py enforces parity in both directions.
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
# off the dict itself rather than retyped here. Checked bidirectionally
# against the AST scan by test_skip_reason_guard.py: a reason
# emitted-but-not-listed here fails, and a reason listed here but no longer
# emitted anywhere also fails.
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
# AST scan: every reason that can reach _skip() or record_skipped_action()
# ---------------------------------------------------------------------------

_ADAPTIVE_COVER_PRO_DIR = (
    Path(__file__).parent.parent.parent / "custom_components" / "adaptive_cover_pro"
)

# (module import path, path to its source file) for every file that may call
# _skip() or record_skipped_action(). Add an entry here if a new one starts
# calling either.
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

# Callable names this scanner resolves reasons for, mapped to the found-set
# bucket each is collected under. Both share the (entity, reason, position/
# state, ...) shape, so the same `_reason_arg` extraction applies to both.
_SCAN_TARGETS: dict[str, str] = {
    "_skip": "skip",
    "record_skipped_action": "record_skipped_action",
}

# (enclosing function name, parameter name) pairs whose value, when it
# reaches a scanned call's reason argument as a bare Name, is not a NEW
# reason to account for — it is that same function's own parameter,
# forwarded from a caller this same scan already visits directly:
#
# - ("_skip", "reason"): CoverCommandService._skip(entity_id, reason,
#   position, ...) forwards its own `reason` parameter to
#   `self._diag.record_skipped_action(entity_id, reason, position, ...)`.
#   Every value `reason` can hold is resolved at `_skip`'s OWN call sites
#   (`self._skip(entity, <reason>, ...)`), visited under the "skip" bucket.
# - ("record_skipped_action", "reason"): CoverCommandService
#   .record_skipped_action(entity, reason, state, ...) forwards its own
#   `reason` parameter to `self._diag.record_skipped_action(...)`. Every
#   value it can hold is resolved at ITS OWN call sites (coordinator.py),
#   visited under the "record_skipped_action" bucket.
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
    """Return the AST node for a scanned call's reason argument.

    Positional index 1 (``entity, reason, position_or_state, ...``) for
    every real signature this scanner targets (``CoverCommandService._skip``,
    ``CoverCommandService.record_skipped_action``,
    ``DiagnosticsRecorder.record_skipped_action``); falls back to a keyword
    ``reason=...`` so a call written that way is still recognized.
    """
    for kw in call.keywords:
        if kw.arg == "reason":
            return kw.value
    if len(call.args) >= 2:
        return call.args[1]
    return None


class _UnresolvedReasonError(AssertionError):
    """Raised when a scanned reason argument or binding is an unrecognized shape."""


class _ScopedAssignmentCollector(ast.NodeVisitor):
    """Collect every value bound to *target_name* within one function's own scope.

    Does not descend into a nested function/class/lambda — a binding there
    is a different variable even if it shares the name. Handles a plain
    assignment (``label = ...``), an annotated assignment
    (``label: str = ...``), and a walrus binding (``label := ...``)
    anywhere in the scope, not just the first one found. A tuple/list target
    that includes *target_name* (``label, other = ...``) raises immediately
    — this scanner has no reliable way to correlate an unpacked target with
    its per-element source value, so that shape must fail loudly rather than
    be silently skipped.
    """

    def __init__(self, path: Path, target_name: str) -> None:
        self.path = path
        self.target_name = target_name
        self.bindings: list[ast.expr] = []

    def visit_FunctionDef(self, node: ast.AST) -> None:  # noqa: N802
        pass  # do not descend into a nested scope

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815
    visit_Lambda = visit_FunctionDef  # noqa: N815
    visit_ClassDef = visit_FunctionDef  # noqa: N815

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            self._record_target(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._record_target(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self._record_target(node.target, node.value)
        self.generic_visit(node)

    def _record_target(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            if target.id == self.target_name:
                self.bindings.append(value)
            return
        if isinstance(target, (ast.Tuple, ast.List)) and any(
            isinstance(elt, ast.Name) and elt.id == self.target_name
            for elt in target.elts
        ):
            raise _UnresolvedReasonError(
                f"{self.path}:{target.lineno}: {self.target_name!r} is bound "
                "via tuple/list unpacking, which this scanner cannot "
                "reliably correlate to a source value. Rewrite as a plain "
                "assignment, or teach tests/_helpers/skip_codes.py's scan."
            )


class _SkipReasonVisitor(ast.NodeVisitor):
    """Collect every reason string that can reach ``_skip()`` or ``record_skipped_action()``.

    Walks one module's AST, tracking the innermost enclosing function so a
    ``Name`` reason argument can be checked against
    :data:`_KNOWN_FORWARDED_PARAMS` (by (function name, param name)) and, for
    the ``_HOLD_SKIP_LABEL``-style dynamic pattern, resolved via every
    binding of that name within the same function's scope.
    """

    def __init__(self, path: Path, module: Any) -> None:
        self.path = path
        self.module = module
        self.found: dict[str, set[str]] = {
            bucket: set() for bucket in _SCAN_TARGETS.values()
        }
        self._func_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        bucket = _SCAN_TARGETS.get(_callee_name(node.func))
        if bucket is not None:
            self._handle_call(node, bucket)
        self.generic_visit(node)

    def _handle_call(self, node: ast.Call, bucket: str) -> None:
        reason_node = _reason_arg(node)
        if reason_node is None:
            raise _UnresolvedReasonError(
                f"{self.path}:{node.lineno}: call has no resolvable reason "
                "argument (positional index 1 or keyword reason=). Update "
                "tests/_helpers/skip_codes.py's scan if the signature changed."
            )
        enclosing = self._func_stack[-1] if self._func_stack else None
        self.found[bucket] |= self._resolve_call_arg(reason_node, enclosing)

    def _resolve_call_arg(
        self,
        node: ast.expr,
        enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None,
    ) -> set[str]:
        """Resolve a call's reason argument node to its possible string value(s)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}

        if isinstance(node, ast.Name):
            func_name = enclosing.name if enclosing is not None else None
            if (func_name, node.id) in _KNOWN_FORWARDED_PARAMS:
                return set()

            constant = self._resolve_module_constant(node.id, str)
            if constant is not None:
                return {constant}

            dynamic = self._resolve_dynamic_assignment(node.id, enclosing)
            if dynamic is not None:
                return dynamic

        raise _UnresolvedReasonError(
            f"{self.path}:{node.lineno}: reason argument ({ast.dump(node)}) "
            "is not a recognized shape — a string literal, a known "
            "forwarded parameter (_KNOWN_FORWARDED_PARAMS), a module-level "
            "string constant, or a dynamic binding resolvable per "
            "_resolve_assignment_value. Teach tests/_helpers/skip_codes.py's "
            "scan the new shape, or rewrite the call to use a recognized one."
        )

    def _resolve_module_constant(self, name: str, expected_type: type) -> Any | None:
        """Return ``getattr(self.module, name)`` if *name* looks like a constant.

        Only names matching the UPPER_CASE (optionally underscore-prefixed)
        convention are considered — a lowercase local variable that happens
        to collide with a module attribute name must never resolve here.
        """
        if not _CONSTANT_NAME_RE.match(name):
            return None
        value = getattr(self.module, name, None)
        return value if isinstance(value, expected_type) else None

    def _resolve_dynamic_assignment(
        self, name: str, enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None
    ) -> set[str] | None:
        """Resolve every binding of *name* within *enclosing*'s own scope.

        Collects ALL bindings (not just the first found) via
        :class:`_ScopedAssignmentCollector`, resolves each with
        :meth:`_resolve_assignment_value`, and unions the results — so a
        second binding on another branch (e.g. a plain literal reassignment)
        is not silently ignored. Returns ``None`` (not an unresolved error)
        only when *name* has no binding in this scope at all, since that
        means it is not this scanner's dynamic pattern — the caller then
        reports the original reason-argument Name as unresolved.
        """
        if enclosing is None:
            return None
        collector = _ScopedAssignmentCollector(self.path, name)
        for stmt in enclosing.body:
            collector.visit(stmt)
        if not collector.bindings:
            return None
        resolved: set[str] = set()
        for value in collector.bindings:
            resolved |= self._resolve_assignment_value(value)
        return resolved

    def _resolve_assignment_value(self, value: ast.expr) -> set[str]:
        """Resolve one assignment's RHS: a dict ``.get(key, "fallback")`` call,
        a string literal, or a module-level string constant name.

        The only dynamic pattern in the codebase today is coordinator.py's
        ``label = _HOLD_SKIP_LABEL.get(result.control_method, "hold")``. The
        fallback literal is read from the AST (never hand-typed), and the
        dict's possible values are read from the live dict object — so a
        value added to or removed from ``_HOLD_SKIP_LABEL``, or a changed
        fallback, is picked up automatically without touching this file.
        """
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get"
            and isinstance(value.func.value, ast.Name)
            and len(value.args) == 2
            and isinstance(value.args[1], ast.Constant)
            and isinstance(value.args[1].value, str)
        ):
            dict_obj = self._resolve_module_constant(value.func.value.id, dict)
            if dict_obj is not None:
                return set(dict_obj.values()) | {value.args[1].value}

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return {value.value}

        if isinstance(value, ast.Name):
            constant = self._resolve_module_constant(value.id, str)
            if constant is not None:
                return {constant}

        raise _UnresolvedReasonError(
            f"{self.path}:{getattr(value, 'lineno', '?')}: assignment value "
            f"({ast.dump(value)}) is not a recognized shape for the "
            'dynamic-binding resolver (a dict `.get(key, "fallback")` '
            "call, a string literal, or a module-level string constant). "
            "Teach tests/_helpers/skip_codes.py's scan the new shape."
        )


def _scan_reasons() -> dict[str, frozenset[str]]:
    """Run one AST scan of ``_SCANNED_MODULES``, returning every target's bucket."""
    found: dict[str, set[str]] = {bucket: set() for bucket in _SCAN_TARGETS.values()}
    for module_name, path in _SCANNED_MODULES:
        module = importlib.import_module(module_name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SkipReasonVisitor(path, module)
        visitor.visit(tree)
        for bucket, values in visitor.found.items():
            found[bucket] |= values
    return {bucket: frozenset(values) for bucket, values in found.items()}


def emitted_skip_codes() -> frozenset[str]:
    """Every reason string that can reach ``CoverCommandService._skip()``, by AST scan.

    Finds every ``self._skip(...)`` call site and resolves its reason
    argument. Raises ``_UnresolvedReasonError`` (an ``AssertionError``
    subclass, so it surfaces as a normal test failure) naming the file:line
    if a call's reason argument is a shape the scan doesn't recognize.
    """
    return _scan_reasons()["skip"]


def emitted_record_skipped_action_reasons() -> frozenset[str]:
    """Every reason string that can reach ``record_skipped_action()``, by AST scan.

    Finds every ``record_skipped_action(...)`` call site (however deeply
    dotted the callee) and resolves its reason argument the same way as
    :func:`emitted_skip_codes`. This is the single source of truth
    ``test_skip_reason_guard.py`` and ``test_spec_translation_keys.py`` both
    build on.
    """
    return _scan_reasons()["record_skipped_action"]
