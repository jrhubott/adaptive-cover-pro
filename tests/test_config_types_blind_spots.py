"""CoverConfig.from_options builds the multi-slot blind-spot tuple (issue #701).

Also locks the signed-gamma storage switch (issue #247): blind-spot edges are
stored as signed gamma from the window normal in NEW option keys, with the
legacy FOV-relative keys kept as migration-read-only fallback. The engine wedge
is ``-bs.right <= gamma <= bs.left`` regardless of which key path fed the value.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import CoverConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_LEFT_GAMMA,
    CONF_BLIND_SPOT_RIGHT,
    CONF_BLIND_SPOT_RIGHT_GAMMA,
    CONF_ENABLE_BLIND_SPOT,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
)
from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry


def _sun_data():
    sd = MagicMock()
    sd.timezone = "UTC"
    return sd


# ---------------------------------------------------------------------------
# B5 — legacy-options wedge-membership sweep (bit-identical pre/post lock).
#
# For a grid of (fov_left, old_left, old_right), a CoverConfig built from
# LEGACY-ONLY options must, for every integer gamma in [-180, 180], report the
# SAME blind-spot membership as the analytic legacy interval
# ``fov_left - old_right <= gamma <= fov_left - old_left``. This holds before
# the storage switch (legacy stored raw, engine subtracted fov_left) AND after
# (from_options converts legacy → signed gamma, engine compares signed) — the
# whole point is that the effective wedge is byte-identical across the switch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fov_left,old_left,old_right",
    [
        (45, 10, 30),
        (45, 0, 45),
        (90, 10, 30),
        (90, 0, 90),
        (60, 5, 55),
        (30, 0, 60),
        (90, 30, 30),  # degenerate (empty) wedge — must agree (both empty)
    ],
)
def test_legacy_options_wedge_membership_sweep(fov_left, old_left, old_right):
    options = {
        CONF_FOV_LEFT: fov_left,
        CONF_FOV_RIGHT: 90,
        CONF_ENABLE_BLIND_SPOT: True,
        CONF_BLIND_SPOT_LEFT: old_left,
        CONF_BLIND_SPOT_RIGHT: old_right,
    }
    config = CoverConfig.from_options(options)
    sg = SunGeometry(180.0, 45.0, _sun_data(), config, MagicMock())
    for gamma in range(-180, 181):
        expected = (fov_left - old_right) <= gamma <= (fov_left - old_left)
        assert (
            sg.is_sun_in_blind_spot_at(float(gamma)) is expected
        ), f"gamma={gamma} fov_left={fov_left} old=({old_left},{old_right})"


# ---------------------------------------------------------------------------
# B6 — new signed-gamma keys read preferentially; legacy converts as fallback.
# ---------------------------------------------------------------------------


def test_new_keys_read_preferentially():
    """New signed-gamma keys are used verbatim (no fov_left conversion)."""
    config = CoverConfig.from_options(
        {
            CONF_FOV_LEFT: 45,
            CONF_FOV_RIGHT: 45,
            CONF_ENABLE_BLIND_SPOT: True,
            CONF_BLIND_SPOT_LEFT_GAMMA: 35,
            CONF_BLIND_SPOT_RIGHT_GAMMA: -15,
        }
    )
    assert len(config.blind_spots) == 1
    assert config.blind_spots[0].left == 35
    assert config.blind_spots[0].right == -15


def test_legacy_fallback_converts():
    """Only legacy keys present → converted via the shared helper.

    fov_left=45, legacy 10/30 → new_left = 45-10 = 35, new_right = 30-45 = -15.
    """
    config = CoverConfig.from_options(
        {
            CONF_FOV_LEFT: 45,
            CONF_FOV_RIGHT: 45,
            CONF_ENABLE_BLIND_SPOT: True,
            CONF_BLIND_SPOT_LEFT: 10,
            CONF_BLIND_SPOT_RIGHT: 30,
        }
    )
    assert config.blind_spots[0].left == 35
    assert config.blind_spots[0].right == -15


def test_fresh_default_wedge_does_not_block_sun_at_transit():
    """A fresh entry that enables blind spot without touching sliders must not
    block direct sun at the window normal (issue #247, finding 6).

    The slot-1 schema default is a harmless 1° sliver at the LEFT acceptance
    edge (left=fov_left, right=1-fov_left), NOT a wedge that swallows gamma 0.
    Build the schema-defaulted options and assert gamma 0 is outside the wedge.
    """
    from custom_components.adaptive_cover_pro.config_dynamic import blind_spot_schema

    fov = {CONF_FOV_LEFT: 45, CONF_FOV_RIGHT: 45}
    defaulted = blind_spot_schema(fov)({})  # Required slot-1 defaults fill in
    options = {**fov, CONF_ENABLE_BLIND_SPOT: True, **defaulted}
    config = CoverConfig.from_options(options)
    sg = SunGeometry(180.0, 45.0, _sun_data(), config, MagicMock())
    assert sg.is_sun_in_blind_spot_at(0.0) is False
    # The default wedge is still a valid (non-empty) sliver at the edge.
    assert len(config.blind_spots) == 1
    assert config.blind_spots[0].left + config.blind_spots[0].right > 0


def test_new_keys_win_when_both_present():
    """When BOTH new and legacy keys exist, the new signed-gamma keys win."""
    config = CoverConfig.from_options(
        {
            CONF_FOV_LEFT: 45,
            CONF_FOV_RIGHT: 45,
            CONF_ENABLE_BLIND_SPOT: True,
            CONF_BLIND_SPOT_LEFT: 10,  # legacy → would convert to 35/-15
            CONF_BLIND_SPOT_RIGHT: 30,
            CONF_BLIND_SPOT_LEFT_GAMMA: 20,  # new keys must win
            CONF_BLIND_SPOT_RIGHT_GAMMA: -5,
        }
    )
    assert config.blind_spots[0].left == 20
    assert config.blind_spots[0].right == -5


def test_legacy_single_slot_only():
    """Only legacy unsuffixed keys → one blind spot (converted to gamma).

    fov_left defaults to 90 (DEFAULT_FOV_LEFT) so legacy 10/30 converts to
    new_left = 90-10 = 80, new_right = 30-90 = -60.
    """
    config = CoverConfig.from_options(
        {
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
        }
    )
    assert len(config.blind_spots) == 1
    assert config.blind_spots[0].left == 80
    assert config.blind_spots[0].right == -60
    # Flat mirror now holds the signed-gamma values.
    assert config.blind_spot_left == 80
    assert config.blind_spot_right == -60


def test_two_slots_configured():
    """Legacy slot-1 keys plus suffixed slot-2 keys → two blind spots (gamma).

    fov_left defaults to 90 → slot-2 legacy 40/60 converts to
    new_left = 90-40 = 50, new_right = 60-90 = -30.
    """
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
    assert config.blind_spots[1].left == 50
    assert config.blind_spots[1].right == -30
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
    """left=0 is a valid edge value, not an 'unset' sentinel (issue #868).

    Both slots stay active after conversion (fov_left=90 → slot-2 0/60 →
    left = 90-0 = 90, right = 60-90 = -30).
    """
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
    assert config.blind_spots[1].left == 90
    assert config.blind_spots[1].right == -30
