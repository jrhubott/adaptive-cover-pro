"""Repairs platform for Adaptive Cover Pro (issue #786).

The integration raises only *informational* Repairs (``is_fixable=False``) via
``SensorHealthManager`` — e.g. an indoor temperature sensor that has stayed
unavailable past the debounce window. Home Assistant renders these directly
from the ``issues`` translation block; a non-fixable issue never triggers a fix
flow, so this platform exists only to satisfy the repairs-platform contract and
return a no-op confirm flow should an issue ever be marked fixable.
"""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict | None,
) -> RepairsFlow:
    """Return a confirm-only fix flow.

    All issues this integration raises are informational (``is_fixable=False``)
    and never reach this hook; a plain :class:`ConfirmRepairFlow` is the safe
    default if that ever changes.
    """
    return ConfirmRepairFlow()
