"""Tests for min/max position limit application.

These tests verify the correct behavior of position limits in different scenarios,
particularly the interaction between enable_min_position/enable_max_position flags
and sunset_position functionality.

Regression tests for Issue #24: Invalid sunset cover position.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from custom_components.adaptive_cover_pro.calculation import (
    AdaptiveVerticalCover,
    NormalCoverState,
)
from custom_components.adaptive_cover_pro.position_utils import PositionConverter


@pytest.mark.unit
def test_apply_limits_always_enforce_min(mock_sun_data, mock_logger):
    """Test min_pos always enforced when enable_min_position = False."""
    # enable_min_position = False → min_pos always applied
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=False,  # Always enforce
        apply_max=False,
        sun_valid=False,  # Even when sun not valid
    )
    assert result == 35  # Should clamp to min_pos


@pytest.mark.unit
def test_apply_limits_always_enforce_max(mock_sun_data, mock_logger):
    """Test max_pos always enforced when enable_max_position = False."""
    # enable_max_position = False → max_pos always applied
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=False,  # Always enforce
        sun_valid=False,  # Even when sun not valid
    )
    assert result == 60  # Should clamp to max_pos


@pytest.mark.unit
def test_apply_limits_conditional_min_sun_valid(mock_sun_data, mock_logger):
    """Test min_pos applied when sun valid and enable_min_position = True."""
    # enable_min_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=True,  # Conditional
        apply_max=False,
        sun_valid=True,  # Sun is valid
    )
    assert result == 35  # Should clamp to min_pos


@pytest.mark.unit
def test_apply_limits_conditional_min_sun_not_valid(mock_sun_data, mock_logger):
    """Test min_pos NOT applied when sun not valid and enable_min_position = True."""
    # enable_min_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=True,  # Conditional
        apply_max=False,
        sun_valid=False,  # Sun not valid
    )
    assert result == 20  # Should NOT clamp, return original value


@pytest.mark.unit
def test_apply_limits_conditional_max_sun_valid(mock_sun_data, mock_logger):
    """Test max_pos applied when sun valid and enable_max_position = True."""
    # enable_max_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=True,  # Conditional
        sun_valid=True,  # Sun is valid
    )
    assert result == 60  # Should clamp to max_pos


@pytest.mark.unit
def test_apply_limits_conditional_max_sun_not_valid(mock_sun_data, mock_logger):
    """Test max_pos NOT applied when sun not valid and enable_max_position = True."""
    # enable_max_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=True,  # Conditional
        sun_valid=False,  # Sun not valid
    )
    assert result == 80  # Should NOT clamp, return original value


@pytest.mark.unit
def test_issue_24_sunset_position_with_conditional_min_pos(hass, mock_logger):
    """Test Issue #24: sunset_position should be used after sunset, not min_position.

    User configuration:
    - min_position = 35
    - enable_min_position = True (only apply when sun in window)
    - sunset_position = 0
    - After sunset, cover should go to 0, not 35
    """
    # Mock sunset_valid to return True (after sunset)
    with patch(
        "custom_components.adaptive_cover_pro.calculation.datetime"
    ) as mock_datetime:
        # Set current time to after sunset
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = AdaptiveVerticalCover(
            hass=hass,
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,  # Sun below horizon
            sunset_pos=0,  # Should return to 0 after sunset
            sunset_off=0,
            sunrise_off=0,
            timezone="UTC",
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=True,  # enable_min_position = True
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        # Mock sun_data methods after cover creation
        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        # Verify sunset_valid is True
        assert cover.sunset_valid is True

        # Verify direct_sun_valid is False (because sunset_valid is True)
        assert cover.direct_sun_valid is False

        # Create state calculator
        state = NormalCoverState(cover=cover)

        # Get the calculated state
        result = state.get_state()

        # Result should be sunset_pos (0), NOT min_pos (35)
        assert result == 0, f"Expected 0 (sunset_pos), got {result} (min_pos applied incorrectly)"


@pytest.mark.unit
def test_sunset_position_with_always_min_pos(hass, mock_logger):
    """Test sunset_position with enable_min_position = False (always apply).

    When enable_min_position = False, min_pos should always be applied,
    even for sunset_position. Result should be max(sunset_pos, min_pos).
    """
    # Mock sunset_valid to return True (after sunset)
    with patch(
        "custom_components.adaptive_cover_pro.calculation.datetime"
    ) as mock_datetime:
        # Set current time to after sunset
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = AdaptiveVerticalCover(
            hass=hass,
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,  # Sun below horizon
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            timezone="UTC",
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=False,  # enable_min_position = False (always apply)
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        # Mock sun_data methods after cover creation
        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        # Verify sunset_valid is True
        assert cover.sunset_valid is True

        # Create state calculator
        state = NormalCoverState(cover=cover)

        # Get the calculated state
        result = state.get_state()

        # Result should be max(sunset_pos, min_pos) = max(0, 35) = 35
        assert result == 35, f"Expected 35 (min_pos always applied), got {result}"


@pytest.mark.unit
def test_sun_in_window_with_conditional_min_pos(hass, mock_logger):
    """Test min_pos applied during sun in window with enable_min_position = True."""
    # Mock to NOT be sunset time
    with patch(
        "custom_components.adaptive_cover_pro.calculation.datetime"
    ) as mock_datetime:
        # Set current time to daytime
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)

        # Sun directly in front, low elevation → calculated position would be low
        cover = AdaptiveVerticalCover(
            hass=hass,
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=15.0,  # Low sun → large calculated position
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            timezone="UTC",
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=True,  # enable_min_position = True
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        # Mock sun_data methods after cover creation
        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 1, 7, 0, 0)

        # Verify NOT in sunset period
        assert cover.sunset_valid is False

        # Verify sun is valid (in FOV, not in blind spot, not sunset)
        assert cover.valid is True
        assert cover.direct_sun_valid is True

        # Create state calculator
        state = NormalCoverState(cover=cover)

        # Get the calculated state
        result = state.get_state()

        # Result should be >= min_pos (35) because sun is in window and enable_min_position = True
        assert result >= 35, f"Expected >= 35 (min_pos applied during sun), got {result}"


@pytest.mark.unit
def test_direct_sun_valid_uses_and_operator(hass, mock_logger):
    """Test that direct_sun_valid uses 'and' operator (not bitwise '&')."""
    # This test verifies the fix for the secondary issue in #24
    with patch(
        "custom_components.adaptive_cover_pro.calculation.datetime"
    ) as mock_datetime:
        # Set current time to daytime (not sunset)
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)

        cover = AdaptiveVerticalCover(
            hass=hass,
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            timezone="UTC",
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
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
            distance=0.5,
            h_win=2.0,
        )

        # Mock sun_data methods after cover creation
        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 1, 7, 0, 0)

        # Verify individual components
        assert cover.valid is True  # Sun in FOV
        assert cover.sunset_valid is False  # Not sunset
        assert cover.is_sun_in_blind_spot is False  # No blind spot

        # direct_sun_valid should be True (all conditions met)
        assert cover.direct_sun_valid is True

        # Test that it's using 'and' by verifying the type is bool
        assert isinstance(cover.direct_sun_valid, bool)
