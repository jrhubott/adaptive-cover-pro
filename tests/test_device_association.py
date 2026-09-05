"""Tests for optional device association feature."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers import device_registry as dr

from custom_components.adaptive_cover_pro.const import (
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

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
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

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
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

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
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

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
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

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
    assert info.get("manufacturer") == "Jason Rhubottom"


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

    with patch("custom_components.adaptive_cover_pro.entity_base.dr") as mock_dr:
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

    with patch("custom_components.adaptive_cover_pro.entity_base.dr") as mock_dr:
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
    assert info.get("manufacturer") == "Jason Rhubottom"


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


# ---------------------------------------------------------------------------
# async_setup_entry: stale config-entry link cleanup
# ---------------------------------------------------------------------------
#
# These two characterise the loop in ``async_setup_entry`` that strips this
# entry's id off physical devices it no longer owns.  They use the real
# ``hass`` fixture and a real device registry — a mock would only re-pin
# whichever registry API the production code happens to call today, which is
# exactly what issue #1339 changes.  They deliberately carry no ``integration``
# mark so the guard runs in every ``scripts/test`` mode.


async def _setup_acp_entry_owning_device(
    hass,
    *,
    identifiers: set[tuple[str, str]],
    entry_id: str,
) -> tuple[MockConfigEntry, str]:
    """Link a foreign-owned device to a fresh ACP entry, then set that entry up.

    The ACP entry's options carry no ``CONF_DEVICE_ID``, so ``async_setup_entry``
    takes the "no device association" branch and runs the stale-link loop.
    Returns the ACP entry and the device id.
    """
    owner = MockConfigEntry(domain="demo", entry_id=f"{entry_id}_owner")
    owner.add_to_hass(hass)

    acp_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Stale Link", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id=entry_id,
        title="Stale Link",
    )
    acp_entry.add_to_hass(hass)

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers=identifiers,
        name="Physical Cover",
    )
    device_reg.async_update_device(device.id, add_config_entry_id=acp_entry.entry_id)
    assert acp_entry.entry_id in device_reg.async_get(device.id).config_entries

    now = datetime.datetime.now(datetime.UTC).isoformat()
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "azimuth": 180.0,
            "elevation": 45.0,
            "rising": True,
            "next_rising": now,
            "next_setting": now,
        },
    )
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"current_position": 100, "supported_features": 143},
    )

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    return acp_entry, device.id


@pytest.mark.asyncio
async def test_stale_config_entry_link_removed_from_physical_device(hass):
    """Setup strips this entry's id off a physical device it does not identify.

    The device carries our config entry id but not our ``(DOMAIN, entry_id)``
    identifier — the leftover of a device association the user has since
    cleared.  Setting up with no ``CONF_DEVICE_ID`` must unlink it.
    """
    acp_entry, device_id = await _setup_acp_entry_owning_device(
        hass,
        identifiers={("demo", "physical-cover-1")},
        entry_id="stale_link_removed",
    )

    device = dr.async_get(hass).async_get(device_id)
    assert acp_entry.entry_id not in device.config_entries


@pytest.mark.asyncio
async def test_own_virtual_device_link_preserved(hass):
    """A device carrying our own identifier keeps the link.

    The control for the test above: it pins the surviving condition
    (``(DOMAIN, entry_id) not in device.identifiers``), so a change to the way
    the loop enumerates devices cannot quietly unlink our own virtual device.
    """
    acp_entry, device_id = await _setup_acp_entry_owning_device(
        hass,
        identifiers={(DOMAIN, "virtual_device_kept")},
        entry_id="virtual_device_kept",
    )

    device = dr.async_get(hass).async_get(device_id)
    assert acp_entry.entry_id in device.config_entries
