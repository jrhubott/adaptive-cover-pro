"""Glare zone handler — extend blind to protect specific floor zones from glare."""

from __future__ import annotations

from typing import cast

from ...engine.covers.vertical import (
    AdaptiveVerticalCover,
    glare_zone_effective_distance,
)
from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import apply_snapshot_limits, compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class GlareZoneHandler(OverrideHandler):
    """Extend the blind further when active glare zones require additional protection.

    Priority 45 — between ClimateHandler (50) and SolarHandler (40).
    Only applies to vertical covers (cover_blind). Computes effective distances
    for all active glare zones using pure geometry, then returns a position
    based on the maximum distance if it exceeds the cover's base distance.

    Falls through to SolarHandler (returns None) when the base distance
    already provides sufficient coverage for all zones.
    """

    name = "glare_zone"
    priority = 45

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return glare-zone-adjusted position when a zone requires deeper coverage."""
        if snapshot.cover_type != "cover_blind":
            return None
        if not snapshot.glare_zones or not snapshot.active_zone_names:
            return None
        if not snapshot.cover.direct_sun_valid:
            return None

        cover = cast(AdaptiveVerticalCover, snapshot.cover)
        window_half_width = snapshot.glare_zones.window_width / 2.0
        base_distance = cover.distance

        zones_by_name = {z.name: z for z in snapshot.glare_zones.zones}
        zone_results: list[tuple[str, float]] = []
        for zone_name in snapshot.active_zone_names:
            zone = zones_by_name.get(zone_name)
            if zone is None:
                continue
            zone_dist = glare_zone_effective_distance(
                zone, cover.gamma, window_half_width
            )
            if zone_dist is not None:
                zone_results.append((zone_name, zone_dist))

        if not zone_results:
            return None

        max_distance = max(d for _, d in zone_results)
        contributing_zones = [name for name, d in zone_results if d == max_distance]

        if max_distance <= base_distance:
            # No zone requires deeper coverage — let SolarHandler handle it
            return None

        state = int(
            round(cover.calculate_percentage(effective_distance_override=max_distance))
        )
        state = max(state, 1)
        position = apply_snapshot_limits(snapshot, state, sun_valid=True)

        zone_names = ", ".join(contributing_zones)
        return PipelineResult(
            position=position,
            control_method=ControlMethod.GLARE_ZONE,
            reason=(
                f"glare zone protection ({zone_names}) — "
                f"effective distance {max_distance:.2f}m → position {position}%"
            ),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when glare zone handler does not match."""
        return "no active glare zones or sun not in FOV"
