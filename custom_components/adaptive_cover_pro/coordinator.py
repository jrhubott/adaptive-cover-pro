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
)

# EventStateChangedData was added in Home Assistant 2024.4+
# For backwards compatibility with older versions
try:
    from homeassistant.core import EventStateChangedData
except ImportError:
    # Fallback for older Home Assistant versions
    EventStateChangedData = dict  # type: ignore[misc,assignment]
from homeassistant.helpers.template import state_attr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .helpers import compute_effective_default
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
    CONF_CUSTOM_POSITION_1,
    CONF_CUSTOM_POSITION_2,
    CONF_CUSTOM_POSITION_3,
    CONF_CUSTOM_POSITION_4,
    CONF_CUSTOM_POSITION_PRIORITY_1,
    CONF_CUSTOM_POSITION_PRIORITY_2,
    CONF_CUSTOM_POSITION_PRIORITY_3,
    CONF_CUSTOM_POSITION_PRIORITY_4,
    CONF_CUSTOM_POSITION_SENSOR_1,
    CONF_CUSTOM_POSITION_SENSOR_2,
    CONF_CUSTOM_POSITION_SENSOR_3,
    CONF_CUSTOM_POSITION_SENSOR_4,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
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
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
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
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_LOW,
    CONF_TEMP_HIGH,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
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
    STARTUP_GRACE_PERIOD_SECONDS,
    TRANSIT_TIMEOUT_SECONDS,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
)
from .diagnostics.builder import DiagnosticContext, DiagnosticsBuilder
from .managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_special_positions,
)
from .managers.grace_period import GracePeriodManager
from .managers.manual_override import AdaptiveCoverManager, inverse_state
from .managers.motion import MotionManager
from .managers.weather import WeatherManager
from .managers.time_window import TimeWindowManager
from .managers.toggles import ToggleManager
from .position_utils import interpolate_position
from .pipeline.handlers import (
    ClimateHandler,
    CloudSuppressionHandler,
    CustomPositionHandler,
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
        self.manual_duration = (
            self.config_entry.options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2}
        )
        self.state_change = False
        self.cover_state_change = False
        self.first_refresh = False
        self._weather_readings: ClimateReadings | None = None
        self.state_change_data: StateChangedData | None = None
        # Queue of cover state-change events pending manual override evaluation.
        # Each call to async_check_cover_state_change() appends to this list so
        # that rapid events from multiple covers are all processed rather than
        # the last event silently overwriting earlier ones (single-variable race).
        # async_handle_cover_state_change() drains the list on every refresh.
        self._pending_cover_events: list[StateChangedData] = []
        # Entities whose target was just reached in the current state-change event.
        # When process_entity_state_change() clears wait_for_target because the cover
        # reached its commanded position (within tolerance), the same event also
        # triggers async_handle_cover_state_change() with wait_for_target already
        # False.  Without this guard the cover's final resting position (which may
        # differ from the commanded value by up to POSITION_TOLERANCE_PERCENT) is
        # immediately flagged as a manual override.  Cleared at the end of each
        # async_handle_cover_state_change() call.
        self._target_just_reached: set[str] = set()
        # Cover engine object — populated at start of each update cycle
        self._cover_data = None
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
        # Override pipeline — custom position handlers are created per-slot so
        # each can carry an independent priority configured by the user.
        self._pipeline = self._build_pipeline()
        self._pipeline_result = None

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

        # Track force override state across update cycles so we can detect
        # the release transition and bypass time/position delta gates.
        self._prev_force_override_active: bool = False

        # Diagnostics builder (extracted from coordinator)
        self._diagnostics_builder = DiagnosticsBuilder()

        # Track position explanation for change detection logging
        self._last_position_explanation: str = ""

        # Cover command service — self-contained: owns positioning, target tracking,
        # and the reconciliation timer (started in async_config_entry_first_refresh).
        # on_tick keeps time window transition checks running on the same 1-min interval
        # without needing a separate HA timer.
        self._cmd_svc = CoverCommandService(
            hass=self.hass,
            logger=self.logger,
            cover_type=self._cover_type,
            grace_mgr=self._grace_mgr,
            open_close_threshold=self.config_entry.options.get(
                CONF_OPEN_CLOSE_THRESHOLD, 50
            ),
            on_tick=self._check_time_window_transition,
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
        return any(self._read_force_sensor_states(self.config_entry.options).values())

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
        # Start cover command service reconciliation timer
        self._cmd_svc.start()

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
            # Keep a per-event copy so async_handle_cover_state_change() can
            # process all covers that fired in a single refresh window, not
            # just the last one to overwrite state_change_data.
            self._pending_cover_events.append(self.state_change_data)
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
            # Returns True if timeout was active (expired) or pending (task
            # still running), so we refresh in both cases, not just when the
            # timeout had already fully expired.
            needs_refresh = self._motion_mgr.record_motion_detected()

            if needs_refresh:
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

            # Grace period expired — check if cover reached target (tolerance-based)
            caps = self._cmd_svc.get_cover_capabilities(entity_id)
            position = self._cmd_svc.read_position_with_capabilities(
                entity_id, caps, event.new_state
            )
            reached = self._cmd_svc.check_target_reached(entity_id, position)
            if reached:
                # Mark this entity so async_handle_cover_state_change() skips the
                # manual override comparison for this event.  The cover has just
                # settled at its commanded position (within position tolerance) —
                # any small positional difference is motor rounding, not a user
                # action.  The set is cleared at the end of that handler.
                self._target_just_reached.add(entity_id)
                self.logger.debug(
                    "Target just reached for %s — skipping manual override check for this event",
                    entity_id,
                )
            else:
                # Grace period expired but the cover is not at the commanded target.
                # Determine whether the cover is still actively moving toward the
                # target (integration-initiated transit) or has stopped / moved away
                # (user action — Issue #147).
                #
                # HA covers report transitional states ("opening"/"closing") while
                # moving, then a final state ("stopped"/"open"/"closed") when done.
                # If ignore_intermediate_states is True, those transitional events
                # are already filtered out above (lines 508-513), so we only reach
                # here with final states and always clear wait_for_target.
                cover_is_transitioning = event.new_state.state in (
                    "opening",
                    "closing",
                )

                if cover_is_transitioning:
                    # Hard backstop: if the command is older than
                    # TRANSIT_TIMEOUT_SECONDS the cover should have arrived.
                    # Clear wait_for_target so that covers without a final
                    # "stopped" state (position-only reporters) cannot block
                    # manual override detection indefinitely when the user
                    # stops them mid-transit.
                    sent_at = self._cmd_svc._sent_at.get(entity_id)  # noqa: SLF001
                    if sent_at is not None:
                        elapsed = (dt.datetime.now(dt.UTC) - sent_at).total_seconds()
                        if elapsed > TRANSIT_TIMEOUT_SECONDS:
                            self._cmd_svc.wait_for_target[entity_id] = False
                            self.logger.debug(
                                "Transit timeout for %s (%.0fs > %ds) "
                                "— clearing wait_for_target",
                                entity_id,
                                elapsed,
                                TRANSIT_TIMEOUT_SECONDS,
                            )
                            self.logger.debug(
                                "Wait for target: %s", self.wait_for_target
                            )
                            return

                    # Cover is within the transit window — check direction.
                    # If the cover is moving closer to the target it is still
                    # responding to our command; keep wait_for_target=True.
                    # If it moved away or stalled, clear it so manual override
                    # detection can run (Issue #147 fix preserved).
                    old_position = self._cmd_svc.read_position_with_capabilities(
                        entity_id, caps, event.old_state
                    )
                    target = self._cmd_svc.target_call.get(entity_id)

                    if (
                        old_position is not None
                        and position is not None
                        and target is not None
                    ):
                        old_distance = abs(old_position - target)
                        new_distance = abs(position - target)
                        if new_distance < old_distance:
                            # Moving closer to target — still in transit.
                            self.logger.debug(
                                "Grace expired but %s still moving toward target "
                                "%s (was %s, now %s) — keeping wait_for_target",
                                entity_id,
                                target,
                                old_position,
                                position,
                            )
                            self.logger.debug(
                                "Wait for target: %s", self.wait_for_target
                            )
                            return

                # Cover has stopped (non-transitional state), moved away from
                # target, stalled (equal distances), or position data unavailable.
                # Clear wait_for_target to allow manual override detection.
                self._cmd_svc.wait_for_target[entity_id] = False
                self.logger.debug(
                    "Grace period expired, cover %s not at target and not in "
                    "active transit — clearing wait_for_target to allow manual "
                    "override detection (position=%s, state=%s)",
                    entity_id,
                    position,
                    event.new_state.state,
                )
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

    def _check_initial_motion_state(self) -> None:
        """Initialize motion state from current sensor readings at startup/reload.

        Reads each configured motion sensor and sets the appropriate state so
        the Motion Status sensor reflects reality immediately instead of showing
        ``waiting_for_data`` until the first sensor state change event arrives.

        - Any sensor **on**  → record_motion_detected() sets last_motion_time
          so the sensor shows ``motion_detected``.
        - All sensors **off** → set_no_motion() marks the timeout active so
          the sensor shows ``no_motion``.
        """
        if not self.config_entry.options.get(CONF_MOTION_SENSORS):
            return
        if self.is_motion_detected:
            self._motion_mgr.record_motion_detected()
        else:
            self._motion_mgr.set_no_motion()

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

    def _calculate_cover_state(self, cover_data, options) -> int:
        """Calculate cover state via pipeline and return final position.

        The pipeline always runs regardless of the operational time window.
        The time-window gate is enforced by CoverCommandService.apply_position()
        which skips sending commands when outside the window (unless forced).
        This means diagnostics, Decision Trace, and sensor state are always
        up-to-date even when no commands are being sent.
        """
        # Read all climate-related entities (temp, presence, weather, lux, irradiance, cloud).
        # The result is stored in self._weather_readings and passed to PipelineSnapshot
        # so ClimateHandler and CloudSuppressionHandler can self-evaluate.
        self._read_climate_state(options)

        # Compute the effective default position from astronomical sunset/sunrise.
        # This is the single source of truth — all pipeline handlers use it via
        # snapshot.default_position.  The sunset_pos is active when current time
        # is after (astronomical_sunset + sunset_offset) or before
        # (astronomical_sunrise + sunrise_offset).
        h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
        sunset_pos_cfg = options.get(CONF_SUNSET_POS)  # None when not configured
        sunset_off = int(options.get(CONF_SUNSET_OFFSET) or 0)
        sunrise_off = int(
            options.get(CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET) or 0)
        )
        effective_default, is_sunset_active = compute_effective_default(
            h_def=h_def,
            sunset_pos=sunset_pos_cfg,
            sun_data=cover_data.sun_data,
            sunset_off=sunset_off,
            sunrise_off=sunrise_off,
        )
        self.logger.debug(
            "Effective default: %s (sunset_active=%s, h_def=%s, sunset_pos=%s)",
            effective_default,
            is_sunset_active,
            h_def,
            sunset_pos_cfg,
        )

        # Store cover engine object for use by diagnostics/sensors
        self._cover_data = cover_data

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
            default_position=effective_default,
            is_sunset_active=is_sunset_active,
            # NOTE: configured_default and configured_sunset_pos are deliberately
            # absent from PipelineSnapshot.  They are annotated onto PipelineResult
            # by the coordinator after evaluation (see below) so the raw config
            # values are never accessible to pipeline handler logic.
            climate_readings=self._weather_readings,
            climate_mode_enabled=self._toggles.switch_mode,
            climate_options=self._build_climate_options(options),
            force_override_sensors=self._read_force_sensor_states(options),
            force_override_position=options.get(CONF_FORCE_OVERRIDE_POSITION, 0),
            manual_override_active=self.manager.binary_cover_manual,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            weather_override_position=options.get(CONF_WEATHER_OVERRIDE_POSITION, 0),
            weather_bypass_auto_control=options.get(
                CONF_WEATHER_BYPASS_AUTO_CONTROL, True
            ),
            glare_zones=glare_zones_cfg,
            active_zone_names=frozenset(active_zone_names),
            in_time_window=self.check_adaptive_time,
            motion_control_enabled=self._toggles.motion_control,
            custom_position_sensors=self._read_custom_position_sensor_states(options),
        )
        self._pipeline_result = self._pipeline.evaluate(snapshot)

        # Annotate the result with the raw config values *after* evaluation.
        # These are for diagnostics and the Decision Trace sensor only; they
        # were deliberately excluded from PipelineSnapshot so handlers cannot
        # use them to derive an alternative default position.
        self._pipeline_result = replace(
            self._pipeline_result,
            configured_default=h_def,
            configured_sunset_pos=(
                int(sunset_pos_cfg) if sunset_pos_cfg is not None else None
            ),
        )

        self.logger.debug(
            "Pipeline result: %s → %s",
            self._pipeline_result.control_method,
            self._pipeline_result.position,
        )

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

        # Capture force override state before this cycle so we can detect
        # the release transition in async_handle_state_change().
        prev_force_override = self._prev_force_override_active

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

        # Reset expired manual overrides BEFORE running the pipeline so the
        # pipeline sees the cleared state and computes the correct position.
        auto_expired = await self.manager.reset_if_needed()

        # Calculate cover state (pipeline runs with up-to-date override state)
        state = self._calculate_cover_state(cover_data, options)

        # Update prev state for next cycle (current force override state is now
        # captured in the snapshot we just built).
        self._prev_force_override_active = self.is_force_override_active

        # Handle types of changes
        if self.state_change:
            await self.async_handle_state_change(state, options, prev_force_override)
        elif auto_expired:
            # One or more manual overrides just timed out.  Proactively send
            # the fresh pipeline position so covers don't linger at the
            # user-moved position until the next solar/entity-state event.
            await self._async_send_after_override_clear(state, options)
        if self.cover_state_change:
            await self.async_handle_cover_state_change(state)
        if self.first_refresh:
            await self.async_handle_first_refresh(state, options)

        # Sync gate state to CoverCommandService so reconciliation respects
        # both manual override and automatic control.  Done after all change
        # handlers so the manager's manual_controlled list is fully up-to-date.
        self._cmd_svc.manual_override_entities = set(self.manager.manual_controlled)
        self._cmd_svc.auto_control_enabled = self.automatic_control
        self._cmd_svc.in_time_window = self.check_adaptive_time

        # Update solar times
        start, end = await self._update_solar_times_if_needed(self._cover_data)

        # Build diagnostic data (always enabled)
        diagnostics = self.build_diagnostic_data()

        # Determine glare_active from last calculation details (vertical covers only)
        glare_active = False
        if hasattr(self._cover_data, "_last_calc_details"):
            details = self._cover_data._last_calc_details  # noqa: SLF001
            glare_active = len(details.get("glare_zones_active", [])) > 0

        return AdaptiveCoverData(
            climate_mode_toggle=self.switch_mode,
            states={
                "state": state,
                "start": start,
                "end": end,
                "control": self._pipeline_result.control_method.value,
                "sun_motion": self._cover_data.direct_sun_valid,
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

    def _build_position_context(
        self,
        entity: str,
        options: dict,
        *,
        force: bool = False,
        sun_just_appeared: bool = False,
    ) -> PositionContext:
        """Build a PositionContext for the given cover entity.

        Assembles all coordinator-level flags into the dataclass that
        CoverCommandService.apply_position() uses for gate checks.

        Args:
            entity: Cover entity ID
            options: Config entry options dict
            force: If True, all gate checks are bypassed
            sun_just_appeared: Pre-computed sun transition flag. Call
                ``_check_sun_validity_transition()`` once before a multi-entity
                loop and pass the result here so the stateful transition check
                fires exactly once per update cycle.

        """
        return PositionContext(
            auto_control=self.automatic_control or self._pipeline_bypasses_auto_control,
            manual_override=self.manager.is_cover_manual(entity),
            sun_just_appeared=sun_just_appeared,
            min_change=self.min_change,
            time_threshold=self.time_threshold,
            special_positions=build_special_positions(options),
            inverse_state=self._inverse_state,
            force=force,
        )

    async def _async_send_after_override_clear(self, state: int, options: dict) -> None:
        """Send the current pipeline position after a manual override auto-expires.

        Called only when a manual override expires via the automatic timer
        (i.e. ``auto_expired`` path in ``_async_update_data``).  The reset
        button bypasses this method and calls ``apply_position`` directly.

        Uses force=True so the command is sent regardless of delta/time
        thresholds — the cover may be far from the calculated position after
        sitting in manual for an extended period.

        **Time-window guard:** If the current time is outside the configured
        active-hours window the integration has no business repositioning
        covers.  In that case we skip the send and log a debug message.  The
        normal update cycle (triggered when the time window opens via
        ``TimeWindowManager.check_transition``) will send the correct position
        at the appropriate time.

        **Automatic-control guard:** If the user has turned off Automatic
        Control the integration must not force covers back to the calculated
        position when an override expires.  The user deliberately paused
        automation; the cover should stay wherever the user left it.

        Args:
            state: Post-reset pipeline position (already computed without override).
            options: Config entry options dict.

        """
        if not self.check_adaptive_time:
            self.logger.debug(
                "Manual override cleared but outside active-hours window — "
                "skipping reposition (pipeline position was %s; will apply when "
                "window opens)",
                state,
            )
            return

        if not self.automatic_control:
            self.logger.debug(
                "Manual override cleared but automatic control is OFF — "
                "skipping reposition (pipeline position was %s)",
                state,
            )
            return

        self.logger.debug(
            "Sending pipeline position %s after manual override cleared", state
        )
        sun_just_appeared = self._check_sun_validity_transition()
        for cover in self.entities:
            ctx = self._build_position_context(
                cover, options, force=True, sun_just_appeared=sun_just_appeared
            )
            await self._cmd_svc.apply_position(
                cover, state, "manual_override_cleared", context=ctx
            )

    async def async_handle_state_change(
        self, state: int, options, prev_force_override: bool = False
    ):
        """Send position commands to all covers when a tracked entity changes.

        When the active pipeline result has bypass_auto_control=True (force
        override or weather safety handler), we pass force=True to the position
        context so that time_delta and position_delta gates cannot block
        safety-critical commands.  The reason string also reflects the handler
        that won rather than always saying "solar".

        When a force override just released (prev_force_override=True and it is
        now inactive), force=True is also passed so the time delta check cannot
        block the return to the calculated position.  The force override's own
        position change should not count against the time threshold.
        """
        sun_just_appeared = self._check_sun_validity_transition()
        is_safety = self._pipeline_bypasses_auto_control
        force_override_released = prev_force_override and not self.is_force_override_active

        # Outside the configured time window, only safety handlers (force
        # override, weather) are allowed to move covers.  All other handlers
        # (solar, climate, cloud, default) must not reposition covers before
        # the user's start time or after the end time.  The pipeline still
        # evaluates so diagnostics/sensor state remain correct.
        if not self.check_adaptive_time and not is_safety and not force_override_released:
            self.state_change = False
            self.logger.debug("Outside time window — skipping position update")
            return

        use_force = is_safety or force_override_released
        if force_override_released:
            reason = "force_override_cleared"
            self.logger.debug(
                "Force override released — bypassing time/position delta gates "
                "to return to calculated position %s",
                state,
            )
        else:
            reason = self._pipeline_result.control_method.value if is_safety else "solar"
        for cover in self.entities:
            ctx = self._build_position_context(
                cover, options, force=use_force, sun_just_appeared=sun_just_appeared
            )
            await self._cmd_svc.apply_position(cover, state, reason, context=ctx)
        self.state_change = False
        self.logger.debug("State change handled")

    async def async_handle_cover_state_change(self, state: int):
        """Compare actual cover position to expected; set manual override if they differ.

        Drains self._pending_cover_events so that rapid state changes from
        multiple covers are all evaluated, not just the most recent one.
        """
        # Drain and clear the queue atomically so a concurrent refresh that
        # fires while we iterate does not re-process the same events.
        events = self._pending_cover_events[:]
        self._pending_cover_events.clear()

        if self.manual_toggle and self.automatic_control:
            # Check startup grace period FIRST; suppress all events during
            # HA restart when covers respond slowly.
            if self._is_in_startup_grace_period():
                entity_ids = [e.entity_id for e in events]
                self.logger.debug(
                    "Position changes for %s ignored (in startup grace period)",
                    entity_ids,
                )
                self.cover_state_change = False
                return

            for event_data in events:
                entity_id = event_data.entity_id

                # Skip manual override detection when the cover just reached its
                # commanded target in this same event.  process_entity_state_change()
                # adds the entity to _target_just_reached when check_target_reached()
                # clears wait_for_target; without this guard the small positional
                # difference allowed by POSITION_TOLERANCE_PERCENT would be
                # misidentified as a user-initiated manual override.
                if entity_id in self._target_just_reached:
                    self._target_just_reached.discard(entity_id)
                    self.logger.debug(
                        "Skipping manual override check for %s — cover just reached commanded target",
                        entity_id,
                    )
                    continue

                # Use target_call if available (contains actual sent position),
                # otherwise fall back to calculated state.
                # This is critical for open/close-only covers where the calculated
                # state gets transformed (via threshold) to 0 or 100 before sending.
                expected_position = self.target_call.get(entity_id, state)

                self.manager.handle_state_change(
                    event_data,
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
        is_safety = self._pipeline_bypasses_auto_control

        # Outside the time window, only safety handlers (force override, weather)
        # are allowed to move covers on startup.  This prevents covers from
        # repositioning when HA restarts at midnight or another time outside the
        # configured operational window.
        if not self.check_adaptive_time and not is_safety:
            self.first_refresh = False
            self.logger.debug("First refresh outside time window — skipping position update")
            return

        sun_just_appeared = self._check_sun_validity_transition()
        for cover in self.entities:
            ctx = self._build_position_context(
                cover, options, force=is_safety, sun_just_appeared=sun_just_appeared
            )
            await self._cmd_svc.apply_position(cover, state, "startup", context=ctx)
        self.first_refresh = False
        self.logger.debug("First refresh handled")

    def _build_pipeline(self) -> PipelineRegistry:
        """Build the override pipeline, creating one CustomPositionHandler per slot.

        Called once at coordinator initialisation.  Because the integration
        reloads fully on every options change (see ``_async_update_listener``
        in ``__init__.py``), this method always sees the current configuration
        and there is no need to rebuild the pipeline at runtime.

        Custom position slots are created only for entries that have both a
        sensor and a position configured.  Each carries an independent priority
        so the PipelineRegistry can sort them into the correct evaluation order
        alongside all other handlers.
        """
        options = self.config_entry.options
        _slot_keys = [
            (1, CONF_CUSTOM_POSITION_SENSOR_1, CONF_CUSTOM_POSITION_1, CONF_CUSTOM_POSITION_PRIORITY_1),
            (2, CONF_CUSTOM_POSITION_SENSOR_2, CONF_CUSTOM_POSITION_2, CONF_CUSTOM_POSITION_PRIORITY_2),
            (3, CONF_CUSTOM_POSITION_SENSOR_3, CONF_CUSTOM_POSITION_3, CONF_CUSTOM_POSITION_PRIORITY_3),
            (4, CONF_CUSTOM_POSITION_SENSOR_4, CONF_CUSTOM_POSITION_4, CONF_CUSTOM_POSITION_PRIORITY_4),
        ]
        custom_handlers: list[CustomPositionHandler] = []
        for slot, sensor_key, pos_key, pri_key in _slot_keys:
            sensor = options.get(sensor_key)
            position = options.get(pos_key)
            if sensor and position is not None:
                priority = int(options.get(pri_key) or DEFAULT_CUSTOM_POSITION_PRIORITY)
                custom_handlers.append(
                    CustomPositionHandler(
                        slot=slot,
                        entity_id=sensor,
                        position=int(position),
                        priority=priority,
                    )
                )

        handlers = [
            ForceOverrideHandler(),
            WeatherOverrideHandler(),
            ManualOverrideHandler(),
            *custom_handlers,
            MotionTimeoutHandler(),
            CloudSuppressionHandler(),
            ClimateHandler(),
            GlareZoneHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
        self.logger.debug(
            "Pipeline built with %d custom position handler(s): %s",
            len(custom_handlers),
            [(h.name, h.priority) for h in custom_handlers],
        )
        return PipelineRegistry(handlers)

    def _update_options(self, options):
        """Update coordinator options from config entry.

        Extracts and caches configuration options from the config entry options
        dictionary. Called on every coordinator update to ensure latest settings
        are used.

        Args:
            options: Configuration options dictionary from config_entry.options

        """
        self.entities = options.get(CONF_ENTITIES, [])
        self.min_change = options.get(CONF_DELTA_POSITION) or 1
        self.time_threshold = options.get(CONF_DELTA_TIME) or 2
        self.manual_reset = options.get(CONF_MANUAL_OVERRIDE_RESET, False)
        self.manual_duration = options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2}
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
        _raw_azi, _raw_elev = self.pos_sun
        sol_azi = _raw_azi if _raw_azi is not None else 0.0
        sol_elev = _raw_elev if _raw_elev is not None else 0.0

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
        elif self.is_awning_cover:
            cover_data = AdaptiveHorizontalCover(
                logger=self.logger,
                sol_azi=sol_azi,
                sol_elev=sol_elev,
                sun_data=sun_data,
                config=config,
                vert_config=self._config_service.get_vertical_data(options),
                horiz_config=self._config_service.get_horizontal_data(options),
            )
        elif self.is_tilt_cover:
            cover_data = AdaptiveTiltCover(
                logger=self.logger,
                sol_azi=sol_azi,
                sol_elev=sol_elev,
                sun_data=sun_data,
                config=config,
                tilt_config=self._config_service.get_tilt_data(options),
            )
        else:
            msg = f"Unsupported cover type: {self._cover_type!r}"
            raise ValueError(msg)
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

    def _read_climate_state(self, options) -> None:
        """Read all climate-related entities into self._weather_readings.

        This is the single call to ClimateProvider.read() for each update cycle.
        It reads temperature sensors, presence, weather, lux, irradiance, and
        cloud coverage.  All pipeline handlers (ClimateHandler, CloudSuppressionHandler)
        consume the result via snapshot.climate_readings.

        NOTE: If ClimateProvider.read() gains new keyword parameters, they MUST
        also be wired here from the corresponding options key.  The coordinator
        wiring test (test_coordinator_climate_wiring.py) will catch any mismatch.
        """
        cloud_suppression_enabled = bool(options.get(CONF_CLOUD_SUPPRESSION, False))
        self._weather_readings = self._climate_provider.read(
            temp_entity=options.get(CONF_TEMP_ENTITY),
            outside_entity=options.get(CONF_OUTSIDETEMP_ENTITY),
            presence_entity=options.get(CONF_PRESENCE_ENTITY),
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

    def _read_custom_position_sensor_states(
        self, options
    ) -> list[tuple[str, bool, int, int]]:
        """Read custom position sensor states from HA into an ordered list.

        Returns a list of (entity_id, is_on, position, priority) tuples for
        every slot that has both a sensor and a position configured.  Priority
        defaults to DEFAULT_CUSTOM_POSITION_PRIORITY (77) when not set so that
        existing installations behave identically to the previous release.
        """
        _sensor_keys = [
            (CONF_CUSTOM_POSITION_SENSOR_1, CONF_CUSTOM_POSITION_1, CONF_CUSTOM_POSITION_PRIORITY_1),
            (CONF_CUSTOM_POSITION_SENSOR_2, CONF_CUSTOM_POSITION_2, CONF_CUSTOM_POSITION_PRIORITY_2),
            (CONF_CUSTOM_POSITION_SENSOR_3, CONF_CUSTOM_POSITION_3, CONF_CUSTOM_POSITION_PRIORITY_3),
            (CONF_CUSTOM_POSITION_SENSOR_4, CONF_CUSTOM_POSITION_4, CONF_CUSTOM_POSITION_PRIORITY_4),
        ]
        result = []
        for sensor_key, pos_key, pri_key in _sensor_keys:
            sensor = options.get(sensor_key)
            position = options.get(pos_key)
            if sensor and position is not None:
                is_on = bool(
                    (state := self.hass.states.get(sensor))
                    and state.state == "on"
                )
                priority = int(options.get(pri_key) or DEFAULT_CUSTOM_POSITION_PRIORITY)
                result.append((sensor, is_on, int(position), priority))
        return result

    def build_diagnostic_data(self) -> dict:
        """Build diagnostic data from current coordinator state."""
        result = self._pipeline_result
        ctx = DiagnosticContext(
            pos_sun=self.pos_sun,
            cover=self._cover_data,
            pipeline_result=result,
            climate_mode=self._climate_mode,
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
        """Final cover position after pipeline, interpolation, and inverse_state transforms.

        The pipeline always runs so _pipeline_result is always set.  Safety
        override handlers (ForceOverride, WeatherOverride) set
        bypass_auto_control=True on their result, which causes their position
        to be returned directly — bypassing interpolation and inverse_state —
        even when automatic_control is OFF or outside the time window.
        """
        # Safety overrides take full precedence — skip post-processing transforms.
        if self._pipeline_bypasses_auto_control:
            return self._pipeline_result.position

        state = self._pipeline_result.position

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

        # interpolate_position() returns numpy float64; inverse_state() returns int.
        # Always coerce to plain Python int so sensors/diagnostics never see a float.
        return int(round(state))

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
    def motion_control(self):
        """Motion control toggle — delegates to ToggleManager."""
        return self._toggles.motion_control

    @motion_control.setter
    def motion_control(self, value):
        """Set motion control toggle."""
        self._toggles.motion_control = value

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
        return self._pipeline_result.bypass_auto_control

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
        """Check time window transitions — delegates to TimeWindowManager.

        When the operational window closes (active→inactive transition) and
        CONF_RETURN_SUNSET is enabled, force-sends the current effective default
        position (which may be sunset_pos if in the astronomical sunset window)
        to all covers.  The command bypasses all gate checks so covers move
        immediately regardless of delta/time thresholds.
        """

        async def _on_window_closed() -> None:
            """Force-send effective default when end time is reached."""
            # Always clear stale daytime targets when the window closes so
            # reconciliation cannot resend them overnight.  Safety targets
            # (placed via force=True) are preserved.
            self._cmd_svc.clear_non_safety_targets()
            if not self._track_end_time:
                return
            options = self.config_entry.options
            # Compute the current effective default (may already be sunset_pos)
            h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
            sunset_pos_cfg = options.get(CONF_SUNSET_POS)
            sunset_off = int(options.get(CONF_SUNSET_OFFSET) or 0)
            sunrise_off = int(
                options.get(CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET) or 0)
            )
            cover_data = self.get_blind_data(options=options)
            effective_pos, is_sunset = compute_effective_default(
                h_def=h_def,
                sunset_pos=sunset_pos_cfg,
                sun_data=cover_data.sun_data,
                sunset_off=sunset_off,
                sunrise_off=sunrise_off,
            )
            pos_to_send = (
                inverse_state(effective_pos) if self._inverse_state else effective_pos
            )
            self.logger.info(
                "End time reached — force-sending effective default %s%% "
                "(sunset_active=%s) to %s cover(s)",
                pos_to_send,
                is_sunset,
                len(self.entities),
            )
            for cover_entity in self.entities:
                ctx = self._build_position_context(cover_entity, options, force=True)
                await self._cmd_svc.apply_position(
                    cover_entity, pos_to_send, "end_time_default", context=ctx
                )
            # Trigger a normal refresh so sensor state and diagnostics update
            await self.async_refresh()

        await self._time_mgr.check_transition(
            track_end_time=self._track_end_time,
            refresh_callback=_on_window_closed,
        )

    def _check_sun_validity_transition(self) -> bool:
        """Check if sun validity state has changed from False to True.

        Returns True if sun just came into field of view, indicating
        covers should immediately reposition regardless of delta checks.
        """
        # Need cover data to check sun validity
        if self._cover_data is None:
            return False

        current_sun_valid = self._cover_data.direct_sun_valid

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

        # Stop cover command service reconciliation timer
        self._cmd_svc.stop()

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager and inverse_state have been moved to managers/manual_override.py
# They are re-imported above to maintain backward compatibility.
