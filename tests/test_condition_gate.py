"""Unit tests for the shared ConditionGate kernel (issue #1167).

The sensor-list + optional-Jinja fold, the grace hold, and the reset-on-config-
change rule were all inline in ``TimeWindowManager``'s daytime gate. Extracting
them means two gates share one implementation instead of mirroring it, so this
module pins the kernel's contract directly — ``test_time_window_manager.py`` and
``test_issue_632_daytime_gate.py`` keep pinning the daytime gate's behaviour
through the manager, unchanged.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import TemplateCombineMode
from custom_components.adaptive_cover_pro.managers.common.condition_gate import (
    ConditionGate,
)


class _Clock:
    """Monotonic clock stub driven by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_gate(grace=120.0, states=None, template_result=None):
    """Build a gate over an in-memory state map and a fixed template verdict."""
    states = states if states is not None else {}
    clock = _Clock()
    gate = ConditionGate(
        grace_seconds=grace,
        read_state=lambda entity_id: states.get(entity_id),
        render_condition=lambda template: template_result,
        clock=clock,
    )
    return gate, clock, states


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_unconfigured_gate_is_not_configured():
    gate, _clock, _states = _make_gate()
    assert gate.is_configured is False


def test_sensors_alone_configure_the_gate():
    gate, _clock, _states = _make_gate()
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.is_configured is True


def test_template_alone_configures_the_gate():
    gate, _clock, _states = _make_gate()
    gate.update_config(template="{{ true }}")
    assert gate.is_configured is True


def test_a_non_template_string_does_not_configure_the_gate():
    """A bare string with no Jinja delimiters is not a template."""
    gate, _clock, _states = _make_gate()
    gate.update_config(template="not a template")
    assert gate.is_configured is False


# ---------------------------------------------------------------------------
# live_verdict — the tri-state fold
# ---------------------------------------------------------------------------


def test_no_sources_is_indeterminate():
    gate, _clock, _states = _make_gate()
    assert gate.live_verdict() is None


def test_every_sensor_invalid_is_indeterminate():
    gate, _clock, _states = _make_gate(states={"binary_sensor.a": None})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.live_verdict() is None


def test_sensors_or_together():
    gate, _clock, states = _make_gate(
        states={"binary_sensor.a": "off", "binary_sensor.b": "on"}
    )
    gate.update_config(sensors=["binary_sensor.a", "binary_sensor.b"])
    assert gate.live_verdict() is True

    states["binary_sensor.b"] = "off"
    assert gate.live_verdict() is False


def test_one_valid_one_dead_sensor_is_not_indeterminate():
    """A dead sensor is dropped, not treated as a veto or an unknown."""
    gate, _clock, _states = _make_gate(
        states={"binary_sensor.dead": None, "binary_sensor.live": "on"}
    )
    gate.update_config(sensors=["binary_sensor.dead", "binary_sensor.live"])
    assert gate.live_verdict() is True


def test_template_alone_supplies_the_verdict():
    gate, _clock, _states = _make_gate(template_result=False)
    gate.update_config(template="{{ false }}")
    assert gate.live_verdict() is False


def test_template_stands_when_the_only_sensor_is_dead():
    gate, _clock, _states = _make_gate(
        states={"binary_sensor.dead": None}, template_result=False
    )
    gate.update_config(sensors=["binary_sensor.dead"], template="{{ false }}")
    assert gate.live_verdict() is False


@pytest.mark.parametrize(
    ("mode", "sensor", "template", "expected"),
    [
        (TemplateCombineMode.OR, "off", True, True),
        (TemplateCombineMode.OR, "off", False, False),
        (TemplateCombineMode.AND, "on", False, False),
        (TemplateCombineMode.AND, "on", True, True),
    ],
)
def test_both_sources_fold_via_the_combine_mode(mode, sensor, template, expected):
    gate, _clock, _states = _make_gate(
        states={"binary_sensor.a": sensor}, template_result=template
    )
    gate.update_config(
        sensors=["binary_sensor.a"], template="{{ x }}", template_mode=mode
    )
    assert gate.live_verdict() is expected


# ---------------------------------------------------------------------------
# effective / resolved — grace behaviour
# ---------------------------------------------------------------------------


def test_unconfigured_gate_has_no_effective_opinion():
    gate, _clock, _states = _make_gate()
    assert gate.effective is None
    assert gate.resolved(default=True) is True
    assert gate.resolved(default=False) is False


def test_determinate_verdict_is_returned_live():
    gate, _clock, _states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False
    assert gate.resolved(default=True) is False


def test_within_grace_the_last_known_verdict_is_held():
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False  # observed dark

    states["binary_sensor.a"] = None  # source goes indeterminate
    clock.advance(60.0)
    assert gate.effective is False  # still held


def test_past_grace_the_gate_falls_back():
    """The window is measured from the FIRST indeterminate sighting, not the last good one.

    So the source must be *observed* indeterminate to anchor the window, and
    only a later observation past the window falls back. A caller with a sparse
    update cadence can sit arbitrarily long between last-known-good and first
    observed bad without that gap eating the grace window.
    """
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False

    states["binary_sensor.a"] = None
    assert gate.effective is False  # first indeterminate sighting → anchors, holds

    clock.advance(121.0)
    assert gate.effective is None
    assert gate.resolved(default=True) is True


def test_indeterminate_with_no_last_known_falls_back_immediately():
    gate, _clock, _states = _make_gate(states={"binary_sensor.a": None})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is None
    assert gate.resolved(default=True) is True


def test_seconds_until_fallback_phases():
    """None while determinate, the remaining window while holding, None once expired."""
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.seconds_until_fallback() is None

    states["binary_sensor.a"] = None
    assert gate.seconds_until_fallback() == pytest.approx(120.0)

    clock.advance(20.0)
    assert gate.seconds_until_fallback() == pytest.approx(100.0)

    clock.advance(200.0)
    assert gate.seconds_until_fallback() is None


# ---------------------------------------------------------------------------
# update_config — reset only on a real change
# ---------------------------------------------------------------------------


def test_unchanged_config_preserves_the_held_verdict():
    """update_config runs every cycle; a steady config must not reset the hold."""
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False

    states["binary_sensor.a"] = None
    clock.advance(60.0)
    gate.update_config(sensors=["binary_sensor.a"])  # same config, next cycle
    assert gate.effective is False  # still holding


def test_changed_config_forgets_the_held_verdict():
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False

    states["binary_sensor.a"] = None
    clock.advance(60.0)
    gate.update_config(sensors=["binary_sensor.b"])  # different sensor
    assert gate.effective is None  # nothing known about the new source


def test_changed_template_forgets_the_held_verdict():
    gate, clock, states = _make_gate(states={"binary_sensor.a": "off"})
    gate.update_config(sensors=["binary_sensor.a"])
    assert gate.effective is False

    states["binary_sensor.a"] = None
    clock.advance(60.0)
    gate.update_config(sensors=["binary_sensor.a"], template="{{ true }}")
    assert gate.effective is None
