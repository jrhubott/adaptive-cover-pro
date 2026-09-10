"""Coordinator-seam integration tests for the dual-panel cover type (#996).

A dual-panel instance binds TWO ``cover.*`` entities — a front sheer panel that
sun-tracks and a back blackout panel that deploys on a trigger set. One resolved
pipeline position must fan out to DIFFERENT physical targets per entity: the
front gets the resolved position verbatim; the back is substituted by the
policy's ``resolve_entity_target`` hook (open/closed, in the device's wire space)
via the coordinator's polymorphic ``_entity_target`` dispatch seam. There is NO
cover-type branch in the coordinator — every other cover type's hook is identity.

Harness mirrors ``tests/test_day_night_dual_entity.py``: a minimal mock
coordinator is built and the *real* unbound coordinator methods
(``_dispatch_to_cover``, ``_entity_target``, ``async_handle_state_change``) are
bound onto it, so the seam is exercised end-to-end against a real policy.
"""

from __future__ import annotations

import datetime as dt
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT_ENTITY,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_SUNSET_POS,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types.dual_panel import (
    DualPanelPolicy,
)
from custom_components.adaptive_cover_pro.position_utils import inverse_state
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.position_utils import interpolate_position

_FRONT = "cover.front_sheer"
_BACK = "cover.back_blackout"


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


def _sunset_cover(sunset_valid: bool):
    cover = MagicMock()
    cover.sunset_valid = sunset_valid
    return cover


def _primed_policy(
    *,
    control_method: ControlMethod = ControlMethod.SOLAR,
    triggers=None,
    sol_elev: float = 45.0,
    sunset_valid: bool = False,
    inverse: bool = False,
    interp: bool = False,
    floor_clamp: bool = False,
    front: str = _FRONT,
) -> DualPanelPolicy:
    """Return a real dual-panel policy with the dispatch cache primed for a cycle."""
    from tests.cover_helpers import make_cover_config, make_vertical_config

    policy = DualPanelPolicy()
    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_glare_zones_config.return_value = None
    sun_data = MagicMock()
    sun_data.timezone = "UTC"

    opts: dict = {CONF_DUAL_PANEL_FRONT_ENTITY: front}
    if triggers is not None:
        opts[CONF_DUAL_PANEL_BLACKOUT_TRIGGERS] = list(triggers)
    if inverse:
        opts[CONF_INVERSE_STATE] = True
    if interp:
        opts[CONF_INTERP] = True
        opts[CONF_INTERP_START] = 10
        opts[CONF_INTERP_END] = 90

    result = PipelineResult(
        position=40,
        control_method=control_method,
        reason="test",
        position_constraint_applied=floor_clamp,
    )
    policy.post_pipeline_resolve(
        result,
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=sol_elev,
        sun_data=sun_data,
        config=make_cover_config(),
        config_service=svc,
        options=opts,
        cover=_sunset_cover(sunset_valid),
    )
    return policy


def _make_coordinator(
    policy: DualPanelPolicy, *, state: int, clock_window_open: bool = True
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.entities = [_FRONT, _BACK]
    coordinator.logger = MagicMock()
    coordinator._policy = policy
    coordinator._cmd_svc = _FakeCmdSvc()

    coordinator._pipeline_result = PipelineResult(
        position=state, control_method=ControlMethod.SOLAR, reason="solar"
    )
    coordinator._pipeline_is_safety_handler = False
    coordinator._pipeline_bypasses_auto_control = False
    # No constraint admission here (#943 item B): a plain SOLAR result never
    # earns one, and a truthy MagicMock would open the clock-window guard.
    coordinator._pipeline_acts_outside_clock_window = False
    coordinator.clock_window_open = clock_window_open
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
async def test_deploy_dispatches_front_identity_back_closed() -> None:
    # Night active + night configured → back closes; front keeps its position.
    policy = _primed_policy(triggers=["night"], sol_elev=-3.0)
    coordinator = _make_coordinator(policy, state=40)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )

    cmd = coordinator._cmd_svc
    assert cmd.get_target(_FRONT) == 40
    assert cmd.get_target(_BACK) == POSITION_CLOSED
    assert {c[0] for c in cmd.calls} == {_FRONT, _BACK}


@pytest.mark.asyncio
async def test_retract_dispatches_back_open() -> None:
    # No trigger active → the back stays retracted (open).
    policy = _primed_policy(triggers=["night"], sol_elev=30.0)
    coordinator = _make_coordinator(policy, state=55)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=55, options={}
    )

    cmd = coordinator._cmd_svc
    assert cmd.get_target(_FRONT) == 55
    assert cmd.get_target(_BACK) == POSITION_OPEN


@pytest.mark.asyncio
async def test_floor_clamp_inverse_back_stays_inverted_end_to_end() -> None:
    """Finding 1 end-to-end: a floor-clamped inverse cycle still inverts the back.

    A floor clamp on the FRONT must not un-invert the back's absolute blackout
    substitution. With inverse-state ON and a deploy, the back's wire target is
    ``inverse_state(CLOSED)`` — physically closed — through the real dispatch
    seam. The pre-fix rule (``... and not floor_clamp``) drove it to raw CLOSED
    (wire 0), leaving an inverted device physically OPEN (no blackout).
    """
    policy = _primed_policy(
        triggers=["night"], sol_elev=-3.0, inverse=True, floor_clamp=True
    )
    # A floor-clamped winner short-circuits interp/inverse in coordinator.state,
    # so the FRONT's dispatched value is the raw clamped position.
    coordinator = _make_coordinator(policy, state=30)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=30, options={}
    )

    cmd = coordinator._cmd_svc
    assert cmd.get_target(_FRONT) == 30
    assert cmd.get_target(_BACK) == inverse_state(POSITION_CLOSED)  # wire 100 = closed


@pytest.mark.asyncio
async def test_interpolation_back_endpoint_is_calibrated_end_to_end() -> None:
    """Finding 5 end-to-end: the back's endpoint goes through the interp curve."""
    policy = _primed_policy(triggers=["night"], sol_elev=-3.0, interp=True)
    # With interp on, coordinator.state feeds the FRONT its interpolated value;
    # the back is substituted then interpolated by the policy.
    front_state = int(round(interpolate_position(40, 10, 90, None, None)))
    coordinator = _make_coordinator(policy, state=front_state)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=front_state, options={}
    )

    cmd = coordinator._cmd_svc
    expected_back = int(
        round(interpolate_position(POSITION_CLOSED, 10, 90, None, None))
    )
    assert cmd.get_target(_BACK) == expected_back
    assert cmd.get_target(_BACK) != POSITION_CLOSED  # not overdriven to raw 0


@pytest.mark.xfail(
    reason="#996 Finding 4 dispatch-timing gap: with an end_time set and no "
    "sunset_position, the night/privacy blackout is never physically dispatched "
    "while the operating window is clock-closed (coordinator.py gate at ~2509 "
    "skips all non-safety cycles). The back deploy is not is_safety, so it is "
    "gated off with the front. Needs a follow-up per-entity out-of-window "
    "dispatch path for the back panel.",
    strict=True,
)
@pytest.mark.asyncio
async def test_night_blackout_deploys_outside_operating_window() -> None:
    # Window clock-closed (after end_time) at night; night trigger active. The
    # back SHOULD still deploy the blackout, but the whole-cycle window gate
    # currently skips it. This xfail documents the desired behaviour.
    policy = _primed_policy(triggers=["night"], sol_elev=-3.0)
    coordinator = _make_coordinator(policy, state=40, clock_window_open=False)

    await AdaptiveDataUpdateCoordinator.async_handle_state_change(
        coordinator, state=40, options={}
    )

    assert coordinator._cmd_svc.get_target(_BACK) == POSITION_CLOSED


# ---------------------------------------------------------------------------
# Broadcast seams (#1035). Each of these dispatches OFF the main pipeline cycle
# and names a frame that is a true statement about the FRONT's value — and a
# false one about the back's absolute substitution. Direct counterparts of the
# day/night seam trio at tests/test_day_night_dual_entity.py:361/423/474, which
# pins the OPPOSITE contract for a policy that genuinely derives from the front.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_control_off_seam_keeps_the_back_inverted() -> None:
    """#1035 seam 6: the return-to-default broadcast must not un-invert the back.

    ``switch.py:388`` names ``inverted=False`` because THAT loop never inverts
    the raw default — a true statement about the FRONT's value (#993). The
    back's absolute substitution never consumed it, so on an inverse install
    the blackout's wire target is still ``inverse_state(CLOSED)`` = 100. Raw 0
    leaves an inverted device physically OPEN. Previously unreported; cured by
    the same policy change.
    """
    from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch

    policy = _primed_policy(triggers=["night"], sol_elev=-3.0, inverse=True)

    coord = MagicMock()
    coord.logger = MagicMock()
    coord.entities = [_FRONT, _BACK]
    coord._policy = policy
    coord.return_to_default_toggle = True
    coord.automatic_control = False
    coord.manager.manual_controlled = []
    coord.config_entry.options = {CONF_DEFAULT_HEIGHT: 60}
    coord.async_refresh = AsyncMock()
    coord._cmd_svc = _FakeCmdSvc()
    coord._build_position_context = MagicMock(return_value=MagicMock())
    coord._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coord
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch.schedule_update_ha_state = MagicMock()

    await switch.async_turn_off()

    assert coord._cmd_svc.get_target(_FRONT) == 60  # front: the raw default
    assert coord._cmd_svc.get_target(_BACK) == inverse_state(POSITION_CLOSED)
    assert coord._cmd_svc.get_target(_BACK) != POSITION_CLOSED


@pytest.mark.asyncio
async def test_end_time_default_seam_calibrates_the_back_under_interpolation() -> None:
    """#1035 seam 4: the end-of-window broadcast must not skip the back's curve.

    The seam names ``inverted=self._inverse_state`` and never interpolates —
    deliberate and #993-mandated for the FRONT's value. The back's absolute
    substitution is not that value, so it still belongs on the motor curve
    (#996 Finding 5): canonical CLOSED (0) on a 10..90 device is wire 10, not
    raw 0, which is outside the calibrated band.
    """
    policy = _primed_policy(triggers=["night"], sol_elev=-3.0, interp=True)

    coord = MagicMock()
    coord._policy = policy
    coord._inverse_state = False
    coord.entities = [_FRONT, _BACK]
    coord._track_end_time = True
    coord.automatic_control = True
    coord._pipeline_has_active_override = MagicMock(return_value=False)
    coord.async_refresh = AsyncMock()
    coord.config_entry.options = {}
    coord._compute_current_effective_default = MagicMock(return_value=(60, False))
    coord._check_sunset_window_transition = AsyncMock()

    targets: dict[str, int] = {}

    async def _apply(entity, position, reason, context=None):
        targets[entity] = position

    coord._cmd_svc.apply_position = _apply
    coord._cmd_svc.clear_non_safety_targets = MagicMock()
    coord._build_position_context = MagicMock(return_value=MagicMock())
    coord._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coord
    )
    # Real clamp-then-order seam (#943 item B), with the bound composition —
    # which has its own tests — stubbed to identity so this stays a curve test.
    coord._clamp_to_outside_window_bounds = lambda position, _options: position
    coord._resolve_broadcast_dispatch = types.MethodType(
        AdaptiveDataUpdateCoordinator._resolve_broadcast_dispatch, coord
    )

    async def _fire_closed(*, track_end_time, refresh_callback, on_window_open):
        await refresh_callback()

    coord._time_mgr = MagicMock()
    coord._time_mgr.check_transition = _fire_closed

    await AdaptiveDataUpdateCoordinator._check_time_window_transition(
        coord, dt.datetime.now(dt.UTC)
    )

    expected_back = int(
        round(interpolate_position(POSITION_CLOSED, 10, 90, None, None))
    )
    # Assert ONLY the back. This seam deliberately does not interpolate the
    # FRONT (#993, coordinator.py:3927-3930) — that divergence is out of scope
    # and must not be "helpfully" fixed.
    assert targets[_BACK] == expected_back
    assert targets[_BACK] != POSITION_CLOSED


@pytest.mark.asyncio
async def test_sunset_seam_calibrates_the_back_under_interpolation() -> None:
    """#1035 seam 5: the sunset broadcast must not skip the back's curve.

    Same shape as seam 4 — ``coordinator.py:4117`` binds
    ``inverted=self._inverse_state`` and leaves ``interpolated`` at its
    default, which is right for the sunset position it sends and wrong for the
    back's substitution.
    """
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    policy = _primed_policy(triggers=["night"], sol_elev=-3.0, interp=True)

    coord = MagicMock()
    coord._policy = policy
    coord._inverse_state = False
    coord.entities = [_FRONT, _BACK]
    coord._track_end_time = True
    coord.automatic_control = True
    coord.manager.is_cover_manual = lambda _e: False
    coord._pipeline_has_active_override = MagicMock(return_value=False)
    coord.async_refresh = AsyncMock()
    coord.config_entry.options = {CONF_SUNSET_POS: 0}

    is_sunset = {"v": False}
    coord._window_tracker = WindowTransitionTracker(
        MagicMock(),
        MagicMock(),
        event_buffer=MagicMock(),
        sunset_window_open_fn=lambda _o: is_sunset["v"],
    )

    targets: dict[str, int] = {}

    async def _apply(entity, position, reason, context=None):
        targets[entity] = position

    coord._cmd_svc.apply_position = _apply
    coord._build_position_context = MagicMock(return_value=MagicMock())
    coord._entity_target = types.MethodType(
        AdaptiveDataUpdateCoordinator._entity_target, coord
    )
    # Real clamp-then-order seam (#943 item B), with the bound composition —
    # which has its own tests — stubbed to identity so this stays a curve test.
    coord._clamp_to_outside_window_bounds = lambda position, _options: position
    for _name in ("_resolve_sunset_dispatch", "_resolve_broadcast_dispatch"):
        setattr(
            coord,
            _name,
            types.MethodType(getattr(AdaptiveDataUpdateCoordinator, _name), coord),
        )

    # Seed prior state (no dispatch), then open the sunset window.
    await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)
    is_sunset["v"] = True
    await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)

    expected_back = int(
        round(interpolate_position(POSITION_CLOSED, 10, 90, None, None))
    )
    assert targets[_BACK] == expected_back
    assert targets[_BACK] != POSITION_CLOSED
