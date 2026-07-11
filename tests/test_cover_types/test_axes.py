"""Tests for the CoverAxis abstraction layer.

Phase 1–4 of the cover-driver refactor introduced ``CoverAxis``, axis
declarations on each policy, and three policy methods (``select_default_axis``,
``position_for_intent``, ``read_axis_value``) that the rest of the codebase
consults instead of comparing cover-type strings. This file pins the
behavioural contract for those additions so a future refactor can't silently
break them.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    POSITION_CLOSED,
    POSITION_OPEN,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    POSITION_AXIS,
    POSITION_AXIS_OPEN_BLOCKS_SUN,
    STATE_ATTR_POSITION,
    STATE_ATTR_TILT_POSITION,
    TILT_AXIS,
    CoverAxis,
)
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities

# Cover-type keys exercised across the parametrised tests below. Listed once
# here so adding a new cover type only changes one place (the parametrize
# decorators below pick up the new value automatically).
ALL_COVER_TYPES = [
    "cover_blind",
    "cover_awning",
    "cover_tilt",
    "cover_venetian",
    "cover_roof_window",
    "cover_sliding_curtain",
    "cover_louvered_roof",
]


# ---------------------------------------------------------------------------
# CoverAxis dataclass shape
# ---------------------------------------------------------------------------


class TestCoverAxis:
    """The dataclass itself — frozen, slotted, hashable."""

    @pytest.mark.unit
    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            POSITION_AXIS.name = "tilt"  # type: ignore[misc]

    @pytest.mark.unit
    def test_uses_slots(self):
        # ``slots=True`` removes ``__dict__``; trying to read it raises.
        with pytest.raises(AttributeError):
            POSITION_AXIS.__dict__  # noqa: B018

    @pytest.mark.unit
    def test_hashable(self):
        # Frozen dataclasses are hashable, so axes can live in sets / dict keys.
        assert {POSITION_AXIS, TILT_AXIS, POSITION_AXIS} == {POSITION_AXIS, TILT_AXIS}

    @pytest.mark.unit
    def test_equality_is_value_based(self):
        twin = CoverAxis(
            name=AXIS_NAME_POSITION,
            service=POSITION_AXIS.service,
            service_attr=POSITION_AXIS.service_attr,
            state_attr=POSITION_AXIS.state_attr,
            capability_key=POSITION_AXIS.capability_key,
            open_blocks_sun=False,
            label_key=POSITION_AXIS.label_key,
            value_min=POSITION_AXIS.value_min,
            value_max=POSITION_AXIS.value_max,
            unit=POSITION_AXIS.unit,
            drive_fallbacks=POSITION_AXIS.drive_fallbacks,
        )
        assert twin == POSITION_AXIS


# ---------------------------------------------------------------------------
# Axis singletons carry the right HA-side identifiers
# ---------------------------------------------------------------------------


class TestAxisSingletons:
    """Sanity-check the constants other call sites depend on."""

    @pytest.mark.unit
    def test_position_axis_attrs(self):
        assert POSITION_AXIS.name == AXIS_NAME_POSITION
        assert POSITION_AXIS.service_attr == ATTR_POSITION
        assert POSITION_AXIS.state_attr == STATE_ATTR_POSITION
        assert POSITION_AXIS.capability_key == CAP_HAS_SET_POSITION
        assert POSITION_AXIS.open_blocks_sun is False

    @pytest.mark.unit
    def test_tilt_axis_attrs(self):
        assert TILT_AXIS.name == AXIS_NAME_TILT
        assert TILT_AXIS.service_attr == ATTR_TILT_POSITION
        assert TILT_AXIS.state_attr == STATE_ATTR_TILT_POSITION
        assert TILT_AXIS.capability_key == CAP_HAS_SET_TILT_POSITION
        assert TILT_AXIS.open_blocks_sun is False

    @pytest.mark.unit
    def test_position_axis_descriptor_fields(self):
        # Discovery-surface metadata (issue #725): each axis carries a label
        # i18n key, a numeric range, and a display unit so the self-discovery
        # payload can be assembled without re-deriving cover-type knowledge.
        assert POSITION_AXIS.label_key == "axes.position"
        assert POSITION_AXIS.value_min == 0
        assert POSITION_AXIS.value_max == 100
        assert POSITION_AXIS.unit == "%"
        assert POSITION_AXIS_OPEN_BLOCKS_SUN.label_key == "axes.position"

    @pytest.mark.unit
    def test_tilt_axis_descriptor_fields(self):
        assert TILT_AXIS.label_key == "axes.tilt"
        assert TILT_AXIS.value_min == 0
        assert TILT_AXIS.value_max == 100
        assert TILT_AXIS.unit == "%"

    @pytest.mark.unit
    def test_awning_position_axis_flips_sun_semantic(self):
        # Awning's "open=blocks-sun" semantic lives on the axis instance, so
        # ``position_for_intent`` falls out of the base implementation without
        # any subclass override.
        assert POSITION_AXIS_OPEN_BLOCKS_SUN.name == AXIS_NAME_POSITION
        assert POSITION_AXIS_OPEN_BLOCKS_SUN.open_blocks_sun is True


# ---------------------------------------------------------------------------
# Each policy declares the right axes
# ---------------------------------------------------------------------------


class TestPolicyAxesDeclarations:
    """Each policy declares the right axes tuple."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("cover_type", "expected_axis_names"),
        [
            ("cover_blind", (AXIS_NAME_POSITION,)),
            ("cover_awning", (AXIS_NAME_POSITION,)),
            ("cover_tilt", (AXIS_NAME_TILT,)),
            ("cover_venetian", (AXIS_NAME_POSITION, AXIS_NAME_TILT)),
            ("cover_louvered_roof", (AXIS_NAME_TILT,)),
        ],
    )
    def test_axes_declaration(self, cover_type, expected_axis_names):
        policy = get_policy(cover_type)
        assert tuple(a.name for a in policy.axes) == expected_axis_names

    @pytest.mark.unit
    def test_blind_tilt_venetian_dont_treat_open_as_sun_blocked(self):
        for cover_type in (
            "cover_blind",
            "cover_tilt",
            "cover_venetian",
            "cover_louvered_roof",
        ):
            assert get_policy(cover_type).axes[0].open_blocks_sun is False

    @pytest.mark.unit
    def test_awning_treats_open_as_sun_blocked(self):
        assert get_policy("cover_awning").axes[0].open_blocks_sun is True


class TestForecastSecondaryAxesHook:
    """The polymorphic forecast secondary-axis hook (issue #724).

    Single-axis covers inherit the base no-op returning ``{}`` — the forecast
    loop asks the hook rather than branching on the cover-type string.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "cover_type",
        ["cover_blind", "cover_awning", "cover_tilt", "cover_louvered_roof"],
    )
    def test_single_axis_policy_returns_no_secondary_axes(self, cover_type):
        from unittest.mock import MagicMock

        result = get_policy(cover_type).forecast_secondary_axes(
            position=50,
            logger=MagicMock(),
            sol_azi=180.0,
            sol_elev=30.0,
            sun_data=MagicMock(),
            config=MagicMock(),
            config_service=MagicMock(),
            options={},
            minimize_movements=False,
            max_coverage_steps=1,
        )
        assert result == {}


# ---------------------------------------------------------------------------
# select_default_axis — parity with the legacy should_use_tilt routing rule
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=[
        # (label, caps, expected axis name per policy primary)
        (
            "full-capable",
            {"has_set_position": True, "has_set_tilt_position": True},
            {
                "cover_blind": AXIS_NAME_POSITION,
                "cover_awning": AXIS_NAME_POSITION,
                "cover_tilt": AXIS_NAME_TILT,
                "cover_venetian": AXIS_NAME_POSITION,
                "cover_roof_window": AXIS_NAME_POSITION,
                "cover_sliding_curtain": AXIS_NAME_POSITION,
                "cover_louvered_roof": AXIS_NAME_TILT,
            },
        ),
        (
            "position-only",
            {"has_set_position": True, "has_set_tilt_position": False},
            {
                "cover_blind": AXIS_NAME_POSITION,
                "cover_awning": AXIS_NAME_POSITION,
                "cover_tilt": AXIS_NAME_TILT,  # cover_tilt always routes tilt
                "cover_venetian": AXIS_NAME_POSITION,
                "cover_roof_window": AXIS_NAME_POSITION,
                "cover_sliding_curtain": AXIS_NAME_POSITION,
                # louvered roof is a tilt-axis type like cover_tilt.
                "cover_louvered_roof": AXIS_NAME_TILT,
            },
        ),
        (
            "tilt-only-fallback",
            {"has_set_position": False, "has_set_tilt_position": True},
            {
                # The "entity only supports tilt" rule overrides declared type:
                # blind/awning/venetian get routed to TILT_AXIS too.
                "cover_blind": AXIS_NAME_TILT,
                "cover_awning": AXIS_NAME_TILT,
                "cover_tilt": AXIS_NAME_TILT,
                "cover_venetian": AXIS_NAME_TILT,
                "cover_roof_window": AXIS_NAME_TILT,
                "cover_sliding_curtain": AXIS_NAME_TILT,
                "cover_louvered_roof": AXIS_NAME_TILT,
            },
        ),
    ],
    ids=lambda p: p[0],
)
def caps_scenario(request):
    return request.param


class TestSelectDefaultAxis:
    """``select_default_axis`` matches the legacy ``should_use_tilt`` rule."""

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", ALL_COVER_TYPES)
    def test_dict_caps(self, caps_scenario, cover_type):
        _label, caps, expected = caps_scenario
        axis = get_policy(cover_type).select_default_axis(caps)
        assert axis.name == expected[cover_type]

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", ALL_COVER_TYPES)
    def test_dataclass_caps(self, caps_scenario, cover_type):
        _label, caps_dict, expected = caps_scenario
        caps = CoverCapabilities(
            has_set_position=caps_dict["has_set_position"],
            has_set_tilt_position=caps_dict["has_set_tilt_position"],
            has_open=True,
            has_close=True,
        )
        axis = get_policy(cover_type).select_default_axis(caps)
        assert axis.name == expected[cover_type]

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", ALL_COVER_TYPES)
    def test_none_caps_normalizes_to_empty(self, cover_type):
        # ``check_cover_features`` returns None when the entity isn't ready;
        # ``select_default_axis`` must not crash and must route to the policy's
        # primary axis (no tilt-fallback, since caps are unknown).
        policy = get_policy(cover_type)
        axis = policy.select_default_axis(None)
        assert axis.name == policy.axes[0].name


# ---------------------------------------------------------------------------
# position_for_intent — semantic intent → numeric axis value
# ---------------------------------------------------------------------------


class TestPositionForIntent:
    """``position_for_intent`` maps semantic intents to numeric values."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("cover_type", "sun_through_value", "sun_blocked_value"),
        [
            ("cover_blind", POSITION_OPEN, POSITION_CLOSED),
            ("cover_awning", POSITION_CLOSED, POSITION_OPEN),
            ("cover_tilt", POSITION_OPEN, POSITION_CLOSED),
            ("cover_venetian", POSITION_OPEN, POSITION_CLOSED),
            ("cover_louvered_roof", POSITION_OPEN, POSITION_CLOSED),
        ],
    )
    def test_intent_map(self, cover_type, sun_through_value, sun_blocked_value):
        policy = get_policy(cover_type)
        assert policy.position_for_intent(sun_through=True) == sun_through_value
        assert policy.position_for_intent(sun_through=False) == sun_blocked_value


# ---------------------------------------------------------------------------
# read_axis_value — single source of truth for "current value on this axis"
# ---------------------------------------------------------------------------


def _hass_with_state(attributes: dict | None, *, state: str = "open") -> MagicMock:
    """Build a mock hass whose ``states.get`` returns one state object.

    ``attributes=None`` simulates an entity without any of the position
    attributes, which exercises the ``get_open_close_state`` fallback.
    """
    hass = MagicMock()
    state_obj = MagicMock()
    state_obj.state = state
    state_obj.attributes = attributes if attributes is not None else {}
    hass.states.get.return_value = state_obj
    return hass


class TestReadAxisValue:
    """``read_axis_value`` is the single source of truth for axis reads."""

    @pytest.mark.unit
    def test_blind_reads_current_position(self):
        hass = _hass_with_state({"current_position": 42})
        caps = {"has_set_position": True, "has_set_tilt_position": False}
        result = get_policy("cover_blind").read_axis_value(hass, "cover.blind", caps)
        assert result == 42

    @pytest.mark.unit
    def test_tilt_reads_current_tilt_position(self):
        hass = _hass_with_state({"current_tilt_position": 35})
        caps = {"has_set_position": False, "has_set_tilt_position": True}
        result = get_policy("cover_tilt").read_axis_value(hass, "cover.tilt", caps)
        assert result == 35

    @pytest.mark.unit
    def test_venetian_reads_current_position(self):
        # Venetian's primary axis is position (its tilt axis is dispatched
        # separately by the DualAxisSequencer), so read_axis_value returns the
        # position value here.
        hass = _hass_with_state({"current_position": 60, "current_tilt_position": 30})
        caps = {"has_set_position": True, "has_set_tilt_position": True}
        result = get_policy("cover_venetian").read_axis_value(
            hass, "cover.venetian", caps
        )
        assert result == 60

    @pytest.mark.unit
    def test_state_obj_overrides_hass_lookup(self):
        # When a state_obj is passed, read_axis_value reads its attributes
        # directly and does not consult ``hass.states`` (preserves the legacy
        # CoverCommandService behaviour where the freshly-arriving state event
        # is the source of truth).
        hass = _hass_with_state({"current_position": 1})  # would-be stale value
        state_obj = MagicMock()
        state_obj.attributes = {"current_position": 75}
        result = get_policy("cover_blind").read_axis_value(
            hass, "cover.blind", {"has_set_position": True}, state_obj=state_obj
        )
        assert result == 75

    @pytest.mark.unit
    def test_falls_back_to_open_close_when_axis_not_capable(self):
        # has_set_position=False → no axis attribute available → fall through
        # to ``get_open_close_state``, which derives a position from "open"/
        # "closed". The mock hass returns state="open" by default so the
        # helper returns POSITION_OPEN.
        hass = _hass_with_state({}, state="open")
        caps = {"has_set_position": False, "has_set_tilt_position": False}
        result = get_policy("cover_blind").read_axis_value(hass, "cover.simple", caps)
        assert result == POSITION_OPEN

    @pytest.mark.unit
    def test_tilt_only_fallback_routes_to_tilt(self):
        # has_set_position=False AND has_set_tilt_position=True flips the
        # blind's primary axis to tilt for this entity, so we read
        # ``current_tilt_position``.
        hass = _hass_with_state({"current_tilt_position": 25})
        caps = {"has_set_position": False, "has_set_tilt_position": True}
        result = get_policy("cover_blind").read_axis_value(hass, "cover.blind", caps)
        assert result == 25


# ---------------------------------------------------------------------------
# position_axis_supported — does this entity expose the policy's primary axis?
# ---------------------------------------------------------------------------


class TestPositionAxisSupported:
    """``position_axis_supported`` reads the primary axis capability per entity.

    Used by the solar floor gate (#569): a set-position-capable cover can be
    commanded to a true 0 % during sun tracking, so the 1 % floor must not
    apply. The signal routes through the policy's ``axes[0].capability_key``
    so no ``caps.get("has_set_position")`` literal leaks outside cover_types/.
    """

    @pytest.mark.unit
    def test_blind_positionable_dict_true(self):
        policy = get_policy("cover_blind")
        assert policy.position_axis_supported({"has_set_position": True}) is True

    @pytest.mark.unit
    def test_blind_positionable_dict_false(self):
        policy = get_policy("cover_blind")
        assert policy.position_axis_supported({"has_set_position": False}) is False

    @pytest.mark.unit
    def test_blind_positionable_dataclass_true(self):
        policy = get_policy("cover_blind")
        caps = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        assert policy.position_axis_supported(caps) is True

    @pytest.mark.unit
    def test_blind_positionable_dataclass_false(self):
        policy = get_policy("cover_blind")
        caps = CoverCapabilities(
            has_set_position=False,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        assert policy.position_axis_supported(caps) is False

    @pytest.mark.unit
    def test_none_caps_defaults_supported(self):
        # caps unknown (entity not ready) → assume supported (default=True) so
        # the floor is NOT spuriously applied. The instance-level rollup in the
        # snapshot builder applies the conservative mixed-instance rule.
        assert get_policy("cover_blind").position_axis_supported(None) is True

    @pytest.mark.unit
    def test_tilt_routes_to_tilt_capability(self):
        # cover_tilt's primary axis is the tilt axis, so the "position axis"
        # support check reads has_set_tilt_position, not has_set_position.
        policy = get_policy("cover_tilt")
        assert (
            policy.position_axis_supported(
                {"has_set_position": True, "has_set_tilt_position": False}
            )
            is False
        )
        assert (
            policy.position_axis_supported(
                {"has_set_position": False, "has_set_tilt_position": True}
            )
            is True
        )


# ---------------------------------------------------------------------------
# Regression guard for CODING_GUIDELINES.md "no hardcoded capability strings"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-cover-type feature flags — replace string-list gates outside cover_types/
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("cover_type", ALL_COVER_TYPES)
def test_is_in_tilt_suppression_uniform_signature(cover_type: str) -> None:
    """Every policy must accept ``is_in_tilt_suppression(entity_id, delta)``.

    Pins the Liskov-safe signature reconciliation. Calling with both
    positional and keyword forms must work for every registered policy so
    the method can be passed as a ``SecondaryAxisCheck.suppression``
    callback without per-type adapters.
    """
    policy = get_policy(cover_type)
    # Positional form.
    assert policy.is_in_tilt_suppression("cover.x", 0.0) is False
    # Keyword form.
    assert policy.is_in_tilt_suppression("cover.x", delta=10.0) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", True),
        ("cover_awning", True),
        ("cover_tilt", False),
        ("cover_venetian", False),
        ("cover_louvered_roof", False),
    ],
)
def test_supports_return_to_default_switch(cover_type: str, expected: bool) -> None:
    """The Return-to-default switch is exposed for position-axis covers only.

    Pins the ClassVar that replaced the legacy ``switch.py`` string-list gate.
    Adding a fifth cover type must add a row here, not branch on the type
    string in ``switch.py``.
    """
    assert get_policy(cover_type).supports_return_to_default_switch is expected


_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_PRODUCTION_ROOT = _REPO_ROOT / "custom_components" / "adaptive_cover_pro"

# ``caps.get("has_X")`` is the banned form. ``caps.get(SOME_VAR)`` (where the
# argument is not a string literal — typically ``axis.capability_key``) is
# allowed. The pattern below matches exactly the banned shape.
_BANNED_CAPS_GET_RE = re.compile(r'caps\.get\(\s*"has_[a-z_]+"')


@pytest.mark.unit
def test_no_hardcoded_caps_get_strings_in_production() -> None:
    """Fail if any production module reintroduces a hardcoded ``caps.get("has_X")``.

    Use ``caps_get(caps, CAP_HAS_X)`` (or read off a ``CoverAxis.capability_key``)
    instead — see CODING_GUIDELINES.md "Cover Type Abstraction".
    """
    offenders: list[str] = []
    for path in _PRODUCTION_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _BANNED_CAPS_GET_RE.search(line):
                # Skip lines that are clearly comment/docstring (start with `#`
                # or contain triple-quote markers). The pattern still catches
                # genuine call sites because production code never starts a
                # call-expression line with a comment marker.
                stripped = line.strip()
                if stripped.startswith(("#", '"', "'")):
                    continue
                rel = path.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "Hardcoded caps.get('has_*') strings found — replace with "
        "caps_get(caps, CAP_HAS_*):\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Cover-type-literal comparison guard
# ---------------------------------------------------------------------------
# Production code outside ``cover_types/`` must dispatch through
# ``get_policy(sensor_type).<flag>`` rather than comparing the sensor type to
# a ``CoverType.<NAME>`` enum or hardcoded ``"cover_<name>"`` string. Adding
# a fifth cover type must not require parallel edits at every call site that
# branched on the previous four — the policy ClassVars exist for exactly that.

# Compares ``something == CoverType.X`` or ``CoverType.X == something``
# (and ``!=``). Variable-to-variable compares (``== current_type``) don't
# match because the right-hand side must be a literal ``CoverType.<NAME>``.
_BANNED_SENSORTYPE_COMPARE_RE = re.compile(
    r"CoverType\.[A-Z_]+\s*(==|!=)|(==|!=)\s*CoverType\.[A-Z_]+"
)
# Compares ``cover_type == "cover_blind"`` and friends — the pre-policy
# string-comparison form that ``caps_get`` and ``get_policy`` replaced.
_BANNED_COVER_TYPE_LITERAL_RE = re.compile(
    r'cover_type\s*(==|!=)\s*["\']cover_[a-z_]+["\']'
)
# Files inside cover_types/ are the boundary the guard enforces — the policy
# layer is allowed (and required) to know about its own concrete types.
_TYPE_BOUNDARY = _PRODUCTION_ROOT / "cover_types"


@pytest.mark.unit
def test_no_cover_type_literals_outside_cover_types() -> None:
    """Fail if any production module outside cover_types/ compares to a CoverType literal.

    The "fifth cover type" punch list grows every time code branches on
    ``CoverType.X`` directly. Add a ClassVar on ``CoverTypePolicy`` (see
    ``exposes_dual_axis_sensor`` and ``custom_position_includes_tilt`` for
    worked examples) and call it through ``get_policy(sensor_type)``
    instead — see CODING_GUIDELINES.md "Cover Type Abstraction".
    """
    offenders: list[str] = []
    for path in _PRODUCTION_ROOT.rglob("*.py"):
        # cover_types/ legitimately knows about concrete types — the guard
        # enforces the boundary, not internal policy code.
        if _TYPE_BOUNDARY in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not (
                _BANNED_SENSORTYPE_COMPARE_RE.search(line)
                or _BANNED_COVER_TYPE_LITERAL_RE.search(line)
            ):
                continue
            stripped = line.strip()
            # Skip lines that are clearly comment/docstring — same heuristic
            # as the sibling caps.get scan above.
            if stripped.startswith(("#", '"', "'")):
                continue
            rel = path.relative_to(_REPO_ROOT)
            offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "Cover-type-literal comparisons found outside cover_types/ — "
        "dispatch through get_policy(sensor_type).<flag> instead:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Tilt-mode-literal comparison guard (issue #373)
# ---------------------------------------------------------------------------
# Code outside ``cover_types/`` and the tilt calc engine must not branch on the
# ``"mode1"`` / ``"mode2"`` tilt-mode string or compare against
# ``TiltMode.MODE2.value``. MODE-aware behavior lives on TiltPolicy.

# Compares ``something == "mode2"`` / ``"mode2" == something`` (and ``!=``).
_BANNED_TILT_MODE_STRING_RE = re.compile(
    r'(==|!=)\s*["\']mode[12]["\']|["\']mode[12]["\']\s*(==|!=)'
)
# Compares ``something == TiltMode.MODE2.value`` — the legacy string-equivalent
# form that should be replaced by passing the enum through TiltPolicy helpers.
_BANNED_TILT_MODE_VALUE_RE = re.compile(
    r"TiltMode\.MODE[12]\.value\s*(==|!=)|(==|!=)\s*TiltMode\.MODE[12]\.value"
)
# Files allowed to branch on the tilt-mode string:
#   - cover_types/ owns mode-specific behavior on TiltPolicy
#   - engine/covers/tilt.py is the calc engine and reads ``mode`` from config
_TILT_MODE_BRANCH_ALLOWED = {
    _PRODUCTION_ROOT / "engine" / "covers" / "tilt.py",
}


@pytest.mark.unit
def test_no_tilt_mode_string_branching_outside_cover_types() -> None:
    """Fail if any module outside cover_types/ branches on the tilt-mode string.

    MODE1/MODE2 differences are a TiltPolicy concern. The climate handler used
    to compare ``tilt_cover.mode == TiltMode.MODE2.value`` inline; that branch
    was extracted to ``TiltPolicy.climate_tilt_percentage`` in fix for
    issue #373.  This guard keeps the pattern out of the rest of the codebase.
    """
    offenders: list[str] = []
    for path in _PRODUCTION_ROOT.rglob("*.py"):
        if _TYPE_BOUNDARY in path.parents:
            continue
        if path in _TILT_MODE_BRANCH_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not (
                _BANNED_TILT_MODE_STRING_RE.search(line)
                or _BANNED_TILT_MODE_VALUE_RE.search(line)
            ):
                continue
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'")):
                continue
            rel = path.relative_to(_REPO_ROOT)
            offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "Tilt-mode string/value comparisons found outside cover_types/ — "
        "MODE1/MODE2 differences live on TiltPolicy (see "
        "climate_tilt_percentage):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", False),
        ("cover_awning", False),
        ("cover_tilt", False),
        ("cover_venetian", True),
        ("cover_louvered_roof", False),
    ],
)
def test_exposes_dual_axis_sensor(cover_type: str, expected: bool) -> None:
    """The dual-axis Target Tilt sensor is enabled only for venetian today.

    Pins the ClassVar that replaced the literal ``CoverType.VENETIAN ==``
    lambda gate on ``sensor.py``. Adding a fifth cover type must add a row
    here, not edit sensor.py.
    """
    assert get_policy(cover_type).exposes_dual_axis_sensor is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", False),
        ("cover_awning", False),
        ("cover_tilt", False),
        ("cover_venetian", True),
        ("cover_louvered_roof", False),
    ],
)
def test_custom_position_includes_tilt(cover_type: str, expected: bool) -> None:
    """The custom-position UI surfaces tilt sliders only for venetian today.

    Pins the ClassVar that replaced the ``is_venetian`` schema branch in
    ``config_flow._build_custom_position_schema_dict``. Adding a fifth cover
    type must add a row here, not edit config_flow.py.
    """
    assert get_policy(cover_type).custom_position_includes_tilt is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "anchor"),
    [
        ("cover_blind", "Configuration-Vertical"),
        ("cover_awning", "Configuration-Horizontal"),
        ("cover_tilt", "Configuration-Tilt"),
        ("cover_venetian", "Venetian-Blinds"),
        ("cover_louvered_roof", "Configuration-Louvered-Roof"),
    ],
)
def test_wiki_anchor(cover_type: str, anchor: str) -> None:
    """Each policy points ``_geometry_wiki_link`` at its own wiki page.

    Pins the per-policy override that replaced the
    ``_GEOMETRY_WIKI_URL`` dict on ``config_flow.py``.
    """
    assert get_policy(cover_type).wiki_anchor() == anchor


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", False),
        ("cover_awning", False),
        ("cover_tilt", False),
        ("cover_venetian", True),
        ("cover_louvered_roof", False),
    ],
)
def test_drift_reset_option_is_venetian_only(cover_type: str, expected: bool) -> None:
    """The drift-reset threshold (#663) is a venetian-only geometry option.

    The accumulator state and reset behaviour live on ``DualAxisSequencer``,
    which only the venetian policy owns. The config key must surface in no
    other cover type's geometry schema — a regression guard so the option
    never leaks into a non-venetian policy.
    """
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_RESET_THRESHOLD,
    )

    schema = get_policy(cover_type).geometry_schema()
    keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
    assert (CONF_VENETIAN_TILT_RESET_THRESHOLD in keys) is expected


class TestLiftTravelMetres:
    """Policy hook returning the configured travel range for the lift axis.

    Pins the per-policy contract the Target Position sensor uses to compute
    its physical-distance attributes. Tilt-only inherits the ``None`` default;
    blind / venetian read ``h_win``; awning reads ``awn_length``.
    """

    @staticmethod
    def _fake_config_service(h_win: float = 2.0, awn_length: float = 1.6) -> MagicMock:
        svc = MagicMock()
        svc.get_vertical_data.return_value = MagicMock(h_win=h_win)
        svc.get_horizontal_data.return_value = MagicMock(awn_length=awn_length)
        return svc

    @pytest.mark.unit
    def test_blind_returns_window_height(self) -> None:
        svc = self._fake_config_service(h_win=2.4)
        assert get_policy("cover_blind").lift_travel_metres(svc, {}) == 2.4

    @pytest.mark.unit
    def test_awning_returns_awn_length(self) -> None:
        svc = self._fake_config_service(awn_length=1.8)
        assert get_policy("cover_awning").lift_travel_metres(svc, {}) == 1.8

    @pytest.mark.unit
    def test_venetian_returns_window_height(self) -> None:
        svc = self._fake_config_service(h_win=1.5)
        assert get_policy("cover_venetian").lift_travel_metres(svc, {}) == 1.5

    @pytest.mark.unit
    def test_tilt_returns_none(self) -> None:
        svc = self._fake_config_service()
        assert get_policy("cover_tilt").lift_travel_metres(svc, {}) is None

    @pytest.mark.unit
    def test_louvered_roof_returns_none(self) -> None:
        # Tilt-axis type — no lift axis, so it inherits the ``None`` default.
        svc = self._fake_config_service()
        assert get_policy("cover_louvered_roof").lift_travel_metres(svc, {}) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cover_type", "label"),
    [
        ("cover_blind", "Vertical Blind"),
        ("cover_awning", "Horizontal Awning"),
        ("cover_tilt", "Venetian / Tilt Blind"),
        ("cover_venetian", "Venetian Blind (Dual-Axis)"),
        ("cover_louvered_roof", "Louvered Roof"),
    ],
)
def test_display_label(cover_type: str, label: str) -> None:
    """Each policy carries its own user-facing label for ``_build_config_summary``.

    Pins the per-policy override that replaced the ``type_labels`` dict on
    ``config_flow.py``. The exact strings remain byte-identical to the
    pre-refactor labels so existing UI tests / screenshots still match.
    """
    assert get_policy(cover_type).display_label() == label
