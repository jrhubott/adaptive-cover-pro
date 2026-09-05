"""Issue #1022 — restore the last-commanded target across an ACP reload.

After an integration reload the ``CoverCommandService`` command-target store is
rebuilt empty, so ``get_target`` returns ``None`` and the coordinator computes
``has_recorded_target=False``. A context-less physical-remote move on a cover
that is resting on its last commanded position is then forced through the
guard-less ``not has_recorded_target`` rescue branch in
``PositionDeltaDetector.detect`` — which cannot reliably rescue an assumed-state
open/close cover (no reliable ``old_position``), so the user's move is dropped
and ACP re-closes the cover on its next cycle (regresses #654/#662).

The fix persists the per-entity commanded target through the
``position_verification`` sensor (RestoreEntity) and rehydrates
``CoverCommandService._state[eid].target`` on setup, so ``has_recorded_target``
is True again post-reload and the move flows through the fully-guarded NORMAL
detection path.

Layer A — the restore vehicle (``CoverCommandService.restore_target`` +
``_PositionVerificationSensor._restore_from_attributes``) with a REAL service.
Layer B — the coordinator reload sequence: empty store → restore → a
context-less remote move engages manual override with ``has_recorded_target``
now True.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_CC = "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features"
_MO = "custom_components.adaptive_cover_pro.managers.manual_override.manager.check_cover_features"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_close_caps() -> dict[str, bool]:
    return {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }


def _numeric_caps() -> dict[str, bool]:
    return {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }


def _state(state_str: str, current_position: int | None = None) -> MagicMock:
    obj = MagicMock()
    obj.state = state_str
    obj.attributes = (
        {} if current_position is None else {"current_position": current_position}
    )
    return obj


def _make_hass(state_obj: MagicMock) -> MagicMock:
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states.get = MagicMock(return_value=state_obj)
    return hass


def _make_svc(hass: MagicMock, cover_type: str = "cover_awning", tolerance: int = 3):
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )

    return CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type=cover_type,
        grace_mgr=MagicMock(),
        position_tolerance=tolerance,
    )


# ===========================================================================
# Layer A — restore vehicle (CoverCommandService.restore_target)
# ===========================================================================


def test_restore_target_seeds_command_store():
    """An assumed-state open/close cover resting closed restores target 0."""
    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")

    with patch(_CC, return_value=_open_close_caps()):
        restored = svc.restore_target(eid, 0)

    assert restored is True
    assert svc.get_target(eid) == 0
    assert svc.has_target(eid) is True


def test_restore_target_numeric_cover():
    """A position-capable cover resting on its last target restores it."""
    eid = "cover.demo"
    hass = _make_hass(_state("open", current_position=65))
    svc = _make_svc(hass, cover_type="cover_blind")

    with patch(_CC, return_value=_numeric_caps()):
        restored = svc.restore_target(eid, 65)

    assert restored is True
    assert svc.get_target(eid) == 65


def test_restore_skipped_when_cover_not_resting_on_target():
    """#187 guard: cover no longer at the stored target → no restore."""
    eid = "cover.demo"
    hass = _make_hass(_state("open", current_position=30))
    svc = _make_svc(hass, cover_type="cover_blind")

    with patch(_CC, return_value=_numeric_caps()):
        restored = svc.restore_target(eid, 65)

    assert restored is False
    assert svc.get_target(eid) is None


def test_restore_never_marks_safety_and_sends_no_command():
    """#187 explicit: restore issues zero cover commands, no safety tag, not waiting."""
    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")

    with patch(_CC, return_value=_open_close_caps()):
        restored = svc.restore_target(eid, 0)

    assert restored is True
    assert svc.is_safety_target(eid) is False
    assert svc.is_waiting_for_target(eid) is False
    # No outbound cover service call was made during restore.
    assert hass.services.async_call.await_count == 0
    assert hass.services.async_call.call_count == 0


def test_restore_does_not_clobber_live_target():
    """A live target set this session is never overwritten by a restore."""
    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")

    svc.set_target(eid, 40)
    with patch(_CC, return_value=_open_close_caps()):
        restored = svc.restore_target(eid, 0)

    assert restored is False
    assert svc.get_target(eid) == 40


def test_restore_filters_unknown_entities():
    """The sensor skips per_entity keys not in coordinator.entities."""
    from custom_components.adaptive_cover_pro.sensor import _PositionVerificationSensor

    known = "cover.known"
    unknown = "cover.removed_from_config"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")

    coordinator = MagicMock()
    coordinator.entities = [known]
    coordinator._cmd_svc = svc

    sensor = object.__new__(_PositionVerificationSensor)
    sensor.coordinator = coordinator

    with patch(_CC, return_value=_open_close_caps()):
        restored_any = sensor._restore_from_attributes(
            {
                known: {"target": 0},
                unknown: {"target": 0},
            }
        )

    assert restored_any is True
    assert svc.get_target(known) == 0
    assert svc.get_target(unknown) is None


def test_sensor_skips_entries_with_none_target():
    """A per_entity entry whose target is None is skipped, restores nothing."""
    from custom_components.adaptive_cover_pro.sensor import _PositionVerificationSensor

    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")

    coordinator = MagicMock()
    coordinator.entities = [eid]
    coordinator._cmd_svc = svc

    sensor = object.__new__(_PositionVerificationSensor)
    sensor.coordinator = coordinator

    with patch(_CC, return_value=_open_close_caps()):
        restored_any = sensor._restore_from_attributes({eid: {"target": None}})

    assert restored_any is False
    assert svc.get_target(eid) is None


# ===========================================================================
# Layer B — coordinator reload sequence (restore → detector, has_recorded_target)
# ===========================================================================


def _make_reload_coordinator(svc, manager, policy):
    """Build a mock coordinator wired with a REAL cmd_svc + manager + policy.

    Exercises the real ``async_handle_cover_state_change`` → ``handle_state_change``
    → ``PositionDeltaDetector.detect`` path so ``has_recorded_target`` is computed
    from the (restored) command-target store, not stubbed.
    """
    coordinator = MagicMock()
    coordinator.manual_toggle = True
    coordinator.manual_ignore_external = False
    coordinator.manual_reset = False
    coordinator.manual_threshold = None
    coordinator._position_tolerance = 3
    coordinator._cmd_svc = svc
    coordinator.manager = manager
    coordinator._policy = policy
    coordinator._pipeline_result = None
    coordinator._target_just_reached = set()
    coordinator._pending_cover_events = []
    coordinator.cover_state_change = True
    coordinator.logger = MagicMock()
    coordinator._is_in_startup_grace_period = MagicMock(return_value=False)
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = MagicMock(return_value=False)
    coordinator._grace_mgr = grace_mgr
    return coordinator


def _contextless_event(entity_id: str, new_state: MagicMock) -> MagicMock:
    """Build a context-less remote-move event: no user_id, no old_state.

    Faithful to the post-reload first event (evidence manager.py:458-467):
    ``old_state is None`` → ``old_position is None``, so the guard-less
    ``not has_recorded_target`` rescue branch CANNOT engage — only the restored
    ``has_recorded_target`` normal path can.
    """
    event = MagicMock()
    event.entity_id = entity_id
    event.old_state = None
    event.new_state = new_state
    ctx = MagicMock()
    ctx.id = "ctx-system-post-reload"
    ctx.user_id = None
    new_state.context = ctx
    new_state.last_updated = "2026-07-24T19:00:00+00:00"
    return event


def _restore_via_sensor(svc, coordinator, per_entity):
    from custom_components.adaptive_cover_pro.sensor import _PositionVerificationSensor

    sensor = object.__new__(_PositionVerificationSensor)
    sensor.coordinator = coordinator
    return sensor._restore_from_attributes(per_entity)


@pytest.mark.asyncio
async def test_reload_then_contextless_remote_move_engages_override_assumed_state():
    """Assumed-state open/close cover: reload → restore → remote open engages override."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    eid = "cover.patio_awning"
    # Live cover rests "closed" (its last commanded target 0), not in transit.
    live_state = _state("closed")
    hass = _make_hass(live_state)
    svc = _make_svc(hass, cover_type="cover_awning")
    manager = AdaptiveCoverManager(
        hass=hass, reset_duration={"hours": 2}, logger=MagicMock()
    )
    manager.add_covers([eid])
    policy = get_policy("cover_awning")
    coordinator = _make_reload_coordinator(svc, manager, policy)
    coordinator.entities = [eid]

    with (
        patch(_CC, return_value=_open_close_caps()),
        patch(_MO, return_value=_open_close_caps()),
    ):
        # Fresh store (reload) — nothing recorded yet.
        assert svc.get_target(eid) is None

        # Restore the persisted target (cover rests on it → restore succeeds).
        assert _restore_via_sensor(svc, coordinator, {eid: {"target": 0}}) is True
        assert svc.get_target(eid) == 0

        # A context-less remote move: cover now reports "open".
        new_state = _state("open")
        coordinator._pending_cover_events = [_contextless_event(eid, new_state)]

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, 0
        )

    assert manager.is_cover_manual(eid) is True


# ===========================================================================
# Layer C — base RestoreEntity plumbing (async_added_to_hass seam)
# ===========================================================================


def _build_restorable_sensor(cls, coordinator, last_state):
    """Build a bare restorable sensor around a mock coordinator.

    Bypasses CoordinatorEntity / HA machinery via ``object.__new__`` and stubs
    ``async_get_last_state`` (RestoreEntity) so the real
    ``_ACPRestorableDiagnosticSensor.async_added_to_hass`` seam can be driven
    end-to-end. Returns ``(sensor, write_spy)``.
    """
    sensor = object.__new__(cls)
    sensor.coordinator = coordinator
    sensor.async_get_last_state = AsyncMock(return_value=last_state)
    write_spy = MagicMock()
    sensor.async_write_ha_state = write_spy
    return sensor, write_spy


@pytest.mark.asyncio
async def test_async_added_to_hass_restores_target_end_to_end():
    """Real async_added_to_hass seam: last state → cmd store + one HA-state write.

    Exercises the full RestoreEntity plumbing (async_added_to_hass →
    async_get_last_state → extract per_entity → _restore_from_attributes →
    conditional async_write_ha_state) rather than a direct
    _restore_from_attributes call, so the seam that only production hits is
    covered. Asserts BOTH the target landed in the real CoverCommandService and
    that HA state was written.
    """
    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    from custom_components.adaptive_cover_pro.sensor import _PositionVerificationSensor

    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")
    coordinator = MagicMock()
    coordinator.entities = [eid]
    coordinator._cmd_svc = svc

    last_state = MagicMock()
    last_state.attributes = {"per_entity": {eid: {"target": 0}}}
    sensor, write_spy = _build_restorable_sensor(
        _PositionVerificationSensor, coordinator, last_state
    )

    with (
        patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch(_CC, return_value=_open_close_caps()),
    ):
        await sensor.async_added_to_hass()

    # (a) target landed in the real command-target store
    assert svc.get_target(eid) == 0
    # (b) HA state was written exactly once (restore happened)
    assert write_spy.call_count == 1


@pytest.mark.asyncio
async def test_async_added_to_hass_tolerates_non_mapping_per_entity():
    """A corrupted non-mapping ``per_entity`` restores nothing without raising.

    A restore snapshot whose ``per_entity`` is a list/str (corruption) must be
    treated as empty by the shared base seam — never crash the entity setup with
    an AttributeError on ``.items()``. This guards BOTH restorable subclasses at
    once (the guard lives in the base ``async_added_to_hass``).
    """
    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    from custom_components.adaptive_cover_pro.sensor import _PositionVerificationSensor

    eid = "cover.demo"
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")
    coordinator = MagicMock()
    coordinator.entities = [eid]
    coordinator._cmd_svc = svc

    last_state = MagicMock()
    # per_entity is a non-mapping (list) — a corrupted restore snapshot.
    last_state.attributes = {"per_entity": ["cover.demo", "junk"]}
    sensor, write_spy = _build_restorable_sensor(
        _PositionVerificationSensor, coordinator, last_state
    )

    with (
        patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch(_CC, return_value=_open_close_caps()),
    ):
        # Must not raise AttributeError on `.items()`.
        await sensor.async_added_to_hass()

    # Nothing restored, no HA-state write.
    assert svc.get_target(eid) is None
    assert write_spy.call_count == 0


# ===========================================================================
# Layer D — composition with #1019/#1021 active-override restore
# ===========================================================================


@pytest.mark.parametrize("override_first", [True, False])
def test_target_restore_composes_with_active_override_restore(override_first):
    """#1022 target restore and #1019 override restore never clobber each other.

    They write to disjoint stores — the override restore populates
    ``manager.overrides`` (#1019/#1021), the target restore seeds
    ``CoverCommandService._state[eid].target`` (#1022). Running both for the
    SAME entity in EITHER order must leave both intact: the active
    manual-override (a live ``OverrideState`` with a future expiry) survives AND
    the command target is set.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )
    from custom_components.adaptive_cover_pro.sensor import (
        _ManualOverrideEndSensor,
        _PositionVerificationSensor,
    )

    eid = "cover.patio_awning"
    # Cover rests "closed" — i.e. on its last commanded target 0 (#187 guard passes).
    hass = _make_hass(_state("closed"))
    svc = _make_svc(hass, cover_type="cover_awning")
    manager = AdaptiveCoverManager(
        hass=hass, reset_duration={"minutes": 60}, logger=MagicMock()
    )
    manager.add_covers([eid])

    coordinator = MagicMock()
    coordinator.manager = manager
    coordinator._cmd_svc = svc
    coordinator.entities = [eid]

    override_sensor = object.__new__(_ManualOverrideEndSensor)
    override_sensor.coordinator = coordinator
    target_sensor = object.__new__(_PositionVerificationSensor)
    target_sensor.coordinator = coordinator

    # An active override with a future expiry (30 min out).
    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    override_payload = {eid: expiry.isoformat()}
    target_payload = {eid: {"target": 0}}

    def _restore_override():
        assert override_sensor._restore_from_attributes(override_payload) is True

    def _restore_target():
        with patch(_CC, return_value=_open_close_caps()):
            assert target_sensor._restore_from_attributes(target_payload) is True

    if override_first:
        _restore_override()
        _restore_target()
    else:
        _restore_target()
        _restore_override()

    # Manual-override state survives intact.
    assert manager.is_cover_manual(eid) is True
    assert manager.override_for(eid) is not None
    # Command target is set on the disjoint store.
    assert svc.get_target(eid) == 0


@pytest.mark.asyncio
async def test_reload_then_contextless_remote_move_engages_override_numeric():
    """Position-capable cover: reload → restore target 65 → remote move to 30 engages."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    eid = "cover.office_blind"
    # Live cover rests at 65 (its last commanded target), not in transit.
    live_state = _state("open", current_position=65)
    hass = _make_hass(live_state)
    svc = _make_svc(hass, cover_type="cover_blind")
    manager = AdaptiveCoverManager(
        hass=hass, reset_duration={"hours": 2}, logger=MagicMock()
    )
    manager.add_covers([eid])
    policy = get_policy("cover_blind")
    coordinator = _make_reload_coordinator(svc, manager, policy)
    coordinator.entities = [eid]

    with (
        patch(_CC, return_value=_numeric_caps()),
        patch(_MO, return_value=_numeric_caps()),
    ):
        assert svc.get_target(eid) is None
        assert _restore_via_sensor(svc, coordinator, {eid: {"target": 65}}) is True
        assert svc.get_target(eid) == 65

        # Context-less remote move to 30 (a real user touch).
        new_state = _state("open", current_position=30)
        coordinator._pending_cover_events = [_contextless_event(eid, new_state)]

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, 0
        )

    assert manager.is_cover_manual(eid) is True
