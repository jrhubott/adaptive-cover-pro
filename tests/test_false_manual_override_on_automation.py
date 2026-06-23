"""Tests for false manual override detection when automation positions a cover.

Covers the race condition where process_entity_state_change() clears
wait_for_target (because the cover reached its commanded position within
POSITION_TOLERANCE_PERCENT) and then async_handle_cover_state_change() runs
on the same event with wait_for_target=False, falsely triggering a manual
override because the cover's final resting position differs slightly from the
commanded value.

Two complementary fixes are tested:

1.  _target_just_reached guard (coordinator.py): when check_target_reached()
    returns True, the entity is added to _target_just_reached and
    async_handle_cover_state_change() skips the manual override comparison for
    that event.

2.  Tolerance floor in handle_state_change (manual_override.py): the effective
    threshold used in handle_state_change() is at least POSITION_TOLERANCE_PERCENT,
    so small motor rounding never triggers a false override regardless of the
    user-configured manual_threshold value.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_change_data(entity_id: str, position: int, is_tilt: bool = False):
    """Build a minimal StateChangedData-like object for testing."""
    data = MagicMock()
    data.entity_id = entity_id
    data.old_state = MagicMock()
    data.old_state.state = "open"
    data.new_state = MagicMock()
    if is_tilt:
        data.new_state.attributes = {"current_tilt_position": position}
    else:
        data.new_state.attributes = {"current_position": position}
    return data


def _make_coordinator(
    entity_id: str = "cover.test",
    target: int = 72,
    manual_toggle: bool = True,
    automatic_control: bool = True,
    manual_threshold: int | None = None,
    target_just_reached: set | None = None,
    enable_position_matching: bool = True,
    position_tolerance: int = 3,
):
    """Build a minimal mock coordinator with the attributes used by the state-change handlers."""
    coordinator = MagicMock()
    coordinator.manual_toggle = manual_toggle
    coordinator.automatic_control = automatic_control
    coordinator.manual_ignore_external = False
    cmd_svc = MagicMock()
    cmd_svc.get_target = MagicMock(return_value=target)
    cmd_svc.is_waiting_for_target = MagicMock(return_value=False)
    cmd_svc.discard_target = MagicMock()
    cmd_svc.enable_position_matching = enable_position_matching
    coordinator._cmd_svc = cmd_svc
    coordinator._cover_type = "cover_blind"
    coordinator.manual_reset = False
    coordinator.manual_threshold = manual_threshold
    coordinator._position_tolerance = position_tolerance
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.cover_state_change = True
    coordinator._is_in_startup_grace_period = MagicMock(return_value=False)
    coordinator._manual_gate_closed_log = MagicMock()
    coordinator._target_just_reached = (
        target_just_reached if target_just_reached is not None else set()
    )
    # Real list so async_handle_cover_state_change can iterate it
    coordinator._pending_cover_events = []
    return coordinator


# ===========================================================================
# Fix 1: _target_just_reached guard in coordinator
# ===========================================================================


@pytest.mark.asyncio
async def test_target_just_reached_skips_override_detection():
    """When cover just reached its target, manual override detection must be skipped.

    Scenario: integration sent 72%, cover settled at 70% (2% motor rounding).
    check_target_reached() clears wait_for_target and populates
    _target_just_reached. async_handle_cover_state_change() must skip
    handle_state_change() for this event.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=72)
    # The cover settled at 70 — 2% off but within POSITION_TOLERANCE_PERCENT (3%)
    event_data = _make_state_change_data(entity_id, position=70)
    coordinator._pending_cover_events = [event_data]
    # Simulate that process_entity_state_change() already populated the set
    coordinator._target_just_reached = {entity_id}

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 72)

    # handle_state_change must NOT be called — this was an automation-driven move
    coordinator.manager.handle_state_change.assert_not_called()
    # cover_state_change flag must be cleared
    assert coordinator.cover_state_change is False
    # The guard set must be empty after the handler processes it
    assert entity_id not in coordinator._target_just_reached


@pytest.mark.asyncio
async def test_target_just_reached_clears_entry_from_set():
    """Each entity in _target_just_reached is consumed exactly once."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    other_entity = "cover.other"
    coordinator = _make_coordinator(entity_id=entity_id, target=50)
    event_data = _make_state_change_data(entity_id, position=51)
    coordinator._pending_cover_events = [event_data]
    # Two entities in the set; only the one being processed should be removed
    coordinator._target_just_reached = {entity_id, other_entity}

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    # The processed entity must be removed
    assert entity_id not in coordinator._target_just_reached
    # The other entity must remain
    assert other_entity in coordinator._target_just_reached


@pytest.mark.asyncio
async def test_genuine_manual_override_still_detected():
    """A large position change not preceded by target-just-reached must trigger override.

    Scenario: user manually moves cover from 72% to 30% — should still be detected.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=72, manual_threshold=5)
    # User moved cover to 30% — a 42% difference, clearly manual
    event_data = _make_state_change_data(entity_id, position=30)
    coordinator._pending_cover_events = [event_data]
    # _target_just_reached is empty (no automation command was just sent)
    coordinator._target_just_reached = set()

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 72)

    # handle_state_change MUST be called to detect the manual override
    coordinator.manager.handle_state_change.assert_called_once()


@pytest.mark.asyncio
async def test_target_just_reached_guard_not_triggered_when_set_empty():
    """Normal state changes proceed to override detection when _target_just_reached is empty."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=50, manual_threshold=10)
    event_data = _make_state_change_data(entity_id, position=50)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = set()  # Nothing just reached

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    # handle_state_change must be called — normal processing
    coordinator.manager.handle_state_change.assert_called_once()


@pytest.mark.asyncio
async def test_different_entity_in_target_just_reached_does_not_skip_current():
    """Only the current entity being processed is guarded — other entities are not affected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=72, manual_threshold=10)
    # A different entity reached its target, but not cover.test
    coordinator._target_just_reached = {"cover.other_cover"}
    event_data = _make_state_change_data(entity_id, position=30)
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 72)

    # handle_state_change must still run for cover.test
    coordinator.manager.handle_state_change.assert_called_once()
    # cover.other_cover must remain in the set (not consumed)
    assert "cover.other_cover" in coordinator._target_just_reached


# ===========================================================================
# Issue #591: with position matching off (the default), a settle beyond the
# position tolerance (but under the user manual-override threshold) must engage
# a full manual override. The coordinator delivers this by lowering the
# detection threshold it passes to handle_state_change to the position tolerance.
# ===========================================================================


@pytest.mark.asyncio
async def test_matching_off_lowers_detection_threshold_to_tolerance():
    """Matching off (default) → detection threshold passed is the tolerance (#591).

    Scenario: target 72, manual_threshold 10, tolerance 3. Cover settles at 65
    (delta 7 — the middle band). With matching on this is "within threshold"
    and would just retry; with matching off the coordinator lowers the
    threshold so the detector engages a manual override.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(
        entity_id=entity_id,
        target=72,
        manual_threshold=10,
        enable_position_matching=False,
        position_tolerance=3,
    )
    event_data = _make_state_change_data(entity_id, position=65)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = set()

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 72)

    coordinator.manager.handle_state_change.assert_called_once()
    # Positional arg 5 is the detection threshold (see the call site).
    passed_threshold = coordinator.manager.handle_state_change.call_args.args[5]
    assert passed_threshold == 3  # lowered to position tolerance, not 10


@pytest.mark.asyncio
async def test_matching_on_passes_manual_threshold_unchanged():
    """Matching on (opt-in) → the user manual_threshold is passed unchanged (#591)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(
        entity_id=entity_id,
        target=72,
        manual_threshold=10,
        enable_position_matching=True,
        position_tolerance=3,
    )
    event_data = _make_state_change_data(entity_id, position=65)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = set()

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 72)

    coordinator.manager.handle_state_change.assert_called_once()
    passed_threshold = coordinator.manager.handle_state_change.call_args.args[5]
    assert passed_threshold == 10  # unchanged — matching-on path untouched


# ===========================================================================
# Fix 2: Tolerance floor in AdaptiveCoverManager.handle_state_change
# ===========================================================================


def _make_manager():
    """Build a real AdaptiveCoverManager for testing handle_state_change."""
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    manager.add_covers(["cover.test"])
    return manager


def test_position_within_tolerance_floor_not_flagged_as_manual():
    """Position within POSITION_TOLERANCE_PERCENT (3%) must NOT trigger override.

    This applies even when manual_threshold is None (no user-configured threshold).
    """
    manager = _make_manager()
    entity_id = "cover.test"

    # Integration sent 72%, cover settled at 70% (2% difference < 3% tolerance)
    state_data = _make_state_change_data(entity_id, position=70)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=None,  # No user threshold configured
    )

    assert not manager.is_cover_manual(
        entity_id
    ), "2% position difference within POSITION_TOLERANCE_PERCENT (3%) should NOT trigger manual override"


def test_position_within_tolerance_floor_not_flagged_strict_user_threshold():
    """Even with a strict user threshold (e.g. 1%), tolerance floor (3%) must win."""
    manager = _make_manager()
    entity_id = "cover.test"

    # Integration sent 72%, cover settled at 70% (2% difference)
    # User threshold is 1% but tolerance floor is 3% → 2% < 3% → no override
    state_data = _make_state_change_data(entity_id, position=70)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=1,
    )

    assert not manager.is_cover_manual(entity_id), (
        "2% difference with 1% user threshold must not trigger override when "
        "POSITION_TOLERANCE_PERCENT (3%) is the floor"
    )


def test_position_exceeding_tolerance_floor_and_no_user_threshold_triggers_override():
    """Position difference > POSITION_TOLERANCE_PERCENT with no user threshold triggers override."""
    manager = _make_manager()
    entity_id = "cover.test"

    # User moved cover to 60% when integration expects 72% — 12% difference
    state_data = _make_state_change_data(entity_id, position=60)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
    )

    assert manager.is_cover_manual(
        entity_id
    ), "12% difference with no user threshold must trigger manual override"


def test_position_exceeding_user_threshold_and_tolerance_triggers_override():
    """Position difference exceeding both user threshold and tolerance triggers override."""
    manager = _make_manager()
    entity_id = "cover.test"

    # User moved cover to 60% when integration expects 72% — 12% difference
    state_data = _make_state_change_data(entity_id, position=60)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
    )

    assert manager.is_cover_manual(
        entity_id
    ), "12% difference exceeds both 5% user threshold and 3% tolerance floor"


def test_position_exactly_at_tolerance_boundary_not_flagged():
    """Position drift exactly equal to POSITION_TOLERANCE_PERCENT must NOT trigger override.

    The tolerance is inclusive: drift <= effective_threshold is absorbed as motor
    imprecision. A 3% drift at a 3% floor is within tolerance and must not mark
    the cover as manually controlled.
    """
    from custom_components.adaptive_cover_pro.const import POSITION_TOLERANCE_PERCENT

    manager = _make_manager()
    entity_id = "cover.test"

    # Cover is exactly at the boundary: difference = POSITION_TOLERANCE_PERCENT
    boundary_pos = 72 - POSITION_TOLERANCE_PERCENT  # e.g. 69
    state_data = _make_state_change_data(entity_id, position=boundary_pos)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
    )

    assert not manager.is_cover_manual(entity_id), (
        f"At exactly POSITION_TOLERANCE_PERCENT={POSITION_TOLERANCE_PERCENT}% difference "
        "the override must NOT trigger (boundary is included in the safe zone)"
    )


def test_position_just_inside_tolerance_boundary_not_flagged():
    """Position 1% inside the tolerance boundary (difference = 2%) must NOT trigger override."""
    from custom_components.adaptive_cover_pro.const import POSITION_TOLERANCE_PERCENT

    manager = _make_manager()
    entity_id = "cover.test"

    # Cover is 1% inside the boundary: difference = POSITION_TOLERANCE_PERCENT - 1
    safe_pos = 72 - (POSITION_TOLERANCE_PERCENT - 1)  # e.g. 70
    state_data = _make_state_change_data(entity_id, position=safe_pos)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
    )

    assert not manager.is_cover_manual(entity_id), (
        f"{POSITION_TOLERANCE_PERCENT - 1}% difference is inside tolerance floor — "
        "must NOT trigger manual override"
    )


def test_large_user_threshold_wins_over_tolerance_floor():
    """When user threshold (e.g. 10%) > tolerance (3%), user threshold governs."""
    manager = _make_manager()
    entity_id = "cover.test"

    # Cover at 67% when target is 72% — difference is 5%
    # User threshold = 10%, tolerance floor = 3% → effective = 10%
    # 5% < 10% → should NOT trigger override
    state_data = _make_state_change_data(entity_id, position=67)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=10,
    )

    assert not manager.is_cover_manual(
        entity_id
    ), "5% difference is below the 10% user threshold — must NOT trigger override"


def test_large_user_threshold_triggers_when_difference_exceeds_it():
    """When user threshold is 10% and difference is 15%, override must trigger."""
    manager = _make_manager()
    entity_id = "cover.test"

    state_data = _make_state_change_data(entity_id, position=57)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=10,
    )

    assert manager.is_cover_manual(
        entity_id
    ), "15% difference exceeds 10% user threshold — must trigger override"


def test_wait_for_target_prevents_override_regardless_of_tolerance():
    """When wait_for_target is True the override check must be skipped entirely."""
    manager = _make_manager()
    entity_id = "cover.test"

    # Even a large position difference must not trigger override while waiting
    state_data = _make_state_change_data(entity_id, position=0)

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: True,  # Still waiting for target
        manual_threshold=None,
    )

    assert not manager.is_cover_manual(
        entity_id
    ), "wait_for_target=True must block all override detection"


def test_position_change_inside_command_grace_is_not_override():
    """Position change inside the command-grace window must NOT trip manual override.

    Grace is active for the entity (timestamp just stamped). Even though the
    position delta exceeds the threshold, the grace gate must suppress it.
    """
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )
    import datetime as _dt

    entity_id = "cover.test"
    grace_mgr = GracePeriodManager(logger=MagicMock())
    # Stamp grace directly to avoid asyncio.create_task in unit-test context.
    grace_mgr._command_timestamps[entity_id] = _dt.datetime.now().timestamp()

    manager = _make_manager()
    state_data = _make_state_change_data(
        entity_id, position=52
    )  # delta=20 > threshold=5

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        is_in_command_grace=grace_mgr.is_in_command_grace_period,
    )

    assert not manager.is_cover_manual(
        entity_id
    ), "Position change inside command grace should NOT trigger manual override"


def test_position_change_after_grace_expired_trips_override():
    """Position change after command-grace has expired must still trip override.

    Grace window is 5 s. Stamping a timestamp 10 s in the past means the
    grace period has already elapsed, so the position-delta check must run
    and detect the manual override normally.
    """
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )
    import datetime as _dt

    entity_id = "cover.test"
    grace_mgr = GracePeriodManager(logger=MagicMock())
    # Backdate the timestamp so grace has already expired (10 s > 5 s window).
    grace_mgr._command_timestamps[entity_id] = _dt.datetime.now().timestamp() - 10

    manager = _make_manager()
    state_data = _make_state_change_data(
        entity_id, position=52
    )  # delta=20 > threshold=5

    manager.handle_state_change(
        state_data,
        our_state=72,
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        is_in_command_grace=grace_mgr.is_in_command_grace_period,
    )

    assert (
        manager.is_cover_manual(entity_id) is True
    ), "Position change after grace expired should trigger manual override"


def test_tolerance_floor_applies_to_tilt_cover():
    """The tolerance floor applies equally to tilt covers (cover_tilt type)."""
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    manager.add_covers(["cover.tilt_test"])
    entity_id = "cover.tilt_test"

    # Tilt integration sent 45%, tilt settled at 43% (2% difference < 3% floor)
    state_data = _make_state_change_data(entity_id, position=43, is_tilt=True)

    manager.handle_state_change(
        state_data,
        our_state=45,
        policy=get_policy("cover_tilt"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
    )

    assert not manager.is_cover_manual(
        entity_id
    ), "2% tilt position difference within tolerance floor must NOT trigger override"


# ===========================================================================
# Integration: both fixes working together
# ===========================================================================


@pytest.mark.asyncio
async def test_coarse_granularity_cover_no_false_override():
    """Cover with 5% position granularity must not trigger false overrides.

    Scenario: integration sends 73%, cover rounds to 75% (nearest 5% step).
    Both fixes must cooperate to prevent a false manual override.
    - _target_just_reached guard: covers the case where the position difference
      is above the 3% tolerance floor (here 2%, but could be 4% or 5%)
    - Tolerance floor: prevents 2% rounding from triggering override
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.coarse"
    # Integration sent 73%, cover rounds to 75% (2% difference within tolerance)
    coordinator = _make_coordinator(entity_id=entity_id, target=73)
    event_data = _make_state_change_data(entity_id, position=75)
    coordinator._pending_cover_events = [event_data]
    # _target_just_reached set by process_entity_state_change()
    coordinator._target_just_reached = {entity_id}

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 73)

    # No manual override should be triggered
    coordinator.manager.handle_state_change.assert_not_called()
    assert coordinator.cover_state_change is False


@pytest.mark.asyncio
async def test_automation_position_change_no_false_override_exact_match():
    """When cover reaches the exact commanded position, no false override must trigger."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.exact"
    # Exact match: integration sent 50%, cover reports 50%
    coordinator = _make_coordinator(entity_id=entity_id, target=50)
    event_data = _make_state_change_data(entity_id, position=50)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = {entity_id}

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    coordinator.manager.handle_state_change.assert_not_called()
    assert coordinator.cover_state_change is False


# ===========================================================================
# Issue #546: has_recorded_target threaded into handle_state_change
# ===========================================================================


@pytest.mark.asyncio
async def test_no_recorded_target_passes_has_recorded_target_false():
    """No recorded command target → handle_state_change gets has_recorded_target=False.

    Issue #546: with no command ever sent, ``get_target`` returns None and the
    coordinator must signal that to the detector so the numeric delta against
    the pipeline default is not misread as a manual override.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=None)
    event_data = _make_state_change_data(entity_id, position=25)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = set()

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
        coordinator, 100
    )

    coordinator.manager.handle_state_change.assert_called_once()
    _, kwargs = coordinator.manager.handle_state_change.call_args
    assert kwargs["has_recorded_target"] is False


@pytest.mark.asyncio
async def test_recorded_target_passes_has_recorded_target_true():
    """A recorded command target → handle_state_change gets has_recorded_target=True."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=72)
    event_data = _make_state_change_data(entity_id, position=25)
    coordinator._pending_cover_events = [event_data]
    coordinator._target_just_reached = set()

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
        coordinator, 100
    )

    coordinator.manager.handle_state_change.assert_called_once()
    _, kwargs = coordinator.manager.handle_state_change.call_args
    assert kwargs["has_recorded_target"] is True


@pytest.mark.asyncio
async def test_user_context_change_marks_override_even_without_recorded_target():
    """User-context fast-path still marks override with no recorded target.

    Issue #546 guard against over-suppression: a real user move (HA context
    carries a user_id, context id not generated by ACP) must engage manual
    override even when ``get_target`` is None — that path never routes through
    the numeric ``handle_state_change`` detector.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entity_id = "cover.test"
    coordinator = _make_coordinator(entity_id=entity_id, target=None)
    # Force the user-context fast-path: a real user_id and a context id that
    # ACP did not generate.
    coordinator._cmd_svc.was_acp_position_context = MagicMock(return_value=False)
    coordinator._target_just_reached = MagicMock()
    coordinator.manager.handle_user_initiated_state_change = MagicMock(
        return_value=True
    )

    event_data = _make_state_change_data(entity_id, position=25)
    event_data.new_state.context = MagicMock()
    event_data.new_state.context.user_id = "holly"
    event_data.new_state.context.id = "ctx-user-1"
    coordinator._pending_cover_events = [event_data]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
        coordinator, 100
    )

    coordinator.manager.handle_user_initiated_state_change.assert_called_once()
    coordinator.manager.handle_state_change.assert_not_called()


# ===========================================================================
# Issue #654: context-less remote move with no recorded target must engage
# ===========================================================================


def test_no_recorded_target_real_move_engages_override_end_to_end():
    """No recorded target + a real position move → manual override engages (#654).

    A physical-remote move arrives with no HA user_id (numeric path) and ACP has
    never commanded the cover (target=None → has_recorded_target=False). The move
    is real because the position changed from old_state (25%) to new_state (80%),
    so the override must engage despite the missing command target.
    """
    manager = _make_manager()
    entity_id = "cover.test"

    state_data = _make_state_change_data(entity_id, position=80)
    state_data.old_state.attributes = {"current_position": 25}

    manager.handle_state_change(
        state_data,
        our_state=100,  # meaningless pipeline default (never commanded)
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        has_recorded_target=False,
    )

    assert manager.is_cover_manual(entity_id), (
        "A real context-less move (25% → 80%) with no recorded target must "
        "engage manual override"
    )


def test_no_recorded_target_resting_republish_no_override_end_to_end():
    """No recorded target + resting-position republish → no override (#546).

    Same numeric path, but the cover republishes its resting position (old == new
    == 25%). There is no move, only pipeline-default divergence, so the override
    must stay suppressed.
    """
    manager = _make_manager()
    entity_id = "cover.test"

    state_data = _make_state_change_data(entity_id, position=25)
    state_data.old_state.attributes = {"current_position": 25}

    manager.handle_state_change(
        state_data,
        our_state=100,  # meaningless pipeline default (never commanded)
        policy=get_policy("cover_blind"),
        allow_reset=False,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        has_recorded_target=False,
    )

    assert not manager.is_cover_manual(entity_id), (
        "A resting-position republish (25% → 25%) with no recorded target must "
        "NOT engage manual override"
    )
