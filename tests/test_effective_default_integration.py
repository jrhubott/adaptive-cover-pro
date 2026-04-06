"""Integration tests for effective default position flowing through the pipeline.

Tests the full chain: compute_effective_default → PipelineSnapshot.default_position
→ pipeline handlers (DefaultHandler, MotionTimeoutHandler) use it correctly.

Covers:
- Step 11: Daytime uses h_def
- Step 12: After sunset uses sunset_pos
- Step 13: Sunset offset delays transition
- Step 14: Sunset position pipeline propagation contract
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, patch

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.helpers import compute_effective_default
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry

from tests.test_pipeline.conftest import make_snapshot


# ---------------------------------------------------------------------------
# Helpers (mirroring test_effective_default.py's pattern)
# ---------------------------------------------------------------------------


def _make_sun_data(*, sunset_hour: int = 20, sunrise_hour: int = 6) -> MagicMock:
    """Return a mock SunData with controllable sunset/sunrise times (naive)."""
    today = dt.date.today()
    sunset_dt = dt.datetime(today.year, today.month, today.day, sunset_hour, 0, 0)
    sunrise_dt = dt.datetime(today.year, today.month, today.day, sunrise_hour, 0, 0)
    sun = MagicMock()
    sun.sunset.return_value = sunset_dt
    sun.sunrise.return_value = sunrise_dt
    return sun


def _freeze_now(naive_dt: dt.datetime):
    """Patch helpers.dt.datetime.now(UTC) to return naive_dt (as UTC-aware)."""
    aware_dt = naive_dt.replace(tzinfo=UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware_dt},
    )


def _today(hour: int, minute: int = 0) -> dt.datetime:
    """Return a naive datetime for today at the given time."""
    today = dt.date.today()
    return dt.datetime(today.year, today.month, today.day, hour, minute, 0)


def _registry(*handlers):
    return PipelineRegistry(list(handlers))


# ---------------------------------------------------------------------------
# Step 11: Daytime uses h_def
# ---------------------------------------------------------------------------


class TestDaytimeUsesHDef:
    """During the day, compute_effective_default returns h_def and the pipeline
    passes it correctly to DefaultHandler / MotionTimeoutHandler."""

    def test_daytime_effective_default_is_h_def(self):
        """At noon (between 6 AM sunrise and 8 PM sunset), h_def is returned."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(12)):
            effective, is_sunset_active = compute_effective_default(
                h_def=40,
                sunset_pos=80,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        assert effective == 40
        assert is_sunset_active is False

    def test_default_handler_uses_h_def_during_day(self):
        """DefaultHandler receives h_def from snapshot.default_position during the day."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(12)):
            effective_default, _ = compute_effective_default(
                h_def=40,
                sunset_pos=80,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        registry = _registry(DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=effective_default,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 40

    def test_motion_timeout_uses_h_def_during_day(self):
        """MotionTimeoutHandler also uses h_def as default_position during the day."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(12)):
            effective_default, _ = compute_effective_default(
                h_def=35,
                sunset_pos=90,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        registry = _registry(MotionTimeoutHandler(), DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=effective_default,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 35


# ---------------------------------------------------------------------------
# Step 12: After sunset uses sunset_pos
# ---------------------------------------------------------------------------


class TestAfterSunsetUsesSunsetPos:
    """After astronomical sunset (no offset), compute_effective_default returns sunset_pos."""

    def test_after_sunset_effective_default_is_sunset_pos(self):
        """At 22:00 (past 20:00 sunset), sunset_pos is returned."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(22)):
            effective, is_sunset_active = compute_effective_default(
                h_def=40,
                sunset_pos=10,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        assert effective == 10
        assert is_sunset_active is True

    def test_after_sunset_default_handler_returns_sunset_pos(self):
        """DefaultHandler returns sunset_pos during the sunset window."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(22)):
            effective_default, _ = compute_effective_default(
                h_def=40,
                sunset_pos=10,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        registry = _registry(DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=effective_default,
            is_sunset_active=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 10

    def test_after_sunset_motion_timeout_returns_sunset_pos(self):
        """MotionTimeoutHandler uses sunset_pos as its default during sunset window."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(22)):
            effective_default, _ = compute_effective_default(
                h_def=40,
                sunset_pos=10,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        registry = _registry(MotionTimeoutHandler(), DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=effective_default,
            is_sunset_active=True,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.MOTION
        assert result.position == 10

    def test_before_sunrise_also_uses_sunset_pos(self):
        """Before sunrise (overnight window) is also treated as sunset-active."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(4)):  # 4 AM < 6 AM sunrise
            effective, is_sunset_active = compute_effective_default(
                h_def=50,
                sunset_pos=5,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        assert effective == 5
        assert is_sunset_active is True

    def test_sunset_pos_none_always_returns_h_def(self):
        """When sunset_pos is None, h_def is returned even after sunset."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        with _freeze_now(_today(22)):
            effective, is_sunset_active = compute_effective_default(
                h_def=50,
                sunset_pos=None,
                sun_data=sun_data,
                sunset_off=0,
                sunrise_off=0,
            )

        assert effective == 50
        assert is_sunset_active is False


# ---------------------------------------------------------------------------
# Step 13: Sunset offset delays transition
# ---------------------------------------------------------------------------


class TestSunsetOffsetDelaysTransition:
    """A positive sunset_off shifts the sunset-window start by that many minutes."""

    def test_before_sunset_plus_offset_returns_h_def(self):
        """30 minutes before sunset+offset (60 min), h_def is still in effect."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        # Sunset 20:00 + 60 min offset → window opens at 21:00
        # At 20:30, window not yet open → h_def
        with _freeze_now(_today(20, 30)):
            effective, is_sunset_active = compute_effective_default(
                h_def=50,
                sunset_pos=5,
                sun_data=sun_data,
                sunset_off=60,
                sunrise_off=0,
            )

        assert effective == 50
        assert is_sunset_active is False

    def test_after_sunset_plus_offset_returns_sunset_pos(self):
        """After sunset+offset, sunset_pos is active."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        # Sunset 20:00 + 60 min offset → window opens at 21:00
        # At 21:30, window is open → sunset_pos
        with _freeze_now(_today(21, 30)):
            effective, is_sunset_active = compute_effective_default(
                h_def=50,
                sunset_pos=5,
                sun_data=sun_data,
                sunset_off=60,
                sunrise_off=0,
            )

        assert effective == 5
        assert is_sunset_active is True

    def test_zero_offset_transition_at_exact_sunset(self):
        """With offset=0, the transition happens exactly at sunset time."""
        sun_data = _make_sun_data(sunset_hour=20, sunrise_hour=6)

        # Just before sunset (19:59) → h_def
        with _freeze_now(_today(19, 59)):
            before_eff, before_active = compute_effective_default(
                h_def=50, sunset_pos=5, sun_data=sun_data, sunset_off=0, sunrise_off=0
            )

        # Just after sunset (20:01) → sunset_pos
        with _freeze_now(_today(20, 1)):
            after_eff, after_active = compute_effective_default(
                h_def=50, sunset_pos=5, sun_data=sun_data, sunset_off=0, sunrise_off=0
            )

        assert before_eff == 50 and before_active is False
        assert after_eff == 5 and after_active is True

    def test_pipeline_propagates_offset_adjusted_default(self):
        """After pipeline receives the offset-adjusted default, it uses it faithfully."""
        # Simulate coordinator passing the computed daytime h_def
        registry = _registry(DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=50,
            is_sunset_active=False,
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 50


# ---------------------------------------------------------------------------
# Step 14: Pipeline propagation contract for sunset_pos
# ---------------------------------------------------------------------------


class TestPipelineDefaultPropagationContract:
    """Whatever compute_effective_default gives, the pipeline honours it.

    This is the core integration invariant: the coordinator feeds the
    effective default into snapshot.default_position, and every handler
    that reads default_position gets the correct sunset-aware value.
    """

    def test_h_def_flows_through_default_handler(self):
        """h_def flows to DefaultHandler position during daytime."""
        registry = _registry(DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=60,
            is_sunset_active=False,
        )
        assert registry.evaluate(snap).position == 60

    def test_sunset_pos_flows_through_default_handler(self):
        """sunset_pos flows to DefaultHandler during sunset window."""
        registry = _registry(DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=10,
            is_sunset_active=True,
        )
        assert registry.evaluate(snap).position == 10

    def test_h_def_flows_through_motion_timeout_handler(self):
        """h_def is used by MotionTimeoutHandler during daytime."""
        registry = _registry(MotionTimeoutHandler(), DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=60,
            is_sunset_active=False,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.MOTION
        assert result.position == 60

    def test_sunset_pos_flows_through_motion_timeout_handler(self):
        """sunset_pos is used by MotionTimeoutHandler during sunset window."""
        registry = _registry(MotionTimeoutHandler(), DefaultHandler())
        snap = make_snapshot(
            direct_sun_valid=False,
            default_position=10,
            is_sunset_active=True,
            motion_timeout_active=True,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.MOTION
        assert result.position == 10

    def test_same_pipeline_two_snapshots_different_defaults(self):
        """A single pipeline correctly handles both day and sunset defaults."""
        registry = _registry(DefaultHandler())

        # Daytime snapshot
        day_snap = make_snapshot(
            direct_sun_valid=False, default_position=60, is_sunset_active=False
        )
        # Sunset snapshot
        sunset_snap = make_snapshot(
            direct_sun_valid=False, default_position=10, is_sunset_active=True
        )

        day_result = registry.evaluate(day_snap)
        sunset_result = registry.evaluate(sunset_snap)

        assert day_result.position == 60
        assert sunset_result.position == 10
