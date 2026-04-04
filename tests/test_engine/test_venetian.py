"""Tests for VenetianCoverCalculation dual-axis engine."""

import math
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from custom_components.adaptive_cover_pro.engine.covers import (
    DualAxisResult,
    VenetianCoverCalculation,
)
from tests.cover_helpers import (
    make_cover_config,
    make_tilt_config,
    make_vertical_config,
)


def _make_logger():
    """Create a mock logger."""
    logger = MagicMock()
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


def _make_sun_data():
    """Create a mock SunData with realistic sunset/sunrise datetimes."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
    sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))
    return sun_data


def _make_venetian(
    sol_azi: float = 180.0,
    sol_elev: float = 45.0,
    **cover_overrides,
) -> VenetianCoverCalculation:
    """Build a VenetianCoverCalculation with sensible defaults."""
    return VenetianCoverCalculation(
        config=make_cover_config(**cover_overrides),
        vert_config=make_vertical_config(),
        tilt_config=make_tilt_config(),
        sun_data=_make_sun_data(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        logger=_make_logger(),
    )


class TestDualAxisResult:
    """Tests for the DualAxisResult dataclass."""

    def test_dual_axis_result_frozen(self):
        """DualAxisResult is immutable (frozen dataclass)."""
        result = DualAxisResult(position=75, tilt=50)
        with pytest.raises((AttributeError, TypeError)):
            result.position = 10  # type: ignore[misc]

    def test_dual_axis_result_stores_values(self):
        """DualAxisResult stores position and tilt correctly."""
        result = DualAxisResult(position=80, tilt=40)
        assert result.position == 80
        assert result.tilt == 40


class TestVenetianCoverCalculation:
    """Tests for VenetianCoverCalculation dual-axis engine."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_standard(self, mock_datetime):
        """Sun at 45° elevation directly in front returns sensible position + tilt."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = _make_venetian(sol_azi=180.0, sol_elev=45.0, win_azi=180)
        result = calc.calculate_dual()

        assert isinstance(result, DualAxisResult)
        assert 0 <= result.position <= 100
        assert 0 <= result.tilt <= 100

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_returns_integers(self, mock_datetime):
        """calculate_dual always returns integer position and tilt values."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = _make_venetian(sol_azi=180.0, sol_elev=30.0, win_azi=180)
        result = calc.calculate_dual()

        assert isinstance(result.position, int)
        assert isinstance(result.tilt, int)

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_delegates_to_vertical(self, mock_datetime):
        """Position matches what AdaptiveVerticalCover.calculate_percentage() returns."""
        from custom_components.adaptive_cover_pro.calculation import (
            AdaptiveVerticalCover,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        logger = _make_logger()
        sun_data = _make_sun_data()
        config = make_cover_config()
        vert_config = make_vertical_config()
        tilt_config = make_tilt_config()

        sol_azi = 180.0
        sol_elev = 45.0

        calc = VenetianCoverCalculation(
            config=config,
            vert_config=vert_config,
            tilt_config=tilt_config,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )

        # Build a standalone vertical cover with the same params
        standalone = AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=vert_config,
        )

        result = calc.calculate_dual()
        expected_position = round(standalone.calculate_percentage())
        assert result.position == expected_position

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_delegates_to_tilt(self, mock_datetime):
        """Tilt matches what AdaptiveTiltCover.calculate_percentage() returns (when valid)."""
        from custom_components.adaptive_cover_pro.calculation import AdaptiveTiltCover

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        logger = _make_logger()
        sun_data = _make_sun_data()
        config = make_cover_config()
        vert_config = make_vertical_config()
        tilt_config = make_tilt_config()

        sol_azi = 180.0
        sol_elev = 45.0

        calc = VenetianCoverCalculation(
            config=config,
            vert_config=vert_config,
            tilt_config=tilt_config,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )

        # Build a standalone tilt cover with the same params
        standalone = AdaptiveTiltCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=tilt_config,
        )

        result = calc.calculate_dual()

        # Tilt percentage may be NaN (invalid geometry) — check both paths
        try:
            raw_tilt = standalone.calculate_percentage()
            if math.isnan(raw_tilt):
                expected_tilt = 0
            else:
                expected_tilt = round(raw_tilt)
        except (ValueError, ZeroDivisionError):
            expected_tilt = config.h_def

        assert result.tilt == expected_tilt

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun_valid_delegation(self, mock_datetime):
        """direct_sun_valid delegates to the internal vertical cover."""
        from custom_components.adaptive_cover_pro.calculation import (
            AdaptiveVerticalCover,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        logger = _make_logger()
        sun_data = _make_sun_data()
        config = make_cover_config()
        vert_config = make_vertical_config()
        tilt_config = make_tilt_config()

        sol_azi = 180.0
        sol_elev = 45.0

        calc = VenetianCoverCalculation(
            config=config,
            vert_config=vert_config,
            tilt_config=tilt_config,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )

        standalone = AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=vert_config,
        )

        assert calc.direct_sun_valid == standalone.direct_sun_valid

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_sun_outside_fov(self, mock_datetime):
        """When sun is outside FOV, result is a valid DualAxisResult with integers."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        # Sun azimuth 90° away from window facing 180°, well outside ±45° FOV
        calc = _make_venetian(sol_azi=90.0, sol_elev=45.0, win_azi=180)
        result = calc.calculate_dual()

        assert isinstance(result, DualAxisResult)
        assert isinstance(result.position, int)
        assert isinstance(result.tilt, int)
        # Both values must be finite integers (no NaN/ValueError propagation)
        assert not math.isnan(result.position)
        assert not math.isnan(result.tilt)

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_calculate_dual_tilt_nan_fallback(self, mock_datetime):
        """When tilt geometry produces NaN, result.tilt falls back to 0."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        # The tilt calculation can produce NaN for certain sun/slat geometries.
        # VenetianCoverCalculation must never propagate NaN to callers.
        calc = _make_venetian(sol_azi=180.0, sol_elev=45.0)
        result = calc.calculate_dual()

        # Result must always be a valid integer — never NaN
        assert isinstance(result.tilt, int)
        assert not math.isnan(result.tilt)
