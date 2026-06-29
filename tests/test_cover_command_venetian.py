"""Dual-axis venetian command sequencing tests.

Issue #33: a venetian instance owns BOTH set_cover_position AND
set_cover_tilt_position on a single HA entity. The work is split between:

  * ``CoverCommandService.apply_position`` — fires ``set_cover_position``
    and then calls ``context.policy.after_position_command``.
  * ``VenetianPolicy`` — owns a ``DualAxisSequencer`` that polls
    ``current_position`` until the cover settles, fires
    ``set_cover_tilt_position``, and answers
    ``is_in_tilt_suppression(entity_id)`` for manual_override.

The settle / suppression unit tests live in
``tests/test_managers/test_dual_axis_sequencer.py``; this file pins the
``apply_position`` ↔ policy contract end-to-end.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import DEFAULT_VENETIAN_TILT_SKIP_ABOVE
from custom_components.adaptive_cover_pro.cover_types import VenetianPolicy, get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_special_positions,
)


@pytest.fixture(autouse=True)
def _zero_post_tilt_delay(monkeypatch):
    """Skip the 1.5s real-motor settle delay in unit tests."""
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
        "VENETIAN_POST_TILT_REBASE_DELAY_SECONDS",
        0,
    )


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def svc(hass):
    s = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_venetian",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    s._enabled = True
    # This suite exercises reconciliation resends; opt into matching (default
    # is off per issue #591).
    s.enable_position_matching = True
    return s


@pytest.fixture
def attached_policy(svc, hass):
    """Return a VenetianPolicy with a DualAxisSequencer attached and pre-stubbed."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=svc._get_current_position,
        set_commanded_position=svc.set_target,
        position_tolerance=5,
        is_dry_run=lambda: False,
    )
    # Skip the real polling loop — covered in dual_axis_sequencer unit tests.
    policy._sequencer._wait_for_position_settle = AsyncMock(return_value=(True, 60))
    return policy


def _ctx_venetian(policy, *, tilt: int | None) -> PositionContext:
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,  # Bypass delta/time gates for unit tests
        tilt=tilt,
        policy=policy,
    )


def _patch_caps_dual_axis():
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": True,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        },
    )


def test_attach_applies_default_threshold(svc, hass, attached_policy):
    """attach() without tilt_skip_above uses the module default."""
    assert attached_policy._tilt_skip_above == DEFAULT_VENETIAN_TILT_SKIP_ABOVE


def test_attach_applies_custom_threshold(svc, hass):
    """attach() with tilt_skip_above kwarg overrides the default."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=MagicMock(),
        set_commanded_position=MagicMock(),
        position_tolerance=5,
        is_dry_run=lambda: False,
        tilt_skip_above=80,
    )
    assert policy._tilt_skip_above == 80


def _state_with_position(pos: int):
    state = MagicMock()
    state.state = "open"
    state.attributes = {"current_position": pos, "current_tilt_position": 50}
    return state


@pytest.mark.asyncio
async def test_apply_position_emits_tilt_then_position_on_open(
    svc, hass, attached_policy
):
    """On opening transitions, tilt fires BEFORE position (issue #33 tilt-first).

    Total service-call count stays at 2: the post-settle tilt resend from
    ``run_sequence`` short-circuits on the target-unchanged dedup added to
    ``_send_tilt_command``.
    """
    entity_id = "cover.venetian_kitchen"
    hass.states.get.return_value = _state_with_position(0)  # opening 0 → 60

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 60, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 2
    services_called = [call.args[1] for call in hass.services.async_call.call_args_list]
    assert services_called == ["set_cover_tilt_position", "set_cover_position"]
    tilt_data = hass.services.async_call.call_args_list[0].args[2]
    assert tilt_data["tilt_position"] == 80


@pytest.mark.asyncio
async def test_apply_position_stamps_suppression_window(svc, hass, attached_policy):
    """The position-axis command stamps the policy's suppression window."""
    entity_id = "cover.venetian_lounge"
    hass.states.get.return_value = _state_with_position(0)

    with _patch_caps_dual_axis():
        await svc.apply_position(
            entity_id, 40, "solar", _ctx_venetian(attached_policy, tilt=70)
        )

    assert attached_policy.is_in_tilt_suppression(entity_id, delta=0.0) is True


@pytest.mark.asyncio
async def test_apply_position_sends_neutral_tilt_when_position_above_threshold(
    svc, hass, attached_policy
):
    """Above the retract threshold the sequencer sends a neutral tilt (POSITION_OPEN).

    KNX/Shelly venetian actuators retain their last commanded tilt and reapply
    it after the carriage settles. Overwriting the cache with POSITION_OPEN
    keeps slats from closing on a fully-retracted blind (issue #33 comment #54).
    The context tilt (80) is intentionally ignored on the retract path.
    """
    from custom_components.adaptive_cover_pro.const import POSITION_OPEN

    entity_id = "cover.venetian_retracted"
    hass.states.get.return_value = _state_with_position(90)  # opening 90 → 96

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 96, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 2
    # Opening transition: tilt-first (issue #33). The retract path overrides
    # context.tilt with POSITION_OPEN regardless of which command fires first.
    tilt_call = hass.services.async_call.call_args_list[0]
    assert tilt_call.args[1] == "set_cover_tilt_position"
    assert tilt_call.args[2]["tilt_position"] == POSITION_OPEN
    assert hass.services.async_call.call_args_list[1].args[1] == "set_cover_position"


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [95, 60])
async def test_apply_position_fires_tilt_at_or_below_threshold(
    svc, hass, attached_policy, position
):
    """Tilt fires normally when position is at or below the retract threshold."""
    entity_id = "cover.venetian_partial"
    hass.states.get.return_value = _state_with_position(max(position - 5, 0))

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, position, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 2


@pytest.mark.asyncio
async def test_apply_position_skips_tilt_when_no_tilt_target(
    svc, hass, attached_policy
):
    """Without ``context.tilt``, only the position service fires."""
    entity_id = "cover.venetian_no_tilt"
    hass.states.get.return_value = _state_with_position(0)

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 60, "solar", _ctx_venetian(attached_policy, tilt=None)
        )

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 1
    assert hass.services.async_call.call_args_list[0].args[1] == "set_cover_position"


@pytest.mark.asyncio
async def test_apply_position_endpoint_close_runs_tilt_sequence(
    svc, hass, attached_policy
):
    """Endpoint open/close (issue #697) still drives the venetian tilt sequence.

    A venetian closing to 0 with endpoint_use_open_close=True must fire
    ``close_cover`` for the carriage AND still run the dual-axis tilt
    sequence (option A: widened sequencer guard). Without the widening the
    tilt sequence is skipped because the service is not set_cover_position.
    """
    entity_id = "cover.venetian_close"
    hass.states.get.return_value = _state_with_position(60)  # closing 60 → 0

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 0, "solar", _ctx_venetian(attached_policy, tilt=20)
        )

    assert outcome == "sent"
    services = [c.args[1] for c in hass.services.async_call.call_args_list]
    assert "close_cover" in services
    assert "set_cover_tilt_position" in services


@pytest.mark.asyncio
async def test_apply_position_endpoint_open_runs_tilt_sequence(
    svc, hass, attached_policy
):
    """Venetian opening to 100 fires open_cover and still sequences tilt."""
    entity_id = "cover.venetian_open"
    hass.states.get.return_value = _state_with_position(0)  # opening 0 → 100

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 100, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    assert outcome == "sent"
    services = [c.args[1] for c in hass.services.async_call.call_args_list]
    assert "open_cover" in services
    assert "set_cover_tilt_position" in services


@pytest.mark.asyncio
async def test_apply_position_no_policy_skips_tilt_entirely(svc, hass):
    """When PositionContext.policy is None (non-venetian path), no tilt fires."""
    entity_id = "cover.kitchen"
    hass.states.get.return_value = _state_with_position(0)
    ctx = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        tilt=80,  # Set but ignored because policy is None
        policy=None,
    )

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(entity_id, 60, "solar", ctx)

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 1
    assert hass.services.async_call.call_args_list[0].args[1] == "set_cover_position"


@pytest.mark.asyncio
async def test_reconciliation_no_op_after_post_tilt_rebase(svc, hass, attached_policy):
    """Reconciliation must not re-fire when the rebase absorbed the motor drift.

    Scenario: commanded 60%, motor back-drives cover to 67% after tilt.
    The sequencer rebases svc.target to 67%. Next reconciliation tick reads
    actual=67% vs target=67% → zero delta → no resend.
    """
    import datetime as dt

    entity_id = "cover.venetian_kitchen"
    # After the tilt command lands, the cover reports 67% (7% drift from 60%).
    # This exceeds the sequencer's tolerance (5) so the rebase fires.
    hass.states.get.return_value = _state_with_position(67)

    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id, 60, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    assert outcome == "sent"
    assert hass.services.async_call.call_count == 2  # position + tilt only
    # Rebase must have updated the target to the actual post-tilt position.
    assert svc.get_target(entity_id) == 67

    # Clear wait_for_target: in production this is cleared when HA fires a
    # cover-position state update; in unit tests we do it manually.
    svc.set_waiting(entity_id, False)

    # Prepare reconciliation pre-conditions (mirrors coordinator setup).
    svc._enabled = True
    svc._auto_control_enabled = True
    svc._in_time_window = True

    with _patch_caps_dual_axis():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # No 3rd service call — reconciliation saw actual==target and skipped resend.
    assert hass.services.async_call.call_count == 2
    assert svc.state(entity_id).retry_count == 0


@pytest.mark.asyncio
async def test_reconciliation_would_loop_without_rebase(svc, hass, attached_policy):
    """Regression guard: without the rebase, reconciliation re-fires set_cover_position.

    This test documents the loop that _rebase_commanded_position closes. By
    disabling the rebase after apply_position, the svc target stays at 60%
    while the cover reports 67% — causing reconciliation to issue a 3rd command.
    """
    import datetime as dt

    entity_id = "cover.venetian_kitchen"
    hass.states.get.return_value = _state_with_position(67)

    with _patch_caps_dual_axis():
        await svc.apply_position(
            entity_id, 60, "solar", _ctx_venetian(attached_policy, tilt=80)
        )

    # Simulate the absence of the rebase: force target back to the original
    # commanded value so reconciliation sees a drift.
    svc.set_target(entity_id, 60)
    assert svc.get_target(entity_id) == 60  # confirm the loop precondition

    # Clear wait_for_target so reconciliation can actually compare target vs actual.
    svc.set_waiting(entity_id, False)

    svc._enabled = True
    svc._auto_control_enabled = True
    svc._in_time_window = True

    with _patch_caps_dual_axis():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Reconciliation detects |67 - 60| = 7 > tolerance(3) and issues a resend.
    assert hass.services.async_call.call_count == 3


@pytest.mark.asyncio
async def test_tilt_on_target_plus_position_back_drive_does_not_trip_manual_override(
    svc, hass, attached_policy
):
    """End-to-end regression for issue #33: back-drive inside suppression window.

    Sequence:
      1. apply_position(34, tilt=70) — position + tilt commands fire, suppression stamped.
      2. Motor back-drives position to 37% (drift=3, = manual threshold floor).
      3. HA fires state-change: tilt=70 (on-target), position=37 (drifted).

    Bug A (fixed): SecondaryAxisCheck.evaluate returned consumed=False when
    new_value==expected, bypassing suppression. The position-axis check then
    evaluated |34-37|=3, which is NOT strictly less than effective_threshold=3,
    and set manual override. With the fix, suppression is consulted first and
    consumed=True blocks the position-axis check entirely.
    """
    import datetime as dt

    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
        SecondaryAxisCheck,
    )

    entity_id = "cover.venetian_morning"
    # Motor back-drove to 38% after the tilt command (drift=4 > default tolerance=3).
    # Using 38 rather than 37 ensures the cover is outside the same-position band
    # so apply_position fires the position command and stamps the suppression window.
    hass.states.get.return_value = _state_with_position(38)

    with _patch_caps_dual_axis():
        await svc.apply_position(
            entity_id, 34, "solar", _ctx_venetian(attached_policy, tilt=70)
        )

    # Suppression window must be open immediately after apply_position.
    assert attached_policy.is_in_tilt_suppression(entity_id, delta=0.0)

    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    mgr.add_covers([entity_id])

    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = {"current_position": 38, "current_tilt_position": 70}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)

    mgr.handle_state_change(
        states_data=event,
        our_state=34,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        secondary_axis_check=SecondaryAxisCheck(
            expected=70,
            attribute="current_tilt_position",
            label="tilt",
            suppression=attached_policy.is_in_tilt_suppression,
        ),
    )

    assert not mgr.is_cover_manual(entity_id)


@pytest.mark.asyncio
async def test_tilt_only_update_does_not_stamp_suppression_window(
    svc, hass, attached_policy
):
    """Issue #33 follow-on: tilt-only updates must NOT extend the back-rotate window.

    The window protects the *position-axis* settle and the tilt-induced back-drive
    that follows. A tilt-only send from ``maybe_update_tilt_only`` doesn't move
    the carriage, so it must not refresh the window — otherwise a user opening
    the blind during the (now extended) window is silently consumed as motor
    drift, stranding reconciliation at the user-driven position.
    """
    entity_id = "cover.venetian_morning"
    hass.states.get.return_value = _state_with_position(50)

    # Seed _last_tilt so maybe_update_tilt_only doesn't short-circuit on None.
    attached_policy._last_tilt = 70

    await attached_policy.maybe_update_tilt_only(
        entity_id, current_position=50, context=None, reason="solar"
    )

    # New contract: tilt-only path leaves the window untouched.
    assert attached_policy.is_in_tilt_suppression(entity_id, delta=0.0) is False


@pytest.mark.asyncio
async def test_tilt_only_small_mid_settle_drift_does_not_trip_manual_override(
    svc, hass, attached_policy
):
    """Tilt-only path: small mid-settle drift falls below manual_threshold and is silent.

    Replaces the pre-fix protection (which stamped the window from the tilt-only
    path and absorbed arbitrarily large drifts). The realistic case for mid-settle
    drift is single-digit percent — covered by the manual_threshold floor — not
    the 50-pt swing the prior bug silently allowed.
    """
    import datetime as dt

    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
        SecondaryAxisCheck,
    )

    entity_id = "cover.venetian_morning"
    hass.states.get.return_value = _state_with_position(50)
    attached_policy._last_tilt = 70

    await attached_policy.maybe_update_tilt_only(
        entity_id, current_position=50, context=None, reason="solar"
    )

    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    mgr.add_covers([entity_id])

    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = {"current_position": 50, "current_tilt_position": 72}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)

    mgr.handle_state_change(
        states_data=event,
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=SecondaryAxisCheck(
            expected=70,
            attribute="current_tilt_position",
            label="tilt",
            suppression=attached_policy.is_in_tilt_suppression,
        ),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_attach_defaults_venetian_mode_to_position_and_tilt(attached_policy):
    """attach() without venetian_mode defaults to position_and_tilt."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_MODE_POSITION_AND_TILT,
    )

    assert attached_policy._venetian_mode == VENETIAN_MODE_POSITION_AND_TILT


def test_attach_applies_custom_venetian_mode(hass):
    """attach() with venetian_mode kwarg stores the given mode."""
    from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=MagicMock(),
        set_commanded_position=MagicMock(),
        position_tolerance=5,
        is_dry_run=lambda: False,
        venetian_mode=VENETIAN_MODE_TILT_ONLY,
    )
    assert policy._venetian_mode == VENETIAN_MODE_TILT_ONLY


@pytest.mark.asyncio
async def test_same_position_skip_calls_maybe_update_tilt_only(svc, hass):
    """When apply_position short-circuits on same-position, maybe_update_tilt_only fires."""
    entity_id = "cover.venetian_x"
    hass.states.get.return_value = _state_with_position(0)

    policy = MagicMock()
    policy.after_position_command = AsyncMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        tilt=70,
        policy=policy,
    )

    with _patch_caps_dual_axis():
        outcome, reason = await svc.apply_position(entity_id, 0, "solar", ctx)

    assert reason == "same_position"
    policy.maybe_update_tilt_only.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_position_skip_does_not_call_hook_when_no_tilt(svc, hass):
    """No tilt in context — maybe_update_tilt_only must not be called."""
    entity_id = "cover.venetian_x"
    hass.states.get.return_value = _state_with_position(0)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        tilt=None,
        policy=policy,
    )

    with _patch_caps_dual_axis():
        outcome, reason = await svc.apply_position(entity_id, 0, "solar", ctx)

    assert reason == "same_position"
    policy.maybe_update_tilt_only.assert_not_awaited()


def test_attach_passes_min_change_callable_to_sequencer(svc, hass):
    """attach() with get_min_change kwarg wires the callable into the sequencer."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=svc._get_current_position,
        set_commanded_position=svc.set_target,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_min_change=lambda: 5,
    )
    assert policy._sequencer._get_min_change() == 5


def test_attach_passes_enforce_delta_at_endpoints_callable(svc, hass):
    """attach() wires the endpoint-enforce callable into the sequencer (#679)."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=svc._get_current_position,
        set_commanded_position=svc.set_target,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_enforce_delta_at_endpoints=lambda: True,
    )
    assert policy._sequencer._get_enforce_delta_at_endpoints() is True


@pytest.mark.asyncio
async def test_tilt_below_delta_threshold_skipped_in_full_pipeline(svc, hass):
    """Tilt commands below min_change are skipped; skip event is emitted on second cycle."""
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=20)
    entity_id = "cover.venetian_x"
    hass.states.get.return_value = _state_with_position(0)

    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=svc._get_current_position,
        set_commanded_position=svc.set_target,
        position_tolerance=5,
        is_dry_run=lambda: False,
        event_buffer=buf,
        get_min_change=lambda: 8,
        # Issue #33: anchor on live actuator. Stub returns the same logical value
        # as the stored target so the gate-logic assertion is isolated from
        # anchor source.
        get_current_tilt_position=lambda _eid: 50,
    )
    policy._sequencer._wait_for_position_settle = AsyncMock(return_value=(True, 60))

    ctx1 = _ctx_venetian(policy, tilt=50)
    ctx2 = _ctx_venetian(policy, tilt=53)  # delta=3, below min_change=8

    with _patch_caps_dual_axis():
        await svc.apply_position(entity_id, 60, "solar", ctx1)
        hass.services.async_call.reset_mock()
        # Expire the suppression window (normally 90s; here cleared to simulate
        # time having passed between update cycles) and set _last_tilt to
        # simulate what post_pipeline_resolve would have computed.
        policy._sequencer._suppression_at.clear()
        policy._last_tilt = 53
        # State now reflects post-first-cycle: position=60, tilt=50 (matches
        # what we stored on cycle 1). The actuator anchor is 50; the new target
        # is 53; delta=3 < min_change=8 → skip.
        state = MagicMock()
        state.state = "open"
        state.attributes = {"current_position": 60, "current_tilt_position": 50}
        hass.states.get.return_value = state
        await svc.apply_position(entity_id, 60, "solar", ctx2)

    # Second cycle: same position (no position command) + tilt below threshold → only 0 new calls
    assert hass.services.async_call.call_count == 0
    skip_events = [
        e
        for e in buf.snapshot()
        if e.get("event") == "tilt_command_skipped"
        and e.get("reason") == "delta_too_small"
    ]
    assert len(skip_events) >= 1


# ---------------------------------------------------------------------------
# Issue #679 capstone: mechanically coupled venetian (set_cover_position
# back-drives tilt). With the endpoint-enforce option ON the cover parks at the
# reachable 3/100 instead of chasing an impossible 0/100; with the option OFF
# the actual-aware tilt dedup still recovers (no permanent 0/0).
# ---------------------------------------------------------------------------


class _CoupledCover:
    """Stateful model of a coupled venetian.

    - ``set_cover_position(p)`` drives the carriage to ``p`` and, because the
      carriage and slats share one motor, back-drives the tilt actuator to 0.
    - ``set_cover_tilt_position(t)`` drives the slats to ``t``; rotating the
      slats open (toward 100) mechanically lifts the carriage by ~3 %, so a
      commanded 0 settles at the reachable 3.
    """

    def __init__(self, position: int, tilt: int) -> None:
        self.position = position
        self.tilt = tilt

    def state(self) -> MagicMock:
        import datetime as dt

        st = MagicMock()
        st.state = "open"
        # Old enough that the time-delta gate (threshold 0) always passes.
        st.last_updated = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        st.last_changed = st.last_updated
        st.attributes = {
            "current_position": self.position,
            "current_tilt_position": self.tilt,
        }
        return st


def _attach_coupled_policy(svc, hass, cover: _CoupledCover, buf):
    """Attach a VenetianPolicy whose actuator reads track a coupled cover."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: cover.position,
        set_commanded_position=svc.set_target,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "open",
        get_current_tilt_position=lambda _eid: cover.tilt,
        event_buffer=buf,
        get_min_change=lambda: 5,
        get_enforce_delta_at_endpoints=lambda: svc._enforce_endpoints,
    )
    # Skip the real polling loop; settle returns the coupled-driven position.
    policy._sequencer._wait_for_position_settle = AsyncMock(
        return_value=(True, cover.position)
    )
    return policy


def _coupled_ctx(policy, *, tilt: int, special_positions: list[int]) -> PositionContext:
    """Build a NON-force ctx so the real position delta gate runs."""
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=5,
        time_threshold=0,
        special_positions=special_positions,
        force=False,
        tilt=tilt,
        policy=policy,
    )


@pytest.mark.asyncio
async def test_coupled_cover_parks_at_reachable_position_with_option_on(svc, hass):
    """Flag ON: cycle-2 position-0 is gated; cover parks at the reachable 3/100."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=64)
    entity_id = "cover.coupled_venetian"
    svc._enforce_endpoints = True
    # Endpoints omitted from the special list when the flag is on (#679).
    specials = build_special_positions({CONF_ENFORCE_DELTA_AT_ENDPOINTS: True})
    assert specials == []

    # Start at 3/100 (the reachable state from a prior settle).
    cover = _CoupledCover(position=3, tilt=100)
    policy = _attach_coupled_policy(svc, hass, cover, buf)

    def _service_side_effect(domain, service, data, **_):
        if service == "set_cover_position":
            cover.position = data["position"]
            cover.tilt = 0  # coupled: position command back-drives the slats
        elif service == "set_cover_tilt_position":
            cover.tilt = data["tilt_position"]
            if cover.position == 0 and cover.tilt >= 100:
                cover.position = 3  # coupled rise lifts carriage to reachable 3
        return None

    hass.services.async_call.side_effect = _service_side_effect

    # --- Cycle 1: solar wants 0/100, but cover is at 3 (delta 3 < 5) and 0 is
    #     no longer special → the position command is GATED. Tilt already at
    #     100 matches the live actuator → no tilt command either. Stable at 3/100.
    hass.states.get.return_value = cover.state()
    with _patch_caps_dual_axis():
        outcome1, reason1 = await svc.apply_position(
            entity_id,
            0,
            "solar",
            _coupled_ctx(policy, tilt=100, special_positions=specials),
        )

    assert outcome1 == "skipped"
    # No position command fired — the endpoint delta gate held.
    pos_calls = [
        c
        for c in hass.services.async_call.call_args_list
        if c.args[1] == "set_cover_position"
    ]
    assert pos_calls == []
    assert cover.position == 3
    assert cover.tilt == 100

    # --- Cycle 2: identical solar decision. Confirm the position-0 target is
    #     still gated (the defect would have re-fired it and stuck at 0/0).
    hass.services.async_call.reset_mock()
    hass.services.async_call.side_effect = _service_side_effect
    hass.states.get.return_value = cover.state()
    with _patch_caps_dual_axis():
        outcome2, reason2 = await svc.apply_position(
            entity_id,
            0,
            "solar",
            _coupled_ctx(policy, tilt=100, special_positions=specials),
        )

    assert outcome2 == "skipped"
    pos_calls2 = [
        c
        for c in hass.services.async_call.call_args_list
        if c.args[1] == "set_cover_position"
    ]
    assert pos_calls2 == [], "cycle-2 position-0 must be gated (issue #679)"
    # Final reachable state preserved.
    assert (cover.position, cover.tilt) == (3, 100)


@pytest.mark.asyncio
async def test_coupled_cover_with_option_off_stable_at_reachable_endpoint(svc, hass):
    """Flag OFF: cover at reachable position 3 (within tolerance of endpoint 0) stays.

    Issue #507 regression lock: when the cover's reachable resting position (3)
    is within _position_tolerance of the hard endpoint (0), the same-position
    gate treats the cover as "at the endpoint" and suppresses the command.  This
    prevents the relay-click storm that repeated set_cover_position commands (every
    solar cycle) would cause for a motor whose mechanical limit is 3% short of 0.

    The cover and tilt are BOTH already at their desired state (3/100 is the
    stable resting position; tilt=100 is correct), so no commands are needed.
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=64)
    entity_id = "cover.coupled_venetian"
    svc._enforce_endpoints = False
    specials = build_special_positions({})  # [0, 100] — endpoints stay special

    cover = _CoupledCover(position=3, tilt=100)
    policy = _attach_coupled_policy(svc, hass, cover, buf)

    hass.services.async_call.side_effect = lambda *a, **kw: None

    # Solar wants 0/100. Cover is at 3/100 — within endpoint tolerance (default 3%)
    # of position=0.  The same-position gate fires: abs(3-0)=3 <= 3 → skip.
    # No position command is sent; no tilt command is needed (tilt already 100).
    hass.states.get.return_value = cover.state()
    with _patch_caps_dual_axis():
        outcome, _ = await svc.apply_position(
            entity_id,
            0,
            "solar",
            _coupled_ctx(policy, tilt=100, special_positions=specials),
        )

    assert outcome == "skipped"
    # No position command fired — the motor is already at its mechanical stop,
    # within endpoint tolerance of 0.  No tilt command either (tilt already 100).
    hass.services.async_call.assert_not_called()
    # Cover rests stably at the reachable endpoint position, tilt correct.
    assert cover.position == 3
    assert cover.tilt == 100
