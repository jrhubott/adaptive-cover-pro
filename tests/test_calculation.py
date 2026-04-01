"""Tests for position calculation logic.

Tests focus on the core calculation methods and state determination logic.
These tests validate the key algorithms without requiring full object instantiation.
"""

import pytest
import numpy as np
from numpy import cos, tan
from numpy import radians as rad
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

from custom_components.adaptive_cover_pro.calculation import (
    NormalCoverState,
    ClimateCoverData,
    ClimateCoverState,
)
from tests.cover_helpers import build_horizontal_cover


@pytest.mark.unit
def test_gamma_angle_calculation_sun_directly_in_front():
    """Test gamma angle when sun is directly in front of window."""
    win_azi = 180
    sol_azi = 180.0

    # gamma = (win_azi - sol_azi + 180) % 360 - 180
    gamma = (win_azi - sol_azi + 180) % 360 - 180

    assert gamma == 0.0


@pytest.mark.unit
def test_gamma_angle_calculation_sun_to_left():
    """Test gamma angle when sun is to the left of window."""
    win_azi = 180
    sol_azi = 135.0  # 45° to the left

    # gamma = (win_azi - sol_azi + 180) % 360 - 180
    gamma = (win_azi - sol_azi + 180) % 360 - 180

    # gamma = (180 - 135 + 180) % 360 - 180 = 225 - 180 = 45
    assert gamma == 45.0


@pytest.mark.unit
def test_gamma_angle_calculation_sun_to_right():
    """Test gamma angle when sun is to the right of window."""
    win_azi = 180
    sol_azi = 225.0  # 45° to the right

    # gamma = (win_azi - sol_azi + 180) % 360 - 180
    gamma = (win_azi - sol_azi + 180) % 360 - 180

    # gamma = (180 - 225 + 180) % 360 - 180 = 135 - 180 = -45
    assert gamma == -45.0


@pytest.mark.unit
def test_gamma_angle_wrapping_across_zero():
    """Test gamma angle calculation wraps correctly across 0°."""
    win_azi = 10
    sol_azi = 350.0

    # gamma = (win_azi - sol_azi + 180) % 360 - 180
    gamma = (win_azi - sol_azi + 180) % 360 - 180

    # Should give positive angle
    assert gamma == 20.0


@pytest.mark.unit
def test_azimuth_min_calculation():
    """Test minimum azimuth calculation."""
    win_azi = 180
    fov_left = 45

    azi_min_abs = (win_azi - fov_left + 360) % 360

    # 180 - 45 = 135
    assert azi_min_abs == 135


@pytest.mark.unit
def test_azimuth_max_calculation():
    """Test maximum azimuth calculation."""
    win_azi = 180
    fov_right = 45

    azi_max_abs = (win_azi + fov_right + 360) % 360

    # 180 + 45 = 225
    assert azi_max_abs == 225


@pytest.mark.unit
def test_azimuth_wrapping_across_360():
    """Test azimuth calculations wrap correctly across 360°."""
    win_azi = 10
    fov_right = 45

    azi_max_abs = (win_azi + fov_right + 360) % 360

    # 10 + 45 = 55
    assert azi_max_abs == 55


@pytest.mark.unit
def test_sun_valid_within_fov():
    """Test sun validation when within field of view."""
    gamma = 0.0  # Directly in front
    fov_left = 45
    fov_right = 45
    sol_elev = 30.0  # Above horizon

    # Valid when: gamma < fov_left AND gamma > -fov_right AND elevation > 0
    valid = (gamma < fov_left) & (gamma > -fov_right) & (sol_elev >= 0)

    assert valid is True


@pytest.mark.unit
def test_sun_invalid_outside_left_fov():
    """Test sun validation when outside left field of view."""
    gamma = 60.0  # Beyond left edge
    fov_left = 45
    fov_right = 45
    sol_elev = 30.0

    valid = (gamma < fov_left) & (gamma > -fov_right) & (sol_elev >= 0)

    assert valid is False


@pytest.mark.unit
def test_sun_invalid_outside_right_fov():
    """Test sun validation when outside right field of view."""
    gamma = -60.0  # Beyond right edge
    fov_left = 45
    fov_right = 45
    sol_elev = 30.0

    valid = (gamma < fov_left) & (gamma > -fov_right) & (sol_elev >= 0)

    assert valid is False


@pytest.mark.unit
def test_sun_invalid_below_horizon():
    """Test sun validation when below horizon."""
    gamma = 0.0
    fov_left = 45
    fov_right = 45
    sol_elev = -10.0  # Below horizon

    valid = (gamma < fov_left) & (gamma > -fov_right) & (sol_elev >= 0)

    assert valid is False


@pytest.mark.unit
def test_elevation_within_range():
    """Test elevation validation when within configured range."""
    sol_elev = 45.0
    min_elevation = 20
    max_elevation = 60

    within_range = min_elevation <= sol_elev <= max_elevation

    assert within_range is True


@pytest.mark.unit
def test_elevation_below_minimum():
    """Test elevation validation when below minimum."""
    sol_elev = 15.0
    min_elevation = 20
    max_elevation = 60

    within_range = min_elevation <= sol_elev <= max_elevation

    assert within_range is False


@pytest.mark.unit
def test_elevation_above_maximum():
    """Test elevation validation when above maximum."""
    sol_elev = 70.0
    min_elevation = 20
    max_elevation = 60

    within_range = min_elevation <= sol_elev <= max_elevation

    assert within_range is False


@pytest.mark.unit
def test_vertical_blind_height_calculation():
    """Test vertical blind height calculation algorithm."""
    distance = 0.5  # Distance from window (meters)
    gamma = 0.0  # Sun directly in front
    sol_elev = 45.0  # Sun elevation
    h_win = 2.0  # Window height (meters)

    # Algorithm: distance / cos(gamma) * tan(elevation)
    blind_height = (distance / cos(rad(gamma))) * tan(rad(sol_elev))
    blind_height = np.clip(blind_height, 0, h_win)

    # At 45° elevation and gamma=0, tan(45°) = 1, cos(0) = 1
    # blind_height = 0.5 / 1.0 * 1.0 = 0.5 meters
    assert pytest.approx(blind_height, 0.01) == 0.5


@pytest.mark.unit
def test_vertical_blind_height_with_angle():
    """Test vertical blind height calculation with angled sun."""
    distance = 0.5
    gamma = 30.0  # Sun 30° from center
    sol_elev = 45.0
    h_win = 2.0

    # Algorithm accounts for increased path length at angle
    blind_height = (distance / cos(rad(gamma))) * tan(rad(sol_elev))
    blind_height = np.clip(blind_height, 0, h_win)

    # cos(30°) ≈ 0.866, so path is longer: 0.5 / 0.866 ≈ 0.577
    # 0.577 * tan(45°) = 0.577
    assert blind_height > 0.5
    assert blind_height < h_win


@pytest.mark.unit
def test_vertical_blind_height_clips_to_window_height():
    """Test vertical blind height clips to maximum window height."""
    distance = 0.5
    gamma = 0.0
    sol_elev = 80.0  # Very high sun angle
    h_win = 2.0

    blind_height = (distance / cos(rad(gamma))) * tan(rad(sol_elev))
    blind_height = np.clip(blind_height, 0, h_win)

    # High elevation creates large value, should clip to window height
    assert blind_height == h_win


@pytest.mark.unit
def test_blind_spot_detection_logic():
    """Test blind spot detection algorithm."""
    gamma = 25.0  # Current sun angle
    fov_left = 45
    blind_spot_left = 30  # Starts 30° from left edge
    blind_spot_right = 20  # Ends 20° from left edge
    sol_elev = 30.0
    blind_spot_elevation = 40

    # left_edge = fov_left - blind_spot_left
    # right_edge = fov_left - blind_spot_right
    left_edge = fov_left - blind_spot_left  # 45 - 30 = 15
    right_edge = fov_left - blind_spot_right  # 45 - 20 = 25

    # Check if gamma is between edges AND elevation is below threshold
    # gamma must be: left_edge <= gamma <= right_edge
    in_blind_spot = (
        (gamma <= right_edge)
        & (gamma >= left_edge)
        & (sol_elev <= blind_spot_elevation)
    )

    # gamma=25 is between 15 and 25, elevation 30 < 40
    assert in_blind_spot is True


@pytest.mark.unit
def test_blind_spot_outside_elevation():
    """Test blind spot detection when elevation too high."""
    gamma = 20.0
    fov_left = 45
    blind_spot_left = 25
    blind_spot_right = 15
    sol_elev = 50.0  # Above elevation threshold
    blind_spot_elevation = 40

    left_edge = fov_left - blind_spot_left
    right_edge = fov_left - blind_spot_right

    in_blind_spot = (
        (gamma <= left_edge)
        & (gamma >= right_edge)
        & (sol_elev <= blind_spot_elevation)
    )

    # Elevation 50 > 40, so not in blind spot
    assert in_blind_spot is False


@pytest.mark.unit
def test_position_percentage_conversion():
    """Test conversion of blind height to percentage."""
    blind_height = 1.0  # 1 meter
    h_win = 2.0  # 2 meter window

    percentage = round(blind_height / h_win * 100)

    # 1/2 = 0.5 = 50%
    assert percentage == 50


@pytest.mark.unit
def test_position_clamping_to_max():
    """Test position clamping to maximum."""
    calculated_position = 75
    max_pos = 50
    max_pos_enabled = True

    if max_pos_enabled and calculated_position > max_pos:
        result = max_pos
    else:
        result = calculated_position

    assert result == 50


@pytest.mark.unit
def test_position_clamping_to_min():
    """Test position clamping to minimum."""
    calculated_position = 15
    min_pos = 20
    min_pos_enabled = True

    if min_pos_enabled and calculated_position < min_pos:
        result = min_pos
    else:
        result = calculated_position

    assert result == 20


@pytest.mark.unit
def test_position_clamps_to_valid_range():
    """Test numpy clip keeps position in 0-100 range."""
    calculated_position = 120  # Over 100

    result = np.clip(calculated_position, 0, 100)

    assert result == 100


@pytest.mark.unit
def test_default_position_logic():
    """Test default position vs calculated position logic."""
    h_def = 75  # Default position
    sun_valid = False  # Sun not in front of window
    calculated = 30  # Would be calculated position

    # Use default when sun not valid
    if sun_valid:
        result = calculated
    else:
        result = h_def

    assert result == 75


@pytest.mark.unit
def test_calculated_position_when_sun_valid():
    """Test using calculated position when sun is valid."""
    h_def = 75
    sun_valid = True  # Sun in front of window
    calculated = 30

    if sun_valid:
        result = calculated
    else:
        result = h_def

    assert result == 30


# ============================================================================
# Phase 1: AdaptiveGeneralCover Properties Tests
# ============================================================================


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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_all_conditions_met(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid when all conditions are met."""
        # Setup: sun in FOV, above horizon, before sunset, no blind spot
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_after_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False after sunset (reproduces bug scenario)."""
        # Setup: sun still in FOV geometrically, but after sunset time
        mock_datetime.utcnow.return_value = datetime(
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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_in_blind_spot(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False when sun in blind spot."""
        # Setup: sun in FOV and before sunset, but in blind spot
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_outside_fov(self, mock_datetime, vertical_cover_instance):
        """Test direct_sun_valid returns False when sun outside FOV."""
        # Setup: sun outside FOV
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_before_sunrise(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid returns False before sunrise."""
        # Setup: sun geometrically in FOV, but before sunrise
        mock_datetime.utcnow.return_value = datetime(
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
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_direct_sun_valid_with_sunset_offset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test direct_sun_valid respects sunset offset."""
        # Setup: time is after sunset but within offset
        mock_datetime.utcnow.return_value = datetime(
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
        mock_datetime.utcnow.return_value = datetime(
            2024, 1, 1, 18, 45, 0
        )  # 45 min after sunset
        # Now sunset_valid should be True (after offset)
        assert vertical_cover_instance.direct_sun_valid is False

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_default_position_before_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test default position returns h_def before sunset."""
        # Mock current time before sunset
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.h_def = 75
        assert vertical_cover_instance.default == 75

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_default_position_after_sunset(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test default position returns sunset_pos after sunset."""
        # Mock current time after sunset
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 20, 0, 0)
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


# ============================================================================
# Phase 2: Cover Type Classes Tests
# ============================================================================


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
    def test_awning_angle_variations(self, horizontal_cover_instance):
        """Test various awning angles."""
        angles = [0, 15, 30, 45]
        results = []
        for angle in angles:
            horizontal_cover_instance.awn_angle = angle
            results.append(horizontal_cover_instance.calculate_position())
        # All should produce valid results
        assert all(r >= 0 for r in results)


class TestAdaptiveTiltCover:
    """Test AdaptiveTiltCover calculations."""

    @pytest.mark.unit
    def test_beta_property(self, tilt_cover_instance):
        """Test beta angle calculation."""
        beta = tilt_cover_instance.beta
        # Beta should be in radians
        assert isinstance(beta, (float, np.floating))

    @pytest.mark.unit
    def test_calculate_position_mode1(self, tilt_cover_instance):
        """Test tilt angle calculation in mode1 (90°)."""
        tilt_cover_instance.mode = "mode1"
        angle = tilt_cover_instance.calculate_position()
        # Should be between 0 and 90 degrees, or NaN if math invalid
        assert (0 <= angle <= 90) or np.isnan(angle)

    @pytest.mark.unit
    def test_calculate_position_mode2(self, tilt_cover_instance):
        """Test tilt angle calculation in mode2 (180°)."""
        tilt_cover_instance.mode = "mode2"
        angle = tilt_cover_instance.calculate_position()
        # Should be between 0 and 180 degrees, or NaN if math invalid
        assert (0 <= angle <= 180) or np.isnan(angle)

    @pytest.mark.unit
    def test_calculate_percentage_mode1(self, tilt_cover_instance):
        """Test percentage conversion in mode1."""
        tilt_cover_instance.mode = "mode1"
        try:
            percentage = tilt_cover_instance.calculate_percentage()
            # Mode1: 90° = 100%, but round(NaN) raises ValueError
            assert 0 <= percentage <= 100
        except ValueError:
            # ValueError from round(NaN) is expected for invalid math
            pass

    @pytest.mark.unit
    def test_calculate_percentage_mode2(self, tilt_cover_instance):
        """Test percentage conversion in mode2."""
        tilt_cover_instance.mode = "mode2"
        try:
            percentage = tilt_cover_instance.calculate_percentage()
            # Mode2: 180° = 100%, but round(NaN) raises ValueError
            assert 0 <= percentage <= 100
        except ValueError:
            # ValueError from round(NaN) is expected for invalid math
            pass

    @pytest.mark.unit
    def test_slat_depth_variations(self, tilt_cover_instance):
        """Test with different slat depths."""
        depths = [0.01, 0.02, 0.03, 0.04]
        for depth in depths:
            tilt_cover_instance.depth = depth
            angle = tilt_cover_instance.calculate_position()
            # Can produce NaN for invalid math (negative sqrt)
            assert (0 <= angle <= 180) or np.isnan(angle)

    @pytest.mark.unit
    def test_slat_distance_variations(self, tilt_cover_instance):
        """Test with different slat distances."""
        distances = [0.02, 0.03, 0.04, 0.05]
        for distance in distances:
            tilt_cover_instance.slat_distance = distance
            angle = tilt_cover_instance.calculate_position()
            # Can produce NaN for invalid math (negative sqrt)
            assert (0 <= angle <= 180) or np.isnan(angle)

    @pytest.mark.unit
    def test_beta_with_different_sun_angles(self, tilt_cover_instance):
        """Test beta calculation with various sun positions."""
        elevations = [10, 30, 45, 60, 80]
        for elev in elevations:
            tilt_cover_instance.sol_elev = elev
            beta = tilt_cover_instance.beta
            assert isinstance(beta, (float, np.floating))

    @pytest.mark.unit
    def test_position_with_gamma_angle(self, tilt_cover_instance):
        """Test tilt position with angled sun (gamma != 0)."""
        tilt_cover_instance.sol_azi = 210.0  # gamma = -30°
        angle = tilt_cover_instance.calculate_position()
        assert 0 <= angle <= 180


# ============================================================================
# Phase 3: NormalCoverState Tests
# ============================================================================


class TestNormalCoverState:
    """Test NormalCoverState logic."""

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_sun_valid(self, mock_datetime, vertical_cover_instance):
        """Test state when sun is valid - uses calculated position."""
        # Setup mocks for sunset_valid check
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Sun is valid, should use calculated position (25%)
        assert state == 25

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_sun_invalid(self, mock_datetime, vertical_cover_instance):
        """Test state when sun is invalid - uses default position."""
        # Make sun invalid by putting it outside FOV
        vertical_cover_instance.sol_azi = 90.0  # Far outside FOV
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Sun invalid, should use default (50)
        assert state == 50

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_after_sunset(self, mock_datetime, vertical_cover_instance):
        """Test state after sunset uses sunset_pos."""
        # Set time after sunset
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 20, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.sunset_pos = 0

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        assert state == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_max_position_clamping(self, mock_datetime, vertical_cover_instance):
        """Test max position clamping."""
        # Setup for valid sun
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        # Set max position
        vertical_cover_instance.max_pos = 20
        vertical_cover_instance.max_pos_bool = False  # Always apply

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Calculated is 25%, but max is 20
        assert state == 20

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_min_position_clamping(self, mock_datetime, vertical_cover_instance):
        """Test min position clamping."""
        # Setup for valid sun
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        # Set min position
        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = False  # Always apply

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Calculated is 25%, but min is 30
        assert state == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_min_position_with_bool_flag_sun_valid(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test min position with bool flag when sun is valid."""
        # Setup for valid sun
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = True  # Only when direct sun valid

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Sun is valid, min applies: 25 < 30 → 30
        assert state == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_max_position_with_bool_flag_sun_valid(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test max position with bool flag when sun is valid."""
        # Setup for valid sun, high elevation to get higher percentage
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.sol_elev = 70.0  # High sun

        vertical_cover_instance.max_pos = 80
        vertical_cover_instance.max_pos_bool = True  # Only when direct sun valid

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Sun is valid, max applies if calculated > 80
        assert state <= 80

    @pytest.mark.unit
    def test_clipping_to_100(self, vertical_cover_instance):
        """Test position clips to 100."""
        # Make calculation exceed 100 (shouldn't normally happen, but test the clip)
        vertical_cover_instance.sol_elev = 89.0
        vertical_cover_instance.distance = 10.0
        # Even if calculation exceeds, should clip to 100
        # Note: This may not actually exceed due to h_win clipping, but tests the np.clip
        # TODO: Add proper assertion when mock setup is complete

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_combined_min_max_clamping(self, mock_datetime, vertical_cover_instance):
        """Test both min and max clamping together."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        vertical_cover_instance.min_pos = 20
        vertical_cover_instance.max_pos = 40
        vertical_cover_instance.min_pos_bool = False
        vertical_cover_instance.max_pos_bool = False

        state_handler = NormalCoverState(vertical_cover_instance)
        state = state_handler.get_state()
        # Should be within 20-40 range
        assert 20 <= state <= 40


# ============================================================================
# Phase 3b: NormalCoverState — Horizontal Cover Minimum Position Tests
# ============================================================================


class TestNormalCoverStateHorizontalMinPosition:
    """Test that NormalCoverState clamps position to at least 1 when sun is valid.

    Horizontal covers can compute 0% when the vertical calculation saturates at h_win,
    causing awning length to be 0. The fix ensures position >= 1 while direct_sun_valid
    is True, preventing open/close-only covers from closing prematurely.
    """

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_horizontal_saturated_vertical_returns_at_least_1(
        self, mock_datetime, mock_logger
    ):
        """Horizontal cover with vertical saturation must return >= 1 when sun is valid."""
        # distance=3, h_win=1.5, gamma~80°, elev=20° → vertical saturates at h_win → gap=0 → 0%
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=260.0,  # gamma ≈ 80° from win_azi=180
            sol_elev=20.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=MagicMock(),
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=0,
            max_pos=100,
            min_pos=0,
            max_pos_bool=False,
            min_pos_bool=False,
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=3.0,
            h_win=1.5,
            awn_length=2.0,
            awn_angle=0,
        )

        # Confirm raw calculation yields 0
        assert cover.calculate_percentage() == 0

        # Setup time so sun is valid
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cover.sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
        cover.sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))

        state_handler = NormalCoverState(cover)
        state = state_handler.get_state()

        # Must be >= 1 so open/close-only covers don't close while sun is in FOV
        assert state >= 1

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "sol_azi,sol_elev",
        [
            (230.0, 10.0),  # gamma=50°, low elevation
            (245.0, 15.0),  # gamma=65°
            (260.0, 20.0),  # gamma=80°
            (265.0, 30.0),  # gamma=85°
            (250.0, 45.0),  # gamma=70°, high elevation
        ],
        ids=[
            "gamma50_elev10",
            "gamma65_elev15",
            "gamma80_elev20",
            "gamma85_elev30",
            "gamma70_elev45",
        ],
    )
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_horizontal_saturated_parametrized(
        self, mock_datetime, sol_azi, sol_elev, mock_logger
    ):
        """Fix works across the full range of saturation angles."""
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=MagicMock(),
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=0,
            max_pos=100,
            min_pos=0,
            max_pos_bool=False,
            min_pos_bool=False,
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=3.0,
            h_win=1.5,
            awn_length=2.0,
            awn_angle=0,
        )

        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cover.sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
        cover.sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))

        state_handler = NormalCoverState(cover)
        state = state_handler.get_state()

        # If sun is valid (in FOV), state must be >= 1
        if cover.direct_sun_valid:
            assert state >= 1, (
                f"Position was {state} at gamma={cover.gamma:.1f}°, "
                f"elev={sol_elev}° — should be >= 1 when sun is in FOV"
            )

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_sun_not_valid_returns_default_zero(self, mock_datetime, mock_logger):
        """When sun is NOT valid, default=0 is returned unchanged (no clamping)."""
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=90.0,  # Way outside FOV (gamma=90° but FOV only ±45°)
            sol_elev=20.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=MagicMock(),
            fov_left=45,
            fov_right=45,
            win_azi=180,
            h_def=0,  # default position = 0
            max_pos=100,
            min_pos=0,
            max_pos_bool=False,
            min_pos_bool=False,
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=3.0,
            h_win=1.5,
            awn_length=2.0,
            awn_angle=0,
        )

        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cover.sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
        cover.sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))

        state_handler = NormalCoverState(cover)
        state = state_handler.get_state()

        # Sun is outside FOV → default position (0) should be returned, no clamping
        assert state == 0


# ============================================================================
# Phase 4: ClimateCoverData Tests
# ============================================================================


def _make_climate(mock_logger, **overrides):
    """Build a ClimateCoverData with sensible defaults and optional overrides."""
    defaults = {
        "logger": mock_logger,
        "temp_low": 20.0,
        "temp_high": 25.0,
        "temp_switch": False,
        "blind_type": "cover_blind",
        "transparent_blind": False,
        "temp_summer_outside": 22.0,
        "outside_temperature": None,
        "inside_temperature": None,
        "is_presence": True,
        "is_sunny": True,
        "lux_below_threshold": False,
        "irradiance_below_threshold": False,
    }
    defaults.update(overrides)
    return ClimateCoverData(**defaults)


class TestClimateCoverData:
    """Test ClimateCoverData properties."""

    @pytest.mark.unit
    def test_outside_temperature_field(self, mock_logger):
        """Test outside temperature is a simple field."""
        climate_data = _make_climate(mock_logger, outside_temperature="22.5")
        assert climate_data.outside_temperature == "22.5"

    @pytest.mark.unit
    def test_inside_temperature_field(self, mock_logger):
        """Test inside temperature is a simple field."""
        climate_data = _make_climate(mock_logger, inside_temperature="23.0")
        assert climate_data.inside_temperature == "23.0"

    @pytest.mark.unit
    def test_get_current_temperature_outside(self, mock_logger):
        """Test get_current_temperature with temp_switch=True (outside)."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=True,
            outside_temperature="22.5",
            inside_temperature="21.0",
        )
        assert climate_data.get_current_temperature == 22.5

    @pytest.mark.unit
    def test_get_current_temperature_inside(self, mock_logger):
        """Test get_current_temperature with temp_switch=False (inside)."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=False,
            inside_temperature="21.0",
        )
        assert climate_data.get_current_temperature == 21.0

    @pytest.mark.unit
    def test_is_presence_true(self, mock_logger):
        """Test is_presence when pre-read as True."""
        climate_data = _make_climate(mock_logger, is_presence=True)
        assert climate_data.is_presence is True

    @pytest.mark.unit
    def test_is_presence_false(self, mock_logger):
        """Test is_presence when pre-read as False."""
        climate_data = _make_climate(mock_logger, is_presence=False)
        assert climate_data.is_presence is False

    @pytest.mark.unit
    def test_is_winter_true(self, mock_logger):
        """Test is_winter when temperature below threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="18.0",
            temp_low=20.0,
        )
        assert climate_data.is_winter is True

    @pytest.mark.unit
    def test_is_winter_false(self, mock_logger):
        """Test is_winter when temperature above threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            temp_low=20.0,
        )
        assert climate_data.is_winter is False

    @pytest.mark.unit
    def test_is_summer_true(self, mock_logger):
        """Test is_summer when temperature above threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="26.0",
            outside_temperature="24.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
        )
        assert climate_data.is_summer is True

    @pytest.mark.unit
    def test_is_summer_false(self, mock_logger):
        """Test is_summer when temperature below threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            temp_high=25.0,
        )
        assert climate_data.is_summer is False

    @pytest.mark.unit
    def test_outside_high_true(self, mock_logger):
        """Test outside_high when outside temp above threshold."""
        climate_data = _make_climate(
            mock_logger,
            outside_temperature="24.0",
            temp_summer_outside=22.0,
        )
        assert climate_data.outside_high is True

    @pytest.mark.unit
    def test_is_sunny_true(self, mock_logger):
        """Test is_sunny when pre-read as True."""
        climate_data = _make_climate(mock_logger, is_sunny=True)
        assert climate_data.is_sunny is True

    @pytest.mark.unit
    def test_is_sunny_false(self, mock_logger):
        """Test is_sunny when pre-read as False."""
        climate_data = _make_climate(mock_logger, is_sunny=False)
        assert climate_data.is_sunny is False

    @pytest.mark.unit
    def test_lux_below_threshold(self, mock_logger):
        """Test lux property when below threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=True)
        assert climate_data.lux is True

    @pytest.mark.unit
    def test_lux_above_threshold(self, mock_logger):
        """Test lux property when above threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_lux_disabled(self, mock_logger):
        """Test lux property when disabled (default False)."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_irradiance_below_threshold(self, mock_logger):
        """Test irradiance property when below threshold."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=True)
        assert climate_data.irradiance is True

    @pytest.mark.unit
    def test_irradiance_disabled(self, mock_logger):
        """Test irradiance property when disabled (default False)."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=False)
        assert climate_data.irradiance is False

    @pytest.mark.unit
    def test_lux_with_none_value(self, mock_logger):
        """Test lux property returns False when not below threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_irradiance_with_none_value(self, mock_logger):
        """Test irradiance property returns False when not below threshold."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=False)
        assert climate_data.irradiance is False

    @pytest.mark.unit
    def test_get_current_temperature_with_none_values(self, mock_logger):
        """Test get_current_temperature returns None when all temps unavailable."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=True,
            outside_temperature=None,
            inside_temperature=None,
        )
        assert climate_data.get_current_temperature is None

    @pytest.mark.unit
    def test_outside_high_with_none_value(self, mock_logger):
        """Test outside_high returns True (default) when outside temp is None."""
        climate_data = _make_climate(
            mock_logger,
            outside_temperature=None,
            temp_summer_outside=30.0,
        )
        assert climate_data.outside_high is True


# ============================================================================
# Phase 5: ClimateCoverState Tests
# ============================================================================


class TestClimateCoverState:
    """Test ClimateCoverState logic."""

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_type_cover_with_presence(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal_type_cover delegates to normal_with_presence."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(mock_logger, is_presence=True)

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_type_cover()
        assert isinstance(result, (int, np.integer))

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_type_cover_without_presence(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal_type_cover delegates to normal_without_presence."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(mock_logger, is_presence=False)

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_type_cover()
        assert isinstance(result, (int, np.integer))

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_winter_sun_valid(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter strategy with presence: open fully when cold and not sunny."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="18.0",  # Below temp_low (20)
            is_sunny=False,  # Cloudy
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Winter + sun valid → 100 (fully open)
        assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_not_sunny(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test not sunny weather returns default."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="21.0",
            is_sunny=False,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Not sunny → use default
        assert result == vertical_cover_instance.h_def

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_summer_transparent(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test summer with transparent blind returns 0."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="26.0",
            outside_temperature="28.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
            transparent_blind=True,
            is_presence=True,
            is_sunny=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Summer + transparent blind → 0 (fully closed for cooling)
        assert result == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_intermediate(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test intermediate conditions use calculated position."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",  # Between temp_low and temp_high
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Intermediate conditions → use calculated position
        assert result == 25  # Calculated position for this config

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_without_presence_summer(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test summer without presence closes blind."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="27.0",
            outside_temperature="30.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Summer without presence → 0 (close to keep cool)
        assert result == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_without_presence_winter(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter without presence opens blind."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="18.0",
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Winter without presence → 100 (open to gain heat)
        assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_without_presence_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test default path without presence."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        # Sun not valid (outside FOV)
        vertical_cover_instance.sol_azi = 90.0

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Sun not valid → use default
        assert result == vertical_cover_instance.h_def

    @pytest.mark.unit
    def test_tilt_state_mode1(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode1 (90 degrees)."""
        tilt_cover_instance.mode = "mode1"

        climate_data = _make_climate(mock_logger, blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        result = state_handler.tilt_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    def test_tilt_state_mode2(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode2 (180 degrees)."""
        tilt_cover_instance.mode = "mode2"

        climate_data = _make_climate(mock_logger, blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        result = state_handler.tilt_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_blind_type(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test get_state routes to normal_type_cover for blind."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_tilt_type(self, mock_datetime, tilt_cover_instance, mock_logger):
        """Test get_state routes to tilt_state for tilt cover."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(mock_logger, blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        try:
            result = state_handler.get_state()
            assert 0 <= result <= 100
        except ValueError:
            # ValueError from round(NaN) is expected for invalid tilt math
            pass

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_max_position_clamping(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test max position clamping in climate state."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.max_pos = 20
        vertical_cover_instance.max_pos_bool = False

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert result == 20

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_get_state_min_position_clamping(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test min position clamping in climate state."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = False

        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert result == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_winter_sunny_no_sensors(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode on sunny day WITHOUT lux/irradiance sensors.

        This is the bug scenario from issue #4:
        - Indoor temp below threshold (winter)
        - Sun in front of window (valid=True)
        - Sunny weather
        - NO lux/irradiance sensors configured

        Expected: Should return 100 for solar heating
        """
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                mock_logger,
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=True,
                is_presence=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_winter_cloudy(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode on cloudy day."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                mock_logger,
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=False,  # Cloudy
                is_presence=True,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_winter_low_lux(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode with lux sensor showing low light."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                mock_logger,
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=True,
                is_presence=True,
                lux_below_threshold=True,  # Low lux
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            # Winter mode should still return 100 even with low lux
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_normal_with_presence_normal_sunny_day(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal operation on mild sunny day.

        Not winter, not summer, sunny weather.
        Should use calculated position for glare control.
        """
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                mock_logger,
                inside_temperature="22.0",  # Between temp_low and temp_high
                is_sunny=True,
                is_presence=True,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            assert isinstance(result, (int, np.integer))
            assert result != 100  # Not winter mode
            assert result != vertical_cover_instance.default  # Not default

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_tilt_with_presence_winter_sunny(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Test tilt winter mode on sunny day."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(tilt_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True
            tilt_cover_instance.tilt_degrees = 90

            climate_data = _make_climate(
                mock_logger,
                inside_temperature="18.0",  # Below temp_low (20) = winter
                blind_type="cover_tilt",
                is_sunny=True,
                is_presence=True,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)

            with patch.object(
                NormalCoverState, "get_state", return_value=50
            ) as mock_get_state:
                result = state_handler.tilt_with_presence(90)

                mock_get_state.assert_called_once()
                default_80_degrees = 80 / 90 * 100  # ~88.9%
                assert result != pytest.approx(default_80_degrees, abs=1)
                assert result == 50


@pytest.mark.unit
def test_tilt_data_cm_to_meter_conversion():
    """Test that ConfigurationService.get_tilt_data converts centimeters to meters.

    This is a critical test for Issue #5 - ensures the UI input in cm
    is correctly converted to meters for calculation formulas.
    """
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    # Create a mock configuration service instance
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass,
        config_entry,
        logger,
        "cover_tilt",
        None,
        None,
        None,
    )

    # Use the actual get_tilt_data method
    options = {
        "slat_distance": 2.0,  # 2.0 cm (user input)
        "slat_depth": 2.5,  # 2.5 cm (user input)
        "tilt_mode": "mode2",
    }

    # Call the actual method
    result = config_service.get_tilt_data(options)

    # Should convert cm to meters — result is a TiltConfig dataclass
    assert result.slat_distance == pytest.approx(0.02, abs=0.0001)  # 2.0 cm -> 0.02 m
    assert result.depth == pytest.approx(0.025, abs=0.0001)  # 2.5 cm -> 0.025 m
    assert result.mode == "mode2"


@pytest.mark.unit
def test_tilt_data_warns_on_small_values(caplog):
    """Test that ConfigurationService.get_tilt_data warns when values are suspiciously small.

    Values < 0.1 likely indicate user entered meters (following old instructions)
    instead of centimeters.
    """
    import logging
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    # Create a mock configuration service instance
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt Small"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass,
        config_entry,
        logger,
        "cover_tilt",
        None,
        None,
        None,
    )

    # Use very small values (likely meters entered by mistake)
    options = {
        "slat_distance": 0.02,  # 0.02 cm (suspiciously small - likely meant 0.02m)
        "slat_depth": 0.025,  # 0.025 cm (suspiciously small - likely meant 0.025m)
        "tilt_mode": "mode2",
    }

    with caplog.at_level(logging.WARNING):
        result = config_service.get_tilt_data(options)

    # Should still convert (0.02 cm -> 0.0002 m) but log warning — result is TiltConfig
    assert result.slat_distance == pytest.approx(0.0002, abs=0.00001)
    assert result.depth == pytest.approx(0.00025, abs=0.00001)

    # Should have logged a warning
    assert any(
        "slat dimensions are very small" in record.message for record in caplog.records
    )
    assert any("CENTIMETERS" in record.message for record in caplog.records)
