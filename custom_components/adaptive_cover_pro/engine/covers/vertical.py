"""Vertical blind (up/down) cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, sin, tan
from numpy import radians as rad

from ...config_types import GlareZone, GlareZonesConfig, VerticalConfig
from ...const import (
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
)
from ...geometry import EdgeCaseHandler
from ...position_utils import PositionConverter
from ..sun_geometry import clamped_cos_gamma, ray_x_at_window_plane
from .base import AdaptiveGeneralCover

# --- Numeric guards (file-local) ---
# Minimum tan(elevation) before sill-offset division — corresponds to
# elevation ≈ 2.9°, below which the projected shadow is geometrically
# unbounded. Capping the divisor keeps sill_offset finite at low sun.
MIN_TAN_ELEVATION_CLAMP = 0.05


def _elevation_offset(height_m: float, sol_elev: float, gamma: float) -> float:
    """Perpendicular in-room depth a sun ray covers while descending `height_m`.

    Units contract: this returns a PERPENDICULAR depth (metres measured
    straight out from the glass plane), the same units as `distance`
    (`distance_shaded_area`) — never a ray path length. A sun ray at
    elevation `sol_elev` (degrees) descending a vertical distance `height_m`
    travels a horizontal PATH of `height_m / tan(sol_elev)`; only the
    component of that path normal to the window, `path * cos(gamma)`, counts
    as perpendicular depth. The denominator is clamped at
    MIN_TAN_ELEVATION_CLAMP so the offset stays finite at low sun, and the
    `cos(gamma)` factor goes through the shared one-sided `clamped_cos_gamma`
    guard (#1030) rather than a raw `cos(rad(gamma))`.

    Before this fix the helper returned the bare path length, which callers
    then subtracted from a perpendicular `distance` and divided by `cos(gamma)`
    a second time in `_project_drop` — over-subtracting the offset by a factor
    of `1/cos(gamma)` at every gamma != 0 (discussion #1283).

    Shared by sill_height geometry in calculate_position and the optional
    glare-zone Z (height above floor) offset — one change here fixes both
    call sites (CODING_GUIDELINES.md "No Code Duplication").
    """
    path_length = height_m / max(float(tan(rad(sol_elev))), MIN_TAN_ELEVATION_CLAMP)
    return path_length * clamped_cos_gamma(gamma)


def glare_zone_effective_distance(
    zone: GlareZone,
    gamma: float,
    sol_elev: float,
    window_half_width: float,
) -> float | None:
    """Convert a glare zone to an effective distance (metres) for this sun angle.

    Returns the perpendicular depth into the room (in metres) to the nearest
    edge of the zone circle facing the sun. Returns None if the sun cannot
    reach this zone through the window opening at angle gamma.

    A smaller return value means the zone is closer to the window and requires
    MORE blind coverage (lower position%) to protect. The GlareZoneHandler
    uses min() across zones to select the most restrictive (closest) zone.

    When `zone.z > 0` the target sits above the floor (eye level, tabletop, TV).
    The effective distance is then `nearest_y + z·cos(gamma)/tan(sol_elev)` —
    the same shared `_elevation_offset` helper (a PERPENDICULAR offset, not a
    ray path length — see its docstring, #1283) used by sill_offset in
    calculate_position, signed in the opposite direction.

    The `clamped_cos_gamma` floor is not a conservatism gap here. This distance
    is only ever consumed by re-entering `calculate_position` (`GlareZoneHandler`
    calls `cover.calculate_raw_percentage(effective_distance_override=min_distance)`,
    `glare_zone.py:114-115`), which projects it through `_project_drop`'s division
    by `clamped_cos_gamma(self.gamma)` — the same gamma, same floor. Multiply then
    divide by the same clamped cosine cancels exactly, so the z-term's contribution
    to the final position is exactly `z`, independent of gamma — the same
    cancellation as the sill case above (verified: elev=45°, gamma=89.6°, z=0.5 →
    position delta = 0.5 exactly, floor engaged or not).

    The one place this doesn't cancel: the raw `min_distance` feeds the
    zone-selection `min()` and the sun-tracking early-return gate at
    `glare_zone.py:109`, both evaluated *before* the re-entry above. There the
    floor can inflate the z-term by up to `z·MIN_COS_GAMMA_CLAMP/tan(sol_elev)` —
    ≤ `0.01·z` at elevations ≥45°, rising to `0.2·z` at the lowest elevations
    `MIN_TAN_ELEVATION_CLAMP` (0.05) allows — enough to shift which zone is
    selected or nudge the early-return gate by centimetres, never enough to
    reach the returned position.

    Pre-#1290 there was no cancellation at all: the z-term carried no
    `cos(gamma)` factor, so it passed into `_project_drop`'s division
    uncancelled and scaled as `z/clamped_cos_gamma(gamma)` — up to `100·z` at
    the floor. This fix removed that blowup; the residual above is what's left
    of it.

    Args:
        zone: The glare zone definition (x, y, radius, z — all in metres).
        gamma: Surface solar azimuth in degrees (positive = sun to the right).
        sol_elev: Sun elevation in degrees (used only when zone.z > 0).
        window_half_width: Half the window width in metres.

    """
    gamma_rad = rad(gamma)

    # First-hit point on the zone circle: the point facing the incoming sun.
    # Sun arrives from direction (sin γ, −cos γ) on the floor XY plane,
    # so the facing point is offset from centre in that direction.
    nearest_x = zone.x + zone.radius * float(sin(gamma_rad))
    nearest_y = zone.y - zone.radius * float(cos(gamma_rad))

    # Zone must be in front of the window wall
    if nearest_y <= 0:
        return None

    # Project back to find where the sun ray enters the window.
    # A ray hitting floor point (fx, fy) entered at x_w = fx + fy * tan(γ).
    x_at_window = ray_x_at_window_plane(nearest_x, nearest_y, gamma)
    if abs(x_at_window) > window_half_width:
        return None  # Ray enters outside the window opening — zone is naturally blocked

    if zone.z > 0:
        nearest_y += _elevation_offset(zone.z, sol_elev, gamma)

    return nearest_y


@dataclass
class AdaptiveVerticalCover(AdaptiveGeneralCover):
    """Calculate state for Vertical blinds."""

    vert_config: VerticalConfig = None  # type: ignore[assignment]

    @property
    def glare_zones(self) -> GlareZonesConfig | None:
        """Get glare zones config from vert_config."""
        return self.vert_config.glare_zones

    @property
    def distance(self) -> float:
        """Get distance from vert_config."""
        return self.vert_config.distance

    @property
    def h_win(self) -> float:
        """Get window height from vert_config."""
        return self.vert_config.h_win

    @property
    def window_depth(self) -> float:
        """Get window depth from vert_config."""
        return self.vert_config.window_depth

    @property
    def sill_height(self) -> float:
        """Get sill height from vert_config."""
        return self.vert_config.sill_height

    def _handle_edge_cases(self) -> tuple[bool, float]:
        """Handle extreme angles with safe fallbacks.

        Delegates to EdgeCaseHandler utility class.

        Returns:
            Tuple of (is_edge_case: bool, position: float)
            - is_edge_case: True if edge case detected
            - position: Safe fallback position (only valid if is_edge_case=True)

        """
        return EdgeCaseHandler.check_and_handle(
            self.sol_elev, self.gamma, self.distance, self.h_win
        )

    def _build_vertical_trace(
        self,
        *,
        edge_case_detected: bool,
        safety_margin: float,
        effective_distance: float,
        effective_distance_source: str,
        window_depth_contribution: float,
        sill_height_offset: float,
        cos_gamma: float,
        cos_gamma_clamped: float,
        path_length: float,
        base_height: float,
        adjusted_height: float,
        result: float,
        clamped_to_window: bool,
    ) -> dict:
        """Assemble the raw vertical solar-calculation trace (issue #682).

        Single source for all three ``calculate_position`` return paths — the
        edge-case return, the lintel-gate return (#1169), and the normal
        return — so the key set never drifts between them. Values are raw
        native floats — rounding happens at the presentation boundary
        (``DiagnosticsBuilder``), never here. ``glare_zones_active`` is left
        empty; the GlareZoneHandler populates it downstream via diagnostics.
        """
        return {
            TRACE_KEY_SOL_ELEV_DEG: float(self.sol_elev),
            TRACE_KEY_GAMMA_DEG: float(self.gamma),
            TRACE_KEY_POSITION_PCT: PositionConverter.to_percentage(result, self.h_win),
            "edge_case_detected": bool(edge_case_detected),
            "effective_distance_m": effective_distance,
            "effective_distance_source": effective_distance_source,
            "window_depth_contribution_m": window_depth_contribution,
            "sill_height_offset_m": sill_height_offset,
            "safety_margin": safety_margin,
            "glare_zones_active": [],
            "cos_gamma": cos_gamma,
            "cos_gamma_clamped": cos_gamma_clamped,
            "path_length_m": path_length,
            "base_height_m": base_height,
            "adjusted_height_m": adjusted_height,
            "clamped_to_window": bool(clamped_to_window),
        }

    def _project_drop(
        self, effective_distance: float
    ) -> tuple[float, float, float, float]:
        """Project the protected horizontal distance onto the vertical glass.

        Returns ``(base_height, cos_gamma, cos_gamma_clamped, path_length)``.

        Factored out of ``calculate_position`` so pitched-glass cover types
        (roof / skylight windows) can re-project the *same* effective distance
        onto a tilted plane without duplicating the surrounding edge-case /
        window-depth / sill pipeline (CODING_GUIDELINES.md "Code duplication
        is not okay").

        The divisor comes from the shared one-sided ``clamped_cos_gamma`` guard
        (#1030); the raw ``cos_gamma`` is still returned for the #682 trace.
        """
        cos_gamma = float(cos(rad(self.gamma)))
        cos_gamma_clamped = clamped_cos_gamma(self.gamma)
        path_length = effective_distance / cos_gamma_clamped
        base_height = path_length * float(tan(rad(self.sol_elev)))
        return base_height, cos_gamma, cos_gamma_clamped, path_length

    def calculate_position(
        self, effective_distance_override: float | None = None
    ) -> float:
        """Calculate blind height with enhanced geometric accuracy.

        Phase 1 (Automatic):
        - Edge case handling: Safe fallbacks for extreme sun angles

        Phase 2 (Optional):
        - Window depth: a binary full-open gate for window reveals/frames
          (0.0-5.0m) — see the lintel-gate comment below (#1169)
        - Sill height: Accounts for windows not starting at floor level (0.0-3.0m)

        Args:
            effective_distance_override: When provided by a pipeline handler (e.g.
                GlareZoneHandler), use this as the effective base distance instead
                of self.distance. Window depth and sill adjustments still apply.

        Returns:
            Blind height in meters (0 to h_win).

        """
        # Check edge cases first
        is_edge_case, edge_position = self._handle_edge_cases()
        if is_edge_case:
            self.logger.debug(
                "Vertical calc: edge case detected (elev=%.1f°, gamma=%.1f°) → %.3fm",
                self.sol_elev,
                self.gamma,
                edge_position,
            )
            self._last_calc_details = self._build_vertical_trace(
                edge_case_detected=True,
                safety_margin=1.0,
                effective_distance=float(self.distance),
                effective_distance_source="edge_case",
                window_depth_contribution=0.0,
                sill_height_offset=0.0,
                cos_gamma=float(cos(rad(self.gamma))),
                cos_gamma_clamped=float(cos(rad(self.gamma))),
                path_length=0.0,
                base_height=0.0,
                adjusted_height=0.0,
                result=edge_position,
                clamped_to_window=False,
            )
            return edge_position

        # Use override from handler (e.g. GlareZoneHandler) or base distance
        if effective_distance_override is not None:
            effective_distance_base = effective_distance_override
            effective_distance_source = "glare_zone"
        else:
            effective_distance_base = self.distance
            effective_distance_source = "base"

        effective_distance = effective_distance_base

        # Account for window sill height (window not starting at floor)
        sill_offset = 0.0
        if self.sill_height > 0:
            sill_offset = _elevation_offset(self.sill_height, self.sol_elev, self.gamma)
            effective_distance -= sill_offset

        # ── Sill geometry — why negative effective_distance means FULLY CLOSED ────────
        # "Position" = exposed glass from the bottom (0 = fully closed, h_win = open).
        # Window plane at y=0 (the glass), room interior at y>0. A ray enters the
        # glass at height H above the FLOOR (H = sill_height + position — the top of
        # the exposed band) at surface-solar-azimuth γ and elevation θ. The ray's
        # horizontal PATH before it reaches the floor is L = H / tan(θ); only the
        # component of that path NORMAL to the window counts as perpendicular
        # penetration into the room: y = L·cos(γ) = H·cos(γ) / tan(θ).
        #
        # Contract: the ray must not penetrate past shaded_distance (D), i.e. y ≤ D:
        #   H·cos(γ) / tan(θ) ≤ D  ⟺  H ≤ D·tan(θ) / cos(γ)
        #
        # With H = sill_height + position, solving for position and clipping to the
        # physical window range gives
        #   position = clip(D·tan(θ)/cos(γ) − sill_height, 0, h_win)
        #
        # `_elevation_offset` returns the PERPENDICULAR sill offset
        # (sill_height·cos(γ)/tan(θ)) — the same units as `distance` — so
        #   effective_distance = distance − sill_offset          (perpendicular − perpendicular)
        #   position           = effective_distance·tan(θ) / cos(γ)   (via _project_drop)
        # is algebraically identical to the formula above for EVERY γ, not only γ = 0 —
        # PROVIDED θ is above the sill division's own clamp. `_elevation_offset` divides
        # by tan(θ) floored at MIN_TAN_ELEVATION_CLAMP (0.05, θ ≈ 2.9°), while
        # `_project_drop` multiplies by the raw, unclamped tan(θ); below ≈2.9° elevation
        # the two no longer use the same tan(θ), so the cancellation is inexact there.
        # This asymmetry predates #1283's fix and is unrelated to it.
        # (Before #1283's fix, `_elevation_offset` returned a ray PATH LENGTH instead of
        # a perpendicular offset, so this same-looking pair of lines silently
        # over-subtracted the sill by a factor of 1/cos(γ) at every γ != 0.)
        #
        # When effective_distance ≤ 0, even the LOWEST glass entry (H = sill_height,
        # i.e. position = 0) already violates the contract above. Every higher entry
        # is worse. The blind must be FULLY CLOSED (position=0).
        #
        # Issue #304 short-circuited here with `return h_win` (fully open), which is
        # the geometric inverse of the correct answer. Issue #358 restores the clamp so
        # the normal path below naturally produces position=0 when effective_distance=0.
        if effective_distance < 0:
            effective_distance = 0.0

        # Base calculation: project the protected distance to a blind drop.
        base_height, cos_gamma, cos_gamma_clamped, path_length = self._project_drop(
            effective_distance
        )

        # ── Lintel gate — window depth is a full-open threshold, not a continuous
        # term (#1169) ───────────────────────────────────────────────────────────
        # The reveal soffit shadows the TOP window_depth·tan(elev)/cos(gamma) of
        # the glass — the same band ``position`` already covers from the top. That
        # shadow can never license a PARTIAL opening (it never protects territory
        # the blind wasn't already covering); it can only ever push the blind to
        # FULLY open, once the reveal shadow and the blind's own coverage together
        # span the whole pane. Re-projecting ``effective_distance + window_depth``
        # gives exactly ``base_height + lintel_shadow`` (the identity holds
        # exactly, no separate trig): if that reaches h_win, every ray is either
        # stopped by the blind or by the reveal, so open fully. Routed through
        # ``_project_drop`` (never a hand-rolled ``cos(rad(gamma))``) so pitched
        # glass (#212 roof-window) gets the right answer through its own override,
        # and so the one-sided ``clamped_cos_gamma`` guard (#1030) still applies.
        depth_contribution = 0.0
        if self.window_depth > 0:
            gated_height, *_ = self._project_drop(
                effective_distance + self.window_depth
            )
            depth_contribution = gated_height - base_height  # lintel shadow, metres
            if gated_height >= self.h_win:
                result = float(np.clip(gated_height, 0, self.h_win))
                clamped_to_window = bool(gated_height > self.h_win)
                self.logger.debug(
                    "Vertical calc: elev=%.1f°, gamma=%.1f°, effective_distance=%.3f "
                    "(sill=%.3f) → base=%.3fm + lintel shadow=%.3fm = %.3fm reaches "
                    "h_win=%.3fm → fully open",
                    self.sol_elev,
                    self.gamma,
                    effective_distance,
                    sill_offset,
                    base_height,
                    depth_contribution,
                    gated_height,
                    self.h_win,
                )
                self._last_calc_details = self._build_vertical_trace(
                    edge_case_detected=False,
                    safety_margin=1.0,
                    effective_distance=float(effective_distance),
                    effective_distance_source=effective_distance_source,
                    window_depth_contribution=float(depth_contribution),
                    sill_height_offset=float(sill_offset),
                    cos_gamma=float(cos_gamma),
                    cos_gamma_clamped=float(cos_gamma_clamped),
                    path_length=float(path_length),
                    base_height=float(base_height),
                    # The vertical axis has no safety-margin step at all now
                    # (#1173), so report the gate path consistent with every
                    # other branch: adjusted_height_m == base_height_m. The
                    # gated height that actually drove the full-open decision
                    # is still fully recoverable as base_height_m +
                    # window_depth_contribution_m (the lintel shadow), so
                    # nothing is lost. The trade is that clamped_to_window is
                    # now True here while adjusted_height_m sits below h_win —
                    # on this path the flag reports the gated height, not the
                    # adjusted one.
                    adjusted_height=float(base_height),
                    result=result,
                    clamped_to_window=clamped_to_window,
                )
                return result

        # No safety margin on this axis (#1173): `position` is already the
        # exposed-glass boundary derived above — `base_height` sits exactly
        # on it with zero headroom (see the sill-geometry comment block).
        # Multiplying it by anything > 1.0 can only push the exposed band
        # PAST that boundary, i.e. it lets more sun in, never less — the
        # opposite of a safety margin. The angle-dependent margin from
        # SafetyMarginCalculator is applied correctly elsewhere: on the tilt
        # axis (`tilt.py:304-331`, #783/#1089) it closes the slats further
        # instead of opening the blind, which is the direction that
        # actually adds slack.
        adjusted_height = base_height
        result = float(np.clip(adjusted_height, 0, self.h_win))
        clamped_to_window = bool(adjusted_height > self.h_win)

        self.logger.debug(
            "Vertical calc: elev=%.1f°, gamma=%.1f°, dist=%.3f→%.3f (sill=%.3f), "
            "base=%.3f, lintel_shadow=%.3f (below gate), adjusted=%.3f, "
            "clipped=%.3f, source=%s",
            self.sol_elev,
            self.gamma,
            self.distance,
            effective_distance,
            sill_offset,
            base_height,
            depth_contribution,
            adjusted_height,
            result,
            effective_distance_source,
        )
        self._last_calc_details = self._build_vertical_trace(
            edge_case_detected=False,
            safety_margin=1.0,
            effective_distance=float(effective_distance),
            effective_distance_source=effective_distance_source,
            window_depth_contribution=float(depth_contribution),
            sill_height_offset=float(sill_offset),
            cos_gamma=float(cos_gamma),
            cos_gamma_clamped=float(cos_gamma_clamped),
            path_length=float(path_length),
            base_height=float(base_height),
            adjusted_height=float(adjusted_height),
            result=result,
            clamped_to_window=clamped_to_window,
        )
        return result

    def calculate_percentage(
        self, effective_distance_override: float | None = None
    ) -> float:
        """Convert blind height to percentage for Home Assistant.

        Args:
            effective_distance_override: Passed through to calculate_position().
                Used by GlareZoneHandler to override base distance.

        Returns:
            Position as percentage (0-100).

        """
        position = self.calculate_position(effective_distance_override)
        self.logger.debug(
            "Converting height to percentage: %s / %s * 100", position, self.h_win
        )
        return PositionConverter.to_percentage(position, self.h_win)

    def calculate_raw_percentage(
        self, effective_distance_override: float | None = None
    ) -> float:
        """Unrounded geometry fraction for directional rounding (issue #978).

        Bypasses the ``round()`` inside ``PositionConverter.to_percentage`` so
        that callers can apply ``floor()`` / ``ceil()`` / ``round()`` as needed.
        Accepts the same *effective_distance_override* as
        :meth:`calculate_percentage` so the glare-zone handler can use it
        without the internal ``round()`` applied by that method.
        """
        position = self.calculate_position(effective_distance_override)
        return (float(position) / self.h_win) * 100.0
