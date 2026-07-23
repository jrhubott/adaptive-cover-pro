"""Transient-unavailable custom-position sensor must HOLD activation (#1005).

A custom-position slot sensor that flips ``on → unavailable → on`` for a few
milliseconds (a template reload, a Zigbee round-trip) must NOT be read as a
release. The old code coerced ``unavailable``/``unknown``/missing to the same
``False`` as a valid ``off`` (``state.state == "on"``), so a transient invalid
read flipped the slot ``is_on True → False``. That (a) deactivated the pipeline
handler → the default position resolved, and (b) fired a false active→inactive
release edge → ``use_force=True`` → every cover failed open.

The fix keys on VALIDITY, not on ``is_on``: a literal ``off`` stays a valid
release; only an *invalid* (unavailable / unknown / missing) read is HELD to
its last-valid activation. The hold lives on ``PipelineSnapshotBuilder`` (one
per coordinator) so both per-cycle consumers — the pipeline snapshot and the
coordinator release-edge stamp — see the same held ``is_on`` with zero
coordinator edits.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_END_TIME,
    CONF_SENSOR_TYPE,
    CONF_START_TIME,
    CoverType,
    DOMAIN,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

# Slot mirrors the incident: single sensor, position 0%, priority 78 (NOT a
# safety slot — the ordinary custom_position_released force-path).
_OPTIONS = {
    "custom_position_sensors_1": ["binary_sensor.mode"],
    "custom_position_1": 0,
    "custom_position_priority_1": 78,
}


def _make_builder(mock_hass) -> PipelineSnapshotBuilder:
    """Snapshot builder bound to the mock hass — the real sensor-read surface."""
    return PipelineSnapshotBuilder(
        hass=mock_hass,
        logger=MagicMock(),
        climate_provider=MagicMock(),
        toggles=MagicMock(),
        policy=MagicMock(),
        config_service=MagicMock(),
    )


def _set_sensor_states(mock_hass, states: dict[str, str | None]) -> None:
    """Wire mock_hass.states.get to return the given per-entity states."""

    def get_state(entity_id):
        value = states.get(entity_id)
        if value is None:
            return None
        state_obj = MagicMock()
        state_obj.state = value
        state_obj.attributes = {}
        return state_obj

    mock_hass.states.get.side_effect = get_state


# ---------------------------------------------------------------------------
# Builder-level unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("invalid_value", ["unavailable", "unknown", None])
def test_transient_invalid_read_holds_activation(mock_hass, invalid_value):
    """A transient invalid read holds the last-valid activation (is_on stays True)."""
    builder = _make_builder(mock_hass)

    # Cycle 1: sensor on → valid activation.
    _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
    (c1,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c1.is_on is True
    assert c1.is_valid is True

    # Cycle 2: sensor invalid (transient) → HELD to the last valid activation.
    _set_sensor_states(mock_hass, {"binary_sensor.mode": invalid_value})
    (c2,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c2.is_on is True  # fails today: coerced to False
    assert c2.is_valid is False
    assert c2.active_entity_ids == ("binary_sensor.mode",)

    # Cycle 3: sensor recovers to on → valid again.
    _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
    (c3,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c3.is_on is True
    assert c3.is_valid is True


@pytest.mark.unit
def test_valid_off_after_hold_still_releases(mock_hass):
    """A literal off is a VALID release, even right after a held invalid read."""
    builder = _make_builder(mock_hass)

    _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
    (c1,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c1.is_on is True

    _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
    (c2,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c2.is_on is True  # held

    _set_sensor_states(mock_hass, {"binary_sensor.mode": "off"})
    (c3,) = builder.read_custom_position_sensors(_OPTIONS)
    assert c3.is_on is False  # genuine release
    assert c3.is_valid is True


@pytest.mark.unit
def test_one_valid_sensor_keeps_slot_valid(mock_hass):
    """One usable sensor makes the slot validly-off — NOT held."""
    builder = _make_builder(mock_hass)
    options = {
        "custom_position_sensors_1": ["binary_sensor.rain", "binary_sensor.wind"],
        "custom_position_1": 0,
        "custom_position_priority_1": 78,
    }

    _set_sensor_states(
        mock_hass, {"binary_sensor.rain": "on", "binary_sensor.wind": "off"}
    )
    (c1,) = builder.read_custom_position_sensors(options)
    assert c1.is_on is True

    # rain unavailable, wind off → the slot has a usable input (wind=off), so it
    # is validly off; the hold must NOT kick in.
    _set_sensor_states(
        mock_hass, {"binary_sensor.rain": "unavailable", "binary_sensor.wind": "off"}
    )
    (c2,) = builder.read_custom_position_sensors(options)
    assert c2.is_valid is True
    assert c2.is_on is False


@pytest.mark.unit
def test_first_ever_invalid_read_defaults_off(mock_hass):
    """A fresh builder whose very first read is invalid defaults to off (no hold)."""
    builder = _make_builder(mock_hass)
    _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})

    (state,) = builder.read_custom_position_sensors(_OPTIONS)

    assert state.is_on is False
    assert state.is_valid is False


@pytest.mark.unit
def test_template_render_failure_holds_last_valid(mock_hass):
    """A template-only slot holds its last-valid activation on a render failure."""
    builder = _make_builder(mock_hass)
    _set_sensor_states(mock_hass, {})  # template-only: no sensors
    options = {
        "custom_position_template_1": "{{ is_state('binary_sensor.mode', 'on') }}",
        "custom_position_1": 0,
        "custom_position_priority_1": 78,
    }

    # render_condition_or_none returns None when a template fails to render.
    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[True, None, True],
    ):
        (c1,) = builder.read_custom_position_sensors(options)
        (c2,) = builder.read_custom_position_sensors(options)
        (c3,) = builder.read_custom_position_sensors(options)

    assert (c1.is_on, c1.is_valid) == (True, True)
    assert (c2.is_on, c2.is_valid) == (True, False)  # held across render failure
    assert (c3.is_on, c3.is_valid) == (True, True)


# ---------------------------------------------------------------------------
# Coordinator integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_unavailable_does_not_release_or_dispatch(
    hass: HomeAssistant,
) -> None:
    """A transient unavailable read must not release the slot nor move covers."""
    from pytest_homeassistant_custom_component.common import async_mock_service

    calls = async_mock_service(hass, "cover", "set_cover_position")

    # Sun away from the window (azimuth 0 vs window 180) → no direct sun, so the
    # would-be default is deterministic when the slot is (wrongly) deactivated.
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {"azimuth": 0.0, "elevation": 45.0, "rising": True},
    )
    # Cover already sits at the slot's commanded 0% so an active slot never
    # dispatches; only a false release to the default (50%) would.
    hass.states.async_set(
        "cover.test_blind",
        "closed",
        {"current_position": 0, "supported_features": 143},
    )
    hass.states.async_set("binary_sensor.mode", "on")

    options = {
        **dict(VERTICAL_OPTIONS),
        **_OPTIONS,
        CONF_START_TIME: "00:00:00",
        CONF_END_TIME: "23:59:59",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Hold", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="issue_1005_01",
        title="Hold",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # Cycle 1: sensor on → slot active, stamped.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True
    baseline = len(calls)

    # Cycle 2: sensor transiently unavailable → held, no release, no dispatch.
    hass.states.async_set("binary_sensor.mode", "unavailable")
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True  # fails today
    assert coordinator._prev_custom_position_states[1].is_valid is False
    assert len(calls) == baseline  # fails today: false release fans out to covers

    # Cycle 3: sensor recovers → slot still active, still no spurious dispatch.
    hass.states.async_set("binary_sensor.mode", "on")
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True
    assert coordinator._prev_custom_position_states[1].is_valid is True
    assert len(calls) == baseline
