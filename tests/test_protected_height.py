"""Room-wide protected-plane geometry and configuration regression tests."""

from datetime import UTC, datetime
from math import cos, radians, tan
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro.config_flow import (
    _geometry_unit_keys,
    _get_geometry_schema,
)
from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DISTANCE,
    CONF_PROTECTED_HEIGHT,
    ControlMethod,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types._helpers import (
    window_dimensions_lines,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone import (
    GlareZoneHandler,
)
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    validate_options_patch,
)
from custom_components.adaptive_cover_pro.unit_system import (
    options_to_display,
    user_input_to_canonical,
)
from tests.cover_helpers import build_vertical_cover
from tests.test_pipeline.conftest import make_snapshot


def make_cover(**kwargs):
    """Construct a real engine facing the sun, with a two-foot protected plane."""
    sun_data = MagicMock()
    sun_data.sunrise.return_value = datetime(2000, 1, 1, tzinfo=UTC)
    sun_data.sunset.return_value = datetime(2100, 1, 1, tzinfo=UTC)
    return build_vertical_cover(
        logger=MagicMock(),
        sun_data=sun_data,
        win_azi=180,
        sol_azi=180,
        sol_elev=45,
        distance=1.2192,
        h_win=2.30124,
        sill_height=0.09906,
        protected_height=0.6096,
        **kwargs,
    )


@pytest.mark.parametrize("elevation", [5, 6.7, 15, 30, 60])
@pytest.mark.parametrize("gamma", [0, -35, 60, 89.6])
def test_boundary_on_elevated_plane(elevation, gamma):
    cover = make_cover()
    cover.sol_elev = elevation
    cover.sol_azi = cover.win_azi - gamma
    expected = (
        1.2192 * tan(radians(elevation)) / max(cos(radians(gamma)), 0.01)
        + 0.6096
        - 0.09906
    )
    assert cover.calculate_position() == pytest.approx(
        min(max(expected, 0), cover.h_win)
    )
    assert cover._last_calc_details["protected_height_m"] == 0.6096


def test_morning_example():
    cover = make_cover()
    cover.sol_elev = 6.7
    cover.sol_azi = 184.1
    assert cover.calculate_raw_percentage() == pytest.approx(28.4245, abs=0.001)


@pytest.mark.parametrize("height", [0.0, 0.6096, 1.5])
def test_sill_above_plane_can_require_full_closure(height):
    cover = make_cover()
    cover.protected_height = height
    cover.sill_height = height + 2.0
    assert cover.calculate_position() == 0.0


def test_zero_distance_still_allows_glass_below_plane():
    cover = make_cover()
    cover.distance = 0.0
    assert cover.calculate_position() == pytest.approx(0.6096 - 0.09906)


def test_default_height_is_exact_legacy_path():
    cover = build_vertical_cover(
        logger=MagicMock(),
        sun_data=MagicMock(),
        win_azi=180,
        sol_azi=180,
        sol_elev=45,
        distance=0.5,
        h_win=2,
        sill_height=0.1,
    )
    assert cover.protected_height == 0.0
    assert cover.tracking_distance == cover.distance
    assert cover.calculate_position() == pytest.approx(0.4)


def test_glare_override_does_not_add_room_height_twice():
    cover = make_cover()
    before = cover.calculate_position(effective_distance_override=0.8)
    cover.protected_height = 1.2
    assert cover.calculate_position(effective_distance_override=0.8) == before
    assert cover._last_calc_details["protected_height_m"] == 0.0


def test_glare_zone_beyond_base_distance_can_still_need_protection():
    cover = make_cover()
    # Edge is 1.5 m from the glass, beyond the room's 1.2192 m distance,
    # but below its elevated plane: ordinary tracking would leave it sunny.
    zone = GlareZone(name="Floor", x=0, y=1.6, radius=0.1)
    snapshot = make_snapshot(
        cover=cover,
        cover_type="cover_blind",
        glare_zones=GlareZonesConfig([zone], 1.0),
        active_zone_names={"Floor"},
    )
    result = GlareZoneHandler().evaluate(snapshot)
    assert result is not None
    assert result.control_method == ControlMethod.GLARE_ZONE
    assert result.position < cover.calculate_percentage()


def test_reveal_can_make_elevated_boundary_fully_open():
    cover = make_cover()
    cover.window_depth = 1.0
    assert cover.calculate_position() == cover.h_win


def test_very_low_sun_fallback_is_retained():
    cover = make_cover()
    cover.sol_elev = 0.01
    assert cover.calculate_position() == 0.0


@pytest.mark.parametrize("imperial", [False, True])
def test_ui_units_order_and_round_trip(imperial):
    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if imperial else METRIC_SYSTEM
    hass.states.get.return_value = None
    schema = _get_geometry_schema("cover_blind", hass)
    keys = [str(k) for k in schema.schema]
    assert keys.index(CONF_PROTECTED_HEIGHT) == keys.index(CONF_DISTANCE) + 1
    selector = next(
        v for k, v in schema.schema.items() if str(k) == CONF_PROTECTED_HEIGHT
    )
    assert selector.config["unit_of_measurement"] == ("in" if imperial else "m")
    lengths, slats = _geometry_unit_keys("cover_blind")
    entered = 24.0 if imperial else 0.6096
    stored = user_input_to_canonical(
        hass, {CONF_PROTECTED_HEIGHT: entered}, length_keys=lengths, slat_keys=slats
    )
    assert stored[CONF_PROTECTED_HEIGHT] == pytest.approx(0.6096)
    displayed = options_to_display(hass, stored, length_keys=lengths, slat_keys=slats)
    assert displayed[CONF_PROTECTED_HEIGHT] == pytest.approx(entered)


@pytest.mark.parametrize(
    "cover_type", [t.value for t in CoverType if t != CoverType.BLIND]
)
def test_other_cover_types_do_not_offer_height(cover_type):
    assert CONF_PROTECTED_HEIGHT not in get_policy(cover_type).live_option_keys()


@pytest.mark.parametrize("value", [-0.1, 50.1, float("nan"), float("inf")])
def test_service_rejects_invalid_height(value):
    with pytest.raises(ServiceValidationError):
        validate_options_patch(
            {CONF_PROTECTED_HEIGHT: value}, {}, sensor_type=CoverType.BLIND
        )


def test_service_accepts_height_and_rejects_other_cover_types():
    assert (
        validate_options_patch(
            {CONF_PROTECTED_HEIGHT: 0.6096}, {}, sensor_type=CoverType.BLIND
        )[CONF_PROTECTED_HEIGHT]
        == 0.6096
    )
    with pytest.raises(ServiceValidationError):
        validate_options_patch(
            {CONF_PROTECTED_HEIGHT: 0.6096}, {}, sensor_type=CoverType.AWNING
        )


def test_config_extraction_defaults_and_preserves_height():
    service = ConfigurationService(
        hass=MagicMock(),
        config_entry=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        temp_toggle=False,
        lux_toggle=False,
        irradiance_toggle=False,
    )
    assert service.get_vertical_data({}).protected_height == 0.0
    assert (
        service.get_vertical_data({CONF_PROTECTED_HEIGHT: None}).protected_height == 0.0
    )
    assert (
        service.get_vertical_data({CONF_PROTECTED_HEIGHT: 0.6096}).protected_height
        == 0.6096
    )


def test_summary_includes_nonzero_height():
    text = " ".join(
        window_dimensions_lines({CONF_DISTANCE: 1.2192, CONF_PROTECTED_HEIGHT: 0.6096})
    )
    assert "0.6096m above floor" in text


@pytest.mark.parametrize("flow_kind", ["create", "options"])
@pytest.mark.parametrize("imperial", [False, True])
async def test_geometry_flow_keeps_height_on_save_and_reopen(flow_kind, imperial):
    """Both flow handlers save metres and redisplay the user's length unit."""
    from unittest.mock import AsyncMock
    from custom_components.adaptive_cover_pro.config_flow import (
        ConfigFlowHandler,
        OptionsFlowHandler,
    )

    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if imperial else METRIC_SYSTEM
    hass.states.get.return_value = None
    if flow_kind == "create":
        flow = ConfigFlowHandler.__new__(ConfigFlowHandler)
        flow.hass = hass
        flow.type_blind = CoverType.BLIND
        flow.config = {}
        flow.async_step_summary = AsyncMock(return_value={"type": "form"})
    else:
        entry = MagicMock()
        entry.options = {}
        entry.data = {"sensor_type": CoverType.BLIND}
        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        flow.sensor_type = CoverType.BLIND
        flow.options = {}
        flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    await flow.async_step_geometry(
        {
            CONF_DISTANCE: 48.0 if imperial else 1.2192,
            CONF_PROTECTED_HEIGHT: 24.0 if imperial else 0.6096,
        }
    )
    # Handlers may replace the dictionary when normalizing input.
    saved = flow.config if flow_kind == "create" else flow.options
    assert saved[CONF_PROTECTED_HEIGHT] == pytest.approx(0.6096)
    result = await flow.async_step_geometry(None)
    marker = next(
        k for k in result["data_schema"].schema if str(k) == CONF_PROTECTED_HEIGHT
    )
    assert marker.description["suggested_value"] == pytest.approx(
        24.0 if imperial else 0.6096
    )


@pytest.mark.parametrize("minimize,expected", [(False, 28), (True, 20)])
def test_forecast_uses_elevated_boundary_and_existing_coverage_steps(
    minimize, expected
):
    """Forecast and live targets share the protected plane and motion rounding."""
    from dataclasses import replace
    from custom_components.adaptive_cover_pro.forecast import build_forecast
    from custom_components.adaptive_cover_pro.pipeline.helpers import (
        solar_position_from_geometry,
    )
    from tests.test_forecast import _make_sun_data, _NOW

    cover = make_cover()
    cover.sol_elev = 6.7
    cover.sol_azi = 184.1
    sd = _make_sun_data(
        azi_at=184.1,
        ele_at=6.7,
        sunrise=datetime(2000, 1, 1, tzinfo=UTC),
        sunset=datetime(2100, 1, 1, tzinfo=UTC),
    )
    policy = get_policy("cover_blind")
    forecast = build_forecast(
        sun_data=sd,
        cover_factory=lambda azi, elev: replace(cover, sol_azi=azi, sol_elev=elev),
        config=cover.config,
        policy=policy,
        now=_NOW,
        minimize_movements=minimize,
        max_coverage_steps=5,
        floor_active=False,
    )
    live = solar_position_from_geometry(
        cover,
        cover.config,
        policy=policy,
        minimize_movements=minimize,
        max_coverage_steps=5,
        floor_active=False,
    )
    assert live == expected
    assert forecast.samples
    assert all(sample.position == live for sample in forecast.samples)
