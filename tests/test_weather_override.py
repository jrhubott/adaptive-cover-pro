"""Tests for weather-based override feature (coordinator integration)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.weather import WeatherManager


def _make_coordinator_with_weather_mgr(
    *,
    wind_speed_sensor=None,
    wind_speed_threshold=50.0,
    timeout_seconds=300,
):
    """Create a MagicMock coordinator with a real WeatherManager pre-configured."""
    hass = MagicMock()
    logger = MagicMock()

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.logger = logger

    mgr = WeatherManager(hass=hass, logger=logger)
    mgr.update_config(
        wind_speed_sensor=wind_speed_sensor,
        wind_direction_sensor=None,
        wind_speed_threshold=wind_speed_threshold,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=timeout_seconds,
    )
    coordinator._weather_mgr = mgr

    return coordinator, hass


# --- is_weather_override_active property ---


def test_is_weather_override_active_no_sensors():
    """Returns False when no weather sensors configured."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr()
    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is False


def test_is_weather_override_active_delegates_to_manager():
    """is_weather_override_active delegates to WeatherManager."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    coordinator._weather_mgr.record_conditions_active()

    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is True


def test_is_weather_override_active_false_when_flag_not_set():
    """Returns False when sensors configured but no conditions active."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    # Fresh manager — override-active flag defaults to False.

    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is False


# --- async_check_weather_state_change ---


@pytest.mark.asyncio
async def test_weather_state_change_activates_on_condition_met():
    """State change handler activates override and refreshes when conditions met."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )

    # Wind speed above threshold
    hass.states.get.return_value = MagicMock(state="75.0")

    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False
    coordinator._start_weather_timeout = MagicMock()

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": MagicMock(state="75.0"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    assert coordinator._weather_mgr.is_weather_override_active is True
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_starts_timeout_when_cleared():
    """State change handler starts clear-delay timeout when all conditions clear."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )

    # Wind speed dropped below threshold — conditions cleared
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._weather_mgr.record_conditions_active()  # was active

    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False
    coordinator._start_weather_timeout = MagicMock()
    # Bind real reconcile so _start_weather_timeout is called transitively
    coordinator._reconcile_weather_override = (
        lambda: AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)
    )

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": MagicMock(state="10.0"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    coordinator._start_weather_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_ignores_none_new_state():
    """State change handler ignores events with no new_state (entity removed)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    coordinator.async_refresh = AsyncMock()
    coordinator._start_weather_timeout = MagicMock()

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": None,
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    coordinator.async_refresh.assert_not_called()
    coordinator._start_weather_timeout.assert_not_called()


# --- _reconcile_weather_override ---


def test_reconcile_weather_override_starts_timer_when_flag_stuck(hass=None):
    """G1: override active, conditions clear, no timer → starts timeout."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # below threshold
    coordinator._weather_mgr.record_conditions_active()
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_called_once()


def test_reconcile_weather_override_noop_when_conditions_active():
    """G2: override active, conditions still active → no timer started."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="75.0")  # above threshold
    coordinator._weather_mgr.record_conditions_active()
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_not_called()


def test_reconcile_weather_override_noop_when_flag_false():
    """G3: override flag False, conditions clear → no timer started."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")
    # Fresh manager — override-active defaults to False.
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_weather_override_noop_when_timer_running():
    """G4: override active, conditions clear, timer already running → no second timer."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._weather_mgr.record_conditions_active()
    coordinator._weather_mgr.start_weather_timeout(AsyncMock())
    coordinator._start_weather_timeout = MagicMock()
    try:
        AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)
        coordinator._start_weather_timeout.assert_not_called()
    finally:
        coordinator._weather_mgr.cancel_weather_timeout()


# --- _recover_weather_override_on_restart ---


def test_recover_on_restart_sets_flag_when_conditions_active():
    """G5: on first refresh, conditions active → restores override-active state."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="75.0")  # above threshold
    # Fresh manager — post-restart state is "flag reset to False".

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr.is_weather_override_active is True


def test_recover_on_restart_noop_when_conditions_clear():
    """G6: on first refresh, conditions clear → flag stays False."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # below threshold

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr.is_weather_override_active is False


def test_recover_on_restart_noop_when_no_sensors_configured():
    """No sensors configured → noop, no state reads."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr()  # no wind sensor

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr.is_weather_override_active is False
    hass.states.get.assert_not_called()


# --- end-to-end: restart race then conditions clear ---


@pytest.mark.asyncio
async def test_restart_race_then_conditions_clear_starts_timer():
    """Regression for #255: after restart recovery, clearing conditions starts timer."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )
    # 1. Conditions active on startup (simulates HA restart with wind still up)
    hass.states.get.return_value = MagicMock(state="75.0")
    # Fresh manager — post-restart state is "flag reset to False".

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)
    assert (
        coordinator._weather_mgr.is_weather_override_active is True
    )  # recovery worked

    # 2. Wind drops; state-change event fires
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._start_weather_timeout = MagicMock()
    # Bind real reconcile so _start_weather_timeout is called transitively
    coordinator._reconcile_weather_override = (
        lambda: AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)
    )

    from homeassistant.core import Event

    event = MagicMock(spec=Event)
    event.data = {"entity_id": "sensor.wind", "new_state": MagicMock(state="10.0")}

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    # Without the restart recovery fix, the override-active flag would be False
    # here and the else-branch would short-circuit without starting the timer.
    coordinator._start_weather_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_cleared_does_not_restart_running_timer():
    """Regression: a cleared event does not restart a timer that's already running."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from homeassistant.core import Event

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # conditions clear
    coordinator._weather_mgr.record_conditions_active()
    coordinator._weather_mgr.start_weather_timeout(AsyncMock())
    coordinator._start_weather_timeout = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False

    try:
        event = MagicMock(spec=Event)
        event.data = {
            "entity_id": "sensor.wind",
            "new_state": MagicMock(state="10.0"),
        }
        await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
            coordinator, event
        )
        coordinator._start_weather_timeout.assert_not_called()
    finally:
        coordinator._weather_mgr.cancel_weather_timeout()


# --- first-refresh ordering: recovery before pipeline ---


@pytest.mark.asyncio
async def test_recover_on_restart_called_before_calculate_on_first_refresh():
    """Regression: on first_refresh, _recover_weather_override_on_restart must be
    called from within _async_update_data BEFORE _calculate_cover_state so the
    pipeline snapshot sees weather_override_active=True on the very first cycle.
    Without the fix, recovery only ran inside async_handle_first_refresh (too late).
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )
    hass.states.get.return_value = (
        None  # state_attr returns None → azimuth/elevation 0.0
    )
    coordinator.first_refresh = True
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.state_change = False
    coordinator.cover_state_change = False
    coordinator.manager.manual_controlled = []
    coordinator.manager.reset_if_needed = AsyncMock(return_value=False)
    coordinator.async_handle_first_refresh = AsyncMock()
    coordinator._update_solar_times_if_needed = AsyncMock(return_value=(None, None))
    coordinator._pipeline_result = MagicMock()
    # Dispatch is exercised by its own tests; stub the extracted step so the
    # update cycle stays awaitable here (issue #756 method extraction).
    coordinator._dispatch_for_cycle = AsyncMock()
    hass.async_add_executor_job = (
        AsyncMock()
    )  # issue #655: prime_cache offloaded to executor

    call_order: list[str] = []
    original_recover = (
        AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart
    )

    def spy_recover():
        call_order.append("recover")
        original_recover(coordinator)

    def spy_calculate(cover_data, options):
        call_order.append("calculate")
        return 50

    coordinator._recover_weather_override_on_restart = spy_recover
    coordinator._calculate_cover_state = spy_calculate

    await AdaptiveDataUpdateCoordinator._async_update_data(coordinator)

    assert (
        "recover" in call_order
    ), "_recover_weather_override_on_restart must be called from _async_update_data"
    assert call_order.index("recover") < call_order.index(
        "calculate"
    ), "Recovery must run before _calculate_cover_state so pipeline sees restored flag"


@pytest.mark.asyncio
async def test_recover_on_restart_not_called_on_subsequent_refresh():
    """Recovery is gated on first_refresh=True; must not run on non-first updates."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )
    hass.states.get.return_value = None
    coordinator.first_refresh = False
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.state_change = False
    coordinator.cover_state_change = False
    coordinator.manager.manual_controlled = []
    coordinator.manager.reset_if_needed = AsyncMock(return_value=False)
    coordinator._update_solar_times_if_needed = AsyncMock(return_value=(None, None))
    coordinator._pipeline_result = MagicMock()
    # Dispatch is exercised by its own tests; stub the extracted step so the
    # update cycle stays awaitable here (issue #756 method extraction).
    coordinator._dispatch_for_cycle = AsyncMock()
    hass.async_add_executor_job = (
        AsyncMock()
    )  # issue #655: prime_cache offloaded to executor

    call_order: list[str] = []

    def spy_recover():
        call_order.append("recover")

    def spy_calculate(cover_data, options):
        call_order.append("calculate")
        return 50

    coordinator._recover_weather_override_on_restart = spy_recover
    coordinator._calculate_cover_state = spy_calculate

    await AdaptiveDataUpdateCoordinator._async_update_data(coordinator)

    assert (
        "recover" not in call_order
    ), "_recover_weather_override_on_restart must NOT run when first_refresh=False"


# --- Addition 1: severe-weather condition template (issue #974) ---


def _make_severe_mgr(
    hass,
    *,
    severe_sensors=None,
    severe_template=None,
    severe_template_mode="or",
    enabled=True,
):
    """Build a real WeatherManager with only the severe path configured."""
    mgr = WeatherManager(hass=hass, logger=MagicMock())
    mgr.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=severe_sensors or [],
        timeout_seconds=300,
        severe_template=severe_template,
        severe_template_mode=severe_template_mode,
        enabled=enabled,
    )
    return mgr


@pytest.mark.asyncio
async def test_severe_template_only_activates(hass):
    """A truthy severe template with no companion sensor engages the override."""
    mgr = _make_severe_mgr(hass, severe_template="{{ true }}")
    assert mgr.is_any_condition_active is True
    assert "severe_weather" in mgr.active_conditions


@pytest.mark.asyncio
async def test_severe_template_and_mode_gates_on_sensor(hass):
    """AND mode: truthy template alone is inactive; a live sensor unlocks it."""
    hass.states.async_set("binary_sensor.hail", "off")
    await hass.async_block_till_done()
    mgr = _make_severe_mgr(
        hass,
        severe_sensors=["binary_sensor.hail"],
        severe_template="{{ true }}",
        severe_template_mode="and",
    )
    # Template truthy + all sensors off → AND gate blocks.
    assert mgr.is_any_condition_active is False

    hass.states.async_set("binary_sensor.hail", "on")
    await hass.async_block_till_done()
    # Template truthy + one sensor on → AND satisfied.
    assert mgr.is_any_condition_active is True


@pytest.mark.asyncio
async def test_severe_broken_template_falls_open_to_sensors(hass):
    """A broken template gives no opinion; the sensor list alone decides."""
    hass.states.async_set("binary_sensor.hail", "on")
    await hass.async_block_till_done()
    mgr = _make_severe_mgr(
        hass,
        severe_sensors=["binary_sensor.hail"],
        severe_template="{{ 1 / 0 }}",
    )
    # Broken template → no opinion → the "on" sensor drives the result.
    assert mgr.is_any_condition_active is True

    hass.states.async_set("binary_sensor.hail", "off")
    await hass.async_block_till_done()
    assert mgr.is_any_condition_active is False


@pytest.mark.asyncio
async def test_severe_template_in_condition_templates(hass):
    """The severe template joins condition_templates for live tracking."""
    mgr = _make_severe_mgr(hass, severe_template="{{ is_state('x', 'on') }}")
    assert "{{ is_state('x', 'on') }}" in mgr.condition_templates


@pytest.mark.asyncio
async def test_severe_template_only_is_feature_configured(hass):
    """A template-only severe source counts as configured when enabled."""
    mgr = _make_severe_mgr(hass, severe_template="{{ true }}")
    assert mgr.is_feature_configured is True
    # Disabled master toggle overrides even a configured template source.
    mgr_off = _make_severe_mgr(hass, severe_template="{{ true }}", enabled=False)
    assert mgr_off.is_feature_configured is False


# --- weather_override_schema: conditional retraction-picker inclusion ---


_RETRACTION_PICKER_KEYS = (
    "weather_wind_speed_sensor",
    "weather_wind_direction_sensor",
    "weather_rain_sensor",
    "weather_is_raining_sensor",
    "weather_is_raining_template",
    "weather_is_windy_sensor",
    "weather_is_windy_template",
    "weather_severe_template",
    "weather_severe_sensors",
)

_ALWAYS_PRESENT_KEYS = (
    "weather_wind_speed_threshold",
    "weather_override_position",
    "weather_timeout",
)


def _schema_keys(schema):
    return {str(marker.schema) for marker in schema.schema}


def test_schema_always_includes_retraction_pickers():
    """The retraction pickers are unconditionally present (pre-PR-700 behavior),
    and the removed ``show_weather_retraction`` toggle is gone.
    """
    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )

    keys = _schema_keys(weather_override_schema())
    for picker in _RETRACTION_PICKER_KEYS:
        assert picker in keys, picker
    for always in _ALWAYS_PRESENT_KEYS:
        assert always in keys, always
    assert "show_weather_retraction" not in keys


# --- Addition 1 (Step 3): severe template schema / specs / sync wiring (#974) ---


def test_severe_template_selectors_in_weather_schema():
    """The severe template gets a TemplateSelector + combine-mode SelectSelector."""
    from homeassistant.helpers import selector

    from custom_components.adaptive_cover_pro.config_dynamic import (
        weather_override_schema,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_SEVERE_TEMPLATE,
        CONF_WEATHER_SEVERE_TEMPLATE_MODE,
    )

    schema = weather_override_schema().schema
    by_key = {str(marker.schema): marker for marker in schema}
    assert CONF_WEATHER_SEVERE_TEMPLATE in by_key
    assert CONF_WEATHER_SEVERE_TEMPLATE_MODE in by_key
    assert isinstance(
        schema[by_key[CONF_WEATHER_SEVERE_TEMPLATE]], selector.TemplateSelector
    )
    assert isinstance(
        schema[by_key[CONF_WEATHER_SEVERE_TEMPLATE_MODE]], selector.SelectSelector
    )


def test_severe_template_in_optional_keys():
    """The clearable template key is stripped on a clear (issue #323 pattern)."""
    from custom_components.adaptive_cover_pro import config_flow as cf
    from custom_components.adaptive_cover_pro.const import CONF_WEATHER_SEVERE_TEMPLATE

    assert CONF_WEATHER_SEVERE_TEMPLATE in cf._WEATHER_OVERRIDE_OPTIONAL_KEYS


def test_severe_template_keys_in_sensor_key_set():
    """Both severe template keys join the profile-owned sensor key set."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_SEVERE_TEMPLATE,
        CONF_WEATHER_SEVERE_TEMPLATE_MODE,
        WEATHER_OVERRIDE_SENSOR_KEYS,
    )

    assert CONF_WEATHER_SEVERE_TEMPLATE in WEATHER_OVERRIDE_SENSOR_KEYS
    assert CONF_WEATHER_SEVERE_TEMPLATE_MODE in WEATHER_OVERRIDE_SENSOR_KEYS


def test_severe_template_in_building_profile_schema():
    """The Building Profile sensor schema exposes the severe template + mode."""
    from custom_components.adaptive_cover_pro.config_dynamic import (
        building_profile_sensors_schema,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_SEVERE_TEMPLATE,
        CONF_WEATHER_SEVERE_TEMPLATE_MODE,
    )

    keys = _schema_keys(building_profile_sensors_schema())
    assert CONF_WEATHER_SEVERE_TEMPLATE in keys
    assert CONF_WEATHER_SEVERE_TEMPLATE_MODE in keys
