"""Integration tests for floor-clamp short-circuit in coordinator dispatch.

Regression coverage for issue #469: when the pipeline registry's floor-clamp
composition raises a non-bypass winner's position to a user-configured floor,
the coordinator's ``state`` property must treat the resulting position as
already in cover-position space and skip interpolation / inverse-state.

Pre-#469 behaviour applied interpolation and inverse_state to the
floor-clamped position, so the dispatched cover command diverged from the
``pipeline_evaluated`` event's reported position.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INVERSE_STATE,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


def _make_pipeline_result(
    *,
    position: int,
    control_method: ControlMethod = ControlMethod.DEFAULT,
    bypass_auto_control: bool = False,
    floor_clamp_applied: bool = False,
) -> PipelineResult:
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass_auto_control,
        floor_clamp_applied=floor_clamp_applied,
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


# ---------------------------------------------------------------------------
# Floor-clamp short-circuit (issue #469)
# ---------------------------------------------------------------------------


def test_floor_clamped_non_bypass_winner_skips_interpolation():
    """Floor-clamped position must not be remapped through interpolation."""
    result = _make_pipeline_result(
        position=25,
        control_method=ControlMethod.DEFAULT,
        bypass_auto_control=False,
        floor_clamp_applied=True,
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

    assert state == 25


def test_floor_clamped_non_bypass_winner_skips_inverse_state():
    """Floor-clamped position must not be inverted (100 - 25 = 75)."""
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

    assert state == 25


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


def test_bypass_auto_control_still_short_circuits_independently_of_floor_flag():
    """bypass_auto_control short-circuit works regardless of floor flag value."""
    result = _make_pipeline_result(
        position=42,
        control_method=ControlMethod.FORCE,
        bypass_auto_control=True,
        floor_clamp_applied=False,
    )
    coord = _make_coordinator(
        pipeline_result=result,
        use_interpolation=True,
        inverse_state_enabled=True,
        normal_list=[0, 25, 58, 100],
        new_list=[0, 45, 58, 100],
        start_value=0,
        end_value=100,
    )

    state = AdaptiveDataUpdateCoordinator.state.fget(coord)

    assert state == 42
