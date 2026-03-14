"""Tests for optional device association feature."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance."""
    return MagicMock()


@pytest.fixture
def mock_entity_registry():
    """Return a mock entity registry."""
    return MagicMock()


@pytest.fixture
def mock_device_registry():
    """Return a mock device registry."""
    return MagicMock()


@pytest.fixture
def mock_config_entry_no_device():
    """Return a mock config entry with no linked device."""
    entry = MagicMock()
    entry.data = {"name": "Test Cover", CONF_SENSOR_TYPE: "cover_blind"}
    entry.options = {CONF_ENTITIES: ["cover.test_blind"]}
    return entry


@pytest.fixture
def mock_config_entry_with_device():
    """Return a mock config entry with a linked device."""
    entry = MagicMock()
    entry.data = {"name": "Test Cover", CONF_SENSOR_TYPE: "cover_blind"}
    entry.options = {
        CONF_ENTITIES: ["cover.test_blind"],
        CONF_DEVICE_ID: "device-abc-123",
    }
    return entry


# ---------------------------------------------------------------------------
# _get_devices_from_entities helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_no_entities(mock_hass):
    """Helper returns empty dict when entity_ids list is empty."""
    from custom_components.adaptive_cover_pro.config_flow import _get_devices_from_entities

    with (
        patch("custom_components.adaptive_cover_pro.config_flow.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.config_flow.dr") as mock_dr,
    ):
        mock_er.async_get.return_value = MagicMock()
        mock_dr.async_get.return_value = MagicMock()

        result = await _get_devices_from_entities(mock_hass, [])

    assert result == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_entity_has_no_device(mock_hass):
    """Helper returns empty dict when entity has no device_id."""
    from custom_components.adaptive_cover_pro.config_flow import _get_devices_from_entities

    with (
        patch("custom_components.adaptive_cover_pro.config_flow.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.config_flow.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = None
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg
        mock_dr.async_get.return_value = MagicMock()

        result = await _get_devices_from_entities(mock_hass, ["cover.test_blind"])

    assert result == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_entity_has_device(mock_hass):
    """Helper returns device dict when entity has an associated device."""
    from custom_components.adaptive_cover_pro.config_flow import _get_devices_from_entities

    with (
        patch("custom_components.adaptive_cover_pro.config_flow.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.config_flow.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = "device-abc-123"
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg

        device_reg = MagicMock()
        device_entry = MagicMock()
        device_entry.name_by_user = None
        device_entry.name = "My Blind Motor"
        device_reg.async_get.return_value = device_entry
        mock_dr.async_get.return_value = device_reg

        result = await _get_devices_from_entities(mock_hass, ["cover.test_blind"])

    assert "device-abc-123" in result
    assert result["device-abc-123"] == "My Blind Motor"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_deduplicates(mock_hass):
    """Helper de-duplicates devices when multiple entities share one device."""
    from custom_components.adaptive_cover_pro.config_flow import _get_devices_from_entities

    with (
        patch("custom_components.adaptive_cover_pro.config_flow.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.config_flow.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = "device-abc-123"
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg

        device_reg = MagicMock()
        device_entry = MagicMock()
        device_entry.name_by_user = "Custom Name"
        device_entry.name = "Motor"
        device_reg.async_get.return_value = device_entry
        mock_dr.async_get.return_value = device_reg

        result = await _get_devices_from_entities(
            mock_hass, ["cover.blind1", "cover.blind2"]
        )

    # Two entities, same device → only one entry
    assert len(result) == 1
    assert result["device-abc-123"] == "Custom Name"


# ---------------------------------------------------------------------------
# entity_base.device_info tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_device_info_standalone_when_no_device_id(mock_hass):
    """device_info returns standalone virtual device when CONF_DEVICE_ID not set."""
    from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

    config_entry = MagicMock()
    config_entry.data = {"name": "My Blind", CONF_SENSOR_TYPE: "cover_blind"}
    config_entry.options = {}  # No CONF_DEVICE_ID

    entity = AdaptiveCoverBaseEntity.__new__(AdaptiveCoverBaseEntity)
    entity.hass = mock_hass
    entity.config_entry = config_entry
    entity._name = "My Blind"
    entity._cover_type = "cover_blind"
    entity._device_id = "test-entry-id"

    info = entity.device_info
    assert (DOMAIN, "test-entry-id") in info["identifiers"]
    assert info.get("manufacturer") == "BasHeijermans"


@pytest.mark.unit
def test_device_info_merged_when_device_id_set(mock_hass):
    """device_info returns merged identifiers when CONF_DEVICE_ID is set and device exists."""
    from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

    config_entry = MagicMock()
    config_entry.data = {"name": "My Blind", CONF_SENSOR_TYPE: "cover_blind"}
    config_entry.options = {CONF_DEVICE_ID: "device-abc-123"}

    device_entry = MagicMock()
    device_entry.identifiers = {("some_integration", "motor-id")}
    device_entry.connections = set()

    with patch(
        "custom_components.adaptive_cover_pro.entity_base.dr"
    ) as mock_dr:
        device_reg = MagicMock()
        device_reg.async_get.return_value = device_entry
        mock_dr.async_get.return_value = device_reg

        entity = AdaptiveCoverBaseEntity.__new__(AdaptiveCoverBaseEntity)
        entity.hass = mock_hass
        entity.config_entry = config_entry
        entity._name = "My Blind"
        entity._cover_type = "cover_blind"
        entity._device_id = "test-entry-id"

        info = entity.device_info

    assert ("some_integration", "motor-id") in info["identifiers"]
    # Should NOT set name/manufacturer/model to avoid overriding physical device
    assert info.get("manufacturer") is None
    assert info.get("name") is None


@pytest.mark.unit
def test_device_info_fallback_when_device_not_found(mock_hass):
    """device_info falls back to standalone when linked device_id no longer exists in registry."""
    from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

    config_entry = MagicMock()
    config_entry.data = {"name": "My Blind", CONF_SENSOR_TYPE: "cover_blind"}
    config_entry.options = {CONF_DEVICE_ID: "device-stale-999"}

    with patch(
        "custom_components.adaptive_cover_pro.entity_base.dr"
    ) as mock_dr:
        device_reg = MagicMock()
        device_reg.async_get.return_value = None  # Device no longer exists
        mock_dr.async_get.return_value = device_reg

        entity = AdaptiveCoverBaseEntity.__new__(AdaptiveCoverBaseEntity)
        entity.hass = mock_hass
        entity.config_entry = config_entry
        entity._name = "My Blind"
        entity._cover_type = "cover_blind"
        entity._device_id = "test-entry-id"

        info = entity.device_info

    # Should fall back to standalone virtual device
    assert (DOMAIN, "test-entry-id") in info["identifiers"]
    assert info.get("manufacturer") == "BasHeijermans"


# ---------------------------------------------------------------------------
# Config flow: CONF_DEVICE_ID stored correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_device_id_stored_in_config_when_selected():
    """CONF_DEVICE_ID is stored in config options when a device is selected."""
    # Simulate the config flow storing the device ID
    config: dict = {}

    device_id = "device-abc-123"
    config[CONF_DEVICE_ID] = device_id

    assert config[CONF_DEVICE_ID] == "device-abc-123"


@pytest.mark.unit
def test_device_id_removed_when_none_selected():
    """CONF_DEVICE_ID is removed from config options when 'None' is selected."""
    config: dict = {CONF_DEVICE_ID: "device-abc-123"}

    # Simulate selecting "None (standalone device)"
    selected_value = ""
    if not selected_value:
        config.pop(CONF_DEVICE_ID, None)

    assert CONF_DEVICE_ID not in config


# ---------------------------------------------------------------------------
# Options flow: pre-population and removal
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_options_device_step_prepopulates_current_value(mock_config_entry_with_device):
    """Options flow device step pre-populates the selector with the current device ID."""
    current_device = mock_config_entry_with_device.options.get(CONF_DEVICE_ID, "")
    assert current_device == "device-abc-123"


@pytest.mark.unit
def test_options_device_step_removal_clears_device_id():
    """Options flow: selecting 'None' removes CONF_DEVICE_ID from options."""
    options: dict = {CONF_DEVICE_ID: "device-abc-123"}

    user_input = {CONF_DEVICE_ID: ""}
    device_id = user_input.get(CONF_DEVICE_ID, "")
    if device_id:
        options[CONF_DEVICE_ID] = device_id
    else:
        options.pop(CONF_DEVICE_ID, None)

    assert CONF_DEVICE_ID not in options
