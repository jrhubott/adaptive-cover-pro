"""Blind-spot slots re-clamped to a narrowed FOV span (issue #852).

Blind-spot left/right are azimuth offsets *within* the FOV span
(``edges = fov_left + fov_right``). Narrowing the FOV on the geometry step
never re-clamped stored slot values, leaving out-of-range wedges (e.g.
``blind_spot_right=172`` under a new ``edges=150``) whose stored value silently
disagreed with the options-flow slider (max = new edges). ``clamp_blind_spots_to_fov``
is the single shared helper that fixes stored values in place; ``blind_spot_edges``
is the single-sourced ``fov_left + fov_right`` formula it (and ``blind_spot_schema``)
both delegate to.
"""

from custom_components.adaptive_cover_pro.config_dynamic import (
    blind_spot_edges,
    clamp_blind_spots_to_fov,
)
from custom_components.adaptive_cover_pro.const import (
    BLIND_SPOT_SLOTS,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
)

# ----------------------------------------------------------------------------
# blind_spot_edges
# ----------------------------------------------------------------------------


def test_blind_spot_edges_sums_fov():
    assert blind_spot_edges({CONF_FOV_LEFT: 75, CONF_FOV_RIGHT: 75}) == 150


def test_blind_spot_edges_defaults_to_90_90_when_absent():
    assert blind_spot_edges({}) == 180
    assert blind_spot_edges(None) == 180


# ----------------------------------------------------------------------------
# clamp_blind_spots_to_fov
# ----------------------------------------------------------------------------


def test_over_range_right_clamped_to_new_edges():
    # fov 86/86 (edges=172) narrowed to fov 75/75 (edges=150).
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": 0,
        "blind_spot_right": 172,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_right"] == 150


def test_over_range_left_clamped_to_edges_minus_one():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": 155,
        "blind_spot_right": 172,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left"] == 149  # edges(150) - 1
    assert result["blind_spot_right"] == 150


def test_in_range_values_left_unchanged():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": 10,
        "blind_spot_right": 30,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left"] == 10
    assert result["blind_spot_right"] == 30


def test_absent_slot_keys_untouched_no_keyerror():
    options = {CONF_FOV_LEFT: 75, CONF_FOV_RIGHT: 75}
    result = clamp_blind_spots_to_fov(options)
    assert "blind_spot_left" not in result
    assert "blind_spot_right" not in result


def test_none_slot_values_not_coerced():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": None,
        "blind_spot_right": None,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left"] is None
    assert result["blind_spot_right"] is None


def test_ordering_preserved_after_clamp():
    # A slot pinned near the OLD edges must still satisfy right > left once
    # both are clamped to the new (narrower) edges.
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": 170,
        "blind_spot_right": 172,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_right"] > result["blind_spot_left"]
    assert result["blind_spot_left"] == 149
    assert result["blind_spot_right"] == 150


def test_suffixed_slot_2_and_3_clamp_too():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_2": 0,
        "blind_spot_right_2": 172,
        "blind_spot_left_3": 149,
        "blind_spot_right_3": 172,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_right_2"] == 150
    assert result["blind_spot_left_3"] == 149  # already at the new cap
    assert result["blind_spot_right_3"] == 150


def test_iterates_every_real_slot_defined_in_const():
    # Data-driven: every slot in BLIND_SPOT_SLOTS gets its left/right clamped,
    # not just a hardcoded subset.
    options = {CONF_FOV_LEFT: 10, CONF_FOV_RIGHT: 10}  # edges=20
    for keys in BLIND_SPOT_SLOTS.values():
        options[keys["left"]] = 100
        options[keys["right"]] = 100
    result = clamp_blind_spots_to_fov(options)
    for keys in BLIND_SPOT_SLOTS.values():
        assert result[keys["left"]] == 19
        assert result[keys["right"]] == 20


def test_returns_same_mapping_mutated_in_place():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_right": 172,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result is options
    assert options["blind_spot_right"] == 150
