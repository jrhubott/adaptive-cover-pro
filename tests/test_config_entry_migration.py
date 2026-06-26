"""Tests for the cm → metres config-entry migration (VERSION 1 → 2).

Exercises async_migrate_entry directly to verify:
- v1 entries (window_width and glare-zone coords in cm) are divided by 100
- Entries already in metres (sentinel ≤ 5) are not re-divided (idempotent)
- Version is bumped to 2 in every case
- Entries with no affected fields are left unchanged aside from the version bump
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import async_migrate_entry
from custom_components.adaptive_cover_pro.const import (
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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5
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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5
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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5


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
    assert entry.minor_version == 5


async def test_migrate_v1_cascades_to_position_matching(hass: HomeAssistant) -> None:
    """A genuine v1 entry ends at 3.4 with position matching enabled."""
    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 200}, version=1)
    await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ENABLE_POSITION_MATCHING] is True
    assert entry.version == 3
    assert entry.minor_version == 5


# ---------------------------------------------------------------------------
# Migration: v3.4 → v3.5 — seed CONF_SHOW_WEATHER_RETRACTION from the cover-type
# policy default (True for awnings, False elsewhere). Additive + rollback-safe:
# an entry that already carries the key keeps its value.
# ---------------------------------------------------------------------------

from custom_components.adaptive_cover_pro.const import (  # noqa: E402
    CONF_SHOW_WEATHER_RETRACTION,
)


async def test_migrate_v3_4_seeds_weather_retraction(hass: HomeAssistant) -> None:
    """An awning seeds the toggle True; a blind seeds it False."""
    awning = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=4,
        sensor_type=CoverType.AWNING,
    )
    assert await async_migrate_entry(hass, awning) is True
    assert awning.options[CONF_SHOW_WEATHER_RETRACTION] is True
    assert awning.minor_version == 5

    blind = _make_entry(
        hass,
        {"azimuth": 180},
        version=3,
        minor_version=4,
        sensor_type=CoverType.BLIND,
    )
    assert await async_migrate_entry(hass, blind) is True
    assert blind.options[CONF_SHOW_WEATHER_RETRACTION] is False
    assert blind.minor_version == 5


async def test_migrate_v3_5_preserves_existing_toggle(hass: HomeAssistant) -> None:
    """An entry that already set the key keeps its value (rollback-safe)."""
    entry = _make_entry(
        hass,
        {CONF_SHOW_WEATHER_RETRACTION: True},
        version=3,
        minor_version=4,
        sensor_type=CoverType.BLIND,
    )
    await async_migrate_entry(hass, entry)
    # Policy default for a blind is False, but the explicit True is preserved.
    assert entry.options[CONF_SHOW_WEATHER_RETRACTION] is True
    assert entry.minor_version == 5


# ---------------------------------------------------------------------------
# Reachability lock: config-flow handler version constants must cover every
# migration block that exists in __init__.py.
# ---------------------------------------------------------------------------


def test_config_flow_minor_version_reaches_highest_migration_target() -> None:
    """ConfigFlowHandler.MINOR_VERSION must equal the highest minor version any
    migration block in async_migrate_entry targets.

    HA only invokes async_migrate_entry when an entry's stored
    (version, minor_version) is strictly less than the handler's class
    (VERSION, MINOR_VERSION).  If MINOR_VERSION is too low, entries sitting at
    that minor are never seen as stale and the migration is dead code in
    production.

    Currently the highest target is 5 (the v3.4 → v3.5 seed-weather-retraction
    setdefault, per issue #693).  Raise this assertion whenever a new minor
    migration block is added.
    """
    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    assert ConfigFlowHandler.MINOR_VERSION == 5
