"""Shared pipeline computation helpers.

These module-level functions eliminate copy-paste of the most repeated
patterns across pipeline handlers:

- ``apply_snapshot_limits``    — apply position limits using config from the snapshot
- ``compute_solar_position``   — calculate_raw_percentage() + floor-at-1 + limits
- ``compute_default_position`` — default_position + limits (sun not in FOV)

Floor-mode composition (the former ``apply_minimum_mode`` semantic) now
lives in :mod:`pipeline.floors` and runs as a post-decision pass in the
registry — see issue #463.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..const import SOLAR_ANTICIPATION_SAMPLES
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
    suppress_sun_tracking_min: bool = False,
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
        suppress_sun_tracking_min: When True, the sun-in-FOV min floor is
            ignored and the effective minimum falls back to ``min_pos`` — used
            by summer climate-close to reach the global min (issue #689).
            Defaults to False so all other callers are unchanged.

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
        suppress_sun_tracking_min=suppress_sun_tracking_min,
    )


def apply_snapshot_limits(
    snapshot: PipelineSnapshot,
    value: int,
    *,
    sun_valid: bool,
    suppress_sun_tracking_min: bool = False,
) -> int:
    """Apply the configured min/max position limits from *snapshot*.

    Thin adapter over :func:`apply_config_limits` using the snapshot's config.

    Args:
        snapshot: Current pipeline snapshot (provides config limits).
        value:    Raw position (0–100) to constrain.
        sun_valid: Whether the sun is currently in the valid tracking zone.
        suppress_sun_tracking_min: When True, the sun-in-FOV min floor is
            ignored and the effective minimum falls back to ``min_pos``
            (issue #689). Defaults to False so all other callers are unchanged.

    Returns:
        Constrained position value (0–100).

    """
    return apply_config_limits(
        value,
        snapshot.config,
        sun_valid=sun_valid,
        suppress_sun_tracking_min=suppress_sun_tracking_min,
    )


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

    1. Calls ``cover.calculate_raw_percentage()`` (pure geometry, unrounded float),
       then quantizes toward full coverage (issue #978) via the engine's
       ``round_toward_coverage`` hook. *policy* supplies the axis-level direction
       — floor() when 0% is full coverage (blind/tilt/venetian), ceil() when 100%
       is (awning) — and the engine refines it where coverage is not monotonic in
       the percentage: a bi-directional slat axis rounds away from horizontal in
       whichever arithmetic direction that is (issue #1090). Falls back to round()
       when policy is None.
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
    pct = cover.calculate_raw_percentage()
    if policy is not None:
        # full_coverage_at_zero=True means 0% = closed = full coverage (blind/tilt/venetian)
        # → round DOWN (floor) toward 0 to keep more coverage.
        # full_coverage_at_zero=False means 100% = extended = full coverage (awning)
        # → round UP (ceil) toward 100 to keep more coverage.
        #
        # That axis-level answer is only the whole story when coverage is
        # MONOTONIC in the percentage. A bi-directional slat axis (MODE2 tilt,
        # the shipped default) peaks in openness at horizontal and closes again
        # past it, so the engine gets the final say via the polymorphic
        # ``round_toward_coverage`` hook — see issue #1090. The engine, not this
        # module, is the only object that knows the tilt scale, which keeps the
        # cover-type branch out of the pipeline layer.
        full_coverage_at_zero = not policy.axes[0].open_blocks_sun
        state = cover.round_toward_coverage(
            pct, full_coverage_at_zero=full_coverage_at_zero
        )
    else:
        state = int(round(pct))
    if minimize_movements and policy is not None:
        # Same division of labour as step 1, one step later: the axis states
        # which end blocks the sun, and the engine says whether that is the
        # whole story. A bi-directional slat hands back its horizontal pivot,
        # and the quantiser then measures the coverage demand from THERE — so
        # the movement-minimisation levels sit on the side the solve is already
        # on instead of being scaled against the far end of the travel, which
        # would snap the slats back toward horizontal and undo the away-from-
        # horizontal rounding above (issues #1090, #1104).
        #
        # The engine also says how far that side actually goes. This quantiser
        # is the LAST thing to touch a tilt axis — ``apply_config_limits`` below
        # carries ``min_pos``/``max_pos``, not ``[min_tilt, max_tilt]`` — so
        # levels scaled to the end of the SCALE rather than to the end of the
        # BAND would command straight past the user's own cap, one step after
        # ``round_toward_coverage`` re-banded to stay inside it.
        state = PositionConverter.quantize_to_coverage_steps(
            state,
            max_coverage_steps,
            full_coverage_at_zero=full_coverage_at_zero,
            pivot=cover.coverage_pivot_percentage(),
            bounds=cover.coverage_travel_bounds(),
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


def anticipated_solar_position_from_geometry(
    cover: AdaptiveGeneralCover,
    config: CoverConfig,
    *,
    horizon_minutes: int,
    minimize_movements: bool,
    max_coverage_steps: int,
    policy: CoverTypePolicy | None,
    floor_active: bool = True,
) -> int:
    """Most-protective sun-tracked position across an upcoming look-ahead window.

    Snapshot-free single source of truth for the anticipation algorithm,
    shared by the live pipeline (:func:`anticipated_solar_position`) and the
    forecast (:func:`..forecast.build_forecast`) — see issue #1091, the
    forecast/live drift this extraction closes (the forecast previously called
    the plain :func:`solar_position_from_geometry` with no look-ahead at all).

    The live solar target is computed from the *current* sun position, but the
    "Minimum interval between position changes" (``CONF_DELTA_TIME``, minutes)
    holds any queued move for that long. While it is held the sun keeps moving,
    so a position that just covers "now" can drift out of coverage before the
    next allowed move. This helper looks ahead across
    ``(now, now + horizon_minutes]`` and returns the most-protective
    sun-tracked position needed anywhere in that window, so coverage is
    guaranteed until the cover is next allowed to move (issue #616).

    The look-ahead reuses the forecast's sampling machinery: future sun angles
    come from ``cover.sun_data`` (the per-day 5-minute table) via
    ``forecast._nearest_index``, each sample is a ``dataclasses.replace`` of the
    live cover with the projected angles and ``eval_time`` (so the sunset gate
    evaluates at the projected moment), and only samples where the sun is still
    ``direct_sun_valid`` contribute. Each candidate is fully transformed by
    :func:`solar_position_from_geometry` (quantize → floor → limits) before
    comparison, then folded through the cover-type-polymorphic
    :meth:`CoverTypePolicy.more_protective_position` comparator. The live
    "now" target seeds the fold, so the result can only ever *increase*
    protection relative to the current position.

    The cover goes to the comparator alongside the two positions, because
    "which of these blocks more sun" is only a ``min``/``max`` on the percentage
    while coverage is MONOTONIC in it. On a bi-directional slat the fold would
    otherwise discard the more-protective of two above-horizontal samples and
    quietly WEAKEN the anticipation guarantee it exists to strengthen — see
    issue #1104.

    When ``horizon_minutes <= 0`` (anticipation disabled / no throttle) or
    *policy* is ``None``, this is exactly :func:`solar_position_from_geometry`.

    Should only be called when ``cover.direct_sun_valid`` is True.

    Returns:
        The most-protective sun-tracked position (limited) across the window.

    """
    live = solar_position_from_geometry(
        cover,
        config,
        minimize_movements=minimize_movements,
        max_coverage_steps=max_coverage_steps,
        policy=policy,
        floor_active=floor_active,
    )

    if horizon_minutes <= 0 or policy is None:
        return live

    # Lazy import: ``forecast`` imports from this module, so importing it at
    # module scope would be circular.
    from ..forecast import _nearest_index

    sun_data = cover.sun_data
    times = list(sun_data.times)
    if not times:
        return live

    azimuths = sun_data.solar_azimuth
    elevations = sun_data.solar_elevation

    now = getattr(cover, "eval_time", None) or datetime.now(UTC)

    best = live
    seen_indices: set[int] = set()
    for n in range(1, SOLAR_ANTICIPATION_SAMPLES + 1):
        fraction = n / SOLAR_ANTICIPATION_SAMPLES
        sample_time = now + timedelta(minutes=horizon_minutes * fraction)
        idx = _nearest_index(times, sample_time)
        if idx is None or idx in seen_indices:
            continue
        seen_indices.add(idx)

        future = dataclasses.replace(
            cover,
            sol_azi=float(azimuths[idx]),
            sol_elev=float(elevations[idx]),
        )
        future.eval_time = times[idx]
        if not future.direct_sun_valid:
            continue

        candidate = solar_position_from_geometry(
            future,
            config,
            minimize_movements=minimize_movements,
            max_coverage_steps=max_coverage_steps,
            policy=policy,
            floor_active=floor_active,
        )
        # The LIVE cover is the right engine handle even though *candidate* came
        # from a projected one: the comparator only asks where this axis's
        # coverage bottoms out, which is a property of the tilt scale, and
        # ``dataclasses.replace`` above changes only the sun angles.
        best = policy.more_protective_position(best, candidate, cover=cover)

    return best


def anticipated_solar_position(snapshot: PipelineSnapshot) -> int:
    """Most-protective sun-tracked position across the upcoming throttle window.

    Thin adapter over :func:`anticipated_solar_position_from_geometry` using
    the snapshot's cover, config, and horizon (``time_threshold_minutes``).

    Should only be called when ``snapshot.cover.direct_sun_valid`` is True.

    Returns:
        The most-protective sun-tracked position (limited) across the window.

    """
    return anticipated_solar_position_from_geometry(
        snapshot.cover,
        snapshot.config,
        horizon_minutes=getattr(snapshot, "time_threshold_minutes", 0),
        minimize_movements=getattr(snapshot, "minimize_movements", False),
        max_coverage_steps=getattr(snapshot, "max_coverage_steps", 1),
        policy=getattr(snapshot, "policy", None),
        floor_active=getattr(snapshot, "solar_floor_active", True),
    )


def compute_raw_calculated_position(snapshot: PipelineSnapshot) -> int:
    """Return the commanded solar position for diagnostics.

    This is what the ``SolarHandler`` would command when direct sun is valid —
    the *anticipated* solar position (the most-protective value across the
    upcoming throttle window, issue #616) — or the effective default when the
    sun is outside the FOV.  Used by overriding handlers (manual, motion,
    force, weather, climate) so that the ``raw_calculated_position`` field on
    ``PipelineResult`` always reflects the commanded solar truth, independent
    of which handler claimed the position.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Solar-tracked position (1–100) when sun is valid, else effective default.

    """
    if snapshot.cover.direct_sun_valid and snapshot.enable_sun_tracking:
        return anticipated_solar_position(snapshot)
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


def compute_default_tilt(
    snapshot: PipelineSnapshot, *, position: int | None = None
) -> int | None:
    """Effective default/sunset tilt for the live pipeline (issue #1214).

    Single source of truth for resolving ``sunset_tilt``/``default_tilt`` —
    extracted from ``DefaultHandler`` so every handler that answers with the
    *effective default position* (``compute_default_position``) can also
    supply the *effective default tilt* that pairs with it, without any of
    them needing to know each other's tilt (issue #1153's ``_MERGEABLE`` seam
    stays closed — each handler states its own tilt as the winner).

    ``position`` is the caller's OWN resolved position for the branch it is
    calling from. When given, the tilt is only returned if ``position``
    equals ``compute_default_position(snapshot)`` — i.e. the caller is
    genuinely AT the effective default position, not merely sharing a
    control-method label with a handler that is. This is the gate that keeps
    the tilt off a ``CloudSuppressionHandler`` ``cloudy_position`` branch
    (issue #1214 finding 1 — that position is a configured override, not the
    default) and off a tilt-primary ``ClimateHandler`` LOW_LIGHT branch that
    resolved via ``_solar`` rather than ``_default`` (issue #1214 finding 2 —
    ``ControlMethod.DEFAULT`` labels the branch, not the position; see the
    class docstring on ``ClimateHandler``). A coincidental match — e.g. a
    ``cloudy_position`` or solar-tracked position that happens to equal the
    default — is treated as a real match: the cover genuinely is sitting at
    the default position, so pairing it with the default tilt is correct.

    Omit ``position`` (the default, ``None``) to skip the gate entirely —
    for a caller that is unconditionally at the default position by
    construction, such as ``DefaultHandler``, which stays at its default
    even on its "Use My at sunset" branch that substitutes a different
    *position* value but still means "the default handler won, use its
    tilt."

    Sunset tilt (and its default_tilt fallback) is a deliberate carve-out and
    stays UNclamped, mirroring the sunset *position* bypass (#128). Only the
    non-sunset default_tilt honors the global min_tilt/max_tilt clamp (#503)
    — exactly as default *position* is clamped. Returns None for cover types
    that never configure a tilt (default_tilt/sunset_tilt both None), which
    is a natural no-op — no cover-type string branch needed.

    Args:
        snapshot: Current pipeline snapshot.
        position: The calling handler's own resolved position, or None to
            skip the position-match gate (see above).

    Returns:
        Effective default/sunset tilt, or None when neither is configured,
        or when ``position`` does not match the effective default position.

    """
    if position is not None and position != compute_default_position(snapshot):
        return None
    tilt: int | None
    if snapshot.is_sunset_active:
        tilt = (
            snapshot.sunset_tilt
            if snapshot.sunset_tilt is not None
            else snapshot.default_tilt
        )
    else:
        tilt = snapshot.default_tilt
        if tilt is not None:
            tilt = PositionConverter.apply_tilt_limits(
                tilt,
                snapshot.min_tilt,
                snapshot.max_tilt,
                snapshot.min_tilt_sun_only,
                snapshot.max_tilt_sun_only,
                sun_valid=False,
            )
    return tilt
