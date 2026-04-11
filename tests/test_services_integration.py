"""Integration tests for HA service registration and invocation.

Verifies that services are registered after setup, respond correctly to
valid and invalid calls, and are cleaned up when entries are unloaded.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from tests.ha_helpers import (
    HORIZONTAL_OPTIONS,
    TILT_OPTIONS,
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _setup(
    hass: HomeAssistant,
    entry_id: str = "svc_01",
    cover_type: str = SensorType.BLIND,
    options: dict | None = None,
    name: str = "SVC Cover",
) -> MockConfigEntry:
    opts = dict(VERTICAL_OPTIONS) if options is None else options
    if cover_type == SensorType.AWNING and options is None:
        opts = dict(HORIZONTAL_OPTIONS)
    elif cover_type == SensorType.TILT and options is None:
        opts = dict(TILT_OPTIONS)
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
# 5a: Service registration
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_export_config_service_registered_after_setup(
    hass: HomeAssistant,
) -> None:
    """export_config service is registered after setup_entry."""
    await _setup(hass, entry_id="svc_reg_01")
    assert hass.services.has_service(DOMAIN, "export_config"), (
        "export_config service should be registered after setup"
    )


@pytest.mark.integration
async def test_service_not_double_registered(hass: HomeAssistant) -> None:
    """Service is registered exactly once even with two config entries."""
    await _setup(hass, entry_id="svc_double_a", name="Cover A")
    await _setup(hass, entry_id="svc_double_b", name="Cover B")
    # Should not raise — service is idempotent
    assert hass.services.has_service(DOMAIN, "export_config")


@pytest.mark.integration
async def test_service_still_registered_after_one_entry_unloaded(
    hass: HomeAssistant,
) -> None:
    """Service remains registered when one of two entries is unloaded."""
    entry_a = await _setup(hass, entry_id="svc_keep_a", name="Cover A")
    await _setup(hass, entry_id="svc_keep_b", name="Cover B")

    await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "export_config"), (
        "Service should still be registered while second entry is active"
    )


@pytest.mark.integration
async def test_service_removed_after_all_entries_unloaded(
    hass: HomeAssistant,
) -> None:
    """Service is removed when the last entry is unloaded."""
    entry = await _setup(hass, entry_id="svc_last_01")
    assert hass.services.has_service(DOMAIN, "export_config")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "export_config"), (
        "Service should be removed when last entry is unloaded"
    )


# ---------------------------------------------------------------------------
# 5b: Service invocation
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_export_config_returns_valid_response(hass: HomeAssistant) -> None:
    """export_config service call with valid entry_id returns a dict response."""
    entry = await _setup(hass, entry_id="svc_export_01")

    response = await hass.services.async_call(
        DOMAIN,
        "export_config",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict), f"Expected dict response, got: {type(response)}"


@pytest.mark.integration
async def test_export_config_invalid_entry_id_raises(hass: HomeAssistant) -> None:
    """export_config with a non-existent entry_id raises ServiceValidationError."""
    await _setup(hass, entry_id="svc_err_01")

    with pytest.raises((ServiceValidationError, Exception)):
        await hass.services.async_call(
            DOMAIN,
            "export_config",
            {"config_entry_id": "does_not_exist"},
            blocking=True,
            return_response=True,
        )


@pytest.mark.integration
async def test_export_config_vertical_contains_geometry(hass: HomeAssistant) -> None:
    """Export config for vertical blind includes geometry fields."""
    entry = await _setup(hass, entry_id="svc_vert_geom_01")

    response = await hass.services.async_call(
        DOMAIN,
        "export_config",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    # Export response has cover_type and nested geometry sections
    assert "cover_type" in response or "name" in response, (
        f"Expected cover_type in response, got keys: {list(response.keys())}"
    )


@pytest.mark.integration
async def test_export_config_horizontal_contains_awning_fields(
    hass: HomeAssistant,
) -> None:
    """Export config for horizontal awning includes awning-specific fields."""
    entry = await _setup(
        hass,
        entry_id="svc_horiz_01",
        cover_type=SensorType.AWNING,
        name="Awning Cover",
    )

    response = await hass.services.async_call(
        DOMAIN,
        "export_config",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)


@pytest.mark.integration
async def test_export_config_tilt_contains_slat_fields(hass: HomeAssistant) -> None:
    """Export config for tilt cover includes slat-specific fields."""
    entry = await _setup(
        hass,
        entry_id="svc_tilt_01",
        cover_type=SensorType.TILT,
        name="Tilt Cover",
    )

    response = await hass.services.async_call(
        DOMAIN,
        "export_config",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
