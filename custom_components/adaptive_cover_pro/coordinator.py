"""The Coordinator for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

import numpy as np
import pytz
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    Event,
    HomeAssistant,
    State,
    callback,
)

# EventStateChangedData was added in Home Assistant 2024.4+
# For backwards compatibility with older versions
try:
    from homeassistant.core import EventStateChangedData
except ImportError:
    # Fallback for older Home Assistant versions
    EventStateChangedData = dict  # type: ignore[misc,assignment]
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.helpers.template import state_attr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .calculation import (
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
    ClimateCoverData,
    ClimateCoverState,
    NormalCoverState,
)
from .config_context_adapter import ConfigContextAdapter
from .services.configuration_service import ConfigurationService
from .const import (
    _LOGGER,
    COMMAND_GRACE_PERIOD_SECONDS,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_RETURN_SUNSET,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_LOW,
    CONF_TEMP_HIGH,
    CONF_PRESENCE_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_TRANSPARENT_BLIND,
    CONF_LUX_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_IRRADIANCE_THRESHOLD,
    DOMAIN,
    LOGGER,
    MAX_POSITION_RETRIES,
    POSITION_CHECK_INTERVAL_MINUTES,
    POSITION_TOLERANCE_PERCENT,
    STARTUP_GRACE_PERIOD_SECONDS,
    DEFAULT_MOTION_TIMEOUT,
    ControlStatus,
)
from .enums import ClimateStrategy, ControlMethod
from .helpers import (
    get_datetime_from_str,
    get_safe_state,
)
from .managers.cover_command import CoverCommandService, build_special_positions
from .managers.grace_period import GracePeriodManager
from .managers.manual_override import AdaptiveCoverManager, inverse_state
from .managers.motion import MotionManager
from .managers.position_verification import PositionVerificationManager
from .state.climate_provider import ClimateProvider


@dataclass
class StateChangedData:
    """StateChangedData class."""

    entity_id: str
    old_state: State | None
    new_state: State | None


@dataclass
class AdaptiveCoverData:
    """AdaptiveCoverData class."""

    climate_mode_toggle: bool
    states: dict
    attributes: dict
    diagnostics: dict | None = None


class AdaptiveDataUpdateCoordinator(DataUpdateCoordinator[AdaptiveCoverData]):
    """Adaptive cover data update coordinator."""

    config_entry: ConfigEntry

    # Default capabilities for covers when entity not ready
    _DEFAULT_CAPABILITIES = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize coordinator with config entry and setup state tracking.

        Sets up the data update coordinator with all necessary state tracking,
        entity listeners, manual override detection, position verification,
        and grace period management for reliable cover control.

        Args:
            hass: Home Assistant instance

        """
        super().__init__(hass, LOGGER, name=DOMAIN)

        self.logger = ConfigContextAdapter(_LOGGER)
        self.logger.set_config_name(self.config_entry.data.get("name"))
        self._cover_type = self.config_entry.data.get("sensor_type")
        self._climate_mode = self.config_entry.options.get(CONF_CLIMATE_MODE, False)
        self._switch_mode = True if self._climate_mode else False
        self._inverse_state = self.config_entry.options.get(CONF_INVERSE_STATE, False)
        self._use_interpolation = self.config_entry.options.get(CONF_INTERP, False)
        self._track_end_time = self.config_entry.options.get(CONF_RETURN_SUNSET)
        self._temp_toggle = None
        self._automatic_control = None
        self._manual_toggle = None
        self._lux_toggle = None
        self._irradiance_toggle = None
        self._return_to_default_toggle = None
        self._start_time = None
        self._sun_end_time = None
        self._sun_start_time = None
        # self._end_time = None
        self.manual_reset = self.config_entry.options.get(
            CONF_MANUAL_OVERRIDE_RESET, False
        )
        self.manual_duration = self.config_entry.options.get(
            CONF_MANUAL_OVERRIDE_DURATION, {"hours": 2}
        )
        self.state_change = False
        self.cover_state_change = False
        self.first_refresh = False
        self.timed_refresh = False
        self.climate_state = None
        self.climate_data = None  # Store climate_data for P1 diagnostics
        self.climate_strategy = None  # Store climate strategy for diagnostics
        self.control_method = ControlMethod.SOLAR
        self.state_change_data: StateChangedData | None = None
        self.raw_calculated_position = 0  # Store raw position for diagnostics
        self.manager = AdaptiveCoverManager(
            self.hass, self.manual_duration, self.logger
        )
        self.ignore_intermediate_states = self.config_entry.options.get(
            CONF_MANUAL_IGNORE_INTERMEDIATE, False
        )
        # Grace period management (command + startup)
        self._grace_mgr = GracePeriodManager(
            logger=self.logger,
            command_grace_seconds=COMMAND_GRACE_PERIOD_SECONDS,
            startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
        )
        # Motion control tracking
        self._motion_mgr = MotionManager(hass=self.hass, logger=self.logger)
        self._update_listener = None
        self._scheduled_time = dt.datetime.now()

        self._cached_options = None

        # Initialize configuration service
        self._config_service = ConfigurationService(
            self.hass,
            self.config_entry,
            self.logger,
            self._cover_type,
            self._temp_toggle,
            self._lux_toggle,
            self._irradiance_toggle,
        )

        # Climate state provider
        self._climate_provider = ClimateProvider(
            hass=self.hass, logger=self.logger
        )

        # Track position explanation for change detection logging
        self._last_position_explanation: str = ""

        # Position verification tracking
        self._pos_verify_mgr = PositionVerificationManager(
            logger=self.logger,
            check_interval_minutes=POSITION_CHECK_INTERVAL_MINUTES,
            position_tolerance=POSITION_TOLERANCE_PERCENT,
            max_retries=MAX_POSITION_RETRIES,
        )

        # Cover command service — encapsulates service calls, delta checks, and tracking
        self._cmd_svc = CoverCommandService(
            hass=self.hass,
            logger=self.logger,
            cover_type=self._cover_type,
            grace_mgr=self._grace_mgr,
            pos_verify_mgr=self._pos_verify_mgr,
            open_close_threshold=self.config_entry.options.get(
                CONF_OPEN_CLOSE_THRESHOLD, 50
            ),
        )

        # Track time window state transitions (for responsive end time handling)
        self._last_time_window_state: bool | None = None

        # Track sun validity transitions (for responsive sun in-front detection)
        self._last_sun_validity_state: bool | None = None

    # --- Property delegates for CoverCommandService state ---

    @property
    def wait_for_target(self) -> dict:
        """Delegate to CoverCommandService.wait_for_target."""
        return self._cmd_svc.wait_for_target

    @property
    def target_call(self) -> dict:
        """Delegate to CoverCommandService.target_call."""
        return self._cmd_svc.target_call

    @property
    def last_cover_action(self) -> dict:
        """Delegate to CoverCommandService.last_cover_action."""
        return self._cmd_svc.last_cover_action

    @property
    def last_skipped_action(self) -> dict:
        """Delegate to CoverCommandService.last_skipped_action."""
        return self._cmd_svc.last_skipped_action

    @property
    def _open_close_threshold(self) -> int:
        """Delegate to CoverCommandService threshold."""
        return self._cmd_svc._open_close_threshold

    def _get_cover_capabilities(self, entity: str) -> dict[str, bool]:
        """Get cover capabilities — delegates to CoverCommandService."""
        return self._cmd_svc.get_cover_capabilities(entity)

    @property
    def is_tilt_cover(self) -> bool:
        """Check if this is a tilt cover."""
        return self._cover_type == "cover_tilt"

    @property
    def is_blind_cover(self) -> bool:
        """Check if this is a vertical blind."""
        return self._cover_type == "cover_blind"

    @property
    def is_awning_cover(self) -> bool:
        """Check if this is a horizontal awning."""
        return self._cover_type == "cover_awning"

    @property
    def is_force_override_active(self) -> bool:
        """Check if any force override sensor is active.

        Returns:
            True if any configured force override sensor is in "on" state

        """
        sensors = self.config_entry.options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
        if not sensors:
            return False

        for sensor in sensors:
            state = self.hass.states.get(sensor)
            if state and state.state == "on":
                self.logger.debug(
                    "Force override sensor %s is active (state: %s)",
                    sensor,
                    state.state,
                )
                return True

        return False

    @property
    def is_motion_detected(self) -> bool:
        """Check if any motion sensor currently detects motion.

        Returns:
            True if any motion sensor is "on" or no sensors configured (assume presence)

        """
        return self._motion_mgr.is_motion_detected

    @property
    def is_motion_timeout_active(self) -> bool:
        """Check if motion timeout is active (no motion for timeout duration).

        Returns:
            True if timeout expired and covers should use default position

        """
        return self._motion_mgr.is_motion_timeout_active

    def _read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj: State | None = None
    ) -> int | None:
        """Read position based on cover type and capabilities — delegates to CoverCommandService."""
        return self._cmd_svc.read_position_with_capabilities(entity, caps, state_obj)

    async def async_config_entry_first_refresh(self) -> None:
        """Config entry first refresh."""
        self.first_refresh = True
        await super().async_config_entry_first_refresh()
        self.logger.debug("Config entry first refresh")
        # Start startup grace period to prevent false manual override detection
        self._start_startup_grace_period()
        # Start position verification after first refresh
        self._start_position_verification()

    async def async_timed_refresh(self, event) -> None:
        """Control state at end time."""

        now = dt.datetime.now()
        if self.end_time is not None:
            time = self.end_time
        if self.end_time_entity is not None:
            time = get_safe_state(self.hass, self.end_time_entity)

        self.logger.debug("Checking timed refresh. End time: %s, now: %s", time, now)

        time_check = now - get_datetime_from_str(time)
        if time is not None and (time_check <= dt.timedelta(seconds=5)):
            self.timed_refresh = True
            self.logger.debug("Timed refresh triggered")
            await self.async_refresh()
        else:
            self.logger.debug("Timed refresh, but: not equal to end time")

    async def async_check_entity_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Event handler for tracked entity state changes.

        Called when any tracked entity (sun, temperature, weather, presence)
        changes state. Triggers a coordinator refresh to recalculate positions
        and update covers if needed.

        Args:
            event: State change event containing old and new state

        """
        entity_id = event.data.get("entity_id", "unknown")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        old_val = old_state.state if old_state else "None"
        new_val = new_state.state if new_state else "None"
        self.logger.debug(
            "Entity state change: %s (%s → %s)", entity_id, old_val, new_val
        )
        self.state_change = True
        await self.async_refresh()

    async def async_check_cover_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Event handler for cover entity state changes.

        Called when a managed cover entity changes position. Used to detect
        manual override (user-initiated position changes) by comparing actual
        position to expected calculated position. Respects grace periods to
        avoid false positives during command execution or HA restart.

        Args:
            event: State change event containing old and new state

        """
        self.logger.debug("Cover state change")
        data = event.data
        if data["old_state"] is None:
            self.logger.debug("Old state is None")
            return
        self.state_change_data = StateChangedData(
            data["entity_id"], data["old_state"], data["new_state"]
        )
        if self.state_change_data.old_state.state != "unknown":
            self.cover_state_change = True
            self.process_entity_state_change()
            await self.async_refresh()
        else:
            self.logger.debug("Old state is unknown, not processing")

    async def async_check_motion_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle motion sensor state changes with debouncing.

        Motion detected (on) → immediate response, cancel timeout
        Motion stopped (off) → start timeout if no other sensors active

        Args:
            event: State change event containing old and new state

        """
        data = event.data
        entity_id = data["entity_id"]
        new_state = data["new_state"]

        if new_state is None:
            return

        self.logger.debug(
            "Motion sensor %s state changed to %s",
            entity_id,
            new_state.state,
        )

        if new_state.state == "on":
            # Motion detected - immediate response
            was_timeout_active = self._motion_mgr._motion_timeout_active
            self._motion_mgr.record_motion_detected()

            if was_timeout_active:
                self.logger.info("Motion detected - resuming automatic sun positioning")
                self.state_change = True
                await self.async_refresh()

        elif new_state.state == "off":
            # Motion stopped - check if any other sensors still active
            if not self.is_motion_detected:
                self._start_motion_timeout()
            else:
                self.logger.debug(
                    "Motion stopped on %s but another sensor still active — timeout not started",
                    entity_id,
                )

    def process_entity_state_change(self):
        """Process cover state change for manual override detection.

        Examines cover position changes to determine if they were user-initiated
        (manual override) or automation-initiated. Handles grace periods to prevent
        false positives during command execution, and ignores intermediate states
        (opening/closing) if configured to do so.

        """
        event = self.state_change_data
        self.logger.debug("Processing state change event: %s", event)
        entity_id = event.entity_id
        if self.ignore_intermediate_states and event.new_state.state in [
            "opening",
            "closing",
        ]:
            self.logger.debug("Ignoring intermediate state change for %s", entity_id)
            return
        if self.wait_for_target.get(entity_id):
            # Check if still in grace period
            if self._is_in_grace_period(entity_id):
                self.logger.debug(
                    "Position change for %s ignored (in grace period)", entity_id
                )
                return  # Ignore ALL position changes during grace period

            # Grace period expired, check if we reached target
            caps = self._get_cover_capabilities(entity_id)

            # Get position based on capability
            position = self._read_position_with_capabilities(
                entity_id, caps, event.new_state
            )

            if position == self.target_call.get(entity_id):
                self.wait_for_target[entity_id] = False
                self.logger.debug("Position %s reached for %s", position, entity_id)
            self.logger.debug("Wait for target: %s", self.wait_for_target)
        else:
            self.logger.debug("No wait for target call for %s", entity_id)

    def _is_in_grace_period(self, entity_id: str) -> bool:
        """Check if entity is in command grace period."""
        return self._grace_mgr.is_in_command_grace_period(entity_id)

    def _start_grace_period(self, entity_id: str) -> None:
        """Start grace period for entity."""
        self._grace_mgr.start_command_grace_period(entity_id)

    def _cancel_grace_period(self, entity_id: str) -> None:
        """Cancel grace period task for entity."""
        self._grace_mgr.cancel_command_grace_period(entity_id)

    def _is_in_startup_grace_period(self) -> bool:
        """Check if integration is in startup grace period."""
        return self._grace_mgr.is_in_startup_grace_period()

    def _start_startup_grace_period(self) -> None:
        """Start startup grace period after first refresh."""
        self._grace_mgr.start_startup_grace_period()

    def _start_motion_timeout(self) -> None:
        """Start motion timeout for no-motion detection."""

        async def _refresh_with_state_change() -> None:
            self.state_change = True
            await self.async_refresh()

        self._motion_mgr.start_motion_timeout(
            refresh_callback=_refresh_with_state_change
        )

    def _cancel_motion_timeout(self) -> None:
        """Cancel motion timeout task."""
        self._motion_mgr.cancel_motion_timeout()

    @callback
    def _async_cancel_update_listener(self) -> None:
        """Cancel the scheduled update."""
        if self._update_listener:
            self._update_listener()
            self._update_listener = None

    async def async_timed_end_time(self) -> None:
        """Control state at end time."""
        self.logger.debug("Scheduling end time update at %s", self._end_time)
        self._async_cancel_update_listener()
        self.logger.debug(
            "End time: %s, Track end time: %s, Scheduled time: %s, Condition: %s",
            self._end_time,
            self._track_end_time,
            self._scheduled_time,
            self._end_time > self._scheduled_time,
        )
        self._update_listener = async_track_point_in_time(
            self.hass, self.async_timed_refresh, self._end_time
        )
        self._scheduled_time = self._end_time

    def _calculate_cover_state(self, cover_data, options) -> int:
        """Calculate cover state and update internal state.

        Args:
            cover_data: Cover calculation data object
            options: Configuration options

        Returns:
            Calculated state position

        """
        # Access climate data if climate mode is enabled
        self.climate_strategy = None
        if self._climate_mode:
            self.climate_mode_data(options, cover_data)

        # When outside the configured start_time/end_time window,
        # report the position the cover was actually commanded to
        if not self.check_adaptive_time:
            sunset_pos = options.get(CONF_SUNSET_POS)
            default_height = options.get(CONF_DEFAULT_HEIGHT, 0)
            outside_window_pos = (
                sunset_pos if sunset_pos is not None else default_height
            )

            self.normal_cover_state = NormalCoverState(cover_data)
            self.default_state = outside_window_pos
            self.raw_calculated_position = outside_window_pos
            self.control_method = ControlMethod.DEFAULT
            self.logger.debug(
                "Outside time window - using position: %s", outside_window_pos
            )
            return self.state

        # Calculate the state of the cover
        self.normal_cover_state = NormalCoverState(cover_data)
        self.logger.debug(
            "Determined normal cover state to be %s", self.normal_cover_state
        )

        self.default_state = round(self.normal_cover_state.get_state())
        self.logger.debug("Determined default state to be %s", self.default_state)

        # Store raw calculated position for diagnostics (before min/max limits)
        # This is the pure geometric calculation
        if cover_data.direct_sun_valid:
            # Sun is in front - use raw calculated percentage
            self.raw_calculated_position = round(cover_data.calculate_percentage())
        else:
            # Sun not in front - use default position (no calculation)
            self.raw_calculated_position = cover_data.default
        self.logger.debug("Raw calculated position: %s", self.raw_calculated_position)

        # Determine control method based on priority (highest to lowest)
        if self.is_force_override_active:
            self.control_method = ControlMethod.FORCE
        elif self.is_motion_timeout_active:
            self.control_method = ControlMethod.MOTION
        elif self.manager.binary_cover_manual:
            self.control_method = ControlMethod.MANUAL
        elif (
            self._climate_mode
            and self.climate_data
            and self.climate_data.is_summer
            and self._switch_mode
        ):
            self.control_method = ControlMethod.SUMMER
        elif (
            self._climate_mode
            and self.climate_data
            and self.climate_data.is_winter
            and self._switch_mode
        ):
            self.control_method = ControlMethod.WINTER
        elif cover_data.direct_sun_valid:
            self.control_method = ControlMethod.SOLAR
        else:
            self.control_method = ControlMethod.DEFAULT
        self.logger.debug("Control method: %s", self.control_method)

        return self.state

    async def _update_solar_times_if_needed(
        self, normal_cover
    ) -> tuple[dt.datetime, dt.datetime]:
        """Update solar times if needed (first refresh or new day).

        Args:
            normal_cover: Cover object with solar_times method

        Returns:
            Tuple of (start_time, end_time)

        """
        if (
            self.first_refresh
            or self._sun_start_time is None
            or dt.datetime.now(pytz.UTC).date() != self._sun_start_time.date()
        ):
            self.logger.debug("Calculating solar times")
            loop = asyncio.get_event_loop()
            start, end = await loop.run_in_executor(None, normal_cover.solar_times)
            self._sun_start_time = start
            self._sun_end_time = end
            self.logger.debug("Sun start time: %s, Sun end time: %s", start, end)
            return start, end

        return self._sun_start_time, self._sun_end_time

    async def _async_update_data(self) -> AdaptiveCoverData:
        """Orchestrate calculation and control in main coordinator update cycle.

        Core update cycle that:
        1. Updates configuration options
        2. Calculates optimal cover positions based on sun/climate
        3. Handles manual override detection and reset
        4. Schedules end-time actions
        5. Processes different update triggers (state change, first refresh, timed)
        6. Updates solar times
        7. Builds diagnostic data if enabled

        Returns:
            AdaptiveCoverData with current state, solar times, and diagnostics

        """
        self.logger.debug("Updating data")
        if self.first_refresh:
            self._cached_options = self.config_entry.options

        options = self.config_entry.options
        self._update_options(options)

        # Get data for the blind and update manager
        cover_data = self.get_blind_data(options=options)
        self._update_manager_and_covers()

        # Calculate cover state
        state = self._calculate_cover_state(cover_data, options)
        await self.manager.reset_if_needed()

        # Schedule end time update if needed
        if (
            self._end_time
            and self._track_end_time
            and self._end_time > self._scheduled_time
        ):
            await self.async_timed_end_time()

        # Handle types of changes
        if self.state_change:
            await self.async_handle_state_change(state, options)
        if self.cover_state_change:
            await self.async_handle_cover_state_change(state)
        if self.first_refresh:
            await self.async_handle_first_refresh(state, options)
        if self.timed_refresh:
            await self.async_handle_timed_refresh(options)

        # Update solar times
        normal_cover = self.normal_cover_state.cover
        start, end = await self._update_solar_times_if_needed(normal_cover)

        # Build diagnostic data (always enabled)
        diagnostics = self.build_diagnostic_data()

        return AdaptiveCoverData(
            climate_mode_toggle=self.switch_mode,
            states={
                "state": state,
                "start": start,
                "end": end,
                "control": self.control_method.value,
                "sun_motion": normal_cover.direct_sun_valid,
                "manual_override": self.manager.binary_cover_manual,
                "manual_list": self.manager.manual_controlled,
            },
            attributes={
                "default": options.get(CONF_DEFAULT_HEIGHT),
                "sunset_default": options.get(CONF_SUNSET_POS),
                "sunset_offset": options.get(CONF_SUNSET_OFFSET),
                "azimuth_window": options.get(CONF_AZIMUTH),
                "field_of_view": [
                    options.get(CONF_FOV_LEFT),
                    options.get(CONF_FOV_RIGHT),
                ],
                "blind_spot": options.get(CONF_BLIND_SPOT_ELEVATION),
            },
            diagnostics=diagnostics,
        )

    async def async_handle_state_change(self, state: int, options):
        """Handle entity state change and send cover commands.

        Called when tracked entities (sun, temperature, weather, presence) change.
        Sends position commands to all managed covers if automatic control is enabled
        and conditions are met (within time window, position/time delta, not manual).

        Args:
            state: Calculated cover position to set
            options: Configuration options dictionary

        """
        if self.automatic_control:
            for cover in self.entities:
                await self.async_handle_call_service(cover, state, options)
        else:
            self.logger.debug("State change but control toggle is off")
        self.state_change = False
        self.logger.debug("State change handled")

    async def async_handle_cover_state_change(self, state: int):
        """Handle cover state change for manual override detection.

        Processes cover position changes to detect manual overrides by comparing
        actual position to expected position. Respects startup grace period
        (30 seconds after HA restart) and command grace periods to prevent false
        positives. Uses target_call for comparison when available to handle
        open/close-only covers correctly.

        Args:
            state: Current calculated position for comparison

        """
        if self.manual_toggle and self.automatic_control:
            # Check startup grace period FIRST to prevent false manual override
            # detection during HA restart when covers respond slowly
            if self._is_in_startup_grace_period():
                entity_id = self.state_change_data.entity_id
                self.logger.debug(
                    "Position change for %s ignored (in startup grace period)",
                    entity_id,
                )
                self.cover_state_change = False
                return

            # Get the entity_id from state_change_data
            entity_id = self.state_change_data.entity_id

            # Use target_call if available (contains actual sent position),
            # otherwise fall back to calculated state.
            # This is critical for open/close-only covers where the calculated
            # state gets transformed (via threshold) to 0 or 100 before sending.
            expected_position = self.target_call.get(entity_id, state)

            self.manager.handle_state_change(
                self.state_change_data,
                expected_position,
                self._cover_type,
                self.manual_reset,
                self.wait_for_target,
                self.manual_threshold,
            )
        self.cover_state_change = False
        self.logger.debug("Cover state change handled")

    async def async_handle_first_refresh(self, state: int, options):
        """Handle first refresh after coordinator startup.

        Called once after initial coordinator setup. Sets target positions for
        all covers and sends positioning commands if conditions are met (within
        time window, not manual, sufficient position delta). Always sets target
        positions even if commands aren't sent to enable position verification.

        Args:
            state: Calculated cover position to set
            options: Configuration options dictionary

        """
        if self.automatic_control:
            for cover in self.entities:
                # Always set target position for verification, even if we don't send command
                # This ensures position verification works after restart
                if self.check_adaptive_time and not self.manager.is_cover_manual(cover):
                    self.target_call[cover] = state
                    self.logger.debug(
                        "First refresh: Set target position %s for %s", state, cover
                    )

                    # Now check if we should actually send the command
                    if self.check_position_delta(cover, state, options):
                        await self.async_set_position(cover, state)
        else:
            self.logger.debug("First refresh but control toggle is off")
        self.first_refresh = False
        self.logger.debug("First refresh handled")

    async def async_handle_timed_refresh(self, options):
        """Handle timed refresh at end time.

        Called when the configured end time is reached. Moves all covers to
        the sunset position if automatic control is enabled and position delta
        is sufficient. Applies inverse_state transformation if configured.

        Args:
            options: Configuration options dictionary containing CONF_SUNSET_POS

        """
        sunset_pos_raw = options.get(CONF_SUNSET_POS)
        self.logger.debug(
            "This is a timed refresh, using sunset position: %s",
            sunset_pos_raw,
        )
        if sunset_pos_raw is None:
            self.logger.debug("Timed refresh: no sunset position configured, skipping")
            self.timed_refresh = False
            return
        if self.automatic_control:
            sunset_pos = (
                inverse_state(sunset_pos_raw) if self._inverse_state else sunset_pos_raw
            )
            for cover in self.entities:
                # Only move if delta is sufficient or it's a special position
                if self.check_position_delta(cover, sunset_pos, options):
                    await self.async_set_manual_position(cover, sunset_pos)
                else:
                    self.logger.debug(
                        "Timed refresh: delta too small for %s, skipping", cover
                    )
        else:
            self.logger.debug("Timed refresh but control toggle is off")
        self.timed_refresh = False
        self.logger.debug("Timed refresh handled")

    async def async_handle_call_service(self, entity, state: int, options):
        """Check conditions and call cover service.

        Validates all conditions before sending position command: within time
        window, sufficient position delta, sufficient time delta, and not under
        manual override. Only sends command if all checks pass.

        Args:
            entity: Cover entity ID to control
            state: Target position to set
            options: Configuration options dictionary

        """
        if not self.check_adaptive_time:
            self.logger.debug("Skipping %s: outside time window", entity)
            self._record_skipped_action(entity, "Outside time window", state)
            return
        if not self.check_position_delta(entity, state, options):
            self.logger.debug("Skipping %s: position delta too small", entity)
            self._record_skipped_action(entity, "Position delta too small", state)
            return
        if not self.check_time_delta(entity):
            self.logger.debug("Skipping %s: time delta too small", entity)
            self._record_skipped_action(entity, "Time delta too small", state)
            return
        if self.manager.is_cover_manual(entity):
            self.logger.debug("Skipping %s: manual override active", entity)
            self._record_skipped_action(entity, "Manual override active", state)
            return
        await self.async_set_position(entity, state)

    def _record_skipped_action(self, entity: str, reason: str, state: int) -> None:
        """Record a skipped cover action — delegates to CoverCommandService."""
        self._cmd_svc.record_skipped_action(entity, reason, state)

    async def async_set_position(self, entity, state: int):
        """Set cover position.

        Wrapper method that delegates to async_set_manual_position. Provided
        for backwards compatibility and clearer calling semantics.

        Args:
            entity: Cover entity ID to control
            state: Target position (0-100)

        """
        await self.async_set_manual_position(entity, state)

    def _prepare_position_service_call(
        self, entity: str, state: int, caps: dict[str, bool]
    ) -> tuple[str | None, dict | None, bool]:
        """Determine service and data based on capabilities — delegates to CoverCommandService."""
        return self._cmd_svc.prepare_position_service_call(
            entity, state, caps, inverse_state=self._inverse_state
        )

    def _track_cover_action(
        self, entity: str, service: str, state: int, supports_position: bool
    ) -> None:
        """Track cover action for diagnostic sensor — delegates to CoverCommandService."""
        self._cmd_svc.track_cover_action(
            entity, service, state, supports_position, inverse_state=self._inverse_state
        )

    async def async_set_manual_position(self, entity, state):
        """Call service to set cover position or open/close.

        Sends position command to cover entity, automatically handling both
        position-capable covers (set_cover_position) and open/close-only covers
        (open_cover/close_cover with threshold). Checks capabilities, prepares
        appropriate service call, tracks action for diagnostics, and starts
        grace period to prevent false manual override detection.

        Args:
            entity: Cover entity ID to control
            state: Target position (0-100)

        """
        if not self.check_position(entity, state):
            return

        caps = self._get_cover_capabilities(entity)
        service, service_data, supports_position = self._prepare_position_service_call(
            entity, state, caps
        )

        if service is None:
            return

        self._track_cover_action(entity, service, state, supports_position)

        self.logger.debug("Run %s with data %s", service, service_data)
        await self._cmd_svc.execute_service_call(service, service_data)
        self.logger.debug("Successfully called service %s for %s", service, entity)

    def _update_options(self, options):
        """Update coordinator options from config entry.

        Extracts and caches configuration options from the config entry options
        dictionary. Called on every coordinator update to ensure latest settings
        are used.

        Args:
            options: Configuration options dictionary from config_entry.options

        """
        self.entities = options.get(CONF_ENTITIES, [])
        self.min_change = options.get(CONF_DELTA_POSITION, 1)
        self.time_threshold = options.get(CONF_DELTA_TIME, 2)
        self.start_time = options.get(CONF_START_TIME)
        self.start_time_entity = options.get(CONF_START_ENTITY)
        self.end_time = options.get(CONF_END_TIME)
        self.end_time_entity = options.get(CONF_END_ENTITY)
        self.manual_reset = options.get(CONF_MANUAL_OVERRIDE_RESET, False)
        self.manual_duration = options.get(CONF_MANUAL_OVERRIDE_DURATION, {"hours": 2})
        self.manual_threshold = options.get(CONF_MANUAL_THRESHOLD)
        self.start_value = options.get(CONF_INTERP_START)
        self.end_value = options.get(CONF_INTERP_END)
        self.normal_list = options.get(CONF_INTERP_LIST)
        self.new_list = options.get(CONF_INTERP_LIST_NEW)
        self._cmd_svc.update_threshold(options.get(CONF_OPEN_CLOSE_THRESHOLD, 50))
        self._motion_mgr.update_config(
            sensors=options.get(CONF_MOTION_SENSORS, []),
            timeout_seconds=options.get(CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT),
        )

    def _update_manager_and_covers(self):
        """Update manager with cover entities.

        Registers cover entities with the AdaptiveCoverManager and resets
        manual override state for all covers if manual override detection
        is disabled.

        """
        self.manager.add_covers(self.entities)
        if not self._manual_toggle:
            for entity in self.manager.manual_controlled:
                self.manager.reset(entity)

    def get_blind_data(self, options):
        """Create appropriate cover class based on cover type.

        Instantiates the correct calculation class (AdaptiveVerticalCover,
        AdaptiveHorizontalCover, or AdaptiveTiltCover) based on the configured
        cover type, passing current sun position and configuration data.

        Args:
            options: Configuration options dictionary

        Returns:
            Cover calculation object (AdaptiveVerticalCover, AdaptiveHorizontalCover,
            or AdaptiveTiltCover)

        """
        if self.is_blind_cover:
            cover_data = AdaptiveVerticalCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self._config_service.get_common_data(options),
                *self._config_service.get_vertical_data(options),
            )
        if self.is_awning_cover:
            cover_data = AdaptiveHorizontalCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self._config_service.get_common_data(options),
                *self._config_service.get_vertical_data(options),
                *self._config_service.get_horizontal_data(options),
            )
        if self.is_tilt_cover:
            cover_data = AdaptiveTiltCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self._config_service.get_common_data(options),
                *self._config_service.get_tilt_data(options),
            )
        return cover_data

    @property
    def check_adaptive_time(self):
        """Check if current time is within operational window.

        Returns:
            True if current time is after start time and before end time,
            False otherwise. Returns True if no time restrictions configured.

        """
        if self._start_time and self._end_time and self._start_time > self._end_time:
            self.logger.error("Start time is after end time")
        return self.before_end_time and self.after_start_time

    @property
    def after_start_time(self):
        """Check if current time is after start time.

        Returns:
            True if current time is after configured start time (from entity
            or static config), False otherwise. Returns True if no start time
            configured.

        """
        now = dt.datetime.now()
        if self.start_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self.hass, self.start_time_entity)
            )
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s ", time, now, now >= time
            )
            self._start_time = time
            return now >= time
        if self.start_time is not None:
            time = get_datetime_from_str(self.start_time)

            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s", time, now, now >= time
            )
            self._start_time
            return now >= time
        return True

    @property
    def _end_time(self) -> dt.datetime | None:
        """Get end time from entity or config.

        Returns:
            End time datetime object from end_time_entity state or end_time
            config value. Handles midnight (00:00) by adding one day. Returns
            None if no end time configured.

        """
        time = None
        if self.end_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self.hass, self.end_time_entity)
            )
        elif self.end_time is not None:
            time = get_datetime_from_str(self.end_time)
            if time.time() == dt.time(0, 0):
                time = time + dt.timedelta(days=1)
        return time

    @property
    def before_end_time(self):
        """Check if current time is before end time.

        Returns:
            True if current time is before configured end time (from entity
            or static config), False otherwise. Returns True if no end time
            configured.

        """
        if self._end_time is not None:
            now = dt.datetime.now()
            self.logger.debug(
                "End time: %s, now: %s, now < time: %s",
                self._end_time,
                now,
                now < self._end_time,
            )
            return now < self._end_time
        return True

    def _get_current_position(self, entity) -> int | None:
        """Get current position of cover — delegates to CoverCommandService."""
        return self._cmd_svc._get_current_position(entity)

    def check_position(self, entity, state):
        """Check if position differs from state.

        Bypasses check if sun just came into field of view to ensure
        covers reposition even if calculated position equals current position
        (can happen when min/max limits clamp low-angle calculations).

        Args:
            entity: Cover entity ID to check
            state: Target position to compare against

        Returns:
            True if position differs from state or sun just appeared,
            False if position matches state

        """
        sun_just_appeared = self._check_sun_validity_transition()
        return self._cmd_svc.check_position(entity, state, sun_just_appeared=sun_just_appeared)

    def check_position_delta(self, entity, state: int, options):
        """Check if position delta exceeds threshold — delegates to CoverCommandService.

        Args:
            entity: Cover entity ID to check
            state: Target position to move to
            options: Configuration options containing special positions

        Returns:
            True if delta exceeds min_change threshold or moving to/from special
            position, False otherwise

        """
        special_positions = build_special_positions(options)
        return self._cmd_svc.check_position_delta(
            entity, state, self.min_change, special_positions
        )

    def check_time_delta(self, entity):
        """Check if time delta exceeds threshold — delegates to CoverCommandService.

        Args:
            entity: Cover entity ID to check

        Returns:
            True if time since last update exceeds time_threshold (minutes),
            False otherwise. Returns True if entity has no last_updated time.

        """
        return self._cmd_svc.check_time_delta(entity, self.time_threshold)

    @property
    def pos_sun(self):
        """Get current sun azimuth and elevation.

        Returns:
            List containing [azimuth, elevation] in degrees from sun.sun entity

        """
        return [
            state_attr(self.hass, "sun.sun", "azimuth"),
            state_attr(self.hass, "sun.sun", "elevation"),
        ]

    def climate_mode_data(self, options, cover_data):
        """Update climate mode data and control method.

        Calculates climate-aware cover state and determines control method
        (summer/winter/intermediate) based on temperature, presence, and weather
        conditions. Stores climate data for diagnostic sensors.

        Args:
            options: Configuration options dictionary
            cover_data: Cover calculation data object

        """
        readings = self._climate_provider.read(
            temp_entity=options.get(CONF_TEMP_ENTITY),
            outside_entity=options.get(CONF_OUTSIDETEMP_ENTITY),
            weather_entity=options.get(CONF_WEATHER_ENTITY),
            weather_condition=options.get(CONF_WEATHER_STATE),
            presence_entity=options.get(CONF_PRESENCE_ENTITY),
            use_lux=bool(self._lux_toggle),
            lux_entity=options.get(CONF_LUX_ENTITY),
            lux_threshold=options.get(CONF_LUX_THRESHOLD),
            use_irradiance=bool(self._irradiance_toggle),
            irradiance_entity=options.get(CONF_IRRADIANCE_ENTITY),
            irradiance_threshold=options.get(CONF_IRRADIANCE_THRESHOLD),
        )
        climate = ClimateCoverData(
            logger=self.logger,
            temp_low=options.get(CONF_TEMP_LOW),
            temp_high=options.get(CONF_TEMP_HIGH),
            temp_switch=bool(self._temp_toggle),
            blind_type=self._cover_type,
            transparent_blind=options.get(CONF_TRANSPARENT_BLIND, False),
            temp_summer_outside=options.get(CONF_OUTSIDE_THRESHOLD),
            outside_temperature=readings.outside_temperature,
            inside_temperature=readings.inside_temperature,
            is_presence=readings.is_presence,
            is_sunny=readings.is_sunny,
            lux_below_threshold=readings.lux_below_threshold,
            irradiance_below_threshold=readings.irradiance_below_threshold,
        )
        climate_cover_state = ClimateCoverState(cover_data, climate)
        self.climate_state = round(climate_cover_state.get_state())
        self.climate_data = climate_cover_state.climate_data  # Store for P1 diagnostics
        self.climate_strategy = (
            climate_cover_state.climate_strategy
        )  # Store for diagnostics
        self.logger.debug(
            "Climate mode: strategy=%s, state=%s%%",
            self.climate_strategy.value if self.climate_strategy else "none",
            self.climate_state,
        )

    def _build_solar_diagnostics(self) -> dict:
        """Build solar position diagnostics."""
        diagnostics = {}
        sun_azimuth, sun_elevation = self.pos_sun
        diagnostics["sun_azimuth"] = sun_azimuth
        diagnostics["sun_elevation"] = sun_elevation

        # Gamma (surface solar azimuth)
        if self.normal_cover_state and hasattr(self.normal_cover_state.cover, "gamma"):
            diagnostics["gamma"] = self.normal_cover_state.cover.gamma

        return diagnostics

    def _get_control_state_reason(self) -> str:
        """Get the current control state reason including coordinator-level overrides.

        Combines cover-level sun position reasons with coordinator-level override
        states (force override, motion timeout, manual override). Coordinator-level
        states take priority over cover-level sun position reasons.

        Returns:
            Human-readable reason string for current cover control state.

        """
        if self.is_force_override_active:
            return "Force Override"
        if self.is_motion_timeout_active:
            return "Motion Timeout"
        if self.manager.binary_cover_manual:
            return "Manual Override"
        if self.normal_cover_state and self.normal_cover_state.cover:
            return self.normal_cover_state.cover.control_state_reason
        return "Unknown"

    _CLIMATE_STRATEGY_LABELS: dict[ClimateStrategy, str] = {
        ClimateStrategy.WINTER_HEATING: "Winter Heating",
        ClimateStrategy.SUMMER_COOLING: "Summer Cooling",
        ClimateStrategy.LOW_LIGHT: "Low Light",
        ClimateStrategy.GLARE_CONTROL: "Glare Control",
    }

    def _build_position_explanation(self) -> str:
        """Build a human-readable explanation of the full position decision chain.

        Returns:
            Single-line string describing each step from raw calculation to final
            position (e.g. "Sun tracking (45%) → Climate: Winter Heating → 100%").

        """
        options = self.config_entry.options

        # Priority overrides
        if self.is_force_override_active:
            pos = options.get(CONF_FORCE_OVERRIDE_POSITION, 0)
            return f"Force override active → {pos}%"

        if self.is_motion_timeout_active:
            return f"No motion detected → default {self.default_state}%"

        if self.manager.binary_cover_manual:
            return "Manual override active"

        # Outside time window
        if not self.check_adaptive_time:
            sunset_pos = options.get(CONF_SUNSET_POS)
            default_height = options.get(CONF_DEFAULT_HEIGHT, 0)
            if sunset_pos is not None:
                return f"Outside time window → Sunset Position ({sunset_pos}%)"
            return f"Outside time window → Default Position ({default_height}%)"

        # Build the decision chain
        parts = []

        # Step 1: Sun position condition and raw calculated value
        if self.normal_cover_state and self.normal_cover_state.cover:
            cover = self.normal_cover_state.cover
            if cover.direct_sun_valid:
                parts.append(f"Sun tracking ({self.raw_calculated_position}%)")
            elif cover.sunset_valid and cover.sunset_pos is not None:
                parts.append(f"Sunset Position ({round(cover.sunset_pos)}%)")
            else:
                reason = cover.control_state_reason
                parts.append(f"{reason} → Default Position ({round(cover.default)}%)")

        # Step 2: Position limits on the non-climate path
        if not self._switch_mode:
            min_pos = options.get(CONF_MIN_POSITION)
            max_pos = options.get(CONF_MAX_POSITION)
            enable_min = options.get(CONF_ENABLE_MIN_POSITION, False)
            enable_max = options.get(CONF_ENABLE_MAX_POSITION, False)
            if min_pos is not None and enable_min and self.default_state == min_pos:
                parts.append(f"min limit ({min_pos}%) → {self.default_state}%")
            elif max_pos is not None and enable_max and self.default_state == max_pos:
                parts.append(f"max limit ({max_pos}%) → {self.default_state}%")

        # Step 3: Climate mode override
        if self._switch_mode and self.climate_state is not None:
            strategy_label = (
                self._CLIMATE_STRATEGY_LABELS.get(self.climate_strategy, "Active")
                if self.climate_strategy
                else "Active"
            )
            parts.append(f"Climate: {strategy_label} → {self.climate_state}%")

        # Step 4: Interpolation / inverse state
        final = self.state
        if self._use_interpolation:
            parts.append(f"interpolated → {final}%")
        elif self._inverse_state:
            parts.append(f"inversed → {final}%")

        return " → ".join(parts) if parts else "Unknown"

    def _build_position_diagnostics(self) -> dict:
        """Build position diagnostics.

        Returns:
            Dictionary containing calculated position (before limits), climate
            position (if enabled), control status, and control state reason

        """
        diagnostics = {}

        # Use raw calculated position (before min/max limits) for diagnostic
        diagnostics["calculated_position"] = self.raw_calculated_position

        if self.climate_state is not None:
            diagnostics["calculated_position_climate"] = self.climate_state

        # Control status determination
        control_status = self._determine_control_status()
        diagnostics["control_status"] = control_status

        # Human-readable reason for current state (including coordinator-level overrides)
        diagnostics["control_state_reason"] = self._get_control_state_reason()

        # Full decision chain explanation
        explanation = self._build_position_explanation()
        diagnostics["position_explanation"] = explanation
        if explanation != self._last_position_explanation:
            self.logger.debug("Position explanation changed: %s", explanation)
            self._last_position_explanation = explanation

        # Delta thresholds (for debugging position_delta_too_small / time_delta_too_small)
        diagnostics["delta_position_threshold"] = self.min_change
        diagnostics["delta_time_threshold_minutes"] = self.time_threshold

        # Position delta from last action
        last_action = self.last_cover_action
        if last_action.get("position") is not None:
            diagnostics["position_delta_from_last_action"] = abs(
                self.raw_calculated_position - last_action["position"]
            )

        # Time since last action
        if last_action.get("timestamp"):
            try:
                last_ts = dt.datetime.fromisoformat(last_action["timestamp"])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=dt.UTC)
                now_utc = dt.datetime.now(dt.UTC)
                elapsed = (now_utc - last_ts).total_seconds()
                diagnostics["seconds_since_last_action"] = round(elapsed)
            except (ValueError, AttributeError):
                pass

        # Vertical cover calculation details (edge case, safety margin, distances)
        if self.normal_cover_state and self.normal_cover_state.cover:
            cover = self.normal_cover_state.cover
            calc_details = getattr(cover, "_last_calc_details", None)
            if calc_details is not None:
                diagnostics["calculation_details"] = calc_details

        # Coordinator last update time
        diagnostics["last_updated"] = dt.datetime.now(dt.UTC).isoformat()

        return diagnostics

    def _build_time_window_diagnostics(self) -> dict:
        """Build time window diagnostics.

        Returns:
            Dictionary containing time window state checks and configured times

        """
        return {
            "time_window": {
                "check_adaptive_time": self.check_adaptive_time,
                "after_start_time": self.after_start_time,
                "before_end_time": self.before_end_time,
                "start_time": self._start_time,
                "end_time": self._end_time,
            }
        }

    def _build_sun_validity_diagnostics(self) -> dict:
        """Build sun validity diagnostics.

        Returns:
            Dictionary containing sun validity state (in field of view, elevation
            valid, in blind spot)

        """
        diagnostics = {}
        if self.normal_cover_state and self.normal_cover_state.cover:
            cover = self.normal_cover_state.cover
            diagnostics["sun_validity"] = {
                "valid": cover.valid,
                "valid_elevation": cover.valid_elevation,
                "in_blind_spot": getattr(cover, "in_blind_spot", None),
            }
        return diagnostics

    def _build_climate_diagnostics(self) -> dict:
        """Build climate mode diagnostics.

        Returns:
            Dictionary containing climate control method, active temperature,
            temperature details, and climate conditions (summer/winter/presence/
            sunny/lux/irradiance)

        """
        diagnostics = {}
        if self._climate_mode and self.climate_data is not None:
            diagnostics["climate_control_method"] = self.control_method

            # Active temperature and temperature details
            diagnostics["active_temperature"] = (
                self.climate_data.get_current_temperature
            )
            diagnostics["temperature_details"] = {
                "inside_temperature": self.climate_data.inside_temperature,
                "outside_temperature": self.climate_data.outside_temperature,
                "temp_switch": self.climate_data.temp_switch,
            }

            # Which strategy branch was taken
            if self.climate_strategy is not None:
                diagnostics["climate_strategy"] = self.climate_strategy.value

            # Climate conditions
            diagnostics["climate_conditions"] = {
                "is_summer": self.climate_data.is_summer,
                "is_winter": self.climate_data.is_winter,
                "is_presence": self.climate_data.is_presence,
                "is_sunny": self.climate_data.is_sunny,
                "lux_active": self.climate_data.lux
                if self.climate_data._use_lux
                else None,
                "irradiance_active": self.climate_data.irradiance
                if self.climate_data._use_irradiance
                else None,
            }

        return diagnostics

    def _build_last_action_diagnostics(self) -> dict:
        """Build last action diagnostics.

        Returns:
            Dictionary containing last cover action details (entity, service,
            position, timestamp, etc.) if any action has been taken

        """
        diagnostics = {}
        if self.last_cover_action.get("entity_id"):
            diagnostics["last_cover_action"] = self.last_cover_action.copy()
        if self.last_skipped_action.get("entity_id"):
            diagnostics["last_skipped_action"] = self.last_skipped_action.copy()
        return diagnostics

    def _build_configuration_diagnostics(self) -> dict:
        """Build configuration diagnostics.

        Returns:
            Dictionary containing current configuration settings (azimuth, FOV,
            elevation limits, blind spot, position limits, inverse state,
            interpolation)

        """
        options = self.config_entry.options
        return {
            "configuration": {
                "azimuth": options.get(CONF_AZIMUTH),
                "fov_left": options.get(CONF_FOV_LEFT),
                "fov_right": options.get(CONF_FOV_RIGHT),
                "min_elevation": options.get(CONF_MIN_ELEVATION),
                "max_elevation": options.get(CONF_MAX_ELEVATION),
                "enable_blind_spot": options.get(CONF_ENABLE_BLIND_SPOT, False),
                "blind_spot_elevation": options.get(CONF_BLIND_SPOT_ELEVATION),
                "blind_spot_left": options.get(CONF_BLIND_SPOT_LEFT),
                "blind_spot_right": options.get(CONF_BLIND_SPOT_RIGHT),
                "min_position": options.get(CONF_MIN_POSITION),
                "max_position": options.get(CONF_MAX_POSITION),
                "enable_min_position": options.get(CONF_ENABLE_MIN_POSITION, False),
                "enable_max_position": options.get(CONF_ENABLE_MAX_POSITION, False),
                "inverse_state": options.get(CONF_INVERSE_STATE, False),
                "interpolation": options.get(CONF_INTERP, False),
                "force_override_sensors": options.get(CONF_FORCE_OVERRIDE_SENSORS, []),
                "force_override_position": options.get(CONF_FORCE_OVERRIDE_POSITION, 0),
                "force_override_active": self.is_force_override_active,
                "motion_sensors": options.get(CONF_MOTION_SENSORS, []),
                "motion_timeout": options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                "motion_detected": self.is_motion_detected,
                "motion_timeout_active": self._motion_mgr._motion_timeout_active,
            }
        }

    def build_diagnostic_data(self) -> dict:
        """Build complete diagnostic data.

        Combines all diagnostic categories (solar, position, time window,
        sun validity, climate, last action, configuration) into single
        dictionary for diagnostic sensors.

        Returns:
            Dictionary containing all diagnostic data

        """
        return {
            **self._build_solar_diagnostics(),
            **self._build_position_diagnostics(),
            **self._build_time_window_diagnostics(),
            **self._build_sun_validity_diagnostics(),
            **self._build_climate_diagnostics(),
            **self._build_last_action_diagnostics(),
            **self._build_configuration_diagnostics(),
        }

    def _determine_control_status(self) -> str:
        """Determine current control status.

        Returns:
            Control status string: AUTOMATIC_CONTROL_OFF, FORCE_OVERRIDE_ACTIVE,
            MANUAL_OVERRIDE, OUTSIDE_TIME_WINDOW, SUN_NOT_VISIBLE, or ACTIVE

        """
        if not self.automatic_control:
            return ControlStatus.AUTOMATIC_CONTROL_OFF

        # Check force override sensors (safety override takes precedence)
        if self.is_force_override_active:
            return ControlStatus.FORCE_OVERRIDE_ACTIVE

        # Check motion timeout (second priority)
        if self.is_motion_timeout_active:
            return ControlStatus.MOTION_TIMEOUT

        if self.manager.binary_cover_manual:
            return ControlStatus.MANUAL_OVERRIDE

        if not self.check_adaptive_time:
            return ControlStatus.OUTSIDE_TIME_WINDOW

        if self.normal_cover_state and not self.normal_cover_state.cover.valid:
            return ControlStatus.SUN_NOT_VISIBLE

        # For position/time delta, we'd need to check per-cover, so we default to active
        # if all other checks pass
        return ControlStatus.ACTIVE

    @property
    def state(self) -> int:
        """Get final state with climate mode, interpolation, and force override.

        Determines final cover position by:
        1. Checking force override (highest priority)
        2. Selecting between default and climate state based on switch_mode
        3. Applying interpolation and inverse_state transformations if configured

        Returns:
            Final calculated cover position (0-100)

        """
        # Check force override first (highest priority)
        if self.is_force_override_active:
            override_position = self.config_entry.options.get(
                CONF_FORCE_OVERRIDE_POSITION, 0
            )
            self.logger.debug(
                "Force override active - using position: %s",
                override_position,
            )
            return override_position

        # Check motion timeout (second priority)
        if self.is_motion_timeout_active:
            self.logger.debug(
                "Motion timeout active - using default position: %s",
                self.default_state,
            )
            return self.default_state

        # Normal position calculation
        self.logger.debug(
            "Basic position: %s; Climate position: %s; Using climate position? %s",
            self.default_state,
            self.climate_state,
            self._switch_mode,
        )
        if self._switch_mode:
            state = self.climate_state
        else:
            state = self.default_state

        if self._use_interpolation:
            self.logger.debug("Interpolating position: %s", state)
            state = self.interpolate_states(state)

        if self._inverse_state and self._use_interpolation:
            self.logger.info(
                "Inverse state is not supported with interpolation, you can inverse the state by arranging the list from high to low"
            )

        if self._inverse_state and not self._use_interpolation:
            state = inverse_state(state)
            self.logger.debug("Inversed position: %s", state)

        self.logger.debug("Final position to use: %s", state)
        return state

    def interpolate_states(self, state):
        """Interpolate state using custom ranges.

        Maps position from normal range to custom range using linear interpolation.
        Supports both simple start/end values or complex multi-point lists.

        Args:
            state: Position in normal range

        Returns:
            Interpolated position in custom range, or original state if no
            interpolation configured

        """
        normal_range = [0, 100]
        new_range = []
        if self.start_value is not None and self.end_value is not None:
            new_range = [self.start_value, self.end_value]
        if self.normal_list and self.new_list:
            normal_range = list(map(int, self.normal_list))
            new_range = list(map(int, self.new_list))
        if new_range:
            state = np.interp(state, normal_range, new_range)
        return state

    @property
    def switch_mode(self):
        """Climate mode toggle property.

        Returns:
            True if climate mode is active, False for basic mode

        """
        return self._switch_mode

    @switch_mode.setter
    def switch_mode(self, value):
        """Set climate mode toggle.

        Args:
            value: True to enable climate mode, False for basic mode

        """
        self._switch_mode = value

    @property
    def temp_toggle(self):
        """Temperature entity toggle property.

        Returns:
            True if using outside temperature, False if using inside temperature,
            None if not configured

        """
        return self._temp_toggle

    @temp_toggle.setter
    def temp_toggle(self, value):
        """Set temperature entity toggle.

        Args:
            value: True for outside temperature, False for inside temperature

        """
        self._temp_toggle = value

    @property
    def automatic_control(self):
        """Automatic control toggle property.

        Returns:
            True if automatic control is enabled, False if disabled, None if
            not configured

        """
        return self._automatic_control

    @automatic_control.setter
    def automatic_control(self, value):
        """Set automatic control toggle.

        Args:
            value: True to enable automatic control, False to disable

        """
        self._automatic_control = value

    @property
    def manual_toggle(self):
        """Manual override detection toggle property.

        Returns:
            True if manual override detection is enabled, False if disabled,
            None if not configured

        """
        return self._manual_toggle

    @manual_toggle.setter
    def manual_toggle(self, value):
        """Set manual override detection toggle.

        Args:
            value: True to enable manual override detection, False to disable

        """
        self._manual_toggle = value

    @property
    def lux_toggle(self):
        """Lux entity toggle property.

        Returns:
            True if using lux sensor, False if not using, None if not configured

        """
        return self._lux_toggle

    @lux_toggle.setter
    def lux_toggle(self, value):
        """Set lux entity toggle.

        Args:
            value: True to use lux sensor, False to not use

        """
        self._lux_toggle = value

    @property
    def irradiance_toggle(self):
        """Irradiance entity toggle property.

        Returns:
            True if using irradiance sensor, False if not using, None if not
            configured

        """
        return self._irradiance_toggle

    @irradiance_toggle.setter
    def irradiance_toggle(self, value):
        """Set irradiance entity toggle.

        Args:
            value: True to use irradiance sensor, False to not use

        """
        self._irradiance_toggle = value

    @property
    def return_to_default_toggle(self):
        """Return to default toggle property.

        Returns:
            True if covers should return to default when automatic control is
            disabled, False otherwise, None if not configured

        """
        return self._return_to_default_toggle

    @return_to_default_toggle.setter
    def return_to_default_toggle(self, value):
        """Set return to default toggle.

        Args:
            value: True to return to default on control disable, False otherwise

        """
        self._return_to_default_toggle = value

    async def _check_time_window_transition(self, now: dt.datetime) -> None:
        """Check if time window state has changed and trigger refresh if needed.

        This method detects when the operational time window changes state
        (e.g., when end time is reached) and triggers appropriate actions.
        Provides <1 minute response time for time window changes.
        """
        # Initialize tracking on first call
        if self._last_time_window_state is None:
            self._last_time_window_state = self.check_adaptive_time
            return

        current_state = self.check_adaptive_time

        # If state changed, trigger appropriate action
        if current_state != self._last_time_window_state:
            self.logger.info(
                "Time window state changed: %s → %s",
                "active" if self._last_time_window_state else "inactive",
                "active" if current_state else "inactive",
            )
            self._last_time_window_state = current_state

            # If we just left the time window, return covers to default position
            if not current_state and self._track_end_time:
                self.logger.info(
                    "End time reached, returning covers to default position"
                )
                self.timed_refresh = True
                await self.async_refresh()

    def _check_sun_validity_transition(self) -> bool:
        """Check if sun validity state has changed from False to True.

        Returns True if sun just came into field of view, indicating
        covers should immediately reposition regardless of delta checks.
        """
        # Need cover data to check sun validity
        if not hasattr(self, "normal_cover_state") or self.normal_cover_state is None:
            return False

        current_sun_valid = self.normal_cover_state.cover.direct_sun_valid

        # Initialize tracking on first call
        if self._last_sun_validity_state is None:
            self._last_sun_validity_state = current_sun_valid
            return False

        # Detect transitions
        sun_just_appeared = (not self._last_sun_validity_state) and current_sun_valid
        sun_just_left = self._last_sun_validity_state and (not current_sun_valid)

        # Update tracking
        self._last_sun_validity_state = current_sun_valid

        if sun_just_appeared:
            self.logger.info(
                "Sun visibility transition detected: OFF → ON (sun came into field of view)"
            )
        elif sun_just_left:
            self.logger.debug(
                "Sun visibility transition detected: ON → OFF (sun left field of view)"
            )

        return sun_just_appeared

    async def async_periodic_position_check(self, now: dt.datetime) -> None:
        """Periodically verify cover positions match calculated positions.

        Called at regular intervals (POSITION_CHECK_INTERVAL_MINUTES) to verify
        covers reached their target positions. Also checks for time window state
        transitions to provide <1 minute response time for end time changes.
        Skips verification for covers under manual override or waiting for target.

        Args:
            now: Current datetime

        """
        # Check if time window state changed (e.g., passed end time)
        # This provides <1 minute response time for time window changes
        await self._check_time_window_transition(now)

        # Skip if not within operational time window
        if not self.check_adaptive_time:
            return

        # Skip if automatic control is disabled
        if not self.automatic_control:
            return

        for entity_id in self.entities:
            await self._verify_entity_position(entity_id, now)

    async def _verify_entity_position(self, entity_id: str, now: dt.datetime) -> None:
        """Verify single entity's position and retry if needed.

        Checks if cover reached its target position within tolerance. If position
        mismatch detected and delta check passes, retries up to MAX_POSITION_RETRIES
        times. Compares against target_call (what was sent) not current calculation
        to prevent false mismatches when sun moves between command and verification.

        Args:
            entity_id: Cover entity ID to verify
            now: Current datetime

        """
        # Update last verification time FIRST for diagnostic tracking
        self._pos_verify_mgr.record_verification(entity_id, now)

        # Skip if manual override active
        if self.manager.is_cover_manual(entity_id):
            self._pos_verify_mgr.reset_retry_count(entity_id)
            return

        # Skip if currently waiting for target (move in progress)
        if self.wait_for_target.get(entity_id, False):
            return

        # Get target position (the position we last sent to this cover)
        target_position = self.target_call.get(entity_id)
        if target_position is None:
            # Only log once when first encountered to avoid log spam
            if self._pos_verify_mgr.mark_never_commanded(entity_id):
                self.logger.debug(
                    "No command sent to %s yet, position verification will begin after first command",
                    entity_id,
                )
            return

        # Get actual position
        actual_position = self._get_current_position(entity_id)

        if actual_position is None:
            self.logger.debug(
                "Cannot verify position for %s: position unavailable", entity_id
            )
            return

        # Check if positions match within tolerance
        # Compare to target_call (what we sent), not self.state (current calculation)
        # This prevents false mismatches when sun moves between command and verification
        position_delta = abs(target_position - actual_position)

        if self._pos_verify_mgr.is_position_matched(actual_position, target_position):
            # Position is correct, reset retry count
            self._pos_verify_mgr.reset_retry_count(entity_id)
            return

        # Check if delta is sufficient before retrying
        options = self.config_entry.options
        if not self.check_position_delta(entity_id, target_position, options):
            self.logger.debug(
                "Position verification: delta too small for %s (current: %s, target: %s, min: %s%%)",
                entity_id,
                actual_position,
                target_position,
                self.min_change,
            )
            self._pos_verify_mgr.reset_retry_count(entity_id)
            return

        # Position mismatch detected - cover failed to reach target we sent
        retry_count = self._pos_verify_mgr.get_retry_count(entity_id)

        if not self._pos_verify_mgr.should_retry(entity_id):
            self.logger.warning(
                "Max retries exceeded for %s. Position mismatch: target=%s, actual=%s, delta=%s",
                entity_id,
                target_position,
                actual_position,
                position_delta,
            )
            return

        self.logger.info(
            "Position mismatch detected for %s (attempt %d/%d): target=%s, actual=%s, delta=%s. Repositioning...",
            entity_id,
            retry_count + 1,
            self._pos_verify_mgr.max_retries,
            target_position,
            actual_position,
            position_delta,
        )

        # Resend the same target position
        # Note: If sun has moved and changed the calculated position, the normal
        # update cycle will handle that separately. We only retry the last command.
        await self.async_set_position(entity_id, target_position)

    def _start_position_verification(self) -> None:
        """Start periodic position verification.

        Registers time interval listener to call async_periodic_position_check
        at configured intervals. Called once during first refresh. Skips if
        already started.

        """
        if self._pos_verify_mgr._position_check_interval is not None:
            return  # Already started

        interval = dt.timedelta(minutes=self._pos_verify_mgr.check_interval_minutes)
        self._pos_verify_mgr._position_check_interval = async_track_time_interval(
            self.hass,
            self.async_periodic_position_check,
            interval,
        )
        self.logger.debug(
            "Started periodic position verification (interval: %s)", interval
        )

    def _stop_position_verification(self) -> None:
        """Stop periodic position verification.

        Cancels time interval listener. Called during coordinator shutdown.

        """
        if self._pos_verify_mgr._position_check_interval:
            self._pos_verify_mgr._position_check_interval()
            self._pos_verify_mgr._position_check_interval = None
            self.logger.debug("Stopped periodic position verification")

    async def async_shutdown(self) -> None:
        """Clean up resources on shutdown.

        Cancels all grace period tasks and stops position verification to ensure
        clean shutdown without lingering tasks or listeners. Called when integration
        is unloaded.

        """
        # Cancel all grace period tasks
        self._grace_mgr.cancel_all()

        # Cancel motion timeout task
        self._cancel_motion_timeout()

        # Stop position verification
        self._stop_position_verification()

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager and inverse_state have been moved to managers/manual_override.py
# They are re-imported above to maintain backward compatibility.
