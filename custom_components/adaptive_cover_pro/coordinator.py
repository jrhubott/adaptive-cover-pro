"""The Coordinator for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, replace

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

from .calculation import NormalCoverState
from .engine.covers import (
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
)
from .config_context_adapter import ConfigContextAdapter
from .services.configuration_service import ConfigurationService
from .const import (
    _LOGGER,
    COMMAND_GRACE_PERIOD_SECONDS,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_TIMEOUT,
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
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_RETURN_SUNSET,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_LOW,
    CONF_TEMP_HIGH,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    CONF_OUTSIDE_THRESHOLD,
    CONF_TRANSPARENT_BLIND,
    CONF_WINTER_CLOSE_INSULATION,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
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
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
)
from .diagnostics.builder import DiagnosticContext, DiagnosticsBuilder
from .enums import ControlMethod
from .managers.cover_command import CoverCommandService, build_special_positions
from .managers.grace_period import GracePeriodManager
from .managers.manual_override import AdaptiveCoverManager, inverse_state
from .managers.motion import MotionManager
from .managers.weather import WeatherManager
from .managers.position_verification import PositionVerificationManager
from .managers.time_window import TimeWindowManager
from .managers.toggles import ToggleManager
from .position_utils import interpolate_position
from .pipeline.handlers import (
    ClimateHandler,
    CloudSuppressionHandler,
    DefaultHandler,
    ForceOverrideHandler,
    GlareZoneHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
    WeatherOverrideHandler,
)
from .pipeline.registry import PipelineRegistry
from .pipeline.types import ClimateOptions, PipelineSnapshot
from .state.climate_provider import ClimateProvider, ClimateReadings
from .state.cover_provider import CoverProvider
from .state.snapshot import CoverStateSnapshot, SunSnapshot
from .state.sun_provider import SunProvider


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
        """Initialize coordinator."""
        super().__init__(hass, LOGGER, name=DOMAIN)

        self.logger = ConfigContextAdapter(_LOGGER)
        self.logger.set_config_name(self.config_entry.data.get("name"))
        self._cover_type = self.config_entry.data.get("sensor_type")
        self._climate_mode = self.config_entry.options.get(CONF_CLIMATE_MODE, False)
        self._inverse_state = self.config_entry.options.get(CONF_INVERSE_STATE, False)
        self._use_interpolation = self.config_entry.options.get(CONF_INTERP, False)
        self._track_end_time = self.config_entry.options.get(CONF_RETURN_SUNSET)
        # Toggle state manager (switch entities delegate here)
        self._toggles = ToggleManager()
        self._toggles.switch_mode = bool(self._climate_mode)
        self._sun_end_time = None
        self._sun_start_time = None
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
        self._weather_readings: ClimateReadings | None = None
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
        # Weather override tracking
        self._weather_mgr = WeatherManager(hass=self.hass, logger=self.logger)
        # Override pipeline
        self._pipeline = PipelineRegistry(
            [
                ForceOverrideHandler(),
                WeatherOverrideHandler(),
                MotionTimeoutHandler(),
                ManualOverrideHandler(),
                CloudSuppressionHandler(),
                ClimateHandler(),
                GlareZoneHandler(),
                SolarHandler(),
                DefaultHandler(),
            ]
        )
        self._pipeline_result = None
        self._update_listener = None
        self._scheduled_time = dt.datetime.now()

        self._cached_options = None

        # Initialize configuration service
        self._config_service = ConfigurationService(
            self.hass,
            self.config_entry,
            self.logger,
            self._cover_type,
            self._toggles.temp_toggle,
            self._toggles.lux_toggle,
            self._toggles.irradiance_toggle,
        )

        # Climate state provider
        self._climate_provider = ClimateProvider(hass=self.hass, logger=self.logger)

        # Sun data provider
        self._sun_provider = SunProvider(hass=self.hass)

        # Cover entity state provider
        self._cover_provider = CoverProvider(hass=self.hass, logger=self.logger)

        # Current state snapshot (built at start of each update cycle)
        self._snapshot: CoverStateSnapshot | None = None

        # Diagnostics builder (extracted from coordinator)
        self._diagnostics_builder = DiagnosticsBuilder()

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

        # Time window manager (start/end time checks)
        self._time_mgr = TimeWindowManager(hass=self.hass, logger=self.logger)

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

    @property
    def is_weather_override_active(self) -> bool:
        """Check if weather override is active (conditions met or in clear-delay).

        Returns:
            True when a weather condition is active or the clear-delay timeout
            has not yet expired. False when no sensors configured (feature disabled).

        """
        return self._weather_mgr.is_weather_override_active

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
        end = self._time_mgr.end_time

        self.logger.debug("Checking timed refresh. End time: %s, now: %s", end, now)

        if end is not None and (now - end) <= dt.timedelta(seconds=5):
            self.timed_refresh = True
            self.logger.debug("Timed refresh triggered")
            await self.async_refresh()
        else:
            self.logger.debug("Timed refresh, but: not equal to end time")

    async def async_check_entity_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Trigger refresh when a tracked entity (sun, temp, weather, presence) changes."""
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
        """Detect manual overrides when a managed cover changes position."""
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

    async def async_check_weather_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle weather sensor state changes.

        Activates the override immediately when any condition exceeds its threshold.
        Starts a clear-delay timeout when all conditions drop back below thresholds,
        so covers stay retracted briefly during intermittent gusts or rain showers.
        """
        data = event.data
        entity_id = data["entity_id"]
        new_state = data["new_state"]

        if new_state is None:
            return

        self.logger.debug(
            "Weather sensor %s state changed to %s",
            entity_id,
            new_state.state,
        )

        is_now_active = self._weather_mgr.is_any_condition_active

        if is_now_active:
            if not self._weather_mgr._override_active:
                self.logger.info(
                    "Weather conditions active (%s) — retracting covers", entity_id
                )
                self._weather_mgr.record_conditions_active()
                self.state_change = True
                await self.async_refresh()
            # Already active: refresh so the pipeline re-evaluates position
            else:
                self.state_change = True
                await self.async_refresh()
        else:
            if self._weather_mgr._override_active:
                self.logger.info(
                    "Weather conditions cleared (%s) — starting clear-delay timeout",
                    entity_id,
                )
                self._weather_mgr.cancel_weather_timeout()
                self._start_weather_timeout()

    async def async_check_motion_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle motion sensor changes: immediate on detection, debounced on stop."""
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
        """Check if cover position change was user-initiated (manual override detection)."""
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
            caps = self._cmd_svc.get_cover_capabilities(entity_id)

            # Get position based on capability
            position = self._cmd_svc.read_position_with_capabilities(
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

    def _start_weather_timeout(self) -> None:
        """Start weather clear-delay timeout."""

        async def _refresh_with_state_change() -> None:
            self.state_change = True
            await self.async_refresh()

        self._weather_mgr.start_weather_timeout(
            refresh_callback=_refresh_with_state_change
        )

    def _cancel_weather_timeout(self) -> None:
        """Cancel weather clear-delay timeout task."""
        self._weather_mgr.cancel_weather_timeout()

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
        """Calculate cover state via pipeline and return final position."""
        # Always read weather/lux/irradiance for cloud suppression (independent of climate mode)
        self._read_weather_conditions(options)
        self.climate_strategy = None

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
            self._pipeline_result = None
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

        # Build snapshot with raw state — handlers evaluate their own conditions
        glare_zones_cfg = None
        active_zone_names: set[str] = set()
        if self.is_blind_cover:
            options_local = self.config_entry.options
            glare_zones_cfg = self._config_service.get_glare_zones_config(options_local)
            if glare_zones_cfg is not None:
                for idx, zone in enumerate(glare_zones_cfg.zones):
                    if getattr(self, f"glare_zone_{idx}", True):
                        active_zone_names.add(zone.name)

        snapshot = PipelineSnapshot(
            cover=cover_data,
            config=cover_data.config,
            cover_type=self._cover_type,
            default_position=int(round(cover_data.default)),
            climate_readings=self._weather_readings,
            climate_mode_enabled=self._toggles.switch_mode,
            climate_options=self._build_climate_options(options),
            force_override_sensors=self._read_force_sensor_states(options),
            force_override_position=options.get(CONF_FORCE_OVERRIDE_POSITION, 0),
            manual_override_active=self.manager.binary_cover_manual,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            weather_override_position=options.get(CONF_WEATHER_OVERRIDE_POSITION, 0),
            glare_zones=glare_zones_cfg,
            active_zone_names=frozenset(active_zone_names),
        )
        self._pipeline_result = self._pipeline.evaluate(snapshot)
        self.control_method = self._pipeline_result.control_method
        self.logger.debug(
            "Pipeline result: %s → %s",
            self.control_method,
            self._pipeline_result.position,
        )

        # Update climate diagnostics from pipeline result
        if self._pipeline_result.climate_state is not None:
            self.climate_state = self._pipeline_result.climate_state
            self.climate_strategy = self._pipeline_result.climate_strategy

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
        """Run the main coordinator update cycle: calculate position, send commands, build diagnostics."""
        self.logger.debug("Updating data")
        if self.first_refresh:
            self._cached_options = self.config_entry.options

        options = self.config_entry.options
        self._update_options(options)

        # Build unified state snapshot for this update cycle
        _sun_azimuth = state_attr(self.hass, "sun.sun", "azimuth")
        _sun_elevation = state_attr(self.hass, "sun.sun", "elevation")
        self._snapshot = CoverStateSnapshot(
            sun=SunSnapshot(
                azimuth=_sun_azimuth if _sun_azimuth is not None else 0.0,
                elevation=_sun_elevation if _sun_elevation is not None else 0.0,
            ),
            climate=None,  # Populated later when climate mode data is read
            cover_positions=self._cover_provider.read_positions(
                self.entities, self._cover_type
            ),
            cover_capabilities=self._cover_provider.read_all_capabilities(
                self.entities
            ),
            motion_detected=self.is_motion_detected,
            force_override_active=self.is_force_override_active,
        )

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

        # Determine glare_active from last calculation details (vertical covers only)
        glare_active = False
        if hasattr(cover_data, "_last_calc_details"):
            details = cover_data._last_calc_details  # noqa: SLF001
            glare_active = len(details.get("glare_zones_active", [])) > 0

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
                "glare_active": glare_active,
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
        """Send position commands to all covers when a tracked entity changes."""
        if self.automatic_control or self._pipeline_bypasses_auto_control:
            for cover in self.entities:
                await self.async_handle_call_service(cover, state, options)
        else:
            self.logger.debug("State change but control toggle is off")
        self.state_change = False
        self.logger.debug("State change handled")

    async def async_handle_cover_state_change(self, state: int):
        """Compare actual cover position to expected; set manual override if they differ."""
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
        """Set target positions and send initial positioning commands after startup."""
        if self.automatic_control or self._pipeline_bypasses_auto_control:
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
        """Move covers to sunset position when the configured end time is reached."""
        sunset_pos_raw = options.get(CONF_SUNSET_POS)
        self.logger.debug(
            "This is a timed refresh, using sunset position: %s",
            sunset_pos_raw,
        )
        if sunset_pos_raw is None:
            self.logger.debug("Timed refresh: no sunset position configured, skipping")
            self.timed_refresh = False
            return
        if self.automatic_control or self._pipeline_bypasses_auto_control:
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
            self._cmd_svc.record_skipped_action(entity, "Outside time window", state)
            return
        if not self.check_position_delta(entity, state, options):
            self.logger.debug("Skipping %s: position delta too small", entity)
            self._cmd_svc.record_skipped_action(
                entity, "Position delta too small", state
            )
            return
        if not self._cmd_svc.check_time_delta(entity, self.time_threshold):
            self.logger.debug("Skipping %s: time delta too small", entity)
            self._cmd_svc.record_skipped_action(entity, "Time delta too small", state)
            return
        if self.manager.is_cover_manual(entity):
            self.logger.debug("Skipping %s: manual override active", entity)
            self._cmd_svc.record_skipped_action(entity, "Manual override active", state)
            return
        await self.async_set_position(entity, state)

    async def async_set_position(self, entity, state: int):
        """Set cover position.

        Wrapper method that delegates to async_set_manual_position. Provided
        for backwards compatibility and clearer calling semantics.

        Args:
            entity: Cover entity ID to control
            state: Target position (0-100)

        """
        await self.async_set_manual_position(entity, state)

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

        caps = self._cmd_svc.get_cover_capabilities(entity)
        service, service_data, supports_position = (
            self._cmd_svc.prepare_position_service_call(
                entity, state, caps, inverse_state=self._inverse_state
            )
        )

        if service is None:
            return

        self._cmd_svc.track_cover_action(
            entity, service, state, supports_position, inverse_state=self._inverse_state
        )

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
        self.manual_reset = options.get(CONF_MANUAL_OVERRIDE_RESET, False)
        self.manual_duration = options.get(CONF_MANUAL_OVERRIDE_DURATION, {"hours": 2})
        self.manual_threshold = options.get(CONF_MANUAL_THRESHOLD)
        self.start_value = options.get(CONF_INTERP_START)
        self.end_value = options.get(CONF_INTERP_END)
        self.normal_list = options.get(CONF_INTERP_LIST)
        self.new_list = options.get(CONF_INTERP_LIST_NEW)
        self._cmd_svc.update_threshold(options.get(CONF_OPEN_CLOSE_THRESHOLD, 50))
        self._time_mgr.update_config(
            start_time=options.get(CONF_START_TIME),
            start_time_entity=options.get(CONF_START_ENTITY),
            end_time=options.get(CONF_END_TIME),
            end_time_entity=options.get(CONF_END_ENTITY),
        )
        self._motion_mgr.update_config(
            sensors=options.get(CONF_MOTION_SENSORS, []),
            timeout_seconds=options.get(CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT),
        )
        self._weather_mgr.update_config(
            wind_speed_sensor=options.get(CONF_WEATHER_WIND_SPEED_SENSOR),
            wind_direction_sensor=options.get(CONF_WEATHER_WIND_DIRECTION_SENSOR),
            wind_speed_threshold=options.get(
                CONF_WEATHER_WIND_SPEED_THRESHOLD, DEFAULT_WEATHER_WIND_SPEED_THRESHOLD
            ),
            wind_direction_tolerance=options.get(
                CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
                DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
            ),
            win_azi=options.get(CONF_AZIMUTH, 180),
            rain_sensor=options.get(CONF_WEATHER_RAIN_SENSOR),
            rain_threshold=options.get(
                CONF_WEATHER_RAIN_THRESHOLD, DEFAULT_WEATHER_RAIN_THRESHOLD
            ),
            is_raining_sensor=options.get(CONF_WEATHER_IS_RAINING_SENSOR),
            is_windy_sensor=options.get(CONF_WEATHER_IS_WINDY_SENSOR),
            severe_sensors=options.get(CONF_WEATHER_SEVERE_SENSORS, []),
            timeout_seconds=options.get(CONF_WEATHER_TIMEOUT, DEFAULT_WEATHER_TIMEOUT),
        )

    def _update_manager_and_covers(self):
        """Update manager with cover entities.

        Registers cover entities with the AdaptiveCoverManager and resets
        manual override state for all covers if manual override detection
        is disabled.

        """
        self.manager.add_covers(self.entities)
        if not self._toggles.manual_toggle:
            for entity in self.manager.manual_controlled:
                self.manager.reset(entity)

    def get_blind_data(self, options):
        """Instantiate the appropriate cover calculation class for the current type."""
        sun_data = self._sun_provider.create_sun_data(self.hass.config.time_zone)
        config = self._config_service.get_common_data(options)
        sol_azi, sol_elev = self.pos_sun

        if self.is_blind_cover:
            vert_config = self._config_service.get_vertical_data(options)
            glare_zones_cfg = self._config_service.get_glare_zones_config(options)
            if glare_zones_cfg is not None:
                vert_config = replace(vert_config, glare_zones=glare_zones_cfg)
            cover_data = AdaptiveVerticalCover(
                logger=self.logger,
                sol_azi=sol_azi,
                sol_elev=sol_elev,
                sun_data=sun_data,
                config=config,
                vert_config=vert_config,
            )
        if self.is_awning_cover:
            cover_data = AdaptiveHorizontalCover(
                logger=self.logger,
                sol_azi=sol_azi,
                sol_elev=sol_elev,
                sun_data=sun_data,
                config=config,
                vert_config=self._config_service.get_vertical_data(options),
                horiz_config=self._config_service.get_horizontal_data(options),
            )
        if self.is_tilt_cover:
            cover_data = AdaptiveTiltCover(
                logger=self.logger,
                sol_azi=sol_azi,
                sol_elev=sol_elev,
                sun_data=sun_data,
                config=config,
                tilt_config=self._config_service.get_tilt_data(options),
            )
        return cover_data

    @property
    def check_adaptive_time(self):
        """Check if current time is within operational window — delegates to TimeWindowManager."""
        return self._time_mgr.is_active

    @property
    def after_start_time(self):
        """Check if current time is after start time — delegates to TimeWindowManager."""
        return self._time_mgr.after_start_time

    @property
    def _end_time(self) -> dt.datetime | None:
        """Get end time — delegates to TimeWindowManager."""
        return self._time_mgr.end_time

    @property
    def before_end_time(self):
        """Check if current time is before end time — delegates to TimeWindowManager."""
        return self._time_mgr.before_end_time

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
        return self._cmd_svc.check_position(
            entity, state, sun_just_appeared=sun_just_appeared
        )

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

    def _read_weather_conditions(self, options) -> None:
        """Read weather/lux/irradiance/cloud-coverage into self._weather_readings (always runs)."""
        cloud_suppression_enabled = bool(options.get(CONF_CLOUD_SUPPRESSION, False))
        self._weather_readings = self._climate_provider.read(
            weather_entity=options.get(CONF_WEATHER_ENTITY),
            weather_condition=options.get(CONF_WEATHER_STATE),
            use_lux=bool(self._toggles.lux_toggle),
            lux_entity=options.get(CONF_LUX_ENTITY),
            lux_threshold=options.get(CONF_LUX_THRESHOLD),
            use_irradiance=bool(self._toggles.irradiance_toggle),
            irradiance_entity=options.get(CONF_IRRADIANCE_ENTITY),
            irradiance_threshold=options.get(CONF_IRRADIANCE_THRESHOLD),
            use_cloud_coverage=cloud_suppression_enabled,
            cloud_coverage_entity=options.get(CONF_CLOUD_COVERAGE_ENTITY),
            cloud_coverage_threshold=options.get(CONF_CLOUD_COVERAGE_THRESHOLD),
        )

    def _build_climate_options(self, options) -> ClimateOptions:
        """Build ClimateOptions from config entry options."""
        return ClimateOptions(
            temp_low=options.get(CONF_TEMP_LOW),
            temp_high=options.get(CONF_TEMP_HIGH),
            temp_switch=bool(self._toggles.temp_toggle),
            transparent_blind=options.get(CONF_TRANSPARENT_BLIND, False),
            temp_summer_outside=options.get(CONF_OUTSIDE_THRESHOLD),
            cloud_suppression_enabled=bool(options.get(CONF_CLOUD_SUPPRESSION, False)),
            winter_close_insulation=bool(
                options.get(CONF_WINTER_CLOSE_INSULATION, False)
            ),
        )

    def _read_force_sensor_states(self, options) -> dict[str, bool]:
        """Read force override sensor states from HA into a plain dict."""
        sensors = options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
        return {
            sensor: bool(
                (state := self.hass.states.get(sensor)) and state.state == "on"
            )
            for sensor in sensors
        }

    def build_diagnostic_data(self) -> dict:
        """Build diagnostic data from current coordinator state."""
        ctx = DiagnosticContext(
            pos_sun=self.pos_sun,
            normal_cover_state=self.normal_cover_state,
            raw_calculated_position=self.raw_calculated_position,
            climate_state=self.climate_state,
            climate_data=self.climate_data,
            climate_strategy=self.climate_strategy,
            climate_mode=self._climate_mode,
            control_method=self.control_method,
            pipeline_result=self._pipeline_result,
            is_force_override_active=self.is_force_override_active,
            is_weather_override_active=self.is_weather_override_active,
            is_motion_timeout_active=self.is_motion_timeout_active,
            is_manual_override_active=self.manager.binary_cover_manual,
            check_adaptive_time=self.check_adaptive_time,
            after_start_time=self.after_start_time,
            before_end_time=self.before_end_time,
            start_time=self._time_mgr.start_time_value,
            end_time=self._end_time,
            automatic_control=self.automatic_control,
            last_cover_action=self.last_cover_action,
            last_skipped_action=self.last_skipped_action,
            min_change=self.min_change,
            time_threshold=self.time_threshold,
            switch_mode=self._toggles.switch_mode,
            inverse_state=self._inverse_state,
            use_interpolation=self._use_interpolation,
            default_state=self.default_state,
            final_state=self.state,
            config_options=dict(self.config_entry.options),
            motion_detected=self.is_motion_detected,
            motion_timeout_active=self._motion_mgr._motion_timeout_active,
            force_override_sensors=self.config_entry.options.get(
                CONF_FORCE_OVERRIDE_SENSORS, []
            ),
            force_override_position=self.config_entry.options.get(
                CONF_FORCE_OVERRIDE_POSITION, 0
            ),
        )

        diagnostics, explanation = self._diagnostics_builder.build(ctx)

        if explanation != self._last_position_explanation:
            self.logger.debug("Position explanation changed: %s", explanation)
            self._last_position_explanation = explanation

        return diagnostics

    @property
    def state(self) -> int:
        """Final cover position after pipeline, interpolation, and inverse_state transforms."""
        # Safety overrides always take full precedence — even outside the time window
        # and even when automatic_control is OFF.  Two paths:
        #   1. Inside time window: _pipeline_bypasses_auto_control is True (pipeline ran
        #      and the winning handler set bypass_auto_control=True).
        #   2. Outside time window: _pipeline_result is None (pipeline skipped), so
        #      fall back to the raw is_force/weather_override_active properties.
        # Both paths skip interpolation and inverse_state transforms.
        if self._pipeline_bypasses_auto_control and self._pipeline_result is not None:
            return self._pipeline_result.position
        if self._pipeline_result is None:
            # Outside time window — pipeline did not run; check safety overrides directly
            if self.is_force_override_active:
                return self.config_entry.options.get(CONF_FORCE_OVERRIDE_POSITION, 0)
            if self.is_weather_override_active:
                return self.config_entry.options.get(CONF_WEATHER_OVERRIDE_POSITION, 0)
        if self.is_motion_timeout_active:
            return self.default_state

        # Use pipeline result if available, otherwise default_state (outside time window)
        if self._pipeline_result is not None:
            state = self._pipeline_result.position
        else:
            state = self.default_state

        # Post-processing: interpolation and inverse state
        if self._use_interpolation:
            state = interpolate_position(
                state,
                self.start_value,
                self.end_value,
                self.normal_list,
                self.new_list,
            )

        if self._inverse_state and self._use_interpolation:
            self.logger.info("Inverse state is not supported with interpolation")

        if self._inverse_state and not self._use_interpolation:
            state = inverse_state(state)

        return state

    # --- Toggle property delegates (switch entities use setattr) ---

    @property
    def switch_mode(self):
        """Climate mode toggle — delegates to ToggleManager."""
        return self._toggles.switch_mode

    @switch_mode.setter
    def switch_mode(self, value):
        """Set climate mode toggle."""
        self._toggles.switch_mode = value

    @property
    def temp_toggle(self):
        """Temperature entity toggle — delegates to ToggleManager."""
        return self._toggles.temp_toggle

    @temp_toggle.setter
    def temp_toggle(self, value):
        """Set temperature entity toggle."""
        self._toggles.temp_toggle = value

    @property
    def automatic_control(self):
        """Automatic control toggle — delegates to ToggleManager."""
        return self._toggles.automatic_control

    @automatic_control.setter
    def automatic_control(self, value):
        """Set automatic control toggle."""
        self._toggles.automatic_control = value

    @property
    def _pipeline_bypasses_auto_control(self) -> bool:
        """True when the active pipeline result should run even if automatic_control is OFF.

        Safety handlers (ForceOverrideHandler, WeatherOverrideHandler) set
        bypass_auto_control=True so that wind/rain/force protection still
        operates when the user has paused normal sun-tracking automation.
        """
        return (
            self._pipeline_result is not None
            and self._pipeline_result.bypass_auto_control
        )

    @property
    def manual_toggle(self):
        """Manual override detection toggle — delegates to ToggleManager."""
        return self._toggles.manual_toggle

    @manual_toggle.setter
    def manual_toggle(self, value):
        """Set manual override detection toggle."""
        self._toggles.manual_toggle = value

    @property
    def lux_toggle(self):
        """Lux entity toggle — delegates to ToggleManager."""
        return self._toggles.lux_toggle

    @lux_toggle.setter
    def lux_toggle(self, value):
        """Set lux entity toggle."""
        self._toggles.lux_toggle = value

    @property
    def irradiance_toggle(self):
        """Irradiance entity toggle — delegates to ToggleManager."""
        return self._toggles.irradiance_toggle

    @irradiance_toggle.setter
    def irradiance_toggle(self, value):
        """Set irradiance entity toggle."""
        self._toggles.irradiance_toggle = value

    @property
    def return_to_default_toggle(self):
        """Return to default toggle — delegates to ToggleManager."""
        return self._toggles.return_to_default_toggle

    @return_to_default_toggle.setter
    def return_to_default_toggle(self, value):
        """Set return to default toggle."""
        self._toggles.return_to_default_toggle = value

    async def _check_time_window_transition(self, now: dt.datetime) -> None:
        """Check time window transitions — delegates to TimeWindowManager."""

        async def _trigger_timed_refresh():
            self.timed_refresh = True
            await self.async_refresh()

        await self._time_mgr.check_transition(
            track_end_time=self._track_end_time,
            refresh_callback=_trigger_timed_refresh,
        )

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

        # Skip if automatic control is disabled (safety overrides may still bypass this)
        if not self.automatic_control and not self._pipeline_bypasses_auto_control:
            return

        for entity_id in self.entities:
            await self._verify_entity_position(entity_id, now)

    async def _verify_entity_position(self, entity_id: str, now: dt.datetime) -> None:
        """Check if entity reached target position; retry up to MAX_POSITION_RETRIES times."""
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

        # Cancel weather clear-delay timeout task
        self._cancel_weather_timeout()

        # Stop position verification
        self._stop_position_verification()

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager and inverse_state have been moved to managers/manual_override.py
# They are re-imported above to maintain backward compatibility.
