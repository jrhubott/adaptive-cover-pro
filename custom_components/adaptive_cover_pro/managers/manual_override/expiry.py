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

# How a cover's recorded ``started_at`` was obtained. The two arming paths
# cannot mean the same thing by it, and a diagnostics consumer must be able to
# tell which it is holding — the value visibly changes for one and the same
# override across an HA restart (issue #1044).
#
#   engaged            — the honest moment ACP engaged the override: the user
#                        touched the cover, a wall switch fired, or the
#                        ``engage_manual_override`` service was called.
#   derived_from_expiry — RECONSTRUCTED via :func:`started_at_for_expiry`. The
#                        restore path (issue #1019) is handed an absolute expiry
#                        and nothing else — the true start was never persisted —
#                        so it back-dates one. Under a non-``fixed`` duration
#                        mode, or against a pinned service deadline, that
#                        back-date is not when the user actually touched the
#                        cover and must not be read as such.
#
# The override's true end is ``expires_at``, which is exact in both cases.
STARTED_AT_SOURCE_ENGAGED = "engaged"
STARTED_AT_SOURCE_DERIVED = "derived_from_expiry"


def expiry_for_started_at(
    started_at: dt.datetime, duration: dt.timedelta
) -> dt.datetime:
    """Return the override expiry for a given start time and reset duration."""
    return started_at + duration


def started_at_for_expiry(expiry: dt.datetime, duration: dt.timedelta) -> dt.datetime:
    """Return the start time that yields ``expiry`` for a given duration (inverse)."""
    return expiry - duration
