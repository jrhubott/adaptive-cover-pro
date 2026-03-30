"""Services for Adaptive Cover Pro integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import SupportsResponse

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .export_service import EXPORT_CONFIG_SCHEMA, async_handle_export


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent — safe to call per config entry)."""
    if hass.services.has_service(DOMAIN, "export_config"):
        return
    hass.services.async_register(
        DOMAIN,
        "export_config",
        async_handle_export,
        schema=EXPORT_CONFIG_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove integration services when the last config entry is unloaded."""
    if hass.data.get(DOMAIN):
        return  # Other entries still active
    hass.services.async_remove(DOMAIN, "export_config")
