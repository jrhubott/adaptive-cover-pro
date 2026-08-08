"""Tests for the cm → metres config-entry migration (VERSION 1 → 2).

Exercises async_migrate_entry directly to verify:
- v1 entries (window_width and glare-zone coords in cm) are divided by 100
- Entries already in metres (sentinel ≤ 5) are not re-divided (idempotent)
- Version is bumped to 2 in every case
- Entries with no affected fields are left unchanged aside from the version bump
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import async_migrate_entry
from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_SENSOR_TYPE,
    CONF_WINDOW_WIDTH,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


def _make_entry(
    hass: HomeAssistant,
    options: dict,
    version: int = 1,
    minor_version: int = 1,
    sensor_type=CoverType.BLIND,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Migration Test", CONF_SENSOR_TYPE: sensor_type},
        options=options,
        version=version,
        minor_version=minor_version,
        title="Migration Test",
    )
    entry.add_to_hass(hass)
    return entry


async def test_v1_window_width_converted_to_metres(hass: HomeAssistant) -> None:
    """CONF_WINDOW_WIDTH of 100 cm becomes 1.0 m (and migration cascades to v3)."""
    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 100})
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.0
    assert entry.version == 3


async def test_v1_glare_zone_coordinates_converted_to_metres(
    hass: HomeAssistant,
) -> None:
    """All four glare-zone slot coordinates are divided by 100."""
    options = {
        CONF_WINDOW_WIDTH: 150,
        "glare_zone_1_name": "Desk",
        "glare_zone_1_x": 50,
        "glare_zone_1_y": 200,
        "glare_zone_1_radius": 30,
        "glare_zone_2_x": -80,
        "glare_zone_2_y": 300,
        "glare_zone_2_radius": 50,
    }
    entry = _make_entry(hass, options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.5
    assert entry.options["glare_zone_1_x"] == 0.5
    assert entry.options["glare_zone_1_y"] == 2.0
    assert entry.options["glare_zone_1_radius"] == 0.3
    assert entry.options["glare_zone_2_x"] == -0.8
    assert entry.options["glare_zone_2_y"] == 3.0
    assert entry.options["glare_zone_2_radius"] == 0.5
    # Name is untouched by the numeric migration
    assert entry.options["glare_zone_1_name"] == "Desk"


async def test_values_at_or_below_sentinel_left_alone(hass: HomeAssistant) -> None:
    """Stored values ≤ 5 are assumed to already be metres and not re-divided."""
    options = {
        CONF_WINDOW_WIDTH: 1.2,  # already metres
        "glare_zone_1_x": 0.5,
        "glare_zone_1_y": 2.0,
        "glare_zone_1_radius": 0.3,
    }
    entry = _make_entry(hass, options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.2
    assert entry.options["glare_zone_1_x"] == 0.5
    assert entry.options["glare_zone_1_y"] == 2.0
    assert entry.options["glare_zone_1_radius"] == 0.3
    assert entry.version == 3


async def test_migration_is_idempotent(hass: HomeAssistant) -> None:
    """Running the migration twice (second time on a v3 entry) is a no-op."""
    entry = _make_entry(
        hass,
        {CONF_WINDOW_WIDTH: 200, "glare_zone_1_y": 150},
    )
    await async_migrate_entry(hass, entry)
    snapshot = dict(entry.options)
    # Second run — entry is already at head so migration short-circuits
    await async_migrate_entry(hass, entry)
    assert entry.options == snapshot
    assert entry.version == 3


async def test_migration_with_no_affected_fields_only_bumps_version(
    hass: HomeAssistant,
) -> None:
    """An entry with no window_width or glare zones gets its version bumped and toggle set."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    options = {"azimuth": 180, "fov_left": 90, "fov_right": 90}
    entry = _make_entry(hass, options)
    await async_migrate_entry(hass, entry)
    # Original fields untouched, plus toggle defaulted to True for the upgrade.
    assert entry.options["azimuth"] == 180
    assert entry.options["fov_left"] == 90
    assert entry.options["fov_right"] == 90
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is True
    assert entry.version == 3


async def test_negative_x_coordinate_migrated(hass: HomeAssistant) -> None:
    """Negative cm values (offset left of window centre) migrate correctly."""
    entry = _make_entry(hass, {"glare_zone_1_x": -150})
    await async_migrate_entry(hass, entry)
    assert entry.options["glare_zone_1_x"] == -1.5


async def test_zero_values_preserved(hass: HomeAssistant) -> None:
    """A value of 0 is within the sentinel band and stays 0."""
    entry = _make_entry(
        hass,
        {"glare_zone_1_x": 0, "glare_zone_1_y": 0, CONF_WINDOW_WIDTH: 120},
    )
    await async_migrate_entry(hass, entry)
    assert entry.options["glare_zone_1_x"] == 0
    assert entry.options["glare_zone_1_y"] == 0
    assert entry.options[CONF_WINDOW_WIDTH] == 1.2


# ---------------------------------------------------------------------------
# Migration: v2 → v3 — enable My-preset entities by default for existing entries
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_sets_my_position_entities_true_for_existing_entry(
    hass: HomeAssistant,
) -> None:
    """Existing v2 entries get enable_my_position_entities=True so behaviour is preserved."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    entry = _make_entry(hass, {"my_position_value": 50}, version=2)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is True
    assert entry.version == 3


async def test_migrate_v3_no_op_when_key_already_set_true(
    hass: HomeAssistant,
) -> None:
    """If the key is already True on a v2 entry, the migration leaves it untouched."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    entry = _make_entry(
        hass,
        {CONF_ENABLE_MY_POSITION_ENTITIES: True, "my_position_value": 60},
        version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is True
    assert entry.version == 3


async def test_migrate_v3_no_op_when_key_already_set_false(
    hass: HomeAssistant,
) -> None:
    """If the key is already False on a v2 entry, the migration leaves it untouched."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    entry = _make_entry(
        hass,
        {CONF_ENABLE_MY_POSITION_ENTITIES: False},
        version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is False
    assert entry.version == 3


async def test_migrate_v1_cascades_through_v3(hass: HomeAssistant) -> None:
    """A genuine v1 entry runs through cm→m migration AND v2→v3 toggle setdefault."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 200}, version=1)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 2.0  # cm → m applied
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is True  # toggle preserved
    assert entry.version == 3


# ---------------------------------------------------------------------------
# Migration: v3.1 → v3.2 — force override merged into custom-position slot 5
# (issue #563). Additive + rollback-safe: legacy keys must survive untouched.
# ---------------------------------------------------------------------------

from custom_components.adaptive_cover_pro.const import (  # noqa: E402
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
)

_SLOT5 = CUSTOM_POSITION_SLOTS[5]
_FORCE_OPTIONS = {
    CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.rain", "binary_sensor.alarm"],
    CONF_FORCE_OVERRIDE_POSITION: 90,
    CONF_FORCE_OVERRIDE_MIN_MODE: True,
}


async def test_migrate_v3_2_copies_force_override_into_slot_5(
    hass: HomeAssistant,
) -> None:
    """Force override config lands in slot 5 at safety priority."""
    entry = _make_entry(hass, dict(_FORCE_OPTIONS), version=3, minor_version=1)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[_SLOT5["sensors"]] == [
        "binary_sensor.rain",
        "binary_sensor.alarm",
    ]
    assert entry.options[_SLOT5["position"]] == 90
    assert entry.options[_SLOT5["priority"]] == CUSTOM_POSITION_SAFETY_PRIORITY
    assert entry.options[_SLOT5["min_mode"]] is True
    assert entry.version == 3
    assert entry.minor_version == 18


async def test_migrate_v3_2_preserves_legacy_keys_for_rollback(
    hass: HomeAssistant,
) -> None:
    """Legacy force_override_* and custom_position_sensor_N keys are byte-identical.

    Rollback contract: an older release must find its config exactly as it
    left it — the old ForceOverrideHandler reads the legacy keys and ignores
    the slot-5 keys (it only iterates slots 1–4). The v3.3 migration promotes
    the slot-1 legacy key into the list key so the multi-select prefills;
    the legacy key itself is left intact.
    """
    options = {
        **_FORCE_OPTIONS,
        "custom_position_sensor_1": "binary_sensor.table",
        "custom_position_1": 60,
    }
    entry = _make_entry(hass, dict(options), version=3, minor_version=1)
    await async_migrate_entry(hass, entry)
    for key, value in options.items():
        assert entry.options[key] == value, f"legacy key {key} changed"
    # v3.3 migration promotes the legacy sensor key into the list key for slot 1.
    assert entry.options[CUSTOM_POSITION_SLOTS[1]["sensors"]] == ["binary_sensor.table"]
    # Slots 2–4 had no legacy sensor configured — no list key is created.
    for slot_n in (2, 3, 4):
        assert CUSTOM_POSITION_SLOTS[slot_n]["sensors"] not in entry.options


async def test_migrate_v3_2_no_force_config_is_a_noop(hass: HomeAssistant) -> None:
    """Absent force override config → minor bumps to 4 (through v3.4), slot 5 stays free."""
    entry = _make_entry(hass, {"azimuth": 180}, version=3, minor_version=1)
    await async_migrate_entry(hass, entry)
    assert _SLOT5["sensors"] not in entry.options
    assert _SLOT5["position"] not in entry.options
    assert entry.minor_version == 18


async def test_migrate_v3_2_empty_sensor_list_is_a_noop(hass: HomeAssistant) -> None:
    """An empty force_override_sensors list does not create slot 5."""
    entry = _make_entry(
        hass,
        {CONF_FORCE_OVERRIDE_SENSORS: [], CONF_FORCE_OVERRIDE_POSITION: 50},
        version=3,
        minor_version=1,
    )
    await async_migrate_entry(hass, entry)
    assert _SLOT5["sensors"] not in entry.options
    assert entry.minor_version == 18


async def test_migrate_v3_2_missing_position_defaults_to_zero(
    hass: HomeAssistant,
) -> None:
    """Sensors without a configured position default to 0 (old snapshot default)."""
    entry = _make_entry(
        hass,
        {CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.rain"]},
        version=3,
        minor_version=1,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[_SLOT5["position"]] == 0
    assert entry.options[_SLOT5["min_mode"]] is False


async def test_migrate_v1_cascades_through_v3_2(hass: HomeAssistant) -> None:
    """A v1 entry with force override config ends at 3.4 with slot 5 populated."""
    entry = _make_entry(
        hass,
        {CONF_WINDOW_WIDTH: 200, **_FORCE_OPTIONS},
        version=1,
    )
    await async_migrate_entry(hass, entry)
    assert entry.version == 3
    assert entry.minor_version == 18
    assert entry.options[CONF_WINDOW_WIDTH] == 2.0
    assert entry.options[_SLOT5["priority"]] == CUSTOM_POSITION_SAFETY_PRIORITY


async def test_migrate_v3_2_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running migration on a 3.2 entry changes nothing (slot-5 edits survive)."""
    entry = _make_entry(hass, dict(_FORCE_OPTIONS), version=3, minor_version=1)
    await async_migrate_entry(hass, entry)
    # User later edits slot 5 through the new UI…
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, _SLOT5["position"]: 25}
    )
    snapshot = dict(entry.options)
    # …a second migration run must not clobber it.
    await async_migrate_entry(hass, entry)
    assert entry.options == snapshot


# ---------------------------------------------------------------------------
# Migration: v3.2 → v3.3 — copy legacy custom_position_sensor_N into list key
# (issue #563 trailing defect). Additive + rollback-safe.
# ---------------------------------------------------------------------------


async def test_migrate_v3_3_copies_legacy_single_sensor_into_list(
    hass: HomeAssistant,
) -> None:
    """Legacy single-sensor key is promoted into the new list key on migration."""
    entry = _make_entry(
        hass,
        {"custom_position_sensor_1": "binary_sensor.table", "custom_position_1": 10},
        version=3,
        minor_version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CUSTOM_POSITION_SLOTS[1]["sensors"]] == ["binary_sensor.table"]
    assert entry.minor_version == 18


async def test_migrate_v3_3_leaves_legacy_key_intact(hass: HomeAssistant) -> None:
    """Migration is additive: the legacy sensor key is NOT removed."""
    entry = _make_entry(
        hass,
        {"custom_position_sensor_1": "binary_sensor.table", "custom_position_1": 10},
        version=3,
        minor_version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CUSTOM_POSITION_SLOTS[1]["sensor"]] == "binary_sensor.table"


async def test_migrate_v3_3_does_not_overwrite_existing_list(
    hass: HomeAssistant,
) -> None:
    """If sensors list already exists it is left unchanged."""
    entry = _make_entry(
        hass,
        {
            "custom_position_sensor_1": "binary_sensor.a",
            "custom_position_sensors_1": ["binary_sensor.b"],
            "custom_position_1": 10,
        },
        version=3,
        minor_version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options["custom_position_sensors_1"] == ["binary_sensor.b"]


async def test_migrate_v3_3_no_legacy_is_noop(hass: HomeAssistant) -> None:
    """No legacy sensor keys → minor bumps to 4, no sensors_N list created."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=2,
    )
    await async_migrate_entry(hass, entry)
    assert entry.minor_version == 18
    for slot_n in (1, 2, 3, 4, 5):
        assert CUSTOM_POSITION_SLOTS[slot_n]["sensors"] not in entry.options


# ---------------------------------------------------------------------------
# Migration: v3.3 → v3.4 — enable position matching by default for existing
# entries so upgrades keep the old reconcile/chase behavior (issue #591, #606).
# Additive: the key is only filled when absent.
# ---------------------------------------------------------------------------

from custom_components.adaptive_cover_pro.const import (  # noqa: E402
    CONF_ENABLE_POSITION_MATCHING,
)


async def test_migrate_v3_4_sets_position_matching_true_for_existing_entry(
    hass: HomeAssistant,
) -> None:
    """A pre-existing entry without the key gets position matching enabled."""
    entry = _make_entry(hass, {"azimuth": 180}, version=3, minor_version=3)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_ENABLE_POSITION_MATCHING] is True
    assert entry.minor_version == 18


async def test_migrate_v3_4_no_op_when_key_already_true(hass: HomeAssistant) -> None:
    """An explicit True is left untouched."""
    entry = _make_entry(
        hass,
        {CONF_ENABLE_POSITION_MATCHING: True},
        version=3,
        minor_version=3,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_POSITION_MATCHING] is True
    assert entry.minor_version == 18


async def test_migrate_v3_4_no_op_when_key_already_false(hass: HomeAssistant) -> None:
    """A user/new-install opt-out (False) is respected, not clobbered to True."""
    entry = _make_entry(
        hass,
        {CONF_ENABLE_POSITION_MATCHING: False},
        version=3,
        minor_version=3,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_POSITION_MATCHING] is False
    assert entry.minor_version == 18


async def test_migrate_v1_cascades_to_position_matching(hass: HomeAssistant) -> None:
    """A genuine v1 entry ends at 3.4 with position matching enabled."""
    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 200}, version=1)
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_POSITION_MATCHING] is True
    assert entry.version == 3
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# Migration: v3.5 → v3.6 — enable the weather override by default for every
# pre-existing entry so upgrading covers keep firing weather safety overrides
# (issue #719). New installs default OFF via the config-flow schema. Additive +
# rollback-safe: the key is only filled when absent.
# ---------------------------------------------------------------------------

from custom_components.adaptive_cover_pro.const import (  # noqa: E402
    CONF_WEATHER_ENABLED,
)


async def test_migrate_v3_6_sets_weather_enabled_true_for_existing_entry(
    hass: HomeAssistant,
) -> None:
    """A pre-existing minor-5 entry without the key gets weather override enabled."""
    entry = _make_entry(hass, {"azimuth": 180}, version=3, minor_version=5)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WEATHER_ENABLED] is True
    assert entry.minor_version == 18


async def test_migrate_v3_6_no_op_when_key_already_false(hass: HomeAssistant) -> None:
    """A pre-set False (idempotent re-run / explicit opt-out) is not clobbered."""
    entry = _make_entry(
        hass,
        {CONF_WEATHER_ENABLED: False},
        version=3,
        minor_version=5,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_WEATHER_ENABLED] is False
    assert entry.minor_version == 18


async def test_migrate_v3_6_explicit_true_survives(hass: HomeAssistant) -> None:
    """An explicit True is left untouched."""
    entry = _make_entry(
        hass,
        {CONF_WEATHER_ENABLED: True},
        version=3,
        minor_version=5,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_WEATHER_ENABLED] is True
    assert entry.minor_version == 18


async def test_migrate_v1_cascades_to_weather_enabled(hass: HomeAssistant) -> None:
    """A genuine v1 entry ends at 3.6 with the weather override enabled."""
    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 200}, version=1)
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_WEATHER_ENABLED] is True
    assert entry.version == 3
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# Migration: v3.6 → v3.7 — no-op minor bump for the additive outside_temp_source
# option (issue #547). An absent key already reads as "live" (the default), so
# nothing needs seeding; the block only advances a stale minor-6 entry to 7.
# ---------------------------------------------------------------------------


async def test_migrate_v3_6_to_3_7_is_noop_bump(hass: HomeAssistant) -> None:
    """A minor-6 entry advances to minor 7 without altering any v3.7-owned option."""
    entry = _make_entry(
        hass,
        {"azimuth": 180, CONF_WEATHER_ENABLED: True},
        version=3,
        minor_version=6,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    # Additive/no-op: no outside_temp_source key seeded, options untouched.
    assert "outside_temp_source" not in entry.options
    # Cascades on through v3.13 too, which seeds default_percentage (#1126).
    assert entry.options == {**before, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_6_to_3_7_is_idempotent(hass: HomeAssistant) -> None:
    """Running the migration twice on a minor-6 entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=6,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_6_to_3_7_preserves_explicit_source(
    hass: HomeAssistant,
) -> None:
    """An entry that already set outside_temp_source keeps its value."""
    entry = _make_entry(
        hass,
        {"outside_temp_source": "max_of_live_and_forecast"},
        version=3,
        minor_version=6,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options["outside_temp_source"] == "max_of_live_and_forecast"
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# Migration: v3.4 → v3.5 — no-op minor bump. This block formerly seeded the
# now-removed CONF_SHOW_WEATHER_RETRACTION toggle; the toggle is gone (the
# retraction pickers are always shown), so the block only advances a stale
# minor-4 entry to minor 5 without touching its options.
# ---------------------------------------------------------------------------


async def test_migrate_v3_4_bumps_through_minor_5_without_seeding(
    hass: HomeAssistant,
) -> None:
    """A minor-4 entry cascades through minor 5 gaining no retraction-toggle key.

    The v3.4→v3.5 block is a no-op (it must not seed the removed
    show_weather_retraction key). The entry continues cascading through
    v3.5→v3.6 (weather_enabled=True) and v3.12→v3.13, which seeds
    default_percentage to this awning's polarity-flipped no-coverage endpoint,
    0 (#1126).
    """
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=4,
        sensor_type=CoverType.AWNING,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    # No dead key seeded by the v3.4→v3.5 block.
    assert "show_weather_retraction" not in entry.options
    assert entry.options == {
        **before,
        CONF_WEATHER_ENABLED: True,
        CONF_DEFAULT_HEIGHT: 0,
    }


# ---------------------------------------------------------------------------
# Migration: v3.7 → v3.8 — additively convert legacy FOV-relative blind-spot
# edges to signed gamma from the window normal (issue #247). New keys are
# setdefault-seeded per slot; legacy keys are retained (rollback-safe).
# ---------------------------------------------------------------------------


async def test_migrate_v3_7_to_v3_8_converts_blind_spots(hass: HomeAssistant) -> None:
    """A v3.7 entry gains signed-gamma keys; legacy keys are preserved.

    fov_left=45 → slot-1 legacy 10/30 converts to gamma 35/-15; slot-2 40/60
    converts to 5/-15. A slot missing one edge (slot 3 left only) is skipped.
    """
    entry = _make_entry(
        hass,
        {
            "fov_left": 45,
            "fov_right": 45,
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
            "blind_spot_left_2": 40,
            "blind_spot_right_2": 60,
            "blind_spot_left_3": 20,  # no right_3 → slot 3 skipped
        },
        version=3,
        minor_version=7,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    opts = entry.options
    # Slot 1 converted (new_left = 45-10 = 35, new_right = 30-45 = -15).
    assert opts["blind_spot_left_gamma"] == 35
    assert opts["blind_spot_right_gamma"] == -15
    # Slot 2 converted (new_left = 45-40 = 5, new_right = 60-45 = 15).
    assert opts["blind_spot_left_gamma_2"] == 5
    assert opts["blind_spot_right_gamma_2"] == 15
    # Slot 3 incomplete → no gamma keys seeded.
    assert "blind_spot_left_gamma_3" not in opts
    assert "blind_spot_right_gamma_3" not in opts
    # Legacy keys retained unchanged (additive / rollback-safe).
    assert opts["blind_spot_left"] == 10
    assert opts["blind_spot_right"] == 30
    assert opts["blind_spot_left_2"] == 40
    assert opts["blind_spot_right_2"] == 60


async def test_migrate_v3_7_to_v3_8_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the v3.8 migration is stable and does not overwrite (setdefault)."""
    entry = _make_entry(
        hass,
        {
            "fov_left": 45,
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
        },
        version=3,
        minor_version=7,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_7_to_v3_8_preserves_existing_gamma_keys(
    hass: HomeAssistant,
) -> None:
    """An entry that already stores signed-gamma keys keeps them (setdefault)."""
    entry = _make_entry(
        hass,
        {
            "fov_left": 45,
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
            "blind_spot_left_gamma": 20,  # pre-existing, must survive
            "blind_spot_right_gamma": -5,
        },
        version=3,
        minor_version=7,
    )
    await async_migrate_entry(hass, entry)
    assert entry.options["blind_spot_left_gamma"] == 20
    assert entry.options["blind_spot_right_gamma"] == -5


async def test_migrate_v3_7_to_v3_8_no_blind_spot_is_noop(hass: HomeAssistant) -> None:
    """An entry without any blind-spot edges gains only the v3.13 default position."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=7,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == {**before, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_7_to_v3_8_tolerates_none_fov_left(
    hass: HomeAssistant,
) -> None:
    """A present-but-None fov_left must NOT crash the startup migration (finding 4).

    ``int(options.get(CONF_FOV_LEFT, 90))`` raises TypeError when the key is
    present but None (a cleared field). Because the blind-spot seed now runs
    inside async_migrate_entry, that would brick entry loading. The None-tolerant
    resolver falls back to DEFAULT_FOV_LEFT (90): legacy 10/30 → gamma 80/-60.
    """
    entry = _make_entry(
        hass,
        {
            "fov_left": None,
            "fov_right": None,
            "blind_spot": True,
            "blind_spot_left": 10,
            "blind_spot_right": 30,
        },
        version=3,
        minor_version=7,
    )
    assert await async_migrate_entry(hass, entry) is True  # no TypeError
    assert entry.minor_version == 18
    assert entry.options["blind_spot_left_gamma"] == 80  # 90 - 10
    assert entry.options["blind_spot_right_gamma"] == -60  # 30 - 90


# ---------------------------------------------------------------------------
# Reachability lock: config-flow handler version constants must cover every
# migration block that exists in __init__.py.
# ---------------------------------------------------------------------------


def test_config_flow_major_version_stays_3_for_rollback_safety() -> None:
    """ConfigFlowHandler.VERSION must stay 3 so develop→main rollback never breaks.

    Rollback contract (see CLAUDE.md § "Rollback-Safe Config Migrations"):
    Home Assistant *refuses to load* a config entry whose stored MAJOR version
    exceeds the running integration's VERSION — a user who installs a develop
    build and rolls back to an older stable would get a hard "migration
    downgrade not supported" failure and a dead integration.

    A MINOR bump is forward-compatible (older code loads the entry and ignores
    keys it doesn't know), which is why every migration to date is a minor bump
    with an additive block. A MAJOR bump breaks that guarantee.

    This lock does NOT forbid ever bumping VERSION — it forces the bump to be a
    deliberate decision. If you truly need a non-additive/structural migration,
    bump VERSION here AND update this assertion AND document the rollback break
    in the release notes so users know a downgrade requires removing the entry.
    """
    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    assert ConfigFlowHandler.VERSION == 3, (
        "Config-entry MAJOR version changed. A major bump breaks rollback to "
        "older releases (HA won't load a newer-major entry). If this is "
        "intentional, update this assertion and flag the rollback break in the "
        "release notes."
    )


def test_config_flow_minor_version_reaches_highest_migration_target() -> None:
    """ConfigFlowHandler.MINOR_VERSION must equal the highest minor version any
    migration block in async_migrate_entry targets.

    HA only invokes async_migrate_entry when an entry's stored
    (version, minor_version) is strictly less than the handler's class
    (VERSION, MINOR_VERSION).  If MINOR_VERSION is too low, entries sitting at
    that minor are never seen as stale and the migration is dead code in
    production.

    Currently the highest target is 18 (the v3.17 → v3.18 no-op block that
    advances entries past the additive per-slot
    ``custom_position_outside_window_N`` flag, per issue #943 item B).
    Raise this assertion whenever a new minor migration block is added.
    """
    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    assert ConfigFlowHandler.MINOR_VERSION == 18


# ---------------------------------------------------------------------------
# Backward-compat guard: slots 6-10 are additive (issue #703).
# ---------------------------------------------------------------------------


async def test_slots_6_to_10_not_injected_into_existing_entry(
    hass: HomeAssistant,
) -> None:
    """An entry with no slot 6-10 keys must not have them injected by migration.

    Slots 6-10 are purely additive: existing entries omit them and
    custom_position_slot_configured() treats absent keys as unconfigured,
    so no handler is created.  Migration must NEVER backfill these keys.
    This test guards against a future migration accidentally doing so.
    """
    options = {
        "custom_position_sensors_5": ["binary_sensor.rain"],
        "custom_position_5": 90,
        "custom_position_priority_5": 100,
        "azimuth": 180,
    }
    entry = _make_entry(hass, options, version=3, minor_version=5)
    assert await async_migrate_entry(hass, entry) is True
    # No slot 6-10 keys should appear after migration.
    for n in range(6, 11):
        assert f"custom_position_sensors_{n}" not in entry.options
        assert f"custom_position_{n}" not in entry.options
        assert f"custom_position_priority_{n}" not in entry.options
    # Existing slot 5 keys remain intact.
    assert entry.options["custom_position_sensors_5"] == ["binary_sensor.rain"]
    assert entry.options["custom_position_5"] == 90


# ---------------------------------------------------------------------------
# v3.8 → v3.9 — additive axis-constraint options (issue #943)
# ---------------------------------------------------------------------------


async def test_migrate_v3_8_to_v3_9_is_additive_noop(hass: HomeAssistant) -> None:
    """A v3.8 entry advances to minor 9 with its options untouched.

    The axis-constraint keys need no seeding — an absent key already reads as
    "constraint off" — so the block exists only to advance the minor so the
    entry stops re-triggering migration on every restart.
    """
    options = {
        "custom_position_sensors_1": ["binary_sensor.door"],
        "custom_position_1": 40,
        "custom_position_min_mode_1": True,
    }
    entry = _make_entry(hass, dict(options), version=3, minor_version=8)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert dict(entry.options) == {**options, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_8_to_v3_9_seeds_no_constraint_keys(
    hass: HomeAssistant,
) -> None:
    """The migration must not invent constraints on an existing slot."""
    entry = _make_entry(
        hass,
        {"custom_position_sensors_1": ["binary_sensor.door"], "custom_position_1": 40},
        version=3,
        minor_version=8,
    )
    assert await async_migrate_entry(hass, entry) is True
    for sub in ("position_max", "tilt_min", "tilt_max"):
        assert CUSTOM_POSITION_SLOTS[1][sub] not in entry.options


async def test_migrate_v3_9_to_v3_10_additive_noop_without_legacy_margin(
    hass: HomeAssistant,
) -> None:
    """A v3.9 entry without the legacy margin key advances to 10, options intact."""
    options = {
        "custom_position_sensors_1": ["binary_sensor.door"],
        "custom_position_tilt_min_1": 50,
    }
    entry = _make_entry(hass, dict(options), version=3, minor_version=9)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert dict(entry.options) == {**options, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_9_to_v3_10_copies_legacy_safety_margin(
    hass: HomeAssistant,
) -> None:
    """The legacy venetian_tilt_safety_margin is copied into the neutral key.

    Rollback-safe: the legacy key is retained (an older build still reads it),
    and the new neutral key carries the same value forward (issue #964).
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_TILT_SAFETY_MARGIN,
        CONF_VENETIAN_TILT_SAFETY_MARGIN,
    )

    entry = _make_entry(
        hass,
        {CONF_VENETIAN_TILT_SAFETY_MARGIN: 0.5},
        version=3,
        minor_version=9,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    # New neutral key seeded from the legacy value.
    assert entry.options[CONF_TILT_SAFETY_MARGIN] == 0.5
    # Legacy key retained unchanged (additive / rollback-safe).
    assert entry.options[CONF_VENETIAN_TILT_SAFETY_MARGIN] == 0.5


async def test_migrate_v3_9_to_v3_10_does_not_overwrite_existing_new_key(
    hass: HomeAssistant,
) -> None:
    """When the neutral key already exists it wins (setdefault, not overwrite)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_TILT_SAFETY_MARGIN,
        CONF_VENETIAN_TILT_SAFETY_MARGIN,
    )

    entry = _make_entry(
        hass,
        {CONF_VENETIAN_TILT_SAFETY_MARGIN: 0.5, CONF_TILT_SAFETY_MARGIN: 0.8},
        version=3,
        minor_version=9,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_TILT_SAFETY_MARGIN] == 0.8
    assert entry.options[CONF_VENETIAN_TILT_SAFETY_MARGIN] == 0.5


async def test_migrate_v3_10_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-migrated (minor 10) entry is stable."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_TILT_SAFETY_MARGIN,
    )

    options = {
        "custom_position_sensors_1": ["binary_sensor.door"],
        CONF_TILT_SAFETY_MARGIN: 0.3,
    }
    entry = _make_entry(hass, dict(options), version=3, minor_version=10)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert dict(entry.options) == {**options, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_10_to_v3_11_seeds_shade_mode_for_awning(
    hass: HomeAssistant,
) -> None:
    """A fixed-awning entry gets the window-glass shade-mode default seeded (#1025).

    Detected by the awning-only geometry key ``length_awning``. Rollback-safe:
    an older build ignores the seeded key; a re-upgrade reads it verbatim.
    """
    from custom_components.adaptive_cover_pro.const import (
        AWNING_SHADE_MODE_WINDOW,
        CONF_AWNING_SHADE_MODE,
        CONF_LENGTH_AWNING,
    )

    entry = _make_entry(
        hass,
        {CONF_LENGTH_AWNING: 3.0},
        version=3,
        minor_version=10,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options[CONF_AWNING_SHADE_MODE] == AWNING_SHADE_MODE_WINDOW


async def test_migrate_v3_10_to_v3_11_skips_non_awning(
    hass: HomeAssistant,
) -> None:
    """A non-awning entry (no length_awning) gets no shade-mode key seeded (#1025)."""
    from custom_components.adaptive_cover_pro.const import CONF_AWNING_SHADE_MODE

    options = {"azimuth": 180, "custom_position_sensors_1": ["binary_sensor.door"]}
    entry = _make_entry(hass, dict(options), version=3, minor_version=10)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert CONF_AWNING_SHADE_MODE not in entry.options
    # Cascades on through v3.13, which seeds this blind's default position (#1126).
    assert dict(entry.options) == {**options, CONF_DEFAULT_HEIGHT: 100}


async def test_migrate_v3_11_does_not_overwrite_existing_shade_mode(
    hass: HomeAssistant,
) -> None:
    """An awning that already chose 'area' keeps it (setdefault, not overwrite)."""
    from custom_components.adaptive_cover_pro.const import (
        AWNING_SHADE_MODE_AREA,
        CONF_AWNING_SHADE_MODE,
        CONF_LENGTH_AWNING,
    )

    entry = _make_entry(
        hass,
        {CONF_LENGTH_AWNING: 3.0, CONF_AWNING_SHADE_MODE: AWNING_SHADE_MODE_AREA},
        version=3,
        minor_version=10,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_AWNING_SHADE_MODE] == AWNING_SHADE_MODE_AREA


async def test_migrate_v3_11_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-seeded awning entry is stable."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_AWNING_SHADE_MODE,
        CONF_LENGTH_AWNING,
    )

    entry = _make_entry(
        hass,
        {CONF_LENGTH_AWNING: 3.0},
        version=3,
        minor_version=10,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.options[CONF_AWNING_SHADE_MODE] == "window"
    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.options) == first


# ---------------------------------------------------------------------------
# v3.11 → v3.12: repair malformed time strings (issue #1049)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("time_key", ["start_time", "end_time"])
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("00:00", "00:00:00"),
        ("0:00:00", "00:00:00"),
        ("00:00:00\n", "00:00:00"),
        ("٠٠:٠٠:٠٠", "00:00:00"),
        ("7:30", "07:30:00"),
    ],
)
async def test_migrate_v3_11_to_v3_12_repairs_malformed_time(
    hass: HomeAssistant, time_key, stored, expected
) -> None:
    """An entry already carrying a malformed time is repaired in place (#1049).

    Validating the write paths stops NEW bad values but does nothing for a user
    already bitten — their override deadline keeps receding until the stored
    value is canonical. This is also what lets such an entry survive an
    export→import round-trip, which the new import validation would otherwise
    reject wholesale.
    """
    entry = _make_entry(hass, {time_key: stored}, version=3, minor_version=11)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options[time_key] == expected


async def test_migrate_v3_11_to_v3_12_leaves_canonical_times_alone(
    hass: HomeAssistant,
) -> None:
    """A well-formed window is untouched — no churn for the overwhelming majority."""
    options = {"start_time": "07:00:00", "end_time": "22:30:00", "azimuth": 180}
    entry = _make_entry(hass, dict(options), version=3, minor_version=11)
    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.options) == {**options, CONF_DEFAULT_HEIGHT: 100}


@pytest.mark.parametrize("stored", ["garbage", "25:00:00", "24:99:99", ""])
async def test_migrate_v3_11_to_v3_12_drops_unrescuable_times(
    hass: HomeAssistant, stored
) -> None:
    """A value no parser can rescue is dropped to the unset sentinel semantics.

    Leaving it is not neutral: ``get_datetime_from_str`` calls
    ``dateutil.parser.parse`` without a guard, so ``"25:00:00"`` raises on every
    coordinator cycle. Both are reachable in stored options — the pre-#1049
    ``set_options`` regex accepted "25:00:00", and import validated no time at
    all. Dropping the key is the #492 "no time set" state, which is what an
    unusable window bound already means in practice.
    """
    entry = _make_entry(hass, {"end_time": stored}, version=3, minor_version=11)
    assert await async_migrate_entry(hass, entry) is True
    assert "end_time" not in entry.options


async def test_migrate_v3_11_to_v3_12_dropped_time_leaves_window_usable(
    hass: HomeAssistant,
) -> None:
    """After the drop, TimeWindowManager resolves the window instead of raising.

    This is the delete branch's whole justification, so assert it against the
    real manager rather than inferring it from the key's absence:
    ``get_datetime_from_str`` calls ``dateutil.parser.parse`` unguarded, so the
    stored ``"25:00:00"`` raises ``ParserError`` on every coordinator cycle
    until the migration removes it.
    """
    import logging
    from unittest.mock import MagicMock

    from dateutil.parser import ParserError

    from custom_components.adaptive_cover_pro.managers.time_window import (
        TimeWindowManager,
    )

    def _manager(options: dict) -> TimeWindowManager:
        manager = TimeWindowManager(hass, logging.getLogger(__name__))
        manager.logger = MagicMock()
        manager.update_config(
            start_time=options.get("start_time"),
            start_time_entity=None,
            end_time=options.get("end_time"),
            end_time_entity=None,
        )
        return manager

    entry = _make_entry(hass, {"end_time": "25:00:00"}, version=3, minor_version=11)

    # Pre-migration state is genuinely fatal, not merely untidy.
    with pytest.raises(ParserError):
        _ = _manager(dict(entry.options)).end_time

    assert await async_migrate_entry(hass, entry) is True
    assert _manager(dict(entry.options)).end_time is None
    assert _manager(dict(entry.options)).before_end_time is True


async def test_migrate_v3_12_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on a repaired entry is stable."""
    entry = _make_entry(hass, {"end_time": "00:00"}, version=3, minor_version=11)
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.options["end_time"] == "00:00:00"
    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.options) == first


# ---------------------------------------------------------------------------
# v3.12 → v3.13: seed default_percentage for the minimal create wizard (#1126)
# ---------------------------------------------------------------------------


async def test_migrate_v3_12_to_v3_13_seeds_default_position_for_blind(
    hass: HomeAssistant,
) -> None:
    """A key-less blind entry is backfilled to its no-coverage endpoint (100).

    The minimal create wizard (#945 Part 2) has no position step, so every
    entry created since then never got default_percentage written — every
    runtime read then fell back to a hard-coded 0, driving the cover fully
    closed until Options -> Position was opened and saved (#1126).
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BLIND,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options[CONF_DEFAULT_HEIGHT] == 100


async def test_migrate_v3_12_to_v3_13_seeds_default_position_for_awning(
    hass: HomeAssistant,
) -> None:
    """A key-less awning entry is backfilled to 0, its polarity-flipped endpoint.

    Awning's position axis has ``open_blocks_sun=True`` (100 % = fully
    extended = maximum shading), so its no-coverage endpoint is 0, not 100 —
    the opposite of every other registered cover type.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {"length_awning": 3.0},
        version=3,
        minor_version=12,
        sensor_type=CoverType.AWNING,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options[CONF_DEFAULT_HEIGHT] == 0


@pytest.mark.parametrize("stored_default", [0, 42, 100])
async def test_migrate_v3_13_does_not_overwrite_existing_default_position(
    hass: HomeAssistant, stored_default: int
) -> None:
    """An entry that already has a configured default position keeps it (setdefault).

    Parametrized over 0, 42, and 100 rather than a single value: 0 is the
    value most at risk of a regression here. ``_seed_default_position`` gates
    on ``CONF_DEFAULT_HEIGHT in options`` — a true membership check — but if
    that ever degraded into a truthiness check
    (``if not options.get(CONF_DEFAULT_HEIGHT)``), a stored 0 would read as
    falsy and get silently reseeded to the blind's no-coverage endpoint
    (100), overwriting a user's deliberately configured fully-closed default.
    42 and 100 keep the ordinary and boundary-open cases covered alongside it.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {CONF_DEFAULT_HEIGHT: stored_default},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BLIND,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_DEFAULT_HEIGHT] == stored_default


async def test_migrate_v3_13_skips_virtual_entry_types(hass: HomeAssistant) -> None:
    """Building Profile and Group entries control no cover and are left untouched.

    Their policies declare no axes, so calling ``position_for_intent`` on them
    raises ``IndexError`` — the same ``controls_cover and not is_orchestrator``
    gate the create finalizer uses (#133) must guard the migration too.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    profile_entry = _make_entry(
        hass,
        {},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BUILDING_PROFILE,
    )
    assert await async_migrate_entry(hass, profile_entry) is True
    assert CONF_DEFAULT_HEIGHT not in profile_entry.options

    group_entry = _make_entry(
        hass, {}, version=3, minor_version=12, sensor_type=CoverType.GROUP
    )
    assert await async_migrate_entry(hass, group_entry) is True
    assert CONF_DEFAULT_HEIGHT not in group_entry.options


async def test_migrate_v3_13_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-seeded entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BLIND,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.options) == first


@pytest.mark.parametrize("bad_sensor_type", [None, "", "not_a_registered_type"])
async def test_migrate_v3_13_unknown_sensor_type_does_not_abort_cascade(
    hass: HomeAssistant, bad_sensor_type
) -> None:
    """A missing/unknown sensor_type must not abort the whole migration cascade.

    ``get_policy`` raises ``ValueError`` for ``None``, ``""``, and any
    unregistered string. Before this fix, ``_seed_default_position`` called
    ``get_policy`` before checking whether the type even resolves, so the
    exception propagated straight out of ``async_migrate_entry`` — parking
    the entry in ``ConfigEntryState.MIGRATION_ERROR`` and silently discarding
    every OTHER repair in the same cascade (cm→m conversion, force-override
    slot-5 copy, blind-spot gamma, the v3.12 malformed-time repair). Pairing
    a malformed end_time (repaired by the v3.11 → v3.12 sibling block) with
    the bad sensor_type proves the *sibling* repair still lands — not merely
    that no exception was raised, which is the actual damage a bare
    "did it raise" assertion would miss.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {"end_time": "00:00"},
        version=3,
        minor_version=11,
        sensor_type=bad_sensor_type,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    # The v3.11 → v3.12 sibling repair still landed — proof the cascade
    # continued past the bad sensor_type instead of aborting.
    assert entry.options["end_time"] == "00:00:00"
    # default_percentage can't be resolved for an unknown type, so it is
    # correctly left unseeded rather than raising.
    assert CONF_DEFAULT_HEIGHT not in entry.options


async def test_migrate_v3_13_logs_seeded_default_position(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The riskiest repair in the migration cascade must not be silent (#1126).

    ``_seed_default_position`` moves an already-bitten cover from an
    effective 0 % (fully closed) to its per-type default — the largest blast
    radius of any repair in ``async_migrate_entry`` — yet, unlike every
    sibling repair (``_merge_force_override_into_slot_5``,
    ``copy_legacy_slot_sensors_to_list``, ``_seed_signed_gamma_blind_spots``,
    ``_repair_malformed_times``), the caller ignored its return value and
    logged nothing. When a user reports "all my blinds opened after the
    update" there must be a log line to point at.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BLIND,
    )
    with caplog.at_level(logging.INFO):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.options[CONF_DEFAULT_HEIGHT] == 100
    matching = [
        r.message
        for r in caplog.records
        if "Migration Test" in r.message
        and "100" in r.message
        and "was silently kept fully closed until this migration ran" in r.message
    ]
    assert matching, (
        "Expected a log line naming the entry, the seeded value, and the"
        " fact that this repair genuinely moved the cover, got: "
        f"{[r.message for r in caplog.records]}"
    )


async def test_migrate_v3_13_logs_no_movement_for_open_blocks_sun_seed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An awning's 0% seed is logged as a no-op, not a "was closed" repair (#1126).

    Awning and oscillating-awning have ``open_blocks_sun=True``, so their
    no-coverage endpoint is 0 — identical to the pre-fix runtime fallback of
    a hard-coded 0. The migration still writes the key (so future reads no
    longer depend on the fallback), but the cover itself never moves, so the
    log line must say so rather than reusing the "was silently kept fully
    closed" wording that is only true for the 100%-seeded types. A maintainer
    reading this log for an awning-only bug report must not be misdirected
    toward a repair that moved nothing.
    """
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {"length_awning": 3.0},
        version=3,
        minor_version=12,
        sensor_type=CoverType.AWNING,
    )
    with caplog.at_level(logging.INFO):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.options[CONF_DEFAULT_HEIGHT] == 0
    matching = [
        r.message
        for r in caplog.records
        if "Migration Test" in r.message
        and "matches the pre-fix runtime fallback, so this cover did not move"
        in r.message
    ]
    assert matching, (
        "Expected a log line naming the entry and stating the cover did not"
        f" move, got: {[r.message for r in caplog.records]}"
    )
    assert not any(
        "was silently kept fully closed until this migration ran" in r.message
        for r in caplog.records
    ), "The awning seed did not move the cover — the log must not claim it did"


async def test_migrate_v3_13_does_not_log_when_key_already_present(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """No log line when default_percentage was already configured (no-op setdefault)."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    entry = _make_entry(
        hass,
        {CONF_DEFAULT_HEIGHT: 42},
        version=3,
        minor_version=12,
        sensor_type=CoverType.BLIND,
    )
    with caplog.at_level(logging.INFO):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.options[CONF_DEFAULT_HEIGHT] == 42
    assert not any("Seeded default position" in r.message for r in caplog.records)


async def test_migrate_v3_9_preserves_user_set_constraints(
    hass: HomeAssistant,
) -> None:
    """A constraint already configured survives the migration verbatim."""
    entry = _make_entry(
        hass,
        {
            "custom_position_sensors_1": ["binary_sensor.door"],
            "custom_position_tilt_min_1": 50,
            "custom_position_position_max_1": 60,
        },
        version=3,
        minor_version=8,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["custom_position_tilt_min_1"] == 50
    assert entry.options["custom_position_position_max_1"] == 60


# ---------------------------------------------------------------------------
# Migration: v3.13 → v3.14 — no-op minor bump for the additive
# day_night_concurrent_rail_travel option (issue #1140). An absent key already
# reads as "on" (the default), so nothing needs seeding; the block only advances
# a stale minor-13 entry to 14 so it stops re-triggering migration every restart
# (the v3.6 → v3.7 precedent).
# ---------------------------------------------------------------------------


async def test_migrate_v3_13_advances_to_14_without_seeding(
    hass: HomeAssistant,
) -> None:
    """A minor-13 entry reaches minor 14 with its options untouched."""
    entry = _make_entry(
        hass,
        {"azimuth": 180, CONF_DEFAULT_HEIGHT: 60},
        version=3,
        minor_version=13,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert "day_night_concurrent_rail_travel" not in entry.options
    assert entry.options == before


async def test_migrate_v3_13_to_v3_14_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-advanced entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=13,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_13_to_v3_14_preserves_an_explicit_choice(
    hass: HomeAssistant,
) -> None:
    """An entry that already turned concurrent rail travel off keeps it off."""
    entry = _make_entry(
        hass,
        {"day_night_concurrent_rail_travel": False},
        version=3,
        minor_version=13,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["day_night_concurrent_rail_travel"] is False
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# Migration: v3.14 → v3.15 — no-op minor bump for the additive
# day_night_external_command_interlock option (issue #1138). An absent key
# already reads as "on" (the default), so nothing needs seeding; the block only
# advances a stale minor-14 entry to 15 so it stops re-triggering migration
# every restart (the v3.6 → v3.7 precedent).
# ---------------------------------------------------------------------------


async def test_migrate_v3_14_advances_to_15_without_seeding(
    hass: HomeAssistant,
) -> None:
    """A minor-14 entry reaches minor 15 with its options untouched."""
    entry = _make_entry(
        hass,
        {"azimuth": 180, CONF_DEFAULT_HEIGHT: 60},
        version=3,
        minor_version=14,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert "day_night_external_command_interlock" not in entry.options
    assert entry.options == before


async def test_migrate_v3_14_to_v3_15_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-advanced entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=14,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_14_to_v3_15_preserves_an_explicit_choice(
    hass: HomeAssistant,
) -> None:
    """An entry that already turned the external interlock off keeps it off."""
    entry = _make_entry(
        hass,
        {"day_night_external_command_interlock": False},
        version=3,
        minor_version=14,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["day_night_external_command_interlock"] is False
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# v3.15 → v3.16 — additive sun-tracking gate options (issue #1167).
# ---------------------------------------------------------------------------


async def test_migrate_v3_15_advances_to_16_without_seeding(
    hass: HomeAssistant,
) -> None:
    """A minor-15 entry reaches minor 16 with its options untouched.

    The three sun-tracking-gate keys are deliberately NOT seeded: an absent key
    already reads as "no gate configured", which resolves to the master
    enable_sun_tracking toggle alone. Seeding anything here would invent a gate
    for every existing install.
    """
    entry = _make_entry(
        hass,
        {"azimuth": 180, CONF_DEFAULT_HEIGHT: 60},
        version=3,
        minor_version=15,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert "sun_tracking_gate_sensors" not in entry.options
    assert "sun_tracking_gate_template" not in entry.options
    assert "sun_tracking_gate_template_mode" not in entry.options
    assert entry.options == before


async def test_migrate_v3_15_to_v3_16_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-advanced entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=15,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_15_to_v3_16_preserves_a_configured_gate(
    hass: HomeAssistant,
) -> None:
    """An entry that already configured a gate keeps it verbatim."""
    entry = _make_entry(
        hass,
        {
            "sun_tracking_gate_sensors": ["binary_sensor.ac_running"],
            "sun_tracking_gate_template": "{{ is_state('climate.up', 'cool') }}",
            "sun_tracking_gate_template_mode": "and",
        },
        version=3,
        minor_version=15,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["sun_tracking_gate_sensors"] == ["binary_sensor.ac_running"]
    assert (
        entry.options["sun_tracking_gate_template"]
        == "{{ is_state('climate.up', 'cool') }}"
    )
    assert entry.options["sun_tracking_gate_template_mode"] == "and"
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# v3.16 → v3.17 — additive named command-queue options (issue #1189).
# ---------------------------------------------------------------------------


async def test_migrate_v3_16_advances_to_17_without_seeding(
    hass: HomeAssistant,
) -> None:
    """A minor-16 entry reaches minor 17 with its options untouched.

    ``command_queue`` is deliberately NOT seeded: an absent key already reads as
    "no queue", which is exactly the dispatch behaviour every existing install
    has today. Seeding anything here would enrol every cover in a queue nobody
    asked for and start spacing commands that were never colliding.
    """
    entry = _make_entry(
        hass,
        {"azimuth": 180, CONF_DEFAULT_HEIGHT: 60},
        version=3,
        minor_version=16,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert "command_queue" not in entry.options
    assert "command_queue_gap" not in entry.options
    assert entry.options == before


async def test_migrate_v3_16_to_v3_17_is_idempotent(hass: HomeAssistant) -> None:
    """Re-running the migration on an already-advanced entry is stable."""
    entry = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=16,
    )
    assert await async_migrate_entry(hass, entry) is True
    first = dict(entry.options)
    assert entry.minor_version == 18
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 18
    assert entry.options == first


async def test_migrate_v3_16_to_v3_17_preserves_an_assigned_queue(
    hass: HomeAssistant,
) -> None:
    """An entry that already names a queue keeps it verbatim, casing included."""
    entry = _make_entry(
        hass,
        {"command_queue": "Facade South"},
        version=3,
        minor_version=16,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["command_queue"] == "Facade South"
    assert entry.minor_version == 18


# ---------------------------------------------------------------------------
# v3.17 → v3.18 — additive per-slot outside-window constraint flag (#943 item B).
# ---------------------------------------------------------------------------


async def test_migration_3_18_is_noop_and_idempotent(hass: HomeAssistant) -> None:
    """A minor-17 entry reaches 3.18 with options byte-identical, twice over.

    ``custom_position_outside_window_N`` is deliberately NOT seeded: an absent
    key already reads as "constraints stop at the clock window", which is
    exactly what every existing install does today. Seeding it would silently
    grant night-time authority to slots nobody opted in.
    """
    entry = _make_entry(
        hass,
        {
            "azimuth": 180,
            CONF_DEFAULT_HEIGHT: 60,
            "custom_position_sensors_1": ["binary_sensor.window"],
            "custom_position_tilt_min_1": 50,
        },
        version=3,
        minor_version=17,
    )
    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert entry.options == before
    for n in range(1, 11):
        assert f"custom_position_outside_window_{n}" not in entry.options

    # Idempotent: re-running leaves both the minor and the options alone.
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 3
    assert entry.minor_version == 18
    assert entry.options == before


async def test_migrate_v3_17_to_v3_18_preserves_an_opted_in_slot(
    hass: HomeAssistant,
) -> None:
    """An entry that already set the flag keeps it verbatim."""
    entry = _make_entry(
        hass,
        {"custom_position_outside_window_2": True},
        version=3,
        minor_version=17,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options["custom_position_outside_window_2"] is True
    assert entry.minor_version == 18
