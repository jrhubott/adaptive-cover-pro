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

# The direction each HA transit state reports, as the sign of the change in
# the entity's raw ``current_position``. This is a WIRE fact: HA's convention
# ties "opening" to that number RISING, whatever the install's inverse-state
# flag says about what "open" means. A caller comparing it against a direction
# expressed in open-percent space has to flip it (see #993).
_TRANSIT_WIRE_SIGN: dict[str, int] = {"opening": 1, "closing": -1}

# HA cover states that indicate the motor is actively moving the carriage.
# Kept as a module-level frozenset so callers that need set membership
# (e.g. existing tests that monkeypatched ``_COVER_MOVING_STATES``) can
# still poke a single attribute. Derived from the sign table rather than
# restated, so the two can never drift apart.
_MOVING_STATES: frozenset[str] = frozenset(_TRANSIT_WIRE_SIGN)


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


def transit_wire_sign(state: str | None) -> int | None:
    """Return which way ``state`` says the raw ``current_position`` is moving.

    ``+1`` for ``"opening"`` (the wire number rising), ``-1`` for ``"closing"``
    (falling), ``None`` for every state that is not a transit state. By
    construction ``transit_wire_sign(s) is not None`` holds for exactly the same
    ``s`` as :func:`is_state_in_transit` — both read the one
    :data:`_TRANSIT_WIRE_SIGN` table — so a caller that needs the direction as
    well as the fact of motion asks this and does not also ask the predicate.

    The sign is in WIRE space. Code comparing it against a direction expressed
    in open-percent / dispatch frame must negate it on an inverse-state install:
    flipping the frame negates every delta and therefore every direction. Used
    by ``DayNightShadePolicy._start_confirmation`` (issues #1145, #993), where a
    rail that publishes a transit state but no intermediate position is the
    only evidence that the leading rail has started to move.
    """
    return _TRANSIT_WIRE_SIGN.get(state)
