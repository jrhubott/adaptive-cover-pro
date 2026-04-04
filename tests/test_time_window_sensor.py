"""Tests for position/state behavior with the start_time/end_time operational window.

With the new architecture the pipeline ALWAYS runs regardless of the time window.
The time window only controls whether commands are sent to covers
(CoverCommandService.apply_position() skips when outside the window unless forced).

Key behaviors verified here:
- Outside time window: pipeline still runs; state reflects the computed pipeline result
- Sunset-aware default: effective default is sunset_pos when in astronomical sunset window
- Force override bypasses the time window gate (bypass_auto_control=True)
- Motion timeout uses snapshot.default_position (sunset-aware)
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.helpers import compute_effective_default
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.force_override import (
    ForceOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sun_data_with_times(
    *,
    sunset_hour: int = 20,
    sunrise_hour: int = 6,
) -> MagicMock:
    """Return a mock SunData with real datetime sunset/sunrise returns."""
    today = dt.date.today()
    sunset_dt = dt.datetime(today.year, today.month, today.day, sunset_hour, 0, 0)
    sunrise_dt = dt.datetime(today.year, today.month, today.day, sunrise_hour, 0, 0)
    sun = MagicMock()
    sun.sunset.return_value = sunset_dt
    sun.sunrise.return_value = sunrise_dt
    return sun


def _make_cover(
    *,
    direct_sun_valid: bool = False,
    sunset_valid: bool = False,
    calculate_percentage_return: float = 50.0,
    h_def: int = 50,
) -> MagicMock:
    """Build a mock cover for time-window tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.sunset_valid = sunset_valid
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.sun_data = _make_sun_data_with_times()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    config.h_def = h_def
    cover.config = config
    return cover


def _make_snapshot(
    cover: MagicMock,
    *,
    default_position: int = 0,
    is_sunset_active: bool = False,
    configured_default: int = 0,
    configured_sunset_pos: int | None = None,
    motion_timeout_active: bool = False,
    force_override_sensors: dict | None = None,
    force_override_position: int = 0,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        cover=cover,
        config=cover.config,
        cover_type="cover_blind",
        default_position=default_position,
        is_sunset_active=is_sunset_active,
        configured_default=configured_default,
        configured_sunset_pos=configured_sunset_pos,
        climate_readings=None,
        climate_mode_enabled=False,
        climate_options=None,
        force_override_sensors=force_override_sensors or {},
        force_override_position=force_override_position,
        manual_override_active=False,
        motion_timeout_active=motion_timeout_active,
        weather_override_active=False,
        weather_override_position=0,
        weather_bypass_auto_control=True,
        glare_zones=None,
        active_zone_names=frozenset(),
    )


# ---------------------------------------------------------------------------
# Tests: effective default position
# ---------------------------------------------------------------------------


class TestEffectiveDefaultInPipeline:
    """Pipeline uses compute_effective_default() value as snapshot.default_position."""

    def test_daytime_snapshot_default_is_h_def(self):
        """During daytime, default_position == h_def."""
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        sun = _make_sun_data_with_times(sunset_hour=20, sunrise_hour=6)

        with patch(
            "custom_components.adaptive_cover_pro.helpers.dt.datetime",
            **{"now.return_value": midday.replace(tzinfo=UTC)},
        ):
            effective, active = compute_effective_default(
                h_def=50, sunset_pos=0, sun_data=sun, sunset_off=0, sunrise_off=0
            )

        assert effective == 50
        assert active is False

    def test_after_sunset_snapshot_default_is_sunset_pos(self):
        """After sunset+offset, default_position == sunset_pos."""
        today = dt.date.today()
        night = dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        sun = _make_sun_data_with_times(sunset_hour=20, sunrise_hour=6)

        with patch(
            "custom_components.adaptive_cover_pro.helpers.dt.datetime",
            **{"now.return_value": night.replace(tzinfo=UTC)},
        ):
            effective, active = compute_effective_default(
                h_def=50, sunset_pos=0, sun_data=sun, sunset_off=0, sunrise_off=0
            )

        assert effective == 0
        assert active is True

    def test_no_sunset_pos_always_uses_h_def(self):
        """No sunset_pos configured → always h_def, never sunset active."""
        today = dt.date.today()
        night = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        sun = _make_sun_data_with_times(sunset_hour=20)

        with patch(
            "custom_components.adaptive_cover_pro.helpers.dt.datetime",
            **{"now.return_value": night.replace(tzinfo=UTC)},
        ):
            effective, active = compute_effective_default(
                h_def=50, sunset_pos=None, sun_data=sun, sunset_off=0, sunrise_off=0
            )

        assert effective == 50
        assert active is False


# ---------------------------------------------------------------------------
# Tests: pipeline always runs (time window doesn't bypass pipeline)
# ---------------------------------------------------------------------------


class TestPipelineAlwaysRuns:
    """Pipeline runs regardless of check_adaptive_time."""

    def test_default_handler_fires_when_sun_not_in_fov(self):
        """DefaultHandler fires when sun not in FOV, returning snapshot.default_position."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(cover, default_position=0)

        result = registry.evaluate(snapshot)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 0

    def test_default_handler_returns_h_def_during_daytime(self):
        """During daytime with no sun in FOV, default_position == h_def."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(cover, default_position=50, configured_default=50)

        result = registry.evaluate(snapshot)

        assert result.position == 50
        assert result.is_sunset_active is False

    def test_default_handler_returns_sunset_pos_at_night(self):
        """At night, default_position == sunset_pos; handler reason reflects it."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=10,
            is_sunset_active=True,
            configured_sunset_pos=10,
        )

        result = registry.evaluate(snapshot)

        assert result.position == 10
        assert result.is_sunset_active is True
        assert "sunset position" in result.reason

    def test_solar_handler_fires_when_sun_in_fov(self):
        """SolarHandler fires when direct_sun_valid=True."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        cover = _make_cover(direct_sun_valid=True, calculate_percentage_return=75.0)
        snapshot = _make_snapshot(cover, default_position=0)

        result = registry.evaluate(snapshot)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 75


# ---------------------------------------------------------------------------
# Tests: CONF_RETURN_SUNSET behavior (outside time window)
# ---------------------------------------------------------------------------


class TestOutsideTimeWindowBehavior:
    """Pipeline result outside time window — commands are gated, state is reported."""

    def test_pipeline_result_still_set_outside_window(self):
        """The pipeline result reflects what would be sent, even if commands are gated."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        # Simulate outside-window: sun not in FOV, effective default = sunset_pos
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=0,
            is_sunset_active=True,
            configured_sunset_pos=0,
            configured_default=50,
        )

        result = registry.evaluate(snapshot)

        # Pipeline ran and returned 0 (sunset_pos)
        assert result.position == 0
        assert result.is_sunset_active is True

    def test_sunset_pos_zero_not_treated_as_none(self):
        """sunset_pos=0 is a valid position, not a None."""
        registry = PipelineRegistry([DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=0,
            is_sunset_active=True,
            configured_sunset_pos=0,
        )

        result = registry.evaluate(snapshot)
        assert result.position == 0

    def test_no_sunset_pos_falls_back_to_default_height(self):
        """No sunset_pos → pipeline uses h_def."""
        registry = PipelineRegistry([DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=50,
            is_sunset_active=False,
            configured_default=50,
            configured_sunset_pos=None,
        )

        result = registry.evaluate(snapshot)
        assert result.position == 50

    def test_no_sunset_pos_no_default_falls_back_to_zero(self):
        """No sunset_pos and h_def=0 → returns 0."""
        registry = PipelineRegistry([DefaultHandler()])
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=0,
            is_sunset_active=False,
            configured_default=0,
        )

        result = registry.evaluate(snapshot)
        assert result.position == 0


# ---------------------------------------------------------------------------
# Tests: Force override works outside time window (bypass_auto_control)
# ---------------------------------------------------------------------------


class TestForceOverrideOutsideWindow:
    """Force override bypasses the time window gate via bypass_auto_control=True."""

    def test_force_override_matches_and_sets_bypass(self):
        """ForceOverrideHandler fires and sets bypass_auto_control=True."""
        registry = PipelineRegistry(
            [ForceOverrideHandler(), SolarHandler(), DefaultHandler()]
        )
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=50,
            force_override_sensors={"binary_sensor.test": True},
            force_override_position=75,
        )

        result = registry.evaluate(snapshot)

        assert result.bypass_auto_control is True
        assert result.position == 75
        assert result.control_method == ControlMethod.FORCE


# ---------------------------------------------------------------------------
# Tests: Motion timeout uses snapshot.default_position
# ---------------------------------------------------------------------------


class TestMotionTimeoutDefaultPosition:
    """MotionTimeoutHandler uses snapshot.default_position (sunset-aware)."""

    def test_motion_timeout_uses_default_position_at_night(self):
        """At night, motion timeout returns sunset_pos via default_position."""
        registry = PipelineRegistry(
            [MotionTimeoutHandler(), SolarHandler(), DefaultHandler()]
        )
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=10,
            is_sunset_active=True,
            configured_sunset_pos=10,
            motion_timeout_active=True,
        )

        result = registry.evaluate(snapshot)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 10
        assert "sunset position" in result.reason

    def test_motion_timeout_uses_default_position_daytime(self):
        """During daytime, motion timeout returns h_def via default_position."""
        registry = PipelineRegistry(
            [MotionTimeoutHandler(), SolarHandler(), DefaultHandler()]
        )
        cover = _make_cover(direct_sun_valid=False)
        snapshot = _make_snapshot(
            cover,
            default_position=50,
            is_sunset_active=False,
            configured_default=50,
            motion_timeout_active=True,
        )

        result = registry.evaluate(snapshot)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 50
        assert "default position" in result.reason
