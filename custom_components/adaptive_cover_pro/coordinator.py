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
from homeassistant.const import STATE_ON
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
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .config_types import RuntimeConfig
from .helpers import (
    compute_effective_default,
    custom_position_slot_sensors,
    get_datetime_from_str,
    get_safe_state,
    state_attr,
)
from .config_context_adapter import ConfigContextAdapter
from .cover_types import CoverTypePolicy, get_policy
from .cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
    CoverDescriptor,
    caps_get,
)
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
    CONF_END_OF_WINDOW_POS,
    CONF_ENTITIES,
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
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_MANUAL_OVERRIDE_STRATEGY,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    DIAG_CACHE_KEY,
    DOMAIN,
    ISSUE_TEMP_SENSOR_UNAVAILABLE,
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
from .managers.cloud_suppression import CloudSuppressionManager
from .managers.motion import MotionManager
from .managers.sensor_health import SensorHealthManager
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
from .pipeline.types import CustomPositionSensorState, GroupIntent
from .templates import TemplateResolver
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

    The entity's *time-of-day* is re-anchored onto today's local date and the
    original date component is discarded. This makes a "next-event" sensor
    (e.g. ``sensor.sun_next_setting``, which rolls over to *tomorrow's*
    setting the instant today's sun sets) behave like a fixed daily wall-clock
    time. ``compute_effective_default`` already compares the boundary against
    *today's* clock, so a future-dated boundary would otherwise make the
    ``after_sunset`` comparison structurally unreachable (issue #531 follow-up).
    """
    if entity_id is None:
        return None
    raw = get_safe_state(hass, entity_id)
    if raw is None:
        return None
    try:
        parsed = get_datetime_from_str(raw)
        today_local = dt_util.now().date()
        return parsed.replace(
            year=today_local.year,
            month=today_local.month,
            day=today_local.day,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not parse time entity %s state %r as datetime",
            entity_id,
            raw,
        )
        return None


# Cover states that carry no usable position. A transition whose *old* state is
# one of these is a reconnection/initialization artifact (issue #342 covers the
# online-from-None case; issue #546 the unavailable-comeback case), not a real
# position change — it must not feed numeric manual-override detection.
_NON_POSITION_COVER_STATES = ("unavailable", "unknown")


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


type AdaptiveConfigEntry = ConfigEntry[AdaptiveDataUpdateCoordinator]
"""Config entry whose ``runtime_data`` holds the coordinator instance."""


# Skip-record label for a hold-mode dispatch, keyed by the winning control
# method.  Motion timeout and manual override both hold the cover in place
# (skip_command=True); the label distinguishes them in diagnostics so a manual
# hold is not mislabeled as motion (issue #809).  Single source of truth — do
# not fork a per-method guard block in ``_dispatch_to_cover``.
_HOLD_SKIP_LABEL: dict[ControlMethod, str] = {
    ControlMethod.MOTION: "motion_hold",
    ControlMethod.MANUAL: "manual_override_hold",
}


class AdaptiveDataUpdateCoordinator(DataUpdateCoordinator[AdaptiveCoverData]):
    """Adaptive cover data update coordinator."""

    config_entry: AdaptiveConfigEntry

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
        # Cloud-suppression smoothing — hysteresis latch + hold-time debounce
        # (issue #864). Consumes provider booleans; never reads HA directly.
        self._cloud_mgr = CloudSuppressionManager(
            logger=self.logger, event_buffer=self._event_buffer
        )
        # Sensor-health Repairs (issue #786): raise an informational Repair when
        # the effective indoor temperature sensor stays unavailable past a
        # generous debounce, and clear it on recovery. Entity-agnostic — this PR
        # wires only the temp sensor.
        self._sensor_health = SensorHealthManager(self.hass, self.logger, domain=DOMAIN)
        self._temp_issue_key = (
            f"{ISSUE_TEMP_SENSOR_UNAVAILABLE}_{self.config_entry.entry_id}"
        )
        # Override pipeline — custom position handlers are created per-slot so
        # each can carry an independent priority configured by the user.
        self._pipeline = self._build_pipeline()
        self._pipeline_result = None

        # Live cover-group intents, one per group entry pushing to this member
        # (issue #790, Phase 2). Mutable manager-style state: groups write via
        # set_group_intent(); each snapshot folds effective_group_intent in.
        self._group_intents: dict[str, GroupIntent] = {}

        # Resolved-target signature last handed to the dispatch path (issue
        # #756). The dispatch decision compares the current cycle's resolved
        # target against this so an override that wins the pipeline is sent even
        # when the transient ``state_change`` edge was lost (clobbered by a long
        # in-flight venetian settle/tilt sequence holding the update cycle).
        self._last_dispatched_target_sig: tuple | None = None

        # Snapshot of the last raw config-entry options. The update listener
        # uses it to distinguish Sun Tracking-only changes from options that
        # still require a full reload.
        self._cached_options = dict(self.config_entry.options)

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

        # Renders templated threshold options to numbers once per cycle (#577).
        self._template_resolver = TemplateResolver(self.hass)
        # Current cycle's options after template resolution (for diagnostics).
        # Resolved at construction so apply_user_position sees float thresholds
        # even before the first _async_update_data cycle runs (#643).
        self._resolved_options: dict = self._template_resolver.resolve(
            self.config_entry.options
        )

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

        # Per-slot trigger state from last cycle, keyed by slot number, so a
        # custom-position slot that flips off can force a return to the
        # calculated position regardless of which lower-priority handler now
        # wins. A released slot at CUSTOM_POSITION_SAFETY_PRIORITY also lifts
        # the outside-time-window gate (the migrated force-override release
        # edge, issue #563).
        self._prev_custom_position_states: dict[int, CustomPositionSensorState] = {}

        # Set by async_check_custom_position_template_change so the update
        # cycle can attribute an entity-less refresh to a slot template flip.
        # Cleared after async_handle_state_change consumes it.
        self._custom_position_template_trigger: bool = False

        # Diagnostics builder (extracted from coordinator)
        self._diagnostics_builder = DiagnosticsBuilder()

        # Track position explanation for change detection logging
        self._last_position_explanation: str = ""

        # Built once and reused for both the command-service construction
        # (position_tolerance) and the late policy.attach below.
        _rc_attach = RuntimeConfig.from_options(self.config_entry.options)
        # Seeded here so the live lambda passed to policy.attach reads a value
        # before the first _update_options cycle; refreshed each cycle (#679).
        self._enforce_delta_at_endpoints = (
            _rc_attach.tracking.enforce_delta_at_endpoints
        )
        # Seeded here so the live drift-reset lambda passed to policy.attach
        # reads a value before the first _update_options cycle; refreshed each
        # cycle (issue #663).
        self._venetian_tilt_reset_threshold = _rc_attach.venetian.tilt_reset_threshold
        # Seeded alongside the threshold so the live drift-reset direction lambda
        # passed to policy.attach reads a value before the first _update_options
        # cycle; refreshed each cycle (issue #686).
        self._venetian_tilt_reset_direction = _rc_attach.venetian.tilt_reset_direction
        # Seeded alongside the threshold so the live drift-reset scope lambda
        # passed to policy.attach reads a value before the first _update_options
        # cycle; refreshed each cycle (issue #808).
        self._venetian_tilt_reset_scope = _rc_attach.venetian.tilt_reset_scope

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
            endpoint_use_open_close=_rc_attach.tracking.endpoint_use_open_close,
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
        # Issue #888: drop the display-only assumed position when the override
        # resets or a real numeric position read arrives.
        self.manager.set_assumed_invalidator(self._cmd_svc.clear_assumed_position)

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
            venetian_tilt_skip_mode=_rc_attach.venetian.tilt_skip_mode,
            venetian_mode=_rc_attach.venetian.venetian_mode,
            post_settle_hold_seconds=_rc_attach.venetian.post_settle_hold_seconds,
            post_settle_mode=_rc_attach.venetian.post_settle_mode,
            backrotate_publish_lag_seconds=(
                _rc_attach.venetian.backrotate_publish_lag_seconds
            ),
            invert_tilt=lambda: self._inverse_tilt,
            get_min_change=lambda: self.min_change,
            get_enforce_delta_at_endpoints=lambda: self._enforce_delta_at_endpoints,
            get_tilt_reset_threshold=lambda: self._venetian_tilt_reset_threshold,
            get_tilt_reset_direction=lambda: self._venetian_tilt_reset_direction,
            get_tilt_reset_scope=lambda: self._venetian_tilt_reset_scope,
            # Wake the update cycle at suppression expiry so a deferred
            # tilt-only update fires promptly (issue #756). No-op for
            # single-axis policies (base attach ignores it).
            schedule_refresh_after=self._schedule_refresh_after,
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

        # Issue #547: cached forecast daily-high (°) for the configured weather
        # entity, refreshed on a slow wall-clock cadence by
        # ``async_recompute_forecast_max`` and fed into the climate read so the
        # outdoor-temp source switch can source it. ``None`` when the source is
        # ``live``, no weather entity is configured, or the last fetch failed —
        # in which case the provider degrades to the live read.
        self._forecast_max_outside: float | None = None
        self._forecast_max_unsub: Callable[[], None] | None = None

        # Issue #742: cancel handle for the single ``async_call_later`` wake that
        # flips the daytime gate from HOLDING its last-known verdict to the
        # astronomical fallback the moment the grace window expires (otherwise the
        # fallback would wait for the next state-change/periodic refresh).
        self._gate_fallback_unsub: Callable[[], None] | None = None

        # Issue #756: cancel handle for the single ``async_call_later`` wake that
        # re-runs the update cycle at venetian back-rotate suppression expiry, so
        # a tilt-only update deferred while the window was open fires promptly.
        self._refresh_after_unsub: Callable[[], None] | None = None

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
        # Issue #547: outdoor forecast daily-high refresher (separate scheduler
        # so the position-forecast timer stays a single-writer).
        self._start_forecast_max_scheduler()

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

    def _start_forecast_max_scheduler(self) -> None:
        """Kick off the initial outdoor forecast-max fetch + periodic refresh.

        Issue #547: mirrors :meth:`_start_forecast_scheduler` but for the
        outdoor forecast daily-high. The fetch is a single
        ``weather.get_forecasts`` service call (cheap vs the position-forecast
        astral walk) and no-ops on the coordinator side when the source is
        ``live`` or no weather entity is configured. Idempotent: reuses the
        existing unsubscribe handle if already scheduled.
        """
        from homeassistant.helpers.event import async_track_time_change

        from .const import FORECAST_RECOMPUTE_INTERVAL_MIN

        if self._forecast_max_unsub is not None:
            return  # already scheduled

        self.config_entry.async_create_background_task(
            self.hass,
            self.async_recompute_forecast_max(),
            name="acp_initial_forecast_max",
        )

        @callback
        def _tick_forecast_max(_now: dt.datetime) -> None:
            self.config_entry.async_create_background_task(
                self.hass,
                self.async_recompute_forecast_max(),
                name="acp_periodic_forecast_max",
            )

        self._forecast_max_unsub = async_track_time_change(
            self.hass,
            _tick_forecast_max,
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

    async def async_recompute_forecast_max(self) -> None:
        """Refresh the cached outdoor forecast daily-high (issue #547).

        Fetches today's forecast high from the configured weather entity via
        ``weather.get_forecasts`` (type ``daily``) and caches it on
        ``self._forecast_max_outside`` so the climate read's outdoor-temp
        source switch can source it.

        Runs only when the outdoor-temp source is not ``live`` AND a weather
        entity is configured; otherwise the cache is cleared. Every failure
        path — service error, missing/empty/non-numeric forecast — degrades to
        ``None`` so the provider falls back to the live read. Hourly-only
        weather integrations (no daily forecast) therefore degrade to live too.
        """
        from .const import (
            CONF_OUTSIDE_TEMP_SOURCE,
            CONF_WEATHER_ENTITY,
            DEFAULT_OUTSIDE_TEMP_SOURCE,
            OutsideTempSource,
        )

        options = self.config_entry.options
        source = options.get(CONF_OUTSIDE_TEMP_SOURCE, DEFAULT_OUTSIDE_TEMP_SOURCE)
        weather_entity = options.get(CONF_WEATHER_ENTITY)
        if source == OutsideTempSource.LIVE.value or not weather_entity:
            self._forecast_max_outside = None
            return

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily"},
                target={"entity_id": weather_entity},
                blocking=True,
                return_response=True,
            )
            forecasts = (response or {}).get(weather_entity, {}).get("forecast") or []
            today_high = float(forecasts[0]["temperature"])
        except Exception:  # noqa: BLE001 — any service/parse failure degrades to live
            self._forecast_max_outside = None
            return
        self._forecast_max_outside = today_high

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
            if (
                new_state is not None
                and new_state.state not in _NON_POSITION_COVER_STATES
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
        old_state_str = self.state_change_data.old_state.state
        if old_state_str not in _NON_POSITION_COVER_STATES:
            self.cover_state_change = True
            self.process_entity_state_change()
            # Keep a per-event copy so async_handle_cover_state_change() can
            # process all covers that fired in a single refresh window, not
            # just the last one to overwrite state_change_data.
            self._pending_cover_events.append(self.state_change_data)
            await self.async_refresh()
        else:
            # Issue #546: a cover returning from unavailable/unknown to a real
            # position is a reconnection artifact, not a user move.
            self.logger.debug(
                "Old state is %s, not processing as a position change",
                old_state_str,
            )

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

        # Capture the stop_cover call's originating HA context (issue #875) so
        # the recorded manual_override_set event is self-attributing —
        # distinguishing a legitimate external stop from a spurious/descendant
        # one without needing the HA logbook.
        for entity_id in tracked:
            # On the not-manual→manual edge the manager fires on_engaged →
            # discard_target (issue #215/#216); see set_transition_callbacks.
            engaged = self.manager.handle_stop_service_call(
                entity_id,
                int(my_position_value),
                self._cmd_svc.is_waiting_for_target,
                context_user_id=event.context.user_id if event.context else None,
                context_id=event.context.id if event.context else None,
                context_parent_id=event.context.parent_id if event.context else None,
            )
            # Update target so the next reconciliation compares against
            # My rather than the stale calculated state.
            self._cmd_svc.set_target(entity_id, int(my_position_value))
            # Issue #888: when the stop actually engaged the #875 override (not a
            # mid-move stop), record My as the display-only assumed position so
            # the card shows My. Confined to covers with no native position axis
            # by the shared helper's caps predicate; the my_position_value gate
            # above already restricts this to configured-My instances.
            if engaged:
                self._cmd_svc._record_assumed_if_blind(
                    entity_id, int(my_position_value)
                )

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

    async def async_check_weather_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Handle a weather condition template's rendered result changing (#639).

        Routed from ``async_track_template_result`` so a template-only override
        (is-raining / is-windy) engages and reacts the instant the template
        flips — the same immediacy as a weather sensor, with no polling. The
        tracked result only signals *that* a template changed; the manager
        re-reads the combined condition state live so the OR/AND mode is honoured.
        """
        is_now_active = self._weather_mgr.is_any_condition_active

        if is_now_active:
            if not self._weather_mgr.is_weather_override_active:
                self.logger.info(
                    "Weather conditions active (template) — retracting covers"
                )
                self._weather_mgr.record_conditions_active()
            self.state_change = True
            await self.async_refresh()
        else:
            self._reconcile_weather_override()

    async def async_check_motion_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle occupancy-source changes: immediate on detection, debounced on stop.

        Delegates to ``_handle_occupancy_change``, which re-reads the combined
        occupancy state (sensors + template per the configured combine mode)
        rather than branching on this single entity's state here.
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
        await self._handle_occupancy_change(source=entity_id)

    async def async_check_manual_override_input_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Engage manual override on the off→on edge of a configured input sensor.

        Wall-switch path (issue #688): a configured input binary sensor (e.g. a
        Shelly ``binary_sensor.*_cover_input_0``) transitioning off→on means the
        user physically operated the cover, so ACP engages manual override on
        every cover in the instance and drops the latched target. Only the
        rising edge engages — ``on→on`` (no edge), ``None→on`` (a sensor restored
        already-on at startup), ``unavailable``/``unknown`` (not "on"), and a
        removed entity (``new_state`` None) all do nothing.
        """
        data = event.data
        old_state = data["old_state"]
        new_state = data["new_state"]
        if new_state is None or new_state.state != STATE_ON:
            return
        if old_state is None or old_state.state == STATE_ON:
            return
        self.logger.debug(
            "Manual override input %s edge off→on — engaging override on all covers",
            data["entity_id"],
        )
        self.manager.engage_manual_override_from_external(reason="input_sensor")
        self.state_change = True
        await self.async_refresh()

    async def async_engage_manual_override(
        self,
        entity_ids: list[str],
        *,
        end_time: dt.datetime | None = None,
        duration: dt.timedelta | None = None,
        trigger: str = "engage_manual_override",
    ) -> None:
        """Engage or extend manual override on the given covers, without moving them.

        Backs the ``adaptive_cover_pro.engage_manual_override`` service. Engages
        each cover purely through the override state machine
        (:meth:`AdaptiveCoverManager.engage_override` — no ``apply_position``,
        no command), then refreshes once so the ``manual_override`` binary
        sensor and ``manual_override_end_time`` sensor reflect the new state
        immediately (mirrors the input-sensor path at
        :meth:`async_check_manual_override_input_change`).

        Args:
            entity_ids: Cover entity IDs to engage.
            end_time: Optional absolute end passed through to the manager.
            duration: Optional relative extend-by passed through to the manager.
            trigger: Diagnostic reason label recorded per cover.

        """
        for entity_id in entity_ids:
            self.manager.engage_override(
                entity_id, end_time=end_time, duration=duration, reason=trigger
            )
        self.state_change = True
        await self.async_refresh()

    async def async_check_motion_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Handle the occupancy template's rendered result changing (#577 follow-up).

        Routed from ``async_track_template_result`` so a template flipping truthy
        resumes positioning instantly — the same immediacy as a motion sensor,
        with no polling. The tracked result only signals *that* the template
        changed; ``_handle_occupancy_change`` re-reads the combined occupancy
        state live (the template re-renders, mapping a render error to
        not-occupied) so the AND/OR combine mode is honoured.
        """
        await self._handle_occupancy_change(source="occupancy template")

    async def async_check_custom_position_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Handle a custom-position slot template's rendered result changing (#563).

        Routed from ``async_track_template_result`` so a template flipping
        truthy applies the slot's position instantly — the same immediacy as a
        trigger sensor, with no polling. The tracked result only signals
        *that* the template changed; the snapshot builder re-renders all slot
        templates live during the refresh so the OR/AND combine mode is
        honoured. There is no triggering entity for the gate logic, so the
        template-trigger flag attributes this refresh to a slot template flip
        for the force-path decisions in ``async_handle_state_change``.
        """
        self.state_change = True
        self._last_state_change_entity = None
        self._custom_position_template_trigger = True
        await self.async_refresh()

    async def async_check_daytime_gate_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Handle the daytime-gate template's rendered result changing (#632).

        Routed from ``async_track_template_result`` so the cover repositions the
        instant the gate template flips dark — the same immediacy as a gate binary
        sensor or weather template, with no polling. The tracked result only
        signals *that* the template changed; ``TimeWindowManager.gate_is_daytime``
        re-reads live sensor/template state during the subsequent refresh so the
        OR/AND combine mode is honoured.
        """
        self.state_change = True
        await self.async_refresh()

    async def _handle_occupancy_change(self, *, source: str) -> None:
        """Apply an occupancy-source transition shared by sensors and the template.

        Acts on the *combined* :attr:`is_motion_detected` after the change rather
        than the single source that fired — required so the template's AND/OR
        combine mode is respected (in AND mode one source going active does not
        mean occupied). The combined property re-reads live state, so it is at
        least as fresh as any captured event value and current state always wins.
        """
        if self.is_motion_detected:
            # record_motion_detected() returns True if the timeout was active
            # (expired) or pending (task still running), so we refresh in both
            # cases, not just when the timeout had already fully expired.
            if self._motion_mgr.record_motion_detected():
                self.logger.info(
                    "Motion detected (%s) - resuming automatic sun positioning", source
                )
                self.state_change = True
                await self.async_refresh()
            else:
                self.logger.debug(
                    "Occupancy still active after change on %s — no action", source
                )
        else:
            # Combined occupancy cleared - start the debounce timeout.
            self._start_motion_timeout()

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

        Runs when any occupancy source is configured — sensors, media players,
        or the occupancy template (issue #577 follow-up).
        """
        if not self._motion_mgr.is_configured:
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
        if not self._weather_mgr.is_feature_configured:
            return
        if self._weather_mgr.is_any_condition_active:
            self.logger.info(
                "Startup: weather conditions active — restoring override state "
                "so clear-delay timeout will fire when conditions end"
            )
            self._weather_mgr.record_conditions_active()

    def _start_cloud_hold_timeout(self) -> None:
        """Start the cloud-suppression hold-time debounce timer (issue #864)."""

        async def _refresh_with_state_change() -> None:
            self.state_change = True
            await self.async_refresh()

        self._cloud_mgr.start_hold_timeout(refresh_callback=_refresh_with_state_change)

    def _reconcile_cloud_suppression(self, readings) -> None:
        """Fold this cycle's readings into the cloud-suppression manager.

        The manager applies hysteresis + the hold-time debounce and resolves a
        single bool. When a transition is pending (hold-time non-zero), it asks
        us to start the hold-timer — the coordinator owns timer creation because
        it holds the refresh callback the manager intentionally does not.
        """
        if self._cloud_mgr.evaluate(readings) == "should_start_timeout":
            self._start_cloud_hold_timeout()

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
        self._weather_readings = self._snapshot_builder.read_climate(
            options, forecast_max_outside=self._forecast_max_outside
        )

        # Fold the fresh readings into the cloud-suppression manager (issue
        # #864): it applies hysteresis + the hold-time debounce and resolves the
        # single bool threaded into the snapshot below. Runs before build() so
        # the snapshot sees this cycle's resolved value.
        self._reconcile_cloud_suppression(self._weather_readings)

        # Sensor-health Repair (issue #786): watch the effective indoor temp
        # entity only when climate mode is on (the sensor is otherwise unused,
        # so an unavailable one is not worth nagging about). Watching the
        # resolved effective entity covers both explicit and area-resolved
        # cases. Fail-open: never let this raise into the update cycle.
        try:
            climate_on = bool(options.get(CONF_CLIMATE_MODE, False))
            effective_temp = (
                self._weather_readings.inside_temperature_entity_id
                if climate_on
                else None
            )
            self._sensor_health.update_watch(
                self._temp_issue_key,
                effective_temp,
                translation_key=ISSUE_TEMP_SENSOR_UNAVAILABLE,
                placeholders={
                    "entity_id": effective_temp or "",
                    "name": self.config_entry.data.get("name", "") or "",
                },
            )
            self._sensor_health.evaluate()
        except Exception:  # noqa: BLE001 — health check must never break the cycle
            self.logger.debug("Sensor-health evaluation failed", exc_info=True)

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
            cloud_suppression_active=self._cloud_mgr.is_suppression_active,
            in_time_window=self.check_adaptive_time,
            current_cover_position=self._compute_mean_cover_position(),
            is_glare_zone_enabled=self._is_glare_zone_enabled,
            effective_default=effective_default,
            is_sunset_active=is_sunset_active,
            cover_capabilities=getattr(
                getattr(self, "_snapshot", None), "cover_capabilities", None
            ),
            group_intent=self.effective_group_intent,
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
            self._cached_options = dict(self.config_entry.options)

        # Render any templated threshold options to numbers for this cycle, so
        # every downstream consumer (RuntimeConfig, climate reads) sees a number,
        # never a raw template string (#577).
        options = self._template_resolver.resolve(self.config_entry.options)
        self._resolved_options = options
        self._update_options(options)

        # Capture last cycle's per-slot trigger map so we can detect a custom
        # position slot flipping off (release edge of #365 / #563).
        prev_custom_position_states = dict(self._prev_custom_position_states)

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
                self.entities,
                self._policy,
                assumed=self._cmd_svc.get_assumed_position,
            ),
            cover_capabilities=self._cover_provider.read_all_capabilities(
                self.entities
            ),
            motion_detected=self.is_motion_detected,
        )

        # Get data for the blind and update manager
        cover_data = self.get_blind_data(options=options)

        # Pre-warm the SunData cache off the event loop. On HAOS (no OS tz data),
        # pd.date_range(tz=<named-tz>) in _ensure_today() blocks the loop by
        # importing tzdata. One executor call per first-of-day cycle fills the
        # module-level _DAY_CACHE; subsequent accesses in this cycle are Tier 1 hits.
        # Calling unconditionally is safe — prime_cache() is a no-op when warm.
        # Issue #655.
        await self.hass.async_add_executor_job(cover_data.sun_data.prime_cache)

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

        # Stamp this cycle's per-slot trigger map so next cycle can detect the
        # on → off transition for the release edge.
        current_custom_position_states = {
            s.slot: s
            for s in self._snapshot_builder.read_custom_position_sensors(options)
        }
        self._prev_custom_position_states = current_custom_position_states

        # Slots that transitioned on → off this cycle.  When the triggering
        # entity belongs to one of these (or the trigger was a slot template),
        # force=True bypasses time/position delta gates so covers return to
        # the calculated position promptly.  A released safety-priority slot
        # additionally lifts the outside-time-window gate — the migrated
        # force-override release edge (issue #563).
        released_slots = [
            prev
            for slot, prev in prev_custom_position_states.items()
            if prev.is_on
            and not getattr(current_custom_position_states.get(slot), "is_on", False)
        ]
        custom_position_released_entities = {
            eid for prev in released_slots for eid in prev.entity_ids
        }
        safety_release = any(
            prev.priority >= CUSTOM_POSITION_SAFETY_PRIORITY for prev in released_slots
        )
        template_release = self._custom_position_template_trigger and bool(
            released_slots
        )

        # Handle types of changes — single dispatch authority (issue #756).
        await self._dispatch_for_cycle(
            state,
            options,
            auto_expired=auto_expired,
            custom_position_released_entities=custom_position_released_entities,
            safety_release=safety_release,
            template_release=template_release,
        )
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

        # Cache this snapshot outside the coordinator so a diagnostics download
        # during a reload window (when entry.runtime_data is briefly unset) can
        # still serve the last-good data instead of an empty marker.
        self._cache_last_good_diagnostics(diagnostics)

        # Record successful update time (after build_diagnostic_data so the
        # diagnostic for this cycle reports the *previous* completed success).
        self._last_update_success_time = dt.datetime.now(dt.UTC)

        # Determine glare_active from last calculation details (vertical covers only)
        glare_active = False
        if hasattr(self._cover_data, "_last_calc_details"):
            details = self._cover_data._last_calc_details  # noqa: SLF001
            glare_active = len(details.get("glare_zones_active", [])) > 0

        # Issue #742: now that the gate verdict is resolved for this cycle, arm a
        # single wake at grace expiry if the gate is HOLDING its last-known value.
        self._schedule_gate_fallback_wake()

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
        user_command: bool = False,
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
            user_command: If True, this is an explicit user-initiated command
                (card Open/Close/Set, set_position / set_axes service, My
                button) and must dispatch even when ACP's raw view already
                matches the target — the same-position gate is bypassed
                (issue #900). Distinct from ``force``: recurring resends set
                ``force`` too but stay deduped to avoid relay clicks (#290).

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
            user_command=user_command,
            use_my_position=(
                use_my_position
                or (
                    self._pipeline_result.use_my_position
                    if self._pipeline_result
                    else False
                )
            ),
            policy=self._policy,
            # Neutral winning-control-method value (issue #808). Cover-type-
            # agnostic here; the venetian policy reads it to gate drift-reset
            # scope. None when no pipeline result is available yet.
            control_method=(
                self._pipeline_result.control_method if self._pipeline_result else None
            ),
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

        When the active pipeline result has skip_command=True (motion-timeout
        hold_position mode, or a manual-override hold), no command is issued. A
        hold skip record is written instead so diagnostics show why the cover
        didn't move; the label reflects the winning control method (motion_hold
        vs manual_override_hold — issue #809). All other callers (forced
        transitions, override clears, window events) bypass this helper and call
        apply_position directly so they are never blocked by hold mode.
        """
        if self._pipeline_result is not None and self._pipeline_result.skip_command:
            result = self._pipeline_result
            label = _HOLD_SKIP_LABEL.get(result.control_method, "hold")
            # Motion holds carry the physical position in ``position`` and leave
            # held_position None; manual holds keep ``position`` as the would-be
            # shadow and put the physical position in held_position.  Prefer
            # held_position when present so the recorded value is the true
            # physical position for both (byte-identical to the old behaviour
            # for motion, which has held_position None).
            held = (
                result.held_position
                if result.held_position is not None
                else result.position
            )
            self._cmd_svc.record_skipped_action(
                cover,
                label,
                state,
                trigger=reason,
                inverse_state=self._inverse_state,
                extras={
                    "held_position": held,
                    "would_be_position": state,
                    "hold_control_method": result.control_method.value,
                },
            )
            return None
        return await self._cmd_svc.apply_position(cover, state, reason, context=ctx)

    def set_group_intent(self, group_id: str, intent: GroupIntent | None) -> None:
        """Store or remove one cover-group's live intent for this member.

        ``None`` removes the group's claim (scene cleared, lock released, or
        the group unloaded). The next snapshot folds ``effective_group_intent``
        in; the caller is responsible for triggering a refresh.
        """
        if intent is None:
            self._group_intents.pop(group_id, None)
        else:
            self._group_intents[group_id] = intent

    @property
    def effective_group_intent(self) -> GroupIntent | None:
        """The highest-priority live group intent, or None.

        Priority-ranked, not last-write-wins, so a whole-house lock is never
        silently clobbered by a facade scene from another group (issue #790).
        """
        if not self._group_intents:
            return None
        return max(self._group_intents.values(), key=lambda intent: intent.priority)

    @property
    def pipeline_winner_name(self) -> str | None:
        """Name of the handler that won the last pipeline evaluation.

        The winner is the first ``matched`` step in trace order (handler
        steps precede synthetic floor/tilt steps). Read by the cover-group
        who-won sensor; ``None`` before the first evaluation.
        """
        result = self._pipeline_result
        if result is None:
            return None
        return next(
            (step.handler for step in result.decision_trace if step.matched), None
        )

    async def async_reset_manual_overrides(
        self,
        entities: list[str] | None = None,
        *,
        trigger: str = "manual_reset",
    ) -> list[str]:
        """Clear manual override on covers and resend the pipeline position.

        Single authoritative reset sequence shared by the Reset Manual
        Override button and the cover-group bulk clear (issue #790): clear the
        override flag, suppress re-detection during the refresh, re-run the
        pipeline, then delegate to the shared post-override send path. Returns
        the entities whose override was actually cleared.
        """
        covers = (
            entities
            if entities is not None
            else self.config_entry.options.get(CONF_ENTITIES, [])
        )
        reset_entities: list[str] = []
        for entity in covers:
            if self.manager.is_cover_manual(entity):
                _LOGGER.debug("Resetting manual override for: %s", entity)
                self.manager.reset(entity)
                # Suppress re-detection: cover state events during refresh must
                # not be treated as a new manual override.
                self._cmd_svc.set_waiting(entity, True)
                self.cover_state_change = False
                reset_entities.append(entity)
            else:
                _LOGGER.debug(
                    "Resetting manual override for %s is not needed since it is already auto-controlled",
                    entity,
                )

        if not reset_entities:
            return []

        # Refresh so the pipeline re-runs without the override active,
        # producing the correct post-override position (climate, solar,
        # default — whichever handler wins now).
        await self.async_refresh()

        # Time-window and automatic-control gates live in the shared send
        # path, along with force=True so time_delta/position_delta are
        # bypassed for this intentional reset.
        sent = await self._async_send_after_override_clear(
            self.state,
            self.config_entry.options,
            entities=reset_entities,
            trigger=trigger,
        )

        # Entities not sent to (gated by time window / auto-control, or
        # skipped inside apply_position) must have wait_for_target cleared so
        # later cover state events are not silently swallowed.
        for entity in reset_entities:
            if entity not in sent:
                _LOGGER.debug(
                    "Manual override reset: no position change sent for %s",
                    entity,
                )
                self._cmd_svc.set_waiting(entity, False)
        return reset_entities

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

        if not self.clock_window_open:
            self.logger.debug(
                "Manual override cleared for %s but outside the clock window — "
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

    def _resolved_target_signature(self) -> tuple | None:
        """Signature of this cycle's resolved cover target (issue #756).

        Captures everything that decides what command would be sent —
        winning handler, final position (post interpolation / inverse-state),
        tilt, and the safety / bypass / skip / floor-clamp flags. Comparing it
        against ``_last_dispatched_target_sig`` lets the dispatch path fire when
        the resolved target changes between cycles even if the transient
        ``state_change`` edge was lost. ``None`` when no pipeline result exists.
        """
        result = self._pipeline_result
        if result is None:
            return None
        return (
            result.control_method.value,
            self.state,
            result.tilt,
            result.is_safety,
            result.bypass_auto_control,
            result.skip_command,
            result.floor_clamp_applied,
        )

    async def _dispatch_for_cycle(
        self,
        state: int,
        options,
        *,
        auto_expired: bool,
        custom_position_released_entities: set[str],
        safety_release: bool,
        template_release: bool,
    ) -> None:
        """Decide and run command dispatch for this update cycle (issue #756).

        The single dispatch authority. Dispatch fires when either:
          * a tracked-entity ``state_change`` edge arrived this cycle, OR
          * the resolved cover target changed versus the last-dispatched one
            (``target_changed``) — even with no ``state_change`` edge. This is
            the #756 fix: a long-blocking venetian settle/tilt sequence holding
            the update cycle could clobber the ``state_change`` flag, stranding
            an override that had already won the pipeline. Comparing resolved
            targets recovers the lost dispatch on the very next cycle.

        The last-dispatched signature is recorded only when no cover still has a
        pending secondary-axis (venetian tilt) command, so a deferred tilt keeps
        being re-attempted until it actually sends.
        """
        current_sig = self._resolved_target_signature()
        target_changed = (
            self._last_dispatched_target_sig is not None
            and current_sig is not None
            and current_sig != self._last_dispatched_target_sig
        )
        if self.state_change:
            await self.async_handle_state_change(
                state,
                options,
                custom_position_released_entities,
                safety_release=safety_release,
                template_release=template_release,
                target_changed=target_changed,
            )
        elif auto_expired:
            # One or more manual overrides just timed out.  Proactively send
            # the fresh pipeline position so covers don't linger at the
            # user-moved position until the next solar/entity-state event.
            await self._async_send_after_override_clear(state, options)
        elif target_changed:
            # No state_change edge this cycle, but the resolved target moved
            # since the last dispatch — send it through the same path.
            await self.async_handle_state_change(
                state,
                options,
                custom_position_released_entities,
                safety_release=safety_release,
                template_release=template_release,
                target_changed=True,
            )
        if not any(self._policy.has_pending_secondary_axis(e) for e in self.entities):
            self._last_dispatched_target_sig = current_sig

    async def async_handle_state_change(
        self,
        state: int,
        options,
        custom_position_released_entities: set[str] | None = None,
        *,
        safety_release: bool = False,
        template_release: bool = False,
        target_changed: bool = False,
    ):
        """Send position commands to all covers when a tracked entity changes.

        When the active pipeline result has is_safety=True (weather safety
        handler or a safety-priority custom position), we pass force=True to
        the position context so that time_delta and position_delta gates
        cannot block safety-critical commands.  The reason string also
        reflects the handler that won rather than always saying "solar".

        ``custom_position_released_entities`` holds the sensors of every
        custom-position slot that flipped on → off this cycle.  When the
        triggering entity for this refresh is one of them — or the refresh
        came from a slot template flip (``template_release``) — force=True is
        also passed so the return to the calculated position is not throttled
        (#365).

        ``safety_release`` is True when a released slot carries safety
        priority (the migrated force override, issue #563): the release then
        also lifts the outside-time-window gate so the cover returns to the
        calculated position immediately, exactly as the old force-override
        release did.
        """
        sun_just_appeared = self._check_sun_validity_transition()
        is_safety = self._pipeline_is_safety_handler

        # Custom-position release edge: the trigger that fired this refresh
        # just flipped off and a lower-priority handler (solar/default) now
        # wins, so _is_custom_position_sensor_trigger() returns False.  The
        # time-delta gate would otherwise drop the return-to-calculated
        # command.  Short-circuit when the set is empty so callers that bypass
        # __init__ (e.g. gate-matrix fixtures) don't need to wire
        # _last_state_change_entity.
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
        if not override_holding and (
            custom_position_released_entities or template_release
        ):
            trigger_entity = self._last_state_change_entity
            custom_position_released = template_release or (
                trigger_entity is not None
                and custom_position_released_entities is not None
                and trigger_entity in custom_position_released_entities
            )

        # Outside the configured time window, only safety results (weather,
        # safety-priority custom positions) are allowed to move covers.  All
        # other handlers (solar, climate, cloud, default) must not reposition
        # covers before the user's start time or after the end time.  The
        # pipeline still evaluates so diagnostics/sensor state remain correct.
        custom_position_sensor_triggered = self._is_custom_position_sensor_trigger()

        if not self.clock_window_open and not is_safety and not safety_release:
            self.state_change = False
            self._last_state_change_entity = None
            self._custom_position_template_trigger = False
            self.logger.debug("Outside the clock window — skipping position update")
            return

        # A floor-clamp raised the winner this cycle (#534).  When manual
        # override holds the cover below an active floor, the clamped value is
        # already in cover-position space and must bypass the time/position
        # delta gates so the raise reaches the cover.
        floor_clamp = bool(
            self._pipeline_result is not None
            and self._pipeline_result.floor_clamp_applied
        )
        # target_changed alone must not defeat the user's delta_position/
        # delta_time throttle for routine solar/climate tracking (issue #853)
        # — only force the bypass when the resolved target already carries
        # override/safety semantics, matching the same delta-gate invariant
        # documented on _pipeline_bypasses_auto_control (issue #290).
        target_changed_override = target_changed and (
            is_safety or self._pipeline_bypasses_auto_control
        )
        use_force = (
            is_safety
            or safety_release
            or custom_position_sensor_triggered
            or custom_position_released
            or floor_clamp
            or target_changed_override
        )
        if custom_position_released or safety_release:
            reason = "custom_position_released"
            self.logger.debug(
                "Custom-position trigger %s released — bypassing time/position "
                "delta gates to return to calculated position %s",
                trigger_entity or "(template/safety slot)",
                state,
            )
        elif floor_clamp:
            reason = "floor_clamp"
            self.logger.debug(
                "Floor clamp active — bypassing time/position delta gates to "
                "raise cover to floor position %s",
                state,
            )
        elif target_changed and not self.state_change:
            # Issue #756: dispatch driven purely by a resolved-target change
            # (the lost-state-change-edge recovery), with no other force driver.
            reason = "target_changed"
            self.logger.debug(
                "Resolved target changed without a state-change edge — "
                "bypassing time/position delta gates to send position %s",
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
        self._custom_position_template_trigger = False
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

            # Issue #591: when position matching is disabled (the default), a
            # settle beyond the position-match tolerance is the cover's final
            # resting position (a remote stop, or a cover that won't reach
            # target). Lower the detection threshold to the tolerance so any
            # "not arrived" settle engages a full manual override for the
            # configured duration — suppressing both resends (handled in
            # reconciliation) and new sun-driven targets — instead of being
            # retried. When matching is enabled the user manual_threshold is
            # used unchanged (no regression to the slow-actuator reconciliation
            # behaviour).
            detection_threshold = self.manual_threshold
            if not self._cmd_svc.enable_position_matching:
                detection_threshold = (
                    self._position_tolerance
                    if self.manual_threshold is None
                    else min(self.manual_threshold, self._position_tolerance)
                )

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
                detection_threshold,
                has_recorded_target=recorded_target is not None,
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

    async def async_apply_sun_tracking_update(self) -> None:
        """Rebuild the pipeline after Sun Tracking changes without a reload.

        ``enable_sun_tracking`` changes only pipeline composition; they do not
        alter entity listeners or cover geometry. The refresh evaluates the new
        winner immediately while the command path keeps its normal gates.
        """
        self._pipeline = self._build_pipeline()
        self._cached_options = dict(self.config_entry.options)
        # The Sun Tracking switch renders from config_entry.options; notify
        # listeners so service/options-flow changes redraw the entity too.
        self.async_update_listeners()
        self.state_change = True
        await self.async_refresh()

    def _build_pipeline(self) -> PipelineRegistry:
        """Build the override pipeline from the registry of handler factories.

        Called at coordinator initialisation and when Sun Tracking is toggled at
        runtime. Other options changes still reload the integration.
        Handler composition lives in ``pipeline.handlers.build_handlers``
        (registry-driven), so adding a handler never touches the coordinator.
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
        # Per-cycle snapshot of the input sensors whose off→on edge engages
        # manual override (issue #688). The HA subscription is (re)registered in
        # async_setup_entry on every reload; this mirror is the single canonical
        # coordinator-side read of the option.
        self.manual_override_input_entities = rc.manual_override.input_entities
        self.manual_threshold = rc.tracking.manual_threshold
        # Mirror the reconciliation tolerance coordinator-side so the cover
        # state-change handler can lower the override-detection threshold when
        # position matching is disabled (issue #591).
        self._position_tolerance = rc.tracking.position_tolerance
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
        self._cmd_svc.update_endpoint_use_open_close(
            rc.tracking.endpoint_use_open_close
        )
        self._cmd_svc.update_position_tolerance(rc.tracking.position_tolerance)
        self._cmd_svc.enable_position_matching = rc.tracking.enable_position_matching
        # Mirror the endpoint-delta-enforcement flag (issue #679) so the
        # venetian tilt-axis gate (wired via a live lambda in policy.attach)
        # and the position-axis special list pick up mid-session changes.
        self._enforce_delta_at_endpoints = rc.tracking.enforce_delta_at_endpoints
        # Mirror the venetian drift-reset threshold (issue #663) so the live
        # lambda wired into policy.attach picks up mid-session changes.
        self._venetian_tilt_reset_threshold = rc.venetian.tilt_reset_threshold
        # Mirror the venetian drift-reset direction (issue #686) so the live
        # lambda wired into policy.attach picks up mid-session changes.
        self._venetian_tilt_reset_direction = rc.venetian.tilt_reset_direction
        # Mirror the venetian drift-reset scope (issue #808) so the live
        # lambda wired into policy.attach picks up mid-session changes.
        self._venetian_tilt_reset_scope = rc.venetian.tilt_reset_scope
        self._time_mgr.update_config(
            start_time=rc.time_window.start_time,
            start_time_entity=rc.time_window.start_time_entity,
            end_time=rc.time_window.end_time,
            end_time_entity=rc.time_window.end_time_entity,
            gate_sensors=rc.time_window.gate_sensors,
            gate_template=rc.time_window.gate_template,
            gate_template_mode=rc.time_window.gate_template_mode,
        )
        self._motion_mgr.update_config(
            sensors=rc.motion.sensors,
            timeout_seconds=rc.motion.timeout_seconds,
            media_players=rc.motion.media_players,
            template=rc.motion.template,
            template_mode=rc.motion.template_mode,
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
            is_raining_template=rc.weather.is_raining_template,
            is_raining_template_mode=rc.weather.is_raining_template_mode,
            is_windy_template=rc.weather.is_windy_template,
            is_windy_template_mode=rc.weather.is_windy_template_mode,
            severe_sensors=rc.weather.severe_sensors,
            timeout_seconds=rc.weather.timeout_seconds,
            enabled=rc.weather.enabled,
        )
        self._cloud_mgr.update_config(
            enabled=rc.cloud_suppression.enabled,
            hold_time_seconds=rc.cloud_suppression.hold_time_seconds,
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
        # When sun.sun is unavailable both attributes read as None. Falling back
        # to 0.0/0.0 is dangerous: azimuth=0, elevation=0 is a valid-looking sun
        # position (on the horizon, due north) that can land inside a window's FOV
        # and trigger spurious cover commands off a phantom sun. When the entity
        # is truly unavailable, drop elevation below the horizon (-1.0) so
        # valid_elevation is False (for the default and min-configured cases) and
        # no solar positioning runs until sun.sun recovers.
        _sun_unavailable = _raw_azi is None and _raw_elev is None
        if _sun_unavailable:
            self.logger.warning(
                "sun.sun attributes unavailable — solar tracking disabled until "
                "the sun entity reports valid azimuth/elevation"
            )
        sol_azi = _raw_azi if _raw_azi is not None else 0.0
        sol_elev = (
            _raw_elev if _raw_elev is not None else (-1.0 if _sun_unavailable else 0.0)
        )
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
    def clock_window_open(self):
        """Whether the user's start/end clock window is open — delegates to TimeWindowManager."""
        return self._time_mgr.clock_window_open

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
        opts = options if options is not None else self._resolved_options
        snapshot = self._snapshot_builder.build(
            opts,
            cover_data=self._cover_data,
            cover_type=self._cover_type,
            climate_readings=self._weather_readings,
            manual_override_active=False,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            # Pure property read — the ad-hoc/preemption build must NOT
            # re-evaluate the manager (that would advance latches off-cycle).
            cloud_suppression_active=self._cloud_mgr.is_suppression_active,
            in_time_window=self.check_adaptive_time,
            current_cover_position=self._compute_mean_cover_position(),
            is_glare_zone_enabled=self._is_glare_zone_enabled,
            cover_capabilities=getattr(
                getattr(self, "_snapshot", None), "cover_capabilities", None
            ),
            group_intent=self.effective_group_intent,
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
            # Explicit user action — must dispatch even when ACP's raw view of a
            # no-feedback cover already matches the target (issue #900). Distinct
            # from force=True alone, which recurring resends also set and which
            # must stay deduped by the same-position gate (issue #290).
            user_command=True,
        )
        return await self._cmd_svc.apply_position(entity_id, clamped, trigger, ctx)

    async def async_apply_user_tilt(
        self,
        entity_id: str,
        tilt: int,
        *,
        trigger: str,
        force: bool = False,
    ) -> tuple[str, str]:
        """Apply a user-initiated tilt to a single cover (issue #684).

        Dedicated tilt-axis entry point for the opt-in proxy cover, the
        ``adaptive_cover_pro.set_tilt`` service, and any future user-facing
        tilt command. Delegates to the cover-type policy's ``apply_user_tilt``
        hook. Cover types with an independent tilt axis (venetian) drive ONLY
        the tilt slats — the carriage is left untouched. Cover types whose
        primary axis already IS the tilt (``cover_tilt``) return not-handled
        from the base hook, so we fall back to ``async_apply_user_position``
        (a tilt there is a position move).

        ``force`` (default ``False``) governs manual-override engagement only:
        when ``False`` (the proxy slider / default service call) manual override
        is engaged like a dashboard control; when ``True`` engagement is
        skipped, matching ``set_position``'s force semantics. There is no
        pipeline-preemption check on the tilt path (by design); ``force`` is
        threaded through to the ``async_apply_user_position`` fallback so the
        ``cover_tilt`` case stays consistent. The venetian sequencer's internal
        force-resend for an explicit command is independent and stays always-on.
        """
        if not force:
            self.manager.mark_user_command(entity_id, reason=trigger)
        handled = await self._policy.apply_user_tilt(
            entity_id, tilt=int(tilt), reason=trigger
        )
        if handled:
            return "sent", ""
        return await self.async_apply_user_position(
            entity_id, int(tilt), trigger=trigger, force=force
        )

    async def async_apply_user_axis(
        self,
        entity_id: str,
        axis_name: str,
        value: int,
        *,
        trigger: str,
        force: bool = False,
    ) -> tuple[str, str]:
        """Dispatch a user-initiated axis command to the matching setter (#725).

        Single collapse point behind the ``set_position`` / ``set_tilt`` /
        ``set_axes`` services: keyed on the ``AXIS_NAME_*`` constant (never a
        cover-type string), it routes to the existing per-axis entry point so
        each setter's force / manual-override / dispatch semantics stay
        bit-identical. Raises ``ValueError`` for an axis name it can't route.
        """
        setters = {
            AXIS_NAME_POSITION: self.async_apply_user_position,
            AXIS_NAME_TILT: self.async_apply_user_tilt,
        }
        setter = setters.get(axis_name)
        if setter is None:
            msg = f"Unknown axis {axis_name!r} for {entity_id}"
            raise ValueError(msg)
        return await setter(entity_id, value, trigger=trigger, force=force)

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

        Issue #888 follow-up: the ACP ``stop`` service (the card's stop button)
        lands a My-configured open/close-only cover on its hardware My preset,
        just like an external ``cover.stop_cover`` — but the external-stop
        detector (:meth:`async_check_cover_service_call`) ignores ACP-originated
        stops (``was_acp_stop_context``), so record My here instead. Mirrors the
        external path's ``is_waiting_for_target`` guard: a stop mid ACP-move is a
        halt, not a My landing.
        """
        # Capture waiting BEFORE mark_user_command, which discards the in-flight
        # target. was_waiting means ACP was mid its-own-move.
        was_waiting = self._cmd_svc.is_waiting_for_target(entity_id)
        self.manager.mark_user_command(entity_id, reason=trigger)
        result = await self._cmd_svc.apply_user_stop(entity_id)
        my_position_value = self.config_entry.options.get(CONF_MY_POSITION_VALUE)
        if my_position_value is not None:
            # The override engaged unconditionally (mark_user_command above), so
            # the display-only assumed My must be recorded unconditionally too — a
            # no-feedback open/close-only cover (Somfy RTS) physically lands on My
            # when stopped, even mid its-own-move. Caps-confined to open/close-only
            # covers by the shared helper (it clears any stale assumed value on
            # position-capable covers), so this is safe for every cover type.
            # Without it, a stop while ACP is mid-move (was_waiting) leaves the stale
            # close/open endpoint assumed value and every reported surface shows it (#888).
            self._cmd_svc._record_assumed_if_blind(entity_id, int(my_position_value))
            if not was_waiting:
                # Fresh target + transit window only when ACP was NOT already
                # mid-move: a mid-halt cover is already inside its transit window,
                # and set_target(My) on a position-capable halt would be wrong.
                # Capture the raw prior position BEFORE set_target overwrites it so
                # the synthetic direction reflects the move toward My.
                prior_position = self._cmd_svc.get_current_position(entity_id)
                self._cmd_svc.set_target(entity_id, int(my_position_value))
                # Give the My move a ~45s transit window (open/close-only covers) so
                # the card renders "Opening…/Closing…". The direction is caps-gated
                # by the shared helper; begin_transit stamps waiting + sent_at so the
                # reconciliation timer clears it when the window closes.
                self._cmd_svc._set_transit_direction_if_blind(
                    entity_id, int(my_position_value), prior_position
                )
                self._cmd_svc.begin_transit(
                    entity_id, self._cmd_svc.get_transit_direction(entity_id)
                )
        # A user stop engages an override and (for a My cover) records the new
        # target + assumed position + transit window. None of that reaches the
        # sensors until a coordinator cycle rebuilds them, so without an explicit
        # refresh the card shows nothing until the next scheduled update — long
        # enough that the ~45s transit window opens and closes unseen. Request a
        # refresh now (debounced, so a blanket stop coalesces to one cycle).
        await self.async_request_refresh()
        return result

    def build_axis_discovery(
        self, labels: dict[str, str] | None = None
    ) -> CoverDescriptor:
        """Assemble the persistent axis/cover self-discovery descriptor (#725).

        Reuses the diagnostics capability read (``read_all_capabilities`` — never
        re-reads HA features) and rolls up per-axis ``supported`` across every
        managed cover entity: an axis is supported if ANY member exposes it. The
        per-axis metadata is delegated to ``policy.describe`` so the payload is
        cover-type-agnostic — a ninth cover type needs no edit here.
        """
        entities = self.entities or []
        caps_map = self._cover_provider.read_all_capabilities(entities)
        # Roll up every capability key each axis's drivability consults — its
        # native flag plus any open/close fallback keys (#886) — so the merged
        # caps view fed to ``describe`` lets ``is_drivable`` see the fallback.
        keys: set[str] = set()
        for axis in self._policy.axes:
            keys.add(axis.capability_key)
            for group in axis.drive_fallbacks:
                keys.update(group)
        rolled: dict[str, bool] = {
            key: (
                any(caps_get(caps, key) for caps in caps_map.values())
                if caps_map
                else True
            )
            for key in keys
        }
        return self._policy.describe(caps=rolled, labels=labels)

    def build_diagnostic_data(self) -> dict:
        """Build diagnostic data from current coordinator state."""
        result = self._pipeline_result

        # Live cover positions and capabilities
        cover_entities = self.entities or []
        _positions = self._cover_provider.read_positions(
            cover_entities, self._policy, assumed=self._cmd_svc.get_assumed_position
        )
        _caps = self._cover_provider.read_all_capabilities(cover_entities)
        _covers = {
            eid: {
                "current_position": _positions.get(eid),
                "transit_state": self._cmd_svc.get_transit_direction(eid),
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

        _temp_readings = self._weather_readings
        ctx = DiagnosticContext(
            pos_sun=self.pos_sun,
            cover=self._cover_data,
            position_forecast=self._position_forecast,
            pipeline_result=result,
            climate_mode=self._climate_mode,
            temp_sensor_entity_id=(
                _temp_readings.inside_temperature_entity_id
                if _temp_readings is not None
                else None
            ),
            temp_sensor_source=(
                _temp_readings.inside_temperature_source
                if _temp_readings is not None
                else "none"
            ),
            temp_sensor_area_id=(
                _temp_readings.inside_temperature_area_id
                if _temp_readings is not None
                else None
            ),
            outside_temp_source=(
                _temp_readings.outside_temperature_source
                if _temp_readings is not None
                else "live"
            ),
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
            resolved_options=dict(self._resolved_options),
            hass=self.hass,
            motion_detected=self.is_motion_detected,
            motion_timeout_active=self._motion_mgr.is_motion_timeout_active,
            motion_template_active=self._motion_mgr.template_active,
            motion_hold_active=(
                self._pipeline_result is not None
                and self._pipeline_result.skip_command
                and self._pipeline_result.control_method == ControlMethod.MOTION
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
            # issue #625: the end-of-window position is the applied/active
            # effective default when the window is clock-closed AND the option
            # is set — the same condition _compute_current_effective_default
            # uses to fire the override.
            end_of_window_active=(
                self.config_entry.options.get(CONF_END_OF_WINDOW_POS) is not None
                and not self.before_end_time
            ),
        )

        diagnostics, explanation = self._diagnostics_builder.build(ctx)

        if explanation != self._last_position_explanation:
            self.logger.debug("Position explanation changed: %s", explanation)
            self._last_position_explanation = explanation

        return diagnostics

    def _cache_last_good_diagnostics(self, diagnostics: dict) -> None:
        """Store the latest diagnostics snapshot in hass.data, keyed by entry_id.

        Survives a coordinator teardown (unlike the in-memory event buffer), so the
        diagnostics download can fall back to it when ``entry.runtime_data`` is
        briefly unset during a reload. Pruned in ``async_remove_entry``.
        """
        cache = self.hass.data.setdefault(DIAG_CACHE_KEY, {})
        cache[self.config_entry.entry_id] = {
            "diagnostics": diagnostics,
            "ts": dt.datetime.now(dt.UTC),
        }

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

        Safety/override handlers (WeatherOverrideHandler, CustomPositionHandler)
        set bypass_auto_control=True so that wind/rain/forced protection still
        operates when the user has paused normal sun-tracking automation.
        Setting this flag does NOT make a result a safety result — see
        _pipeline_is_safety_handler.
        """
        return self._pipeline_result.bypass_auto_control

    @property
    def _pipeline_is_safety_handler(self) -> bool:
        """True only when the active pipeline result carries safety semantics.

        Safety results (WeatherOverrideHandler, and CustomPositionHandler at
        CUSTOM_POSITION_SAFETY_PRIORITY — the migrated force override, issue
        #563) require force=True so wind/rain/forced protection bypasses delta
        and time gates and always acts immediately.

        Other handlers that set bypass_auto_control=True (e.g. lower-priority
        custom positions) want to defeat the auto_control gate but must still
        be subject to the same-position short-circuit and delta/time gates so
        they don't issue redundant set_cover_position calls on every update
        cycle (issue #290).
        """
        return bool(self._pipeline_result and self._pipeline_result.is_safety)

    def _is_custom_position_sensor_trigger(self) -> bool:
        """Return True when this refresh was edge-triggered by a custom-position slot's own trigger.

        When a sensor (or slot template) toggles, the cover may be at a
        completely different position (e.g. solar tracking just moved it).
        Passing force=True lets the command bypass the time-delta gate so the
        slot's position is actually applied.  The same-position short-circuit
        (PR #300) still suppresses redundant re-sends when the trigger stays
        active across subsequent solar refreshes.
        """
        if self._pipeline_result is None:
            return False
        if self._pipeline_result.control_method is not ControlMethod.CUSTOM_POSITION:
            return False
        if self._custom_position_template_trigger:
            return True
        trigger = self._last_state_change_entity
        if trigger is None:
            return False
        options = self.config_entry.options
        return any(
            trigger in custom_position_slot_sensors(options, slot_keys)
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
        # A configured daytime gate (issue #632) OWNS the day/night boundary:
        # ``effective_daytime_gate`` is the single tri-state verdict to forward —
        # True=daytime→no sunset, False=dark→apply sunset position, None=defer to
        # the astronomical decision. None covers both an unconfigured gate and the
        # graceful fallback after every gate source has been indeterminate past the
        # grace window (issue #742).
        daytime_gate = self._time_mgr.effective_daytime_gate
        # End-of-window position (issue #625): an optional, clearable position
        # applied once the operating window is clock-closed. ``before_end_time``
        # is True all morning (end is later today), so the override only fires in
        # the evening — never before the start time. compute_effective_default
        # owns the two-phase astral handoff; here we only read the inputs.
        eow_pos = options.get(CONF_END_OF_WINDOW_POS)
        window_is_closed = not self._time_mgr.before_end_time
        return compute_effective_default(
            h_def=h_def,
            sunset_pos=sunset_pos_cfg,
            sun_data=cover_data.sun_data,
            sunset_off=sunset_off,
            sunrise_off=sunrise_off,
            sunset_time=sunset_time,
            sunrise_time=sunrise_time,
            window_explicitly_started=self.window_explicitly_started,
            daytime_gate=daytime_gate,
            end_of_window_pos=eow_pos,
            end_of_window_active=window_is_closed,
        )

    @callback
    def _schedule_gate_fallback_wake(self) -> None:
        """Schedule one refresh at daytime-gate grace expiry (issue #742).

        While the gate is HOLDING its last-known verdict, nothing else would
        trigger the fall-back to the astronomical window until the next
        state-change or periodic refresh. Schedule a single ``async_call_later``
        wake at the remaining grace so the fallback engages promptly. Any
        in-flight wake is cancelled first so there is never more than one; a
        determinate (or already-fallen-back) gate schedules none.
        """
        if self._gate_fallback_unsub is not None:
            self._gate_fallback_unsub()
            self._gate_fallback_unsub = None
        secs = self._time_mgr.seconds_until_gate_fallback()
        if secs is None:
            return
        self._gate_fallback_unsub = async_call_later(
            self.hass, secs, self._on_gate_fallback_due
        )

    async def _on_gate_fallback_due(self, _now: dt.datetime) -> None:
        """Fire when the daytime-gate grace window expires: request a refresh."""
        self._gate_fallback_unsub = None
        await self.async_request_refresh()

    @callback
    def _schedule_refresh_after(self, secs: float) -> None:
        """Schedule one refresh ``secs`` from now (issue #756).

        Injected into the cover-type policy so a venetian tilt-only update that
        was deferred while the back-rotate suppression window was open gets a
        prompt wake at window expiry — instead of waiting for the next
        unrelated tracked-entity change. Any in-flight wake is cancelled first
        so there is never more than one outstanding.
        """
        if self._refresh_after_unsub is not None:
            self._refresh_after_unsub()
            self._refresh_after_unsub = None
        delay = secs if secs and secs > 0 else 0
        self._refresh_after_unsub = async_call_later(
            self.hass, delay, self._on_refresh_after_due
        )

    async def _on_refresh_after_due(self, _now: dt.datetime) -> None:
        """Fire when a scheduled deferred-tilt wake is due: request a refresh."""
        self._refresh_after_unsub = None
        await self.async_request_refresh()

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

        # Cancel any in-flight sensor-health debounce timers (issue #786).
        self._sensor_health.shutdown()

        # Stop cover command service reconciliation timer
        self._cmd_svc.stop()

        # Cancel the periodic forecast-recompute timer (issue #437).
        if self._forecast_unsub is not None:
            self._forecast_unsub()
            self._forecast_unsub = None

        # Cancel the outdoor forecast daily-high refresher (issue #547).
        if self._forecast_max_unsub is not None:
            self._forecast_max_unsub()
            self._forecast_max_unsub = None

        # Cancel the daytime-gate fallback wake (issue #742).
        if self._gate_fallback_unsub is not None:
            self._gate_fallback_unsub()
            self._gate_fallback_unsub = None

        # Cancel the deferred-tilt refresh wake (issue #756).
        if self._refresh_after_unsub is not None:
            self._refresh_after_unsub()
            self._refresh_after_unsub = None

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager and inverse_state live in the managers/manual_override
# package. They are re-imported above to maintain backward compatibility.
