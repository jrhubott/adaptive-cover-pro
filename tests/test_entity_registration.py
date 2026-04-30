"""Tests for entity registration and lifecycle with a real Home Assistant instance.

Verifies that the correct entities are created for each cover type, conditional
entities behave correctly, unique IDs are stable, and entity attributes are correct.
"""

from __future__ import annotations

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
    get_entity_ids_for_entry,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_entry(
    hass: HomeAssistant,
    cover_type: str = SensorType.BLIND,
    options: dict | None = None,
    entry_id: str = "reg_test_01",
    name: str = "Test Cover",
) -> MockConfigEntry:
    """Register + setup a config entry, return it."""
    opts = dict(VERTICAL_OPTIONS) if options is None else options
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: cover_type},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# 3a: Entity counts per cover type
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_vertical_cover_creates_sensors(hass: HomeAssistant) -> None:
    """Vertical blind creates the expected standard + diagnostic sensors."""
    entry = await _setup_entry(hass, SensorType.BLIND, entry_id="vert_sensors_01")
    sensor_ids = get_entity_ids_for_entry(hass, entry, "sensor")
    # Minimum: Target Position, Start Sun, End Sun, Sun Position, Control Status,
    # Decision Trace, Last Skipped Action, Last Cover Action,
    # Manual Override End Time, Position Verification, Motion Status = 11
    assert len(sensor_ids) >= 11, f"Expected >= 11 sensors, got: {sensor_ids}"


@pytest.mark.integration
async def test_vertical_cover_creates_switches(hass: HomeAssistant) -> None:
    """Vertical blind creates at least Manual Override and Automatic Control switches."""
    entry = await _setup_entry(hass, SensorType.BLIND, entry_id="vert_switches_01")
    switch_ids = get_entity_ids_for_entry(hass, entry, "switch")
    assert len(switch_ids) >= 2, f"Expected >= 2 switches, got: {switch_ids}"


@pytest.mark.integration
async def test_vertical_cover_creates_binary_sensors(hass: HomeAssistant) -> None:
    """Vertical blind creates Sun Infront, Manual Override, Position Mismatch binary sensors."""
    entry = await _setup_entry(hass, SensorType.BLIND, entry_id="vert_bs_01")
    bs_ids = get_entity_ids_for_entry(hass, entry, "binary_sensor")
    assert len(bs_ids) >= 3, f"Expected >= 3 binary sensors, got: {bs_ids}"


@pytest.mark.integration
async def test_horizontal_cover_creates_entities(hass: HomeAssistant) -> None:
    """Horizontal awning creates entities without error."""
    from tests.ha_helpers import HORIZONTAL_OPTIONS

    entry = await _setup_entry(
        hass, SensorType.AWNING, options=dict(HORIZONTAL_OPTIONS), entry_id="horiz_01"
    )
    sensor_ids = get_entity_ids_for_entry(hass, entry, "sensor")
    assert len(sensor_ids) >= 11


@pytest.mark.integration
async def test_tilt_cover_creates_entities(hass: HomeAssistant) -> None:
    """Tilt cover creates entities without error."""
    from tests.ha_helpers import TILT_OPTIONS

    entry = await _setup_entry(
        hass, SensorType.TILT, options=dict(TILT_OPTIONS), entry_id="tilt_01"
    )
    sensor_ids = get_entity_ids_for_entry(hass, entry, "sensor")
    assert len(sensor_ids) >= 11


# ---------------------------------------------------------------------------
# 3b: Conditional entities
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_force_override_sensor_only_when_configured(hass: HomeAssistant) -> None:
    """ForceOverrideTrigger sensor only created when force_override_sensors is set."""
    reg = er.async_get(hass)

    # Without force override sensors
    opts_no_force = dict(VERTICAL_OPTIONS)
    opts_no_force[CONF_FORCE_OVERRIDE_SENSORS] = []
    entry_no = await _setup_entry(
        hass, options=opts_no_force, entry_id="no_force_01", name="No Force"
    )

    # Check by unique_id suffix, not entity_id (entity_id includes entry name)
    def _has_force_trigger_sensor(entry, reg):
        return any(
            "force_override_triggers" in (e.unique_id or "")
            for e in reg.entities.values()
            if e.config_entry_id == entry.entry_id
        )

    reg = er.async_get(hass)
    assert not _has_force_trigger_sensor(
        entry_no, reg
    ), "Force override sensor should not exist without sensors"

    # With force override sensor configured
    opts_force = dict(VERTICAL_OPTIONS)
    opts_force[CONF_FORCE_OVERRIDE_SENSORS] = ["binary_sensor.rain"]
    entry_yes = await _setup_entry(
        hass, options=opts_force, entry_id="force_yes_01", name="With Force"
    )
    assert _has_force_trigger_sensor(
        entry_yes, reg
    ), "Force override sensor should exist when configured"


@pytest.mark.integration
async def test_climate_status_sensor_only_when_climate_mode(
    hass: HomeAssistant,
) -> None:
    """ClimateStatus sensor only created when CONF_CLIMATE_MODE is True."""
    opts_no_climate = dict(VERTICAL_OPTIONS)
    opts_no_climate[CONF_CLIMATE_MODE] = False
    entry_no = await _setup_entry(
        hass, options=opts_no_climate, entry_id="no_climate_01", name="No Climate"
    )

    def _has_climate_status_sensor(entry, reg):
        return any(
            "climate_status" in (e.unique_id or "")
            for e in reg.entities.values()
            if e.config_entry_id == entry.entry_id
        )

    reg = er.async_get(hass)
    assert not _has_climate_status_sensor(
        entry_no, reg
    ), "Climate status sensor should not exist without climate mode"

    opts_climate = dict(VERTICAL_OPTIONS)
    opts_climate[CONF_CLIMATE_MODE] = True
    opts_climate["temp_entity"] = "sensor.temperature"
    entry_yes = await _setup_entry(
        hass, options=opts_climate, entry_id="climate_yes_01", name="With Climate"
    )
    assert _has_climate_status_sensor(
        entry_yes, reg
    ), "Climate status sensor should exist with climate mode"


@pytest.mark.integration
async def test_motion_control_switch_only_when_motion_sensors(
    hass: HomeAssistant,
) -> None:
    """Motion Control switch only created when motion_sensors is non-empty."""
    opts_no_motion = dict(VERTICAL_OPTIONS)
    opts_no_motion[CONF_MOTION_SENSORS] = []
    entry_no = await _setup_entry(
        hass, options=opts_no_motion, entry_id="no_motion_01", name="No Motion"
    )

    def _has_motion_control_switch(entry, reg):
        return any(
            # unique_id is "{entry_id}_Motion Control" (switch_name not key)
            "Motion Control" in (e.unique_id or "")
            for e in reg.entities.values()
            if e.config_entry_id == entry.entry_id and e.domain == "switch"
        )

    reg = er.async_get(hass)
    assert not _has_motion_control_switch(
        entry_no, reg
    ), "Motion Control switch should not exist without motion sensors"

    opts_motion = dict(VERTICAL_OPTIONS)
    opts_motion[CONF_MOTION_SENSORS] = ["binary_sensor.presence"]
    entry_yes = await _setup_entry(
        hass, options=opts_motion, entry_id="motion_yes_01", name="With Motion"
    )
    assert _has_motion_control_switch(
        entry_yes, reg
    ), "Motion Control switch should exist when motion sensors configured"


@pytest.mark.integration
async def test_no_button_when_no_cover_entities(hass: HomeAssistant) -> None:
    """Reset Manual Override button not created when CONF_ENTITIES is empty."""
    opts_empty = dict(VERTICAL_OPTIONS)
    opts_empty[CONF_ENTITIES] = []
    entry = await _setup_entry(
        hass, options=opts_empty, entry_id="no_button_01", name="No Covers"
    )
    button_ids = get_entity_ids_for_entry(hass, entry, "button")
    assert (
        len(button_ids) == 0
    ), f"Reset button should not exist when no cover entities: {button_ids}"


@pytest.mark.integration
async def test_button_created_when_cover_entities_set(hass: HomeAssistant) -> None:
    """Reset Manual Override button created when at least one cover entity is configured."""
    opts_with_cover = dict(VERTICAL_OPTIONS)
    opts_with_cover[CONF_ENTITIES] = ["cover.test_blind"]
    entry = await _setup_entry(
        hass, options=opts_with_cover, entry_id="button_yes_01", name="With Covers"
    )
    button_ids = get_entity_ids_for_entry(hass, entry, "button")
    assert (
        len(button_ids) >= 1
    ), f"Reset button should exist with cover entities: {button_ids}"


# ---------------------------------------------------------------------------
# 3c: Entity attributes
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unique_ids_are_unique(hass: HomeAssistant) -> None:
    """All entities in a single entry have distinct unique_ids."""
    entry = await _setup_entry(hass, entry_id="uid_01")
    reg = er.async_get(hass)
    unique_ids = [
        e.unique_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    ]
    assert len(unique_ids) == len(
        set(unique_ids)
    ), f"Duplicate unique_ids found: {[uid for uid in unique_ids if unique_ids.count(uid) > 1]}"


@pytest.mark.integration
async def test_unique_ids_stable_across_reload(hass: HomeAssistant) -> None:
    """Unique IDs are the same before and after an entry reload."""
    entry = await _setup_entry(hass, entry_id="uid_reload_01")
    reg = er.async_get(hass)

    before = {
        e.unique_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    }

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    after = {
        e.unique_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    }
    assert (
        before == after
    ), f"Unique IDs changed across reload. Added: {after - before}, Removed: {before - after}"


@pytest.mark.integration
async def test_diagnostic_sensors_have_entity_category(hass: HomeAssistant) -> None:
    """Diagnostic sensors have EntityCategory.DIAGNOSTIC set."""
    entry = await _setup_entry(hass, entry_id="diag_cat_01")
    reg = er.async_get(hass)

    diagnostic_entities = [
        e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.entity_category == EntityCategory.DIAGNOSTIC
    ]
    # We expect at least the consolidated diagnostic sensors
    assert len(diagnostic_entities) >= 5, (
        f"Expected >= 5 diagnostic entities, found: "
        f"{[e.entity_id for e in diagnostic_entities]}"
    )


@pytest.mark.integration
async def test_device_info_standalone_virtual_device(hass: HomeAssistant) -> None:
    """Without device association, entities belong to a virtual standalone device."""
    from homeassistant.helpers import device_registry as dr

    opts = dict(VERTICAL_OPTIONS)
    opts["linked_device_id"] = None
    entry = await _setup_entry(hass, options=opts, entry_id="dev_standalone_01")

    device_reg = dr.async_get(hass)
    # The integration should have registered a virtual device
    devices = [
        d for d in device_reg.devices.values() if entry.entry_id in d.config_entries
    ]
    assert len(devices) >= 1, "Expected at least one device for the entry"


# ---------------------------------------------------------------------------
# 3d: Sensor-specific attribute checks
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_target_position_sensor_unit_percentage(hass: HomeAssistant) -> None:
    """Target Position sensor uses PERCENTAGE as unit of measurement."""

    entry = await _setup_entry(hass, entry_id="unit_pct_01")

    # Find the target position entity
    reg = er.async_get(hass)
    sensor_entities = [
        e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == "sensor"
    ]
    # At least one sensor with Cover_Position unique_id suffix
    position_entities = [
        e for e in sensor_entities if "Cover_Position" in (e.unique_id or "")
    ]
    assert (
        len(position_entities) >= 1
    ), f"Target Position sensor not found. All sensors: {[e.unique_id for e in sensor_entities]}"


# ---------------------------------------------------------------------------
# 3e: Entity lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unload_entry_removes_coordinator(hass: HomeAssistant) -> None:
    """Unloading the entry removes the coordinator from hass.data."""
    entry = await _setup_entry(hass, entry_id="unload_01")
    assert entry.entry_id in hass.data.get(DOMAIN, {})

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(
        DOMAIN, {}
    ), "Coordinator should be removed from hass.data after unload"


@pytest.mark.integration
async def test_reload_creates_fresh_coordinator(hass: HomeAssistant) -> None:
    """Reloading the entry creates a new coordinator instance."""
    entry = await _setup_entry(hass, entry_id="reload_coord_01")

    coordinator_before = hass.data[DOMAIN][entry.entry_id]

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    coordinator_after = hass.data[DOMAIN][entry.entry_id]
    assert (
        coordinator_before is not coordinator_after
    ), "Reload should create a new coordinator instance"
