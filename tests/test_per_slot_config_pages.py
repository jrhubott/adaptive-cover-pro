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
    DEFAULT_CUSTOM_POSITION_ENABLED,
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


# ---------------------------------------------------------------------------
# Slot delete — full-key-map clear (issue #1071)
# ---------------------------------------------------------------------------


def _populate(options: dict, slot_keys: dict[str, str], marker: str) -> None:
    """Store a distinguishable value under every wire key a slot owns."""
    for sub, wire in slot_keys.items():
        options[wire] = f"{marker}:{sub}"


def test_clear_slot_pops_every_stored_key():
    from custom_components.adaptive_cover_pro.helpers import clear_slot

    for slots, target, sibling in (
        (CUSTOM_POSITION_SLOTS, 3, 1),
        (BLIND_SPOT_SLOTS, 2, 1),
        (GLARE_ZONE_SLOTS, 2, 1),
    ):
        options: dict = {}
        _populate(options, slots[target], "target")
        _populate(options, slots[sibling], "sibling")

        clear_slot(options, slots[target])

        for wire in slots[target].values():
            # Absent, not None — a present-but-falsy key is not a cleared slot.
            assert wire not in options, f"{wire} survived the clear"
        for wire in slots[sibling].values():
            assert options[wire].startswith("sibling"), f"{wire} was collateral damage"

    # Blind spot's legacy FOV-relative edges never appear on the form, so only a
    # full-key-map clear can reach them.
    options = {}
    _populate(options, BLIND_SPOT_SLOTS[2], "target")
    clear_slot(options, BLIND_SPOT_SLOTS[2])
    assert "blind_spot_left_2" not in options
    assert "blind_spot_right_2" not in options


def test_clear_slot_pops_enabled_rather_than_nulling_it():
    from custom_components.adaptive_cover_pro.helpers import clear_slot

    keys = CUSTOM_POSITION_SLOTS[3]
    options = {keys["enabled"]: False}

    clear_slot(options, keys)

    assert keys["enabled"] not in options
    # `enabled` is opt-out: a stored None would read as present-but-falsy and
    # leave the reused slot silently disabled.
    assert options.get(keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED) is True


def test_clear_custom_position_slot_drops_the_legacy_sensor_mirror():
    from custom_components.adaptive_cover_pro.helpers import clear_custom_position_slot

    slot1 = CUSTOM_POSITION_SLOTS[1]
    slot3 = CUSTOM_POSITION_SLOTS[3]
    options = {
        slot1["sensors"]: ["binary_sensor.one"],
        slot1["sensor"]: "binary_sensor.one",
        slot3["sensors"]: ["binary_sensor.x"],
        slot3["sensor"]: "binary_sensor.x",
    }

    clear_custom_position_slot(options, 3)

    assert slot3["sensors"] not in options
    assert slot3["sensor"] not in options
    assert options[slot1["sensors"]] == ["binary_sensor.one"]
    assert options[slot1["sensor"]] == "binary_sensor.one"


# A value for every sub-key a custom-position slot owns. `sensor` mirrors
# `sensors[0]` so the mirror pass leaves an untouched slot byte-identical.
_CUSTOM_POSITION_SAMPLE: dict[str, object] = {
    "name": "Movie night",
    "sensor": "binary_sensor.trigger",
    "sensors": ["binary_sensor.trigger"],
    "template": "{{ true }}",
    "template_mode": "or",
    "position": 25,
    "priority": 60,
    "min_mode": True,
    "use_my": True,
    "tilt": 30,
    "tilt_only": True,
    "position_max": 80,
    "tilt_min": 10,
    "tilt_max": 90,
    "outside_window": True,
    "enabled": False,
}


def _seed_custom_position(options: dict, n: int) -> None:
    """Populate every stored key of custom-position slot *n*."""
    keys = CUSTOM_POSITION_SLOTS[n]
    # A newly added sub-key must be covered here or the delete guard goes blind.
    assert set(_CUSTOM_POSITION_SAMPLE) == set(keys)
    for sub, value in _CUSTOM_POSITION_SAMPLE.items():
        options[keys[sub]] = value


@pytest.mark.asyncio
async def test_delete_slot_step_clears_only_its_slot():
    # ── custom positions ───────────────────────────────────────────────
    options: dict = {}
    _seed_custom_position(options, 2)
    _seed_custom_position(options, 3)
    flow = _options_flow(options)
    flow.async_step_custom_position = AsyncMock(return_value={"type": "menu"})

    result = await flow.async_step_delete_custom_position_slot_3()

    assert result["type"] == "menu"
    for wire in CUSTOM_POSITION_SLOTS[3].values():
        assert wire not in flow.options, f"{wire} survived the delete"
    for sub, wire in CUSTOM_POSITION_SLOTS[2].items():
        assert flow.options[wire] == _CUSTOM_POSITION_SAMPLE[sub]

    # ── blind spots ────────────────────────────────────────────────────
    options = {}
    _populate(options, BLIND_SPOT_SLOTS[1], "sibling")
    _populate(options, BLIND_SPOT_SLOTS[2], "target")
    flow = _options_flow(options)
    flow.async_step_blind_spot = AsyncMock(return_value={"type": "menu"})

    result = await flow.async_step_delete_blind_spot_slot_2()

    assert result["type"] == "menu"
    for wire in BLIND_SPOT_SLOTS[2].values():
        assert wire not in flow.options, f"{wire} survived the delete"
    # The legacy FOV-relative edges are not on the form; only a full-key-map
    # clear reaches them.
    assert "blind_spot_left_2" not in flow.options
    assert "blind_spot_right_2" not in flow.options
    for wire in BLIND_SPOT_SLOTS[1].values():
        assert flow.options[wire].startswith("sibling")

    # ── glare zones ────────────────────────────────────────────────────
    options = {}
    _populate(options, GLARE_ZONE_SLOTS[1], "sibling")
    _populate(options, GLARE_ZONE_SLOTS[2], "target")
    flow = _options_flow(options)
    flow.async_step_glare_zones = AsyncMock(return_value={"type": "menu"})

    result = await flow.async_step_delete_glare_zone_slot_2()

    assert result["type"] == "menu"
    for wire in GLARE_ZONE_SLOTS[2].values():
        assert wire not in flow.options, f"{wire} survived the delete"
    for wire in GLARE_ZONE_SLOTS[1].values():
        assert flow.options[wire].startswith("sibling")


@pytest.mark.asyncio
async def test_delete_chooser_lists_configured_slots_and_cancel():
    options: dict = {}
    _seed_custom_position(options, 1)
    _seed_custom_position(options, 3)
    flow = _options_flow(options)

    result = await flow.async_step_delete_custom_position()

    assert result["step_id"] == "delete_slot"
    menu = result["menu_options"]
    assert "delete_custom_position_slot_1" in menu
    assert "delete_custom_position_slot_3" in menu
    assert "delete_custom_position_slot_2" not in menu  # unconfigured
    assert "custom_position" in menu  # ⬅️ Cancel → the area sub-menu

    # Glare zones cancel back to the plural historical step id.
    flow = _options_flow({GLARE_ZONE_SLOTS[2]["name"]: "Desk"})

    result = await flow.async_step_delete_glare_zone()

    assert result["step_id"] == "delete_slot"
    menu = result["menu_options"]
    assert "delete_glare_zone_slot_2" in menu
    assert "glare_zones" in menu
    assert "glare_zone" not in menu


def test_slot_menu_offers_delete_only_when_a_slot_is_configured():
    flow = _options_flow({})

    empty = flow._slot_menu("custom_position")["menu_options"]
    assert "delete_custom_position" not in empty
    assert "add_custom_position" in empty

    options: dict = {}
    _seed_custom_position(options, 1)
    flow = _options_flow(options)

    populated = flow._slot_menu("custom_position")["menu_options"]
    assert "delete_custom_position" in populated
    assert "add_custom_position" in populated
    assert list(populated)[-1] == "init"  # Back stays last


def test_getattr_rejects_non_slot_delete_names():
    flow = _options_flow({})

    assert hasattr(flow, "async_step_delete_bogus") is False
    assert hasattr(flow, "async_step_delete_glare_zones") is False
    assert hasattr(flow, "async_step_delete_custom_position_slot_x") is False


@pytest.mark.asyncio
async def test_add_slot_wipes_inert_keys_before_reuse():
    # A slot holding only inert leftovers is NOT configured, so it never shows
    # on the menu and Delete cannot reach it — but Add hands it straight back.
    # "Inert" means the single-slot page cannot render it: the card-controlled
    # `enabled` flag and the legacy single-sensor mirror.
    slot1 = CUSTOM_POSITION_SLOTS[1]
    inert = {
        slot1["enabled"]: False,
        slot1["sensor"]: "binary_sensor.stale",
    }
    flow = _options_flow(dict(inert))
    assert flow._lowest_free_slot("custom_position") == 1

    result = await flow.async_step_add_custom_position()

    for wire in inert:
        assert wire not in flow.options, f"{wire} leaked into the reused slot"
    assert result["type"] == "form"
    assert result["step_id"] == "custom_position_slot"
    assert result["description_placeholders"]["slot"] == "1"


def _suggested(result) -> dict[str, object]:
    """Map schema key → the value seeded back onto the rendered form."""
    return {
        str(marker): marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if getattr(marker, "description", None)
        and "suggested_value" in marker.description
    }


@pytest.mark.asyncio
async def test_add_slot_preserves_partial_form_data_but_drops_inert_keys():
    """Add must not throw away a half-finished slot's entered data (#1071).

    ``_lowest_free_slot`` returns the lowest slot failing its *configured*
    gate, and "not configured" is not "empty": a custom position with a
    trigger but no axis claim, a blind spot with an elevation but no gammas, a
    glare zone with geometry but no name are all real states a user can leave
    behind — and none of them shows on the sub-menu, so Add is the only route
    back in. Add therefore clears only the sub-keys the page cannot render;
    every form key is seeded straight back so the user can finish or clear it.
    """
    # ── custom positions: trigger + name + template, no axis claim ──────
    slot1 = CUSTOM_POSITION_SLOTS[1]
    flow = _options_flow(
        {
            slot1["sensors"]: ["binary_sensor.trigger"],
            slot1["name"]: "Movie night",
            slot1["template"]: "{{ true }}",
            # BooleanSelectors on the page — pre-filled, so not silent.
            slot1["min_mode"]: True,
            slot1["use_my"]: True,
            # Not on this page: `enabled` has no field on any cover type, and a
            # blind's page hides the tilt block. Both must go.
            slot1["tilt_only"]: True,
            slot1["enabled"]: False,
        }
    )
    assert flow._lowest_free_slot("custom_position") == 1

    result = await flow.async_step_add_custom_position()

    assert flow.options[slot1["sensors"]] == ["binary_sensor.trigger"]
    assert flow.options[slot1["name"]] == "Movie night"
    assert flow.options[slot1["template"]] == "{{ true }}"
    assert flow.options[slot1["min_mode"]] is True
    assert flow.options[slot1["use_my"]] is True
    # Never on the form, so nothing could show or reset it — it must go. The
    # card-controlled `enabled` flag has no UI on any cover type; `tilt_only`
    # has none on THIS one, because a blind's page hides the tilt block (see
    # the include_tilt pair below).
    assert slot1["enabled"] not in flow.options
    assert slot1["tilt_only"] not in flow.options
    # The legacy mirror is re-derived from the surviving list, not left stale.
    assert flow.options[slot1["sensor"]] == "binary_sensor.trigger"
    assert result["type"] == "form"
    assert result["description_placeholders"]["slot"] == "1"
    # The user-visible half: the rendered form carries the values back.
    suggested = _suggested(result)
    assert suggested[CUSTOM_POSITION_FORM_KEYS["name"]] == "Movie night"
    assert suggested[CUSTOM_POSITION_FORM_KEYS["sensors"]] == ["binary_sensor.trigger"]
    assert suggested[CUSTOM_POSITION_FORM_KEYS["template"]] == "{{ true }}"
    assert suggested[CUSTOM_POSITION_FORM_KEYS["min_mode"]] is True
    assert suggested[CUSTOM_POSITION_FORM_KEYS["use_my"]] is True

    # ── blind spots: elevation set, gammas unset → not configured ───────
    bs1 = BLIND_SPOT_SLOTS[1]
    flow = _options_flow(
        {
            "fov_left": 45,
            "fov_right": 45,
            bs1["elevation"]: 20,
            bs1["elevation_mode"]: "below",
            # Legacy FOV-relative edges — migration-read-only, never rendered.
            bs1["left"]: 10,
            bs1["right"]: 12,
        }
    )
    assert flow._lowest_free_slot("blind_spot") == 1

    result = await flow.async_step_add_blind_spot()

    assert flow.options[bs1["elevation"]] == 20
    assert flow.options[bs1["elevation_mode"]] == "below"
    assert bs1["left"] not in flow.options
    assert bs1["right"] not in flow.options
    assert result["type"] == "form"
    assert result["description_placeholders"]["slot"] == "1"
    suggested = _suggested(result)
    assert suggested[BLIND_SPOT_FORM_KEYS["elevation"]] == 20
    assert suggested[BLIND_SPOT_FORM_KEYS["elevation_mode"]] == "below"

    # ── glare zones: geometry set, name blank → not configured ──────────
    # Form keys and slot keys are set-identical here, so Add pops nothing.
    gz1 = GLARE_ZONE_SLOTS[1]
    assert set(GLARE_ZONE_FORM_KEYS) == set(gz1)
    flow = _options_flow({gz1["x"]: 0.5, gz1["y"]: 1.0, gz1["radius"]: 0.3})
    assert flow._lowest_free_slot("glare_zone") == 1

    result = await flow.async_step_add_glare_zone()

    assert flow.options[gz1["x"]] == 0.5
    assert flow.options[gz1["y"]] == 1.0
    assert flow.options[gz1["radius"]] == 0.3
    assert result["type"] == "form"
    assert result["description_placeholders"]["slot"] == "1"
    suggested = _suggested(result)
    assert suggested[GLARE_ZONE_FORM_KEYS["x"]] == 0.5
    assert suggested[GLARE_ZONE_FORM_KEYS["radius"]] == 0.3


# The tilt block a custom-position slot page renders only when the policy says
# this cover type has tilt. Seeded under slot 1 with no trigger, so the slot
# stays *unconfigured* — invisible on the menu, reachable only via "➕ Add…".
_TILT_SEED: dict[str, object] = {
    "tilt": 30,
    "tilt_only": True,
    "tilt_min": 10,
    "tilt_max": 90,
}


@pytest.mark.asyncio
async def test_add_slot_drops_tilt_keys_when_the_page_hides_them():
    """Add's keep-set means rendered *here*, not merely in the key map (#1071).

    ``custom_position_slot_schema(include_tilt=False)`` emits no tilt markers, so
    on the ten cover types without tilt the four tilt sub-keys have no UI at all
    — the user cannot see or reset them and HA cannot put them in ``user_input``.
    A ``set_option`` call can still store them (they are neither a trigger nor a
    claim key, so the slot stays unconfigured and Delete cannot reach it), and a
    spared ``tilt_only`` makes the freshly configured slot claim nothing on
    either axis with nothing in the form to explain why. Add must drop them.
    """
    slot1 = CUSTOM_POSITION_SLOTS[1]
    flow = _options_flow(
        {slot1[sub]: value for sub, value in _TILT_SEED.items()}, CoverType.BLIND
    )
    assert flow._custom_position_include_tilt() is False
    assert flow._lowest_free_slot("custom_position") == 1

    result = await flow.async_step_add_custom_position()

    for sub in _TILT_SEED:
        assert (
            slot1[sub] not in flow.options
        ), f"{slot1[sub]} survived Add with no field on the page to reset it"
    assert result["type"] == "form"
    assert result["step_id"] == "custom_position_slot"
    assert result["description_placeholders"]["slot"] == "1"


@pytest.mark.asyncio
async def test_add_slot_keeps_tilt_keys_when_the_page_renders_them():
    """The mirror case: on a venetian the tilt block IS the user's own data.

    Dropping it there would be the over-correction — the page renders every one
    of these fields, so Add must seed them straight back like any other form key.
    """
    slot1 = CUSTOM_POSITION_SLOTS[1]
    flow = _options_flow(
        {slot1[sub]: value for sub, value in _TILT_SEED.items()}, CoverType.VENETIAN
    )
    assert flow._custom_position_include_tilt() is True
    assert flow._lowest_free_slot("custom_position") == 1

    result = await flow.async_step_add_custom_position()

    for sub, value in _TILT_SEED.items():
        assert flow.options[slot1[sub]] == value, f"{slot1[sub]} was wiped by Add"
    assert result["type"] == "form"
    suggested = _suggested(result)
    for sub, value in _TILT_SEED.items():
        assert suggested[CUSTOM_POSITION_FORM_KEYS[sub]] == value


@pytest.mark.asyncio
async def test_delete_slot_still_pops_form_keys():
    """Delete is the other half of the split: it erases the FULL key map.

    Add spares form keys so half-finished work survives a reuse; Delete means
    "this slot is gone", so every key it owns goes — form keys included.
    """
    slot3 = CUSTOM_POSITION_SLOTS[3]
    options: dict = {}
    _seed_custom_position(options, 3)
    flow = _options_flow(options)
    flow.async_step_custom_position = AsyncMock(return_value={"type": "menu"})

    await flow.async_step_delete_custom_position_slot_3()

    for sub in CUSTOM_POSITION_FORM_KEYS:
        assert slot3[sub] not in flow.options, f"{slot3[sub]} survived the delete"

    bs2 = BLIND_SPOT_SLOTS[2]
    flow = _options_flow({bs2[sub]: 5 for sub in bs2})
    flow.async_step_blind_spot = AsyncMock(return_value={"type": "menu"})

    await flow.async_step_delete_blind_spot_slot_2()

    for sub in BLIND_SPOT_FORM_KEYS:
        assert bs2[sub] not in flow.options, f"{bs2[sub]} survived the delete"

    gz2 = GLARE_ZONE_SLOTS[2]
    flow = _options_flow({gz2[sub]: "x" for sub in gz2})
    flow.async_step_glare_zones = AsyncMock(return_value={"type": "menu"})

    await flow.async_step_delete_glare_zone_slot_2()

    for sub in GLARE_ZONE_FORM_KEYS:
        assert gz2[sub] not in flow.options, f"{gz2[sub]} survived the delete"


@pytest.mark.asyncio
async def test_delete_slot_step_without_an_area_falls_back_to_the_root_menu():
    """A bare ``delete_slot`` re-render has no area yet — show init, not blow up.

    ``_delete_area`` is only set by the chooser, so an early or replayed
    ``delete_slot`` step would index ``_SLOT_AREA_META[""]`` and raise.
    """
    flow = _options_flow({})
    # async_step_init reads the live entry through HA's flow plumbing.
    flow.handler = "delete_slot_fallback"
    flow.hass.config_entries.async_entries.return_value = []

    result = await flow.async_step_delete_slot()

    assert result["type"] == "menu"
    assert result["step_id"] == "init"
