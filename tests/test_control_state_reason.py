"""Tests for the control_state_reason diagnostic property.

Tests the human-readable reason for why the cover is in its current state,
covering all possible conditions: direct sun, FOV exit, elevation limit,
sunset offset, and blind spot.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from custom_components.adaptive_cover_pro.calculation import AdaptiveVerticalCover


@pytest.fixture
def mock_logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def mock_sun_data():
    """Return a mock SunData instance."""
    return MagicMock()


def make_cover(mock_sun_data, mock_logger, **overrides) -> AdaptiveVerticalCover:
    """Create an AdaptiveVerticalCover with sensible defaults and optional overrides."""
    defaults = {
        "logger": mock_logger,
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "sun_data": mock_sun_data,
        "fov_left": 45,
        "fov_right": 45,
        "win_azi": 180,
        "h_def": 50,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_bool": False,
        "min_pos_bool": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
        "distance": 0.5,
        "h_win": 2.0,
    }
    defaults.update(overrides)
    return AdaptiveVerticalCover(**defaults)


class TestControlStateReasonDirectSun:
    """Tests for 'Direct Sun' reason."""

    def test_direct_sun_when_sun_in_fov_and_no_exclusions(
        self, mock_sun_data, mock_logger
    ):
        """Sun in FOV, no blind spot, no sunset offset → Direct Sun."""
        cover = make_cover(mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=45.0)

        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            assert cover.control_state_reason == "Direct Sun"

    def test_direct_sun_elevation_in_range(self, mock_sun_data, mock_logger):
        """Sun in FOV with elevation within configured limits → Direct Sun."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=30.0,
            min_elevation=10,
            max_elevation=60,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            assert cover.control_state_reason == "Direct Sun"


class TestControlStateReasonFOVExit:
    """Tests for 'Default: FOV Exit' reason."""

    def test_fov_exit_sun_behind_window(self, mock_sun_data, mock_logger):
        """Sun behind the window (180° away) → Default: FOV Exit."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=0.0,  # 180° from window azimuth 180°
            sol_elev=45.0,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: FOV Exit"

    def test_fov_exit_sun_outside_fov_left(self, mock_sun_data, mock_logger):
        """Sun outside left FOV boundary → Default: FOV Exit."""
        # fov_left=45, win_azi=180: left edge at azimuth 135. sun at 90 is outside.
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=90.0,
            sol_elev=45.0,
            fov_left=45,
            fov_right=45,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: FOV Exit"


class TestControlStateReasonElevationLimit:
    """Tests for 'Default: Elevation Limit' reason."""

    def test_elevation_below_min_elevation(self, mock_sun_data, mock_logger):
        """Sun elevation below configured minimum → Default: Elevation Limit."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=5.0,  # Below min_elevation of 10
            min_elevation=10,
            max_elevation=None,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Elevation Limit"

    def test_elevation_above_max_elevation(self, mock_sun_data, mock_logger):
        """Sun elevation above configured maximum → Default: Elevation Limit."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=80.0,  # Above max_elevation of 70
            min_elevation=None,
            max_elevation=70,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Elevation Limit"

    def test_elevation_outside_both_limits(self, mock_sun_data, mock_logger):
        """Sun elevation outside both min and max → Default: Elevation Limit."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=5.0,  # Below min of 10
            min_elevation=10,
            max_elevation=70,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Elevation Limit"


class TestControlStateReasonSunsetOffset:
    """Tests for 'Default: Sunset Offset' reason."""

    def test_sunset_offset_active(self, mock_sun_data, mock_logger):
        """When sunset_valid is True → Default: Sunset Offset (takes priority over FOV)."""
        cover = make_cover(mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=45.0)

        # Patch sunset_valid to return True (in offset window)
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Sunset Offset"

    def test_sunset_offset_priority_over_fov_exit(self, mock_sun_data, mock_logger):
        """Sunset offset is checked before FOV exit when both conditions are True."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=0.0,  # Outside FOV
            sol_elev=45.0,
        )
        # Both FOV exit and sunset offset are True
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            reason = cover.control_state_reason
            # Sunset offset should take priority
            assert reason == "Default: Sunset Offset"


class TestControlStateReasonBlindSpot:
    """Tests for 'Default: Blind Spot' reason."""

    def test_blind_spot_active(self, mock_sun_data, mock_logger):
        """Sun in configured blind spot → Default: Blind Spot."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            # Blind spot: 10° to 30° from left
            blind_spot_left=10,
            blind_spot_right=30,
            blind_spot_elevation=None,
            blind_spot_on=True,
            fov_left=45,
        )
        # Patch is_sun_in_blind_spot to confirm it's in the blind spot
        with (
            patch.object(
                type(cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(cover),
                "is_sun_in_blind_spot",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Blind Spot"

    def test_blind_spot_not_active_when_disabled(self, mock_sun_data, mock_logger):
        """Blind spot feature disabled → not in blind spot."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            blind_spot_left=10,
            blind_spot_right=30,
            blind_spot_on=False,  # Disabled
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            # Should be Direct Sun because blind spot is disabled
            assert reason == "Direct Sun"


class TestControlStateReasonPriority:
    """Test that conditions are evaluated in the correct priority order."""

    def test_direct_sun_has_highest_priority(self, mock_sun_data, mock_logger):
        """direct_sun_valid=True always returns 'Direct Sun'."""
        cover = make_cover(mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=45.0)
        with (
            patch.object(
                type(cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(cover),
                "is_sun_in_blind_spot",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            assert cover.control_state_reason == "Direct Sun"

    def test_elevation_limit_takes_priority_over_blind_spot(
        self, mock_sun_data, mock_logger
    ):
        """When sun has invalid elevation AND is in blind spot, elevation wins."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=5.0,  # Below min
            min_elevation=10,
        )
        with (
            patch.object(
                type(cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(cover),
                "is_sun_in_blind_spot",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            reason = cover.control_state_reason
            assert reason == "Default: Elevation Limit"

    def test_fov_exit_when_only_azimuth_invalid(self, mock_sun_data, mock_logger):
        """Invalid azimuth with valid elevation → FOV Exit (not elevation limit)."""
        cover = make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=0.0,  # Outside FOV
            sol_elev=45.0,  # Valid elevation
            min_elevation=10,
            max_elevation=80,
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            reason = cover.control_state_reason
            assert reason == "Default: FOV Exit"
