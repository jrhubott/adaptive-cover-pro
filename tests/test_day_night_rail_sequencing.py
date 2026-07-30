"""Model C (dual_entity) rail travel sequencing — issue #1115.

Two stacked rails hang in one headbox: sheer fabric spans head rail → middle
rail, blackout spans middle rail → bottom rail. They share a track, so the
middle rail physically CANNOT pass below the bottom rail. That makes the
arithmetic no-pass clamp in ``resolve_entity_target`` (``M >= P`` on two
numbers) necessary but not sufficient: if the bottom rail is currently stacked
ABOVE the middle rail's target, commanding the middle rail there tells a motor
to travel somewhere it cannot reach until the bottom rail descends past it —
stall, over-current trip, or lost calibration.

Two independent mechanisms fix that, and this module covers both:

* **Ordering** — the bottom rail's command must go out before the middle
  rail's, whatever order the user picked the covers in. Owned by
  ``CoverTypePolicy.dispatch_order_key`` and applied by the single shared
  ``order_for_dispatch`` view every dispatch seam consumes.
* **The wait gate** — the middle rail's command is withheld until the bottom
  rail's LIVE position has cleared the middle rail's target, compared in
  open-percent space. Owned by ``DayNightShadePolicy.before_position_command``
  and enforced at the one real chokepoint, ``CoverCommandService.apply_position``.
"""

from __future__ import annotations

import ast
import pathlib
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_ENTITIES,
    CONF_INVERSE_STATE,
    CONF_MY_POSITION_VALUE,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

_BOTTOM = "cover.bottom_rail"
_MIDDLE = "cover.middle_rail"
_SEQ = "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"


class _FakeCmdSvc:
    """Minimal command service recording the order targets were sent in."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def apply_position(
        self, entity_id, position, reason, context=None
    ):  # noqa: ARG002
        self.calls.append((entity_id, position))
        return ("sent", "set_cover_position")


def _dual_policy(
    *,
    position: int,
    blend: int,
    middle: str = _MIDDLE,
    inverse: bool = False,
) -> DayNightShadePolicy:
    """Build a real Model C policy with its per-cycle dispatch cache primed."""
    from tests.cover_helpers import make_cover_config, make_vertical_config

    policy = DayNightShadePolicy()
    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    options: dict = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: middle,
    }
    if inverse:
        options[CONF_INVERSE_STATE] = True
    policy.post_pipeline_resolve(
        PipelineResult(
            position=position,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=blend,
        ),
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=sun_data,
        config=make_cover_config(),
        config_service=svc,
        options=options,
        cover=None,
    )
    return policy


# ---------------------------------------------------------------------------
# Ordering — the bottom rail is always commanded first
# ---------------------------------------------------------------------------


def test_order_for_dispatch_preserves_order_for_default_policy() -> None:
    """A cover type with independent entities keeps the user's pick order.

    ``dispatch_order_key`` is a constant for every policy that does not need
    rail sequencing, so the sort is a stable-sort no-op.
    """
    picked = ["cover.c", "cover.a", "cover.b"]
    assert get_policy("cover_blind").order_for_dispatch(picked) == picked


def test_order_for_dispatch_preserves_order_for_day_night_model_a() -> None:
    """A day/night Model A instance drives ONE entity — nothing to reorder."""
    policy = DayNightShadePolicy()  # default model = position_tilt (A)
    picked = [_MIDDLE, _BOTTOM]
    assert policy.order_for_dispatch(picked) == picked


def test_order_for_dispatch_puts_bottom_rail_first_under_model_c() -> None:
    """Model C sorts the middle rail last, whatever order it was picked in."""
    policy = _dual_policy(position=40, blend=50)
    assert policy.order_for_dispatch([_MIDDLE, _BOTTOM]) == [_BOTTOM, _MIDDLE]
    assert policy.order_for_dispatch([_BOTTOM, _MIDDLE]) == [_BOTTOM, _MIDDLE]


def _ordering_coordinator(policy: DayNightShadePolicy, *, entities: list[str]):
    coordinator = MagicMock()
    coordinator.entities = entities
    coordinator.logger = MagicMock()
    coordinator._policy = policy
    coordinator._cmd_svc = _FakeCmdSvc()
    coordinator._pipeline_result = PipelineResult(
        position=40, control_method=ControlMethod.SOLAR, reason="solar"
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
    coordinator._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coordinator
    )
    coordinator._dispatch_to_cover = types.MethodType(
        AdaptiveDataUpdateCoordinator._dispatch_to_cover, coordinator
    )
    return coordinator


@pytest.mark.asyncio
async def test_dispatch_cycle_commands_bottom_rail_before_middle() -> None:
    """The main dispatch loop honours the policy's rail order (issue #1115).

    The user's cover pick order is arbitrary — here the middle rail was picked
    first. Sending its command before the bottom rail's leaves the middle motor
    driving toward a target the (still-stacked) bottom rail is blocking, because
    the bottom rail's own command has not even been issued yet.
    """
    policy = _dual_policy(position=40, blend=50)
    coordinator = _ordering_coordinator(policy, entities=[_MIDDLE, _BOTTOM])

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )

    assert [eid for eid, _pos in coordinator._cmd_svc.calls] == [_BOTTOM, _MIDDLE]
    # The per-rail arithmetic is unchanged by the reordering.
    assert dict(coordinator._cmd_svc.calls) == {_BOTTOM: 40, _MIDDLE: 70}


@pytest.mark.asyncio
async def test_first_refresh_commands_bottom_rail_before_middle() -> None:
    """Startup dispatch rides the same ordered view as the main loop."""
    policy = _dual_policy(position=40, blend=50)
    coordinator = _ordering_coordinator(policy, entities=[_MIDDLE, _BOTTOM])
    coordinator.manager.is_cover_manual = lambda _eid: False
    coordinator.check_adaptive_time = True
    coordinator._is_reload = False

    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(coordinator, 40, {})

    assert [eid for eid, _pos in coordinator._cmd_svc.calls] == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_force_send_commands_bottom_rail_before_middle() -> None:
    """The force-send seam orders an explicitly-supplied cover subset too."""
    policy = _dual_policy(position=40, blend=50)
    coordinator = _ordering_coordinator(policy, entities=[_MIDDLE, _BOTTOM])
    coordinator.manager.is_cover_manual = lambda _eid: False
    coordinator.clock_window_open = True
    coordinator.automatic_control = True

    sent = await AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, 40, {}, entities=[_MIDDLE, _BOTTOM]
    )

    assert sent == {_BOTTOM, _MIDDLE}
    assert [eid for eid, _pos in coordinator._cmd_svc.calls] == [_BOTTOM, _MIDDLE]


# ---------------------------------------------------------------------------
# Ordering — the user-command fan-out seams
# ---------------------------------------------------------------------------
# A user command is the worst place to lose the rail order, because the same
# press engages manual override: the middle rail's command gets dropped by the
# clearance gate (it polls a bottom rail that has not been commanded yet and
# times out), and the override then blocks the pending-latch retry on every
# later cycle. The shade stays in a wrong split until the user intervenes again.


def _record_user_commands(coordinator, method_name: str) -> list[str]:
    """Replace one user-command entry point with an order-recording stub."""
    sent: list[str] = []

    async def _record(entity_id, *_args, **_kwargs):
        sent.append(entity_id)

    setattr(coordinator, method_name, AsyncMock(side_effect=_record))
    return sent


@pytest.mark.asyncio
async def test_my_position_button_commands_bottom_rail_before_middle() -> None:
    """The My Position button fans its preset out in policy-mandated rail order."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )

    button = MagicMock()
    button._entities = [_MIDDLE, _BOTTOM]
    button.config_entry.options = {CONF_MY_POSITION_VALUE: 60}
    button.coordinator._policy = _dual_policy(position=40, blend=50)
    sent = _record_user_commands(button.coordinator, "async_apply_user_position")

    await AdaptiveCoverMyPositionButton.async_press(button)

    assert sent == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_set_position_service_commands_bottom_rail_before_middle(
    monkeypatch,
) -> None:
    """``adaptive_cover_pro.set_position`` over a whole instance orders its fan-out."""
    from custom_components.adaptive_cover_pro.services import set_position_service

    coord = MagicMock()
    coord.entities = [_MIDDLE, _BOTTOM]
    coord._policy = _dual_policy(position=40, blend=50)
    sent = _record_user_commands(coord, "async_apply_user_axis")
    monkeypatch.setattr(
        set_position_service, "_resolve_targets", lambda _hass, _call: {coord: None}
    )
    call = MagicMock()
    call.data = {"position": 40, "force": False}

    await set_position_service.async_handle_set_position(call)

    assert sent == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_set_tilt_service_commands_bottom_rail_before_middle(
    monkeypatch,
) -> None:
    """``set_tilt`` shares the collapse point, so it shares the ordering too.

    The tilt axis falls back to the position axis on an entity without slat
    support (#684), which puts a real position on the wire — the same seam
    shape, and the same reason to order it.
    """
    from custom_components.adaptive_cover_pro.services import set_tilt_service

    coord = MagicMock()
    coord.entities = [_MIDDLE, _BOTTOM]
    coord._policy = _dual_policy(position=40, blend=50)
    sent = _record_user_commands(coord, "async_apply_user_axis")
    monkeypatch.setattr(
        set_tilt_service, "_resolve_targets", lambda _hass, _call: {coord: None}
    )
    call = MagicMock()
    call.data = {"tilt": 30, "force": False}

    await set_tilt_service.async_handle_set_tilt(call)

    assert sent == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_set_axes_service_commands_bottom_rail_before_middle(
    monkeypatch,
) -> None:
    """``set_axes`` validates up front, then dispatches in rail order."""
    from custom_components.adaptive_cover_pro.services import set_axes_service

    coord = MagicMock()
    coord.entities = [_MIDDLE, _BOTTOM]
    coord._policy = _dual_policy(position=40, blend=50)
    coord._cover_provider.read_single_capabilities = MagicMock(
        return_value={"has_set_position": True, "has_set_tilt_position": True}
    )
    sent = _record_user_commands(coord, "async_apply_user_axis")
    monkeypatch.setattr(
        set_axes_service, "_resolve_targets", lambda _hass, _call: {coord: None}
    )
    call = MagicMock()
    call.data = {"axes": {"position": 40}, "force": False}

    await set_axes_service.async_handle_set_axes(call)

    assert sent == [_BOTTOM, _MIDDLE]


def _group_fan_out_to_one_member(member_coord) -> MagicMock:
    """Build a GroupCoordinator-shaped stub that fans out to one ACP member."""
    member_entry = MagicMock()
    member_entry.options = {CONF_ENTITIES: [_MIDDLE, _BOTTOM]}

    async def _fan_out(member, _generic, **_kwargs):
        await member(member_entry, member_coord)

    group = MagicMock()
    group._fan_out_commands = AsyncMock(side_effect=_fan_out)
    group.async_refresh = AsyncMock()
    return group


@pytest.mark.asyncio
async def test_group_cover_slider_commands_bottom_rail_before_middle() -> None:
    """A cover group dragging its slider orders each member's own rails."""
    from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator

    member_coord = MagicMock()
    member_coord._policy = _dual_policy(position=40, blend=50)
    sent = _record_user_commands(member_coord, "async_apply_user_position")

    await GroupCoordinator.async_set_position(
        _group_fan_out_to_one_member(member_coord), 40
    )

    assert sent == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_group_cover_tilt_commands_bottom_rail_before_middle() -> None:
    """The group tilt slider rides the same per-member ordered view."""
    from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator

    member_coord = MagicMock()
    member_coord._policy = _dual_policy(position=40, blend=50)
    sent = _record_user_commands(member_coord, "async_apply_user_tilt")

    await GroupCoordinator.async_set_tilt(
        _group_fan_out_to_one_member(member_coord), 30
    )

    assert sent == [_BOTTOM, _MIDDLE]


# ---------------------------------------------------------------------------
# The wait gate — the middle rail waits for the bottom rail to clear its target
# ---------------------------------------------------------------------------


class _Rails:
    """Scripted per-entity position readings; the final value repeats forever.

    Models real motor travel: each read of the bottom rail during its descent
    returns the next sampled position. ``log`` records every read so a test can
    tell "gate polled and waited" from "gate proceeded immediately".
    """

    def __init__(self, script: dict[str, list[int | None]]) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self.log: list[str] = []

    def read(self, entity_id: str) -> int | None:
        self.log.append(entity_id)
        values = self._script.get(entity_id) or [None]
        return values.pop(0) if len(values) > 1 else values[0]

    def park(self, entity_id: str, position: int) -> None:
        """Park one rail at a fixed position for every subsequent read."""
        self._script[entity_id] = [position]


def _rail_harness(
    *,
    script: dict[str, list[int | None]],
    position: int,
    blend: int,
    inverse: bool = False,
):
    """Real ``CoverCommandService`` + real Model C policy over a scripted motor.

    Returns ``(cmd_svc, policy, rails, events)``. ``events`` interleaves the
    gate's position polls (``poll:<entity>``) with the actual service calls
    (``send:<entity>``), which is exactly the ordering the physical constraint
    is about.
    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )

    rails = _Rails(script)
    events: list[str] = []

    async def _async_call(_domain, _service, data, context=None):  # noqa: ARG001
        events.append(f"send:{data['entity_id']}")

    hass = MagicMock()
    hass.services.async_call = AsyncMock(side_effect=_async_call)
    state_obj = MagicMock()
    state_obj.state = "open"
    hass.states.get = MagicMock(return_value=state_obj)

    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_day_night_shade",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        position_tolerance=2,
    )
    cmd_svc._enabled = True
    cmd_svc._get_current_position = MagicMock(side_effect=rails.read)

    policy = _dual_policy(position=position, blend=blend, inverse=inverse)

    def _gate_read(entity_id: str) -> int | None:
        events.append(f"poll:{entity_id}")
        return rails.read(entity_id)

    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=_gate_read,
        set_commanded_position=MagicMock(),
        position_tolerance=2,
        is_dry_run=lambda: False,
        entities=[_BOTTOM, _MIDDLE],
    )
    return cmd_svc, policy, rails, events


def _rail_context(policy, *, inverse: bool = False):
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        PositionContext,
    )

    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        inverse_state=inverse,
        force=True,
        policy=policy,
    )


def _patch_caps():
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command"
        ".check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        },
    )


@pytest.mark.asyncio
async def test_middle_rail_waits_for_bottom_rail_to_descend_past_it(
    monkeypatch,
) -> None:
    """The crash repro: bottom rail stacked ABOVE the middle rail's target.

    Bottom rail live at 100 (fully up), this cycle's targets are bottom 30 /
    middle 65 (blend 50). The middle rail cannot reach 65 while the bottom rail
    sits at 100 above it — commanding it there stalls the motor. The gate must
    hold the middle rail's ``set_cover_position`` until a live reading shows the
    bottom rail has descended to (or past) 65.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        # Bottom: apply_position's own read, then the gate's descent samples.
        script={_BOTTOM: [100, 100, 80, 60], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        bottom_outcome = await cmd_svc.apply_position(_BOTTOM, 30, "solar", ctx)
        middle_outcome = await cmd_svc.apply_position(
            _MIDDLE, policy.resolve_entity_target(_MIDDLE, 30), "solar", ctx
        )

    assert bottom_outcome[0] == "sent"
    assert middle_outcome[0] == "sent"
    # The bottom rail's command goes out first, then the gate polls it until the
    # reading clears 65, and only then does the middle rail's command fire.
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}")
    polls_before_send = [
        e for e in events[: events.index(f"send:{_MIDDLE}")] if e == f"poll:{_BOTTOM}"
    ]
    assert len(polls_before_send) >= 2, events


@pytest.mark.asyncio
async def test_middle_rail_proceeds_immediately_when_bottom_already_clear(
    monkeypatch,
) -> None:
    """Steady state: bottom rail already below the middle target → no waiting."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [40], _MIDDLE: [100]},
        position=40,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 70, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    # One confirming read, no polling loop.
    assert events.count(f"poll:{_BOTTOM}") == 1
    assert events[-1] == f"send:{_MIDDLE}"


@pytest.mark.asyncio
async def test_bottom_rail_is_never_gated(monkeypatch) -> None:
    """Only the middle rail is gated — the blocking rail must move freely."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 30, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert events == [f"send:{_BOTTOM}"]


@pytest.mark.asyncio
async def test_middle_rail_defers_and_latches_pending_on_timeout(
    monkeypatch,
) -> None:
    """A bottom rail that never clears defers the middle command, not sends it.

    The command is withheld with a ``policy_deferred`` skip and the entity is
    latched pending, which keeps the coordinator withholding this cycle's
    dispatched-target signature so the next cycle re-attempts the send.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events
    assert policy.has_pending_secondary_axis(_MIDDLE) is True
    assert cmd_svc.last_skipped_action["reason"] == "policy_deferred"


@pytest.mark.asyncio
async def test_pending_latch_clears_once_the_bottom_rail_has_cleared(
    monkeypatch,
) -> None:
    """The next cycle's retry sends the middle command and clears the latch."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        assert (await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx))[1] == (
            "policy_deferred"
        )
        assert policy.has_pending_secondary_axis(_MIDDLE) is True
        # Cycle 2: the bottom rail has descended to 20 — well clear of 65.
        rails.park(_BOTTOM, 20)
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert policy.has_pending_secondary_axis(_MIDDLE) is False
    assert f"send:{_MIDDLE}" in events


@pytest.mark.asyncio
async def test_middle_rail_defers_when_bottom_rail_position_unreadable(
    monkeypatch,
) -> None:
    """An unreadable bottom rail cannot be proven clear — withhold, don't guess."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [None], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events


@pytest.mark.asyncio
async def test_gate_is_inert_without_a_second_rail(monkeypatch) -> None:
    """A degenerate Model C entry (no identifiable bottom rail) must not stall.

    ``entities`` carries only the middle rail, so there is nothing to sequence
    against. Withholding forever would brick the shade; the gate proceeds.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    policy.attach(
        hass=MagicMock(),
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=MagicMock(return_value=100),
        set_commanded_position=MagicMock(),
        position_tolerance=2,
        is_dry_run=lambda: False,
        entities=[_MIDDLE],
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_MIDDLE}" in events


# ---------------------------------------------------------------------------
# Inverse state — the clearance test is an OPEN-PERCENT comparison (#993 class)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_compares_in_open_percent_not_raw_wire(monkeypatch) -> None:
    """Inverse state on: wire numbers say "wait", open-percent says "clear".

    Mirrors the existing raw middle-0 / bottom-100 inverse pair: with inversion
    the middle rail's wire target 0 IS fully open (open 100) and the bottom
    rail's wire reading 100 IS fully closed (open 0). The bottom rail is at the
    very bottom of its travel — as clear of the middle rail as it can possibly
    be — so the gate must proceed with no waiting. A naive raw comparison
    (100 > 0) would wait forever and defer a command that is already safe.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [50]},
        position=100,  # wire 100 == open 0 for the bottom rail
        blend=0,  # all blackout → middle fully open (open 100 → wire 0)
        inverse=True,
    )
    ctx = _rail_context(policy, inverse=True)
    wire_middle = policy.resolve_entity_target(_MIDDLE, 100)
    assert wire_middle == 0  # fully open on the wire

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, wire_middle, "solar", ctx)

    # A wire target of 0 routes through ``close_cover`` under the endpoint
    # substitution — the gate runs identically whichever service is emitted.
    assert outcome[0] == "sent"
    assert events.count(f"poll:{_BOTTOM}") == 1  # proceeded on the first read


@pytest.mark.asyncio
async def test_gate_still_waits_when_open_percent_is_stacked_under_inverse(
    monkeypatch,
) -> None:
    """The converse: raw numbers say "clear", open-percent says "wait".

    Inverse state on, middle wire target 100 (open 0 — fully closed / at the
    bottom) while the bottom rail reads wire 0 (open 100 — fully up, stacked
    above it). Raw ``0 <= 100`` would wave the command through into a stall; the
    open-percent comparison correctly withholds it.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [0], _MIDDLE: [50]},
        position=100,
        blend=100,  # all sheer → middle coincides with the bottom rail
        inverse=True,
    )
    ctx = _rail_context(policy, inverse=True)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 100, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events


# ---------------------------------------------------------------------------
# Model A must be untouched — the blend pre-send path stays byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_a_before_position_command_still_pre_sends_blend() -> None:
    """Inserting the Model C branch must not disturb Model A's tilt-first send."""
    policy = get_policy("cover_day_night_shade")  # default model = position_tilt
    seq = MagicMock()
    seq._get_current_position = MagicMock(return_value=10)
    seq._send_tilt_command = AsyncMock()
    policy._sequencer = seq
    ctx = MagicMock()
    ctx.tilt = 80

    result = await policy.before_position_command(
        MagicMock(),
        "cover.shade",
        service="set_cover_position",
        position=60,
        context=ctx,
        reason="solar",
    )

    assert result is not False  # never withholds
    seq._send_tilt_command.assert_awaited_once()


# ---------------------------------------------------------------------------
# Structural guard — every position fan-out seam consumes order_for_dispatch
# ---------------------------------------------------------------------------
# Converting the seams one at a time is an enumeration, and an enumeration is
# only ever as complete as the last person who read the codebase: three
# user-command seams (My Position, set_position, set_axes) were missed on the
# first pass at issue #1115. This scan closes the CLASS of mistake — a new
# dispatch seam that iterates the raw config pick order fails here rather than
# shipping and stalling somebody's shade.
#
# Same shape as the source-scan guards in ``tests/test_cover_types/test_axes.py``
# (hardcoded ``caps.get("has_*")``, cover-type literals, tilt-mode strings):
# walk the production tree, collect offenders, assert the list is empty with a
# message that says what to do instead.

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PRODUCTION_ROOT = _REPO_ROOT / "custom_components" / "adaptive_cover_pro"

# Calls that put a position on the wire for ONE cover. A ``for`` loop whose body
# reaches any of these is fanning a position out over a collection of the
# instance's covers, which is exactly where the rail order has to be decided.
#
# ``async_apply_user_tilt`` is in the set because the tilt axis falls back to the
# position axis on an entity without slat support (#684). Stop dispatch
# (``async_apply_user_stop``, ``stop_all``) is deliberately absent: a stop has no
# travel target, so there is no clearance to sequence against.
_POSITION_DISPATCH_CALLS = frozenset(
    {
        "apply_position",  # CoverCommandService — the real chokepoint
        "_dispatch_to_cover",  # coordinator's per-cover dispatch helper
        "async_apply_user_position",  # user-command entry point
        "async_apply_user_axis",  # axis-generic user-command entry point
        "async_apply_user_tilt",  # falls back to the position axis (#684)
    }
)
_ORDER_VIEW = "order_for_dispatch"

# Raw entity-collection expressions. A matched loop iterating one of these is
# unordered by construction, whatever else its enclosing function does — this
# catches a half-converted function that still has one raw loop in it.
_RAW_ENTITY_ATTRS = frozenset({"entities", "_entities"})

# Modules expected to contain at least one seam. A rename that makes the scan
# stop matching (say ``apply_position`` gets renamed) would otherwise leave this
# guard silently green over zero call sites.
_SEAM_MODULES = frozenset(
    {
        "button.py",
        "coordinator.py",
        "group_coordinator.py",
        "switch.py",
        "services/set_position_service.py",
        "services/set_tilt_service.py",
        "services/set_axes_service.py",
        "state/window_transition_tracker.py",
    }
)

# (module path relative to the production root, innermost enclosing function) →
# why that seam legitimately does not call ``order_for_dispatch`` itself.
_ORDERING_EXEMPT = {
    ("state/window_transition_tracker.py", "check_sunset_window"): (
        "Receives an already-ordered entity list. The tracker is HA-boundary "
        "code with no policy handle, so the coordinator applies "
        "order_for_dispatch at the call site "
        "(_check_sunset_window_transition, entities=...)."
    ),
}


def _called_names(node: ast.AST) -> set[str]:
    """Every callee name reachable from ``node`` (attribute or bare name)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _is_raw_entity_collection(node: ast.expr) -> bool:
    """Whether this ``for`` iterable is a raw, unordered entity collection."""
    # self.entities / coord.entities / self.coordinator.entities / self._entities
    if isinstance(node, ast.Attribute) and node.attr in _RAW_ENTITY_ATTRS:
        return True
    # entry.options.get(CONF_ENTITIES, [])
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "CONF_ENTITIES"
    )


def _dispatch_loops(tree: ast.AST) -> list[tuple[ast.stmt, ast.AST | None]]:
    """Every position fan-out loop, paired with its innermost enclosing function."""
    found: list[tuple[ast.stmt, ast.AST | None]] = []

    def walk(node: ast.AST, enclosing: ast.AST | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            enclosing = node
        if isinstance(node, ast.For | ast.AsyncFor) and (
            _called_names(node) & _POSITION_DISPATCH_CALLS
        ):
            found.append((node, enclosing))
        for child in ast.iter_child_nodes(node):
            walk(child, enclosing)

    walk(tree, None)
    return found


@pytest.mark.unit
def test_every_position_fan_out_seam_consumes_order_for_dispatch() -> None:
    """Fail if a loop fans a position out over covers without the ordered view.

    Wrap the iteration in ``self._policy.order_for_dispatch(...)`` — the single
    shared view that expresses ``dispatch_order_key`` once (CODING_GUIDELINES.md
    "No Code Duplication", issue #1115). It is a stable sort with a constant
    default key, so it is an exact no-op for every cover type whose entities are
    physically independent. If a seam genuinely receives an already-ordered list
    from its caller, add it to ``_ORDERING_EXEMPT`` with the reason.
    """
    offenders: list[str] = []
    seen_modules: set[str] = set()

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(_PRODUCTION_ROOT).as_posix()
        for loop, enclosing in _dispatch_loops(tree):
            seen_modules.add(module)
            fn_name = getattr(enclosing, "name", "<module>")
            where = f"{path.relative_to(_REPO_ROOT)}:{loop.lineno} ({fn_name})"
            if _is_raw_entity_collection(loop.iter):
                offenders.append(
                    f"{where}: iterates the raw collection "
                    f"{ast.unparse(loop.iter)!r}"
                )
                continue
            if (module, fn_name) in _ORDERING_EXEMPT:
                continue
            if enclosing is None or _ORDER_VIEW not in _called_names(enclosing):
                offenders.append(f"{where}: never calls {_ORDER_VIEW}")

    missing = _SEAM_MODULES - seen_modules
    assert not missing, (
        "The dispatch-seam scan found no position fan-out in: "
        f"{sorted(missing)}. Either the seam moved (update _SEAM_MODULES) or a "
        f"dispatch entry point was renamed (update _POSITION_DISPATCH_CALLS) — "
        "until then this guard is watching nothing."
    )
    assert not offenders, (
        "Position fan-out seams that skip the policy's dispatch order "
        "(issue #1115) — wrap the iteration in "
        "self._policy.order_for_dispatch(...):\n  " + "\n  ".join(offenders)
    )
