"""Glare zone handler — lower blind to protect specific floor zones from glare.

Fix: use min() (closest zone) instead of max() (farthest zone) for zone
selection. The closest zone demands the most blind coverage because smaller
effective distance → lower position% → more blind deployed.
Thanks to @ZamenWolk for identifying this in GitHub issue #213.
"""

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
    """Lower the blind further when active glare zones need more protection than SolarHandler.

    Priority 45 — below ClimateHandler (50), above SolarHandler (40).
    Only applies to vertical covers (cover_blind). Computes effective distances
    for all active glare zones using pure geometry, then returns a position
    based on the minimum (closest) zone distance when it is less than the
    cover's base distance.

    ClimateHandler defers its GLARE_CONTROL case (returns None), so this
    handler fires naturally when climate mode is on and the sun is tracking.
    When climate mode is off, this handler fires directly after time-window
    and cover-type gates.

    Geometry: smaller effective distance → lower position% → more blind deployed.
    A zone closer to the window than the base distance is in the illuminated area
    and needs the blind lowered further than SolarHandler would compute.

    Falls through to SolarHandler (returns None) when all zones are at or beyond
    the base distance (already in shadow from normal solar tracking).
    """

    name = "glare_zone"
    priority = 45

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return glare-zone-adjusted position when a zone requires deeper coverage."""
        if not snapshot.in_time_window:
            return None
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

        # The CLOSEST zone is most restrictive: smaller distance → lower position%
        # → more blind deployed → more protection. A blind set to block sun beyond
        # depth d allows sun to penetrate up to d from the window. So the zone
        # nearest to the window demands the most blind coverage.
        min_distance = min(d for _, d in zone_results)
        contributing_zones = [name for name, d in zone_results if d == min_distance]

        if min_distance >= base_distance:
            # All zones are at or beyond the base distance — they're already in
            # shadow from SolarHandler's normal calculation. No override needed.
            return None

        state = int(
            round(cover.calculate_percentage(effective_distance_override=min_distance))
        )
        state = max(state, 1)
        position = apply_snapshot_limits(snapshot, state, sun_valid=True)

        zone_names = ", ".join(contributing_zones)
        return PipelineResult(
            position=position,
            control_method=ControlMethod.GLARE_ZONE,
            reason=(
                f"glare zone protection ({zone_names}) — "
                f"effective distance {min_distance:.2f}m → position {position}%"
            ),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when glare zone handler does not match."""
        if not snapshot.in_time_window:
            return "outside time window"
        return "no active glare zones or sun not in FOV"
