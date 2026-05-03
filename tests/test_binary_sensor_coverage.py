"""Unit tests for binary_sensor.py uncovered branches."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.binary_sensor import (
    AdaptiveCoverPositionMismatchSensor,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)


def _make_config_entry(
    options: dict | None = None, sensor_type: str = SensorType.BLIND
):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: sensor_type}
    entry.options = options or {}
    return entry


def _make_coordinator(mock_hass=None):
    coord = MagicMock()
    coord.hass = mock_hass or MagicMock()
    coord.logger = MagicMock()
    coord.entities = []
    coord._cmd_svc = MagicMock()
    coord._cmd_svc._position_tolerance = 5
    coord._cmd_svc.get_target = MagicMock(return_value=None)
    return coord


# ---------------------------------------------------------------------------
# Glare active binary sensor creation
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_glare_active_binary_sensor_created_when_enabled(hass) -> None:
    """async_setup_entry creates a glare_active binary sensor when glare zones are enabled."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_GLARE_ZONES] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Glare Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=options,
        entry_id="glare_bs_01",
        title="Glare Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    binary_sensor_entities = [
        e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == "binary_sensor"
    ]
    entity_unique_ids = [e.unique_id for e in binary_sensor_entities]
    assert any(
        "glare_active" in uid for uid in entity_unique_ids
    ), f"Expected glare_active binary sensor, got unique_ids: {entity_unique_ids}"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_glare_active_binary_sensor_not_created_when_awning(hass) -> None:
    """async_setup_entry does NOT create a glare_active sensor for awning type."""
    from tests.ha_helpers import HORIZONTAL_OPTIONS, _patch_coordinator_refresh

    options = dict(HORIZONTAL_OPTIONS)
    options[CONF_ENABLE_GLARE_ZONES] = True  # ignored for awning

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Awning Test", CONF_SENSOR_TYPE: SensorType.AWNING},
        options=options,
        entry_id="glare_bs_awning_01",
        title="Awning Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    binary_sensor_entities = [
        e
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == "binary_sensor"
    ]
    entity_unique_ids = [e.unique_id for e in binary_sensor_entities]
    assert not any("glare_active" in uid for uid in entity_unique_ids)

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Position mismatch sensor: is_on logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_position_mismatch_is_on_true_when_delta_exceeds_tolerance():
    """is_on returns True when any entity has |target - actual| > tolerance."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=50)
    coord._get_current_position = MagicMock(return_value=42)  # delta = 8 > 5
    coord._cmd_svc._position_tolerance = 5

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    assert sensor.is_on is True


@pytest.mark.unit
def test_position_mismatch_is_on_false_when_delta_within_tolerance():
    """is_on returns False when all entities have |target - actual| <= tolerance."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=50)
    coord._get_current_position = MagicMock(return_value=48)  # delta = 2 <= 5
    coord._cmd_svc._position_tolerance = 5

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    assert sensor.is_on is False


@pytest.mark.unit
def test_position_mismatch_is_on_false_when_no_target():
    """is_on returns False when no target has been set (target is None)."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=None)  # no target
    coord._get_current_position = MagicMock(return_value=50)

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    assert sensor.is_on is False


@pytest.mark.unit
def test_position_mismatch_is_on_false_when_actual_none():
    """is_on returns False when actual position is None (cover unavailable)."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=50)
    coord._get_current_position = MagicMock(return_value=None)

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    assert sensor.is_on is False


# ---------------------------------------------------------------------------
# Position mismatch sensor: extra_state_attributes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_position_mismatch_extra_state_attributes_with_mismatch():
    """extra_state_attributes includes per-entity detail when target/actual both set."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=50)
    coord._get_current_position = MagicMock(return_value=42)
    coord._cmd_svc._position_tolerance = 5
    coord._cmd_svc.get_diagnostics.return_value = {
        "target": 50,
        "actual": 42,
        "retry_count": 0,
    }

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "tolerance" in attrs
    assert "entities" in attrs
    entity_detail = attrs["entities"]["cover.test"]
    assert entity_detail["target_position"] == 50
    assert entity_detail["actual_position"] == 42
    assert entity_detail["mismatch"] is True


@pytest.mark.unit
def test_position_mismatch_extra_state_attributes_no_entities():
    """extra_state_attributes omits 'entities' key when no diagnostics are available."""
    coord = _make_coordinator()
    coord.entities = ["cover.test"]
    coord._cmd_svc.get_target = MagicMock(return_value=None)
    coord._cmd_svc._position_tolerance = 5
    coord._cmd_svc.get_diagnostics.return_value = {
        "target": None,
        "actual": None,
        "retry_count": 0,
    }

    config_entry = _make_config_entry()
    sensor = AdaptiveCoverPositionMismatchSensor(
        config_entry=config_entry,
        unique_id="test_entry",
        coordinator=coord,
    )

    attrs = sensor.extra_state_attributes
    assert "tolerance" in attrs
    assert "entities" not in attrs
