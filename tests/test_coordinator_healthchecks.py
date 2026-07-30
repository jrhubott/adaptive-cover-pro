"""Coordinator wiring for the non-sensor health checks (issue #975).

Covers A1 (controlled-cover availability, per entity + stale-cover unwatch),
C1 (``sun.sun`` availability), B1 (position-envelope coherence), and B2
(time-window coherence). All four are informational Repairs raised/cleared
through the shared debounced lifecycle. The wiring lives in one fail-open guard
so no health check can break the update cycle, and every issue key is
per-config-entry namespaced.

The tests drive ``_evaluate_health_checks`` directly against a minimal
coordinator stub (built without ``__init__``) wired with real
``SensorHealthManager`` / ``RepairManager`` instances (debounce 0 so a raise
lands after one event-loop drain), patching the issue registry.

Because these stubs set ``policy._control_model`` by hand, they cannot observe
the *coordinator ordering* the A3 model-aware relaxation depends on. That guard
is an integration test and lives in
``tests/test_issue_1114_control_model_ordering.py``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    DAY_NIGHT_MODEL_SPLIT_RANGE,
    DOMAIN,
    ISSUE_CONFIG_POSITION_ENVELOPE,
    ISSUE_CONFIG_TIME_WINDOW,
    ISSUE_COVER_NOT_MOVING,
    ISSUE_COVER_TILT_UNSUPPORTED,
    ISSUE_COVER_UNAVAILABLE,
    ISSUE_SUN_UNAVAILABLE,
    ISSUE_TEMP_SENSOR_UNAVAILABLE,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.repair import RepairManager
from custom_components.adaptive_cover_pro.managers.sensor_health import (
    SensorHealthManager,
)
from tests.ha_helpers import _bare_coordinator

pytestmark = pytest.mark.unit

_BASE = "custom_components.adaptive_cover_pro.managers.common.debounced_repair"
_COORD = "custom_components.adaptive_cover_pro.coordinator"
_ENTRY = "entry1"


class _State:
    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        # A3 (issue #991) reads capabilities via ``check_cover_features``, which
        # touches ``state.attributes``. Default to an empty dict so the real
        # helper returns optimistic (position-only, no-tilt) defaults for a
        # readable cover rather than raising — A3 tests patch the helper directly
        # when they need specific capabilities.
        self.attributes = attributes if attributes is not None else {}


class _States:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._d = {k: _State(v) for k, v in mapping.items()}

    def get(self, entity_id: str):
        return self._d.get(entity_id)


class _Hass:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.states = _States(mapping)

    def async_create_background_task(self, coro, name=None, eager_start=True):
        # Mirror HA's tracked-task helper so the debounce timer (now spawned
        # via async_create_background_task for leak-free cleanup) runs under
        # the test event loop and a single _drain() fires it.
        return asyncio.create_task(coro)


async def _drain():
    for _ in range(4):
        await asyncio.sleep(0)


def _make_coord(
    *,
    states: dict[str, str] | None = None,
    entities: list[str] | None = None,
    options: dict | None = None,
    inside_temp: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    debounce: float = 0,
    policy=None,
):
    """Build a minimal coordinator stub wired for _evaluate_health_checks.

    Starts from ``_bare_coordinator()`` (the async_shutdown-collaborator stub
    shared with ``tests/test_issue_632_daytime_gate.py``) so ``async_shutdown``
    can run against this fixture too, then layers on the health-check-specific
    wiring, overwriting ``_sensor_health``/``_repair``/``_cmd_svc`` with the
    real instances this module's tests actually need.
    """
    hass = _Hass(states if states is not None else {"sun.sun": "above_horizon"})
    logger = logging.getLogger("test.coord_health")
    coord = _bare_coordinator()
    coord.hass = hass
    coord.logger = logger
    coord.config_entry = SimpleNamespace(entry_id=_ENTRY, data={"name": "Bedroom"})
    coord.entities = entities if entities is not None else []
    coord._weather_readings = SimpleNamespace(inside_temperature_entity_id=inside_temp)
    coord._time_mgr = SimpleNamespace(resolved_start_time=start, end_time=end)
    coord._sensor_health = SensorHealthManager(
        hass, logger, domain=DOMAIN, debounce_seconds=debounce
    )
    coord._repair = RepairManager(
        hass, logger, domain=DOMAIN, debounce_seconds=debounce
    )
    coord._temp_issue_key = f"{ISSUE_TEMP_SENSOR_UNAVAILABLE}_{_ENTRY}"
    coord._sun_issue_key = f"{ISSUE_SUN_UNAVAILABLE}_{_ENTRY}"
    coord._envelope_issue_key = f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}"
    coord._time_window_issue_key = f"{ISSUE_CONFIG_TIME_WINDOW}_{_ENTRY}"
    coord._cover_issue_keys = set()
    coord._a1_orphans_swept = False
    # A2 (issue #990): the command service supplies the per-entity
    # "commanded but not reached" predicate. Default False so A1/B1/B2/C1
    # tests see no spurious A2 Repairs; A2 tests override the return value.
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.is_target_unreached.return_value = False
    coord._a2_issue_keys = set()
    # A3 (issue #991): the active cover-type policy answers the tilt-capability
    # contradiction predicate. Default to a NON-tilt policy (blind) so the
    # A1/A2/B/C tests never see a spurious A3 Repair; A3 tests pass a
    # tilt-declaring policy explicitly.
    coord._policy = policy if policy is not None else get_policy("cover_blind")
    coord._a3_issue_keys = set()
    coord._resolved_options = options or {}
    return coord


def _raised_keys(create_mock) -> set[str]:
    """Issue keys passed to ir.async_create_issue (3rd positional arg)."""
    return {call.args[2] for call in create_mock.call_args_list}


async def _run(coord, options):
    with (
        patch(f"{_BASE}.ir.async_create_issue") as create,
        patch(f"{_BASE}.ir.async_delete_issue") as delete,
    ):
        coord._evaluate_health_checks(options)
        await _drain()
        return create, delete


# --- A1: controlled-cover availability -------------------------------------


async def test_a1_raises_per_unavailable_cover_only():
    coord = _make_coord(
        states={
            "sun.sun": "above_horizon",
            "cover.a": "unavailable",
            "cover.b": "open",
        },
        entities=["cover.a", "cover.b"],
    )
    create, _delete = await _run(coord, {})
    raised = _raised_keys(create)
    assert f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.a" in raised
    assert f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.b" not in raised


async def test_a1_missing_cover_is_unhealthy():
    coord = _make_coord(
        states={"sun.sun": "above_horizon"},  # cover.gone has no state
        entities=["cover.gone"],
    )
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.gone" in _raised_keys(create)


async def test_a1_stale_cover_is_unwatched_and_cleared():
    """A cover dropped from config has its active Repair cleared."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "unavailable"},
        entities=["cover.a"],
    )
    # Cycle 1: cover.a unavailable → raise + tracked in _cover_issue_keys.
    await _run(coord, {})
    assert f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.a" in coord._cover_issue_keys
    # Cycle 2: cover.a removed from config → its issue is deleted (unwatch).
    coord.entities = []
    _create, delete = await _run(coord, {})
    assert f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.a" in {
        call.args[2] for call in delete.call_args_list
    }
    assert coord._cover_issue_keys == set()


# --- A1: cross-lifetime orphan sweep (issue #975 audit) --------------------


def _sweep_run(coord, options, registry):
    """Run one health-check cycle with the coordinator issue registry patched.

    Patches ``ir`` in both the debounced-repair module (raise/clear of live
    Repairs) and the coordinator module (the one-time orphan sweep, which calls
    ``ir.async_get`` + ``ir.async_delete_issue`` directly).
    """
    with (
        patch(f"{_BASE}.ir.async_create_issue"),
        patch(f"{_BASE}.ir.async_delete_issue"),
        patch(f"{_COORD}.ir.async_get", return_value=registry) as reg_get,
        patch(f"{_COORD}.ir.async_delete_issue") as coord_delete,
    ):
        coord._evaluate_health_checks(options)
        return reg_get, coord_delete


def _swept_keys(coord_delete) -> set[str]:
    """Issue ids the coordinator sweep deleted (3rd positional arg)."""
    return {call.args[2] for call in coord_delete.call_args_list}


async def test_a1_orphan_cleared_on_reload_after_cover_removed():
    """A Repair for a cover no longer configured is swept once per lifetime.

    Removing a cover (the fix path for a cover_unavailable Repair) reloads the
    config entry → fresh coordinator with an empty ``_cover_issue_keys``. The
    removed cover's key is in neither ``desired`` nor the in-lifetime unwatch
    loop, so only the registry sweep can clear it.
    """
    orphan = f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.old"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.current": "open"},
        entities=["cover.current"],  # cover.old was removed from config
    )
    registry = SimpleNamespace(issues={(DOMAIN, orphan): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert orphan in _swept_keys(coord_delete)


async def test_a1_configured_unavailable_cover_not_swept():
    """A still-configured but unavailable cover keeps its Repair (in ``desired``).

    Sweeping its key would clear a valid warning and force a debounce re-raise
    flap on every restart.
    """
    key = f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.a"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "unavailable"},
        entities=["cover.a"],  # still configured, just unavailable
    )
    registry = SimpleNamespace(issues={(DOMAIN, key): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert key not in _swept_keys(coord_delete)


async def test_a1_orphan_sweep_runs_once_per_lifetime():
    """The registry enumeration fires a single time per coordinator lifetime."""
    orphan = f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.old"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.current": "open"},
        entities=["cover.current"],
    )
    registry = SimpleNamespace(issues={(DOMAIN, orphan): object()})
    reg_get, _coord_delete = _sweep_run(coord, {}, registry)
    _sweep_run(coord, {}, registry)  # second cycle: sweep already done
    await _drain()
    assert reg_get.call_count == 1


async def test_a1_sweep_ignores_other_entry_and_domain():
    """The sweep only touches this entry's A1 prefix under this integration.

    ``other_entry`` (a different config entry) and ``other_domain`` (a different
    integration) share the A1 shape but must survive. Both issue ids are never
    watched, so their absence from the delete calls is attributable to the
    sweep's prefix/domain filter alone (nothing else would delete them).
    """
    orphan = f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.old"
    other_entry = f"{ISSUE_COVER_UNAVAILABLE}_entryOTHER_cover.old"
    other_domain_id = f"{ISSUE_COVER_UNAVAILABLE}_{_ENTRY}_cover.zzz"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.current": "open"},
        entities=["cover.current"],
    )
    registry = SimpleNamespace(
        issues={
            (DOMAIN, orphan): object(),
            (DOMAIN, other_entry): object(),
            ("other_domain", other_domain_id): object(),
        }
    )
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    swept = _swept_keys(coord_delete)
    assert orphan in swept
    assert other_entry not in swept
    assert other_domain_id not in swept


# --- C1: sun.sun availability ----------------------------------------------


async def test_c1_raises_when_sun_unavailable():
    coord = _make_coord(states={"sun.sun": "unavailable"}, entities=[])
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_SUN_UNAVAILABLE}_{_ENTRY}" in _raised_keys(create)


async def test_c1_no_raise_when_sun_healthy():
    coord = _make_coord(states={"sun.sun": "above_horizon"}, entities=[])
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_SUN_UNAVAILABLE}_{_ENTRY}" not in _raised_keys(create)


# --- B1: position envelope --------------------------------------------------


async def test_b1_inverted_envelope_raises():
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, {CONF_MIN_POSITION: 80, CONF_MAX_POSITION: 20})
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" in _raised_keys(create)


def _envelope_call(create_mock):
    """Return the ir.async_create_issue call that raised the envelope Repair."""
    key = f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}"
    for call in create_mock.call_args_list:
        if call.args[2] == key:
            return call
    return None


async def test_b1_placeholders_render_as_int():
    """min/max placeholders render as plain ints even when HA stores floats."""
    coord = _make_coord(entities=[])
    # NumberSelector hands back floats — the Repair text must not read "80.0".
    create, _delete = await _run(
        coord, {CONF_MIN_POSITION: 80.0, CONF_MAX_POSITION: 20.0}
    )
    call = _envelope_call(create)
    assert call is not None
    placeholders = call.kwargs["translation_placeholders"]
    assert placeholders["min"] == "80"
    assert placeholders["max"] == "20"


async def test_b1_pinned_slot_outside_envelope_raises():
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: True,
        slot["position"]: 80,  # above max
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" in _raised_keys(create)


async def test_b1_use_my_slot_outside_envelope_no_raise():
    """A ``use_my`` slot routes to the hardware My preset — its stored position
    is ignored, so it cannot conflict with the envelope.
    """
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: True,
        slot["position"]: 80,  # outside, but not the delivered position
        slot["use_my"]: True,
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


async def test_b1_tilt_only_slot_outside_envelope_no_raise():
    """A ``tilt_only`` slot fixes only the slat angle; solar drives position, so
    the stored position value is not a fixed-position claim.
    """
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: True,
        slot["position"]: 80,
        slot["tilt_only"]: True,
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


async def test_b1_nonfixed_mode_slot_outside_envelope_no_raise():
    """A non-FIXED constraint-mode slot (here a ``min_mode`` floor) composes as a
    constraint and never overrides the envelope with an exact position.
    """
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: True,
        slot["position"]: 80,  # a FLOOR (min_mode), not an exact position
        slot["min_mode"]: True,
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


async def test_b1_safety_slot_ignored():
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: True,
        slot["position"]: 80,
        slot["priority"]: CUSTOM_POSITION_SAFETY_PRIORITY,  # safety → exempt
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


async def test_b1_disabled_slot_ignored():
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 50,
        slot["enabled"]: False,
        slot["position"]: 80,
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


async def test_b1_coherent_envelope_no_raise():
    slot = CUSTOM_POSITION_SLOTS[1]
    options = {
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 100,
        slot["enabled"]: True,
        slot["position"]: 50,
    }
    coord = _make_coord(entities=[])
    create, _delete = await _run(coord, options)
    assert f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{_ENTRY}" not in _raised_keys(create)


# --- B2: time window --------------------------------------------------------


async def test_b2_inverted_window_raises():
    start = dt.datetime(2026, 7, 19, 18, 0)
    end = dt.datetime(2026, 7, 19, 8, 0)
    coord = _make_coord(entities=[], start=start, end=end)
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_CONFIG_TIME_WINDOW}_{_ENTRY}" in _raised_keys(create)


async def test_b2_coherent_window_no_raise():
    start = dt.datetime(2026, 7, 19, 8, 0)
    end = dt.datetime(2026, 7, 19, 18, 0)
    coord = _make_coord(entities=[], start=start, end=end)
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_CONFIG_TIME_WINDOW}_{_ENTRY}" not in _raised_keys(create)


async def test_b2_one_side_none_no_raise():
    # Entity-provided start unavailable (None) → do not false-fire.
    coord = _make_coord(entities=[], start=None, end=dt.datetime(2026, 7, 19, 8, 0))
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_CONFIG_TIME_WINDOW}_{_ENTRY}" not in _raised_keys(create)


# --- fail-open + shutdown ---------------------------------------------------


async def test_health_checks_fail_open():
    """A predicate that raises must not break the cycle."""
    coord = _make_coord(entities=[])
    # Force an error inside the guard: weather readings access explodes.
    coord._weather_readings = None
    with (
        patch(f"{_BASE}.ir.async_create_issue"),
        patch(f"{_BASE}.ir.async_delete_issue"),
    ):
        # Must not raise.
        coord._evaluate_health_checks({})
        await _drain()


async def test_shutdown_cancels_both_managers():
    """async_shutdown cancels in-flight timers on both health managers."""
    coord = _make_coord(
        states={"sun.sun": "unavailable", "cover.a": "unavailable"},
        entities=["cover.a"],
        start=dt.datetime(2026, 7, 19, 18, 0),
        end=dt.datetime(2026, 7, 19, 8, 0),
        debounce=100,
    )
    # _make_coord's _bare_coordinator() base already stubs the rest of
    # async_shutdown's cleanup collaborators (grace_mgr, motion/weather
    # cancel, cmd_svc, and the unsub handles).
    with (
        patch(f"{_BASE}.ir.async_create_issue") as create,
        patch(f"{_BASE}.ir.async_delete_issue"),
    ):
        coord._evaluate_health_checks({CONF_MIN_POSITION: 80, CONF_MAX_POSITION: 20})
        # Both managers now have in-flight (long) debounce timers.
        assert coord._sensor_health._timers
        assert coord._repair._timers
        await coord.async_shutdown()
        await _drain()
    create.assert_not_called()


# --- A2: commanded-but-unreached (issue #990) ------------------------------


async def test_a2_raises_when_cover_stuck_past_debounce():
    """A settled off-target cover raises cover_not_moving after the debounce."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )
    coord._cmd_svc.is_target_unreached.side_effect = lambda eid: eid == "cover.a"
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a" in _raised_keys(create)


async def test_a2_does_not_raise_before_debounce():
    """A single stuck cycle under a long debounce does not raise (debounce gate)."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        debounce=100,
    )
    coord._cmd_svc.is_target_unreached.return_value = True
    create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a" not in _raised_keys(create)
    coord._repair.shutdown()  # cancel the in-flight (long) debounce timer


async def test_a2_clears_on_recovery():
    """Once the cover reaches target, the A2 Repair is deleted."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )
    key = f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a"
    coord._cmd_svc.is_target_unreached.return_value = True
    await _run(coord, {})  # cycle 1: raise
    coord._cmd_svc.is_target_unreached.return_value = False
    _create, delete = await _run(coord, {})  # cycle 2: recovered
    assert key in {call.args[2] for call in delete.call_args_list}


async def test_a2_no_false_positive_on_slow_cover():
    """A cover whose predicate stays False (still moving) never raises."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )
    coord._cmd_svc.is_target_unreached.return_value = False
    create, _delete = await _run(coord, {})
    await _run(coord, {})
    assert f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a" not in _raised_keys(create)


async def test_a2_fail_open_on_predicate_exception():
    """A raising A2 predicate must not starve A1/B1/B2/C1 (issue #990 audit).

    The per-entity ``is_target_unreached`` call has its own narrow guard, so an
    entity whose predicate explodes is skipped — the A2 loop still finishes,
    ``evaluate()`` runs, and a concurrently-unhealthy C1 (``sun.sun``
    unavailable) Repair STILL raises. Without the per-entity guard the exception
    would abort the try before ``evaluate()`` and silently block every other
    check. The outer fail-open guard stays as belt-and-suspenders, but this
    per-entity guard is what preserves the original design guarantee.
    """
    coord = _make_coord(
        states={"sun.sun": "unavailable", "cover.a": "open"},
        entities=["cover.a"],
    )
    coord.logger = MagicMock()
    coord._cmd_svc.is_target_unreached.side_effect = RuntimeError("boom")
    # Patch the orphan-sweep registry read too, so the ONLY exception source is
    # the A2 predicate — otherwise the sweep's async_get would trip the guard on
    # the mock hass and mask whether A2 is actually inside it.
    with (
        patch(f"{_BASE}.ir.async_create_issue") as create,
        patch(f"{_BASE}.ir.async_delete_issue"),
        patch(f"{_COORD}.ir.async_get", return_value=SimpleNamespace(issues={})),
        patch(f"{_COORD}.ir.async_delete_issue"),
    ):
        # Must not raise — the per-entity guard swallows the predicate exception.
        coord._evaluate_health_checks({})
        await _drain()
    # C1 still evaluated despite the A2 predicate blowing up: the sun_unavailable
    # Repair still raises. This is the original design guarantee.
    assert f"{ISSUE_SUN_UNAVAILABLE}_{_ENTRY}" in _raised_keys(create)
    # The per-entity guard logged the skip exactly once (debug logged once).
    coord.logger.debug.assert_called_once()
    assert "A2 predicate failed" in coord.logger.debug.call_args.args[0]
    assert coord.logger.debug.call_args.args[1] == "cover.a"


async def test_a2_stale_key_cleared_when_cover_removed():
    """A cover dropped from config has its A2 predicate cleared (in-lifetime)."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )
    key = f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a"
    coord._cmd_svc.is_target_unreached.return_value = True
    await _run(coord, {})  # cycle 1: cover.a tracked
    assert key in coord._a2_issue_keys
    coord.entities = []
    _create, delete = await _run(coord, {})  # cycle 2: cover removed
    assert key in {call.args[2] for call in delete.call_args_list}
    assert coord._a2_issue_keys == set()


async def test_a2_orphan_cleared_on_reload_after_cover_removed():
    """An A2 Repair for a cover no longer configured is swept once per lifetime."""
    orphan = f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.old"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.current": "open"},
        entities=["cover.current"],
    )
    registry = SimpleNamespace(issues={(DOMAIN, orphan): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert orphan in _swept_keys(coord_delete)


async def test_a2_configured_stuck_cover_not_swept():
    """A still-configured stuck cover keeps its A2 Repair (in ``a2_desired``)."""
    key = f"{ISSUE_COVER_NOT_MOVING}_{_ENTRY}_cover.a"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )
    coord._cmd_svc.is_target_unreached.return_value = True
    registry = SimpleNamespace(issues={(DOMAIN, key): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert key not in _swept_keys(coord_delete)


# --- A3: tilt cover type bound to a non-tilt-capable device (issue #991) -----
#
# A3 is a config-vs-hardware coherence check: a tilt-declaring cover type
# (venetian / tilt / louvered_roof) bound to a cover whose supported_features
# lacks set_tilt_position can never honour its tilt behaviour. Predicate-shaped
# like B1/B2 and per-entity like A1/A2, so it rides the same RepairManager. The
# contradiction test lives on the policy (``tilt_capability_contradiction``);
# the coordinator reads RAW capabilities via ``check_cover_features`` (None when
# a cover is still loading) so an unreadable cover never false-fires. Open/close
# only covers are OUT of scope and must never raise.

# Capability dicts as ``check_cover_features`` returns them (helper shape).
_NO_TILT = {"has_set_position": True, "has_set_tilt_position": False}
_WITH_TILT = {"has_set_position": True, "has_set_tilt_position": True}
_OPEN_CLOSE_ONLY = {"has_open": True, "has_close": True}


async def test_a3_raises_for_tilt_type_without_set_tilt_position():
    """A tilt-declaring type on a device lacking set_tilt_position raises."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" in _raised_keys(create)


async def test_a3_no_raise_for_open_close_only_cover():
    """A blind/awning (no declared tilt axis) never raises A3, even open/close only."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
    )  # default policy = blind (no tilt axis)
    with patch(f"{_COORD}.check_cover_features", return_value=_OPEN_CLOSE_ONLY):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" not in _raised_keys(
        create
    )


async def test_a3_no_raise_for_tilt_type_with_set_tilt_position():
    """A tilt-declaring type on a tilt-capable device is coherent — no raise."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_WITH_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" not in _raised_keys(
        create
    )


async def test_a3_no_raise_for_day_night_split_range_on_position_only_cover():
    """A Model B (split_range) day/night shade needs only set_position (#991).

    The day/night policy statically declares a tilt axis, but the single-carriage
    split-range model drives only the carriage. A position-only cover is its
    intended, supported hardware, so A3 must NOT raise a cover_tilt_unsupported
    Repair that could never clear.
    """
    policy = get_policy("cover_day_night_shade")
    policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=policy,
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" not in _raised_keys(
        create
    )


async def test_a3_no_raise_for_day_night_dual_entity_on_position_only_cover():
    """A Model C (dual_entity) day/night shade needs only set_position (#991).

    Same single-carriage relaxation as split-range: the blend folds into a
    second entity's position axis, never a physical tilt axis, so a position-only
    cover is coherent and must not raise A3.
    """
    policy = get_policy("cover_day_night_shade")
    policy._control_model = DAY_NIGHT_MODEL_DUAL_ENTITY
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=policy,
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" not in _raised_keys(
        create
    )


async def test_a3_raises_for_day_night_model_a_on_position_only_cover():
    """A Model A (position_tilt) day/night shade DOES need a physical tilt axis.

    The dual-axis model drives the fabric blend on a real tilt axis, so binding
    it to a position-only cover is a genuine contradiction that must raise A3 —
    the model-aware override relaxes B/C without masking A.
    """
    policy = get_policy("cover_day_night_shade")  # default model = position_tilt (A)
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=policy,
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" in _raised_keys(create)


async def test_a3_no_false_fire_on_unknown_caps():
    """An unreadable cover (``check_cover_features`` → None) never raises A3.

    ``check_cover_features`` returns None while a cover is still loading /
    unavailable. Reading the masked ``read_single_capabilities`` would surface
    has_set_tilt_position=False and false-fire; reading the raw helper and
    skipping None does not. The skipped cover is also NOT tracked, so the stale
    loop can't act on a key it never added.
    """
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    key = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a"
    with patch(f"{_COORD}.check_cover_features", return_value=None):
        create, _delete = await _run(coord, {})
    assert key not in _raised_keys(create)
    assert key not in coord._a3_issue_keys


async def test_a3_does_not_raise_before_debounce():
    """A single contradictory cycle under a long debounce does not raise."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
        debounce=100,
    )
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        create, _delete = await _run(coord, {})
    assert f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a" not in _raised_keys(
        create
    )
    coord._repair.shutdown()  # cancel the in-flight (long) debounce timer


async def test_a3_clears_on_recovery():
    """Once the device gains tilt support, the A3 Repair is deleted."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    key = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a"
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        await _run(coord, {})  # cycle 1: raise
    with patch(f"{_COORD}.check_cover_features", return_value=_WITH_TILT):
        _create, delete = await _run(coord, {})  # cycle 2: recovered
    assert key in {call.args[2] for call in delete.call_args_list}


async def test_a3_stale_key_cleared_when_cover_removed():
    """A cover dropped from config has its A3 predicate cleared (in-lifetime)."""
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    key = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a"
    with patch(f"{_COORD}.check_cover_features", return_value=_NO_TILT):
        await _run(coord, {})  # cycle 1: cover.a tracked
    assert key in coord._a3_issue_keys
    coord.entities = []
    _create, delete = await _run(coord, {})  # cycle 2: cover removed
    assert key in {call.args[2] for call in delete.call_args_list}
    assert coord._a3_issue_keys == set()


async def test_a3_orphan_cleared_on_reload_after_cover_removed():
    """An A3 Repair for a cover no longer configured is swept once per lifetime."""
    orphan = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.old"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.current": "open"},
        entities=["cover.current"],
        policy=get_policy("cover_venetian"),
    )
    registry = SimpleNamespace(issues={(DOMAIN, orphan): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert orphan in _swept_keys(coord_delete)


async def test_a3_configured_contradiction_not_swept():
    """A still-configured contradictory cover keeps its A3 Repair (in ``a3_desired``)."""
    key = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{_ENTRY}_cover.a"
    coord = _make_coord(
        states={"sun.sun": "above_horizon", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    registry = SimpleNamespace(issues={(DOMAIN, key): object()})
    _reg_get, coord_delete = _sweep_run(coord, {}, registry)
    await _drain()
    assert key not in _swept_keys(coord_delete)


async def test_a3_fail_open_on_predicate_exception():
    """A raising A3 predicate must not starve A1/A2/B/C1 (mirrors the A2 audit).

    The per-entity capability-read + predicate call has its own narrow guard, so
    an entity whose read explodes is skipped — the A3 loop still finishes,
    ``evaluate()`` runs, and a concurrently-unhealthy C1 (``sun.sun``
    unavailable) Repair STILL raises. The outer fail-open guard stays as
    belt-and-suspenders, but this per-entity guard preserves the design
    guarantee.
    """
    coord = _make_coord(
        states={"sun.sun": "unavailable", "cover.a": "open"},
        entities=["cover.a"],
        policy=get_policy("cover_venetian"),
    )
    coord.logger = MagicMock()
    with (
        patch(f"{_BASE}.ir.async_create_issue") as create,
        patch(f"{_BASE}.ir.async_delete_issue"),
        patch(f"{_COORD}.ir.async_get", return_value=SimpleNamespace(issues={})),
        patch(f"{_COORD}.ir.async_delete_issue"),
        patch(f"{_COORD}.check_cover_features", side_effect=RuntimeError("boom")),
    ):
        # Must not raise — the per-entity guard swallows the read/predicate error.
        coord._evaluate_health_checks({})
        await _drain()
    # C1 still evaluated despite the A3 read blowing up: sun_unavailable raises.
    assert f"{ISSUE_SUN_UNAVAILABLE}_{_ENTRY}" in _raised_keys(create)
    # The per-entity guard logged the skip exactly once.
    coord.logger.debug.assert_called_once()
    assert "A3 predicate failed" in coord.logger.debug.call_args.args[0]
    assert coord.logger.debug.call_args.args[1] == "cover.a"
