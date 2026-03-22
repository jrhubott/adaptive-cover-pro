"""Tests for legacy Adaptive Cover import detection and flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_DEFAULT_HEIGHT,
    CONF_DISTANCE,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_SENSOR_TYPE,
    LEGACY_DOMAIN,
)


def _make_legacy_entry(
    entry_id="legacy-1",
    name="Living Room",
    sensor_type="cover_blind",
    state_name="NOT_LOADED",
):
    """Create a mock legacy config entry."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"name": name, CONF_SENSOR_TYPE: sensor_type}
    entry.options = {
        CONF_ENTITIES: ["cover.living_room_blinds"],
        CONF_AZIMUTH: 180,
        CONF_HEIGHT_WIN: 2.0,
        CONF_DISTANCE: 0.5,
        CONF_FOV_LEFT: 45,
        CONF_FOV_RIGHT: 45,
        CONF_DEFAULT_HEIGHT: 50,
    }
    # Simulate a config entry state enum
    state_mock = MagicMock()
    state_mock.name = state_name
    entry.state = state_mock
    return entry


@pytest.fixture
def flow():
    """Return a ConfigFlowHandler instance with a mocked hass."""
    handler = ConfigFlowHandler()
    handler.hass = MagicMock()
    handler.context = {}
    return handler


@pytest.mark.unit
def test_detect_legacy_entries_not_loaded(flow):
    """Detection finds entries even when they are NOT_LOADED."""
    entry = _make_legacy_entry(state_name="NOT_LOADED")
    flow.hass.config_entries.async_entries.return_value = [entry]

    # Call synchronously by running the coroutine
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._detect_legacy_entries(flow.hass)
    )

    assert len(result) == 1
    assert result[0].entry_id == "legacy-1"
    flow.hass.config_entries.async_entries.assert_called_once_with(LEGACY_DOMAIN)


@pytest.mark.unit
def test_detect_legacy_entries_setup_error(flow):
    """Detection finds entries in SETUP_ERROR state."""
    entry = _make_legacy_entry(state_name="SETUP_ERROR")
    flow.hass.config_entries.async_entries.return_value = [entry]

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._detect_legacy_entries(flow.hass)
    )

    assert len(result) == 1


@pytest.mark.unit
def test_detect_legacy_entries_empty(flow):
    """Detection returns empty list when no legacy entries exist."""
    flow.hass.config_entries.async_entries.return_value = []

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._detect_legacy_entries(flow.hass)
    )

    assert result == []


@pytest.mark.unit
def test_detect_legacy_entries_multiple(flow):
    """Detection finds all legacy entries regardless of state."""
    entries = [
        _make_legacy_entry("id-1", "Room A", state_name="NOT_LOADED"),
        _make_legacy_entry("id-2", "Room B", state_name="SETUP_ERROR"),
        _make_legacy_entry("id-3", "Room C", state_name="LOADED"),
    ]
    flow.hass.config_entries.async_entries.return_value = entries

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._detect_legacy_entries(flow.hass)
    )

    assert len(result) == 3


@pytest.mark.unit
def test_import_select_single_entry(flow):
    """import_select stores a single entry ID (not a list) and wraps it."""
    entry = _make_legacy_entry()
    flow.legacy_entries = [
        {
            "entry_id": entry.entry_id,
            "name": entry.data["name"],
            "type": entry.data[CONF_SENSOR_TYPE],
        }
    ]
    flow.selected_for_import = []

    import asyncio

    # Simulate user selecting one entry
    user_input = {"selected_entries": "legacy-1"}
    with patch.object(flow, "async_step_import_review", new=AsyncMock(return_value={})):
        asyncio.get_event_loop().run_until_complete(
            flow.async_step_import_select(user_input)
        )

    assert flow.selected_for_import == ["legacy-1"]


@pytest.mark.unit
def test_import_execute_uses_first_entry(flow):
    """import_execute processes the first (only) selected entry."""
    entry = _make_legacy_entry()
    flow.selected_for_import = [entry.entry_id]
    flow.hass.config_entries.async_entries.return_value = [entry]

    import asyncio

    with patch.object(
        flow,
        "async_create_entry",
        return_value={"type": "create_entry"},
    ) as mock_create, patch.object(
        flow,
        "_ensure_unique_name",
        new=AsyncMock(side_effect=lambda name: name),
    ):
        asyncio.get_event_loop().run_until_complete(flow.async_step_import_execute())

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["data"]["name"] == "Living Room"
    assert call_kwargs["data"][CONF_SENSOR_TYPE] == "cover_blind"


@pytest.mark.unit
def test_import_execute_aborts_if_entry_missing(flow):
    """import_execute aborts when the selected entry no longer exists."""
    flow.selected_for_import = ["nonexistent-id"]
    flow.hass.config_entries.async_entries.return_value = []

    import asyncio

    with patch.object(
        flow,
        "async_abort",
        return_value={"type": "abort"},
    ) as mock_abort:
        asyncio.get_event_loop().run_until_complete(flow.async_step_import_execute())

    mock_abort.assert_called_once_with(reason="import_failed")


@pytest.mark.unit
def test_import_execute_aborts_if_no_selection(flow):
    """import_execute aborts when no entries are selected."""
    flow.selected_for_import = []

    import asyncio

    with patch.object(
        flow,
        "async_abort",
        return_value={"type": "abort"},
    ) as mock_abort:
        asyncio.get_event_loop().run_until_complete(flow.async_step_import_execute())

    mock_abort.assert_called_once_with(reason="no_entries_selected")


@pytest.mark.unit
def test_ensure_unique_name_no_conflict(flow):
    """_ensure_unique_name returns the name unchanged when no conflict."""
    flow.hass.config_entries.async_entries.return_value = []

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._ensure_unique_name("Living Room")
    )
    assert result == "Living Room"


@pytest.mark.unit
def test_ensure_unique_name_with_conflict(flow):
    """_ensure_unique_name appends (Imported) suffix on conflict."""
    existing = MagicMock()
    existing.data = {"name": "Living Room"}
    flow.hass.config_entries.async_entries.return_value = [existing]

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        flow._ensure_unique_name("Living Room")
    )
    assert result == "Living Room (Imported)"
