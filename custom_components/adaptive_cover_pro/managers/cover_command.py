"""Cover command service for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.cover.const import DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import state_attr

from ..const import ATTR_POSITION, ATTR_TILT_POSITION, CONF_DEFAULT_HEIGHT, CONF_SUNSET_POS
from ..helpers import check_cover_features, get_last_updated, get_open_close_state


class CoverCommandService:
    """Service for sending commands to cover entities.

    Encapsulates cover capability detection, position reading, delta checking,
    service call preparation, action tracking, and diagnostic recording.

    """

    # Default capabilities for covers when entity not ready
    _DEFAULT_CAPABILITIES = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        cover_type: str,
        grace_mgr,
        pos_verify_mgr,
        open_close_threshold: int = 50,
    ) -> None:
        """Initialize the CoverCommandService.

        Args:
            hass: Home Assistant instance
            logger: Logger instance
            cover_type: Cover type string (cover_blind, cover_awning, cover_tilt)
            grace_mgr: GracePeriodManager instance
            pos_verify_mgr: PositionVerificationManager instance
            open_close_threshold: Threshold (0-100) for open/close-only covers

        """
        self._hass = hass
        self._logger = logger
        self._cover_type = cover_type
        self._grace_mgr = grace_mgr
        self._pos_verify_mgr = pos_verify_mgr
        self._open_close_threshold = open_close_threshold

        self.wait_for_target: dict[str, bool] = {}
        self.target_call: dict[str, Any] = {}
        self.last_cover_action: dict[str, Any] = {
            "entity_id": None,
            "service": None,
            "position": None,
            "calculated_position": None,
            "threshold_used": None,
            "inverse_state_applied": False,
            "timestamp": None,
            "covers_controlled": 0,
        }
        self.last_skipped_action: dict[str, Any] = {
            "entity_id": None,
            "reason": None,
            "calculated_position": None,
            "timestamp": None,
        }

    # --- Properties ---

    @property
    def is_tilt_cover(self) -> bool:
        """Check if this is a tilt cover."""
        return self._cover_type == "cover_tilt"

    # --- Threshold update ---

    def update_threshold(self, threshold: int) -> None:
        """Update the open/close threshold.

        Args:
            threshold: New threshold value (0-100)

        """
        self._open_close_threshold = threshold

    # --- Capability detection ---

    def get_cover_capabilities(self, entity: str) -> dict[str, bool]:
        """Get cover capabilities with fallback to safe defaults.

        Args:
            entity: The cover entity ID

        Returns:
            Dict of capabilities (has_set_position, has_set_tilt_position,
            has_open, has_close)

        """
        caps = check_cover_features(self._hass, entity)
        if caps is None:
            self._logger.debug("Cover %s not ready, using safe defaults", entity)
            return self._DEFAULT_CAPABILITIES.copy()
        return caps

    # --- Position reading ---

    def _read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Read position based on cover type and capabilities.

        Args:
            entity: Entity ID
            caps: Capabilities dict
            state_obj: Optional state object (for event handling)

        Returns:
            Current position or None

        """
        if self.is_tilt_cover:
            if caps.get("has_set_tilt_position", True):
                if state_obj:
                    return state_obj.attributes.get("current_tilt_position")
                return state_attr(self._hass, entity, "current_tilt_position")
        else:
            if caps.get("has_set_position", True):
                if state_obj:
                    return state_obj.attributes.get("current_position")
                return state_attr(self._hass, entity, "current_position")

        return get_open_close_state(self._hass, entity)

    def read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Public wrapper for reading position based on cover capabilities.

        Args:
            entity: Entity ID
            caps: Capabilities dict
            state_obj: Optional state object (for event handling)

        Returns:
            Current position or None

        """
        return self._read_position_with_capabilities(entity, caps, state_obj)

    def _get_current_position(self, entity: str) -> int | None:
        """Get current position of cover.

        For position-capable covers, returns current_position or
        current_tilt_position. For open/close-only covers, maps state to
        0 (closed) or 100 (open).

        Args:
            entity: Cover entity ID

        Returns:
            Current position or None

        """
        caps = self.get_cover_capabilities(entity)
        return self._read_position_with_capabilities(entity, caps)

    # --- Delta checks ---

    def check_position(
        self, entity: str, state: int, sun_just_appeared: bool = False
    ) -> bool:
        """Check if position differs from state.

        Bypasses check if sun just came into field of view to ensure
        covers reposition even if calculated position equals current position.

        Args:
            entity: Cover entity ID to check
            state: Target position to compare against
            sun_just_appeared: If True, bypass equality check

        Returns:
            True if position differs from state or sun just appeared,
            False if position matches state

        """
        position = self._get_current_position(entity)
        if position is not None:
            if sun_just_appeared:
                self._logger.debug(
                    "Bypassing position equality check: sun just came into field of view "
                    "(entity: %s, position: %s, state: %s)",
                    entity,
                    position,
                    state,
                )
                return True

            return position != state

        self._logger.debug("Cover is already at position %s", state)
        return False

    def check_position_delta(
        self,
        entity: str,
        state: int,
        min_change: int,
        special_positions: list[int],
    ) -> bool:
        """Check if position delta exceeds threshold.

        Determines if position change is large enough to warrant sending a
        command. Always allows moves to/from special positions (0, 100, default,
        sunset) to ensure responsive behavior at key positions.

        Args:
            entity: Cover entity ID to check
            state: Target position to move to
            min_change: Minimum position delta to trigger a move
            special_positions: List of positions that always bypass delta check

        Returns:
            True if delta exceeds min_change threshold or moving to/from special
            position, False otherwise

        """
        position = self._get_current_position(entity)
        if position is not None:
            condition = abs(position - state) >= min_change

            self._logger.debug(
                "Entity: %s, position: %s, state: %s, delta position: %s, min_change: %s, condition: %s",
                entity,
                position,
                state,
                abs(position - state),
                min_change,
                condition,
            )

            # Bypass delta check when moving TO special positions
            if state in special_positions:
                self._logger.debug(
                    "Bypassing delta check: moving TO special position %s", state
                )
                condition = True

            # Bypass delta check when moving FROM special positions
            elif position in special_positions:
                self._logger.debug(
                    "Bypassing delta check: moving FROM special position %s to calculated position %s",
                    position,
                    state,
                )
                condition = True

            return condition
        return True

    def check_time_delta(self, entity: str, time_threshold: int) -> bool:
        """Check if time delta exceeds threshold.

        Determines if enough time has passed since last position command to
        warrant sending a new command.

        Args:
            entity: Cover entity ID to check
            time_threshold: Minimum minutes between commands

        Returns:
            True if time since last update exceeds time_threshold (minutes),
            False otherwise. Returns True if entity has no last_updated time.

        """
        now = dt.datetime.now(dt.UTC)
        last_updated = get_last_updated(entity, self._hass)
        if last_updated is not None:
            condition = now - last_updated >= dt.timedelta(minutes=time_threshold)
            self._logger.debug(
                "Entity: %s, time delta: %s, threshold: %s, condition: %s",
                entity,
                now - last_updated,
                time_threshold,
                condition,
            )
            return condition
        return True

    # --- Service call preparation and tracking ---

    def prepare_position_service_call(
        self,
        entity: str,
        state: int,
        caps: dict[str, bool],
        inverse_state: bool = False,
    ) -> tuple[str | None, dict | None, bool]:
        """Determine service and data based on capabilities.

        Prepares the service call, sets wait_for_target, target_call, starts
        grace period, and calls pos_verify_mgr.mark_commanded when needed.

        Args:
            entity: Entity ID
            state: Target position (0-100)
            caps: Cover capabilities dict
            inverse_state: Whether inverse state is enabled (unused here but
                available for tracking)

        Returns:
            Tuple of (service_name, service_data, supports_position).
            Returns (None, None, False) if capabilities are insufficient.

        """
        # Determine if cover supports position control
        supports_position = False
        if self.is_tilt_cover:
            supports_position = caps.get("has_set_tilt_position", True)
        else:
            supports_position = caps.get("has_set_position", True)

        self._logger.debug(
            "Cover %s: supports_position=%s, caps=%s",
            entity,
            supports_position,
            caps,
        )

        if supports_position:
            service = SERVICE_SET_COVER_POSITION
            service_data = {ATTR_ENTITY_ID: entity}

            if self.is_tilt_cover:
                service = SERVICE_SET_COVER_TILT_POSITION
                service_data[ATTR_TILT_POSITION] = state
            else:
                service_data[ATTR_POSITION] = state

            self.wait_for_target[entity] = True
            self.target_call[entity] = state
            self._grace_mgr.start_command_grace_period(entity)
            self._logger.debug(
                "Set wait for target %s and target call %s",
                self.wait_for_target,
                self.target_call,
            )
        else:
            has_open = caps.get("has_open", False)
            has_close = caps.get("has_close", False)

            if not has_open or not has_close:
                self._logger.warning(
                    "Cover %s does not support both open and close. Skipping.",
                    entity,
                )
                return None, None, False

            if state >= self._open_close_threshold:
                service = "open_cover"
                self.target_call[entity] = 100
                self._pos_verify_mgr.mark_commanded(entity)
            else:
                service = "close_cover"
                self.target_call[entity] = 0
                self._pos_verify_mgr.mark_commanded(entity)

            service_data = {ATTR_ENTITY_ID: entity}
            self.wait_for_target[entity] = True
            self._grace_mgr.start_command_grace_period(entity)

            self._logger.debug(
                "Using open/close control: state=%s, threshold=%s, service=%s",
                state,
                self._open_close_threshold,
                service,
            )

        return service, service_data, supports_position

    def track_cover_action(
        self,
        entity: str,
        service: str,
        state: int,
        supports_position: bool,
        inverse_state: bool = False,
    ) -> None:
        """Track cover action for diagnostic sensor.

        Args:
            entity: Entity ID
            service: Service name called
            state: Requested position
            supports_position: Whether position control is used
            inverse_state: Whether inverse state is applied

        """
        self.last_cover_action = {
            "entity_id": entity,
            "service": service,
            "position": state if supports_position else self.target_call[entity],
            "calculated_position": state,
            "threshold_used": self._open_close_threshold if not supports_position else None,
            "inverse_state_applied": inverse_state,
            "timestamp": dt.datetime.now().isoformat(),
            "covers_controlled": 1,
        }

    def record_skipped_action(self, entity: str, reason: str, state: int) -> None:
        """Record a skipped cover action for diagnostic tracking.

        Args:
            entity: Cover entity ID that was skipped
            reason: Human-readable reason for skipping
            state: Target position that would have been set

        """
        self.last_skipped_action = {
            "entity_id": entity,
            "reason": reason,
            "calculated_position": state,
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        }

    # --- Async execution ---

    async def execute_service_call(self, service: str, service_data: dict) -> None:
        """Execute a cover service call.

        Args:
            service: Service name (e.g. set_cover_position, open_cover)
            service_data: Service data dict (must include ATTR_ENTITY_ID)

        """
        await self._hass.services.async_call(COVER_DOMAIN, service, service_data)


def build_special_positions(options: dict) -> list[int]:
    """Build list of special positions from options.

    Special positions (0, 100, default_height, sunset_pos) bypass the delta
    check so covers always reposition at key values.

    Args:
        options: Configuration options dictionary

    Returns:
        List of integer position values that are always allowed

    """
    special_positions = [0, 100]
    default_height = options.get(CONF_DEFAULT_HEIGHT)
    sunset_pos = options.get(CONF_SUNSET_POS)
    if default_height is not None:
        special_positions.append(default_height)
    if sunset_pos is not None:
        special_positions.append(sunset_pos)
    return special_positions
