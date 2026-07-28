"""Single source of truth for "is this axis inverted right now" (issue #1028).

``axis_inverted(axis, options)`` replaces the ``inverse_state and not
use_interpolation`` formula that was previously written out at three call
sites (``coordinator.state``, ``dual_panel/policy.py``,
``day_night_shade/policy.py``).

The position/tilt asymmetry it encodes is real and verified:
``coordinator.state`` suppresses position inversion whenever interpolation is
on, but ``cover_types/venetian/sequencer.py``'s ``_to_wire`` reads the raw
``inverse_tilt`` option and is never interpolation-gated.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
)
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_TILT,
    POSITION_AXIS,
    POSITION_AXIS_OPEN_BLOCKS_SUN,
    TILT_AXIS,
    axis_inverted,
)

pytestmark = pytest.mark.unit


# Derived from the policy registry rather than hand-listed. The tests below
# used to exercise only the axis *singletons*, which is exactly how a
# tilt-only policy declaring the shared ``TILT_AXIS`` as its PRIMARY axis
# slipped through: ``inverse_state`` stopped inverting for ``cover_tilt`` and
# ``cover_louvered_roof`` with no test to notice. Deriving the parametrisation
# from ``POLICY_REGISTRY`` means a future cover type cannot slip past either.
_COVER_TYPES_WITH_AXES = sorted(
    cover_type for cover_type, cls in POLICY_REGISTRY.items() if cls.axes
)

# Cover types whose one and only axis is the tilt axis. Those instances are
# configured with ``inverse_state`` (the global position-schema option) — they
# are never offered ``inverse_tilt``, which only the venetian geometry schema
# exposes.
_TILT_ONLY_COVER_TYPES = sorted(
    cover_type
    for cover_type, cls in POLICY_REGISTRY.items()
    if len(cls.axes) == 1 and cls.axes[0].name == AXIS_NAME_TILT
)

# Cover types with a secondary tilt axis (venetian, day/night shade). Those DO
# carry a separately-configured ``inverse_tilt``.
_DUAL_AXIS_COVER_TYPES = sorted(
    cover_type for cover_type, cls in POLICY_REGISTRY.items() if len(cls.axes) > 1
)


def test_registry_derived_parametrisation_is_populated() -> None:
    """Guard the derivations above against silently collapsing to empty lists."""
    assert _COVER_TYPES_WITH_AXES
    assert _TILT_ONLY_COVER_TYPES
    assert _DUAL_AXIS_COVER_TYPES


@pytest.mark.parametrize("axis", [POSITION_AXIS, POSITION_AXIS_OPEN_BLOCKS_SUN])
def test_position_axis_not_inverted_by_default(axis) -> None:
    """Absent config — and absent options entirely — means "not inverted"."""
    assert axis_inverted(axis, {}) is False
    assert axis_inverted(axis, None) is False


@pytest.mark.parametrize("axis", [POSITION_AXIS, POSITION_AXIS_OPEN_BLOCKS_SUN])
def test_position_axis_inverted_when_configured(axis) -> None:
    """Both position singletons key off ``inverse_state``."""
    assert axis_inverted(axis, {CONF_INVERSE_STATE: True}) is True


def test_interpolation_suppresses_position_inversion() -> None:
    """Encodes ``coordinator.py:3590-3594`` — the two are mutually exclusive."""
    options = {CONF_INVERSE_STATE: True, CONF_INTERP: True}
    assert axis_inverted(POSITION_AXIS, options) is False


def test_tilt_axis_keys_off_inverse_tilt() -> None:
    """The tilt axis reads ``inverse_tilt``, never ``inverse_state``."""
    assert axis_inverted(TILT_AXIS, {CONF_INVERSE_STATE: True}) is False
    assert axis_inverted(TILT_AXIS, {CONF_INVERSE_TILT: True}) is True


def test_tilt_inversion_not_suppressed_by_interpolation() -> None:
    """Encodes ``sequencer.py:243-252`` — ``_to_wire`` ignores interpolation."""
    options = {CONF_INVERSE_TILT: True, CONF_INTERP: True}
    assert axis_inverted(TILT_AXIS, options) is True


# ---------------------------------------------------------------------------
# Every policy's PRIMARY axis, exhaustively over the registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _COVER_TYPES_WITH_AXES)
def test_primary_axis_inverts_on_inverse_state(cover_type: str) -> None:
    """``inverse_state`` inverts the primary axis of EVERY cover type.

    ``CONF_INVERSE_STATE`` lives in the shared position schema, so every
    instance can set it — including tilt-only types, whose primary axis is the
    tilt axis. ``coordinator.position_axis_inverted`` reads ``axes[0]``, so a
    primary axis that keys off any other option silently stops inverting.
    """
    policy = get_policy(cover_type)
    assert axis_inverted(policy.axes[0], {CONF_INVERSE_STATE: True}) is True


@pytest.mark.parametrize("cover_type", _COVER_TYPES_WITH_AXES)
def test_primary_axis_not_inverted_without_options(cover_type: str) -> None:
    """No options — and no ``inverse_state`` — means "not inverted", everywhere."""
    policy = get_policy(cover_type)
    assert axis_inverted(policy.axes[0], None) is False
    assert axis_inverted(policy.axes[0], {}) is False


@pytest.mark.parametrize("cover_type", _COVER_TYPES_WITH_AXES)
def test_interpolation_suppresses_primary_axis_inversion(cover_type: str) -> None:
    """Interpolation suppresses primary-axis inversion for every cover type.

    ``coordinator._to_cover_frame`` logs the combination as unsupported and
    skips the flip; a tilt-only cover must behave the same as every other
    single-axis cover here.
    """
    policy = get_policy(cover_type)
    options = {CONF_INVERSE_STATE: True, CONF_INTERP: True}
    assert axis_inverted(policy.axes[0], options) is False


@pytest.mark.parametrize("cover_type", _TILT_ONLY_COVER_TYPES)
def test_tilt_only_primary_axis_ignores_inverse_tilt(cover_type: str) -> None:
    """``inverse_tilt`` is not part of a tilt-only cover's config, so it does nothing.

    Only the venetian geometry schema offers ``CONF_INVERSE_TILT``. A tilt-only
    instance can never set it, and a primary axis that keyed off it would read
    an option that is never written.
    """
    policy = get_policy(cover_type)
    assert axis_inverted(policy.axes[0], {CONF_INVERSE_TILT: True}) is False


# ---------------------------------------------------------------------------
# The SECONDARY tilt axis (venetian, day/night shade) is untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
def test_secondary_tilt_axis_keys_off_inverse_tilt(cover_type: str) -> None:
    """A dual-axis cover's tilt axis still reads ``inverse_tilt``, never ``inverse_state``."""
    secondary = get_policy(cover_type).axes[1]
    assert secondary.name == AXIS_NAME_TILT
    assert axis_inverted(secondary, {CONF_INVERSE_TILT: True}) is True
    assert axis_inverted(secondary, {CONF_INVERSE_STATE: True}) is False


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
def test_secondary_tilt_axis_never_suppressed_by_interpolation(
    cover_type: str,
) -> None:
    """Interpolation must never suppress tilt inversion on a dual-axis cover.

    The venetian sequencer's ``_to_wire`` reads ``inverse_tilt`` directly and
    never consults the calibration curve.
    """
    secondary = get_policy(cover_type).axes[1]
    options = {CONF_INVERSE_TILT: True, CONF_INTERP: True}
    assert axis_inverted(secondary, options) is True
