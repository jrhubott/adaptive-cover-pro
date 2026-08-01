"""Model C (dual_entity) rail travel sequencing — issues #1115 and #1118.

Two stacked rails hang in one headbox: sheer fabric spans head rail → middle
rail, blackout spans middle rail → bottom rail. They share a track, so neither
rail can pass the other. That makes the arithmetic no-pass clamp in
``resolve_entity_target`` (``M >= P`` on two numbers) necessary but not
sufficient: it says where the two rails will END UP, never where either one
currently IS, and a rail commanded through a space its partner still occupies
tells a motor to travel somewhere it cannot reach — stall, over-current trip,
or lost calibration.

The rail DOWNSTREAM in the direction of travel has to vacate first, so which
rail leads depends on which way the shade is going:

* **lowering** — the bottom rail leads, the middle rail waits until the bottom
  rail's live position has cleared the middle rail's target (#1115);
* **raising** — the middle rail leads, the bottom rail waits until the middle
  rail's live position has cleared the bottom rail's target (#1118).

Two independent mechanisms enforce that, and this module covers both:

* **Ordering** — the leading rail's command must go out first, whatever order
  the user picked the covers in. Owned by
  ``CoverTypePolicy.dispatch_order_key`` and applied by the single shared
  ``order_for_dispatch`` view every dispatch seam consumes. A seam that can name
  the number it is fanning out gets a direction-aware order; one that cannot
  falls back to bottom-first.
* **The clearance gate** — the following rail's command is withheld until the
  leading rail's LIVE position has cleared the follower's target, compared in
  open-percent space against the frame the DISPATCHING SEAM expressed its value
  in. Owned by ``DayNightShadePolicy`` and reached through the one
  ``await_dispatch_clearance`` hook, which ``CoverCommandService.apply_position``
  asks before it books an outbound command and ``run_reconciliation_pass`` asks
  (single-shot) before it re-sends a recorded one. Always authoritative,
  whatever order the seam dispatched in.

Both mechanisms apply to every seam that puts a position on the wire, including
the reconciliation timer — the motor does not care which code path asked.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL,
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_EXTERNAL_COMMAND_INTERLOCK,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_DEFAULT_HEIGHT,
    CONF_ENTITIES,
    CONF_INTERP,
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
_POLICY = "custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy"


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
    blend: int | None,
    middle: str = _MIDDLE,
    inverse: bool = False,
    interp: bool = False,
    concurrent: bool | None = None,
    interlock: bool | None = None,
    policy: DayNightShadePolicy | None = None,
) -> DayNightShadePolicy:
    """Build a real Model C policy with its per-cycle dispatch cache primed.

    Pass ``policy`` to run a LATER resolve cycle on an existing instance —
    everything ``post_pipeline_resolve`` refreshes per cycle, without a fresh
    object.

    ``interp`` turns interpolation on, which is how an install can have
    inverse-state CONFIGURED while ``axis_inverted`` — and therefore the cached
    ``_dual_entity_inverse`` — answers ``False``. That split is what makes the
    seams' "invert iff configured" contract diverge from the install's own
    frame.

    ``concurrent`` writes ``CONF_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL`` explicitly
    and ``interlock`` writes ``CONF_DAY_NIGHT_EXTERNAL_COMMAND_INTERLOCK``;
    ``None`` (the default) leaves the key absent, which is how an upgraded
    install arrives and therefore what the option's own default governs.
    """
    from tests.cover_helpers import make_cover_config, make_vertical_config

    policy = policy if policy is not None else DayNightShadePolicy()
    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    options: dict = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: middle,
    }
    if concurrent is not None:
        options[CONF_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL] = concurrent
    if interlock is not None:
        options[CONF_DAY_NIGHT_EXTERNAL_COMMAND_INTERLOCK] = interlock
    if inverse:
        options[CONF_INVERSE_STATE] = True
    if interp:
        options[CONF_INTERP] = True
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


def test_order_for_dispatch_falls_back_to_bottom_first_without_a_live_reading() -> None:
    """No sequencer and no named target → #1115's shipped constant order.

    Was ``test_order_for_dispatch_puts_bottom_rail_first_under_model_c``. Since
    #1118 the order depends on the direction of travel, which needs both a live
    middle-rail reading and the number the seam is fanning out. This policy has
    neither — it was never ``attach``ed and no ``position=`` is named — so it
    gets the documented conservative fallback, whatever order it was picked in.

    The assertion this used to carry ("bottom first under Model C") is now
    carried, with a live reading and a real direction evaluation behind it, by
    ``test_order_for_dispatch_keeps_bottom_first_when_lowering``; its raise
    counterpart is ``test_order_for_dispatch_puts_middle_rail_first_when_raising``.
    """
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
async def test_dispatch_cycle_commands_middle_rail_before_bottom_when_raising() -> None:
    """A RAISING cycle inverts the rail order (issue #1118).

    The mirror of the test above. The bottom rail sits low at 10 and this cycle
    resolves it up to 60, past a middle rail still parked at 20 — so the middle
    rail is now the rail downstream of the travel and has to vacate first.

    The main dispatch loop can only get that right if it NAMES the number it is
    fanning out (``order_for_dispatch(self.entities, position=state)``). Drop
    that argument and the ordering view falls back to the direction-blind
    constant: this test goes red while every unit-level ordering test stays
    green, which is exactly the regression it exists to catch.

    (``_attach_scripted_rails`` is defined further down, beside the ``_Rails``
    script it wraps — the ordering hook needs a live middle-rail reading, but
    not the whole command-service harness.)
    """
    policy = _dual_policy(position=60, blend=50)
    _attach_scripted_rails(policy, {_BOTTOM: [10], _MIDDLE: [20]})
    coordinator = _ordering_coordinator(policy, entities=[_BOTTOM, _MIDDLE])

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=60, options={}
    )

    assert [eid for eid, _pos in coordinator._cmd_svc.calls] == [_MIDDLE, _BOTTOM]
    # The per-rail arithmetic is untouched by the reordering.
    assert dict(coordinator._cmd_svc.calls) == {_BOTTOM: 60, _MIDDLE: 80}


@pytest.mark.asyncio
async def test_first_refresh_commands_bottom_rail_before_middle() -> None:
    """Startup dispatch rides the same ordered view as the main loop.

    This seam does name its fanned-out target (#1118), but the policy here is
    never ``attach``ed, so the direction check has no live reading to work from
    and lands on the conservative bottom-first fallback.
    """
    policy = _dual_policy(position=40, blend=50)
    coordinator = _ordering_coordinator(policy, entities=[_MIDDLE, _BOTTOM])
    coordinator.manager.is_cover_manual = lambda _eid: False
    coordinator.check_adaptive_time = True
    coordinator._is_reload = False

    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(coordinator, 40, {})

    assert [eid for eid, _pos in coordinator._cmd_svc.calls] == [_BOTTOM, _MIDDLE]


@pytest.mark.asyncio
async def test_force_send_commands_bottom_rail_before_middle() -> None:
    """The force-send seam orders an explicitly-supplied cover subset too.

    This seam does name its fanned-out target (#1118), but the policy here is
    never ``attach``ed, so the direction check has no live reading to work from
    and lands on the conservative bottom-first fallback.
    """
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
    """The My Position button fans its preset out in policy-mandated rail order.

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
    """
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
    """``adaptive_cover_pro.set_position`` over a whole instance orders its fan-out.

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
    """
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

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
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
    """``set_axes`` validates up front, then dispatches in rail order.

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
    """
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
    """A cover group dragging its slider orders each member's own rails.

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
    """
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
    """The group tilt slider rides the same per-member ordered view.

    This seam DOES name its fanned-out target (#1118), but the coordinator here
    is a stub whose policy was never ``attach``ed, so the direction check has no
    live reading to work from and lands on the conservative bottom-first
    fallback — which is what this test is about.
    """
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


def _attach_scripted_rails(policy, script: dict[str, list[int | None]]) -> _Rails:
    """Attach ``policy`` over a scripted motor, with no command service at all.

    The ordering hook reads the middle rail's LIVE position to tell a raise from
    a lower (issue #1118), so even an ordering-only test needs a sequencer. This
    is the cheap half of ``_rail_harness``: rails and an ``attach``, nothing
    else.
    """
    rails = _Rails(script)
    policy.attach(
        hass=MagicMock(),
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=rails.read,
        set_commanded_position=MagicMock(),
        position_tolerance=2,
        is_dry_run=lambda: False,
        entities=[_BOTTOM, _MIDDLE],
    )
    return rails


def _rail_harness(
    *,
    script: dict[str, list[int | None]],
    position: int,
    blend: int | None,
    inverse: bool = False,
    interp: bool = False,
    concurrent: bool | None = None,
):
    """Real ``CoverCommandService`` + real Model C policy over a scripted motor.

    Returns ``(cmd_svc, policy, rails, events)``. ``events`` interleaves the
    gate's position polls (``poll:<entity>``) with the actual service calls
    (``send:<entity>``), which is exactly the ordering the physical constraint
    is about.

    ``concurrent`` is forwarded to :func:`_dual_policy` — ``None`` leaves the
    rail-travel key absent so the option's own default governs.
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

    policy = _dual_policy(
        position=position,
        blend=blend,
        inverse=inverse,
        interp=interp,
        concurrent=concurrent,
    )

    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_day_night_shade",
        # Production shares ONE policy object between the coordinator and this
        # manager; a private instance would answer every rail question with the
        # unprimed default.
        policy=policy,
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        position_tolerance=2,
    )
    cmd_svc._enabled = True
    cmd_svc._get_current_position = MagicMock(side_effect=rails.read)

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


def _auto_control_off_switch(cmd_svc, policy, *, default_position: int):
    """Build the real auto-control switch over a real cmd_svc + Model C policy.

    Reproduces the return-to-default seam end to end: the switch orders the
    rails, remaps the middle one through ``_entity_target(..., inverted=False)``
    and dispatches both through ``apply_position`` — the chokepoint the travel
    gate hangs off. The context carries the install's real ``inverse_state``,
    which is exactly what diverges from the frame this seam dispatches in.
    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        PositionContext,
    )
    from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch

    coord = MagicMock()
    coord.logger = MagicMock()
    coord.entities = [_MIDDLE, _BOTTOM]
    coord._policy = policy
    coord._cmd_svc = cmd_svc
    coord.return_to_default_toggle = True
    coord.automatic_control = False
    coord.manager.manual_controlled = []
    coord.config_entry.options = {CONF_DEFAULT_HEIGHT: default_position}
    coord.async_refresh = AsyncMock()
    coord._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coord
    )
    coord._build_position_context = (
        lambda entity, options, **kw: PositionContext(  # noqa: ARG005
            auto_control=False,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            inverse_state=True,
            force=kw.get("force", False),
            bypass_auto_control=kw.get("bypass_auto_control", False),
            policy=policy,
        )
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch._option_key = None
    switch.schedule_update_ha_state = MagicMock()
    return switch


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


# ---------------------------------------------------------------------------
# The concurrent-rail-travel option is cached by the same hooks the model is
# ---------------------------------------------------------------------------
# The gate runs at the coordinator's dispatch seam, which is handed no
# ``options``. Model C's control model solves that by caching from
# ``sync_runtime_options`` (the coordinator's per-cycle hook) and again from
# ``post_pipeline_resolve`` (so the hook is self-sufficient when a test drives
# it directly). The rail-travel option rides exactly that path — no second
# mechanism, no ``RuntimeConfig`` entry.


def test_concurrent_travel_option_is_cached_by_runtime_hooks() -> None:
    """Both option-reading hooks cache the flag; an absent key reads as ON."""
    off = _dual_policy(position=30, blend=50, concurrent=False)
    assert off._dual_entity_concurrent_travel is False

    on = _dual_policy(position=30, blend=50, concurrent=True)
    assert on._dual_entity_concurrent_travel is True

    # An upgraded install has no key at all — the new behaviour is the default.
    absent = _dual_policy(position=30, blend=50)
    assert absent._dual_entity_concurrent_travel is True

    # The coordinator's own per-cycle hook writes it too, not just the
    # post-pipeline one, and a later cycle can flip it back.
    off.sync_runtime_options(
        {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL: True,
        }
    )
    assert off._dual_entity_concurrent_travel is True
    assert off._control_model == DAY_NIGHT_MODEL_DUAL_ENTITY
    off.sync_runtime_options({CONF_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL: False})
    assert off._dual_entity_concurrent_travel is False


def test_concurrent_travel_defaults_on_for_a_fresh_policy() -> None:
    """Before any options are seen the policy already answers with the default."""
    from custom_components.adaptive_cover_pro.const import (
        DEFAULT_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL,
    )

    assert DEFAULT_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL is True
    assert (
        DayNightShadePolicy()._dual_entity_concurrent_travel
        is DEFAULT_DAY_NIGHT_CONCURRENT_RAIL_TRAVEL
    )


@pytest.mark.asyncio
async def test_middle_rail_waits_for_bottom_rail_to_start_descending(
    monkeypatch,
) -> None:
    """The crash repro: bottom rail stacked ABOVE the middle rail's target.

    Bottom rail live at 100 (fully up), this cycle's targets are bottom 30 /
    middle 65 (blend 50). The middle rail cannot reach 65 while the bottom rail
    still sits at 100 above it — commanding it there stalls the motor. The gate
    must hold the middle rail's ``set_cover_position`` until live readings prove
    the bottom rail is descending out of the way.

    Under the default concurrent-travel policy (#1140) "out of the way" means
    under way and ahead: the two rails share one track at one speed and their
    two TARGETS can never cross, so a bottom rail that has demonstrably started
    down stays ahead for the rest of the move. What the gate withholds against
    is therefore a bottom rail that has not moved at all — which is exactly the
    #1115 state, a rail parked in the space the middle rail is being driven
    into. The full-clearance wait this used to demand is still available and
    pinned by ``test_middle_rail_waits_for_full_clearance_when_concurrent``
    ``_travel_off``.
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
    # The bottom rail's command goes out first, then the gate polls its descent,
    # and only then does the middle rail's command fire.
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}")
    polls_before_send = [
        e for e in events[: events.index(f"send:{_MIDDLE}")] if e == f"poll:{_BOTTOM}"
    ]
    assert len(polls_before_send) >= 2, events


@pytest.mark.asyncio
async def test_middle_rail_waits_for_full_clearance_when_concurrent_travel_off(
    monkeypatch,
) -> None:
    """With the option off, the follower waits out the leader's WHOLE travel.

    The conservative #1115 semantics, kept verbatim behind the switch. The
    bottom rail descends 100 → 90 → 80 → 70 → 60 and only the last reading is
    at-or-below the middle rail's 65 target (+2 tolerance). Start confirmation
    would have released on the 90; full clearance holds all five readings, which
    is what the exact poll count pins.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100, 90, 80, 70, 60], _MIDDLE: [100]},
        position=30,
        blend=50,
        concurrent=False,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    polls_before_send = [
        e for e in events[: events.index(f"send:{_MIDDLE}")] if e == f"poll:{_BOTTOM}"
    ]
    assert len(polls_before_send) == 5, events


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
async def test_bottom_rail_is_not_gated_when_lowering(monkeypatch) -> None:
    """The leading rail moves freely — and on a lowering that is the bottom rail.

    Was ``test_bottom_rail_is_never_gated`` (#1115), when the bottom rail led
    every move by construction. It leads a LOWERING (#1118), so the outcome
    assertion is preserved verbatim: a lowering move never waits on anything.

    What changed is the cost of establishing that. The bottom rail now reads the
    middle rail ONCE to settle the direction, finds no raise collision, and is
    waved straight through — one synchronous read, never a poll loop. The exact
    event list is what pins that: a regression that turned the direction check
    into a wait would show up here as extra polls, and one that dropped the
    check entirely would show up as none.

    The "the bottom rail can be gated" case this used to exclude is covered by
    the raise-direction suite above.
    """
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
    assert events == [f"poll:{_MIDDLE}", f"send:{_BOTTOM}"]


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
# The raise direction — the bottom rail is the follower (issue #1118)
# ---------------------------------------------------------------------------
# #1115 sequenced one direction. The rail DOWNSTREAM in the direction of travel
# has to vacate first, and which rail that is depends on where the shade is
# going: lowering, the bottom rail leads and the middle rail waits; raising, the
# middle rail leads and the BOTTOM rail waits, because its target now sits above
# a middle rail that has not moved out of the way yet.
#
#   | Travel            | Downstream | Leads  | Follower waits until          |
#   |-------------------|------------|--------|-------------------------------|
#   | Lowering (P < p)  | bottom     | bottom | p_open <= M_target_open + tol |
#   | Raising  (P > m)  | middle     | middle | m_open + tol >= P_target_open |
#
# Both mechanisms — ordering and gating — become direction-aware, and both read
# the direction from ONE predicate so they can never disagree about which rail
# leads (which is what would deadlock the pair).


@pytest.mark.asyncio
async def test_bottom_rail_is_gated_when_raising_past_the_middle_rail() -> None:
    """The issue's worked example, at the smallest surface that shows it.

    Bottom rail live at 10, middle rail live at 20 (an intact stack), and this
    cycle resolves the bottom rail up to 60. Travelling there drags it straight
    through the middle rail sitting at 20 — the identical mechanical block
    #1115 closed for the lowering direction, in the opposite direction.

    ``wait=False`` takes the single-shot reading, so this asks the gate's
    verdict without paying the settle budget.
    """
    _cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=60,
        blend=50,
    )

    assert (
        await policy.await_dispatch_clearance(
            _BOTTOM, position=60, reason="solar", wait=False
        )
        is False
    )


def test_order_for_dispatch_puts_middle_rail_first_when_raising() -> None:
    """A raising move commands the middle rail first, whatever the pick order.

    The middle rail is the rail downstream of the travel here, so it is the one
    that has to start vacating before the bottom rail can follow it up.
    """
    _cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=60,
        blend=50,
    )

    assert policy.order_for_dispatch([_BOTTOM, _MIDDLE], position=60) == [
        _MIDDLE,
        _BOTTOM,
    ]
    assert policy.order_for_dispatch([_MIDDLE, _BOTTOM], position=60) == [
        _MIDDLE,
        _BOTTOM,
    ]


def test_order_for_dispatch_keeps_bottom_first_when_lowering() -> None:
    """#1115's order survives, and now for a REASON rather than by construction.

    Same live reading, same evaluation, opposite target: the move lowers, so the
    bottom rail is downstream and leads. A key that answered "bottom first"
    unconditionally would pass this too — which is why its raising counterpart
    above is written against the same harness.
    """
    _cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )

    assert policy.order_for_dispatch([_MIDDLE, _BOTTOM], position=30) == [
        _BOTTOM,
        _MIDDLE,
    ]


def test_order_for_dispatch_falls_back_to_bottom_first_without_a_named_target() -> None:
    """A seam that names no fanned-out target rides the conservative default.

    The reconciliation pass holds a MAP of per-entity targets booked by
    different seams in different eras, not one fanned-out number, so it cannot
    hand the ordering view the pair the gate will later be asked about. It gets
    #1115's shipped constant instead — ordering is a LATENCY mechanism, and the
    gates (which are always authoritative) still withhold anything the fallback
    order sends too early. That deferral is harmless there specifically because
    every gate on the reconciliation path runs ``wait=False``: one reading, no
    settle budget, and the recorded target is still booked for the next pass.

    The empty event log is load-bearing: the fallback must short-circuit before
    it touches the sequencer, or every poll-count assertion in this module
    starts drifting.
    """
    _cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )

    assert policy.order_for_dispatch([_MIDDLE, _BOTTOM]) == [_BOTTOM, _MIDDLE]
    assert events == []


@pytest.mark.asyncio
async def test_a_raising_cycle_dispatches_both_rails_and_neither_deadlocks(
    monkeypatch,
) -> None:
    """End to end: the middle rail leads, the bottom rail waits, both go out.

    The deadlock this design has to avoid is both rails waiting on each other.
    Ordering and gating read the SAME direction predicate, so exactly one rail
    is ever the follower — here the bottom rail, which polls the middle rail's
    ascent (20 → 50 → 80) and only then travels to 60.

    The wall-clock assertion is the deadlock detector: a mutually-blocked pair
    would each burn the full ``VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS``
    budget, which is deliberately left at its real 60 s here.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        # The middle rail climbs once commanded; the bottom rail sits at 10.
        script={_BOTTOM: [10], _MIDDLE: [20, 20, 20, 20, 20, 50, 80]},
        position=60,
        blend=50,
    )
    ctx = _rail_context(policy)

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        for eid in policy.order_for_dispatch([_BOTTOM, _MIDDLE], position=60):
            await cmd_svc.apply_position(
                eid, policy.resolve_entity_target(eid, 60), "solar", ctx
            )
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert f"send:{_MIDDLE}" in events, events
    assert f"send:{_BOTTOM}" in events, events
    assert events.index(f"send:{_MIDDLE}") < events.index(f"send:{_BOTTOM}")
    # Neither rail was withheld, so neither latched pending.
    assert policy.has_pending_secondary_axis(_BOTTOM) is False
    assert policy.has_pending_secondary_axis(_MIDDLE) is False
    # The bottom rail genuinely waited on the ascent rather than sailing
    # through — counted AFTER the middle rail's send, so the ordering view's own
    # direction reads cannot satisfy this on their own.
    ascent = events[events.index(f"send:{_MIDDLE}") : events.index(f"send:{_BOTTOM}")]
    assert len([e for e in ascent if e == f"poll:{_MIDDLE}"]) >= 2, events
    assert (
        len(
            [
                e
                for e in events[: events.index(f"send:{_BOTTOM}")]
                if e == f"poll:{_MIDDLE}"
            ]
        )
        >= 2
    ), events
    # No rail entered the settle budget, i.e. nothing blocked on anything.
    assert elapsed < 10, elapsed


@pytest.mark.asyncio
async def test_bottom_rail_defers_and_latches_pending_on_timeout(
    monkeypatch,
) -> None:
    """A middle rail that never rises defers the bottom command, not sends it.

    The mirror of ``test_middle_rail_defers_and_latches_pending_on_timeout``:
    withheld is a ``policy_deferred`` SKIP and the entity latches pending, so
    the coordinator keeps withholding this cycle's dispatched-target signature
    and the next cycle re-attempts the send.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=60,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_BOTTOM}" not in events
    assert policy.has_pending_secondary_axis(_BOTTOM) is True
    assert cmd_svc.last_skipped_action["reason"] == "policy_deferred"


@pytest.mark.asyncio
async def test_pending_latch_clears_once_the_middle_rail_has_risen(
    monkeypatch,
) -> None:
    """The next cycle's retry sends the bottom command and clears the latch.

    Proves the withheld bottom-rail command rides the SAME
    ``_pending_*`` → ``has_pending_secondary_axis`` → withheld-signature →
    next-cycle-re-dispatch path #1115 built for the middle rail — reused, not
    rebuilt.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=60,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        assert (await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx))[1] == (
            "policy_deferred"
        )
        assert policy.has_pending_secondary_axis(_BOTTOM) is True
        # Cycle 2: the middle rail has climbed to 90 — well clear of 60.
        rails.park(_MIDDLE, 90)
        outcome = await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert policy.has_pending_secondary_axis(_BOTTOM) is False
    assert f"send:{_BOTTOM}" in events


@pytest.mark.asyncio
async def test_bottom_rail_proceeds_when_the_middle_rail_is_unreadable(
    monkeypatch,
) -> None:
    """An unreadable MIDDLE rail is fail-OPEN — the deliberate asymmetry.

    ``_gate_rail_clearance`` is fail-CLOSED on an unreadable bottom rail
    (``test_middle_rail_defers_when_bottom_rail_position_unreadable``, #1115)
    because there the collision geometry is already KNOWN and an unreadable rail
    cannot disprove it. Here the unreadable reading is the collision EVIDENCE
    itself: with no middle-rail position there is nothing to say the bottom rail
    is about to run into anything, and withholding it on every cycle while the
    middle-rail entity is unavailable stops the whole shade — the same
    "withholding forever would brick the shade" reasoning the gate already
    applies to an unresolvable bottom rail.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [None]},
        position=60,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_BOTTOM}" in events
    assert policy.has_pending_secondary_axis(_BOTTOM) is False


@pytest.mark.asyncio
async def test_bottom_rail_proceeds_immediately_when_the_middle_is_already_clear(
    monkeypatch,
) -> None:
    """Steady state on a raise: middle rail already above the target → no wait."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [90]},
        position=60,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    # One confirming read to establish direction, no polling loop.
    assert events.count(f"poll:{_MIDDLE}") == 1, events
    assert events[-1] == f"send:{_BOTTOM}"


# ---------------------------------------------------------------------------
# A raising USER command must not strand the bottom rail
# ---------------------------------------------------------------------------
# The user-command seams are the worst place to lose the rail order, because the
# same press engages manual override. Ordered bottom-first on a RAISE, the bottom
# rail's gate polls a middle rail that nothing has commanded — it cannot move, so
# the gate burns its whole settle budget and then withholds. A withheld command
# is a ``policy_deferred`` SKIP: nothing is booked, so the reconciliation pass
# has no target to resend, and manual override stops the pipeline re-driving it.
# The shade half-moves, hangs for the budget, and stays split until the user
# presses again. Naming the fanned-out number at the seam makes the middle rail
# lead instead, and the whole failure disappears.


def _min_mode_floor(position: int, priority: int):
    """One active min-mode floor, shaped exactly as the pipeline hands it over.

    ``priority`` above :class:`ManualOverrideHandler`'s 80 is what makes the
    floor clamp a user command at all (#472). A weather override with min-mode
    (90) or any custom-position slot configured above 80 produces this.
    """
    from custom_components.adaptive_cover_pro.pipeline.floors import FloorClampInfo

    return FloorClampInfo(
        source="weather_override_min_mode",
        label="Weather override",
        position=position,
        priority=priority,
    )


def _user_seam_coordinator(cmd_svc, policy, *, entities, monkeypatch, floors=()):
    """Build a coordinator over the REAL ``async_apply_user_position`` tail.

    Everything a user command actually crosses on its way to the wire is
    production code: the logical→cover frame mapping, the per-entity middle-rail
    remap, the command service and the policy's travel gate. Only the
    snapshot / pipeline / manager collaborators around the floor clamp are
    stubbed, because none of them has anything to say about the rails.

    ``floors`` declares what the min-mode gatherer finds. The gatherer itself
    has to be stubbed either way — the snapshot here is a ``MagicMock`` and the
    real one would walk it — but everything downstream of it stays production
    code: ``effective_floor``'s max-of-floors composition, the
    outranks-manual-override predicate and the ``max()`` clamp. That arithmetic
    is precisely what the ordering view has to agree with, so it must not be
    stubbed alongside the walk.
    """
    from custom_components.adaptive_cover_pro import coordinator as coordinator_module

    coord = MagicMock()
    coord.logger = MagicMock()
    coord.entities = list(entities)
    coord._policy = policy
    coord._cmd_svc = cmd_svc
    coord._resolved_options = {}
    coord.config_entry.options = {}
    coord._inverse_state = False
    coord._use_interpolation = False
    coord.position_axis_inverted = False
    coord._pipeline.evaluate = MagicMock(
        return_value=types.SimpleNamespace(decision_trace=[])
    )
    coord._build_position_context = lambda _entity, _options, **_kw: _rail_context(
        policy
    )  # noqa: ARG005
    for name in (
        "_entity_target",
        "_to_cover_frame",
        "_build_user_command_snapshot",
        "_clamp_to_active_floor",
        "user_dispatch_position",
        "async_apply_user_position",
    ):
        setattr(
            coord,
            name,
            types.MethodType(getattr(AdaptiveDataUpdateCoordinator, name), coord),
        )
    monkeypatch.setattr(
        coordinator_module, "gather_active_floors", lambda _s: list(floors)
    )
    return coord


@pytest.mark.asyncio
async def test_my_position_button_raise_sends_both_rails_without_stalling(
    monkeypatch,
) -> None:
    """A raising My press must move BOTH rails, and must not wait out a budget.

    End to end through the real button, the real
    ``Coordinator.async_apply_user_position``, the real ``CoverCommandService``
    and the real policy gate — the ordering decision is the seam's own, not a
    hand-rolled ``order_for_dispatch`` call, which is the point: this is the one
    thing that notices if the My button stops naming the number it fans out.

    The middle rail is pinned at 20 until its OWN command goes out, which is what
    makes the failure deterministic rather than a race: dispatched bottom-first,
    the bottom rail's gate polls a rail that physically cannot have moved and
    burns the full ``VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS``, then withholds.
    The budget is deliberately left at its real value — entering it at all is the
    failure this test exists to catch.
    """
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )

    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=60,
        blend=50,
    )

    # A rail only moves once it has been commanded. Until then the middle rail
    # sits where it is, however long anything waits on it.
    scripted_read = rails.read

    def _read(entity_id: str) -> int | None:
        if entity_id == _MIDDLE and f"send:{_MIDDLE}" in events:
            return 90
        return scripted_read(entity_id)

    rails.read = _read

    coord = _user_seam_coordinator(
        cmd_svc, policy, entities=[_MIDDLE, _BOTTOM], monkeypatch=monkeypatch
    )
    button = MagicMock()
    button._entities = [_MIDDLE, _BOTTOM]
    button.config_entry.options = {CONF_MY_POSITION_VALUE: 60}
    button.coordinator = coord

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        await AdaptiveCoverMyPositionButton.async_press(button)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert f"send:{_BOTTOM}" in events, events
    assert f"send:{_MIDDLE}" in events, events
    assert events.index(f"send:{_MIDDLE}") < events.index(f"send:{_BOTTOM}")
    # Nothing was withheld, so nothing is left latched waiting for a retry that
    # manual override has already blocked.
    assert policy.has_pending_secondary_axis(_BOTTOM) is False
    assert elapsed < 10, elapsed


# ---------------------------------------------------------------------------
# The ordering view and the gate must be asked about the SAME number
# ---------------------------------------------------------------------------
# A user command's dispatched value is not the value the user asked for: an
# active min-mode floor that outranks manual override raises it before dispatch
# (#472), and the interpolation curve reshapes it again (#1027). The travel gate
# is asked about the number that actually goes on the wire, so if the ordering
# view is handed the raw request instead, the two can disagree about which way
# the shade is travelling — in EITHER direction:
#
#   * a floor turning a lower into a raise: ordering sees a lower and leads with
#     the bottom rail, whose gate then finds the real (clamped) target above the
#     middle rail and withholds it;
#   * a DESCENDING interpolation curve (np.interp puts no monotonicity on
#     new_range): the floor raises the logical number while the curve lowers the
#     dispatched one, so ordering INVENTS a raise, leads with the middle rail,
#     and stalls the middle rail's unconditional gate instead.
#
# Both are the same bug — two derivations of one number — so both are fixed by
# naming it once. Both cost a full settle budget and then drop the withheld
# rail's command, which at a user seam nothing re-sends.


@pytest.mark.asyncio
async def test_my_position_button_raise_under_a_floor_sends_both_rails(
    monkeypatch,
) -> None:
    """A floor that turns the user's lower into a raise must reorder the rails.

    The My preset is 20 — below the bottom rail's live 10 is a raise of 10
    points, and below the middle rail's live 20 it would need no reordering at
    all. Then a min-mode floor at 80 (priority 90, so it outranks manual
    override and clamps) turns the dispatch into a raise to 80, well past the
    middle rail. The gate sees 80 and withholds the bottom rail; the ordering
    view must see 80 too, or it leads with exactly the rail the gate is about
    to block.

    The settle budget is left at its real value on purpose — entering it at all
    is half the failure, and the withheld command is the other half: a
    ``policy_deferred`` skip books no target, so reconciliation has nothing to
    re-send and the manual override this very press engaged stops the pipeline
    driving it either.
    """
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )

    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20]},
        position=20,
        blend=50,
    )

    # A rail only moves once it has been commanded.
    scripted_read = rails.read

    def _read(entity_id: str) -> int | None:
        if entity_id == _MIDDLE and f"send:{_MIDDLE}" in events:
            return 90
        return scripted_read(entity_id)

    rails.read = _read

    coord = _user_seam_coordinator(
        cmd_svc,
        policy,
        entities=[_MIDDLE, _BOTTOM],
        monkeypatch=monkeypatch,
        floors=[_min_mode_floor(80, priority=90)],
    )
    button = MagicMock()
    button._entities = [_MIDDLE, _BOTTOM]
    button.config_entry.options = {CONF_MY_POSITION_VALUE: 20}
    button.coordinator = coord

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        await AdaptiveCoverMyPositionButton.async_press(button)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert f"send:{_MIDDLE}" in events, events
    assert f"send:{_BOTTOM}" in events, events
    assert events.index(f"send:{_MIDDLE}") < events.index(f"send:{_BOTTOM}")
    # The floor really did clamp — without this the test could pass by the
    # request and the dispatch happening to agree.
    assert cmd_svc.get_target(_BOTTOM) == 80
    assert policy.has_pending_secondary_axis(_BOTTOM) is False
    assert elapsed < 10, elapsed


@pytest.mark.asyncio
async def test_my_position_button_lower_under_a_descending_curve_leads_bottom(
    monkeypatch,
) -> None:
    """A descending interpolation curve must not let the floor invent a raise.

    The mirror of the case above, and the reason the fix is "name one number"
    rather than "apply the clamp at the seam too". Interpolation is the user's
    own calibration table and ``np.interp`` imposes no monotonicity on it, so a
    curve mapping 0→100 and 100→0 is legal. Under it the floor raises the
    LOGICAL request (0 → 80) while the curve lowers the DISPATCHED value
    (100 → 20), and the two derivations point opposite ways.

    Handed the raw request, the ordering view reads 100 — above the middle
    rail's live 95 — and leads with the middle rail on what is really a
    lowering. The middle rail's gate is unconditional (#1115): it waits for the
    bottom rail to descend past its own target, the bottom rail has not been
    commanded yet, and the whole settle budget burns before the middle rail's
    command is dropped.
    """
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )

    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [90], _MIDDLE: [95]},
        position=0,
        blend=50,
    )

    scripted_read = rails.read

    def _read(entity_id: str) -> int | None:
        if entity_id == _BOTTOM and f"send:{_BOTTOM}" in events:
            return 20
        return scripted_read(entity_id)

    rails.read = _read

    coord = _user_seam_coordinator(
        cmd_svc,
        policy,
        entities=[_MIDDLE, _BOTTOM],
        monkeypatch=monkeypatch,
        floors=[_min_mode_floor(80, priority=90)],
    )
    # A descending calibration curve: f(0) = 100, f(80) = 20.
    coord._use_interpolation = True
    coord.start_value = None
    coord.end_value = None
    coord.normal_list = [0, 100]
    coord.new_list = [100, 0]

    button = MagicMock()
    button._entities = [_MIDDLE, _BOTTOM]
    button.config_entry.options = {CONF_MY_POSITION_VALUE: 0}
    button.coordinator = coord

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        await AdaptiveCoverMyPositionButton.async_press(button)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert f"send:{_BOTTOM}" in events, events
    assert f"send:{_MIDDLE}" in events, events
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}")
    # Curve applied to the CLAMPED request, not the raw one (f(80) = 20).
    assert cmd_svc.get_target(_BOTTOM) == 20
    assert policy.has_pending_secondary_axis(_MIDDLE) is False
    assert elapsed < 10, elapsed


# ---------------------------------------------------------------------------
# Exactly one rail is ever withheld — the deadlock tiebreak, made mechanical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_two_rail_gates_are_never_both_armed() -> None:
    """At most one rail is withheld for any pair a single dispatch can produce.

    The theorem the design rests on, over the whole legal state space. The
    bottom rail blocks only when ``m + tol < P``; the middle rail blocks only
    when ``p > M + tol``. Both at once needs
    ``p > M + tol >= P + tol > m + 2·tol`` — i.e. the bottom rail physically
    ABOVE the middle rail — while ``M >= P`` holds for every target pair
    ``resolve_entity_target``'s no-pass clamp can emit. Within one dispatch the
    two gates are therefore mutually exclusive by the same clamp that makes the
    target pair physically legal.

    ``wait=False`` keeps the whole grid single-shot and sub-second.
    """
    for blend in (0, 50, 100):
        policy = _dual_policy(position=50, blend=blend)
        rails = _attach_scripted_rails(policy, {_BOTTOM: [0], _MIDDLE: [0]})
        for p in range(0, 101, 10):
            for m in range(p, 101, 10):  # an intact stack: middle at or above bottom
                rails.park(_BOTTOM, p)
                rails.park(_MIDDLE, m)
                for target in range(0, 101, 10):
                    middle_target = policy.resolve_entity_target(_MIDDLE, target)
                    bottom_ok = await policy.await_dispatch_clearance(
                        _BOTTOM, position=target, reason="t", wait=False
                    )
                    middle_ok = await policy.await_dispatch_clearance(
                        _MIDDLE, position=middle_target, reason="t", wait=False
                    )
                    assert bottom_ok or middle_ok, (p, m, target, middle_target, blend)


@pytest.mark.asyncio
async def test_the_two_rail_gates_are_never_both_armed_under_concurrent_travel() -> (
    None
):
    """The same theorem, over the same grid, with concurrent travel switched on.

    The proof above is stated in terms of ``_cleared``'s inequalities, so the
    relaxed predicate does not inherit it for free — it has to be re-derived,
    and re-derived proofs deserve the same exhaustive check the original got.
    It holds for three reasons: branch selection is untouched by the option; on
    the FIRST reading the concurrent disjunct is structurally ``False`` (there
    is no start reference yet), which is the only reading a ``wait=False`` pass
    takes; and the disjunct is a monotone weakening, so it can release an armed
    gate earlier but never arm one that was not already armed.
    """
    for blend in (0, 50, 100):
        policy = _dual_policy(position=50, blend=blend, concurrent=True)
        rails = _attach_scripted_rails(policy, {_BOTTOM: [0], _MIDDLE: [0]})
        for p in range(0, 101, 10):
            for m in range(p, 101, 10):  # an intact stack: middle at or above bottom
                rails.park(_BOTTOM, p)
                rails.park(_MIDDLE, m)
                for target in range(0, 101, 10):
                    middle_target = policy.resolve_entity_target(_MIDDLE, target)
                    bottom_ok = await policy.await_dispatch_clearance(
                        _BOTTOM, position=target, reason="t", wait=False
                    )
                    middle_ok = await policy.await_dispatch_clearance(
                        _MIDDLE, position=middle_target, reason="t", wait=False
                    )
                    assert bottom_ok or middle_ok, (p, m, target, middle_target, blend)


@pytest.mark.asyncio
async def test_a_lowering_cycle_still_leads_with_the_bottom_rail(monkeypatch) -> None:
    """#1115's whole mechanism survives the generalisation, end to end.

    The mirror of ``test_a_raising_cycle_dispatches_both_rails_and_neither_deadlocks``:
    both rails start stacked at 100 and the cycle lowers them to 30 / 65, so the
    bottom rail leads and the middle rail polls its descent. Same ordering view,
    same gate, opposite roles.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        # Bottom: apply_position's own read, then the gate's descent samples.
        script={_BOTTOM: [100, 100, 100, 80, 60], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        for eid in policy.order_for_dispatch([_MIDDLE, _BOTTOM], position=30):
            await cmd_svc.apply_position(
                eid, policy.resolve_entity_target(eid, 30), "solar", ctx
            )

    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}")
    descent = [
        e for e in events[: events.index(f"send:{_MIDDLE}")] if e == f"poll:{_BOTTOM}"
    ]
    assert len(descent) >= 2, events
    assert policy.has_pending_secondary_axis(_BOTTOM) is False
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


# ---------------------------------------------------------------------------
# Reconciliation resends are a dispatch seam too
# ---------------------------------------------------------------------------
# ``run_reconciliation_pass`` re-sends recorded targets on its own timer, over
# its own loop, through ``_execute_command`` — which never reaches
# ``apply_position``. Both rails drifting off target therefore replay the exact
# #1115 collision unless the pass honours the same order and the same clearance
# the dispatch path does. Opt-in (``enable_position_matching``), but the physics
# do not care how the command was triggered.


def _reconcile_harness(
    *,
    script: dict[str, list[int | None]],
    position: int,
    blend: int | None,
    targets: dict[str, int],
    tokens: dict[str, bool] | None = None,
):
    """Build a ``CoverCommandService`` primed for a reconciliation pass.

    ``targets`` is inserted in the given order, which is the order
    ``iter_targets`` yields — pass the middle rail first to model the user's
    config pick order putting it there.

    Seeding goes through ``set_target``, the single production writer of the
    ``(target, dispatch_token)`` pair, so this harness cannot manufacture a
    state production could not reach. A raw ``s.target = ...`` leaves the stamp
    at ``None``, and every pass built on it quietly exercises the policy's
    no-provenance fallback instead of the stamp mechanism the tests are named
    after (issue #1115).

    ``tokens`` names the frame a rail's target was booked in, for a test
    modelling a seam that dispatched in its own space. A rail left out of it
    gets the stamp ``capture_dispatch_token`` mints right now — which is what a
    dispatch in the install's own frame books.
    """
    cmd_svc, policy, rails, events = _rail_harness(
        script=script, position=position, blend=blend
    )
    cmd_svc._enable_position_matching = True
    cmd_svc._auto_control_enabled = True
    cmd_svc._in_time_window = True
    tokens = tokens or {}
    for entity_id, target in targets.items():
        cmd_svc.set_target(
            entity_id,
            target,
            dispatch_token=tokens.get(
                entity_id, policy.capture_dispatch_token(entity_id)
            ),
        )
        cmd_svc.state(entity_id).waiting = False
    return cmd_svc, policy, rails, events


@pytest.mark.asyncio
async def test_reconciliation_resends_the_bottom_rail_before_the_middle(
    monkeypatch,
) -> None:
    """A reconciliation pass fans its resends out in policy-mandated rail order."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, _policy, _rails, events = _reconcile_harness(
        # Bottom rail already well clear of the middle rail's 65 target.
        script={_BOTTOM: [30], _MIDDLE: [100]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 90},
    )

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_BOTTOM}" in events, events
    assert f"send:{_MIDDLE}" in events, events
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}")


@pytest.mark.asyncio
async def test_reconciliation_withholds_the_middle_rail_until_the_bottom_clears(
    monkeypatch,
) -> None:
    """The stall repro on the reconciliation path (issue #1115).

    Both rails hold recorded targets and the bottom rail has drifted back up to
    95, dragging the middle rail with it. Resending the middle rail's 65 while
    the bottom is still stacked above drives the motor into a mechanical block —
    and ``_is_cover_in_transit`` cannot save it, because a mechanically-dragged
    rail reports ``open``, never ``closing``.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, _policy, _rails, events = _reconcile_harness(
        script={_BOTTOM: [95], _MIDDLE: [95]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 30},
    )

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_BOTTOM}" in events, events
    assert f"send:{_MIDDLE}" not in events, events
    # A withheld resend must not burn one of the pass's limited retries.
    assert cmd_svc.state(_MIDDLE).retry_count == 0


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


@pytest.mark.asyncio
async def test_gate_uses_the_dispatching_seams_frame_not_the_install_flag(
    monkeypatch,
) -> None:
    """Auto-control-OFF on an inverse-state install must still send the middle rail.

    The return-to-default seam dispatches the raw default UN-inverted
    (``_entity_target(..., inverted=False)``) — a deliberate contract locked by
    ``test_auto_control_off_seam_never_inverts_middle_rail``. The
    ``PositionContext`` it builds still carries the install's real
    ``inverse_state=True``, so a gate that flips against THAT reads the middle
    rail's open-space target 80 as open 20 and then waits for a bottom rail that
    is already parked exactly where the same seam put it. The command is
    withheld for the full wait budget and the rail never returns to default.

    The gate must flip against the frame the dispatched value is actually
    expressed in — the one ``_entity_target`` named for this very dispatch.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)

    cmd_svc, policy, _rails, events = _rail_harness(
        # The bottom rail is parked at the un-inverted default this seam sends.
        script={_BOTTOM: [60], _MIDDLE: [100]},
        position=60,
        blend=50,
        inverse=True,
    )
    switch = _auto_control_off_switch(cmd_svc, policy, default_position=60)

    with _patch_caps():
        await switch.async_turn_off()

    # Middle rail open-space target 80 >= bottom rail 60 — already clear.
    assert cmd_svc.get_target(_MIDDLE) == 80
    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


@pytest.mark.asyncio
async def test_raise_gate_compares_in_open_percent_not_raw_wire(monkeypatch) -> None:
    """The raise predicate is an open-percent comparison too (issue #1118).

    Inverse state on. The middle rail reads wire 80 (= open 20, low down) and
    the bottom rail's dispatched wire target is 40 (= open 60, high up). Raw
    wire says ``80 >= 40`` — "the middle rail is already above the target,
    proceed" — and drives the bottom rail straight into it. Open-percent says
    ``20 < 60`` and withholds.

    The whole point is that this rides the SAME ``_dispatch_frame``/``flip_if``
    path the lowering predicate uses; a second, divergent inversion computation
    would reintroduce the #993 bug class in the new direction.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [90], _MIDDLE: [80]},
        position=60,
        blend=50,
        inverse=True,
    )
    ctx = _rail_context(policy, inverse=True)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 40, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_BOTTOM}" not in events


@pytest.mark.asyncio
async def test_raise_gate_uses_the_dispatching_seams_frame_not_the_install_flag(
    monkeypatch,
) -> None:
    """The bottom rail's gate un-transforms against the SEAM's frame (issue #1118).

    Inverse-state install, auto-control toggled OFF. The return-to-default seam
    dispatches the raw default UN-inverted (``_entity_target(..., inverted=False)``),
    and the middle rail is parked at the un-inverted 80 that same seam put it
    at. In the seam's frame the middle rail's open 80 is comfortably above the
    bottom rail's open-60 target, so there is no collision and the command goes
    out.

    Resolve it against the install flag instead and the numbers swap — middle
    open 20 under a bottom target of open 40 — so the rail is withheld, and
    withheld forever, because only that seam ever refreshes the target. That is
    why ``capture_dispatch_token`` has to stamp the BOTTOM rail as well: with no
    stamp the gate falls back to the cached ``axis_inverted`` flag, which is the
    #993 bug class pointed at the new direction.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [80]},
        position=60,
        blend=50,
        inverse=True,
    )
    switch = _auto_control_off_switch(cmd_svc, policy, default_position=60)

    with _patch_caps():
        await switch.async_turn_off()

    assert f"send:{_BOTTOM}" in events, events
    assert policy.has_pending_secondary_axis(_BOTTOM) is False


# ---------------------------------------------------------------------------
# Concurrent rail travel — the follower starts once the leader is under way
# ---------------------------------------------------------------------------
# Both rails hang on one track and travel at one speed, and the no-pass clamp
# already guarantees their two TARGETS never cross. So the moment the leader is
# under way AND ahead of the follower in the direction of travel, the separation
# holds for the whole remaining move: there is nothing left for the follower to
# wait for, and waiting doubles the time the shade takes to reach position.
#
# "Under way and ahead" is the leader's live OPEN-PERCENT reading having moved
# from its first sampled value, by more than the position tolerance, TOWARD
# satisfying that branch's own ``_cleared`` inequality. The direction comes from
# the branch's own release predicate, never from ``_dual_entity_middle_leads`` —
# consulting it from the middle rail's branch is the #1115 back door the
# asymmetry at ``policy.py``'s gate docstring exists to keep shut.


@pytest.mark.asyncio
async def test_middle_rail_starts_once_bottom_rail_is_underway(monkeypatch) -> None:
    """The middle rail is released by MOTION, not by full clearance.

    Same crash geometry as ``test_middle_rail_waits_for_bottom_rail_to_descend``
    ``_past_it``: bottom rail stacked at 100, middle target 65. The scripted
    descent stops at 90 and stays there — it NEVER reaches the 67 that the
    full-clearance predicate needs. Under the serialized gate that is a timeout
    and a deferral; with concurrent travel the 10-point descent is proof the
    bottom rail is under way and ahead, so the middle rail's command goes out.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100, 90], _MIDDLE: [100]},
        position=30,
        blend=50,
        concurrent=True,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False
    # Two readings minimum: the first only establishes the start reference.
    polls = [
        e for e in events[: events.index(f"send:{_MIDDLE}")] if e == f"poll:{_BOTTOM}"
    ]
    assert len(polls) >= 2, events


@pytest.mark.asyncio
async def test_bottom_rail_starts_once_middle_rail_is_underway(monkeypatch) -> None:
    """The raise mirror: the bottom rail is released by the middle rail's ascent.

    Bottom rail live at 10 with a target of 60, middle rail live at 20 — the
    #1118 collision. The middle rail rises to 30 and stops, never reaching the
    58 full clearance would demand, and the bottom rail's command still goes
    out.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        # First middle read settles the direction; the rest are the gate's.
        script={_BOTTOM: [10], _MIDDLE: [20, 20, 30]},
        position=60,
        blend=50,
        concurrent=True,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_BOTTOM, 60, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_BOTTOM}" in events, events
    assert policy.has_pending_secondary_axis(_BOTTOM) is False


@pytest.mark.asyncio
async def test_wrong_direction_motion_keeps_the_middle_rail_gated(monkeypatch) -> None:
    """The #1115 back door, nailed shut: motion the WRONG way never releases.

    A lowering cycle with the bottom rail RISING — nudged up by a user or a
    stray command while the cycle is in flight. It moves, and moves by far more
    than the tolerance, but every step takes it further from vacating the middle
    rail's target. A bare "has the leader started moving at all" predicate would
    wave the middle rail straight into it; the direction-signed one must not.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [70, 80, 90, 100], _MIDDLE: [100]},
        position=30,
        blend=50,
        concurrent=True,
    )
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events
    assert policy.has_pending_secondary_axis(_MIDDLE) is True


@pytest.mark.asyncio
async def test_start_confirmation_times_out_and_latches(monkeypatch) -> None:
    """A leader that reports nothing new falls into today's exact fail-closed path.

    The start confirmation gets its own short budget, not the settle cap: the
    cap is left at 2 s here and the confirmation timeout patched to 0.05 s, so a
    wait that honours the settle cap instead would take more than a second. The
    outcome on expiry is unchanged — ``policy_deferred``, latched pending, retried
    next cycle. No new way to hang.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(f"{_POLICY}.DAY_NIGHT_RAIL_START_CONFIRM_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
        concurrent=True,
    )
    ctx = _rail_context(policy)

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events
    assert policy.has_pending_secondary_axis(_MIDDLE) is True
    assert cmd_svc.last_skipped_action["reason"] == "policy_deferred"
    assert elapsed < 1.0, elapsed


@pytest.mark.asyncio
async def test_start_confirmation_compares_in_open_percent_not_raw_wire(
    monkeypatch,
) -> None:
    """The motion delta rides the same open-percent frame the threshold does.

    Inverse state on, so the wire runs backwards: the bottom rail's readings
    CLIMB from wire 10 to wire 25 while its open percent FALLS from 90 to 75 —
    a genuine descent toward the middle rail's open-65 target, which it never
    actually reaches. A delta computed on raw wire numbers gets the sign exactly
    backwards, reads a descent as a retreat, and withholds a command that is
    already safe (the #993 bug class, pointed at the new predicate).
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [10, 25], _MIDDLE: [50]},
        position=70,  # wire 70 == open 30 for the bottom rail
        blend=50,
        inverse=True,
        concurrent=True,
    )
    ctx = _rail_context(policy, inverse=True)
    wire_middle = policy.resolve_entity_target(_MIDDLE, 70)
    assert wire_middle == 35  # open 65 on an inverse install

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, wire_middle, "solar", ctx)

    assert outcome == ("sent", "set_cover_position")
    assert f"send:{_MIDDLE}" in events, events


@pytest.mark.asyncio
async def test_start_confirmation_needs_two_samples() -> None:
    """One reading can never confirm motion — which is what keeps arming intact.

    The mutual-exclusion theorem is a statement about when a gate ARMS, and the
    relaxation must not touch that. It doesn't, structurally: on the first read
    there is no start reference yet, so the concurrent disjunct is ``False`` and
    the verdict is exactly ``_cleared``'s. The single-shot reconciliation path
    is therefore byte-for-byte today's, and — because the reference lives in a
    per-invocation closure — a second single-shot pass cannot inherit the first
    one's sample and release on a delta that spans two cycles.
    """
    _cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [10], _MIDDLE: [20, 30, 40]},
        position=60,
        blend=50,
        concurrent=True,
    )

    for _pass in range(3):
        assert (
            await policy.await_dispatch_clearance(
                _BOTTOM, position=60, reason="t", wait=False
            )
            is False
        )


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
# The settle cap is a CEILING, not merely a fallback (issue #1140)
# ---------------------------------------------------------------------------
# ``wait_until_position`` used to treat an explicit ``timeout_seconds`` as the
# whole answer and only reach for ``VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS``
# when the caller passed ``None``. The start-confirmation gate introduces the
# first caller that passes a real number, and ~19 existing tests shrink the
# settle cap via ``monkeypatch`` to keep the suite sub-second. If the explicit
# number won outright, every one of those tests would silently start paying the
# real 10 s budget. The cap therefore has to bound BOTH forms.


def _bare_sequencer(read):
    """Build a ``DualAxisSequencer`` wired to nothing but a position reader."""
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        DualAxisSequencer,
    )

    return DualAxisSequencer(
        hass=MagicMock(),
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=read,
        set_commanded_position=MagicMock(),
        position_tolerance=2,
        is_dry_run=lambda: False,
    )


@pytest.mark.asyncio
async def test_wait_until_position_explicit_timeout_is_clamped_by_settle_cap(
    monkeypatch,
) -> None:
    """An explicit budget above the settle cap is clamped down to the cap.

    The predicate never accepts, so the wait can only end on the deadline. With
    the cap patched to 0.05 s, a caller asking for 10 s must still give up
    almost immediately — proof the cap bounds an explicit number rather than
    only filling in for ``None``.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    seq = _bare_sequencer(lambda _eid: 50)

    started = dt.datetime.now(dt.UTC)
    cleared = await seq.wait_until_position(
        _BOTTOM, lambda _pos: False, timeout_seconds=10.0
    )
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert cleared is False
    assert elapsed < 1.0, elapsed


@pytest.mark.asyncio
async def test_wait_until_position_explicit_timeout_below_the_cap_is_honoured(
    monkeypatch,
) -> None:
    """A budget under the cap still wins — the clamp is a ``min``, not a swap."""
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 30.0)
    seq = _bare_sequencer(lambda _eid: 50)

    started = dt.datetime.now(dt.UTC)
    assert (
        await seq.wait_until_position(_BOTTOM, lambda _pos: False, timeout_seconds=0)
        is False
    )
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    assert elapsed < 1.0, elapsed


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
        "_execute_command",  # reconciliation's resend, which BYPASSES the above
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
        "managers/cover_command/__init__.py",
        "switch.py",
        "services/set_position_service.py",
        "services/set_tilt_service.py",
        "services/set_axes_service.py",
        "state/window_transition_tracker.py",
    }
)

# (module path relative to the production root, innermost enclosing function) →
# (the test in THIS module that covers the compensating control, why that seam
# legitimately does not call ``order_for_dispatch`` itself).
#
# The second half of that pair is not documentation. An exemption moves the
# ordering obligation onto some OTHER function, and nothing in this scan can see
# whether that function still honours it — so each exemption must name a test
# that does. See test_every_ordering_exemption_names_a_test_that_covers_its_caller.
_ORDERING_EXEMPT = {
    ("state/window_transition_tracker.py", "check_sunset_window"): (
        "test_sunset_window_transition_hands_the_tracker_an_ordered_list",
        "Receives an already-ordered entity list. The tracker is HA-boundary "
        "code with no policy handle, so the coordinator applies "
        "order_for_dispatch at the call site "
        "(_check_sunset_window_transition, entities=...).",
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


@pytest.mark.unit
def test_every_ordering_exemption_names_a_test_that_covers_its_caller() -> None:
    """An exemption's claimed compensating control must itself be covered.

    ``_ORDERING_EXEMPT`` silences the structural scan for a seam whose CALLER
    does the ordering on its behalf. Nothing in the scan can check that claim —
    so without a behavioural test on the caller, deleting its
    ``order_for_dispatch(...)`` wrap leaves the entire suite green while the rail
    order is silently gone. Every exemption therefore has to name a test in this
    module that exercises the caller, and that name has to resolve.
    """
    unproven = {
        f"{module}::{fn_name}": covered_by
        for (module, fn_name), (covered_by, _reason) in _ORDERING_EXEMPT.items()
        if not callable(globals().get(covered_by))
    }
    assert not unproven, (
        "_ORDERING_EXEMPT entries whose compensating control names a test that "
        "does not exist in this module — write it, or drop the exemption and "
        f"order the seam itself: {unproven}"
    )


# ---------------------------------------------------------------------------
# Structural guard — a seam that CAN name its fanned-out target must do so
# ---------------------------------------------------------------------------
# The scan above proves every fan-out loop consumes the ordered view. Since
# #1118 that view also needs to know WHICH WAY the shade is travelling, and the
# only honest source for that is the number the seam is about to dispatch. Four
# of the five wired call sites are invisible to every behavioural test in this
# module — delete ``position=state`` from the startup, force-send, end-time or
# auto-off seam and the whole suite stays green while a raise silently stalls
# for a settle budget at that seam. Same class of hole the scan above was
# written to close, one argument deeper.
#
# (module path relative to the production root, innermost enclosing function) →
# why this seam legitimately cannot name the number it is fanning out.
_UNNAMED_TARGET_SEAMS = {
    ("managers/cover_command/__init__.py", "run_reconciliation_pass"): (
        "Holds a MAP of per-entity targets booked by different seams in "
        "different eras, not one fanned-out number. Harmless: every gate on "
        "this path runs wait=False, so a mis-ordered rail takes ONE reading, "
        "defers for a tick and is re-asked by the next pass — it never enters "
        "a settle budget and the recorded target is still there to resend."
    ),
}


def _order_view_calls(tree: ast.AST) -> list[tuple[ast.Call, ast.AST | None]]:
    """Every ``order_for_dispatch(...)`` call, with its enclosing function."""
    found: list[tuple[ast.Call, ast.AST | None]] = []

    def walk(node: ast.AST, enclosing: ast.AST | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            enclosing = node
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == _ORDER_VIEW
        ):
            found.append((node, enclosing))
        for child in ast.iter_child_nodes(node):
            walk(child, enclosing)

    walk(tree, None)
    return found


@pytest.mark.unit
def test_pipeline_dispatch_seams_name_their_fanned_out_target() -> None:
    """Fail if a seam that holds its dispatched number orders without naming it.

    Pass ``position=`` (and ``inverted=`` where the seam dispatches in its own
    divergent space) — the SAME pair the loop body hands ``_entity_target``.
    Without it the ordering view cannot tell a raise from a lower and falls back
    to bottom-first, which on a raising cycle commands the follower rail into a
    middle rail that has not vacated yet: the command is correctly withheld by
    the gate, but the shade stalls for a whole cycle at that seam.

    If a seam genuinely has no such pair — because the per-entity value is only
    framed further down its own call chain — add it to
    ``_UNNAMED_TARGET_SEAMS`` with the reason. Inventing a pair there would buy
    ordering latency at the cost of naming a number that is not the one being
    dispatched, which is the exact provenance mistake #1115 closed.
    """
    offenders: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(_PRODUCTION_ROOT).as_posix()
        for call, enclosing in _order_view_calls(tree):
            fn_name = getattr(enclosing, "name", "<module>")
            seen.add((module, fn_name))
            if any(kw.arg == "position" for kw in call.keywords):
                continue
            if (module, fn_name) in _UNNAMED_TARGET_SEAMS:
                continue
            offenders.append(
                f"{path.relative_to(_REPO_ROOT)}:{call.lineno} ({fn_name})"
            )

    stale = set(_UNNAMED_TARGET_SEAMS) - seen
    assert not stale, (
        "_UNNAMED_TARGET_SEAMS names call sites that no longer exist — the seam "
        f"moved or was wired up; drop the entry: {sorted(stale)}"
    )
    assert not offenders, (
        "Dispatch seams that order without naming the number they are fanning "
        "out (issue #1118) — pass position=<the value _entity_target gets>, or "
        "add the seam to _UNNAMED_TARGET_SEAMS with its reason:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Structural guard — a USER-command seam must name its CLAMPED target
# ---------------------------------------------------------------------------
# The scan above proves every seam that can name a number does. This one proves
# the number is the right one. A user command's dispatched value is not the
# value the user asked for: ``async_apply_user_position`` raises it to any
# min-mode floor outranking manual override (#472) before mapping it into the
# cover's frame. The gate is asked about the result; an ordering view handed the
# raw request can therefore read the direction backwards — a floor turns a lower
# into a raise, and a descending interpolation curve turns the clamp itself into
# an invented raise. Both strand a rail for a settle budget and then drop its
# command, which at a user seam nothing re-sends.
#
# ``Coordinator.user_dispatch_position`` is the shared derivation. Only the My
# button is covered end to end (both directions, above); the other five sites
# would regress in silence.
_USER_COMMAND_ENTRY_POINTS = frozenset(
    {"async_apply_user_position", "async_apply_user_tilt", "async_apply_user_axis"}
)
_USER_DISPATCH_ACCESSOR = "user_dispatch_position"

# (module path relative to the production root, innermost enclosing function).
_USER_COMMAND_SEAMS = frozenset(
    {
        ("button.py", "async_press"),
        ("services/set_position_service.py", "async_handle_set_position"),
        ("services/set_tilt_service.py", "async_handle_set_tilt"),
        ("services/set_axes_service.py", "async_handle_set_axes"),
        ("group_coordinator.py", "_member"),
    }
)


@pytest.mark.unit
def test_user_command_seams_name_the_number_they_will_actually_dispatch() -> None:
    """Fail if a user-command seam orders on the request instead of the dispatch.

    A seam whose fan-out ends in ``async_apply_user_position`` / ``_tilt`` /
    ``_axis`` must derive its ``position=`` through
    ``Coordinator.user_dispatch_position`` — the same clamp and the same frame
    mapping that tail runs. Calling ``_to_cover_frame`` directly reproduces the
    frame half and drops the floor clamp, which is exactly enough to make the
    ordering view and the travel gate disagree about the direction of travel.

    On a group seam the accessor has to be the MEMBER coordinator's: the floors
    and the frame both belong to the instance being commanded, not to the group.
    That is not checkable structurally, which is why it is stated here.
    """
    offenders: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(_PRODUCTION_ROOT).as_posix()
        for call, enclosing in _order_view_calls(tree):
            if enclosing is None or not (
                _called_names(enclosing) & _USER_COMMAND_ENTRY_POINTS
            ):
                continue
            fn_name = enclosing.name
            seen.add((module, fn_name))
            named = next(
                (kw.value for kw in call.keywords if kw.arg == "position"), None
            )
            if named is None or _USER_DISPATCH_ACCESSOR not in _called_names(named):
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT)}:{call.lineno} ({fn_name})"
                )

    missing = _USER_COMMAND_SEAMS - seen
    assert not missing, (
        "The user-command seam scan found no ordered fan-out in: "
        f"{sorted(missing)}. Either the seam moved (update _USER_COMMAND_SEAMS) "
        "or it stopped dispatching through a user-command entry point (update "
        "_USER_COMMAND_ENTRY_POINTS) — until then this guard is watching "
        "nothing."
    )
    assert not offenders, (
        "User-command seams ordering on the requested number rather than the "
        "dispatched one (issue #1118) — pass "
        f"position=<coord>.{_USER_DISPATCH_ACCESSOR}(<request>) so the ordering "
        "view and the travel gate are asked about the same value:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.asyncio
async def test_sunset_window_transition_hands_the_tracker_an_ordered_list() -> None:
    """The compensating control behind the one ordering exemption (issue #1115).

    ``WindowTransitionTracker.check_sunset_window`` fans the sunset position out
    in the order it is handed, and has no policy handle of its own — so the
    coordinator has to order the list at the call site. This is the only thing
    that notices when that wrap goes away.
    """
    coord = MagicMock()
    coord.entities = [_MIDDLE, _BOTTOM]
    coord._policy = _dual_policy(position=40, blend=50)
    coord.config_entry.options = {}
    coord._inverse_state = False
    coord._window_tracker.check_sunset_window = AsyncMock()

    await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)

    kwargs = coord._window_tracker.check_sunset_window.await_args.kwargs
    assert kwargs["entities"] == [_BOTTOM, _MIDDLE]


# ---------------------------------------------------------------------------
# A withheld command must leave NOTHING behind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_deferred_skip_records_no_command_state(monkeypatch) -> None:
    """Withholding is a SKIP, so nothing about a sent command may be recorded.

    A ``policy_deferred`` rail never reaches the wire. If the clearance question
    is asked after the outbound command has already been booked, the withheld
    rail is left carrying a tracked target, a ``waiting`` flag, a ``sent_at``
    stamp, an open command-grace window and an ``on_command_sent`` tick — for a
    command that does not exist. Every one of those has a live consequence: the
    grace window suppresses genuine manual-override detection, ``waiting`` makes
    the next reconciliation pass skip the entity, and once ``waiting`` lapses the
    A2 health check raises "cover not moving" about a rail ACP is deliberately
    holding still.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    cmd_svc._on_command_sent = MagicMock()
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)
        # A2 reads the predicate once the transit window has lapsed; force that
        # lapse rather than sleeping for the real backstop.
        cmd_svc._clear_waiting(cmd_svc.state(_MIDDLE))
        unreached = cmd_svc.is_target_unreached(_MIDDLE)

    assert outcome == ("skipped", "policy_deferred")
    assert f"send:{_MIDDLE}" not in events
    assert cmd_svc.get_target(_MIDDLE) is None
    assert cmd_svc.state(_MIDDLE).waiting is False
    assert cmd_svc.state(_MIDDLE).sent_at is None
    cmd_svc._grace_mgr.start_command_grace_period.assert_not_called()
    cmd_svc._on_command_sent.assert_not_called()
    assert unreached is False


@pytest.mark.asyncio
async def test_dry_run_never_waits_on_the_rail_gate(monkeypatch) -> None:
    """A simulated command must not block on a physical clearance it can't cause.

    Dry run books the target so the card display stays honest, then skips
    without touching the wire. Asking the rail gate first would stall the cycle
    for the whole wait budget on a bottom rail nothing is going to move, and
    report ``policy_deferred`` for a command that was never going to be sent.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        # Bottom rail parked above the middle rail's target — the gate would
        # withhold if it ran.
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    cmd_svc.dry_run = True
    ctx = _rail_context(policy)

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, 65, "solar", ctx)

    assert outcome[0] == "skipped"
    assert outcome[1] == "dry_run"
    assert f"poll:{_BOTTOM}" not in events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


# ---------------------------------------------------------------------------
# Reconciliation asks; it never waits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_clearance_check_never_blocks_the_timer() -> None:
    """The reconciliation pass must not hold the timer for the wait budget.

    The gate's budget (``VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS``, 60 s) is
    the reconciliation interval (``POSITION_CHECK_INTERVAL_MINUTES``, 1 min).
    HA re-arms the interval listener before dispatching each fire, and each fire
    is its own background task — so a pass that blocks for its whole budget on a
    middle rail whose bottom rail is pinned (manual override, step 2 skips its
    resend forever) is still running when the next pass starts. Two live passes
    then mutate ``retry_count``, ``last_reconcile_at`` and the pending latch,
    and can both drive the bottom rail.

    A periodic retry loop has no business waiting: it asks "is it clear right
    now?", withholds if not, and re-asks on the next tick. Same eventual
    behaviour, no overlap. The settle constants are deliberately NOT patched
    down here — the point is that the real 60 s budget is never entered.
    """
    cmd_svc, _policy, _rails, events = _reconcile_harness(
        script={_BOTTOM: [95], _MIDDLE: [95]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 30},
    )

    started = dt.datetime.now(dt.UTC)
    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    # One read of the bottom rail, no poll loop.
    assert events.count(f"poll:{_BOTTOM}") == 1, events
    assert elapsed < 10, elapsed
    # ...and the middle rail is still withheld, exactly as a blocking wait
    # would have withheld it.
    assert f"send:{_MIDDLE}" not in events, events
    assert cmd_svc.state(_MIDDLE).retry_count == 0


# ---------------------------------------------------------------------------
# The recorded dispatch frame belongs to the SEAM whose target is being resent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_gate_keeps_the_frame_of_the_seam_it_resends() -> None:
    """A resend rides the frame of the dispatch that recorded the target.

    Reconciliation re-sends the number a broadcast seam put on the wire — so
    that seam's inversion frame is the one the number is expressed in, and the
    only one the gate can legitimately un-transform it against.

    Here the recorded middle-rail target was booked by a seam that named
    ``inverted=True`` explicitly, while this cycle's own ``axis_inverted``
    answer is ``False``. Read in the seam's frame the bottom rail is at the very
    bottom of its travel and the middle rail is clear; read in the install's own
    frame the two swap and the rail is withheld — and, since the seam is the
    only thing that will ever refresh that target, withheld forever.

    Scope, stated plainly: the seam's ``True`` reaches the gate here as BOTH the
    booked stamp and the policy's live per-cycle record, which still agree — so
    this test cannot tell those two apart and does not claim to. What it pins is
    the narrower thing: the resend is gated in the SEAM's frame and not in the
    install's own. Separating stamp from record is the neighbouring
    ``test_reconciliation_resends_when_a_later_resolve_flips_the_cached_frame``,
    which adds the later resolve that moves the record off the stamp.
    """
    cmd_svc, policy, _rails, events = _reconcile_harness(
        # Wire 80 == open 20 in the seam's frame: the bottom rail is low, well
        # clear of a middle rail whose wire-20 target is open 80.
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=20,
        blend=100,  # all sheer → the middle rail's remap is identity
        targets={_MIDDLE: 20},
        # The seam that booked this target dispatched in ITS own frame, so that
        # is the stamp ``capture_dispatch_token`` minted for it.
        tokens={_MIDDLE: True},
    )
    assert policy._dual_entity_inverse is False

    # The broadcast seam's remap: wire 20 is what it put on the wire.
    assert policy.resolve_entity_target(_MIDDLE, 20, inverted=True) == 20

    # A later cycle resolves normally but dispatches nothing (every rail held by
    # a gate upstream of dispatch), so nothing re-books this target.
    _dual_policy(position=20, blend=100, policy=policy)

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


@pytest.mark.asyncio
async def test_gate_reads_the_seam_frame_even_when_no_blend_resolved(
    monkeypatch,
) -> None:
    """A blend-less cycle still dispatches the middle rail, so it still names a frame.

    ``resolve_entity_target`` returns the position untouched when no blend
    resolved — but the rail is dispatched all the same, and the gate that
    follows has to un-transform that value against the frame THIS call named.
    Recording the frame only on the remapping path leaves the gate reading
    whatever the last dispatch (or nothing at all) left behind, which for a seam
    naming a frame that differs from the cached flag is the wrong one.

    Cycle: no blend (a non-solar, non-climate method clears it), inverse-state
    suppressed for the cycle, and a seam dispatching with ``inverted=True``. In
    the seam's frame the bottom rail is low and clear; in the cached frame it is
    stacked on top of the middle rail's target and the command is withheld.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=20,
        blend=None,  # no fabric resolved this cycle
    )
    assert policy._dual_entity_blend is None
    assert policy._dual_entity_inverse is False
    ctx = _rail_context(policy, inverse=True)

    wire = policy.resolve_entity_target(_MIDDLE, 20, inverted=True)
    assert wire == 20  # identity — no blend to fold in

    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, wire, "end_time", ctx)

    assert outcome[0] == "sent", outcome
    assert f"send:{_MIDDLE}" in events, events


# ---------------------------------------------------------------------------
# Provenance: the frame belongs to the BOOKED target, not to the last resolve
# ---------------------------------------------------------------------------
# ``resolve_entity_target`` runs as an ARGUMENT to ``apply_position``, so a cycle
# that resolves and then skips on a delta gate still re-states the policy's
# per-cycle dispatch frame — while the recorded target reconciliation resends was
# booked by an older dispatch, in an older frame. Reading the frame off the policy
# at resend time therefore un-transforms the number against a dispatch that never
# happened. The frame has to travel WITH the booked target (issue #1115).


async def _dispatch_middle_rail(
    cmd_svc,
    policy,
    *,
    position: int,
    inverted: bool,
    context_inverse: bool,
) -> int:
    """Dispatch the middle rail through the real seam and book its target.

    ``resolve_entity_target`` → ``apply_position`` is exactly the production
    chain: the remap names the seam's frame, the command service gates on it and
    then books the resulting wire value. Clears ``waiting`` afterwards so the
    reconciliation pass under test is not skipped as "still in transit".
    """
    wire = policy.resolve_entity_target(_MIDDLE, position, inverted=inverted)
    ctx = _rail_context(policy, inverse=context_inverse)
    with _patch_caps():
        outcome = await cmd_svc.apply_position(_MIDDLE, wire, "end_time", ctx)
    assert outcome[0] == "sent", outcome
    cmd_svc.state(_MIDDLE).waiting = False
    return wire


def _arm_reconciliation(cmd_svc) -> None:
    """Open the reconciliation pass's own gates (matching ``_reconcile_harness``)."""
    cmd_svc._enable_position_matching = True
    cmd_svc._auto_control_enabled = True
    cmd_svc._in_time_window = True


@pytest.mark.asyncio
async def test_reconciliation_never_resends_against_a_frame_the_target_never_used(
    monkeypatch,
) -> None:
    """The dangerous direction: a resolve-then-skip must not unlock a blocked rail.

    Inverse-state install. The auto-control-off return loop dispatches the raw
    default UN-INVERTED and books wire 80 for the middle rail. The bottom rail
    then drifts back up to 95 — stacked well above that target. Automatic control
    comes back on inside the delta window: the main pipeline resolves the middle
    rail in the install's own (inverted) frame and ``apply_position`` skips, so
    nothing goes out but the policy's per-cycle frame is now ``True``.

    Read in that frame the recorded 80 looks like open 20 and the bottom rail's
    95 looks like open 5, so the gate calls a physically blocked rail clear and
    resends into it — the exact mechanical block #1115 exists to prevent, on the
    path added to prevent it. Read in the frame the number was actually
    dispatched in, open 95 is stacked above open 80 and the command is withheld.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, rails, events = _rail_harness(
        script={_BOTTOM: [30], _MIDDLE: [90]},
        position=60,
        blend=50,
        inverse=True,
    )
    assert policy._dual_entity_inverse is True

    # 1. The auto-control-off return loop: un-inverted, bottom rail still low.
    wire = await _dispatch_middle_rail(
        cmd_svc, policy, position=60, inverted=False, context_inverse=True
    )
    assert wire == 80
    assert cmd_svc.get_target(_MIDDLE) == 80

    # 2. The bottom rail drifts back up and stacks above that target.
    rails.park(_BOTTOM, 95)

    # 3. Automatic control returns; the cycle resolves the rail and then skips.
    _dual_policy(position=20, blend=50, inverse=True, policy=policy)
    events.clear()
    policy.resolve_entity_target(_MIDDLE, 20)
    assert events == [], events

    # 4. Reconciliation resends the number the FIRST seam put on the wire.
    _arm_reconciliation(cmd_svc)
    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" not in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is True


@pytest.mark.asyncio
async def test_reconciliation_resends_when_a_later_resolve_flips_the_cached_frame(
    monkeypatch,
) -> None:
    """The regression direction: an unrelated resolve must not strand the rail.

    Same shape as ``test_reconciliation_gate_keeps_the_frame_of_the_seam_it_resends``
    — a seam dispatched the middle rail naming ``inverted=True`` while the cycle's
    cached decision is ``False`` — but with one main-pipeline resolve added that
    never dispatches. Reading the frame off the policy at resend time now flips
    the verdict to "withhold", and since only that seam ever refreshes this
    target, the rail is never reconciled again. Before the gate existed the
    resend simply went out, so withholding here is a NEW regression, not merely
    unfired protection.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        # Wire 80 == open 20 in the seam's frame: the bottom rail is low, well
        # clear of a middle rail whose wire-20 target is open 80.
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=20,
        blend=100,  # all sheer → the middle rail's remap is identity
    )
    assert policy._dual_entity_inverse is False

    wire = await _dispatch_middle_rail(
        cmd_svc, policy, position=20, inverted=True, context_inverse=False
    )
    assert wire == 20
    assert cmd_svc.get_target(_MIDDLE) == 20

    # A later cycle resolves the rail in the cached frame and dispatches nothing.
    _dual_policy(position=20, blend=100, policy=policy)
    events.clear()
    policy.resolve_entity_target(_MIDDLE, 20)
    assert events == [], events

    _arm_reconciliation(cmd_svc)
    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


@pytest.mark.asyncio
async def test_dispatch_provenance_is_written_and_cleared_with_the_target() -> None:
    """The stamp lives and dies with the target it explains.

    Every path that records a target records its provenance in the SAME call,
    and every path that clears one clears the stamp with it. Split those apart
    and a target booked by a path with no dispatch behind it — a rehydrated
    target, an externally-observed My move, a window-close clear — inherits the
    stamp of an older command, and the resend gate un-transforms it against a
    frame it was never expressed in. That is the whole defect (issue #1115),
    just relocated.
    """
    cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=20,
        blend=100,
    )

    # A real dispatch stamps the frame the seam resolved the value in.
    await _dispatch_middle_rail(
        cmd_svc, policy, position=20, inverted=True, context_inverse=False
    )
    assert cmd_svc.state(_MIDDLE).dispatch_token is True

    # A target recorded with no dispatch behind it carries no stamp — it must
    # not inherit the previous command's.
    cmd_svc.set_target(_MIDDLE, 40)
    assert cmd_svc.state(_MIDDLE).dispatch_token is None

    # A My move is a real outbound command but NOT a real dispatch of a number:
    # ``stop_cover`` carries no position, so the recorded target is the user's
    # configured My percent and no seam ever expressed it in a frame. Stamping
    # it with the frame of the last unrelated middle-rail resolve would invent
    # provenance — and freeze it — so it books none.
    policy.resolve_entity_target(_MIDDLE, 20, inverted=False)
    with _patch_caps():
        assert await cmd_svc.send_my_position(_MIDDLE, 55) is True
    assert cmd_svc.state(_MIDDLE).dispatch_token is None

    # Clearing the target clears the stamp with it.
    cmd_svc.state(_MIDDLE).is_safety = False
    cmd_svc.clear_non_safety_targets()
    assert cmd_svc.get_target(_MIDDLE) is None
    assert cmd_svc.state(_MIDDLE).dispatch_token is None

    # ...and discarding the record takes both away outright.
    cmd_svc.set_target(_MIDDLE, 40, dispatch_token=True)
    cmd_svc.discard_target(_MIDDLE)
    assert cmd_svc.state(_MIDDLE).dispatch_token is None


@pytest.mark.asyncio
async def test_my_preset_books_no_frame_so_its_gate_stays_live() -> None:
    """A My preset has no dispatch frame, so it must not be stamped with one.

    ``send_my_position`` sends ``stop_cover``, which carries no position: the
    number it records is the user's configured My percent, and nothing resolved
    it or expressed it in an inversion frame. Stamping it with the frame of the
    last middle-rail RESOLVE attributes it to a dispatch that never produced it
    — and freezes that attribution, so every later resend of that target is
    gated against a frame chosen by an unrelated command.

    Inverse-state Model C install whose last middle-rail resolve came from the
    auto-control-off return loop, a deliberately divergent ``inverted=False``
    space. A frozen ``False`` reads My=30 as open 30 and the bottom rail's 80 as
    open 80, calls a clear rail blocked and withholds the resend — and, since
    only the user's My button ever refreshes that target, withholds it forever.
    With no stamp the gate reads the install's own frame instead, which is the
    frame a raw My percent is genuinely in: My=30 is open 70, the bottom rail is
    open 20, and the resend goes out. The ordinary cycle run at the end is a
    negative control — an unrelated later resolve must not disturb that answer.
    """
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=60,
        blend=50,
        inverse=True,
    )
    assert policy._dual_entity_inverse is True

    # The divergent seam resolves the middle rail un-inverted...
    policy.resolve_entity_target(_MIDDLE, 60, inverted=False)
    # ...and the user's My preset is recorded immediately after it.
    with _patch_caps():
        assert await cmd_svc.send_my_position(_MIDDLE, 30) is True
    assert cmd_svc.get_target(_MIDDLE) == 30
    assert cmd_svc.state(_MIDDLE).dispatch_token is None

    # An ordinary later cycle resolves the rail in the install's own frame.
    _dual_policy(position=20, blend=50, inverse=True, policy=policy)
    policy.resolve_entity_target(_MIDDLE, 20)

    cmd_svc.state(_MIDDLE).waiting = False
    events.clear()
    _arm_reconciliation(cmd_svc)
    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


@pytest.mark.asyncio
async def test_an_unstamped_target_is_gated_in_the_installs_own_frame() -> None:
    """A stamp-less target rides the INSTALL's frame, never the live record.

    ``restore_target``, the coordinator's externally-observed My move and
    ``send_my_position`` each book a raw COVER-frame number — the space
    ``get_current_position`` reports in and ``_at_target`` compares against —
    with no dispatch behind it. The only honest way to map that onto
    open-percent is the install's own ``axis_inverted`` answer, which is what an
    unnamed frame resolves to everywhere else in this policy.

    The live ``_dual_entity_dispatch_inverse`` record is not a substitute, and
    this is the case that separates them. Inverse-state is CONFIGURED but
    suppressed by interpolation, so the install's own frame is ``False`` — while
    the sunset/end-time seam inverts iff inverse-state is CONFIGURED and so
    leaves ``True`` on the record. The coordinator then books My=30 from an
    externally-observed stop, and reconciliation fires before any ordinary
    resolve restates the record.

    Read in the install's frame the bottom rail's 60 is open 60, stacked above
    the middle rail's open-30 target, and the resend must be withheld. Read
    against the record's ``True`` the two swap — open 40 under open 70 — and a
    physically blocked rail is waved through: the mechanical block #1115 exists
    to prevent, reached through the gate added to prevent it.
    """
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [60], _MIDDLE: [90]},
        position=30,
        blend=100,  # all sheer → the middle rail's remap is identity
        inverse=True,
        interp=True,
    )
    assert policy._dual_entity_inverse is False

    # 1. The sunset seam resolves the middle rail in the CONFIGURED frame.
    assert policy.resolve_entity_target(_MIDDLE, 40, inverted=True) == 40
    assert policy._dual_entity_dispatch_inverse is True

    # 2. The coordinator records an externally-observed My move through the one
    #    writer of the pair — no dispatch produced it, so it books no stamp.
    cmd_svc.set_target(_MIDDLE, 30)
    assert cmd_svc.state(_MIDDLE).dispatch_token is None

    cmd_svc.state(_MIDDLE).waiting = False
    events.clear()
    _arm_reconciliation(cmd_svc)
    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" not in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is True


@pytest.mark.asyncio
async def test_a_resend_carries_the_original_dispatch_frame_forward(
    monkeypatch,
) -> None:
    """Re-booking the same number must not re-stamp it with a newer frame.

    A resend routes back through the same booking chokepoint the first dispatch
    used, so it rewrites the recorded target — and would rewrite its provenance
    too if the pass did not carry the original forward. Since the number going
    back on the wire is the one the FIRST dispatch produced, its frame is still
    that dispatch's; re-stamping it with the policy's current per-cycle view
    would simply defer the same mismatch by one pass, and the rail would strand
    on the tick after the one that reconciled it.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [80], _MIDDLE: [90]},
        position=20,
        blend=100,
    )
    await _dispatch_middle_rail(
        cmd_svc, policy, position=20, inverted=True, context_inverse=False
    )
    _dual_policy(position=20, blend=100, policy=policy)
    policy.resolve_entity_target(_MIDDLE, 20)
    _arm_reconciliation(cmd_svc)

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
        # The rail never reaches the target (the script pins it at 90), so the
        # next tick asks again — against a target the resend just re-booked.
        cmd_svc.state(_MIDDLE).waiting = False
        events.clear()
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    assert policy.has_pending_secondary_axis(_MIDDLE) is False


@pytest.mark.asyncio
async def test_reconciliation_resends_the_pair_it_read_not_the_snapshot_target() -> (
    None
):
    """The resent number and the stamp explaining it come from one read.

    The pass takes its target snapshot before the loop starts and then awaits —
    on the clearance gate, and on every earlier rail's own resend. An
    ``apply_position`` landing in one of those windows re-books this entity, and
    pairing the snapshot's target with a stamp read afterwards describes one
    number with another number's frame. ``_prepare_service_call`` then persists
    that mismatched pair as the record the NEXT pass gates against — the #1115
    provenance defect re-entering through the reconciliation loop's own
    bookkeeping.

    The bottom rail's resend is the window here: it re-books the middle rail at
    40 in an explicitly-named frame while the pass is mid-flight. The middle
    rail must go out at 40 wearing that booking's stamp, not at the snapshot's
    65 wearing it. Both numbers clear the gate on the readings scripted below,
    so the verdict is not what separates them — the recorded pair is.

    (A bare ``set_target`` stands in for the concurrent dispatch deliberately: a
    full ``apply_position`` would also set ``waiting``, and step 1 would skip the
    rail before the code under test ran.)
    """
    cmd_svc, _policy, _rails, events = _reconcile_harness(
        script={_BOTTOM: [70], _MIDDLE: [90]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 90},
    )
    payloads: dict[str, dict] = {}
    hass = cmd_svc._hass
    inner = hass.services.async_call.side_effect

    async def _record_and_rebook(domain, service, data, context=None):
        await inner(domain, service, data, context=context)
        payloads[data["entity_id"]] = dict(data)
        if data["entity_id"] == _BOTTOM:
            cmd_svc.set_target(_MIDDLE, 40, dispatch_token=True)

    hass.services.async_call = AsyncMock(side_effect=_record_and_rebook)

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    assert payloads[_MIDDLE]["position"] == 40, payloads
    assert cmd_svc.get_target(_MIDDLE) == 40
    assert cmd_svc.state(_MIDDLE).dispatch_token is True


@pytest.mark.asyncio
async def test_a_rebooking_during_the_gate_cannot_split_the_resent_pair() -> None:
    """The clearance await is a window too, and the resend must survive it intact.

    The gate polls a physical rail, so a dispatch can land between the pass
    reading its pair and the resend being booked. If the booking re-reads the
    stamp at that point it pairs the number it is restating with a frame minted
    for a different one — and that hybrid becomes the record the next pass gates
    against, which is exactly the provenance defect, one loop further in. The
    stamp therefore travels down from the same read the target came from instead
    of being fetched again.

    Which of the two numbers ends up recorded is the pre-existing stale-resend
    question (a resend restates what the pass read); this asserts only that the
    two halves belong to each other.
    """
    cmd_svc, policy, _rails, events = _reconcile_harness(
        script={_BOTTOM: [60], _MIDDLE: [90]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 90},
    )
    payloads: dict[str, dict] = {}
    hass = cmd_svc._hass
    inner = hass.services.async_call.side_effect

    async def _record(domain, service, data, context=None):
        await inner(domain, service, data, context=context)
        payloads[data["entity_id"]] = dict(data)

    hass.services.async_call = AsyncMock(side_effect=_record)

    # A concurrent dispatch re-books the middle rail while the gate is polling
    # the bottom rail — i.e. after the pass read its pair, before it books.
    seq = policy._sequencer
    inner_read = seq._get_current_position

    def _rebook_mid_gate(entity_id: str) -> int | None:
        value = inner_read(entity_id)
        if entity_id == _BOTTOM:
            cmd_svc.set_target(_MIDDLE, 40, dispatch_token=True)
        return value

    seq._get_current_position = _rebook_mid_gate

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_MIDDLE}" in events, events
    recorded = (payloads[_MIDDLE]["position"], cmd_svc.state(_MIDDLE).dispatch_token)
    assert recorded in {(65, False), (40, True)}, recorded


@pytest.mark.asyncio
async def test_reconciliation_skips_a_target_cleared_out_from_under_the_pass() -> None:
    """A target cleared mid-pass is not restated from the snapshot.

    Same window as above, used the other way: the bottom rail's resend clears
    the middle rail's target (a time-window close, a reload) while the pass is
    between its snapshot and the middle rail's turn. There is no longer a number
    to put back on the wire, and reviving the snapshot's would re-book a target
    something deliberately took away.

    The bottom rail is parked low enough that the snapshot's 65 would sail
    through the clearance gate, so nothing but the cleared target stops it.
    """
    cmd_svc, _policy, _rails, events = _reconcile_harness(
        script={_BOTTOM: [60], _MIDDLE: [90]},
        position=30,
        blend=50,
        targets={_MIDDLE: 65, _BOTTOM: 90},
    )
    hass = cmd_svc._hass
    inner = hass.services.async_call.side_effect

    async def _clear_middle_on_bottom_send(domain, service, data, context=None):
        await inner(domain, service, data, context=context)
        if data["entity_id"] == _BOTTOM:
            cmd_svc.set_target(_MIDDLE, None)

    hass.services.async_call = AsyncMock(side_effect=_clear_middle_on_bottom_send)

    with _patch_caps():
        await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    assert f"send:{_BOTTOM}" in events, events
    assert f"send:{_MIDDLE}" not in events, events
    assert cmd_svc.get_target(_MIDDLE) is None


# ---------------------------------------------------------------------------
# The external-command interlock rides the SAME gate (issue #1138)
# ---------------------------------------------------------------------------
# A ``cover.close_cover`` sent straight at one rail never passes through ACP's
# dispatch seam, so nothing orders or gates it — the motor is asked to travel
# into the space its partner occupies and stalls. The correction moves the
# blocking rail and re-issues the user's own command, and the whole point of
# routing it through ``CoverCommandService.apply_position`` rather than
# ``hass.services.async_call`` is that the re-issue meets the same
# ``await_dispatch_clearance`` gate as every other dispatch. A corrective path
# that talked to HA directly would recreate #1115's stall class from inside ACP.


def _interlock_coordinator(cmd_svc, policy):
    """Wire the REAL listener + executor onto a real command service and policy.

    Returns ``(coord, spawned)``. ``spawned`` collects the executor coroutines
    the listener hands to ``config_entry.async_create_task``, so a test can await
    the correction inline instead of racing a background task.
    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        PositionContext,
    )

    spawned: list = []
    coord = MagicMock()
    coord.entities = [_BOTTOM, _MIDDLE]
    coord.logger = MagicMock()
    coord._policy = policy
    coord._cmd_svc = cmd_svc
    # The same fake hass the command service talks to, so a corrective path that
    # reached for ``hass.services.async_call`` directly would still land on the
    # recorder — and be caught by the ordering/gate assertions rather than
    # dying on an un-awaitable mock.
    coord.hass = cmd_svc._hass
    coord.manual_ignore_external = False
    coord.manager = MagicMock()
    coord._event_buffer = MagicMock()
    coord._external_interlock_tasks = {}
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    coord.config_entry.async_create_task = MagicMock(
        side_effect=lambda _hass, coro, name=None: (  # noqa: ARG005
            spawned.append(coro) or MagicMock()
        )
    )
    coord._build_position_context = (
        lambda entity, options, **kw: PositionContext(  # noqa: ARG005
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            inverse_state=False,
            force=kw.get("force", False),
            bypass_auto_control=kw.get("bypass_auto_control", False),
            user_command=kw.get("user_command", False),
            policy=policy,
        )
    )
    for name in ("_plan_external_interlock", "_execute_external_interlock"):
        setattr(
            coord,
            name,
            types.MethodType(getattr(AdaptiveDataUpdateCoordinator, name), coord),
        )
    return coord, spawned


def _external_call_event(service: str, entity_id: str, *, position: int | None = None):
    service_data: dict = {"entity_id": entity_id}
    if position is not None:
        service_data["position"] = position
    event = MagicMock()
    event.data = {
        "domain": "cover",
        "service": service,
        "service_data": service_data,
    }
    event.context = MagicMock()
    event.context.id = "ctx-external-user"
    return event


@pytest.mark.asyncio
async def test_external_close_on_middle_rail_dispatches_bottom_first(
    monkeypatch,
) -> None:
    """The reported repro, corrected end to end through the real gate.

    Bottom rail parked at 100, a user closes the middle rail from a dashboard.
    The bottom rail's command must go out first, the middle rail is stopped (it
    may be latched in ``closing`` from the refused command), and only then is the
    user's own target re-issued — through ``apply_position``, which asks
    ``await_dispatch_clearance`` and therefore polls the bottom rail's descent
    before letting the middle rail move.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100, 100, 100, 90, 0], _MIDDLE: [100]},
        position=30,
        blend=50,
    )

    gate_calls: list[tuple[str, int]] = []
    real_gate = policy.await_dispatch_clearance

    async def _spy(entity_id, **kwargs):
        gate_calls.append((entity_id, kwargs.get("position")))
        return await real_gate(entity_id, **kwargs)

    policy.await_dispatch_clearance = _spy

    hass = cmd_svc._hass
    inner = hass.services.async_call.side_effect
    wire: list[tuple[str, str]] = []

    async def _record(domain, service, data, context=None):
        wire.append((service, data["entity_id"]))
        await inner(domain, service, data, context=context)

    hass.services.async_call = AsyncMock(side_effect=_record)

    coord, spawned = _interlock_coordinator(cmd_svc, policy)

    with _patch_caps():
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
            coord, _external_call_event("close_cover", _MIDDLE)
        )
        assert spawned, "the blocked external command produced no correction"
        await spawned[0]

    # Bottom rail first, then the follower's stop, then the follower's re-issue.
    assert wire[0][1] == _BOTTOM, wire
    assert ("stop_cover", _MIDDLE) in wire, wire
    assert wire.index(("stop_cover", _MIDDLE)) < len(wire) - 1, wire
    assert wire[-1][1] == _MIDDLE and wire[-1][0] != "stop_cover", wire
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}"), events

    # The re-issue went through the gate, and the gate actually waited on the
    # bottom rail's live descent before releasing it.
    assert (_MIDDLE, 0) in gate_calls, gate_calls
    polls_before_middle = [
        e
        for e in events[: len(events) - 1 - events[::-1].index(f"send:{_MIDDLE}")]
        if e == f"poll:{_BOTTOM}"
    ]
    assert len(polls_before_middle) >= 2, events


@pytest.mark.asyncio
async def test_external_interlock_never_arms_both_rail_gates() -> None:
    """The mutual-exclusion theorem holds for the corrective caller too.

    Same grid and same shape as ``test_the_two_rail_gates_are_never_both_armed``,
    but every dispatch pair here is one the external-command interlock itself
    produced. Two things are asserted, and the stronger one is what makes the
    correction terminate: the LEADING rail of any plan is always clear right now,
    so the corrective sequence can never open with a command that is itself
    withheld. Both being blocked would be a deadlock the coordinator would sit
    in; a blocked leader would be a correction that never starts.
    """
    policy = _dual_policy(position=50, blend=50)
    rails = _attach_scripted_rails(policy, {_BOTTOM: [0], _MIDDLE: [0]})
    for p in range(0, 101, 10):
        for m in range(p, 101, 10):  # an intact stack: middle at or above bottom
            rails.park(_BOTTOM, p)
            rails.park(_MIDDLE, m)
            for target in range(0, 101, 10):
                for entity in (_BOTTOM, _MIDDLE):
                    plan = policy.plan_external_command_interlock(
                        entity, service="set_cover_position", wire_target=target
                    )
                    if plan is None:
                        continue
                    lead_ok = await policy.await_dispatch_clearance(
                        plan.leading_entity_id,
                        position=plan.leading_target,
                        reason="t",
                        wait=False,
                    )
                    follow_ok = await policy.await_dispatch_clearance(
                        plan.follower_entity_id,
                        position=plan.follower_target,
                        reason="t",
                        wait=False,
                    )
                    assert lead_ok, (p, m, target, entity)
                    assert lead_ok or follow_ok, (p, m, target, entity)


@pytest.mark.asyncio
async def test_external_interlock_gates_the_correction_in_the_install_frame(
    monkeypatch,
) -> None:
    """A divergent seam must not flip the corrective dispatch's frame (#1138).

    ``plan_external_command_interlock`` decides in ``_dispatch_frame(None)`` —
    the install's own frame, which is the honest answer for a wire number no ACP
    dispatch produced. The corrective dispatches then have to be GATED in that
    same frame. Left to ``capture_dispatch_token`` they are not: that stamp
    replays ``_dual_entity_dispatch_inverse``, the frame of the last rail
    RESOLUTION, and the end-time/sunset loop parks a ``True`` there on an
    interpolation install whose own ``axis_inverted`` answers ``False``.
    Overnight, after ``end_time``, is exactly when an external command arrives.

    Un-flipped in the wrong frame the verdict inverts BOTH ways at once: the
    bottom rail — the plan's leader, which is clear by construction — reads as
    blocked behind a middle rail that is parked and will never move, while the
    middle rail's own gate reads as clear and waves it into the bottom rail it
    cannot pass. That is #1115's stall, re-opened from inside ACP's own
    correction.
    """
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_POLL_SECONDS", 0)
    monkeypatch.setattr(f"{_SEQ}.VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS", 0.05)
    cmd_svc, policy, _rails, events = _rail_harness(
        script={_BOTTOM: [100, 100, 100, 90, 0], _MIDDLE: [100]},
        position=30,
        blend=50,
    )

    # The divergent seam in its own call shape: the end-time/sunset loop
    # resolves every rail with ``inverted=self._inverse_state``, unconditional
    # of interpolation, so it parks a frame that is not the install's own.
    policy.resolve_entity_target(_BOTTOM, 40, inverted=True)
    assert policy._dual_entity_dispatch_inverse is True
    assert policy._dual_entity_inverse is False

    hass = cmd_svc._hass
    inner = hass.services.async_call.side_effect
    wire: list[tuple[str, str]] = []

    async def _record(domain, service, data, context=None):
        wire.append((service, data["entity_id"]))
        await inner(domain, service, data, context=context)

    hass.services.async_call = AsyncMock(side_effect=_record)

    coord, spawned = _interlock_coordinator(cmd_svc, policy)

    with _patch_caps():
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
            coord, _external_call_event("close_cover", _MIDDLE)
        )
        assert spawned, "the blocked external command produced no correction"
        await spawned[0]

    # Leader first, follower's stop, follower's re-issue — the same sequence the
    # non-divergent case produces, because the frame the gate uses is the frame
    # the DECISION used, not whatever a later seam happened to park.
    assert wire and wire[0][1] == _BOTTOM, wire
    assert ("stop_cover", _MIDDLE) in wire, wire
    assert wire[-1][1] == _MIDDLE and wire[-1][0] != "stop_cover", wire
    assert events.index(f"send:{_BOTTOM}") < events.index(f"send:{_MIDDLE}"), events

    # The stamp booked WITH the re-issued target is the install's frame too, so
    # a reconciliation resend of it un-transforms the same way.
    assert cmd_svc.state(_MIDDLE).dispatch_token is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "dry_run"])
async def test_external_interlock_stops_no_rail_when_the_leader_is_withheld(
    mode,
) -> None:
    """A correction that cannot dispatch must not touch the motor at all (#1138).

    ``apply_position`` honours the integration's hard kill switch and dry-run
    mode. ``apply_user_stop`` → ``StopTracker.call_stop_cover`` honours neither
    — it calls ``cover.stop_cover`` unconditionally. So with ACP disabled (or
    simulating) the two position commands are skipped while the STOP is real:
    ACP interrupts the user's in-flight command on the hardware and then never
    completes it. A disabled ACP is exactly when people drive rails by hand, so
    this is ordinary use, not a corner.
    """
    cmd_svc, policy, _rails, _events = _rail_harness(
        script={_BOTTOM: [100], _MIDDLE: [100]},
        position=30,
        blend=50,
    )
    if mode == "disabled":
        cmd_svc._enabled = False
    else:
        cmd_svc._dry_run = True

    hass = cmd_svc._hass
    wire: list[tuple[str, str]] = []

    async def _record(_domain, service, data, context=None):  # noqa: ARG001
        wire.append((service, data["entity_id"]))

    hass.services.async_call = AsyncMock(side_effect=_record)

    coord, spawned = _interlock_coordinator(cmd_svc, policy)

    with _patch_caps():
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
            coord, _external_call_event("close_cover", _MIDDLE)
        )
        assert spawned, "the blocked external command produced no correction"
        await spawned[0]

    assert wire == [], wire
