"""Issue #954 — venetian tilt axis silently coupled to the carriage delta gate.

Three venetian covers (``venetian_mode: position_and_tilt``) stopped emitting
tilt commands entirely while the solar handler kept winning and the engine
kept calculating changing tilt targets. Root cause:
``VenetianPolicy.maybe_update_tilt_only`` — the only mechanism that services
the tilt axis on a cycle where the carriage doesn't move — had exactly ONE
call site: the ``same_position`` branch of
``CoverCommandService.apply_position``. Every other early-return
(``delta_too_small``, ``time_delta_too_small``, ``manual_override``) returned
without giving the policy a chance at the secondary axis, so a below-threshold
*carriage* delta silently dropped the tilt target for hours (reporter's exact
numbers: calculated 1%, current 2%, min delta 5%).

The fix extracts a private ``_service_secondary_axis`` helper and calls it
from the ``same_position``, ``delta_too_small``, and ``time_delta_too_small``
branches — but deliberately NOT from ``manual_override`` (genuine hands-off
intent, not hysteresis; see #927/#930).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.cover_types import VenetianPolicy, get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)

pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def svc(hass):
    """Return a venetian CoverCommandService — matches the reporter's cover type.

    ``_get_current_position`` is monkeypatched per-test (see ``_patch_position``)
    so tests don't need to construct realistic ``hass.states.get(...)`` mocks;
    only ``hass.services.async_call`` needs to be real for tests that assert
    on dispatched service calls.
    """
    s = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_venetian",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    s._enabled = True
    return s


def _patch_position(svc, value):
    svc._get_current_position = MagicMock(return_value=value)


def _ctx(**overrides) -> PositionContext:
    """PositionContext with all gates passing by default (force=False)."""
    defaults = {
        "auto_control": True,
        "manual_override": False,
        "sun_just_appeared": False,
        "min_change": 5,
        "time_threshold": 10,
        "special_positions": [0, 100],
        "inverse_state": False,
        "force": False,
    }
    defaults.update(overrides)
    return PositionContext(**defaults)


def _attach_venetian_policy(hass, *, current_position: int, event_buffer=None):
    """Real VenetianPolicy + DualAxisSequencer, mocked hass (issue #954 repro)."""
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: current_position,
        set_commanded_position=MagicMock(),
        position_tolerance=5,
        is_dry_run=lambda: False,
        event_buffer=event_buffer,
    )
    return policy


# ---------------------------------------------------------------------------
# RED — new behaviour: the delta/time gates must still service the tilt axis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tilt_still_sent_when_position_delta_too_small(svc):
    """Reporter's exact scenario: calc=1, current=2, min_change=5 → delta_too_small
    on the carriage, but ``context.tilt`` (47%, the engine's live calculation)
    must still reach ``maybe_update_tilt_only`` — the tilt axis owns its own
    independent gates downstream and must not be starved by the carriage gate.
    """
    entity_id = "cover.og_studio_tur"
    _patch_position(svc, 2)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy)

    outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "delta_too_small"
    policy.maybe_update_tilt_only.assert_awaited_once_with(
        entity_id,
        current_position=2,
        context=ctx,
        reason="solar",
    )

    skip = svc.last_skipped_action
    assert skip["reason"] == "delta_too_small"
    assert skip["position_delta"] == 1
    assert skip["min_delta_required"] == 5


@pytest.mark.asyncio
async def test_tilt_sequencer_emits_command_when_position_delta_too_small(svc, hass):
    """End-to-end reproduction of the diagnostics: 83 minutes of delta_too_small
    skips with zero tilt events (d2 skip-histogram: ``delta_too_small: 124``,
    nothing else). Position delta is below min_change (2 -> 1, delta=1 < 5) so
    the carriage is correctly gated, but the real ``DualAxisSequencer`` must
    still dispatch ``set_cover_tilt_position`` and record ``tilt_command_sent``.
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=20)
    entity_id = "cover.og_studio_tur"
    _patch_position(svc, 2)

    policy = _attach_venetian_policy(hass, current_position=2, event_buffer=buf)
    policy._last_tilt = 47  # engine's calculated tilt this cycle (d2/d3 diagnostics)

    ctx = _ctx(tilt=47, policy=policy)

    outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "delta_too_small"

    tilt_calls = [
        c
        for c in hass.services.async_call.call_args_list
        if c.args[1] == "set_cover_tilt_position"
    ]
    assert len(tilt_calls) == 1
    assert tilt_calls[0].args[2]["tilt_position"] == 47

    sent_events = [e for e in buf.snapshot() if e.get("event") == "tilt_command_sent"]
    assert len(sent_events) == 1
    assert sent_events[0]["tilt_position"] == 47


@pytest.mark.asyncio
async def test_tilt_still_sent_when_time_delta_too_small(svc):
    """Same shape as the delta gate, but tripping the time gate instead.

    ``delta_time`` is a carriage rate-limiter, not a tilt gate (approved
    in-scope decision) — a carriage move suppressed for being too recent must
    not also block the independently-gated tilt axis.
    """
    entity_id = "cover.og_studio_fenster"
    _patch_position(svc, 90)  # large position delta so only the time gate fires
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy)

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent,
    ):
        outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "time_delta_too_small"
    policy.maybe_update_tilt_only.assert_awaited_once_with(
        entity_id,
        current_position=90,
        context=ctx,
        reason="solar",
    )


# ---------------------------------------------------------------------------
# Pins — existing behaviour that must NOT change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tilt_not_sent_under_manual_override(svc):
    """Manual override is genuine hands-off intent, not hysteresis (#927/#930).

    The hook must NOT be called and the skip reason must stay
    ``manual_override`` — pins the fix against over-reaching into the
    false-manual-override family the drift-reset suppression machinery
    guards against.
    """
    entity_id = "cover.og_kuche"
    _patch_position(svc, 90)  # position + time gates both pass

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy, manual_override=True, time_threshold=0)

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "manual_override"
    policy.maybe_update_tilt_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_tilt_not_sent_under_manual_override_when_position_delta_too_small(svc):
    """Manual override combined with a tripped hysteresis gate (audit finding).

    ``delta_too_small`` and ``time_delta_too_small`` are evaluated BEFORE the
    ``manual_override`` branch, so the branch-ordering alone cannot be relied
    on to keep tilt off the wire under override — the guard inside
    ``_service_secondary_axis`` itself must check ``context.manual_override``.
    Reporter's exact numbers: current=2, target=1, min_change=5 -> delta=1<5.
    """
    entity_id = "cover.og_studio_tur"
    _patch_position(svc, 2)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy, manual_override=True)

    outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "delta_too_small"
    policy.maybe_update_tilt_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_tilt_not_sent_under_manual_override_when_time_delta_too_small(svc):
    """Same as above but tripping the time gate instead of the delta gate."""
    entity_id = "cover.og_studio_fenster"
    _patch_position(svc, 90)  # large position delta so only the time gate fires
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy, manual_override=True)

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent,
    ):
        outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "time_delta_too_small"
    policy.maybe_update_tilt_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_forced_cycle_services_tilt_despite_manual_override(svc):
    """Re-audit regression pin: a forced cycle must still service the tilt
    axis even while ``manual_override`` is latched.

    The ``same_position`` branch has no ``not context.force`` guard — only
    ``sun_just_appeared``/``force_endpoint``/``user_command`` bypass it — so a
    safety slot or any custom-position slot outranking
    ``ManualOverrideHandler`` (priority 80) reaches it with ``force=True``
    while override is still latched. If the carriage already sits at the
    slot's position, this is the exact scenario: current == target == 50,
    tilt=47, force=True, manual_override=True. The tilt must still go out —
    ``_service_secondary_axis``'s guard only excludes manual_override when
    NOT forced, mirroring where the production gates actually live
    (``PositionContext.force``: "skip delta/time/manual_override gates").
    """
    entity_id = "cover.og_studio_tur"
    _patch_position(svc, 50)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy, manual_override=True, force=True)

    outcome, reason = await svc.apply_position(entity_id, 50, "custom_position", ctx)

    assert outcome == "skipped"
    assert reason == "same_position"
    policy.maybe_update_tilt_only.assert_awaited_once_with(
        entity_id,
        current_position=50,
        context=ctx,
        reason="custom_position",
    )


@pytest.mark.asyncio
async def test_unforced_same_position_does_not_service_tilt_under_manual_override(svc):
    """The other direction of the force interaction: without ``force``, the
    manual-override exclusion still applies on the ``same_position`` branch.

    Same current/target/tilt as the forced test above, but ``force=False`` —
    pins that the fix doesn't accidentally drop the exclusion for the
    ordinary (non-safety, non-custom-position-slot) case.
    """
    entity_id = "cover.og_studio_tur"
    _patch_position(svc, 50)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=47, policy=policy, manual_override=True, force=False)

    outcome, reason = await svc.apply_position(entity_id, 50, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "same_position"
    policy.maybe_update_tilt_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_position_branch_still_updates_tilt(svc):
    """The pre-existing same_position -> tilt wiring must survive the extraction."""
    entity_id = "cover.og_kuche"
    _patch_position(svc, 2)

    policy = MagicMock()
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=62, policy=policy)

    outcome, reason = await svc.apply_position(entity_id, 2, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "same_position"
    policy.maybe_update_tilt_only.assert_awaited_once_with(
        entity_id,
        current_position=2,
        context=ctx,
        reason="solar",
    )


@pytest.mark.asyncio
async def test_single_axis_cover_delta_branch_does_not_call_tilt_hook(svc):
    """Single-axis cover types (no tilt) never trip the new call site.

    ``context.tilt=None`` short-circuits the guard in the shared helper
    regardless of which ``CoverTypePolicy`` is in play — the base's no-op
    ``maybe_update_tilt_only`` is what makes every non-venetian cover type
    correct, but the guard itself must not even reach the policy call.
    """
    entity_id = "cover.living_room_blind"
    _patch_position(svc, 2)

    policy = get_policy("cover_blind")
    policy.maybe_update_tilt_only = AsyncMock()

    ctx = _ctx(tilt=None, policy=policy)

    outcome, reason = await svc.apply_position(entity_id, 1, "solar", ctx)

    assert outcome == "skipped"
    assert reason == "delta_too_small"
    policy.maybe_update_tilt_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_delta_branch_tilt_respects_drift_reset_eligibility(hass, svc):
    """Guard against #930/#927: the delta-branch tilt send must thread the exact
    same ``drift_reset_eligible`` value through to the sequencer that the
    ``same_position`` branch already produces for an identical context.

    Reporter's setting: ``venetian_tilt_reset_scope=all_tilt_commands``
    (the module default) — the most exposed configuration, since every tilt
    send is drift-reset eligible under that scope.
    """
    entity_id = "cover.og_kuche"
    policy = _attach_venetian_policy(hass, current_position=2)
    policy._last_tilt = 62  # d1's calculated tilt

    spy = AsyncMock()
    policy._sequencer.update_tilt_only = spy

    ctx = _ctx(tilt=62, policy=policy)

    # Drive through the same_position branch (current == target == 2).
    _patch_position(svc, 2)
    await svc.apply_position(entity_id, 2, "solar", ctx)
    assert spy.await_count == 1
    same_position_eligible = spy.await_args.kwargs["drift_reset_eligible"]

    # Drive through the delta_too_small branch (current=2, target=1, delta=1<5)
    # with an otherwise identical context.
    await svc.apply_position(entity_id, 1, "solar", ctx)
    assert spy.await_count == 2
    delta_branch_eligible = spy.await_args.kwargs["drift_reset_eligible"]

    assert delta_branch_eligible == same_position_eligible
