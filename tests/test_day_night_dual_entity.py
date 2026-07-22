"""Coordinator-seam integration tests for the Model C dual-entity day/night shade.

A ``dual_entity`` day/night instance binds TWO ``cover.*`` entities — a bottom
rail (the primary / total-coverage position) and a middle rail. One resolved
pipeline position must fan out to DIFFERENT physical targets per entity: the
bottom rail gets the resolved position verbatim, the middle rail is remapped by
the policy's ``resolve_entity_target`` hook via the coordinator's polymorphic
``_entity_target`` dispatch seam. There is NO cover-type branch in the
coordinator — every other cover type's hook is identity.

Harness modelled on ``tests/test_coordinator_integration.py``: a minimal mock
coordinator is built with the required attributes, and the *real* unbound
coordinator methods (``_dispatch_to_cover``, ``_entity_target``,
``async_handle_state_change``) are bound onto it so the seam is exercised
end-to-end against a real policy.
"""

from __future__ import annotations

import datetime as dt
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

_BOTTOM = "cover.bottom_rail"
_MIDDLE = "cover.middle_rail"


class _FakeCmdSvc:
    """Minimal command service that records the target sent to each entity."""

    def __init__(self) -> None:
        self._targets: dict[str, int] = {}
        self.calls: list[tuple[str, int, str]] = []

    async def apply_position(self, entity_id, position, reason, context=None):
        self._targets[entity_id] = position
        self.calls.append((entity_id, position, reason))
        return ("sent", "set_cover_position")

    def get_target(self, entity_id: str) -> int | None:
        return self._targets.get(entity_id)


def _dual_policy(
    *, position: int, blend: int, middle: str = _MIDDLE
) -> DayNightShadePolicy:
    """Return a real policy with the Model C dispatch cache primed for one cycle."""
    policy = DayNightShadePolicy()
    svc = MagicMock()
    from tests.cover_helpers import make_cover_config, make_vertical_config

    svc.get_vertical_data.return_value = make_vertical_config()
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    result = PipelineResult(
        position=position,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom",
        tilt=blend,
    )
    policy.post_pipeline_resolve(
        result,
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=sun_data,
        config=make_cover_config(),
        config_service=svc,
        options={
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: middle,
        },
        cover=None,
    )
    return policy


def _make_coordinator(policy: DayNightShadePolicy, *, state: int) -> MagicMock:
    coordinator = MagicMock()
    coordinator.entities = [_BOTTOM, _MIDDLE]
    coordinator.logger = MagicMock()
    coordinator._policy = policy
    coordinator._cmd_svc = _FakeCmdSvc()

    # Non-skip pipeline result so _dispatch_to_cover sends rather than records.
    coordinator._pipeline_result = PipelineResult(
        position=state, control_method=ControlMethod.SOLAR, reason="solar"
    )
    coordinator._pipeline_is_safety_handler = False
    coordinator._pipeline_bypasses_auto_control = False
    coordinator.clock_window_open = True
    coordinator.state_change = True
    coordinator._last_state_change_entity = None
    coordinator._custom_position_template_trigger = False
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())

    # Bind the REAL seam methods so the polymorphic hook is exercised.
    coordinator._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coordinator
    )
    coordinator._dispatch_to_cover = types.MethodType(
        AdaptiveDataUpdateCoordinator._dispatch_to_cover, coordinator
    )
    return coordinator


@pytest.mark.asyncio
async def test_dual_entity_dispatches_distinct_targets_per_rail() -> None:
    # P=40, blend=50 → bottom rail 40, middle rail 100 - 50*(100-40)/100 = 70.
    policy = _dual_policy(position=40, blend=50)
    coordinator = _make_coordinator(policy, state=40)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )

    cmd = coordinator._cmd_svc
    assert cmd.get_target(_BOTTOM) == 40
    assert cmd.get_target(_MIDDLE) == 70
    # Both entities were commanded exactly once in the single cycle.
    dispatched = {c[0] for c in cmd.calls}
    assert dispatched == {_BOTTOM, _MIDDLE}


@pytest.mark.asyncio
async def test_dual_entity_blend_100_sends_same_target_to_both() -> None:
    # blend 100 (all sheer) → middle coincides with the bottom rail.
    policy = _dual_policy(position=30, blend=100)
    coordinator = _make_coordinator(policy, state=30)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=30, options={}
    )

    cmd = coordinator._cmd_svc
    assert cmd.get_target(_BOTTOM) == 30
    assert cmd.get_target(_MIDDLE) == 30


# ---------------------------------------------------------------------------
# Step 23 — per-rail manual override
# ---------------------------------------------------------------------------


def _state_event(entity_id: str, position: int):
    event = MagicMock()
    event.entity_id = entity_id
    event.old_state = None
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = {"current_position": position}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.new_state.context = None
    return event


@pytest.mark.asyncio
async def test_manual_override_per_rail() -> None:
    """Moving only the middle rail engages override for that entity alone.

    Because each rail's dispatched target is recorded per entity
    (``cmd_svc.get_target``), the coordinator feeds the middle rail's own
    remapped target — not the bottom rail's — into override detection. A user
    move of the middle rail away from its target trips manual control for the
    middle rail; the bottom rail, sitting at its own target, is untouched.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    # One resolved cycle records distinct per-rail targets (bottom 40, middle 70).
    policy = _dual_policy(position=40, blend=50)
    coordinator = _make_coordinator(policy, state=40)
    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )
    cmd = coordinator._cmd_svc
    assert cmd.get_target(_BOTTOM) == 40
    assert cmd.get_target(_MIDDLE) == 70

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 1},
        logger=MagicMock(),
    )
    manager.add_covers([_BOTTOM, _MIDDLE])

    # User drags the middle rail to 40 — which happens to equal the BOTTOM
    # rail's target, but is 30% off the middle rail's own target of 70. Only
    # the per-entity target distinction makes this a manual override; a shared
    # target would misread the middle rail as "already at target".
    manager.handle_state_change(
        states_data=_state_event(_MIDDLE, 40),
        our_state=cmd.get_target(_MIDDLE),
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
    )
    # The bottom rail is sitting exactly at its own recorded target of 40.
    manager.handle_state_change(
        states_data=_state_event(_BOTTOM, 40),
        our_state=cmd.get_target(_BOTTOM),
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
    )

    assert manager.is_cover_manual(_MIDDLE) is True
    assert manager.is_cover_manual(_BOTTOM) is False


# ---------------------------------------------------------------------------
# Broadcast dispatch seam — astronomical-sunset transition remaps the middle rail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sunset_window_transition_remaps_middle_rail() -> None:
    """The astronomical-sunset broadcast seam must remap the Model C middle rail.

    ``WindowTransitionTracker.check_sunset_window`` fans one sunset position out
    to every cover; a dual-entity instance must feed the middle rail its
    ``entity_target``-remapped value, consistent with the other dispatch seams —
    polymorphically, with no cover-type branch in the tracker.
    """
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    calls: dict[str, int] = {}

    async def _apply_position(entity_id, position, reason, context=None):
        calls[entity_id] = position

    # Middle rail is opened 30% above the bottom rail's sunset position.
    def _entity_target(entity_id: str, position: int) -> int:
        return position + 30 if entity_id == _MIDDLE else position

    is_sunset = {"value": False}
    tracker = WindowTransitionTracker(
        MagicMock(),
        MagicMock(),
        event_buffer=MagicMock(),
        effective_default_fn=lambda _opts: (60, is_sunset["value"]),
    )

    common = {
        "track_end_time": True,
        "automatic_control": True,
        "sunset_pos_cfg": 60,
        "options": {},
        "inverse_state_enabled": False,
        "entities": [_BOTTOM, _MIDDLE],
        "is_cover_manual": lambda _eid: False,
        "build_position_context": lambda _c, _o: MagicMock(),
        "apply_position": _apply_position,
        "refresh": AsyncMock(),
        "entity_target": _entity_target,
    }

    # First call seeds the prior state (no dispatch).
    await tracker.check_sunset_window(**common)
    assert calls == {}

    # Window opens False→True → dispatch, with the middle rail remapped.
    is_sunset["value"] = True
    await tracker.check_sunset_window(**common)

    assert calls[_BOTTOM] == 60
    assert calls[_MIDDLE] == 90
