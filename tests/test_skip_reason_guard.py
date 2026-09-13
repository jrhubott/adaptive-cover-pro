"""Skip reason guard tests.

Three guards in one module:

1. Skip-code exhaustiveness — the canonical set of reason codes embedded here
   must match every ``_skip()`` call site in cover_command.py. Fails when a new
   skip code is added or an old one removed without updating EXPECTED_SKIP_CODES.

2. Literal-reason exhaustiveness outside ``_skip()`` — the small number of
   reason literals that reach ``record_skipped_action()`` directly, or via a
   ``Mapping.get(..., "literal")`` fallback, bypassing ``_skip()`` entirely.
   Fails when a new one is added to cover_command.py or coordinator.py
   without updating EXTRA_RECORD_SKIPPED_ACTION_REASONS.

3. Always-present keys — every skip code must produce a ``last_skipped_action``
   dict that contains all 7 always-present keys documented in CLAUDE.md. Fails
   when ``record_skipped_action()`` is changed in a way that drops a required key.

When you add a new skip code:
  - Add the reason string to EXPECTED_SKIP_CODES in tests/_helpers/skip_codes.py
  - Update the always-present-keys test if the new code produces extras
  - Update the CLAUDE.md "last_skipped_action Dict Structure" section
  - Add a translated state to translations/{en,de,fr}.json entity.sensor
    .last_skipped_action.state (tests/test_spec_translation_keys.py enforces
    this)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from tests._helpers.skip_codes import (
    EXPECTED_SKIP_CODES as _EXPECTED_SKIP_CODES,
    EXTRA_RECORD_SKIPPED_ACTION_REASONS,
)

# Always-present keys in any last_skipped_action dict (CLAUDE.md §last_skipped_action).
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

_COORDINATOR_SRC = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "coordinator.py"
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
                f"Skip code {code!r} is in _EXPECTED_SKIP_CODES but not found in "
                "managers/cover_command.py. Remove it from _EXPECTED_SKIP_CODES."
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
            f"Skip codes in cover_command.py not in _EXPECTED_SKIP_CODES: "
            f"{sorted(undocumented)}\n"
            "Add them to _EXPECTED_SKIP_CODES and update CLAUDE.md."
        )

    def test_no_undocumented_literal_reasons_outside_skip(self) -> None:
        """Every literal reason reaching record_skipped_action() outside
        ``_skip()`` must be in EXTRA_RECORD_SKIPPED_ACTION_REASONS.

        ``_skip()`` call sites are covered by
        ``test_no_undocumented_skip_codes_in_source`` above. This closes the
        gap for the OTHER two shapes a ``last_skipped_action.reason`` literal
        can take, scanning both cover_command.py and coordinator.py:

        1. A literal passed directly to ``record_skipped_action()`` —
           currently only ``record_preempted_skip``'s
           ``"preempted_by_handler"``.
        2. A literal fallback in ``_HOLD_SKIP_LABEL.get(..., "literal")`` —
           currently only coordinator's pseudo-hold ``"hold"`` default.

        A new literal reaching either shape without being added to
        EXTRA_RECORD_SKIPPED_ACTION_REASONS fails here. This is what would
        have caught #1353's hand-typed, undocumented
        ``{"hold", "preempted_by_handler", "no_action_skipped"}`` set that
        used to live directly in test_spec_translation_keys.py.
        """
        direct_literal = re.compile(
            r'record_skipped_action\(\s*\w+,\s*["\']([^"\']+)["\']'
        )
        found: set[str] = set()
        for src in (_COVER_COMMAND_SRC, _COORDINATOR_SRC):
            found |= set(direct_literal.findall(src))

        # The pseudo-hold fallback is a lookup, not a direct call argument —
        # scoped to _HOLD_SKIP_LABEL specifically so this doesn't turn into a
        # blanket (and noisy) scan of every `.get(..., "literal")` in the file.
        hold_fallback = re.search(
            r'_HOLD_SKIP_LABEL\.get\([^,]+,\s*["\']([^"\']+)["\']\)', _COORDINATOR_SRC
        )
        assert hold_fallback, (
            'Expected to find _HOLD_SKIP_LABEL.get(..., "<fallback>") in '
            "coordinator.py — has the pseudo-hold fallback been refactored? "
            "Update this scan to match."
        )
        found.add(hold_fallback.group(1))

        undocumented = found - EXTRA_RECORD_SKIPPED_ACTION_REASONS
        assert not undocumented, (
            f"New literal last_skipped_action reason(s) found outside "
            f"_skip(): {sorted(undocumented)}\n"
            "Add them to tests/_helpers/skip_codes.EXTRA_RECORD_SKIPPED_ACTION_REASONS "
            "and to translations/{en,de,fr}.json "
            "entity.sensor.last_skipped_action.state."
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
