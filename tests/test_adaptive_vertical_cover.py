"""Tests for AdaptiveVerticalCover calculations."""

import pytest


class TestAdaptiveVerticalCover:
    """Test AdaptiveVerticalCover calculations."""

    @pytest.mark.unit
    def test_calculate_position_standard(self, vertical_cover_instance):
        """Test blind height calculation with standard config."""
        height = vertical_cover_instance.calculate_position()
        # At 45° elevation, gamma=0, distance=0.5, tan(45°)=1, cos(0)=1
        # height = 0.5 / 1.0 * 1.0 = 0.5
        assert pytest.approx(height, 0.01) == 0.5

    @pytest.mark.unit
    def test_calculate_position_high_sun(self, vertical_cover_instance):
        """Test blind height calculation with high sun angle."""
        vertical_cover_instance.sol_elev = 80.0
        height = vertical_cover_instance.calculate_position()
        # High sun should clip to window height
        assert height == vertical_cover_instance.h_win

    @pytest.mark.unit
    def test_calculate_position_low_sun(self, vertical_cover_instance):
        """Test blind height calculation with low sun angle."""
        vertical_cover_instance.sol_elev = 10.0
        height = vertical_cover_instance.calculate_position()
        # Low sun creates shorter blind height
        assert 0 < height < 0.5

    @pytest.mark.unit
    def test_calculate_position_with_gamma_angle(self, vertical_cover_instance):
        """Test blind height with angled sun (gamma != 0)."""
        vertical_cover_instance.sol_azi = 210.0  # gamma = -30°
        height = vertical_cover_instance.calculate_position()
        # Angled sun increases path length
        assert height > 0.5

    @pytest.mark.unit
    def test_calculate_position_clips_to_window_height(self, vertical_cover_instance):
        """Test position clips to maximum window height."""
        vertical_cover_instance.sol_elev = 85.0
        height = vertical_cover_instance.calculate_position()
        assert height == vertical_cover_instance.h_win

    @pytest.mark.unit
    def test_calculate_percentage_standard(self, vertical_cover_instance):
        """Test percentage conversion."""
        percentage = vertical_cover_instance.calculate_percentage()
        # At 45° elevation: 0.5m / 2.0m = 25%
        assert percentage == 25

    @pytest.mark.unit
    def test_calculate_percentage_with_different_window_height(
        self, vertical_cover_instance
    ):
        """Test percentage with different window height."""
        vertical_cover_instance.h_win = 3.0
        percentage = vertical_cover_instance.calculate_percentage()
        # Same blind height (0.5m) but taller window: 0.5/3.0 ≈ 17%
        assert percentage == 17

    @pytest.mark.unit
    def test_calculate_percentage_with_different_distance(
        self, vertical_cover_instance
    ):
        """Test percentage with different distance."""
        vertical_cover_instance.distance = 1.0
        percentage = vertical_cover_instance.calculate_percentage()
        # Double distance: 1.0m / 2.0m = 50%
        assert percentage == 50
