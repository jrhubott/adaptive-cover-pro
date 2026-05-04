"""Skip reason guard tests.

Two guards in one module:

1. Skip-code exhaustiveness — the canonical set of reason codes embedded here
   must match every ``_skip()`` call site in cover_command.py. Fails when a new
   skip code is added or an old one removed without updating _EXPECTED_SKIP_CODES.

2. Always-present keys — every skip code must produce a ``last_skipped_action``
   dict that contains all 7 always-present keys documented in CLAUDE.md. Fails
   when ``record_skipped_action()`` is changed in a way that drops a required key.

When you add a new skip code:
  - Add the reason string to _EXPECTED_SKIP_CODES below
  - Update the always-present-keys test if the new code produces extras
  - Update the CLAUDE.md "last_skipped_action Dict Structure" section
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)

# Canonical set of skip reason codes.  Update this (and CLAUDE.md) whenever a
# code is added or removed from managers/cover_command.py.
_EXPECTED_SKIP_CODES: frozenset[str] = frozenset(
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
    }
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
    / "cover_command.py"
).read_text()


class _MinimalSvc:
    """Minimal stand-in that satisfies record_skipped_action's only self dependency."""

    last_skipped_action: dict = {}


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
