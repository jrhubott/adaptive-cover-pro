"""The dispatch boundary reads the frame from the contract, not from provenance.

Issue #1036 re-derives issue #469's guards. ``PipelineResult.position`` is
contractually a LOGICAL (pre-inversion canonical) value for *every* winner —
``pipeline/types.py`` says so at the field's definition site and requires any
handler holding a raw cover read to convert it first. So
``coordinator.state`` must never decide "which frame is this number in?" from
provenance flags (``bypass_auto_control`` / ``floor_clamp_applied``): it maps
every winner through :meth:`_to_cover_frame`, unconditionally.

#469's genuine requirement survives, asserted harder than before: **the
dispatched value must be explicable from the reported one**. Equality was one
way to get that; frame invariance — ``state == _to_cover_frame(position)`` for
every winner shape in every frame — is strictly stronger, because it also rules
out the non-monotonic dispatch curve the carve-out produced. Under #469's
behaviour a "minimum 25% open" floor drove an inverse-state cover to 75% open,
and a user asking for logical 10 landed further open than one asking for 30.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INVERSE_STATE,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)

from tests.test_pipeline.conftest import make_snapshot


def _make_pipeline_result(
    *,
    position: int,
    control_method: ControlMethod = ControlMethod.DEFAULT,
    bypass_auto_control: bool = False,
    floor_clamp_applied: bool = False,
    is_safety: bool = False,
) -> PipelineResult:
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass_auto_control,
        floor_clamp_applied=floor_clamp_applied,
        is_safety=is_safety,
    )


def _make_coordinator(
    *,
    pipeline_result: PipelineResult,
    inverse_state_enabled: bool = False,
    use_interpolation: bool = False,
    start_value=None,
    end_value=None,
    normal_list=None,
    new_list=None,
) -> MagicMock:
    coordinator = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coordinator._pipeline_result = pipeline_result
    coordinator._pipeline_bypasses_auto_control = pipeline_result.bypass_auto_control
    coordinator._use_interpolation = use_interpolation
    coordinator._inverse_state = inverse_state_enabled
    coordinator.start_value = start_value
    coordinator.end_value = end_value
    coordinator.normal_list = normal_list
    coordinator.new_list = new_list
    coordinator.logger = MagicMock()
    # `state` asks the coordinator's own `position_axis_inverted` property,
    # which derives from the entry options (#1028). Give the mock real options
    # + policy and evaluate the real property.
    coordinator.config_entry = SimpleNamespace(
        options={
            CONF_INVERSE_STATE: inverse_state_enabled,
            CONF_INTERP: use_interpolation,
        }
    )
    coordinator._policy = get_policy("cover_blind")
    coordinator.position_axis_inverted = (
        AdaptiveDataUpdateCoordinator.position_axis_inverted.fget(coordinator)
    )
    # `state` delegates its post-processing to the shared logical → cover-frame
    # helper the user-command path also uses (#1027); bind the real one so the
    # spec'd mock does not intercept it.
    coordinator._to_cover_frame = AdaptiveDataUpdateCoordinator._to_cover_frame.__get__(
        coordinator
    )
    return coordinator


# A calibration curve with a visibly non-linear middle so an interpolated value
# cannot coincide with its input by luck.
_CURVE = {
    "use_interpolation": True,
    "normal_list": [0, 25, 58, 100],
    "new_list": [0, 45, 58, 100],
    "start_value": 0,
    "end_value": 100,
}


# ---------------------------------------------------------------------------
# Frame invariance — the replacement for #469's carve-out guards
# ---------------------------------------------------------------------------

# Every shape a winner can reach the dispatch boundary in. The two flags each
# still do real work (reason labelling, skip_command=False / forced dispatch,
# the auto-control gate) — none of them says anything about the value's frame.
_WINNER_SHAPES: dict[str, dict] = {
    "plain": {},
    "floor_clamped": {"floor_clamp_applied": True},
    "bypass": {"bypass_auto_control": True},
    "bypass+safety": {"bypass_auto_control": True, "is_safety": True},
    "bypass+floor_clamped": {
        "bypass_auto_control": True,
        "floor_clamp_applied": True,
    },
}

_FRAMES: dict[str, dict] = {
    "plain": {},
    "inverse": {"inverse_state_enabled": True},
    "interpolated": dict(_CURVE),
    "inverse+interpolated": {"inverse_state_enabled": True, **_CURVE},
}


@pytest.mark.parametrize("shape_name", sorted(_WINNER_SHAPES))
@pytest.mark.parametrize("frame_name", sorted(_FRAMES))
@pytest.mark.parametrize("position", [0, 25, 42, 58, 100])
def test_state_equals_to_cover_frame_for_every_winner(
    shape_name: str, frame_name: str, position: int
) -> None:
    """``state`` is ``_to_cover_frame(position)`` for every winner, in every frame.

    The property #469 actually needed: the dispatched value must be explicable
    from the reported one. Provenance flags cannot buy an exemption, because
    ``PipelineResult.position`` is logical no matter which handler produced it
    (#1036).
    """
    result = _make_pipeline_result(position=position, **_WINNER_SHAPES[shape_name])
    coord = _make_coordinator(pipeline_result=result, **_FRAMES[frame_name])

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == coord._to_cover_frame(result.position), (
        f"{shape_name} / {frame_name} / logical {position}: "
        f"state {state} is not the frame transform of the reported position"
    )


def test_floor_clamped_winner_is_inverted_like_any_other() -> None:
    """Concrete pin: a logical-25 floor on an inverse cover dispatches 75.

    Under #469 this dispatched 25 — a "minimum 25% open" floor driving the
    cover to 75% open.
    """
    result = _make_pipeline_result(
        position=25,
        control_method=ControlMethod.DEFAULT,
        bypass_auto_control=False,
        floor_clamp_applied=True,
    )
    coord = _make_coordinator(
        pipeline_result=result,
        inverse_state_enabled=True,
        use_interpolation=False,
    )

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 75


def test_floor_clamped_winner_is_interpolated_like_any_other() -> None:
    """Concrete pin: a floor is a *calculated* position, so it is calibrated.

    ``interp_list`` is documented as "the theoretical positions the integration
    calculates" and ``interp_list_new`` as "what actually gets sent" — a floor
    composed onto ``PipelineResult.position`` is the former. Under #469 it
    escaped the curve entirely.
    """
    result = _make_pipeline_result(
        position=25,
        control_method=ControlMethod.DEFAULT,
        bypass_auto_control=False,
        floor_clamp_applied=True,
    )
    coord = _make_coordinator(pipeline_result=result, **_CURVE)

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 45


def test_bypass_winner_is_transformed_like_any_other() -> None:
    """Concrete pin: a bypass winner is calibrated too (42 → 52 on this curve).

    ``bypass_auto_control`` means "apply even when automatic control is OFF and
    outside the time window" — a statement about gate precedence (#767), never
    about the number's frame.
    """
    result = _make_pipeline_result(
        position=42,
        control_method=ControlMethod.FORCE,
        bypass_auto_control=True,
        floor_clamp_applied=False,
    )
    coord = _make_coordinator(
        pipeline_result=result,
        inverse_state_enabled=True,
        **_CURVE,
    )

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 52


def test_non_floor_winner_still_runs_interpolation():
    """Anti-regression: ordinary (non floor-clamped) winners still get interp."""
    result = _make_pipeline_result(
        position=25,
        control_method=ControlMethod.DEFAULT,
        bypass_auto_control=False,
        floor_clamp_applied=False,
    )
    coord = _make_coordinator(
        pipeline_result=result,
        use_interpolation=True,
        normal_list=[0, 25, 58, 100],
        new_list=[0, 45, 58, 100],
        start_value=0,
        end_value=100,
    )

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 45


def test_non_floor_winner_still_runs_inverse_state():
    """Anti-regression: ordinary winners still get inverse_state applied."""
    result = _make_pipeline_result(
        position=25,
        control_method=ControlMethod.DEFAULT,
        bypass_auto_control=False,
        floor_clamp_applied=False,
    )
    coord = _make_coordinator(
        pipeline_result=result,
        inverse_state_enabled=True,
        use_interpolation=False,
    )

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 75


# ---------------------------------------------------------------------------
# Monotonicity — the property that generalises past specific numbers
# ---------------------------------------------------------------------------

_FLOOR = 25


def _min_mode_slot(position: int) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.floor",),
        is_on=True,
        position=position,
        priority=90,
        min_mode=True,
        use_my=False,
        slot=1,
        active_entity_ids=("binary_sensor.floor",),
    )


@pytest.mark.parametrize("frame_name", sorted(_FRAMES))
def test_dispatch_is_monotone_in_the_logical_request(frame_name: str) -> None:
    """Sweeping the logical request with an active floor yields a monotone curve.

    Built through the REAL ``PipelineRegistry`` so composition and dispatch are
    both exercised — a hand-set ``floor_clamp_applied`` would only test half of
    it. Under #469 the inverse-state curve was
    ``[25, 25, 25, 75, 74, 70, ..., 0]``: asking for logical 10 opened the cover
    further than asking for logical 30. No restatement of any transform can
    satisfy monotonicity; only a single consistent frame can.
    """
    registry = PipelineRegistry(
        [
            CustomPositionHandler(slot=1, position=_FLOOR, priority=90),
            DefaultHandler(),
        ]
    )
    curve: list[int] = []
    for logical in range(0, 101, 5):
        snapshot = make_snapshot(
            default_position=logical,
            custom_position_sensors=[_min_mode_slot(_FLOOR)],
        )
        result = registry.evaluate(snapshot)
        coord = _make_coordinator(pipeline_result=result, **_FRAMES[frame_name])
        curve.append(AdaptiveDataUpdateCoordinator.state.fget(coord))

    assert curve == sorted(curve) or curve == sorted(
        curve, reverse=True
    ), f"{frame_name}: non-monotonic dispatch curve {curve}"
