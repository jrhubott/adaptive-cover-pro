"""Shared pipeline computation helpers.

These module-level functions eliminate copy-paste of the most repeated
patterns across pipeline handlers:

- ``apply_snapshot_limits``    — apply position limits using config from the snapshot
- ``compute_solar_position``   — calculate_percentage() + floor-at-1 + limits
- ``compute_default_position`` — default_position + limits (sun not in FOV)
- ``apply_minimum_mode``       — pick max(configured, raw) when min-mode is on,
                                 else just the configured value, plus a
                                 reason-string suffix for diagnostics
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
    if snapshot.cover.direct_sun_valid and snapshot.enable_sun_tracking:
        return compute_solar_position(snapshot)
    if snapshot.is_sunset_active:
        return snapshot.default_position
    return apply_snapshot_limits(
        snapshot,
        snapshot.default_position,
        sun_valid=False,
    )


def apply_minimum_mode(
    configured: int,
    raw: int,
    *,
    enabled: bool,
) -> tuple[int, str]:
    """Pick a position from the configured/raw pair under min-mode rules.

    Several override handlers (force, weather, custom-position) support a
    "minimum-mode" toggle: when on, the override acts as a *floor* under the
    sun-tracked position rather than a hard target — covers go to whichever
    is more closed (the configured override, or what solar tracking would do
    anyway).  When off, the configured override is used as-is.

    Args:
        configured: User-configured override position (0–100).
        raw:        The sun-tracked position the handler would otherwise yield.
        enabled:    Whether minimum-mode is active for this handler instance.

    Returns:
        ``(position, reason_suffix)`` — the position to send and a short
        diagnostic suffix to append to the handler's reason string. Suffix is
        empty when minimum-mode is off so the reason reads cleanly.

    """
    if enabled:
        return (
            max(configured, raw),
            f" [minimum mode — floor {configured}%, calculated {raw}%]",
        )
    return configured, ""


def compute_default_position(snapshot: PipelineSnapshot) -> int:
    """Return the effective default position with limits applied.

    Uses ``snapshot.default_position`` (the sunset-aware single source of truth)
    and applies configured min/max position limits with ``sun_valid=False`` so
    sun-only limits are not enforced when the sun is outside the FOV.

    When ``snapshot.is_sunset_active`` is True, limits are bypassed entirely —
    the sunset position is an explicit user configuration for nighttime and
    should not be clamped by min/max safety limits (#128).

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Effective default position (0–100, limited).

    """
    if snapshot.is_sunset_active:
        return snapshot.default_position
    return apply_snapshot_limits(
        snapshot,
        snapshot.default_position,
        sun_valid=False,
    )
