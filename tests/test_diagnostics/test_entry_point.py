"""Regression guards for the diagnostics module/package name collision.

Background: the integration had both a standalone diagnostics.py and a
diagnostics/ package. Python's import system resolves the package first,
so HA's loader never found async_get_config_entry_diagnostics and Download
Diagnostics silently returned nothing.

These tests catch any re-introduction of that collision:
  (a) plain-import smoke test — no hass, runs in milliseconds
  (b) HA loader test — uses async_get_integration, the same code path HA's
      diagnostics component uses when the UI button is clicked
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.const import DOMAIN


def test_async_get_config_entry_diagnostics_is_exported() -> None:
    """Normal package import must expose the HA entry-point attribute.

    If this fails, Python's import system is resolving a diagnostics/ package
    that shadows the HA entry-point function.
    """
    from custom_components.adaptive_cover_pro import diagnostics

    assert hasattr(diagnostics, "async_get_config_entry_diagnostics"), (
        "HA's diagnostics loader imports this attribute by name; "
        "if it's missing, Download Diagnostics silently returns nothing."
    )


@pytest.mark.integration
async def test_ha_loader_finds_diagnostics_entry_point(hass: HomeAssistant) -> None:
    """HA's integration loader must resolve our diagnostics platform.

    Uses async_get_integration — the same code path HA's diagnostics
    component uses when the Download Diagnostics UI button is clicked.
    """
    from homeassistant.loader import async_get_integration

    integration = await async_get_integration(hass, DOMAIN)
    diag_platform = await hass.async_add_executor_job(
        integration.get_platform, "diagnostics"
    )
    assert hasattr(diag_platform, "async_get_config_entry_diagnostics"), (
        "HA's loader could not find async_get_config_entry_diagnostics on "
        "the diagnostics platform — Download Diagnostics would silently fail."
    )
