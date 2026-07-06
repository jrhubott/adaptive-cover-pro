"""GroupSceneHandler (85) and GroupLockHandler (100) — issue #790 Phase 2.

The group handlers read ``snapshot.group_intent``; ``None`` is the universal
pass signal (absence of intent IS non-membership). Arbitration contracts:
weather (90) beats a scene (85); a scene beats manual override (80); a
member's own custom-position safety slot beats the group lock on 100-ties
via handler build order (stable sort).
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    GROUP_SCENE_PRIORITY,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlMethod,
    GroupIntentKind,
    GroupScene,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import build_handlers
from custom_components.adaptive_cover_pro.pipeline.handlers.group_lock import (
    GroupLockHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.group_scene import (
    GroupSceneHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import GroupIntent

from .conftest import make_snapshot

pytestmark = pytest.mark.unit

GROUP_ID = "group_entry_1"


def _scene_intent(scene: GroupScene = GroupScene.PRIVACY) -> GroupIntent:
    return GroupIntent(
        kind=GroupIntentKind.SCENE,
        scene=scene,
        priority=GROUP_SCENE_PRIORITY,
        group_id=GROUP_ID,
    )


def _lock_intent() -> GroupIntent:
    return GroupIntent(
        kind=GroupIntentKind.LOCK,
        scene=None,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        group_id=GROUP_ID,
    )


# ---------------------------------------------------------------------------
# GroupSceneHandler
# ---------------------------------------------------------------------------


def test_scene_handler_priority_is_const() -> None:
    assert GroupSceneHandler.priority == GROUP_SCENE_PRIORITY


def test_scene_handler_defers_without_intent() -> None:
    handler = GroupSceneHandler()
    assert handler.evaluate(make_snapshot()) is None


def test_scene_handler_defers_on_lock_intent() -> None:
    handler = GroupSceneHandler()
    snapshot = make_snapshot(group_intent=_lock_intent())
    assert handler.evaluate(snapshot) is None


@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", POSITION_CLOSED),
        ("cover_awning", POSITION_OPEN),
    ],
)
def test_scene_handler_resolves_position_per_policy(
    cover_type: str, expected: int
) -> None:
    """PRIVACY resolves through the member's own policy, never a shared number."""
    handler = GroupSceneHandler()
    snapshot = make_snapshot(cover_type=cover_type, group_intent=_scene_intent())

    result = handler.evaluate(snapshot)

    assert result is not None
    assert result.position == expected
    assert result.control_method is ControlMethod.GROUP_SCENE
    # Explicit user intent: runs even with automatic control off, but it is
    # NOT a safety result (weather/member-safety still outrank it).
    assert result.bypass_auto_control is True
    assert result.is_safety is False
    assert GROUP_ID in result.reason


def test_scene_handler_reason_names_scene() -> None:
    handler = GroupSceneHandler()
    snapshot = make_snapshot(group_intent=_scene_intent(GroupScene.ALL_OPEN))
    result = handler.evaluate(snapshot)
    assert result is not None
    assert str(GroupScene.ALL_OPEN) in result.reason


# ---------------------------------------------------------------------------
# GroupLockHandler
# ---------------------------------------------------------------------------


def test_lock_handler_priority_is_safety() -> None:
    assert GroupLockHandler.priority == CUSTOM_POSITION_SAFETY_PRIORITY


def test_lock_handler_defers_without_intent_or_on_scene() -> None:
    handler = GroupLockHandler()
    assert handler.evaluate(make_snapshot()) is None
    assert handler.evaluate(make_snapshot(group_intent=_scene_intent())) is None


def test_lock_handler_freezes_in_place() -> None:
    """Lock wins the pipeline but sends nothing — the cover holds position."""
    handler = GroupLockHandler()
    snapshot = make_snapshot(group_intent=_lock_intent(), current_cover_position=37)

    result = handler.evaluate(snapshot)

    assert result is not None
    assert result.skip_command is True
    assert result.held_position == 37
    assert result.position == 37
    assert result.control_method is ControlMethod.GROUP_LOCK
    assert GROUP_ID in result.reason


def test_lock_handler_without_readable_position_uses_default() -> None:
    handler = GroupLockHandler()
    snapshot = make_snapshot(
        group_intent=_lock_intent(),
        current_cover_position=None,
        default_position=60,
    )

    result = handler.evaluate(snapshot)

    assert result is not None
    assert result.skip_command is True
    assert result.position == 60


# ---------------------------------------------------------------------------
# Registry arbitration
# ---------------------------------------------------------------------------


def _registry() -> PipelineRegistry:
    return PipelineRegistry(build_handlers({}))


def test_group_handlers_always_built() -> None:
    names = [handler.name for handler in build_handlers({})]
    assert "group_scene" in names
    assert "group_lock" in names
    # Tiebreak order: custom-position slots (member safety) must be listed
    # BEFORE group_lock so the stable priority sort favors the member at 100.
    # No slots configured in empty options, so pin via the factory tuple order
    # in the arbitration test below instead.


def test_weather_outranks_group_scene() -> None:
    snapshot = make_snapshot(
        group_intent=_scene_intent(),
        weather_override_active=True,
        weather_override_position=90,
    )
    result = _registry().evaluate(snapshot)
    assert result.control_method is ControlMethod.WEATHER


def test_group_scene_outranks_manual_override() -> None:
    snapshot = make_snapshot(
        group_intent=_scene_intent(),
        manual_override_active=True,
    )
    result = _registry().evaluate(snapshot)
    assert result.control_method is ControlMethod.GROUP_SCENE


def test_member_safety_slot_beats_group_lock_on_tie() -> None:
    """Physical safety local to the cover trumps a zone lock at equal priority."""
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS
    from custom_components.adaptive_cover_pro.pipeline.types import (
        CustomPositionSensorState,
    )

    slot_keys = CUSTOM_POSITION_SLOTS[1]
    options = {
        slot_keys["sensors"]: ["binary_sensor.wind_alarm"],
        slot_keys["position"]: 0,
        slot_keys["priority"]: CUSTOM_POSITION_SAFETY_PRIORITY,
    }
    sensor_state = CustomPositionSensorState(
        entity_ids=("binary_sensor.wind_alarm",),
        is_on=True,
        position=0,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        min_mode=False,
        use_my=False,
        slot=1,
        active_entity_ids=("binary_sensor.wind_alarm",),
    )
    snapshot = make_snapshot(
        group_intent=_lock_intent(),
        custom_position_sensors=[sensor_state],
        current_cover_position=50,
    )

    result = PipelineRegistry(build_handlers(options)).evaluate(snapshot)

    assert result.control_method is ControlMethod.CUSTOM_POSITION
    assert result.skip_command is False


def test_group_lock_wins_over_scene_and_weather() -> None:
    """Lock at 100 outranks weather (90) — and freezes instead of retracting."""
    snapshot = make_snapshot(
        group_intent=_lock_intent(),
        weather_override_active=True,
        weather_override_position=90,
        current_cover_position=42,
    )
    result = _registry().evaluate(snapshot)
    assert result.control_method is ControlMethod.GROUP_LOCK
    assert result.skip_command is True
