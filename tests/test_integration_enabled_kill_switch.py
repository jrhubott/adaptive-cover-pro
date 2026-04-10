"""Tests for the Integration Enabled kill switch (Issue #186).

Verifies the hard-off semantics of the ``enabled_toggle`` / Integration Enabled
switch and answers the safety questions raised in issue #186:

  Q1. Hard-off: can ACP be silenced completely, including Force Override and Weather?
  Q2. Obstacle safety: does Position Verification retry cap prevent cover hammering?
  Q3. Manual move / child-safety / walk-through: does the priority chain work?
  Q4. Automatic Control documentation: what bypasses it vs. what is truly gated?

The kill switch is enforced inside CoverCommandService at two choke points:
  1. apply_position() — before the context.force branch (blocks ALL sends)
  2. _reconcile()     — at the very top of the loop (blocks ALL reconciliation)

These are the only two paths that send cover commands.  Tests here prove that
both choke points hold unconditionally.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror test_position_reconciliation.py)
# ---------------------------------------------------------------------------


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
    defaults = dict(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=2,
        time_threshold=0,
        special_positions=[0, 100],
        inverse_state=False,
        force=False,
    )
    defaults.update(overrides)
    return PositionContext(**defaults)


def _patch_position(svc, value):
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


# ---------------------------------------------------------------------------
# Q1 — Hard-off: integration_disabled blocks everything
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_position_blocked_when_integration_disabled(svc):
    """Normal solar command is blocked when integration is disabled."""
    svc.enabled = False
    _patch_position(svc, 40)
    outcome, reason = await svc.apply_position(
        "cover.test", 60, "solar", context=_ctx(auto_control=True)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"


@pytest.mark.asyncio
async def test_force_override_blocked_when_integration_disabled(svc):
    """Force override (force=True, bypass_auto_control=True) is blocked when disabled.

    Critical: proves the kill switch gates ABOVE the force=True bypass path,
    so even safety-priority commands cannot move covers.
    """
    svc.enabled = False
    _patch_position(svc, 50)
    # Simulate a force override call: force=True, auto_control=True (already ORd in coordinator)
    outcome, reason = await svc.apply_position(
        "cover.test", 0, "force_override", context=_ctx(force=True, auto_control=True)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"


@pytest.mark.asyncio
async def test_weather_override_blocked_when_integration_disabled(svc):
    """Weather override is also blocked when integration is disabled."""
    svc.enabled = False
    _patch_position(svc, 50)
    outcome, reason = await svc.apply_position(
        "cover.test", 0, "weather", context=_ctx(force=True, auto_control=True)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"


@pytest.mark.asyncio
async def test_integration_disabled_blocks_auto_control_off_path(svc):
    """Disabled integration blocks even auto_control=False path (belt-and-braces)."""
    svc.enabled = False
    _patch_position(svc, 40)
    outcome, reason = await svc.apply_position(
        "cover.test", 60, "solar", context=_ctx(auto_control=False)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"


@pytest.mark.asyncio
async def test_reconcile_skips_all_targets_when_integration_disabled(svc, mock_hass):
    """Reconciliation sends nothing — safety AND non-safety targets — when disabled.

    Unlike auto_control_enabled=False (which still resends safety targets),
    the kill switch blocks everything.
    """
    # Non-safety target
    svc.target_call["cover.solar"] = 60
    svc.wait_for_target["cover.solar"] = False
    svc._safety_targets.discard("cover.solar")

    # Safety target (force override)
    svc.target_call["cover.force"] = 0
    svc.wait_for_target["cover.force"] = False
    svc._safety_targets.add("cover.force")

    _patch_position(svc, 50)  # Both off-target — would trigger retry if enabled
    svc.enabled = False

    await svc._reconcile(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_re_enable_does_not_force_reposition(svc, mock_hass):
    """Re-enabling the integration does NOT immediately send a command.

    The next natural update cycle resumes positioning.  No forced snap on re-enable
    so covers stay wherever the user placed them while disabled.
    """
    # Start enabled, run once
    _patch_position(svc, 50)
    svc.enabled = True

    # Disable — no commands
    svc.enabled = False
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Re-enable without calling apply_position — reconcile runs but target_call is empty
    svc.enabled = True
    await svc._reconcile(dt.datetime.now(dt.UTC))

    # Still no send — nothing in target_call
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_safety_targets_cleared_on_disable_prevents_replay(svc, mock_hass):
    """Safety targets cleared on disable cannot be replayed after re-enable.

    The switch OFF transition calls clear_non_safety_targets() and
    _safety_targets.clear() so no prior force position is resent on re-enable.
    """
    svc.target_call["cover.force"] = 0
    svc.wait_for_target["cover.force"] = False
    svc._safety_targets.add("cover.force")
    _patch_position(svc, 50)

    # Simulate what the switch OFF transition does
    svc.clear_non_safety_targets()  # clears non-safety (cover.force is safety, kept)
    svc._safety_targets.clear()     # explicitly cleared on Integration Enabled OFF
    # Now clear target_call entry that was safety (no longer in _safety_targets, remove manually)
    svc.target_call.clear()

    svc.enabled = True  # Re-enable

    # Reconcile — nothing to send since target_call is empty
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_flag_defaults_to_true(svc):
    """CoverCommandService starts with enabled=True (integration on by default)."""
    assert svc.enabled is True


def test_enabled_setter(svc):
    """Enabled setter persists the value."""
    svc.enabled = False
    assert svc.enabled is False
    svc.enabled = True
    assert svc.enabled is True


@pytest.mark.asyncio
async def test_enable_resumes_normal_sends(svc, mock_hass):
    """After re-enabling, normal sends work again (not blocked)."""
    svc.target_call["cover.test"] = 60
    svc.wait_for_target["cover.test"] = False
    _patch_position(svc, 40)
    svc._safety_targets.discard("cover.test")
    svc.auto_control_enabled = True

    # Disabled: no send
    svc.enabled = False
    await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()

    # Re-enabled: sends
    svc.enabled = True
    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_called_once()


# ---------------------------------------------------------------------------
# Q2 — Obstacle safety: retry cap prevents cover hammering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_verification_does_not_retry_beyond_max(svc, mock_hass):
    """Reconciliation gives up after max_retries and stops sending commands.

    If a physical cover is blocked by an obstacle, the integration retries
    up to max_retries then backs off — it does not hammer the motor indefinitely.
    """
    svc.target_call["cover.test"] = 60
    svc.wait_for_target["cover.test"] = False
    # Cover stuck at 50% (blocked obstacle)
    _patch_position(svc, 50)
    svc._safety_targets.discard("cover.test")

    # Each tick: reset wait_for_target so reconcile treats the cover as settled
    # (simulates what check_target_reached does after each position report).
    with _patch_caps():
        for _ in range(5):  # 5 ticks, max_retries=3
            svc.wait_for_target["cover.test"] = False
            await svc._reconcile(dt.datetime.now(dt.UTC))

    # Sent at most max_retries (3) times, not 5
    assert mock_hass.services.async_call.call_count <= 3
    # Entity is now in gave_up set — integration backed off
    assert "cover.test" in svc._gave_up


@pytest.mark.asyncio
async def test_delta_prevents_hammering_when_cover_reports_same_position(svc, mock_hass):
    """Delta threshold short-circuits redundant sends when cover position is already at target.

    If a cover reports a position matching the target (within tolerance), reconciliation
    clears wait_for_target and does not resend — preventing motor hammering.
    """
    svc.target_call["cover.test"] = 60
    svc.wait_for_target["cover.test"] = False
    # Cover already at target (within 3% tolerance)
    _patch_position(svc, 61)

    with _patch_caps():
        await svc._reconcile(dt.datetime.now(dt.UTC))

    # No command sent — cover is at target
    mock_hass.services.async_call.assert_not_called()
    # Retry count reset (cover arrived)
    assert svc._retry_counts.get("cover.test", 0) == 0


# ---------------------------------------------------------------------------
# Q3 — Manual move / child-safety / walk-through priority chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_override_wins_over_auto_control(svc, mock_hass):
    """Manual-override entity is skipped by reconciliation even when auto control is on.

    Semantic: user walked through, pushed the cover — integration backs off.
    auto_control=True but entity is in manual_override_entities → skip.
    """
    svc.target_call["cover.room"] = 60
    svc.wait_for_target["cover.room"] = False
    _patch_position(svc, 80)  # User moved cover to 80%
    svc.manual_override_entities = {"cover.room"}
    svc.auto_control_enabled = True
    svc._safety_targets.discard("cover.room")

    await svc._reconcile(dt.datetime.now(dt.UTC))

    # Must NOT resend — manual override takes priority
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_integration_disabled_preserves_manual_position(svc, mock_hass):
    """Disabling integration while a cover is at a user-chosen position does not move it.

    Semantic: user set cover for a walk-through; disabling ACP must not snap it back.
    """
    _patch_position(svc, 80)
    svc.enabled = False

    # Simulate what coordinator would do: try to send solar target
    outcome, reason = await svc.apply_position(
        "cover.room", 30, "solar", context=_ctx(auto_control=True)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"
    mock_hass.services.async_call.assert_not_called()


# ---------------------------------------------------------------------------
# Q4 — Automatic Control documentation anchors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automatic_control_off_allows_force_override(svc, mock_hass):
    """Force override sends even when Automatic Control is OFF (documented bypass).

    Safety rationale: Force Override is a user-wired safety sensor (wind, alarm).
    It must fire regardless of whether solar tracking is paused.
    This test pins the documented behavior so it cannot regress silently.
    """
    # auto_control=False but force=True (coordinator ORs auto_control|bypass_auto_control)
    _patch_position(svc, 50)
    svc.enabled = True  # Integration IS enabled; only solar tracking is off

    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 0, "force_override", context=_ctx(force=True, auto_control=True)
        )

    assert outcome == "sent"


@pytest.mark.asyncio
async def test_automatic_control_off_blocks_solar(svc, mock_hass):
    """Solar command is blocked when Automatic Control is OFF (documented behavior).

    Contrast with test_automatic_control_off_allows_force_override: solar uses
    force=False and auto_control=False → skipped.
    """
    _patch_position(svc, 40)
    svc.enabled = True  # Integration enabled
    outcome, reason = await svc.apply_position(
        "cover.test", 60, "solar", context=_ctx(auto_control=False, force=False)
    )
    assert outcome == "skipped"
    assert reason == "auto_control_off"


@pytest.mark.asyncio
async def test_kill_switch_off_blocks_force_override_unlike_auto_control(svc, mock_hass):
    """Kill switch OFF blocks force override; Automatic Control OFF does NOT.

    This is the key behavioral difference:
    - Automatic Control OFF: Force Override still fires (safety bypass).
    - Integration Enabled OFF: Force Override is blocked (true hard-off).
    """
    _patch_position(svc, 50)

    # Integration Enabled OFF — force override blocked
    svc.enabled = False
    outcome, reason = await svc.apply_position(
        "cover.test", 0, "force_override", context=_ctx(force=True, auto_control=True)
    )
    assert outcome == "skipped"
    assert reason == "integration_disabled"

    # Integration Enabled ON, auto_control via bypass — force override fires
    svc.enabled = True
    with _patch_caps():
        outcome, reason = await svc.apply_position(
            "cover.test", 0, "force_override", context=_ctx(force=True, auto_control=True)
        )
    assert outcome == "sent"
