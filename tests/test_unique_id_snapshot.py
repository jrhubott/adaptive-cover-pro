"""Snapshot test that locks every entity unique_id produced by Adaptive Cover Pro.

This is the contract that protects against unique_id drift during the
sensor/switch/binary_sensor refactor (see plan: dict-driven spec/factory).

The test boots a config entry that exercises every conditional gate
(climate_mode, motion sensors, force-override, glare zones, etc.) and asserts
that the sorted set of `unique_id`s in the entity registry equals an exact
literal. Any rename, addition, or removal of an entity's unique_id will fail
this test — which is exactly what we want, because user installations carry
state keyed by these strings.

If you intentionally add or rename an entity, update both the literal here
and `migrations.py` so existing installs keep their history.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_ENABLE_GLARE_ZONES,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_MOTION_SENSORS,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


# Entry id used in the literal expected list below. Changing this string
# requires regenerating EXPECTED_UNIQUE_IDS — the prefix appears in every entry.
ENTRY_ID = "snap_entry"


# Maximally-configured options: every conditional entity gate is on.
# - cover_blind: glare-zone switches + glare_active binary sensor
# - climate_mode + temp/lux/irradiance entities: 4 climate-related switches +
#   climate_status sensor
# - motion_sensors: motion_control switch
# - force_override_sensors: force_override_triggers sensor
# - 2 named glare zones: 2 glare-zone switches with user-text in the unique_id
MAX_OPTIONS = {
    **VERTICAL_OPTIONS,
    CONF_CLIMATE_MODE: True,
    CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp",
    CONF_LUX_ENTITY: "sensor.lux",
    CONF_IRRADIANCE_ENTITY: "sensor.irradiance",
    CONF_MOTION_SENSORS: ["binary_sensor.motion_a"],
    CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.force_a"],
    CONF_ENABLE_GLARE_ZONES: True,
    "glare_zone_1_name": "Living Room",
    "glare_zone_2_name": "Kitchen",
}


# Exact list of unique_ids that the maximally-configured entry must produce,
# stored without the entry-id prefix for readability. Each value below becomes
# f"{ENTRY_ID}_{value}" at assertion time.
#
# Origin of each suffix:
#   sensor.py     → entity_base.AdaptiveCoverSensorBase: f"{entry_id}_{suffix}"
#                   (TimeSensor passes sensor_name "Start Sun"/"End Sun" — not key)
#   switch.py     → AdaptiveCoverSwitch: f"{entry_id}_{switch_name}" (display name!)
#   binary_sensor → AdaptiveCoverBinarySensor: f"{entry_id}_{key}"
#                   AdaptiveCoverPositionMismatchSensor: f"{entry_id}_position_mismatch"
#   button.py     → AdaptiveCoverButton: f"{entry_id}_Reset Manual Override"
#
# DO NOT modify any of these strings without a coordinated migration in
# `migrations.py`. They are the registry keys for every existing user install.
EXPECTED_UNIQUE_ID_SUFFIXES = sorted(
    [
        # --- sensor platform ---
        "Cover_Position",
        "Start Sun",
        "End Sun",
        "sun_position",
        "control_status",
        "decision_trace",
        "last_skipped_action",
        "last_cover_action",
        "manual_override_end_time",
        "position_verification",
        "motion_status",
        "force_override_triggers",
        "climate_status",
        # --- switch platform (uses display switch_name, not translation key) ---
        "Integration Enabled",
        "Automatic Control",
        "Manual Override",  # translation key is "manual_toggle"; unique_id keeps display name
        "Climate Mode",
        "Outside Temperature",
        "Lux",
        "Irradiance",
        "Return to default when disabled",
        "Motion Control",
        "Glare Zone: Living Room",
        "Glare Zone: Kitchen",
        # --- binary_sensor platform ---
        "sun_motion",
        "manual_override",
        "glare_active",
        "position_mismatch",
        # --- button platform ---
        "Reset Manual Override",
    ]
)


@pytest.mark.integration
async def test_unique_id_snapshot_max_config(hass: HomeAssistant) -> None:
    """All entities for a maximally-configured entry have the expected unique_ids.

    Sentinel test for refactors: if any entity's unique_id changes by even one
    byte, this fails. Existing user installations carry registry state keyed by
    these strings — drifting them silently orphans entities and resets settings
    (e.g. the v2.14.3 incident that gave us migrations.py).
    """
    # Pre-populate referenced entities so async_setup_entry doesn't choke.
    hass.states.async_set("binary_sensor.motion_a", "off")
    hass.states.async_set("binary_sensor.force_a", "off")
    hass.states.async_set("sensor.outside_temp", "20")
    hass.states.async_set("sensor.lux", "500")
    hass.states.async_set("sensor.irradiance", "200")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Snap", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=MAX_OPTIONS,
        entry_id=ENTRY_ID,
        title="Snap",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    actual_uids = sorted(
        e.unique_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    )
    expected_uids = sorted(
        f"{ENTRY_ID}_{suffix}" for suffix in EXPECTED_UNIQUE_ID_SUFFIXES
    )

    # Build a precise diff for the failure message — the literal list is the
    # contract; surface exactly what drifted so the fix is obvious.
    extra = sorted(set(actual_uids) - set(expected_uids))
    missing = sorted(set(expected_uids) - set(actual_uids))
    assert actual_uids == expected_uids, (
        f"\nUnexpected unique_ids (added since snapshot): {extra}"
        f"\nMissing unique_ids (removed since snapshot): {missing}"
    )
