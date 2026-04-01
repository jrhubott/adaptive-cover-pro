"""Tests for SunGeometry standalone class.

Verifies that SunGeometry works independently of AdaptiveGeneralCover,
producing identical results for all sun position analysis properties.
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from custom_components.adaptive_cover_pro.config_types import CoverConfig
from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry


def _make_config(**overrides) -> CoverConfig:
    """Build a CoverConfig with sensible defaults."""
    defaults = {
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "h_def": 50,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_sun_only": False,
        "min_pos_sun_only": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
    }
    defaults.update(overrides)
    return CoverConfig(**defaults)


def _make_logger():
    """Create a mock logger with debug/info/warning/error stubs."""
    logger = MagicMock()
    logger.debug = Mock()
    return logger


def _make_sun_data():
    """Create a mock SunData instance."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return sun_data


# ------------------------------------------------------------------
# gamma
# ------------------------------------------------------------------


class TestGamma:
    """Tests for gamma (surface solar azimuth) calculation."""

    def test_gamma_sun_directly_ahead(self):
        """Gamma is 0 when sun azimuth equals window azimuth."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.gamma == pytest.approx(0.0)

    def test_gamma_sun_to_the_right(self):
        """Gamma is positive when sun is to the right of window normal."""
        sg = SunGeometry(160.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.gamma == pytest.approx(20.0)

    def test_gamma_sun_to_the_left(self):
        """Gamma is negative when sun is to the left of window normal."""
        sg = SunGeometry(200.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.gamma == pytest.approx(-20.0)

    def test_gamma_wrap_around(self):
        """Gamma wraps correctly for large azimuth differences."""
        sg = SunGeometry(10.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.gamma == pytest.approx(170.0)


# ------------------------------------------------------------------
# azi_min_abs / azi_max_abs
# ------------------------------------------------------------------


class TestAzimuthBoundaries:
    """Tests for absolute azimuth FOV boundaries."""

    def test_standard_fov(self):
        """Standard 45-degree FOV around south-facing window."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.azi_min_abs == 135
        assert sg.azi_max_abs == 225

    def test_fov_wrapping_north(self):
        """FOV wraps correctly near north (0/360 boundary)."""
        config = _make_config(win_azi=10, fov_left=20)
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), config, _make_logger())
        assert sg.azi_min_abs == 350  # 10 - 20 + 360

    def test_fov_list(self):
        """fov() returns [min, max] azimuth list."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.fov() == [135, 225]


# ------------------------------------------------------------------
# valid / valid_elevation
# ------------------------------------------------------------------


class TestValidity:
    """Tests for sun validity checks."""

    def test_valid_sun_directly_ahead(self):
        """Sun directly ahead at 45 deg elevation is valid."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.valid is True

    def test_invalid_sun_behind_window(self):
        """Sun behind the window is not valid."""
        sg = SunGeometry(10.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.valid is False

    def test_valid_elevation_no_limits(self):
        """No elevation limits and sun above horizon is valid."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.valid_elevation is True

    def test_valid_elevation_below_horizon(self):
        """Negative elevation is invalid with no limits."""
        sg = SunGeometry(180.0, -5.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.valid_elevation is False

    def test_valid_elevation_within_limits(self):
        """Elevation within configured min/max is valid."""
        config = _make_config(min_elevation=10, max_elevation=60)
        sg = SunGeometry(180.0, 30.0, _make_sun_data(), config, _make_logger())
        assert sg.valid_elevation is True

    def test_valid_elevation_below_min(self):
        """Elevation below minimum limit is invalid."""
        config = _make_config(min_elevation=20)
        sg = SunGeometry(180.0, 10.0, _make_sun_data(), config, _make_logger())
        assert sg.valid_elevation is False

    def test_valid_elevation_above_max(self):
        """Elevation above maximum limit is invalid."""
        config = _make_config(max_elevation=60)
        sg = SunGeometry(180.0, 70.0, _make_sun_data(), config, _make_logger())
        assert sg.valid_elevation is False


# ------------------------------------------------------------------
# sunset_valid
# ------------------------------------------------------------------


class TestSunsetValid:
    """Tests for sunset/sunrise offset validity."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_daytime_not_sunset(self, mock_dt):
        """Midday is not within sunset offset period."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(180.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.sunset_valid is False

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_after_sunset(self, mock_dt):
        """After sunset is within sunset offset period."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 19, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(180.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.sunset_valid is True


# ------------------------------------------------------------------
# direct_sun_valid
# ------------------------------------------------------------------


class TestDirectSunValid:
    """Tests for combined sun validity."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_all_clear(self, mock_dt):
        """All conditions met returns True."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(180.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.direct_sun_valid is True

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_invalid_outside_fov(self, mock_dt):
        """Sun outside FOV returns False."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(10.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.direct_sun_valid is False


# ------------------------------------------------------------------
# control_state_reason
# ------------------------------------------------------------------


class TestControlStateReason:
    """Tests for human-readable reason string."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun(self, mock_dt):
        """Direct sun in FOV returns Direct Sun."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(180.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.control_state_reason == "Direct Sun"

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_fov_exit(self, mock_dt):
        """Sun outside FOV returns Default: FOV Exit."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(10.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.control_state_reason == "Default: FOV Exit"

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_sunset_offset(self, mock_dt):
        """After sunset returns Default: Sunset Offset."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 19, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        sg = SunGeometry(180.0, 45.0, sun_data, _make_config(), _make_logger())
        assert sg.control_state_reason == "Default: Sunset Offset"

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_elevation_limit(self, mock_dt):
        """Elevation below min returns Default: Elevation Limit."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        config = _make_config(min_elevation=20)
        sg = SunGeometry(180.0, 5.0, sun_data, config, _make_logger())
        assert sg.control_state_reason == "Default: Elevation Limit"

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_blind_spot(self, mock_dt):
        """Sun in blind spot returns Default: Blind Spot."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        config = _make_config(
            blind_spot_left=10, blind_spot_right=30, blind_spot_on=True
        )
        # gamma = 180 - 160 = 20, blind spot: fov_left-left to fov_left-right = 35 to 15
        sg = SunGeometry(160.0, 45.0, sun_data, config, _make_logger())
        assert sg.control_state_reason == "Default: Blind Spot"


# ------------------------------------------------------------------
# default
# ------------------------------------------------------------------


class TestDefault:
    """Tests for default position property."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_default_daytime(self, mock_dt):
        """Daytime returns h_def."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        config = _make_config(h_def=50, sunset_pos=10)
        sg = SunGeometry(180.0, 45.0, sun_data, config, _make_logger())
        assert sg.default == 50

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_default_after_sunset_uses_sunset_pos(self, mock_dt):
        """After sunset returns sunset_pos when configured."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 19, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        config = _make_config(h_def=50, sunset_pos=10)
        sg = SunGeometry(180.0, 45.0, sun_data, config, _make_logger())
        assert sg.default == 10

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_default_after_sunset_no_sunset_pos(self, mock_dt):
        """After sunset with no sunset_pos returns h_def."""
        mock_dt.utcnow.return_value = datetime(2024, 1, 1, 19, 0, 0)
        sun_data = _make_sun_data()
        sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
        sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
        config = _make_config(h_def=50, sunset_pos=None)
        sg = SunGeometry(180.0, 45.0, sun_data, config, _make_logger())
        assert sg.default == 50


# ------------------------------------------------------------------
# is_sun_in_blind_spot
# ------------------------------------------------------------------


class TestBlindSpot:
    """Tests for blind spot detection."""

    def test_no_blind_spot_configured(self):
        """No blind spot configured returns False."""
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), _make_config(), _make_logger())
        assert sg.is_sun_in_blind_spot is False

    def test_in_blind_spot(self):
        """Sun in configured blind spot returns True."""
        config = _make_config(
            blind_spot_left=10, blind_spot_right=30, blind_spot_on=True
        )
        # gamma = 180 - 160 = 20, blind spot edges: 45-10=35 to 45-30=15
        sg = SunGeometry(160.0, 45.0, _make_sun_data(), config, _make_logger())
        assert sg.is_sun_in_blind_spot is True

    def test_outside_blind_spot(self):
        """Sun outside blind spot returns False."""
        config = _make_config(
            blind_spot_left=10, blind_spot_right=30, blind_spot_on=True
        )
        # gamma = 180 - 180 = 0, outside blind spot range 15-35
        sg = SunGeometry(180.0, 45.0, _make_sun_data(), config, _make_logger())
        assert sg.is_sun_in_blind_spot is False

    def test_blind_spot_disabled(self):
        """Blind spot disabled returns False even if sun in range."""
        config = _make_config(
            blind_spot_left=10, blind_spot_right=30, blind_spot_on=False
        )
        sg = SunGeometry(160.0, 45.0, _make_sun_data(), config, _make_logger())
        assert sg.is_sun_in_blind_spot is False
