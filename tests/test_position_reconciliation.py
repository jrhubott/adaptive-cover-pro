"""Tests for CoverCommandService reconciliation and apply_position lifecycle.

Covers:
- apply_position: all gate checks, force bypass, sent/skipped return values
- check_target_reached: tolerance-based clearance of wait_for_target
- _reconcile: cover at target, cover missed target (retry), max retries,
  wait_for_target timeout, on_tick callback, retry count resets on new target
- start/stop lifecycle
- get_diagnostics
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def grace_mgr():
    return MagicMock()


@pytest.fixture
def svc(mock_hass, grace_mgr):
    service = CoverCommandService(
        hass=mock_hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )
    # This suite exercises the reconciliation/resend machinery, so it opts into
    # position matching. The product default is OFF (issue #591) — that default
    # is covered by test_reconcile_skips_resend_when_position_matching_disabled
    # here and by the runtime-config/schema/summary tests.
    service.enable_position_matching = True
    return service


def _ctx(**overrides) -> PositionContext:
    """Return a PositionContext with all gates passing by default."""
    defaults = {
        "auto_control": True,
        "manual_override": False,
        "sun_just_appeared": False,
        "min_change": 2,
        "time_threshold": 0,  # 0 = always passes
        "special_positions": [0, 100],
        "inverse_state": False,
        "force": False,
    }
    defaults.update(overrides)
    return PositionContext(**defaults)


def _patch_position(svc, value):
    """Patch _get_current_position on svc to return value."""
    svc._get_current_position = MagicMock(return_value=value)


def _patch_caps(position_supported=True):
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": position_supported,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        },
    )


def _patch_explicit_caps(caps):
    """Patch check_cover_features with a caller-supplied capability dict."""
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value=caps,
    )


def _patch_no_prior_command():
    """Patch get_last_updated to None so the time-delta gate always passes.

    The ``mock_hass`` fixture is a bare MagicMock, so an unpatched
    ``get_last_updated`` hands the gate a MagicMock it cannot subtract.
    """
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    )


# ------------------------------------------------------------------ #
# apply_position — gate checks
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_apply_skips_auto_control_off(svc):
    outcome, reason = await svc.apply_position(
        "cover.test", 50, "solar", context=_ctx(auto_control=False)
    )
    assert outcome == "skipped"
    assert reason == "auto_control_off"
    assert not svc.has_target("cover.test")


@pytest.mark.asyncio
async def test_apply_skips_delta_too_small(svc):
    # delta=4 (50→54) is outside the tolerance band (svc has position_tolerance=3,
    # so |50-54|=4 > 3) but still below min_change=5 → delta_too_small gate fires.
    _patch_position(svc, 50)
    outcome, reason = await svc.apply_position(
        "cover.test", 54, "solar", context=_ctx(min_change=5)
    )
    assert outcome == "skipped"
    assert reason == "delta_too_small"


@pytest.mark.asyncio
async def test_apply_skips_time_delta_too_small(svc):
    _patch_position(svc, 30)  # big position delta
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent,
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 60, "solar", context=_ctx(time_threshold=5)
        )
    assert outcome == "skipped"
    assert reason == "time_delta_too_small"


@pytest.mark.asyncio
async def test_apply_force_bypasses_time_delta_for_custom_position(svc, mock_hass):
    """Issue #348: force=True bypasses the time-delta gate for custom-position edge-triggers."""
    _patch_position(svc, 30)
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            60,
            "custom_position",
            context=_ctx(time_threshold=5, force=True, auto_control=True),
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_force_same_position_still_skipped_for_custom_position(
    svc, mock_hass
):
    """PR #300 invariant: force=True with same position is still skipped."""
    _patch_position(svc, 60)
    with _patch_caps():
        outcome, detail = await svc.apply_position(
            "cover.test",
            60,
            "custom_position",
            context=_ctx(force=True, auto_control=True),
        )
    assert outcome == "skipped"
    assert detail == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_apply_skips_manual_override(svc):
    _patch_position(svc, 30)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 60, "solar", context=_ctx(manual_override=True)
        )
    assert outcome == "skipped"
    assert reason == "manual_override"


@pytest.mark.asyncio
async def test_apply_sends_when_all_gates_pass(svc, mock_hass):
    _patch_position(svc, 30)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, _ = await svc.apply_position("cover.test", 60, "solar", context=_ctx())
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 60
    assert svc.is_waiting_for_target("cover.test") is True
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_force_bypasses_delta_and_manual_override_gates(svc, mock_hass):
    """force=True bypasses delta/time/manual_override but NOT auto_control (issue #293)."""
    # Use current=50 so the cover is genuinely far from target=0 (|50-0|=50 > tolerance=3)
    # confirming force bypasses delta/manual_override, not the same-position band.
    _patch_position(svc, 50)
    with _patch_caps():
        outcome, _ = await svc.apply_position(
            "cover.test",
            0,
            "sunset",
            context=_ctx(auto_control=True, manual_override=True, force=True),
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_force_does_NOT_bypass_auto_control(svc, mock_hass):
    """Issue #293: force=True alone must not bypass auto_control_off."""
    with _patch_caps():
        outcome, detail = await svc.apply_position(
            "cover.test",
            0,
            "sunset",
            context=_ctx(auto_control=False, manual_override=True, force=True),
        )
    assert outcome == "skipped"
    assert detail == "auto_control_off"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_apply_records_skip_action(svc):
    outcome, reason = await svc.apply_position(
        "cover.test", 50, "solar", context=_ctx(auto_control=False)
    )
    assert svc.last_skipped_action["entity_id"] == "cover.test"
    assert svc.last_skipped_action["reason"] == "auto_control_off"
    assert svc.last_skipped_action["calculated_position"] == 50
    assert svc.last_skipped_action["trigger"] == "solar"
    assert svc.last_skipped_action["inverse_state_applied"] is False


@pytest.mark.asyncio
async def test_apply_new_target_resets_retry_count(svc, mock_hass):
    """Sending a new target resets the reconciliation retry counter."""
    svc.state("cover.test").retry_count = 2
    _patch_position(svc, 30)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        await svc.apply_position("cover.test", 60, "solar", context=_ctx())
    assert svc.state("cover.test").retry_count == 0


# ------------------------------------------------------------------ #
# check_target_reached — tolerance-based clearance
# ------------------------------------------------------------------ #


def test_check_target_reached_within_tolerance(svc):
    """Clears wait_for_target when position is within tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    svc.state("cover.test").retry_count = 1

    reached = svc.check_target_reached("cover.test", 52)  # delta=2 <= 3

    assert reached is True
    assert svc.is_waiting_for_target("cover.test") is False
    assert svc.state("cover.test").retry_count == 0


def test_check_target_reached_outside_tolerance(svc):
    """Does NOT clear wait_for_target when outside tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)

    reached = svc.check_target_reached("cover.test", 54)  # delta=4 > 3

    assert reached is False
    assert svc.is_waiting_for_target("cover.test") is True


def test_check_target_reached_exact_match(svc):
    """Clears wait_for_target on exact match (delta=0)."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)

    assert svc.check_target_reached("cover.test", 50) is True
    assert svc.is_waiting_for_target("cover.test") is False


def test_check_target_reached_no_target(svc):
    """Returns False when no target has been set."""
    assert svc.check_target_reached("cover.test", 50) is False


def test_check_target_reached_none_position(svc):
    """Returns False when reported position is None."""
    svc.set_target("cover.test", 50)
    assert svc.check_target_reached("cover.test", None) is False


def test_check_target_reached_tolerance_boundary(svc):
    """At exactly tolerance boundary (delta==3), should clear."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    assert svc.check_target_reached("cover.test", 47) is True  # delta=3 == tolerance


@pytest.mark.asyncio
async def test_reconcile_no_dispatch_after_same_position_skip_records_target(
    svc, mock_hass
):
    """Issue #1158 / #187 guard: a same_position skip must record the target
    so reconciliation immediately sees this entity's target == actual and
    never resends.

    Before the fix, ``_skip()`` never called ``set_target()``, so an entity
    that landed on its computed position without ever being dispatched had
    ``PerEntityState.target`` stuck at ``None`` forever — it was simply
    absent from ``iter_targets()``, and this reconciliation pass would find
    nothing to do for the wrong reason (no target recorded, not "at
    target"). This test drives through ``apply_position``'s same_position
    branch first (mirroring #300's force+same-position invariant) so the
    target is genuinely recorded, then confirms reconciliation matches
    immediately and issues no resend.
    """
    _patch_position(svc, 60)
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test",
            60,
            "custom_position",
            context=_ctx(force=True, auto_control=True),
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 60

    mock_hass.services.async_call.reset_mock()
    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    # Strengthen beyond "no service call": prove reconciliation actually
    # reached step 7's match branch (target == actual), not merely that it
    # bailed out early for an unrelated reason. retry_count == 0 alone is
    # vacuous — that's its default value, never perturbed in this test.
    assert svc.get_diagnostics("cover.test")["at_target"] is True
    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_resends_booked_target_after_skip_then_drift(svc, mock_hass):
    """Issue #1158 MUST-FIX (round-3 audit): a same_position skip's booked
    target is a new dispatch path, not just a diagnostics label — it makes
    a never-dispatched entity reconciliation-eligible, and once eligible a
    later drift off that target resends it exactly like any other booked
    target would.

    Before the fix, ``_skip()`` never called ``set_target()``, so this
    entity had no recorded target at all and ``run_reconciliation_pass``
    had nothing to compare against — no resend, for the wrong reason (no
    target, not "at target"). Reproduces the auditor's
    ``audit5_drift.py``: book 63 via the same_position gate's direct-equality
    arm (arm 1a), drift the cover off that target without ever engaging
    manual override, then confirm reconciliation resends the booked value.
    """
    _patch_position(svc, 63)
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 63, "solar", context=_ctx()
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 63

    # Cover drifts away from the booked target — no manual override involved.
    _patch_position(svc, 40)
    mock_hass.services.async_call.reset_mock()

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    called_service = mock_hass.services.async_call.call_args[0][1]
    called_data = mock_hass.services.async_call.call_args[0][2]
    assert called_service == "set_cover_position"
    assert called_data["entity_id"] == "cover.test"
    assert called_data["position"] == 63


@pytest.mark.asyncio
async def test_same_position_skip_tracks_this_cycles_is_safety_verdict(svc, mock_hass):
    """Issues #1158 / #1165: a same_position skip records THIS cycle's safety
    verdict, and the verdict comes back down when the condition ends.

    ``PerEntityState.is_safety`` describes the BOOKED number — "is what ACP
    currently has this cover down for a safety-protected number?" Cycle 1
    books 50 and grants the flag in the same breath, so the grant is about the
    number this cycle routed to. Cycle 2 revokes it, and revoking is
    unconditional: it only removes a licence.

    What neither polarity may do is ride inside the ``(target,
    dispatch_token)`` value-change guard the booking uses. That is what #1165
    reports as the bug: the guard suppresses the write on exactly the cycles
    that re-confirm an unchanged target, so a since-cleared condition could
    never come back down. A frozen ``True`` then survives
    ``clear_non_safety_targets()`` and makes ``run_reconciliation_pass``
    resend with auto_control off or outside the time window — what steps 3/4
    exist to prevent, and what the tail of this test guards.
    """
    # Cycle 1: a weather-safety cycle skips because the cover is already at
    # the safety position (50) — nothing is dispatched, so
    # ``_prepare_service_call`` (the REAL-dispatch writer) never runs. The skip
    # books the 50 itself, and the grant that follows is about that booked
    # number.
    _patch_position(svc, 50)
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is True

    # Cycle 2: weather clears; an ordinary cycle re-confirms the same 50. The
    # value-change guard suppresses the BOOKING (unchanged value) — the verdict
    # is written anyway, so the flag comes back down. This is the #1165
    # assertion: it was vacuous while cycle 1 left the flag at its default.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.state("cover.test").is_safety is False

    # Window closes: clear_non_safety_targets() must sweep this target —
    # proof is_safety never latched True. Under the old bug this assertion
    # fails: the frozen is_safety=True would have protected it from the
    # sweep.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None

    # With auto_control off AND outside the time window, reconciliation must
    # not resend anything for this entity — exactly what steps 3/4 exist to
    # guarantee for a non-safety (or, as here, no longer recorded) target.
    svc._auto_control_enabled = False
    svc._in_time_window = False
    _patch_position(svc, 90)  # cover drifted / was moved since
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_same_position_skip_clears_is_safety_after_condition_ends(svc, mock_hass):
    """Issue #1165: a same_position skip must record THIS cycle's safety
    verdict, so a flag left ``True`` by an earlier REAL dispatch comes back
    down once the safety condition ends.

    ``PerEntityState.is_safety`` describes the booked number, and revoking it
    is unconditional — a revoke only removes a licence to act outside
    ``clear_non_safety_targets()`` and reconciliation steps 3/4. Here the
    booked 50 also happens to be this cycle's routed target, so the revoke is
    about the booked number either way;
    ``..._revokes_is_safety_on_a_foreign_stale_target`` covers the case where
    it is not, and the revoke has to fire anyway.

    The skip branch used to leave the flag untouched, so a weather dispatch's
    ``True`` rode forward onto every later cycle that re-confirmed the same
    target — and the #1115 value-change guard made that permanent, because an
    unchanged target suppresses ``set_target`` entirely. A frozen ``True``
    then defeats ``clear_non_safety_targets()`` and lets
    ``run_reconciliation_pass`` steps 3/4 resend the target with automatic
    control off and outside the time window.
    """
    # Cycle 1: weather safety drives the cover from 90 to 50 — a REAL dispatch,
    # so ``_prepare_service_call`` genuinely stamps is_safety=True.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True)
        )
    assert outcome == "sent"
    assert svc.state("cover.test").is_safety is True
    assert svc.get_target("cover.test") == 50

    # The cover arrives and settles on the safety position.
    _patch_position(svc, 50)
    svc.set_waiting("cover.test", False)

    # Cycle 2: weather clears. An ordinary solar cycle re-confirms the SAME 50
    # through the same_position skip. The booked value does not change, so the
    # value-change guard suppresses set_target — but the safety verdict for
    # this cycle is False and must be recorded regardless.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.state("cover.test").is_safety is False

    # Window closes: the target is no longer safety-protected, so the sweep
    # must take it.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None

    # And with automatic control off AND outside the time window, nothing is
    # resent for this entity — what steps 3/4 exist to guarantee.
    svc._auto_control_enabled = False
    svc._in_time_window = False
    _patch_position(svc, 90)  # cover drifted / was moved since
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_same_position_skip_endpoint_tolerance_still_revokes_is_safety(
    svc, mock_hass
):
    """Issue #1165: the safety verdict is revoked even on the one same_position
    sub-arm that deliberately books nothing.

    Revoking is unconditional — it only removes a licence — so it cannot ride
    inside the booking guard. Arm 1's endpoint-tolerance sub-arm proves the
    point: a cover resting at 98 against a 100 target skips, and #1158's
    narrowed fix withholds the booking there because the routed target (100)
    does not match the actual (98). The revoke still has to be written.

    The entity's booked 100 does happen to equal this cycle's routed target
    here, so a grant would also have been permitted — but that is incidental.
    ``..._does_not_protect_a_foreign_stale_target`` is the grant-side case on
    this same sub-arm, where the booked number differs and the grant is
    correctly refused, and
    ``..._revokes_is_safety_on_a_foreign_stale_target`` is the revoke under
    the same mismatch.
    """
    # Cycle 1: a weather-safety dispatch to the 100 endpoint. Real dispatch →
    # is_safety=True, target booked at the routed endpoint.
    _patch_position(svc, 50)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 100, "weather", context=_ctx(is_safety=True)
        )
    assert outcome == "sent"
    assert svc.state("cover.test").is_safety is True
    assert svc.get_target("cover.test") == 100

    # The motor settles two percent short — inside position_tolerance=3, but
    # not on the endpoint.
    _patch_position(svc, 98)
    svc.set_waiting("cover.test", False)

    # Cycle 2: weather clears. The endpoint-tolerance sub-arm skips and does
    # NOT book (target/actual would mismatch, #1158) — but the verdict for
    # this cycle is False.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "solar", context=_ctx(is_safety=False)
        )

    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 100  # unbooked-guard still holds
    assert svc.state("cover.test").is_safety is False


@pytest.mark.asyncio
async def test_same_position_skip_does_not_protect_a_foreign_stale_target(
    svc, mock_hass
):
    """Issue #1165 (round-2 audit): GRANTING the verdict describes the BOOKED
    target, so a skip that withholds its booking must not stamp ``is_safety``
    onto a stale number some earlier, non-safety decision left behind.

    The withheld-booking sub-arm (#1158) is the one place where the entity can
    still be holding a target that has nothing to do with the value this cycle
    routed to. Granting there anyway inverts #1165's own defect: a ``True``
    that protects and re-drives a number the safety handler never asked for.
    ``clear_non_safety_targets()`` then leaves the stale target in place, and
    steps 3/4 resend it with automatic control off and outside the time window.

    Only the grant is gated this way. The mirror-image test
    ``..._revokes_is_safety_on_a_foreign_stale_target`` pins the other
    polarity: a revoke on the same foreign-target shape fires unconditionally,
    because it only takes a licence away.
    """
    # Cycle 1: an ordinary solar dispatch books 60. No safety involved.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 60, "solar", context=_ctx(is_safety=False)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 60
    assert svc.state("cover.test").is_safety is False

    # The cover never lands on 60 — it comes to rest at 2 (landing error, or
    # someone moved it), and the booking is still 60.
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 2)

    # Cycle 2: weather safety wants the 0 endpoint. ``_current=2`` is inside
    # position_tolerance of 0, so the endpoint-tolerance sub-arm skips — and
    # #1158 withholds the booking because 2 != the routed target 0. The entity
    # therefore still holds 60, a target no safety decision produced.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 0, "weather", context=_ctx(is_safety=True)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 60  # unbooked-guard still holds

    # The safety verdict was about 0, not about 60. The booked 60 must stay
    # unprotected.
    assert svc.state("cover.test").is_safety is False

    # Window closes: 60 is an ordinary stale target, so the sweep takes it.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None

    # And with automatic control off AND outside the time window, nothing is
    # driven to 60 — the resend steps 3/4 exist to prevent.
    svc._auto_control_enabled = False
    svc._in_time_window = False
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_same_position_skip_revokes_is_safety_on_a_foreign_stale_target(
    svc, mock_hass
):
    """Issue #1165 (round-2 audit): REVOKING the safety verdict is never
    gated on the booked target — only granting it is.

    The two polarities are not symmetrical. Granting ``True`` hands out a
    licence to act outside ``clear_non_safety_targets()`` and reconciliation
    steps 3/4, so it has to be about the number actually booked. Revoking only
    takes that licence away, which is safe on any number.

    Gating both directions leaves #1165 reachable and, in this configuration,
    permanent: a safety cycle books 60, the cover never arrives (position
    matching is off by default, so nothing resends it) and comes to rest at 2,
    and from then on every cycle computes the 0 endpoint. Each of those cycles
    lands in arm 1's endpoint-tolerance sub-arm, where #1158 withholds the
    booking because 2 != 0 — so the entity keeps holding the foreign 60 and a
    symmetric gate withholds the revoke forever. Nothing in the loop can
    correct it, which is why this test runs the cycle twice.
    """
    # Cycle 1: weather safety books 60 with a REAL dispatch, so
    # ``_prepare_service_call`` genuinely stamps is_safety=True.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 60, "weather", context=_ctx(is_safety=True)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 60
    assert svc.state("cover.test").is_safety is True

    # The cover never reaches 60. It comes to rest at 2 and stays there —
    # enable_position_matching is off on a real install, so no resend chases it.
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 2)

    # Weather clears. Every later cycle computes 0 (sunset, climate-close, a
    # north window). Run it twice: the state is a fixed point, so a single
    # pass would not distinguish "revoked" from "about to self-correct".
    for cycle in (1, 2):
        with _patch_caps():
            outcome, reason = await svc.apply_position(
                "cover.test", 0, "solar", context=_ctx(is_safety=False)
            )
        assert (outcome, reason) == ("skipped", "same_position"), cycle
        # #1158 still withholds the booking: 2 is not the routed 0.
        assert svc.get_target("cover.test") == 60, cycle
        # ...but the licence is gone. This is the assertion #1165 is about.
        assert svc.state("cover.test").is_safety is False, cycle

    # Window closes: 60 is an ordinary abandoned target, so the sweep takes it.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None

    # And with automatic control off AND outside the time window, nothing is
    # driven to the abandoned 60 — what steps 3/4 exist to guarantee.
    svc._auto_control_enabled = False
    svc._in_time_window = False
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_same_position_skip_does_not_protect_an_unbooked_entity(svc, mock_hass):
    """Issue #1165 (round-2 audit): a skip that books nothing on an entity
    with nothing booked must not leave ``is_safety`` True.

    The verdict describes the booked target, so with no booked target there is
    no subject for it and ``False`` is the honest answer. Granting ``True``
    would be inert against both readers today — each no-ops on ``target is
    None`` — but five ``set_target`` sites book without touching the verdict
    (``restore_target``, ``send_my_position``, the two coordinator external-
    stop paths, and the venetian carriage rebase), so the next booking through
    any of them would silently inherit the licence. The rebase is reachable in
    this very cycle: ``_service_secondary_axis`` runs after the verdict write.
    """
    # A fresh entity: nothing booked, verdict at its default.
    assert svc.get_target("cover.test") is None
    assert svc.state("cover.test").is_safety is False

    # A weather-safety cycle wants the 0 endpoint and the cover already sits
    # at 2 — inside position_tolerance=3. Arm 1's endpoint-tolerance sub-arm
    # skips, and #1158 withholds the booking because 2 != 0.
    _patch_position(svc, 2)
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 0, "weather", context=_ctx(is_safety=True)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") is None

    # No booked number, so no licence to hand out.
    assert svc.state("cover.test").is_safety is False

    # A later booking through one of the verdict-blind ``set_target`` sites
    # must therefore start unprotected, not inherit a licence. ``restore_target``
    # only seeds a target the cover is already resting on, so the cover has to
    # move to 60 first — otherwise it returns False, books nothing, and the
    # three assertions below have no subject to be about.
    _patch_position(svc, 60)
    assert svc.restore_target("cover.test", 60) is True
    assert svc.state("cover.test").is_safety is False
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None


@pytest.mark.asyncio
async def test_same_position_skip_regrants_is_safety_when_the_condition_returns(
    svc, mock_hass
):
    """Issue #1165: the GRANT polarity does not ride inside the booking guard
    either — a returning safety condition re-protects an unchanged target.

    The revoke tests all leave the flag ``False``, so on their own they would
    stay green under a rule that simply never granted from a skip. This pins
    the other direction on the one shape where the ``(target,
    dispatch_token)`` value-change guard suppresses ``set_target`` entirely:
    the booked 50 never changes across all three cycles, so every grant and
    revoke here has to come from outside that guard.
    """
    # Cycle 1: weather safety drives 90 -> 50. Real dispatch, flag True.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is True

    _patch_position(svc, 50)
    svc.set_waiting("cover.test", False)

    # Cycle 2: weather clears, solar re-confirms the same 50 -> revoked.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.state("cover.test").is_safety is False

    # Cycle 3: the weather condition returns on the SAME 50. Nothing is
    # dispatched and nothing is re-booked — the grant has to happen anyway.
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is True

    # The licence is real: the window-close sweep leaves the target alone.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") == 50


@pytest.mark.asyncio
async def test_delta_skip_revokes_is_safety_after_condition_ends(svc, mock_hass):
    """Issue #1165, same rule at the ``delta_too_small`` skip.

    ``same_position`` is not the only gate that can hold a booked safety target
    in place indefinitely. Once weather clears, a cover parked within
    ``min_change`` of every subsequent calculated position skips on delta
    forever — no dispatch, so ``_prepare_service_call`` never runs, so nothing
    revokes the verdict a genuine earlier safety dispatch stamped. The booked
    50 stays licensed to survive ``clear_non_safety_targets()`` and to be
    re-driven by reconciliation steps 3/4 with automatic control off.

    Unlike the ``same_position`` case this one self-corrects the moment the
    calculated value moves outside ``min_change`` — it is not a fixed point —
    but "eventually, if the sun cooperates" is not a lifetime.
    """
    # Cycle 1: weather safety drives 90 -> 50. A REAL dispatch, so
    # ``_prepare_service_call`` genuinely stamps is_safety=True.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(force=True, is_safety=True)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is True

    # The cover arrives and settles.
    _patch_position(svc, 50)
    svc.set_waiting("cover.test", False)

    # Cycle 2: weather clears. Solar wants 51 — one percent away, under
    # min_change, so the delta gate skips. Nothing is dispatched and nothing is
    # booked, but the verdict for THIS cycle is False.
    with _patch_caps(), _patch_no_prior_command():
        outcome, reason = await svc.apply_position(
            "cover.test", 51, "solar", context=_ctx(min_change=5, is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "delta_too_small")
    assert svc.state("cover.test").is_safety is False
    # The skip books nothing — the 50 the safety dispatch put there is still
    # the booked number, and it is now unprotected.
    assert svc.get_target("cover.test") == 50

    # Window closes: the sweep must take it.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None

    # And with automatic control off AND outside the time window, nothing is
    # resent for this entity.
    svc._auto_control_enabled = False
    svc._in_time_window = False
    _patch_position(svc, 90)
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_time_delta_skip_revokes_is_safety_after_condition_ends(svc, mock_hass):
    """Issue #1165, same rule at the ``time_delta_too_small`` skip.

    The sibling of the ``delta_too_small`` case: here the position delta is
    large enough to act on but the rate limiter holds the command back, so
    again nothing dispatches and again the stale verdict would ride forward.
    Both gates are hysteresis on the carriage, neither is a safety decision,
    and neither may leave the previous decision's licence standing.
    """
    # Cycle 1: weather safety drives 90 -> 50 with the delta/time gates
    # bypassed, exactly as a safety context does in production.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(force=True, is_safety=True)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is True

    _patch_position(svc, 50)
    svc.set_waiting("cover.test", False)

    # Cycle 2: weather clears. Solar wants 90 — a big move, so the position
    # delta passes — but the last command was ten seconds ago and the
    # rate limiter is five minutes.
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 90, "solar", context=_ctx(time_threshold=5, is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "time_delta_too_small")
    assert svc.state("cover.test").is_safety is False
    assert svc.get_target("cover.test") == 50

    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None


@pytest.mark.asyncio
async def test_delta_skip_does_not_protect_a_target_it_did_not_route_to(svc, mock_hass):
    """Issue #1165: the GRANT half stays attached to the booked number here too.

    The delta gates book nothing, so ``get_target`` is whatever an earlier
    decision left. A safety cycle that skips on delta must not therefore
    license that older number: the licence has to attach to the number the
    safety decision actually routed to.

    In production this shape is currently unreachable — every
    ``PositionContext`` producer that sets ``is_safety=True`` also sets
    ``force=True``, and the delta gates run only under ``not force`` — which is
    exactly why it is pinned here. The rule lives in one helper shared with the
    ``same_position`` site rather than being re-decided per gate, so if that
    coupling is ever relaxed the grant does not silently start protecting
    stale numbers.
    """
    # Solar books 50 through a real dispatch — no safety flag.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(is_safety=False)
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 50
    assert svc.state("cover.test").is_safety is False

    # The cover misses and comes to rest at 47; nothing chases it.
    _patch_position(svc, 47)
    svc.set_waiting("cover.test", False)

    # A weather-safety cycle wants 49 and skips on delta. 49 is not the booked
    # 50, and nothing was sent, so the booked 50 must NOT become protected.
    with _patch_caps(), _patch_no_prior_command():
        outcome, reason = await svc.apply_position(
            "cover.test", 49, "weather", context=_ctx(min_change=5, is_safety=True)
        )
    assert (outcome, reason) == ("skipped", "delta_too_small")
    assert svc.state("cover.test").is_safety is False
    assert svc.get_target("cover.test") == 50

    # So the window-close sweep still takes it.
    svc.clear_non_safety_targets()
    assert svc.get_target("cover.test") is None


@pytest.mark.asyncio
async def test_reconcile_resend_preserves_is_safety_flag(svc, mock_hass):
    """Issue #1134 defect 2: a reconciliation resend RESTATES the safety
    verdict; it must not clear the very flag that authorised it.

    ``_execute_command`` funnels into ``_prepare_service_call``, which writes
    ``is_safety`` unconditionally. Omitting the keyword let it default to
    ``False``, so a safety target un-protected itself on its own first resend
    — the second pass then hit steps 3/4 and skipped, with automatic control
    off and outside the time window, exactly where a safety target is supposed
    to keep being resent.
    """
    # A weather-safety dispatch to 50, from 90.
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True)
        )
    assert outcome == "sent"
    assert svc.state("cover.test").is_safety is True

    # The cover never got there. Automatic control is off and we are outside
    # the time window — only the safety flag keeps this target eligible.
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 90)
    svc._auto_control_enabled = False
    svc._in_time_window = False

    now = dt.datetime.now(dt.UTC)

    # Pass 1: resent because is_safety is True.
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(now)

    mock_hass.services.async_call.assert_called_once()
    assert mock_hass.services.async_call.call_args[0][1] == "set_cover_position"
    assert mock_hass.services.async_call.call_args[0][2]["position"] == 50
    assert svc.state("cover.test").is_safety is True

    # Pass 2: still a safety target, so it is resent again (max_retries=3).
    svc.set_waiting("cover.test", False)
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(now)

    mock_hass.services.async_call.assert_called_once()
    assert mock_hass.services.async_call.call_args[0][1] == "set_cover_position"


@pytest.mark.asyncio
async def test_reconcile_resend_of_my_target_reroutes_through_stop_cover(
    svc, mock_hass
):
    """Issue #1134 defect 1: a resend of a My-preset target must go back out
    as ``stop_cover``, and must not silently rebook the user's My value as 0.

    ``_execute_command`` let ``use_my_position`` default to ``False``, so
    ``route_service_call`` fell past the My branch to the open/close threshold:
    a My of 10 is below the 50 threshold, so the resend sent ``close_cover``
    and slammed the cover shut — and ``_prepare_service_call`` then booked the
    branch's ``routed_target`` of 0 over the user's 10.

    The RTS/ZVIDAR shape makes this reachable: the cover reports HA state
    "open" forever, so ``get_open_close_state`` maps it to 100 and the pass
    always judges the 10 target missed.
    """
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }
    mock_hass.states.get.return_value = MagicMock(state="open", attributes={})
    _patch_position(svc, None)

    with _patch_explicit_caps(caps), _patch_no_prior_command():
        outcome, reason = await svc.apply_position(
            "cover.rts",
            10,
            "solar",
            context=_ctx(use_my_position=True, special_positions=[0, 100, 10]),
        )
    assert (outcome, reason) == ("sent", "stop_cover")
    assert svc.get_target("cover.rts") == 10

    # The cover parks at its My preset but keeps reporting "open" → 100, so
    # reconciliation always sees the target as missed.
    svc.set_waiting("cover.rts", False)
    _patch_position(svc, 100)
    mock_hass.services.async_call.reset_mock()

    with _patch_explicit_caps(caps):
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    assert mock_hass.services.async_call.call_args[0][1] == "stop_cover"
    assert svc.get_target("cover.rts") == 10


# ------------------------------------------------------------------ #
# _reconcile — cover reached target
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reconcile_no_action_when_at_target(svc, mock_hass):
    """Reconciliation does nothing when cover is within tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 51)  # delta=1, within tolerance=3

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 0


@pytest.fixture
def svc_tol6(mock_hass, grace_mgr):
    """CoverCommandService with a widened reconciliation tolerance (issue #507)."""
    service = CoverCommandService(
        hass=mock_hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=6,
        max_retries=3,
    )
    # See the `svc` fixture: this suite opts into matching to exercise resends.
    service.enable_position_matching = True
    return service


@pytest.mark.asyncio
async def test_reconcile_no_resend_within_configured_tolerance(svc_tol6, mock_hass):
    """A configured tolerance of 6 treats 95-vs-100 as arrived → no resend (issue #507)."""
    svc_tol6.set_target("cover.test", 100)
    svc_tol6.set_waiting("cover.test", False)
    _patch_position(svc_tol6, 95)  # delta=5 ≤ tolerance=6

    with _patch_caps():
        await svc_tol6.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    assert svc_tol6.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_default_tolerance_still_resends_at_95(svc, mock_hass):
    """Default tolerance (3) still resends 95-vs-100 — preserves today's behavior."""
    svc.set_target("cover.test", 100)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 95)  # delta=5 > tolerance=3

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_retries_when_cover_missed_target(svc, mock_hass):
    """Reconciliation resends command when cover is outside tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 42)  # delta=8 > tolerance=3

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_skips_resend_when_position_matching_disabled(svc, mock_hass):
    """Matching off (the default) → a mismatch never resends the command (issue #591)."""
    svc.enable_position_matching = False  # the product default
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 42)  # delta=8 > tolerance=3 — would resend if matching on

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 0  # never entered the retry path


@pytest.mark.asyncio
async def test_reconcile_resends_when_position_matching_enabled(svc, mock_hass):
    """Matching on (opt-in) → resends on a mismatch until the cover arrives (issue #591)."""
    svc.enable_position_matching = True
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 42)  # delta=8 > tolerance=3

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_stops_at_max_retries(svc, mock_hass):
    """Reconciliation gives up after max_retries and logs warning."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    svc.state("cover.test").retry_count = 3  # Already at max (max_retries=3)
    _patch_position(svc, 40)  # Still off target

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # No additional service call — already at max
    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 3  # Not incremented


@pytest.mark.asyncio
async def test_reconcile_skips_while_wait_for_target_active(svc, mock_hass):
    """Reconciliation skips entity while cover is still expected to be moving."""
    now = dt.datetime.now(dt.UTC)
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    svc.state("cover.test").sent_at = now  # Just sent — within 30s timeout

    await svc.run_reconciliation_pass(now)

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_clears_wait_for_target_after_timeout(svc, mock_hass):
    """Reconciliation force-clears wait_for_target after configured timeout (default 45s)."""
    now = dt.datetime.now(dt.UTC)
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    svc.state("cover.test").sent_at = now - dt.timedelta(
        seconds=50
    )  # Expired (> 45s default)
    _patch_position(svc, 50)  # At target after timeout

    await svc.run_reconciliation_pass(now)

    # wait_for_target should be cleared, no retry needed (at target)
    assert svc.is_waiting_for_target("cover.test") is False
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_retries_after_wait_for_target_timeout(svc, mock_hass):
    """After wait_for_target timeout, reconcile retries if still off target."""
    now = dt.datetime.now(dt.UTC)
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    svc.state("cover.test").sent_at = now - dt.timedelta(
        seconds=50
    )  # Expired (> 45s default)
    _patch_position(svc, 40)  # Off target

    with _patch_caps():
        await svc.run_reconciliation_pass(now)

    # Command was sent, so wait_for_target is True again (set by _prepare_service_call)
    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_skips_when_position_unavailable(svc, mock_hass):
    """Reconciliation skips entity when position cannot be read."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, None)

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_resets_retry_count_on_target_reached(svc):
    """Reconciliation resets retry count when cover reaches target."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    svc.state("cover.test").retry_count = 2
    _patch_position(svc, 50)  # At target

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_calls_on_tick_callback(svc):
    """Reconciliation calls the on_tick callback at the start of each tick."""
    on_tick = AsyncMock()
    svc._on_tick = on_tick
    now = dt.datetime.now(dt.UTC)

    await svc.run_reconciliation_pass(now)

    on_tick.assert_called_once_with(now)


@pytest.mark.asyncio
async def test_reconcile_multiple_entities(svc, mock_hass):
    """Reconciliation handles multiple entities independently."""
    svc.set_target("cover.blind", 50)
    svc.set_target("cover.awning", 70)
    svc.set_waiting("cover.blind", False)
    svc.set_waiting("cover.awning", False)

    # blind: at target; awning: missed
    def fake_position(entity):
        return 50 if entity == "cover.blind" else 60

    svc._get_current_position = MagicMock(side_effect=fake_position)

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Only awning should have been retried
    assert mock_hass.services.async_call.call_count == 1
    called_data = mock_hass.services.async_call.call_args[0][2]
    assert (
        called_data[list(called_data.keys())[0]] == "cover.awning"
        or called_data.get("entity_id") == "cover.awning"
    )


# ------------------------------------------------------------------ #
# start / stop lifecycle
# ------------------------------------------------------------------ #


def test_start_registers_timer(svc, mock_hass):
    """start() registers the async_track_time_interval listener."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.async_track_time_interval",
        return_value=MagicMock(),
    ) as mock_track:
        svc.start()
        mock_track.assert_called_once()
        assert svc._reconcile_unsub is not None


def test_start_is_idempotent(svc, mock_hass):
    """start() called twice does not register a second timer."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.async_track_time_interval",
        return_value=MagicMock(),
    ) as mock_track:
        svc.start()
        svc.start()
        mock_track.assert_called_once()


def test_stop_cancels_timer(svc, mock_hass):
    """stop() calls the unsubscribe function and clears the handle."""
    unsub = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.async_track_time_interval",
        return_value=unsub,
    ):
        svc.start()
        svc.stop()

    unsub.assert_called_once()
    assert svc._reconcile_unsub is None


def test_stop_when_not_started_is_safe(svc):
    """stop() when timer not started does not raise."""
    svc.stop()  # Should not raise


# ------------------------------------------------------------------ #
# get_diagnostics
# ------------------------------------------------------------------ #


def test_get_diagnostics_at_target(svc):
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 51)  # within tolerance=3

    diag = svc.get_diagnostics("cover.test")

    assert diag["target"] == 50
    assert diag["actual"] == 51
    assert diag["at_target"] is True
    assert diag["retry_count"] == 0
    assert diag["wait_for_target"] is False


def test_get_diagnostics_off_target(svc):
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", True)
    svc.state("cover.test").retry_count = 2
    _patch_position(svc, 40)  # outside tolerance=3

    diag = svc.get_diagnostics("cover.test")

    assert diag["at_target"] is False
    assert diag["retry_count"] == 2
    assert diag["wait_for_target"] is True


def test_get_diagnostics_no_target(svc):
    _patch_position(svc, 50)
    diag = svc.get_diagnostics("cover.test")

    assert diag["target"] is None
    assert diag["actual"] == 50
    assert diag["at_target"] is False


def test_get_diagnostics_includes_reconcile_time(svc):
    now = dt.datetime.now(dt.UTC)
    svc.set_target("cover.test", 50)
    svc.state("cover.test").last_reconcile_at = now
    _patch_position(svc, 50)

    diag = svc.get_diagnostics("cover.test")
    assert diag["last_reconcile_time"] == now.isoformat()


# ------------------------------------------------------------------ #
# _reconcile — manual override skip (issue #116)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reconcile_skips_entity_in_manual_override(svc, mock_hass):
    """Reconciliation must NOT resend the old target when cover is in manual override.

    Regression test for issue #116: user manually moves cover but it snaps
    back because reconciliation fights the manual position.
    """
    svc.set_target("cover.blind", 85)  # integration last sent 85%
    svc.set_waiting("cover.blind", False)
    _patch_position(svc, 50)  # user moved it to 50%

    # Coordinator marks this entity as manually overridden
    svc.manual_override_entities = {"cover.blind"}

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Must NOT resend — cover should stay where the user put it
    mock_hass.services.async_call.assert_not_called()
    # retry count must not be incremented
    assert svc.state("cover.blind").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_resumes_after_manual_override_cleared(svc, mock_hass):
    """Once manual override clears, reconciliation should resume protecting target."""
    svc.set_target("cover.blind", 85)
    svc.set_waiting("cover.blind", False)
    _patch_position(svc, 50)

    # Override active — should skip
    svc.manual_override_entities = {"cover.blind"}
    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Override cleared — should now retry
    svc.manual_override_entities = set()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_only_skips_manual_entity_not_others(svc, mock_hass):
    """Reconciliation skips the manually-overridden entity but still retries others."""
    svc.set_target("cover.blind", 85)  # manually moved — should skip
    svc.set_target("cover.awning", 70)  # auto-controlled — should retry
    svc.set_waiting("cover.blind", False)
    svc.set_waiting("cover.awning", False)

    def fake_position(entity):
        return 50  # both off target

    svc._get_current_position = MagicMock(side_effect=fake_position)
    svc.manual_override_entities = {"cover.blind"}

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Exactly one call — only for cover.awning
    assert mock_hass.services.async_call.call_count == 1
    called_data = mock_hass.services.async_call.call_args[0][2]
    assert called_data.get("entity_id") == "cover.awning"


@pytest.mark.asyncio
async def test_reconcile_safety_override_still_protected(svc, mock_hass):
    """Safety handlers (force override) use apply_position(force=True) which
    overwrites target_call — reconciliation then protects that new safety target
    even if the entity is still in the manual override set (edge case: safety
    fires while manual override is active).
    """
    # Safety handler fired: target_call updated to safety position (100%)
    svc.set_target("cover.blind", 100)
    svc.set_waiting("cover.blind", False)
    _patch_position(svc, 50)  # Cover still moving toward safety position

    # Manual override set still contains the entity (coordinator syncs next cycle)
    svc.manual_override_entities = {"cover.blind"}

    # Because the entity is in manual_override_entities, reconciliation will
    # skip it this tick — the safety position will have been sent already by
    # apply_position(force=True), so this is acceptable; the test documents
    # that we rely on apply_position(force=True) for immediate safety, not
    # the reconciliation retry for the safety-override case.
    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_manual_override_entities_property_getter_and_setter(svc):
    """manual_override_entities property read/write round-trips correctly."""
    assert svc.manual_override_entities == set()

    svc.manual_override_entities = {"cover.blind", "cover.awning"}
    assert svc.manual_override_entities == {"cover.blind", "cover.awning"}

    # Setting to empty clears it
    svc.manual_override_entities = set()
    assert svc.manual_override_entities == set()


@pytest.mark.asyncio
async def test_reconcile_with_force_override_sensor_scenario(svc, mock_hass):
    """Regression: issue #116 — cover with force override sensor configured
    (but inactive) snaps back after manual move.

    The force override sensor generates extra state-change events for its
    coordinator, causing more frequent update cycles.  Reconciliation was
    fighting the user's manual position on every cycle.
    """
    # Integration last sent default position (85%) — target_call is set
    svc.set_target("cover.balcony", 85)
    svc.set_waiting("cover.balcony", False)
    # wait_for_target is False — cover reached 85% and stopped

    # User manually closes cover to 50%
    _patch_position(svc, 50)

    # Coordinator detects manual override and syncs to CoverCommandService
    svc.manual_override_entities = {"cover.balcony"}

    # Force override sensor fires a state-change (door attribute update, etc.)
    # → coordinator runs update cycle → reconciliation tick fires
    for _ in range(3):  # max_retries = 3; should never fire even once
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Cover must NOT have been moved back — user's 50% position preserved
    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.balcony").retry_count == 0


# ------------------------------------------------------------------ #
# is_safety flag controls safety target classification;
# force flag is independent (bypasses gates only);
# _reconcile skips non-safety targets when auto_control is off
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_safety_apply_marks_safety_target(svc, mock_hass):
    """apply_position(is_safety=True) adds entity to _safety_targets."""
    _patch_position(svc, 30)
    with _patch_caps():
        await svc.apply_position(
            "cover.test", 0, "force_override", context=_ctx(force=True, is_safety=True)
        )
    assert svc.is_safety_target("cover.test")


@pytest.mark.asyncio
async def test_force_without_is_safety_does_not_mark_safety_target(svc, mock_hass):
    """apply_position(force=True, is_safety=False) does NOT add entity to _safety_targets.

    force=True only bypasses gate checks (delta, time, manual override).
    Safety target classification is controlled exclusively by is_safety.
    Callers like _async_force_send_pipeline_position use force=True to bypass
    gates but is_safety=False so the target does not persist across window
    boundaries (fix for issue #223).
    """
    _patch_position(svc, 30)
    with _patch_caps():
        await svc.apply_position(
            "cover.test",
            0,
            "manual_override_cleared",
            context=_ctx(force=True, is_safety=False),
        )
    assert not svc.is_safety_target("cover.test")


@pytest.mark.asyncio
async def test_non_safety_apply_removes_from_safety_targets(svc, mock_hass):
    """apply_position(is_safety=False) removes entity from _safety_targets.

    When a safety override clears and normal solar tracking resumes, the
    entity should no longer be treated as a safety target.
    """
    # First set it as safety
    svc.state("cover.test").is_safety = True

    _patch_position(svc, 30)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        await svc.apply_position("cover.test", 60, "solar", context=_ctx(force=False))
    assert not svc.is_safety_target("cover.test")


@pytest.mark.asyncio
async def test_reconcile_skips_non_safety_when_auto_control_off(svc, mock_hass):
    """Reconciliation skips normal targets when automatic control is disabled.

    Regression: after the user turned off Automatic Control a later
    reconciliation tick was still resending the old solar position.
    """
    svc.set_target("cover.test", 60)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 40)  # Off target — would normally trigger retry

    # Mark as non-safety (normal solar target)
    svc.state("cover.test").is_safety = False
    # Automatic control turned off
    svc.auto_control_enabled = False

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Must NOT resend — automatic control is off
    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_still_resends_safety_target_when_auto_control_off(
    svc, mock_hass
):
    """Safety targets (force override, weather) are resent even when auto control is off.

    The whole point of safety overrides is that they work regardless of whether
    automatic control is enabled.
    """
    svc.set_target("cover.test", 0)  # Force override retracted to 0%
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 50)  # Cover hasn't reached safety position yet

    # Mark as safety target (sent via is_safety=True)
    svc.state("cover.test").is_safety = True
    # Automatic control is off
    svc.auto_control_enabled = False

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # MUST resend — safety target even though auto control is off
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_resumes_when_auto_control_re_enabled(svc, mock_hass):
    """Turning automatic control back on resumes reconciliation for normal targets."""
    svc.set_target("cover.test", 60)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 40)  # Off target
    svc.state("cover.test").is_safety = False

    # Control off: should skip
    svc.auto_control_enabled = False
    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Control back on: should retry
    svc.auto_control_enabled = True
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_skips_non_safety_outside_time_window(svc, mock_hass):
    """Reconciliation skips normal targets when outside the operational time window.

    Regression for #179: covers were being commanded at midnight by reconciliation
    resending stale daytime targets after the time window had closed.
    """
    svc.set_target("cover.test", 60)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 40)  # Off target — would normally trigger retry

    # Normal solar target (not safety)
    svc.state("cover.test").is_safety = False
    # Time window closed
    svc.in_time_window = False

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # Must NOT resend — outside time window
    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_resends_safety_target_outside_time_window(svc, mock_hass):
    """Safety targets are resent even outside the operational time window.

    Force override and weather safety commands must work at any hour.
    """
    svc.set_target("cover.test", 0)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 50)  # Cover hasn't reached safety position yet

    # Mark as safety target (sent via is_safety=True)
    svc.state("cover.test").is_safety = True
    # Time window is closed
    svc.in_time_window = False

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    # MUST resend — safety target regardless of time window
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_resumes_when_time_window_reopens(svc, mock_hass):
    """Reconciliation resumes normal targets when the time window reopens."""
    svc.set_target("cover.test", 60)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 40)
    svc.state("cover.test").is_safety = False

    # Window closed: should skip
    svc.in_time_window = False
    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Window re-opened: should retry
    svc.in_time_window = True
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_called_once()


def test_clear_non_safety_targets(svc):
    """clear_non_safety_targets removes only non-safety entries.

    Safety targets (force override, weather, end_time_default) must survive
    so reconciliation can still drive covers to their safe position after window close.
    """
    svc.set_target("cover.solar", 60)
    svc.set_waiting("cover.solar", True)
    svc.state("cover.solar").retry_count = 2
    svc.state("cover.solar").gave_up = True

    svc.set_target("cover.safety", 0)
    svc.set_waiting("cover.safety", False)
    svc.state("cover.safety").retry_count = 1
    svc.state("cover.safety").is_safety = True

    svc.clear_non_safety_targets()

    # Non-safety entry fully removed
    assert not svc.has_target("cover.solar")
    assert not svc.is_waiting_for_target("cover.solar")
    assert svc.state("cover.solar").retry_count == 0
    assert not svc.state("cover.solar").gave_up

    # Safety entry preserved
    assert svc.get_target("cover.safety") == 0
    assert svc.is_waiting_for_target("cover.safety") is False
    assert svc.state("cover.safety").retry_count == 1
    assert svc.is_safety_target("cover.safety")


# ------------------------------------------------------------------ #
# _reconcile — in-transit guard (issue #418)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reconcile_skips_while_cover_opening(svc, mock_hass):
    """Reconciliation must not resend a target while the cover reports state=opening.

    Regression for issue #418: the reconcile pass did not honour the
    in-transit guard that apply_position and manual_override already
    respected. A cover that just received a command and is actively
    opening would be incorrectly retried before it reached its target.
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=20)
    svc._event_buffer = buf

    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 30)  # Off target — would normally trigger retry

    # Cover is actively moving toward the target
    state_obj = MagicMock()
    state_obj.state = "opening"
    mock_hass.states.get.return_value = state_obj

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    event_names = [e["event"] for e in buf.snapshot()]
    assert "reconcile_skipped_in_transit" in event_names


@pytest.mark.asyncio
async def test_reconcile_skips_while_cover_closing(svc, mock_hass):
    """Reconciliation must not resend a target while the cover reports state=closing."""
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    buf = EventBuffer(maxlen=20)
    svc._event_buffer = buf

    svc.set_target("cover.test", 10)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 70)  # Off target — would normally trigger retry

    # Cover is actively closing toward the target
    state_obj = MagicMock()
    state_obj.state = "closing"
    mock_hass.states.get.return_value = state_obj

    await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    event_names = [e["event"] for e in buf.snapshot()]
    assert "reconcile_skipped_in_transit" in event_names


@pytest.mark.asyncio
async def test_reconcile_retries_stationary_off_target(svc, mock_hass):
    """Reconciliation does retry a stationary cover that is off target.

    Regression guard: the in-transit guard must not block retries for covers
    that have stopped but not reached their target (state=stopped or similar).
    """
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 30)  # Off target

    # Cover is stationary — not in transit
    state_obj = MagicMock()
    state_obj.state = "stopped"
    mock_hass.states.get.return_value = state_obj

    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_force_apply_bypasses_time_delta_gate(svc, mock_hass):
    """force=True must bypass time_delta_too_small so safety commands always get sent.

    Regression: force_override and weather_override commands were being blocked
    by the time_delta gate even though force=True should skip all gates.
    """
    import datetime as _dt

    # Use current=50 so cover is far from target=0 (|50-0|=50 > tolerance=3)
    # ensuring the same-position band doesn't interfere.
    _patch_position(svc, 50)
    recent = _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=30)  # 0.5 min ago
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        # time_threshold=5 min but force=True — must NOT be blocked
        outcome, detail = await svc.apply_position(
            "cover.test",
            0,
            "force_override",
            context=_ctx(time_threshold=5, force=True),
        )
    assert outcome == "sent", f"Expected sent, got skipped: {detail}"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_force_apply_bypasses_position_delta_gate(svc, mock_hass):
    """force=True must bypass delta_too_small so safety commands always get sent.

    Uses current=64, target=60 (delta=4): outside tolerance=3 but below
    min_change=5, confirming force bypasses the delta gate (not the band).
    """
    _patch_position(
        svc, 64
    )  # delta=4 to target=60 → outside tolerance=3, below min_change=5
    with _patch_caps():
        outcome, detail = await svc.apply_position(
            "cover.test",
            60,
            "force_override",
            context=_ctx(min_change=5, force=True),
        )
    assert outcome == "sent", f"Expected sent, got skipped: {detail}"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_auto_control_enabled_property_defaults_true(svc):
    """auto_control_enabled defaults to True (backward compatible)."""
    assert svc.auto_control_enabled is True


@pytest.mark.asyncio
async def test_auto_control_enabled_setter(svc):
    """auto_control_enabled setter round-trips correctly."""
    svc.auto_control_enabled = False
    assert svc.auto_control_enabled is False
    svc.auto_control_enabled = True
    assert svc.auto_control_enabled is True


@pytest.mark.asyncio
async def test_safety_target_cleared_on_open_close_non_force(svc, mock_hass):
    """Non-force apply on open/close-only covers also clears safety target."""
    svc.state("cover.test").is_safety = True

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value={
                "has_set_position": False,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
            },
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        await svc.apply_position("cover.test", 80, "solar", context=_ctx(force=False))
    assert not svc.is_safety_target("cover.test")


@pytest.mark.asyncio
async def test_safety_target_set_on_open_close_force(svc, mock_hass):
    """Safety apply on open/close-only covers marks entity as safety target."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        },
    ):
        await svc.apply_position(
            "cover.test", 0, "force_override", context=_ctx(force=True, is_safety=True)
        )
    assert svc.is_safety_target("cover.test")


# ------------------------------------------------------------------ #
# Step 39: Special position bypasses delta check
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_special_position_target_bypasses_delta(svc, mock_hass):
    """Moving TO a special position (0, 100, default, sunset) bypasses delta check.

    Scenario: cover at 96%, target=100% (special).  delta=4 is outside the
    tolerance band (svc has position_tolerance=3, |96-100|=4 > 3) but below
    min_change=5; the special-position bypass lets the command through.
    """
    _patch_position(svc, 96)  # delta=4 → outside tolerance=3, below min_change=5
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            100,  # ← special position, bypasses delta
            "solar",
            context=_ctx(min_change=5, special_positions=[0, 100, 50]),
        )
    assert outcome == "sent"
    assert svc.get_target("cover.test") == 100


@pytest.mark.asyncio
async def test_special_position_current_bypasses_delta(svc, mock_hass):
    """Moving FROM a special position also bypasses the delta check.

    Cover is at 0% (special), target is 4% — delta=4 is outside tolerance=3
    (svc has position_tolerance=3) but below min_change=5.  Because current
    position (0%) is special, the check is bypassed.
    """
    _patch_position(svc, 0)  # current is special
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            4,  # delta=4 outside tolerance=3, below min_change=5, FROM special
            "solar",
            context=_ctx(min_change=5, special_positions=[0, 100, 50]),
        )
    assert outcome == "sent"


@pytest.mark.asyncio
async def test_non_special_small_delta_is_blocked(svc, mock_hass):
    """Without a special position, a small delta IS blocked by min_change.

    Control: verify that without the special bypass, a small delta fails.
    Uses delta=4 (55→59) which is outside tolerance=3 (svc has position_tolerance=3)
    but below min_change=5, so the delta gate fires.
    """
    _patch_position(svc, 55)  # delta=4 to 59 → outside tolerance=3, below min_change=5
    outcome, reason = await svc.apply_position(
        "cover.test",
        59,  # delta=4 < min_change=5, |55-59|=4 > tolerance=3
        "solar",
        context=_ctx(min_change=5, special_positions=[]),  # no specials
    )
    assert outcome == "skipped"
    assert reason == "delta_too_small"


# ------------------------------------------------------------------ #
# Step 40: Same position short-circuits before special bypass
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_same_position_skips_even_for_special_target(svc, mock_hass):
    """Cover already at target → NO command even if target is a special position.

    The same-position short-circuit runs BEFORE the special-positions bypass.
    Regression: without this guard, covers at 0%/100% would receive a command
    every time_threshold minutes because the special-bypass would always fire.
    Since issue #290, the skip reason is "same_position" (caught by the top-level
    guard in apply_position that applies even to force=True callers).
    """
    _patch_position(svc, 100)  # cover is already at 100%
    outcome, reason = await svc.apply_position(
        "cover.test",
        100,  # same as current → short-circuit fires
        "solar",
        context=_ctx(min_change=1, special_positions=[0, 100, 50]),
    )
    assert outcome == "skipped"
    assert reason == "same_position"


@pytest.mark.asyncio
async def test_same_position_skips_for_zero_special(svc, mock_hass):
    """Cover at 0% targeting 0% is short-circuited (no command sent)."""
    _patch_position(svc, 0)
    outcome, reason = await svc.apply_position(
        "cover.test",
        0,
        "solar",
        context=_ctx(min_change=1, special_positions=[0, 100]),
    )
    assert outcome == "skipped"
    assert reason == "same_position"


@pytest.mark.asyncio
async def test_sun_just_appeared_sends_despite_same_position(svc, mock_hass):
    """sun_just_appeared=True sends command even when cover is already at target.

    When the sun enters the FOV for the first time, we re-confirm the cover
    position even if it hasn't changed, to ensure the cover is tracking.
    This overrides the same-position short-circuit.
    """
    _patch_position(svc, 65)  # same as target
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            65,  # same as current position
            "solar",
            context=_ctx(
                min_change=1,
                special_positions=[0, 100, 50],
                sun_just_appeared=True,  # ← bypasses same-position check
            ),
        )
    assert outcome == "sent"


# ------------------------------------------------------------------ #
# Step 43: sun_just_appeared re-confirms position
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_sun_just_appeared_bypasses_delta(svc, mock_hass):
    """sun_just_appeared=True bypasses the delta check (small change allowed)."""
    _patch_position(svc, 50)  # delta=1 < min_change=5
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            51,  # delta=1 < min_change=5 — normally blocked
            "solar",
            context=_ctx(
                min_change=5,
                special_positions=[0, 100],
                sun_just_appeared=True,  # ← bypasses delta
            ),
        )
    assert outcome == "sent"


@pytest.mark.asyncio
async def test_sun_just_appeared_false_enforces_delta(svc, mock_hass):
    """With sun_just_appeared=False, small delta is still blocked.

    Uses delta=4 (50→54) which is outside tolerance=3 (svc has position_tolerance=3)
    but below min_change=5, confirming the delta gate enforces the threshold.
    """
    _patch_position(svc, 50)  # delta=4 to 54 → outside tolerance=3, below min_change=5
    outcome, reason = await svc.apply_position(
        "cover.test",
        54,
        "solar",
        context=_ctx(
            min_change=5,
            special_positions=[0, 100],
            sun_just_appeared=False,  # ← delta enforced
        ),
    )
    assert outcome == "skipped"
    assert reason == "delta_too_small"


@pytest.mark.asyncio
async def test_sun_just_appeared_no_resend_at_mechanical_stop(svc, mock_hass):
    """Issue #985: sun_just_appeared must NOT re-fire an endpoint command the
    cover already occupies (state=closed, target=0). Re-sending close_cover there
    is a no-op on single-axis covers and disturbs a venetian's slats. The tilt
    axis is still serviced via the same-position skip branch.
    """
    _patch_position(svc, 0)  # carriage already at 0
    state = MagicMock()
    state.state = "closed"  # STATE_CLOSED -> _is_at_mechanical_stop True
    mock_hass.states.get.return_value = state
    svc._service_secondary_axis = AsyncMock()  # spy: tilt must still get a turn
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "solar",
            context=_ctx(
                min_change=1,
                special_positions=[0, 100, 50],
                sun_just_appeared=True,
            ),
        )
    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()
    svc._service_secondary_axis.assert_awaited_once()


# ------------------------------------------------------------------ #
# Force override release — end-to-end gate behavior (#177)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_force_override_release_force_true_bypasses_time_delta(svc, mock_hass):
    """force=True (set on force override release) bypasses the time delta gate.

    Scenario: force override moved the cover 5 minutes ago (within the 10-min
    threshold).  Without fix, solar tracking would be blocked.  With fix the
    coordinator passes force=True, allowing the return to calculated position.
    """
    _patch_position(svc, 30)  # large enough position delta
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            60,
            "force_override_cleared",
            context=_ctx(
                time_threshold=10, force=True
            ),  # force=True set by coordinator
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_solar_tracking_blocked_by_recent_force_override_move(svc):
    """Without force=True, time delta gate blocks return after force override move.

    This documents the bug that issue #177 fixed: a recent cover move (caused
    by force override) would block the subsequent solar-tracking command.
    """
    _patch_position(svc, 30)
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent,
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            60,
            "solar",
            context=_ctx(
                time_threshold=10, force=False
            ),  # force=False — pre-fix behavior
        )
    assert outcome == "skipped"
    assert reason == "time_delta_too_small"


@pytest.mark.asyncio
async def test_solar_tracking_passes_when_time_elapsed(svc, mock_hass):
    """Solar tracking is allowed once the time threshold has elapsed."""
    _patch_position(svc, 30)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=15)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=old,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            60,
            "solar",
            context=_ctx(time_threshold=10, force=False),
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_force_true_bypasses_time_delta_and_position_delta(svc, mock_hass):
    """force=True bypasses both time delta and position delta simultaneously.

    Verifies that no single gate can block a force=True command, which is
    required for force override release, manual override expiry, and safety
    handlers to work reliably.

    Uses current=56, target=60 (delta=4): outside tolerance=3 (svc has
    position_tolerance=3) but below min_change=5, so both time and position
    delta gates would block without force=True.
    """
    _patch_position(
        svc, 56
    )  # delta=4 to target=60 → outside tolerance=3, below min_change=5
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=30)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            60,
            "force_override_cleared",
            context=_ctx(min_change=5, time_threshold=10, force=True),
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_manual_override_expiry_force_true_bypasses_time_delta(svc, mock_hass):
    """Manual override expiry (force=True) also bypasses time delta.

    Manual override expiry already uses force=True (_async_force_send_pipeline_position).
    This test confirms the gate behavior is identical to force override release.
    """
    _patch_position(svc, 30)
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=3)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=recent,
        ),
    ):
        outcome, _ = await svc.apply_position(
            "cover.test",
            70,
            "manual_override_cleared",
            context=_ctx(time_threshold=10, force=True),
        )
    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


# ------------------------------------------------------------------ #
# dry-run mode
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_apply_dry_run_skips_service_call(svc, mock_hass):
    """Dry-run suppresses async_call, returns ('skipped', 'dry_run'), populates diagnostics."""
    svc.dry_run = True
    _patch_position(svc, 30)
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=None,
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 60, "solar", context=_ctx()
        )

    assert outcome == "skipped"
    assert reason == "dry_run"
    mock_hass.services.async_call.assert_not_called()
    # last_cover_action still populated with the intended action
    assert svc.last_cover_action["entity_id"] == "cover.test"
    assert svc.last_cover_action["dry_run"] is True
    # last_skipped_action records the dry_run reason
    assert svc.last_skipped_action["reason"] == "dry_run"
    assert svc.last_skipped_action["entity_id"] == "cover.test"


@pytest.mark.asyncio
async def test_dry_run_still_honors_earlier_gates(svc, mock_hass):
    """When delta is too small AND dry-run is on, delta gate fires before dry-run.

    Uses delta=4 (50→54) which is outside tolerance=3 (svc has position_tolerance=3)
    but below min_change=5, confirming the delta gate still fires before the dry-run
    skip when the position change is genuinely below the threshold.
    """
    svc.dry_run = True
    _patch_position(svc, 50)
    outcome, reason = await svc.apply_position(
        "cover.test", 54, "solar", context=_ctx(min_change=5)
    )
    assert outcome == "skipped"
    assert reason == "delta_too_small"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_execute_command_dry_run_no_send(svc, mock_hass):
    """_execute_command with dry_run=True logs but does not call async_call."""
    svc.dry_run = True
    with _patch_caps():
        await svc._execute_command("cover.test", 70)
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_stop_in_flight_dry_run_no_send(svc, mock_hass):
    """stop_in_flight with dry_run=True skips async_call but still clears wait_for_target."""
    svc.dry_run = True
    svc.set_waiting("cover.test", True)
    state_obj = MagicMock()
    state_obj.state = "opening"
    mock_hass.states.get.return_value = state_obj
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={"has_stop": True},
    ):
        stopped = await svc.stop_in_flight()
    mock_hass.services.async_call.assert_not_called()
    assert "cover.test" in stopped
    assert svc.is_waiting_for_target("cover.test") is False


@pytest.mark.asyncio
async def test_stop_all_dry_run_no_send(svc, mock_hass):
    """stop_all with dry_run=True skips async_call but still reports stopped entities."""
    svc.dry_run = True
    state_obj = MagicMock()
    state_obj.state = "closing"
    mock_hass.states.get.return_value = state_obj
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={"has_stop": True},
    ):
        stopped = await svc.stop_all(["cover.test"])
    mock_hass.services.async_call.assert_not_called()
    assert "cover.test" in stopped


# ------------------------------------------------------------------ #
# Outside-window constraint licence (issue #943 item B)
#
# A SEPARATE per-entity flag from ``is_safety`` — sharing that one would
# reopen #1165 (a stale safety verdict surviving clear_non_safety_targets and
# being re-driven with automatic control off). This one buys exactly one
# thing: reconciliation step 4 may resend the target it licensed while the
# user's clock window is closed.
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_flagged_target_resent_outside_window(svc, mock_hass):
    """A licensed target keeps being chased overnight."""
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test",
            50,
            "constraint",
            context=_ctx(force=True, outside_window_constraint=True),
        )
    assert outcome == "sent"
    assert svc.state("cover.test").outside_window_constraint is True
    assert svc.state("cover.test").is_safety is False

    svc._in_time_window = False
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 90)
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_called_once()
    # A resend RESTATES the record; it must not un-licence its own target.
    assert svc.state("cover.test").outside_window_constraint is True


@pytest.mark.asyncio
async def test_unflagged_target_still_skipped_outside_window(svc, mock_hass):
    """Positive control: without the licence step 4 still blocks the resend."""
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        outcome, _ = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(force=True)
        )
    assert outcome == "sent"
    assert svc.state("cover.test").outside_window_constraint is False

    svc._in_time_window = False
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 90)
    mock_hass.services.async_call.reset_mock()
    with _patch_caps():
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_outside_window_flag_not_written_by_safety_writers(svc):
    """``is_safety``'s writers keep their own lifetime and never touch this one.

    The two flags have separate writers and separate sweepers on purpose
    (#1226/#1165). A safety dispatch, and the skip-path verdict recorder that
    follows it, must leave the outside-window licence exactly as they found it.
    """
    _patch_position(svc, 90)
    with _patch_caps(), _patch_no_prior_command():
        await svc.apply_position(
            "cover.test", 50, "weather", context=_ctx(is_safety=True, force=True)
        )
    assert svc.state("cover.test").is_safety is True
    assert svc.state("cover.test").outside_window_constraint is False

    # A skip cycle records a safety verdict through ``_record_safety_verdict``;
    # that writer owns ``is_safety`` alone.
    svc.state("cover.test").outside_window_constraint = True
    _patch_position(svc, 50)
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 50, "solar", context=_ctx(is_safety=False)
        )
    assert (outcome, reason) == ("skipped", "same_position")
    assert svc.state("cover.test").is_safety is False
    assert svc.state("cover.test").outside_window_constraint is True


@pytest.mark.asyncio
async def test_clear_outside_window_targets_spares_safety_targets(svc):
    """The sweeper revokes only non-safety licences, target and flag together."""
    svc.set_target("cover.licensed", 40)
    svc.state("cover.licensed").outside_window_constraint = True
    svc.set_target("cover.safety", 60)
    svc.state("cover.safety").is_safety = True
    svc.state("cover.safety").outside_window_constraint = True
    svc.set_target("cover.plain", 70)

    svc.clear_outside_window_targets()

    assert svc.get_target("cover.licensed") is None
    assert svc.state("cover.licensed").outside_window_constraint is False
    # Safety rows answer to clear_non_safety_targets and to is_safety's own
    # lifetime — this sweeper must not reach into either.
    assert svc.get_target("cover.safety") == 60
    assert svc.state("cover.safety").outside_window_constraint is True
    # An unlicensed target is none of this sweeper's business.
    assert svc.get_target("cover.plain") == 70


@pytest.mark.asyncio
async def test_outside_window_licence_is_not_restored_after_a_reload(svc):
    """Rehydration books the number without the licence (#943 item B).

    The licence is a live-cycle verdict, not persisted state: first-refresh
    admission re-establishes it from a freshly evaluated result, and inheriting
    it across a restart would let a stale number move a cover at 03:00 with no
    pipeline behind it.
    """
    _patch_position(svc, 40)
    svc.restore_target("cover.test", 40)
    assert svc.get_target("cover.test") == 40
    assert svc.state("cover.test").outside_window_constraint is False
