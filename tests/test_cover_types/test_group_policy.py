"""Tests for the virtual ``GroupPolicy`` orchestrator entry type and scenes.

A cover group is a virtual config-entry type that orchestrates a roster of
member covers. Unlike the building profile it *does* control covers
(``controls_cover = True``) but it is not geometry-driven — the new
``is_orchestrator`` ClassVar is the setup discriminator that routes it to a
``GroupCoordinator`` instead of the sun/geometry pipeline. Scene resolution
is per-member policy behavior via ``position_for_scene``.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.config_flow import SENSOR_TYPE_MENU
from custom_components.adaptive_cover_pro.const import (
    POSITION_CLOSED,
    POSITION_OPEN,
    CoverType,
    GroupScene,
)
from custom_components.adaptive_cover_pro.cover_types import (
    POLICY_REGISTRY,
    get_policy,
)

pytestmark = pytest.mark.unit


def test_group_policy_registers() -> None:
    """The policy is registered and reachable via ``get_policy``."""
    policy = get_policy(CoverType.GROUP)
    assert policy.cover_type == CoverType.GROUP


def test_group_controls_covers_but_is_orchestrator() -> None:
    """A group commands covers, but through orchestration, not geometry."""
    policy = get_policy(CoverType.GROUP)
    assert policy.controls_cover is True
    assert policy.is_orchestrator is True


def test_group_has_no_axes() -> None:
    """A group drives members, not an axis of its own."""
    assert get_policy(CoverType.GROUP).axes == ()


def test_group_is_the_only_orchestrator() -> None:
    """Every other registered policy defaults to ``is_orchestrator = False``."""
    for key in POLICY_REGISTRY:
        if key == CoverType.GROUP:
            continue
        assert get_policy(key).is_orchestrator is False


def test_group_not_in_cover_type_menu() -> None:
    """The group is its own top-level create option, not a cover-type dropdown
    entry — ``controls_cover`` alone no longer suffices as the menu filter.
    """
    assert CoverType.GROUP not in SENSOR_TYPE_MENU
    assert all(not get_policy(k).is_orchestrator for k in SENSOR_TYPE_MENU)


def test_group_scene_wire_values() -> None:
    """Scene identifiers are wire-stable (stored in options / select state)."""
    assert GroupScene.ALL_OPEN == "all_open"
    assert GroupScene.ALL_CLOSED == "all_closed"
    assert GroupScene.PRIVACY == "privacy"


@pytest.mark.parametrize(
    ("cover_type", "scene", "expected"),
    [
        # ALL_OPEN / ALL_CLOSED are HA cover semantics: 100 = open, 0 = closed
        # (blinds raised / awning extended vs blinds lowered / awning retracted).
        (CoverType.BLIND, GroupScene.ALL_OPEN, POSITION_OPEN),
        (CoverType.BLIND, GroupScene.ALL_CLOSED, POSITION_CLOSED),
        (CoverType.AWNING, GroupScene.ALL_OPEN, POSITION_OPEN),
        (CoverType.AWNING, GroupScene.ALL_CLOSED, POSITION_CLOSED),
        (CoverType.TILT, GroupScene.ALL_OPEN, POSITION_OPEN),
        (CoverType.VENETIAN, GroupScene.ALL_CLOSED, POSITION_CLOSED),
        # PRIVACY resolves per-policy to maximum coverage — the existing
        # ``position_for_intent(sun_through=False)`` polymorphism, so the
        # awning's open-blocks-sun axis flips the answer.
        (CoverType.BLIND, GroupScene.PRIVACY, POSITION_CLOSED),
        (CoverType.AWNING, GroupScene.PRIVACY, POSITION_OPEN),
        (CoverType.TILT, GroupScene.PRIVACY, POSITION_CLOSED),
        (CoverType.VENETIAN, GroupScene.PRIVACY, POSITION_CLOSED),
    ],
)
def test_position_for_scene_resolves_per_policy(
    cover_type: CoverType, scene: GroupScene, expected: int
) -> None:
    assert get_policy(cover_type).position_for_scene(scene) == expected
