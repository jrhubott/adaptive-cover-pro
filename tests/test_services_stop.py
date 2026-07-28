"""Integration and unit tests for the adaptive_cover_pro.stop service.

Steps 1, 4, 5, 9, 10, 11 of the TDD plan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup(hass, entry_id: str = "stop_01", name: str = "Stop Cover"):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _make_coord(*, entities: list[str] | None = None):
    """Minimal mock coordinator for unit-level stop tests."""
    coord = MagicMock()
    coord.entities = entities or ["cover.test_blind"]
    coord.async_apply_user_stop = AsyncMock(return_value=("sent", "stop_cover"))
    return coord


# ---------------------------------------------------------------------------
# Step 1: Service is registered and unregistered
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stop_service_registered_after_setup(hass) -> None:
    """adaptive_cover_pro.stop is registered after async_setup_services."""
    await _setup(hass, entry_id="stop_reg_01")
    assert hass.services.has_service(
        DOMAIN, "stop"
    ), "stop service should be registered after setup"


@pytest.mark.integration
async def test_stop_service_removed_after_all_entries_unloaded(hass) -> None:
    """Stop service is removed when the last entry is unloaded."""
    entry = await _setup(hass, entry_id="stop_unload_01")
    assert hass.services.has_service(DOMAIN, "stop")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(
        DOMAIN, "stop"
    ), "stop service should be removed when last entry is unloaded"


# ---------------------------------------------------------------------------
# Step 4: cover.stop_cover is forwarded (via async_apply_user_stop → _cmd_svc)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_apply_user_stop_calls_mark_user_command_and_stop() -> None:
    """async_apply_user_stop calls mark_user_command then apply_user_stop."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock()
    coord.manager = MagicMock()
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_user_stop = AsyncMock(return_value=("sent", "stop_cover"))

    # Bind the real method
    coord.async_apply_user_stop = (
        AdaptiveDataUpdateCoordinator.async_apply_user_stop.__get__(coord)
    )

    await coord.async_apply_user_stop("cover.test_blind", trigger="stop")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test_blind", reason="stop"
    )
    coord._cmd_svc.apply_user_stop.assert_awaited_once_with("cover.test_blind")


# ---------------------------------------------------------------------------
# Step 5: ACP context stamped → was_acp_stop_context returns True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_user_stop_public_method_wraps_stop_tracker() -> None:
    """CoverCommandService.apply_user_stop delegates to _stop_tracker.call_stop_cover."""
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )

    cmd_svc = MagicMock(spec=CoverCommandService)
    cmd_svc._stop_tracker = MagicMock()
    cmd_svc._stop_tracker.call_stop_cover = AsyncMock()

    # Bind the real method
    cmd_svc.apply_user_stop = CoverCommandService.apply_user_stop.__get__(cmd_svc)

    await cmd_svc.apply_user_stop("cover.test_blind")

    cmd_svc._stop_tracker.call_stop_cover.assert_awaited_once_with("cover.test_blind")


# ---------------------------------------------------------------------------
# Step 9: ACP service path bypasses the manual_ignore_external gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acp_stop_service_bypasses_manual_ignore_external_gate() -> None:
    """When async_apply_user_stop stamps ACP context, async_check_cover_service_call
    sees was_acp_stop_context=True and ignores the external event.

    This verifies the end-to-end bypass: ACP stop → context stamped → gate passes.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from homeassistant.core import Context, Event

    entity_id = "cover.test_blind"

    # Build a minimal coordinator mock with was_acp_stop_context=True
    coordinator = MagicMock()
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.manual_ignore_external = True
    coordinator.entities = [entity_id]
    coordinator.logger = MagicMock()
    coordinator._manual_gate_closed_log = MagicMock()

    cmd_svc = MagicMock()
    # Simulate ACP context — was_acp_stop_context returns True
    cmd_svc.was_acp_stop_context = MagicMock(return_value=True)
    cmd_svc.is_waiting_for_target = MagicMock(return_value=False)
    cmd_svc.set_target = MagicMock()
    cmd_svc.discard_target = MagicMock()
    coordinator._cmd_svc = cmd_svc
    coordinator.manager = MagicMock()
    coordinator.manager.is_cover_manual = MagicMock(return_value=False)
    coordinator.manager.handle_stop_service_call = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}

    ctx = Context()
    event = Event(
        "call_service",
        {
            "domain": "cover",
            "service": "stop_cover",
            "service_data": {"entity_id": entity_id},
            "context": ctx,
        },
        context=ctx,
    )

    await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(
        coordinator, event
    )

    # Gate should have passed the ACP context check and returned early
    coordinator.manager.handle_stop_service_call.assert_not_called()


# ---------------------------------------------------------------------------
# Step 10: services.yaml documents the stop service
# ---------------------------------------------------------------------------


def test_services_yaml_has_stop_entry() -> None:
    """services.yaml contains a top-level 'stop' key."""
    import yaml
    from pathlib import Path

    services_yaml = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "services.yaml"
    )
    data = yaml.safe_load(services_yaml.read_text())
    assert (
        "stop" in data
    ), f"'stop' key missing from services.yaml; found: {list(data.keys())}"
    entry = data["stop"]
    assert "name" in entry
    assert "description" in entry
    assert "target" in entry


# ---------------------------------------------------------------------------
# Step 11: translations/en.json has stop entry
# ---------------------------------------------------------------------------


def test_en_json_has_stop_service_entry() -> None:
    """translations/en.json has a services.stop entry."""
    import json
    from pathlib import Path

    en_json = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    )
    data = json.loads(en_json.read_text())
    services = data.get("services", {})
    assert (
        "stop" in services
    ), f"'stop' key missing from en.json services; found: {list(services.keys())}"
    stop = services["stop"]
    assert "name" in stop
    assert "description" in stop
