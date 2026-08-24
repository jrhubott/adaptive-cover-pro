"""External-command rail interlock — issue #1138 (item 2).

A Model C day/night shade hangs two rails in one headbox on a shared track.
ACP's own dispatches are already ordered and gated (#1115 / #1118 / #1140), but
a ``cover.close_cover`` sent straight at one rail — a dashboard tap, a script, a
handheld remote — never passes through that seam. When the partner rail is
parked in the space the commanded rail is being driven into, the motor is asked
to travel somewhere it physically cannot reach: it stalls, latches ``closing``,
and in the reporter's recorder trace stayed there for 41 minutes.

ACP cannot veto the call — ``EVENT_CALL_SERVICE`` fires as the call executes —
so the fix is corrective rather than preventive: observe the external command,
ask the cover-type policy whether the partner rail blocks it, and if so move the
partner out of the way and re-issue the user's own command through the SAME
gated path every other ACP dispatch uses.

Three seams, three sections here:

* the policy's decision — a pure, synchronous ``plan_external_command_interlock``
  returning an :class:`ExternalInterlockPlan` or ``None``;
* the coordinator's listener — which external calls reach the hook at all;
* the coordinator's executor — the order the corrective sequence fires in.

The end-to-end proof that the corrective dispatch is really gated (rather than
merely intended to be) lives in ``tests/test_day_night_rail_sequencing.py``,
beside the mutual-exclusion invariant it must not break.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

pytestmark = pytest.mark.unit

_BOTTOM = "cover.bottom_rail"
_MIDDLE = "cover.middle_rail"
_INTERLOCK_LOGGER = "tests.external_command_interlock"


def _model_c(
    *,
    bottom: int | None = 100,
    middle: int | None = 100,
    inverse: bool = False,
    interlock: bool | None = None,
    attach: bool = True,
):
    """Build a real Model C policy with both rails parked at the given readings.

    Reuses the rail-sequencing module's own ``_dual_policy`` /
    ``_attach_scripted_rails`` builders rather than restating the Model C
    priming here — the decision under test reads exactly the state those two
    set up, and a second copy would be free to drift from it.

    ``attach=False`` skips the sequencer entirely, which is the degenerate
    "no resolvable rail pair" install.
    """
    from tests.test_day_night_rail_sequencing import (
        _attach_scripted_rails,
        _dual_policy,
    )

    policy = _dual_policy(position=40, blend=50, inverse=inverse, interlock=interlock)
    if attach:
        _attach_scripted_rails(policy, {_BOTTOM: [bottom], _MIDDLE: [middle]})
        # A real logger so the disabled-option WARNING is caplog-visible; the
        # shared builder injects a MagicMock, which captures nothing.
        policy._logger = logging.getLogger(_INTERLOCK_LOGGER)
    return policy


# ---------------------------------------------------------------------------
# (a) The base hook — every cover type answers it, only Model C ever says yes
# ---------------------------------------------------------------------------


def test_base_policy_plans_no_interlock() -> None:
    """A cover type with independent entities has no partner rail to move."""
    from custom_components.adaptive_cover_pro.cover_types.blind import BlindPolicy

    assert (
        BlindPolicy().plan_external_command_interlock(
            "cover.living_room", service="close_cover", wire_target=0
        )
        is None
    )


def test_external_interlock_plan_is_a_frozen_slots_dataclass() -> None:
    """The plan is data, not a dict — the repo's dataclass preference."""
    from custom_components.adaptive_cover_pro.cover_types.base import (
        ExternalInterlockPlan,
    )

    plan = ExternalInterlockPlan(
        leading_entity_id="cover.bottom",
        leading_target=0,
        follower_entity_id="cover.middle",
        follower_target=0,
        reason="external_command_interlock",
    )
    assert dataclasses.is_dataclass(plan)
    assert dataclasses.fields(ExternalInterlockPlan)
    assert not hasattr(plan, "__dict__")  # slots=True
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.leading_target = 50


# ---------------------------------------------------------------------------
# (b) The Model C decision — stated as the complement of the clearance gate
# ---------------------------------------------------------------------------
# ``_gate_rail_clearance`` releases the MIDDLE rail when
# ``p_open <= target_open + tol`` and the BOTTOM rail when
# ``_dual_entity_middle_leads`` says no raise collision exists. "Blocked" is
# therefore the negation of each — stated as an inequality rather than by
# inverting ``resolve_entity_target``'s blend arithmetic, which is singular at
# exactly the reported failure (bottom rail at 100, no reachable middle target
# for any blend).


def test_middle_rail_close_blocked_when_bottom_at_full_open() -> None:
    """The reported repro: bottom rail at 100, an external close on the middle.

    ``M = 100 - blend·(100-P)/100`` collapses to 100 for EVERY blend when the
    bottom rail sits at 100, so the inverted-blend form of this question has no
    answer at all. The inequality form does: 100 open percent is above the 0 the
    user asked for, so the bottom rail blocks it, and the minimal move that
    unblocks it is the bottom rail to the same wire number.
    """
    policy = _model_c(bottom=100)

    plan = policy.plan_external_command_interlock(
        _MIDDLE, service="close_cover", wire_target=0
    )

    assert plan is not None
    assert plan.leading_entity_id == _BOTTOM
    assert plan.leading_target == 0
    assert plan.follower_entity_id == _MIDDLE
    assert plan.follower_target == 0
    assert plan.reason


def test_middle_rail_partial_target_below_bottom_is_blocked() -> None:
    """A mid-range ``set_cover_position`` is the same question, not a special case."""
    policy = _model_c(bottom=60)

    plan = policy.plan_external_command_interlock(
        _MIDDLE, service="set_cover_position", wire_target=40
    )

    assert plan is not None
    assert (plan.leading_entity_id, plan.leading_target) == (_BOTTOM, 40)
    assert (plan.follower_entity_id, plan.follower_target) == (_MIDDLE, 40)


def test_middle_rail_target_at_or_above_bottom_is_clear() -> None:
    """No plan when the bottom rail is already out of the way — tolerance included.

    The boundary is the gate's own ``<= target + tol``: at tolerance 2 a bottom
    rail reading 62 still clears a 60 target and 63 does not, so the interlock
    can never fire for a command the gate would have passed.
    """
    assert (
        _model_c(bottom=60).plan_external_command_interlock(
            _MIDDLE, service="set_cover_position", wire_target=60
        )
        is None
    )
    assert (
        _model_c(bottom=62).plan_external_command_interlock(
            _MIDDLE, service="set_cover_position", wire_target=60
        )
        is None
    )
    assert (
        _model_c(bottom=63).plan_external_command_interlock(
            _MIDDLE, service="set_cover_position", wire_target=60
        )
        is not None
    )


def test_bottom_rail_raise_past_middle_is_blocked() -> None:
    """The raise direction (#1118): the middle rail is the one that must vacate."""
    policy = _model_c(middle=30)

    plan = policy.plan_external_command_interlock(
        _BOTTOM, service="open_cover", wire_target=100
    )

    assert plan is not None
    assert (plan.leading_entity_id, plan.leading_target) == (_MIDDLE, 100)
    assert (plan.follower_entity_id, plan.follower_target) == (_BOTTOM, 100)


def test_bottom_rail_lowering_is_never_blocked() -> None:
    """Nothing is downstream of the bottom rail on the way down — #1115's rule."""
    policy = _model_c(middle=90)

    assert (
        policy.plan_external_command_interlock(
            _BOTTOM, service="close_cover", wire_target=0
        )
        is None
    )


def test_external_interlock_middle_rail_decision_ignores_middle_leads(
    monkeypatch,
) -> None:
    """The #1118 direction asymmetry survives the new caller.

    ``_gate_rail_clearance`` deliberately does NOT consult
    ``_dual_entity_middle_leads`` on the middle rail's branch: that predicate
    reads the middle rail's own live position, which during a lowering is the
    number the collision is ABOUT, so consulting it would ungate the very rail
    #1115 exists to hold. The corrective decision is just another caller of the
    same question and must not become the back door — proven by making the
    predicate explode.
    """
    policy = _model_c(bottom=100)

    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "the middle rail's decision must never read the raise-direction "
            "predicate (#1115/#1118 asymmetry)"
        )

    monkeypatch.setattr(policy, "_dual_entity_middle_leads", _boom)

    plan = policy.plan_external_command_interlock(
        _MIDDLE, service="close_cover", wire_target=0
    )
    assert plan is not None
    assert plan.leading_entity_id == _BOTTOM


def test_inverse_install_decision_uses_dispatch_frame() -> None:
    """Both sides of the comparison ride one frame rule (the #993 bug class).

    On an inverse-state install a bottom rail on wire 0 is FULLY OPEN. HA's
    cover services are still device-frame, so ``close_cover`` means wire 0 —
    which is 100 open percent, at-or-above the bottom rail, and therefore clear.
    ``open_cover`` means wire 100 — 0 open percent, below it, and blocked. Get
    the frame wrong and the two verdicts swap.

    The plan's targets stay WIRE numbers either way: they go straight to
    ``apply_position``, whose contract is "already transformed".
    """
    clear = _model_c(bottom=0, inverse=True).plan_external_command_interlock(
        _MIDDLE, service="close_cover", wire_target=0
    )
    assert clear is None

    blocked = _model_c(bottom=0, inverse=True).plan_external_command_interlock(
        _MIDDLE, service="open_cover", wire_target=100
    )
    assert blocked is not None
    assert (blocked.leading_entity_id, blocked.leading_target) == (_BOTTOM, 100)
    assert blocked.follower_target == 100


def test_models_a_b_and_unrelated_entities_return_none() -> None:
    """Only a Model C rail of THIS instance's pair has a partner to interlock with."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_DAY_NIGHT_CONTROL_MODEL,
        DAY_NIGHT_MODEL_POSITION_TILT,
        DAY_NIGHT_MODEL_SPLIT_RANGE,
    )
    from custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy import (  # noqa: E501
        DayNightShadePolicy,
    )

    for model in (DAY_NIGHT_MODEL_POSITION_TILT, DAY_NIGHT_MODEL_SPLIT_RANGE):
        single = DayNightShadePolicy()
        single.sync_runtime_options({CONF_DAY_NIGHT_CONTROL_MODEL: model})
        assert (
            single.plan_external_command_interlock(
                _MIDDLE, service="close_cover", wire_target=0
            )
            is None
        )

    assert (
        _model_c(bottom=100).plan_external_command_interlock(
            "cover.somewhere_else", service="close_cover", wire_target=0
        )
        is None
    )


def test_unresolved_rail_pair_returns_none() -> None:
    """A Model C instance with no sequencer has no live readings to decide on."""
    assert (
        _model_c(attach=False).plan_external_command_interlock(
            _MIDDLE, service="close_cover", wire_target=0
        )
        is None
    )


def test_unreadable_partner_rail_returns_none() -> None:
    """No reading, no evidence of a collision — nothing to correct."""
    assert (
        _model_c(bottom=None).plan_external_command_interlock(
            _MIDDLE, service="close_cover", wire_target=0
        )
        is None
    )


def test_option_off_returns_none_and_logs_warning(caplog) -> None:
    """Opted out: ACP stays out of the way but says why the command won't land."""
    policy = _model_c(bottom=100, interlock=False)

    with caplog.at_level(logging.WARNING, logger=_INTERLOCK_LOGGER):
        plan = policy.plan_external_command_interlock(
            _MIDDLE, service="close_cover", wire_target=0
        )

    assert plan is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, caplog.text
    message = warnings[0].getMessage()
    assert _MIDDLE in message
    assert "day_night_external_command_interlock" in message


def test_plan_stamps_the_frame_its_own_decision_used() -> None:
    """The plan carries its targets' provenance, not whatever cache is parked.

    The decision runs in ``_dispatch_frame(None)`` — the install's own frame,
    the honest answer for a wire number no ACP dispatch produced. The gate that
    later releases the corrective commands has to be handed THAT bool, or it
    falls back to ``capture_dispatch_token``, which replays
    ``_dual_entity_dispatch_inverse`` — the frame of the last rail RESOLUTION,
    which a sunset/end-time seam parks divergent on an interpolation install.
    Decision frame and gate frame must be one bool, minted once, here.
    """
    policy = _model_c(bottom=100)
    # The sunset/end-time loop's own call shape: ``inverted=`` is the install's
    # CONFIGURED inverse-state, unconditional of interpolation.
    policy.resolve_entity_target(_BOTTOM, 40, inverted=True)

    plan = policy.plan_external_command_interlock(
        _MIDDLE, service="close_cover", wire_target=0
    )

    assert plan is not None
    assert plan.dispatch_token is False


def test_option_on_by_default_for_an_upgraded_install() -> None:
    """No stored key at all still plans — the option ships on."""
    from custom_components.adaptive_cover_pro.const import (
        DEFAULT_DAY_NIGHT_EXTERNAL_COMMAND_INTERLOCK,
    )

    assert DEFAULT_DAY_NIGHT_EXTERNAL_COMMAND_INTERLOCK is True
    policy = _model_c(bottom=100, interlock=None)
    assert policy._dual_entity_external_interlock is True
    assert (
        policy.plan_external_command_interlock(
            _MIDDLE, service="close_cover", wire_target=0
        )
        is not None
    )


# ---------------------------------------------------------------------------
# (c) The listener — which external calls reach the hook, and what it spawns
# ---------------------------------------------------------------------------
# ``async_check_cover_service_call`` is driven unbound over a duck-typed self,
# the pattern ``tests/test_manual_ignore_external.py`` already uses for this
# listener: it exercises the real branching without standing up a coordinator.


def _plan(
    follower: str = _MIDDLE,
    leader: str = _BOTTOM,
    target: int = 0,
    dispatch_token: object = False,
):
    from custom_components.adaptive_cover_pro.cover_types.base import (
        ExternalInterlockPlan,
    )

    return ExternalInterlockPlan(
        leading_entity_id=leader,
        leading_target=target,
        follower_entity_id=follower,
        follower_target=target,
        reason="external_command_interlock",
        dispatch_token=dispatch_token,
    )


def _listener_coordinator(*, plan=None, acp_contexts: set[str] | None = None):
    import types
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    # The listener's position branch is a real method, bound onto the duck-typed
    # self the way the rail-sequencing suite binds ``_entity_target``. Left as an
    # auto-mock it would swallow the very dispatch under test. ``_start_interlock_task``
    # has to be real too — it holds the cancel-prior/register block
    # ``_plan_external_interlock`` now delegates to (#1156).
    coordinator._plan_external_interlock = types.MethodType(
        AdaptiveDataUpdateCoordinator._plan_external_interlock, coordinator
    )
    coordinator._start_interlock_task = types.MethodType(
        AdaptiveDataUpdateCoordinator._start_interlock_task, coordinator
    )
    # ``_interlock_pair_key`` is a ``staticmethod``: binding it the way the
    # instance methods above are bound would pass ``coordinator`` as an
    # implicit first argument, shadowing the real ``plan`` argument. A direct
    # attribute assignment keeps it a plain one-argument callable.
    coordinator._interlock_pair_key = AdaptiveDataUpdateCoordinator._interlock_pair_key
    coordinator.entities = [_BOTTOM, _MIDDLE]
    coordinator.logger = MagicMock()
    coordinator._policy = MagicMock()
    coordinator._policy.plan_external_command_interlock = MagicMock(return_value=plan)
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.was_acp_position_context = MagicMock(
        side_effect=lambda cid: cid in (acp_contexts or set())
    )
    coordinator._external_interlock_tasks = {}
    coordinator.config_entry = MagicMock()

    def _spawn_task(*_a, **_kw):
        # A fresh mock per call — not the shared ``.return_value`` — so two
        # calls (a supersede) produce two distinct task objects, the way two
        # real ``asyncio.Task``s would. Also parked onto ``.return_value``
        # itself, so a test can assert identity against
        # ``async_create_task.return_value`` right after the call it cares
        # about, without a mock plumbing detail leaking into the assertion.
        task = MagicMock()
        coordinator.config_entry.async_create_task.return_value = task
        return task

    coordinator.config_entry.async_create_task = MagicMock(side_effect=_spawn_task)
    return coordinator


def _position_event(
    service: str,
    entity_id,
    *,
    position: int | None = None,
    context_id: str = "ctx-external",
    domain: str = "cover",
):
    from unittest.mock import MagicMock

    service_data: dict = {"entity_id": entity_id}
    if position is not None:
        service_data["position"] = position
    event = MagicMock()
    event.data = {
        "domain": domain,
        "service": service,
        "service_data": service_data,
    }
    event.context = MagicMock()
    event.context.id = context_id
    return event


async def _listen(coordinator, event) -> None:
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
        coordinator, event
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "position", "expected_wire"),
    [
        ("close_cover", None, 0),
        ("open_cover", None, 100),
        ("set_cover_position", 35, 35),
    ],
)
async def test_external_position_services_map_to_wire_targets(
    service, position, expected_wire
) -> None:
    """HA's cover services are device-frame, so the mapping is unconditional."""
    coordinator = _listener_coordinator()

    await _listen(coordinator, _position_event(service, _MIDDLE, position=position))

    coordinator._policy.plan_external_command_interlock.assert_called_once_with(
        _MIDDLE, service=service, wire_target=expected_wire
    )


@pytest.mark.asyncio
async def test_list_entity_id_form_is_handled() -> None:
    """``entity_id`` may arrive as a list; only tracked members are asked about."""
    coordinator = _listener_coordinator()

    await _listen(
        coordinator,
        _position_event("close_cover", [_MIDDLE, "cover.untracked"]),
    )

    coordinator._policy.plan_external_command_interlock.assert_called_once_with(
        _MIDDLE, service="close_cover", wire_target=0
    )


@pytest.mark.asyncio
async def test_acp_stamped_position_call_is_ignored() -> None:
    """Loop safety: ACP's own corrective dispatch must not re-trigger the hook.

    ``apply_position`` records its context BEFORE the HA service call runs and
    ``EVENT_CALL_SERVICE`` fires during it, so the stamp is always in place by
    the time this listener sees the echo — no new tracking mechanism needed.
    """
    coordinator = _listener_coordinator(plan=_plan(), acp_contexts={"ctx-from-acp"})

    await _listen(
        coordinator,
        _position_event("close_cover", _MIDDLE, context_id="ctx-from-acp"),
    )

    coordinator._policy.plan_external_command_interlock.assert_not_called()
    assert coordinator._external_interlock_tasks == {}


@pytest.mark.asyncio
async def test_no_plan_spawns_no_task() -> None:
    """An unblocked external command is left entirely alone."""
    coordinator = _listener_coordinator(plan=None)

    await _listen(coordinator, _position_event("close_cover", _MIDDLE))

    coordinator.config_entry.async_create_task.assert_not_called()
    assert coordinator._external_interlock_tasks == {}


@pytest.mark.asyncio
async def test_a_plan_spawns_an_entry_scoped_task_keyed_by_the_pair() -> None:
    """The corrective sequence runs off the listener so gate waits never block it."""
    coordinator = _listener_coordinator(plan=_plan())

    await _listen(coordinator, _position_event("close_cover", _MIDDLE))

    coordinator.config_entry.async_create_task.assert_called_once()
    assert set(coordinator._external_interlock_tasks) == {frozenset({_BOTTOM, _MIDDLE})}
    assert (
        coordinator._external_interlock_tasks[frozenset({_BOTTOM, _MIDDLE})]
        is coordinator.config_entry.async_create_task.return_value
    )


@pytest.mark.asyncio
async def test_second_plan_cancels_prior_task_for_the_same_pair() -> None:
    """Last writer wins: two genuine commands in a row must not race each other."""
    coordinator = _listener_coordinator(plan=_plan())

    await _listen(coordinator, _position_event("close_cover", _MIDDLE))
    first = coordinator._external_interlock_tasks[frozenset({_BOTTOM, _MIDDLE})]
    first.done.return_value = False

    await _listen(coordinator, _position_event("open_cover", _MIDDLE))
    second = coordinator._external_interlock_tasks[frozenset({_BOTTOM, _MIDDLE})]

    first.cancel.assert_called_once()
    assert second is not first


@pytest.mark.asyncio
async def test_a_correction_on_either_rail_supersedes_the_other() -> None:
    """The registry keys on the PAIR, not the follower (#1144 item 2 / #1156).

    A command aimed at the middle rail and a command aimed at the bottom rail
    plan two DIFFERENT follower entities, but they correct the SAME two
    physical rails. Follower-only keying let both corrections live at once —
    the registry has to supersede across the pair regardless of which rail's
    command named the follower.
    """
    coordinator = _listener_coordinator(plan=_plan(follower=_MIDDLE, leader=_BOTTOM))

    await _listen(coordinator, _position_event("close_cover", _MIDDLE))
    first = next(iter(coordinator._external_interlock_tasks.values()))
    first.done.return_value = False

    coordinator._policy.plan_external_command_interlock.return_value = _plan(
        follower=_BOTTOM, leader=_MIDDLE
    )
    await _listen(coordinator, _position_event("open_cover", _BOTTOM))

    first.cancel.assert_called_once()
    assert set(coordinator._external_interlock_tasks) == {frozenset({_BOTTOM, _MIDDLE})}


@pytest.mark.asyncio
async def test_a_finished_prior_task_is_not_cancelled() -> None:
    """Nothing to cancel once the previous correction has already completed."""
    coordinator = _listener_coordinator(plan=_plan())

    await _listen(coordinator, _position_event("close_cover", _MIDDLE))
    first = coordinator._external_interlock_tasks[frozenset({_BOTTOM, _MIDDLE})]
    first.done.return_value = True

    await _listen(coordinator, _position_event("open_cover", _MIDDLE))

    first.cancel.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_kwargs",
    [
        {"domain": "light"},
        {"entity_id": "cover.untracked"},
        {"service": "toggle"},
    ],
)
async def test_irrelevant_calls_never_reach_the_hook(event_kwargs) -> None:
    """Domain, service and tracked-entity filters are shared with the stop branch."""
    coordinator = _listener_coordinator(plan=_plan())
    service = event_kwargs.pop("service", "close_cover")
    entity_id = event_kwargs.pop("entity_id", _MIDDLE)

    await _listen(coordinator, _position_event(service, entity_id, **event_kwargs))

    coordinator._policy.plan_external_command_interlock.assert_not_called()
    coordinator.config_entry.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_call_without_an_entity_id_is_ignored() -> None:
    """The shared parse bails before either branch when nothing was named."""
    coordinator = _listener_coordinator(plan=_plan())
    event = _position_event("close_cover", _MIDDLE)
    del event.data["service_data"]["entity_id"]

    await _listen(coordinator, event)

    coordinator._policy.plan_external_command_interlock.assert_not_called()


@pytest.mark.asyncio
async def test_set_cover_position_without_a_position_is_ignored() -> None:
    """A malformed call names no target to compare anything against."""
    coordinator = _listener_coordinator(plan=_plan())

    await _listen(coordinator, _position_event("set_cover_position", _MIDDLE))

    coordinator._policy.plan_external_command_interlock.assert_not_called()


# ---------------------------------------------------------------------------
# (d) The executor — the order the corrective sequence fires in
# ---------------------------------------------------------------------------
# The whole point of routing through ``_cmd_svc`` is that BOTH dispatches meet
# ``await_dispatch_clearance``. Nothing here may call ``hass.services.async_call``
# directly: that is exactly how #1115's stall class comes back, this time
# triggered by ACP's own corrective command. That the routing really is gated is
# proven end to end in ``tests/test_day_night_rail_sequencing.py``.


def _executor_coordinator(
    *,
    manual_ignore_external: bool = False,
    leader_outcome: tuple[str, str] = ("sent", "set_cover_position"),
):
    """Build a duck-typed coordinator recording every side effect in call order.

    ``leader_outcome`` is what the FIRST ``apply_position`` answers — the
    leading rail's dispatch. ``apply_position``'s real contract is
    ``("sent", service)`` or ``("skipped", reason)``, and the corrective
    sequence only makes sense once the leader is genuinely away.
    """
    from unittest.mock import AsyncMock, MagicMock

    calls: list[tuple] = []
    coordinator = MagicMock()
    coordinator.logger = MagicMock()
    coordinator.manual_ignore_external = manual_ignore_external
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {"azimuth": 180}

    async def _apply_position(
        entity_id, position, reason, context, dispatch_token=None
    ):
        first = not any(c[0] == "apply_position" for c in calls)
        calls.append(
            ("apply_position", entity_id, position, reason, context, dispatch_token)
        )
        return leader_outcome if first else ("sent", "set_cover_position")

    async def _apply_user_stop(entity_id):
        calls.append(("apply_user_stop", entity_id))
        return ("sent", "stop_cover")

    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(side_effect=_apply_position)
    coordinator._cmd_svc.apply_user_stop = AsyncMock(side_effect=_apply_user_stop)

    coordinator.manager = MagicMock()
    coordinator.manager.mark_user_command = MagicMock(
        side_effect=lambda entity_id, **kw: calls.append(
            ("mark_user_command", entity_id, kw.get("reason"))
        )
    )

    coordinator._event_buffer = MagicMock()
    coordinator._event_buffer.record = MagicMock(
        side_effect=lambda row: calls.append(("record", row["event"], row))
    )

    coordinator._build_position_context = MagicMock(
        side_effect=lambda entity, options, **kw: ("ctx", entity, kw)  # noqa: ARG005
    )
    return coordinator, calls


async def _execute(coordinator, plan) -> None:
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    await AdaptiveDataUpdateCoordinator._execute_external_interlock(coordinator, plan)


@pytest.mark.asyncio
async def test_executor_order_leader_then_stop_then_follower() -> None:
    """Leader out of the way, follower stopped, follower re-issued — in that order.

    The stop matters because a refused command can leave the follower latched in
    ``closing`` forever; re-dispatching on top of that latch is a no-op. It is
    ``apply_user_stop`` rather than a raw service call so the resulting
    ``stop_cover`` event carries an ACP stamp and this listener ignores it.
    """
    coordinator, calls = _executor_coordinator()

    await _execute(coordinator, _plan())

    assert [c[0] for c in calls if c[0] != "mark_user_command"] == [
        "record",
        "apply_position",
        "apply_user_stop",
        "apply_position",
        "record",
    ]

    dispatches = [c for c in calls if c[0] == "apply_position"]
    assert dispatches[0][1:4] == (_BOTTOM, 0, "external_command_interlock")
    assert dispatches[1][1:4] == (_MIDDLE, 0, "external_command_interlock")

    stop = next(c for c in calls if c[0] == "apply_user_stop")
    assert stop[1] == _MIDDLE

    for dispatch in dispatches:
        _tag, entity, kwargs = dispatch[4]
        assert kwargs["force"] is True
        assert kwargs["bypass_auto_control"] is True
        assert kwargs["user_command"] is True
        assert entity == dispatch[1]


@pytest.mark.asyncio
async def test_executor_forwards_the_plan_stamp_to_both_dispatches() -> None:
    """Both corrective commands are gated in the frame the DECISION used (#1138).

    The plan's targets are wire numbers no ACP dispatch produced, so their
    provenance is the install's own frame and the policy mints it once, at
    decision time. Handed down, the gate un-transforms against exactly that.
    Left off, ``apply_position`` falls back to ``capture_dispatch_token``, which
    replays the frame of the last rail RESOLUTION — a divergent seam parks one
    there and the verdict inverts. Opaque here: the executor replays the stamp
    without interpreting it, exactly as the command service does.
    """
    coordinator, calls = _executor_coordinator()

    await _execute(coordinator, _plan(dispatch_token=False))

    assert [c[5] for c in calls if c[0] == "apply_position"] == [False, False]


@pytest.mark.asyncio
async def test_executor_marks_user_command_on_both_rails_once_the_leader_is_away() -> (
    None
):
    """Manual override lands before the re-dispatch, and only if there IS one.

    The manual-override handler sits at priority 80, so a marked pair is left
    alone by the solar path while the correction completes. What has to be
    marked before is the FOLLOWER's re-dispatch — the one that sits in the
    clearance gate waiting on the leader — not the leader's own command, which
    is ungated by construction and returns immediately.

    Marking earlier than the leader's outcome is known would hand ACP's tracking
    of BOTH rails to the manual-override handler for the full override duration
    on the strength of a correction that never happened — including the partner
    rail, which the user never touched and which never moved.
    """
    coordinator, calls = _executor_coordinator()

    await _execute(coordinator, _plan())

    marks = [c for c in calls if c[0] == "mark_user_command"]
    assert {m[1] for m in marks} == {_BOTTOM, _MIDDLE}
    assert all(m[2] == "external_command_interlock" for m in marks)

    leader = next(i for i, c in enumerate(calls) if c[0] == "apply_position")
    stop = next(i for i, c in enumerate(calls) if c[0] == "apply_user_stop")
    assert all(leader < calls.index(m) < stop for m in marks), calls


@pytest.mark.asyncio
@pytest.mark.parametrize("skip_reason", ["integration_disabled", "dry_run"])
async def test_executor_abandons_the_correction_when_the_leader_never_went_out(
    skip_reason,
) -> None:
    """No stop, no re-dispatch, no override marks — and a row that says why.

    ``apply_position`` answers ``("skipped", <reason>)`` for a command it
    withheld; the integration's hard kill switch and dry-run mode both land
    there. ``apply_user_stop`` → ``call_stop_cover`` consults NEITHER. Ignoring
    the leader's outcome therefore interrupts the user's in-flight command on
    real hardware and then refuses to complete it, flags both rails
    manually-overridden, and files the whole thing as completed — strictly worse
    than the stall this feature exists to fix, and reachable in ordinary use,
    since a disabled ACP is precisely when people drive rails by hand.
    """
    coordinator, calls = _executor_coordinator(leader_outcome=("skipped", skip_reason))

    await _execute(coordinator, _plan())

    assert [c[0] for c in calls] == ["record", "apply_position", "record"], calls
    coordinator._cmd_svc.apply_user_stop.assert_not_awaited()
    coordinator.manager.mark_user_command.assert_not_called()

    rows = [c[2] for c in calls if c[0] == "record"]
    assert [r["event"] for r in rows] == [
        "rail_interlock_engaged",
        "rail_interlock_abandoned",
    ]
    assert rows[-1]["outcome"] == skip_reason
    assert rows[-1]["leading_entity_id"] == _BOTTOM
    assert rows[-1]["follower_entity_id"] == _MIDDLE


@pytest.mark.asyncio
async def test_manual_ignore_external_skips_override_marking_but_still_corrects() -> (
    None
):
    """That option governs OWNERSHIP, not reachability.

    A user who told ACP to ignore external commands still gets the command they
    sent physically carried out — they only opted out of the cover being handed
    over to manual override afterwards.
    """
    coordinator, calls = _executor_coordinator(manual_ignore_external=True)

    await _execute(coordinator, _plan())

    assert not [c for c in calls if c[0] == "mark_user_command"]
    assert [c[0] for c in calls] == [
        "record",
        "apply_position",
        "apply_user_stop",
        "apply_position",
        "record",
    ]


@pytest.mark.asyncio
async def test_executor_records_engaged_and_completed_event_rows() -> None:
    """Both ends of the correction show up on the diagnostics event timeline."""
    coordinator, calls = _executor_coordinator()

    await _execute(coordinator, _plan())

    rows = [c[2] for c in calls if c[0] == "record"]
    assert [r["event"] for r in rows] == [
        "rail_interlock_engaged",
        "rail_interlock_completed",
    ]
    for row in rows:
        assert row["ts"]
        assert row["leading_entity_id"] == _BOTTOM
        assert row["follower_entity_id"] == _MIDDLE
        assert row["leading_target"] == 0
        assert row["follower_target"] == 0
        assert row["reason"] == "external_command_interlock"


# ---------------------------------------------------------------------------
# (e) Task lifecycle — nothing survives an unload
# ---------------------------------------------------------------------------
# The corrective sequence can sit inside a rail-clearance wait for the settle
# cap, and it is a config-entry task, so HA would block the unload on it. It is
# also exactly the shape of untracked-task leak this repo's xdist teardown
# catches: cancelled and cleared on shutdown, no exceptions.


@pytest.mark.asyncio
async def test_async_shutdown_cancels_external_interlock_tasks() -> None:
    from unittest.mock import MagicMock

    from tests.ha_helpers import _bare_coordinator

    coord = _bare_coordinator()
    running = MagicMock()
    running.done.return_value = False
    finished = MagicMock()
    finished.done.return_value = True
    coord._external_interlock_tasks = {_MIDDLE: running, _BOTTOM: finished}

    await coord.async_shutdown()

    running.cancel.assert_called_once()
    finished.cancel.assert_not_called()
    assert coord._external_interlock_tasks == {}
