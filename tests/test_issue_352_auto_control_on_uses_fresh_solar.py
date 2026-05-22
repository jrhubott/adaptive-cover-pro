"""Issue #352: Auto Control OFF→ON must dispatch the post-refresh solar value.

Before the fix, ``switch.async_turn_on`` for ``automatic_control`` dispatched
``coordinator.state`` BEFORE calling ``async_refresh()``. That cached state was
the previous pipeline cycle's result — and when the previous cycle ran outside
the time window (or with auto-control off), it was the DefaultHandler position
(typically "open"=100). The subsequent refresh did NOT set ``state_change=True``,
so the freshly-computed solar position was never dispatched until the next
periodic window-transition tick (up to ~1 min later). The blind opened fully,
then closed to the solar position seconds later.

The fix funnels through the coordinator's normal state-change dispatch:
``switch.async_turn_on`` sets ``coordinator.state_change = True`` and lets
``async_refresh`` → ``async_handle_state_change`` own dispatch, so the value
sent reflects the post-refresh pipeline result, not the stale pre-refresh
snapshot.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch


SOLAR_POSITION = 30
CACHED_DEFAULT_POSITION = 100  # previous cycle's DefaultHandler win (open)


def _make_coord_with_stale_default():
    """Coordinator whose pre-refresh ``state`` is the DefaultHandler position from
    a previous out-of-window cycle, and whose next refresh will produce the
    SOLAR_POSITION via ``async_handle_state_change``.
    """
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = False
    coord.entities = ["cover.test_1"]
    coord._policy = MagicMock()
    coord._policy.sequencer = None
    coord._inverse_state = False
    coord._use_interpolation = False  # ``coord.state`` post-processing
    coord._pending_cover_events = []

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

    # Pre-refresh: stale state from previous out-of-window cycle. ``coord.state``
    # is a derived property that reads ``_pipeline_result.position`` and applies
    # interpolation/inverse_state, so setting the pipeline result is enough.
    coord._pipeline_result = PipelineResult(
        position=CACHED_DEFAULT_POSITION,
        control_method=ControlMethod.DEFAULT,
        reason="default",
        bypass_auto_control=False,
    )
    coord.state_change = False

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    manager.manual_controlled = []
    coord.manager = manager
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True  # check_adaptive_time delegates here
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coord._last_state_change_entity = None

    coord.config_entry = MagicMock()
    coord.config_entry.options = {}

    coord.min_change = 2
    coord.time_threshold = 0

    def _fake_build_ctx(
        entity,
        options,
        *,
        force=False,
        is_safety=False,
        bypass_auto_control=False,
        sun_just_appeared=False,
    ):
        return PositionContext(
            auto_control=True,  # automatic_control has been flipped True
            manual_override=False,
            sun_just_appeared=sun_just_appeared,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
            bypass_auto_control=bypass_auto_control,
        )

    coord._build_position_context = _fake_build_ctx

    return coord


def _wire_solar_refresh(coord):
    """Make ``coord.async_refresh`` simulate a pipeline tick that computes
    SOLAR_POSITION and routes through ``async_handle_state_change`` when
    ``coord.state_change`` is set.
    """

    async def _fake_refresh():
        # ``coord.state`` derives from ``_pipeline_result.position``; setting
        # the pipeline result is enough to flip the state from 100 (default) to
        # SOLAR_POSITION.
        coord._pipeline_result = PipelineResult(
            position=SOLAR_POSITION,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            bypass_auto_control=False,
        )
        if coord.state_change:
            with patch.object(
                type(coord),
                "is_force_override_active",
                new_callable=PropertyMock,
                return_value=False,
            ):
                await AdaptiveDataUpdateCoordinator.async_handle_state_change(
                    coord,
                    coord.state,
                    coord.config_entry.options,
                    prev_force_override=False,
                    custom_position_released_entities=set(),
                )

    coord.async_refresh = AsyncMock(side_effect=_fake_refresh)


def _make_switch(coord):
    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._switch_name = "Automatic Control"
    switch._initial_state = True
    switch._attr_is_on = False
    switch.schedule_update_ha_state = MagicMock()
    return switch


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_control_on_dispatches_post_refresh_solar_not_cached_default():
    """Turn on auto-control must dispatch the FRESH solar value, not the cached default.

    Pre-refresh ``coord.state == 100`` (DefaultHandler win from a previous
    out-of-window cycle). After the fix, the only dispatch happens inside
    ``async_handle_state_change`` (driven by the refresh) and the value is
    ``SOLAR_POSITION``. Before the fix, the switch dispatches ``100`` directly
    from the loop in ``async_turn_on`` and never re-dispatches the fresh
    solar value because ``state_change`` was never set.
    """
    coord = _make_coord_with_stale_default()
    _wire_solar_refresh(coord)

    switch = _make_switch(coord)
    await switch.async_turn_on()

    assert coord._cmd_svc.apply_position.await_count >= 1
    dispatched_positions = [
        c.args[1] for c in coord._cmd_svc.apply_position.await_args_list
    ]
    assert SOLAR_POSITION in dispatched_positions, (
        f"expected fresh solar {SOLAR_POSITION} to be dispatched, got "
        f"{dispatched_positions}"
    )
    assert CACHED_DEFAULT_POSITION not in dispatched_positions, (
        f"stale pre-refresh default {CACHED_DEFAULT_POSITION} must NOT be "
        f"dispatched; got {dispatched_positions}"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_control_on_signals_state_change_and_refresh_owns_dispatch():
    """Structural guard: the switch must signal state_change and refresh owns dispatch.

    Asserts:
    1. ``coord.state_change is True`` at the moment ``async_refresh`` is invoked
       (so the refresh's ``async_handle_state_change`` step actually runs).
    2. NO ``apply_position`` calls happen BEFORE ``async_refresh`` is invoked
       (BINDING_GUIDELINES #1/#5: switch is signal-only, coordinator owns dispatch).
    """
    coord = _make_coord_with_stale_default()
    _wire_solar_refresh(coord)

    refresh_observed_state_change: dict[str, bool] = {}
    dispatches_before_refresh: list[int] = []
    refresh_started = {"value": False}

    original_apply = coord._cmd_svc.apply_position

    async def _record_dispatch(*args, **kwargs):
        if not refresh_started["value"]:
            dispatches_before_refresh.append(args[1])
        return await original_apply(*args, **kwargs)

    coord._cmd_svc.apply_position = AsyncMock(side_effect=_record_dispatch)

    original_refresh_fn = coord.async_refresh.side_effect

    async def _refresh_with_capture():
        refresh_observed_state_change["was_true"] = coord.state_change
        refresh_started["value"] = True
        await original_refresh_fn()

    coord.async_refresh = AsyncMock(side_effect=_refresh_with_capture)

    switch = _make_switch(coord)
    await switch.async_turn_on()

    assert refresh_observed_state_change.get("was_true") is True, (
        "switch.async_turn_on must set coordinator.state_change=True before "
        "calling async_refresh() so async_handle_state_change runs"
    )
    assert dispatches_before_refresh == [], (
        "switch.async_turn_on must NOT dispatch positions before "
        "async_refresh() — the coordinator's normal state-change path owns "
        f"dispatch. Saw pre-refresh dispatches: {dispatches_before_refresh}"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_control_on_outside_time_window_does_not_dispatch():
    """Outside the time window the coordinator's normal gate still blocks dispatch.

    With ``check_adaptive_time=False``, ``async_handle_state_change`` returns
    early at the non-safety guard (coordinator.py:1329-1337). After the fix,
    the switch flips ``state_change=True`` and lets the refresh decide — and
    the refresh correctly skips dispatch outside the window.
    """
    coord = _make_coord_with_stale_default()
    coord._time_mgr.is_active = False  # outside time window
    _wire_solar_refresh(coord)

    switch = _make_switch(coord)
    await switch.async_turn_on()

    coord._cmd_svc.apply_position.assert_not_awaited()
