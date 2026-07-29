"""Tilted/venetian slat cover calculation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from numpy import tan

from ...config_types import TiltConfig
from ...const import (
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

    def _is_specify_angles(self) -> bool:
        """Return True when endpoint-angle mapping is configured."""
        return self.mode == TiltMode.SPECIFY_ANGLES or self.mode == (
            TiltMode.SPECIFY_ANGLES.value
        )

    def _specified_target_angle(self, raw_angle: float) -> float:
        """Return the useful raw target angle for explicit endpoint calibration."""
        return max(0.0, min(180.0, float(raw_angle)))

    def _percentage_from_angle(self, angle: float) -> float | None:
        """Map a raw slat angle onto this engine's tilt percentage scale.

        Sole owner of the angle→percentage map. Three callers need it and they
        MUST agree: the solved position (:meth:`calculate_raw_percentage`), the
        calibrated endpoint mapping (:meth:`calculate_percentage`), and the
        horizontal pivot (:meth:`_horizontal_percentage`). The pivot is what
        makes sharing load-bearing rather than merely tidy — it points at
        maximum openness only while it is divided by the SAME denominator the
        position is, and :meth:`_effective_max_degrees` lets the louvered roof
        move that denominator to a configurable ``max_slat_angle``. Re-deriving
        either map at a second site is exactly how the pivot drifts off the
        scale it is supposed to sit on.

        The solver and the configured endpoints both use ACP's raw/card angle
        convention: 0° closed downward, 90° horizontal, 180° closed upward. Two
        affine maps, selected by the configured mode:

        * legacy / custom-max — ``pct = angle / max_degrees × 100``.
        * ``specify_angles`` — ``pct = (angle − angle_0) / travel × 100``, which
          may run BACKWARDS (``travel < 0``, an inverted calibration where 0 %
          is the upward-closed slat). Affine either way, so the pivot's image
          moves with the map and the sign never has to be tested.

        ``None`` means the scale is degenerate — zero width — so no percentage
        exists at all. Each caller decides what to do about that.
        """
        if self._is_specify_angles():
            travel = self.angle_100 - self.angle_0
            if travel == 0:
                return None
            target_angle = self._specified_target_angle(angle)
            return ((target_angle - self.angle_0) / travel) * 100.0
        max_degrees = float(self._effective_max_degrees())
        if max_degrees <= 0:
            return None
        return (float(angle) / max_degrees) * 100.0

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

        # Configurable safety margin (issue #783): reuse the vertical axis'
        # angle-dependent geometry margin (>=1.0), scaled by the user's
        # ``safety_margin`` (0.0-1.0), applied in the slat-CLOSING direction.
        # Vertical multiplies a drop by the margin; tilt must instead close the
        # slats further, so we scale the closure away from horizontal. At
        # ``safety_margin=0.0`` (or a benign geometry where the geometry margin
        # is 1.0) ``eff_margin`` is exactly 1.0 and the block is skipped — a
        # provable byte-for-byte no-op that preserves the exact grazing angle.
        geo_margin = SafetyMarginCalculator.calculate(self.gamma, self.sol_elev)
        eff_margin = 1.0 + (geo_margin - 1.0) * self.tilt_config.safety_margin
        if eff_margin != 1.0:
            result = TILT_HORIZONTAL_DEG - (TILT_HORIZONTAL_DEG - result) * eff_margin

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
        # user-calibrated endpoint range via an affine transform — an offset
        # (angle_0) plus a scale — which the pure ``max_degrees`` denominator
        # below cannot express. Handle it here, before the polymorphic base
        # path, and correct the trace's position percentage in place.
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
            # A zero-width scale has no percentage to report. A *negative*
            # denominator is unreachable from config: both tilt-mode maxima are
            # nonzero and ``max_slat_angle`` is bounded to [0, 180]. A *zero*
            # one is reachable, though — ``AdaptiveLouveredRoofCover``
            # truncates ``max_slat_angle`` with ``int()``, so a fractional
            # value in (0, 1) lands on 0 rather than on the "use the mode max"
            # sentinel. That case already raises inside ``calculate_position``
            # above while it builds its trace, so this is the same failure
            # surfaced at a second seam rather than a new one. Raising keeps
            # the contract ``VenetianCoverCalculation._compute_tilt`` relies
            # on: a tilt geometry that does not resolve falls back to the
            # default position.
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
