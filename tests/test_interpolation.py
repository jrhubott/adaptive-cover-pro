"""Tests for custom position interpolation feature."""

import pytest

from custom_components.adaptive_cover_pro.position_utils import interpolate_position


class TestInterpolationSimpleMode:
    """Test simple mode interpolation (start/end values)."""

    def test_limited_range_blind(self):
        """Test mapping [0,100] -> [10,90] for limited range covers."""
        assert interpolate_position(0, 10, 90, None, None) == 10
        assert interpolate_position(100, 10, 90, None, None) == 90
        assert interpolate_position(50, 10, 90, None, None) == 50
        assert interpolate_position(25, 10, 90, None, None) == pytest.approx(
            30, abs=0.1
        )

    def test_inverted_operation(self):
        """Test mapping [0,100] -> [100,0] for inversion."""
        assert interpolate_position(0, 100, 0, None, None) == 100
        assert interpolate_position(100, 100, 0, None, None) == 0
        assert interpolate_position(50, 100, 0, None, None) == 50
        assert interpolate_position(25, 100, 0, None, None) == 75

    def test_offset_range(self):
        """Test mapping [0,100] -> [20,80]."""
        assert interpolate_position(0, 20, 80, None, None) == 20
        assert interpolate_position(100, 20, 80, None, None) == 80
        assert interpolate_position(50, 20, 80, None, None) == 50


class TestInterpolationListMode:
    """Test list mode interpolation (advanced)."""

    def test_linear_mapping(self):
        """Test list mode with linear mapping."""
        assert interpolate_position(0, None, None, [0, 50, 100], [0, 50, 100]) == 0
        assert interpolate_position(50, None, None, [0, 50, 100], [0, 50, 100]) == 50
        assert interpolate_position(100, None, None, [0, 50, 100], [0, 50, 100]) == 100

    def test_nonlinear_aggressive_closing(self):
        """Test non-linear mapping for aggressive closing behavior."""
        nl = [0, 25, 50, 75, 100]
        new = [0, 15, 35, 60, 100]

        assert interpolate_position(0, None, None, nl, new) == 0
        assert interpolate_position(25, None, None, nl, new) == 15
        assert interpolate_position(50, None, None, nl, new) == 35
        assert interpolate_position(75, None, None, nl, new) == 60
        assert interpolate_position(100, None, None, nl, new) == 100

    def test_inverted_list(self):
        """Test inverted list for reverse operation."""
        nl = [0, 25, 50, 75, 100]
        new = [100, 75, 50, 25, 0]

        assert interpolate_position(0, None, None, nl, new) == 100
        assert interpolate_position(25, None, None, nl, new) == 75
        assert interpolate_position(50, None, None, nl, new) == 50
        assert interpolate_position(75, None, None, nl, new) == 25
        assert interpolate_position(100, None, None, nl, new) == 0


class TestInterpolationEdgeCases:
    """Test edge cases and error conditions."""

    def test_no_interpolation_configured(self):
        """Test that empty range returns original value."""
        assert interpolate_position(50, None, None, None, None) == 50

    def test_intermediate_values(self):
        """Test interpolation between defined points."""
        result = interpolate_position(33, 10, 90, None, None)
        assert pytest.approx(result, abs=0.5) == 36.4
