"""Direct tests for :class:`PipelineSnapshotBuilder`.

The pre-existing climate-wiring tests (``tests/test_coordinator_climate_wiring``)
also exercise the builder through coordinator shims to preserve their original
intent.  These tests are the public-API contract tests that don't pretend to
involve a coordinator at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_LUX_ENTITY,
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TRACKING_SEASONS,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WINTER_CLOSE_INSULATION,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_TRACKING_SEASONS,
    AxisConstraintMode,
    TrackingSeason,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    ClimateTempFlags,
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


# ---------------------------------------------------------------------------
# Multi-sensor OR / legacy fallback / template trigger (issue #563)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_custom_position_sensors_multi_sensor_or():
    """The `sensors` list key reads every sensor; OR logic drives is_on."""
    on_state = MagicMock()
    on_state.state = "on"
    off_state = MagicMock()
    off_state.state = "off"
    builder, _, hass = _make_builder(
        states={
            "binary_sensor.alarm": on_state,
            "binary_sensor.calm": off_state,
        }
    )

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensors"]: ["binary_sensor.alarm", "binary_sensor.calm"],
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    state = out[0]
    assert state.entity_ids == ("binary_sensor.alarm", "binary_sensor.calm")
    assert state.is_on is True  # OR: one sensor on suffices
    assert state.active_entity_ids == ("binary_sensor.alarm",)
    # One hass.states.get call per bound sensor.
    read_entities = {c.args[0] for c in hass.states.get.call_args_list}
    assert {"binary_sensor.alarm", "binary_sensor.calm"} <= read_entities


@pytest.mark.unit
def test_read_custom_position_sensors_multi_sensor_all_off():
    """All sensors off (or missing) → is_on False, no active entity ids."""
    builder, _, _ = _make_builder(states={})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensors"]: ["binary_sensor.ghost", "binary_sensor.gone"],
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    assert out[0].is_on is False
    assert out[0].active_entity_ids == ()


@pytest.mark.unit
def test_read_custom_position_sensors_legacy_single_key_fallback():
    """The legacy single-sensor key still works when the list key is absent."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.legacy": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.legacy",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    assert out[0].entity_ids == ("binary_sensor.legacy",)
    assert out[0].is_on is True
    assert out[0].active_entity_ids == ("binary_sensor.legacy",)


@pytest.mark.unit
def test_read_custom_position_sensors_template_only_slot():
    """A slot with only a condition template (no sensors) is a valid trigger."""
    builder, _, _ = _make_builder()

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["template"]: "{{ is_state('sun.sun', 'above_horizon') }}",
        first_slot_keys["position"]: 42,
    }
    # render_condition needs a working hass; mock it at the builder's import site.
    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder.render_condition",
        return_value=True,
    ):
        out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    state = out[0]
    assert state.entity_ids == ()
    assert state.is_on is True
    assert state.template_active is True
    assert state.active_entity_ids == ()
    assert state.sensor_name is None


@pytest.mark.unit
def test_read_custom_position_sensors_template_false_keeps_slot_off():
    """A False-rendering template leaves a template-only slot inactive."""
    builder, _, _ = _make_builder()

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["template"]: "{{ is_state('sun.sun', 'above_horizon') }}",
        first_slot_keys["position"]: 42,
    }
    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder.render_condition",
        return_value=False,
    ):
        out = builder.read_custom_position_sensors(opts)
    assert len(out) == 1
    assert out[0].is_on is False
    assert out[0].template_active is False


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
        CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR: True,
        CONF_CLOUDY_POSITION: 30,
        CONF_TRACKING_SEASONS: [TrackingSeason.SUMMER.value],
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
    assert out.summer_close_bypass_sun_floor is True
    assert out.cloudy_position == 30
    # A populated list is honoured literally.
    assert out.tracking_seasons == frozenset({TrackingSeason.SUMMER.value})


@pytest.mark.unit
def test_read_climate_forwards_condition_templates():
    """is_sunny / presence templates + modes thread into climate_provider.read (#639)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_IS_SUNNY_TEMPLATE,
        CONF_IS_SUNNY_TEMPLATE_MODE,
        CONF_PRESENCE_TEMPLATE,
        CONF_PRESENCE_TEMPLATE_MODE,
    )

    builder, climate_provider, _ = _make_builder()
    opts = {
        CONF_IS_SUNNY_TEMPLATE: "{{ true }}",
        CONF_IS_SUNNY_TEMPLATE_MODE: "and",
        CONF_PRESENCE_TEMPLATE: "{{ false }}",
        CONF_PRESENCE_TEMPLATE_MODE: "and",
    }
    builder.read_climate(opts)
    kwargs = climate_provider.read.call_args.kwargs
    assert kwargs["is_sunny_template"] == "{{ true }}"
    assert kwargs["is_sunny_template_mode"] == "and"
    assert kwargs["presence_template"] == "{{ false }}"
    assert kwargs["presence_template_mode"] == "and"


@pytest.mark.unit
def test_read_climate_template_modes_default_to_or():
    """Absent template-mode keys default to OR (#639)."""
    builder, climate_provider, _ = _make_builder()
    builder.read_climate({})
    kwargs = climate_provider.read.call_args.kwargs
    assert kwargs["is_sunny_template"] is None
    assert kwargs["is_sunny_template_mode"] == "or"
    assert kwargs["presence_template"] is None
    assert kwargs["presence_template_mode"] == "or"


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
    assert out.summer_close_bypass_sun_floor is False
    assert out.cloudy_position is None
    # Absent key → all seasons (backward-compatible "track always").
    assert out.tracking_seasons == frozenset(DEFAULT_TRACKING_SEASONS)


@pytest.mark.unit
def test_build_climate_options_tracking_seasons_none_defaults_to_all():
    """An explicit None (e.g. a cleared option) is treated like an absent key."""
    builder, _, _ = _make_builder()
    out = builder.build_climate_options({CONF_TRACKING_SEASONS: None})
    assert out.tracking_seasons == frozenset(DEFAULT_TRACKING_SEASONS)


@pytest.mark.unit
def test_build_climate_options_tracking_seasons_empty_means_never_track():
    """An explicit empty list is honoured literally: glare tracking never runs."""
    builder, _, _ = _make_builder()
    out = builder.build_climate_options({CONF_TRACKING_SEASONS: []})
    assert out.tracking_seasons == frozenset()


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
    assert state.entity_ids == ("binary_sensor.guest",)
    assert state.is_on is True
    assert state.active_entity_ids == ("binary_sensor.guest",)
    assert state.template_active is None  # no template configured
    assert state.position == 42
    assert state.priority == DEFAULT_CUSTOM_POSITION_PRIORITY
    assert state.min_mode is False
    assert state.use_my is False
    assert state.tilt is None
    assert state.slot == 1


@pytest.mark.unit
def test_read_custom_position_sensors_reads_tilt_only():
    """tilt_only flag is read from options into the sensor state (issue #514)."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
        first_slot_keys["tilt"]: 30,
        first_slot_keys["tilt_only"]: True,
    }
    out = builder.read_custom_position_sensors(opts)
    assert out[0].tilt_only is True


@pytest.mark.unit
def test_read_custom_position_sensors_tilt_only_normalizes_min_mode_use_my():
    """tilt_only wins: min_mode and use_my are forced False (decision Q3)."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
        first_slot_keys["tilt"]: 30,
        first_slot_keys["tilt_only"]: True,
        first_slot_keys["min_mode"]: True,
        first_slot_keys["use_my"]: True,
    }
    out = builder.read_custom_position_sensors(opts)
    state = out[0]
    assert state.tilt_only is True
    assert state.min_mode is False
    assert state.use_my is False


@pytest.mark.unit
def test_read_custom_position_sensors_tilt_only_defaults_false():
    """tilt_only defaults to False when the option is absent."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert out[0].tilt_only is False


@pytest.mark.unit
def test_read_custom_position_sensors_unconfigured_returns_empty():
    builder, _, _ = _make_builder()
    assert builder.read_custom_position_sensors({}) == []


@pytest.mark.unit
def test_read_custom_position_sensors_carries_friendly_name():
    """sensor_name is populated from the bound sensor's friendly_name attribute.

    Surfaces the human label of the sensor that triggered a slot so that
    downstream diagnostics (decision_trace, companion card badge) can show
    "Custom · Table extension" instead of just "Custom #1".
    """
    on_state = MagicMock()
    on_state.state = "on"
    on_state.attributes = {"friendly_name": "Table extension"}
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert out[0].sensor_name == "Table extension"


@pytest.mark.unit
def test_read_custom_position_sensors_sensor_name_none_when_state_missing():
    """sensor_name is None when the bound sensor isn't in hass.states."""
    builder, _, _ = _make_builder()  # no states map → hass.states.get returns None

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert out[0].sensor_name is None


@pytest.mark.unit
def test_read_custom_position_sensors_sensor_name_none_when_no_friendly_name_attr():
    """sensor_name is None when the bound sensor has no friendly_name attribute."""
    on_state = MagicMock()
    on_state.state = "on"
    on_state.attributes = {}  # no friendly_name key
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
    }
    out = builder.read_custom_position_sensors(opts)
    assert out[0].sensor_name is None


@pytest.mark.unit
def test_read_custom_position_sensors_skips_slots_with_enabled_false():
    """Disabled slots are omitted from the snapshot entirely.

    A slot with sensor + position configured but `enabled=False` must not
    appear in the snapshot, so its CustomPositionHandler can never claim
    position even if the bound sensor goes on.
    """
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
        first_slot_keys["enabled"]: False,
    }
    assert builder.read_custom_position_sensors(opts) == []


@pytest.mark.unit
def test_read_custom_position_sensors_defaults_enabled_true_when_key_absent():
    """A slot configured before the enabled key existed behaves as enabled."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.guest": on_state})

    first_slot_keys = next(iter(CUSTOM_POSITION_SLOTS.values()))
    opts = {
        first_slot_keys["sensor"]: "binary_sensor.guest",
        first_slot_keys["position"]: 42,
        # no `enabled` key — pre-feature options
    }
    assert len(builder.read_custom_position_sensors(opts)) == 1


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
def test_build_threads_climate_temp_flags():
    """build() threads climate_temp_flags onto the snapshot (issue #917)."""
    builder, _, _ = _make_builder()
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    flags = ClimateTempFlags(
        winter=True, summer_warm=False, outside_high=True, extreme_heat=False
    )
    snapshot = builder.build(
        {CONF_DEFAULT_HEIGHT: 0},
        cover_data=cover_data,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: True,
        effective_default=0,
        is_sunset_active=False,
        climate_temp_flags=flags,
    )
    assert snapshot.climate_temp_flags is flags


@pytest.mark.unit
def test_build_climate_temp_flags_default_none():
    """Omitting climate_temp_flags leaves the snapshot field None (back-compat)."""
    builder, _, _ = _make_builder()
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    snapshot = builder.build(
        {CONF_DEFAULT_HEIGHT: 0},
        cover_data=cover_data,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: True,
        effective_default=0,
        is_sunset_active=False,
    )
    assert snapshot.climate_temp_flags is None


@pytest.mark.unit
def test_build_forwards_explicit_effective_default():
    builder, _, _ = _make_builder(switch_mode=True, motion_control=True)
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    opts = {
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
def test_build_reads_tilt_limits_and_sun_only_toggles():
    """max_tilt / *_sun_only options flow onto the snapshot (issue #503)."""
    builder, _, _ = _make_builder()
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()

    snapshot = builder.build(
        {CONF_MAX_TILT: 60, CONF_MAX_TILT_SUN_ONLY: True},
        cover_data=cover_data,
        cover_type="cover_tilt",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: False,
        effective_default=0,
        is_sunset_active=False,
    )
    assert snapshot.max_tilt == 60
    assert snapshot.max_tilt_sun_only is True
    # Absent keys fall back to no-op defaults.
    assert snapshot.min_tilt == 0
    assert snapshot.min_tilt_sun_only is False


@pytest.mark.unit
def test_build_tilt_limits_default_when_options_absent():
    """No tilt options → snapshot uses no-op defaults (100 / 0 / False)."""
    builder, _, _ = _make_builder()
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()

    snapshot = builder.build(
        {},
        cover_data=cover_data,
        cover_type="cover_tilt",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: False,
        effective_default=0,
        is_sunset_active=False,
    )
    assert snapshot.max_tilt == 100
    assert snapshot.min_tilt == 0
    assert snapshot.max_tilt_sun_only is False
    assert snapshot.min_tilt_sun_only is False


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


# ---------------------------------------------------------------------------
# solar_floor_active rollup (#569)
# ---------------------------------------------------------------------------


def _caps(*, has_set_position: bool):
    from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities

    return CoverCapabilities(
        has_set_position=has_set_position,
        has_set_tilt_position=False,
        has_open=True,
        has_close=True,
    )


def _build_with_caps(builder, caps_map):
    """Run ``builder.build`` with a given cover_capabilities map.

    Wires ``policy.position_axis_supported`` to read ``has_set_position`` so
    the rollup is exercised against realistic per-entity capability data.
    """
    builder._policy.position_axis_supported.side_effect = lambda c: c.has_set_position
    cover_data = MagicMock()
    cover_data.config = MagicMock()
    cover_data.sun_data = MagicMock()
    return builder.build(
        {},
        cover_data=cover_data,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: False,
        effective_default=0,
        is_sunset_active=False,
        cover_capabilities=caps_map,
    )


@pytest.mark.unit
def test_solar_floor_inactive_when_all_entities_positionable():
    """All bound entities support set_position → floor off (reaches 0%)."""
    builder, _, _ = _make_builder()
    snap = _build_with_caps(
        builder,
        {
            "cover.a": _caps(has_set_position=True),
            "cover.b": _caps(has_set_position=True),
        },
    )
    assert snap.solar_floor_active is False


@pytest.mark.unit
def test_solar_floor_active_when_any_entity_open_close_only():
    """A single open/close-only entity keeps the floor active (conservative)."""
    builder, _, _ = _make_builder()
    snap = _build_with_caps(
        builder,
        {
            "cover.a": _caps(has_set_position=True),
            "cover.b": _caps(has_set_position=False),
        },
    )
    assert snap.solar_floor_active is True


@pytest.mark.unit
def test_solar_floor_active_when_caps_empty():
    """Empty caps map → floor active (no positive evidence of positionability)."""
    builder, _, _ = _make_builder()
    snap = _build_with_caps(builder, {})
    assert snap.solar_floor_active is True


@pytest.mark.unit
def test_solar_floor_active_when_caps_none():
    """None caps (entities not readable) → floor active."""
    builder, _, _ = _make_builder()
    snap = _build_with_caps(builder, None)
    assert snap.solar_floor_active is True


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


# ---------------------------------------------------------------------------
# Axis-constraint derivation — issue #943
#
# The stored wire format stays the min_mode / tilt_only booleans plus the
# optional numeric constraint keys; the per-axis mode is DERIVED here, at the
# one normalization site, and never persisted.
# ---------------------------------------------------------------------------


def _constraint_builder(slot_keys: dict, opts: dict):
    """Read one slot whose trigger sensor is on, and return its state."""
    on_state = MagicMock()
    on_state.state = "on"
    builder, _, _ = _make_builder(states={"binary_sensor.trigger": on_state})
    merged = {slot_keys["sensors"]: ["binary_sensor.trigger"], **opts}
    out = builder.read_custom_position_sensors(merged)
    assert len(out) == 1
    return out[0]


@pytest.mark.unit
def test_constraint_keys_land_on_state():
    """position_max / tilt_min / tilt_max are carried onto the slot state."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {
            keys["position"]: 30,
            keys["position_max"]: 70,
            keys["tilt_min"]: 40,
            keys["tilt_max"]: 80,
        },
    )
    assert state.position_max == 70
    assert state.tilt_min == 40
    assert state.tilt_max == 80


@pytest.mark.unit
def test_constraint_keys_default_to_none():
    """A legacy slot carries no constraints — every new field reads None."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30})
    assert state.position_max is None
    assert state.tilt_min is None
    assert state.tilt_max is None


# --- Position axis ---------------------------------------------------------


@pytest.mark.unit
def test_position_mode_fixed_for_legacy_exact_slot():
    """A plain position slot claims the position axis exactly — parity."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30})
    assert state.position_mode is AxisConstraintMode.FIXED


@pytest.mark.unit
def test_position_mode_min_for_legacy_min_mode_slot():
    """min_mode reads as a position floor — parity with today's floor pass."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30, keys["min_mode"]: True})
    assert state.position_mode is AxisConstraintMode.MIN


@pytest.mark.unit
def test_position_mode_range_for_min_mode_plus_position_max():
    """min_mode + position_max is a two-sided position bound."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {keys["position"]: 30, keys["min_mode"]: True, keys["position_max"]: 70},
    )
    assert state.position_mode is AxisConstraintMode.RANGE


@pytest.mark.unit
def test_position_mode_max_for_position_max_only():
    """position_max alone defers the position and clamps it down."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position_max"]: 70})
    assert state.position_mode is AxisConstraintMode.MAX


@pytest.mark.unit
def test_position_mode_none_for_tilt_only_slot():
    """tilt_only makes no position claim — parity with today's deferral."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {keys["position"]: 30, keys["tilt"]: 50, keys["tilt_only"]: True},
    )
    assert state.position_mode is AxisConstraintMode.NONE


@pytest.mark.unit
def test_position_mode_none_when_no_position_claim():
    """Trigger + tilt_min only: the slot is present but claims no position."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["tilt_min"]: 50})
    assert state.position_mode is AxisConstraintMode.NONE
    assert state.tilt_mode is AxisConstraintMode.MIN


@pytest.mark.unit
def test_use_my_keeps_my_path_and_drops_position_max():
    """The My path is hardware-pinned — orthogonal to the constraint model."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {keys["position"]: 30, keys["use_my"]: True, keys["position_max"]: 70},
    )
    assert state.use_my is True
    assert state.position_max is None


@pytest.mark.unit
def test_tilt_only_normalizes_position_max_off():
    """tilt_only wins: the slot's position-axis constraints are dropped."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {keys["tilt"]: 50, keys["tilt_only"]: True, keys["position_max"]: 70},
    )
    assert state.position_max is None


# --- Tilt axis -------------------------------------------------------------


@pytest.mark.unit
def test_tilt_mode_none_for_legacy_slot():
    """A slot with no tilt configuration claims nothing on the tilt axis."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30})
    assert state.tilt_mode is AxisConstraintMode.NONE


@pytest.mark.unit
def test_tilt_mode_fixed_for_tilt_only_slot():
    """tilt_only + a tilt value is an exact tilt claim — parity with #514.

    A tilt-only slot still stores a position (the pre-#943 gate requires one);
    tilt_only is what makes the slot ignore it on the position axis.
    """
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys, {keys["position"]: 30, keys["tilt"]: 50, keys["tilt_only"]: True}
    )
    assert state.tilt_mode is AxisConstraintMode.FIXED


@pytest.mark.unit
def test_tilt_mode_none_for_tilt_only_without_tilt_value():
    """A tilt-only slot with no slat angle contributes nothing — parity."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30, keys["tilt_only"]: True})
    assert state.tilt_mode is AxisConstraintMode.NONE


@pytest.mark.unit
def test_tilt_mode_min_for_tilt_min_only():
    """The reporter's ask: a minimum tilt boundary."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30, keys["tilt_min"]: 50})
    assert state.tilt_mode is AxisConstraintMode.MIN


@pytest.mark.unit
def test_tilt_mode_max_for_tilt_max_only():
    """A maximum tilt boundary."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(keys, {keys["position"]: 30, keys["tilt_max"]: 60})
    assert state.tilt_mode is AxisConstraintMode.MAX


@pytest.mark.unit
def test_tilt_mode_range_for_both_tilt_bounds():
    """Both bounds set is a two-sided tilt range."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys, {keys["position"]: 30, keys["tilt_min"]: 40, keys["tilt_max"]: 80}
    )
    assert state.tilt_mode is AxisConstraintMode.RANGE


@pytest.mark.unit
def test_tilt_only_wins_over_tilt_bounds():
    """FIXED beats MIN/MAX on the tilt axis, mirroring tilt_only's precedence."""
    keys = CUSTOM_POSITION_SLOTS[1]
    state = _constraint_builder(
        keys,
        {
            keys["tilt"]: 50,
            keys["tilt_only"]: True,
            keys["tilt_min"]: 20,
            keys["tilt_max"]: 80,
        },
    )
    assert state.tilt_mode is AxisConstraintMode.FIXED
    assert state.tilt_min is None
    assert state.tilt_max is None
