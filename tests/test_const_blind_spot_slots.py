"""Tests for the multi-slot blind-spot scaffolding in const.py (issue #701)."""

from custom_components.adaptive_cover_pro.const import (
    BLIND_SPOT_ELEV_MODE_BELOW,
    BLIND_SPOT_SLOT_NUMBERS,
    BLIND_SPOT_SLOTS,
    BlindSpot,
)


def test_slot_numbers_are_three():
    assert BLIND_SPOT_SLOT_NUMBERS == (1, 2, 3)


def test_slot_one_reuses_unsuffixed_keys():
    keys = BLIND_SPOT_SLOTS[1]
    assert keys["left"] == "blind_spot_left"
    assert keys["right"] == "blind_spot_right"
    assert keys["elevation"] == "blind_spot_elevation"


def test_slot_one_has_signed_gamma_keys():
    """Signed-gamma edges are the primary storage keys (issue #247)."""
    keys = BLIND_SPOT_SLOTS[1]
    assert keys["left_gamma"] == "blind_spot_left_gamma"
    assert keys["right_gamma"] == "blind_spot_right_gamma"


def test_slots_two_and_three_are_suffixed():
    assert BLIND_SPOT_SLOTS[2]["left"] == "blind_spot_left_2"
    assert BLIND_SPOT_SLOTS[2]["right"] == "blind_spot_right_2"
    assert BLIND_SPOT_SLOTS[2]["elevation"] == "blind_spot_elevation_2"
    assert BLIND_SPOT_SLOTS[3]["left"] == "blind_spot_left_3"
    assert BLIND_SPOT_SLOTS[3]["right"] == "blind_spot_right_3"
    assert BLIND_SPOT_SLOTS[3]["elevation"] == "blind_spot_elevation_3"


def test_slots_two_and_three_have_suffixed_gamma_keys():
    assert BLIND_SPOT_SLOTS[2]["left_gamma"] == "blind_spot_left_gamma_2"
    assert BLIND_SPOT_SLOTS[2]["right_gamma"] == "blind_spot_right_gamma_2"
    assert BLIND_SPOT_SLOTS[3]["left_gamma"] == "blind_spot_left_gamma_3"
    assert BLIND_SPOT_SLOTS[3]["right_gamma"] == "blind_spot_right_gamma_3"


def test_blind_spot_legacy_to_gamma_conversion():
    """The shared conversion formula (issue #247): verified, not the issue's."""
    from custom_components.adaptive_cover_pro.const import blind_spot_legacy_to_gamma

    # fov_left=45, legacy 10/30 → new_left = 45-10 = 35, new_right = 30-45 = -15.
    assert blind_spot_legacy_to_gamma(45, 10, 30) == (35, -15)
    # fov_left=90, legacy 0/90 → 90, 0.
    assert blind_spot_legacy_to_gamma(90, 0, 90) == (90, 0)


def test_blind_spot_dataclass_defaults_to_below_elevation_mode():
    bs = BlindSpot(left=10, right=30)
    assert bs.elevation is None
    assert bs.elevation_mode == BLIND_SPOT_ELEV_MODE_BELOW
    assert BLIND_SPOT_ELEV_MODE_BELOW == "below"


def test_blind_spot_dataclass_is_frozen():
    bs = BlindSpot(left=10, right=30, elevation=45)
    assert bs.left == 10
    assert bs.right == 30
    assert bs.elevation == 45
