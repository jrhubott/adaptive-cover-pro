"""Non-blocking forecast-outdoor-temp-source-without-weather-entity notice.

Issue #912: a user who opens only "Temperature & Climate" from the options
menu can select a forecast-based outdoor-temperature source
(``forecast_max`` / ``max_of_live_and_forecast``, issue #547) without ever
seeing the weather-entity picker, which lives on the separate Light/Cloud
step. The source then silently degrades to the live reading
(``state/climate_provider.py`` ``live_fallback``). This adds a non-blocking
Markdown pointer via ``description_placeholders`` on both the initial
``ConfigFlow`` and the ``OptionsFlowHandler`` variants of
``temperature_climate``, computed from the currently saved values.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_OUTSIDE_TEMP_SOURCE,
    CONF_WEATHER_ENTITY,
    CoverType,
    OutsideTempSource,
)

# ---------------------------------------------------------------------------
# Pure helper: _forecast_temp_source_notice
# ---------------------------------------------------------------------------


def test_notice_empty_when_weather_entity_configured():
    from custom_components.adaptive_cover_pro.config_flow import (
        _forecast_temp_source_notice,
    )

    assert (
        _forecast_temp_source_notice(
            OutsideTempSource.FORECAST_MAX.value, "weather.home"
        )
        == ""
    )


def test_notice_empty_when_source_is_live():
    from custom_components.adaptive_cover_pro.config_flow import (
        _forecast_temp_source_notice,
    )

    assert _forecast_temp_source_notice(OutsideTempSource.LIVE.value, None) == ""


def test_notice_present_for_forecast_max_without_weather_entity():
    from custom_components.adaptive_cover_pro.config_flow import (
        _forecast_temp_source_notice,
    )

    notice = _forecast_temp_source_notice(OutsideTempSource.FORECAST_MAX.value, None)
    assert "⚠️" in notice
    assert "weather entity" in notice.lower()
    assert "Weather, Light & Cloud" in notice


def test_notice_present_for_max_of_live_and_forecast_without_weather_entity():
    from custom_components.adaptive_cover_pro.config_flow import (
        _forecast_temp_source_notice,
    )

    notice = _forecast_temp_source_notice(
        OutsideTempSource.MAX_OF_LIVE_AND_FORECAST.value, ""
    )
    assert "⚠️" in notice
    assert "weather entity" in notice.lower()


# ---------------------------------------------------------------------------
# Step wiring: OptionsFlowHandler.async_step_temperature_climate
# ---------------------------------------------------------------------------


def _options_flow(options: dict, sensor_type=CoverType.BLIND):
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": sensor_type}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.sensor_type = sensor_type
    flow.options = dict(options)
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    return flow


@pytest.mark.asyncio
async def test_options_temperature_climate_get_includes_forecast_notice_placeholder():
    flow = _options_flow(
        {CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value}
    )
    result = await flow.async_step_temperature_climate()
    assert "forecast_notice" in result["description_placeholders"]
    assert "⚠️" in result["description_placeholders"]["forecast_notice"]


@pytest.mark.asyncio
async def test_options_temperature_climate_no_notice_when_weather_entity_set():
    flow = _options_flow(
        {
            CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value,
            CONF_WEATHER_ENTITY: "weather.home",
        }
    )
    result = await flow.async_step_temperature_climate()
    assert result["description_placeholders"]["forecast_notice"] == ""


@pytest.mark.asyncio
async def test_options_temperature_climate_errors_reshow_includes_forecast_notice():
    """The blocking climate_mode-without-temp_entity reshow must also resolve
    ``{forecast_notice}`` — computed from the currently SAVED options, not the
    rejected submission.
    """
    flow = _options_flow(
        {CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value}
    )
    result = await flow.async_step_temperature_climate({"climate_mode": True})
    assert result["errors"]
    assert "forecast_notice" in result["description_placeholders"]
    assert "⚠️" in result["description_placeholders"]["forecast_notice"]


# ---------------------------------------------------------------------------
# Step wiring: ConfigFlowHandler.async_step_temperature_climate (initial setup)
# ---------------------------------------------------------------------------


def _config_flow(config: dict, sensor_type=CoverType.BLIND):
    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    flow = ConfigFlowHandler()
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.type_blind = sensor_type
    flow.config = dict(config)
    flow.mode = "basic"
    return flow


@pytest.mark.asyncio
async def test_config_flow_temperature_climate_get_includes_forecast_notice_placeholder():
    flow = _config_flow(
        {CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value}
    )
    result = await flow.async_step_temperature_climate()
    assert "forecast_notice" in result["description_placeholders"]
    assert "⚠️" in result["description_placeholders"]["forecast_notice"]


@pytest.mark.asyncio
async def test_config_flow_temperature_climate_no_notice_when_weather_entity_set():
    flow = _config_flow(
        {
            CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value,
            CONF_WEATHER_ENTITY: "weather.home",
        }
    )
    result = await flow.async_step_temperature_climate()
    assert result["description_placeholders"]["forecast_notice"] == ""


@pytest.mark.asyncio
async def test_config_flow_temperature_climate_errors_reshow_includes_forecast_notice():
    flow = _config_flow(
        {CONF_OUTSIDE_TEMP_SOURCE: OutsideTempSource.FORECAST_MAX.value}
    )
    result = await flow.async_step_temperature_climate({"climate_mode": True})
    assert result["errors"]
    assert "forecast_notice" in result["description_placeholders"]
    assert "⚠️" in result["description_placeholders"]["forecast_notice"]
