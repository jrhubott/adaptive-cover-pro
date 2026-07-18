"""Tilted/venetian slat cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, tan
from numpy import radians as rad

from ...config_types import TiltConfig
from ...const import (
    TILT_HORIZONTAL_DEG,
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
    TiltMode,
)
from ...geometry import SafetyMarginCalculator
from ...position_utils import PositionConverter
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

        Returns:
            Beta angle in radians.

        """
        beta = np.arctan(tan(rad(self.sol_elev)) / cos(rad(self.gamma)))
        return beta

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

    def _percentage_from_specified_angles(self, raw_angle: float) -> float:
        """Map a target raw slat angle to the configured tilt percentage.

        The solver and the configured endpoints both use ACP's raw/card angle
        convention: 0° closed downward, 90° horizontal, 180° closed upward.
        """
        travel = self.angle_100 - self.angle_0
        if travel == 0:
            return 0.0

        target_angle = self._specified_target_angle(raw_angle)
        return ((target_angle - self.angle_0) / travel) * 100.0

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
            percentage = self._percentage_from_specified_angles(position)
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
        applies the identical limits itself, so this is skipped there.
        """
        if not self.apply_tilt_axis_limits:
            return pct
        cfg = self.tilt_config
        limited = PositionConverter.apply_tilt_limits(
            int(round(pct)),
            cfg.min_tilt,
            cfg.max_tilt,
            cfg.min_tilt_sun_only,
            cfg.max_tilt_sun_only,
            sun_valid=True,
            transform=cfg.tilt_transform,
        )
        # The shared primitive is int-valued, but ``calculate_percentage`` has
        # always returned a float — specify-angles yields a fractional percent
        # the pipeline rounds downstream. When the band leaves the rounded value
        # untouched (a no-op / within-band clamp), keep the exact float so that
        # precision is preserved byte-for-byte; only substitute the primitive's
        # value when it actually moved the tilt (a cap, floor, or transform bit).
        if limited == int(round(pct)):
            return pct
        return float(limited)
