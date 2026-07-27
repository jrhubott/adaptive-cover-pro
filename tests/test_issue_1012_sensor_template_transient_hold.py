"""Sensor+template custom-position slots must hold per-input, not just whole-slot (#1012).

#1005 added a whole-slot hold: when *every* bound input (sensor or template)
goes invalid in the same cycle, the last-valid combined ``is_on`` is held.
That leaves a gap: when a slot has *both* a bound sensor and a condition
template in OR (or AND) combine mode, one input can go transiently invalid
while the other keeps opining. The whole-slot ``is_valid`` stays ``True`` (the
still-opining input is usable this cycle), so the #1005 hold never engages —
and the dropped input's *fresh* (coerced-to-``False``) value silently wins the
fold, flipping ``is_on`` from ``True`` to ``False`` even though nothing
genuinely changed.

The fix adds a per-input hold: each side of the fold (the sensor OR and the
template opinion) is substituted with its own last-valid contribution when
*that side alone* is invalid this cycle, before ``combine_with_mode`` runs.
The pre-existing whole-slot hold (#1005) is left completely untouched as the
outer safety net for the fully-invalid case.
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

# Slot with BOTH a bound sensor and a condition template, OR combine mode
# (the default — custom_position_template_mode_1 left unset).
_OPTIONS = {
    "custom_position_sensors_1": ["binary_sensor.mode"],
    "custom_position_template_1": "{{ 1 == 2 }}",
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
def test_sensor_drops_while_template_opines_false_holds_on(mock_hass):
    """A transiently-invalid sensor must hold ITS OWN last-valid opinion even
    while the template opines a real (unrelated) False this cycle — the
    template's real opinion keeps the whole slot "valid", so the #1005
    whole-slot hold never engages, and only the new per-input hold prevents
    the sensor's fresh False from winning the OR fold.
    """
    builder = _make_builder(mock_hass)

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        return_value=False,
    ):
        # Cycle 0: cold start — the very first read for this slot has an
        # invalid sensor and no prior cached value to hold, so the sensor
        # side's own cold-start contract is "no hold" (mirrors #1005's
        # whole-slot cache): it contributes its fresh (empty) reading, and
        # the template's real False leaves the slot off overall.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        (c0,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c0.is_on is False
        assert c0.is_valid is True  # template alone still opined this cycle

        # Cycle 1: sensor on, template False → OR-fold True (sensor wins).
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor transiently unavailable, template still False → the
        # sensor side is held to its last-valid True; is_valid stays True on
        # the template's strength (a real opinion). Today this flips to
        # is_on=False.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True  # fails today: coerced to False
        assert c2.is_valid is True

        # Cycle 3: sensor recovers → fresh True, still on.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is True
        assert c3.is_valid is True


@pytest.mark.unit
def test_template_render_failure_while_sensor_off_holds_on(mock_hass):
    """Symmetric direction: the template side holds its own last-valid opinion
    when it fails to render, even while the sensor is validly off this cycle
    (the sensor's valid off keeps the whole slot "valid" on its own).
    """
    builder = _make_builder(mock_hass)
    _set_sensor_states(mock_hass, {"binary_sensor.mode": "off"})

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[True, None, True],
    ):
        # Cycle 1: sensor off, template True → OR-fold True (template wins).
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor still off (valid), template fails to render → the
        # template side is held to its last-valid True. is_valid stays True
        # on the sensor's valid off alone. Today this computes fresh to False.
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True  # fails today: coerced to False
        assert c2.is_valid is True

        # Cycle 3: template renders True again → still on.
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is True
        assert c3.is_valid is True


@pytest.mark.unit
def test_both_inputs_invalid_still_uses_whole_slot_hold(mock_hass):
    """When sensor AND template both go invalid in the same cycle, the
    pre-existing #1005 whole-slot hold still engages — proving the new
    per-input caches don't interfere with the pre-existing whole-slot
    fallback for the fully-invalid case.
    """
    builder = _make_builder(mock_hass)

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[True, None],
    ):
        # Cycle 1: sensor on, template True → valid on.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor invalid AND template fails to render → whole-slot
        # invalid → #1005 hold engages → is_on held True, is_valid False.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True
        assert c2.is_valid is False


# ---------------------------------------------------------------------------
# Coordinator integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_sensor_dropout_with_template_does_not_release_or_dispatch(
    hass: HomeAssistant,
) -> None:
    """A transiently-invalid sensor, with a validly-opining template on the
    same slot, must not release the slot nor move covers.
    """
    from pytest_homeassistant_custom_component.common import async_mock_service

    calls = async_mock_service(hass, "cover", "set_cover_position")

    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {"azimuth": 0.0, "elevation": 45.0, "rising": True},
    )
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
        data={"name": "Hold1012", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="issue_1012_01",
        title="Hold1012",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # Cycle 1: sensor on, template constant-False → slot active, stamped.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True
    baseline = len(calls)

    # Cycle 2: sensor transiently unavailable, template still False → held,
    # no release, no dispatch.
    hass.states.async_set("binary_sensor.mode", "unavailable")
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True  # fails today
    assert len(calls) == baseline  # fails today: false release fans out to covers

    # Cycle 3: sensor recovers → slot still active, still no spurious dispatch.
    hass.states.async_set("binary_sensor.mode", "on")
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._prev_custom_position_states[1].is_on is True
    assert len(calls) == baseline
