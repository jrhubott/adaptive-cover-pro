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
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity

from .const import (
    CONF_GROUP_ENABLE_CLIMATE_SENSOR,
    CONF_GROUP_ENABLE_POSITION_SENSOR,
    CONF_GROUP_ENABLE_STATE_SENSOR,
    CONF_GROUP_ENABLE_WHO_WON_SENSOR,
    DEFAULT_GROUP_ENABLE_SENSOR,
    GROUP_SCENE_SELECT_AUTO,
    GroupScene,
    GroupState,
)
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


class GroupClimateSensor(_GroupEntityBase, SensorEntity):
    """Read-only rollup of member climate modes (issue #790, Phase 3).

    The mode when all reporting members agree, "mixed" when they disagree,
    None when no member reports one. Values reuse the member Climate Status
    sensor's wire strings (single-sourced in
    ``helpers.climate_mode_from_diagnostics``); the group shares no climate
    inputs — that stays Building Profile territory.
    """

    _attr_translation_key = "group_climate_mode"
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:sun-thermometer-outline"

    # Wire-stable disagreement state, file-private: produced only by this
    # sensor (distinct concept from GroupState.MIXED, which is positional).
    _CLIMATE_MIXED = "mixed"

    @property
    def native_value(self) -> str | None:
        """The agreed member climate mode, "mixed", or None."""
        reported = {
            mode
            for mode in self.coordinator.member_climate_modes().values()
            if mode is not None
        }
        if not reported:
            return None
        if len(reported) == 1:
            return next(iter(reported))
        return self._CLIMATE_MIXED

    @property
    def extra_state_attributes(self) -> dict:
        """Per-member climate-mode map."""
        return {"member_climate_modes": self.coordinator.member_climate_modes()}


class AdaptiveGroupCover(_GroupEntityBase, CoverEntity):
    """Opt-in aggregate cover entity for a group (issue #790, Phase 3).

    Position reads the group aggregates; commands are USER semantics — a
    dashboard drag, routed exactly like the per-cover proxy (member
    manual-override engagement and floor clamps apply). Tilt features are
    exposed only when every member — ACP and generic — has a tilt axis.
    """

    _attr_translation_key = "group_cover"

    def __init__(self, *args) -> None:
        """Initialize the aggregate cover."""
        super().__init__(*args, "group_cover")

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Position/open/close/stop always; tilt only for all-tilt rosters."""
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.STOP
        )
        if self.coordinator.all_members_tilt():
            features |= CoverEntityFeature.SET_TILT_POSITION
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Aggregate (average) member position."""
        return self.coordinator.data.position

    @property
    def is_closed(self) -> bool | None:
        """True when every member reports fully closed."""
        if self.coordinator.data.state is GroupState.UNKNOWN:
            return None
        return self.coordinator.data.state is GroupState.CLOSED

    async def async_set_cover_position(self, **kwargs) -> None:
        """Fan the requested position out as a user command."""
        await self.coordinator.async_set_position(int(kwargs["position"]))

    async def async_open_cover(self, **kwargs) -> None:  # noqa: ARG002
        """Open all members (position 100)."""
        await self.coordinator.async_set_position(100)

    async def async_close_cover(self, **kwargs) -> None:  # noqa: ARG002
        """Close all members (position 0)."""
        await self.coordinator.async_set_position(0)

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        """Fan the requested tilt out on the dedicated tilt path."""
        await self.coordinator.async_set_tilt(int(kwargs["tilt_position"]))

    async def async_stop_cover(self, **kwargs) -> None:  # noqa: ARG002
        """Stop every member immediately."""
        await self.coordinator.async_stop()


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
    options = config_entry.options

    def _enabled(key: str) -> bool:
        return bool(options.get(key, DEFAULT_GROUP_ENABLE_SENSOR))

    sensors: list[SensorEntity] = []
    if _enabled(CONF_GROUP_ENABLE_POSITION_SENSOR):
        sensors.append(GroupPositionSensor(*args, "group_position"))
    if _enabled(CONF_GROUP_ENABLE_STATE_SENSOR):
        sensors.append(GroupStateSensor(*args, "group_state"))
    # Active scene is always exposed — it is the select's state twin.
    sensors.append(GroupActiveSceneSensor(*args, "group_active_scene"))
    if _enabled(CONF_GROUP_ENABLE_CLIMATE_SENSOR):
        sensors.append(GroupClimateSensor(*args, "group_climate_mode"))
    if _enabled(CONF_GROUP_ENABLE_WHO_WON_SENSOR):
        sensors.append(GroupWhoWonSensor(*args, "group_who_won"))
    return sensors


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


def build_group_covers(
    entry_id: str,
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: GroupCoordinator,
) -> list[CoverEntity]:
    """Build the opt-in aggregate cover (empty when the toggle is off)."""
    return [AdaptiveGroupCover(entry_id, hass, config_entry, coordinator)]
