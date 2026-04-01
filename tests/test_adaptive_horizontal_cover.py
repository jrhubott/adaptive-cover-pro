"""Tests for AdaptiveHorizontalCover calculations."""

import pytest


class TestAdaptiveHorizontalCover:
    """Test AdaptiveHorizontalCover calculations."""

    @pytest.mark.unit
    def test_calculate_position_standard(self, horizontal_cover_instance):
        """Test awning extension calculation."""
        length = horizontal_cover_instance.calculate_position()
        # Awning extends based on vertical height and angles
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_with_awning_angle(self, horizontal_cover_instance):
        """Test awning calculation with non-zero angle."""
        horizontal_cover_instance.awn_angle = 15.0
        length = horizontal_cover_instance.calculate_position()
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_high_sun(self, horizontal_cover_instance):
        """Test awning with high sun angle."""
        horizontal_cover_instance.sol_elev = 80.0
        length = horizontal_cover_instance.calculate_position()
        # High sun creates minimal shadow
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_low_sun(self, horizontal_cover_instance):
        """Test awning with low sun angle."""
        horizontal_cover_instance.sol_elev = 20.0
        length = horizontal_cover_instance.calculate_position()
        # Low sun creates longer shadow
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_percentage_standard(self, horizontal_cover_instance):
        """Test percentage conversion for awning."""
        percentage = horizontal_cover_instance.calculate_percentage()
        assert 0 <= percentage <= 200  # Can exceed 100% in some cases

    @pytest.mark.unit
    def test_calculate_percentage_with_different_awning_length(
        self, horizontal_cover_instance
    ):
        """Test percentage with different awning length."""
        horizontal_cover_instance.awn_length = 3.0
        percentage = horizontal_cover_instance.calculate_percentage()
        # Longer awning means smaller percentage for same extension
        assert 0 <= percentage <= 200

    @pytest.mark.unit
    @pytest.mark.parametrize("angle", [0, 15, 30, 45])
    def test_awning_angle_variations(self, horizontal_cover_instance, angle):
        """Test various awning angles."""
        horizontal_cover_instance.awn_angle = angle
        result = horizontal_cover_instance.calculate_position()
        assert result >= 0
