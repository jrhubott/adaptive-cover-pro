"""Shared pipeline computation helpers.

These module-level functions eliminate copy-paste of the most repeated
patterns across pipeline handlers:

- ``apply_snapshot_limits``    — apply position limits using config from the snapshot
- ``compute_solar_position``   — calculate_raw_percentage() + floor-at-1 + limits
- ``compute_default_position`` — default_position + limits (sun not in FOV)
- ``compute_default_tilt``     — sunset_tilt/default_tilt + the #503 tilt clamp

Floor-mode composition (the former ``apply_minimum_mode`` semantic) now
lives in :mod:`pipeline.floors` and runs as a post-decision pass in the
registry — see issue #463.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..const import DEFAULT_SNAP_CLOSED_THRESHOLD, SOLAR_ANTICIPATION_SAMPLES
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


def apply_snapshot_tilt_limits(
    snapshot: PipelineSnapshot,
    value: int,
    *,
    sun_valid: bool,
) -> int:
    """Apply the configured min/max tilt limits from *snapshot*.

    The tilt-axis mirror of :func:`apply_snapshot_limits`: one adapter that
    knows which four snapshot fields make up a tilt band, so no caller has to
    spell out the five-argument
    :meth:`PositionConverter.apply_tilt_limits` call itself. Extracted when
    ``resolve_cloudy_tilt`` became a second caller (#175) — the position axis
    had a snapshot adapter from the start and the tilt axis did not, which is
    the asymmetry this closes rather than widens.

    Args:
        snapshot: Current pipeline snapshot (provides the tilt band).
        value:    Raw tilt (0–100) to constrain.
        sun_valid: Whether the sun is currently in the valid tracking zone.
            The ``*_sun_only`` flags are enforced only while this is True.

    Returns:
        Constrained tilt value (0–100).

    """
    return PositionConverter.apply_tilt_limits(
        value,
        snapshot.min_tilt,
        snapshot.max_tilt,
        snapshot.min_tilt_sun_only,
        snapshot.max_tilt_sun_only,
        sun_valid=sun_valid,
    )


def resolve_cloudy_tilt(snapshot: PipelineSnapshot) -> int | None:
    """Effective cloud-suppression slat angle, or None when unset (issue #175).

    Single source of truth for the cloud tilt, so every branch of
    ``CloudSuppressionHandler`` that answers with a configured cloud override
    resolves it the same way.

    Returns ``None`` whenever the option is absent — there is no
    ``DEFAULT_CLOUDY_TILT`` and no fallback to ``default_tilt``. That is the
    invariant the whole option rests on: an install that never opened the Light
    & Cloud step keeps naming no tilt on the cloudy branch, so the slats hold
    exactly as they did before #175 and no config migration is needed. An
    explicit ``0`` is a real answer (slats closed), which is why the check is
    ``is not None`` and not a truthiness test.

    A configured angle is clamped to the global tilt band with
    ``sun_valid=False``, matching how ``default_tilt`` is treated (#503) and
    how ``cloudy_position`` — this value's own position sibling, resolved on
    the very same handler branch — is already treated. The #128 sunset bypass
    does not apply: sunset is an explicit nighttime carve-out, a cloudy hold is
    a daytime state.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Constrained cloud tilt, or None when no cloud tilt is configured.

    """
    options = snapshot.climate_options
    tilt = options.cloudy_tilt if options is not None else None
    if tilt is None:
        return None
    return apply_snapshot_tilt_limits(snapshot, tilt, sun_valid=False)


def solar_position_from_geometry(
    cover: AdaptiveGeneralCover,
    config: CoverConfig,
    *,
    minimize_movements: bool,
    max_coverage_steps: int,
    policy: CoverTypePolicy | None,
    floor_active: bool = True,
    snap_closed_below: bool = False,
    snap_closed_threshold: int = DEFAULT_SNAP_CLOSED_THRESHOLD,
) -> int:
    """Sun-tracked position from raw geometry, with all standard transforms.

    Snapshot-free single source of truth for the solar branch, shared by the
    live pipeline (:func:`compute_solar_position`), the anticipation look-ahead
    (:func:`anticipated_solar_position_from_geometry`), and the forecast:

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
    3. Optionally collapses a small non-zero demand to the closed endpoint
       (declutter snap, opt-in — issue #1379). Runs on the quantized value from
       step 2 and before the floor/limit clamp below, so a live active
       min_pos/min_pos_sun_tracking floor — or a post-decision axis constraint
       — always wins over the snap via ``clamp_to_bounds``'s existing
       floor-wins-on-conflict rule (``pipeline/axis_constraints.py``). Position
       axis only: gated on the SAME *pivot* step 2's quantizer reads —
       ``cover_tilt`` / ``cover_louvered_roof`` declare TILT at ``axes[0]``,
       not position, so ``policy is not None`` alone cannot tell them apart
       from a real position axis; a non-``None`` pivot means a bi-directional
       axis and is an unconditional bail-out inside the snap itself (audit
       fix, issue #1379 — keeps the MODE2 pivot logic in step 2 untouched,
       issues #1104/#1107).
    4. Floors at ``SOLAR_TRACKING_FLOOR_PCT`` (1 %) so open/close-only covers
       never close while the sun is still in the field of view — but only when
       ``floor_active``. Set-position-capable instances pass ``floor_active``
       False so the cover can reach a true 0 % (issue #569).
    5. Applies the configured min/max position limits (``sun_valid=True``).

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
        # The bi-directional-axis discriminator (issue #1104): ``None`` means
        # monotonic (the position axis — a bi-directional engine always
        # reports a real numeric pivot instead, even for a nominally-monotonic
        # tilt calibration like MODE1, per
        # ``AdaptiveTiltCover.coverage_pivot_percentage``). Resolved once here
        # and shared by the quantizer AND the declutter snap below, since
        # ``cover_tilt`` / ``cover_louvered_roof`` declare TILT at
        # ``axes[0]`` — ``policy is not None`` alone cannot tell a real
        # position axis apart from those (issue #1379 audit fix).
        pivot = cover.coverage_pivot_percentage()
    else:
        state = int(round(pct))
        pivot = None
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
            pivot=pivot,
            bounds=cover.coverage_travel_bounds(),
        )
    if policy is not None:
        # Position-axis-only declutter snap (issue #1379): gated on the SAME
        # pivot the quantizer reads above, not merely on ``policy is not
        # None`` — a bi-directional axis (a real numeric pivot) is an
        # unconditional bail-out inside ``snap_closed_below_threshold``
        # itself, regardless of which axis a policy happens to declare at
        # ``axes[0]``.
        state = PositionConverter.snap_closed_below_threshold(
            state,
            snap_closed_threshold,
            enabled=snap_closed_below,
            full_coverage_at_zero=full_coverage_at_zero,
            pivot=pivot,
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
        snap_closed_below=getattr(snapshot, "snap_closed_below", False),
        snap_closed_threshold=getattr(
            snapshot, "snap_closed_threshold", DEFAULT_SNAP_CLOSED_THRESHOLD
        ),
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
    snap_closed_below: bool = False,
    snap_closed_threshold: int = DEFAULT_SNAP_CLOSED_THRESHOLD,
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
        snap_closed_below=snap_closed_below,
        snap_closed_threshold=snap_closed_threshold,
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
            snap_closed_below=snap_closed_below,
            snap_closed_threshold=snap_closed_threshold,
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
        snap_closed_below=getattr(snapshot, "snap_closed_below", False),
        snap_closed_threshold=getattr(
            snapshot, "snap_closed_threshold", DEFAULT_SNAP_CLOSED_THRESHOLD
        ),
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


def compute_default_tilt(snapshot: PipelineSnapshot) -> int | None:
    """Effective default/sunset tilt for the live pipeline (issue #1214).

    Single source of truth for resolving ``sunset_tilt``/``default_tilt`` —
    extracted from ``DefaultHandler`` so every handler branch that resolves
    its position *from* the default position can also supply the default tilt
    that pairs with it, without any of them needing to know each other's tilt
    (issue #1153's ``_MERGEABLE`` seam stays closed — each handler states its
    own tilt as the winner).

    **Deciding whether a branch qualifies is the caller's job, not this
    helper's.** A handler knows structurally which of its own branches it
    took and whether that branch resolved from the default; it calls this on
    those branches and passes ``tilt=None`` on the others. That is not a
    duplication of policy — the shared part (sunset precedence, the #503
    clamp, the #128 carve-out) lives here, and each handler owning its own
    output is exactly the #1153 principle. Concretely:

    * ``DefaultHandler`` — every branch, including "Use My at sunset", which
      substitutes the *position* only.
    * ``CloudSuppressionHandler`` — the sunset and no-``cloudy_position``
      branches; the ``cloudy_position`` branch is a configured override, so it
      asks :func:`resolve_cloudy_tilt` instead and stays untilted unless the
      user configured a ``cloudy_tilt`` (#175). It never falls back here: a
      branch that did not resolve from the default must not borrow the
      default's tilt.
    * ``MotionTimeoutHandler`` — the return-to-default branch; the
      ``hold_position`` branch stays untilted.
    * ``ClimateHandler`` — the ``ControlMethod.DEFAULT`` branches whose
      matched rule reports ``ClimateRule.resolves_default_position``.

    Note that qualification is about PROVENANCE, never about whether the
    branch's number happens to equal ``compute_default_position(snapshot)``.
    A limit clamp can move a genuine default answer away from that value
    (``min_position`` versus the sunset bypass, #128), and a configured
    override can coincide with it — so a value comparison is wrong in both
    directions.

    Sunset tilt (and its default_tilt fallback) is a deliberate carve-out and
    stays UNclamped, mirroring the sunset *position* bypass (#128). Only the
    non-sunset default_tilt honors the global min_tilt/max_tilt clamp (#503)
    — exactly as default *position* is clamped. Returns None for cover types
    that never configure a tilt (default_tilt/sunset_tilt both None), which
    is a natural no-op — no cover-type string branch needed.

    Args:
        snapshot: Current pipeline snapshot.

    Returns:
        Effective default/sunset tilt, or None when neither is configured.

    """
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
            tilt = apply_snapshot_tilt_limits(snapshot, tilt, sun_valid=False)
    return tilt
