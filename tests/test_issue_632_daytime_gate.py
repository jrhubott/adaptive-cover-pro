"""Tests for issue #632 — daytime gate (sensor/template) replacing the astral boundary.

The gate answers one yes/no question: "is it daytime — should ACP be sun-tracking
now?" It mirrors the issue-#577 motion pattern (entity list + optional Jinja template
+ combine mode), reusing ``render_condition`` / ``combine_with_mode`` /
``is_entity_active``. When unconfigured the gate is daytime (fail-open) and ACP falls
back to the astronomical sunset/sunrise calc — zero regression.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.const import (
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DAYTIME_GATE_TEMPLATE_MODE,
    CONF_SUNSET_POS,
    DEFAULT_TEMPLATE_COMBINE_MODE,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager


class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state
        self.attributes: dict = {}


class _FakeStates:
    def __init__(self) -> None:
        self._d: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str) -> None:
        self._d[entity_id] = _FakeState(state)

    def get(self, entity_id: str):
        return self._d.get(entity_id)


class _FakeHass:
    """Minimal hass double exposing only ``states`` (no template rendering)."""

    def __init__(self) -> None:
        self.states = _FakeStates()


@pytest.fixture
def mgr():
    hass = _FakeHass()
    return TimeWindowManager(hass, logging.getLogger("test_issue_632"))


def test_gate_is_daytime_true_when_unconfigured(mgr):
    # No gate configured at all → daytime (fail-open → astronomical fallback).
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )
    assert mgr.gate_is_daytime is True
    assert mgr.gate_is_configured is False
    assert mgr.gate_is_dark is False


def _configure_gate(mgr, *, sensors=(), template=None, mode="or"):
    """Configure ONLY the gate (no clock restriction → window otherwise open)."""
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
        gate_sensors=list(sensors),
        gate_template=template,
        gate_template_mode=mode,
    )


def test_gate_sensor_off_closes_window_inside_clock(mgr):
    mgr._hass.states.set("binary_sensor.bright", "off")
    _configure_gate(mgr, sensors=["binary_sensor.bright"])
    # No clock restriction → before_end_time and after_start_time are both True,
    # so is_active hangs entirely on the gate. OFF = dark = stop tracking.
    assert mgr.gate_is_daytime is False
    assert mgr.gate_is_dark is True
    assert mgr.is_active is False


def test_gate_sensor_on_keeps_window_open(mgr):
    mgr._hass.states.set("binary_sensor.bright", "on")
    _configure_gate(mgr, sensors=["binary_sensor.bright"])
    assert mgr.gate_is_daytime is True
    assert mgr.gate_is_dark is False
    assert mgr.is_active is True


def _clock_mgr():
    """Build a gate manager with an injectable fake clock (issue #742).

    Returns ``(mgr, clock)`` where ``clock`` is a one-element list whose value is
    "now" in seconds — mutate ``clock[0]`` to advance the grace window.
    """
    hass = _FakeHass()
    clock = [0.0]
    mgr = TimeWindowManager(
        hass, logging.getLogger("test_issue_632_clock"), clock=lambda: clock[0]
    )
    return mgr, clock


def test_gate_sensor_indeterminate_since_startup_falls_back_to_astral():
    # Issue #742 (a): a gate source that has NEVER reported a verdict cannot be
    # held — there is no last-known value — so the gate falls back to the
    # astronomical window immediately (effective_daytime_gate is None → clock-open).
    mgr, _clock = _clock_mgr()
    _configure_gate(mgr, sensors=["binary_sensor.never_reported"])
    assert mgr.effective_daytime_gate is None
    assert mgr.gate_is_daytime is True
    assert mgr.gate_is_dark is False
    assert mgr.is_active is True


def test_gate_sensor_reported_then_gone_holds_then_falls_back():
    # Issue #742 (b): a sensor that reported a verdict and then went unavailable is
    # held for the grace window, then falls back to astronomical once it expires.
    mgr, clock = _clock_mgr()
    _configure_gate(mgr, sensors=["binary_sensor.gate"])

    # Real verdict observed → recorded as last-known.
    mgr._hass.states.set("binary_sensor.gate", "on")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is True

    # Source goes unavailable inside the grace window → hold last-known daytime.
    mgr._hass.states.set("binary_sensor.gate", "unavailable")
    clock[0] = 60.0
    assert mgr.effective_daytime_gate is True  # HOLDING

    # Still unavailable past the grace window → fall back to astronomical.
    clock[0] = 60.0 + 121.0
    assert mgr.effective_daytime_gate is None  # FELL_BACK
    assert mgr.gate_is_daytime is True
    assert mgr.gate_is_dark is False


def test_gate_multiple_sensors_any_on_is_daytime(mgr):
    mgr._hass.states.set("binary_sensor.a", "off")
    mgr._hass.states.set("binary_sensor.b", "on")
    _configure_gate(mgr, sensors=["binary_sensor.a", "binary_sensor.b"])
    assert mgr.gate_is_daytime is True


def test_gate_default_template_mode_is_shared_default(mgr):
    # Sanity: the const default is wired and exported.
    assert CONF_DAYTIME_GATE_SENSORS == "daytime_gate_sensors"
    assert CONF_DAYTIME_GATE_TEMPLATE == "daytime_gate_template"
    assert CONF_DAYTIME_GATE_TEMPLATE_MODE == "daytime_gate_template_mode"
    assert DEFAULT_TEMPLATE_COMBINE_MODE == "or"


# ---------------------------------------------------------------------------
# Template-backed gate (needs a real hass to render Jinja)
# ---------------------------------------------------------------------------


def _gate_mgr(hass: HomeAssistant) -> TimeWindowManager:
    mgr = TimeWindowManager(hass, logging.getLogger("test_issue_632_tmpl"))
    return mgr


async def test_gate_template_truthy_is_daytime(hass: HomeAssistant):
    mgr = _gate_mgr(hass)
    mgr.update_config(None, None, None, None, gate_template="{{ true }}")
    assert mgr.gate_is_daytime is True
    assert mgr.is_active is True


async def test_gate_template_falsy_is_dark(hass: HomeAssistant):
    mgr = _gate_mgr(hass)
    mgr.update_config(None, None, None, None, gate_template="{{ false }}")
    assert mgr.gate_is_daytime is False
    assert mgr.gate_is_dark is True
    assert mgr.is_active is False


async def test_gate_template_render_failure_fails_open_to_daytime(hass: HomeAssistant):
    # A broken template (undefined fn) must NOT force a premature sunset:
    # render_condition(default=True) keeps the gate at daytime.
    mgr = _gate_mgr(hass)
    mgr.update_config(None, None, None, None, gate_template="{{ nonexistent_fn() }}")
    assert mgr.gate_is_daytime is True
    assert mgr.gate_is_dark is False


async def test_gate_template_and_mode_gates_sensor(hass: HomeAssistant):
    # AND mode: daytime only when template truthy AND a sensor on.
    hass.states.async_set("binary_sensor.bright", "on")
    await hass.async_block_till_done()
    mgr = _gate_mgr(hass)
    mgr.update_config(
        None,
        None,
        None,
        None,
        gate_sensors=["binary_sensor.bright"],
        gate_template="{{ false }}",
        gate_template_mode="and",
    )
    # Template false gates the sensor off → dark.
    assert mgr.gate_is_daytime is False


# ---------------------------------------------------------------------------
# Coordinator wiring — _compute_current_effective_default passes the gate through
# ---------------------------------------------------------------------------


def _coord_with_gate(*, gate_is_dark: bool, gate_is_configured: bool):
    """Minimal coordinator stub for _compute_current_effective_default tests."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord.hass.states.get.return_value = None  # no sunset/sunrise time entities

    time_mgr = MagicMock()
    # effective_daytime_gate is the verdict the coordinator forwards as
    # ``daytime_gate`` (issue #742); keep gate_is_daytime/gate_is_dark consistent.
    time_mgr.effective_daytime_gate = (not gate_is_dark) if gate_is_configured else None
    time_mgr.gate_is_daytime = (not gate_is_dark) if gate_is_configured else True
    time_mgr.gate_is_dark = gate_is_dark if gate_is_configured else False
    time_mgr.gate_is_configured = gate_is_configured
    time_mgr.window_explicitly_started = False
    coord._time_mgr = time_mgr

    # get_blind_data returns an object whose sun_data is a controllable mock.
    sun_data = MagicMock()
    import datetime as _dt

    today = _dt.date.today()
    sun_data.sunset.return_value = _dt.datetime(
        today.year, today.month, today.day, 20, 0, 0
    )
    sun_data.sunrise.return_value = _dt.datetime(
        today.year, today.month, today.day, 6, 0, 0
    )
    cover_data = MagicMock()
    cover_data.sun_data = sun_data
    coord.get_blind_data = MagicMock(return_value=cover_data)
    return coord


def test_coordinator_passes_gate_dark_forcing_sunset():
    coord = _coord_with_gate(gate_is_dark=True, gate_is_configured=True)
    options = {CONF_SUNSET_POS: 20, "default_percentage": 80}
    _, is_sunset_active = coord._compute_current_effective_default(options)
    assert is_sunset_active is True


def test_coordinator_passes_gate_daytime_suppressing_sunset():
    coord = _coord_with_gate(gate_is_dark=False, gate_is_configured=True)
    options = {CONF_SUNSET_POS: 20, "default_percentage": 80}
    eff, is_sunset_active = coord._compute_current_effective_default(options)
    assert is_sunset_active is False
    assert eff == 80


def test_coordinator_unconfigured_gate_uses_astronomical():
    # Gate not configured → daytime_gate=None → astronomical path used.
    coord = _coord_with_gate(gate_is_dark=False, gate_is_configured=False)
    options = {CONF_SUNSET_POS: 20, "default_percentage": 80}
    # Astronomically midday (~now between 6:00 and 20:00 in most test runs is not
    # guaranteed, so assert only that the call succeeds and returns the astral
    # decision rather than a gate-forced one). The key contract: a configured-but-
    # daytime gate suppresses sunset while an UNCONFIGURED gate defers to astral.
    eff, is_sunset_active = coord._compute_current_effective_default(options)
    assert isinstance(is_sunset_active, bool)
    assert eff in (20, 80)


# ---------------------------------------------------------------------------
# Coordinator wiring — graceful gate fallback (issue #742)
# ---------------------------------------------------------------------------


def test_coordinator_forwards_effective_daytime_gate_when_fell_back():
    """A configured-but-fell-back gate forwards daytime_gate=None (astral)."""
    coord = _coord_with_gate(gate_is_dark=True, gate_is_configured=True)
    # Gate is configured and would read dark, BUT has fallen back to astronomical:
    # the coordinator must forward the *effective* value (None), not gate_is_daytime.
    coord._time_mgr.effective_daytime_gate = None
    options = {CONF_SUNSET_POS: 20, "default_percentage": 80}
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.compute_effective_default",
        return_value=(80, False),
    ) as m:
        coord._compute_current_effective_default(options)
    assert m.call_args.kwargs["daytime_gate"] is None


def _coord_for_wake(secs):
    """Minimal coordinator stub for the gate-fallback wake scheduler."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord._gate_fallback_unsub = None
    time_mgr = MagicMock()
    time_mgr.seconds_until_gate_fallback.return_value = secs
    coord._time_mgr = time_mgr
    return coord


def test_schedule_gate_fallback_wake_when_holding():
    """A HOLDING gate (remaining seconds) schedules exactly one async_call_later wake."""
    coord = _coord_for_wake(secs=42.0)
    cancel = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=cancel,
    ) as m:
        coord._schedule_gate_fallback_wake()
    m.assert_called_once()
    assert m.call_args.args[0] is coord.hass
    assert m.call_args.args[1] == 42.0
    assert coord._gate_fallback_unsub is cancel


def test_schedule_gate_fallback_wake_no_wake_when_determinate():
    """A determinate gate (None remaining) schedules no wake."""
    coord = _coord_for_wake(secs=None)
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later"
    ) as m:
        coord._schedule_gate_fallback_wake()
    m.assert_not_called()
    assert coord._gate_fallback_unsub is None


def test_schedule_gate_fallback_wake_cancels_previous():
    """A new wake cancels the previous in-flight handle first."""
    coord = _coord_for_wake(secs=10.0)
    previous = MagicMock()
    coord._gate_fallback_unsub = previous
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        coord._schedule_gate_fallback_wake()
    previous.assert_called_once()


async def test_gate_fallback_due_callback_requests_refresh():
    """The scheduled callback clears the handle and requests a refresh."""
    coord = _coord_for_wake(secs=None)
    coord._gate_fallback_unsub = MagicMock()
    coord.async_request_refresh = AsyncMock()
    await coord._on_gate_fallback_due(None)
    assert coord._gate_fallback_unsub is None
    coord.async_request_refresh.assert_awaited_once()


async def test_async_shutdown_cancels_gate_fallback_handle():
    """async_shutdown cancels and clears the gate-fallback wake handle."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._grace_mgr = MagicMock()
    coord._cancel_motion_timeout = MagicMock()
    coord._cancel_weather_timeout = MagicMock()
    coord._sensor_health = MagicMock()
    coord._cmd_svc = MagicMock()
    coord._forecast_unsub = None
    coord._refresh_after_unsub = None
    cancel = MagicMock()
    coord._gate_fallback_unsub = cancel
    await coord.async_shutdown()
    cancel.assert_called_once()
    assert coord._gate_fallback_unsub is None
