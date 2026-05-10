"""Vertical blind (up/down) cover calculation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy import cos, sin, tan
from numpy import radians as rad

from ...config_types import GlareZone, GlareZonesConfig, VerticalConfig
from ...const import WINDOW_DEPTH_GAMMA_THRESHOLD
from ...geometry import EdgeCaseHandler, SafetyMarginCalculator
from ...position_utils import PositionConverter
from .base import AdaptiveGeneralCover

# --- Numeric guards (file-local) ---
# Minimum tan(elevation) before sill-offset division — corresponds to
# elevation ≈ 2.9°, below which the projected shadow is geometrically
# unbounded. Capping the divisor keeps sill_offset finite at low sun.
MIN_TAN_ELEVATION_CLAMP = 0.05
# Minimum |cos(gamma)| before path-length division — corresponds to gamma
# ≈ 89.4°. Bridges the gap between the edge-case threshold (85°) and the
# 90° singularity where cos(gamma) → 0.
MIN_COS_GAMMA_CLAMP = 0.01


def glare_zone_effective_distance(
    zone: GlareZone,
    gamma: float,
    window_half_width: float,
) -> float | None:
    """Convert a glare zone to an effective distance (metres) for this sun angle.

    Returns the perpendicular depth into the room (in metres) to the nearest
    edge of the zone circle facing the sun. Returns None if the sun cannot
    reach this zone through the window opening at angle gamma.

    A smaller return value means the zone is closer to the window and requires
    MORE blind coverage (lower position%) to protect. The GlareZoneHandler
    uses min() across zones to select the most restrictive (closest) zone.

    Args:
        zone: The glare zone definition (x, y, radius — all in metres).
        gamma: Surface solar azimuth in degrees (positive = sun to the right).
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
    x_at_window = nearest_x + nearest_y * float(tan(gamma_rad))
    if abs(x_at_window) > window_half_width:
        return None  # Ray enters outside the window opening — zone is naturally blocked

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

    def _calculate_safety_margin(self, gamma: float, sol_elev: float) -> float:
        """Calculate angle-dependent safety margin multiplier (≥1.0).

        Delegates to SafetyMarginCalculator utility class.

        Args:
            gamma: Surface solar azimuth in degrees (-180 to 180)
            sol_elev: Sun elevation angle in degrees (0-90)

        Returns:
            Safety margin multiplier (1.0 to 1.45)

        """
        return SafetyMarginCalculator.calculate(gamma, sol_elev)

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

    def calculate_position(
        self, effective_distance_override: float | None = None
    ) -> float:
        """Calculate blind height with enhanced geometric accuracy.

        Phase 1 (Automatic):
        - Edge case handling: Safe fallbacks for extreme sun angles
        - Safety margins: Angle-dependent multipliers (1.0-1.45x)

        Phase 2 (Optional):
        - Window depth: Accounts for window reveals/frames (0.0-0.5m)
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
            self._last_calc_details = {
                "edge_case_detected": True,
                "safety_margin": 1.0,
                "effective_distance": self.distance,
                "window_depth_contribution": 0.0,
                "sill_height_offset": 0.0,
                "glare_zones_active": [],
                "effective_distance_source": "edge_case",
            }
            return edge_position

        # Use override from handler (e.g. GlareZoneHandler) or base distance
        if effective_distance_override is not None:
            effective_distance_base = effective_distance_override
            effective_distance_source = "glare_zone"
        else:
            effective_distance_base = self.distance
            effective_distance_source = "base"

        effective_distance = effective_distance_base

        # Account for window depth at angles (creates additional shadow)
        depth_contribution = 0.0
        if self.window_depth > 0 and abs(self.gamma) > WINDOW_DEPTH_GAMMA_THRESHOLD:
            depth_contribution = self.window_depth * float(sin(rad(abs(self.gamma))))
            effective_distance += depth_contribution

        # Account for window sill height (window not starting at floor)
        sill_offset = 0.0
        if self.sill_height > 0:
            sill_offset = self.sill_height / max(
                float(tan(rad(self.sol_elev))), MIN_TAN_ELEVATION_CLAMP
            )
            effective_distance -= sill_offset

        # ── Sill geometry — why negative effective_distance means FULLY CLOSED ────────
        # "Position" = exposed glass from the bottom (0 = fully closed, h_win = open).
        # A ray entering the glass at height h from the floor travels into the room at
        # angle θ (sun elevation). At horizontal distance d from the window the ray is
        # at height  h − d·tan(θ).
        #
        # At d = shaded_distance (the protected-zone boundary):
        #   • h > shaded_distance·tan(θ):  ray is ABOVE the floor at the boundary —
        #     it has not hit anything and keeps travelling deeper into the room.
        #     This counts as sun penetration past the protected zone.
        #   • h ≤ shaded_distance·tan(θ):  ray hits the floor at or before the boundary.
        #
        # To stop ALL rays at the boundary, the top of exposed glass must satisfy
        #   h_top ≤ shaded_distance·tan(θ)
        # giving  position = clip(shaded_distance·tan(θ) − sill_height, 0, h_win),
        # equivalently  effective_distance = max(distance − sill_offset, 0)
        #               position           = effective_distance·tan(θ) / cos(γ)
        #
        # When effective_distance ≤ 0, even the LOWEST glass entry (at sill_height)
        # produces a ray that is still above the floor at the boundary. Every higher
        # entry is worse. The blind must be FULLY CLOSED (position=0).
        #
        # Issue #304 short-circuited here with `return h_win` (fully open), which is
        # the geometric inverse of the correct answer. Issue #358 restores the clamp so
        # the normal path below naturally produces position=0 when effective_distance=0.
        if effective_distance < 0:
            effective_distance = 0.0

        # Base calculation: project to vertical blind height.
        cos_gamma = float(cos(rad(self.gamma)))
        cos_gamma_clamped = max(abs(cos_gamma), MIN_COS_GAMMA_CLAMP) * (
            1 if cos_gamma >= 0 else -1
        )
        path_length = effective_distance / cos_gamma_clamped
        base_height = path_length * float(tan(rad(self.sol_elev)))

        # Apply safety margin for extreme angles
        safety_margin = self._calculate_safety_margin(self.gamma, self.sol_elev)
        adjusted_height = base_height * safety_margin
        result = float(np.clip(adjusted_height, 0, self.h_win))

        self.logger.debug(
            "Vertical calc: elev=%.1f°, gamma=%.1f°, dist=%.3f→%.3f "
            "(depth=%.3f, sill=%.3f), base=%.3f, margin=%.3f, adjusted=%.3f, "
            "clipped=%.3f, source=%s",
            self.sol_elev,
            self.gamma,
            self.distance,
            effective_distance,
            depth_contribution,
            sill_offset,
            base_height,
            safety_margin,
            adjusted_height,
            result,
            effective_distance_source,
        )
        self._last_calc_details = {
            "edge_case_detected": False,
            "safety_margin": round(safety_margin, 4),
            "effective_distance": round(effective_distance, 4),
            "window_depth_contribution": round(depth_contribution, 4),
            "sill_height_offset": round(sill_offset, 4),
            "glare_zones_active": [],  # populated by GlareZoneHandler via diagnostics
            "effective_distance_source": effective_distance_source,
        }
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
