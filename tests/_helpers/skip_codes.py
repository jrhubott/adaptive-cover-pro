"""Canonical ``last_skipped_action`` reason-code sets, derived where possible.

Shared by ``tests/test_skip_reason_guard.py`` (exhaustiveness: every reason
literal that can reach ``CoverCommandService.record_skipped_action`` must be
accounted for here) and ``tests/test_spec_translation_keys.py`` (translation
completeness: every accounted-for reason must have a translated state in
every shipped language).

Two canons, because ``last_skipped_action.reason`` values reach the wire
through two different shapes:

- ``EXPECTED_SKIP_CODES``: reason codes passed to ``self._skip(entity,
  "code", ...)`` inside ``managers/cover_command/__init__.py``.
  ``test_skip_reason_guard.py`` cross-checks this set against the actual
  ``_skip(`` call sites in that file.
- ``EXTRA_RECORD_SKIPPED_ACTION_REASONS``: the small number of reason
  literals that reach ``record_skipped_action()`` OUTSIDE ``_skip()`` —
  ``"preempted_by_handler"`` (passed directly by
  ``CoverCommandService.record_preempted_skip``) and ``"hold"`` (the
  fallback default in coordinator.py's
  ``_HOLD_SKIP_LABEL.get(result.control_method, "hold")``).
  ``test_skip_reason_guard.py`` cross-checks this set against those call
  sites too, so a new literal dropped into either shape without updating
  this set fails loudly instead of silently reaching production untranslated
  (issue #1353 audit finding #5).

The idle state (``last_skipped_action`` has no record yet) is a third,
unrelated shape — ``sensor.py``'s ``_last_skipped_value`` returns a literal
directly, never going through ``record_skipped_action`` at all — so it is
derived separately via :func:`idle_last_skipped_value`, by calling the real
function rather than hand-typing its return value.
"""

from __future__ import annotations

from types import SimpleNamespace

# Canonical set of skip reason codes passed to `_skip()` in
# managers/cover_command/__init__.py.  Update this (and CLAUDE.md) whenever a
# code is added or removed there — test_skip_reason_guard.py enforces parity.
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

# Reason literals that reach record_skipped_action() outside of _skip() —
# either passed directly, or as a Mapping.get(..., "literal") fallback.
# test_skip_reason_guard.TestSkipCodeExhaustiveness scans both shapes.
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
