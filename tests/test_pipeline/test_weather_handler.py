"""Tests for WeatherOverrideHandler."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from tests.test_pipeline.conftest import make_snapshot


class TestWeatherOverrideHandler:
    """Tests for WeatherOverrideHandler."""

    handler = WeatherOverrideHandler()

    def test_returns_none_when_not_active(self) -> None:
        """Handler returns None when weather override is not active."""
        snap = make_snapshot(weather_override_active=False)
        assert self.handler.evaluate(snap) is None

    def test_returns_result_when_active(self) -> None:
        """Handler returns PipelineResult when weather override is active."""
        snap = make_snapshot(weather_override_active=True, weather_override_position=0)
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_uses_configured_position(self) -> None:
        """Handler uses the configured weather_override_position."""
        snap = make_snapshot(weather_override_active=True, weather_override_position=25)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 25

    def test_default_position_is_zero(self) -> None:
        """Default override position is 0 (fully retracted)."""
        snap = make_snapshot(weather_override_active=True, weather_override_position=0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 0

    def test_control_method_is_weather(self) -> None:
        """Result uses ControlMethod.WEATHER."""
        snap = make_snapshot(weather_override_active=True)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WEATHER

    def test_reason_includes_position(self) -> None:
        """Result reason mentions the override position."""
        snap = make_snapshot(weather_override_active=True, weather_override_position=10)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "10" in result.reason

    def test_priority_is_90(self) -> None:
        """WeatherOverrideHandler has priority 90."""
        assert WeatherOverrideHandler.priority == 90

    def test_name_is_weather(self) -> None:
        """WeatherOverrideHandler name is 'weather'."""
        assert WeatherOverrideHandler.name == "weather"

    def test_describe_skip_meaningful(self) -> None:
        """describe_skip returns a non-empty string."""
        snap = make_snapshot()
        reason = self.handler.describe_skip(snap)
        assert isinstance(reason, str)
        assert len(reason) > 0

    @pytest.mark.parametrize("position", [0, 10, 50, 75, 100])
    def test_various_positions(self, position: int) -> None:
        """Handler respects any configured override position."""
        snap = make_snapshot(
            weather_override_active=True, weather_override_position=position
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == position


class TestWeatherOverrideHandlerMinMode:
    """Tests for WeatherOverrideHandler minimum position mode."""

    handler = WeatherOverrideHandler()

    def test_min_mode_off_uses_exact_position(self) -> None:
        """With min_mode off, position is always the configured value (default behavior)."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=False,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_higher_uses_calculated(self) -> None:
        """With min_mode on, if calculated position > floor, use calculated."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 50

    def test_min_mode_on_calculated_lower_uses_floor(self) -> None:
        """With min_mode on, if calculated position < floor, use the floor."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=10.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_calculated_equal_uses_floor(self) -> None:
        """With min_mode on, if calculated equals floor, position equals floor."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=30.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 30

    def test_min_mode_on_reason_mentions_minimum_mode(self) -> None:
        """With min_mode on, reason string mentions minimum mode."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "minimum mode" in result.reason

    def test_min_mode_control_method_still_weather(self) -> None:
        """ControlMethod remains WEATHER regardless of min_mode."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=70.0,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WEATHER
