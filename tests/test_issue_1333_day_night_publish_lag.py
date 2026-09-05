"""Regression tests for issue #1333 — day/night blend-only publish-lag gap.

``DayNightShadePolicy`` (Model A, ``position_tilt``) drives its blend axis
through the SAME ``DualAxisSequencer`` venetian uses, by composition. That
sequencer stamps ``_tilt_sent_at`` on every successful tilt dispatch
regardless of which policy called ``update_tilt_only``, so the issue #1329
publish-lag anchor already exists on the day/night path — it was simply never
consulted there:

* ``DayNightShadePolicy.is_in_tilt_suppression`` carried the PRE-#1329 body
  (``is_in_suppression_with_cap`` alone), never OR-ing in
  ``sequencer.is_in_tilt_publish_lag``;
* ``DayNightShadePolicy.secondary_axis_check`` never wired that predicate as
  ``single_axis_suppression=``, so ``SecondaryAxisCheck.evaluate`` could not
  reach the tilt gate at all for a day/night blend publish.

Net effect (pre-fix): a blend-only send's own late re-publish, landing past
the 5 s command grace, fell straight through to ``manual_override_set`` —
exactly the venetian mechanism #1329 fixed, on the other policy that shares
the sequencer.

Mirrors ``tests/test_issue_1329_tilt_only_publish_lag.py``'s structure with
``DayNightShadePolicy`` swapped in, so the two dual-axis policies are held to
the same contract by the same shape of test.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError

import pytest

from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_VERIFY_TOLERANCE
from custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    SecondaryAxisCheck,
    StateChangeInputs,
)
from tests.test_issue_1329_tilt_only_publish_lag import _pin_elapsed

# Zero the real-motor sleep delays. The fixture patches the *sequencer*
# module namespace, so it neutralizes day/night's composed sequencer exactly
# as it does venetian's.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def _make_attached_dns_policy(
    *,
    grace_expired: bool = True,
    async_call: AsyncMock | None = None,
) -> tuple[DayNightShadePolicy, MagicMock]:
    """Build a Model A ``DayNightShadePolicy`` wired to a real ``DualAxisSequencer``.

    Goes through ``DayNightShadePolicy.attach`` (so ``is_in_tilt_suppression``
    and ``secondary_axis_check`` run exactly as production composes them) and
    deliberately seeds NO position command — no ``stamp_position_command`` call
    anywhere. Issue #1333, like #1329, is exclusively about the secondary-axis
    dispatch path, which never stamps the position-axis window in the first
    place. ``grace_expired=True`` (the default) models the state every routine
    blend-only send is dispatched into: the shared 5 s command grace has
    normally elapsed by the time the send's own publish lands.
    """
    hass = MagicMock()
    hass.services.async_call = async_call if async_call is not None else AsyncMock()
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = MagicMock(return_value=not grace_expired)
    policy = DayNightShadePolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=grace_mgr,
        get_current_position=lambda _eid: 19,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "stopped",
    )
    assert policy._drives_dual_axis(), "default DNS control model must be Model A"
    return policy, hass


async def _dispatch_blend_and_build_check(
    policy: DayNightShadePolicy, entity_id: str, *, blend_target: int = 85
) -> SecondaryAxisCheck:
    """Dispatch a real blend-only send, then build the check via production wiring.

    Populates BOTH sequencer anchors a real cycle would: ``_tilt_targets``
    (the issue #1006 dispatched-value anchor ``expected`` resolves to) and
    ``_tilt_sent_at`` (the issue #1329/#1333 publish-lag anchor). Building the
    ``SecondaryAxisCheck`` via ``policy.secondary_axis_check`` — not by hand —
    means these tests break if production's wiring (``suppression=`` /
    ``single_axis_suppression=``) is ever mis-pointed.
    """
    await policy._sequencer.update_tilt_only(
        entity_id,
        tilt_target=blend_target,
        current_position=19,
        reason="solar_tracking",
    )
    return policy.secondary_axis_check(
        SimpleNamespace(tilt=blend_target), None, entity_id=entity_id
    )


async def test_dns_blend_only_publish_after_grace_within_verify_tolerance_is_suppressed() -> (
    None
):
    """A blend-only send's own late publish must be absorbed within the publish-lag window.

    ``update_tilt_only(tilt_target=85)`` dispatches with no position command
    in flight. The actuator republishes ``current_tilt_position=81`` (delta
    4%, the same delta reported on the venetian side of this defect in #1329)
    16 s later — past the 5 s command grace, inside the publish-lag window,
    and within ``VENETIAN_TILT_VERIFY_TOLERANCE`` (5) of the commanded value.
    That publish must be suppressed as ACP's own blend settling, not read as
    a manual override.
    """
    entity_id = "cover.day_night_blend_only"
    policy, hass = _make_attached_dns_policy()

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )
    assert hass.services.async_call.call_count == 1

    # Advance the clock to T+16s past the blend dispatch — past the 5s grace
    # (mocked expired via grace_mgr above), still inside the publish-lag
    # window.
    policy._sequencer._tilt_sent_at[entity_id] -= dt.timedelta(seconds=16)

    # Built via the real production wiring (not hand-rolled) so this test
    # breaks if DayNightShadePolicy.secondary_axis_check is mis-wired. Built
    # directly rather than through the dispatch helper so the 16s offset above
    # survives (a second dispatch would re-stamp ``_tilt_sent_at``).
    check = policy.secondary_axis_check(
        SimpleNamespace(tilt=85), None, entity_id=entity_id
    )
    new_state = SimpleNamespace(attributes={"current_tilt_position": 81})

    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is False
    # NOT consumed (issue #930 finding #2): a blend-only send never commands
    # the position axis, so unlike the position-anchored suppression window
    # this rejection must not blind the position axis' own independent
    # manual-override check for the cycle.
    assert result.consumed is False
    assert result.event_name == "manual_override_rejected_tilt_suppression"


async def test_dns_secondary_axis_check_wires_single_axis_suppression_callback() -> (
    None
):
    """Wiring lock: the blend check must point the two suppression seams correctly.

    ``suppression=`` stays :meth:`primary_axis_suppression` (consuming — a
    True result blinds both axes) and ``single_axis_suppression=`` gets
    :meth:`is_in_tilt_suppression` (non-consuming — blend axis only). Pointing
    the publish-lag predicate at ``suppression=`` instead would reintroduce
    the cross-axis blinding issue #930 finding #2 fixed, and would pass every
    other test in this file.
    """
    entity_id = "cover.day_night_blend_wiring"
    policy, _hass = _make_attached_dns_policy()

    check = await _dispatch_blend_and_build_check(policy, entity_id)

    assert check.single_axis_suppression == policy.is_in_tilt_suppression
    assert check.suppression == policy.primary_axis_suppression


async def test_dns_is_in_tilt_suppression_true_within_publish_lag_window_at_tolerance_boundary() -> (
    None
):
    """Predicate pin: the composed day/night gate ORs in the publish-lag window.

    No position command is ever stamped, so ``primary_axis_suppression`` — the
    first disjunct — is provably False here (``is_in_suppression_with_cap``
    gates on ``is_in_suppression``, which returns False with no stamp, and the
    command grace is mocked expired). The only term that can make this True is
    ``sequencer.is_in_tilt_publish_lag``. ``delta`` sits exactly on
    ``VENETIAN_TILT_VERIFY_TOLERANCE``, pinning the ``<=`` boundary from the
    policy level too.
    """
    entity_id = "cover.day_night_predicate_at_tolerance"
    policy, _hass = _make_attached_dns_policy()
    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    # Guard: prove the first disjunct contributes nothing, so a True below can
    # only have come from the publish-lag term.
    assert (
        policy.primary_axis_suppression(entity_id, VENETIAN_TILT_VERIFY_TOLERANCE)
        is False
    )

    assert (
        policy.is_in_tilt_suppression(entity_id, delta=VENETIAN_TILT_VERIFY_TOLERANCE)
        is True
    )


async def test_dns_is_in_tilt_suppression_false_above_verify_tolerance() -> None:
    """Delta cap: a delta past ``VENETIAN_TILT_VERIFY_TOLERANCE`` is a user move.

    Over-suppression guard — green before AND after the fix. It fails only if
    the new OR-term is widened past the verify tolerance the shared sequencer
    predicate enforces.
    """
    entity_id = "cover.day_night_predicate_above_tolerance"
    policy, _hass = _make_attached_dns_policy()
    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert (
        policy.is_in_tilt_suppression(
            entity_id, delta=VENETIAN_TILT_VERIFY_TOLERANCE + 1
        )
        is False
    )


async def test_dns_is_in_tilt_suppression_false_at_lag_boundary() -> None:
    """Time boundary: elapsed == ``_backrotate_publish_lag_seconds`` is EXPIRED.

    Over-suppression guard — green before AND after the fix. Uses
    ``_pin_elapsed`` because a wall-clock offset can never land on the exact
    boundary (see that helper's docstring in the #1329 file). The window is
    read off the sequencer instance rather than hardcoded, so the pin can't
    drift from whatever default/config value the sequencer actually holds.
    """
    entity_id = "cover.day_night_predicate_at_lag_boundary"
    policy, _hass = _make_attached_dns_policy()
    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )
    lag_seconds = policy._sequencer._backrotate_publish_lag_seconds
    _pin_elapsed(policy._sequencer, lag_seconds)

    # Guard: prove the shadow is live before trusting the assertion below —
    # real elapsed here is ~0s, which would make this pass for the wrong
    # reason if the pin ever stopped taking effect.
    assert (
        policy._sequencer._seconds_since(policy._sequencer._tilt_sent_at[entity_id])
        == lag_seconds
    )

    assert policy.is_in_tilt_suppression(entity_id, delta=0.0) is False


async def test_dns_blend_only_publish_delta_above_verify_tolerance_trips_override() -> (
    None
):
    """Call-path pin: delta above the verify tolerance is NOT suppressed.

    Companion to the predicate-level delta-cap test, pinned through
    ``DayNightShadePolicy.secondary_axis_check``'s real wiring instead of the
    bare predicate. Over-suppression guard — green on both sides of the fix.
    """
    entity_id = "cover.day_night_call_path_delta_above"
    policy, _hass = _make_attached_dns_policy()
    check = await _dispatch_blend_and_build_check(policy, entity_id)

    reported = 85 - (VENETIAN_TILT_VERIFY_TOLERANCE + 1)
    new_state = SimpleNamespace(attributes={"current_tilt_position": reported})
    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is True
    assert result.consumed is True
    assert result.event_name == "manual_override_set"


async def test_dns_blend_publish_at_lag_boundary_trips_override() -> None:
    """Call-path time-boundary pin: elapsed == lag_seconds is EXPIRED, not suppressed.

    Delta is 4% — comfortably inside ``VENETIAN_TILT_VERIFY_TOLERANCE`` (5) so
    ONLY the time boundary is under test; if the lag window were still open
    this delta would have been suppressed. Over-suppression guard — green on
    both sides of the fix.
    """
    entity_id = "cover.day_night_call_path_time_boundary"
    policy, _hass = _make_attached_dns_policy()
    check = await _dispatch_blend_and_build_check(policy, entity_id)
    _pin_elapsed(policy._sequencer, policy._sequencer._backrotate_publish_lag_seconds)

    new_state = SimpleNamespace(attributes={"current_tilt_position": 81})  # delta 4%
    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is True
    assert result.consumed is True
    assert result.event_name == "manual_override_set"


async def test_dns_position_axis_still_trips_manual_override_inside_blend_publish_lag_window() -> (
    None
):
    """Drive the real manual-override entry point with both axes moving at once.

    The blend axis reports a value inside the fresh publish-lag window (delta
    4%, <= ``VENETIAN_TILT_VERIFY_TOLERANCE``) — suppressed, non-consumed. The
    SAME state change also reports a POSITION delta far above
    ``manual_threshold``, with no position command ever stamped for this
    entity (a blend-only send never touches ``primary_axis_suppression``'s
    anchor). ``AdaptiveCoverManager.handle_state_change`` is the same
    production entry point the coordinator calls.

    This is the property that distinguishes ``single_axis_suppression``
    (``consumed=False``) from a consuming suppression: if the blend-only
    publish-lag suppression ever blinded the position axis too, the pair of
    event assertions below would not both hold (issue #930 finding #2).
    """
    entity_id = "cover.day_night_dual_axis_trip"
    policy, _hass = _make_attached_dns_policy()
    check = await _dispatch_blend_and_build_check(policy, entity_id)

    mgr = AdaptiveCoverManager(
        hass=MagicMock(), reset_duration={"hours": 2}, logger=MagicMock()
    )
    mgr.add_covers([entity_id])
    mgr.hass.states.get = MagicMock(return_value=None)

    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    # Blend: delta 4% from the commanded 85 -> above manual_threshold=3 (so the
    # numeric branch is even reached) but inside VENETIAN_TILT_VERIFY_TOLERANCE
    # (5) and the fresh publish-lag window -> suppressed. Position: our_state
    # 19 (the last commanded position, never stamped by the blend-only send)
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
        "blend axis's own publish-lag window suppressed the blend check "
        "(issue #930 finding #2 / the consumed=False contract this fix relies on)"
    )
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_rejected_tilt_suppression"
        for evt in events
    ), f"expected the blend axis to be suppressed too, got {events}"
    assert any(
        evt.get("event") == "manual_override_set" for evt in events
    ), f"expected the position axis to trip manual_override_set, got {events}"


async def test_dns_failed_blend_send_opens_no_publish_lag_window() -> None:
    """A failed ``set_cover_tilt_position`` call must not open the publish-lag window.

    The stamp is written only AFTER a successful ``services.async_call``, so a
    ``HomeAssistantError`` leaves no stale anchor behind and the composed
    day/night predicate stays False. Asserted through the POLICY predicate,
    not the bare sequencer method, so it also covers the composition.
    Over-suppression guard — green on both sides of the fix.
    """
    entity_id = "cover.day_night_failed_blend_send"
    policy, _hass = _make_attached_dns_policy(
        async_call=AsyncMock(side_effect=HomeAssistantError("boom"))
    )

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert policy.is_in_tilt_suppression(entity_id, delta=0.0) is False


def test_dns_attach_forwards_publish_lag_to_sequencer() -> None:
    """attach() must forward ``backrotate_publish_lag_seconds`` to the sequencer.

    The coordinator hands this option to EVERY policy's ``attach``; venetian
    forwards it, day/night dropped it on the floor, so a day/night sequencer
    always ran the built-in default no matter what the user configured — and
    the publish-lag window this fix relies on is exactly the window that
    option sizes.
    """
    policy = DayNightShadePolicy()

    with patch(
        "custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy.DualAxisSequencer"
    ) as MockSeq:
        MockSeq.return_value = MagicMock()
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            backrotate_publish_lag_seconds=120.0,
        )
        _, kwargs = MockSeq.call_args
        assert kwargs.get("backrotate_publish_lag_seconds") == 120.0


def test_resolve_single_axis_suppression_composition() -> None:
    """The shared helper composes exactly two disjuncts, in a pinned order.

    Imported INSIDE the test body on purpose: a top-level import of a symbol
    the fix introduces would make pre-fix COLLECTION of this whole file fail,
    masking every other test's true red reason.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        resolve_single_axis_suppression,
    )

    entity_id = "cover.day_night_helper"
    primary = MagicMock(return_value=True)

    # No sequencer -> False, and the primary predicate is never consulted.
    assert resolve_single_axis_suppression(None, entity_id, 0.0, primary) is False
    primary.assert_not_called()

    # Primary True short-circuits: the lag term must not even be evaluated.
    sequencer = MagicMock()
    sequencer.is_in_tilt_publish_lag = MagicMock(return_value=False)
    assert resolve_single_axis_suppression(sequencer, entity_id, 1.0, primary) is True
    sequencer.is_in_tilt_publish_lag.assert_not_called()

    # Primary False, lag True -> True (the disjunct this fix adds).
    primary_false = MagicMock(return_value=False)
    sequencer.is_in_tilt_publish_lag = MagicMock(return_value=True)
    assert (
        resolve_single_axis_suppression(sequencer, entity_id, 2.0, primary_false)
        is True
    )
    sequencer.is_in_tilt_publish_lag.assert_called_once_with(entity_id, 2.0)

    # Both False -> False.
    sequencer.is_in_tilt_publish_lag = MagicMock(return_value=False)
    assert (
        resolve_single_axis_suppression(sequencer, entity_id, 3.0, primary_false)
        is False
    )
