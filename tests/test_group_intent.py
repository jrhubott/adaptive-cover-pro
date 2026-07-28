"""GroupIntent types and the member-side intent seam (issue #790, Phase 2).

A group pushes a ``GroupIntent`` into each member coordinator
(``set_group_intent``); the member holds one live intent per group and folds
the highest-priority one into its ``PipelineSnapshot`` each cycle, where the
group pipeline handlers read it.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    GROUP_SCENE_PRIORITY,
    GroupIntentKind,
    GroupScene,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import GroupIntent

pytestmark = pytest.mark.unit


def _scene_intent(group_id: str, priority: int = GROUP_SCENE_PRIORITY) -> GroupIntent:
    return GroupIntent(
        kind=GroupIntentKind.SCENE,
        scene=GroupScene.PRIVACY,
        priority=priority,
        group_id=group_id,
    )


def test_group_scene_priority_sits_between_manual_and_weather() -> None:
    """85: above manual override (80), below weather safety (90)."""
    from custom_components.adaptive_cover_pro.pipeline.handlers import (
        ManualOverrideHandler,
        WeatherOverrideHandler,
    )

    assert ManualOverrideHandler.priority < GROUP_SCENE_PRIORITY
    assert WeatherOverrideHandler.priority > GROUP_SCENE_PRIORITY


def test_group_intent_is_frozen() -> None:
    intent = _scene_intent("g1")
    with pytest.raises(AttributeError):
        intent.priority = 99  # type: ignore[misc]


def test_set_group_intent_stores_and_removes_per_group() -> None:
    coordinator = MagicMock()
    coordinator._group_intents = {}

    intent = _scene_intent("g1")
    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "g1", intent)
    assert coordinator._group_intents == {"g1": intent}

    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "g1", None)
    assert coordinator._group_intents == {}
    # Removing an absent group is a no-op, not an error.
    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "gone", None)


def test_effective_group_intent_picks_highest_priority() -> None:
    """Two groups pushing to one member: the higher-priority intent wins —
    a whole-house lock is not clobbered by a facade scene.
    """
    coordinator = MagicMock()
    coordinator._group_intents = {}

    scene = _scene_intent("facade")
    lock = GroupIntent(
        kind=GroupIntentKind.LOCK,
        scene=None,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        group_id="house",
    )
    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "facade", scene)
    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "house", lock)

    effective = AdaptiveDataUpdateCoordinator.effective_group_intent.fget(coordinator)
    assert effective is lock

    AdaptiveDataUpdateCoordinator.set_group_intent(coordinator, "house", None)
    effective = AdaptiveDataUpdateCoordinator.effective_group_intent.fget(coordinator)
    assert effective is scene


def test_effective_group_intent_none_when_empty() -> None:
    coordinator = MagicMock()
    coordinator._group_intents = {}
    assert (
        AdaptiveDataUpdateCoordinator.effective_group_intent.fget(coordinator) is None
    )


def test_snapshot_builder_accepts_group_intent() -> None:
    """The builder threads ``group_intent`` into the snapshot (default None)."""
    params = inspect.signature(PipelineSnapshotBuilder.build).parameters
    assert "group_intent" in params
    assert params["group_intent"].default is None


def test_pipeline_winner_name_reads_first_matched_step() -> None:
    """Winner = first matched trace step (handler steps precede synthetic)."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.types import (
        DecisionStep,
        PipelineResult,
    )

    coordinator = MagicMock()
    coordinator._pipeline_result = None
    assert AdaptiveDataUpdateCoordinator.pipeline_winner_name.fget(coordinator) is None

    coordinator._pipeline_result = PipelineResult(
        position=0,
        control_method=ControlMethod.GROUP_SCENE,
        reason="group scene",
        decision_trace=[
            DecisionStep(
                handler="weather_override", matched=False, reason="x", position=None
            ),
            DecisionStep(handler="group_scene", matched=True, reason="won", position=0),
            DecisionStep(handler="floor_clamp", matched=True, reason="y", position=10),
        ],
    )
    assert (
        AdaptiveDataUpdateCoordinator.pipeline_winner_name.fget(coordinator)
        == "group_scene"
    )
