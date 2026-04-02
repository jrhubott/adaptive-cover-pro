"""Tests for CloudSuppressionHandler."""

from __future__ import annotations

import pytest

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
    )


class TestCloudSuppressionHandler:
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
            climate_readings=_make_readings(is_sunny=True, irradiance_below_threshold=True),
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
        assert CloudSuppressionHandler.priority == 60

    def test_name(self) -> None:
        assert CloudSuppressionHandler.name == "cloud_suppression"
