"""Vertical blind (up/down) cover calculation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy import cos, sin, tan
from numpy import radians as rad

from ...config_types import GlareZone, GlareZonesConfig, VerticalConfig
from ...const import WINDOW_DEPTH_GAMMA_THRESHOLD
from ...geometry import EdgeCaseHandler, SafetyMarginCalculator
from ...position_utils import PositionConverter
from .base import AdaptiveGeneralCover


def _glare_zone_effective_distance(
    zone: GlareZone,
    gamma: float,
    window_half_width: float,
) -> float | None:
    """Convert a glare zone to an effective distance (metres) for this sun angle.

    Returns the perpendicular depth into the room (in metres) that the blind
    must shade to protect the nearest edge of the zone circle. Returns None if
    the sun cannot reach this zone through the window opening at angle gamma.

    Args:
        zone: The glare zone definition (x, y in cm, radius in cm).
        gamma: Surface solar azimuth in degrees (positive = sun to the right).
        window_half_width: Half the window width in cm.

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

    return nearest_y / 100.0  # cm → metres


@dataclass
class AdaptiveVerticalCover(AdaptiveGeneralCover):
    """Calculate state for Vertical blinds."""

    vert_config: VerticalConfig = None  # type: ignore[assignment]
    active_zone_names: set[str] = field(default_factory=set)

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

    def calculate_position(self) -> float:
        """Calculate blind height with enhanced geometric accuracy.

        Implements Phase 1 and Phase 2 geometric enhancements (v2.7.0+):

        Phase 1 (Automatic):
        - Edge case handling: Safe fallbacks for extreme sun angles (elev<2°, |gamma|>85°)
        - Safety margins: Angle-dependent multipliers (1.0-1.45x) for better sun blocking:
          * Gamma margin: 1.0 at 45° → 1.2 at 90° (smoothstep)
          * Low elevation: 1.0 at 10° → 1.15 at 0° (linear)
          * High elevation: 1.0 at 75° → 1.1 at 90° (linear)
        - Smooth transitions: Prevents jarring position changes

        Phase 2 (Optional):
        - Window depth: Accounts for window reveals/frames (0.0-0.5m)
        - Only active when window_depth > 0 and |gamma| > 10°
        - Adds horizontal offset: depth × sin(|gamma|)
        - Sill height: Accounts for windows not starting at floor level (0.0-3.0m)
        - Only active when sill_height > 0
        - Subtracts horizontal offset: sill_height / tan(elevation), capped at 0.05 minimum

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

        # Gather all distances to protect: base + any active glare zones
        distances_to_protect: list[float] = [self.distance]
        glare_zones_contributing: list[str] = []

        if self.glare_zones and self.active_zone_names:
            window_half_width = self.glare_zones.window_width / 2.0
            for zone in self.glare_zones.zones:
                if zone.name not in self.active_zone_names:
                    continue
                zone_dist = _glare_zone_effective_distance(
                    zone, self.gamma, window_half_width
                )
                if zone_dist is not None:
                    distances_to_protect.append(zone_dist)
                    glare_zones_contributing.append(zone.name)

        effective_distance_base = max(distances_to_protect)
        effective_distance = effective_distance_base
        effective_distance_source = (
            "glare_zone"
            if glare_zones_contributing and effective_distance_base > self.distance
            else "base"
        )

        # Account for window depth at angles (creates additional shadow)
        depth_contribution = 0.0
        if self.window_depth > 0 and abs(self.gamma) > WINDOW_DEPTH_GAMMA_THRESHOLD:
            # At angles, window depth creates additional horizontal offset
            depth_contribution = self.window_depth * sin(rad(abs(self.gamma)))
            effective_distance += depth_contribution

        # Account for window sill height (window not starting at floor)
        # Sill at height S means blind bottom is S meters above floor,
        # providing S/tan(elevation) meters of "free" horizontal protection.
        # Subtract from effective_distance to account for this.
        sill_offset = 0.0
        if self.sill_height > 0:
            sill_offset = self.sill_height / max(
                tan(rad(self.sol_elev)), 0.05
            )  # ~2.9° minimum
            effective_distance -= sill_offset

        # Base calculation: project glare zone to vertical blind height
        path_length = effective_distance / cos(rad(self.gamma))
        base_height = path_length * tan(rad(self.sol_elev))

        # Apply safety margin for extreme angles
        safety_margin = self._calculate_safety_margin(self.gamma, self.sol_elev)
        adjusted_height = base_height * safety_margin
        result = float(np.clip(adjusted_height, 0, self.h_win))

        self.logger.debug(
            "Vertical calc: elev=%.1f°, gamma=%.1f°, dist=%.3f→%.3f (depth=%.3f, sill=%.3f), "
            "base=%.3f, margin=%.3f, adjusted=%.3f, clipped=%.3f, source=%s",
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
        # Store for diagnostic sensor access
        self._last_calc_details = {
            "edge_case_detected": False,
            "safety_margin": round(safety_margin, 4),
            "effective_distance": round(effective_distance, 4),
            "window_depth_contribution": round(depth_contribution, 4),
            "sill_height_offset": round(sill_offset, 4),
            "glare_zones_active": glare_zones_contributing,
            "effective_distance_source": effective_distance_source,
        }
        return result

    def calculate_percentage(self) -> float:
        """Convert blind height to percentage for Home Assistant.

        Converts calculated blind height (meters) to percentage (0-100) for
        cover entity position attribute.

        Returns:
            Position as percentage (0-100).

        """
        position = self.calculate_position()
        self.logger.debug(
            "Converting height to percentage: %s / %s * 100", position, self.h_win
        )
        return PositionConverter.to_percentage(position, self.h_win)
