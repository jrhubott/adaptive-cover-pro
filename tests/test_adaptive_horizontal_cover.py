"""Tests for AdaptiveHorizontalCover calculations."""

import pytest

from tests.cover_helpers import build_horizontal_cover


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
        assert 0 <= percentage <= 100

    @pytest.mark.unit
    def test_calculate_percentage_with_different_awning_length(
        self, horizontal_cover_instance
    ):
        """Test percentage with different awning length."""
        horizontal_cover_instance.awn_length = 3.0
        percentage = horizontal_cover_instance.calculate_percentage()
        # Longer awning means smaller percentage for same extension
        assert 0 <= percentage <= 100

    @pytest.mark.unit
    @pytest.mark.parametrize("angle", [0, 15, 30, 45])
    def test_awning_angle_variations(self, horizontal_cover_instance, angle):
        """Test various awning angles."""
        horizontal_cover_instance.awn_angle = angle
        result = horizontal_cover_instance.calculate_position()
        assert result >= 0

    @pytest.mark.unit
    def test_calculate_position_saturates_at_awn_length(
        self, mock_sun_data, mock_logger
    ):
        """Regression (#209): extension must saturate at awn_length, never exceed it.

        Low sun (15°) + tall window (3 m) + short awning (1 m) forces the raw
        trig result to ~11.2 m.  Old code clipped at 2×awn_length (2.0 m → 200%);
        new code clips at awn_length (1.0 m → 100%).
        """
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=15.0,
            sun_data=mock_sun_data,
            h_win=3.0,
            distance=0.5,
            awn_length=1.0,
            awn_angle=0.0,
        )
        assert cover.calculate_position() == pytest.approx(1.0)
        assert cover.calculate_percentage() == pytest.approx(100.0)
