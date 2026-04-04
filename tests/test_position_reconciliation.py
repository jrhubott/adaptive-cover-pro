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
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def grace_mgr():
    return MagicMock()


@pytest.fixture
def svc(hass, grace_mgr):
    return CoverCommandService(
        hass=hass,
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
    defaults = dict(
        auto_control=True,
        in_time_window=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=2,
        time_threshold=0,  # 0 = always passes
        special_positions=[0, 100],
        inverse_state=False,
        force=False,
    )
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
    assert "cover.test" not in svc.target_call


@pytest.mark.asyncio
async def test_apply_skips_outside_time_window(svc):
    outcome, reason = await svc.apply_position(
        "cover.test", 50, "solar", context=_ctx(in_time_window=False)
    )
    assert outcome == "skipped"
    assert reason == "outside_time_window"


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
async def test_apply_sends_when_all_gates_pass(svc, hass):
    _patch_position(svc, 30)
    with _patch_caps(), patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        outcome, _ = await svc.apply_position(
            "cover.test", 60, "solar", context=_ctx()
        )
    assert outcome == "sent"
    assert svc.target_call["cover.test"] == 60
    assert svc.wait_for_target["cover.test"] is True
    hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_force_bypasses_all_gates(svc, hass):
    """force=True sends command even when all gates would fail."""
    with _patch_caps():
        outcome, _ = await svc.apply_position(
            "cover.test",
            0,
            "sunset",
            context=_ctx(auto_control=False, in_time_window=False, manual_override=True, force=True),
        )
    assert outcome == "sent"
    hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_records_skip_action(svc):
    outcome, reason = await svc.apply_position(
        "cover.test", 50, "solar", context=_ctx(auto_control=False)
    )
    assert svc.last_skipped_action["entity_id"] == "cover.test"
    assert svc.last_skipped_action["reason"] == "auto_control_off"
    assert svc.last_skipped_action["calculated_position"] == 50


@pytest.mark.asyncio
async def test_apply_new_target_resets_retry_count(svc, hass):
    """Sending a new target resets the reconciliation retry counter."""
    svc._retry_counts["cover.test"] = 2
    _patch_position(svc, 30)
    with _patch_caps(), patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        await svc.apply_position("cover.test", 60, "solar", context=_ctx())
    assert svc._retry_counts.get("cover.test", 0) == 0


# ------------------------------------------------------------------ #
# check_target_reached — tolerance-based clearance
# ------------------------------------------------------------------ #


def test_check_target_reached_within_tolerance(svc):
    """Clears wait_for_target when position is within tolerance."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    svc._retry_counts["cover.test"] = 1

    reached = svc.check_target_reached("cover.test", 52)  # delta=2 <= 3

    assert reached is True
    assert svc.wait_for_target["cover.test"] is False
    assert "cover.test" not in svc._retry_counts


def test_check_target_reached_outside_tolerance(svc):
    """Does NOT clear wait_for_target when outside tolerance."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True

    reached = svc.check_target_reached("cover.test", 54)  # delta=4 > 3

    assert reached is False
    assert svc.wait_for_target["cover.test"] is True


def test_check_target_reached_exact_match(svc):
    """Clears wait_for_target on exact match (delta=0)."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True

    assert svc.check_target_reached("cover.test", 50) is True
    assert svc.wait_for_target["cover.test"] is False


def test_check_target_reached_no_target(svc):
    """Returns False when no target has been set."""
    assert svc.check_target_reached("cover.test", 50) is False


def test_check_target_reached_none_position(svc):
    """Returns False when reported position is None."""
    svc.target_call["cover.test"] = 50
    assert svc.check_target_reached("cover.test", None) is False


def test_check_target_reached_tolerance_boundary(svc):
    """At exactly tolerance boundary (delta==3), should clear."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    assert svc.check_target_reached("cover.test", 47) is True  # delta=3 == tolerance


# ------------------------------------------------------------------ #
# _reconcile — cover reached target
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reconcile_no_action_when_at_target(svc, hass):
    """Reconciliation does nothing when cover is within tolerance."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    _patch_position(svc, 51)  # delta=1, within tolerance=3

    await svc._reconcile(dt.datetime.now(dt.UTC))

    hass.services.async_call.assert_not_called()
    assert svc._retry_counts.get("cover.test", 0) == 0


@pytest.mark.asyncio
async def test_reconcile_retries_when_cover_missed_target(svc, hass):
    """Reconciliation resends command when cover is outside tolerance."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    _patch_position(svc, 42)  # delta=8 > tolerance=3

    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))

    hass.services.async_call.assert_called_once()
    assert svc._retry_counts["cover.test"] == 1


@pytest.mark.asyncio
async def test_reconcile_stops_at_max_retries(svc, hass):
    """Reconciliation gives up after max_retries and logs warning."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    svc._retry_counts["cover.test"] = 3  # Already at max (max_retries=3)
    _patch_position(svc, 40)  # Still off target

    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))

    # No additional service call — already at max
    hass.services.async_call.assert_not_called()
    assert svc._retry_counts["cover.test"] == 3  # Not incremented


@pytest.mark.asyncio
async def test_reconcile_skips_while_wait_for_target_active(svc, hass):
    """Reconciliation skips entity while cover is still expected to be moving."""
    now = dt.datetime.now(dt.UTC)
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    svc._sent_at["cover.test"] = now  # Just sent — within 30s timeout

    await svc._reconcile(now)

    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_clears_wait_for_target_after_timeout(svc, hass):
    """Reconciliation force-clears wait_for_target after 30s timeout."""
    now = dt.datetime.now(dt.UTC)
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    svc._sent_at["cover.test"] = now - dt.timedelta(seconds=35)  # Expired
    _patch_position(svc, 50)  # At target after timeout

    await svc._reconcile(now)

    # wait_for_target should be cleared, no retry needed (at target)
    assert svc.wait_for_target["cover.test"] is False
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_retries_after_wait_for_target_timeout(svc, hass):
    """After wait_for_target timeout, reconcile retries if still off target."""
    now = dt.datetime.now(dt.UTC)
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    svc._sent_at["cover.test"] = now - dt.timedelta(seconds=35)  # Expired
    _patch_position(svc, 40)  # Off target

    with _patch_caps():
        await svc._reconcile(now)

    # Command was sent, so wait_for_target is True again (set by _prepare_service_call)
    hass.services.async_call.assert_called_once()
    assert svc._retry_counts["cover.test"] == 1


@pytest.mark.asyncio
async def test_reconcile_skips_when_position_unavailable(svc, hass):
    """Reconciliation skips entity when position cannot be read."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    _patch_position(svc, None)

    await svc._reconcile(dt.datetime.now(dt.UTC))

    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_resets_retry_count_on_target_reached(svc):
    """Reconciliation resets retry count when cover reaches target."""
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    svc._retry_counts["cover.test"] = 2
    _patch_position(svc, 50)  # At target

    await svc._reconcile(dt.datetime.now(dt.UTC))

    assert "cover.test" not in svc._retry_counts


@pytest.mark.asyncio
async def test_reconcile_calls_on_tick_callback(svc):
    """Reconciliation calls the on_tick callback at the start of each tick."""
    on_tick = AsyncMock()
    svc._on_tick = on_tick
    now = dt.datetime.now(dt.UTC)

    await svc._reconcile(now)

    on_tick.assert_called_once_with(now)


@pytest.mark.asyncio
async def test_reconcile_multiple_entities(svc, hass):
    """Reconciliation handles multiple entities independently."""
    svc.target_call["cover.blind"] = 50
    svc.target_call["cover.awning"] = 70
    svc.wait_for_target["cover.blind"] = False
    svc.wait_for_target["cover.awning"] = False

    # blind: at target; awning: missed
    def fake_position(entity):
        return 50 if entity == "cover.blind" else 60

    svc._get_current_position = MagicMock(side_effect=fake_position)

    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))

    # Only awning should have been retried
    assert hass.services.async_call.call_count == 1
    called_data = hass.services.async_call.call_args[0][2]
    assert called_data[list(called_data.keys())[0]] == "cover.awning" or \
        called_data.get("entity_id") == "cover.awning"


# ------------------------------------------------------------------ #
# start / stop lifecycle
# ------------------------------------------------------------------ #


def test_start_registers_timer(svc, hass):
    """start() registers the async_track_time_interval listener."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.async_track_time_interval",
        return_value=MagicMock(),
    ) as mock_track:
        svc.start()
        mock_track.assert_called_once()
        assert svc._reconcile_unsub is not None


def test_start_is_idempotent(svc, hass):
    """start() called twice does not register a second timer."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.async_track_time_interval",
        return_value=MagicMock(),
    ) as mock_track:
        svc.start()
        svc.start()
        mock_track.assert_called_once()


def test_stop_cancels_timer(svc, hass):
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
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = False
    _patch_position(svc, 51)  # within tolerance=3

    diag = svc.get_diagnostics("cover.test")

    assert diag["target"] == 50
    assert diag["actual"] == 51
    assert diag["at_target"] is True
    assert diag["retry_count"] == 0
    assert diag["wait_for_target"] is False


def test_get_diagnostics_off_target(svc):
    svc.target_call["cover.test"] = 50
    svc.wait_for_target["cover.test"] = True
    svc._retry_counts["cover.test"] = 2
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
    svc.target_call["cover.test"] = 50
    svc._last_reconcile_time["cover.test"] = now
    _patch_position(svc, 50)

    diag = svc.get_diagnostics("cover.test")
    assert diag["last_reconcile_time"] == now.isoformat()
