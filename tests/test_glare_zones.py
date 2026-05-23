"""Tests for glare zone data model and geometry.

Units: all glare zone coordinates (x, y, radius) and window_width are metres.
"""

from math import radians, tan

import pytest
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
    VerticalConfig,
)
from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    MIN_TAN_ELEVATION_CLAMP,
    _elevation_offset,
    glare_zone_effective_distance as _glare_zone_effective_distance,
)
from tests.cover_helpers import build_vertical_cover


class TestGlareZoneDataModel:
    """Test GlareZone and GlareZonesConfig dataclasses."""

    def test_glare_zone_fields(self):
        """GlareZone stores name, x, y, radius."""
        zone = GlareZone(name="Desk", x=0.5, y=2.0, radius=0.3)
        assert zone.name == "Desk"
        assert zone.x == 0.5
        assert zone.y == 2.0
        assert zone.radius == 0.3

    def test_glare_zones_config_fields(self):
        """GlareZonesConfig stores zones list and window_width."""
        zone = GlareZone(name="Table", x=0.0, y=1.5, radius=0.6)
        cfg = GlareZonesConfig(zones=[zone], window_width=1.2)
        assert len(cfg.zones) == 1
        assert cfg.window_width == 1.2

    def test_vertical_config_glare_zones_defaults_none(self):
        """VerticalConfig.glare_zones defaults to None."""
        vc = VerticalConfig(distance=0.5, h_win=2.0)
        assert vc.glare_zones is None

    def test_vertical_config_accepts_glare_zones(self):
        """VerticalConfig.glare_zones accepts a GlareZonesConfig."""
        zone = GlareZone(name="Couch", x=-0.8, y=3.0, radius=0.5)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=2.0)
        vc = VerticalConfig(distance=0.5, h_win=2.0, glare_zones=zones_cfg)
        assert vc.glare_zones is zones_cfg


class TestElevationOffset:
    """The shared elevation-offset trig helper used by sill geometry and zone Z."""

    def test_zero_height_returns_zero(self):
        assert _elevation_offset(0.0, 45.0) == pytest.approx(0.0)

    def test_height_one_at_45_degrees(self):
        # tan(45°) = 1 → height / tan = height
        assert _elevation_offset(1.0, 45.0) == pytest.approx(1.0, rel=1e-9)

    def test_height_two_at_30_degrees(self):
        expected = 2.0 / tan(radians(30.0))
        assert _elevation_offset(2.0, 30.0) == pytest.approx(expected, rel=1e-9)

    def test_low_elevation_clamped(self):
        # Below the MIN_TAN_ELEVATION_CLAMP threshold, denominator caps so result stays finite.
        result_low = _elevation_offset(1.0, 0.01)
        expected_cap = 1.0 / MIN_TAN_ELEVATION_CLAMP
        assert result_low == pytest.approx(expected_cap, rel=1e-9)


class TestGlareZoneZHeight:
    """The optional Z (height above floor) extension to glare_zone_effective_distance."""

    def test_z_defaults_to_zero_reproduces_floor_behaviour(self):
        """A zone built without z behaves identically to today."""
        zone = GlareZone(name="Floor", x=0.0, y=2.0, radius=0.3)
        assert zone.z == 0.0
        baseline = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=1.0
        )
        assert baseline == pytest.approx(2.0 - 0.3, rel=1e-9)

    def test_z_positive_adds_elevation_offset(self):
        """Above-floor target shifts the effective distance by Z/tan(elev)."""
        zone_floor = GlareZone(name="Floor", x=0.0, y=2.0, radius=0.3, z=0.0)
        zone_eye = GlareZone(name="Eye", x=0.0, y=2.0, radius=0.3, z=1.1)
        floor = _glare_zone_effective_distance(
            zone_floor, gamma=0.0, sol_elev=45.0, window_half_width=1.0
        )
        eye = _glare_zone_effective_distance(
            zone_eye, gamma=0.0, sol_elev=45.0, window_half_width=1.0
        )
        # tan(45°) = 1 → expected delta is 1.1
        assert eye == pytest.approx(floor + 1.1, rel=1e-9)

    def test_z_low_elevation_uses_same_clamp_as_sill(self):
        """Z-offset shares the MIN_TAN_ELEVATION_CLAMP guard with sill geometry."""
        zone = GlareZone(name="Eye", x=0.0, y=2.0, radius=0.3, z=1.0)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=0.01, window_half_width=1.0
        )
        expected = (2.0 - 0.3) + (1.0 / MIN_TAN_ELEVATION_CLAMP)
        assert dist == pytest.approx(expected, rel=1e-9)

    def test_z_does_not_bypass_window_aperture_gate(self):
        """Z>0 cannot rescue a zone whose sun ray enters outside the window."""
        # Wide-offset zone with a steep gamma that misses the window; Z mustn't help.
        zone = GlareZone(name="OffAxis", x=5.0, y=2.0, radius=0.3, z=1.0)
        assert (
            _glare_zone_effective_distance(
                zone, gamma=30.0, sol_elev=45.0, window_half_width=0.5
            )
            is None
        )


class TestGlareZoneGeometry:
    """Test _glare_zone_effective_distance."""

    def test_zone_directly_in_front_gamma_zero(self):
        """Zone centred on window normal, gamma=0 → nearest_y = y - radius."""
        zone = GlareZone(name="Z", x=0.0, y=2.0, radius=0.3)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=1.5
        )
        # nearest_y = 2.0 - 0.3*cos(0) = 1.70 m
        assert dist == pytest.approx(1.70, abs=1e-6)

    def test_zone_on_right_side_gamma_zero(self):
        """Zone offset to the right, gamma=0, centred ray still passes through window."""
        zone = GlareZone(name="Z", x=0.5, y=1.0, radius=0.0)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=1.5
        )
        # x_at_window = 0.5 + 1.0*tan(0) = 0.5 < 1.5 → reachable; nearest_y = 1.0
        assert dist == pytest.approx(1.00, abs=1e-6)

    def test_zone_behind_window_wall_returns_none(self):
        """Zone with y ≤ radius (nearest_y ≤ 0) is behind the wall."""
        zone = GlareZone(name="Z", x=0.0, y=0.2, radius=0.3)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=1.5
        )
        # nearest_y = 0.2 - 0.3 = -0.1 → None
        assert dist is None

    def test_zone_outside_window_width_returns_none(self):
        """Zone whose sun ray enters outside the window frame → None."""
        zone2 = GlareZone(name="Z2", x=2.0, y=1.0, radius=0.0)
        # x_at_window = 2.0 + 1.0*tan(0) = 2.0; window_half_width=0.5 → outside
        dist = _glare_zone_effective_distance(
            zone2, gamma=0.0, sol_elev=45.0, window_half_width=0.5
        )
        assert dist is None

    def test_zone_angled_sun_reachable(self):
        """Zone at centre, moderate gamma: check the nearest_y is correct."""
        zone = GlareZone(name="Z", x=0.0, y=2.0, radius=0.0)
        gamma = 30.0
        # nearest_x = 0; nearest_y = 2.0
        # x_at_window = 0 + 2.0*tan(30) ≈ 1.1547; window_half_width=1.5 → reachable
        dist = _glare_zone_effective_distance(
            zone, gamma=gamma, sol_elev=45.0, window_half_width=1.5
        )
        assert dist == pytest.approx(2.00, abs=1e-6)

    def test_zone_outside_window_angle_returns_none(self):
        """Zone at (0, 2.0), gamma=30°, narrow window (half=0.5m) → None."""
        zone = GlareZone(name="Z", x=0.0, y=2.0, radius=0.0)
        # x_at_window ≈ 1.1547 > 0.5 → outside window
        dist = _glare_zone_effective_distance(
            zone, gamma=30.0, sol_elev=45.0, window_half_width=0.5
        )
        assert dist is None

    def test_returns_metres(self):
        """Result is in metres (no unit conversion)."""
        zone = GlareZone(name="Z", x=0.0, y=3.0, radius=0.0)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=2.0
        )
        assert dist == pytest.approx(3.00, abs=1e-6)

    def test_zone_offset_negative_gamma(self):
        """Zone offset left, sun from right (negative gamma): ray misses narrow window."""
        # Zone at x=-1.0, y=2.0, r=0; gamma=-30 (sun from right)
        # nearest_x = -1.0 + 0*sin(-30) = -1.0
        # nearest_y = 2.0 - 0*cos(-30) = 2.0
        # x_at_window = -1.0 + 2.0*tan(-30) ≈ -1.0 - 1.1547 = -2.1547
        # abs(-2.1547) > 1.5 → None (ray exits left of window)
        zone = GlareZone(name="Z", x=-1.0, y=2.0, radius=0.0)
        dist = _glare_zone_effective_distance(
            zone, gamma=-30.0, sol_elev=45.0, window_half_width=1.5
        )
        assert dist is None


class TestGlareZoneCalculation:
    """Test glare zone integration in AdaptiveVerticalCover.calculate_position()."""

    def _make_cover(self, glare_zones=None, active_zone_names=None, **kwargs):
        """Build a vertical cover with optional glare zone config."""
        logger = MagicMock()
        sun_data = MagicMock()
        cover = build_vertical_cover(
            logger=logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=sun_data,
            distance=0.5,
            h_win=2.0,
            glare_zones=glare_zones,
            **kwargs,
        )
        cover.active_zone_names = active_zone_names or set()
        return cover

    def test_no_zones_configured_unchanged(self):
        """With no glare zones, calculate_position() is identical to baseline."""
        cover_no_zones = self._make_cover(glare_zones=None)
        baseline = cover_no_zones.calculate_position()

        cover_empty = self._make_cover(
            glare_zones=GlareZonesConfig(zones=[], window_width=2.0)
        )
        result = cover_empty.calculate_position()
        assert result == pytest.approx(baseline, rel=1e-6)

    def test_effective_distance_override_farther_than_base_extends_position(self):
        """effective_distance_override larger than base → higher blind position.

        The glare loop has moved to GlareZoneHandler; calculate_position now
        accepts an override directly.
        """
        cover = self._make_cover(glare_zones=None)
        baseline = cover.calculate_position()

        # Simulate what GlareZoneHandler would pass: a farther zone distance
        zone_distance = 2.0  # metres — farther than base (0.5 m)
        result = cover.calculate_position(effective_distance_override=zone_distance)

        assert result > baseline

    def test_effective_distance_override_closer_than_base_still_lowers(self):
        """effective_distance_override smaller than base → lower blind position.

        GlareZoneHandler is responsible for taking max(base, zone_dist);
        calculate_position just uses whatever override is supplied.
        """
        cover = self._make_cover(glare_zones=None)
        baseline = cover.calculate_position()

        # Closer override should produce a lower or equal position
        result = cover.calculate_position(effective_distance_override=0.1)
        assert result <= baseline

    def test_no_override_uses_base_distance(self):
        """Without effective_distance_override, base distance is used."""
        cover = self._make_cover(glare_zones=None)
        baseline = cover.calculate_position()
        result = cover.calculate_position(effective_distance_override=None)
        assert result == pytest.approx(baseline, rel=1e-6)

    def test_glare_zone_loop_no_longer_in_calculate_position(self):
        """active_zone_names no longer affects calculate_position() output."""
        zone = GlareZone(name="Desk", x=0.0, y=2.0, radius=0.3)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=2.0)
        cover_with = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Desk"})
        cover_without = self._make_cover(glare_zones=None)
        assert cover_with.calculate_position() == pytest.approx(
            cover_without.calculate_position(), rel=1e-6
        )

    def test_zone_geometry_function_returns_correct_distance(self):
        """glare_zone_effective_distance returns metres for a reachable zone."""
        zone = GlareZone(name="Desk", x=0.0, y=2.0, radius=0.0)
        dist = _glare_zone_effective_distance(
            zone, gamma=0.0, sol_elev=45.0, window_half_width=2.0
        )
        assert dist == pytest.approx(2.0, rel=1e-6)
