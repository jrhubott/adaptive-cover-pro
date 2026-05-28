"""Shared in-transit predicate for the cover-command service and sequencer.

Single source of truth for "is this HA cover state value one of the motor-
running states?". Before this module the literal
``state in ("opening", "closing")`` was duplicated across
``CoverCommandService._is_cover_in_transit``, every inline check inside
``StateClassifier``, and ``DualAxisSequencer._COVER_MOVING_STATES``. Issue
#33 forced both axes to share the same publish-lag policy, which is the
trigger for collapsing those copies into one helper.

Per CODING_GUIDELINES.md § "Code duplication is not okay": when two code
paths need the same policy, extract one helper and have both call it.
"""

from __future__ import annotations

# HA cover states that indicate the motor is actively moving the carriage.
# Kept as a module-level frozenset so callers that need set membership
# (e.g. existing tests that monkeypatched ``_COVER_MOVING_STATES``) can
# still poke a single attribute.
_MOVING_STATES: frozenset[str] = frozenset({"opening", "closing"})


def is_state_in_transit(state: str | None) -> bool:
    """Return True when ``state`` denotes the cover actively moving.

    Returns False for ``None``, empty strings, ``"open"``/``"closed"``,
    ``"stopped"``, ``"unknown"``, ``"unavailable"``, or anything else
    outside :data:`_MOVING_STATES`. Used by:

    * ``CoverCommandService._is_cover_in_transit`` — gate for skipping
      reconciliation while the motor is running.
    * ``StateClassifier.classify`` — five inline checks collapsed onto
      this helper (issue #33 progress-aware backstop guard).
    * ``DualAxisSequencer._wait_for_position_settle`` — settle-loop motion
      observation.
    * ``DualAxisSequencer.is_in_suppression_with_cap`` — mid-travel
      override-suppression tier (a) for the tilt axis.
    """
    return state in _MOVING_STATES
