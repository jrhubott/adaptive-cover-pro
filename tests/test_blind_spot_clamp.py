"""Blind-spot slots re-clamped to a narrowed FOV span (issue #852 + #247).

As of issue #247 the blind-spot edges are stored as SIGNED GAMMA from the window
normal in the ``blind_spot_*_gamma`` keys. ``clamp_blind_spots_to_fov`` re-clamps
those new keys to the signed bounds ``left_gamma ∈ [-fov_right, fov_left]`` and
``right_gamma ∈ [-fov_left, fov_right]`` when the FOV narrows. The legacy
FOV-relative keys are migration-read-only and are NEVER clamped.
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
# blind_spot_edges (unchanged FOV-span helper)
# ----------------------------------------------------------------------------


def test_blind_spot_edges_sums_fov():
    assert blind_spot_edges({CONF_FOV_LEFT: 75, CONF_FOV_RIGHT: 75}) == 150


def test_blind_spot_edges_defaults_to_90_90_when_absent():
    assert blind_spot_edges({}) == 180
    assert blind_spot_edges(None) == 180


# ----------------------------------------------------------------------------
# clamp_blind_spots_to_fov — signed gamma keys
# ----------------------------------------------------------------------------


def test_left_gamma_clamped_to_fov_left_max():
    # fov 75/75: left_gamma ∈ [-75, 75].
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_gamma": 100,
        "blind_spot_right_gamma": 0,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left_gamma"] == 75


def test_left_gamma_clamped_to_neg_fov_right_min():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 60,
        "blind_spot_left_gamma": -100,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left_gamma"] == -60  # -fov_right


def test_right_gamma_clamped_to_fov_right_max():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_right_gamma": 100,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_right_gamma"] == 75


def test_right_gamma_clamped_to_neg_fov_left_min():
    options = {
        CONF_FOV_LEFT: 60,
        CONF_FOV_RIGHT: 75,
        "blind_spot_right_gamma": -100,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_right_gamma"] == -60  # -fov_left


def test_in_range_gamma_values_unchanged():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_gamma": 35,
        "blind_spot_right_gamma": -15,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left_gamma"] == 35
    assert result["blind_spot_right_gamma"] == -15


def test_legacy_keys_are_never_clamped():
    """Legacy FOV-relative keys are migration-read-only — untouched by the clamp."""
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left": 172,  # far out of any signed bound
        "blind_spot_right": 200,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left"] == 172
    assert result["blind_spot_right"] == 200


def test_absent_gamma_keys_untouched_no_keyerror():
    options = {CONF_FOV_LEFT: 75, CONF_FOV_RIGHT: 75}
    result = clamp_blind_spots_to_fov(options)
    assert "blind_spot_left_gamma" not in result
    assert "blind_spot_right_gamma" not in result


def test_none_gamma_values_not_coerced():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_gamma": None,
        "blind_spot_right_gamma": None,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left_gamma"] is None
    assert result["blind_spot_right_gamma"] is None


def test_suffixed_slot_2_and_3_gamma_clamp_too():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_gamma_2": 100,
        "blind_spot_right_gamma_2": 100,
        "blind_spot_left_gamma_3": -100,
        "blind_spot_right_gamma_3": -100,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result["blind_spot_left_gamma_2"] == 75
    assert result["blind_spot_right_gamma_2"] == 75
    assert result["blind_spot_left_gamma_3"] == -75
    assert result["blind_spot_right_gamma_3"] == -75


def test_iterates_every_real_slot_defined_in_const():
    options = {CONF_FOV_LEFT: 10, CONF_FOV_RIGHT: 10}  # bounds ±10
    for keys in BLIND_SPOT_SLOTS.values():
        options[keys["left_gamma"]] = 100
        options[keys["right_gamma"]] = 100
    result = clamp_blind_spots_to_fov(options)
    for keys in BLIND_SPOT_SLOTS.values():
        assert result[keys["left_gamma"]] == 10
        assert result[keys["right_gamma"]] == 10


def test_returns_same_mapping_mutated_in_place():
    options = {
        CONF_FOV_LEFT: 75,
        CONF_FOV_RIGHT: 75,
        "blind_spot_left_gamma": 100,
    }
    result = clamp_blind_spots_to_fov(options)
    assert result is options
    assert options["blind_spot_left_gamma"] == 75
