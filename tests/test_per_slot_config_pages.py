"""Per-slot config pages — options-flow slot paging (issue #945, Part 1).

Three options-flow areas (Custom Positions, Blind Spots, Glare Zones) render one
focused page per slot instead of flattening every slot onto a single giant form.
The pages use generic un-suffixed field keys (one translation block per area);
GET seeds them from a slot's stored suffixed keys and POST maps them back, so
storage is unchanged. These tests lock the const slot table, the single-slot
schema builders, and the slot-N-writes-only-its-keys guarantee.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    BLIND_SPOT_FORM_KEYS,
    BLIND_SPOT_SLOTS,
    CUSTOM_POSITION_FORM_KEYS,
    CUSTOM_POSITION_SLOTS,
    GLARE_ZONE_FORM_KEYS,
    GLARE_ZONE_SLOT_NUMBERS,
    GLARE_ZONE_SLOTS,
    CoverType,
)


def _schema_keys(schema) -> set[str]:
    return {str(marker) for marker in schema.schema}


# ---------------------------------------------------------------------------
# const.py — glare-zone slot table mirrors the other two areas.
# ---------------------------------------------------------------------------


def test_glare_zone_slot_numbers_are_one_to_four():
    assert GLARE_ZONE_SLOT_NUMBERS == (1, 2, 3, 4)


def test_glare_zone_slots_shape_parity():
    assert set(GLARE_ZONE_SLOTS) == set(GLARE_ZONE_SLOT_NUMBERS)
    for n, keys in GLARE_ZONE_SLOTS.items():
        assert set(keys) == {"name", "x", "y", "radius", "z"}
        assert keys["name"] == f"glare_zone_{n}_name"
        assert keys["x"] == f"glare_zone_{n}_x"
        assert keys["radius"] == f"glare_zone_{n}_radius"


def test_form_key_maps_cover_the_slot_subkeys():
    # Every form (generic) key names a sub-key the storage slots also carry.
    assert set(CUSTOM_POSITION_FORM_KEYS) <= set(CUSTOM_POSITION_SLOTS[1])
    assert set(BLIND_SPOT_FORM_KEYS) <= set(BLIND_SPOT_SLOTS[1])
    assert set(GLARE_ZONE_FORM_KEYS) <= set(GLARE_ZONE_SLOTS[1])
    # Blind-spot slot 1's storage keys ARE the un-suffixed generic keys.
    assert BLIND_SPOT_FORM_KEYS["left_gamma"] == BLIND_SPOT_SLOTS[1]["left_gamma"]


# ---------------------------------------------------------------------------
# Single-slot schema builders render exactly one slot, keyed generically.
# ---------------------------------------------------------------------------


def test_custom_position_slot_schema_uses_generic_keys():
    from custom_components.adaptive_cover_pro.config_fields import (
        custom_position_slot_schema,
    )

    keys = _schema_keys(custom_position_slot_schema())
    assert CUSTOM_POSITION_FORM_KEYS["position"] in keys  # "custom_position"
    assert CUSTOM_POSITION_FORM_KEYS["priority"] in keys
    assert CUSTOM_POSITION_FORM_KEYS["sensors"] in keys
    # No suffixed slot keys leak onto the single-slot page.
    assert "custom_position_1" not in keys
    assert "custom_position_priority_2" not in keys


def test_custom_position_slot_schema_tilt_gated():
    from custom_components.adaptive_cover_pro.config_fields import (
        custom_position_slot_schema,
    )

    no_tilt = _schema_keys(custom_position_slot_schema(include_tilt=False))
    with_tilt = _schema_keys(custom_position_slot_schema(include_tilt=True))
    assert CUSTOM_POSITION_FORM_KEYS["tilt"] not in no_tilt
    assert CUSTOM_POSITION_FORM_KEYS["tilt"] in with_tilt
    # Global default/sunset tilt are NOT on a per-slot page (they live on their
    # own venetian sub-menu entry).
    assert "default_tilt" not in with_tilt
    assert "sunset_tilt" not in with_tilt


def test_blind_spot_slot_schema_slot1_keeps_required_sliver():
    from custom_components.adaptive_cover_pro.config_dynamic import (
        blind_spot_slot_schema,
    )

    schema = blind_spot_slot_schema(1, {"fov_left": 45, "fov_right": 45})
    keys = _schema_keys(schema)
    assert BLIND_SPOT_FORM_KEYS["left_gamma"] in keys
    assert "blind_spot_left_gamma_2" not in keys  # single slot only
    # Slot 1 markers are Required (legacy semantics, #247).
    required = {
        str(m) for m in schema.schema if getattr(m, "default", None) is not None
    }
    assert BLIND_SPOT_FORM_KEYS["left_gamma"] in required


def test_blind_spot_slot_schema_bounds_track_fov():
    from custom_components.adaptive_cover_pro.config_dynamic import (
        blind_spot_slot_schema,
    )

    schema = blind_spot_slot_schema(2, {"fov_left": 60, "fov_right": 40})
    for marker, sel in schema.schema.items():
        if str(marker) == BLIND_SPOT_FORM_KEYS["left_gamma"]:
            assert sel.config["min"] == -40  # -fov_right
            assert sel.config["max"] == 60  # fov_left


def test_glare_zone_slot_schema_uses_generic_keys():
    from custom_components.adaptive_cover_pro.config_dynamic import (
        glare_zone_slot_schema,
    )

    keys = _schema_keys(glare_zone_slot_schema(3, {}, None))
    assert GLARE_ZONE_FORM_KEYS["name"] in keys  # "glare_zone_name"
    assert GLARE_ZONE_FORM_KEYS["x"] in keys
    assert "glare_zone_3_name" not in keys  # no suffixed key on the page
    assert "enable_glare_zones" not in keys  # enable toggle lives on the sub-menu


# ---------------------------------------------------------------------------
# Options-flow slot pages write ONLY their own slot's suffixed storage keys.
# ---------------------------------------------------------------------------


def _options_flow(options: dict, sensor_type=CoverType.BLIND):
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": sensor_type}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.sensor_type = sensor_type
    flow.options = dict(options)
    return flow


@pytest.mark.asyncio
async def test_custom_position_slot_saves_only_its_slot():
    flow = _options_flow({})
    flow.async_step_custom_position = AsyncMock(return_value={"type": "menu"})
    # Open slot 3 (GET), then POST generic values.
    await flow.async_step_custom_position_slot_3(None)
    result = await flow.async_step_custom_position_slot(
        {
            CUSTOM_POSITION_FORM_KEYS["sensors"]: ["binary_sensor.x"],
            CUSTOM_POSITION_FORM_KEYS["position"]: 40,
            CUSTOM_POSITION_FORM_KEYS["priority"]: 77,
        }
    )
    assert result["type"] == "menu"
    assert flow.options["custom_position_3"] == 40
    assert flow.options["custom_position_priority_3"] == 77
    assert flow.options["custom_position_sensors_3"] == ["binary_sensor.x"]
    # No other slot and no generic key leaked into storage.
    assert "custom_position_1" not in flow.options
    assert "custom_position" not in flow.options
    assert CUSTOM_POSITION_FORM_KEYS["sensors"] not in flow.options


@pytest.mark.asyncio
async def test_glare_zone_slot_saves_only_its_slot():
    flow = _options_flow({})
    flow.async_step_glare_zones = AsyncMock(return_value={"type": "menu"})
    await flow.async_step_glare_zone_slot_2(None)
    await flow.async_step_glare_zone_slot(
        {
            GLARE_ZONE_FORM_KEYS["name"]: "Desk",
            GLARE_ZONE_FORM_KEYS["x"]: 0.5,
            GLARE_ZONE_FORM_KEYS["y"]: 1.0,
            GLARE_ZONE_FORM_KEYS["radius"]: 0.3,
            GLARE_ZONE_FORM_KEYS["z"]: 0.0,
        }
    )
    assert flow.options["glare_zone_2_name"] == "Desk"
    assert flow.options["glare_zone_2_x"] == 0.5
    assert "glare_zone_1_name" not in flow.options
    assert GLARE_ZONE_FORM_KEYS["name"] not in flow.options


@pytest.mark.asyncio
async def test_blind_spot_slot_saves_only_its_slot():
    flow = _options_flow({"fov_left": 45, "fov_right": 45})
    flow.async_step_blind_spot = AsyncMock(return_value={"type": "menu"})
    await flow.async_step_blind_spot_slot_2(None)
    await flow.async_step_blind_spot_slot(
        {
            BLIND_SPOT_FORM_KEYS["left_gamma"]: 35,
            BLIND_SPOT_FORM_KEYS["right_gamma"]: -15,
        }
    )
    assert flow.options["blind_spot_left_gamma_2"] == 35
    assert flow.options["blind_spot_right_gamma_2"] == -15
    assert "blind_spot_left_gamma" not in flow.options  # slot-1 untouched
