"""Issue #293 — full reproduction: auto_control=OFF + force=True caller +
non-position-capable cover (awning).

Re-creates the exact diagnostic timeline observed in the user's report:
- Skip recorded with reason auto_control_off (the regular update cycle)
- A force=True (is_safety=False) caller fires within the same window — should
  be skipped, NOT sent (Defect A fix)
- User's manual response feeds into async_handle_cover_state_change — must
  be observed, registering a manual override and discarding any latched
  target (Defect B fix)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)


def _patch_caps_awning():
    """Awning capability profile from the user's diagnostic file."""
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        },
    )


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def cmd_svc(hass):
    s = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_awning",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    s._enabled = True
    return s


def _ctx(*, force=False, is_safety=False, bypass_auto_control=False, auto_control=False):
    return PositionContext(
        auto_control=auto_control,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=force,
        is_safety=is_safety,
        bypass_auto_control=bypass_auto_control,
    )


@pytest.mark.asyncio
async def test_full_repro_no_command_escapes_when_auto_off(cmd_svc, hass):
    """Sequence: regular update skip → force=True caller skip → no command sent.

    Defect A: the force=True caller (e.g. the post-fix incorrect call we are
    guarding against) must be skipped now, not sent.
    """
    with _patch_caps_awning():
        # 1. Regular update: solar pipeline result with auto_control=False
        outcome1, detail1 = await cmd_svc.apply_position(
            "cover.awning",
            18,
            "solar",
            context=_ctx(force=False, is_safety=False, auto_control=False),
        )

        # 2. Same cycle: a force=True caller (manual_reset / after_override_clear)
        outcome2, detail2 = await cmd_svc.apply_position(
            "cover.awning",
            100,
            "force_caller",
            context=_ctx(force=True, is_safety=False, auto_control=False),
        )

    assert outcome1 == "skipped"
    assert detail1 == "auto_control_off"
    assert outcome2 == "skipped"
    assert detail2 == "auto_control_off"

    # No service call escaped to HA — this is the user-visible fix.
    hass.services.async_call.assert_not_called()
    assert cmd_svc.target_call.get("cover.awning") is None
    assert cmd_svc.wait_for_target.get("cover.awning") is not True


@pytest.mark.asyncio
async def test_full_repro_user_recovery_observed():
    """Defect B: even with auto_control=False, the user's manual move registers.

    Drives async_handle_cover_state_change with auto_control=False and verifies
    that handle_state_change is called and discard_target fires when the
    observation flips the entity to manual.
    """
    coord = MagicMock()
    coord.manual_toggle = True
    coord.automatic_control = False
    coord.target_call = {"cover.awning": 100}  # latched by hypothetical earlier escape
    coord.wait_for_target = {"cover.awning": True}
    coord._cover_type = "cover_awning"
    coord.manual_reset = False
    coord.manual_threshold = 5
    coord.logger = MagicMock()
    coord.cover_state_change = True
    coord._is_in_startup_grace_period = MagicMock(return_value=False)
    coord._target_just_reached = set()
    coord._cmd_svc = MagicMock()

    coord.manager = MagicMock()
    # was_manual=False before observation, became_manual=True after
    coord.manager.is_cover_manual.side_effect = [False, True]

    user_event = MagicMock()
    user_event.entity_id = "cover.awning"
    user_event.new_state = MagicMock()
    user_event.new_state.attributes = {"current_position": 30}
    coord._pending_cover_events = [user_event]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # Manual override must register, and discard_target must clear the latched
    # target so reconciliation cannot resurrect it.
    coord.manager.handle_state_change.assert_called_once()
    coord._cmd_svc.discard_target.assert_called_once_with("cover.awning")
