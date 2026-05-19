"""Direct tests for :class:`PipelineSnapshotBuilder`.

The pre-existing climate-wiring tests (``tests/test_coordinator_climate_wiring``)
also exercise the builder through coordinator shims to preserve their original
intent.  These tests are the public-API contract tests that don't pretend to
involve a coordinator at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_LUX_ENTITY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WINTER_CLOSE_INSULATION,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
)
from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateProvider,
    ClimateReadings,
)


def _dummy_readings() -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _make_builder(
    *,
    lux_toggle: bool | None = False,
    irradiance_toggle: bool | None = False,
    temp_toggle: bool = False,
    switch_mode: bool = False,
    motion_control: bool = False,
    states: dict | None = None,
):
    hass = MagicMock()
    states_map = states or {}

    def _states_get(eid):
        return states_map.get(eid)

    hass.states.get.side_effect = _states_get

    climate_provider = MagicMock(spec=ClimateProvider)
    climate_provider.read.return_value = _dummy_readings()

    toggles = MagicMock()
    toggles.lux_toggle = lux_toggle
    toggles.irradiance_toggle = irradiance_toggle
    toggles.temp_toggle = temp_toggle
    toggles.switch_mode = switch_mode
    toggles.motion_control = motion_control

    policy = MagicMock()
    policy.glare_zones_config.return_value = None

    builder = PipelineSnapshotBuilder(
        hass=hass,
        logger=MagicMock(),
        climate_provider=climate_provider,
        toggles=toggles,
        policy=policy,
        config_service=MagicMock(),
    )
    return builder, climate_provider, hass


@pytest.mark.unit
def test_read_force_sensors_returns_on_off_dict():
    on_state = MagicMock()
    on_state.state = "on"
    off_state = MagicMock()
    off_state.state = "off"
    builder, _, _ = _make_builder(
        states={
            "binary_sensor.alarm": on_state,
            "binary_sensor.calm": off_state,
        }
    )
    out = builder.read_force_sensors(
        {CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.alarm", "binary_sensor.calm"]}
    )
    assert out == {"binary_sensor.alarm": True, "binary_sensor.calm": False}


@pytest.mark.unit
def test_read_force_sensors_missing_entity_treated_as_off():
    builder, _, _ = _make_builder(states={})
    out = builder.read_force_sensors(
        {CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.ghost"]}
    )
    assert out == {"binary_sensor.ghost": False}


@pytest.mark.unit
def test_read_force_sensors_no_config_returns_empty():
    builder, _, _ = _make_builder()
    assert builder.read_force_sensors({}) == {}


@pytest.mark.unit
def test_build_climate_options_full_mapping():
    builder, _, _ = _make_builder(temp_toggle=True)
    opts = {
        CONF_TEMP_LOW: 18.0,
        CONF_TEMP_HIGH: 24.0,
        CONF_TRANSPARENT_BLIND: True,
        CONF_OUTSIDE_THRESHOLD: 28.0,
        CONF_CLOUD_SUPPRESSION: True,
        CONF_WINTER_CLOSE_INSULATION: True,
        CONF_CLOUDY_POSITION: 30,
    }
    out = builder.build_climate_options(opts)
    assert isinstance(out, ClimateOptions)
    assert out.temp_low == 18.0
    assert out.temp_high == 24.0
    assert out.temp_switch is True
    assert out.transparent_blind is True
    assert out.temp_summer_outside == 28.0
    assert out.cloud_suppression_enabled is True
    assert out.winter_close_insulation is True
    assert out.cloudy_position == 30


@pytest.mark.unit
def test_build_climate_options_minimal_defaults_to_none_or_false():
    builder, _, _ = _make_builder()
    out = builder.build_climate_options({})
    assert out.temp_low is None
    assert out.temp_high is None
    assert out.temp_switch is False
    assert out.transparent_blind is False
    assert out.cloud_suppression_enabled is False
    assert out.winter_close_insulation is False
    assert out.cloudy_position is None


@pytest.mark.unit
def test_read_custom_position_sensors_emits_one_state_per_configured_slot():
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    state = out[0]
    assert isinstance(state, CustomPositionSensorState)
    assert state.entity_id == "binary_sensor.guest"
    assert state.is_on is True
    assert state.position == 42
    assert state.priority == DEFAULT_CUSTOM_POSITION_PRIORITY
    assert state.min_mode is False
    assert state.use_my is False
    assert state.tilt is None


@pytest.mark.unit
def test_read_custom_position_sensors_unconfigured_returns_empty():
    builder, _, _ = _make_builder()
    assert builder.read_custom_position_sensors({}) == []


@pytest.mark.unit
def test_build_recomputes_effective_default_when_omitted():
    builder, _, _ = _make_builder()
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    cover_data.sun_data.astral_sunset = None
    cover_data.sun_data.astral_sunrise = None
    cover_data.sun_data.now = None
    opts = {CONF_DEFAULT_HEIGHT: 55}

    snapshot = builder.build(
        opts,
        cover_data=cover_data,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: True,
    )
    assert snapshot.default_position == 55
    assert snapshot.is_sunset_active is False


@pytest.mark.unit
def test_build_forwards_explicit_effective_default():
    builder, _, _ = _make_builder(switch_mode=True, motion_control=True)
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    opts = {
        CONF_FORCE_OVERRIDE_POSITION: 95,
        CONF_WEATHER_OVERRIDE_POSITION: 5,
        CONF_DEFAULT_TILT: 50,
        CONF_WEATHER_BYPASS_AUTO_CONTROL: False,
    }

    snapshot = builder.build(
        opts,
        cover_data=cover_data,
        cover_type="cover_tilt",
        climate_readings=None,
        manual_override_active=True,
        motion_timeout_active=True,
        weather_override_active=True,
        in_time_window=False,
        current_cover_position=37,
        is_glare_zone_enabled=lambda idx: False,
        effective_default=10,
        is_sunset_active=True,
    )
    assert snapshot.default_position == 10
    assert snapshot.is_sunset_active is True
    assert snapshot.force_override_position == 95
    assert snapshot.weather_override_position == 5
    assert snapshot.weather_bypass_auto_control is False
    assert snapshot.manual_override_active is True
    assert snapshot.motion_timeout_active is True
    assert snapshot.weather_override_active is True
    assert snapshot.in_time_window is False
    assert snapshot.current_cover_position == 37
    assert snapshot.climate_mode_enabled is True
    assert snapshot.motion_control_enabled is True
    assert snapshot.default_tilt == 50
    assert snapshot.cover_type == "cover_tilt"


@pytest.mark.unit
def test_build_consults_is_glare_zone_enabled_callable():
    """Per-zone master switch is read via the callable, not via getattr on coord."""
    builder, _, _ = _make_builder()

    zone_a = MagicMock()
    zone_a.name = "zone_a"
    zone_b = MagicMock()
    zone_b.name = "zone_b"
    glare_cfg = MagicMock()
    glare_cfg.zones = [zone_a, zone_b]
    builder._policy.glare_zones_config.return_value = glare_cfg

    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()

    snapshot = builder.build(
        {},
        cover_data=cover_data,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: idx == 0,
        effective_default=0,
        is_sunset_active=False,
    )
    assert snapshot.active_zone_names == frozenset({"zone_a"})


@pytest.mark.unit
def test_read_climate_use_lux_inferred_from_cloud_suppression():
    """Phase D preserves the Issue #268 cloud-suppression override."""
    builder, climate_provider, _ = _make_builder(lux_toggle=None)
    opts = {
        CONF_CLOUD_SUPPRESSION: True,
        CONF_LUX_ENTITY: "sensor.lux",
    }
    builder.read_climate(opts)
    _, kwargs = climate_provider.read.call_args
    assert kwargs["use_lux"] is True
