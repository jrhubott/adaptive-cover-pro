"""Shared pipeline computation helpers.

These module-level functions eliminate copy-paste of the three most repeated
patterns across pipeline handlers:

- ``apply_snapshot_limits`` — apply position limits using config from the snapshot
- ``compute_solar_position``  — calculate_percentage() + floor-at-1 + limits
- ``compute_default_position`` — default_position + limits (sun not in FOV)
"""

from __future__ import annotations

from ..position_utils import PositionConverter
from .types import PipelineSnapshot


def apply_snapshot_limits(
    snapshot: PipelineSnapshot,
    value: int,
    *,
    sun_valid: bool,
) -> int:
    """Apply the configured min/max position limits from *snapshot*.

    Replaces the 6-argument ``PositionConverter.apply_limits()`` call that was
    copy-pasted into every handler.

    Args:
        snapshot: Current pipeline snapshot (provides config limits).
        value:    Raw position (0–100) to constrain.
        sun_valid: Whether the sun is currently in the valid tracking zone.

    Returns:
        Constrained position value (0–100).

    """
    return PositionConverter.apply_limits(
        value,
        snapshot.config.min_pos,
        snapshot.config.max_pos,
        snapshot.config.min_pos_sun_only,
        snapshot.config.max_pos_sun_only,
        sun_valid,
    )


def compute_solar_position(snapshot: PipelineSnapshot) -> int:
    """Return the sun-tracked position with all standard transforms applied.

    1. Calls ``cover.calculate_percentage()`` (pure geometry).
    2. Floors the result at 1 % so open/close-only covers never close while
       the sun is still in the field of view.
    3. Applies the configured min/max position limits.

    Should only be called when ``snapshot.cover.direct_sun_valid`` is True.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Sun-tracked position (1–100 after floor, then limited).

    """
    state = int(round(snapshot.cover.calculate_percentage()))
    state = max(state, 1)
    return apply_snapshot_limits(snapshot, state, sun_valid=True)


def compute_raw_calculated_position(snapshot: PipelineSnapshot) -> int:
    """Return the raw geometric position for diagnostics.

    This is what the ``SolarHandler`` would compute when direct sun is valid,
    or the effective default when the sun is outside the FOV.  Used by
    overriding handlers (manual, motion, force, weather, climate) so that
    the ``raw_calculated_position`` field on ``PipelineResult`` always reflects
    the true sun-geometry result, independent of which handler claimed the
    position.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Solar-tracked position (1–100) when sun is valid, else effective default.

    """
    if snapshot.cover.direct_sun_valid:
        return compute_solar_position(snapshot)
    return apply_snapshot_limits(
        snapshot,
        snapshot.default_position,
        sun_valid=False,
    )


def compute_default_position(snapshot: PipelineSnapshot) -> int:
    """Return the effective default position with limits applied.

    Uses ``snapshot.default_position`` (the sunset-aware single source of truth)
    and applies configured min/max position limits with ``sun_valid=False`` so
    sun-only limits are not enforced when the sun is outside the FOV.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Effective default position (0–100, limited).

    """
    return apply_snapshot_limits(
        snapshot,
        snapshot.default_position,
        sun_valid=snapshot.cover.direct_sun_valid,
    )
