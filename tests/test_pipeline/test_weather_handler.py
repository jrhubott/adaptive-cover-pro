"""Tests for WeatherOverrideHandler."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod, ReasonCode
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.reason_i18n import Reason, render_en
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
        """describe_skip renders a non-empty English string."""
        snap = make_snapshot()
        reason = render_en(self.handler.describe_skip(snap))
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_reason_payload_code_and_params(self) -> None:
        """Winning result carries a weather.active payload; prose byte-identical."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=10,
            weather_bypass_auto_control=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.WEATHER_ACTIVE
        assert result.reason_payload.params["position"] == 10
        assert result.reason_payload.params["bypass_note"] == ""
        assert result.reason == "weather override active — position 10%"

    def test_reason_payload_bypass_note(self) -> None:
        """bypass_auto_control folds a bypass-note fragment into the payload."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=10,
            weather_bypass_auto_control=True,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.reason_payload is not None
        bypass_note = result.reason_payload.params["bypass_note"]
        assert isinstance(bypass_note, Reason)
        assert bypass_note.code == ReasonCode.FRAGMENT_BYPASS_NOTE
        assert result.reason == (
            "weather override active — position 10% [bypasses automatic control]"
        )

    def test_describe_skip_payload_code(self) -> None:
        """describe_skip returns a skip.weather_not_active payload."""
        snap = make_snapshot()
        payload = self.handler.describe_skip(snap)
        assert payload.code == ReasonCode.SKIP_WEATHER_NOT_ACTIVE

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
    """WeatherOverrideHandler defers in min_mode; the registry composes the floor.

    See ``tests/test_pipeline/test_floor_composition.py`` for the end-to-end
    floor-clamp composition tests.
    """

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

    def test_min_mode_on_defers(self) -> None:
        """With min_mode on, evaluate() returns None — the registry composes
        the floor as a post-decision clamp.
        """
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=30,
            weather_override_min_mode=True,
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
        )
        result = self.handler.evaluate(snap)
        assert result is None
