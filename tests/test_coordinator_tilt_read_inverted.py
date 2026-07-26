"""The full truth table for ``coordinator.tilt_read_inverted`` (issue #1034).

``grep -rn "tilt_read_inverted" tests/`` returned zero hits before this module:
the predicate #1033 added was only ever exercised indirectly, through two proxy
round-trip tests, which is how #1034's three broken cases stayed invisible.

Written AFTER the fix by design. The three integration reproductions in
``tests/cover/test_proxy_cover_commands.py`` are what proved the behaviour
change (they failed on the published value and now pass); this module is the
regression net around it, pinning ~180 (cover type × options × capability)
combinations that no single reproduction can express.

Parametrisation is derived from ``POLICY_REGISTRY`` rather than hand-listed —
the same technique as ``tests/test_cover_types/test_axis_inverted.py`` — so a
tenth cover type cannot slip past.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_TILT,
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    STATE_ATTR_TILT_POSITION,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registry-derived cover-type groups
# ---------------------------------------------------------------------------

# Venetian / day-night shade: a SECOND axis owns the tilt attribute. It is
# ``TILT_AXIS`` — keyed on ``inverse_tilt``, written through the dual-axis
# sequencer's ``_to_wire``, never interpolation-gated.
_DUAL_AXIS_COVER_TYPES = sorted(
    cover_type for cover_type, cls in POLICY_REGISTRY.items() if len(cls.axes) > 1
)

# ``cover_tilt`` / ``cover_louvered_roof``: the tilt attribute is owned by the
# PRIMARY (and only) axis, ``TILT_AXIS_PRIMARY`` — keyed on ``inverse_state``,
# written through ``_to_cover_frame``, and interpolation-suppressed with it.
_TILT_PRIMARY_COVER_TYPES = sorted(
    cover_type
    for cover_type, cls in POLICY_REGISTRY.items()
    if len(cls.axes) == 1 and cls.axes[0].name == AXIS_NAME_TILT
)

# Everything that declares axes but none that owns the tilt attribute. These
# only ever write it when ``should_use_tilt`` reroutes them there.
_POSITION_ONLY_COVER_TYPES = sorted(
    cover_type
    for cover_type, cls in POLICY_REGISTRY.items()
    if cls.axes
    and not any(axis.state_attr == STATE_ATTR_TILT_POSITION for axis in cls.axes)
)


# ---------------------------------------------------------------------------
# Capability sets — what the BOUND entity advertises, not what is configured
# ---------------------------------------------------------------------------

_CAPS_POSITION_AND_TILT = {
    CAP_HAS_SET_POSITION: True,
    CAP_HAS_SET_TILT_POSITION: True,
}
_CAPS_POSITION_ONLY = {
    CAP_HAS_SET_POSITION: True,
    CAP_HAS_SET_TILT_POSITION: False,
}
_CAPS_TILT_ONLY = {
    CAP_HAS_SET_POSITION: False,
    CAP_HAS_SET_TILT_POSITION: True,
}

# ``caps=None`` is what ``check_cover_features`` hands back when it cannot read
# the entity (HA hasn't initialised it, or it is unavailable).
_ALL_CAPS = [
    pytest.param(_CAPS_POSITION_AND_TILT, id="caps_position_and_tilt"),
    pytest.param(_CAPS_POSITION_ONLY, id="caps_position_only"),
    pytest.param(_CAPS_TILT_ONLY, id="caps_tilt_only"),
    pytest.param(None, id="caps_unknown"),
]

_ALL_OPTIONS = [
    pytest.param({}, id="opts_none"),
    pytest.param({CONF_INVERSE_STATE: True}, id="opts_inverse_state"),
    pytest.param({CONF_INVERSE_TILT: True}, id="opts_inverse_tilt"),
    pytest.param(
        {CONF_INVERSE_STATE: True, CONF_INVERSE_TILT: True}, id="opts_both_inversions"
    ),
    pytest.param(
        {CONF_INVERSE_STATE: True, CONF_INTERP: True}, id="opts_inverse_state_interp"
    ),
    pytest.param(
        {CONF_INVERSE_TILT: True, CONF_INTERP: True}, id="opts_inverse_tilt_interp"
    ),
]

_INVERSE_TILT_OPTIONS = [
    pytest.param({CONF_INVERSE_TILT: True}, id="opts_inverse_tilt"),
    pytest.param(
        {CONF_INVERSE_STATE: True, CONF_INVERSE_TILT: True}, id="opts_both_inversions"
    ),
    pytest.param(
        {CONF_INVERSE_TILT: True, CONF_INTERP: True}, id="opts_inverse_tilt_interp"
    ),
]

_POSITION_CAPABLE_CAPS = [
    pytest.param(_CAPS_POSITION_AND_TILT, id="caps_position_and_tilt"),
    pytest.param(_CAPS_POSITION_ONLY, id="caps_position_only"),
]


def _coordinator(cover_type: str, options: dict) -> MagicMock:
    """Build a coordinator stub carrying only the seam ``tilt_read_inverted`` reads.

    Same pattern as ``tests/test_day_night_dual_entity.py`` — a ``MagicMock``
    for the surrounding object, with the REAL unbound members bound onto it, so
    the production code under test is the shipped code and not a paraphrase.
    """
    coord = MagicMock()
    coord._policy = get_policy(cover_type)
    coord.config_entry.options = options
    coord.position_axis_inverted = (
        AdaptiveDataUpdateCoordinator.position_axis_inverted.fget(coord)
    )
    coord.tilt_read_inverted = types.MethodType(
        AdaptiveDataUpdateCoordinator.tilt_read_inverted, coord
    )
    return coord


def test_registry_derived_parametrisation_is_populated() -> None:
    """Guard the derivations above against silently collapsing to empty lists."""
    assert _DUAL_AXIS_COVER_TYPES
    assert _TILT_PRIMARY_COVER_TYPES
    assert _POSITION_ONLY_COVER_TYPES


# ---------------------------------------------------------------------------
# Dual-axis (venetian, day/night shade): the SECOND axis owns the attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
@pytest.mark.parametrize("options", _INVERSE_TILT_OPTIONS)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_dual_axis_tilt_read_inverts_on_inverse_tilt(
    cover_type: str, options: dict, caps: dict | None
) -> None:
    """The core of #1034 — the slat read owes ``_to_wire`` its inverse.

    Every venetian / day-night tilt dispatch goes through the sequencer's
    ``_to_wire``, which flips on ``inverse_tilt``. The read side answered
    ``False`` here until #1034 because it asked about the PRIMARY axis, which
    on these types is the position axis.

    Independent of ``caps``: this is a configured transform, not a routing one.
    """
    coord = _coordinator(cover_type, options)
    assert coord.tilt_read_inverted(caps) is True


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_dual_axis_tilt_read_ignores_inverse_state(
    cover_type: str, caps: dict | None
) -> None:
    """``inverse_state`` does not reach a dual-axis cover's tilt attribute.

    It keys the POSITION axis; the slats are keyed on ``inverse_tilt`` alone.
    The negative half of the same boundary
    ``test_proxy_tilt_read_stays_verbatim_for_second_axis_tilt`` pins
    end-to-end.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_STATE: True})
    assert coord.tilt_read_inverted(caps) is False


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_dual_axis_tilt_read_not_suppressed_by_interpolation(
    cover_type: str, caps: dict | None
) -> None:
    """Interpolation must never suppress tilt inversion on a dual-axis cover.

    ``TILT_AXIS.interpolatable`` is ``False`` deliberately (#925): ``_to_wire``
    reads ``inverse_tilt`` raw and never consults the calibration curve, so a
    read that gated on interpolation would stop un-inverting a value the write
    side still inverted.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_TILT: True, CONF_INTERP: True})
    assert coord.tilt_read_inverted(caps) is True


@pytest.mark.parametrize("cover_type", _DUAL_AXIS_COVER_TYPES)
def test_declared_tilt_axis_wins_over_caps_routing(cover_type: str) -> None:
    """A declared tilt axis beats the caps fallback — branch ORDER is load-bearing.

    On tilt-only hardware a dual-axis cover is pathological (carriage and slats
    both land on ``current_tilt_position``; ``tilt_capability_contradiction``
    warns about it), but the slats are still written by the sequencer on
    ``inverse_tilt``. Consulting ``select_default_axis`` first would answer
    ``position_axis_inverted`` and report ``True`` for an install configured
    only with ``inverse_state`` — the exact inversion a well-meaning refactor
    would introduce.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_STATE: True})
    assert coord.tilt_read_inverted(_CAPS_TILT_ONLY) is False


# ---------------------------------------------------------------------------
# Tilt-primary (cover_tilt, cover_louvered_roof): #1033's behaviour, pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _TILT_PRIMARY_COVER_TYPES)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_tilt_primary_tilt_read_inverts_on_inverse_state(
    cover_type: str, caps: dict | None
) -> None:
    """#1033, unchanged: a tilt-only type's single axis is keyed on ``inverse_state``.

    ``async_apply_user_tilt`` falls through to ``async_apply_user_position``,
    so the wire value came out of ``_to_cover_frame``.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_STATE: True})
    assert coord.tilt_read_inverted(caps) is True


@pytest.mark.parametrize("cover_type", _TILT_PRIMARY_COVER_TYPES)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_tilt_primary_tilt_read_ignores_inverse_tilt(
    cover_type: str, caps: dict | None
) -> None:
    """#1033, unchanged: ``inverse_tilt`` is offered by the venetian schema alone.

    A tilt-only instance can never set it, so an axis keyed off it would read
    an option that is never written.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_TILT: True})
    assert coord.tilt_read_inverted(caps) is False


@pytest.mark.parametrize("cover_type", _TILT_PRIMARY_COVER_TYPES)
@pytest.mark.parametrize("caps", _ALL_CAPS)
def test_tilt_primary_tilt_read_suppressed_by_interpolation(
    cover_type: str, caps: dict | None
) -> None:
    """#1033, unchanged: interpolation and inverse-state are mutually exclusive.

    ``TILT_AXIS_PRIMARY.interpolatable`` is ``True`` — a single-axis cover runs
    its one axis through the calibration curve like any other, and
    ``_to_cover_frame`` logs the combination as unsupported and skips the flip.
    The read has to skip it too.
    """
    coord = _coordinator(cover_type, {CONF_INVERSE_STATE: True, CONF_INTERP: True})
    assert coord.tilt_read_inverted(caps) is False


# ---------------------------------------------------------------------------
# Position-only types: the tilt attribute is reached only by caps routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cover_type", _POSITION_ONLY_COVER_TYPES)
@pytest.mark.parametrize("options", _ALL_OPTIONS)
@pytest.mark.parametrize("caps", _POSITION_CAPABLE_CAPS)
def test_position_only_tilt_read_false_with_position_capable_caps(
    cover_type: str, options: dict, caps: dict
) -> None:
    """An entity that can take ``set_position`` never has its tilt attribute driven.

    ``should_use_tilt`` only reroutes when position is absent, so nothing ACP
    writes lands on ``current_tilt_position`` here — whatever the source
    reports is somebody else's number and must be mirrored verbatim.
    """
    coord = _coordinator(cover_type, options)
    assert coord.tilt_read_inverted(caps) is False


@pytest.mark.parametrize("cover_type", _POSITION_ONLY_COVER_TYPES)
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param({}, False, id="opts_none"),
        pytest.param({CONF_INVERSE_STATE: True}, True, id="opts_inverse_state"),
        pytest.param({CONF_INVERSE_TILT: True}, False, id="opts_inverse_tilt"),
        pytest.param(
            {CONF_INVERSE_STATE: True, CONF_INTERP: True},
            False,
            id="opts_inverse_state_interp",
        ),
    ],
)
def test_position_only_tilt_read_follows_inverse_state_on_tilt_only_caps(
    cover_type: str, options: dict, expected: bool
) -> None:
    """Tilt-only hardware reroutes the dispatch, so the read follows it there.

    ``should_use_tilt`` sends any policy onto ``set_cover_tilt_position`` when
    the entity advertises ``set_tilt_position`` but not ``set_position``. The
    value written is ``_to_cover_frame``'s, so the frame is the POSITION axis's
    — ``inverse_state``, suppressed by interpolation, and deaf to
    ``inverse_tilt`` (which ``select_default_axis``'s returned ``TILT_AXIS``
    would have wrongly consulted).
    """
    coord = _coordinator(cover_type, options)
    assert coord.tilt_read_inverted(_CAPS_TILT_ONLY) is expected


@pytest.mark.parametrize("cover_type", _POSITION_ONLY_COVER_TYPES)
@pytest.mark.parametrize("options", _ALL_OPTIONS)
def test_position_only_tilt_read_false_when_caps_unknown(
    cover_type: str, options: dict
) -> None:
    """Unknown caps must not be guessed into a re-framed read.

    ``select_default_axis`` normalises ``None`` to ``{}``, where
    ``has_set_position`` defaults to ``True`` — the same assumption every other
    dispatch-side caller makes, so the read cannot disagree with the write
    about a cover HA has not described yet.
    """
    coord = _coordinator(cover_type, options)
    assert coord.tilt_read_inverted(None) is False
