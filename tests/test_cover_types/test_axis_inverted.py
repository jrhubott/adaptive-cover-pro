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
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
    POSITION_AXIS_OPEN_BLOCKS_SUN,
    TILT_AXIS,
    axis_inverted,
)

pytestmark = pytest.mark.unit


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
