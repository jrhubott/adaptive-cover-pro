"""Two-pass reveal of the weather-retraction pickers in the weather-override step.

Flipping ``CONF_SHOW_WEATHER_RETRACTION`` on (when it was off) re-renders the
form with the wind/rain/severe pickers exposed instead of committing; a second
submit commits and advances. Mirrors the ``async_step_cover_entities`` /
fov-compute two-pass pattern. Exercised against both the config FlowHandler and
the OptionsFlowHandler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_flow import (
    ConfigFlowHandler,
    OptionsFlowHandler,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_SHOW_WEATHER_RETRACTION,
    CONF_WEATHER_RAIN_SENSOR,
    CoverType,
)


def _schema_keys(schema):
    return {str(marker.schema) for marker in schema.schema}


def _create_flow(sensor_type=CoverType.BLIND) -> ConfigFlowHandler:
    flow = ConfigFlowHandler.__new__(ConfigFlowHandler)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.type_blind = sensor_type
    flow.config = {}
    flow.async_step_manual_override = AsyncMock(
        return_value={"type": "form", "step_id": "manual_override"}
    )
    return flow


def _options_flow(options: dict, sensor_type=CoverType.BLIND) -> OptionsFlowHandler:
    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": sensor_type}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.options = dict(options)
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    return flow


# --- config FlowHandler ---


@pytest.mark.asyncio
async def test_create_flow_toggle_on_rerenders_then_commits():
    """Turning the toggle on re-renders with pickers; a second submit advances."""
    flow = _create_flow(CoverType.BLIND)

    result1 = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True}
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "weather_override"
    assert CONF_WEATHER_RAIN_SENSOR in _schema_keys(result1["data_schema"])
    flow.async_step_manual_override.assert_not_called()

    result2 = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True, CONF_WEATHER_RAIN_SENSOR: "sensor.rain"}
    )
    assert result2["step_id"] == "manual_override"
    assert flow.config[CONF_SHOW_WEATHER_RETRACTION] is True
    assert flow.config[CONF_WEATHER_RAIN_SENSOR] == "sensor.rain"


@pytest.mark.asyncio
async def test_create_flow_awning_commits_without_rerender():
    """An awning shows pickers by default, so submitting the toggle on commits."""
    flow = _create_flow(CoverType.AWNING)

    result = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True}
    )
    assert result["step_id"] == "manual_override"
    flow.async_step_manual_override.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_flow_toggle_off_commits_without_rerender():
    """Leaving the toggle off (blind default) commits straight away."""
    flow = _create_flow(CoverType.BLIND)

    result = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: False}
    )
    assert result["step_id"] == "manual_override"


# --- OptionsFlowHandler ---


@pytest.mark.asyncio
async def test_options_flow_toggle_on_rerenders_then_commits():
    """Options flow mirrors the two-pass reveal."""
    flow = _options_flow({}, sensor_type=CoverType.BLIND)

    result1 = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True}
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "weather_override"
    assert CONF_WEATHER_RAIN_SENSOR in _schema_keys(result1["data_schema"])
    flow.async_step_init.assert_not_called()

    result2 = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True, CONF_WEATHER_RAIN_SENSOR: "sensor.rain"}
    )
    assert result2["type"] == "menu"
    assert flow.options[CONF_SHOW_WEATHER_RETRACTION] is True
    assert flow.options[CONF_WEATHER_RAIN_SENSOR] == "sensor.rain"


@pytest.mark.asyncio
async def test_options_flow_awning_commits_without_rerender():
    """An awning options flow commits the toggle-on submit directly."""
    flow = _options_flow({}, sensor_type=CoverType.AWNING)

    result = await flow.async_step_weather_override(
        {CONF_SHOW_WEATHER_RETRACTION: True}
    )
    assert result["type"] == "menu"
    flow.async_step_init.assert_awaited_once()
