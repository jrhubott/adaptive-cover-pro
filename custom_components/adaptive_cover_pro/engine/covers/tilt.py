"""Tilted/venetian slat cover calculation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from numpy import tan

from ...config_types import TiltConfig
from ...const import (
    SAFETY_MARGIN_USER_SLACK_MAX,
    TILT_HORIZONTAL_DEG,
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
    VENETIAN_TILT_TRANSFORM_CLAMP,
    TiltMode,
)
from ...geometry import SafetyMarginCalculator
from ...position_utils import PositionConverter
from ..sun_geometry import foreshortened_slope
from .base import AdaptiveGeneralCover


def _interpolate(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Map *x* from the ``[x0, x1]`` scale onto ``[y0, y1]``, linearly.

    Single source of the straight-line arithmetic every tilt calibration
    segment is made of: the legacy/custom-max map and the two-point
    ``specify_angles`` map in :meth:`AdaptiveTiltCover._percentage_from_angle`,
    both of their inverses in
    :meth:`AdaptiveTiltCover._angle_from_percentage`, and both halves of the
    hinged three-point calibration (issue #1222). Forward and inverse are the
    same formula with the pairs swapped, which is exactly why neither may carry
    its own copy — a rearrangement on one side and not the other is how an
    inverse silently stops inverting.

    Deliberately written as ``y0 + (x − x0)/(x1 − x0) · (y1 − y0)`` rather than
    any algebraically equal rearrangement: at ``y0 = 0`` / ``y1 = 100`` that
    reduces bit-for-bit to the ``(angle − angle_0) / travel × 100`` expressions
    it replaced, so an existing calibration's percentages do not shift in the
    last ulp.

    The caller owns the ``x1 != x0`` guard. Every segment here has one already —
    a zero-width scale is reported as ``None`` rather than divided by.
    """
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def hinge_is_usable(
    angle_0: float, angle_100: float, horizontal_percent: float
) -> bool:
    """Whether a three-point tilt calibration can be honoured (issue #1222).

    Sole owner of the rule, and pure so that all three layers that need it can
    share this one copy: the engine gates its hinged map on it
    (:meth:`AdaptiveTiltCover._hinge_percent`), and the config flow and the
    ``set_geometry`` service both refuse to STORE a combination it rejects, via
    the message wrapper in ``cover_types.tilt``. A validator that only happens
    to agree with the engine is a validator that will one day disagree.

    Two conditions, and both are about the segments having width:

    * the mid-point is strictly interior — ``0`` is the disabled sentinel and
      ``100`` would collapse the upper segment to nothing;
    * the endpoint angles straddle ``TILT_HORIZONTAL_DEG`` in that order, so
      there is an interior horizontal slat for the hinge to sit at. This also
      declines an INVERTED calibration (``angle_0`` the upward-closed slat):
      the hinge is defined on the ordered travel only.
    """
    strictly_interior = 0.0 < float(horizontal_percent) < 100.0
    straddles_horizontal = float(angle_0) < TILT_HORIZONTAL_DEG < float(angle_100)
    return strictly_interior and straddles_horizontal


def slat_cutoff_angle(
    beta: float, slat_distance: float, depth: float
) -> tuple[float, float, bool]:
    """Solve the venetian slat cut-off angle for a profile angle ``beta``.

    Single source of truth for the MDPI cut-off expression
    (https://www.mdpi.com/1996-1073/13/7/1731) plus its negative-discriminant
    guard, shared by :class:`AdaptiveTiltCover` (vertical-facade profile angle)
    and the louvered-roof engine (pitched-plane profile angle). Only ``beta``
    changes between callers; the slat geometry solve is identical.

    Returns ``(slat_angle_deg, discriminant, negative_discriminant)``:

    * ``negative_discriminant`` is ``True`` when the slat_distance/depth ratio is
      large relative to ``tan(beta)`` (``sqrt`` of a negative). NumPy would
      return ``nan`` silently; the caller returns ``0.0`` (closed) instead, so
      the angle is ``0.0`` in that case.
    * otherwise the angle is ``2·arctan((tan β + √disc)/(1 + ratio))`` in degrees.
    """
    ratio = slat_distance / depth
    discriminant = (tan(beta) ** 2) - (ratio**2) + 1
    if discriminant < 0:
        return 0.0, float(discriminant), True
    slat = 2 * np.arctan((tan(beta) + np.sqrt(discriminant)) / (1 + ratio))
    return float(np.rad2deg(slat)), float(discriminant), False


@dataclass
class AdaptiveTiltCover(AdaptiveGeneralCover):
    """Calculate state for tilted blinds."""

    tilt_config: TiltConfig = None  # type: ignore[assignment]
    # When True (tilt-only / louvered-roof), ``calculate_percentage`` self-applies
    # the shared tilt-axis limits (``[min_tilt, max_tilt]`` + the ``*_sun_only``
    # flags + ``tilt_transform``) via ``PositionConverter.apply_tilt_limits``
    # (issue #964). Venetian composes this engine and applies the identical limits
    # itself downstream at ``VenetianCoverCalculation._clamp_tilt``, so it builds
    # its sub-engine with ``apply_tilt_axis_limits=False`` to avoid clamping twice
    # (a double proportional remap would otherwise compress the band twice).
    apply_tilt_axis_limits: bool = True

    @property
    def slat_distance(self) -> float:
        """Get slat distance from tilt_config."""
        return self.tilt_config.slat_distance

    @property
    def depth(self) -> float:
        """Get depth from tilt_config."""
        return self.tilt_config.depth

    @property
    def mode(self) -> TiltMode | str:
        """Get mode from tilt_config."""
        return self.tilt_config.mode

    @property
    def beta(self) -> float:
        """Calculate beta angle (incident angle of sun on slat plane).

        Beta represents the effective sun elevation angle as seen from the slat's
        perspective, accounting for both sun elevation and horizontal angle (gamma).
        Used in slat tilt calculation to block direct sun while maximizing view/light.

        The argument is the shared ``foreshortened_slope`` VSA tangent. This copy
        had drifted — it divided by a raw ``cos(gamma)`` with no guard at all, so
        past ``|gamma| = 90`` it returned ``≈ −π/2`` instead of ``+π/2`` and the
        cut-off solve collapsed the slat angle 180° → 0° (#1030).

        Returns:
            Beta angle in radians.

        """
        return float(np.arctan(foreshortened_slope(self.sol_elev, self.gamma)))

    def _max_degrees(self) -> float:
        """Resolve max slat degrees for the configured mode (string or enum)."""
        if self._is_specify_angles():
            return float(TiltMode.MODE2.max_degrees)
        if isinstance(self.mode, TiltMode):
            return float(self.mode.max_degrees)
        return float(TiltMode(self.mode).max_degrees)

    @property
    def angle_0(self) -> float:
        """Raw slat angle represented by 0% tilt."""
        return float(self.tilt_config.angle_0)

    @property
    def angle_100(self) -> float:
        """Raw slat angle represented by 100% tilt."""
        return float(self.tilt_config.angle_100)

    @property
    def horizontal_percent(self) -> float:
        """Configured tilt percentage of the horizontal slat (0 = disabled)."""
        return float(self.tilt_config.horizontal_percent)

    def _is_specify_angles(self) -> bool:
        """Return True when endpoint-angle mapping is configured."""
        return self.mode == TiltMode.SPECIFY_ANGLES or self.mode == (
            TiltMode.SPECIFY_ANGLES.value
        )

    def _hinge_percent(self) -> float | None:
        """Active three-point hinge, or ``None`` when the scale stays two-point.

        The optional third calibration point (issue #1222): hardware whose slats
        reach horizontal somewhere other than the affine midpoint of its
        reported travel — the reporting KNX venetian runs 0°→0 %, 90°→50 %,
        130°→100 %, i.e. 1.8 °/% below horizontal against 0.8 °/% above it. No
        affine map has a knee, so no combination of the endpoint angles can hold
        all three points at once.

        ``None`` means "behave exactly as before", and it covers three
        distinguishable situations on purpose, because they all want the same
        answer and none of them is an error:

        * the mode is not ``specify_angles`` — the presets carry their own
          scale and the hinge has nothing to hinge;
        * the option is at its ``0`` disabled sentinel (or absent from a config
          entry written before this existed);
        * :func:`hinge_is_usable` declines the combination — see there for the
          two width conditions and why the config layers share that predicate
          rather than restating it.

        Config flow and ``set_geometry`` reject an unusable combination before
        it can be stored, so reaching one means a hand-edited or
        partially-restored entry. Answering ``None`` there keeps this map total:
        it never raises and never divides by a zero-width segment.
        """
        if not self._is_specify_angles():
            return None
        hinge = self.horizontal_percent
        if not hinge_is_usable(self.angle_0, self.angle_100, hinge):
            return None
        return hinge

    def _specified_target_angle(self, raw_angle: float) -> float:
        """Return the useful raw target angle for explicit endpoint calibration."""
        return max(0.0, min(180.0, float(raw_angle)))

    def _percentage_from_angle(self, angle: float) -> float | None:
        """Map a raw slat angle onto this engine's tilt percentage scale.

        Sole owner of the angle→percentage map. Four callers need it and they
        MUST agree: the solved position (:meth:`calculate_raw_percentage`), the
        calibrated endpoint mapping (:meth:`calculate_percentage`), the
        horizontal pivot (:meth:`_horizontal_percentage`), and the climate
        handler's target-angle translation (:meth:`climate_tilt_percentage`).
        The pivot is what makes sharing load-bearing rather than merely tidy —
        it points at maximum openness only while it is divided by the SAME
        denominator the position is, and :meth:`_effective_max_degrees` lets the
        louvered roof move that denominator to a configurable
        ``max_slat_angle``. Re-deriving either map at a second site is exactly
        how the pivot drifts off the scale it is supposed to sit on — and how
        the climate path spent several releases mapping ``specify_angles``
        covers on a MODE1 denominator that ignored their calibration outright.

        :meth:`_angle_from_percentage` is the inverse and shares this map's
        segments; the two are one seam, not two.

        The solver and the configured endpoints both use ACP's raw/card angle
        convention: 0° closed downward, 90° horizontal, 180° closed upward.
        Three shapes, selected by the configured mode and calibration:

        * legacy / custom-max — ``pct = angle / max_degrees × 100``.
        * ``specify_angles``, two-point — ``pct = (angle − angle_0) / travel ×
          100``, which may run BACKWARDS (``travel < 0``, an inverted
          calibration where 0 % is the upward-closed slat). Affine either way,
          so the pivot's image moves with the map and the sign never has to be
          tested.
        * ``specify_angles`` with a hinge (issue #1222) — two straight segments
          meeting at ``(TILT_HORIZONTAL_DEG, horizontal_percent)``. Affine on
          each SIDE of horizontal but not across it, which is the whole point:
          the reporting hardware runs at 1.8 °/% below horizontal and 0.8 °/%
          above. :meth:`_hinge_percent` decides when this shape applies; when it
          declines, the two-point map above is used unchanged, so an install
          that never set the option is byte-identical to before.

        The hinge is deliberately anchored at ``TILT_HORIZONTAL_DEG`` and
        nowhere else. That angle is not an arbitrary interior point — it is the
        maximum-openness slat this whole module already pivots on (the
        safety-margin fixed point, the rounding reference, and
        :meth:`coverage_pivot_percentage`), so hinging there is what makes the
        pivot land exactly on the configured percentage with no second
        derivation anywhere downstream.

        ``None`` means the scale is degenerate — zero width — so no percentage
        exists at all. Each caller decides what to do about that.
        """
        if self._is_specify_angles():
            travel = self.angle_100 - self.angle_0
            if travel == 0:
                return None
            target_angle = self._specified_target_angle(angle)
            hinge = self._hinge_percent()
            if hinge is None:
                return _interpolate(
                    target_angle, self.angle_0, self.angle_100, 0.0, 100.0
                )
            if target_angle < TILT_HORIZONTAL_DEG:
                return _interpolate(
                    target_angle, self.angle_0, TILT_HORIZONTAL_DEG, 0.0, hinge
                )
            return _interpolate(
                target_angle, TILT_HORIZONTAL_DEG, self.angle_100, hinge, 100.0
            )
        max_degrees = float(self._effective_max_degrees())
        if max_degrees <= 0:
            return None
        return _interpolate(float(angle), 0.0, max_degrees, 0.0, 100.0)

    def _angle_from_percentage(self, pct: float) -> float | None:
        """Map a tilt percentage back onto the raw slat angle it commands.

        The exact inverse of :meth:`_percentage_from_angle`, segment for
        segment, and the codebase's only percentage→angle map (issue #1222).
        Before it existed the inverse was implicit — the protective comparator
        assumed the forward map was globally affine and ranked candidates by
        ``|pct − pivot|``, which is proportional to ``|angle − 90°|`` only while
        that assumption holds. A hinged scale breaks it across the pivot, so
        :meth:`coverage_distance` measures in ANGLE space through this map
        instead.

        Both directions delegate to the same :func:`_interpolate` with the pairs
        swapped, so a change to one segment cannot leave the other behind.

        Deliberately NOT clamped: unlike the forward direction, which pins the
        solved angle into the physical 0–180° range via
        :meth:`_specified_target_angle` before mapping, this answers what the
        scale SAYS a percentage means, including outside the calibrated travel
        where an off-travel pivot lives. Clamping would flatten exactly the
        ordering its callers need.

        ``None`` on the same degenerate scales the forward map reports ``None``
        for — a scale that cannot produce a percentage cannot consume one.
        """
        if self._is_specify_angles():
            if self.angle_100 - self.angle_0 == 0:
                return None
            hinge = self._hinge_percent()
            if hinge is None:
                return _interpolate(
                    float(pct), 0.0, 100.0, self.angle_0, self.angle_100
                )
            if pct <= hinge:
                return _interpolate(
                    float(pct), 0.0, hinge, self.angle_0, TILT_HORIZONTAL_DEG
                )
            return _interpolate(
                float(pct), hinge, 100.0, TILT_HORIZONTAL_DEG, self.angle_100
            )
        max_degrees = float(self._effective_max_degrees())
        if max_degrees <= 0:
            return None
        return _interpolate(float(pct), 0.0, 100.0, 0.0, max_degrees)

    def _effective_max_degrees(self) -> float:
        """Ceiling + percentage denominator for the slat angle.

        Polymorphic hook. Base: the tilt mode's max (90 for MODE1, 180 for
        MODE2). The louvered-roof engine overrides this to honour a configurable
        physical ``max_slat_angle`` for pergola drives whose mechanical travel
        is neither 90° nor 180°.
        """
        return self._max_degrees()

    def _blocking_depth(self) -> float:
        """Slat depth used in the cut-off solve.

        Polymorphic hook. Base: the nominal chord (vertical venetian slats shade
        tip-to-tip). The louvered-roof engine overrides this to account for the
        interlock overlap of bioclimatic-pergola lamellae (#830).
        """
        return self.depth

    def _resolve_slat_angle(self, cutoff_angle: float) -> float:
        """Map the magnitude cut-off angle to the physical slat angle.

        Polymorphic hook. Base: identity — the vertical-facade venetian/tilt
        angle IS the physical slat angle. The louvered-roof engine overrides
        this to realize far-side sun as the flipped face (``180° − θ``).
        """
        return cutoff_angle

    def _build_trace(
        self,
        *,
        beta: float,
        discriminant: float,
        negative_discriminant: bool,
        slat_angle_raw_deg: float | None,
        nan_result: bool,
        max_degrees: float,
        result: float,
        safety_margin: float = 1.0,
    ) -> dict:
        """Assemble the raw tilt solar-calculation trace (issue #682).

        Single source for the negative-discriminant guard, the NaN guard, and the
        normal return path so the key set never drifts. Raw native floats — the
        ``DiagnosticsBuilder`` rounds at the presentation boundary.
        """
        mode_value = self.mode.value if isinstance(self.mode, TiltMode) else self.mode
        return {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: PositionConverter.to_percentage(
                result, max_degrees
            ),
            "beta_rad": float(beta),
            "discriminant": float(discriminant),
            "negative_discriminant": bool(negative_discriminant),
            "slat_angle_raw_deg": (
                None if slat_angle_raw_deg is None else float(slat_angle_raw_deg)
            ),
            "nan_result": bool(nan_result),
            "max_degrees": float(max_degrees),
            "tilt_mode": str(mode_value),
            "safety_margin": float(safety_margin),
        }

    def calculate_position(self) -> float:
        """Calculate optimal slat tilt angle to block direct sun.

        Implements venetian blind optimization algorithm from:
        https://www.mdpi.com/1996-1073/13/7/1731

        Uses slat geometry (depth, spacing) and sun incident angle (beta) to
        calculate the tilt angle that blocks direct solar radiation while
        maximizing view and diffuse light.

        Supports two modes:
        - MODE1 (90°): Single-direction tilt (0° closed → 90° fully open)
        - MODE2 (180°): Bi-directional tilt (0° closed → 90° horizontal → 180° closed)

        Returns:
            Optimal slat tilt angle in degrees (0-90 for MODE1, 0-180 for MODE2).

        """
        beta = self.beta
        max_degrees = self._effective_max_degrees()

        # Guard: discriminant can be negative when slat_distance/depth ratio is
        # large relative to tan(beta), making sqrt of a negative.  NumPy returns
        # nan silently; we return 0.0 (closed) as a safe fallback instead. The
        # cut-off math is shared with the louvered-roof engine via
        # ``slat_cutoff_angle`` (only ``beta`` and the ``_blocking_depth()`` hook
        # differ between them — the roof widens the depth for interlock overlap).
        result, discriminant, negative_discriminant = slat_cutoff_angle(
            beta, self.slat_distance, self._blocking_depth()
        )
        if negative_discriminant:
            self.logger.debug(
                "Tilt calc: negative discriminant (%.4f) — returning 0° (closed)",
                float(discriminant),
            )
            self._last_calc_details = self._build_trace(
                beta=beta,
                discriminant=discriminant,
                negative_discriminant=True,
                slat_angle_raw_deg=None,
                nan_result=False,
                max_degrees=max_degrees,
                result=0.0,
            )
            return 0.0

        # Additional nan guard in case of unexpected floating-point edge cases
        if np.isnan(result):
            self.logger.debug(
                "Tilt calc: NaN result (elev=%.1f°, gamma=%.1f°, beta=%.4f) — returning 0°",
                self.sol_elev,
                self.gamma,
                float(beta),
            )
            self._last_calc_details = self._build_trace(
                beta=beta,
                discriminant=discriminant,
                negative_discriminant=False,
                slat_angle_raw_deg=None,
                nan_result=True,
                max_degrees=max_degrees,
                result=0.0,
            )
            return 0.0

        # Realize the physical slat angle from the magnitude cut-off (identity
        # for tilt/venetian; the louvered-roof engine flips the far-side face to
        # ``180° − θ`` here, before the safety margin closes it toward 180).
        result = self._resolve_slat_angle(result)
        slat_angle_raw_deg = float(result)

        # Configurable safety margin (issue #783; corrected in #1089). The user's
        # ``safety_margin`` (0.0-1.0) scales a slack budget made of the vertical
        # axis' angle-dependent geometry margin PLUS a flat
        # ``SAFETY_MARGIN_USER_SLACK_MAX`` term, applied in the slat-CLOSING
        # direction. Vertical multiplies a drop by the margin; tilt must instead
        # close the slats further, so we scale the closure away from horizontal.
        # The flat term is what makes the slider mean anything inside the normal
        # envelope (|gamma| <= 45°, 10° <= elev <= 75°), where the geometry margin
        # is exactly 1.0 and the original ``(geo_margin - 1) * s`` form multiplied
        # a zero excess — the slider was inert for most of the day (#1089).
        # ``safety_margin = 0.0`` is still a byte-for-byte no-op at EVERY geometry
        # (the whole bracket is gated by the user's value), so the guard below
        # tests that value directly: ``_safety_margin`` only ever ADDS
        # non-negative terms to 1.0, so ``geo_margin >= 1.0`` and the bracket is
        # never below ``SAFETY_MARGIN_USER_SLACK_MAX`` — "the user opted in" and
        # "``eff_margin`` left 1.0" are the same question, minus a float-equality
        # test on a derived quantity. Because the transform is relative to
        # ``TILT_HORIZONTAL_DEG``, a slat resolved to exactly horizontal is a
        # fixed point, and a MODE1 raw angle at or above the 90° ceiling stays
        # clamped fully open — the margin cannot close what is already at the rail.
        geo_margin = SafetyMarginCalculator.calculate(self.gamma, self.sol_elev)
        eff_margin = 1.0 + self.tilt_config.safety_margin * (
            (geo_margin - 1.0) + SAFETY_MARGIN_USER_SLACK_MAX
        )
        if self.tilt_config.safety_margin != 0.0:
            result = TILT_HORIZONTAL_DEG - (TILT_HORIZONTAL_DEG - result) * eff_margin

        # Clamp to the drive's physical travel. On MODE1 (90°) this fires
        # whenever the cut-off runs past horizontal, and reads at first glance
        # like it commands maximum openness at the worst moment of the day —
        # the sun low, the solve asking for 106°, the answer 90°. The geometry
        # says otherwise, as an identity rather than a coincidence:
        #
        #     raw solve > 90°  ⟺  depth · tan β > slat_distance
        #
        # and the right-hand side is exactly the condition for a HORIZONTAL slat
        # to intercept the beam (over its own depth the ray drops
        # ``depth · tan β``, landing on the slat below once that exceeds the
        # spacing). So wherever this clamp engages, 90° is already a BLOCKING
        # position — a 90°-travel drive has no more-closed position on that side
        # of horizontal, and the only alternative, the 0° end, is a full sweep
        # away. Pinned by
        # ``tests/test_adaptive_tilt_cover.py::TestMode1ClampCrossover`` so the
        # identity is checked rather than asserted here (#1222 audit).
        result = max(0.0, min(float(max_degrees), float(result)))

        self.logger.debug(
            "Tilt calc: elev=%.1f°, gamma=%.1f°, beta=%.4f rad, slat_angle=%.1f°",
            self.sol_elev,
            self.gamma,
            beta,
            result,
        )
        self._last_calc_details = self._build_trace(
            beta=beta,
            discriminant=discriminant,
            negative_discriminant=False,
            slat_angle_raw_deg=slat_angle_raw_deg,
            nan_result=False,
            max_degrees=max_degrees,
            result=result,
            safety_margin=eff_margin,
        )
        return result

    def calculate_percentage(self) -> float:
        """Convert slat tilt angle to percentage for Home Assistant.

        Converts calculated tilt angle (degrees) to percentage (0-100) for cover
        entity position attribute. Maximum degrees depends on mode:
        - MODE1: 0° (closed) → 90° (fully open) = 0-100%
        - MODE2: 0° (closed) → 180° (closed inverted) = 0-100%

        Returns:
            Position as percentage (0-100).

        """
        # Legacy modes use a fixed degree range. The custom mode uses explicit
        # raw endpoint angles and interpolates the target angle into that
        # calibrated range.
        position = self.calculate_position()

        # The specify-angles mode maps the solved raw slat angle into a
        # user-calibrated endpoint range — an offset (angle_0) plus a scale,
        # optionally hinged at horizontal (#1222) — which the pure
        # ``max_degrees`` denominator below cannot express. Handle it here,
        # before the polymorphic base path, and correct the trace's position
        # percentage in place.
        if self._is_specify_angles():
            percentage = self._percentage_from_angle(position)
            if percentage is None:
                # Degenerate calibration (``angle_0 == angle_100``): no width to
                # interpolate into, and 0 % is what this path has always
                # answered for it.
                percentage = 0.0
            if hasattr(self, "_last_calc_details"):
                self._last_calc_details[TRACE_KEY_POSITION_PCT] = float(percentage)
                self._last_calc_details["target_angle_deg"] = (
                    self._specified_target_angle(position)
                )
                self._last_calc_details["tilt_angle_0_deg"] = self.angle_0
                self._last_calc_details["tilt_angle_100_deg"] = self.angle_100
                # The companion Lovelace card rebuilds this scale from the
                # trace to draw the slat, so the hinge has to travel with the
                # endpoints or it would draw a straight map over a bent one
                # (#1222). Published even at the 0 sentinel — a stable key set
                # is what lets the card read it without probing.
                self._last_calc_details["tilt_horizontal_percent"] = (
                    self.horizontal_percent
                )
            pct = max(0.0, min(100.0, percentage))
        else:
            # Same effective ceiling the position solve clamps to (the mode max
            # for tilt/venetian; a configurable physical max for the louvered roof).
            pct = float(
                PositionConverter.to_percentage(position, self._effective_max_degrees())
            )

        return self._apply_tilt_axis_limits(pct)

    def calculate_raw_percentage(self) -> float:
        """Unrounded tilt fraction for directional rounding (issue #978).

        Mirrors :meth:`calculate_percentage` but skips the ``round()`` inside
        ``PositionConverter.to_percentage`` on the legacy/custom-max path, so
        :func:`pipeline.helpers.solar_position_from_geometry` sees the true
        fraction instead of an already-rounded value (which would neutralise the
        floor/ceil direction signal). The specify-angles path already yields an
        unrounded percentage — and populates the diagnostics trace — so it is
        reused as-is. ``_apply_tilt_axis_limits`` returns the exact float at the
        default band and only rounds when the band actually moves the tilt,
        matching :meth:`calculate_percentage`.
        """
        if self._is_specify_angles():
            return self.calculate_percentage()
        position = self.calculate_position()
        pct = self._percentage_from_angle(position)
        if pct is None:
            # A zero-width scale has no percentage to report. Post-#1105, a
            # zero (or negative) denominator is unreachable from config at
            # all: both tilt-mode maxima are nonzero, ``max_slat_angle`` is
            # bounded to [0, 180], and the ``0`` sentinel routes to the mode
            # max rather than to a literal zero. (The fractional-truncation
            # path that used to collapse onto this branch via an ``int()``
            # cast is gone — see #1105.) This branch is now reachable only
            # through a mocked ``_effective_max_degrees`` in tests, or a
            # hand-corrupted stored value. Raising keeps the contract
            # ``VenetianCoverCalculation._compute_tilt`` relies on: a tilt
            # geometry that does not resolve falls back to the default
            # position.
            raise ZeroDivisionError("tilt percentage scale has zero width")
        return self._apply_tilt_axis_limits(pct)

    def _horizontal_percentage(self) -> float | None:
        """Tilt percentage that maps to the horizontal slat, or ``None``.

        ``TILT_HORIZONTAL_DEG`` is the maximum-openness angle on every tilt
        scale this engine drives — it is the pivot the safety margin already
        scales the closure away from in :meth:`calculate_position`, and the
        angle the louvered-roof override drives *toward* when it wants the
        slats as open as possible. Expressing it as a percentage lets
        :meth:`round_toward_coverage` decide direction without reconstructing
        the angle or branching on the tilt mode.

        Nothing here but the pivot's image under :meth:`_percentage_from_angle`
        — the very map the solved position goes through — and deliberately so:
        "away from horizontal" is only "away from this percentage" while the
        two share a scale. A second copy of either map would let a new mode, or
        an ``_effective_max_degrees`` override, move the position without moving
        the pivot, and the rounding would quietly start closing the wrong way.

        ``None`` means the pivot is undefined (a degenerate zero-width scale),
        and callers fall back to the monotonic axis rule.
        """
        return self._percentage_from_angle(TILT_HORIZONTAL_DEG)

    def coverage_pivot_percentage(self) -> float | None:
        """Report horizontal as the pivot — the slat axis is bi-directional (#1104).

        Publishes the very pivot :meth:`round_toward_coverage` already rounds
        away from, so every consumer of "how much coverage does this percentage
        carry" measures against the same point the rounding does. Deliberately
        the same call, not a second derivation: routing through
        :meth:`_horizontal_percentage` means an ``_effective_max_degrees``
        override (the louvered roof's ``max_slat_angle``, which puts the pivot at
        a fractional 64.2857 % for a 140° drive) moves the pivot everywhere at
        once, and MODE1's 100 % pivot keeps the monotonic behaviour it always
        had rather than needing a special case.

        Inherited unchanged by :class:`~.louvered_roof.AdaptiveLouveredRoofCover`.
        ``None`` on a degenerate zero-width scale, matching the base contract.

        The answer can fall OUTSIDE 0–100, and deliberately is not clamped or
        suppressed here. ``max_slat_angle = 45`` puts horizontal at 200 %, and a
        ``specify_angles`` pair of 0°/45° or 120°/180° does the same (200 % and
        −50 %) — the config flow only orders the endpoints. Such a slat never
        passes through maximum openness, so the axis really is monotonic over its
        travel; but the pivot is still the correct ORDERING reference, and the
        one it corrects is the inverted calibration the axis flag gets backwards
        (0 % is the OPEN end at 120°/180°, not the covering one). Reporting
        ``None`` for it would hand every consumer back exactly that wrong rule.
        The one consumer that anchors on the pivot pulls it onto the reachable
        travel itself — see ``quantize_to_coverage_steps``.

        Percentages are reported here in the same command space the engine's
        percentage output lives in. ``[min_tilt, max_tilt]`` and
        ``tilt_transform`` (#957) reshape the DEMAND on its way into that space;
        they do not rescale the space itself, so horizontal stays where this map
        puts it and the pivot needs no second transform to be compared against a
        banded command.
        """
        return self._horizontal_percentage()

    def coverage_distance(self, pct: float) -> float | None:
        """Measure coverage in DEGREES off horizontal, not in percent (#1222).

        The base metric — ``|pct − pivot|`` — is proportional to this on every
        affine scale, so this override changes no answer on MODE1, MODE2, the
        louvered roof's ``max_slat_angle``, or a two-point ``specify_angles``
        pair; the proportionality constant cancels out of the only thing the
        caller does with two distances, which is compare them.

        It stops cancelling the moment the scale hinges. On the reporting
        blind's 0°/90°/130° calibration, 40 % is 72° — 18° of closure — and 65 %
        is 102°, only 12°; ranked by percentage the second wins by 15 points to
        10 and the comparator commands the slat that lets more sun through.
        Degrees off horizontal is what "how much sun does this block" actually
        means on a slat, so measuring there is right on the hinge and merely
        redundant everywhere else.

        Routes through :meth:`_angle_from_percentage`, the exact inverse of the
        map the pivot itself comes from, so the two can never disagree about
        which side of maximum openness a percentage sits on.
        """
        angle = self._angle_from_percentage(pct)
        if angle is None:
            return None
        return abs(angle - TILT_HORIZONTAL_DEG)

    def climate_tilt_percentage(
        self, angle_deg: float, *, sun_through: bool = False
    ) -> int | None:
        """Translate a climate TARGET slat angle into this engine's percentage.

        The climate handler asks for an angle — ``CLIMATE_SUMMER_TILT_ANGLE``,
        ``CLIMATE_DEFAULT_TILT_ANGLE``, or the live profile angle for winter
        heating — and needs the percentage that commands it. That is the same
        question :meth:`_percentage_from_angle` answers for the solar path, and
        answering it here rather than in the policy is what finally makes the
        two agree: ``TiltPolicy.climate_tilt_percentage``'s static formula tests
        only "is this MODE2?" and falls back to a hardcoded 90° denominator, so
        a ``specify_angles`` cover — which is not MODE2 — had its climate tilt
        computed on MODE1's scale with its calibration ignored outright
        (issue #1222). Any future scale inherits the fix for free.

        *sun_through* is winter heating: mirror the angle across horizontal so
        the slat sits parallel to the beam, the maximum-transmission
        orientation. Gated on the pivot being STRICTLY interior, which is the
        scale-independent way to ask "does this axis have two hemispheres?".
        MODE1's pivot is 100 % — horizontal is its fully-open rail and there is
        nothing on the far side — so it declines, exactly as the static
        formula's MODE1 early return did. MODE2's is 50 % and takes it, also
        exactly as before.

        The mirror is applied to the ANGLE, before the map, because that is
        where it means anything: on a hinged scale, reflecting the percentage
        about the pivot lands somewhere else entirely.

        ``None`` on a degenerate scale, leaving the caller free to fall back —
        which the policy does, to the formula it has always used.
        """
        pivot = self._horizontal_percentage()
        if pivot is None:
            return None
        effective_angle = float(angle_deg)
        if sun_through and 0.0 < pivot < 100.0:
            effective_angle = TILT_HORIZONTAL_DEG + effective_angle
        percentage = self._percentage_from_angle(effective_angle)
        if percentage is None:
            return None
        return round(percentage)

    def coverage_travel_bounds(self) -> tuple[float, float]:
        """Report ``[min_tilt, max_tilt]`` as the reachable travel (#1104).

        Derived by asking :meth:`_limit_tilt` — the sole owner of the band
        argument bundle — what it does to the two ends of the scale, rather than
        by reading ``tilt_config.min_tilt``/``max_tilt`` a second time here. The
        band is not just those two numbers: the ``*_sun_only`` toggles and the
        degenerate/reversed-band fallback are part of it, and a reversed band
        pins BOTH ends to ``min_tilt``, which this probe reports as a zero-width
        travel — exactly what the axis does there. A hand-rolled tuple would be
        a band that only happens to match, and would drift the first time that
        seam learns a new rule.

        The probe is transform-independent by construction: the proportional
        remap sends 0 → ``min_tilt`` and 100 → ``max_tilt``, the same two values
        the flat clamp does, so this asks for the clamp and gets the band edges
        under either setting.

        Deliberately NOT gated on ``apply_tilt_axis_limits``. That flag answers
        "does this engine apply the band itself?", and both answers still end in
        a banded tilt: the tilt-only path bands it in
        :meth:`round_toward_coverage`, and venetian — which clears the flag —
        bands it in ``VenetianCoverCalculation._clamp_tilt``. The coverage-step
        quantiser runs after both, so both need the same travel.

        Inherited unchanged by :class:`~.louvered_roof.AdaptiveLouveredRoofCover`.
        """
        return (
            float(self._limit_tilt(0, transform=VENETIAN_TILT_TRANSFORM_CLAMP)),
            float(self._limit_tilt(100, transform=VENETIAN_TILT_TRANSFORM_CLAMP)),
        )

    def round_toward_coverage(self, pct: float, *, full_coverage_at_zero: bool) -> int:
        """Quantise the slat percentage AWAY from horizontal (issue #1090).

        Coverage on a slat axis is NOT monotonic in the percentage, which is the
        assumption the base implementation encodes. On MODE2 — the shipped
        default for tilt-only and venetian covers — 0° is closed downward, 90°
        is horizontal, and 180° is closed upward, so 50 % is the single most
        sun-permissive position and coverage grows as the angle leaves it in
        EITHER direction. :meth:`calculate_position` returns the exact grazing
        angle (the most-open slat that still blocks the beam), so any
        quantisation toward horizontal leaks direct sun.

        Rounding away from :meth:`_horizontal_percentage` is therefore the
        conservative direction, and it subsumes the monotonic case rather than
        special-casing it: MODE1 spans 0–90°, so its pivot is 100 % and every
        reachable percentage rounds down exactly as before.

        Exactly ON the pivot, both directions increase coverage equally, so
        there is no conservative answer to pick — ``floor`` wins the tie for
        consistency with the position axis. That tie is genuinely reachable, not
        a theoretical edge: a louvered roof with a configured ``max_slat_angle``
        has a FRACTIONAL pivot (140° → 64.2857 %), and its ``_resolve_slat_angle``
        returns exactly ``TILT_HORIZONTAL_DEG`` whenever the slats self-block,
        so the raw percentage lands bit-exactly on the pivot rather than near
        it. Nothing rides on which side wins there — at 140° the neighbours are
        89.6° and 91.0°, both farther from horizontal than the 90.0° solve — but
        the choice is pinned by test so the boundary cannot drift unnoticed. On
        the two whole-percentage pivots (MODE1's 100 %, MODE2's 50 %) ``floor``
        and ``ceil`` agree anyway.

        The quantised integer is then re-banded, because on this path the
        ``[min_tilt, max_tilt]`` band was applied to the FLOAT back in
        :meth:`calculate_raw_percentage` and that pass predicts the final
        integer with ``int(round(pct))`` — a prediction the away-from-horizontal
        rule invalidates. A raw percentage inside ``(max_tilt, max_tilt + 0.5)``
        survives the band check and would leave here one point past the cap
        (mirror-image below ``min_tilt`` under ``floor``). Re-banding is a clamp
        only, so it is idempotent: the proportional remap has already landed
        inside the band and is not re-applied, and the shipped ``0``/``100``
        default is a provable no-op. Venetian clears
        ``apply_tilt_axis_limits``, so its sub-engine skips this and
        ``VenetianCoverCalculation._clamp_tilt`` stays the single band owner
        there — applying the band through the same :meth:`_limit_tilt` seam.
        """
        horizontal_pct = self._horizontal_percentage()
        if horizontal_pct is None:
            quantised = super().round_toward_coverage(
                pct, full_coverage_at_zero=full_coverage_at_zero
            )
        elif pct > horizontal_pct:
            quantised = ceil(pct)
        else:
            quantised = floor(pct)
        if not self.apply_tilt_axis_limits:
            return quantised
        return self._limit_tilt(quantised, transform=VENETIAN_TILT_TRANSFORM_CLAMP)

    def _limit_tilt(self, value: int, *, transform: str) -> int:
        """Fit an integer tilt % to this engine's ``[min_tilt, max_tilt]`` band.

        Sole owner of the band argument bundle — genuinely sole, across all
        three seams that apply it: the pre-quantisation transform
        (:meth:`_apply_tilt_axis_limits`), the post-quantisation band guard
        (:meth:`round_toward_coverage`), and venetian's dual-axis band
        (``VenetianCoverCalculation._clamp_tilt``, which reaches in here rather
        than rebuilding the same call). They differ only in *transform*: the
        first and last honour the user's ``tilt_transform``, the band guard
        always clamps because the transform has already run by then and
        remapping twice would compress the band twice — the same
        double-application venetian avoids by clearing
        ``apply_tilt_axis_limits``.

        That flag is checked by the callers rather than here, because "not my
        band" means handing back a different thing in each: the float seam owes
        its caller the untouched ``pct`` (including a NaN it must stay
        transparent to), the integer seam owes it the untouched quantised value
        — and venetian never asks the question at all, because clearing the flag
        on its sub-engine is exactly how it claims the band for itself.

        The engine path is always sun-tracking, so ``sun_valid=True`` and the
        ``*_sun_only`` toggles are unconditional; they are passed through anyway
        so the shared primitive keeps deciding that, not this call site.
        """
        cfg = self.tilt_config
        return PositionConverter.apply_tilt_limits(
            value,
            cfg.min_tilt,
            cfg.max_tilt,
            cfg.min_tilt_sun_only,
            cfg.max_tilt_sun_only,
            sun_valid=True,
            transform=transform,
        )

    def _apply_tilt_axis_limits(self, pct: float) -> float:
        """Clamp the sun-derived tilt % to the configured tilt-axis band.

        Routes through the shared :meth:`PositionConverter.apply_tilt_limits`
        seam (issue #503/#957) so a tilt-only or louvered-roof cover honors the
        same ``[min_tilt, max_tilt]`` band, ``*_sun_only`` flags, and
        ``tilt_transform`` venetian already reaches (issue #964). The engine
        path is always sun-tracking, so ``sun_valid=True``. A no-op at defaults
        (``min_tilt=0``/``max_tilt=100``/``clamp``), preserving the exact raw %.

        Only the return value is limited — the diagnostics trace keeps the raw
        geometry percentage, matching how venetian's ``_clamp_tilt`` leaves the
        tilt engine's trace untouched.

        Venetian's composed sub-engine sets ``apply_tilt_axis_limits=False`` and
        applies the identical limits itself, so this is skipped there — before
        ``round()`` is reached, keeping the seam transparent to the NaN
        ``VenetianCoverCalculation._compute_tilt`` explicitly tests for.
        """
        if not self.apply_tilt_axis_limits:
            return pct
        rounded = int(round(pct))
        limited = self._limit_tilt(rounded, transform=self.tilt_config.tilt_transform)
        # The shared primitive is int-valued, but ``calculate_percentage`` has
        # always returned a float — specify-angles yields a fractional percent
        # the pipeline rounds downstream. When the band leaves the rounded value
        # untouched (a no-op / within-band clamp), keep the exact float so that
        # precision is preserved byte-for-byte; only substitute the primitive's
        # value when it actually moved the tilt (a cap, floor, or transform bit).
        #
        # ``rounded`` is only a PREDICTION of the integer this float becomes, and
        # the away-from-horizontal rule can beat it by one at a band edge — which
        # is why :meth:`round_toward_coverage` re-bands the real integer (#1090)
        # rather than this prediction being sharpened here: sharpening it would
        # feed a different value into the proportional remap and move
        # in-band results that have nothing to do with the escape.
        if limited == rounded:
            return pct
        return float(limited)
