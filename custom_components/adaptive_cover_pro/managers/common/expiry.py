"""Single source of truth for the expiry ↔ start-time inverse.

Two managers hold a wall-clock deadline that is really "a start instant plus a
configured duration", and both need to walk that relationship in both
directions:

* :class:`~..manual_override.manager.AdaptiveCoverManager` derives the
  ``fixed``-mode override expiry from ``started_at``, and inverts a restored
  absolute expiry back into a displayable ``started_at`` (issue #1044 / #1019);
* :class:`~..cloud_suppression.CloudSuppressionManager` derives the
  cloud-escalation deadline from the instant suppression engaged, and inverts a
  restored deadline back into that instant after a reboot (issue #175).

The arithmetic is two lines, which is exactly why it lives in one place:
``a + d`` and ``e - d`` drifting apart would be a silently wrong deadline, and
CODING_GUIDELINES.md § "Single-Source-of-Truth Helpers for Repeated Formulas"
puts the formula behind a name rather than in each caller. Promoted here from
``manual_override/expiry.py`` when cloud escalation became the second caller;
that module re-exports both names, so its own callers are unchanged.
"""

from __future__ import annotations

import datetime as dt


def expiry_for_started_at(
    started_at: dt.datetime, duration: dt.timedelta
) -> dt.datetime:
    """Return the expiry for a given start time and hold duration."""
    return started_at + duration


def started_at_for_expiry(expiry: dt.datetime, duration: dt.timedelta) -> dt.datetime:
    """Return the start time that yields ``expiry`` for a given duration (inverse)."""
    return expiry - duration
