"""Tests for NormalCoverState logic."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from custom_components.adaptive_cover_pro.calculation import NormalCoverState
from tests.cover_helpers import build_horizontal_cover


class TestNormalCoverState:
    """Test NormalCoverState logic."""

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_sun_valid(self, mock_datetime, vertical_cover_instance):
        """Test state when sun is valid - uses calculated position."""
        # Setup mocks for sunset_valid check
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_sun_invalid(self, mock_datetime, vertical_cover_instance):
        """Test state when sun is invalid - uses default position."""
        # Make sun invalid by putting it outside FOV
        vertical_cover_instance.sol_azi = 90.0  # Far outside FOV
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_after_sunset(self, mock_datetime, vertical_cover_instance):
        """Test state after sunset uses sunset_pos."""
        # Set time after sunset
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_max_position_clamping(self, mock_datetime, vertical_cover_instance):
        """Test max position clamping."""
        # Setup for valid sun
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_min_position_clamping(self, mock_datetime, vertical_cover_instance):
        """Test min position clamping."""
        # Setup for valid sun
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_min_position_with_bool_flag_sun_valid(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test min position with bool flag when sun is valid."""
        # Setup for valid sun
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_max_position_with_bool_flag_sun_valid(
        self, mock_datetime, vertical_cover_instance
    ):
        """Test max position with bool flag when sun is valid."""
        # Setup for valid sun, high elevation to get higher percentage
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_combined_min_max_clamping(self, mock_datetime, vertical_cover_instance):
        """Test both min and max clamping together."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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


class TestNormalCoverStateHorizontalMinPosition:
    """Test that NormalCoverState clamps position to at least 1 when sun is valid.

    Horizontal covers can compute 0% when the vertical calculation saturates at h_win,
    causing awning length to be 0. The fix ensures position >= 1 while direct_sun_valid
    is True, preventing open/close-only covers from closing prematurely.
    """

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
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
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
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

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
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
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
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

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cover.sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
        cover.sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))

        state_handler = NormalCoverState(cover)
        state = state_handler.get_state()

        # Sun is outside FOV → default position (0) should be returned, no clamping
        assert state == 0
