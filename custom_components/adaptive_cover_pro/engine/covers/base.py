"""Abstract base class for all adaptive cover calculations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from math import ceil, cos, floor, radians

from ...config_context_adapter import ConfigContextAdapter
from ...config_types import CoverConfig
from ...const import ReasonCode
from ...reason_i18n import Reason, render_en
from ...sun import SunData
from ..sun_geometry import SunGeometry, azimuth_within_fov

_COVER_CONFIG_FIELDS = frozenset(
    {
        "win_azi",
        "fov_left",
        "fov_right",
        "h_def",
        "sunset_pos",
        "sunset_off",
        "sunrise_off",
        "max_pos",
        "min_pos",
        "max_pos_sun_only",
        "min_pos_sun_only",
        "blind_spot_left",
        "blind_spot_right",
        "blind_spot_elevation",
        "blind_spot_on",
        "min_elevation",
        "max_elevation",
    }
)

_COVER_CONFIG_RENAMES = {
    "max_pos_bool": "max_pos_sun_only",
    "min_pos_bool": "min_pos_sun_only",
}

_VERT_CONFIG_FIELDS = frozenset({"distance", "h_win", "window_depth", "sill_height"})
_HORIZ_CONFIG_FIELDS = frozenset({"awn_length", "awn_angle"})
_TILT_CONFIG_FIELDS = frozenset({"slat_distance", "depth", "mode"})


@dataclass
class AdaptiveGeneralCover(ABC):
    """Collect common data."""

    logger: ConfigContextAdapter
    sol_azi: float
    sol_elev: float
    sun_data: SunData
    config: CoverConfig

    @property
    def solar(self) -> SunGeometry:
        """Build a SunGeometry from current field values (always fresh).

        ``eval_time`` is read via ``getattr`` rather than being a dataclass
        field: the base carries no default field of its own (subclasses add
        non-default config fields after it, which would break dataclass field
        ordering). The forecast sets ``cover.eval_time`` dynamically per sample
        so its time-dependent gates are evaluated at the projected time. The
        live anticipation look-ahead
        (:func:`pipeline.helpers.anticipated_solar_position_from_geometry`,
        since PR #617) sets it the same way on the ``dataclasses.replace`` copies
        it probes; the *current* cover instance is never given an
        ``eval_time`` of its own, so its gates still evaluate at wall-clock
        now.
        """
        return SunGeometry(
            self.sol_azi,
            self.sol_elev,
            self.sun_data,
            self.config,
            self.logger,
            eval_time=getattr(self, "eval_time", None),
        )

    def __getattr__(self, name: str) -> object:
        """Route old flat field names to the appropriate config dataclass for reads.

        Note: __getattr__ is only called when normal lookup fails, so this
        won't intercept accesses to real dataclass fields (logger, sol_azi, etc.).
        """
        canonical = _COVER_CONFIG_RENAMES.get(name, name)
        if canonical in _COVER_CONFIG_FIELDS:
            # Access config via __dict__ to avoid infinite recursion
            config = object.__getattribute__(self, "config")
            return getattr(config, canonical)
        if canonical in _VERT_CONFIG_FIELDS:
            try:
                vert_config = object.__getattribute__(self, "vert_config")
                return getattr(vert_config, canonical)
            except AttributeError:
                pass
        if canonical in _TILT_CONFIG_FIELDS:
            try:
                tilt_config = object.__getattribute__(self, "tilt_config")
                return getattr(tilt_config, canonical)
            except AttributeError:
                pass
        if canonical in _HORIZ_CONFIG_FIELDS:
            try:
                horiz_config = object.__getattribute__(self, "horiz_config")
                return getattr(horiz_config, canonical)
            except AttributeError:
                pass
        msg = f"'{type(self).__name__}' object has no attribute '{name}'"
        raise AttributeError(msg)

    def __setattr__(self, name: str, value: object) -> None:
        """Route old flat field names to the appropriate config dataclass for writes."""
        canonical = _COVER_CONFIG_RENAMES.get(name, name)
        if canonical in _COVER_CONFIG_FIELDS:
            try:
                object.__setattr__(self.config, canonical, value)
            except AttributeError:
                # During __init__, self.config may not exist yet
                object.__setattr__(self, name, value)
            return
        if canonical in _VERT_CONFIG_FIELDS and hasattr(self, "vert_config"):
            object.__setattr__(self.vert_config, canonical, value)
            return
        if canonical in _TILT_CONFIG_FIELDS and hasattr(self, "tilt_config"):
            object.__setattr__(self.tilt_config, canonical, value)
            return
        if canonical in _HORIZ_CONFIG_FIELDS and hasattr(self, "horiz_config"):
            object.__setattr__(self.horiz_config, canonical, value)
            return
        object.__setattr__(self, name, value)

    # ------------------------------------------------------------------
    # Leaf properties delegated to SunGeometry
    # ------------------------------------------------------------------

    @property
    def azi_min_abs(self) -> int:
        """Delegate to SunGeometry.azi_min_abs."""
        return self.solar.azi_min_abs

    @property
    def azi_max_abs(self) -> int:
        """Delegate to SunGeometry.azi_max_abs."""
        return self.solar.azi_max_abs

    @property
    def gamma(self) -> float:
        """Delegate to SunGeometry.gamma."""
        return self.solar.gamma

    @property
    def cos_aoi(self) -> float:
        """Cosine of the angle of incidence on this cover's own plane (``s·n``).

        Polymorphic hook, same shape as :attr:`fov_angle`. The default is the
        vertical facade, ``cos(elev)·cos(gamma)``; cover types whose working
        plane is not vertical (roof window, louvered roof) override it with the
        pitched-plane form. Positive means the sun strikes the outer face.
        """
        return cos(radians(self.sol_elev)) * cos(radians(self.gamma))

    @property
    def sun_behind_plane(self) -> bool:
        """Whether the sun is on the far side of this cover's plane (#1030).

        The single predicate both the illumination gate and the reason branch
        read. ``cos(AOI) <= 0`` means the face receives no direct sun at all, so
        the shading projection has no answer — asking it anyway is what made
        ``tan(elev)/cos(gamma)`` flip sign past ``|gamma| = 90`` and slam
        vertical / tilt / venetian covers to an endpoint (and drive a drop-arm
        awning to full extension). Cover types whose shade target is not the
        glass — an area-mode patio awning — override this.
        """
        return self.cos_aoi <= 0.0

    @property
    def valid_elevation(self) -> bool:
        """Configured elevation bounds AND the sun striking the outer face.

        The bounds come from ``SunGeometry``; the illumination clause is added
        here because "is this plane lit?" is a per-geometry question that
        ``SunGeometry`` (cover-type agnostic) deliberately does not answer.
        """
        return bool(self.solar.valid_elevation and not self.sun_behind_plane)

    @property
    def sunset_valid(self) -> bool:
        """Delegate to SunGeometry.sunset_valid."""
        return self.solar.sunset_valid

    @property
    def is_sun_in_blind_spot(self) -> bool:
        """Whether the sun is in a blind spot, in the acceptance frame (#913).

        Evaluates the blind-spot wedge in the SAME frame the FOV gate accepts —
        the polymorphic ``fov_angle`` — so an obstruction the glass physically
        responds to is reachable. For vertical / awning / tilt / venetian covers
        ``fov_angle == gamma``, so this is bit-for-bit identical to the raw
        ``SunGeometry.is_sun_in_blind_spot``; for a pitched roof window it uses
        the tilted-plane effective gamma.
        """
        return self.solar.is_sun_in_blind_spot_at(self.fov_angle)

    @property
    def fov_angle(self) -> float:
        """Azimuth angle compared against the FOV (vertical = horizontal gamma).

        Polymorphic hook: cover types whose acceptance cone is not the raw
        horizontal azimuth (e.g. a pitched roof window, where the gate must be
        measured in the tilted glass plane — #212) override this. The position
        projection still uses the raw ``gamma``; only the FOV gate reads here.
        """
        return self.gamma

    @property
    def in_fov(self) -> bool:
        """Whether the sun azimuth is within the FOV (elevation ignored)."""
        return azimuth_within_fov(
            self.fov_angle, self.config.fov_left, self.config.fov_right
        )

    def solar_times(self) -> tuple[datetime | None, datetime | None]:
        """Delegate to the SunGeometry solar_times helper."""
        return self.solar.solar_times()

    def solar_times_with_position(
        self,
    ) -> tuple[
        tuple[datetime, float, float] | None,
        tuple[datetime, float, float] | None,
    ]:
        """Delegate to the SunGeometry solar_times_with_position helper."""
        return self.solar.solar_times_with_position()

    # ------------------------------------------------------------------
    # Compound properties (kept here so tests can patch individual parts)
    # ------------------------------------------------------------------

    @property
    def _get_azimuth_edges(self) -> tuple[int, int]:
        """Get absolute azimuth boundaries of window's field of view."""
        return (self.azi_min_abs, self.azi_max_abs)

    @property
    def valid(self) -> bool:
        """Check if sun is in front of window within field of view."""
        valid = bool(
            azimuth_within_fov(
                self.fov_angle, self.config.fov_left, self.config.fov_right
            )
            and self.valid_elevation
        )
        self.logger.debug("Sun in front of window (ignoring blindspot)? %s", valid)
        return valid

    def fov(self) -> list[int]:
        """Get absolute azimuth boundaries of field of view."""
        return [self.azi_min_abs, self.azi_max_abs]

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front with no exclusions."""
        result = self.valid and not self.sunset_valid and not self.is_sun_in_blind_spot
        self.logger.debug(
            "direct_sun_valid=%s (valid=%s, sunset_valid=%s, in_blind_spot=%s)",
            result,
            self.valid,
            self.sunset_valid,
            self.is_sun_in_blind_spot,
        )
        return result

    @property
    def control_state_reason_code(self) -> ReasonCode:
        """Return the stable :class:`ReasonCode` for the current control state.

        The pure engine emits a frozen code (no HA, no translation); the prose
        is resolved at the diagnostics/sensor boundary. Branches mirror
        :attr:`control_state_reason` exactly.
        """
        if self.direct_sun_valid:
            return ReasonCode.ENGINE_DIRECT_SUN
        if self.sunset_valid:
            return ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET
        if not self.valid:
            # An unlit face outranks the elevation clause: reporting "Elevation
            # Limit" for a sun behind the plane sends the user to a bound that
            # is satisfied (#1030 — also the roof window's pre-existing
            # mis-attribution).
            if self.sun_behind_plane:
                return ReasonCode.ENGINE_DEFAULT_SUN_BEHIND_PLANE
            if not self.valid_elevation:
                return ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT
            return ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT
        if self.is_sun_in_blind_spot:
            return ReasonCode.ENGINE_DEFAULT_BLIND_SPOT
        return ReasonCode.ENGINE_DEFAULT

    @property
    def control_state_reason(self) -> str:
        """Determine why the cover is tracking the sun or using the default position.

        Byte-identical English shim over :attr:`control_state_reason_code`.
        """
        return render_en(Reason(self.control_state_reason_code))

    @abstractmethod
    def calculate_position(self) -> float:
        """Calculate the position of the blind."""

    @abstractmethod
    def calculate_percentage(self) -> int:
        """Calculate percentage from position."""

    def calculate_raw_percentage(self) -> float:
        """Raw geometry percentage before final integer rounding (issue #978).

        Default delegates to ``calculate_percentage()``. That is only correct
        for subclasses whose ``calculate_percentage`` already returns an
        unrounded float — oscillating (arc solve) and the sliding curtain
        (continuous interval solve). Subclasses that round internally override
        this to expose the unrounded fraction: vertical and horizontal round via
        ``PositionConverter.to_percentage``; tilt rounds on its legacy/custom-max
        path. Exposing the raw fraction lets
        :func:`pipeline.helpers.solar_position_from_geometry` apply directional
        (conservative) rounding without the pre-rounding neutralising the
        direction signal.
        """
        return float(self.calculate_percentage())

    def round_toward_coverage(self, pct: float, *, full_coverage_at_zero: bool) -> int:
        """Quantise a raw geometry percentage toward MORE sun coverage (#978).

        Polymorphic hook — the single definition of "which way do we round" for
        both the solar branch (:func:`pipeline.helpers.solar_position_from_geometry`)
        and venetian's dual-axis tilt sub-path
        (``VenetianCoverCalculation._compute_tilt``), so the two can never drift
        apart again (issue #1090).

        *full_coverage_at_zero* is the axis-level semantic the caller reads off
        ``CoverTypePolicy.axes[n].open_blocks_sun``: ``True`` means 0 % is the
        fully-covering end (blind / tilt / venetian) so we round down, ``False``
        means 100 % is (awning) so we round up. That holds for every axis whose
        coverage is MONOTONIC in the percentage — which is every axis except a
        bi-directional slat, where :class:`~.tilt.AdaptiveTiltCover` overrides
        this with an angle-aware direction.
        """
        return floor(pct) if full_coverage_at_zero else ceil(pct)

    def coverage_pivot_percentage(self) -> float | None:
        """Percentage at which this axis blocks the LEAST sun, or ``None`` (#1104).

        The companion of :meth:`round_toward_coverage`, and polymorphic for the
        same reason: rounding needs to know which arithmetic direction adds
        coverage, and every *other* consumer of "how much coverage does this
        percentage carry" needs to know where coverage starts from.

        ``None`` — the base answer — means coverage is MONOTONIC in the
        percentage, so the caller's ``full_coverage_at_zero`` axis semantic is
        already the whole story: coverage runs from one end of the travel to the
        other and the open end is 0 % or 100 %. A float means the axis is
        bi-directional: coverage bottoms out at that percentage and grows as the
        position leaves it in EITHER direction, so a distance-from-the-pivot
        measure replaces "distance from the open end".

        The float is a point on this axis's SCALE, and it is NOT promised to lie
        on the 0–100 travel: it is the maximum-openness angle pushed through the
        engine's own angle→percentage map, and a scale calibrated entirely to one
        side of that angle images it outside the range (a louvered roof with
        ``max_slat_angle`` under 90°, or a one-sided ``specify_angles`` pair).
        That is still the right answer for consumers that only ORDER by it —
        the map is affine, so distance from the pivot stays proportional to
        distance from maximum openness wherever the pivot sits, which is what
        :meth:`round_toward_coverage` and
        ``CoverTypePolicy.more_protective_position`` need. It is also the answer
        that tells them which END of an off-travel scale covers, which the
        axis's static ``open_blocks_sun`` flag gets backwards on an inverted
        calibration. A consumer that ANCHORS arithmetic on the pivot must pull
        it onto the reachable travel first; the one that does,
        ``PositionConverter.quantize_to_coverage_steps``, carries the clamp and
        its rationale.

        Only :class:`~.tilt.AdaptiveTiltCover` overrides it, because only the
        engine knows the tilt scale — which keeps the cover-type branch out of
        the pipeline and ``position_utils`` layers that consume the answer.
        """
        return None

    def coverage_travel_bounds(self) -> tuple[float, float]:
        """Lowest and highest percentage this axis can be COMMANDED to (#1104).

        The companion of :meth:`coverage_pivot_percentage` for the one consumer
        that anchors arithmetic on the pivot rather than merely ordering by it:
        ``PositionConverter.quantize_to_coverage_steps`` spans its coverage
        levels from the pivot outward, so BOTH ends of that span have to be
        positions the axis can actually reach — the far one because it is where
        the top level lands, the near one because an anchor the axis cannot
        reach reads a zero-coverage demand as a covered one.

        The base answer is the whole travel, which makes the bound a no-op —
        correct for every engine whose own configuration cannot narrow the
        percentage range it emits. :class:`~.tilt.AdaptiveTiltCover` overrides
        it, because ``[min_tilt, max_tilt]`` narrows exactly that, and the
        quantiser runs after the last seam that applies the band.

        Note this is deliberately NOT the position axis's ``min_pos``/``max_pos``:
        those are applied by ``pipeline.helpers.apply_config_limits`` AFTER the
        quantiser, so that band clamps the command itself and needs no help from
        the level arithmetic to hold.
        """
        return (0.0, 100.0)
