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

    def test_wins_outside_the_clock_window_by_default(self) -> None:
        """Weather is a SAFETY override and acts at any hour unless scoped.

        Characterization of the shipped contract (#1308): with no opt-out the
        handler ignores the clock entirely and stamps ``is_safety``, which is
        the licence ``coordinator._dispatch`` reads to let a storm retraction
        reach hardware at 03:00. Nothing pinned this before, so the behaviour
        every awning install depends on was undocumented by test.
        """
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=90,
            clock_window_open=False,
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method is ControlMethod.WEATHER
        assert result.position == 90
        assert result.is_safety is True


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


class TestWeatherOverrideWindowScope:
    """``weather_outside_window`` scopes the override to the CLOCK window (#1308).

    Weather is the one override with no window gate, and its ``is_safety`` flag
    is the outside-window dispatch licence. A user running the override as a
    *comfort* rule (rain → raise an interior blind) has no way to stop it
    firing at 03:00 short of turning the whole feature off. The opt-out gives
    them one; the default keeps every storm-protection install byte-identical.

    The gate reads ``clock_window_open``, NEVER ``in_time_window`` — see
    ``test_still_acts_when_the_gate_is_dark_but_the_clock_is_open``.
    """

    handler = WeatherOverrideHandler()

    def test_stands_down_outside_the_clock_window_when_scoped(self) -> None:
        """Scoped + clock closed → the handler declines and DEFAULT falls through."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=90,
            clock_window_open=False,
            in_time_window=False,
            weather_outside_window=False,
        )
        assert self.handler.evaluate(snap) is None

    def test_still_acts_when_the_gate_is_dark_but_the_clock_is_open(self) -> None:
        """A dark daytime gate mid-clock is NOT "outside the window" (#656/#632).

        ``in_time_window`` folds the sun-tracking daytime gate into the clock
        window, so it reads False on a gate-dark winter morning at 10:00 while
        the user's 06:00–18:30 clock is wide open. Gating weather on that field
        would silently strip storm protection for every gate-configured install
        — a much larger behaviour change than the one this option promises.
        Every seat that decides "outside the window" speaks ``clock_window_open``
        and this one must too.
        """
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=90,
            clock_window_open=True,
            in_time_window=False,
            weather_outside_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.is_safety is True

    def test_describe_skip_reads_outside_time_window_when_scoped(self) -> None:
        """The trace names the gate that actually stopped it, like solar does."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_position=90,
            clock_window_open=False,
            in_time_window=False,
            weather_outside_window=False,
        )
        assert self.handler.describe_skip(snap).code is ReasonCode.SKIP_OUTSIDE_WINDOW

    def test_describe_skip_still_reads_not_active_when_conditions_are_clear(
        self,
    ) -> None:
        """Clear skies out here still read "not active", not "outside window".

        ``describe_skip`` mirrors ``evaluate``'s gate order, and the order is
        load-bearing: reporting a window scope to a user whose weather sensors
        are simply quiet sends them looking for the wrong setting. Same
        principle as ``solar.describe_skip`` splitting gate-closed from
        tracking-off.
        """
        snap = make_snapshot(
            weather_override_active=False,
            clock_window_open=False,
            in_time_window=False,
            weather_outside_window=False,
        )
        assert (
            self.handler.describe_skip(snap).code is ReasonCode.SKIP_WEATHER_NOT_ACTIVE
        )

    def test_describe_skip_reads_the_scope_in_min_mode_too(self) -> None:
        """Min mode + scoped + clock closed reports the scope, and that is right.

        Both later gates hold here, so the reason is a choice rather than a
        deduction. The scope is the truthful one: the min-mode floor is the
        *other* seat of this same option, and out here ``_window_eligible``
        drops it from the same ``weather_outside_window`` field — so nothing
        weather-shaped acts, and the window scope is what stopped all of it.
        Nor is a distinction lost: min-mode deferral has never had a reason
        code of its own and reads ``SKIP_WEATHER_NOT_ACTIVE`` with the clock
        open, exactly as it did before #1308. What the order buys is that an
        ACTIVE override stops reporting itself as inactive.
        """
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_min_mode=True,
            weather_override_position=60,
            clock_window_open=False,
            in_time_window=False,
            weather_outside_window=False,
        )
        assert self.handler.evaluate(snap) is None
        assert self.handler.describe_skip(snap).code is ReasonCode.SKIP_OUTSIDE_WINDOW

    def test_describe_skip_in_min_mode_is_unchanged_with_the_clock_open(self) -> None:
        """The pre-#1308 min-mode trace is untouched where the scope cannot apply."""
        snap = make_snapshot(
            weather_override_active=True,
            weather_override_min_mode=True,
            weather_override_position=60,
            clock_window_open=True,
            in_time_window=True,
            weather_outside_window=False,
        )
        assert self.handler.evaluate(snap) is None
        assert (
            self.handler.describe_skip(snap).code is ReasonCode.SKIP_WEATHER_NOT_ACTIVE
        )
