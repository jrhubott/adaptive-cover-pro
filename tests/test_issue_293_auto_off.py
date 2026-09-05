"""Issue #293 — auto_control=OFF must send nothing, but must still observe.

Two defects, reproduced from the user's diagnostic timeline (an awning: a
non-position-capable cover, with automatic control off):

* **Defect A** — a ``force=True`` (non-safety) caller escaped the auto-control
  gate and sent a command during a cycle already skipped with
  ``auto_control_off``.
* **Defect B** — ``async_handle_cover_state_change`` early-returned when
  ``automatic_control=False``, so the user's manual response to the unwanted
  move could not register. ``wait_for_target=True`` stayed latched and the
  diagnostics file was blind to the user's intent.

The fix gates the early-return on ``manual_toggle`` ONLY. Observing a state
change is not the same as acting on it: recording manual overrides while auto
control is off lets reconciliation back off via the existing
``_manual_override_entities`` check in ``cover_command.py``, and surfaces the
user's intent in diagnostics.

Refs #293.
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
from custom_components.adaptive_cover_pro.managers.manual_override import (
    StateChangeInputs,
)


def _event(entity_id, position):
    e = MagicMock()
    e.entity_id = entity_id
    e.new_state = MagicMock()
    e.new_state.attributes = {"current_position": position}
    return e


def _make_coord_auto_off():
    """Build a minimal coordinator stub with auto_control=False, manual_toggle=True."""
    coord = MagicMock()
    coord.manual_toggle = True
    coord.automatic_control = False  # ← key: auto control is OFF
    coord.manual_ignore_external = False
    coord._cover_type = "cover_awning"
    coord.manual_reset = False
    coord.manual_threshold = 5
    coord._position_tolerance = 3
    coord.logger = MagicMock()
    coord.cover_state_change = True
    coord._is_in_startup_grace_period = MagicMock(return_value=False)
    coord._manual_gate_closed_log = MagicMock()
    coord._target_just_reached = set()
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.get_target = MagicMock(return_value=100)  # latched
    coord._cmd_svc.is_waiting_for_target = MagicMock(return_value=True)
    coord._cmd_svc.enable_position_matching = True
    return coord


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


def _ctx(
    *, force=False, is_safety=False, bypass_auto_control=False, auto_control=False
):
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


# ===========================================================================
# Defect A — nothing escapes the auto-control gate
# ===========================================================================


@pytest.mark.asyncio
async def test_full_repro_no_command_escapes_when_auto_off(cmd_svc, hass):
    """Sequence: regular update skip → force=True caller skip → no command sent.

    The force=True caller (e.g. the post-fix incorrect call we are guarding
    against) must be skipped now, not sent.
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
    assert cmd_svc.get_target("cover.awning") is None
    assert cmd_svc.is_waiting_for_target("cover.awning") is not True


# ===========================================================================
# Defect B — the user's manual response is still observed
# ===========================================================================


@pytest.mark.asyncio
async def test_state_change_observed_when_auto_control_off():
    """Manual override observation must run even when automatic_control=False."""
    coord = _make_coord_auto_off()
    coord.manager = MagicMock()
    coord.manager.is_cover_manual.return_value = False
    coord._pending_cover_events = [_event("cover.a", 30)]  # user moved cover

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # The user's manual move MUST be observed.
    assert coord.manager.handle_state_change.call_count == 1
    assert coord.manager.handle_state_change.call_args.args[0].entity_id == "cover.a"


@pytest.mark.asyncio
async def test_full_repro_user_recovery_observed():
    """The full timeline's recovery step, on the awning entity from the report."""
    coord = _make_coord_auto_off()
    coord.manager = MagicMock()
    # was_manual=False before observation, became_manual=True after
    coord.manager.is_cover_manual.side_effect = [False, True]
    coord._pending_cover_events = [_event("cover.awning", 30)]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # Manual override observation must register even with auto_control=False.
    # The latched-target discard now fires from the manager's on_engaged edge
    # callback (wired to cmd_svc.discard_target) rather than this loop — see
    # test_discard_target_called_when_observation_flips_to_manual below and the
    # #215 test for the engine-level discard verification.
    coord.manager.handle_state_change.assert_called_once()
    assert (
        coord.manager.handle_state_change.call_args.args[0].entity_id == "cover.awning"
    )


@pytest.mark.asyncio
async def test_discard_target_called_when_observation_flips_to_manual():
    """When observation registers a manual override, latched target must be cleared.

    Without this, the unwanted force=True command's target_call would persist
    and reconciliation could resurrect it. The discard now fires from the
    manager's ``on_engaged`` edge callback (wired to ``cmd_svc.discard_target``),
    so this drives a real engine and asserts the relocated seam.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    cmd_svc = MagicMock()
    entity_id = "cover.a"
    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
        on_engaged=cmd_svc.discard_target,
    )
    manager.add_covers([entity_id])

    # User moved the cover to 30 against the latched target of 100.
    policy = MagicMock()
    policy.read_axis_value.return_value = 30
    policy.primary_axis_suppression.return_value = False

    event = _event(entity_id, 30)
    event.old_state = MagicMock()
    event.new_state.state = "open"
    event.new_state.context = None
    event.new_state.last_updated = "2026-05-10T20:42:00+00:00"

    manager.handle_state_change(
        event,
        StateChangeInputs(
            our_state=100,  # latched target
            policy=policy,
            allow_reset=False,
            is_waiting=lambda _e: False,
            manual_threshold=5,
            is_in_command_grace=lambda _e: False,
            is_in_transit=lambda _e: False,
        ),
    )

    assert manager.is_cover_manual(entity_id)
    cmd_svc.discard_target.assert_called_once_with(entity_id)


@pytest.mark.asyncio
async def test_manual_toggle_off_still_short_circuits():
    """Regression: when manual_toggle=False, early-return still short-circuits."""
    coord = _make_coord_auto_off()
    coord.manual_toggle = False  # globally disable manual override detection
    coord.manager = MagicMock()
    coord._pending_cover_events = [_event("cover.a", 30)]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # When manual_toggle is off, observation does not run.
    coord.manager.handle_state_change.assert_not_called()
