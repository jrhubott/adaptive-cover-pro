"""CoverConfig.from_options builds the multi-slot blind-spot tuple (issue #701)."""

from custom_components.adaptive_cover_pro.config_types import CoverConfig


def test_legacy_single_slot_only():
    """Only legacy unsuffixed keys → one blind spot."""
    config = CoverConfig.from_options(
        {
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
        }
    )
    assert len(config.blind_spots) == 1
    assert config.blind_spots[0].left == 10
    assert config.blind_spots[0].right == 30
    # Legacy flat mirror is still populated.
    assert config.blind_spot_left == 10
    assert config.blind_spot_right == 30


def test_two_slots_configured():
    """Legacy slot-1 keys plus suffixed slot-2 keys → two blind spots."""
    config = CoverConfig.from_options(
        {
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
            "blind_spot_left_2": 40,
            "blind_spot_right_2": 60,
            "blind_spot_elevation_2": 25,
        }
    )
    assert len(config.blind_spots) == 2
    assert config.blind_spots[1].left == 40
    assert config.blind_spots[1].right == 60
    assert config.blind_spots[1].elevation == 25


def test_disabled_master_yields_empty():
    """Master disable → empty tuple even with slot keys present."""
    config = CoverConfig.from_options(
        {
            "blind_spot": False,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
            "blind_spot_left_2": 40,
            "blind_spot_right_2": 60,
        }
    )
    assert config.blind_spots == ()


def test_incomplete_slot_skipped():
    """A slot missing its right edge is inactive."""
    config = CoverConfig.from_options(
        {
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
            "blind_spot_left_3": 40,  # no right_3 → slot 3 inactive
        }
    )
    assert len(config.blind_spots) == 1


def test_slot_left_zero_is_not_treated_as_missing():
    """left=0 is a valid edge value, not an 'unset' sentinel (issue #868)."""
    config = CoverConfig.from_options(
        {
            "blind_spot": True,
            "blind_spot_left": 0,
            "blind_spot_right": 30,
            "blind_spot_left_2": 0,
            "blind_spot_right_2": 60,
            "blind_spot_elevation_2": 5,
        }
    )
    assert len(config.blind_spots) == 2
    assert config.blind_spots[1].left == 0
    assert config.blind_spots[1].right == 60
