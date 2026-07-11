"""Tests for the policy-owned axis/cover discovery descriptors (issue #725).

``CoverTypePolicy.describe`` assembles a ``CoverDescriptor`` — cover-type id +
label + one ``AxisDescriptor`` per declared axis — so the self-discovery
surface (the sensor attribute and the ``set_axes`` service validation) can be
built generically off ``policy.axes`` without ever branching on a cover-type
string. ``supported_axes`` filters the declared axes to those a given entity's
capabilities actually expose; it is the single source of truth for both the
service's unsupported-axis rejection and each descriptor's ``supported`` flag.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AxisDescriptor,
    CoverDescriptor,
)

from .test_axes import ALL_COVER_TYPES

pytestmark = pytest.mark.unit

_FULL_CAPS = {"has_set_position": True, "has_set_tilt_position": True}


@pytest.mark.parametrize("cover_type", ALL_COVER_TYPES)
def test_describe_round_trips_all_policies(cover_type: str) -> None:
    """Every cover type round-trips through ``describe`` into a descriptor.

    Parametrised over every registered cover type so a ninth cover type needs
    zero edits to the discovery builder — it inherits ``describe`` unchanged.
    """
    policy = get_policy(cover_type)
    desc = policy.describe(caps=_FULL_CAPS, labels=None)

    assert isinstance(desc, CoverDescriptor)
    assert desc.cover_type == cover_type
    assert desc.cover_label == policy.display_label()
    assert len(desc.axes) == len(policy.axes)

    for axis_desc, axis in zip(desc.axes, policy.axes, strict=True):
        assert isinstance(axis_desc, AxisDescriptor)
        assert axis_desc.id == axis.name
        assert axis_desc.label_key == axis.label_key
        assert axis_desc.min == axis.value_min
        assert axis_desc.max == axis.value_max
        assert axis_desc.unit == axis.unit
        assert axis_desc.capability_key == axis.capability_key
        assert axis_desc.state_attr == axis.state_attr
        assert axis_desc.service_attr == axis.service_attr
        assert axis_desc.open_blocks_sun == axis.open_blocks_sun
        assert axis_desc.supported is True


def test_axis_label_resolves_from_english_defaults() -> None:
    """With ``labels=None`` the English axis label defaults are used."""
    desc = get_policy("cover_venetian").describe(caps=_FULL_CAPS)
    by_id = {a.id: a for a in desc.axes}
    assert by_id["position"].label == "Position"
    assert by_id["tilt"].label == "Tilt"


def test_axis_label_override_wins() -> None:
    """A translated ``labels`` overlay overrides the English default."""
    desc = get_policy("cover_venetian").describe(
        caps=_FULL_CAPS, labels={"axes.tilt": "Lamellenwinkel"}
    )
    by_id = {a.id: a for a in desc.axes}
    assert by_id["tilt"].label == "Lamellenwinkel"
    # Non-overridden axis keeps the English default.
    assert by_id["position"].label == "Position"


def test_supported_axes_filters_by_caps() -> None:
    """``describe`` keeps every declared axis but flags ``supported`` per caps.

    For a dual-axis venetian with tilt disabled, both axes still appear in the
    descriptor, but only the position axis reports ``supported=True``. The
    separate ``supported_axes`` accessor returns ONLY the caps-supported axes —
    the set the service validates against.
    """
    policy = get_policy("cover_venetian")
    caps = {"has_set_position": True, "has_set_tilt_position": False}

    desc = policy.describe(caps=caps)
    by_id = {a.id: a for a in desc.axes}
    assert set(by_id) == {"position", "tilt"}
    assert by_id["position"].supported is True
    assert by_id["tilt"].supported is False

    supported = policy.supported_axes(caps)
    assert tuple(a.name for a in supported) == ("position",)


def test_supported_axes_position_only_blind() -> None:
    """A position-only blind exposes only the position axis."""
    policy = get_policy("cover_blind")
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    supported = policy.supported_axes(caps)
    assert tuple(a.name for a in supported) == ("position",)


def test_supported_axes_open_close_only_blind_keeps_position() -> None:
    """A blind with no ``set_position`` but open+close still exposes position.

    Regression for #886: the open/close fallback drives the position axis, so
    ``supported_axes`` (and the descriptor's ``supported`` flag) must include it.
    """
    policy = get_policy("cover_blind")
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    supported = policy.supported_axes(caps)
    assert tuple(a.name for a in supported) == ("position",)

    by_id = {a.id: a for a in policy.describe(caps=caps).axes}
    assert by_id["position"].supported is True


def test_supported_axes_excludes_position_when_undrivable() -> None:
    """Neither native position nor open+close → position is not drivable (#886)."""
    policy = get_policy("cover_blind")
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": False,
        "has_close": True,
    }
    assert policy.supported_axes(caps) == ()


def test_tilt_axis_has_no_open_close_fallback() -> None:
    """Tilt is drivable only with native tilt — open/close does not reach it."""
    policy = get_policy("cover_venetian")
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    supported = policy.supported_axes(caps)
    assert tuple(a.name for a in supported) == ("position",)
