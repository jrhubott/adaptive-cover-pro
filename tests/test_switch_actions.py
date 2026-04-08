"""Unit tests for switch.py uncovered branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch


def _make_config_entry(options: dict | None = None, sensor_type: str = SensorType.BLIND):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: sensor_type}
    entry.options = options or {CONF_DEFAULT_HEIGHT: 60}
    return entry


def _make_coordinator(mock_hass=None):
    coord = MagicMock()
    coord.hass = mock_hass or MagicMock()
    coord.logger = MagicMock()
    coord.entities = []
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock()
    coord.manager = MagicMock()
    coord.manager.is_cover_manual = MagicMock(return_value=False)
    coord.manager.manual_controlled = []
    coord.check_adaptive_time = True
    coord._build_position_context = MagicMock(return_value=MagicMock())
    coord.async_refresh = AsyncMock()
    coord.state = 50
    return coord


def _make_switch(key: str = "automatic_control", coordinator=None, config_entry=None):
    coord = coordinator or _make_coordinator()
    entry = config_entry or _make_config_entry()
    return AdaptiveCoverSwitch(
        entry_id="test_entry",
        hass=coord.hass,
        config_entry=entry,
        coordinator=coord,
        switch_name="Automatic Control",
        initial_state=True,
        key=key,
    )


# ---------------------------------------------------------------------------
# async_turn_on: automatic_control key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_on_automatic_control_sends_position_to_non_manual_covers():
    """Turn on automatic_control sends position to non-manual covers in window."""
    coord = _make_coordinator()
    coord.entities = ["cover.test_1", "cover.test_2"]
    coord.manager.is_cover_manual.side_effect = lambda e: e == "cover.test_2"

    switch = _make_switch(key="automatic_control", coordinator=coord)
    await switch.async_turn_on()

    # apply_position called once — for cover.test_1 (cover.test_2 is manual)
    assert coord._cmd_svc.apply_position.call_count == 1
    call_args = coord._cmd_svc.apply_position.call_args
    assert call_args[0][0] == "cover.test_1"
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_on_automatic_control_skips_outside_time_window():
    """Turn on automatic_control skips apply when not in time window."""
    coord = _make_coordinator()
    coord.entities = ["cover.test_1"]
    coord.manager.is_cover_manual.return_value = False
    coord.check_adaptive_time = False  # outside window

    switch = _make_switch(key="automatic_control", coordinator=coord)
    await switch.async_turn_on()

    coord._cmd_svc.apply_position.assert_not_called()
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_on_with_added_kwarg_skips_position_send():
    """Turn on with added=True does not send positions (startup init path)."""
    coord = _make_coordinator()
    coord.entities = ["cover.test_1"]

    switch = _make_switch(key="automatic_control", coordinator=coord)
    await switch.async_turn_on(added=True)

    coord._cmd_svc.apply_position.assert_not_called()
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_on_non_automatic_control_key_no_position_send():
    """Turn on a non-automatic_control switch does not trigger position logic."""
    coord = _make_coordinator()
    coord.entities = ["cover.test_1"]

    switch = _make_switch(key="switch_mode", coordinator=coord)
    await switch.async_turn_on()

    coord._cmd_svc.apply_position.assert_not_called()
    coord.async_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# async_turn_off: automatic_control key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_off_automatic_control_resets_manual_overrides():
    """Turn off automatic_control resets all manual override entities."""
    coord = _make_coordinator()
    coord.manager.manual_controlled = ["cover.test_1", "cover.test_2"]
    coord.return_to_default_toggle = False

    switch = _make_switch(key="automatic_control", coordinator=coord)
    await switch.async_turn_off()

    assert coord.manager.reset.call_count == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_off_automatic_control_sends_default_position_when_toggle_on():
    """Turn off automatic_control sends default_height position when return_to_default_toggle is True."""
    coord = _make_coordinator()
    coord.entities = ["cover.test_1"]
    coord.manager.manual_controlled = []
    coord.return_to_default_toggle = True
    config_entry = _make_config_entry(options={CONF_DEFAULT_HEIGHT: 75})
    coord.config_entry = config_entry

    switch = _make_switch(key="automatic_control", coordinator=coord, config_entry=config_entry)
    await switch.async_turn_off()

    coord._cmd_svc.apply_position.assert_called_once()
    call_args = coord._cmd_svc.apply_position.call_args
    assert call_args[0][1] == 75  # default_height from options
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_turn_off_non_automatic_control_key_no_special_logic():
    """Turn off a non-automatic_control switch does not reset overrides or send positions."""
    coord = _make_coordinator()
    coord.manager.manual_controlled = ["cover.test_1"]

    switch = _make_switch(key="switch_mode", coordinator=coord)
    await switch.async_turn_off()

    coord.manager.reset.assert_not_called()
    coord._cmd_svc.apply_position.assert_not_called()
    coord.async_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Conditional switch creation — integration
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_glare_zone_switches_created_when_configured(hass) -> None:
    """Glare zone switches are created for each named zone."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_GLARE_ZONES] = True
    options["glare_zone_1_name"] = "Zone One"
    options["glare_zone_2_name"] = "Zone Two"
    options["glare_zone_3_name"] = ""  # unnamed — skipped
    options["glare_zone_4_name"] = ""

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Glare Switch Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=options,
        entry_id="glare_sw_01",
        title="Glare Switch Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    switch_entities = [
        e for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == "switch"
    ]
    switch_names = [e.unique_id for e in switch_entities]
    # Should have 2 glare zone switches (2 named zones)
    glare_switches = [s for s in switch_names if "Glare Zone" in s]
    assert len(glare_switches) == 2


@pytest.mark.integration
async def test_climate_switches_created_when_climate_mode_with_entities(hass) -> None:
    """Climate-related switches (temp_switch) created when climate_mode + temp entity."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options["climate_mode"] = True
    options["temp_entity"] = "sensor.indoor_temp"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Climate Switch Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=options,
        entry_id="climate_sw_01",
        title="Climate Switch Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    switch_entities = [
        e for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == "switch"
    ]
    # Should have more switches than the base count (temp toggle added)
    assert len(switch_entities) >= 3
