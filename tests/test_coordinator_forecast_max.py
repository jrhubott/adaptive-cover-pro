"""Coordinator forecast daily-max fetch + cache (issue #547).

The coordinator fetches today's forecast high from the configured weather
entity via ``weather.get_forecasts`` on a slow wall-clock cadence and caches
it in ``self._forecast_max_outside`` so the climate read can source it. The
fetch runs only when the outdoor-temp source is not ``live`` AND a weather
entity is configured; every failure path degrades to ``None`` (the provider
then falls back to the live read).

Uses the mock-coordinator pattern: the unbound method is invoked on a minimal
mock carrying just the attributes it touches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_OUTSIDE_TEMP_SOURCE,
    CONF_WEATHER_ENTITY,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)


def _make_coordinator(*, options: dict, forecast_response=None, raises=None):
    """Minimal mock coordinator for the forecast-max fetch method."""
    coord = MagicMock()
    coord._forecast_max_outside = None
    coord.logger = MagicMock()

    entry = MagicMock()
    entry.options = options
    coord.config_entry = entry

    async def _async_call(*args, **kwargs):
        if raises is not None:
            raise raises
        return forecast_response

    coord.hass = MagicMock()
    coord.hass.services.async_call = AsyncMock(side_effect=_async_call)
    return coord


async def _run(coord):
    await AdaptiveDataUpdateCoordinator.async_recompute_forecast_max(coord)


class TestForecastMaxFetch:
    """The coordinator's forecast daily-high fetch + cache."""

    @pytest.mark.asyncio
    async def test_caches_today_high_from_daily_forecast(self):
        """A daily forecast's first entry temperature is cached as the max."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "forecast_max",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            forecast_response={"weather.home": {"forecast": [{"temperature": 26.0}]}},
        )
        await _run(coord)
        assert coord._forecast_max_outside == 26.0
        coord.hass.services.async_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_fetch_when_source_live(self):
        """Source ``live`` skips the fetch and clears any stale cache."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "live",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            forecast_response={"weather.home": {"forecast": [{"temperature": 26.0}]}},
        )
        coord._forecast_max_outside = 99.0
        await _run(coord)
        coord.hass.services.async_call.assert_not_called()
        assert coord._forecast_max_outside is None

    @pytest.mark.asyncio
    async def test_no_fetch_when_no_weather_entity(self):
        """No weather entity → no fetch, cache cleared."""
        coord = _make_coordinator(
            options={CONF_OUTSIDE_TEMP_SOURCE: "forecast_max"},
            forecast_response={},
        )
        coord._forecast_max_outside = 12.0
        await _run(coord)
        coord.hass.services.async_call.assert_not_called()
        assert coord._forecast_max_outside is None

    @pytest.mark.asyncio
    async def test_service_error_degrades_to_none(self):
        """A service exception leaves the cache at None."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "max_of_live_and_forecast",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            raises=RuntimeError("boom"),
        )
        coord._forecast_max_outside = 20.0
        await _run(coord)
        assert coord._forecast_max_outside is None

    @pytest.mark.asyncio
    async def test_empty_forecast_degrades_to_none(self):
        """An empty forecast list degrades to None."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "forecast_max",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            forecast_response={"weather.home": {"forecast": []}},
        )
        await _run(coord)
        assert coord._forecast_max_outside is None

    @pytest.mark.asyncio
    async def test_non_numeric_temperature_degrades_to_none(self):
        """A non-numeric forecast temperature degrades to None."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "forecast_max",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            forecast_response={"weather.home": {"forecast": [{"temperature": "n/a"}]}},
        )
        await _run(coord)
        assert coord._forecast_max_outside is None

    @pytest.mark.asyncio
    async def test_missing_temperature_key_degrades_to_none(self):
        """A forecast entry without a temperature key degrades to None."""
        coord = _make_coordinator(
            options={
                CONF_OUTSIDE_TEMP_SOURCE: "forecast_max",
                CONF_WEATHER_ENTITY: "weather.home",
            },
            forecast_response={"weather.home": {"forecast": [{"cloud": 5}]}},
        )
        await _run(coord)
        assert coord._forecast_max_outside is None


class TestForecastMaxScheduler:
    """The wall-clock scheduler that drives the forecast-max refresher."""

    @pytest.mark.asyncio
    async def test_scheduler_kicks_off_initial_task_and_timer(self, monkeypatch):
        """One background task + one wall-clock timer are registered."""
        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.hass = MagicMock()
        coord.config_entry = MagicMock()
        coord._forecast_max_unsub = None

        def _capture_bg(_hass, coro, name=None):  # noqa: ARG001
            coro.close()
            return MagicMock(name="task")

        coord.config_entry.async_create_background_task = MagicMock(
            side_effect=_capture_bg
        )

        track_calls: list = []

        def _fake_track_time_change(_hass, cb, **kwargs):
            track_calls.append((cb, kwargs))
            return MagicMock(name="unsub")

        monkeypatch.setattr(
            "homeassistant.helpers.event.async_track_time_change",
            _fake_track_time_change,
        )

        AdaptiveDataUpdateCoordinator._start_forecast_max_scheduler(coord)

        assert coord.config_entry.async_create_background_task.call_count == 1
        assert len(track_calls) == 1
        from custom_components.adaptive_cover_pro.const import (
            FORECAST_RECOMPUTE_INTERVAL_MIN,
        )

        cb, kwargs = track_calls[0]
        assert list(kwargs["minute"]) == list(
            range(0, 60, FORECAST_RECOMPUTE_INTERVAL_MIN)
        )
        assert kwargs["second"] == 0
        assert getattr(cb, "_hass_callback", False) is True
        assert coord._forecast_max_unsub is not None

        # Tick fires another background task.
        import datetime as _dt

        cb(_dt.datetime.now(_dt.UTC))
        assert coord.config_entry.async_create_background_task.call_count == 2

    @pytest.mark.asyncio
    async def test_scheduler_is_idempotent(self, monkeypatch):
        """A second call with an existing handle registers nothing new."""
        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.hass = MagicMock()
        coord.config_entry = MagicMock()
        coord._forecast_max_unsub = MagicMock(name="existing")

        coord.config_entry.async_create_background_task = MagicMock()
        track_mock = MagicMock(return_value=MagicMock(name="unsub"))
        monkeypatch.setattr(
            "homeassistant.helpers.event.async_track_time_change", track_mock
        )

        AdaptiveDataUpdateCoordinator._start_forecast_max_scheduler(coord)

        assert coord.config_entry.async_create_background_task.call_count == 0
        assert track_mock.call_count == 0
