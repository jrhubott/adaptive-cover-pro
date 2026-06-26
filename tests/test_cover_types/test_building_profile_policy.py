"""Tests for the virtual ``BuildingProfilePolicy``.

The building profile is a virtual config-entry type: it stores shared
building-level sensor entity IDs and registers no platforms. Its policy
exists so the registry/menu machinery treats it uniformly, but it must be
filtered out of every cover-contract surface via ``controls_cover``, never
by a cover-type string branch.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.config_flow import SENSOR_TYPE_MENU
from custom_components.adaptive_cover_pro.const import CoverType
from custom_components.adaptive_cover_pro.cover_types import get_policy

pytestmark = pytest.mark.unit


def test_building_profile_policy_registers() -> None:
    """The policy is registered and reachable via ``get_policy``."""
    policy = get_policy(CoverType.BUILDING_PROFILE)
    assert policy.cover_type == CoverType.BUILDING_PROFILE


def test_building_profile_does_not_control_a_cover() -> None:
    """It is a virtual entry type — no platforms, no cover control."""
    assert get_policy(CoverType.BUILDING_PROFILE).controls_cover is False


def test_building_profile_has_no_axes() -> None:
    """A profile drives nothing, so it declares zero axes."""
    assert get_policy(CoverType.BUILDING_PROFILE).axes == ()


def test_building_profile_is_last_menu_entry() -> None:
    """It appears in the create-entry picker, sorted to the bottom."""
    assert CoverType.BUILDING_PROFILE in SENSOR_TYPE_MENU
    assert SENSOR_TYPE_MENU[-1] == CoverType.BUILDING_PROFILE
