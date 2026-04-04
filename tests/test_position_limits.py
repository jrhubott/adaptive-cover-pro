"""Tests for min/max position limit application.

These tests verify the correct behavior of position limits in different scenarios,
particularly the interaction between enable_min_position/enable_max_position flags
and sunset_position functionality.

Regression tests for Issue #24: Invalid sunset cover position.
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from custom_components.adaptive_cover_pro.calculation import (
    NormalCoverState,
)
from custom_components.adaptive_cover_pro.position_utils import PositionConverter
from tests.cover_helpers import build_vertical_cover


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
def test_issue_24_sunset_position_with_conditional_min_pos(mock_sun_data, mock_logger):
    """Test Issue #24: sunset_position should be used after sunset, not min_position.

    With the pipeline architecture, DefaultHandler applies position limits
    to snapshot.default_position.  When enable_min_position=True (sun-only),
    min_pos is NOT applied when direct_sun_valid=False (sun not in window).
    After sunset, compute_effective_default() returns sunset_pos=0;
    the DefaultHandler sees direct_sun_valid=False so does not apply min_pos.
    Result should be 0 (sunset_pos), not 35 (min_pos).
    """
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot

    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=True,  # only apply min_pos when sun in window
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        assert cover.sunset_valid is True
        assert cover.direct_sun_valid is False

        # Build snapshot with effective default = sunset_pos = 0
        snapshot = PipelineSnapshot(
            cover=cover,
            config=cover.config,
            cover_type="cover_blind",
            default_position=0,          # sunset_pos via compute_effective_default
            is_sunset_active=True,
            configured_default=60,
            configured_sunset_pos=0,
            climate_readings=None,
            climate_mode_enabled=False,
            climate_options=None,
            force_override_sensors={},
            force_override_position=0,
            manual_override_active=False,
            motion_timeout_active=False,
            weather_override_active=False,
            weather_override_position=0,
            weather_bypass_auto_control=True,
            glare_zones=None,
            active_zone_names=frozenset(),
        )

        result = PipelineRegistry([DefaultHandler()]).evaluate(snapshot)

        # min_pos_bool=True means min_pos is only applied when sun is in window.
        # direct_sun_valid=False so min_pos is NOT applied.
        # Result should be sunset_pos (0), NOT min_pos (35).
        assert result.position == 0, (
            f"Expected 0 (sunset_pos), got {result.position} (min_pos applied incorrectly)"
        )


@pytest.mark.unit
def test_sunset_position_with_always_min_pos(mock_sun_data, mock_logger):
    """Test sunset_position with enable_min_position = False (always apply).

    When min_pos_bool=False (enable_min_position=False), min_pos is always
    applied including during the sunset window.  After sunset, the effective
    default = sunset_pos = 0, but min_pos = 35 is always enforced, so the
    result should be max(0, 35) = 35.
    """
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot

    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=False,  # always apply min_pos
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        assert cover.sunset_valid is True
        assert cover.direct_sun_valid is False

        snapshot = PipelineSnapshot(
            cover=cover,
            config=cover.config,
            cover_type="cover_blind",
            default_position=0,          # sunset_pos via compute_effective_default
            is_sunset_active=True,
            configured_default=60,
            configured_sunset_pos=0,
            climate_readings=None,
            climate_mode_enabled=False,
            climate_options=None,
            force_override_sensors={},
            force_override_position=0,
            manual_override_active=False,
            motion_timeout_active=False,
            weather_override_active=False,
            weather_override_position=0,
            weather_bypass_auto_control=True,
            glare_zones=None,
            active_zone_names=frozenset(),
        )

        result = PipelineRegistry([DefaultHandler()]).evaluate(snapshot)

        # min_pos_bool=False means min_pos always applied.
        # max(sunset_pos=0, min_pos=35) = 35
        assert result.position == 35, f"Expected 35 (min_pos always applied), got {result.position}"


@pytest.mark.unit
def test_sun_in_window_with_conditional_min_pos(mock_sun_data, mock_logger):
    """Test min_pos applied during sun in window with enable_min_position = True."""
    # Mock to NOT be sunset time
    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        # Set current time to daytime
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        # Sun directly in front, low elevation → calculated position would be low
        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=15.0,  # Low sun → large calculated position
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
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
        assert result >= 35, (
            f"Expected >= 35 (min_pos applied during sun), got {result}"
        )


@pytest.mark.unit
def test_direct_sun_valid_uses_and_operator(mock_sun_data, mock_logger):
    """Test that direct_sun_valid uses 'and' operator (not bitwise '&')."""
    # This test verifies the fix for the secondary issue in #24
    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        # Set current time to daytime (not sunset)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
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
