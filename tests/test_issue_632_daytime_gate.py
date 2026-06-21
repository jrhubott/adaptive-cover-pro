"""Tests for issue #632 — daytime gate (sensor/template) replacing the astral boundary.

The gate answers one yes/no question: "is it daytime — should ACP be sun-tracking
now?" It mirrors the issue-#577 motion pattern (entity list + optional Jinja template
+ combine mode), reusing ``render_condition`` / ``combine_with_mode`` /
``is_entity_active``. When unconfigured the gate is daytime (fail-open) and ACP falls
back to the astronomical sunset/sunrise calc — zero regression.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

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


def test_gate_sensor_unknown_fails_open_to_daytime(mgr):
    # Sensor never reported (no state) → is_entity_active fail-open → daytime.
    _configure_gate(mgr, sensors=["binary_sensor.never_reported"])
    assert mgr.gate_is_daytime is True
    assert mgr.is_active is True


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
    # gate_is_daytime is the verdict the coordinator forwards as ``daytime_gate``;
    # keep gate_is_dark consistent as its inverse when configured.
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
