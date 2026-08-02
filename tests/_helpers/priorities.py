"""Shared priority anchors for pipeline tests.

CODING_GUIDELINES § Handler Priorities makes the handler class the source of
truth and bans priority literals at fixture construction; § No Magic Numbers
puts constants used by more than one test file here rather than duplicating
them. Both test files that exercise the #1170 hold gate need the same three
anchors, so they are derived once.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)

#: The priority a hold is judged against in the #1170 tests.
HOLDER_PRIORITY = ManualOverrideHandler.priority

#: A bound that must YIELD to that hold — the shipped slot default (77 < 80).
BELOW_HOLDER = DEFAULT_CUSTOM_POSITION_PRIORITY

#: A bound that must still CLAMP it. Derived, so re-prioritizing manual
#: override cannot silently make a test vacuous.
ABOVE_HOLDER = ManualOverrideHandler.priority + 2

# `outranking` exempts CUSTOM_POSITION_SAFETY_PRIORITY from the comparison
# entirely, so an anchor that landed on 100 would let the "still clamps" tests
# pass through the safety carve-out instead of the `>` they name — the exact
# vacuity this module exists to prevent. Built-in handlers cap at 99, so this
# only fires if someone declares manual override at 98+.
assert ABOVE_HOLDER < CUSTOM_POSITION_SAFETY_PRIORITY, (  # noqa: S101
    f"ABOVE_HOLDER ({ABOVE_HOLDER}) collides with the safety carve-out; "
    "the #1170 clamp tests would pass for the wrong reason"
)
