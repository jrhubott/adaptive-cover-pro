"""Glare zone handler — lower blind to protect specific floor zones from glare.

Fix: use min() (closest zone) instead of max() (farthest zone) for zone
selection. The closest zone demands the most blind coverage because smaller
effective distance → lower position% → more blind deployed.
Thanks to @ZamenWolk for identifying this in GitHub issue #213.
"""

from __future__ import annotations

import logging
import math

from ...cover_types import get_policy
from ...engine.covers.vertical import (
    AdaptiveVerticalCover,
    glare_zone_effective_distance,
)
from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
from ..handler import OverrideHandler
from ..helpers import (
    apply_snapshot_limits,
    compute_default_position,
    compute_raw_calculated_position,
    solar_floor,
)
from ..types import PipelineResult, PipelineSnapshot

_LOGGER = logging.getLogger(__name__)


class GlareZoneHandler(OverrideHandler):
    """Lower the blind when active glare zones need more protection.

    Priority 45 — below ClimateHandler (50), above SolarHandler (40).
    Only applies to vertical covers (cover_blind). Computes effective distances
    for all active glare zones using pure geometry, then returns a position
    based on the minimum (closest) zone distance when it is more protective
    than the lower-priority fallback.

    ClimateHandler defers its GLARE_CONTROL case (returns None), so this
    handler fires naturally when climate mode is on and the sun is tracking.
    When climate mode is off, this handler fires directly after time-window
    and cover-type gates.

    Geometry: smaller effective distance → lower position% → more blind deployed.
    With Sun Tracking enabled, a zone closer to the window than the base distance
    needs the blind lowered further than SolarHandler would compute. With Sun
    Tracking disabled, the computed glare-zone position is compared to Default.

    Falls through to SolarHandler (returns None) when all zones are at or beyond
    the base distance. Falls through to Default when Sun Tracking is disabled
    and the glare-zone position would not be more protective.
    """

    name = "glare_zone"
    priority = 45

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return glare-zone-adjusted position when a zone requires deeper coverage."""
        if not snapshot.in_time_window:
            return None
        policy = snapshot.policy or get_policy(snapshot.cover_type)
        if not policy.supports_glare_zones:
            return None
        if not snapshot.glare_zones or not snapshot.active_zone_names:
            return None
        if not snapshot.cover.direct_sun_valid:
            return None

        # Belt-and-braces: ``supports_glare_zones`` is the public gate, but a
        # future policy that flips the flag without binding a vertical engine
        # would silently type-confuse here. Verify at runtime; skip + warn so
        # the pipeline doesn't crash on the next attribute access.
        if not isinstance(snapshot.cover, AdaptiveVerticalCover):
            _LOGGER.warning(
                "GlareZoneHandler gated on supports_glare_zones=True but cover "
                "is %s, not AdaptiveVerticalCover — skipping",
                type(snapshot.cover).__name__,
            )
            return None
        cover = snapshot.cover
        window_half_width = snapshot.glare_zones.window_width / 2.0
        base_distance = cover.distance

        zones_by_name = {z.name: z for z in snapshot.glare_zones.zones}
        zone_results: list[tuple[str, float]] = []
        for zone_name in snapshot.active_zone_names:
            zone = zones_by_name.get(zone_name)
            if zone is None:
                continue
            zone_dist = glare_zone_effective_distance(
                zone, cover.gamma, cover.sol_elev, window_half_width
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

        if snapshot.enable_sun_tracking and min_distance >= base_distance:
            # All zones are at or beyond the base distance — they're already in
            # shadow from SolarHandler's normal calculation. No override needed.
            return None

        state = math.floor(
            cover.calculate_raw_percentage(effective_distance_override=min_distance)
        )
        state = solar_floor(state, floor_active=snapshot.solar_floor_active)
        # ``sun_valid`` follows the live tracking state: the sun-only limits
        # (``*_sun_only`` and ``min_pos_sun_tracking``) apply only while Sun
        # Tracking is enabled — matching ``compute_default_position`` /
        # ``compute_raw_calculated_position``. With tracking off, the glare
        # position and the Default it is weighed against share the same regime.
        position = apply_snapshot_limits(
            snapshot, state, sun_valid=snapshot.enable_sun_tracking
        )
        if not snapshot.enable_sun_tracking:
            default_position = compute_default_position(snapshot)
            if (
                policy.more_protective_position(position, default_position)
                == default_position
            ):
                return None

        zone_names = ", ".join(contributing_zones)
        z_adjusted = any(zones_by_name[name].z > 0 for name in contributing_zones)
        z_suffix: Reason | str = (
            Reason(ReasonCode.FRAGMENT_Z_ADJUSTED) if z_adjusted else ""
        )
        return PipelineResult(
            position=position,
            control_method=ControlMethod.GLARE_ZONE,
            reason_payload=Reason(
                ReasonCode.GLARE_PROTECTION,
                {
                    "zones": zone_names,
                    "distance": min_distance,
                    "z_suffix": z_suffix,
                    "position": position,
                },
            ),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:
        """Reason when glare zone handler does not match."""
        if not snapshot.in_time_window:
            return Reason(ReasonCode.SKIP_OUTSIDE_WINDOW)
        return Reason(ReasonCode.SKIP_NO_GLARE_ZONES)
