"""Skip reason guard tests.

Three guards in one module:

1. Skip-code exhaustiveness — the canonical set of reason codes embedded here
   must match every ``_skip()`` call site in cover_command.py. Fails when a new
   skip code is added or an old one removed without updating EXPECTED_SKIP_CODES.

2. Reason exhaustiveness outside ``_skip()`` — the AST-derived set of every
   reason that can reach ``record_skipped_action()`` some other way (a direct
   literal, a module-level constant, or the ``_HOLD_SKIP_LABEL`` dynamic
   lookup) must match EXTRA_RECORD_SKIPPED_ACTION_REASONS plus
   ``_HOLD_SKIP_LABEL``'s own values, in BOTH directions: a reason newly
   emitted there but undocumented fails, and a documented reason no longer
   emitted anywhere also fails (see
   ``tests/_helpers/skip_codes.emitted_record_skipped_action_reasons``).

3. Always-present keys — every skip code must produce a ``last_skipped_action``
   dict that contains all 7 always-present keys documented in
   CODING_GUIDELINES.md. Fails when ``record_skipped_action()`` is changed in
   a way that drops a required key.

When you add a new skip code:
  - Add the reason string to EXPECTED_SKIP_CODES in tests/_helpers/skip_codes.py
  - Update the always-present-keys test if the new code produces extras
  - Update CODING_GUIDELINES.md's "last_skipped_action Dict Structure" section
  - Add a translated state to translations/{en,de,fr}.json entity.sensor
    .last_skipped_action.state (tests/test_spec_translation_keys.py enforces
    this)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.coordinator import _HOLD_SKIP_LABEL
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from tests._helpers.skip_codes import (
    EXPECTED_SKIP_CODES as _EXPECTED_SKIP_CODES,
    EXTRA_RECORD_SKIPPED_ACTION_REASONS,
    emitted_record_skipped_action_reasons,
)

# Always-present keys in any last_skipped_action dict (CODING_GUIDELINES.md §
# `last_skipped_action` Dict Structure).
_ALWAYS_PRESENT_KEYS: frozenset[str] = frozenset(
    {
        "entity_id",
        "reason",
        "calculated_position",
        "current_position",
        "trigger",
        "inverse_state_applied",
        "timestamp",
    }
)

_COVER_COMMAND_SRC = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "managers"
    / "cover_command"
    / "__init__.py"
).read_text()


class _MinimalSvc:
    """Minimal stand-in that satisfies record_skipped_action's only self dependency."""

    def __init__(self) -> None:
        # record_skipped_action delegates to self._diag.record_skipped_action,
        # so the stub needs a real DiagnosticsRecorder to write into.
        from custom_components.adaptive_cover_pro.managers.cover_command.diagnostics import (
            DiagnosticsRecorder,
        )

        self._diag = DiagnosticsRecorder()

    @property
    def last_skipped_action(self) -> dict:
        return self._diag.last_skipped_action


class TestSkipCodeExhaustiveness:
    """The _EXPECTED_SKIP_CODES set must stay in sync with cover_command.py."""

    def test_all_expected_skip_codes_present_in_source(self) -> None:
        """Every code in _EXPECTED_SKIP_CODES must appear as a literal in _skip() calls.

        Fails when _EXPECTED_SKIP_CODES contains a code no longer used in the source.
        """
        for code in _EXPECTED_SKIP_CODES:
            assert (
                f'"{code}"' in _COVER_COMMAND_SRC or f"'{code}'" in _COVER_COMMAND_SRC
            ), (
                f"Skip code {code!r} is in EXPECTED_SKIP_CODES (tests/_helpers/"
                "skip_codes.py) but not found in managers/cover_command.py. "
                "Remove it from EXPECTED_SKIP_CODES."
            )

    def test_no_undocumented_skip_codes_in_source(self) -> None:
        """Every skip reason literal in _skip() calls must be in _EXPECTED_SKIP_CODES.

        Fails when a new skip code is added to cover_command.py without updating
        _EXPECTED_SKIP_CODES.
        """
        # Match the second positional argument (reason) in self._skip(entity, "code", ...)
        pattern = re.compile(r'self\._skip\(\s*\w+,\s*["\']([^"\']+)["\']')
        found = frozenset(pattern.findall(_COVER_COMMAND_SRC))

        undocumented = found - _EXPECTED_SKIP_CODES
        assert not undocumented, (
            f"Skip codes in cover_command.py not in EXPECTED_SKIP_CODES: "
            f"{sorted(undocumented)}\n"
            "Add them to EXPECTED_SKIP_CODES in tests/_helpers/skip_codes.py "
            "and update CODING_GUIDELINES.md's `last_skipped_action` Dict "
            "Structure section."
        )

    def test_no_undocumented_or_stale_reasons_outside_skip(self) -> None:
        """Every reason reaching record_skipped_action() outside ``_skip()``
        must be documented — in both directions.

        ``_skip()`` call sites are covered by
        ``test_no_undocumented_skip_codes_in_source`` above. This covers every
        OTHER shape a ``last_skipped_action.reason`` value can take — a
        literal passed directly to ``record_skipped_action()``
        (``record_preempted_skip``'s ``"preempted_by_handler"``), a
        module-level constant name (coordinator's
        ``_MANUAL_OVERRIDE_SKIP_LABEL``), and the ``_HOLD_SKIP_LABEL``
        dynamic lookup (its live values plus the AST-read fallback literal)
        — via ``emitted_record_skipped_action_reasons()``'s AST scan of
        coordinator.py and cover_command/__init__.py.

        Checked against ``EXTRA_RECORD_SKIPPED_ACTION_REASONS |
        set(_HOLD_SKIP_LABEL.values())`` in BOTH directions: a reason newly
        emitted there but undocumented fails, and a reason documented but no
        longer emitted anywhere also fails (the reverse check the round-1
        version of this guard didn't have). This is what would have caught
        #1353 round 2's gap: coordinator.py's
        ``_MANUAL_OVERRIDE_SKIP_LABEL`` reached ``record_skipped_action()``
        as a bare ``Name`` (never a quoted literal, so the old regex scan
        could not see it) and silently passed only because its value happens
        to equal an already-documented ``_skip()`` code.
        """
        documented = EXTRA_RECORD_SKIPPED_ACTION_REASONS | set(
            _HOLD_SKIP_LABEL.values()
        )
        emitted = emitted_record_skipped_action_reasons() - _EXPECTED_SKIP_CODES

        undocumented = emitted - documented
        assert not undocumented, (
            f"Reason(s) reach record_skipped_action() outside _skip() but "
            f"aren't documented: {sorted(undocumented)}\n"
            "Add them to tests/_helpers/skip_codes.EXTRA_RECORD_SKIPPED_ACTION_REASONS "
            "(or to _HOLD_SKIP_LABEL, if that's the true source) and to "
            "translations/{en,de,fr}.json entity.sensor.last_skipped_action.state."
        )

        stale = documented - emitted
        assert not stale, (
            f"Documented reason(s) are no longer emitted anywhere: "
            f"{sorted(stale)}\n"
            "Remove them from tests/_helpers/skip_codes"
            ".EXTRA_RECORD_SKIPPED_ACTION_REASONS if genuinely dead, or fix "
            "the code path that stopped emitting them."
        )


class TestAlwaysPresentKeys:
    """record_skipped_action must produce all 7 always-present keys for any code."""

    @pytest.mark.parametrize("reason", sorted(_EXPECTED_SKIP_CODES))
    def test_always_present_keys(self, reason: str) -> None:
        """Every skip code produces a dict containing the 7 always-present keys."""
        svc = _MinimalSvc()
        CoverCommandService.record_skipped_action(
            svc,  # type: ignore[arg-type]
            "cover.test_entity",
            reason,
            42,
        )
        result = svc.last_skipped_action
        missing = _ALWAYS_PRESENT_KEYS - result.keys()
        assert not missing, (
            f"Skip code {reason!r} is missing always-present keys: {sorted(missing)}\n"
            "Fix record_skipped_action() in managers/cover_command.py."
        )

    def test_delta_too_small_has_extras(self) -> None:
        """delta_too_small must include position_delta and min_delta_required."""
        svc = _MinimalSvc()
        CoverCommandService.record_skipped_action(
            svc,  # type: ignore[arg-type]
            "cover.test",
            "delta_too_small",
            50,
            extras={"position_delta": 2, "min_delta_required": 5},
        )
        assert "position_delta" in svc.last_skipped_action
        assert "min_delta_required" in svc.last_skipped_action

    def test_time_delta_too_small_has_extras(self) -> None:
        """time_delta_too_small must include elapsed_minutes and time_threshold_minutes."""
        svc = _MinimalSvc()
        CoverCommandService.record_skipped_action(
            svc,  # type: ignore[arg-type]
            "cover.test",
            "time_delta_too_small",
            50,
            extras={"elapsed_minutes": 1.5, "time_threshold_minutes": 2.0},
        )
        assert "elapsed_minutes" in svc.last_skipped_action
        assert "time_threshold_minutes" in svc.last_skipped_action
