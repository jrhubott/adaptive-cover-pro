"""Position calculation utilities for Adaptive Cover Pro."""

from __future__ import annotations

import math

import numpy as np

from .const import (
    VENETIAN_TILT_TRANSFORM_CLAMP,
    VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
)


def inverse_state(state: int) -> int:
    """Inverse state."""
    return 100 - state


def flip_if(value: int, *, inverted: bool) -> int:
    """Map a position between the cover frame and the logical frame.

    Single source of truth for the ``inverse_state(v) if inverted else v``
    conditional (issues #1036 / #1042). The involution ``100 - x`` is its own
    inverse, so ONE primitive serves both directions:

    * **cover → logical** — a producer that reads a physical position off an
      entity and hands it to a logical-frame field.
      ``PipelineResult.position`` is logical for every winner, so a handler
      holding a raw read (``MotionTimeoutHandler``'s hold,
      ``GroupLockHandler``'s freeze) must convert first, and the registry must
      convert before comparing a held position against a user-configured bound.
    * **logical → cover** — a consumer turning a pipeline value into the number
      actually dispatched to, or recorded as held on, the entity.

    The name is deliberately direction-free: while this helper carried a
    directional name, every caller converting the other way hand-rolled the
    same ternary instead of delegating. Lives beside :func:`inverse_state` in this
    pure module so the arithmetic is written once and the pipeline layer can
    reach it without importing a manager. The caller supplies the "is this axis
    inverted right now?" answer, which is itself sourced from
    ``cover_types.base.axis_inverted``.
    """
    return inverse_state(value) if inverted else value


def _proportional_remap(value: int, lo: int, hi: int) -> int:
    """Linearly remap the full 0–100% demand onto the ``[lo, hi]`` band (#957).

    ``transformed = round(lo + (hi - lo) * (value / 100))`` — monotonic,
    order-preserving, and the identity when ``[lo, hi] == [0, 100]``. The input
    is clipped to ``[0, 100]`` first; ``None`` bounds fall back to the full
    range (0 floor, 100 ceiling), matching the clamp path's semantics.
    """
    v = min(100, max(0, value))
    lo_eff = 0 if lo is None else lo
    hi_eff = 100 if hi is None else hi
    return round(lo_eff + (hi_eff - lo_eff) * (v / 100))


def interpolate_position(
    state: float,
    start_value: float | None,
    end_value: float | None,
    normal_list: list | None,
    new_list: list | None,
) -> float:
    """Interpolate state using custom ranges.

    Maps position from normal range to custom range using linear interpolation.
    Supports both simple start/end values or complex multi-point lists.

    Args:
        state: Position in normal range (0-100)
        start_value: Start of custom range (or None)
        end_value: End of custom range (or None)
        normal_list: Multi-point normal range values (or None)
        new_list: Multi-point custom range values (or None)

    Returns:
        Interpolated position in custom range, or original state if no
        interpolation configured

    """
    normal_range = [0, 100]
    new_range: list = []
    if start_value is not None and end_value is not None:
        new_range = [start_value, end_value]
    if normal_list and new_list:
        normal_range = list(map(int, normal_list))
        new_range = list(map(int, new_list))
    if new_range:
        state = float(np.interp(state, normal_range, new_range))
    return state


class PositionConverter:
    """Handles position-to-percentage conversions and limit application."""

    @staticmethod
    def to_percentage(position: float, max_value: float) -> int:
        """Convert position to percentage.

        Args:
            position: Position value (height, length, angle, etc.)
            max_value: Maximum possible value (window height, awning length, max degrees)

        Returns:
            Percentage value (0-100), rounded to nearest integer

        """
        percentage = (position / max_value) * 100
        return round(percentage)

    @staticmethod
    def quantize_to_coverage_steps(
        percentage: int,
        n_steps: int,
        full_coverage_at_zero: bool,
        *,
        pivot: float | None = None,
        bounds: tuple[float, float] = (0.0, 100.0),
    ) -> int:
        """Snap an engine-orientation percentage to one of N coverage levels.

        Rounds **toward full coverage** so sun protection is never reduced by the
        quantization. The travel is divided into ``n_steps`` evenly-spaced
        coverage levels; the calculated coverage is rounded *up* to the next
        level. With ``n_steps == 1`` any non-zero coverage demand snaps straight
        to full coverage, which is what the "minimize movements" feature wants.

        Where the coverage is measured FROM depends on the axis (issue #1104):

        * ``pivot is None`` — coverage is monotonic in the percentage, so the
          axis has exactly one coverage-zero end and ``full_coverage_at_zero``
          names it. The whole 0–100 range is one scale, and this is the only
          branch that reads the flag at all.
        * a ``pivot`` — the axis is bi-directional (a MODE2 slat: 0° closed
          downward, 90° horizontal, 180° closed upward), so coverage bottoms out
          at that percentage and grows toward BOTH ends. Each side of the pivot
          gets its own ``n_steps`` levels, spanning that side only, and the
          result stays on the input's side. Two sides of a slat are physically
          distinct orientations, not a coarser version of one shared scale —
          collapsing them would command a sweep through the fully-open
          horizontal to reach "more coverage", the wasted movement this feature
          exists to avoid.

        A pivot of 100.0 (with ``full_coverage_at_zero``) or 0.0 (without) is the
        monotonic case restated, and reduces to the ``None`` branch exactly.

        *bounds* is how far the axis can actually be driven, and the levels are
        laid out ENTIRELY inside it — anchored on the least-covering position the
        axis can reach and spanned to the most-covering one, per side. A tilt
        axis carries its own ``[min_tilt, max_tilt]`` band, applied by the engine
        BEFORE this function and by nothing after it, so a ladder spanned to the
        end of the percentage SCALE puts its top level past the user's own cap:
        a ``max_tilt`` of 60 with the shipped single step commanded 100 % for
        every above-pivot solve. Spanning to the band edge instead makes an
        out-of-band command unreachable by construction — the result never
        passes the far edge, and never falls back past the input — while keeping
        ``n_steps`` distinct levels inside the band. Clamping this function's
        output afterwards would also stop the escape, but it collapses
        100 / 75 / 67 onto a single 60 and quietly empties out the setting the
        user changed.

        The pivot itself is clamped into *bounds* for the same reason, and that
        is what lets the two ends of the band be the whole story here. The pivot
        is ``TILT_HORIZONTAL_DEG``'s image under the engine's angle→percentage
        map and NOTHING constrains that image to 0–100: a louvered roof with
        ``max_slat_angle=45`` puts it at 200 %, a ``specify_angles`` pair of
        0°/45° likewise, and an INVERTED 120°/180° pair at −50 %. Such a slat
        never passes through maximum openness, so it is monotonic over its
        travel — but WHICH end covers is then the side the pivot fell off, not
        ``full_coverage_at_zero``: on the 120°/180° calibration 0 % is the most
        OPEN slat the drive reaches and 100 % is the shut one, the exact
        opposite of what every tilt axis's static flag declares. Clamping the
        anchor answers both at once. It lands on the near band edge, so a
        zero-coverage demand does not move (an unclamped anchor reads the
        fully-open 100 % slat of a 200 % pivot as half-covered and commands 0),
        and the single remaining side runs toward the covering end the pivot
        names (an unclamped −50 % anchor still measures from below, so the flag
        decides and gets it backwards).

        Only this consumer needs the pivot to be reachable, which is why the
        clamp lives here rather than on the engine hook.
        ``round_toward_coverage`` and ``CoverTypePolicy.more_protective_position``
        use the pivot purely as an ordering reference — one tests which SIDE of
        it a percentage falls on, the other ranks by distance from it — and
        clamping it would corrupt both. The two consumers still agree with this
        one about which end covers, because a clamped anchor is on the same side
        of every reachable percentage as the pivot it came from.

        Note the ordering consumers no longer rest on a globally affine map. A
        three-point tilt calibration (#1222) is affine on each SIDE of
        horizontal and not across it, which is invisible to the side test and to
        this function — each side is already spanned independently here — but
        not to a distance comparison, so ``more_protective_position`` asks the
        engine for the distance (``coverage_distance``, measured in degrees off
        horizontal on a slat) instead of taking ``|pct − pivot|`` itself. The
        per-side affinity is what all three still rely on.

        Args:
            percentage: Engine-orientation position (0–100) from
                ``calculate_percentage()``.
            n_steps: Number of discrete coverage levels per side (>= 1).
            full_coverage_at_zero: True when 0% means maximum sun blocking
                (vertical blind, tilt, venetian); False when 100% means maximum
                blocking (awning — open/extended blocks the sun). Derived from the
                policy's primary ``CoverAxis.open_blocks_sun`` flag. Read only
                when *pivot* is absent — a stated pivot is the more specific
                answer and outranks it.
            pivot: Percentage carrying zero coverage, from the engine's
                ``coverage_pivot_percentage()`` hook. ``None`` (the default) for
                every monotonic axis, which is every caller that does not hold a
                bi-directional engine. Not required to lie on the travel.
            bounds: ``(lowest, highest)`` percentage the axis can be commanded
                to, from the engine's ``coverage_travel_bounds()`` hook. Defaults
                to the full travel, which is a no-op. Ignored without a pivot,
                since there is then no anchor for it to constrain.

        Returns:
            The quantized position (0–100) in the same engine orientation.

        """
        if n_steps < 1:
            return percentage
        if pivot is None:
            # Coverage fraction: 1.0 = full coverage, 0.0 = fully open.
            coverage = (
                (100 - percentage) / 100 if full_coverage_at_zero else percentage / 100
            )
            # ceil → round toward more coverage; clamp guards float drift past 1.0.
            level = min(math.ceil(coverage * n_steps) / n_steps, 1.0)
            coverage_pct = level * 100
            if full_coverage_at_zero:
                return round(100 - coverage_pct)
            return round(coverage_pct)
        # Bi-directional axis: measure the demand as a fraction of the side the
        # input sits on, and give it back on that same side. The side runs from
        # the anchor — the least-covering REACHABLE position, i.e. the pivot
        # pulled onto the band — to the far end of that band.
        # The ``max``/``min`` widen the side back out for an input that already
        # sits past the far edge. Neither call site can produce one (both band
        # before they quantize), but "never reduce coverage" is this function's
        # contract and must not become conditional on the caller having banded
        # first.
        low, high = bounds
        anchor = min(max(pivot, low), high)
        if percentage > anchor:
            far = max(high, float(percentage))
            side_sign = 1.0
        else:
            far = min(low, float(percentage))
            side_sign = -1.0
        span = abs(far - anchor)
        if span <= 0:
            # The input sits on a boundary anchor, or on a band pinned to a
            # single point, so its side has no width to quantize into — there is
            # exactly one reachable position on it.
            return percentage
        coverage = abs(percentage - anchor) / span
        level = min(math.ceil(coverage * n_steps) / n_steps, 1.0)
        # ``level * span >= |percentage - anchor|`` by construction and
        # ``level <= 1``, so the answer sits between the input and the far edge:
        # never less covering than the demand, never outside the band.
        commanded = round(anchor + side_sign * level * span)
        # Rounding the top level can add up to half a point, which escapes the
        # band only where its edge is FRACTIONAL. No engine reports one today
        # (``coverage_travel_bounds`` yields whole percentages), but "never
        # passes the far edge" is stated without conditions, so hold it without
        # conditions. The demand is an integer no farther from the anchor than
        # ``far`` is, so this can never pull the answer back short of it.
        if side_sign > 0:
            return min(commanded, math.floor(far))
        return max(commanded, math.ceil(far))

    @staticmethod
    def apply_limits(
        value: int,
        min_pos: int | None,
        max_pos: int | None,
        apply_min: bool,
        apply_max: bool,
        sun_valid: bool,
        sun_tracking_min_pos: int | None = None,
        suppress_sun_tracking_min: bool = False,
    ) -> int:
        """Apply min/max position limits.

        Args:
            value: Position value to constrain (0-100)
            min_pos: Minimum position limit
            max_pos: Maximum position limit
            apply_min: Whether min limit applies (when False, always apply)
            apply_max: Whether max limit applies (when False, always apply)
            sun_valid: Whether sun is in valid position (direct sunlight)
            sun_tracking_min_pos: Optional separate minimum floor that applies
                only during sun tracking (sun_valid=True). When set, overrides
                min_pos for sun-tracking paths. None means fall back to min_pos.
            suppress_sun_tracking_min: When True, the sun-tracking floor is
                ignored even while sun_valid — the effective minimum falls back
                to min_pos. Used by summer climate-close to reach the global min
                instead of the sun-in-FOV floor (issue #689). The max clamp is
                unaffected. Defaults to False so all other callers are unchanged.

        Returns:
            Constrained position value (0-100)

        Note:
            When apply_min/apply_max is False, limits are always enforced.
            When True, limits only apply during direct sun tracking (sun_valid=True).

        """
        # First clip to valid range
        result = np.clip(value, 0, 100)

        # Apply max position limit
        if max_pos is not None and max_pos != 100:
            # Always apply if enable flag is False, or if sun is valid
            if not apply_max or sun_valid:
                result = min(result, max_pos)

        # Sun-tracking floor: when sun_tracking_min_pos is set and sun is valid,
        # use it as the effective min floor instead of min_pos.
        # None means "fall back to min_pos" — preserves existing behavior exactly.
        # Guard: isinstance(x, (int, float)) accepts both int and float so the floor
        # fires when HA's NumberSelector stores a float (e.g. 25.0) — issue #475.
        # Using (int, float) rather than is-not-None avoids false-positives from
        # unspecified MagicMock attributes in tests.
        _use_sun_tracking = (
            isinstance(sun_tracking_min_pos, int | float)
            and sun_valid
            and not suppress_sun_tracking_min
        )
        effective_min = int(sun_tracking_min_pos) if _use_sun_tracking else min_pos

        # Apply min position limit
        if effective_min is not None and effective_min != 0:
            # Always apply if enable flag is False, or if sun is valid
            if not apply_min or sun_valid:
                result = max(result, effective_min)

        return int(result)

    @staticmethod
    def apply_tilt_limits(
        value: int,
        min_tilt: int | None,
        max_tilt: int | None,
        min_tilt_sun_only: bool,
        max_tilt_sun_only: bool,
        *,
        sun_valid: bool,
        transform: str = VENETIAN_TILT_TRANSFORM_CLAMP,
    ) -> int:
        """Clamp a tilt value to the configured ``[min_tilt, max_tilt]`` range.

        Single shared tilt-limit primitive (issue #503): the engine's
        sun-derived tilt (``sun_valid=True``) and the DefaultHandler's
        non-sunset default tilt (``sun_valid=False``) both delegate here so the
        clamp policy lives in exactly one place.

        The ``transform`` argument (issue #957) selects how the value is fitted
        into the band. ``clamp`` (default) preserves every existing caller
        byte-for-byte via the ``apply_limits`` delegation below. When
        ``proportional`` is requested *and* the sun is being tracked, the full
        0–100% demand is linearly remapped onto ``[min_tilt, max_tilt]`` instead
        of flat-capped — see :func:`_proportional_remap`. The proportional path
        only applies while ``sun_valid`` (the sun-tracking engine seam); the
        default-tilt / non-sun path keeps the clamp semantics.

        Delegates to :meth:`apply_limits` — ``min_tilt_sun_only`` /
        ``max_tilt_sun_only`` map onto its ``apply_min`` / ``apply_max`` flags
        (False = always enforce; True = enforce only during sun tracking),
        exactly mirroring the ``enable_min/max_position`` position semantics.
        There is no sun-tracking floor for tilt, so ``sun_tracking_min_pos`` is
        left unset.

        Args:
            value: Tilt value to constrain (0-100).
            min_tilt: Minimum tilt limit (0 = no floor).
            max_tilt: Maximum tilt limit (100 = no cap).
            min_tilt_sun_only: When True, the floor applies only while
                ``sun_valid`` is True; when False it always applies.
            max_tilt_sun_only: When True, the cap applies only while
                ``sun_valid`` is True; when False it always applies.
            sun_valid: Whether the sun is currently tracked (direct sunlight).
            transform: Output transform (issue #957). ``clamp`` (default)
                flat-caps at the band edges; ``proportional`` linearly remaps
                the full 0–100% demand onto ``[min_tilt, max_tilt]`` and only
                takes effect while ``sun_valid``.

        Returns:
            Constrained tilt value (0-100).

        """
        # Degenerate/reversed band guard (issue #957 nit): min_tilt and max_tilt
        # have independent (0,100) ranges with no cross-field validation, so a
        # reversed band (min > max) is reachable via misconfiguration. A
        # proportional ramp over hi <= lo would descend (more sun → less tilt),
        # diverging from clamp. Fall back to the clamp path so proportional and
        # clamp behave identically here (predictably pinned to the band). None
        # bounds map to lo=0/hi=100, so a None max is not degenerate.
        _lo = 0 if min_tilt is None else min_tilt
        _hi = 100 if max_tilt is None else max_tilt
        if (
            transform == VENETIAN_TILT_TRANSFORM_PROPORTIONAL
            and sun_valid
            and _hi > _lo
        ):
            return _proportional_remap(value, min_tilt, max_tilt)
        return PositionConverter.apply_limits(
            value,
            min_tilt,
            max_tilt,
            apply_min=min_tilt_sun_only,
            apply_max=max_tilt_sun_only,
            sun_valid=sun_valid,
        )
