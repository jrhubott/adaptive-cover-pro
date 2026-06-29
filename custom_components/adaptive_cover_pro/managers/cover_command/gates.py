"""Pure delta/time gate checks for cover_command.

Decisions only — every input the gate needs is passed in. The orchestrator
(:class:`CoverCommandService`) does the HA-side reads (current position,
last-updated timestamp) and hands the values to these functions, which
keeps the gates trivially unit-testable and free of HA imports.

Why pre-fetched values rather than callbacks: tests historically patch
``managers.cover_command.get_last_updated`` at the package's __init__
module path. Keeping the read inside the wrapper method lets that patch
keep working; the gate functions then operate on the (already-fetched)
values without caring where they came from.
"""

from __future__ import annotations

import datetime as dt

# Canonical endpoint values (mirrors POSITION_CLOSED / POSITION_OPEN from const.py;
# kept as literals here so gates.py remains free of const imports).
_ENDPOINT_VALUES: frozenset[int] = frozenset({0, 100})


def filter_endpoint_specials(positions: list, enforced: bool) -> list:
    """Remove 0/100 endpoint values from *positions* when *enforced* is True.

    Single-point decision shared by :func:`build_special_positions` (routing.py)
    and the venetian sequencer's tilt-specials computation (sequencer.py), so the
    "drop endpoints when enforce_delta_at_endpoints is on" logic lives in exactly
    one place (no-duplication guideline).

    When *enforced* is False the list is returned unchanged — callers may pass the
    same list they constructed; no copy is made in that branch.
    """
    if not enforced:
        return positions
    return [p for p in positions if p not in _ENDPOINT_VALUES]


def check_position_delta(
    entity: str,
    target: int,
    min_change: int,
    special_positions: list[int] | None,
    *,
    position: int | None,
    logger,
    sun_just_appeared: bool = False,
    axis_label: str = "position",
) -> bool:
    """Return True if a command should be sent based on position delta.

    Bypasses delta check for:
    - Unknown current position (cover not yet reporting)
    - sun_just_appeared (cover may need to re-confirm same position)
    - moves to/from special positions (0, 100, default, sunset)

    Same-position short-circuit is handled upstream in apply_position and
    applies to all callers including force=True (issue #290).

    ``special_positions`` may be ``None`` (treated as empty, so no bypass for
    special values). Both the position axis and the tilt axis pass the special
    set [0, 100] so fully-open/closed targets are never gated (issue #629).
    ``axis_label`` appears in debug log lines for readability.
    """
    _special = special_positions or []

    if position is None:
        return True  # Unknown position — send command to be safe

    if sun_just_appeared:
        logger.debug(
            "Delta check bypassed (sun appeared): %s current=%s target=%s",
            entity,
            position,
            target,
        )
        return True

    if target in _special:
        logger.debug("Delta check bypassed (special target %s): %s", target, entity)
        return True

    if position in _special:
        logger.debug("Delta check bypassed (special current %s): %s", position, entity)
        return True

    delta = abs(position - target)
    passes = delta >= min_change
    logger.debug(
        "%s delta check: %s current=%s target=%s delta=%s min=%s pass=%s",
        axis_label,
        entity,
        position,
        target,
        delta,
        min_change,
        passes,
    )
    return passes


def check_time_delta(
    entity: str,
    time_threshold: int,
    *,
    last_updated: dt.datetime | None,
    logger,
) -> bool:
    """Return True if enough time has passed since last command."""
    if last_updated is None:
        return True
    elapsed = dt.datetime.now(dt.UTC) - last_updated
    passes = elapsed >= dt.timedelta(minutes=time_threshold)
    logger.debug(
        "Time delta check: %s elapsed=%s threshold=%smin pass=%s",
        entity,
        elapsed,
        time_threshold,
        passes,
    )
    return passes


def elapsed_minutes(last_updated: dt.datetime | None) -> float | None:
    """Return minutes elapsed since ``last_updated``, or None when unknown."""
    if last_updated is None:
        return None
    elapsed = dt.datetime.now(dt.UTC) - last_updated
    return round(elapsed.total_seconds() / 60, 2)
