"""Repairs platform for Adaptive Cover Pro.

Most of what this integration raises is *informational* (``is_fixable=False``)
— an indoor temperature sensor that has stayed unavailable past the debounce
window, a cover that never reaches its commanded position (issue #786 and the
health checks that followed).  Home Assistant renders those straight from the
``issues`` translation block and never opens a flow, so they get a plain
:class:`ConfirmRepairFlow`.

The one exception is ``duplicate_device`` (issue #1369): a device this config
entry solely owns that is not our service device and holds no entities at all.
It is the residue of the identifier-borrowing device model, and it is fixable
precisely because it is empty — the fix is deleting a registry record nothing
points at.  A leftover that still holds *somebody's* entities is deliberately
never raised as this issue, because removing a device removes the entities of
its config entries with it; ``state.device_link.classify_own_device`` draws that
line once, for the raise and for the removal below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow

from .const import (
    ISSUE_DATA_DEVICE_ID,
    ISSUE_DATA_ENTRY_ID,
    is_duplicate_device_issue,
)
from .state import device_link

if TYPE_CHECKING:
    from homeassistant.components.repairs import RepairsFlowResult
    from homeassistant.core import HomeAssistant


class DuplicateDeviceRepairFlow(ConfirmRepairFlow):
    """Confirm-then-delete flow for a leftover duplicate device.

    Only :meth:`async_step_confirm`'s side effect is added; the form itself,
    its placeholders and the entry creation all stay HA's — ``ConfirmRepairFlow``
    shows the form when ``user_input`` is ``None`` and creates the entry
    otherwise, so the override composes rather than reimplements.  The repairs
    manager deletes the issue once the flow completes.
    """

    def __init__(self, data: dict[str, Any] | None) -> None:
        """Bind the issue's stored ``entry_id`` / ``device_id`` payload."""
        self._data = data or {}

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Remove the duplicate device, then hand back to the confirm flow."""
        if user_input is not None:
            entry_id = self._data.get(ISSUE_DATA_ENTRY_ID)
            device_id = self._data.get(ISSUE_DATA_DEVICE_ID)
            if not (
                entry_id
                and device_id
                and device_link.remove_duplicate_device(self.hass, entry_id, device_id)
            ):
                # The device is gone, is not solely ours, is our own service
                # device, or has acquired entities since the Repair was raised.
                # Deleting it now would take those entities with it, so refuse
                # and say so.
                return self.async_abort(reason="device_in_use")
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the fix flow for *issue_id*.

    Dispatches on the duplicate-device family only — the per-entry, per-device
    suffix makes an exact match impossible — so every informational issue keeps
    the side-effect-free confirm flow it has always had.  The family test is
    ``const.is_duplicate_device_issue``, beside the builder that produces these
    ids, so nothing here restates their shape.
    """
    if is_duplicate_device_issue(issue_id):
        return DuplicateDeviceRepairFlow(data)
    return ConfirmRepairFlow()
