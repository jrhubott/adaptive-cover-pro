"""Sun-tracking gate: bind solar tracking to a sensor/template (issue #1167).

The gate answers "should ACP sun-track right now?" from binary sensors and/or a
Jinja condition. When it says no, ``SolarHandler`` declines and the chain falls
through to whatever is below it — it does NOT force a position, and it does NOT
close the operating window the way the daytime gate does.

The distinction from the daytime gate is the whole point of the feature, so it
is pinned explicitly below: a dark daytime gate suppresses climate, glare zones,
cloud suppression and motion timeout too (they all read ``in_time_window``); a
closed sun-tracking gate suppresses solar and nothing else.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro import config_dynamic, config_flow as cf
from custom_components.adaptive_cover_pro.const import (
    BUILDING_PROFILE_ENTITY_KEYS,
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_SUN_TRACKING_GATE_SENSORS,
    CONF_SUN_TRACKING_GATE_TEMPLATE,
    CONF_SUN_TRACKING_GATE_TEMPLATE_MODE,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    ReasonCode,
)
from tests._helpers.fake_clock import FakeClock


def _schema_keys(schema) -> set[str]:
    return {str(k) for k in schema.schema}


# ---------------------------------------------------------------------------
# Option keys
# ---------------------------------------------------------------------------


def test_gate_option_keys_are_wire_stable():
    """The stored key names are the rollback contract — pin them literally."""
    assert CONF_SUN_TRACKING_GATE_SENSORS == "sun_tracking_gate_sensors"
    assert CONF_SUN_TRACKING_GATE_TEMPLATE == "sun_tracking_gate_template"
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE == "sun_tracking_gate_template_mode"


# ---------------------------------------------------------------------------
# Per-cover schema — the Sun Tracking step
# ---------------------------------------------------------------------------


def test_gate_fields_render_on_the_sun_tracking_step():
    keys = _schema_keys(config_dynamic.sun_tracking_schema())
    assert CONF_SUN_TRACKING_GATE_SENSORS in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in keys


def test_gate_does_not_leak_onto_unrelated_steps():
    """It gates solar, not positions or motion — it must not appear there."""
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.POSITION_SCHEMA)
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.AUTOMATION_SCHEMA)
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.BEHAVIOR_SCHEMA)


def test_gate_template_round_trips_as_cleared():
    """No schema default → voluptuous omits it when cleared, so it needs the
    optional-key treatment or the old value silently survives (issue #439/#1085).
    """
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in cf._SUN_TRACKING_OPTIONAL_KEYS


def test_gate_sensors_default_to_an_empty_list():
    """A cleared multi-select must round-trip as [], never None."""
    marker = next(
        k
        for k in config_dynamic.sun_tracking_schema().schema
        if str(k) == CONF_SUN_TRACKING_GATE_SENSORS
    )
    assert marker.default() == []
    assert CONF_SUN_TRACKING_GATE_SENSORS not in cf._SUN_TRACKING_OPTIONAL_KEYS


def test_gate_template_mode_defaults_to_the_shared_default():
    marker = next(
        k
        for k in config_dynamic.sun_tracking_schema().schema
        if str(k) == CONF_SUN_TRACKING_GATE_TEMPLATE_MODE
    )
    assert marker.default() == DEFAULT_TEMPLATE_COMBINE_MODE


def test_gate_keys_sync_with_the_sun_tracking_category():
    """Copy-to-sibling-cover must carry the gate with the rest of sun tracking."""
    category = cf.SYNC_CATEGORIES["sun_tracking"]
    assert CONF_SUN_TRACKING_GATE_SENSORS in category
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in category
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in category


# ---------------------------------------------------------------------------
# Building profile — set the gate once for the whole building
# ---------------------------------------------------------------------------


def test_gate_keys_are_profile_owned():
    assert CONF_SUN_TRACKING_GATE_SENSORS in BUILDING_PROFILE_SENSOR_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in BUILDING_PROFILE_SENSOR_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in BUILDING_PROFILE_SENSOR_KEYS


def test_gate_fields_render_on_the_profile_screen():
    keys = _schema_keys(config_dynamic.building_profile_sensors_schema())
    assert CONF_SUN_TRACKING_GATE_SENSORS in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in keys


def test_only_the_sensor_list_counts_as_an_entity_key():
    """A template body and a combine mode are config, not entity ids — cataloguing
    them as entities made Troubleshoot flag them as unavailable sensors (#1017).
    """
    assert CONF_SUN_TRACKING_GATE_SENSORS in BUILDING_PROFILE_ENTITY_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE not in BUILDING_PROFILE_ENTITY_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE not in BUILDING_PROFILE_ENTITY_KEYS


def test_profile_gate_merges_into_a_linked_cover():
    """A profile-defined gate reaches an inheriting cover's config."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.profile_link import (
        merge_profile_into_config,
    )

    profile = MagicMock()
    profile.options = {
        CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"],
        CONF_SUN_TRACKING_GATE_TEMPLATE: "{{ true }}",
        CONF_SUN_TRACKING_GATE_TEMPLATE_MODE: "and",
    }
    config: dict = {}
    merge_profile_into_config(profile, config)

    assert config[CONF_SUN_TRACKING_GATE_SENSORS] == ["binary_sensor.ac"]
    assert config[CONF_SUN_TRACKING_GATE_TEMPLATE] == "{{ true }}"
    assert config[CONF_SUN_TRACKING_GATE_TEMPLATE_MODE] == "and"


def test_a_cover_overriding_the_gate_keeps_its_own():
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.profile_link import (
        merge_profile_into_config,
    )

    profile = MagicMock()
    profile.options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.building"]}
    config = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.this_room"]}
    merge_profile_into_config(
        profile, config, overridden=frozenset({CONF_SUN_TRACKING_GATE_SENSORS})
    )

    assert config[CONF_SUN_TRACKING_GATE_SENSORS] == ["binary_sensor.this_room"]


# ---------------------------------------------------------------------------
# Reason code
# ---------------------------------------------------------------------------


def test_gate_skip_has_its_own_reason_code():
    """A closed gate must be distinguishable from "sun outside the FOV" in the trace."""
    from custom_components.adaptive_cover_pro.reason_i18n import _REASON_TEMPLATES_EN

    assert ReasonCode.SKIP_SUN_TRACKING_GATE.value == "skip.sun_tracking_gate"
    assert ReasonCode.SKIP_SUN_TRACKING_GATE in _REASON_TEMPLATES_EN
    assert (
        _REASON_TEMPLATES_EN[ReasonCode.SKIP_SUN_TRACKING_GATE]
        != _REASON_TEMPLATES_EN[ReasonCode.SKIP_SUN_OUTSIDE]
    )


# ---------------------------------------------------------------------------
# Runtime — the solar handler declines and the chain falls through
# ---------------------------------------------------------------------------


def _pipeline(options: dict):
    """Build a real pipeline registry from options, via a bare coordinator."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = MagicMock()
    entry = MagicMock()
    entry.options = options
    coord.config_entry = entry
    return coord._build_pipeline()


def test_solar_wins_when_the_gate_is_open():
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    registry = _pipeline({})
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        enable_sun_tracking=True,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.SOLAR


def test_a_closed_gate_falls_through_to_default():
    """The whole point: solar declines, the chain continues, no forced position."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    registry = _pipeline({})
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        default_position=100,
        enable_sun_tracking=False,  # the gate folds into this field
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.DEFAULT
    assert result.position == 100


def test_the_solar_handler_stays_in_the_chain_even_with_tracking_off():
    """Unlike the old composition-gating, the handler stays and skips.

    Sun tracking is now expressed in exactly one place — ``enable_sun_tracking``
    on the snapshot, which the static toggle and the gate both fold into — rather
    than by two mechanisms (absent handler / declining handler) meaning the same
    thing. The visible payoff is a decision trace that can say WHY solar did not
    run instead of silently omitting it, which is indistinguishable from "never
    configured" to the Lovelace card.
    """
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING
    from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
        SolarHandler,
    )

    registry = _pipeline({CONF_ENABLE_SUN_TRACKING: False})
    assert SolarHandler in {type(h) for h in registry._handlers}


def test_a_closed_gate_reports_the_gate_skip_reason():
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(
        direct_sun_valid=True,
        enable_sun_tracking=False,
        sun_tracking_gate_closed=True,
    )

    assert handler.evaluate(snap) is None
    assert handler.describe_skip(snap).code is ReasonCode.SKIP_SUN_TRACKING_GATE


def test_an_out_of_window_skip_still_outranks_the_gate_reason():
    """Outside the window is the more fundamental reason — it must win the message."""
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=False, in_time_window=False
    )

    assert handler.describe_skip(snap).code is ReasonCode.SKIP_OUTSIDE_WINDOW


def test_sun_outside_the_fov_still_reports_its_own_reason():
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(direct_sun_valid=False, enable_sun_tracking=True)

    assert handler.describe_skip(snap).code is ReasonCode.SKIP_SUN_OUTSIDE


def _solar_handler():
    from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
        SolarHandler,
    )

    return SolarHandler()


def test_a_closed_gate_does_not_suppress_climate():
    """The distinction from the daytime gate, pinned on a climate rule that WINS.

    A dark daytime gate closes ``in_time_window``, which suppresses climate,
    glare zones, cloud suppression and motion timeout along with solar. A closed
    sun-tracking gate must suppress solar and nothing else.

    Both snapshots below are configured so ClimateHandler produces a real result.
    The gated one must still get it; the window-closed one must not. Asserting
    that climate still *wins* is the point — an earlier version of this test
    asserted ``gated.in_time_window is True``, which is ``make_snapshot``'s own
    default and left the headline property unguarded: adding a gate check to
    ``ClimateHandler.evaluate`` kept the whole suite green.
    """
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
        ClimateHandler,
    )
    from tests.test_pipeline.conftest import make_snapshot
    from tests.test_pipeline.test_climate_handler import (
        _make_blind_cover,
        _make_options,
        _make_readings,
    )

    def _climate_snapshot(**overrides):
        return make_snapshot(
            cover=_make_blind_cover(),
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=10.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
            climate_temp_flags=None,
            **overrides,
        )

    climate = ClimateHandler()

    gated = _climate_snapshot(enable_sun_tracking=False, sun_tracking_gate_closed=True)
    windowed = _climate_snapshot(enable_sun_tracking=True, in_time_window=False)

    # A closed sun-tracking gate leaves climate producing its normal result...
    result = climate.evaluate(gated)
    assert result is not None
    assert result.control_method == ControlMethod.WINTER

    # ...whereas a closed window (what the daytime gate does) suppresses it.
    assert climate.evaluate(windowed) is None

    # And through the real pipeline, climate — not solar, not default — wins.
    registry = _pipeline({})
    assert registry.evaluate(gated).control_method == ControlMethod.WINTER


# ---------------------------------------------------------------------------
# The gate itself — driving real options through PipelineSnapshotBuilder
#
# Everything above this point exercises the SNAPSHOT FIELD. These exercise the
# thing that computes it: without them the gate could be wired to nothing and
# the rest of this file would still pass (issue #1167 audit, finding 2).
# ---------------------------------------------------------------------------


def _builder(states: dict, clock=None, template_result=None):
    """Build a PipelineSnapshotBuilder whose HA reads come from an in-memory map."""
    from unittest.mock import MagicMock, patch

    from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
        PipelineSnapshotBuilder,
    )

    builder = PipelineSnapshotBuilder(
        hass=MagicMock(),
        logger=MagicMock(),
        climate_provider=MagicMock(),
        toggles=MagicMock(),
        policy=MagicMock(),
        config_service=MagicMock(),
        clock=clock or FakeClock(),
    )
    # Patch the module globals the gate's injected readers close over — the same
    # seam managers.time_window exposes for the daytime gate.
    ctx = patch.multiple(
        "custom_components.adaptive_cover_pro.pipeline.snapshot_builder",
        get_safe_state=lambda _hass, entity_id: states.get(entity_id),
        render_condition_or_none=lambda _hass, template, variables=None: (
            template_result if template else None
        ),
    )
    return builder, ctx


def _resolve(options: dict, states: dict, clock=None, template_result=None):
    builder, ctx = _builder(states, clock=clock, template_result=template_result)
    with ctx:
        return builder._resolve_sun_tracking(options)


def test_no_gate_configured_tracks():
    """Zero regression: every existing entry has no gate and must be unaffected."""
    assert _resolve({}, {}) == (True, False)


def test_master_toggle_off_is_not_reported_as_a_gate():
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING

    assert _resolve({CONF_ENABLE_SUN_TRACKING: False}, {}) == (False, False)


def test_an_on_gate_sensor_tracks():
    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    assert _resolve(options, {"binary_sensor.ac": "on"}) == (True, False)


def test_an_off_gate_sensor_closes_the_gate():
    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    assert _resolve(options, {"binary_sensor.ac": "off"}) == (False, True)


def test_the_master_toggle_wins_over_an_open_gate():
    """The two AND together — an open gate cannot re-enable a disabled cover."""
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING

    options = {
        CONF_ENABLE_SUN_TRACKING: False,
        CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"],
    }
    assert _resolve(options, {"binary_sensor.ac": "on"}) == (False, False)


def test_a_template_alone_can_close_the_gate():
    options = {CONF_SUN_TRACKING_GATE_TEMPLATE: "{{ false }}"}
    assert _resolve(options, {}, template_result=False) == (False, True)


def test_and_mode_makes_the_template_a_gate_over_the_sensor():
    options = {
        CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"],
        CONF_SUN_TRACKING_GATE_TEMPLATE: "{{ x }}",
        CONF_SUN_TRACKING_GATE_TEMPLATE_MODE: "and",
    }
    assert _resolve(options, {"binary_sensor.ac": "on"}, template_result=False) == (
        False,
        True,
    )
    assert _resolve(options, {"binary_sensor.ac": "on"}, template_result=True) == (
        True,
        False,
    )


def test_or_mode_lets_either_source_open_the_gate():
    options = {
        CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"],
        CONF_SUN_TRACKING_GATE_TEMPLATE: "{{ x }}",
        CONF_SUN_TRACKING_GATE_TEMPLATE_MODE: "or",
    }
    assert _resolve(options, {"binary_sensor.ac": "off"}, template_result=True) == (
        True,
        False,
    )


def test_an_unavailable_sensor_fails_open_to_tracking():
    """#1012/#1014: a dropout must never silently disable tracking.

    With no last-known verdict there is nothing to hold, so the gate reports no
    opinion immediately and ``resolved(default=True)`` tracks.
    """
    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    assert _resolve(options, {"binary_sensor.ac": None}) == (True, False)


def test_a_dropout_holds_the_last_verdict_then_fails_open():
    """Closed → source dies → held closed through the grace window → opens."""
    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    states = {"binary_sensor.ac": "off"}
    clock = FakeClock()
    builder, ctx = _builder(states, clock=clock)

    with ctx:
        assert builder._resolve_sun_tracking(options) == (False, True)

        states["binary_sensor.ac"] = None
        assert builder._resolve_sun_tracking(options) == (False, True)  # holding

        clock.advance(60.0)
        assert builder._resolve_sun_tracking(options) == (False, True)  # still holding

        clock.advance(61.0)  # past the 120s grace window
        assert builder._resolve_sun_tracking(options) == (True, False)  # fails OPEN


def test_the_grace_wake_is_armed_only_while_holding():
    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    states = {"binary_sensor.ac": "off"}
    clock = FakeClock()
    builder, ctx = _builder(states, clock=clock)

    with ctx:
        builder._resolve_sun_tracking(options)
        assert builder.seconds_until_sun_tracking_gate_fallback(options) is None

        states["binary_sensor.ac"] = None
        builder._resolve_sun_tracking(options)
        assert builder.seconds_until_sun_tracking_gate_fallback(
            options
        ) == pytest.approx(120.0)


# The option->snapshot binding that composition-gating used to prove is guarded
# at the seam that actually performs it — ``builder.build()`` — in
# tests/test_pipeline/test_snapshot_builder.py::
# test_build_carries_sun_tracking_onto_the_snapshot. Asserting it here against
# ``_resolve_sun_tracking`` would look like an end-to-end guard while stopping
# one call short of the thing that can actually break.


# ---------------------------------------------------------------------------
# The skip reason names the real cause
# ---------------------------------------------------------------------------


def test_the_toggle_and_the_gate_report_different_skip_reasons():
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()

    toggle_off = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=False, sun_tracking_gate_closed=False
    )
    gate_closed = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=False, sun_tracking_gate_closed=True
    )

    assert handler.describe_skip(toggle_off).code is ReasonCode.SKIP_SUN_TRACKING_OFF
    assert handler.describe_skip(gate_closed).code is ReasonCode.SKIP_SUN_TRACKING_GATE


# ---------------------------------------------------------------------------
# Coordinator plumbing — the grace wake and the template tracker
#
# Mirrors the five-test shape both precedents ship
# (tests/test_issue_632_daytime_gate.py, tests/test_issue_1012_*.py). Worth
# spelling out: tests/conftest.py sets expected_lingering_timers = True for
# integration tests, so a wake handle that is never cancelled leaks WITHOUT
# failing anything. The shutdown test below is the only thing guarding it.
# ---------------------------------------------------------------------------


def _coord_for_wake(secs, options=None):
    """Build a minimal coordinator stub for the sun-tracking-gate wake scheduler."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord._sun_tracking_gate_unsub = None
    entry = MagicMock()
    entry.options = options if options is not None else {}
    coord.config_entry = entry
    builder = MagicMock()
    builder.seconds_until_sun_tracking_gate_fallback.return_value = secs
    coord._snapshot_builder = builder
    return coord


def test_the_wake_reads_the_live_options():
    """The coordinator must hand the builder its options, or the toggle guard is dead.

    The guard that stops a disabled cover arming a wake lives in the builder and
    keys off ``options``; if the coordinator stopped passing them the guard would
    silently never engage. Asserting the call args on the stub builder is exactly
    what catches that.
    """
    from unittest.mock import patch

    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING

    opts = {CONF_ENABLE_SUN_TRACKING: True}
    coord = _coord_for_wake(secs=None, options=opts)
    with patch("custom_components.adaptive_cover_pro.coordinator.async_call_later"):
        coord._schedule_sun_tracking_gate_wake()
    coord._snapshot_builder.seconds_until_sun_tracking_gate_fallback.assert_called_once_with(
        opts
    )


def test_the_wake_is_scheduled_while_the_gate_is_holding():
    from unittest.mock import MagicMock, patch

    coord = _coord_for_wake(secs=42.0)
    cancel = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=cancel,
    ) as m:
        coord._schedule_sun_tracking_gate_wake()
    m.assert_called_once()
    assert m.call_args.args[0] is coord.hass
    assert m.call_args.args[1] == 42.0
    assert coord._sun_tracking_gate_unsub is cancel


def test_no_wake_is_scheduled_while_the_gate_is_determinate():
    from unittest.mock import patch

    coord = _coord_for_wake(secs=None)
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later"
    ) as m:
        coord._schedule_sun_tracking_gate_wake()
    m.assert_not_called()
    assert coord._sun_tracking_gate_unsub is None


def test_a_new_wake_cancels_the_previous_one():
    from unittest.mock import MagicMock, patch

    coord = _coord_for_wake(secs=10.0)
    previous = MagicMock()
    coord._sun_tracking_gate_unsub = previous
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        coord._schedule_sun_tracking_gate_wake()
    previous.assert_called_once()


async def test_the_due_callback_clears_the_handle_and_refreshes():
    from unittest.mock import AsyncMock, MagicMock

    coord = _coord_for_wake(secs=None)
    coord._sun_tracking_gate_unsub = MagicMock()
    coord.async_request_refresh = AsyncMock()
    await coord._on_sun_tracking_gate_due(None)
    assert coord._sun_tracking_gate_unsub is None
    coord.async_request_refresh.assert_awaited_once()


async def test_shutdown_cancels_the_wake_handle():
    """The only guard against a leaked timer — lingering timers cannot fail a test."""
    from unittest.mock import MagicMock

    from tests.ha_helpers import _bare_coordinator

    cancel = MagicMock()
    coord = _bare_coordinator(sun_tracking_gate_unsub=cancel)
    await coord.async_shutdown()
    cancel.assert_called_once()
    assert coord._sun_tracking_gate_unsub is None


async def test_a_template_flip_triggers_a_refresh():
    """The tracked template result only signals *that* it changed; the refresh re-reads."""
    from unittest.mock import AsyncMock

    coord = _coord_for_wake(secs=None)
    coord.async_refresh = AsyncMock()
    coord.state_change = False
    await coord.async_check_sun_tracking_gate_template_change(None, [])
    assert coord.state_change is True
    coord.async_refresh.assert_awaited_once()


def test_no_wake_is_armed_while_the_master_toggle_is_off():
    """A disabled cover must not anchor a grace window it can never act on.

    ``ConditionGate.seconds_until_fallback`` *observes* in order to answer, so an
    unguarded call would arm a wake for a gate whose verdict cannot change what
    the cover does. The guard lives in the builder rather than at the coordinator
    call site so it holds for every caller.
    """
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING

    options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"]}
    states = {"binary_sensor.ac": "off"}
    builder, ctx = _builder(states)

    with ctx:
        builder._resolve_sun_tracking(options)
        states["binary_sensor.ac"] = None
        builder._resolve_sun_tracking(options)
        # Tracking on: a held verdict arms a wake.
        assert builder.seconds_until_sun_tracking_gate_fallback(options) is not None
        # Tracking off: nothing to wake for.
        off = {**options, CONF_ENABLE_SUN_TRACKING: False}
        assert builder.seconds_until_sun_tracking_gate_fallback(off) is None
