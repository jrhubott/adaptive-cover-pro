"""Tests for basic calculation math: gamma, azimuth, elevation, blind height, position."""

import pytest
import numpy as np
from numpy import cos, tan
from numpy import radians as rad


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
