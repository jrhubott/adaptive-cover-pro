"""Tests for delta_position (minimum position adjustment) behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_limit_positions,
    build_special_positions,
)


def _make_cmd_svc(current_position):
    """Build a CoverCommandService with mocked position reading."""
    svc = CoverCommandService(
        hass=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
    )
    svc._get_current_position = MagicMock(return_value=current_position)
    return svc


def test_check_position_delta_respects_threshold():
    """Test that check_position_delta enforces minimum threshold."""
    svc = _make_cmd_svc(current_position=50)

    # Test with 5% delta (below min_change=20 — should fail)
    result = svc._check_position_delta(
        "cover.test", 55, min_change=20, special_positions=[0, 100]
    )
    assert result is False

    # Test with 25% delta (should pass)
    result = svc._check_position_delta(
        "cover.test", 75, min_change=20, special_positions=[0, 100]
    )
    assert result is True


def test_check_position_delta_already_at_target_zero():
    """_check_position_delta bypasses delta for special target 0% (issue #290).

    Same-position short-circuit was moved to apply_position (issue #290) so
    it applies to force=True callers too.  _check_position_delta no longer
    returns False for same-position — it returns True (bypass) when the target
    is a special position, which is the case for 0%.
    """
    svc = _make_cmd_svc(current_position=0)
    result = svc._check_position_delta(
        "cover.test", 0, min_change=1, special_positions=[0, 100]
    )
    assert result is True  # special target bypass; same-position caught upstream


def test_check_position_delta_already_at_target_hundred():
    """_check_position_delta bypasses delta for special target 100% (issue #290).

    Same-position short-circuit lives in apply_position (issue #290), not here.
    """
    svc = _make_cmd_svc(current_position=100)
    result = svc._check_position_delta(
        "cover.test", 100, min_change=1, special_positions=[0, 100]
    )
    assert result is True  # special target bypass; same-position caught upstream


def test_check_position_delta_already_at_target_default():
    """_check_position_delta bypasses delta for special target = default_height (issue #290).

    Same-position short-circuit lives in apply_position (issue #290), not here.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    svc = _make_cmd_svc(current_position=40)
    special = build_special_positions({CONF_DEFAULT_HEIGHT: 40})
    result = svc._check_position_delta(
        "cover.test", 40, min_change=1, special_positions=special
    )
    assert result is True  # special target bypass; same-position caught upstream


def test_check_position_delta_transition_to_zero_from_five():
    """Cover at 5%, target 0% — special target bypasses delta check (transition allowed)."""
    svc = _make_cmd_svc(current_position=5)
    result = svc._check_position_delta(
        "cover.test", 0, min_change=20, special_positions=[0, 100]
    )
    assert result is True


def test_check_position_delta_transition_from_zero_to_fifty():
    """Cover at 0%, target 50% — special current bypasses delta check (transition allowed)."""
    svc = _make_cmd_svc(current_position=0)
    result = svc._check_position_delta(
        "cover.test", 50, min_change=20, special_positions=[0, 100]
    )
    assert result is True


def test_check_position_delta_already_at_target_sun_just_appeared():
    """Cover at 0%, target 0%, sun_just_appeared=True — sun-appearance bypasses same-position check.

    The sun_just_appeared flag fires once when the sun emerges; the cover needs to
    re-confirm its position even if it appears to already be at the target, because
    the last known position may be stale.  This takes priority over the same-position
    short-circuit.
    """
    svc = _make_cmd_svc(current_position=0)
    result = svc._check_position_delta(
        "cover.test",
        0,
        min_change=1,
        special_positions=[0, 100],
        sun_just_appeared=True,
    )
    assert result is True


def test_check_position_delta_allows_special_positions():
    """Test that special positions (0, 100) are always allowed."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_DEFAULT_HEIGHT,
        CONF_SUNSET_POS,
    )

    svc = _make_cmd_svc(current_position=50)
    options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 40}
    special = build_special_positions(options)

    # Test 0% (special position — also sunset_pos)
    result = svc._check_position_delta(
        "cover.test", 0, min_change=20, special_positions=special
    )
    assert result is True

    # Test 100% (special position)
    result = svc._check_position_delta(
        "cover.test", 100, min_change=20, special_positions=special
    )
    assert result is True

    # Test default height (special position)
    result = svc._check_position_delta(
        "cover.test", 40, min_change=20, special_positions=special
    )
    assert result is True


def test_check_position_delta_handles_none_position():
    """Test that check_position_delta handles unavailable position."""
    svc = _make_cmd_svc(current_position=None)

    # Should allow move when position unavailable
    result = svc._check_position_delta(
        "cover.test", 75, min_change=20, special_positions=[0, 100]
    )
    assert result is True


def test_timed_refresh_skips_small_delta():
    """Test that timed refresh respects delta_position."""
    # This is a documentation test showing expected behavior
    # The actual implementation is tested via integration tests

    # Expected behavior:
    # 1. Timed refresh is triggered with sunset_pos
    # 2. check_position_delta() is called before moving cover
    # 3. If delta < min_change, cover does NOT move
    # 4. If delta >= min_change OR special position, cover moves

    # This test documents the fix for Issue #10
    pass


def test_position_verification_respects_delta():
    """Test that position verification retry respects delta_position."""
    # This is a documentation test showing expected behavior
    # The actual implementation is tested via integration tests

    # Expected behavior:
    # 1. Position verification detects mismatch
    # 2. check_position_delta() is called before retrying
    # 3. If delta < min_change, retry is skipped
    # 4. If delta >= min_change OR special position, retry happens

    # This test documents the fix for Issue #10
    pass


def test_button_reset_respects_delta():
    """Test that manual override reset button respects delta_position."""
    # This is a documentation test showing expected behavior
    # The actual implementation is tested via integration tests

    # Expected behavior:
    # 1. User presses reset button
    # 2. check_position_delta() is called before moving
    # 3. If delta < min_change, cover does NOT move
    # 4. If delta >= min_change OR special position, cover moves
    # 5. Manual override flag is reset regardless

    # This test documents the fix for Issue #10
    pass


# ---------------------------------------------------------------------------
# Issue #474 — snap-to-floor: active position limits bypass the delta gate
# ---------------------------------------------------------------------------


def test_build_special_positions_includes_active_min_floor():
    """Active floor (always-enforced) is included in special positions (issue #474).

    When enable_min_position is False the floor is always enforced regardless of
    sun-tracking state.  A target pinned to 25 must bypass the delta gate so the
    cover can reach its configured floor even when the delta is below min_change.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION,
    )

    options = {CONF_MIN_POSITION: 25, CONF_ENABLE_MIN_POSITION: False}
    special = build_special_positions(options)
    assert 25 in special


def test_delta_bypassed_when_target_is_active_min_floor():
    """Cover at 29%, target 25% (active floor), delta_position=5 → command sent (issue #474).

    Exact symptom from the issue: the 4-point delta (29→25) is below the 5% threshold
    so the gate suppresses it — unless the floor (25) is in the special-positions set.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION,
    )

    svc = _make_cmd_svc(current_position=29)
    options = {CONF_MIN_POSITION: 25, CONF_ENABLE_MIN_POSITION: False}
    special = build_special_positions(options)
    result = svc._check_position_delta(
        "cover.test", 25, min_change=5, special_positions=special
    )
    assert result is True  # floor bypass — command must reach the configured floor


def test_build_special_positions_includes_active_max_ceiling():
    """Active ceiling (always-enforced) is included in special positions (issue #474).

    Symmetric with the floor: when enable_max_position is False the ceiling is
    always active and a target pinned to it bypasses the delta gate.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MAX_POSITION,
        CONF_MAX_POSITION,
    )

    options = {CONF_MAX_POSITION: 80, CONF_ENABLE_MAX_POSITION: False}
    special = build_special_positions(options)
    assert 80 in special


def test_build_special_positions_skips_min_floor_when_sun_tracking_only():
    """Floor is NOT bypassed when enable_min_position=True (sun-tracking-only, conservative v1).

    Conservative v1: only always-enforced limits bypass the delta gate.
    When the floor only applies during sun-tracking (enable_min_position=True),
    we cannot determine activeness without sun_valid context, so we do not add it
    to special_positions.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION,
    )

    options = {CONF_MIN_POSITION: 25, CONF_ENABLE_MIN_POSITION: True}
    special = build_special_positions(options)
    assert 25 not in special


# ---------------------------------------------------------------------------
# Issue #1350 — the min/max limit delta-gate bypass must be one-shot per
# approach, not permanent (mirrors the ``forced_endpoint`` anti-relay latch).
# ---------------------------------------------------------------------------


def _make_limit_latch_svc(current_position: int, state: str = "open"):
    """Build a CoverCommandService wired for an ``apply_position()``-level test.

    Mirrors ``tests/test_managers/test_cover_command.py``'s
    ``_make_svc_with_tolerance`` + ``_stub_state`` pair: a MagicMock hass
    whose ``states.get`` reports an available cover at *current_position*
    with ``services.async_call`` wired as an AsyncMock, so ``apply_position``
    runs its real gate chain (nothing about the delta/latch logic is
    stubbed away — only ``_prepare_service_call`` and ``_check_time_delta``
    are, exactly as the existing apply_position-level tests in
    ``test_managers/test_cover_command.py`` do).
    """
    hass = MagicMock()
    state_obj = MagicMock()
    state_obj.state = state
    state_obj.attributes = {
        "current_position": current_position,
        "supported_features": 15,
    }
    hass.states.get.return_value = state_obj
    hass.services.async_call = AsyncMock(return_value=None)
    svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
    )
    svc._get_current_position = MagicMock(return_value=current_position)
    return svc


def _limit_context(options: dict, min_change: int) -> PositionContext:
    """PositionContext for a limit-pinned target, wired like the coordinator.

    ``special_positions``/``limit_positions`` both come from the same
    ``options`` dict via the production helpers so the test exercises the
    real integration seam (``build_special_positions`` +
    ``build_limit_positions``) rather than a hand-built list.
    """
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=min_change,
        time_threshold=0,
        special_positions=build_special_positions(options),
        limit_positions=build_limit_positions(options),
    )


@pytest.mark.asyncio
async def test_limit_pinned_target_commanded_once_then_held():
    """A limit-pinned target is commanded once, then held (issue #1350).

    min_position=10, enable_min_position=False (always-enforced floor),
    delta_position=5. The cover reports 11 after being commanded to 10 (a
    Shelly 2PM Gen4's tilt back-drives the carriage by ~1% on a coupled
    venetian) and never moves from there. PR #763 (#474) made 10 a
    permanent delta-gate bypass, so every cycle re-sent the command forever.
    The fix narrows the bypass to once per approach: cycle 1 sends (the
    #763 guarantee), cycle 2 (same target, position still pinned at 11) is
    held by the ordinary delta_position gate instead of re-firing.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION,
    )

    options = {CONF_MIN_POSITION: 10, CONF_ENABLE_MIN_POSITION: False}
    ctx = _limit_context(options, min_change=5)
    svc = _make_limit_latch_svc(current_position=11)

    with (
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        result_1 = await svc.apply_position("cover.test", 10, "solar", ctx)
        result_2 = await svc.apply_position("cover.test", 10, "solar", ctx)

    assert result_1 == ("sent", "set_cover_position")
    assert result_2 == ("skipped", "delta_too_small")
    skipped = svc.last_skipped_action
    assert skipped["position_delta"] == 1
    assert skipped["min_delta_required"] == 5


@pytest.mark.asyncio
async def test_limit_snap_first_approach_still_bypasses_delta():
    """First approach to a configured limit still bypasses the delta gate (#763 preserved).

    Cover at 29%, floor 25% (always-enforced), delta_position=5 — the exact
    #763 scenario, now pinned at the ``apply_position`` integration seam
    rather than only at the ``_check_position_delta`` unit level. The latch
    is unset on a fresh approach, so the floor still bypasses the 4-point
    delta and the command is sent.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MIN_POSITION,
        CONF_MIN_POSITION,
    )

    options = {CONF_MIN_POSITION: 25, CONF_ENABLE_MIN_POSITION: False}
    ctx = _limit_context(options, min_change=5)
    svc = _make_limit_latch_svc(current_position=29)

    with (
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        result = await svc.apply_position("cover.test", 25, "solar", ctx)

    assert result == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_limit_flip_between_floor_and_ceiling_refires():
    """A flip between the floor and the ceiling re-fires the bypass each time (issue #1350).

    min_position=10, max_position=80, both always-enforced, delta_position=5.
    Mirrors ``forced_endpoint``'s flip semantics: latching onto one limit
    must not suppress a later approach to the OTHER limit. Floor (10) is
    dispatched once, then ceiling (80), then floor (10) again — each
    dispatch fires exactly once because the latched value always differs
    from the new target at the moment it is checked.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MAX_POSITION,
        CONF_ENABLE_MIN_POSITION,
        CONF_MAX_POSITION,
        CONF_MIN_POSITION,
    )

    options = {
        CONF_MIN_POSITION: 10,
        CONF_ENABLE_MIN_POSITION: False,
        CONF_MAX_POSITION: 80,
        CONF_ENABLE_MAX_POSITION: False,
    }
    ctx = _limit_context(options, min_change=5)
    svc = _make_limit_latch_svc(current_position=29)

    with (
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        to_floor = await svc.apply_position("cover.test", 10, "solar", ctx)
        to_ceiling = await svc.apply_position("cover.test", 80, "solar", ctx)
        back_to_floor = await svc.apply_position("cover.test", 10, "solar", ctx)

    assert to_floor == ("sent", "set_cover_position")
    assert to_ceiling == ("sent", "set_cover_position")
    assert back_to_floor == ("sent", "set_cover_position")
