"""Tests for the sliding-curtain cover-type policy (#829, Part 1).

Pins the policy contract for the binary horizontal sliding curtain: a
single position axis (blind-like, ``open_blocks_sun=False``), a meaningful
default/rest position, and dispatch to the binary
:class:`AdaptiveSlidingCurtainCover` engine.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    POSITION_CLOSED,
    POSITION_OPEN,
)
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.cover_types.base import POSITION_AXIS
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveSlidingCurtainCover,
)
from tests.cover_helpers import make_cover_config

_SLUG = "cover_sliding_curtain"


# ---------------------------------------------------------------------------
# Registration / wiring
# ---------------------------------------------------------------------------


def test_registered_and_enum_member():
    from custom_components.adaptive_cover_pro.const import CoverType

    assert _SLUG in POLICY_REGISTRY
    assert CoverType.SLIDING_CURTAIN.value == _SLUG


def test_controls_cover():
    assert get_policy(_SLUG).controls_cover is True


# ---------------------------------------------------------------------------
# Axis + intent semantics (blind-like)
# ---------------------------------------------------------------------------


def test_single_position_axis():
    assert get_policy(_SLUG).axes == (POSITION_AXIS,)


def test_position_for_intent_blind_like():
    policy = get_policy(_SLUG)
    # sun_through (winter heating) → retract; block-sun (summer) → draw across.
    assert policy.position_for_intent(sun_through=True) == POSITION_OPEN
    assert policy.position_for_intent(sun_through=False) == POSITION_CLOSED


def test_supports_return_to_default_switch():
    assert get_policy(_SLUG).supports_return_to_default_switch is True


# ---------------------------------------------------------------------------
# UI labels
# ---------------------------------------------------------------------------


def test_wiki_anchor():
    assert get_policy(_SLUG).wiki_anchor() == "Configuration-Sliding-Curtain"


def test_display_label():
    assert get_policy(_SLUG).display_label() == "Sliding Curtain"


def test_display_label_honours_translation_override():
    out = get_policy(_SLUG).display_label(
        labels={"cover_types.sliding_curtain": "Schiebevorhang"}
    )
    assert out == "Schiebevorhang"


# ---------------------------------------------------------------------------
# Capability warnings (mirrors the blind/awning open-close-only warning)
# ---------------------------------------------------------------------------


def test_capability_warning_when_no_set_position():
    warnings = get_policy(_SLUG).cover_capability_warnings(
        {"cover.x": {"has_set_position": False}}
    )
    assert warnings and "sliding curtain" in warnings[0].lower()


def test_no_capability_warning_with_set_position():
    warnings = get_policy(_SLUG).cover_capability_warnings(
        {"cover.x": {"has_set_position": True}}
    )
    assert warnings == []


# ---------------------------------------------------------------------------
# Engine dispatch
# ---------------------------------------------------------------------------


def test_build_calc_engine_returns_sliding_curtain_engine():
    engine = get_policy(_SLUG).build_calc_engine(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(timezone="UTC"),
        config=make_cover_config(),
        config_service=MagicMock(),
        options={},
    )
    assert isinstance(engine, AdaptiveSlidingCurtainCover)


# ---------------------------------------------------------------------------
# Geometry stays type-clean — no window-width or cross-type geometry keys yet
# ---------------------------------------------------------------------------


def test_entity_selector_filter_is_plain_cover_domain():
    cfg = get_policy(_SLUG).entity_selector_filter()
    assert cfg["domain"] == "cover"


def test_disallowed_geometry_rejects_awning_and_tilt():
    pairs = get_policy(_SLUG).disallowed_geometry_fields(
        vertical_only=set(),
        awning_only={"awn_length"},
        tilt_only={"tilt_depth"},
    )
    labels = {label for _fields, label in pairs}
    assert labels == {"awning", "tilt"}


@pytest.mark.parametrize(
    "caps",
    [
        {"has_set_position": True, "has_set_tilt_position": True},
        {"has_set_position": True, "has_set_tilt_position": False},
    ],
)
def test_position_axis_is_default_when_positionable(caps):
    axis = get_policy(_SLUG).select_default_axis(caps)
    assert axis.name == POSITION_AXIS.name


# ---------------------------------------------------------------------------
# Part 2 — shade-area geometry schema, length keys, summary, engine wiring
# ---------------------------------------------------------------------------


def test_geometry_schema_has_shade_area_keys():
    from custom_components.adaptive_cover_pro.const import (
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
        CONF_SLIDING_SLIDE_DIRECTION,
        CONF_WINDOW_WIDTH,
    )

    schema = get_policy(_SLUG).geometry_schema()
    keys = {str(m.schema) for m in schema.schema}
    assert {
        CONF_WINDOW_WIDTH,
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_SLIDING_SLIDE_DIRECTION,
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
    } <= keys


def test_geometry_schema_localised_path_returns_schema():
    from custom_components.adaptive_cover_pro.const import CONF_SLIDING_SLIDE_DIRECTION

    schema = get_policy(_SLUG).geometry_schema(hass=MagicMock())
    keys = {str(m.schema) for m in schema.schema}
    assert CONF_SLIDING_SLIDE_DIRECTION in keys


def test_geometry_length_keys_are_width_and_points():
    from custom_components.adaptive_cover_pro.const import (
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
        CONF_WINDOW_WIDTH,
    )

    keys = set(get_policy(_SLUG).geometry_length_keys())
    assert keys == {
        CONF_WINDOW_WIDTH,
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
    }


def test_summary_geometry_lines_render_slide_width_and_points():
    from custom_components.adaptive_cover_pro.const import (
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_SLIDING_POINT1_X,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_POINT2_X,
        CONF_SLIDING_POINT2_Y,
        CONF_SLIDING_SLIDE_DIRECTION,
        CONF_WINDOW_WIDTH,
    )

    config = {
        CONF_SLIDING_SLIDE_DIRECTION: "left",
        CONF_WINDOW_WIDTH: 2.4,
        CONF_SLIDING_ENABLE_SHADE_AREA: True,
        CONF_SLIDING_POINT1_X: -0.5,
        CONF_SLIDING_POINT1_Y: 3.0,
        CONF_SLIDING_POINT2_X: 0.8,
        CONF_SLIDING_POINT2_Y: 4.5,
    }
    joined = " ".join(get_policy(_SLUG).summary_geometry_lines(config))
    assert "2.4" in joined  # window width
    assert "-0.5" in joined and "0.8" in joined  # shade-area point x
    assert "left" in joined.lower() or "single" in joined.lower()  # slide direction


def test_summary_geometry_lines_note_binary_when_area_off():
    from custom_components.adaptive_cover_pro.const import (
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_WINDOW_WIDTH,
    )

    config = {CONF_WINDOW_WIDTH: 2.0, CONF_SLIDING_ENABLE_SHADE_AREA: False}
    joined = " ".join(get_policy(_SLUG).summary_geometry_lines(config))
    assert "binary" in joined.lower()


def test_build_calc_engine_passes_populated_sc_config():
    from custom_components.adaptive_cover_pro.const import (
        CONF_SLIDING_ENABLE_SHADE_AREA,
        CONF_SLIDING_POINT1_Y,
        CONF_SLIDING_SLIDE_DIRECTION,
        CONF_WINDOW_WIDTH,
    )

    options = {
        CONF_SLIDING_ENABLE_SHADE_AREA: True,
        CONF_SLIDING_SLIDE_DIRECTION: "left",
        CONF_WINDOW_WIDTH: 2.4,
        CONF_SLIDING_POINT1_Y: 3.0,
    }
    engine = get_policy(_SLUG).build_calc_engine(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(timezone="UTC"),
        config=make_cover_config(),
        config_service=MagicMock(),
        options=options,
    )
    assert engine.sc_config is not None
    assert engine.sc_config.is_area_configured is True
    assert engine.sc_config.slide_direction == "left"
    assert engine.sc_config.window_width == 2.4
