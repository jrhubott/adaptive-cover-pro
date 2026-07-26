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


# A min-mode floor high enough to bind on part of the logical sweep and not on
# the rest, so the parametrisation exercises both the clamped and the inert
# branch. Priority 90 is load-bearing: ``async_apply_user_position`` only lets a
# floor clamp a user move when it strictly outranks manual override (#472).
_FLOOR = 60
_FLOOR_PRIORITY = 90


def _floor_slot() -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.floor",),
        is_on=True,
        position=_FLOOR,
        priority=_FLOOR_PRIORITY,
        min_mode=True,
        use_my=False,
        slot=1,
        active_entity_ids=("binary_sensor.floor",),
    )


def _composed(logical: int, floor_clamped: bool) -> tuple[int, bool]:
    """Compose what the registry's axis-constraint pass puts on the winner.

    Returns ``(position, floor_clamp_applied)``. The flag is DERIVED — it is
    only True when the floor actually binds — because forcing it on for the
    whole sweep would exercise a shape the registry never produces.
    """
    if not floor_clamped:
        return logical, False
    raised = max(logical, _FLOOR)
    return raised, raised != logical


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


def _dual_panel_case(frame_options: dict, logical: int, floor_clamped: bool):
    """Real dual-panel policy + options + the entities to compare."""
    options = {
        CONF_DUAL_PANEL_FRONT_ENTITY: _FRONT,
        CONF_DUAL_PANEL_BLACKOUT_TRIGGERS: ["night"],
        **frame_options,
    }
    position, clamped = _composed(logical, floor_clamped)
    policy = DualPanelPolicy()
    _prime(
        policy,
        PipelineResult(
            position=position,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            floor_clamp_applied=clamped,
        ),
        options,
        _sunset_cover(False),
    )
    return policy, options, (_FRONT, _BACK), CoverType.DUAL_PANEL


def _day_night_case(frame_options: dict, logical: int, floor_clamped: bool):
    """Real Model C day/night policy + options + the entities to compare."""
    options = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
        **frame_options,
    }
    position, clamped = _composed(logical, floor_clamped)
    policy = DayNightShadePolicy()
    _prime(
        policy,
        PipelineResult(
            position=position,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=50,
            floor_clamp_applied=clamped,
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
    policy, options: dict, cover_type: str, logical: int, floor_clamped: bool = False
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
            custom_position_sensors=[_floor_slot()] if floor_clamped else []
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
    # reaches dispatch instead of being preempted. When the sweep asks for a
    # floor-clamped axis the winner carries what the registry's constraint pass
    # would have composed onto it (#1036).
    position, clamped = _composed(logical, floor_clamped)
    winner = PipelineResult(
        position=position,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        floor_clamp_applied=clamped,
        decision_trace=[
            DecisionStep(
                handler="solar", matched=True, reason="solar", position=position
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
@pytest.mark.parametrize("floor_clamped", [False, True])
async def test_user_and_auto_paths_resolve_identical_entity_targets(
    case_name: str, frame_name: str, logical: int, floor_clamped: bool
) -> None:
    """Same logical value, same wire target — on every entity, in every frame.

    The ``inverted: bool`` seam could name the inversion space but not the
    interpolation space, so an interpolating dual-panel user command routed
    into the broadcast-seam branch and skipped the back panel's calibration
    entirely (auto 20, user 0 for a deployed blackout). Parity is the property
    that was actually broken; assert the property.

    The ``floor_clamped`` axis (#1035's Scope note, #1036) widens the sweep onto
    a winner an active min-mode floor raised. Both paths used to *skip* the
    frame transform in that case, and skipped it differently — the automatic
    seam reused a per-cycle cached flag that ignores the clamp, the user seam
    hard-named ``inverted=False, interpolated=False``.
    """
    policy, options, entities, cover_type = _CASES[case_name](
        _FRAMES[frame_name], logical, floor_clamped
    )
    coord = _make_coord(policy, options, cover_type, logical, floor_clamped)

    for entity_id in entities:
        auto = _auto_target(coord, entity_id)
        user = await _user_target(coord, entity_id, logical)
        assert user == auto, (
            f"{case_name} / {frame_name} / logical {logical} / "
            f"floor_clamped={floor_clamped}: "
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
    policy, options, _entities, cover_type = _dual_panel_case(
        dict(_INTERP_OPTIONS), 30, False
    )
    coord = _make_coord(policy, options, cover_type, 30)

    assert await _user_target(coord, _BACK, 30) == 20
