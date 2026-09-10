"""Regression tests for issue #1329 — tilt-only publish-lag suppression gap.

A venetian **tilt-only** command (``update_tilt_only``, the dispatch path used
whenever the position axis is delta-gated but the tilt axis keeps solar
tracking — the normal steady state on a slow-changing sun day) opens **no**
back-rotate suppression window at all: ``update_tilt_only`` deliberately never
calls ``stamp_position_command`` (issue #33 follow-on — that stamp is shared
with the position axis and pops ``_settled_at``). Once the 5s command-grace
tail expires, an ordinary tilt-only send is completely unguarded against the
actuator's own late re-publish of the tilt it just settled at, and that late
publish trips a false ``manual_override_set``.

Reproduces the reporter's diagnostics timeline: ``update_tilt_only`` dispatches
tilt=85, the actuator republishes tilt=81 (delta 4%) 16s later — past the 5s
grace, well inside the user's configured 90s ``venetian_backrotate_publish_lag``
— and (pre-fix) nothing suppresses it because the position-axis suppression
window (``is_in_suppression``) was never opened by a tilt-only send.

Wired through ``VenetianPolicy.secondary_axis_check`` exactly as production
composes it — this test builds a real ``DualAxisSequencer`` via
``VenetianPolicy.attach`` (mirroring the wiring helper in
``tests/test_manual_override_venetian.py``) but, deliberately, never seeds a
position command: issue #1329 is exclusively about the tilt-only path, which
never stamps the position-axis window in the first place.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

import pytest

from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_VERIFY_TOLERANCE
from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
    VenetianPolicy,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    SecondaryAxisCheck,
    StateChangeInputs,
)

# Zero the real-motor sleep delays — update_tilt_only drives the real
# sequencer's post-tilt verify/rebase waits, which otherwise add real idle
# time to this suite.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def _make_attached_policy(
    *,
    grace_expired: bool = True,
    backrotate_publish_lag_seconds: float = 90,
    async_call: AsyncMock | None = None,
) -> tuple[VenetianPolicy, MagicMock]:
    """Build a ``VenetianPolicy`` wired to a real ``DualAxisSequencer``.

    Mirrors ``tests/test_manual_override_venetian.py``'s
    ``_make_sequencer_suppression`` wiring, but goes through
    ``VenetianPolicy.attach`` (so ``is_in_tilt_suppression`` runs exactly as
    production composes it) and deliberately seeds NO position command — no
    ``stamp_position_command`` call anywhere. ``grace_expired=True`` (the
    default) models the state every routine tilt-only send is dispatched
    into: ``maybe_update_tilt_only`` only ever fires once the suppression
    window is already closed (issue #756), so by the time a tilt-only send's
    own publish lands, the shared 5s command grace has normally elapsed too.
    """
    hass = MagicMock()
    hass.services.async_call = async_call if async_call is not None else AsyncMock()
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = MagicMock(return_value=not grace_expired)
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=grace_mgr,
        get_current_position=lambda _eid: 19,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "stopped",
        backrotate_publish_lag_seconds=backrotate_publish_lag_seconds,
    )
    return policy, hass


def _pin_elapsed(seq, seconds: float) -> None:
    """Pin a sequencer's elapsed-time formula to an exact value for boundary tests.

    Wall-clock offsets (``stamp -= timedelta(seconds=N)``) always drift a few
    microseconds past ``N`` by the time the assertion runs — enough to prove
    "past the boundary" but never the EXACT boundary itself, which is what
    actually distinguishes ``<`` from ``<=`` (or ``>`` from ``>=``).
    ``DualAxisSequencer._seconds_since`` is a ``staticmethod`` — a non-data
    descriptor — so assigning a plain callable directly on the INSTANCE
    shadows it for every ``self._seconds_since(...)`` call on this sequencer
    only, without touching the class or any other instance.
    """
    seq._seconds_since = lambda _stamp: seconds


async def _dispatch_tilt_and_build_check(
    policy: VenetianPolicy, entity_id: str, *, tilt_target: int = 85
) -> SecondaryAxisCheck:
    """Dispatch a real tilt-only send, then build the check via production wiring.

    Populates BOTH sequencer anchors a real cycle would: ``_tilt_targets``
    (the issue #1006 dispatched-value anchor ``expected`` resolves to) and
    ``_tilt_sent_at`` (the issue #1329 publish-lag anchor). Building the
    ``SecondaryAxisCheck`` via ``policy.secondary_axis_check`` — not by
    hand — means these tests break if production's wiring (``suppression=``
    / ``single_axis_suppression=``) is ever mis-pointed.
    """
    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=tilt_target, current_position=19, reason="solar_tracking"
    )
    return policy.secondary_axis_check(
        SimpleNamespace(tilt=tilt_target), None, entity_id=entity_id
    )


async def test_tilt_only_publish_after_grace_within_verify_tolerance_is_suppressed() -> (
    None
):
    """A tilt-only send's own late publish must be absorbed within the publish-lag window.

    ``update_tilt_only(tilt_target=85)`` dispatches with no position command
    in flight. The actuator republishes ``current_tilt_position=81`` (delta
    4%, exactly the reported issue #1329 delta) 16s later — past the 5s
    command grace, inside the user's 90s ``venetian_backrotate_publish_lag``
    — and within ``VENETIAN_TILT_VERIFY_TOLERANCE`` (5) of the commanded
    value, the same band ``_verify_and_record_tilt`` already treats as
    "arrived". That publish must be suppressed as ACP's own tilt settling,
    not read as a manual override.
    """
    entity_id = "cover.venetian_tilt_only"
    policy, hass = _make_attached_policy()

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )
    assert hass.services.async_call.call_count == 1

    # Advance the clock to T+16s past the tilt dispatch — past the 5s grace
    # (mocked expired via grace_mgr above), still inside the 90s publish-lag
    # window.
    policy._sequencer._tilt_sent_at[entity_id] -= dt.timedelta(seconds=16)

    # Built via the real production wiring (not hand-rolled) so this test
    # breaks if VenetianPolicy.secondary_axis_check is mis-wired.
    check = policy.secondary_axis_check(
        SimpleNamespace(tilt=85), None, entity_id=entity_id
    )
    new_state = SimpleNamespace(attributes={"current_tilt_position": 81})

    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is False
    # NOT consumed (issue #930 finding #2): a tilt-only send never touches
    # the position axis, so unlike the position-anchored suppression window
    # this rejection must not blind the position axis' own independent
    # manual-override check for the cycle.
    assert result.consumed is False
    assert result.event_name == "manual_override_rejected_tilt_suppression"


async def test_tilt_only_dispatch_leaves_position_suppression_closed() -> None:
    """Shape lock: the fix must never call ``stamp_position_command`` from the tilt path.

    That stamp is shared with the position axis via ``primary_axis_suppression``
    and pops ``_settled_at`` (issue #33 follow-on) — a tilt-only send that
    reached for it would blind the position axis' own manual-override
    detection for the full suppression window. This must hold both before and
    after the fix.
    """
    entity_id = "cover.venetian_tilt_only_shape_lock"
    policy, _hass = _make_attached_policy()

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert policy._sequencer.is_in_suppression(entity_id) is False


async def test_failed_tilt_send_opens_no_publish_lag_window() -> None:
    """A failed ``set_cover_tilt_position`` call must not open the publish-lag window.

    Matches the #927 precedent that a dispatch-anchored stamp records only
    once the move really went out: ``_tilt_sent_at`` must be written AFTER a
    successful ``services.async_call``, not before it. Otherwise a
    ``HomeAssistantError`` would still leave a stale stamp behind, opening a
    publish-lag window for a tilt command that was never actually sent.
    """
    entity_id = "cover.venetian_tilt_only_failed_send"
    policy, _hass = _make_attached_policy(
        async_call=AsyncMock(side_effect=HomeAssistantError("boom"))
    )

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert policy._sequencer.is_in_tilt_publish_lag(entity_id, delta=0.0) is False


# ---------------------------------------------------------------------------
# MUST-FIX 1 (post-merge audit): pin BOTH False branches of
# ``is_in_tilt_publish_lag`` and the exact boundaries, from the predicate
# directly AND from the production call path (BINDING_GUIDELINES §4 —
# "pin the boundary from both call sites").
# ---------------------------------------------------------------------------


def test_is_in_tilt_publish_lag_delta_above_verify_tolerance_returns_false() -> None:
    """Delta cap, general case: a delta past ``VENETIAN_TILT_VERIFY_TOLERANCE``
    is a real user move, not settle-lag — the predicate must NOT suppress it,
    even with a freshly-stamped, well-inside-the-window dispatch.
    """
    entity_id = "cover.venetian_delta_above_tolerance"
    policy, _hass = _make_attached_policy()
    policy._sequencer._tilt_sent_at[entity_id] = dt.datetime.now(dt.UTC)

    assert (
        policy._sequencer.is_in_tilt_publish_lag(
            entity_id, delta=VENETIAN_TILT_VERIFY_TOLERANCE + 1
        )
        is False
    )


def test_is_in_tilt_publish_lag_delta_at_verify_tolerance_boundary_returns_true() -> (
    None
):
    """Boundary pin: ``delta == VENETIAN_TILT_VERIFY_TOLERANCE`` IS suppressed.

    Pins the docstring's ``<=`` — not ``<``. Flipping the operator in
    ``is_in_tilt_publish_lag`` to ``<`` makes this fail.
    """
    entity_id = "cover.venetian_delta_at_tolerance_boundary"
    policy, _hass = _make_attached_policy()
    policy._sequencer._tilt_sent_at[entity_id] = dt.datetime.now(dt.UTC)

    assert (
        policy._sequencer.is_in_tilt_publish_lag(
            entity_id, delta=VENETIAN_TILT_VERIFY_TOLERANCE
        )
        is True
    )


def test_is_in_tilt_publish_lag_time_elapsed_at_lag_boundary_returns_false() -> None:
    """Boundary pin: elapsed == ``_backrotate_publish_lag_seconds`` is EXPIRED.

    Pins the ``>=`` in ``if self._seconds_since(stamp) >= lag: return False``
    — not ``>``. Uses ``_pin_elapsed`` because a wall-clock offset can never
    land on the exact boundary (see that helper's docstring). Flipping the
    operator to ``>`` makes this fail.
    """
    entity_id = "cover.venetian_time_at_lag_boundary"
    policy, _hass = _make_attached_policy(backrotate_publish_lag_seconds=90)
    policy._sequencer._tilt_sent_at[entity_id] = dt.datetime.now(dt.UTC)
    # Derived from the sequencer's own window, not a second hardcoded ``90``,
    # so the pin and the configured window can't drift apart by hand.
    lag_seconds = policy._sequencer._backrotate_publish_lag_seconds
    _pin_elapsed(policy._sequencer, lag_seconds)

    assert policy._sequencer.is_in_tilt_publish_lag(entity_id, delta=0.0) is False


def test_is_in_tilt_publish_lag_time_elapsed_just_under_lag_boundary_returns_true() -> (
    None
):
    """Contrast case: a hair under the lag boundary is still inside the window.

    Paired with the boundary test above so the boundary is actually pinned
    from both sides — this alone can't distinguish ``>`` from ``>=``, but
    together with the exact-boundary test it proves the transition happens
    exactly at ``lag_seconds``, not one tick either side of it.

    ``_tilt_sent_at`` is set to real "now" just above, so REAL elapsed time
    at assertion time is also ~0s — comfortably under the window even if
    ``_pin_elapsed`` silently stopped shadowing ``_seconds_since``. That would
    make the behavioural assertion below pass for the wrong reason, so the
    guard assertion proves the pin actually took effect before trusting it.
    """
    entity_id = "cover.venetian_time_under_lag_boundary"
    policy, _hass = _make_attached_policy(backrotate_publish_lag_seconds=90)
    policy._sequencer._tilt_sent_at[entity_id] = dt.datetime.now(dt.UTC)
    # Derived from the sequencer's own window, not a second hardcoded ``90``,
    # so the pin and the configured window can't drift apart by hand.
    lag_seconds = policy._sequencer._backrotate_publish_lag_seconds
    pinned_elapsed = lag_seconds - 0.001
    _pin_elapsed(policy._sequencer, pinned_elapsed)

    # Guard: prove the shadow is live before trusting the assertion below.
    assert (
        policy._sequencer._seconds_since(policy._sequencer._tilt_sent_at[entity_id])
        == pinned_elapsed
    )

    assert policy._sequencer.is_in_tilt_publish_lag(entity_id, delta=0.0) is True


async def test_tilt_only_publish_delta_above_verify_tolerance_trips_override() -> None:
    """Call-path pin: delta above the verify tolerance is NOT suppressed.

    Companion to ``test_is_in_tilt_publish_lag_delta_above_verify_tolerance_returns_false``
    — pins the same boundary through ``VenetianPolicy.secondary_axis_check``'s
    real wiring instead of the bare predicate.
    """
    entity_id = "cover.venetian_call_path_delta_above"
    policy, _hass = _make_attached_policy()
    check = await _dispatch_tilt_and_build_check(policy, entity_id)

    reported = 85 - (VENETIAN_TILT_VERIFY_TOLERANCE + 1)
    new_state = SimpleNamespace(attributes={"current_tilt_position": reported})
    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is True
    assert result.consumed is True
    assert result.event_name == "manual_override_set"


async def test_tilt_only_publish_delta_at_verify_tolerance_boundary_is_suppressed() -> (
    None
):
    """Call-path boundary pin: ``delta == VENETIAN_TILT_VERIFY_TOLERANCE`` IS suppressed.

    Companion to ``test_is_in_tilt_publish_lag_delta_at_verify_tolerance_boundary_returns_true``,
    pinned through ``secondary_axis_check(...).evaluate(...)`` — the actual
    production call path (BINDING_GUIDELINES §4).
    """
    entity_id = "cover.venetian_call_path_delta_boundary"
    policy, _hass = _make_attached_policy()
    check = await _dispatch_tilt_and_build_check(policy, entity_id)

    reported = 85 - VENETIAN_TILT_VERIFY_TOLERANCE
    new_state = SimpleNamespace(attributes={"current_tilt_position": reported})
    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is False
    assert result.consumed is False
    assert result.event_name == "manual_override_rejected_tilt_suppression"


async def test_tilt_only_publish_at_lag_boundary_trips_override() -> None:
    """Call-path time-boundary pin: elapsed == lag_seconds is EXPIRED, not suppressed.

    Companion to ``test_is_in_tilt_publish_lag_time_elapsed_at_lag_boundary_returns_false``,
    pinned through the production call path. Delta is 4% (the exact reported
    issue #1329 value) — comfortably inside ``VENETIAN_TILT_VERIFY_TOLERANCE``
    (5) so ONLY the time boundary is under test; if the lag window were still
    open this delta would have been suppressed.
    """
    entity_id = "cover.venetian_call_path_time_boundary"
    policy, _hass = _make_attached_policy(backrotate_publish_lag_seconds=90)
    check = await _dispatch_tilt_and_build_check(policy, entity_id)
    # Derived from the sequencer's own window, not a second hardcoded ``90``,
    # so the pin and the configured window can't drift apart by hand.
    _pin_elapsed(policy._sequencer, policy._sequencer._backrotate_publish_lag_seconds)

    new_state = SimpleNamespace(attributes={"current_tilt_position": 81})  # delta 4%
    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is True
    assert result.consumed is True
    assert result.event_name == "manual_override_set"


# ---------------------------------------------------------------------------
# MUST-FIX 3 (post-merge audit): the deviation's own contract — a tilt
# publish inside the lag window must suppress ONLY the tilt axis. A
# simultaneous genuine POSITION move must still trip (issue #930 finding #2 —
# this is the entire reason ``single_axis_suppression`` is non-consuming
# rather than repointing ``suppression=`` outright).
# ---------------------------------------------------------------------------


async def test_position_axis_still_trips_manual_override_inside_tilt_publish_lag_window() -> (
    None
):
    """Drive the real manual-override entry point with both axes moving at once.

    The tilt axis reports a value inside the fresh tilt-only publish-lag
    window (delta 4%, <= ``VENETIAN_TILT_VERIFY_TOLERANCE``) — suppressed,
    non-consumed. The SAME state change also reports a POSITION delta far
    above ``manual_threshold``, with no position command ever stamped for
    this entity (a tilt-only send never touches
    ``primary_axis_suppression``'s anchor). ``AdaptiveCoverManager.handle_state_change``
    is the same production entry point the coordinator calls.

    This is the property that distinguishes ``single_axis_suppression``
    (``consumed=False``) from the rejected ``consumed=True`` design: if the
    tilt-only publish-lag suppression ever blinded the position axis too,
    this test would fail with the cover NOT marked manual.
    """
    entity_id = "cover.venetian_dual_axis_trip"
    policy, _hass = _make_attached_policy()
    check = await _dispatch_tilt_and_build_check(policy, entity_id)

    mgr = AdaptiveCoverManager(
        hass=MagicMock(), reset_duration={"hours": 2}, logger=MagicMock()
    )
    mgr.add_covers([entity_id])
    mgr.hass.states.get = MagicMock(return_value=None)

    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    # Tilt: delta 4% from the commanded 85 -> above manual_threshold=3 (so the
    # numeric branch is even reached) but inside VENETIAN_TILT_VERIFY_TOLERANCE
    # (5) and the fresh publish-lag window -> suppressed. Position: our_state
    # 19 (the last commanded position, never stamped by the tilt-only send)
    # vs reported 60 -> delta 41%, far past manual_threshold=3 -> must trip.
    event.new_state.attributes = {
        "current_tilt_position": 81,
        "current_position": 60,
    }
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.old_state = None

    mgr.handle_state_change(
        event,
        StateChangeInputs(
            our_state=19,
            policy=policy,
            allow_reset=True,
            is_waiting=lambda _eid: False,
            manual_threshold=3,
            secondary_axis_check=check,
        ),
    )

    assert mgr.is_cover_manual(entity_id), (
        "the position axis must still trip manual override even though the "
        "tilt axis's own publish-lag window suppressed the tilt check "
        "(issue #930 finding #2 / the consumed=False contract this fix relies on)"
    )
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_rejected_tilt_suppression"
        for evt in events
    ), f"expected the tilt axis to be suppressed too, got {events}"
    assert any(
        evt.get("event") == "manual_override_set" for evt in events
    ), f"expected the position axis to trip manual_override_set, got {events}"
