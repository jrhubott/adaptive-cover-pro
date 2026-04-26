"""Issue #293 — auto_control gate must not be bypassed by force=True alone.

When auto_control is OFF, only callers passing is_safety=True (force_override,
weather override) or bypass_auto_control=True (the sanctioned switch
return-to-default one-shot) may move covers. Plain force=True (e.g.
manual_reset, after_override_clear, switch turn_on) MUST still respect
auto_control=False.

The corollary: is_safety=True callers MUST continue to bypass the gate so
weather and force_override safety overrides work even when auto control is off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def svc(hass):
    s = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    s._enabled = True
    return s


def _ctx(*, force=False, is_safety=False, auto_control=True):
    return PositionContext(
        auto_control=auto_control,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=force,
        is_safety=is_safety,
    )


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


# ---------------------------------------------------------------------------
# Defect A — force=True alone must NOT bypass auto_control_off
# ---------------------------------------------------------------------------


class TestForceWithoutSafetyDoesNotBypassAutoControl:
    """When auto_control is off and is_safety is False, the gate must hold."""

    @pytest.mark.asyncio
    async def test_force_only_skipped_when_auto_control_off(self, svc, hass):
        with _patch_caps():
            outcome, detail = await svc.apply_position(
                "cover.test",
                100,
                "manual_reset",
                _ctx(force=True, is_safety=False, auto_control=False),
            )

        assert outcome == "skipped"
        assert detail == "auto_control_off"
        assert svc.last_skipped_action["reason"] == "auto_control_off"
        hass.services.async_call.assert_not_called()
        assert svc.target_call.get("cover.test") is None
        assert svc.wait_for_target.get("cover.test") is not True


# ---------------------------------------------------------------------------
# Safety regression — is_safety=True MUST still bypass auto_control_off
# ---------------------------------------------------------------------------


class TestSafetyBypassStillWorks:
    """Safety overrides (force_override, weather) bypass auto_control_off."""

    @pytest.mark.asyncio
    async def test_is_safety_proceeds_when_auto_control_off(self, svc, hass):
        with _patch_caps():
            outcome, _detail = await svc.apply_position(
                "cover.test",
                0,
                "force_override",
                _ctx(force=True, is_safety=True, auto_control=False),
            )

        assert outcome == "sent"
        hass.services.async_call.assert_awaited_once()
        assert svc.target_call["cover.test"] == 0
        assert "cover.test" in svc._safety_targets

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "trigger",
        ["force_override", "weather", "synthetic_safety"],
    )
    async def test_safety_callers_bypass_gate(self, svc, hass, trigger):
        with _patch_caps():
            outcome, _ = await svc.apply_position(
                f"cover.{trigger}",
                25,
                trigger,
                _ctx(force=True, is_safety=True, auto_control=False),
            )

        assert outcome == "sent"
        assert svc.target_call[f"cover.{trigger}"] == 25
        assert f"cover.{trigger}" in svc._safety_targets
