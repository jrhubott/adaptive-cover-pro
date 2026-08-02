"""Shared priority anchors for pipeline tests.

CODING_GUIDELINES § Handler Priorities makes the handler class the source of
truth and bans priority literals at fixture construction; § No Magic Numbers
puts constants used by more than one test file here rather than duplicating
them. Both test files that exercise the #1170 hold gate need the same three
anchors, so they are derived once.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import (
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
