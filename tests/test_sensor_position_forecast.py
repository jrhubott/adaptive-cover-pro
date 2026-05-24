"""Tests for the `position_forecast` sensor's read path (issue #437).

The sensor must read from `coordinator.data.position_forecast` and never
recompute the forecast itself. These tests pin that contract.

Before the fix:
- `_position_forecast_value` and `_position_forecast_attrs` each called
  `_safe_forecast(coord)`, recomputing the full forecast twice per state
  write.
- 14 switches per entry × `await coordinator.async_refresh()` on
  `async_added_to_hass` blew past HA's bootstrap-stage-2 timeout.

After the fix:
- Both `value_fn` and `attrs_fn` read `coordinator.data.position_forecast`.
- The coordinator (not the sensor) owns the recompute on a slow cadence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock

import pytest


_NOW = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)


def _make_sensor_with_coord(forecast):
    """Build the minimal sensor stand-in the value/attrs callables need."""
    from custom_components.adaptive_cover_pro.coordinator import AdaptiveCoverData

    sensor = MagicMock()
    sensor.coordinator = MagicMock()
    sensor.coordinator.data = AdaptiveCoverData(
        climate_mode_toggle=False,
        states={},
        attributes={},
        position_forecast=forecast,
    )
    return sensor


@pytest.mark.unit
def test_value_and_attrs_do_not_recompute_forecast(monkeypatch):
    """Reading `native_value` then `extra_state_attributes` must NOT call build_forecast_for_coord.

    Before the fix, each read triggered a full 12-hour forecast rebuild.
    After the fix the data is pre-baked on the coordinator.
    """
    build_mock = MagicMock(side_effect=AssertionError("forecast must not recompute"))
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        build_mock,
    )

    from custom_components.adaptive_cover_pro.forecast import (
        Forecast,
        ForecastEvent,
        ForecastSample,
    )
    from custom_components.adaptive_cover_pro.sensor import (
        _position_forecast_attrs,
        _position_forecast_value,
    )

    upcoming_t = _NOW + timedelta(hours=2)
    forecast = Forecast(
        samples=(ForecastSample(t=_NOW, position=50, handler="solar"),),
        events=(ForecastEvent(t=upcoming_t, kind="sunrise", label="Sunrise"),),
    )
    sensor = _make_sensor_with_coord(forecast)

    # Patch dt_util.now() to a fixed reference so the upcoming-event slice
    # is deterministic.
    from custom_components.adaptive_cover_pro import sensor as sensor_mod

    monkeypatch.setattr(sensor_mod.dt_util, "now", lambda: _NOW)

    value = _position_forecast_value(sensor)
    attrs = _position_forecast_attrs(sensor)

    assert value == upcoming_t
    assert "forecast" in attrs
    assert "events" in attrs
    # build_forecast_for_coord was patched to fail loudly if called.
    assert build_mock.call_count == 0


@pytest.mark.unit
def test_value_returns_none_when_forecast_missing():
    """Pre-first-refresh / failed forecast: `native_value` must degrade to None."""
    from custom_components.adaptive_cover_pro.sensor import _position_forecast_value

    sensor = _make_sensor_with_coord(None)
    assert _position_forecast_value(sensor) is None


@pytest.mark.unit
def test_attrs_returns_none_when_forecast_missing():
    """Pre-first-refresh / failed forecast: `extra_state_attributes` must degrade to None."""
    from custom_components.adaptive_cover_pro.sensor import _position_forecast_attrs

    sensor = _make_sensor_with_coord(None)
    assert _position_forecast_attrs(sensor) is None


@pytest.mark.unit
def test_value_returns_none_when_no_upcoming_events(monkeypatch):
    """When every event is in the past, the sensor reports no next event."""
    from custom_components.adaptive_cover_pro.forecast import (
        Forecast,
        ForecastEvent,
    )
    from custom_components.adaptive_cover_pro import sensor as sensor_mod
    from custom_components.adaptive_cover_pro.sensor import _position_forecast_value

    past_t = _NOW - timedelta(hours=2)
    forecast = Forecast(
        samples=(),
        events=(ForecastEvent(t=past_t, kind="sunrise", label="Sunrise"),),
    )
    sensor = _make_sensor_with_coord(forecast)
    monkeypatch.setattr(sensor_mod.dt_util, "now", lambda: _NOW)

    assert _position_forecast_value(sensor) is None


@pytest.mark.unit
def test_no_recompute_across_n_state_writes(monkeypatch):
    """Driving N back-to-back reads through both callables triggers zero recomputes.

    Simulates the boot path where ~14 switches each fire a coordinator
    refresh → 14 sensor state writes → 28 callable invocations. Before the
    fix this was 28 full forecast computations; after the fix it must be 0.
    """
    build_mock = MagicMock(side_effect=AssertionError("forecast must not recompute"))
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        build_mock,
    )
    from custom_components.adaptive_cover_pro.forecast import Forecast
    from custom_components.adaptive_cover_pro.sensor import (
        _position_forecast_attrs,
        _position_forecast_value,
    )
    from custom_components.adaptive_cover_pro import sensor as sensor_mod

    sensor = _make_sensor_with_coord(Forecast(samples=(), events=()))
    monkeypatch.setattr(sensor_mod.dt_util, "now", lambda: _NOW)

    for _ in range(14):
        _position_forecast_value(sensor)
        _position_forecast_attrs(sensor)

    assert build_mock.call_count == 0
