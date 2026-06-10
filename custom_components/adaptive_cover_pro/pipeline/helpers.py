"""Shared pipeline computation helpers.

These module-level functions eliminate copy-paste of the most repeated
patterns across pipeline handlers:

- ``apply_snapshot_limits``    — apply position limits using config from the snapshot
- ``compute_solar_position``   — calculate_percentage() + floor-at-1 + limits
- ``compute_default_position`` — default_position + limits (sun not in FOV)

Floor-mode composition (the former ``apply_minimum_mode`` semantic) now
lives in :mod:`pipeline.floors` and runs as a post-decision pass in the
registry — see issue #463.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..position_utils import PositionConverter
from .types import PipelineSnapshot

if TYPE_CHECKING:
    from ..config_types import CoverConfig
    from ..cover_types.base import CoverTypePolicy
    from ..engine.covers.base import AdaptiveGeneralCover


# The minimum sun-tracked position (%) for open/close-only covers. Keeping a
# binary cover at >= 1 % stops it from fully retracting while the sun is still
# in the field of view (a 0 % command means "open/retract" on a cover with no
# set_position). Set-position-capable covers can reach a true 0 %, so the
# floor is gated off for them at instance compute time (issue #569).
SOLAR_TRACKING_FLOOR_PCT = 1


def solar_floor(value: int, *, floor_active: bool) -> int:
    """Apply the sun-tracking minimum-position floor when *floor_active*.

    Single source of truth for the former ``max(state, 1)`` clamp that lived
    in both :func:`solar_position_from_geometry` and the glare-zone handler.
    When ``floor_active`` is False (every bound entity supports set_position),
    the value passes through untouched so the cover can reach a true 0 %.
    """
    if floor_active:
        return max(value, SOLAR_TRACKING_FLOOR_PCT)
    return value


def apply_config_limits(
    value: int,
    config: CoverConfig,
    *,
    sun_valid: bool,
) -> int:
    """Apply the configured min/max position limits from a bare ``CoverConfig``.

    The single point where the five limit fields are unpacked into
    ``PositionConverter.apply_limits()``. Snapshot-free so both the live
    pipeline (via :func:`apply_snapshot_limits`) and the forecast (which has no
    snapshot) share the exact same clamping.

    Args:
        value:     Raw position (0–100) to constrain.
        config:    Cover configuration providing the limit fields.
        sun_valid: Whether the sun is currently in the valid tracking zone.

    Returns:
        Constrained position value (0–100).

    """
    return PositionConverter.apply_limits(
        value,
        config.min_pos,
        config.max_pos,
        config.min_pos_sun_only,
        config.max_pos_sun_only,
        sun_valid,
        sun_tracking_min_pos=config.min_pos_sun_tracking,
    )


def apply_snapshot_limits(
    snapshot: PipelineSnapshot,
    value: int,
    *,
    sun_valid: bool,
) -> int:
    """Apply the configured min/max position limits from *snapshot*.

    Thin adapter over :func:`apply_config_limits` using the snapshot's config.

    Args:
        snapshot: Current pipeline snapshot (provides config limits).
        value:    Raw position (0–100) to constrain.
        sun_valid: Whether the sun is currently in the valid tracking zone.

    Returns:
        Constrained position value (0–100).

    """
    return apply_config_limits(value, snapshot.config, sun_valid=sun_valid)


def solar_position_from_geometry(
    cover: AdaptiveGeneralCover,
    config: CoverConfig,
    *,
    minimize_movements: bool,
    max_coverage_steps: int,
    policy: CoverTypePolicy | None,
    floor_active: bool = True,
) -> int:
    """Sun-tracked position from raw geometry, with all standard transforms.

    Snapshot-free single source of truth for the solar branch, shared by the
    live pipeline (:func:`compute_solar_position`) and the forecast:

    1. Calls ``cover.calculate_percentage()`` (pure geometry), rounded.
    2. Optionally quantizes into the configured number of discrete coverage
       levels (movement minimization — opt-in, rounds toward coverage).
    3. Floors at ``SOLAR_TRACKING_FLOOR_PCT`` (1 %) so open/close-only covers
       never close while the sun is still in the field of view — but only when
       ``floor_active``. Set-position-capable instances pass ``floor_active``
       False so the cover can reach a true 0 % (issue #569).
    4. Applies the configured min/max position limits (``sun_valid=True``).

    Should only be called when ``cover.direct_sun_valid`` is True.

    Returns:
        Sun-tracked position (0–100; >= 1 only when ``floor_active``), limited.

    """
    state = int(round(cover.calculate_percentage()))
    if minimize_movements and policy is not None:
        state = PositionConverter.quantize_to_coverage_steps(
            state,
            max_coverage_steps,
            full_coverage_at_zero=not policy.axes[0].open_blocks_sun,
        )
    state = solar_floor(state, floor_active=floor_active)
    return apply_config_limits(state, config, sun_valid=True)


def compute_solar_position(snapshot: PipelineSnapshot) -> int:
    """Sun-tracked position for the live pipeline — adapter over the primitive.

    Should only be called when ``snapshot.cover.direct_sun_valid`` is True.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Sun-tracked position (>= 1 only when ``snapshot.solar_floor_active``),
        then limited.

    """
    return solar_position_from_geometry(
        snapshot.cover,
        snapshot.config,
        minimize_movements=getattr(snapshot, "minimize_movements", False),
        max_coverage_steps=getattr(snapshot, "max_coverage_steps", 1),
        policy=getattr(snapshot, "policy", None),
        floor_active=getattr(snapshot, "solar_floor_active", True),
    )


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


def default_position_with_limits(
    default_pos: int,
    config: CoverConfig,
    *,
    is_sunset_active: bool,
) -> int:
    """Effective default position with limits applied — snapshot-free primitive.

    Applies the configured min/max limits with ``sun_valid=False`` so sun-only
    limits are not enforced when the sun is outside the FOV. When
    *is_sunset_active* is True the limits are bypassed entirely — the sunset
    position is an explicit user configuration for nighttime and should not be
    clamped by min/max safety limits (#128).

    Shared by the live pipeline (:func:`compute_default_position`) and the
    forecast.

    Returns:
        Effective default position (0–100, limited).

    """
    if is_sunset_active:
        return default_pos
    return apply_config_limits(default_pos, config, sun_valid=False)


def compute_default_position(snapshot: PipelineSnapshot) -> int:
    """Effective default position for the live pipeline — adapter over primitive.

    Uses ``snapshot.default_position`` (the sunset-aware single source of truth).

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Effective default position (0–100, limited).

    """
    return default_position_with_limits(
        snapshot.default_position,
        snapshot.config,
        is_sunset_active=snapshot.is_sunset_active,
    )
