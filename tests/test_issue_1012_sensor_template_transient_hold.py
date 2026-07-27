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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_END_TIME,
    CONF_SENSOR_TYPE,
    CONF_START_TIME,
    CUSTOM_POSITION_INPUT_HOLD_SECONDS,
    CoverType,
    DOMAIN,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
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


def _make_builder(mock_hass, *, clock=None) -> PipelineSnapshotBuilder:
    """Snapshot builder bound to the mock hass — the real sensor-read surface.

    ``clock``, when given, is an injected monotonic time source (seconds) so
    a test can advance the per-input hold window deterministically without
    ``sleep`` (issue #1012 finding 1). Omitted entirely for callers that
    don't care — the builder then falls back to the real ``time.monotonic``,
    which is what every pre-existing test in this file still relies on.
    """
    kwargs = {} if clock is None else {"clock": clock}
    return PipelineSnapshotBuilder(
        hass=mock_hass,
        logger=MagicMock(),
        climate_provider=MagicMock(),
        toggles=MagicMock(),
        policy=MagicMock(),
        config_service=MagicMock(),
        **kwargs,
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


# ---------------------------------------------------------------------------
# Per-input hold TIME BOUND (finding 1): a permanently-dead input must
# eventually release the slot, not mask a healthy peer forever.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hold_expires_after_window_and_healthy_peer_wins(mock_hass):
    """The audit's exact pathology: an OR-mode slot with a sensor whose
    battery dies permanently and a template that validly opines False. The
    per-input hold must protect a brief blip (#1012's original contract:
    "briefly went unavailable"), but a permanently-dead sensor must
    eventually release the slot back to the template's real opinion — else
    the slot stays pinned forever with no release path (issue #1012,
    finding 1).
    """
    clock = [0.0]
    builder = _make_builder(mock_hass, clock=lambda: clock[0])

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        return_value=False,
    ):
        # Cycle 1: sensor on, template False → OR-fold True (sensor wins).
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor unavailable, clock advanced LESS than the hold
        # window → the #1012 per-input hold still protects the blip (this is
        # the behavior #1012 exists to preserve — it must survive finding 1).
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS - 1.0
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True
        assert c2.is_valid is True

        # Cycle 3: sensor STILL unavailable, clock advanced PAST the window →
        # the per-input hold expires; the template's real, validly-opined
        # False wins the fold again — the sensor can no longer mask it.
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS + 1.0
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is False
        assert c3.is_valid is True


@pytest.mark.unit
def test_expired_hold_recovers_when_sensor_returns(mock_hass):
    """After the per-input hold expires and the slot releases, the sensor
    recovering must re-arm a fresh hold window — proving expiry doesn't
    permanently poison the slot; a later short blip is held again exactly
    like the very first one was (issue #1012, finding 1).
    """
    clock = [0.0]
    builder = _make_builder(mock_hass, clock=lambda: clock[0])

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        return_value=False,
    ):
        # Cycle 1: sensor on → valid True, cached at t=0.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor unavailable, clock past the window → hold expires,
        # template's False wins → slot goes off.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS + 1.0
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is False
        assert c2.is_valid is True

        # Cycle 3: sensor recovers → fresh True again, re-cached at the
        # CURRENT clock reading (not poisoned by the earlier expiry).
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is True
        assert c3.is_valid is True

        # Cycle 4: a later short blip, well within a NEW window measured from
        # cycle 3's timestamp, must be held again — expiry didn't leave the
        # slot permanently unable to hold.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        clock[0] += 1.0
        (c4,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c4.is_on is True
        assert c4.is_valid is True


@pytest.mark.unit
def test_template_hold_expires_and_healthy_sensor_wins(mock_hass):
    """Mirror of the expiry test for the template side: a template that
    permanently fails to render must eventually release the slot back to a
    validly-opined sensor's own value, rather than masking it forever
    (issue #1012, finding 1 — symmetric direction).
    """
    clock = [0.0]
    builder = _make_builder(mock_hass, clock=lambda: clock[0])
    _set_sensor_states(mock_hass, {"binary_sensor.mode": "off"})

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[True, None, None],
    ):
        # Cycle 1: sensor off, template True → OR-fold True (template wins),
        # cached at t=0.
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: template fails to render, clock within the window → held.
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS - 1.0
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True
        assert c2.is_valid is True  # sensor's valid off keeps is_valid True

        # Cycle 3: template STILL fails to render, clock past the window →
        # the template's hold expires; the sensor's real (valid) off wins.
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS + 1.0
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is False
        assert c3.is_valid is True


# ---------------------------------------------------------------------------
# AND-mode coverage (finding 3): the per-input hold must protect an AND-mode
# slot too, not just OR — a held sensor can keep an AND slot on where,
# pre-#1012, it would have gone off.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode, template_render, expected_is_on_by_cycle",
    [
        # OR mode: a constant-False template means the sensor's own (fresh
        # or held) contribution alone decides is_on — the audit's original
        # scenario.
        ("or", False, (False, True, True, True)),
        # AND mode: with a constant-False template, combine_with_mode's AND
        # clause (template_truthy and others_truthy) would force is_on False
        # on EVERY cycle regardless of the sensor, so a False-template AND
        # scenario exercises nothing about the sensor hold at all. A
        # constant-TRUE template instead makes is_on = template_truthy AND
        # sensors_on — so the *held* sensor value now decides the AND
        # result exactly as it decides the OR result above, which is what
        # actually proves the per-input hold protects AND slots too.
        ("and", True, (False, True, True, True)),
    ],
)
def test_held_sensor_protects_or_and_and_mode(
    mock_hass, mode, template_render, expected_is_on_by_cycle
):
    """The per-input sensor hold must protect both OR-mode and AND-mode
    slots — the mode only changes which template render value makes the
    sensor's contribution decisive, not whether the hold does its job.
    """
    builder = _make_builder(mock_hass)
    options = {**_OPTIONS, "custom_position_template_mode_1": mode}

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        return_value=template_render,
    ):
        # Cycle 0: cold start, sensor unavailable → no prior hold to fall
        # back on; sensor contributes its fresh (empty) reading.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        (c0,) = builder.read_custom_position_sensors(options)
        assert c0.is_on is expected_is_on_by_cycle[0]
        assert c0.is_valid is True

        # Cycle 1: sensor on → fresh True feeds the fold directly.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(options)
        assert c1.is_on is expected_is_on_by_cycle[1]
        assert c1.is_valid is True

        # Cycle 2: sensor transiently unavailable → held to its last-valid
        # True. This is the regression-guard assertion: without the
        # per-input hold, the sensor's fresh False would flip is_on here.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        (c2,) = builder.read_custom_position_sensors(options)
        assert c2.is_on is expected_is_on_by_cycle[2]
        assert c2.is_valid is True

        # Cycle 3: sensor recovers → fresh True again.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c3,) = builder.read_custom_position_sensors(options)
        assert c3.is_on is expected_is_on_by_cycle[3]
        assert c3.is_valid is True


@pytest.mark.unit
def test_both_inputs_invalid_still_uses_whole_slot_hold(mock_hass):
    """When sensor AND template both go invalid in the same cycle, the
    pre-existing #1005 whole-slot hold still engages as the outer safety
    net — proven distinctly from the per-input hold by spanning PAST the
    per-input window (CUSTOM_POSITION_INPUT_HOLD_SECONDS): each per-input
    hold, evaluated on its own, would itself have expired by then and
    folded to is_on=False. The whole-slot hold (#1005) carries no time
    bound, so is_on=True surviving here can only be explained by the
    whole-slot cache overwriting the (already-expired) per-input fold —
    proving the two mechanisms don't collapse into one indistinguishable
    path (issue #1012, finding 4).
    """
    clock = [0.0]
    builder = _make_builder(mock_hass, clock=lambda: clock[0])

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[False, None],
    ):
        # Cycle 1: sensor on, template False → valid on (OR-fold: sensor
        # wins). Both the per-input caches and the whole-slot cache record
        # this at t=0.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "on"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is True
        assert c1.is_valid is True

        # Cycle 2: sensor invalid AND template fails to render, clock
        # advanced PAST the per-input hold window. Each per-input hold has
        # itself expired (sensor: unavailable past window → falls through to
        # fresh False; template: never valid this run beyond cycle 1, and
        # also past window → falls through to fresh False) — folded alone,
        # that is is_on=False. Whole-slot is_valid is False this cycle, so
        # the unbounded #1005 hold engages and overwrites is_on with the last
        # valid COMBINED state (True) — the only mechanism left standing.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS + 1.0
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True
        assert c2.is_valid is False


# ---------------------------------------------------------------------------
# Whole-slot cache pollution guard (audit finding, second pass on #1012): the
# unbounded #1005 cache must only ever be refreshed from a cycle where BOTH
# inputs' fold contributions were genuinely fresh — never from a cycle where
# a per-input hold substituted a stale value into the fold. Otherwise a
# transient per-input hold can permanently overwrite the true last-known-good
# combined state with a value that was only ever "mixed" (partly held), and
# that pollution survives forever once both inputs later die together.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_staggered_input_death_does_not_pin_slot_forever(mock_hass):
    """A per-input HELD value must never be promoted into the unbounded
    whole-slot (#1005) cache, or a transient mix of "one fresh + one held"
    input can permanently overwrite the slot's true last-known-good combined
    state.

    Sequence (OR mode, both inputs eventually die together, never recover —
    the #1005 outer hold has no time bound, so once that happens the cached
    value replays forever):

    * Cycle 1 (t=0): sensor OFF, template False — BOTH genuinely fresh. This
      is the slot's true last-known-good combined state: ``is_on=False``.
    * Cycle 2 (t=10): sensor dies (unavailable) — its OWN per-input hold
      substitutes its last-known False. The template, still genuinely
      reading, changes its opinion to True. The OR-fold is legitimately
      ``True`` this cycle (the template alone earns it) — but that fold
      result is built from ONE held input, not two fresh ones.
    * Cycle 3 (t=20): the template ALSO dies (fails to render) — every input
      is now raw-invalid, so the #1005 whole-slot hold takes over and
      replays whatever is cached.
    * Cycle 4 (t=10,000): both inputs are still permanently dead; nothing
      can ever make either genuinely valid again, so whatever the whole-slot
      cache holds at cycle 3 replays forever.

    Pre-fix, cycle 2's mixed (fresh-template + held-sensor) ``True`` gets
    promoted into the whole-slot cache (``is_valid`` was True that cycle),
    permanently overwriting cycle 1's genuine ``False`` — the slot then
    stays wrongly pinned ``True`` forever from cycle 3 onward. Post-fix, the
    cycle-2 write is suppressed (a per-input hold was substituted that
    cycle), so the whole-slot cache still holds cycle 1's genuine ``False``
    when both inputs die — the slot correctly releases to ``False`` and
    stays there.
    """
    clock = [0.0]
    builder = _make_builder(mock_hass, clock=lambda: clock[0])

    with patch(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder."
        "render_condition_or_none",
        side_effect=[False, True, None, None],
    ):
        # Cycle 1: sensor off, template False — both genuinely fresh, is_on
        # False. This is the true last-known-good combined state.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "off"})
        (c1,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c1.is_on is False
        assert c1.is_valid is True

        # Cycle 2: sensor dies, template changes to a genuine True. The
        # OR-fold is True this cycle (template alone earns it), but it is
        # built from a held sensor contribution, not two fresh inputs.
        _set_sensor_states(mock_hass, {"binary_sensor.mode": "unavailable"})
        clock[0] = 10.0
        (c2,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c2.is_on is True
        assert c2.is_valid is True

        # Cycle 3: the template also dies — every input is now invalid, so
        # the whole-slot hold engages. It must replay cycle 1's genuine
        # False, not cycle 2's held-mixed True (fails today: True).
        clock[0] = 20.0
        (c3,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c3.is_on is False  # fails today: pinned True from cycle 2
        assert c3.is_valid is False

        # Cycle 4: both inputs are still permanently dead, far past the
        # per-input hold window — the slot must stay released, not spring
        # back to a stale pinned value.
        clock[0] = CUSTOM_POSITION_INPUT_HOLD_SECONDS * 10
        (c4,) = builder.read_custom_position_sensors(_OPTIONS)
        assert c4.is_on is False  # fails today: pinned True forever
        assert c4.is_valid is False


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


# ---------------------------------------------------------------------------
# Coordinator-level prompt wake at hold expiry (issue #1012, Task C).
#
# ACP has no ``update_interval`` (it is purely event-driven), so while a
# per-input hold is HOLDING, nothing else would prompt a refresh at exactly
# the moment it falls back to its own fresh (possibly different) reading —
# mirroring the gap #742's ``_schedule_gate_fallback_wake`` closed for the
# daytime gate. These tests exercise the generalized scheduler
# (``_schedule_optional_wake``) through its custom-position caller.
# ---------------------------------------------------------------------------


def _coord_for_custom_position_wake(secs):
    """Minimal coordinator stub for the custom-position hold wake scheduler."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord._custom_position_hold_unsub = None
    builder = MagicMock()
    builder.seconds_until_custom_position_hold_fallback.return_value = secs
    coord._snapshot_builder = builder
    return coord


@pytest.mark.unit
def test_schedule_custom_position_hold_wake_when_holding():
    """An active per-input hold (remaining seconds) schedules exactly one
    ``async_call_later`` wake.
    """
    coord = _coord_for_custom_position_wake(secs=42.0)
    cancel = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=cancel,
    ) as m:
        coord._schedule_custom_position_hold_wake()
    m.assert_called_once()
    assert m.call_args.args[0] is coord.hass
    assert m.call_args.args[1] == 42.0
    assert coord._custom_position_hold_unsub is cancel


@pytest.mark.unit
def test_schedule_custom_position_hold_wake_no_wake_when_no_hold_active():
    """No slot currently HOLDING (``None`` remaining) schedules no wake."""
    coord = _coord_for_custom_position_wake(secs=None)
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later"
    ) as m:
        coord._schedule_custom_position_hold_wake()
    m.assert_not_called()
    assert coord._custom_position_hold_unsub is None


@pytest.mark.unit
def test_schedule_custom_position_hold_wake_cancels_previous():
    """A new wake cancels the previous in-flight handle first."""
    coord = _coord_for_custom_position_wake(secs=10.0)
    previous = MagicMock()
    coord._custom_position_hold_unsub = previous
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        coord._schedule_custom_position_hold_wake()
    previous.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_position_hold_due_callback_requests_refresh():
    """The scheduled callback clears the handle and requests a refresh."""
    coord = _coord_for_custom_position_wake(secs=None)
    coord._custom_position_hold_unsub = MagicMock()
    coord.async_request_refresh = AsyncMock()
    await coord._on_custom_position_hold_due(None)
    assert coord._custom_position_hold_unsub is None
    coord.async_request_refresh.assert_awaited_once()
