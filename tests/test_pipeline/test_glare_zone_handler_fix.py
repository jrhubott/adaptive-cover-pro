"""TDD tests for GlareZoneHandler fix (Issue #213).

The GlareZoneHandler logic is backwards:
- It uses max() when it should use min() (closest zone is most restrictive)
- It overrides when max_distance > base_distance (should be min_distance < base_distance)

These tests describe the CORRECT behavior.

Geometry refresher:
- calculate_position returns blind height = (distance/cos(gamma)) * tan(elev)
- Larger distance → higher position% → LESS blind deployed → LESS protection
- Smaller distance → lower position% → MORE blind deployed → MORE protection
- A blind set to block depth d allows sun to penetrate up to d from the window
- A zone CLOSER than base_distance is in the illuminated zone and needs protection
- A zone FARTHER than base_distance is already in shadow from SolarHandler
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone import (
    GlareZoneHandler,
)
from tests.test_pipeline.conftest import make_snapshot


def _make_vertical_cover(
    distance: float = 3.0,
    gamma: float = 0.0,
    direct_sun_valid: bool = True,
    calculate_percentage_return: float = 60.0,
):
    """Build a mock AdaptiveVerticalCover for GlareZoneHandler tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.distance = distance
    cover.gamma = gamma
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.config = MagicMock()
    cover.config.min_pos = None
    cover.config.max_pos = None
    cover.config.min_pos_sun_only = False
    cover.config.max_pos_sun_only = False
    return cover


handler = GlareZoneHandler()


class TestZoneCloserThanBaseShouldOverride:
    """Zone closer than base_distance is in the illuminated zone — handler MUST fire."""

    def test_zone_closer_than_base_fires_handler(self) -> None:
        """Zone at 0.9m with base_distance=10m: zone is illuminated, handler must fire.

        SolarHandler would compute position for 10m (very open, ~high %).
        The zone at 0.9m is well within the illuminated area and needs protection.
        GlareZoneHandler must override with a more protective (lower %) position.
        """
        cover = _make_vertical_cover(
            distance=10.0,  # base distance 10m — SolarHandler allows sun deep into room
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=5.0,  # glare zone position would be very closed
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=10.0)],  # 0.9m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = handler.evaluate(snap)
        # Zone at 0.9m < base 10m → zone is illuminated → handler MUST fire
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE

    def test_zone_at_1m_base_at_3m_fires_handler(self) -> None:
        """Reporter's scenario: zone 1m deep, base_distance 3m.

        SolarHandler with base=3m allows sun to penetrate up to 3m.
        Zone at 1m is illuminated. GlareZoneHandler must override.
        """
        cover = _make_vertical_cover(
            distance=3.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=15.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],  # 1.0m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE


class TestZoneFartherThanBaseShouldFallThrough:
    """Zone farther than base_distance is already in shadow — handler should NOT fire."""

    def test_zone_farther_than_base_falls_through(self) -> None:
        """Zone at 4m with base_distance=1m: zone is already in shadow.

        SolarHandler with base=1m computes a very closed position (low %).
        Sun cannot penetrate beyond ~1m. Zone at 4m is safely in shadow.
        GlareZoneHandler should NOT override — it would make things WORSE
        (computing a higher position for the 4m distance = less blind).
        """
        cover = _make_vertical_cover(
            distance=1.0,  # base distance 1m — SolarHandler blocks beyond 1m
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=80.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=400.0, radius=30.0)],  # ~3.7m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = handler.evaluate(snap)
        # Zone at ~3.7m > base 1m → zone is in shadow → fall through
        assert result is None


class TestMinDistanceUsedNotMax:
    """With multiple zones, the CLOSEST zone dictates the position (most restrictive)."""

    def test_closest_zone_determines_position(self) -> None:
        """Two zones at 0.5m and 2m, base_distance=3m.

        Both zones are closer than base (illuminated). The 0.5m zone needs
        the most blind (lowest position%). Handler must use min distance (0.5m),
        not max distance (2m).
        """
        cover = _make_vertical_cover(
            distance=3.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=10.0,
        )
        zone_near = GlareZone(name="monitor", x=0.0, y=50.0, radius=0.0)  # 0.5m
        zone_far = GlareZone(name="desk", x=0.0, y=200.0, radius=0.0)  # 2.0m
        glare_cfg = GlareZonesConfig(
            zones=[zone_near, zone_far],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"monitor", "desk"},
        )
        result = handler.evaluate(snap)
        assert result is not None
        # The handler must have called calculate_percentage with the CLOSEST
        # zone distance (0.5m), not the farthest (2.0m).
        # Note: calculate_percentage is called twice — once by the handler with
        # effective_distance_override, and once by compute_raw_calculated_position
        # without it. Check the first call.
        first_call = cover.calculate_percentage.call_args_list[0]
        override = first_call.kwargs.get("effective_distance_override")
        assert override == 0.5, (
            f"Expected effective_distance_override=0.5 (closest zone), got {override}"
        )

    def test_contributing_zone_is_closest(self) -> None:
        """The reason string should name the closest zone, not the farthest."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=8.0,
        )
        zone_near = GlareZone(name="monitor", x=0.0, y=80.0, radius=0.0)  # 0.8m
        zone_far = GlareZone(name="desk", x=0.0, y=300.0, radius=0.0)  # 3.0m
        glare_cfg = GlareZonesConfig(
            zones=[zone_near, zone_far],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"monitor", "desk"},
        )
        result = handler.evaluate(snap)
        assert result is not None
        assert "monitor" in result.reason


class TestMixedZonesAboveAndBelowBase:
    """When some zones are closer than base and some farther, only closer ones matter."""

    def test_only_zones_closer_than_base_trigger_override(self) -> None:
        """Zone at 0.5m (needs protection) and zone at 8m (already in shadow).

        base_distance=3m. Only the 0.5m zone needs protection.
        The 8m zone is in shadow and irrelevant.
        """
        cover = _make_vertical_cover(
            distance=3.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=5.0,
        )
        zone_needs_protection = GlareZone(name="monitor", x=0.0, y=50.0, radius=0.0)
        zone_in_shadow = GlareZone(name="couch", x=0.0, y=800.0, radius=0.0)
        glare_cfg = GlareZonesConfig(
            zones=[zone_needs_protection, zone_in_shadow],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"monitor", "couch"},
        )
        result = handler.evaluate(snap)
        assert result is not None
        # Must use the closest zone (0.5m) for the override
        first_call = cover.calculate_percentage.call_args_list[0]
        override = first_call.kwargs.get("effective_distance_override")
        assert override == 0.5
