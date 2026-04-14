"""Tests for CloudSuppressionHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot


def _make_readings(
    *,
    is_sunny: bool = True,
    lux_below_threshold: bool = False,
    irradiance_below_threshold: bool = False,
    cloud_coverage_above_threshold: bool = False,
) -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
    )


def _make_options(enabled: bool = True) -> ClimateOptions:
    return ClimateOptions(
        temp_low=None,
        temp_high=None,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=enabled,
        winter_close_insulation=False,
    )


class TestCloudSuppressionHandler:
    """Test CloudSuppressionHandler."""

    handler = CloudSuppressionHandler()

    def test_returns_none_when_feature_disabled(self) -> None:
        """Return None when cloud_suppression_enabled is False."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=False),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_readings(self) -> None:
        """Return None when no climate readings are available."""
        snap = make_snapshot(
            climate_readings=None,
            climate_options=_make_options(enabled=True),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_options(self) -> None:
        """Return None when no climate options are configured."""
        snap = make_snapshot(
            climate_readings=_make_readings(),
            climate_options=None,
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_sunny_and_no_thresholds(self) -> None:
        """Return None when sun is present and all thresholds are fine."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=True),
            climate_options=_make_options(enabled=True),
        )
        assert self.handler.evaluate(snap) is None

    def test_activates_when_not_sunny(self) -> None:
        """Activate when weather state is not sunny."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 30

    def test_activates_when_lux_below_threshold(self) -> None:
        """Activate when lux is below the configured threshold."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_activates_when_irradiance_below_threshold(self) -> None:
        """Activate when solar irradiance is below threshold."""
        snap = make_snapshot(
            climate_readings=_make_readings(
                is_sunny=True, irradiance_below_threshold=True
            ),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_activates_when_cloud_coverage_above_threshold(self) -> None:
        """Activate when cloud coverage sensor exceeds threshold."""
        snap = make_snapshot(
            climate_readings=_make_readings(cloud_coverage_above_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_returns_default_position(self) -> None:
        """Return snapshot.default_position when suppressing."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=55,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 55

    def test_priority_is_60(self) -> None:
        """CloudSuppressionHandler has priority 60."""
        assert CloudSuppressionHandler.priority == 60

    def test_name(self) -> None:
        """CloudSuppressionHandler name is 'cloud_suppression'."""
        assert CloudSuppressionHandler.name == "cloud_suppression"

    def test_sun_only_max_not_applied_to_default_regression_105(self) -> None:
        """Regression #105: sun-only max limit must NOT clamp the default position.

        User scenario: default=50, max_pos=26 (sun-only), sun geometrically in FOV
        but cloudy. Cloud suppression fires and should return 50, not 26.
        """
        cover = MagicMock()
        cover.direct_sun_valid = True  # sun is geometrically in FOV
        cover.valid = True
        cover.calculate_percentage = MagicMock(return_value=15.0)
        cover.logger = MagicMock()
        config = MagicMock()
        config.min_pos = None
        config.max_pos = 26
        config.min_pos_sun_only = False
        config.max_pos_sun_only = True  # "during sun tracking only"
        cover.config = config

        snap = make_snapshot(
            cover=cover,
            climate_readings=_make_readings(cloud_coverage_above_threshold=True),
            climate_options=_make_options(enabled=True),
            default_position=50,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 50, (
            f"Expected default position 50 but got {result.position}. "
            "Sun-only max limit must not clamp cloud suppression output."
        )


# ---------------------------------------------------------------------------
# Issue #145 — CloudSuppressionHandler must respect in_time_window
# ---------------------------------------------------------------------------


class TestCloudHandlerTimeWindow:
    """CloudSuppressionHandler must return None outside the time window.

    Before the fix, CloudSuppressionHandler ignored ``snapshot.in_time_window``.
    """

    handler = CloudSuppressionHandler()

    def test_returns_none_outside_time_window(self) -> None:
        """Cloud suppression must be inactive outside the time window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None, (
            "CloudSuppressionHandler should return None outside the time window "
            f"but returned {result}"
        )

    def test_returns_result_inside_time_window(self) -> None:
        """Cloud suppression should activate when inside the time window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
            in_time_window=True,
        )
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_describe_skip_outside_window(self) -> None:
        """describe_skip() should mention 'time window' when outside window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            in_time_window=False,
        )
        reason = self.handler.describe_skip(snap)
        assert "time window" in reason.lower(), (
            f"Expected 'time window' in describe_skip reason but got: {reason!r}"
        )

    def test_lux_threshold_outside_window_returns_none(self) -> None:
        """Lux-based suppression must also be gated by time window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None
