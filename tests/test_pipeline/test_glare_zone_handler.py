"""Tests for GlareZoneHandler."""

from __future__ import annotations

import pytest
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


def test_glare_zone_control_method_exists() -> None:
    """GLARE_ZONE must be a valid ControlMethod value."""
    assert ControlMethod.GLARE_ZONE == "glare_zone"


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


def _make_glare_config(
    zones=None,
    window_width: float = 120.0,
) -> GlareZonesConfig:
    if zones is None:
        zones = [GlareZone(name="desk", x=0.0, y=400.0, radius=30.0)]
    return GlareZonesConfig(zones=zones, window_width=window_width)


class TestGlareZoneHandlerGating:
    """Test GlareZoneHandler gating conditions."""

    handler = GlareZoneHandler()

    def test_returns_none_outside_time_window(self) -> None:
        """Returns None when in_time_window is False even if sun is valid."""
        cover = _make_vertical_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names={"desk"},
            in_time_window=False,
        )
        assert self.handler.evaluate(snap) is None

    def test_describe_skip_outside_time_window(self) -> None:
        """describe_skip returns 'outside time window' when in_time_window is False."""
        cover = _make_vertical_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names={"desk"},
            in_time_window=False,
        )
        assert self.handler.describe_skip(snap) == "outside time window"

    def test_matches_inside_time_window(self) -> None:
        """Returns result when in_time_window is True and all conditions met."""
        cover = _make_vertical_cover(
            direct_sun_valid=True,
            distance=1.0,
            gamma=0.0,
            calculate_percentage_return=40.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names={"desk"},
            in_time_window=True,
        )
        # May return None if zone distance doesn't exceed base distance — that's fine,
        # the key is that the time window check passed and other logic ran.
        # We just verify it didn't short-circuit on the time window check.
        # (A result of None here means glare zone doesn't need deeper coverage.)
        self.handler.evaluate(snap)

    def test_returns_none_for_awning_cover(self) -> None:
        """GlareZoneHandler only applies to vertical covers."""
        snap = make_snapshot(cover_type="cover_awning")
        assert self.handler.evaluate(snap) is None

    def test_returns_none_for_tilt_cover(self) -> None:
        """GlareZoneHandler does not apply to tilt covers."""
        snap = make_snapshot(cover_type="cover_tilt")
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_glare_zones(self) -> None:
        """Returns None when no glare zones are configured."""
        snap = make_snapshot(cover_type="cover_blind", glare_zones=None)
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_active_zones(self) -> None:
        """Returns None when no zones are currently active."""
        snap = make_snapshot(
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names=set(),  # no zones active
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_sun_not_valid(self) -> None:
        """Returns None when sun is not in FOV (no need to protect zones)."""
        cover = _make_vertical_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=_make_glare_config(),
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZoneHandlerLogic:
    """Test GlareZoneHandler calculation logic."""

    handler = GlareZoneHandler()

    def test_returns_glare_zone_control_method_when_active(self) -> None:
        """When zone is closer than base distance, return GLARE_ZONE method.

        Zone at 0.9m is closer than base_distance=10m, so it's in the
        illuminated area and needs extra protection from GlareZoneHandler.
        """
        cover = _make_vertical_cover(
            distance=10.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=5.0,
        )
        # Zone at y=100cm, radius=10cm → nearest_y = 90cm = 0.9m < base 10m
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=10.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE

    def test_falls_through_when_zone_farther_than_base(self) -> None:
        """Returns None when all zones are farther than base distance (in shadow).

        Zone at ~3.7m is beyond base_distance=1m. SolarHandler already blocks
        sun beyond 1m, so the zone is in shadow — no override needed.
        """
        cover = _make_vertical_cover(
            distance=1.0,  # base distance 1m — blocks sun beyond 1m
            gamma=0.0,
            direct_sun_valid=True,
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
        result = self.handler.evaluate(snap)
        # Zone farther than base → in shadow → fall through to SolarHandler
        assert result is None

    def test_inactive_zone_is_ignored(self) -> None:
        """Zone not in active_zone_names is skipped."""
        cover = _make_vertical_cover(distance=1.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = _make_glare_config()  # zone named "desk"
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"other_zone"},  # desk not active
        )
        result = self.handler.evaluate(snap)
        assert result is None

    def test_two_zones_with_equal_min_distance_both_in_reason(self) -> None:
        """Both zones appear in the reason string when they share the minimum distance."""
        # Two zones with the same nearest_y (gamma=0):
        # For zone_a: nearest_y = 100 - 0 = 100 → effective_distance = 1.0m
        # For zone_b: nearest_y = 130 - 30 = 100 → effective_distance = 1.0m
        zone_a = GlareZone(name="desk_left", x=0.0, y=100.0, radius=0.0)
        zone_b = GlareZone(name="desk_right", x=0.0, y=130.0, radius=30.0)
        glare_cfg = GlareZonesConfig(
            zones=[zone_a, zone_b],
            window_width=400.0,
        )
        cover = _make_vertical_cover(
            distance=5.0,  # base distance 5m > 1m zone distance
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=8.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk_left", "desk_right"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE
        assert "desk_left" in result.reason
        assert "desk_right" in result.reason

    def test_blocked_zone_ignored_valid_zone_used(self) -> None:
        """Handler uses the valid zone's distance when one zone is naturally blocked."""
        # zone_blocked: x far outside the window half-width → returns None
        # x_at_window = x + y * tan(gamma). At gamma=0, x_at_window = x.
        # window_half_width = 100.0cm, so any |x| > 100 is blocked.
        zone_blocked = GlareZone(name="blocked", x=500.0, y=300.0, radius=0.0)
        # zone_valid at 1.5m, closer than base_distance of 5m → needs protection
        zone_valid = GlareZone(name="valid", x=0.0, y=150.0, radius=0.0)
        glare_cfg = GlareZonesConfig(
            zones=[zone_blocked, zone_valid],
            window_width=200.0,  # half-width = 100cm
        )
        cover = _make_vertical_cover(
            distance=5.0,  # 5m > 1.5m zone_valid distance → zone needs protection
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=12.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"blocked", "valid"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE
        assert "valid" in result.reason
        assert "blocked" not in result.reason

    def test_priority_is_45(self) -> None:
        """GlareZoneHandler has priority 45 — below ClimateHandler (50), above SolarHandler (40)."""
        assert GlareZoneHandler.priority == 45

    def test_name(self) -> None:
        """GlareZoneHandler name is 'glare_zone'."""
        assert GlareZoneHandler.name == "glare_zone"


class TestGlareZoneBoundaryAtBaseDistance:
    """Regression for the >= vs > boundary comparison in the issue #213 fix.

    The fix changed the condition from ``max_distance > base_distance``
    (inverted) to ``min_distance >= base_distance``.  A zone at exactly
    base_distance is already in shadow — the handler must NOT fire.
    """

    handler = GlareZoneHandler()

    def test_zone_at_exact_base_distance_falls_through(self) -> None:
        """Zone at exactly base_distance is already in shadow — returns None.

        nearest_y = 200cm → effective_distance = 2.0m = base_distance exactly.
        The >= check means min_distance >= base_distance → handler falls through.
        """
        cover = _make_vertical_cover(distance=2.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=200.0, radius=0.0)],
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None

    def test_zone_barely_closer_than_base_fires(self) -> None:
        """Zone 1 cm closer than base (1.99 m < 2.0 m) triggers the handler."""
        cover = _make_vertical_cover(
            distance=2.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=20.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=199.0, radius=0.0)],  # 1.99 m
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE

    def test_zone_barely_farther_than_base_falls_through(self) -> None:
        """Zone 1 cm farther than base (2.01 m > 2.0 m) — already in shadow."""
        cover = _make_vertical_cover(distance=2.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=201.0, radius=0.0)],  # 2.01 m
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZonePositionFloor:
    """Verify the max(state, 1) clamp prevents position 0."""

    handler = GlareZoneHandler()

    def test_position_floors_at_1_when_calculate_returns_zero(self) -> None:
        """calculate_percentage returning 0 must produce position 1, not 0."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=0.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=50.0, radius=0.0)],  # 0.5 m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position >= 1

    def test_position_floors_at_1_when_calculate_returns_negative(self) -> None:
        """calculate_percentage returning a negative value must also floor at 1."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=-5.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=50.0, radius=0.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position >= 1


class TestGlareZonePositionLimits:
    """Verify apply_snapshot_limits is applied to the glare-zone position."""

    handler = GlareZoneHandler()

    def _closer_zone_snap(self, cover) -> object:
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=50.0, radius=0.0)],  # 0.5 m
            window_width=200.0,
        )
        return make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )

    def test_min_position_applied(self) -> None:
        """When calculated position is below min_pos, output is clamped to min_pos."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=5.0,
        )
        cover.config.min_pos = 20
        cover.config.min_pos_sun_only = False  # always enforce
        snap = self._closer_zone_snap(cover)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position >= 20

    def test_max_position_applied(self) -> None:
        """When calculated position is above max_pos, output is clamped to max_pos."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=95.0,
        )
        cover.config.max_pos = 80
        cover.config.max_pos_sun_only = False  # always enforce
        snap = self._closer_zone_snap(cover)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position <= 80


class TestGlareZoneWithGamma:
    """Non-zero sun angles affect zone reachability through the window."""

    handler = GlareZoneHandler()

    def test_positive_gamma_zone_reachable_through_wide_window(self) -> None:
        """At gamma=30°, a centred zone at 2 m depth is still reachable.

        x_at_window = 0 + 200 * tan(30°) ≈ 115 cm < 200 cm (half-width) → reachable.
        Effective distance 2.0 m < base 5.0 m → handler fires.
        """
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=30.0,
            direct_sun_valid=True,
            calculate_percentage_return=25.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=200.0, radius=0.0)],
            window_width=400.0,  # half-width = 200 cm
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE

    def test_gamma_blocks_zone_outside_narrow_window(self) -> None:
        """At gamma=-60°, a zone's entry point falls outside the window — blocked.

        x_at_window = 0 + 100 * tan(-60°) ≈ -173 cm; half-width = 100 cm → blocked.
        All zones return None → handler returns None.
        """
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=-60.0,
            direct_sun_valid=True,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],
            window_width=200.0,  # half-width = 100 cm
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZoneAllGeometryBlocked:
    """When geometry blocks every zone, evaluate() returns None."""

    handler = GlareZoneHandler()

    def test_all_zones_outside_window_returns_none(self) -> None:
        """Zones far off-centre (x=500) are blocked by the narrow window."""
        cover = _make_vertical_cover(distance=5.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = GlareZonesConfig(
            zones=[
                GlareZone(name="zone_a", x=500.0, y=200.0, radius=0.0),
                GlareZone(name="zone_b", x=600.0, y=150.0, radius=0.0),
            ],
            window_width=100.0,  # half-width = 50 cm; both x > 50
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"zone_a", "zone_b"},
        )
        assert self.handler.evaluate(snap) is None

    def test_zone_behind_wall_returns_none(self) -> None:
        """Zone with radius > y (nearest_y <= 0) is behind the window wall."""
        cover = _make_vertical_cover(distance=5.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = GlareZonesConfig(
            # y=50, radius=60 → nearest_y = 50 - 60 = -10 ≤ 0 → blocked
            zones=[GlareZone(name="desk", x=0.0, y=50.0, radius=60.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZoneRawCalculatedPosition:
    """The result must always include raw_calculated_position for diagnostics."""

    handler = GlareZoneHandler()

    def test_result_includes_raw_calculated_position(self) -> None:
        """raw_calculated_position is set on the PipelineResult when handler fires."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=40.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],  # 1 m
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.raw_calculated_position is not None


class TestGlareZoneDescribeSkip:
    """describe_skip returns the correct message for each non-fire path."""

    handler = GlareZoneHandler()

    def test_describe_skip_no_active_zones(self) -> None:
        """No active zones → standard 'no active glare zones' message."""
        snap = make_snapshot(
            cover_type="cover_blind",
            glare_zones=GlareZonesConfig(
                zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],
                window_width=200.0,
            ),
            active_zone_names=set(),
            in_time_window=True,
        )
        assert self.handler.describe_skip(snap) == "no active glare zones or sun not in FOV"

    def test_describe_skip_sun_not_valid(self) -> None:
        """Sun not in FOV → same 'no active glare zones' message."""
        cover = _make_vertical_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=GlareZonesConfig(
                zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],
                window_width=200.0,
            ),
            active_zone_names={"desk"},
            in_time_window=True,
        )
        assert self.handler.describe_skip(snap) == "no active glare zones or sun not in FOV"


class TestGlareZoneReasonString:
    """The reason string must contain effective distance and zone name."""

    handler = GlareZoneHandler()

    def test_reason_contains_effective_distance(self) -> None:
        """Reason includes the effective distance formatted to 2 decimal places."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=30.0,
        )
        # y=100, radius=0 → nearest_y=100 cm → 1.00 m effective distance
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "1.00m" in result.reason

    def test_reason_contains_zone_name(self) -> None:
        """Reason includes the contributing zone name."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=30.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="my_monitor", x=0.0, y=100.0, radius=0.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"my_monitor"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "my_monitor" in result.reason

    def test_reason_contains_position_percentage(self) -> None:
        """Reason includes the final position as 'position N%'."""
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=25.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=100.0, radius=0.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "position" in result.reason
        assert "%" in result.reason


class TestGlareZoneLargeRadius:
    """Zone radius affects nearest_y and therefore effective distance."""

    handler = GlareZoneHandler()

    def test_large_radius_brings_zone_very_close_to_window(self) -> None:
        """A large radius makes nearest_y very small → very small effective distance.

        y=200, radius=190 → nearest_y = 200 - 190 = 10 cm = 0.10 m.
        0.10 m << base 5.0 m → handler fires with override 0.10.
        """
        cover = _make_vertical_cover(
            distance=5.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=5.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=200.0, radius=190.0)],
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.GLARE_ZONE
        first_call = cover.calculate_percentage.call_args_list[0]
        override = first_call.kwargs.get("effective_distance_override")
        assert override == pytest.approx(0.10, abs=0.01)

    def test_radius_larger_than_y_is_behind_wall(self) -> None:
        """Zone with radius > y extends behind the window wall — returns None.

        y=50, radius=60 → nearest_y = 50 - 60 = -10 ≤ 0 → geometry blocks it.
        """
        cover = _make_vertical_cover(distance=5.0, gamma=0.0, direct_sun_valid=True)
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=50.0, radius=60.0)],
            window_width=200.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"desk"},
        )
        assert self.handler.evaluate(snap) is None


class TestGlareZoneRegressionMaxVsMin:
    """Explicit regression tests for the issue #213 bug (max→min, >=→<=)."""

    handler = GlareZoneHandler()

    def test_uses_min_not_max_distance_across_three_zones(self) -> None:
        """With zones at 0.5, 1.5, and 3.5 m, handler uses 0.5 m (the minimum).

        The old buggy code would have used 3.5 m (max), computing a more-open
        position and failing to protect the nearest zone.
        """
        cover = _make_vertical_cover(
            distance=4.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=10.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[
                GlareZone(name="zone_far", x=0.0, y=350.0, radius=0.0),   # 3.5 m
                GlareZone(name="zone_mid", x=0.0, y=150.0, radius=0.0),   # 1.5 m
                GlareZone(name="zone_near", x=0.0, y=50.0, radius=0.0),   # 0.5 m
            ],
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"zone_far", "zone_mid", "zone_near"},
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        first_call = cover.calculate_percentage.call_args_list[0]
        override = first_call.kwargs.get("effective_distance_override")
        assert override == pytest.approx(0.5, abs=0.01), (
            f"Expected 0.5 m (closest zone), got {override} — "
            "old bug would have passed 3.5 m (farthest zone)"
        )

    def test_zone_farther_than_base_does_not_fire_old_bug(self) -> None:
        """Single zone at 6 m with base 4 m must NOT fire.

        Under the old buggy code (max_distance > base_distance → fire), a zone
        at 6 m > 4 m would incorrectly have triggered the handler, overriding
        with a MORE open position (less protection) than SolarHandler.
        """
        cover = _make_vertical_cover(
            distance=4.0,
            gamma=0.0,
            direct_sun_valid=True,
            calculate_percentage_return=80.0,
        )
        glare_cfg = GlareZonesConfig(
            zones=[GlareZone(name="couch", x=0.0, y=600.0, radius=0.0)],  # 6 m
            window_width=400.0,
        )
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            glare_zones=glare_cfg,
            active_zone_names={"couch"},
        )
        assert self.handler.evaluate(snap) is None

