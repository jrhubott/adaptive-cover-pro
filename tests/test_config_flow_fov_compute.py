"""Config-flow behaviour for the FOV-from-measurements button (#565).

Covers the schema rendering (toggle present before the always-shown sliders),
the button press that derives ``fov_left``/``fov_right`` and re-renders the form,
the normal save path when the button is not pressed, the transient nature of the
toggle (never persisted), and venetian parity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro.config_flow import (
    ConfigFlowHandler,
    OptionsFlowHandler,
    _get_sun_tracking_schema,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DISTANCE,
    CONF_FOV_COMPUTE,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    CoverType,
)
from custom_components.adaptive_cover_pro.unit_system import options_to_display


def _keys(schema) -> set[str]:
    return {str(m) for m in schema.schema}


def _suggested(result, key):
    for m in result["data_schema"].schema:
        if str(m) == key and m.description:
            return m.description.get("suggested_value")
    raise AssertionError(f"no suggested_value for {key!r}")


# ----------------------------------------------------------------------------
# Schema rendering
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", [CoverType.BLIND, CoverType.VENETIAN])
def test_supported_types_show_button_and_sliders(cover_type):
    keys = _keys(_get_sun_tracking_schema(cover_type))
    assert CONF_FOV_COMPUTE in keys
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys


def test_awning_has_no_button():
    keys = _keys(_get_sun_tracking_schema(CoverType.AWNING))
    assert CONF_FOV_COMPUTE not in keys
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys


# ----------------------------------------------------------------------------
# Options-flow save path
# ----------------------------------------------------------------------------


def _options_flow(options: dict, sensor_type=CoverType.BLIND) -> OptionsFlowHandler:
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
async def test_button_press_derives_fov_and_rerenders():
    # width 2.0 / depth 0.5 → atan(4) ≈ 76°. Ticking the button fills the
    # sliders and re-renders the form rather than advancing.
    flow = _options_flow({CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5})
    advanced = False

    async def _next():
        nonlocal advanced
        advanced = True
        return {"type": "menu"}

    flow.async_step_init = _next
    result = await flow.async_step_sun_tracking(
        {
            CONF_FOV_COMPUTE: True,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            "distance_shaded_area": 0.5,
        }
    )
    assert advanced is False
    assert result["type"] == "form"
    assert result["step_id"] == "sun_tracking"
    # The re-rendered form shows the derived angle as the suggested value.
    assert _suggested(result, CONF_FOV_LEFT) == 76
    assert _suggested(result, CONF_FOV_RIGHT) == 76
    # The toggle is never written to options.
    assert CONF_FOV_COMPUTE not in flow.options


@pytest.mark.asyncio
async def test_button_not_pressed_saves_typed_values():
    flow = _options_flow({CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5})
    result = await flow.async_step_sun_tracking(
        {
            CONF_FOV_COMPUTE: False,
            CONF_FOV_LEFT: 30,
            CONF_FOV_RIGHT: 40,
            "distance_shaded_area": 0.5,
        }
    )
    assert result["type"] == "menu"  # advanced (saved)
    assert flow.options[CONF_FOV_LEFT] == 30
    assert flow.options[CONF_FOV_RIGHT] == 40
    assert CONF_FOV_COMPUTE not in flow.options


@pytest.mark.asyncio
async def test_absent_toggle_saves_typed_values():
    # The toggle may be omitted entirely (default off) → typed values saved.
    flow = _options_flow({CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5})
    await flow.async_step_sun_tracking(
        {
            CONF_FOV_LEFT: 55,
            CONF_FOV_RIGHT: 65,
            "distance_shaded_area": 0.5,
        }
    )
    assert flow.options[CONF_FOV_LEFT] == 55
    assert flow.options[CONF_FOV_RIGHT] == 65


@pytest.mark.asyncio
async def test_legacy_fov_mode_key_dropped_on_save():
    # An entry created before the button replaced the selector may carry a stale
    # ``fov_mode`` option — it must be dropped on the next sun-tracking save.
    flow = _options_flow(
        {CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5, "fov_mode": "measurements"}
    )
    await flow.async_step_sun_tracking(
        {CONF_FOV_LEFT: 45, CONF_FOV_RIGHT: 45, "distance_shaded_area": 0.5}
    )
    assert "fov_mode" not in flow.options


@pytest.mark.asyncio
async def test_button_works_for_venetian():
    flow = _options_flow(
        {CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5}, sensor_type=CoverType.VENETIAN
    )
    result = await flow.async_step_sun_tracking(
        {
            CONF_FOV_COMPUTE: True,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            "distance_shaded_area": 0.5,
        }
    )
    assert result["type"] == "form"
    assert _suggested(result, CONF_FOV_LEFT) == 76


# ----------------------------------------------------------------------------
# Imperial round-trip stability across the button re-render (#565)
# ----------------------------------------------------------------------------


def _imperial_options_flow(options: dict) -> OptionsFlowHandler:
    flow = _options_flow(options)
    flow.hass.config.units = US_CUSTOMARY_SYSTEM
    flow.hass.states.get.return_value = None
    return flow


@pytest.mark.asyncio
async def test_imperial_shaded_area_stable_across_button_rerender():
    # The button press re-renders the form. On an imperial hass the "shaded
    # area" (distance) value must NOT be re-converted metres->inches a second
    # time, or it compounds on each rerender until the slider overruns.
    flow = _imperial_options_flow({CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5})
    expected_in = options_to_display(
        flow.hass, {CONF_DISTANCE: 0.5}, length_keys=(CONF_DISTANCE,)
    )[CONF_DISTANCE]

    result1 = await flow.async_step_sun_tracking(
        {CONF_FOV_COMPUTE: True, CONF_DISTANCE: expected_in}
    )
    assert result1["type"] == "form"
    s1 = _suggested(result1, CONF_DISTANCE)
    assert s1 == pytest.approx(expected_in, abs=0.1)

    # Second submit without the button: saves rather than looping.
    result2 = await flow.async_step_sun_tracking(
        {CONF_DISTANCE: s1, CONF_FOV_LEFT: 76, CONF_FOV_RIGHT: 76}
    )
    assert result2["type"] == "menu"
    import math

    assert math.isclose(flow.options[CONF_DISTANCE], 0.5, abs_tol=0.05)


# ----------------------------------------------------------------------------
# Create-flow parity
# ----------------------------------------------------------------------------


def _create_flow(sensor_type: str = CoverType.BLIND) -> ConfigFlowHandler:
    """Build a minimal ConfigFlowHandler suitable for unit tests."""
    flow = ConfigFlowHandler.__new__(ConfigFlowHandler)
    flow.hass = MagicMock()
    flow.hass.config.units = MagicMock()
    flow.hass.config.units.is_metric = True
    flow.hass.states.get.return_value = None
    flow.type_blind = sensor_type
    flow.config = {}
    flow.async_step_position = AsyncMock(
        return_value={"type": "form", "step_id": "position"}
    )
    return flow


@pytest.mark.asyncio
async def test_create_flow_button_press_then_save():
    flow = _create_flow()
    flow.config[CONF_WINDOW_WIDTH] = 2.0
    flow.config[CONF_WINDOW_DEPTH] = 0.5

    # Button press → re-render, no advance.
    result1 = await flow.async_step_sun_tracking(
        {CONF_FOV_COMPUTE: True, "distance_shaded_area": 0.5}
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "sun_tracking"
    assert _suggested(result1, CONF_FOV_LEFT) == 76

    # Plain submit → advance to position, persisting the fov values.
    result2 = await flow.async_step_sun_tracking(
        {CONF_FOV_LEFT: 76, CONF_FOV_RIGHT: 76, "distance_shaded_area": 0.5}
    )
    assert result2["step_id"] == "position"
    assert flow.config[CONF_FOV_LEFT] == 76
    assert CONF_FOV_COMPUTE not in flow.config


def test_supported_type_sliders_are_optional():
    schema = _get_sun_tracking_schema(CoverType.BLIND)
    markers = {str(m): m for m in schema.schema}
    assert isinstance(markers[CONF_FOV_LEFT], vol.Optional)
    assert isinstance(markers[CONF_FOV_RIGHT], vol.Optional)
