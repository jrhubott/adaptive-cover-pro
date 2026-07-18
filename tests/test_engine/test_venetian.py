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

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_for_position_matches_calculate_dual(self, mock_datetime):
        """tilt_for_position returns the same tilt calculate_dual would emit."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = _make_venetian(sol_azi=180.0, sol_elev=45.0)

        dual = calc.calculate_dual()
        # Position is decided upstream; tilt comes from sun geometry alone, so
        # passing any valid position must yield the same tilt as calculate_dual.
        for resolved_position in (0, 25, 50, dual.position, 100):
            assert calc.tilt_for_position(resolved_position) == dual.tilt


class TestVenetianTiltSafetyMargin:
    """Configurable tilt safety margin composes through the dual-axis engine (#783)."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_safety_margin_delegates_to_tilt_engine(self, mock_datetime):
        """Dual-path tilt equals the standalone tilt engine with the same margin."""
        from custom_components.adaptive_cover_pro.calculation import AdaptiveTiltCover

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        logger = _make_logger()
        sun_data = _make_sun_data()
        config = make_cover_config(win_azi=180)
        vert_config = make_vertical_config()
        # Extreme geometry (low elev, high gamma) so the margin actually bites.
        tilt_config = make_tilt_config(
            slat_distance=0.02, depth=0.03, mode="mode1", safety_margin=0.5
        )
        sol_azi, sol_elev = 255.0, 8.0

        calc = VenetianCoverCalculation(
            config=config,
            vert_config=vert_config,
            tilt_config=tilt_config,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )
        standalone = AdaptiveTiltCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=tilt_config,
        )
        assert calc.calculate_dual().tilt == round(standalone.calculate_percentage())

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_safety_margin_respects_max_tilt_clamp(self, mock_datetime):
        """The margin runs inside the tilt engine; ``max_tilt`` still caps it after."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cap = 80
        sol_azi, sol_elev = 255.0, 8.0
        uncapped = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(
                slat_distance=0.02, depth=0.03, mode="mode1", safety_margin=1.0
            ),
            sun_data=_make_sun_data(),
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=_make_logger(),
        )
        capped = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(
                slat_distance=0.02,
                depth=0.03,
                mode="mode1",
                safety_margin=1.0,
                max_tilt=cap,
            ),
            sun_data=_make_sun_data(),
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=_make_logger(),
        )
        uncapped_tilt = uncapped.calculate_dual().tilt
        assert (
            uncapped_tilt > cap
        ), f"test setup: margin-adjusted tilt {uncapped_tilt} must exceed cap {cap}"
        assert capped.calculate_dual().tilt == cap


class TestMaxTiltCap:
    """Tests for max_tilt configuration cap on slat angle."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_compute_tilt_respects_max_tilt_cap(self, mock_datetime):
        """When natural tilt exceeds max_tilt, calculate_dual returns max_tilt."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cap = 30
        uncapped_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=100),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        capped_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=cap),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        uncapped_tilt = uncapped_calc.calculate_dual().tilt
        assert (
            uncapped_tilt > cap
        ), f"Test setup: natural tilt {uncapped_tilt} must exceed cap {cap}"
        assert capped_calc.calculate_dual().tilt == cap

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_compute_tilt_passthrough_when_below_cap(self, mock_datetime):
        """When natural tilt is below max_tilt, the cap has no effect."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        uncapped_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=100),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=30.0,
            logger=_make_logger(),
        )
        high_cap_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=90),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=30.0,
            logger=_make_logger(),
        )
        uncapped_tilt = uncapped_calc.calculate_dual().tilt
        assert (
            uncapped_tilt < 90
        ), f"Test setup: natural tilt {uncapped_tilt} must be below cap 90"
        assert high_cap_calc.calculate_dual().tilt == uncapped_tilt

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_max_tilt_default_100_is_no_op(self, mock_datetime):
        """Default max_tilt=100 produces identical results to before the cap existed."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc_default = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc_explicit = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=100),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        assert calc_default.calculate_dual().tilt == calc_explicit.calculate_dual().tilt

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_for_position_uses_capped_value(self, mock_datetime):
        """tilt_for_position also respects max_tilt — both paths share _compute_tilt."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cap = 30
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=cap),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        tilt_via_position = calc.tilt_for_position(50)
        tilt_via_dual = calc.calculate_dual().tilt
        assert tilt_via_position <= cap
        assert tilt_via_position == tilt_via_dual


class TestMinTiltFloor:
    """Tests for min_tilt configuration floor on slat angle (issue #33)."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_compute_tilt_respects_min_tilt_floor(self, mock_datetime):
        """When natural tilt is below min_tilt, calculate_dual returns min_tilt."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        floor = 40
        unfloored_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=0),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=30.0,
            logger=_make_logger(),
        )
        floored_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=floor),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=30.0,
            logger=_make_logger(),
        )
        unfloored_tilt = unfloored_calc.calculate_dual().tilt
        assert (
            unfloored_tilt < floor
        ), f"Test setup: natural tilt {unfloored_tilt} must be below floor {floor}"
        assert floored_calc.calculate_dual().tilt == floor

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_compute_tilt_passthrough_when_above_floor(self, mock_datetime):
        """When natural tilt is above min_tilt, the floor has no effect."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        unfloored_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=0),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        low_floor_calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=10),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        unfloored_tilt = unfloored_calc.calculate_dual().tilt
        assert (
            unfloored_tilt > 10
        ), f"Test setup: natural tilt {unfloored_tilt} must be above floor 10"
        assert low_floor_calc.calculate_dual().tilt == unfloored_tilt

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_min_tilt_default_zero_is_no_op(self, mock_datetime):
        """Default min_tilt=0 produces identical results to before the floor existed."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc_default = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc_explicit = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=0),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        assert calc_default.calculate_dual().tilt == calc_explicit.calculate_dual().tilt

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_for_position_uses_floored_value(self, mock_datetime):
        """tilt_for_position also respects min_tilt — both paths share _compute_tilt."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        floor = 40
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=floor),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=30.0,
            logger=_make_logger(),
        )
        tilt_via_position = calc.tilt_for_position(50)
        tilt_via_dual = calc.calculate_dual().tilt
        assert tilt_via_position >= floor
        assert tilt_via_position == tilt_via_dual

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_min_tilt_applies_to_nan_fallback(self, mock_datetime):
        """When tilt geometry yields NaN, the floor still applies.

        Regression guard for the NaN return path: ``_clamp_tilt`` must be
        applied in both branches of ``_compute_tilt``, otherwise a NaN-falling
        cover with ``min_tilt=15`` would return 0 and violate the user's floor.
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        floor = 15
        # Patch the inner tilt sub-calc to return NaN, forcing the NaN branch
        # without depending on a specific geometric configuration.
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=floor),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc._tilt.calculate_percentage = Mock(return_value=math.nan)
        assert calc.calculate_dual().tilt == floor


class TestClampTiltDelegation:
    """Characterization: engine tilt clamp delegates to the shared primitive (#503).

    The engine path is a sun-tracking path (``sun_valid=True``), so the clamp
    always applies regardless of the ``*_sun_only`` toggles — preserving the
    original unconditional ``max(min_tilt, min(value, max_tilt))`` behavior.
    """

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_max_tilt_60_clamps_high_geometry_tilt(self, mock_datetime):
        """Geometry that yields tilt 80 is clamped to max_tilt=60."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=60),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=80.0,
            logger=_make_logger(),
        )
        calc._tilt.calculate_percentage = Mock(return_value=80.0)
        assert calc.calculate_dual().tilt == 60

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_nan_fallback_floored_by_min_tilt(self, mock_datetime):
        """NaN geometry falls back to 0, then min_tilt=20 floors it to 20."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(min_tilt=20),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc._tilt.calculate_percentage = Mock(return_value=math.nan)
        assert calc.calculate_dual().tilt == 20


class TestProportionalTiltTransform:
    """Proportional tilt output transform (issue #957).

    Feeds a deterministic raw tilt through ``calculate_dual`` by mocking the
    inner ``calculate_percentage``, so the assertions isolate the transform
    applied at the ``_clamp_tilt`` seam. ``clamp`` (default) must stay identical
    to today's flat clamp; ``proportional`` linearly remaps the full 0–100%
    demand onto ``[min_tilt, max_tilt]``.
    """

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_proportional_remaps_50_to_20_on_band_0_40(self, mock_datetime):
        """Reporter's worked example: raw 50 on band [0,40] → 20 (clamp gives 40)."""
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        proportional = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(
                max_tilt=40, tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL
            ),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        clamp = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(max_tilt=40),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        proportional._tilt.calculate_percentage = Mock(return_value=50.0)
        clamp._tilt.calculate_percentage = Mock(return_value=50.0)
        assert proportional.calculate_dual().tilt == 20
        assert clamp.calculate_dual().tilt == 40

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_proportional_identity_on_default_band(self, mock_datetime):
        """Proportional on the default band [0,100] equals the clamp result (no-op)."""
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        for raw in (0.0, 25.0, 50.0, 75.0, 100.0):
            proportional = VenetianCoverCalculation(
                config=make_cover_config(win_azi=180),
                vert_config=make_vertical_config(),
                tilt_config=make_tilt_config(
                    tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL
                ),
                sun_data=_make_sun_data(),
                sol_azi=180.0,
                sol_elev=45.0,
                logger=_make_logger(),
            )
            proportional._tilt.calculate_percentage = Mock(return_value=raw)
            assert proportional.calculate_dual().tilt == round(raw)

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_proportional_monotonic_sweep_on_band_0_40(self, mock_datetime):
        """Proportional remap is monotonic non-decreasing across a 0–100 sweep."""
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        results = []
        for raw in range(0, 101, 5):
            calc = VenetianCoverCalculation(
                config=make_cover_config(win_azi=180),
                vert_config=make_vertical_config(),
                tilt_config=make_tilt_config(
                    max_tilt=40, tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL
                ),
                sun_data=_make_sun_data(),
                sol_azi=180.0,
                sol_elev=45.0,
                logger=_make_logger(),
            )
            calc._tilt.calculate_percentage = Mock(return_value=float(raw))
            results.append(calc.calculate_dual().tilt)
        assert all(b >= a for a, b in zip(results, results[1:]))
        assert results[0] == 0
        assert results[-1] == 40

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_proportional_nan_fallback_floors_to_min_tilt(self, mock_datetime):
        """NaN geometry falls back to raw 0, proportional band [10,40] → floor 10."""
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(
                min_tilt=10,
                max_tilt=40,
                tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
            ),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc._tilt.calculate_percentage = Mock(return_value=math.nan)
        assert calc.calculate_dual().tilt == 10

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_explicit_clamp_still_flat_clamps(self, mock_datetime):
        """Explicit clamp transform keeps today's flat cap: raw 80 on [0,40] → 40."""
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_CLAMP,
        )

        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        calc = VenetianCoverCalculation(
            config=make_cover_config(win_azi=180),
            vert_config=make_vertical_config(),
            tilt_config=make_tilt_config(
                max_tilt=40, tilt_transform=VENETIAN_TILT_TRANSFORM_CLAMP
            ),
            sun_data=_make_sun_data(),
            sol_azi=180.0,
            sol_elev=45.0,
            logger=_make_logger(),
        )
        calc._tilt.calculate_percentage = Mock(return_value=80.0)
        assert calc.calculate_dual().tilt == 40
