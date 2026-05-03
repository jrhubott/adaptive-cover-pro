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
    return CoverCommandService(
        hass=mock_hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


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
    _patch_position(svc, 50)
    outcome, reason = await svc.apply_position(
        "cover.test", 51, "solar", context=_ctx(min_change=5)
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


# ------------------------------------------------------------------ #
# _reconcile — cover reached target
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reconcile_no_action_when_at_target(svc, mock_hass):
    """Reconciliation does nothing when cover is within tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 51)  # delta=1, within tolerance=3

    await svc._reconcile(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_retries_when_cover_missed_target(svc, mock_hass):
    """Reconciliation resends command when cover is outside tolerance."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, 42)  # delta=8 > tolerance=3

    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))

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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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

    await svc._reconcile(now)

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

    await svc._reconcile(now)

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
        await svc._reconcile(now)

    # Command was sent, so wait_for_target is True again (set by _prepare_service_call)
    mock_hass.services.async_call.assert_called_once()
    assert svc.state("cover.test").retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_skips_when_position_unavailable(svc, mock_hass):
    """Reconciliation skips entity when position cannot be read."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    _patch_position(svc, None)

    await svc._reconcile(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_resets_retry_count_on_target_reached(svc):
    """Reconciliation resets retry count when cover reaches target."""
    svc.set_target("cover.test", 50)
    svc.set_waiting("cover.test", False)
    svc.state("cover.test").retry_count = 2
    _patch_position(svc, 50)  # At target

    await svc._reconcile(dt.datetime.now(dt.UTC))

    assert svc.state("cover.test").retry_count == 0


@pytest.mark.asyncio
async def test_reconcile_calls_on_tick_callback(svc):
    """Reconciliation calls the on_tick callback at the start of each tick."""
    on_tick = AsyncMock()
    svc._on_tick = on_tick
    now = dt.datetime.now(dt.UTC)

    await svc._reconcile(now)

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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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

    await svc._reconcile(dt.datetime.now(dt.UTC))

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
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Override cleared — should now retry
    svc.manual_override_entities = set()
    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))
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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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
    await svc._reconcile(dt.datetime.now(dt.UTC))
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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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
    Callers like _async_send_after_override_clear use force=True to bypass
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

    await svc._reconcile(dt.datetime.now(dt.UTC))

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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Control back on: should retry
    svc.auto_control_enabled = True
    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))
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

    await svc._reconcile(dt.datetime.now(dt.UTC))

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
        await svc._reconcile(dt.datetime.now(dt.UTC))

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
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Window re-opened: should retry
    svc.in_time_window = True
    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))
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


@pytest.mark.asyncio
async def test_force_apply_bypasses_time_delta_gate(svc, mock_hass):
    """force=True must bypass time_delta_too_small so safety commands always get sent.

    Regression: force_override and weather_override commands were being blocked
    by the time_delta gate even though force=True should skip all gates.
    """
    import datetime as _dt

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
    """force=True must bypass delta_too_small so safety commands always get sent."""
    _patch_position(svc, 61)  # Would be delta=1 which is < min_change=5
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

    Scenario: cover is at 55%, min_change=10, target is 0% (special).
    Normally delta=55 >= 10 would pass, but even if delta were small,
    special positions bypass it.  This test uses a scenario where delta
    would be blocked if not for the special bypass: cover at 99%, target=100%.
    """
    _patch_position(svc, 99)  # delta=1, below min_change=5
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

    Cover is at 0% (special), target is 2% — delta=2 < min_change=5.
    Because current position (0%) is special, the check is bypassed.
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
            2,  # small delta=2, but moving FROM special position
            "solar",
            context=_ctx(min_change=5, special_positions=[0, 100, 50]),
        )
    assert outcome == "sent"


@pytest.mark.asyncio
async def test_non_special_small_delta_is_blocked(svc, mock_hass):
    """Without a special position, a small delta IS blocked by min_change.

    Control: verify that without the special bypass, a small delta fails.
    """
    _patch_position(svc, 55)  # delta=2 < min_change=5, no special positions
    outcome, reason = await svc.apply_position(
        "cover.test",
        57,  # delta=2 < min_change=5
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
    """With sun_just_appeared=False, small delta is still blocked."""
    _patch_position(svc, 50)  # delta=1 < min_change=5
    outcome, reason = await svc.apply_position(
        "cover.test",
        51,
        "solar",
        context=_ctx(
            min_change=5,
            special_positions=[0, 100],
            sun_just_appeared=False,  # ← delta enforced
        ),
    )
    assert outcome == "skipped"
    assert reason == "delta_too_small"


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
    """
    _patch_position(svc, 59)  # delta=1, below min_change=5
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

    Manual override expiry already uses force=True (_async_send_after_override_clear).
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
    """When delta is too small AND dry-run is on, delta gate fires before dry-run."""
    svc.dry_run = True
    _patch_position(svc, 50)
    outcome, reason = await svc.apply_position(
        "cover.test", 51, "solar", context=_ctx(min_change=5)
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
