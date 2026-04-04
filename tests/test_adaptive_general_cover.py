"""Tests for AdaptiveGeneralCover base class properties."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestAdaptiveGeneralCoverProperties:
    """Test AdaptiveGeneralCover property methods."""

    @pytest.mark.unit
    def test_azi_min_abs_standard(self, vertical_cover_instance):
        """Test min azimuth calculation."""
        assert vertical_cover_instance.azi_min_abs == 135  # 180 - 45

    @pytest.mark.unit
    def test_azi_max_abs_standard(self, vertical_cover_instance):
        """Test max azimuth calculation."""
        assert vertical_cover_instance.azi_max_abs == 225  # 180 + 45

    @pytest.mark.unit
    def test_azi_min_abs_wrapping_around_zero(self, vertical_cover_instance):
        """Test min azimuth wraps correctly around 0°."""
        vertical_cover_instance.win_azi = 10
        vertical_cover_instance.fov_left = 45
        # (10 - 45 + 360) % 360 = 325
        assert vertical_cover_instance.azi_min_abs == 325

    @pytest.mark.unit
    def test_azi_max_abs_wrapping_around_360(self, vertical_cover_instance):
        """Test max azimuth wraps correctly around 360°."""
        vertical_cover_instance.win_azi = 350
        vertical_cover_instance.fov_right = 45
        # (350 + 45 + 360) % 360 = 35
        assert vertical_cover_instance.azi_max_abs == 35

    @pytest.mark.unit
    def test_azi_edges_calculation(self, vertical_cover_instance):
        """Test azimuth edges calculation."""
        edges = vertical_cover_instance._get_azimuth_edges
        assert edges == (135, 225)  # (win_azi - fov_left, win_azi + fov_right)

    @pytest.mark.unit
    def test_gamma_sun_directly_in_front(self, vertical_cover_instance):
        """Test gamma when sun is directly in front."""
        assert vertical_cover_instance.gamma == 0.0

    @pytest.mark.unit
    def test_gamma_sun_to_left(self, vertical_cover_instance):
        """Test gamma when sun is to the left."""
        vertical_cover_instance.sol_azi = 135.0  # 45° to left
        # (180 - 135 + 180) % 360 - 180 = 45
        assert vertical_cover_instance.gamma == 45.0

    @pytest.mark.unit
    def test_gamma_sun_to_right(self, vertical_cover_instance):
        """Test gamma when sun is to the right."""
        vertical_cover_instance.sol_azi = 225.0  # 45° to right
        # (180 - 225 + 180) % 360 - 180 = -45
        assert vertical_cover_instance.gamma == -45.0

    @pytest.mark.unit
    def test_gamma_wrapping_around_180(self, vertical_cover_instance):
        """Test gamma wrapping at ±180° boundaries."""
        vertical_cover_instance.win_azi = 10
        vertical_cover_instance.sol_azi = 350.0
        # (10 - 350 + 180) % 360 - 180 = 20
        assert vertical_cover_instance.gamma == 20.0

    @pytest.mark.unit
    def test_gamma_wrapping_negative(self, vertical_cover_instance):
        """Test gamma wrapping in negative direction."""
        vertical_cover_instance.win_azi = 350
        vertical_cover_instance.sol_azi = 10.0
        # (350 - 10 + 180) % 360 - 180 = -20
        assert vertical_cover_instance.gamma == -20.0

    @pytest.mark.unit
    def test_valid_elevation_with_both_limits(self, vertical_cover_instance):
        """Test elevation validation with min and max set."""
        vertical_cover_instance.min_elevation = 20
        vertical_cover_instance.max_elevation = 60
        vertical_cover_instance.sol_elev = 45.0
        assert vertical_cover_instance.valid_elevation is True

    @pytest.mark.unit
    def test_valid_elevation_below_minimum(self, vertical_cover_instance):
        """Test elevation validation when below minimum."""
        vertical_cover_instance.min_elevation = 20
        vertical_cover_instance.max_elevation = 60
        vertical_cover_instance.sol_elev = 15.0
        assert vertical_cover_instance.valid_elevation is False

    @pytest.mark.unit
    def test_valid_elevation_above_maximum(self, vertical_cover_instance):
        """Test elevation validation when above maximum."""
        vertical_cover_instance.min_elevation = 20
        vertical_cover_instance.max_elevation = 60
        vertical_cover_instance.sol_elev = 70.0
        assert vertical_cover_instance.valid_elevation is False

    @pytest.mark.unit
    def test_valid_elevation_only_min_set(self, vertical_cover_instance):
        """Test elevation validation with only min set."""
        vertical_cover_instance.min_elevation = 20
        vertical_cover_instance.max_elevation = None
        vertical_cover_instance.sol_elev = 30.0
        assert vertical_cover_instance.valid_elevation is True

    @pytest.mark.unit
    def test_valid_elevation_only_max_set(self, vertical_cover_instance):
        """Test elevation validation with only max set."""
        vertical_cover_instance.min_elevation = None
        vertical_cover_instance.max_elevation = 60
        vertical_cover_instance.sol_elev = 50.0
        assert vertical_cover_instance.valid_elevation is True

    @pytest.mark.unit
    def test_valid_elevation_neither_set_above_horizon(self, vertical_cover_instance):
        """Test elevation validation with no limits (default >= 0)."""
        vertical_cover_instance.min_elevation = None
        vertical_cover_instance.max_elevation = None
        vertical_cover_instance.sol_elev = 10.0
        assert vertical_cover_instance.valid_elevation is True

    @pytest.mark.unit
    def test_valid_elevation_neither_set_below_horizon(self, vertical_cover_instance):
        """Test elevation validation with no limits, sun below horizon."""
        vertical_cover_instance.min_elevation = None
        vertical_cover_instance.max_elevation = None
        vertical_cover_instance.sol_elev = -5.0
        assert vertical_cover_instance.valid_elevation is False

    @pytest.mark.unit
    def test_valid_sun_in_fov_and_above_horizon(self, vertical_cover_instance):
        """Test sun validity when in FOV with valid elevation."""
        # gamma=0, fov_left=45, fov_right=45, elev=45
        assert vertical_cover_instance.valid is True

    @pytest.mark.unit
    def test_valid_sun_outside_left_fov(self, vertical_cover_instance):
        """Test sun validity when outside left FOV."""
        vertical_cover_instance.sol_azi = 100.0  # gamma = 80° (beyond left edge)
        assert vertical_cover_instance.valid is False

    @pytest.mark.unit
    def test_valid_sun_outside_right_fov(self, vertical_cover_instance):
        """Test sun validity when outside right FOV."""
        vertical_cover_instance.sol_azi = 260.0  # gamma = -80° (beyond right edge)
        assert vertical_cover_instance.valid is False

    @pytest.mark.unit
    def test_valid_sun_below_horizon(self, vertical_cover_instance):
        """Test sun validity when below horizon."""
        vertical_cover_instance.sol_elev = -10.0
        assert vertical_cover_instance.valid is False

    @pytest.mark.unit
    def test_valid_sun_at_left_boundary(self, vertical_cover_instance):
        """Test sun validity at left FOV boundary."""
        vertical_cover_instance.sol_azi = 135.0  # gamma = 45° (at left edge)
        # gamma < fov_left is False when gamma == 45
        assert vertical_cover_instance.valid is False

    @pytest.mark.unit
    def test_valid_sun_at_right_boundary(self, vertical_cover_instance):
        """Test sun validity at right FOV boundary."""
        vertical_cover_instance.sol_azi = 225.0  # gamma = -45° (at right edge)
        # gamma > -fov_right is False when gamma == -45
        assert vertical_cover_instance.valid is False

    @pytest.mark.unit
    def test_is_sun_in_blind_spot_true(self, vertical_cover_instance):
        """Test blind spot detection when sun is in spot."""
        vertical_cover_instance.blind_spot_left = 25
        vertical_cover_instance.blind_spot_right = 35
        vertical_cover_instance.blind_spot_elevation = 50
        vertical_cover_instance.blind_spot_on = True
        vertical_cover_instance.sol_azi = 165.0  # gamma = 15°
        vertical_cover_instance.sol_elev = 30.0
        # left_edge = 45 - 25 = 20, right_edge = 45 - 35 = 10
        # gamma=15 is between 10 and 20, elev 30 <= 50
        assert vertical_cover_instance.is_sun_in_blind_spot is True

    @pytest.mark.unit
    def test_is_sun_in_blind_spot_elevation_too_high(self, vertical_cover_instance):
        """Test blind spot detection when elevation too high."""
        vertical_cover_instance.blind_spot_left = 30
        vertical_cover_instance.blind_spot_right = 20
        vertical_cover_instance.blind_spot_elevation = 50
        vertical_cover_instance.blind_spot_on = True
        vertical_cover_instance.sol_azi = 155.0  # gamma = 25°
        vertical_cover_instance.sol_elev = 60.0  # Above threshold
        assert vertical_cover_instance.is_sun_in_blind_spot is False

    @pytest.mark.unit
    def test_is_sun_in_blind_spot_outside_area(self, vertical_cover_instance):
        """Test blind spot detection when sun outside area."""
        vertical_cover_instance.blind_spot_left = 30
        vertical_cover_instance.blind_spot_right = 20
        vertical_cover_instance.blind_spot_on = True
        vertical_cover_instance.sol_azi = 180.0  # gamma = 0° (outside spot)
        vertical_cover_instance.sol_elev = 30.0
        assert vertical_cover_instance.is_sun_in_blind_spot is False

    @pytest.mark.unit
    def test_is_sun_in_blind_spot_disabled(self, vertical_cover_instance):
        """Test blind spot detection when disabled."""
        vertical_cover_instance.blind_spot_left = 30
        vertical_cover_instance.blind_spot_right = 20
        vertical_cover_instance.blind_spot_on = False
        vertical_cover_instance.sol_azi = 155.0
        assert vertical_cover_instance.is_sun_in_blind_spot is False

    @pytest.mark.unit
    def test_is_sun_in_blind_spot_none_values(self, vertical_cover_instance):
        """Test blind spot detection with None values (disabled)."""
        vertical_cover_instance.blind_spot_left = None
        vertical_cover_instance.blind_spot_right = None
        vertical_cover_instance.blind_spot_on = True
        assert vertical_cover_instance.is_sun_in_blind_spot is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_all_conditions_met(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid when all conditions are met."""
        # Setup: sun in FOV, above horizon, before sunset, no blind spot
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.blind_spot_on = False

        # All conditions met: valid=True, sunset_valid=False, blind_spot=False
        assert vertical_cover_instance.direct_sun_valid is True

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_after_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False after sunset (reproduces bug scenario)."""
        # Setup: sun still in FOV geometrically, but after sunset time
        mock_datetime.now.return_value = datetime(
            2024, 1, 1, 20, 0, 0
        )  # After sunset
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.blind_spot_on = False

        # valid should be True (geometric check)
        assert vertical_cover_instance.valid is True
        # But direct_sun_valid should be False (includes sunset check)
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_in_blind_spot(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False when sun in blind spot."""
        # Setup: sun in FOV and before sunset, but in blind spot
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        # Configure blind spot
        vertical_cover_instance.blind_spot_left = 25
        vertical_cover_instance.blind_spot_right = 35
        vertical_cover_instance.blind_spot_elevation = 50
        vertical_cover_instance.blind_spot_on = True
        vertical_cover_instance.sol_azi = 165.0  # gamma = 15°, in blind spot
        vertical_cover_instance.sol_elev = 30.0

        # valid should be True (geometric check only)
        assert vertical_cover_instance.valid is True
        # is_sun_in_blind_spot should be True
        assert vertical_cover_instance.is_sun_in_blind_spot is True
        # direct_sun_valid should be False (includes blind spot check)
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_outside_fov(self, mock_datetime, vertical_cover_instance):
        """Test direct_sun_valid returns False when sun outside FOV."""
        # Setup: sun outside FOV
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.sol_azi = 90.0  # Far outside FOV
        vertical_cover_instance.blind_spot_on = False

        # Both should be False
        assert vertical_cover_instance.valid is False
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_before_sunrise(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False before sunrise."""
        # Setup: sun geometrically in FOV, but before sunrise
        mock_datetime.now.return_value = datetime(
            2024, 1, 1, 5, 0, 0
        )  # Before sunrise
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.blind_spot_on = False

        # valid could be True (if elevation is positive - edge case)
        # But direct_sun_valid should be False (before sunrise)
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_with_sunset_offset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid respects sunset offset."""
        # Setup: time is after sunset but within offset
        mock_datetime.now.return_value = datetime(
            2024, 1, 1, 18, 15, 0
        )  # 15 min after sunset
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.sunset_off = 30  # 30 minute offset
        vertical_cover_instance.blind_spot_on = False

        # With 30 min offset, sunset_valid should be False (not yet after offset)
        # So direct_sun_valid should be True (if valid is True)
        assert vertical_cover_instance.direct_sun_valid is True

        # Now test beyond offset
        mock_datetime.now.return_value = datetime(
            2024, 1, 1, 18, 45, 0
        )  # 45 min after sunset
        # Now sunset_valid should be True (after offset)
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_default_position_before_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test default position returns h_def before sunset."""
        # Mock current time before sunset
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.h_def = 75
        assert vertical_cover_instance.default == 75

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_default_position_after_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test default position returns sunset_pos after sunset."""
        # Mock current time after sunset
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.sunset_pos = 0
        assert vertical_cover_instance.default == 0

    @pytest.mark.unit
    def test_fov_method_returns_list(self, vertical_cover_instance):
        """Test fov() method returns azimuth min and max."""
        fov_list = vertical_cover_instance.fov()
        assert fov_list == [135, 225]
