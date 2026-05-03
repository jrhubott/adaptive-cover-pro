"""Issue #293 — switch return-to-default uses bypass_auto_control=True.

When the auto_control switch is toggled OFF and return_to_default_toggle is
enabled, the integration moves covers to the default position as a one-shot
transition action.  Without an explicit bypass channel, the auto_control gate
fix (issue #293) would skip this legitimate caller too.

This test asserts that the switch path passes bypass_auto_control=True so
return-to-default still works after the gate fix.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)
from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch


def _patch_caps(*, has_set_position=True):
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": has_set_position,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        },
    )


def _make_coord_with_real_cmd_svc(hass):
    """Build a coordinator stub backed by a real CoverCommandService."""
    coord = MagicMock()
    coord.logger = MagicMock()
    coord.entities = ["cover.test"]
    coord.return_to_default_toggle = True
    coord.automatic_control = False  # toggle just flipped to off
    coord.manager.manual_controlled = []
    coord.config_entry.options = {"default_height": 60}
    coord.async_refresh = AsyncMock()

    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    cmd_svc._enabled = True
    coord._cmd_svc = cmd_svc

    def _build_ctx(
        entity,
        options,
        *,
        force=False,
        is_safety=False,
        bypass_auto_control=False,
        sun_just_appeared=False,
    ):
        return PositionContext(
            auto_control=False,
            manual_override=False,
            sun_just_appeared=sun_just_appeared,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
            bypass_auto_control=bypass_auto_control,
        )

    coord._build_position_context = _build_ctx
    return coord


@pytest.mark.asyncio
async def test_return_to_default_fires_when_auto_control_toggled_off():
    """The sanctioned one-shot return-to-default still fires after #293 fix."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    coord = _make_coord_with_real_cmd_svc(hass)

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch._attr_is_on = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps():
        await switch.async_turn_off()

    # The return-to-default command must have been sent.
    hass.services.async_call.assert_awaited()
    assert coord._cmd_svc.get_target("cover.test") == 60


@pytest.mark.asyncio
async def test_return_to_default_skipped_without_bypass_flag():
    """Sanity: the same call pattern WITHOUT bypass_auto_control would be skipped.

    This proves the bypass_auto_control flag is the load-bearing mechanism.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    cmd_svc._enabled = True

    ctx = PositionContext(
        auto_control=False,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=False,
        bypass_auto_control=False,  # MISSING — should be skipped
    )

    with _patch_caps():
        outcome, detail = await cmd_svc.apply_position(
            "cover.test", 60, "auto_control_off", context=ctx
        )

    assert outcome == "skipped"
    assert detail == "auto_control_off"
    hass.services.async_call.assert_not_called()
