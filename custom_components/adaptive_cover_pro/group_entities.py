"""Entities exposed by a Cover Group entry (issue #790, Phase 1).

One module holds every group entity class plus a per-platform builder, so
the platform files (``sensor.py`` / ``switch.py`` / ``button.py`` /
``select.py``) each add a single ``is_orchestrator`` branch and stay free
of group logic. Unique_ids follow the locked ``f"{entry_id}_{suffix}"``
contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity

from .const import GROUP_SCENE_SELECT_AUTO, GroupScene
from .entity_base import AdaptiveCoverBaseEntity
from .pipeline.handlers import GroupLockHandler, GroupSceneHandler

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .group_coordinator import GroupCoordinator

# Group entities read GroupCoordinator state, not a cover pipeline.
# ``AdaptiveCoverBaseEntity`` is coordinator-shape-agnostic (device info,
# availability gate, render-signature dedup), so it is reused as-is.


class _GroupEntityBase(AdaptiveCoverBaseEntity):
    """Shared init: group coordinator + locked unique_id suffix."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: GroupCoordinator,
        unique_id_suffix: str,
    ) -> None:
        """Initialize with the locked unique_id."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"


class GroupPositionSensor(_GroupEntityBase, SensorEntity):
    """Average position across member covers, with per-member attributes."""

    _attr_translation_key = "group_position"
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:page-layout-body"

    @property
    def native_value(self) -> int | None:
        """Average of readable member positions."""
        return self.coordinator.data.position

    @property
    def extra_state_attributes(self) -> dict:
        """Per-member position readings."""
        return {"member_positions": self.coordinator.data.member_positions}


class GroupStateSensor(_GroupEntityBase, SensorEntity):
    """Aggregate open/closed/mixed classification of the member covers."""

    _attr_translation_key = "group_state"
    # Text/status sensor: empty unit excludes it from the logbook.
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:window-shutter-cog"

    @property
    def native_value(self) -> str:
        """Aggregate GroupState classification."""
        return self.coordinator.data.state


class GroupActiveSceneSensor(_GroupEntityBase, SensorEntity):
    """The last scene activated on this group, if any."""

    _attr_translation_key = "group_active_scene"
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:palette-outline"

    @property
    def native_value(self) -> str | None:
        """Wire value of the last activated scene, or None."""
        scene = self.coordinator.active_scene
        return str(scene) if scene is not None else None


class GroupAutomationSwitch(_GroupEntityBase, SwitchEntity):
    """Bulk-enable/disable sun-tracking automation across all ACP members.

    Reflects the last bulk command sent through this group (defaults to on),
    not a consensus of member states — members remain individually togglable.
    """

    _attr_translation_key = "group_automation"
    _attr_icon = "mdi:cog-clockwise"

    def __init__(self, *args) -> None:
        """Initialize with automation considered on."""
        super().__init__(*args, "group_automation")
        self._attr_is_on = True

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ARG002
        """Bulk-enable automation on all members."""
        await self.coordinator.async_set_automation(True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ARG002
        """Bulk-disable automation on all members."""
        await self.coordinator.async_set_automation(False)
        self._attr_is_on = False
        self.async_write_ha_state()


class GroupLockSwitch(_GroupEntityBase, SwitchEntity):
    """Freeze every member in place via the LOCK intent at safety priority."""

    _attr_translation_key = "group_lock"
    _attr_icon = "mdi:lock-outline"

    def __init__(self, *args) -> None:
        """Initialize unlocked."""
        super().__init__(*args, "group_lock")
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ARG002
        """Push the lock intent to every member."""
        await self.coordinator.async_set_lock(True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ARG002
        """Release the lock (re-pushing an active scene, if any)."""
        await self.coordinator.async_set_lock(False)
        self._attr_is_on = False
        self.async_write_ha_state()


class GroupWhoWonSensor(_GroupEntityBase, SensorEntity):
    """How many members this group currently drives, with per-member detail.

    A member counts as group-driven when its pipeline's winning handler is
    one of the group handlers (names imported, never string literals).
    """

    _attr_translation_key = "group_who_won"
    _attr_native_unit_of_measurement = "members"
    _attr_icon = "mdi:scale-balance"

    _GROUP_HANDLER_NAMES = frozenset({GroupSceneHandler.name, GroupLockHandler.name})

    @property
    def native_value(self) -> int:
        """Count of members whose pipeline is currently won by this group."""
        return sum(
            1
            for winner in self.coordinator.member_winners().values()
            if winner in self._GROUP_HANDLER_NAMES
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Per-member winning-handler map."""
        return {"member_winners": self.coordinator.member_winners()}


class GroupSceneButton(_GroupEntityBase, ButtonEntity):
    """Activate one built-in scene across the group."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: GroupCoordinator,
        scene: GroupScene,
    ) -> None:
        """Initialize the button for one scene."""
        super().__init__(
            entry_id, hass, config_entry, coordinator, f"group_scene_{scene}"
        )
        self._scene = scene
        self._attr_translation_key = f"group_scene_{scene}"
        self._attr_icon = "mdi:palette"

    async def async_press(self) -> None:
        """Activate this button's scene across the group."""
        await self.coordinator.async_activate_scene(self._scene)


class GroupClearOverridesButton(_GroupEntityBase, ButtonEntity):
    """Clear manual overrides on every ACP member of the group."""

    _attr_translation_key = "group_clear_overrides"
    _attr_icon = "mdi:account-cancel-outline"

    def __init__(self, *args) -> None:
        """Initialize the clear-overrides button."""
        super().__init__(*args, "group_clear_overrides")

    async def async_press(self) -> None:
        """Clear manual overrides on every ACP member."""
        await self.coordinator.async_clear_overrides()


class GroupSceneSelect(_GroupEntityBase, SelectEntity):
    """Scene picker: selecting an option activates the scene group-wide."""

    _attr_translation_key = "group_scene_select"
    _attr_icon = "mdi:palette-swatch"

    def __init__(self, *args) -> None:
        """Initialize the scene picker: Auto (no scene) plus the built-ins."""
        super().__init__(*args, "group_scene_select")
        self._attr_options = [
            GROUP_SCENE_SELECT_AUTO,
            *(str(scene) for scene in GroupScene),
        ]

    @property
    def current_option(self) -> str | None:
        """Wire value of the active scene; Auto when no scene claims the group."""
        scene = self.coordinator.active_scene
        return str(scene) if scene is not None else GROUP_SCENE_SELECT_AUTO

    async def async_select_option(self, option: str) -> None:
        """Activate the picked scene — or release the claim via Auto."""
        if option == GROUP_SCENE_SELECT_AUTO:
            await self.coordinator.async_clear_scene()
        else:
            await self.coordinator.async_activate_scene(GroupScene(option))
        self.async_write_ha_state()


def build_group_sensors(
    entry_id: str,
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: GroupCoordinator,
) -> list[SensorEntity]:
    """Build the aggregate sensors for one group entry."""
    args = (entry_id, hass, config_entry, coordinator)
    return [
        GroupPositionSensor(*args, "group_position"),
        GroupStateSensor(*args, "group_state"),
        GroupActiveSceneSensor(*args, "group_active_scene"),
        GroupWhoWonSensor(*args, "group_who_won"),
    ]


def build_group_switches(
    entry_id: str,
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: GroupCoordinator,
) -> list[SwitchEntity]:
    """Build the bulk switches for one group entry."""
    return [
        GroupAutomationSwitch(entry_id, hass, config_entry, coordinator),
        GroupLockSwitch(entry_id, hass, config_entry, coordinator),
    ]


def build_group_buttons(
    entry_id: str,
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: GroupCoordinator,
) -> list[ButtonEntity]:
    """Per-scene activate buttons plus the clear-overrides button."""
    args = (entry_id, hass, config_entry, coordinator)
    return [
        *(GroupSceneButton(*args, scene) for scene in GroupScene),
        GroupClearOverridesButton(*args),
    ]


def build_group_selects(
    entry_id: str,
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: GroupCoordinator,
) -> list[SelectEntity]:
    """Build the scene-picker select for one group entry."""
    return [GroupSceneSelect(entry_id, hass, config_entry, coordinator)]
