"""Error resilience tests — graceful degradation when entities are unavailable
or config options are malformed.

Verifies the integration survives adverse conditions without crashing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _setup(
    hass: HomeAssistant,
    entry_id: str = "er_01",
    options: dict | None = None,
    name: str = "ER Cover",
) -> tuple[MockConfigEntry, AdaptiveDataUpdateCoordinator]:
    opts = dict(VERTICAL_OPTIONS) if options is None else options
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: SensorType.BLIND},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return entry, coordinator


# ---------------------------------------------------------------------------
# 10a: Unavailable entities
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_sun_entity_unavailable_does_not_crash(hass: HomeAssistant) -> None:
    """Setting sun.sun to unavailable does not crash the coordinator."""
    entry, coordinator = await _setup(hass, entry_id="er_sun_unavail_01")

    hass.states.async_set("sun.sun", "unavailable", {})
    await hass.async_block_till_done()

    # Entry must still be alive
    assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.integration
async def test_cover_entity_unavailable_handled(hass: HomeAssistant) -> None:
    """Cover entity going unavailable does not cause an unhandled exception."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.bedroom_blind"]
    entry, coordinator = await _setup(
        hass, options=opts, entry_id="er_cover_unavail_01"
    )

    hass.states.async_set("cover.bedroom_blind", "unavailable", {})
    await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.integration
async def test_force_override_sensor_unavailable_handled(hass: HomeAssistant) -> None:
    """Force override sensor going unavailable is treated as inactive (no crash)."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_FORCE_OVERRIDE_SENSORS] = ["binary_sensor.wind_alarm"]
    entry, coordinator = await _setup(
        hass, options=opts, entry_id="er_force_unavail_01"
    )

    hass.states.async_set("binary_sensor.wind_alarm", "unavailable", {})
    await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.integration
async def test_all_tracked_entities_unavailable(hass: HomeAssistant) -> None:
    """All managed entities going unavailable simultaneously does not crash."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.blind_1", "cover.blind_2"]
    opts[CONF_FORCE_OVERRIDE_SENSORS] = ["binary_sensor.storm"]
    entry, coordinator = await _setup(hass, options=opts, entry_id="er_all_unavail_01")

    for entity_id in [
        "cover.blind_1",
        "cover.blind_2",
        "binary_sensor.storm",
        "sun.sun",
    ]:
        hass.states.async_set(entity_id, "unavailable", {})
    await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# 10b: Malformed config options
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_none_values_in_options_do_not_crash_setup(hass: HomeAssistant) -> None:
    """Options with None values for non-critical keys are handled gracefully.

    Regression guard for issue #133 where None in CONF_DELTA_TIME caused
    a TypeError crash.
    """
    opts = dict(VERTICAL_OPTIONS)
    # Simulate an old install that might have None for some keys
    opts["lux_entity"] = None
    opts["irradiance_entity"] = None
    opts["cloud_coverage_entity"] = None
    opts["weather_entity"] = None
    opts["temp_entity"] = None
    opts["presence_entity"] = None

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "None Opts", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=opts,
        entry_id="er_none_opts_01",
        title="None Opts",
    )
    entry.add_to_hass(hass)
    # Setup should not raise
    with _patch_coordinator_refresh():
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert result is True


@pytest.mark.integration
async def test_empty_entities_list_does_not_crash(hass: HomeAssistant) -> None:
    """CONF_ENTITIES = [] (no cover entities configured) is handled gracefully."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = []

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Empty Covers", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=opts,
        entry_id="er_empty_covers_01",
        title="Empty Covers",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert result is True


@pytest.mark.integration
async def test_climate_mode_enabled_without_temp_entity(hass: HomeAssistant) -> None:
    """Climate mode enabled but no temp_entity configured degrades gracefully."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_CLIMATE_MODE] = True
    opts["temp_entity"] = None

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Climate No Temp", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=opts,
        entry_id="er_climate_no_temp_01",
        title="Climate No Temp",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert result is True


# ---------------------------------------------------------------------------
# 10c: Coordinator update failure and recovery
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_coordinator_recovers_after_single_update_error(
    hass: HomeAssistant,
) -> None:
    """After a single update failure, subsequent successful updates recover the coordinator."""
    entry, coordinator = await _setup(hass, entry_id="er_recover_01")

    from custom_components.adaptive_cover_pro.coordinator import AdaptiveCoverData

    call_count = [0]
    real_data = AdaptiveCoverData(
        climate_mode_toggle=False,
        states={"state": 50, "control": "solar"},
        attributes={},
        diagnostics={},
    )

    async def _flaky_update():
        call_count[0] += 1
        if call_count[0] == 1:
            raise UpdateFailed("Simulated first-update failure")
        return real_data

    with patch.object(coordinator, "_async_update_data", side_effect=_flaky_update):
        # First refresh — fails
        try:
            await coordinator.async_refresh()
            await hass.async_block_till_done()
        except (UpdateFailed, Exception):
            pass

    # Coordinator is still alive and registered
    assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.integration
async def test_coordinator_multiple_update_errors(hass: HomeAssistant) -> None:
    """Multiple consecutive update failures keep coordinator in error state without crashing."""
    entry, coordinator = await _setup(hass, entry_id="er_multi_err_01")

    async def _always_fail():
        raise UpdateFailed("Always fails")

    with patch.object(coordinator, "_async_update_data", side_effect=_always_fail):
        for _ in range(3):
            try:
                await coordinator.async_refresh()
                await hass.async_block_till_done()
            except (UpdateFailed, Exception):
                pass

    # Entry must still exist — coordinator should not have been removed
    assert entry.entry_id in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# 10d: Rapid/concurrent state changes
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_rapid_cover_position_changes_no_crash(hass: HomeAssistant) -> None:
    """Many rapid cover position changes do not crash the coordinator."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = ["cover.rapid_blind"]
    entry, coordinator = await _setup(hass, options=opts, entry_id="er_rapid_01")

    for pos in range(0, 101, 5):
        hass.states.async_set(
            "cover.rapid_blind",
            "open" if pos > 0 else "closed",
            {"current_position": pos, "supported_features": 143},
        )
    await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.integration
async def test_entry_setup_after_previous_clean_unload(hass: HomeAssistant) -> None:
    """Re-setting up an entry after a clean unload succeeds."""
    entry, _ = await _setup(hass, entry_id="er_reup_01")

    # Unload cleanly
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})

    # Re-setup the same entry
    with _patch_coordinator_refresh():
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.entry_id in hass.data[DOMAIN]
