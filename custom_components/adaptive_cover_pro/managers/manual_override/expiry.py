"""Single source of truth for the manual-override expiry ↔ start-time inverse.

The manual-override *end time* is derived, not stored: a cover's override
expires at ``manual_control_time[eid] + reset_duration``. Three call sites need
the same arithmetic and its inverse — the end-time sensor value_fn/attrs, the
RestoreEntity restore path, and the ``engage_manual_override`` service — so the
formula lives here and every caller delegates (CODING_GUIDELINES.md §
"Single-Source-of-Truth Helpers for Repeated Formulas").
"""

from __future__ import annotations

import datetime as dt


def expiry_for_started_at(
    started_at: dt.datetime, duration: dt.timedelta
) -> dt.datetime:
    """Return the override expiry for a given start time and reset duration."""
    return started_at + duration


def started_at_for_expiry(expiry: dt.datetime, duration: dt.timedelta) -> dt.datetime:
    """Return the start time that yields ``expiry`` for a given duration (inverse)."""
    return expiry - duration
