"""The Coordinator for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt
import dataclasses
import json
import pathlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .forecast import Forecast

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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_types import RuntimeConfig
from .helpers import (
    compute_effective_default,
    get_datetime_from_str,
    get_safe_state,
    is_entity_active,
    motion_entities,
    state_attr,
)
from .config_context_adapter import ConfigContextAdapter
from .cover_types import CoverTypePolicy, get_policy
from .services.configuration_service import ConfigurationService
from .const import (
    _LOGGER,
    COMMAND_GRACE_PERIOD_SECONDS,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_CLIMATE_MODE,
    CONF_CLOUDY_POSITION,
    CONF_DEBUG_CATEGORIES,
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DRY_RUN,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
    CONF_MANUAL_IGNORE_EXTERNAL,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_OVERRIDE_STRATEGY,
    CONF_MANUAL_THRESHOLD,
    CONF_MY_POSITION_VALUE,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_RETURN_SUNSET,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_TRANSIT_TIMEOUT,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_MANUAL_OVERRIDE_STRATEGY,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    DOMAIN,
    LOGGER,
    POSITION_TOLERANCE_PERCENT,
    STARTUP_GRACE_PERIOD_SECONDS,
)
from .diagnostics.builder import DiagnosticContext, DiagnosticsBuilder
from .diagnostics.event_buffer import EventBuffer
from .managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_special_positions,
)
from .managers.grace_period import GracePeriodManager
from .managers.manual_override import (
    AdaptiveCoverManager,
    DetectorConfig,
    get_detector,
    inverse_state,
)
from .managers.motion import MotionManager
from .managers.weather import WeatherManager
from .managers.time_window import TimeWindowManager
from .managers.toggles import ToggleManager
from .position_utils import interpolate_position
from .pipeline.handlers import (
    ManualOverrideHandler,
    build_handlers,
)
from .pipeline.floors import effective_floor, gather_active_floors
from .pipeline.registry import PipelineRegistry
from .pipeline.snapshot_builder import PipelineSnapshotBuilder
from .const import ControlMethod
from .state.climate_provider import ClimateProvider, ClimateReadings
from .state.cover_provider import CoverProvider
from .state.snapshot import CoverStateSnapshot, SunSnapshot
from .state.sun_provider import SunProvider
from .state.window_transition_tracker import WindowTransitionTracker

_MANIFEST_VERSION: str = json.loads(
    (pathlib.Path(__file__).parent / "manifest.json").read_text()
)["version"]


def _read_time_entity(hass: HomeAssistant, entity_id: str | None) -> dt.datetime | None:
    """Read an entity whose state is an ISO-8601 datetime.

    Returns naive-local datetime on success; None if entity_id is None,
    the entity is unavailable, or the state cannot be parsed.
    """
    if entity_id is None:
        return None
    raw = get_safe_state(hass, entity_id)
    if raw is None:
        return None
    try:
        return get_datetime_from_str(raw)
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not parse time entity %s state %r as datetime",
            entity_id,
            raw,
        )
        return None


@dataclass
class StateChangedData:
    """StateChangedData class."""

    entity_id: str
    old_state: State | None
    new_state: State | None


@dataclass
class AdaptiveCoverData:
    """AdaptiveCoverData class.

    Mutates each coordinator update cycle. ``position_forecast`` is the
    one field that is NOT computed inside ``_async_update_data`` — it's
    refreshed on a slow background cadence by ``async_recompute_forecast``
    via the executor (see issue #437), and rolls forward between cycles.
    """

    climate_mode_toggle: bool
    states: dict
    attributes: dict
    diagnostics: dict | None = None
    position_forecast: Forecast | None = None


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
        self._policy: CoverTypePolicy = get_policy(self._cover_type)
        self._climate_mode = self.config_entry.options.get(CONF_CLIMATE_MODE, False)
        self._inverse_state = self.config_entry.options.get(CONF_INVERSE_STATE, False)
        self._inverse_tilt = self.config_entry.options.get(CONF_INVERSE_TILT, False)
        self._use_interpolation = self.config_entry.options.get(CONF_INTERP, False)
        self._track_end_time = self.config_entry.options.get(CONF_RETURN_SUNSET)
        # Toggle state manager (switch entities delegate here)
        self._toggles = ToggleManager()
        self._toggles.switch_mode = bool(self._climate_mode)
        self._sun_end_time = None
        self._sun_start_time = None
        self._sun_start_position: dict[str, float] | None = None
        self._sun_end_position: dict[str, float] | None = None
        self.manual_reset = self.config_entry.options.get(
            CONF_MANUAL_OVERRIDE_RESET, False
        )
        self.manual_duration = self.config_entry.options.get(
            CONF_MANUAL_OVERRIDE_DURATION
        ) or {"hours": 2}
        self.manual_ignore_external = self.config_entry.options.get(
            CONF_MANUAL_IGNORE_EXTERNAL, False
        )
        self.state_change = False
        self.cover_state_change = False
        self.first_refresh = False
        self._last_state_change_entity: str | None = None
        # Set to True when the coordinator is created during a config-entry reload
        # (HA already running) vs. a cold HA boot.  On reload, first-refresh dispatch
        # is suppressed for non-safety handlers to avoid disturbing covers that the
        # user has manually positioned.  Cleared after first refresh.
        self._is_reload: bool = False
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
        # Initialised here so coordinator.entities is always defined, even
        # before the first refresh.  Entity state-writes during platform setup
        # (which run concurrently with first_refresh) would otherwise hit an
        # AttributeError if they reference this attribute before _update_options
        # runs for the first time.  The refresh path overwrites this each cycle.
        self.entities = self.config_entry.options.get(CONF_ENTITIES, [])
        # Cover engine object — populated at start of each update cycle
        self._cover_data = None

        # Shared diagnostic ring buffer — owned here, injected into all writers
        self._event_buffer = EventBuffer(
            maxlen=self.config_entry.options.get(
                CONF_DEBUG_EVENT_BUFFER_SIZE, DEFAULT_DEBUG_EVENT_BUFFER_SIZE
            )
        )

        self.manager = AdaptiveCoverManager(
            self.hass,
            self.manual_duration,
            self.logger,
            event_buffer=self._event_buffer,
            detector=get_detector(
                self.config_entry.options.get(CONF_MANUAL_OVERRIDE_STRATEGY)
                or DEFAULT_MANUAL_OVERRIDE_STRATEGY,
                self._make_detector_config(self.config_entry.options),
            ),
        )
        self.ignore_intermediate_states = self.config_entry.options.get(
            CONF_MANUAL_IGNORE_INTERMEDIATE, False
        )
        # Grace period management (command + startup)
        self._grace_mgr = GracePeriodManager(
            logger=self.logger,
            command_grace_seconds=COMMAND_GRACE_PERIOD_SECONDS,
            startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
            event_buffer=self._event_buffer,
        )
        # Motion control tracking
        self._motion_mgr = MotionManager(
            hass=self.hass, logger=self.logger, event_buffer=self._event_buffer
        )
        # Weather override tracking
        self._weather_mgr = WeatherManager(
            hass=self.hass, logger=self.logger, event_buffer=self._event_buffer
        )
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

        # Pipeline snapshot builder — owns the HA reads + assembly for each
        # PipelineSnapshot.  Coordinator drives it once per cycle in
        # _calculate_cover_state and again from async_apply_user_position for
        # the preemption check.
        self._snapshot_builder = PipelineSnapshotBuilder(
            hass=self.hass,
            logger=self.logger,
            climate_provider=self._climate_provider,
            toggles=self._toggles,
            policy=self._policy,
            config_service=self._config_service,
        )

        # Current state snapshot (built at start of each update cycle)
        self._snapshot: CoverStateSnapshot | None = None

        # Track force override state across update cycles so we can detect
        # the release transition and bypass time/position delta gates.
        self._prev_force_override_active: bool = False

        # Per-sensor on/off state from last cycle.  Mirrors
        # _prev_force_override_active so a custom-position sensor that flips
        # off can also force a return to the calculated position regardless of
        # which lower-priority handler now wins.  Keyed by sensor entity_id.
        self._prev_custom_position_sensors_active: dict[str, bool] = {}

        # Diagnostics builder (extracted from coordinator)
        self._diagnostics_builder = DiagnosticsBuilder()

        # Track position explanation for change detection logging
        self._last_position_explanation: str = ""

        # Built once and reused for both the command-service construction
        # (position_tolerance) and the late policy.attach below.
        _rc_attach = RuntimeConfig.from_options(self.config_entry.options)

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
            position_tolerance=_rc_attach.tracking.position_tolerance,
            transit_timeout_seconds=self.config_entry.options.get(CONF_TRANSIT_TIMEOUT)
            or DEFAULT_TRANSIT_TIMEOUT_SECONDS,
            on_tick=self._check_time_window_transition,
            event_buffer=self._event_buffer,
            # Routes manual-override classifier debug lines through the
            # coordinator's debug-categories gate (INFO when debug_mode +
            # category enabled, otherwise DEBUG).
            debug_log=self._debug_log,
            # Clock the post-command window for time-based override detectors.
            on_command_sent=self.manager.note_command_sent,
        )

        # Wire the manual-override engine's edge + origin seams once. Any
        # detection channel that flips a cover into manual override fires
        # on_engaged → discard the latched command target (issue #215/#216);
        # every current and future detector inherits this without coordinator
        # changes. The ACP-origin predicate lets detectors distinguish
        # ACP-issued context ids from genuine user actions.
        self.manager.set_transition_callbacks(on_engaged=self._cmd_svc.discard_target)
        self.manager.set_acp_context_predicate(self._cmd_svc.was_acp_position_context)

        # Late-bind cover-type policy dependencies (e.g. VenetianPolicy
        # constructs its DualAxisSequencer here once cmd_svc + grace_mgr are
        # available).  Default policies have a no-op attach.
        self._policy.attach(
            hass=self.hass,
            logger=self.logger,
            grace_mgr=self._grace_mgr,
            get_current_position=self._cmd_svc.get_current_position,
            set_commanded_position=self._cmd_svc.set_target,
            position_tolerance=POSITION_TOLERANCE_PERCENT,
            is_dry_run=lambda: self._cmd_svc.dry_run,
            get_state=lambda eid: getattr(self.hass.states.get(eid), "state", None),
            get_current_tilt_position=lambda eid: state_attr(
                self.hass, eid, "current_tilt_position"
            ),
            event_buffer=self._event_buffer,
            tilt_skip_above=_rc_attach.venetian.tilt_skip_above,
            venetian_mode=_rc_attach.venetian.venetian_mode,
            post_settle_hold_seconds=_rc_attach.venetian.post_settle_hold_seconds,
            backrotate_publish_lag_seconds=(
                _rc_attach.venetian.backrotate_publish_lag_seconds
            ),
            invert_tilt=lambda: self._inverse_tilt,
            get_min_change=lambda: self.min_change,
        )

        # Time window manager (start/end time checks)
        self._time_mgr = TimeWindowManager(
            hass=self.hass, logger=self.logger, event_buffer=self._event_buffer
        )

        # Window-transition tracker — owns sun-visibility and astronomical
        # sunset-window transition state (extracted from coordinator in Phase E).
        self._window_tracker = WindowTransitionTracker(
            hass=self.hass,
            logger=self.logger,
            event_buffer=self._event_buffer,
            effective_default_fn=self._compute_current_effective_default,
        )

        # Time of the last successful _async_update_data() completion.
        # HA's DataUpdateCoordinator only exposes last_update_success (bool);
        # we track the timestamp ourselves so diagnostics can report it.
        self._last_update_success_time: dt.datetime | None = None

        # Issue #437: forecast cache + scheduling.  The forecast is heavy
        # (~289-call astral walk × 49-sample window) and must NOT run inline
        # on the event loop every state-write.  ``_position_forecast`` is
        # the live cache that ``_async_update_data`` promotes into
        # ``AdaptiveCoverData.position_forecast`` each cycle; the sensor
        # reads exclusively from there.  ``_forecast_unsub`` holds the
        # ``async_track_time_interval`` cancel handle.
        self._position_forecast: Forecast | None = None
        self._forecast_unsub: Callable[[], None] | None = None

    def _make_detector_config(self, options) -> DetectorConfig:
        """Build the manual-override DetectorConfig from raw options.

        Single source of truth shared by manager construction and
        ``update_config`` so the detector and the engine never drift.
        """
        return DetectorConfig(
            manual_threshold=options.get(CONF_MANUAL_THRESHOLD),
            command_window_seconds=float(
                options.get(CONF_TRANSIT_TIMEOUT) or DEFAULT_TRANSIT_TIMEOUT_SECONDS
            ),
            reset=options.get(CONF_MANUAL_OVERRIDE_RESET, False),
            duration=options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2},
            ignore_external=options.get(CONF_MANUAL_IGNORE_EXTERNAL, False),
        )

    # --- Property delegates for CoverCommandService state ---

    @property
    def last_cover_action(self) -> dict:
        """Delegate to CoverCommandService.last_cover_action."""
        return self._cmd_svc.last_cover_action

    @property
    def last_skipped_action(self) -> dict:
        """Delegate to CoverCommandService.last_skipped_action."""
        return self._cmd_svc.last_skipped_action

    @property
    def is_force_override_active(self) -> bool:
        """Check if any force override sensor is active.

        Returns:
            True if any configured force override sensor is in "on" state

        """
        return any(
            self._snapshot_builder.read_force_sensors(
                self.config_entry.options
            ).values()
        )

    def _is_glare_zone_enabled(self, idx: int) -> bool:
        """Return the per-instance glare-zone switch for ``zone idx``.

        The coordinator owns the dynamic ``glare_zone_N`` attributes the
        switch platform writes to.  Exposed as a callable so the snapshot
        builder can read them without reaching back into ``self``.
        """
        return getattr(self, f"glare_zone_{idx}", True)

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

    def _debug_log(self, category: str, msg: str, *args) -> None:
        """Log at INFO when debug_mode is on and category is enabled, else DEBUG."""
        options = self.config_entry.options
        if options.get(CONF_DEBUG_MODE) and category in options.get(
            CONF_DEBUG_CATEGORIES, []
        ):
            self.logger.info(msg, *args)
        else:
            self.logger.debug(msg, *args)

    async def async_config_entry_first_refresh(self) -> None:
        """Config entry first refresh."""
        self.first_refresh = True
        await super().async_config_entry_first_refresh()
        self.logger.debug("Config entry first refresh")
        # Start startup grace period to prevent false manual override detection
        self._start_startup_grace_period()
        # Start cover command service reconciliation timer
        self._cmd_svc.start()
        # Schedule the position-forecast background recompute.  We do this
        # AFTER super().async_config_entry_first_refresh() so the initial
        # forecast lands on a populated AdaptiveCoverData.  The compute itself
        # runs as a background task so setup never waits for it (issue #437).
        self._start_forecast_scheduler()

    def _start_forecast_scheduler(self) -> None:
        """Kick off the initial forecast compute + periodic recompute timer.

        Idempotent: calling this twice (e.g. on reload) reuses the existing
        unsubscribe handle if already set.  Imported lazily so the import
        graph at coordinator init time stays minimal.
        """
        from homeassistant.helpers.event import async_track_time_change

        from .const import FORECAST_RECOMPUTE_INTERVAL_MIN

        if self._forecast_unsub is not None:
            return  # already scheduled

        # Fire the initial compute as a background task so the rest of
        # entry setup doesn't wait on the executor.  Use the config-entry
        # task helper (not hass.async_create_background_task): it ties the
        # task to the entry, which keeps a hard reference until the
        # coroutine completes.  hass.async_create_background_task can race
        # with the GC when called from a sync timer callback — tasks were
        # being destroyed before reaching their first await, surfacing as
        # "Task was destroyed but it is pending!" in the HA log.
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_recompute_forecast(),
            name="acp_initial_forecast",
        )

        # Periodic recompute aligned to wall-clock 5-minute boundaries
        # (:00, :05, :10, …) so every entry's forecast attribute updates
        # in lockstep — the dashboard sees one synchronised refresh
        # instead of staggered per-entry ticks.  The forecast is a
        # 12-hour outlook, so refreshing more often than every few
        # minutes adds no information.  The timer fires a background
        # task each tick to keep the event loop free.
        #
        # ``@callback`` is required: without it HA classifies the plain
        # ``def`` as ``HassJobType.Executor`` and dispatches the tick to
        # a worker thread, where ``loop.create_task(..., eager_start=True)``
        # raises ``RuntimeError: loop is not the running loop`` and the
        # recompute silently never happens.
        @callback
        def _tick(_now: dt.datetime) -> None:
            self.config_entry.async_create_background_task(
                self.hass,
                self.async_recompute_forecast(),
                name="acp_periodic_forecast",
            )

        self._forecast_unsub = async_track_time_change(
            self.hass,
            _tick,
            minute=range(0, 60, FORECAST_RECOMPUTE_INTERVAL_MIN),
            second=0,
        )

    async def async_recompute_forecast(self) -> None:
        """Refresh ``coordinator.data.position_forecast`` via an executor job.

        Issue #437: the underlying :func:`build_forecast_for_coord` walks
        289 solar samples and constructs a fresh ``AdaptiveGeneralCover``
        per tick — running this on the event loop blocks for hundreds of
        milliseconds on ARM hosts and trips HA's bootstrap-stage-2
        timeout. Offloading to the executor keeps the loop responsive.

        Failures are swallowed: the sensor degrades gracefully to ``None``
        when the forecast cannot be computed (same contract the pre-fix
        ``_safe_forecast`` wrapper offered).
        """
        from .forecast import build_forecast_for_coord

        try:
            forecast = await self.hass.async_add_executor_job(
                build_forecast_for_coord, self
            )
        except Exception:  # noqa: BLE001 — defensive degradation
            forecast = None
        self._position_forecast = forecast
        if self.data is not None:
            self.data = replace(self.data, position_forecast=forecast)
            self.async_update_listeners()

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
        self._last_state_change_entity = entity_id
        self.state_change = True
        await self.async_refresh()

    async def async_check_cover_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Detect manual overrides when a managed cover changes position."""
        self.logger.debug("Cover state change")
        data = event.data
        if data["old_state"] is None:
            # Issue #342: a cover transitioning from "not registered yet" to a
            # real state is the cue that the platform finished loading. The
            # initial first_refresh likely skipped this entity with
            # cover_unavailable; recompute now that it's reachable.
            new_state = data["new_state"]
            if new_state is not None and new_state.state not in (
                "unavailable",
                "unknown",
            ):
                self.logger.debug(
                    "Cover %s came online (%s); requesting refresh",
                    data["entity_id"],
                    new_state.state,
                )
                await self.async_request_refresh()
            else:
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

    async def async_check_cover_service_call(self, event: Event) -> None:
        """Detect user-initiated cover.stop_cover and start manual override.

        Listens to EVENT_CALL_SERVICE for ``cover.stop_cover`` on tracked
        entities. If the call was NOT originated by ACP (per
        ``_cmd_svc.was_acp_stop_context``) and a ``my_position_value`` is
        configured, the cover is flagged as manually overridden.

        This path covers non-position-capable covers (e.g. Somfy RTS) where
        pressing STOP moves to the hardware "My" preset without ever reporting
        a new position — the normal state-change detection is blind to it.
        """
        data = event.data
        if data.get("domain") != "cover" or data.get("service") != "stop_cover":
            return

        service_data = data.get("service_data") or {}
        raw_entity_id = service_data.get("entity_id")
        if raw_entity_id is None:
            return

        if isinstance(raw_entity_id, str):
            called_entities = {raw_entity_id}
        else:
            called_entities = set(raw_entity_id)

        tracked = called_entities & set(self.entities)
        if not tracked:
            return

        # Skip if ACP originated this stop_cover call.
        if event.context and self._cmd_svc.was_acp_stop_context(event.context.id):
            self.logger.debug(
                "async_check_cover_service_call: ignoring ACP-originated stop_cover "
                "(context %s)",
                event.context.id,
            )
            return

        if not self.manual_toggle or not self.automatic_control:
            self._manual_gate_closed_log("service_call", list(tracked))
            return

        # When manual_ignore_external is on, treat external stop_cover calls
        # the same as external set_cover_position — only ACP-routed commands
        # engage manual override.
        if self.manual_ignore_external:
            self.logger.debug(
                "async_check_cover_service_call: ignoring external stop_cover on %s "
                "(manual_ignore_external on)",
                tracked,
            )
            return

        my_position_value = self.config_entry.options.get(CONF_MY_POSITION_VALUE)
        if my_position_value is None:
            self.logger.debug(
                "async_check_cover_service_call: user stop_cover on %s but "
                "my_position_value not configured — skipping manual override",
                tracked,
            )
            return

        for entity_id in tracked:
            # On the not-manual→manual edge the manager fires on_engaged →
            # discard_target (issue #215/#216); see set_transition_callbacks.
            self.manager.handle_stop_service_call(
                entity_id,
                int(my_position_value),
                self._cmd_svc.is_waiting_for_target,
            )
            # Update target so the next reconciliation compares against
            # My rather than the stale calculated state.
            self._cmd_svc.set_target(entity_id, int(my_position_value))

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
            if not self._weather_mgr.is_weather_override_active:
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
            self._reconcile_weather_override()

    async def async_check_motion_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle occupancy-source changes: immediate on detection, debounced on stop.

        Evaluates the changed entity through the same domain-aware
        ``is_entity_active`` predicate the manager uses, so motion sensors
        (state "on") and media players (any non-off state) are handled
        uniformly without branching on literal state strings here.
        """
        data = event.data
        entity_id = data["entity_id"]
        new_state = data["new_state"]

        if new_state is None:
            return

        self.logger.debug(
            "Occupancy source %s state changed to %s",
            entity_id,
            new_state.state,
        )

        if is_entity_active(self.hass, entity_id):
            # Occupancy detected - immediate response.
            # Returns True if timeout was active (expired) or pending (task
            # still running), so we refresh in both cases, not just when the
            # timeout had already fully expired.
            needs_refresh = self._motion_mgr.record_motion_detected()

            if needs_refresh:
                self.logger.info("Motion detected - resuming automatic sun positioning")
                self.state_change = True
                await self.async_refresh()

        elif not self.is_motion_detected:
            # This source cleared and no other source is active - start timeout.
            self._start_motion_timeout()
        else:
            self.logger.debug(
                "Occupancy cleared on %s but another source still active — timeout not started",
                entity_id,
            )

    def process_entity_state_change(self):
        """Check if cover position change was user-initiated (manual override detection).

        Thin shim over :meth:`CoverCommandService.classify_state_change` —
        Phase F relocated the body into ``managers/cover_command/state_classifier.py``.
        The ``_target_just_reached`` set is passed by reference so the
        classifier mutates the same object that
        :meth:`async_handle_cover_state_change` reads and clears later in
        the same event lifecycle.
        """
        self._cmd_svc.classify_state_change(
            self.state_change_data,
            ignore_intermediate_states=self.ignore_intermediate_states,
            target_just_reached=self._target_just_reached,
            grace_mgr=self._grace_mgr,
        )

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

    def _manual_gate_closed_log(
        self, where: str, entity_ids: list[str] | None = None
    ) -> None:
        """Emit a single debug line when the manual-override detection gate is closed."""
        self.logger.debug(
            "manual override detection gate closed at %s "
            "(manual_toggle=%s, automatic_control=%s) — skipping %s",
            where,
            self.manual_toggle,
            self.automatic_control,
            entity_ids if entity_ids is not None else "<no entities>",
        )
        self._event_buffer.record(
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "manual_override_gate_closed",
                "where": where,
                "manual_toggle": self.manual_toggle,
                "automatic_control": self.automatic_control,
                "entity_ids": entity_ids,
            }
        )

    def _check_initial_motion_state(self) -> None:
        """Initialize motion state from current sensor readings at startup/reload.

        Reads each configured motion entity (sensors and/or media players) and
        sets the appropriate state so the Motion Status sensor reflects reality
        immediately instead of showing ``waiting_for_data`` until the first
        state change event arrives.

        - Any entity **on/active** → record_motion_detected() sets
          last_motion_time so the sensor shows ``motion_detected``.
        - All entities **off** → set_no_motion() marks the timeout active so
          the sensor shows ``no_motion``.
        """
        if not motion_entities(self.config_entry.options):
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

    def _recover_weather_override_on_restart(self) -> None:
        """Restore weather override state after HA restart.

        On restart, WeatherManager._override_active resets to False. If conditions
        are still active, no state-change event fires, so async_check_weather_state_change
        never sees the active→clear transition and never starts the clear-delay timer.
        Restoring the flag here ensures the normal clear-delay path runs correctly.
        """
        if not self._weather_mgr.configured_sensors:
            return
        if self._weather_mgr.is_any_condition_active:
            self.logger.info(
                "Startup: weather conditions active — restoring override state "
                "so clear-delay timeout will fire when conditions end"
            )
            self._weather_mgr.record_conditions_active()

    def _reconcile_weather_override(self) -> None:
        """Self-heal a stuck weather override flag.

        If the override flag is True but no condition is currently active and
        no clear-delay timer is running, start the clear-delay timer. This
        covers missed state-change events (e.g. HA restart race, event bus drop).
        """
        if self._weather_mgr.reconcile() == "should_start_timeout":
            self.logger.info(
                "Weather reconciliation: override active but conditions clear "
                "and no timer running — starting clear-delay timeout"
            )
            self._start_weather_timeout()

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
        self._weather_readings = self._snapshot_builder.read_climate(options)

        # Compute the effective default position from astronomical sunset/sunrise.
        # This is the single source of truth — all pipeline handlers use it via
        # snapshot.default_position.  The sunset_pos is active when current time
        # is after (astronomical_sunset + sunset_offset) or before
        # (astronomical_sunrise + sunrise_offset).
        h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
        sunset_pos_cfg = options.get(CONF_SUNSET_POS)  # None when not configured
        effective_default, is_sunset_active = self._compute_current_effective_default(
            options, cover_data=cover_data
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

        snapshot = self._snapshot_builder.build(
            options,
            cover_data=cover_data,
            cover_type=self._cover_type,
            climate_readings=self._weather_readings,
            manual_override_active=self.manager.binary_cover_manual,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            in_time_window=self.check_adaptive_time,
            current_cover_position=self._compute_mean_cover_position(),
            is_glare_zone_enabled=self._is_glare_zone_enabled,
            effective_default=effective_default,
            is_sunset_active=is_sunset_active,
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
            configured_cloudy_pos=options.get(CONF_CLOUDY_POSITION),
        )

        # Cover-type policy hook: dual-axis covers (venetian) compose the
        # secondary-axis target here and append a synthetic decision-trace
        # step. Default policies return the result unchanged.
        self._pipeline_result = self._policy.post_pipeline_resolve(
            self._pipeline_result,
            logger=self.logger,
            sol_azi=cover_data.sol_azi,
            sol_elev=cover_data.sol_elev,
            sun_data=cover_data.sun_data,
            config=cover_data.config,
            config_service=self._config_service,
            options=options,
            cover=cover_data,
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
            start_pos, end_pos = await loop.run_in_executor(
                None, normal_cover.solar_times_with_position
            )
            if start_pos is None or end_pos is None:
                self._sun_start_time = None
                self._sun_end_time = None
                self._sun_start_position = None
                self._sun_end_position = None
            else:
                self._sun_start_time = start_pos[0]
                self._sun_end_time = end_pos[0]
                self._sun_start_position = {
                    "azimuth": start_pos[1],
                    "elevation": start_pos[2],
                }
                self._sun_end_position = {
                    "azimuth": end_pos[1],
                    "elevation": end_pos[2],
                }
            self.logger.debug(
                "Sun start time: %s, Sun end time: %s",
                self._sun_start_time,
                self._sun_end_time,
            )
            return self._sun_start_time, self._sun_end_time

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

        # Capture last cycle's per-sensor active map so we can detect a custom
        # position sensor flipping off (release edge of #365).
        prev_custom_position_sensors_active = dict(
            self._prev_custom_position_sensors_active
        )

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
                self.entities, self._policy
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

        # On first refresh after HA restart, restore the weather override flag BEFORE
        # the pipeline runs so the weather handler sees the correct state on cycle 1.
        # Without this, covers briefly dispatch to the sun-tracked position while
        # conditions are still active (flag was reset to False on coordinator init).
        if self.first_refresh:
            self._recover_weather_override_on_restart()

        # Self-heal stuck weather override (issue #255: missed state-change events)
        self._reconcile_weather_override()

        # Calculate cover state (pipeline runs with up-to-date override state)
        state = self._calculate_cover_state(cover_data, options)

        # Update prev state for next cycle (current force override state is now
        # captured in the snapshot we just built).
        self._prev_force_override_active = self.is_force_override_active

        # Same for custom-position sensors: stamp this cycle's on/off map so
        # next cycle can detect the on → off transition for the release edge.
        current_custom_position_sensors_active = {
            s.entity_id: s.is_on
            for s in self._snapshot_builder.read_custom_position_sensors(options)
        }
        self._prev_custom_position_sensors_active = (
            current_custom_position_sensors_active
        )

        # Set of sensors that transitioned on → off this cycle.  When the
        # triggering entity is one of these, force=True bypasses time/position
        # delta gates so covers return to the calculated position promptly.
        custom_position_released_entities = {
            eid
            for eid, was_on in prev_custom_position_sensors_active.items()
            if was_on and not current_custom_position_sensors_active.get(eid, False)
        }

        # Handle types of changes
        if self.state_change:
            await self.async_handle_state_change(
                state,
                options,
                prev_force_override,
                custom_position_released_entities,
            )
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
        self._cmd_svc.enabled = (
            self.enabled_toggle if self.enabled_toggle is not None else True
        )
        self._cmd_svc.dry_run = self.config_entry.options.get(CONF_DRY_RUN, False)

        # Update solar times
        start, end = await self._update_solar_times_if_needed(self._cover_data)

        # Build diagnostic data (always enabled)
        diagnostics = self.build_diagnostic_data()

        # Record successful update time (after build_diagnostic_data so the
        # diagnostic for this cycle reports the *previous* completed success).
        self._last_update_success_time = dt.datetime.now(dt.UTC)

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
                "start_position": self._sun_start_position,
                "end_position": self._sun_end_position,
                "control": self._pipeline_result.control_method.value,
                "sun_motion": self._cover_data.direct_sun_valid,
                "manual_override": self.manager.binary_cover_manual,
                "manual_list": self.manager.manual_controlled,
                "glare_active": glare_active,
                "held_position": self._pipeline_result.held_position,
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
            # Carry the last computed forecast forward across cycles; the
            # forecast recompute timer is the only writer (issue #437).
            position_forecast=self._position_forecast,
        )

    def _compute_mean_cover_position(self) -> int | None:
        """Return integer mean of current entity positions, or None if none are available.

        Used to populate PipelineSnapshot.current_cover_position so the
        MotionTimeoutHandler can hold covers at their current physical position
        in hold_position mode.
        """
        if self._snapshot is None:
            return None
        positions = [
            p
            for p in self._snapshot.cover_positions.values()
            if isinstance(p, int | float)
        ]
        if not positions:
            return None
        return int(round(sum(positions) / len(positions)))

    def _build_position_context(
        self,
        entity: str,
        options: dict,
        *,
        force: bool = False,
        is_safety: bool = False,
        bypass_auto_control: bool = False,
        sun_just_appeared: bool = False,
        use_my_position: bool = False,
    ) -> PositionContext:
        """Build a PositionContext for the given cover entity.

        Assembles all coordinator-level flags into the dataclass that
        CoverCommandService.apply_position() uses for gate checks.

        Args:
            entity: Cover entity ID
            options: Config entry options dict
            force: If True, delta/time/manual_override gate checks are bypassed.
                Use for any intentional non-solar reposition.  NOTE: force=True
                does NOT bypass auto_control_off — pass bypass_auto_control=True
                or is_safety=True for that.
            is_safety: If True, the target is classified as safety-critical
                (force override, weather) and will persist across window
                boundaries — reconciliation will resend it even when outside
                the active-hours window or with auto control off.  Must be
                kept independent of ``force``: override-clear and toggle
                actions need ``force=True`` (bypass gates) but
                ``is_safety=False`` (don't persist past the window).
            bypass_auto_control: If True, the auto_control_off gate is bypassed
                for this one-shot call without classifying the target as a
                safety target.  Use only for sanctioned transition actions
                (e.g. switch return-to-default at the moment auto_control
                toggles off).  Not used by the regular update loop.
            sun_just_appeared: Pre-computed sun transition flag. Call
                ``_check_sun_validity_transition()`` once before a multi-entity
                loop and pass the result here so the stateful transition check
                fires exactly once per update cycle.
            use_my_position: If True, ORed into the context flag that routes
                non-position-capable covers through ``stop_cover`` instead of
                open/close.  Caller-supplied value is ORed with the pipeline
                result's ``use_my_position`` so either source can enable it.

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
            is_safety=is_safety,
            bypass_auto_control=bypass_auto_control,
            use_my_position=(
                use_my_position
                or (
                    self._pipeline_result.use_my_position
                    if self._pipeline_result
                    else False
                )
            ),
            policy=self._policy,
            **self._policy.position_context_overrides(self._pipeline_result),
        )

    async def _dispatch_to_cover(
        self,
        cover: str,
        state: int,
        reason: str,
        ctx,
    ) -> tuple[str, str] | None:
        """Send a position command or record a hold-mode skip.

        When the active pipeline result has skip_command=True (hold_position
        mode during motion timeout), no command is issued. A motion_hold skip
        record is written instead so diagnostics show why the cover didn't move.
        All other callers (forced transitions, override clears, window events)
        bypass this helper and call apply_position directly so they are never
        blocked by hold mode.
        """
        if self._pipeline_result is not None and self._pipeline_result.skip_command:
            self._cmd_svc.record_skipped_action(
                cover,
                "motion_hold",
                state,
                trigger=reason,
                inverse_state=self._inverse_state,
                extras={
                    "held_position": self._pipeline_result.position,
                    "would_be_position": state,
                    "motion_timeout_mode": "hold_position",
                },
            )
            return None
        return await self._cmd_svc.apply_position(cover, state, reason, context=ctx)

    async def _async_send_after_override_clear(
        self,
        state: int,
        options: dict,
        *,
        entities: list[str] | None = None,
        trigger: str = "manual_override_cleared",
    ) -> set[str]:
        """Send the pipeline position after a manual override clears.

        Single authoritative path for both the auto-expiry timer and the reset
        button.  All gate checks live here so neither caller needs to duplicate
        them.

        **Time-window guard:** Outside the active-hours window the integration
        has no business repositioning covers.  The normal update cycle sends the
        correct position when the window reopens.

        **Automatic-control guard:** When Automatic Control is OFF the cover
        must stay wherever the user left it.

        Args:
            state: Post-reset pipeline position (computed without the override).
            options: Config entry options dict.
            entities: Covers to target.  Defaults to ``self.entities`` (all
                covers), but the reset button supplies only the covers it just
                cleared so multi-cover instances are not accidentally moved.
            trigger: Reason string forwarded to ``apply_position`` and recorded
                in ``last_skipped_action``.  Defaults to
                ``"manual_override_cleared"`` (auto-expiry); the reset button
                passes ``"manual_reset"``.

        Returns:
            Set of entity_ids that were successfully sent to (``"sent"``
            outcome).  Callers use this to clear ``wait_for_target`` for
            entities that were gated or skipped.

        """
        target_covers = entities if entities is not None else list(self.entities)

        if not self.check_adaptive_time:
            self.logger.debug(
                "Manual override cleared for %s but outside active-hours window — "
                "skipping reposition (pipeline position was %s; will apply when "
                "window opens)",
                target_covers,
                state,
            )
            return set()

        if not self.automatic_control:
            self.logger.debug(
                "Manual override cleared for %s but automatic control is OFF — "
                "skipping reposition (pipeline position was %s)",
                target_covers,
                state,
            )
            return set()

        self.logger.debug(
            "Sending pipeline position %s after manual override cleared for %s",
            state,
            target_covers,
        )
        sun_just_appeared = self._check_sun_validity_transition()
        sent: set[str] = set()
        for cover in target_covers:
            ctx = self._build_position_context(
                cover, options, force=True, sun_just_appeared=sun_just_appeared
            )
            outcome, _ = await self._cmd_svc.apply_position(
                cover, state, trigger, context=ctx
            )
            if outcome == "sent":
                sent.add(cover)
        return sent

    async def async_handle_state_change(
        self,
        state: int,
        options,
        prev_force_override: bool = False,
        custom_position_released_entities: set[str] | None = None,
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

        ``custom_position_released_entities`` holds the set of custom-position
        sensors that flipped on → off this cycle.  When the triggering entity
        for this refresh is one of them, force=True is also passed so the
        return to the calculated position is not throttled (#365).
        """
        sun_just_appeared = self._check_sun_validity_transition()
        is_safety = self._pipeline_is_safety_handler
        force_override_released = (
            prev_force_override and not self.is_force_override_active
        )

        # Custom-position sensor release edge: the entity that triggered this
        # refresh just flipped off and a lower-priority handler (solar/default)
        # now wins, so _is_custom_position_sensor_trigger() returns False.
        # Mirrors force_override_released for the same reason: the time-delta
        # gate would otherwise drop the return-to-calculated command.  Short-
        # circuit when the set is empty so callers that bypass __init__ (e.g.
        # gate-matrix fixtures) don't need to wire _last_state_change_entity.
        # When the manual-override hold is the winner, a floor sensor releasing
        # must NOT take the release force-path — otherwise the override's
        # theoretical default (e.g. 90%) would be force-driven onto the cover.
        # The cover stays where the floor left it; the override keeps holding
        # the (now recomputed) physical position (#534).
        override_holding = (
            self._pipeline_result is not None
            and self._pipeline_result.control_method is ControlMethod.MANUAL
        )
        trigger_entity: str | None = None
        custom_position_released = False
        if custom_position_released_entities and not override_holding:
            trigger_entity = self._last_state_change_entity
            custom_position_released = (
                trigger_entity is not None
                and trigger_entity in custom_position_released_entities
            )

        # Outside the configured time window, only safety handlers (force
        # override, weather) are allowed to move covers.  All other handlers
        # (solar, climate, cloud, default) must not reposition covers before
        # the user's start time or after the end time.  The pipeline still
        # evaluates so diagnostics/sensor state remain correct.
        custom_position_sensor_triggered = self._is_custom_position_sensor_trigger()

        if (
            not self.check_adaptive_time
            and not is_safety
            and not force_override_released
        ):
            self.state_change = False
            self._last_state_change_entity = None
            self.logger.debug("Outside time window — skipping position update")
            return

        # A floor-clamp raised the winner this cycle (#534).  When manual
        # override holds the cover below an active floor, the clamped value is
        # already in cover-position space and must bypass the time/position
        # delta gates so the raise reaches the cover.
        floor_clamp = bool(
            self._pipeline_result is not None
            and self._pipeline_result.floor_clamp_applied
        )
        use_force = (
            is_safety
            or force_override_released
            or custom_position_sensor_triggered
            or custom_position_released
            or floor_clamp
        )
        if force_override_released:
            reason = "force_override_cleared"
            self.logger.debug(
                "Force override released — bypassing time/position delta gates "
                "to return to calculated position %s",
                state,
            )
        elif custom_position_released:
            reason = "custom_position_released"
            self.logger.debug(
                "Custom-position sensor %s released — bypassing time/position "
                "delta gates to return to calculated position %s",
                trigger_entity,
                state,
            )
        elif floor_clamp:
            reason = "floor_clamp"
            self.logger.debug(
                "Floor clamp active — bypassing time/position delta gates to "
                "raise cover to floor position %s",
                state,
            )
        else:
            reason = (
                self._pipeline_result.control_method.value
                if self._pipeline_bypasses_auto_control
                else "solar"
            )
        for cover in self.entities:
            ctx = self._build_position_context(
                cover,
                options,
                force=use_force,
                is_safety=is_safety,
                sun_just_appeared=sun_just_appeared,
            )
            await self._dispatch_to_cover(cover, state, reason, ctx)
        self.state_change = False
        self._last_state_change_entity = None
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

        # NB (issue #293): observation is not action.  When automatic_control
        # is OFF we still drain events so the user's manual response is recorded
        # and any latched target gets discarded via the existing
        # discard_target() call below.  Only manual_toggle=False (the user has
        # globally disabled manual override detection) short-circuits the loop.
        if not self.manual_toggle:
            self._manual_gate_closed_log("state_change", [e.entity_id for e in events])
            self.cover_state_change = False
            self.logger.debug("Cover state change handled")
            return

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

        # When manual_ignore_external is on, only ACP-routed commands (proxy
        # entity, set_position service) engage manual override — those use the
        # pre-emptive mark_user_command path inside async_apply_user_position
        # and never reach the detection paths below. Skip the whole loop, but
        # still drain _target_just_reached housekeeping so a later legitimate
        # move isn't misclassified.
        if self.manual_ignore_external:
            for event_data in events:
                self._target_just_reached.discard(event_data.entity_id)
            self.logger.debug(
                "Position changes for %s ignored (manual_ignore_external on; "
                "only ACP proxy/service commands engage manual override)",
                [e.entity_id for e in events],
            )
            self.cover_state_change = False
            return

        for event_data in events:
            entity_id = event_data.entity_id

            # User-context fast-path: when a cover state-change event carries
            # an HA Context whose id was NOT generated by ACP and whose user_id
            # is not None, a real user took action (HA dashboard, voice
            # assistant, etc.). Mark manual override directly. This is the only
            # reliable path for assumed-state and OPEN/CLOSE-only covers — the
            # numeric path in handle_state_change() can be defeated by races
            # where ACP's reconciliation counter-commands before the queued
            # event is drained, masking the user's input.
            new_state_obj = event_data.new_state
            ctx = getattr(new_state_obj, "context", None) if new_state_obj else None
            if (
                ctx is not None
                and ctx.user_id is not None
                and not self._cmd_svc.was_acp_position_context(ctx.id)
            ):
                handled = self.manager.handle_user_initiated_state_change(
                    entity_id,
                    new_state_obj,
                    self.manual_reset,
                    context_user_id=ctx.user_id,
                    context_id=ctx.id,
                )
                if handled:
                    # On the not-manual→manual edge the manager fires
                    # on_engaged → discard_target (issue #215/#216).
                    # Consume any pending target_just_reached flag so the
                    # numeric path doesn't fire later for the same entity.
                    self._target_just_reached.discard(entity_id)
                    continue

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

            # Use the recorded target if available (the actual sent position),
            # otherwise fall back to calculated state. Critical for open/close-only
            # covers where the calculated state gets transformed (via threshold)
            # to 0 or 100 before sending.
            recorded_target = self._cmd_svc.get_target(entity_id)
            expected_position = state if recorded_target is None else recorded_target

            secondary_axis_check = (
                self._policy.secondary_axis_check(self._pipeline_result, self._cmd_svc)
                if self._pipeline_result is not None
                else None
            )
            # On the not-manual→manual edge the manager fires on_engaged →
            # discard_target, so a freshly-detected override drops any
            # pre-existing integration target (incl. safety-tagged end-time
            # defaults) before reconciliation can resurrect it (issue #215/#216).
            self.manager.handle_state_change(
                event_data,
                expected_position,
                self._policy,
                self.manual_reset,
                self._cmd_svc.is_waiting_for_target,
                self.manual_threshold,
                secondary_axis_check=secondary_axis_check,
                is_in_command_grace=self._grace_mgr.is_in_command_grace_period,
                is_in_transit=self._cmd_svc._is_cover_in_transit,
            )

        self.cover_state_change = False
        self.logger.debug("Cover state change handled")

    async def async_handle_first_refresh(self, state: int, options):
        """Set target positions and send initial positioning commands after startup."""
        is_safety = self._pipeline_is_safety_handler

        # Outside the time window, only safety handlers (force override, weather)
        # are allowed to move covers on startup.  This prevents covers from
        # repositioning when HA restarts at midnight or another time outside the
        # configured operational window.
        if not self.check_adaptive_time and not is_safety:
            self.first_refresh = False
            self.logger.debug(
                "First refresh outside time window — skipping position update"
            )
            return

        if self._is_reload and not is_safety:
            self.first_refresh = False
            self._is_reload = False
            self.logger.debug(
                "First refresh during config-entry reload — "
                "skipping position update to avoid disturbing user-controlled covers"
            )
            return

        sun_just_appeared = self._check_sun_validity_transition()
        for cover in self.entities:
            if self.manager.is_cover_manual(cover):
                self.logger.debug(
                    "Startup: skipping position command for %s (manual override active/restored)",
                    cover,
                )
                continue
            ctx = self._build_position_context(
                cover,
                options,
                force=is_safety,
                is_safety=is_safety,
                sun_just_appeared=sun_just_appeared,
            )
            await self._dispatch_to_cover(cover, state, "startup", ctx)
        self.first_refresh = False
        self._is_reload = False
        self.logger.debug("First refresh handled")

    def _build_pipeline(self) -> PipelineRegistry:
        """Build the override pipeline from the registry of handler factories.

        Called once at coordinator initialisation.  Because the integration
        reloads fully on every options change (see ``_async_update_listener``
        in ``__init__.py``), this always sees the current configuration and
        there is no need to rebuild at runtime. Handler composition lives in
        ``pipeline.handlers.build_handlers`` (registry-driven), so adding a
        handler never touches the coordinator.
        """
        handlers = build_handlers(self.config_entry.options)
        self.logger.debug(
            "Pipeline built: %s",
            [(h.name, h.priority) for h in handlers],
        )
        self._handler_by_name = {h.name: h for h in handlers}
        return PipelineRegistry(
            handlers, event_buffer=getattr(self, "_event_buffer", None)
        )

    def _update_options(self, options):
        """Update coordinator options from config entry.

        Reads every option once into a typed ``RuntimeConfig`` snapshot and
        propagates each slice to the appropriate manager. Called on every
        coordinator update so option changes take effect on the next cycle.

        Args:
            options: Configuration options dictionary from config_entry.options

        """
        rc = RuntimeConfig.from_options(options)

        self.entities = rc.entities
        self.min_change = rc.tracking.min_change
        self.time_threshold = rc.tracking.time_threshold
        self.manual_reset = rc.manual_override.reset
        self.manual_duration = rc.manual_override.duration
        self.manual_ignore_external = rc.manual_override.ignore_external
        self.manual_threshold = rc.tracking.manual_threshold
        # Apply manual-override config to the engine + active detector at
        # runtime (auto-reset duration, threshold, command window) so changes
        # take effect without a reload. The detection *strategy* itself is
        # selected at construction; switching it requires a config-entry reload.
        self.manager.update_config(self._make_detector_config(options))
        self.start_value = rc.tracking.interp_start
        self.end_value = rc.tracking.interp_end
        self.normal_list = rc.tracking.interp_list
        self.new_list = rc.tracking.interp_list_new

        self._cmd_svc.update_threshold(rc.open_close_threshold)
        self._cmd_svc.update_position_tolerance(rc.tracking.position_tolerance)
        self._time_mgr.update_config(
            start_time=rc.time_window.start_time,
            start_time_entity=rc.time_window.start_time_entity,
            end_time=rc.time_window.end_time,
            end_time_entity=rc.time_window.end_time_entity,
        )
        self._motion_mgr.update_config(
            sensors=rc.motion.sensors,
            timeout_seconds=rc.motion.timeout_seconds,
            media_players=rc.motion.media_players,
        )
        self._weather_mgr.update_config(
            wind_speed_sensor=rc.weather.wind_speed_sensor,
            wind_direction_sensor=rc.weather.wind_direction_sensor,
            wind_speed_threshold=rc.weather.wind_speed_threshold,
            wind_direction_tolerance=rc.weather.wind_direction_tolerance,
            win_azi=rc.weather.win_azi,
            rain_sensor=rc.weather.rain_sensor,
            rain_threshold=rc.weather.rain_threshold,
            is_raining_sensor=rc.weather.is_raining_sensor,
            is_windy_sensor=rc.weather.is_windy_sensor,
            severe_sensors=rc.weather.severe_sensors,
            timeout_seconds=rc.weather.timeout_seconds,
        )

        event_buffer = getattr(self, "_event_buffer", None)
        if event_buffer is not None and rc.event_buffer_size != event_buffer.maxlen:
            event_buffer.resize(rc.event_buffer_size)

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
        return self._policy.build_calc_engine(
            logger=self.logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            config_service=self._config_service,
            options=options,
        )

    @property
    def check_adaptive_time(self):
        """Check if current time is within operational window — delegates to TimeWindowManager."""
        return self._time_mgr.is_active

    @property
    def after_start_time(self):
        """Check if current time is after start time — delegates to TimeWindowManager."""
        return self._time_mgr.after_start_time

    @property
    def window_explicitly_started(self):
        """Whether a real (non-blank) start time is configured and has passed.

        Delegates to TimeWindowManager. Distinct from ``after_start_time``
        (issue #492): feeds ``compute_effective_default`` so a blank start time
        does not suppress the overnight position after midnight.
        """
        return self._time_mgr.window_explicitly_started

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
        return self._cmd_svc.get_current_position(entity)

    def get_current_position(self, entity) -> int | None:
        """Public surface for reading a cover's current position.

        Delegates to :meth:`_get_current_position` so binary_sensor + tests
        that mock the private name keep working until the cover_command split
        replaces them in commit 4.
        """
        return self._get_current_position(entity)

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

    async def async_apply_user_position(
        self,
        entity_id: str,
        requested: int,
        *,
        trigger: str,
        options: dict | None = None,
        force: bool = False,
        use_my_position: bool = False,
    ) -> tuple[str, str]:
        """Apply a user-initiated position to a single cover.

        Single delegation point for any user-facing command (the
        ``set_position`` service, the opt-in proxy cover entity, the My
        Position button, future external triggers). Owns the min-mode floor
        clamp, the pipeline preemption check, manual-override engagement, and
        dispatch to ``CoverCommandService.apply_position``.

        Because every caller is an explicit user action, the dispatch always
        bypasses the ``auto_control_off`` gate (``bypass_auto_control=True``):
        "automatic control off" suppresses the integration's own sun tracking,
        not the user directly commanding a cover. This is distinct from the
        internal ``force=True`` callers (solar update, override-clear) that go
        through ``apply_position`` directly and stay blocked when auto control
        is off (issue #293).

        Default behavior (``force=False``): engages manual override and
        consults the pipeline. When a handler with priority strictly greater
        than :class:`ManualOverrideHandler` priority (force_override 100,
        weather 90, custom-position slots configured > 80) wins, the move is
        dropped and recorded via ``CoverCommandService.record_preempted_skip``.

        ``force=True``: legacy programmatic behavior — skip the pipeline
        preemption check and skip manual-override engagement. Used by the
        ``adaptive_cover_pro.set_position`` service when callers explicitly
        opt in.
        """
        opts = options if options is not None else self.config_entry.options
        snapshot = self._snapshot_builder.build(
            opts,
            cover_data=self._cover_data,
            cover_type=self._cover_type,
            climate_readings=self._weather_readings,
            manual_override_active=False,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            in_time_window=self.check_adaptive_time,
            current_cover_position=self._compute_mean_cover_position(),
            is_glare_zone_enabled=self._is_glare_zone_enabled,
        )
        floors = gather_active_floors(snapshot)
        effective_floor_pos, _ = effective_floor(floors)
        clamped = max(int(requested), effective_floor_pos)
        if clamped != requested:
            _LOGGER.info(
                "%s: requested %d clamped to %d (active min-mode floor)",
                trigger,
                requested,
                clamped,
            )
        else:
            _LOGGER.debug(
                "%s: requested %d, floor %d — no clamping needed",
                trigger,
                requested,
                effective_floor_pos,
            )

        if not force:
            result = self._pipeline.evaluate(snapshot)
            winner_step = next(
                (
                    s
                    for s in result.decision_trace
                    if s.matched and s.handler != "floor_clamp"
                ),
                None,
            )
            if winner_step is not None:
                winner_name = winner_step.handler
                winner_handler = self._handler_by_name.get(winner_name)
                winner_priority = (
                    winner_handler.priority if winner_handler is not None else 0
                )
                if winner_priority > ManualOverrideHandler.priority:
                    _LOGGER.info(
                        "user move on %s preempted by %s (priority %d > %d)",
                        entity_id,
                        winner_name,
                        winner_priority,
                        ManualOverrideHandler.priority,
                    )
                    self._cmd_svc.record_preempted_skip(
                        entity_id,
                        clamped,
                        trigger=trigger,
                        winner_name=winner_name,
                    )
                    return "skipped", f"preempted_by_{winner_name}"
            self.manager.mark_user_command(entity_id, reason=trigger)

        ctx = self._build_position_context(
            entity_id,
            opts,
            force=True,
            bypass_auto_control=True,
            use_my_position=use_my_position,
        )
        return await self._cmd_svc.apply_position(entity_id, clamped, trigger, ctx)

    async def async_apply_user_stop(
        self,
        entity_id: str,
        *,
        trigger: str,
    ) -> tuple[str, str]:
        """Apply a user-initiated stop to a single cover.

        Engages manual override (so the next cycle does not immediately
        counter-command the cover) then dispatches an ACP-context-stamped
        ``cover.stop_cover`` via :meth:`CoverCommandService.apply_user_stop`.
        Stop is unconditional — no pipeline preemption check.
        """
        self.manager.mark_user_command(entity_id, reason=trigger)
        return await self._cmd_svc.apply_user_stop(entity_id)

    def build_diagnostic_data(self) -> dict:
        """Build diagnostic data from current coordinator state."""
        result = self._pipeline_result

        # Live cover positions and capabilities
        cover_entities = self.entities or []
        _positions = self._cover_provider.read_positions(cover_entities, self._policy)
        _caps = self._cover_provider.read_all_capabilities(cover_entities)
        _covers = {
            eid: {
                "current_position": _positions.get(eid),
                "available": _positions.get(eid) is not None,
                "capabilities": (
                    dataclasses.asdict(_caps[eid]) if eid in _caps else None
                ),
            }
            for eid in cover_entities
        }

        # Per-entity manual override live state
        _now = dt.datetime.now(dt.UTC)
        _reset_secs = self.manager.reset_duration.total_seconds()
        _mo_entries = {}
        for eid in self.manager.covers:
            active = self.manager.manual_control.get(eid, False)
            started_at = self.manager.manual_control_time.get(eid)
            if started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=dt.UTC)
                elapsed = (_now - started_at).total_seconds()
                remaining = max(0, _reset_secs - elapsed)
                _mo_entries[eid] = {
                    "active": active,
                    "started_at": started_at.isoformat(),
                    "remaining_seconds": int(remaining),
                }
        _manual_override_state = {
            "reset_duration_seconds": int(_reset_secs),
            "tracked_covers": sorted(self.manager.covers),
            "entries": _mo_entries,
        }

        # Coordinator update health
        _last_success_time = self._last_update_success_time
        _last_exc = self.last_exception

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
            motion_timeout_active=self._motion_mgr.is_motion_timeout_active,
            motion_hold_active=(
                self._pipeline_result is not None
                and self._pipeline_result.skip_command
                and self._pipeline_result.control_method == ControlMethod.MOTION
            ),
            force_override_sensors=self.config_entry.options.get(
                CONF_FORCE_OVERRIDE_SENSORS, []
            ),
            force_override_position=self.config_entry.options.get(
                CONF_FORCE_OVERRIDE_POSITION, 0
            ),
            event_timeline=self._event_buffer.snapshot() or None,
            cover_command_state=self._cmd_svc.get_all_entity_state_snapshots() or None,
            debug_config={
                "dry_run": self.config_entry.options.get(CONF_DRY_RUN, False),
                "debug_mode": self.config_entry.options.get(CONF_DEBUG_MODE, False),
                "debug_categories": self.config_entry.options.get(
                    CONF_DEBUG_CATEGORIES, []
                ),
                "debug_event_buffer_size": self.config_entry.options.get(
                    CONF_DEBUG_EVENT_BUFFER_SIZE, DEFAULT_DEBUG_EVENT_BUFFER_SIZE
                ),
            },
            # New meta fields
            integration_version=_MANIFEST_VERSION,
            cover_type=self._cover_type,
            last_update_success=self.last_update_success,
            last_exception_repr=repr(_last_exc) if _last_exc is not None else None,
            last_update_success_time_iso=(
                _last_success_time.isoformat()
                if _last_success_time is not None
                else None
            ),
            update_interval_seconds=(
                self.update_interval.total_seconds()
                if self.update_interval is not None
                else None
            ),
            covers=_covers,
            manual_override_state=_manual_override_state,
            manual_toggle=self.manual_toggle,
            enabled_toggle=(
                self.enabled_toggle if self.enabled_toggle is not None else True
            ),
            primary_axis_suppression_counts=(
                self.manager.primary_axis_suppression_counts()
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

        Floor-clamped winners (issue #469): when the registry raises a
        non-bypass winner's position to a user-configured floor, the
        resulting value is already in cover-position space.  Interpolation
        and inverse_state would re-map a user-typed floor through the
        calibration curve and silently dispatch a different position, so
        the same short-circuit applies.
        """
        # Safety overrides and floor-clamped winners both produce positions
        # already in cover-position space — skip post-processing transforms.
        if (
            self._pipeline_bypasses_auto_control
            or self._pipeline_result.floor_clamp_applied
        ):
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
        CustomPositionHandler also sets this flag to defeat the auto_control
        gate, but is NOT a safety handler — see _pipeline_is_safety_handler.
        """
        return self._pipeline_result.bypass_auto_control

    @property
    def _pipeline_is_safety_handler(self) -> bool:
        """True only when the active pipeline result came from a genuine safety handler.

        Genuine safety handlers (ForceOverrideHandler, WeatherOverrideHandler)
        require force=True so wind/rain/force protection bypasses delta and time
        gates and always acts immediately.

        Other handlers that set bypass_auto_control=True (e.g.
        CustomPositionHandler) want to defeat the auto_control gate but must
        still be subject to the same-position short-circuit and delta/time gates
        so they don't issue redundant set_cover_position calls on every update
        cycle (issue #290).
        """
        if self._pipeline_result is None:
            return False
        return self._pipeline_result.control_method in (
            ControlMethod.FORCE,
            ControlMethod.WEATHER,
        )

    def _is_custom_position_sensor_trigger(self) -> bool:
        """Return True when this refresh was edge-triggered by a custom-position slot's own sensor.

        When a sensor toggles, the cover may be at a completely different position
        (e.g. solar tracking just moved it).  Passing force=True lets the command
        bypass the time-delta gate so the slot's position is actually applied.
        The same-position short-circuit (PR #300) still suppresses redundant
        re-sends when the sensor stays active across subsequent solar refreshes.
        """
        if self._pipeline_result is None:
            return False
        if self._pipeline_result.control_method is not ControlMethod.CUSTOM_POSITION:
            return False
        trigger = self._last_state_change_entity
        if trigger is None:
            return False
        options = self.config_entry.options
        return any(
            options.get(slot_keys["sensor"]) == trigger
            for slot_keys in CUSTOM_POSITION_SLOTS.values()
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

    @property
    def enabled_toggle(self):
        """Integration enabled toggle — master kill switch — delegates to ToggleManager."""
        return self._toggles.enabled_toggle

    @enabled_toggle.setter
    def enabled_toggle(self, value):
        """Set integration enabled toggle."""
        self._toggles.enabled_toggle = value

    async def _check_time_window_transition(self, now: dt.datetime) -> None:
        """Check time window transitions — delegates to TimeWindowManager.

        When the operational window closes (active→inactive transition) and
        CONF_RETURN_SUNSET is enabled, force-sends the current effective default
        position (which may be sunset_pos if in the astronomical sunset window)
        to all covers.  The command bypasses all gate checks so covers move
        immediately regardless of delta/time thresholds.
        """

        async def _on_window_closed() -> None:
            """Send effective default when end time is reached.

            Does NOT use force=True so the target is never tagged as a safety
            target.  Safety-tagging an end-time send lets reconciliation
            resurrect the target hours later after a manual override expires
            (issue #215/#216).  The necessary guards (return_sunset toggle,
            automatic_control) are already applied above; there is no reason
            to bypass the command-service delta/manual-override gates here.
            """
            # Always clear stale daytime targets when the window closes so
            # reconciliation cannot resend them overnight.
            self._cmd_svc.clear_non_safety_targets()
            if not self._track_end_time:
                return
            if not self.automatic_control:
                self.logger.debug(
                    "End time reached but automatic control is OFF — "
                    "skipping return-to-default reposition"
                )
                return
            options = self.config_entry.options
            effective_pos, is_sunset = self._compute_current_effective_default(options)
            pos_to_send = (
                inverse_state(effective_pos) if self._inverse_state else effective_pos
            )
            self.logger.info(
                "End time reached — sending effective default %s%% "
                "(sunset_active=%s) to %s cover(s)",
                pos_to_send,
                is_sunset,
                len(self.entities),
            )
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "end_time_default_sent",
                    "position": pos_to_send,
                    "sunset_active": is_sunset,
                    "cover_count": len(self.entities),
                }
            )
            for cover_entity in self.entities:
                ctx = self._build_position_context(cover_entity, options, force=False)
                await self._cmd_svc.apply_position(
                    cover_entity, pos_to_send, "end_time_default", context=ctx
                )
            # Trigger a normal refresh so sensor state and diagnostics update
            await self.async_refresh()

        async def _on_window_open() -> None:
            """Trigger a full refresh when the time window opens.

            This ensures covers reposition at the start of the day when the
            window transitions from inactive to active (e.g. at sunrise when
            sensor.sun_next_rising is the start entity).
            """
            self.state_change = True
            await self.async_refresh()

        await self._time_mgr.check_transition(
            track_end_time=self._track_end_time,
            refresh_callback=_on_window_closed,
            on_window_open=_on_window_open,
        )
        await self._check_sunset_window_transition()

    def _compute_current_effective_default(
        self, options: dict, cover_data=None
    ) -> tuple[int, bool]:
        """Return (effective_pos, is_sunset_active) for the current moment.

        Single source of truth for reading the sunset/sunrise options and
        calling ``compute_effective_default``. Shared by the main update cycle
        (``_calculate_cover_state``), ``_on_window_closed`` and
        ``_check_sunset_window_transition`` so the options-reading and the
        ``window_explicitly_started`` signal are not duplicated.

        Args:
            options: The config-entry options dict.
            cover_data: An already-computed cover-data object whose ``sun_data``
                is reused. When ``None`` the cover data is computed fresh via
                ``get_blind_data`` (the transition call sites have no cover_data
                in hand).

        """
        h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
        sunset_pos_cfg = options.get(CONF_SUNSET_POS)
        sunset_off = int(options.get(CONF_SUNSET_OFFSET) or 0)
        sunrise_off = int(
            options.get(CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET) or 0)
        )
        sunset_time = _read_time_entity(self.hass, options.get(CONF_SUNSET_TIME_ENTITY))
        sunrise_time = _read_time_entity(
            self.hass, options.get(CONF_SUNRISE_TIME_ENTITY)
        )
        if cover_data is None:
            cover_data = self.get_blind_data(options=options)
        return compute_effective_default(
            h_def=h_def,
            sunset_pos=sunset_pos_cfg,
            sun_data=cover_data.sun_data,
            sunset_off=sunset_off,
            sunrise_off=sunrise_off,
            sunset_time=sunset_time,
            sunrise_time=sunrise_time,
            window_explicitly_started=self.window_explicitly_started,
        )

    async def _check_sunset_window_transition(self) -> None:
        """Delegate astronomical-sunset-window transition handling to the tracker.

        See :meth:`WindowTransitionTracker.check_sunset_window` for the
        full contract (issue #266).
        """
        options = self.config_entry.options
        await self._window_tracker.check_sunset_window(
            track_end_time=self._track_end_time,
            automatic_control=self.automatic_control,
            sunset_pos_cfg=options.get(CONF_SUNSET_POS),
            options=options,
            inverse_state_enabled=self._inverse_state,
            entities=self.entities,
            is_cover_manual=self.manager.is_cover_manual,
            build_position_context=lambda c, o: self._build_position_context(
                c, o, force=False
            ),
            apply_position=self._cmd_svc.apply_position,
            refresh=self.async_refresh,
        )

    def _check_sun_validity_transition(self) -> bool:
        """Delegate sun-visibility transition detection to the tracker."""
        return self._window_tracker.sun_just_appeared(self._cover_data)

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

        # Cancel the periodic forecast-recompute timer (issue #437).
        if self._forecast_unsub is not None:
            self._forecast_unsub()
            self._forecast_unsub = None

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager and inverse_state live in the managers/manual_override
# package. They are re-imported above to maintain backward compatibility.
