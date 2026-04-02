"""Tests for ClimateHandler — full climate strategy."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import ClimateHandler
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot


def _make_readings(
    *,
    outside_temperature=None,
    inside_temperature=25.0,
    is_presence=True,
    is_sunny=True,
    lux_below_threshold=False,
    irradiance_below_threshold=False,
    cloud_coverage_above_threshold=False,
) -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=outside_temperature,
        inside_temperature=inside_temperature,
        is_presence=is_presence,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
    )


def _make_options(
    *,
    temp_low=18.0,
    temp_high=26.0,
    temp_switch=False,
    transparent_blind=False,
    temp_summer_outside=None,
) -> ClimateOptions:
    return ClimateOptions(
        temp_low=temp_low,
        temp_high=temp_high,
        temp_switch=temp_switch,
        transparent_blind=transparent_blind,
        temp_summer_outside=temp_summer_outside,
        cloud_suppression_enabled=False,
    )


def _make_blind_cover(
    direct_sun_valid=True,
):
    """Build a mock cover for climate tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.default = 0.0
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


class TestClimateHandlerGating:
    """Test that ClimateHandler respects enable/disable conditions."""

    handler = ClimateHandler()

    def test_returns_none_when_climate_disabled(self) -> None:
        snap = make_snapshot(climate_mode_enabled=False)
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_readings(self) -> None:
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=None,
            climate_options=_make_options(),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_options(self) -> None:
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_make_readings(),
            climate_options=None,
        )
        assert self.handler.evaluate(snap) is None


class TestClimateHandlerSummerStrategy:
    """Summer cooling: temperature above high threshold."""

    handler = ClimateHandler()

    def test_summer_uses_summer_control_method(self) -> None:
        """High inside temperature → SUMMER control method."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER

    def test_summer_sets_climate_state_on_result(self) -> None:
        """climate_state is populated on PipelineResult when ClimateHandler fires."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_state is not None
        assert isinstance(result.climate_state, int)


class TestClimateHandlerWinterStrategy:
    """Winter heating: temperature below low threshold."""

    handler = ClimateHandler()

    def test_winter_uses_winter_control_method(self) -> None:
        """Low inside temperature → WINTER control method."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER


class TestClimateHandlerGlareControl:
    """Intermediate: glare control (neither summer nor winter)."""

    handler = ClimateHandler()

    def test_intermediate_uses_solar_method(self) -> None:
        """Comfortable temperature → SOLAR method (glare control)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SOLAR


class TestClimateHandlerMetadata:
    handler = ClimateHandler()

    def test_result_includes_climate_strategy(self) -> None:
        """PipelineResult.climate_strategy is populated."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_strategy is not None

    def test_priority_is_50(self) -> None:
        assert ClimateHandler.priority == 50

    def test_name(self) -> None:
        assert ClimateHandler.name == "climate"
