"""Config-flow behaviour for the two-mode FOV selector (#565).

Covers per-mode schema rendering, the re-render-on-mode-change pattern, the
save path that derives fov_left/right in MEASUREMENTS mode, and backward
compatibility when ``fov_mode`` is absent.
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
    CONF_FOV_LEFT,
    CONF_FOV_MODE,
    CONF_FOV_RIGHT,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    FovMode,
)
from custom_components.adaptive_cover_pro.const import CoverType
from custom_components.adaptive_cover_pro.unit_system import options_to_display


def _keys(schema) -> set[str]:
    return {str(m) for m in schema.schema}


def _marker_for(schema, key) -> vol.Marker:
    for m in schema.schema:
        if str(m) == key:
            return m
    raise AssertionError(f"{key!r} not in schema")


def _suggested(result, key):
    for m in result["data_schema"].schema:
        if str(m) == key and m.description:
            return m.description.get("suggested_value")
    raise AssertionError(f"no suggested_value for {key!r}")


# ----------------------------------------------------------------------------
# Per-mode schema rendering
# ----------------------------------------------------------------------------


def test_blind_angles_mode_shows_fov_sliders():
    schema = _get_sun_tracking_schema(CoverType.BLIND, mode=FovMode.ANGLES)
    keys = _keys(schema)
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys
    assert CONF_FOV_MODE in keys


def test_blind_measurements_mode_shows_fov_sliders():
    schema = _get_sun_tracking_schema(CoverType.BLIND, mode=FovMode.MEASUREMENTS)
    keys = _keys(schema)
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys
    # The mode selector itself stays so the user can switch back.
    assert CONF_FOV_MODE in keys


def test_blind_measurements_mode_sliders_have_suggested_value():
    import math

    schema = _get_sun_tracking_schema(
        CoverType.BLIND,
        mode=FovMode.MEASUREMENTS,
        source_config={CONF_WINDOW_WIDTH: 2.0, CONF_WINDOW_DEPTH: 0.5},
    )
    expected = round(math.degrees(math.atan(2.0 / 0.5)))  # ≈ 76

    def _get_suggested(schema, key):
        for m in schema.schema:
            if str(m) == key and m.description:
                return m.description.get("suggested_value")
        raise AssertionError(f"no suggested_value for {key!r}")

    assert _get_suggested(schema, CONF_FOV_LEFT) == expected
    assert _get_suggested(schema, CONF_FOV_RIGHT) == expected


def test_blind_default_mode_is_angles():
    # No mode passed → behaves as ANGLES (sliders shown).
    schema = _get_sun_tracking_schema(CoverType.BLIND)
    keys = _keys(schema)
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys


def test_awning_never_gets_mode_selector():
    schema = _get_sun_tracking_schema(CoverType.AWNING, mode=FovMode.MEASUREMENTS)
    keys = _keys(schema)
    assert CONF_FOV_MODE not in keys
    # Awnings keep their fov sliders regardless of mode argument.
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys


@pytest.mark.parametrize("mode", [FovMode.ANGLES, None])
def test_blind_fov_fields_are_optional(mode):
    # #565: the fov sliders must be vol.Optional so HA's frontend client-side
    # "required field" check never blocks switching to Measurements mode before
    # the backend can re-render with them hidden. The default is preserved.
    schema = _get_sun_tracking_schema(CoverType.BLIND, mode=mode)
    assert isinstance(_marker_for(schema, CONF_FOV_LEFT), vol.Optional)
    assert isinstance(_marker_for(schema, CONF_FOV_RIGHT), vol.Optional)
    assert _marker_for(schema, CONF_FOV_LEFT).default() == 90
    assert _marker_for(schema, CONF_FOV_RIGHT).default() == 90


def test_awning_fov_fields_stay_required():
    # Only blinds relax requiredness (they own the FOV-mode feature). Awnings
    # have no mode selector, so their fov sliders stay vol.Required.
    schema = _get_sun_tracking_schema(CoverType.AWNING, mode=None)
    assert isinstance(_marker_for(schema, CONF_FOV_LEFT), vol.Required)
    assert isinstance(_marker_for(schema, CONF_FOV_RIGHT), vol.Required)


# ----------------------------------------------------------------------------
# Save-path derivation (options flow)
# ----------------------------------------------------------------------------


def _options_flow(options: dict) -> OptionsFlowHandler:
    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": CoverType.BLIND}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.sensor_type = CoverType.BLIND
    flow.options = dict(options)
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    return flow


@pytest.mark.asyncio
async def test_measurements_mode_stores_derived_fov():
    # width 2.0 / depth 0.5 → atan(4) ≈ 76°. The form was already in
    # Measurements mode (stored mode == MEASUREMENTS), so submitting it derives
    # and saves rather than re-rendering.
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
        }
    )
    await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            "distance_shaded_area": 0.5,
        }
    )
    assert flow.options[CONF_FOV_LEFT] == 76
    assert flow.options[CONF_FOV_RIGHT] == 76
    # window_depth itself is untouched.
    assert flow.options[CONF_WINDOW_DEPTH] == 0.5
    assert flow.options[CONF_FOV_MODE] == FovMode.MEASUREMENTS


@pytest.mark.asyncio
async def test_measurements_mode_stores_user_override_fov():
    # When sliders are shown in MEASUREMENTS mode and the user types explicit
    # values, those values must be stored — NOT overwritten by the derived angle.
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
        }
    )
    await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 60,
            "distance_shaded_area": 0.5,
        }
    )
    # User typed 90 and 60 — derived would be 76. Must keep user values.
    assert flow.options[CONF_FOV_LEFT] == 90
    assert flow.options[CONF_FOV_RIGHT] == 60


@pytest.mark.asyncio
async def test_angles_mode_keeps_typed_fov():
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
        }
    )
    await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.ANGLES,
            CONF_FOV_LEFT: 30,
            CONF_FOV_RIGHT: 40,
            "distance_shaded_area": 0.5,
        }
    )
    assert flow.options[CONF_FOV_LEFT] == 30
    assert flow.options[CONF_FOV_RIGHT] == 40


@pytest.mark.asyncio
async def test_absent_fov_mode_behaves_as_angles():
    # Backward compat: no CONF_FOV_MODE in submission → typed fov untouched,
    # no derivation runs.
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


# ----------------------------------------------------------------------------
# Re-render on mode change
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_measurements_rerenders_form_not_next_step():
    # Form was built in ANGLES (the stored/default mode); submitting a different
    # mode must re-show the sun_tracking form, not advance to the next step.
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_MODE: FovMode.ANGLES,
        }
    )
    advanced = False

    async def _next():
        nonlocal advanced
        advanced = True
        return {"type": "menu"}

    flow.async_step_init = _next
    result = await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            "distance_shaded_area": 0.5,
        }
    )
    assert advanced is False
    assert result["type"] == "form"
    assert result["step_id"] == "sun_tracking"
    # The re-rendered form is in MEASUREMENTS mode (sliders shown with
    # suggested_value derived from window width + reveal depth).
    assert CONF_FOV_LEFT in _keys(result["data_schema"])
    assert CONF_FOV_RIGHT in _keys(result["data_schema"])


@pytest.mark.asyncio
async def test_measurements_mode_submittable_without_fov():
    # #565: a user in Measurements mode submits the form without any fov
    # values (the sliders are hidden). The save must succeed and backfill the
    # derived angles from width/depth — it must not block on "required fov".
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
        }
    )
    result = await flow.async_step_sun_tracking(
        {CONF_FOV_MODE: FovMode.MEASUREMENTS, "distance_shaded_area": 0.5}
    )
    assert result["type"] == "menu"  # advanced (saved), not re-rendered
    assert flow.options[CONF_FOV_LEFT] == 76
    assert flow.options[CONF_FOV_RIGHT] == 76


# ----------------------------------------------------------------------------
# Imperial round-trip stability across rerenders (#565)
# ----------------------------------------------------------------------------


def _imperial_options_flow(options: dict) -> OptionsFlowHandler:
    flow = _options_flow(options)
    flow.hass.config.units = US_CUSTOMARY_SYSTEM
    flow.hass.states.get.return_value = None
    return flow


@pytest.mark.asyncio
async def test_imperial_shaded_area_stable_across_mode_switch_rerender():
    # #565: switching FOV mode re-renders the form. On an imperial hass the
    # "shaded area" (distance) value must NOT be re-converted metres->inches a
    # second time — otherwise it compounds (~x39 per rerender) until it
    # overruns the slider/OPTION_RANGES max and the form becomes unsaveable.
    flow = _imperial_options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_DISTANCE: 0.5,  # canonical metres
            CONF_FOV_MODE: FovMode.ANGLES,
        }
    )
    # The value the form displays for a stored 0.5 m (≈19.7 in).
    expected_in = options_to_display(
        flow.hass, {CONF_DISTANCE: 0.5}, length_keys=(CONF_DISTANCE,)
    )[CONF_DISTANCE]

    # First mode switch (ANGLES -> MEASUREMENTS) re-renders the form. The user
    # submits the inch value the form currently shows.
    result1 = await flow.async_step_sun_tracking(
        {CONF_FOV_MODE: FovMode.MEASUREMENTS, CONF_DISTANCE: expected_in}
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "sun_tracking"
    s1 = _suggested(result1, CONF_DISTANCE)
    assert s1 == pytest.approx(expected_in, abs=0.1)

    # The re-rendered suggested value must not have compounded (metres->inches
    # applied twice). After the fix the second submit saves rather than
    # looping, so we only verify value-stability on the first re-render, then
    # assert that saving terminates correctly.
    assert s1 == pytest.approx(expected_in, abs=0.1)

    # Second submit with the (un-compounded) inch value: must save, not loop.
    result2 = await flow.async_step_sun_tracking(
        {CONF_FOV_MODE: FovMode.MEASUREMENTS, CONF_DISTANCE: s1}
    )
    assert (
        result2["type"] == "menu"
    ), f"second imperial submit must save, not loop; got {result2['type']}"


# ----------------------------------------------------------------------------
# Switch-then-save regression tests (#565) — the perpetual re-render loop
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_measurements_then_submitting_saves():
    # #565: seed flow in ANGLES; submit MEASUREMENTS once (expect re-render);
    # submit MEASUREMENTS a SECOND time and assert it advances out of
    # sun_tracking and persists the derived fov values.
    # width 2.0 / depth 0.5 → atan(4) ≈ 76°.
    flow = _options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            CONF_FOV_MODE: FovMode.ANGLES,
        }
    )

    # First submit: mode change (ANGLES → MEASUREMENTS) → must re-render.
    result1 = await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            "distance_shaded_area": 0.5,
        }
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "sun_tracking"

    # Second submit: mode is now MEASUREMENTS (no change) → must save.
    result2 = await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            "distance_shaded_area": 0.5,
        }
    )
    assert (
        result2["type"] == "menu"
    ), f"expected save (menu) on second submit, got {result2!r}"
    assert flow.options[CONF_FOV_MODE] == FovMode.MEASUREMENTS
    assert flow.options[CONF_FOV_LEFT] == 76


@pytest.mark.asyncio
async def test_imperial_switch_to_measurements_then_save():
    # #565 imperial variant: same two-submit sequence; the second submit must
    # save and the stored CONF_DISTANCE round-trips to canonical ~0.5 m.
    flow = _imperial_options_flow(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_DISTANCE: 0.5,  # canonical metres
            CONF_FOV_MODE: FovMode.ANGLES,
        }
    )
    # The display value the form shows for stored 0.5 m (≈19.7 in).
    expected_in = options_to_display(
        flow.hass, {CONF_DISTANCE: 0.5}, length_keys=(CONF_DISTANCE,)
    )[CONF_DISTANCE]

    # First submit: mode switch → re-render.
    result1 = await flow.async_step_sun_tracking(
        {CONF_FOV_MODE: FovMode.MEASUREMENTS, CONF_DISTANCE: expected_in}
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "sun_tracking"

    # Second submit: same mode → must save (advance past sun_tracking).
    result2 = await flow.async_step_sun_tracking(
        {CONF_FOV_MODE: FovMode.MEASUREMENTS, CONF_DISTANCE: expected_in}
    )
    assert (
        result2["type"] == "menu"
    ), f"expected save (menu) on second submit, got {result2!r}"
    # The stored distance is in canonical metres; round-trip must be ~0.5 m.
    import math

    stored_m = flow.options.get(CONF_DISTANCE)
    assert stored_m is not None
    assert math.isclose(
        stored_m, 0.5, abs_tol=0.05
    ), f"stored CONF_DISTANCE {stored_m!r} is not ~0.5 m"


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
async def test_create_flow_switch_to_measurements_then_save():
    # #565 create-flow: seed flow with no fov_mode (defaults ANGLES); submit
    # MEASUREMENTS once (expect re-render); submit MEASUREMENTS a SECOND time
    # and assert it calls async_step_position (advances) and persists the mode.
    flow = _create_flow()
    flow.config[CONF_WINDOW_WIDTH] = 2.0
    flow.config[CONF_WINDOW_DEPTH] = 0.5

    # First submit: mode switch (absent/ANGLES → MEASUREMENTS) → re-render.
    result1 = await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            "distance_shaded_area": 0.5,
        }
    )
    assert result1["type"] == "form"
    assert result1["step_id"] == "sun_tracking"

    # Second submit: still MEASUREMENTS → must advance to position step.
    result2 = await flow.async_step_sun_tracking(
        {
            CONF_FOV_MODE: FovMode.MEASUREMENTS,
            "distance_shaded_area": 0.5,
        }
    )
    assert (
        result2["step_id"] == "position"
    ), f"expected advance to position on second submit, got {result2!r}"
    assert flow.config[CONF_FOV_MODE] == FovMode.MEASUREMENTS
