"""User-path vs automatic-path dispatch parity (#1027).

The whole point of #1027 is that ONE logical position must reach the cover as
ONE wire value, whichever path produced it. This module locks that as an
invariant rather than as a handful of hand-computed expectations:

    for the same logical input, ``async_apply_user_position`` and the automatic
    ``state`` → ``_entity_target`` pair must resolve to identical per-entity
    targets, across the whole frame matrix and for every remapping cover type.

Both halves run against a REAL cover-type policy with its per-cycle dispatch
cache primed exactly as ``post_pipeline_resolve`` primes it, and the automatic
side is computed by calling the real ``state`` property — never by restating
what the transform is supposed to do. A parametrised parity assertion is what
catches a seam that can name *inversion* but not *interpolation*: a narrow
"dual panel + interp dispatches 20" test passes the moment someone hard-codes
20 somewhere, whereas parity cannot be satisfied by anything except the two
paths genuinely agreeing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT_ENTITY,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    POSITION_CLOSED,
    ControlMethod,
    CoverType,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.cover_types.dual_panel import DualPanelPolicy
from custom_components.adaptive_cover_pro.managers.manual_override import inverse_state
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    DecisionStep,
    PipelineResult,
)
from tests.ha_helpers import wire_dispatch_frame

_FRONT = "cover.front_sheer"
_BACK = "cover.back_blackout"
_BOTTOM = "cover.bottom_rail"
_MIDDLE = "cover.middle_rail"

# A calibration curve whose endpoints are deliberately NOT 0/100, so a back
# panel or middle rail that skips interpolation lands on a visibly different
# number (canonical closed 0 → motor 20) instead of coinciding by luck.
_INTERP_OPTIONS = {
    CONF_INTERP: True,
    CONF_INTERP_START: 20,
    CONF_INTERP_END: 80,
}

_FRAMES: dict[str, dict] = {
    "plain": {},
    "inverse": {CONF_INVERSE_STATE: True},
    "interpolated": dict(_INTERP_OPTIONS),
    "inverse+interpolated": {CONF_INVERSE_STATE: True, **_INTERP_OPTIONS},
}

# A min-mode floor whose priority strictly exceeds ManualOverrideHandler's (80).
# At or below 80, ``floor_applies`` is False at coordinator.py:3180 and the
# floor never clamps a user move at all (#472) — the sweep would silently
# degrade to the no-floor case.
_FLOOR_POS = 40


def _floor_slot(pos: int, priority: int = 82) -> CustomPositionSensorState:
    """Build an active min-mode floor slot that outranks manual override."""
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.floor_{pos}",),
        is_on=True,
        position=pos,
        priority=priority,
        min_mode=True,
        use_my=False,
    )


def _sunset_cover(sunset_valid: bool) -> MagicMock:
    cover = MagicMock()
    cover.sunset_valid = sunset_valid
    return cover


def _prime(policy, result: PipelineResult, options: dict, cover) -> None:
    """Run the real ``post_pipeline_resolve`` so the policy caches a real cycle."""
    from tests.cover_helpers import make_cover_config, make_vertical_config

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_glare_zones_config.return_value = None
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    policy.post_pipeline_resolve(
        result,
        logger=MagicMock(),
        sol_azi=180.0,
        # Below the horizon → the "night" blackout trigger is active, so the
        # dual panel's back panel actually deploys and its remap is exercised.
        sol_elev=-3.0,
        sun_data=sun_data,
        config=make_cover_config(),
        config_service=svc,
        options=options,
        cover=cover,
    )


def _dual_panel_case(frame_options: dict, logical: int, *, floor: int | None = None):
    """Real dual-panel policy + options + the entities to compare."""
    options = {
        CONF_DUAL_PANEL_FRONT_ENTITY: _FRONT,
        CONF_DUAL_PANEL_BLACKOUT_TRIGGERS: ["night"],
        **frame_options,
    }
    policy = DualPanelPolicy()
    _prime(
        policy,
        PipelineResult(
            position=logical,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            floor_clamp_applied=floor is not None,
        ),
        options,
        _sunset_cover(False),
    )
    return policy, options, (_FRONT, _BACK), CoverType.DUAL_PANEL


def _day_night_case(frame_options: dict, logical: int, *, floor: int | None = None):
    """Real Model C day/night policy + options + the entities to compare."""
    options = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
        **frame_options,
    }
    policy = DayNightShadePolicy()
    _prime(
        policy,
        PipelineResult(
            position=logical,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=50,
            floor_clamp_applied=floor is not None,
        ),
        options,
        None,
    )
    return policy, options, (_BOTTOM, _MIDDLE), CoverType.DAY_NIGHT_SHADE


_CASES = {
    "dual_panel": _dual_panel_case,
    "day_night_shade": _day_night_case,
}


def _make_coord(
    policy, options: dict, cover_type: str, logical: int, *, floor: int | None = None
):
    """Coordinator-shaped mock carrying a real policy and real frame inputs."""
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.config_entry = MagicMock()
    coord.config_entry.options = options
    wire_dispatch_frame(coord, options, cover_type=cover_type)
    # Swap in the primed policy instance, then re-derive the inversion flag
    # through the REAL property so nothing here restates the formula.
    coord._policy = policy
    coord.position_axis_inverted = (
        AdaptiveDataUpdateCoordinator.position_axis_inverted.fget(coord)
    )
    coord._resolved_options = options

    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.build = MagicMock(
        return_value=make_snapshot(
            custom_position_sensors=[_floor_slot(floor)] if floor is not None else []
        )
    )
    coord._cover_data = MagicMock(name="cover_data")
    coord._cover_type = cover_type
    coord._weather_readings = None
    coord._cloud_mgr = MagicMock()
    coord._climate_smoothing_mgr = MagicMock()
    coord._climate_smoothing_mgr.resolved_flags = None
    coord._build_position_context.return_value = MagicMock(name="ctx")
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))

    # Solar winner at priority 40 — below manual override, so a user command
    # reaches dispatch instead of being preempted.
    winner = PipelineResult(
        position=logical,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        floor_clamp_applied=floor is not None,
        decision_trace=[
            DecisionStep(
                handler="solar", matched=True, reason="solar", position=logical
            )
        ],
    )
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = winner
    solar = MagicMock()
    solar.priority = 40
    coord._handler_by_name = {"solar": solar}
    coord.manager = MagicMock()

    # The automatic path's inputs: a plain non-bypass, non-clamped winner.
    coord._pipeline_result = winner
    coord._pipeline_bypasses_auto_control = False

    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    return coord


async def _user_target(coord, entity_id: str, logical: int) -> int:
    coord._cmd_svc.apply_position.reset_mock()
    await coord.async_apply_user_position(entity_id, logical, trigger="set_position")
    coord._cmd_svc.apply_position.assert_awaited_once()
    return coord._cmd_svc.apply_position.await_args.args[1]


def _auto_target(coord, entity_id: str) -> int:
    """Resolve what the automatic dispatch seam sends this entity this cycle."""
    state = AdaptiveDataUpdateCoordinator.state.fget(coord)
    return coord._entity_target(entity_id, state)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", sorted(_CASES))
@pytest.mark.parametrize("frame_name", sorted(_FRAMES))
@pytest.mark.parametrize("logical", [0, 30, 50, 100])
@pytest.mark.parametrize("floor", [None, _FLOOR_POS])
async def test_user_and_auto_paths_resolve_identical_entity_targets(
    case_name: str, frame_name: str, logical: int, floor: int | None
) -> None:
    """Same logical value, same wire target — every entity, every frame, floor or not.

    The ``inverted: bool`` seam could name the inversion space but not the
    interpolation space, so an interpolating dual-panel user command routed
    into the broadcast-seam branch and skipped the back panel's calibration
    entirely (auto 20, user 0 for a deployed blackout). Parity is the property
    that was actually broken; assert the property.

    The floor axis closes #1035. When a min-mode floor above the request is
    active, BOTH paths carve out the transforms — the registry already raised
    the winner into cover space (#469) and the user path's ``skip_transforms``
    mirrors it (#472/#469) — so the number they must agree on is the floor
    itself. A policy whose target is an absolute substitution rather than a
    transform of that number must still land in its own device wire space; the
    dual-panel back did not, and dispatched raw canonical 0 while the automatic
    side sent wire 100 (inverse) or calibrated 20 (interpolated).
    """
    # With the floor active the registry has ALREADY raised the pipeline winner
    # to the floor, so the agreed number IS the floor; the user asks for
    # something strictly below it and the #472 clamp raises it there.
    agreed = logical if floor is None else floor
    requested = logical if floor is None else min(logical, floor - 1)

    policy, options, entities, cover_type = _CASES[case_name](
        _FRAMES[frame_name], agreed, floor=floor
    )
    coord = _make_coord(policy, options, cover_type, agreed, floor=floor)

    for entity_id in entities:
        auto = _auto_target(coord, entity_id)
        user = await _user_target(coord, entity_id, requested)
        assert user == auto, (
            f"{case_name} / {frame_name} / logical {logical} / floor {floor}: "
            f"{entity_id} auto target {auto} != user target {user}"
        )


@pytest.mark.asyncio
async def test_interpolating_dual_panel_user_command_calibrates_the_back_panel() -> (
    None
):
    """The concrete regression: a calibrated back panel must not dispatch raw 0.

    Pinned separately from the parity sweep so the failure mode is legible —
    ``CONF_INTERP_START=20`` means canonical closed (0) is motor 20 on this
    hardware, and driving the blackout panel to 0 puts it outside its
    calibrated band (#996 Finding 5).
    """
    policy, options, _entities, cover_type = _dual_panel_case(dict(_INTERP_OPTIONS), 30)
    coord = _make_coord(policy, options, cover_type, 30)

    assert await _user_target(coord, _BACK, 30) == 20


@pytest.mark.asyncio
async def test_floor_raised_user_command_keeps_the_back_panel_inverted() -> None:
    """#1035: a floor clamp on the FRONT must not un-invert the back's blackout.

    Pinned separately from the parity sweep so the failure names the physical
    consequence. The floor raises a user request into cover space, so the seam
    correctly names ``(inverted=False, interpolated=False)`` — a true statement
    about the FRONT's value (#993/#1027). The back's target is an absolute
    substitution that never consumed that value, so its wire space is device
    semantics only (#996 Finding 1): a deployed blackout on an inverse install
    is wire 100 = physically CLOSED. Raw 0 leaves the device physically OPEN
    and the blackout silently never deploys on a night/privacy/heat trigger.
    """
    policy, options, _entities, cover_type = _dual_panel_case(
        {CONF_INVERSE_STATE: True}, _FLOOR_POS, floor=_FLOOR_POS
    )
    coord = _make_coord(policy, options, cover_type, _FLOOR_POS, floor=_FLOOR_POS)

    target = await _user_target(coord, _BACK, 10)

    assert target == inverse_state(POSITION_CLOSED)  # wire 100 = physically closed
    # Legibility guard, mirroring the overdrive assertion at
    # tests/test_dual_panel_dispatch.py:228 — name the physical failure.
    assert target != POSITION_CLOSED
