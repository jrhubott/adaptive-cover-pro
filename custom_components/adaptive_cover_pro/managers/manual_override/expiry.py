"""Single source of truth for the manual-override expiry ↔ start-time inverse.

``manual_control_time[eid] + reset_duration`` is the ``fixed``-mode hold: the
override runs for a flat clock duration from the moment the user touched the
cover. Since issue #1044 that is one of several duration modes, so this pair is
no longer the end-time *authority* — :meth:`.manager.AdaptiveCoverManager.expiry_for`
is, and every surface reads through it.

These two helpers remain the single home of the arithmetic itself, used to
derive the ``fixed``-mode expiry and — via the inverse — to reconstruct the
displayed ``started_at`` when an absolute expiry is restored after a reboot
(CODING_GUIDELINES.md § "Single-Source-of-Truth Helpers for Repeated Formulas").
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
