"""The Coordinator for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt
import dataclasses
import json
import pathlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .forecast import Forecast

import pytz
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
    STATE_ON,
    UnitOfIrradiance,
)
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
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .config_types import CoverConfig, RuntimeConfig
from .engine.solar_gain import (
    AREA_SOURCE_CONFIGURED,
    AREA_SOURCE_DERIVED,
    AREA_SOURCE_UNKNOWN,
    GlassArea,
)
from .engine.solar_transmittance import (
    SolarTransmittance,
    solar_transmittance as _compute_solar_transmittance,
)
from .helpers import (
    _read_current_effective_default,
    _utc_naive_to_local_naive,
    check_cover_features,
    custom_position_slot_delivers_fixed_position,
    custom_position_slot_name,
    custom_position_slot_sensors,
    has_configured_window_end,
    read_sun_boundaries,
    read_sunset_window_open,
    resolve_override_deadline,
    state_attr,
)
from .config_context_adapter import ConfigContextAdapter
from .cover_types import CoverTypePolicy, get_policy
from .cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
    STATE_ATTR_TILT_POSITION,
    CoverDescriptor,
    ExternalInterlockPlan,
    axis_inverted,
    caps_get,
)
from .services.configuration_service import ConfigurationService
from .const import (
    _LOGGER,
    ATTR_POSITION,
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
    CONF_GLASS_AREA,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
    CONF_IRRADIANCE_ENTITY,
    CONF_MANUAL_IGNORE_EXTERNAL,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_OVERRIDE_STRATEGY,
    CONF_MANUAL_THRESHOLD,
    CONF_MY_POSITION_VALUE,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_RETURN_SUNSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TRANSIT_TIMEOUT,
    CONF_TRAVEL_TIME_CALIBRATION,
    TRAVEL_CALIBRATION_TICK_SECONDS,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_MANUAL_OVERRIDE_STRATEGY,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    DIAG_CACHE_KEY,
    DOMAIN,
    ISSUE_CONFIG_POSITION_ENVELOPE,
    ISSUE_CONFIG_TIME_WINDOW,
    ISSUE_COVER_NOT_MOVING,
    ISSUE_COVER_TILT_UNSUPPORTED,
    ISSUE_COVER_UNAVAILABLE,
    ISSUE_CUSTOM_POSITION_OUT_OF_RANGE,
    ISSUE_DAY_NIGHT_MIDDLE_RAIL_UNSET,
    ISSUE_SUN_UNAVAILABLE,
    ISSUE_TEMP_SENSOR_UNAVAILABLE,
    LOGGER,
    MANUAL_INTERLOCK_REASON,
    MANUAL_OVERRIDE_DURATION_MODE_FIXED,
    POSITION_CLOSED,
    POSITION_OPEN,
    POSITION_TOLERANCE_PERCENT,
    STARTUP_GRACE_PERIOD_SECONDS,
    TRIGGER_FORCE_APPLY_CALCULATED,
)
from .diagnostics.builder import DiagnosticContext, DiagnosticsBuilder
from .diagnostics.event_buffer import EventBuffer
from .managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_special_positions,
)
from .managers.cover_command.queue import (
    CommandQueue,
    get_command_queue,
    normalize_queue_name,
)
from .managers.grace_period import GracePeriodManager
from .managers.manual_override import (
    STARTED_AT_SOURCE_ENGAGED,
    AdaptiveCoverManager,
    DetectorConfig,
    get_detector,
)
from .managers.climate_smoothing import ClimateSmoothingManager
from .managers.cloud_suppression import CloudSuppressionManager
from .managers.motion import MotionManager
from .managers.repair import RepairManager
from .managers.sensor_health import SensorHealthManager
from .managers.weather import WeatherManager
from .managers.time_window import TimeWindowManager
from .managers.toggles import ToggleManager
from .managers.travel_calibration import TravelTimeCalibrator
from .position_utils import flip_if, interpolate_position, inverse_state
from .pipeline.handlers import (
    ManualOverrideHandler,
    build_handlers,
    resolve_handler_priority,
)
from .pipeline.axis_constraints import (
    clamp_to_bounds,
    compose_bounds,
    gather_axis_constraints,
)
from .pipeline.floors import effective_floor, gather_active_floors, outranking
from .pipeline.registry import PipelineRegistry
from .pipeline.snapshot_builder import PipelineSnapshotBuilder
from .pipeline.types import (
    CustomPositionSensorState,
    GroupIntent,
    HoldClampVerdict,
    PipelineSnapshot,
)
from .templates import (
    TemplateResolver,
    build_acp_template_variables,
    render_condition_or_none,
)
from .const import ControlMethod
from .state.climate_provider import ClimateProvider, ClimateReadings
from .state.cover_provider import CoverProvider
from .state.snapshot import CoverStateSnapshot, SunSnapshot
from .state.sun_provider import SunProvider
from .state.window_transition_tracker import WindowTransitionTracker

_MANIFEST_VERSION: str = json.loads(
    (pathlib.Path(__file__).parent / "manifest.json").read_text()
)["version"]


# Cover states that carry no usable position. A transition whose *old* state is
# one of these is a reconnection/initialization artifact (issue #342 covers the
# online-from-None case; issue #546 the unavailable-comeback case), not a real
# position change — it must not feed numeric manual-override detection.
_NON_POSITION_COVER_STATES = ("unavailable", "unknown")

# The device-frame position each observed ``cover.*`` service implies (#1138).
# HA defines these services in the wire frame regardless of ACP's own
# ``inverse_state`` — that option describes how ACP's logical numbers map ONTO
# this same frame, so an external caller's ``close_cover`` is wire 0 on every
# install. ``set_cover_position`` carries its number in the call itself, hence
# the ``None`` placeholder.
_EXTERNAL_POSITION_SERVICES: dict[str, int | None] = {
    SERVICE_CLOSE_COVER: POSITION_CLOSED,
    SERVICE_OPEN_COVER: POSITION_OPEN,
    SERVICE_SET_COVER_POSITION: None,
}

# Every ``cover.*`` service ``async_check_cover_service_call`` reacts to: the
# stop that feeds manual-override detection plus the three position services the
# external-command interlock corrects. Derived from the mapping above so a new
# position service is added in exactly one place.
_EXTERNAL_COVER_SERVICES: frozenset[str] = frozenset(
    {SERVICE_STOP_COVER, *_EXTERNAL_POSITION_SERVICES}
)


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

# Skip-record label written when a cover is left alone because a manual
# override is live.  Mirrors the reason code the manual-override gate inside
# ``CoverCommandService.apply_position`` emits on the normal (non-forced) path,
# so a cover pre-filtered out of a forced dispatch renders identically in
# ``last_skipped_action``, diagnostics and the Lovelace card.
_MANUAL_OVERRIDE_SKIP_LABEL = "manual_override"

# Control methods whose held ``PipelineResult.position`` is the INSTANCE MEAN
# rather than any cover's calculated target — that, and only that, is why
# ``_async_force_send_pipeline_position(honor_holds=True)`` routes through
# ``_dispatch_to_cover``.  Both derive their position from
# ``snapshot.current_cover_position`` (``pipeline/handlers/group_lock.py:43``,
# ``pipeline/handlers/motion_timeout.py:40``), which on a multi-cover instance
# is the arithmetic mean of every bound cover.  Dispatching it would break the
# hold AND drive each cover to a number that is nobody's position.
#
# The mean hazard is unchanged for MOTION: that handler publishes its hold
# through ``PipelineResult.position``, sets no ``held_position``, and is
# therefore never judged per cover.  For a GROUP_LOCK or MANUAL hold on a cover
# type whose entities move independently, issue #1174 moved the whole dispatch
# decision off the mean and onto ``PipelineResult.hold_clamp_verdicts``: each
# bound cover is judged on its own position, commanded (or held) on its own
# verdict, sent to its own resolved target, and its skip record written with its
# own position.  ``position`` remains the shared summary the trace and the
# singular surfaces carry — it is no longer what reaches a judged cover.  A
# coupled cover type (its rails are one geometry, not N opinions) produces no
# verdicts and keeps the shared target, mean hazard and all.
#
# ``ControlMethod.MANUAL`` is deliberately NOT a member and must not be added.
# The manual-override handler's position is ``compute_solar_position`` /
# ``compute_default_position`` (``pipeline/handlers/manual_override.py:39,:51``)
# — the genuine calculated position — so the mean hazard does not exist for it.
# Its hold is also instance-wide (it fires on ``snapshot.manual_override_active``,
# i.e. *any* cover is manual) while the ``respect_manual_override`` pre-filter is
# per-cover: honouring it would suppress every cover on the instance, including
# the ones the pre-filter deliberately kept.  The pre-filter owns MANUAL.
_INSTANCE_MEAN_POSITION_HOLDS: frozenset[ControlMethod] = frozenset(
    {ControlMethod.GROUP_LOCK, ControlMethod.MOTION}
)


def _revoke_stale_closed_clock_licences(cmd_svc) -> None:
    """Retire both outside-window licences on a "nothing admitted" exit.

    Shared by every closed-clock exit that returns before ``apply_position``
    runs — currently ``async_handle_state_change`` (#1311/#1312) and
    ``_async_force_send_pipeline_position`` (#1313). Neither ``is_safety``
    writer runs on those exits, so a booking made before the clock closed
    would otherwise ride forward licensed forever and reconciliation step 4
    resends it overnight.

    Two licences expire here, in this exact order:

    1. The safety verdict (#1311). This call is the same unconditional
       revoke the three hysteresis gates apply; it strips the flag and
       leaves every target, exactly as a cycle that HAD reached
       same_position would.
    2. The outside-window constraint licence (#943 item B): the slot
       released or the bound is already satisfied, and target and licence
       die together.

    Order matters. A row carrying both flags is spared by the sweeper while
    it still reads as safety, and would keep an outside-window licence that
    step 4 admits just as readily. Revoking first lets the sweeper see the
    truth and retire both in one cycle instead of leaking until the next
    one.

    A module-level function rather than a coordinator method: both call
    sites are exercised by unit tests that drive
    ``AdaptiveDataUpdateCoordinator`` methods with a bare ``MagicMock()`` as
    ``self`` (see ``tests/test_override_expiry_time_window.py``). A
    ``self.<name>()`` call against that kind of double resolves to an inert
    auto-mock, not this body — taking ``cmd_svc`` directly keeps the shared
    logic real under that test technique.
    """
    cmd_svc.revoke_safety_verdicts()
    cmd_svc.clear_outside_window_targets()


class AdaptiveDataUpdateCoordinator(DataUpdateCoordinator[AdaptiveCoverData]):
    """Adaptive cover data update coordinator."""

    config_entry: AdaptiveConfigEntry

    # The shared dispatch queue this entry belongs to, or None when the cover
    # names no queue — which is the overwhelmingly common case (issue #1189).
    # Declared at class level, with the unqueued default, so it is a real
    # attribute of the type: ``__init__`` only ever narrows it, and every
    # consumer (diagnostics, the command service) can read it unconditionally.
    _command_queue: CommandQueue | None = None

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
        # Read once and never refreshed — unlike start_value/end_value/
        # normal_list/new_list, which _update_options re-reads every cycle. Safe
        # only because CONF_INTERP is not in _RUNTIME_APPLICABLE_OPTIONS, so
        # changing it reloads the config entry and re-runs this __init__. That
        # matters more since #1230: PipelineSnapshotBuilder reads CONF_INTERP
        # fresh out of options on every build, and a stale flag here would have
        # the judge un-map a read that _to_cover_frame then declines to re-map.
        # Pinned by test_snapshot_builder.py::
        # test_toggling_interpolation_reloads_rather_than_patching_a_live_coordinator.
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
        # Initialised here so the manual-override input-template handler
        # (registered as a template tracker during setup, with awaits before
        # the first _update_options) never hits an AttributeError if it fires
        # before _update_options assigns the real value (issue #974).
        self.manual_override_input_template: str | None = None
        # Cover engine object — populated at start of each update cycle
        self._cover_data = None

        # The ``acp`` self-reference render context (issue #1159), built once
        # here — before every collaborator that renders an option template — and
        # threaded into all of them, so every render site shares one context.
        # Nothing is cached inside it: each key access re-reads the entity
        # registry, so a rename lands on the next render.
        #
        # Two shapes. Condition/tracked fields get the entity_id forms only:
        # ``acp_state``'s value reads are invisible to ``RenderInfo``, so a
        # tracked template written against them would register no state
        # listeners and silently lose its sensor-grade immediacy. The untracked
        # numeric threshold renders are the one flavour that also gets it.
        self._template_variables = build_acp_template_variables(
            self.hass, self.config_entry.entry_id
        )
        self._template_variables_with_state = build_acp_template_variables(
            self.hass, self.config_entry.entry_id, include_state=True
        )

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
        # Populate the manager's cover set at construction so the manual-override
        # restore callback (fires during platform setup, before first_refresh) sees
        # the configured covers instead of an empty set (issue #1019).
        self.manager.add_covers(self.entities)
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
            hass=self.hass,
            logger=self.logger,
            event_buffer=self._event_buffer,
            template_variables=self._template_variables,
        )
        # Weather override tracking
        self._weather_mgr = WeatherManager(
            hass=self.hass,
            logger=self.logger,
            event_buffer=self._event_buffer,
            template_variables=self._template_variables,
        )
        # Cloud-suppression smoothing — hysteresis latch + hold-time debounce
        # (issue #864). Consumes provider booleans; never reads HA directly.
        self._cloud_mgr = CloudSuppressionManager(
            logger=self.logger, event_buffer=self._event_buffer
        )
        # Climate-mode temperature smoothing — four Schmitt latches + a hold-time
        # debounce over the season crossings (issue #917). Same shape as the
        # cloud manager; consumes provider booleans only, never reads HA.
        self._climate_smoothing_mgr = ClimateSmoothingManager(
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
        # Non-sensor health checks (issue #975): controlled-cover + sun.sun
        # availability (entity-shaped, on SensorHealthManager) and config-coherence
        # predicates (envelope, time window) on the RepairManager sibling. Same
        # informational contract and debounce as the temp watch. Issue keys are
        # per-config-entry namespaced so each cover instance owns its Repairs.
        self._repair = RepairManager(self.hass, self.logger, domain=DOMAIN)
        entry_id = self.config_entry.entry_id
        self._sun_issue_key = f"{ISSUE_SUN_UNAVAILABLE}_{entry_id}"
        self._envelope_issue_key = f"{ISSUE_CONFIG_POSITION_ENVELOPE}_{entry_id}"
        self._custom_position_issue_key = (
            f"{ISSUE_CUSTOM_POSITION_OUT_OF_RANGE}_{entry_id}"
        )
        self._time_window_issue_key = f"{ISSUE_CONFIG_TIME_WINDOW}_{entry_id}"
        # B3 (issue #1115): entry-scoped like B1/B2 — the cover-type policy owns
        # the "is a bound role entity unfilled?" decision, so no cover-type
        # knowledge lands here.
        self._role_entity_issue_key = f"{ISSUE_DAY_NIGHT_MIDDLE_RAIL_UNSET}_{entry_id}"
        # Namespaced cover-availability watch keys currently registered, so a
        # cover dropped from config gets unwatched (its Repair cleared) next cycle.
        self._cover_issue_keys: set[str] = set()
        # A2 (issue #990): per-entity "commanded but not reaching target" Repair
        # keys currently tracked, so a cover dropped from config gets its
        # predicate cleared next cycle (symmetric with _cover_issue_keys).
        self._a2_issue_keys: set[str] = set()
        # A3 (issue #991): per-entity "tilt cover type on a non-tilt device"
        # Repair keys currently tracked, cleared symmetrically when a cover is
        # dropped from config (same shape as _a2_issue_keys).
        self._a3_issue_keys: set[str] = set()
        # One-shot: the first health-check cycle of this lifetime sweeps the issue
        # registry for A1 Repairs orphaned by a prior lifetime (removing a cover
        # reloads the entry, so a cross-lifetime orphan is in neither the fresh
        # ``desired`` set nor the in-lifetime unwatch loop). Mirrors the base's
        # ``_reconciled`` first-pass philosophy — see _evaluate_health_checks.
        self._a1_orphans_swept = False
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
        self._climate_provider = ClimateProvider(
            hass=self.hass,
            logger=self.logger,
            template_variables=self._template_variables,
        )

        # Renders templated threshold options to numbers once per cycle (#577).
        self._template_resolver = TemplateResolver(
            self.hass, self._template_variables_with_state
        )
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

        def _resolve_window_sunrise() -> dt.datetime | None:
            """Return this instance's sunrise boundary as naive-local wall-clock time.

            The window's single reading of "sunrise", feeding both
            ``TimeWindowManager`` consumers: the blank-start anchor so a blank
            start time bounds the window at sunrise rather than midnight once
            an end bound is configured (issue #1256), and the opt-in
            ``sunrise_gates_start`` floor on a real start (issue #1340).

            It routes through ``read_sun_boundaries`` — the same definition
            ``compute_effective_default`` and the manual-override deadline use
            — so a configured ``sunrise_time_entity`` / ``sunrise_offset``
            governs the window too. Reading pure astral here (the pre-#1340
            shape) made those two options silent no-ops for the gate, the same
            class of bug as #1048.

            Resolved lazily on each call — a fresh ``SunData`` read at
            evaluation time, not a value captured once at startup — and fails
            open to ``None`` on any error, which both consumers treat as "no
            sunrise available" (the pre-#1256 behaviour).
            """
            try:
                sun_data = self._sun_provider.create_sun_data(
                    self.hass.config.time_zone
                )
                boundaries = read_sun_boundaries(
                    self.hass, self.config_entry.options, sun_data
                )
                return _utc_naive_to_local_naive(boundaries.sunrise)
            except Exception:  # noqa: BLE001 — fail open to pre-#1256 behaviour
                return None

        # Time window manager (start/end time checks). Built here, ahead of the
        # snapshot builder, because the builder's effective-default fallback
        # reads the live window state off it (issue #1055).
        self._time_mgr = TimeWindowManager(
            hass=self.hass,
            logger=self.logger,
            event_buffer=self._event_buffer,
            template_variables=self._template_variables,
            sunrise_provider=_resolve_window_sunrise,
        )

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
            time_mgr=self._time_mgr,
            template_variables=self._template_variables,
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

        # Instance-language reason-template overlay (issue #882). Primed once in
        # async_setup_entry via reason_i18n.async_prime(hass.config.language) and
        # threaded into the DiagnosticContext + read by sensor.py so decision-trace
        # reason strings render in the user's language. ``None`` → English defaults.
        self._reason_labels: dict[str, str] | None = None

        # Track position explanation for change detection logging
        self._last_position_explanation: str = ""

        # (entity_id, unit) last WARNED about for the estimated-solar-gain
        # irradiance-unit refusal (issue #1280 Fix 4), so the once-a-cycle
        # ``build_diagnostic_data`` call logs a refusal ONCE rather than every
        # cycle forever. ``None`` means nothing is currently being warned
        # about — reset there whenever the unit becomes acceptable again, so a
        # user who fixes and later re-breaks their sensor sees the warning
        # again instead of it staying silenced from the first occurrence.
        self._irradiance_unit_warned: tuple[str | None, str | None] | None = None

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
        # Seeded so the end-time sensor and the reboot-restore path — both of
        # which can reach expiry_for() before the first _update_options cycle —
        # read a real mode rather than raising AttributeError; refreshed each
        # cycle (issue #1051). ManualOverrideSlice is the single source for the
        # coordinator: no *runtime* consumer re-reads
        # CONF_MANUAL_OVERRIDE_DURATION_MODE from options. The config/options
        # flow, the field schema and the service validator still read the raw
        # key — correctly, since none of them has a coordinator to read from.
        self.manual_override_duration_mode = _rc_attach.manual_override.duration_mode

        # Named dispatch queue (issue #1189). Resolved once, at setup: the queue
        # is cross-entry shared state, so it is looked up in the hass.data
        # registry and refcounted rather than constructed here. Deliberately
        # absent from ``_RUNTIME_APPLICABLE_OPTIONS``, so changing the
        # assignment full-reloads the entry — it is setup wiring (this
        # constructor argument, this refcount), not a value the running
        # coordinator can re-read.
        _queue_name = normalize_queue_name(_rc_attach.tracking.command_queue)
        if _queue_name:
            self._command_queue = get_command_queue(self.hass, _queue_name)
            self._command_queue.attach()
            self.config_entry.async_on_unload(self._command_queue.detach)

        # Cover command service — self-contained: owns positioning, target tracking,
        # and the reconciliation timer (started in async_config_entry_first_refresh).
        # on_tick keeps time window transition checks running on the same 1-min interval
        # without needing a separate HA timer.
        self._cmd_svc = CoverCommandService(
            hass=self.hass,
            logger=self.logger,
            cover_type=self._cover_type,
            # Share THIS entry's policy object rather than letting the manager
            # build a private one. Anything the manager asks a stateful policy
            # — the Model C rail order and travel clearance its reconciliation
            # pass consults — is only answerable on the instance the dispatch
            # path primes and ``attach``es (issue #1115).
            policy=self._policy,
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
            # Clock the post-command window for time-based override detectors,
            # and start the travel-ramp republish tick if this dispatch created
            # a plan.
            on_command_sent=self._on_command_sent,
            # Travel-time row for one entity, read live from options at each
            # dispatch. A per-cycle snapshot would serve stale numbers for a
            # whole cycle after a calibration run or a manual edit rewrites the
            # table out-of-band, and the table changes far too rarely to be
            # worth caching.
            get_travel_calibration=lambda eid: (
                self.config_entry.options.get(CONF_TRAVEL_TIME_CALIBRATION) or {}
            ).get(eid),
            command_queue=self._command_queue,
        )

        # Wire the manual-override engine's edge + origin seams once. Any
        # detection channel that flips a cover into manual override fires
        # on_engaged → discard the latched command target (issue #215/#216);
        # every current and future detector inherits this without coordinator
        # changes. Clearing an override discards it too (issue #1052) — the
        # target the override itself latched must not survive the cancel, or
        # reconciliation drives the cover back to it whenever the post-cancel
        # cycle's corrective command is suppressed by the delta gates. The
        # ACP-origin predicate lets detectors distinguish ACP-issued context
        # ids from genuine user actions.
        self.manager.set_transition_callbacks(
            on_engaged=self._cmd_svc.discard_target,
            on_cleared=self._cmd_svc.discard_targets,
        )
        self.manager.set_acp_context_predicate(self._cmd_svc.was_acp_position_context)
        # Issue #888: drop the display-only assumed position when the override
        # resets or a real numeric position read arrives.
        self.manager.set_assumed_invalidator(self._cmd_svc.clear_assumed_position)
        # Issue #1044: supply the duration mode's deadline. Wired here rather
        # than passed to the constructor because the resolver is a coordinator
        # bound method — it resolves lazily at call time, so the wiring stays
        # post-construction regardless of where its collaborators are built.
        self.manager.set_deadline_resolver(self._resolve_override_deadline)

        # Late-bind cover-type policy dependencies (e.g. VenetianPolicy
        # constructs its DualAxisSequencer here once cmd_svc + grace_mgr are
        # available).  Default policies have a no-op attach.
        self._policy.attach(
            hass=self.hass,
            logger=self.logger,
            grace_mgr=self._grace_mgr,
            # The instance's covers. A cover type that binds several entities to
            # distinct physical roles resolves them from this list (the Model C
            # day/night bottom rail is "whichever cover isn't the middle rail",
            # issue #1115). Additive: every other policy ignores it.
            entities=self.entities,
            get_current_position=self._cmd_svc.get_current_position,
            set_commanded_position=self._cmd_svc.set_target,
            position_tolerance=POSITION_TOLERANCE_PERCENT,
            is_dry_run=lambda: self._cmd_svc.dry_run,
            get_state=lambda eid: getattr(self.hass.states.get(eid), "state", None),
            # The target ACP currently has IN FLIGHT on an entity, or None once
            # it settles. A physically coupled cover type uses it as direct
            # evidence that a partner entity has been sent on its way, instead
            # of waiting for an actuator to report a motion it may never publish
            # (issue #1145). The in-flight test is what makes a settled entity's
            # stale target answer None rather than confirm a finished move.
            #
            # Dry run answers None unconditionally: nothing was sent, so there
            # is no motion to be evidence OF. The booking still happens there on
            # purpose — it is what makes the simulated diagnostics look like a
            # real cycle — which is exactly why this accessor, whose whole
            # contract is "a partner is physically travelling", must not read it.
            get_booked_target=lambda eid: (
                None
                if self._cmd_svc.dry_run
                else (
                    self._cmd_svc.get_target(eid)
                    if self._cmd_svc.is_waiting_for_target(eid)
                    else None
                )
            ),
            # The raw target ACP last COMMANDED an entity to, regardless of
            # whether it is still in flight (issue #1154). Unlike
            # ``get_booked_target`` above, this accessor's contract is not
            # "evidence of physical travel" — it is "what did ACP last book for
            # this rail", used only to decide what VALUE a correction that is
            # already going ahead should send. A failed dispatch's
            # ``HomeAssistantError`` handler clears ``waiting`` but deliberately
            # keeps the target (it is still the honest last-commanded value),
            # so gating this on ``is_waiting_for_target`` the way
            # ``get_booked_target`` is would make a correction blind to that
            # kept value in exactly the case it must read it.
            #
            # NOT gated on dry_run, unlike ``get_booked_target``: dry run books
            # a target through the identical ``set_target`` call a live cycle
            # makes (before the dry-run gate short-circuits the actual service
            # call), so the value is equally meaningful bookkeeping either way,
            # and this accessor never feeds a suppression decision that a
            # fictional "clearing" could corrupt — it only picks a number for a
            # correction plan that ``_booked_clears`` has already decided must
            # be built. Gating it would just make dry-run's simulated
            # diagnostics diverge from what the same cycle does live.
            get_command_target=lambda eid: self._cmd_svc.get_target(eid),
            get_current_tilt_position=lambda eid: state_attr(
                self.hass, eid, "current_tilt_position"
            ),
            event_buffer=self._event_buffer,
            tilt_skip_above=_rc_attach.venetian.tilt_skip_above,
            venetian_tilt_skip_mode=_rc_attach.venetian.tilt_skip_mode,
            venetian_mode=_rc_attach.venetian.venetian_mode,
            venetian_tilt_only_scope=_rc_attach.venetian.tilt_only_scope,
            post_settle_hold_seconds=_rc_attach.venetian.post_settle_hold_seconds,
            post_settle_mode=_rc_attach.venetian.post_settle_mode,
            backrotate_publish_lag_seconds=(
                _rc_attach.venetian.backrotate_publish_lag_seconds
            ),
            # Lets a dual-axis policy report its own tilt frames to this
            # instance's command queue (issue #1189). The settle+tilt tail runs
            # outside the queue slot by design, so without this the queue would
            # believe the air was free while the tail was still keying it.
            mark_air_busy=self._cmd_svc.mark_queue_external_transmit,
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

        # Travel-time calibration. Built here because it drives covers through
        # the command service and reads the same policy the dispatch path uses;
        # it stays idle until something asks it to run.
        self._travel_calibrator = TravelTimeCalibrator(
            self.hass,
            self.logger,
            cmd_svc=self._cmd_svc,
            policy=self._policy,
            # Resolved live, not captured: a run can start long after setup, and
            # the cover list and rail roles may have changed in between.
            get_entities=lambda: list(self.entities or []),
            get_options=lambda: self.config_entry.options,
            persist=self._async_persist_travel_calibration,
            on_progress=self.async_update_listeners,
        )

        # Window-transition tracker — owns sun-visibility and astronomical
        # sunset-window transition state (extracted from coordinator in Phase E).
        self._window_tracker = WindowTransitionTracker(
            hass=self.hass,
            logger=self.logger,
            event_buffer=self._event_buffer,
            sunset_window_open_fn=self._sunset_window_is_open,
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

        # Cancel handle for the travel-ramp republish tick. Non-None ONLY while
        # at least one cover has a live travel plan — see ``_sync_travel_tick``.
        self._travel_tick_unsub: Callable[[], None] | None = None

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

        # Issue #1012: cancel handle for the single ``async_call_later`` wake
        # that re-runs the update cycle the moment a custom-position slot's
        # per-input hold falls back to its own fresh (possibly different)
        # reading — the same "otherwise nothing else would trigger it until
        # the next state-change/periodic refresh" gap #742 closed for the
        # daytime gate.
        self._custom_position_hold_unsub: Callable[[], None] | None = None
        self._sun_tracking_gate_unsub: Callable[[], None] | None = None

        # Issue #1138 (re-keyed for #1156 / #1144 item 2): in-flight
        # external-command interlock corrections, keyed by the UNORDERED PAIR
        # of entities the correction moves — not by whichever one's command is
        # being re-issued. One per pair — a fresh plan touching either rail of
        # an already-in-flight pair supersedes (cancels) the previous
        # correction rather than racing it, whether the two commands named the
        # same entity or opposite ends of the same pair. Entry-scoped tasks,
        # cancelled on shutdown so nothing is left pending after an unload.
        self._external_interlock_tasks: dict[frozenset[str], asyncio.Task] = {}

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
        """Route an externally-issued ``cover.*`` call on a tracked entity.

        Two independent responses hang off one EVENT_CALL_SERVICE subscription,
        because both need the same four answers first — is it a cover service we
        care about, which entities did it name, are any of them ours, and did ACP
        issue it? That parse is stated once here and each branch adds only its
        own question.

        ``stop_cover`` → manual-override detection. If the call was NOT
        originated by ACP (per ``_cmd_svc.was_acp_stop_context``) and a
        ``my_position_value`` is configured, the cover is flagged as manually
        overridden. This path covers non-position-capable covers (e.g. Somfy RTS)
        where pressing STOP moves to the hardware "My" preset without ever
        reporting a new position — the normal state-change detection is blind
        to it.

        ``open_cover`` / ``close_cover`` / ``set_cover_position`` → the
        external-command interlock (#1138). A position command that never passed
        through ACP's dispatch seam is not ordered or gated against a physically
        coupled entity, so the owning cover-type policy is asked whether a
        partner entity blocks it and, if so, what would clear the way. Purely
        corrective: EVENT_CALL_SERVICE fires as the call executes, so there is
        nothing left to veto by the time we see it.
        """
        data = event.data
        if data.get("domain") != "cover":
            return
        service = data.get("service")
        if service not in _EXTERNAL_COVER_SERVICES:
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

        if service != SERVICE_STOP_COVER:
            self._plan_external_interlock(event, service, service_data, tracked)
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
            # Issue #1225: an external stop is a user action, not a safety
            # decision, so revoke any safety licence the row still carries
            # from an earlier, still-in-flight safety dispatch — unconditional
            # on ``engaged``, since the My number now booked is a user action
            # either way.
            self._cmd_svc.revoke_safety_verdict(entity_id)
            # Issue #888: when the stop actually engaged the #875 override (not a
            # mid-move stop), record My as the display-only assumed position so
            # the card shows My. Confined to covers with no native position axis
            # by the shared helper's caps predicate; the my_position_value gate
            # above already restricts this to configured-My instances.
            if engaged:
                self._cmd_svc._record_assumed_if_blind(
                    entity_id, int(my_position_value)
                )

    @staticmethod
    def _interlock_pair_key(plan: ExternalInterlockPlan) -> frozenset[str]:
        """Return the unordered pair of entities a correction moves (#1156 / #1144).

        Derived entirely from the plan the policy already produced — no
        cover-type knowledge, no "these are rails" — so both writers of
        ``_external_interlock_tasks`` (and its shutdown drain) can key on the
        pair without the coordinator learning anything cover-type-specific.
        """
        return frozenset({plan.leading_entity_id, plan.follower_entity_id})

    def _start_interlock_task(
        self, plan: ExternalInterlockPlan, **kwargs: Any
    ) -> asyncio.Task:
        """Register a correction, superseding whatever still holds this pair.

        Single source of truth for the cancel-prior / register / name-the-task
        policy (CODING_GUIDELINES.md § No Code Duplication): the external
        listener (:meth:`_plan_external_interlock`, fire-and-forget) and the
        user seam (:meth:`_interlock_user_command`, which awaits the returned
        task so it can hand back the correction's own outcome) both delegate
        here instead of each keeping its own copy of the block.

        Keyed by :meth:`_interlock_pair_key`, not by ``plan.follower_entity_id``
        alone — a command naming either end of an already-in-flight pair must
        supersede the correction in progress, not just a second command naming
        the same entity (#1156, subsuming #1144 item 2). ``**kwargs`` forwards
        ``stop_follower`` / ``mark_override`` straight through to
        :meth:`_execute_external_interlock` so a caller's distinguishing
        arguments are never dropped by this shared seam.
        """
        key = self._interlock_pair_key(plan)
        prior = self._external_interlock_tasks.get(key)
        if prior is not None and not prior.done():
            prior.cancel()
        task = self.config_entry.async_create_task(
            self.hass,
            self._execute_external_interlock(plan, **kwargs),
            name=f"acp_rail_interlock_{plan.follower_entity_id}",
        )
        self._external_interlock_tasks[key] = task
        return task

    def _plan_external_interlock(
        self,
        event: Event,
        service: str,
        service_data: dict,
        tracked: set[str],
    ) -> None:
        """Ask the policy whether an external position command needs correcting.

        The position half of :meth:`async_check_cover_service_call` (#1138).
        Cover-type-agnostic by construction: it maps the observed service onto a
        device-frame number, asks ONE polymorphic hook, and executes whatever
        plan comes back. The base hook answers ``None`` for every cover type
        whose entities are physically independent, so this costs a virtual call
        and nothing else on the other four types.

        Skipping ACP's own position calls is what makes the correction
        loop-safe. ``apply_position`` records its context BEFORE the HA service
        call runs and ``EVENT_CALL_SERVICE`` fires during it, so the stamp is
        always in place by the time the echo arrives here — the same guarantee
        ``was_acp_stop_context`` gives the stop branch, and no new tracking
        mechanism.

        Deliberately does NOT consult ``manual_ignore_external``,
        ``manual_toggle`` or ``automatic_control``. Those govern whether a user
        touch takes OWNERSHIP of a cover; this branch is about whether the
        command the user already sent can physically complete, which is true
        regardless. ``manual_ignore_external`` is still honoured where it
        belongs — the override-marking step inside the executor.
        """
        if event.context and self._cmd_svc.was_acp_position_context(event.context.id):
            self.logger.debug(
                "async_check_cover_service_call: ignoring ACP-originated %s "
                "(context %s)",
                service,
                event.context.id,
            )
            return

        wire_target = _EXTERNAL_POSITION_SERVICES[service]
        if wire_target is None:
            raw_position = service_data.get(ATTR_POSITION)
            if raw_position is None:
                return
            wire_target = int(raw_position)

        for entity_id in sorted(tracked):
            plan = self._policy.plan_external_command_interlock(
                entity_id, service=service, wire_target=wire_target
            )
            if plan is None:
                continue
            # Last writer wins, keyed by the pair: a second genuine external
            # command touching either rail of this pair supersedes the
            # correction still in flight rather than racing it. Fire-and-forget
            # — the listener call stack must not block on a rail-clearance
            # wait — so the task is dropped, not awaited.
            self._start_interlock_task(plan)

    async def _execute_external_interlock(
        self,
        plan: ExternalInterlockPlan,
        *,
        stop_follower: bool = True,
        mark_override: bool | None = None,
    ) -> tuple[str, str] | None:
        """Run the corrective sequence a policy planned (#1138).

        Generic by construction: two entity ids and two device-frame targets. It
        never asks what kind of cover this is, which entity is "upper", or why
        one blocks the other — that is the policy's answer, already given.

        ``stop_follower`` says whether the follower's own command already reached
        the motor. It did on the external path, and a refused one can leave the
        entity latched in ``closing`` indefinitely, so that latch is cleared
        before the re-issue. It did NOT on the user-seam path
        (:meth:`_interlock_user_command`): the clearance gate withheld
        the command, so there is no latch, and stopping anyway is a live side
        effect rather than a no-op — on an open/close-only motor a
        stop-while-stationary is the hardware's "go to My" gesture, which would
        drive the follower somewhere nobody asked for.

        **Both dispatches go through ``_cmd_svc.apply_position``**, the only
        path that consults ``await_dispatch_clearance``. Calling
        ``hass.services.async_call`` here would drive a physically coupled entity
        with no ordering guarantee at all — the exact stall/over-current defect
        #1115 and #1118 closed, re-opened from inside ACP. The targets are passed
        through untouched for the same reason the plan carries wire numbers: they
        already speak the device frame, so ``_to_cover_frame`` / ``_entity_target``
        would double-apply the transforms and re-map the user's own target.

        ``plan.dispatch_token`` is replayed onto both dispatches, opaque —
        exactly as ``apply_position`` itself treats the stamp. It has to travel
        with the plan because these numbers came off a service call rather than
        out of a policy resolve, so the stamp ``capture_dispatch_token`` would
        mint here describes some unrelated earlier dispatch and can un-transform
        the corrective targets in a frame they were never expressed in
        (issues #1115 / #1138).

        **Everything after the leading dispatch is conditional on that dispatch
        having actually been SENT.** ``apply_position`` answers
        ``("skipped", reason)`` for a command it withheld — the integration's
        hard kill switch and dry-run mode both land there — while
        ``apply_user_stop`` consults neither and reaches the motor regardless.
        Running on would interrupt the user's in-flight command on real hardware
        and then decline to complete it, which is worse than the stall this
        whole feature exists to fix and is reachable in ordinary use: a disabled
        ACP is exactly when people drive rails by hand. A withheld leader
        therefore ends the sequence, loudly, on its own event row.

        The stop between the two dispatches is ``apply_user_stop``, not a raw
        service call, so the resulting ``stop_cover`` carries an ACP context
        stamp and :meth:`async_check_cover_service_call` ignores its own echo.
        It exists because a command the hardware refused can leave the follower
        latched in ``closing`` indefinitely (41 minutes in the reporter's
        recorder trace), and re-dispatching on top of that latch does nothing.

        Manual override is engaged once the leader is away and BEFORE the
        follower's re-dispatch — the one that sits in the clearance gate waiting
        on it — so the next pipeline evaluation yields to the manual-override
        handler (priority 80) instead of fighting the correction mid-flight.
        Handlers above it — weather at 90, the safety custom-position slot at
        100 — can still retake the pair on a later cycle, which is the inherited
        and intended precedence. Marking any earlier would hand ACP's tracking
        of BOTH rails away for the whole override duration on the strength of a
        correction that never happened, including the partner rail the user
        never touched.

        ``manual_ignore_external`` gates only that marking: it governs whether an
        external touch takes OWNERSHIP of the cover, never whether the command
        the user already sent is allowed to physically complete. It is read
        BEFORE the marking rather than inside the loop, so the two entities can
        never diverge on it mid-sequence.

        The event rows bracket the sequence on the diagnostics timeline (and
        therefore the Lovelace card), so a rail that moved without being
        commanded — or a correction that was engaged and then abandoned — is
        explainable after the fact rather than mysterious.
        """
        options = self.config_entry.options
        # One context shape for both commands, stated once. ``force`` clears the
        # delta and time gates — this is not a tracking move and must not be
        # deduped against the last calculated target. ``bypass_auto_control`` and
        # ``user_command`` follow the #1128 user-seam doctrine: completing a
        # command the user explicitly sent is a user action, so it lands even
        # with automatic control off and even when ACP's own view already matches
        # the target (#900).
        ctx = {
            entity_id: self._build_position_context(
                entity_id,
                options,
                force=True,
                bypass_auto_control=True,
                user_command=True,
            )
            for entity_id in (plan.leading_entity_id, plan.follower_entity_id)
        }
        row = {
            "leading_entity_id": plan.leading_entity_id,
            "leading_target": plan.leading_target,
            "follower_entity_id": plan.follower_entity_id,
            "follower_target": plan.follower_target,
            "reason": plan.reason,
        }
        self._event_buffer.record(
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "rail_interlock_engaged",
                **row,
            }
        )
        self.logger.info(
            "[%s] %s blocks %s — moving it to %s%% first, then re-issuing %s → %s%%",
            plan.reason,
            plan.leading_entity_id,
            plan.follower_entity_id,
            plan.leading_target,
            plan.follower_entity_id,
            plan.follower_target,
        )

        outcome, detail = await self._cmd_svc.apply_position(
            plan.leading_entity_id,
            plan.leading_target,
            plan.reason,
            ctx[plan.leading_entity_id],
            dispatch_token=plan.dispatch_token,
        )
        if outcome != "sent":
            self.logger.warning(
                "[%s] %s was not dispatched (%s), so %s is left alone: stopping "
                "and re-issuing it would interrupt the user's command without "
                "completing it",
                plan.reason,
                plan.leading_entity_id,
                detail,
                plan.follower_entity_id,
            )
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "rail_interlock_abandoned",
                    "outcome": detail,
                    **row,
                }
            )
            return outcome, detail

        # Whether this correction takes OWNERSHIP of the pair. The external
        # path's answer is ``manual_ignore_external``, the option that governs
        # exactly that question for a touch from outside ACP — so ``None`` means
        # "use it". A caller with its own override contract states the answer
        # instead: ACP's user seams honour ``force``, which documents that the
        # command skips engagement, and reading the external option there would
        # let an option about other people's commands override it.
        if (
            mark_override
            if mark_override is not None
            else not self.manual_ignore_external
        ):
            for entity_id in (plan.leading_entity_id, plan.follower_entity_id):
                self.manager.mark_user_command(entity_id, reason=plan.reason)

        if stop_follower:
            await self._cmd_svc.apply_user_stop(plan.follower_entity_id)
        follower_outcome = await self._cmd_svc.apply_position(
            plan.follower_entity_id,
            plan.follower_target,
            plan.reason,
            ctx[plan.follower_entity_id],
            dispatch_token=plan.dispatch_token,
        )

        self._event_buffer.record(
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "rail_interlock_completed",
                **row,
            }
        )
        return follower_outcome

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
        await self._engage_override_from_input("input_sensor")

    async def async_check_manual_override_input_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Engage manual override when the input *template* renders truthy (#974).

        The template counterpart to the input-sensor edge path: routed from
        ``async_track_template_result``, it fires whenever the rendered result
        changes and engages on the rising edge only (a truthy render). It is a
        single engage-on-truthy trigger — there is NO combine mode and NO
        companion sensor to fold with, so a falsy result or a broken/empty
        template ("no opinion" → ``None``) does nothing. Live state always wins:
        the template is re-rendered here rather than trusting the tracked value.
        """
        if (
            render_condition_or_none(
                self.hass,
                self.manual_override_input_template,
                variables=self._template_variables,
            )
            is not True
        ):
            return
        self.logger.debug(
            "Manual override input template rendered truthy — engaging override on all covers"
        )
        await self._engage_override_from_input("input_template")

    async def _engage_override_from_input(self, reason: str) -> None:
        """Engage manual override on every cover, then refresh once.

        The shared body of both input-trigger paths — the off→on sensor edge
        (``input_sensor``) and the truthy template render (``input_template``,
        #974). Both are rising-edge triggers with no combine semantics, so they
        share one engage-and-refresh implementation rather than mirroring it.
        """
        self.manager.engage_manual_override_from_external(reason=reason)
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

    async def async_check_sun_tracking_gate_template_change(
        self, event: Event | None, updates: list
    ) -> None:
        """Handle the sun-tracking-gate template's rendered result changing (#1167).

        The daytime-gate counterpart above, for the gate that suppresses solar
        positioning rather than the day/night boundary. Same contract: the
        tracked result only signals *that* the template changed, and the
        subsequent refresh re-reads live sensor/template state through
        ``PipelineSnapshotBuilder._resolve_sun_tracking`` so the OR/AND combine
        mode and the grace hold are both honoured.
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

    def _start_climate_temp_hold_timeout(self) -> None:
        """Start the climate-temp season hold-time debounce timer (issue #917)."""

        async def _refresh_with_state_change() -> None:
            self.state_change = True
            await self.async_refresh()

        self._climate_smoothing_mgr.start_hold_timeout(
            refresh_callback=_refresh_with_state_change
        )

    def _reconcile_climate_smoothing(self, readings) -> None:
        """Fold this cycle's readings into the climate-smoothing manager.

        Twin of :meth:`_reconcile_cloud_suppression` — the manager applies the
        four Schmitt latches + the hold-time debounce and resolves the season
        flags threaded into the snapshot. When a transition is pending (hold-time
        non-zero) it asks us to start the hold-timer.
        """
        if self._climate_smoothing_mgr.evaluate(readings) == "should_start_timeout":
            self._start_climate_temp_hold_timeout()

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

    def _evaluate_health_checks(self, options: dict) -> None:
        """Raise/clear informational Repairs for sensor + config health.

        One fail-open guard covers all checks (issue #786, #975) so none can
        break the update cycle. Entity-availability checks (temp sensor, each
        controlled cover, ``sun.sun``) ride the ``SensorHealthManager``; config
        coherence (position envelope, time window) rides the ``RepairManager``
        predicate sibling. Everything is per-config-entry namespaced and cleared
        automatically on recovery.
        """
        try:
            name = self.config_entry.data.get("name", "") or ""

            # Temp sensor (issue #786): watch the effective indoor temp entity
            # only when climate mode is on — an unavailable sensor is not worth
            # nagging about when climate is off. Watching the resolved effective
            # entity covers both explicit and area-resolved cases.
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
                placeholders={"entity_id": effective_temp or "", "name": name},
            )

            # C1 — sun.sun availability. Always watched; the whole integration
            # depends on it, so an unavailable sun entity is always worth flagging.
            self._sensor_health.update_watch(
                self._sun_issue_key,
                "sun.sun",
                translation_key=ISSUE_SUN_UNAVAILABLE,
                placeholders={"name": name},
            )

            # A1 — each controlled cover's availability. Watch every entity under
            # its own namespaced key; unwatch (and clear) any cover dropped from
            # config since last cycle. A cover removed from the registry has no
            # state → already treated unhealthy.
            desired: set[str] = set()
            for eid in self.entities:
                key = f"{ISSUE_COVER_UNAVAILABLE}_{self.config_entry.entry_id}_{eid}"
                desired.add(key)
                self._sensor_health.update_watch(
                    key,
                    eid,
                    translation_key=ISSUE_COVER_UNAVAILABLE,
                    placeholders={"entity_id": eid, "name": name},
                )
            for stale in self._cover_issue_keys - desired:
                self._sensor_health.update_watch(
                    stale, None, translation_key=ISSUE_COVER_UNAVAILABLE
                )
            self._cover_issue_keys = desired

            # B1 — position-envelope coherence, split into two independent
            # predicates (issue #1146) so each Repair's message states only its
            # own condition: min>max and slot-outside-envelope are unrelated
            # misconfigurations that used to share one message asserting both
            # clauses regardless of which actually fired. Consume the canonical
            # min/max resolution from CoverConfig (single source of truth)
            # instead of re-deriving the defaults here, and render the
            # placeholders as plain ints so a HA NumberSelector float doesn't
            # surface as "80.0".
            cover_cfg = CoverConfig.from_options(options)
            min_pos = int(cover_cfg.min_pos)
            max_pos = int(cover_cfg.max_pos)
            # Each Repair gets its own placeholder dict (issue #1146): the
            # custom-position description carries `{slot}` but the envelope's
            # does not, and HA silently drops a description whose placeholder
            # set differs from the key's, so the two must never share one dict.
            envelope_placeholders = {
                "name": name,
                "min": str(min_pos),
                "max": str(max_pos),
            }
            self._repair.update_predicate(
                self._envelope_issue_key,
                self._min_exceeds_max(min_pos, max_pos),
                translation_key=ISSUE_CONFIG_POSITION_ENVELOPE,
                placeholders=envelope_placeholders,
            )
            violating_slot = self._custom_position_out_of_range(
                options, min_pos, max_pos
            )
            custom_position_placeholders = {
                "name": name,
                "min": str(min_pos),
                "max": str(max_pos),
            }
            if violating_slot is not None:
                slot_keys = CUSTOM_POSITION_SLOTS[violating_slot]
                custom_position_placeholders["slot"] = (
                    custom_position_slot_name(options, slot_keys)
                    or f"Slot {violating_slot}"
                )
            self._repair.update_predicate(
                self._custom_position_issue_key,
                violating_slot is not None,
                translation_key=ISSUE_CUSTOM_POSITION_OUT_OF_RANGE,
                placeholders=custom_position_placeholders,
            )

            # B2 — time-window coherence. Only fire when BOTH sides resolve, so an
            # entity-provided-but-unavailable start/end never false-fires.
            start = self._time_mgr.resolved_start_time
            end = self._time_mgr.end_time
            self._repair.update_predicate(
                self._time_window_issue_key,
                start is not None and end is not None and start >= end,
                translation_key=ISSUE_CONFIG_TIME_WINDOW,
                placeholders={
                    "name": name,
                    "start": start.strftime("%H:%M") if start is not None else "",
                    "end": end.strftime("%H:%M") if end is not None else "",
                },
            )

            # A2 — commanded-but-unreached (issue #990). Per-entity like A1;
            # predicate-shaped like B1/B2, so it rides the same RepairManager.
            # is_target_unreached is read-only and False while a cover is still
            # moving (waiting) or under manual override, so a slow cover / a
            # user move never trips. Clear any stale key for a cover dropped
            # since last cycle (symmetric unwatch).
            # Per-entity guard (issue #990 audit): the A2 block runs BEFORE
            # ``evaluate()``, so a predicate that raises every cycle would abort
            # the whole try and silently starve A1/B1/B2/C1 (they'd be watched
            # but never evaluated/cleared). Wrap each ``is_target_unreached``
            # call so one entity's exception only skips that entity — the loop
            # and the downstream ``evaluate()``/orphan-sweep still complete. The
            # outer fail-open guard stays as belt-and-suspenders.
            a2_desired: set[str] = set()
            for eid in self.entities:
                try:
                    unreached = self._cmd_svc.is_target_unreached(eid)
                except Exception:  # noqa: BLE001 — one entity must not starve the rest
                    self.logger.debug(
                        "A2 predicate failed for %s; skipping", eid, exc_info=True
                    )
                    continue
                key = f"{ISSUE_COVER_NOT_MOVING}_{self.config_entry.entry_id}_{eid}"
                a2_desired.add(key)
                self._repair.update_predicate(
                    key,
                    unreached,
                    translation_key=ISSUE_COVER_NOT_MOVING,
                    placeholders={"entity_id": eid, "name": name},
                )
            for stale in self._a2_issue_keys - a2_desired:
                self._repair.clear_predicate(stale)
            self._a2_issue_keys = a2_desired

            # A3 — tilt cover type on a non-tilt-capable device (issue #991).
            # Per-entity like A1/A2; predicate-shaped like B1/B2. The "is this a
            # tilt contradiction?" decision lives on the policy
            # (``tilt_capability_contradiction``) so the coordinator never
            # branches on cover type or a hardcoded capability literal. Read RAW
            # ``check_cover_features`` (None-preserving) rather than the masked
            # ``read_single_capabilities`` — a still-loading cover reads None,
            # and skipping it avoids a false contradiction (mirrors B2's "only
            # fire when the reading is real"). Same per-entity guard as A2 so one
            # entity's read/predicate blow-up can't starve the rest before
            # ``evaluate()``.
            a3_desired: set[str] = set()
            for eid in self.entities:
                try:
                    caps = check_cover_features(self.hass, eid)
                    if caps is None:
                        continue  # unreadable → don't claim a contradiction
                    contradiction = self._policy.tilt_capability_contradiction(caps)
                except Exception:  # noqa: BLE001 — one entity must not starve the rest
                    self.logger.debug(
                        "A3 predicate failed for %s; skipping", eid, exc_info=True
                    )
                    continue
                key = (
                    f"{ISSUE_COVER_TILT_UNSUPPORTED}_{self.config_entry.entry_id}_{eid}"
                )
                a3_desired.add(key)
                self._repair.update_predicate(
                    key,
                    contradiction,
                    translation_key=ISSUE_COVER_TILT_UNSUPPORTED,
                    placeholders={"entity_id": eid, "name": name},
                )
            for stale in self._a3_issue_keys - a3_desired:
                self._repair.clear_predicate(stale)
            self._a3_issue_keys = a3_desired

            # B3 — a cover type binds a second entity to a named physical role and
            # that role is unfilled (issue #1115). Entry-scoped like B1/B2, but
            # sourced from a polymorphic policy predicate like A3
            # (``required_role_entity_missing``) so the coordinator never branches
            # on cover type. Today's only such role is the Model C day/night
            # middle rail: unset — or naming a cover outside this instance's list
            # — leaves BOTH rails driven to the bottom rail's position, i.e. the
            # shade silently behaves like a plain vertical blind. Same narrow
            # per-check guard as A2/A3 so a policy blowing up here cannot starve
            # ``evaluate()`` and strand every other Repair.
            try:
                role_entity_missing = self._policy.required_role_entity_missing(
                    options, self.entities
                )
            except Exception:  # noqa: BLE001 — one check must not starve the rest
                self.logger.debug("B3 predicate failed; skipping", exc_info=True)
            else:
                self._repair.update_predicate(
                    self._role_entity_issue_key,
                    role_entity_missing,
                    translation_key=ISSUE_DAY_NIGHT_MIDDLE_RAIL_UNSET,
                    placeholders={"name": name},
                )

            self._sensor_health.evaluate()
            self._repair.evaluate()

            # Per-cover cross-lifetime orphan sweep (issue #975 audit, extended
            # for A2 in #990, A3 in #991). The primary fix for a per-cover Repair
            # is REMOVING the cover, which reloads the config entry → a fresh
            # coordinator with empty ``_cover_issue_keys`` / ``_a2_issue_keys`` /
            # ``_a3_issue_keys``. The removed cover's key is in neither the
            # recomputed ``desired`` sets nor the in-lifetime unwatch loops, so
            # its Repair would orphan until an HA restart. Once per lifetime,
            # enumerate this integration's issues and delete any A1
            # (cover_unavailable), A2 (cover_not_moving), or A3
            # (cover_tilt_unsupported) Repair for this entry that is no longer
            # desired. Runs last (inside the single fail-open guard) so a registry
            # hiccup can never skip A1/A2/A3/B1/B2. The membership filter is
            # critical: a still-configured cover IS in its desired set, so its
            # valid warning is preserved rather than flapped.
            if not self._a1_orphans_swept:
                entry_id = self.config_entry.entry_id
                sweeps = (
                    (f"{ISSUE_COVER_UNAVAILABLE}_{entry_id}_", self._cover_issue_keys),
                    (f"{ISSUE_COVER_NOT_MOVING}_{entry_id}_", self._a2_issue_keys),
                    (
                        f"{ISSUE_COVER_TILT_UNSUPPORTED}_{entry_id}_",
                        self._a3_issue_keys,
                    ),
                )
                registry = ir.async_get(self.hass)
                for reg_domain, issue_id in list(registry.issues):
                    if reg_domain != DOMAIN:
                        continue
                    for prefix, desired_keys in sweeps:
                        if issue_id.startswith(prefix) and issue_id not in desired_keys:
                            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                self._a1_orphans_swept = True
        except Exception:  # noqa: BLE001 — health check must never break the cycle
            self.logger.debug("Health-check evaluation failed", exc_info=True)

    @staticmethod
    def _min_exceeds_max(min_pos: int, max_pos: int) -> bool:
        """Whether the position envelope itself is inverted (issue #975/#1146, B1).

        True only when ``min > max``. Split, along with
        ``_custom_position_out_of_range``, out of the former
        ``_position_envelope_incoherent`` (issue #1146) so this Repair's message
        states only this clause — the sibling condition (a slot pinned outside a
        *coherent* envelope) is that method's job, not this one's.
        """
        return min_pos > max_pos

    @staticmethod
    def _custom_position_out_of_range(
        options: dict, min_pos: int, max_pos: int
    ) -> int | None:
        """Return the first fixed custom-position slot pinned outside the envelope.

        Returns ``None`` if none is (issue #975/#1146, B1). Returns the slot
        *number*, not a bool, so the caller can name which of up-to-10 slots
        violated in the Repair's ``{slot}`` placeholder — the message would
        otherwise give no way to tell which slot is at fault. ``None``
        immediately when ``_min_exceeds_max`` reports the envelope itself
        inverted — that is the sibling predicate's condition to report, and
        delegating to the single seam (rather than re-testing ``min > max``
        here) keeps the two Repairs mutually exclusive so they never
        double-fire for one underlying breakage, and keeps the inversion
        definition in exactly one place. Otherwise the number of the first
        (lowest-numbered) enabled, non-safety slot that would deliver an exact
        (FIXED) cover position pinned outside ``[min, max]`` — deterministic
        when more than one slot violates, since ``CUSTOM_POSITION_SLOTS``
        iterates in slot order. Slots that never deliver a fixed position
        claim — ``use_my``, tilt-only, or a non-FIXED constraint mode (floor /
        ceiling / range) — are exempt: they compose as constraints the
        envelope clamps and cannot conflict with it. Cover-type-agnostic:
        loops the slots generically and delegates the fixed-position
        determination to the shared helper (same seam the pipeline handler
        uses), with no branching on cover type or capabilities.
        """
        if AdaptiveDataUpdateCoordinator._min_exceeds_max(min_pos, max_pos):
            return None
        for slot_number, slot_keys in CUSTOM_POSITION_SLOTS.items():
            if not options.get(slot_keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED):
                continue
            priority = options.get(
                slot_keys["priority"], DEFAULT_CUSTOM_POSITION_PRIORITY
            )
            if priority == CUSTOM_POSITION_SAFETY_PRIORITY:
                continue  # safety slots command outside the envelope by design
            if not custom_position_slot_delivers_fixed_position(options, slot_keys):
                continue  # only exact-position slots can contradict the envelope
            position = options.get(slot_keys["position"])
            if position < min_pos or position > max_pos:
                return slot_number
        return None

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

        # Fold the same fresh readings into the climate-smoothing manager (issue
        # #917): four Schmitt latches + a hold-time debounce resolve the season
        # flags threaded into the snapshot below. Runs before build() so the
        # snapshot sees this cycle's resolved flags.
        self._reconcile_climate_smoothing(self._weather_readings)

        # Health-check Repairs (issue #786, #975): sensor availability + config
        # coherence, all informational and debounced. Runs inside one fail-open
        # guard so no check can break the update cycle.
        self._evaluate_health_checks(options)

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
            climate_temp_flags=self._climate_smoothing_mgr.resolved_flags,
            in_time_window=self.check_adaptive_time,
            # The user's clock alone, kept separate from the gate-folded
            # predicate above (#656) because only the clock decides whether a
            # non-safety result may reach the hardware (#215/#216).
            clock_window_open=self.clock_window_open,
            current_cover_position=self._compute_mean_cover_position(),
            # Same read the mean summarises — the registry needs the per-entity
            # dict to judge each held cover's clamp verdict on its own position
            # instead of on that mean (#1174).
            cover_positions=self._live_cover_positions,
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

        # Issue #1012: same idea for the custom-position per-input hold — arm a
        # single wake at the soonest active hold's expiry, now that this cycle's
        # reads have resolved every slot's GracefulSource state.
        self._schedule_custom_position_hold_wake()

        # Issue #1167: and for the sun-tracking gate, so a HELD verdict fails
        # open to tracking at the exact grace expiry rather than whenever the
        # next incidental update happens to land.
        self._schedule_sun_tracking_gate_wake()

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

    @property
    def _live_cover_positions(self) -> Mapping[str, int | None] | None:
        """This cycle's per-entity RAW cover-frame positions, or None.

        One read serves both consumers: ``_compute_mean_cover_position`` (which
        summarises it) and ``PipelineSnapshot.cover_positions`` (which the
        registry judges each held cover against, #1174). None before the first
        update cycle has built a ``CoverStateSnapshot``.
        """
        snapshot = getattr(self, "_snapshot", None)
        return snapshot.cover_positions if snapshot is not None else None

    def _compute_mean_cover_position(self) -> int | None:
        """Return integer mean of current entity positions, or None if none are available.

        Populates ``PipelineSnapshot.current_cover_position``, which serves two
        roles, both of them summaries or fallbacks — never a per-cover decision:

        * ``MotionTimeoutHandler`` holds covers at it in hold_position mode
          (the instance-mean hazard ``_INSTANCE_MEAN_POSITION_HOLDS`` documents
          still applies there in full);
        * the manual-override / group-lock holds publish it as the "Target
          Position" sensor's scalar, and the registry falls back to it for a
          cover that reports no position of its own.

        Whether each held cover is released to a clamping bound is judged
        against that cover's own entry in ``_live_cover_positions`` (#1174),
        never against this mean.
        """
        positions = [
            p
            for p in (self._live_cover_positions or {}).values()
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
            # Read straight off this cycle's result rather than taken as a
            # parameter (issue #943 item B): the admission is a property of the
            # evaluated pipeline, not of the caller's intent, and every dispatch
            # seam that can fire outside the clock window already went through
            # ``_pipeline_acts_outside_clock_window`` to get here. Never ORed
            # into ``is_safety`` — the two licences stay separate all the way
            # down to ``PerEntityState``.
            outside_window_constraint=bool(
                self._pipeline_result
                and self._pipeline_result.outside_window_constraint_active
            ),
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

    def _verdict_dispatch_target(self, verdict: HoldClampVerdict) -> int:
        """Map ONE per-cover hold verdict onto the wire.

        Every ``HoldClampVerdict.target`` is now a LOGICAL value — either a
        composed bound edge for a cover a bound moved, or the cover's own read
        un-mapped back onto the linear scale for one nothing moved. Issue #1230
        made the second half true: the registry judges through
        ``position_utils.from_cover_frame``, the full inverse of
        :meth:`_to_cover_frame`, so what comes back here is on the logical side
        of the same mapping rather than a raw device number wearing a logical
        label. :meth:`_to_cover_frame` is therefore the whole mapping for both
        — which is what issue #469 was about, where skipping the transforms for
        a floor made "minimum 25 % open" drive an inverse cover to 75 %.

        **The ``own_read`` short-circuit below is REDUNDANT for a read inside
        the calibrated travel of a contracting curve, and load-bearing
        everywhere else.** Keep it. Inside such a travel the un-mapping is exact
        — the inverse of a contraction expands, so rounding the logical value
        moves the re-mapped device value by less than half a point — and
        ``_to_cover_frame(target)`` returns the read the branch would have
        returned anyway. Outside the travel there is nothing to be exact about:
        a shade reading device 0 under a 20–80 curve un-maps to logical 0
        (``np.interp`` clamps to the endpoint), and re-mapping that opens it to
        20. The short-circuit fires there — the target equals the read, because
        both clamped to the same end — and the cover stays put. It does NOT
        absorb the one-point round-trip miss a locally EXPANDING multi-point
        curve can produce (``position_utils.from_cover_frame`` states that
        bound): the branch compares a logical target against a device read, so
        on that curve's expanding leg the two differ and it does not fire —
        device 11 judges to logical 51 and re-maps to 12. The delta gate is what
        absorbs that one point, not this branch. Every
        ``interp_start``/``interp_end`` pair contracts, so the case needs a
        hand-built control-point list to reach at all. Removing the branch
        as "redundant post-#1230" would put a phantom carriage move back into
        exactly the tilt-only command this whole path exists to keep
        positionally inert (#1170 / #1174).

        The equality test is also what tells the two cases apart, and it says it
        in the frame the comparison has to happen in: the target equals the
        cover's own logical read precisely when no bound moved it — the same
        ``fin != eff`` the registry evaluated to set ``released``, before
        ``_command_every_held_cover`` overwrote that field so a tilt clamp could
        command the whole group. Where a bound edge lands exactly on the cover's
        own read the test cannot tell the two apart — and does not need to: the
        equality itself proves the clamp moved this cover nowhere.

        Uncalibrated installs are unaffected: ``_to_cover_frame`` and
        ``from_cover_frame`` both reduce to the same ``flip_if``, so both
        branches return the same number.
        """
        own_read = verdict.held_position
        if own_read is not None and verdict.target == flip_if(
            own_read, inverted=self.position_axis_inverted
        ):
            return own_read
        return self._to_cover_frame(verdict.target)

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
        apply_position directly so they are never blocked by hold mode — the
        one exception being the Apply Calculated Position button (#1045), which
        opts back in via ``_async_force_send_pipeline_position(honor_holds=True)``
        and is routed here only for the ``_INSTANCE_MEAN_POSITION_HOLDS``
        winners, never for a manual-override hold.

        When the winner is a hold and the registry judged each bound cover
        individually (``hold_clamp_verdicts``, issue #1174), *this* cover's
        verdict is the whole answer — both halves of it. Released means a
        command goes out, and it goes to the verdict's own resolved target
        rather than the shared ``state``: the two diverge whenever a floor and a
        ceiling bind different covers, and whenever the TILT axis is what forced
        the dispatch, in which case every cover is commanded to where it already
        is so the slats reach the hardware without moving the carriage. Held
        means the skip record is written — with the cover's own position, not
        the instance mean the singular ``held_position`` carries. Without a
        verdict (a computed winner, a motion hold, a legacy snapshot, a cover
        absent from the dict, or a cover type whose entities do not move
        independently — including one that named its own abstract position via
        ``CoverTypePolicy.hold_reference_position``, #1179) the singular
        ``skip_command`` and ``state`` answer for every cover, exactly as
        before. A coupled type's clamped position is deliberately routed that
        way: ``state`` has already been through ``post_pipeline_resolve`` and
        goes through ``_entity_target`` once, which is what makes a clamped hold
        dispatch the same per-entity wire values as a computed winner at the
        same position.

        Since #943 item B the verdicts can also come from the registry's
        outside-window PSEUDO-hold, where the winning control method is usually
        ``DEFAULT``. A cover the bound did not move then gets the generic
        ``"hold"`` skip label, which is the honest answer: nothing is being sent
        to it, and ``hold_control_method`` in the record says which winner the
        cycle was carrying.
        """
        result = self._pipeline_result
        verdict = (result.hold_clamp_verdicts or {}).get(cover) if result else None
        skip_this = (
            not verdict.released
            if verdict is not None
            else (result is not None and result.skip_command)
        )
        if skip_this:
            label = _HOLD_SKIP_LABEL.get(result.control_method, "hold")
            # Where the cover is actually sitting, in the COVER frame — one
            # frame for every hold type, never "logical when motion won and
            # cover-frame when manual won" (#1028). This extra ships beside
            # ``would_be_position`` (the cover-frame ``state``) and the
            # ``inverse_state_applied`` label, which is what fixes the frame:
            # this whole surface speaks the cover's own numbers.
            #
            # Manual and group-lock holds already carry the raw read in
            # ``held_position``. A motion hold describes the same physical
            # place in ``position``, converted to the logical frame so
            # ``coordinator.state`` can invert it back out — so flip it here
            # rather than pushing the raw value onto ``held_position``. That
            # keeps every hold type's ``held_position`` in one frame, the
            # cover's own (#534 / #809). The registry converts it to logical
            # itself, at the one site that compares it against a floor (#1036).
            #
            # A per-cover verdict carries THIS cover's raw read, in that same
            # cover frame; the singular ``held_position`` is the instance mean
            # and is only the fallback for holds the registry did not judge
            # per cover (#1174).
            held = (
                verdict.held_position if verdict is not None else result.held_position
            )
            if held is None:
                held = flip_if(result.position, inverted=self.position_axis_inverted)
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
        # A verdict names its own wire value — see ``_verdict_dispatch_target``
        # for why the shared ``_to_cover_frame`` seam cannot answer for both
        # halves of one. A cover with no verdict keeps the value the caller
        # already resolved.
        target = (
            self._verdict_dispatch_target(verdict) if verdict is not None else state
        )
        return await self._cmd_svc.apply_position(
            cover, self._entity_target(cover, target), reason, context=ctx
        )

    def _entity_target(
        self,
        cover: str,
        state: int,
        *,
        inverted: bool | None = None,
        interpolated: bool = False,
    ) -> int:
        """Per-entity dispatch target for this cover (identity for most types).

        The pipeline resolves ONE position per cycle, which is then sent to
        every bound entity. A cover type that drives several physical entities
        to different positions from that one value — the Model C day/night
        shade with a separate middle-rail entity, the dual panel's blackout
        panel — remaps here via its polymorphic ``resolve_entity_target`` hook.
        Every other cover type's hook is identity, so this seam never branches
        on the cover type.

        A hold judged per cover (#1174) hands this hook that cover's OWN target
        rather than the shared one — which is safe precisely because overriding
        ``resolve_entity_target`` is one of the three signals
        ``CoverTypePolicy.entities_move_independently`` reads: a remapping
        policy is never judged per cover, so the value arriving here is a shared
        position for exactly the types that transform it, and already
        entity-resolved for the identity ones.

        ``inverted`` and ``interpolated`` name the dispatch frame of ``state``
        so a remapping policy reproduces or undoes it correctly. The main
        pipeline path leaves ``inverted`` ``None`` (the policy reuses its cached
        per-cycle decision that mirrors ``coordinator.state``); every seam that
        dispatches off that cycle names its own frame explicitly (#993 /
        #1027). ``interpolated`` is the half ``inverted`` alone could not
        express — see ``CoverTypePolicy.resolve_entity_target``.
        """
        return self._policy.resolve_entity_target(
            cover, state, inverted=inverted, interpolated=interpolated
        )

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
        sent = await self._async_force_send_pipeline_position(
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

    async def async_force_apply_calculated_position(
        self,
        entities: list[str] | None = None,
        *,
        trigger: str = TRIGGER_FORCE_APPLY_CALCULATED,
    ) -> set[str]:
        """Recompute and force-dispatch the calculated position (issue #1045).

        Backs the Apply Calculated Position button.  An explicit user press is a
        user command, so this bypasses the ``delta_position`` / ``delta_time``
        gates, the Automatic Control gate (the #430 My Position precedent) and
        the clock window.  It does NOT bypass the master kill switch, the
        cover-unavailable boundary (#342) or the same-position / relay-click
        short-circuit (#290/#507/#567/#779), and it leaves covers under a live
        manual override alone — the dedicated Reset Manual Override button is
        the surface for those.  That exclusion is strictly **per cover**: on a
        multi-cover instance one overridden cover does not stop the press from
        moving the rest.  A pre-filtered cover still gets a ``manual_override``
        skip record so diagnostics say why it stayed put.

        **The mean-position pipeline holds are honoured.**  When a live group
        lock (which outranks every handler, weather included) or a motion
        ``hold_position`` wins, every remaining cover is skipped and a hold-skip
        record is written instead of a command: those two winners' ``position``
        is ``snapshot.current_cover_position``, the arithmetic mean of the
        instance's covers, so dispatching it would break the hold *and* send a
        number that is nobody's calculated position.

        A **manual-override hold** is deliberately NOT honoured here, and that
        is not an oversight — see ``_INSTANCE_MEAN_POSITION_HOLDS``.  Its
        ``position`` is the genuine calculated position, and its ``skip_command``
        is instance-wide (it fires when *any* cover is manual), so honouring it
        would cancel the press for the covers the per-cover pre-filter above
        deliberately kept.

        The auto-control bypass rides ``bypass_auto_control``, never
        ``is_safety`` — an ``is_safety`` target would be resent by
        reconciliation outside the time window (#215/#216).

        Args:
            entities: Covers to target.  ``None`` (the button's value) resolves
                ``self.entities`` live inside the shared helper.
            trigger: Reason string recorded against the command.

        Returns:
            Set of entity_ids that were successfully sent to.

        """
        # Recompute first so the forced position reflects current conditions
        # rather than a stale cached state (mirrors async_reset_manual_overrides).
        await self.async_refresh()

        return await self._async_force_send_pipeline_position(
            self.state,
            self.config_entry.options,
            entities=entities,
            trigger=trigger,
            bypass_auto_control=True,
            respect_manual_override=True,
            ignore_clock_window=True,
            honor_holds=True,
        )

    async def _async_force_send_pipeline_position(
        self,
        state: int,
        options: dict,
        *,
        entities: list[str] | None = None,
        trigger: str = "manual_override_cleared",
        bypass_auto_control: bool = False,
        respect_manual_override: bool = False,
        ignore_clock_window: bool = False,
        honor_holds: bool = False,
    ) -> set[str]:
        """Force-send this cycle's pipeline position, past the delta/time gates.

        Single authoritative force-dispatch path.  All gate checks live here so
        no caller needs to duplicate them.  ``force=True`` bypasses the position
        delta, time delta and manual-override gates; ``is_safety=False`` keeps
        the target from persisting across window boundaries (#223).

        **Clock-window guard:** Outside the active-hours window the integration
        has no business repositioning covers.  The normal update cycle sends the
        correct position when the window reopens.

        **Automatic-control guard:** When Automatic Control is OFF the cover
        must stay wherever the user left it.

        Args:
            state: Pipeline position to send.
            options: Config entry options dict.
            entities: Covers to target.  Defaults to ``self.entities`` (all
                covers), but the reset button supplies only the covers it just
                cleared so multi-cover instances are not accidentally moved.
            trigger: Reason string forwarded to ``apply_position`` and recorded
                in ``last_skipped_action``.  Defaults to
                ``"manual_override_cleared"`` (auto-expiry); the reset button
                passes ``"manual_reset"``.
            bypass_auto_control: If True, skip the Automatic-Control guard AND
                set ``bypass_auto_control`` on the built ``PositionContext``.
                The two must move together — skipping only this guard would
                leave ``apply_position`` refusing the command with
                ``auto_control_off``.  Never reach the bypass via
                ``is_safety=True``: that also tags the target so reconciliation
                resends it outside the window (#215/#216).
            respect_manual_override: If True, drop covers under a live manual
                override from the target list *before* dispatch.  Necessary
                because ``force=True`` disables the manual-override gate inside
                ``apply_position`` (#1022/#654).
            ignore_clock_window: If True, skip the clock-window guard and
                dispatch outside the active-hours window.
            honor_holds: If True, route dispatch through ``_dispatch_to_cover``
                — but ONLY when this cycle's winning control method is one of
                ``_INSTANCE_MEAN_POSITION_HOLDS`` (group lock, motion
                hold_position).  Those two are exactly the winners whose
                ``position`` is ``snapshot.current_cover_position``, i.e. the
                arithmetic MEAN of every cover's position on a multi-cover
                instance; sending it would both break the hold and drive each
                cover to a number that is not its calculated position, so the
                cover is skipped and a hold-skip record written instead.
                Any other winner — a MANUAL hold above all, whose position is
                the genuine calculated one and whose hold is instance-wide
                while ``respect_manual_override`` is per-cover — takes the
                direct ``apply_position`` path even under this flag, so a
                partial manual override cannot neutralise the whole instance.
                No pipeline result yet (``None``) likewise means nothing to
                honour.  The default ``False`` reproduces the documented
                forced-transition behaviour: forced transitions, override
                clears and window events call ``apply_position`` directly and
                are never blocked by hold mode.

        Every one of the four flags above defaults to ``False``, which
        reproduces the pre-existing behaviour of the two override-clear callers
        (``async_reset_manual_overrides`` and the auto-expiry branch in
        ``_dispatch_for_cycle``, which passes ``entities=None``) bit-for-bit.
        Only ``async_force_apply_calculated_position`` — the Apply Calculated
        Position button, issue #1045 — sets all four True.

        Returns:
            Set of entity_ids that were successfully sent to (``"sent"``
            outcome).  Callers use this to clear ``wait_for_target`` for
            entities that were gated or skipped.

        """
        # Policy-mandated dispatch order (issue #1115) — applied to an explicitly
        # supplied subset too, since a Model C subset can hold both rails. Name
        # the number and frame this loop fans out so the ordering view can tell a
        # raise from a lower (issue #1118) — the same pair ``_entity_target``
        # gets below.
        target_covers = self._policy.order_for_dispatch(
            entities if entities is not None else self.entities,
            position=state,
        )

        if respect_manual_override:
            # Pre-filter rather than lean on the manual-override gate inside
            # apply_position — ``force=True`` disables that gate, so a cover
            # under a live override would be commanded straight through
            # (#1022/#654).
            kept: list[str] = []
            for cover in target_covers:
                if self.manager.is_cover_manual(cover):
                    self.logger.debug(
                        "Force-send: skipping position command for %s (manual override active/restored)",
                        cover,
                    )
                    # Leave the same diagnostic trace the manual-override gate
                    # inside apply_position writes on the normal path, so
                    # last_skipped_action / diagnostics / the Lovelace card
                    # explain why this cover did not move.  A silent `continue`
                    # makes the button look broken for that cover.
                    self._cmd_svc.record_skipped_action(
                        cover,
                        _MANUAL_OVERRIDE_SKIP_LABEL,
                        state,
                        trigger=trigger,
                        current_position=self._cmd_svc.get_current_position(cover),
                        inverse_state=self._inverse_state,
                    )
                    continue
                kept.append(cover)
            target_covers = kept

        if (
            not ignore_clock_window
            and not self.clock_window_open
            # Same admission as the main dispatch path: a safety result, or an
            # opted-in constraint that actually clamped this cycle (#943 B).
            and not self._pipeline_acts_outside_clock_window
        ):
            # Nothing is admitted this cycle — the same "nothing admitted"
            # exit async_handle_state_change's closed-clock branch handles,
            # reached here instead via _dispatch_for_cycle's auto_expired
            # branch (#1313). See _revoke_stale_closed_clock_licences for the
            # ordering rationale.
            _revoke_stale_closed_clock_licences(self._cmd_svc)
            self.logger.debug(
                "Force-send requested for %s but outside the clock window — "
                "skipping reposition (pipeline position was %s; will apply when "
                "window opens)",
                target_covers,
                state,
            )
            return set()

        if not bypass_auto_control and not self.automatic_control:
            self.logger.debug(
                "Force-send requested for %s but automatic control is OFF — "
                "skipping reposition (pipeline position was %s)",
                target_covers,
                state,
            )
            return set()

        self.logger.debug(
            "Force-sending pipeline position %s to %s",
            state,
            target_covers,
        )
        # ``honor_holds`` applies only to the holds whose position is the
        # instance mean — see ``_INSTANCE_MEAN_POSITION_HOLDS``.  Every other
        # winner (notably a MANUAL hold, whose position IS the calculated one
        # and whose hold is instance-wide while the pre-filter above is
        # per-cover) takes the direct path, so the covers the pre-filter kept
        # still move.  Decided once: the pipeline result is instance-wide, and
        # ``None`` (no result computed yet) simply means no hold to honour.
        pipeline_result = self._pipeline_result
        # An ADMITTED outside-window cycle (#943 item B) routes through the same
        # seam unconditionally — ``honor_holds`` does not enter into it, and both
        # callers that reach here out there (the manual-override auto-expiry
        # branch and ``async_reset_manual_overrides``) leave it False.  Out here
        # the registry has already converted the computed winner into a
        # pseudo-hold, so ``state`` is the instance MEAN and each cover's real
        # answer is its ``hold_clamp_verdicts`` entry: sending the mean would
        # drive a two-cover instance at 80/10 to 45/45 at 03:00 — precisely the
        # hazard ``_INSTANCE_MEAN_POSITION_HOLDS`` exists to prevent, and a
        # violation of the invariant's "every other axis receives the cover's
        # current read".
        #
        # Widening that frozenset is NOT the fix and it was evaluated: it is
        # keyed on ``ControlMethod``, and the pseudo-hold's winner is whichever
        # non-safety handler computed this cycle.  Exactly FOUR can be:
        # DEFAULT most nights, plus CUSTOM_POSITION (a FIXED slot below 100,
        # which wins at 77 and is not safety), MOTION and GROUP_SCENE — the
        # four that neither gate on ``in_time_window`` nor set
        # ``held_position``.  MOTION belongs because only its hold_position
        # branch reads ``in_time_window``; the return-to-default branch it falls
        # through to out here is ungated and sets no ``held_position``
        # (``pipeline/handlers/motion_timeout.py``).  SOLAR, CLIMATE, CLOUD and
        # GLARE_ZONE are NOT among them: every windowed handler returns None
        # once ``in_time_window`` is False, and ``not clock_window_open``
        # implies that.  WEATHER is excluded by ``is_safety``, MANUAL and
        # GROUP_LOCK by ``held_position`` — and on the one snapshot where those
        # two leave it None (no readable cover position) the pseudo-hold
        # declines for that same reason.  So no membership set can express
        # "this cycle was admitted by a constraint": MOTION is already a member
        # here for its own in-window mean hazard while most admitted cycles are
        # DEFAULT, and adding DEFAULT would change the in-window
        # ``honor_holds=True`` press (#1045) for every ordinary default cycle,
        # which has nothing to do with #943.
        #
        # ``outside_window_constraint_active`` is only ever set on a CLOSED-clock
        # cycle and is never co-written with ``is_safety``, so the in-window path
        # and the safety path are both byte-identical.
        outside_window_constraint = bool(
            pipeline_result is not None
            and pipeline_result.outside_window_constraint_active
        )
        route_via_hold_seam = outside_window_constraint or (
            honor_holds
            and pipeline_result is not None
            and pipeline_result.control_method in _INSTANCE_MEAN_POSITION_HOLDS
        )

        sun_just_appeared = self._check_sun_validity_transition()
        sent: set[str] = set()
        for cover in target_covers:
            ctx = self._build_position_context(
                cover,
                options,
                force=True,
                bypass_auto_control=bypass_auto_control,
                sun_just_appeared=sun_just_appeared,
            )
            if route_via_hold_seam:
                # Returns None when the cover is held — no command, hold-skip
                # record already written.  Unpack defensively so a held cover
                # simply never joins ``sent``.
                result = await self._dispatch_to_cover(cover, state, trigger, ctx)
            else:
                result = await self._cmd_svc.apply_position(
                    cover, self._entity_target(cover, state), trigger, context=ctx
                )
            if result is not None and result[0] == "sent":
                sent.add(cover)
        return sent

    def _resolved_target_signature(self) -> tuple | None:
        """Signature of this cycle's resolved cover target (issue #756).

        Captures everything that decides what command would be sent —
        winning handler, final position (post interpolation / inverse-state),
        tilt, and the safety / bypass / skip / floor-clamp / outside-window
        admission flags. Comparing it
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
            result.position_constraint_applied,
            result.outside_window_constraint_active,
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
            await self._async_force_send_pipeline_position(state, options)
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

        **The outside-the-clock-window invariant**, stated once, here, because
        this is the guard that enforces it:

            Outside the user's start/end clock window, a cover moves only for
            (a) a safety result (``is_safety`` — weather, or a priority-100
            slot), or (b) an opted-in slot's active min/max constraint, and then
            only to satisfy that constraint: the constrained axis receives the
            composed bound edge, every other axis receives the cover's current
            read. A non-safety winner's own values (default, sunset, solar,
            climate — position or tilt) never reach hardware outside the clock
            window.

        Clause (b) is issue #943 item B. The second half of it is not enforced
        here but in ``PipelineRegistry.evaluate``, which converts a computed
        winner into a pseudo-hold before this method ever sees the result — so
        by the time dispatch is admitted, the winner's own position has already
        been replaced by where the cover actually is.
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

        # Outside the user's start/end CLOCK window a cover moves only for
        #   (a) a safety result (``is_safety`` — weather, or a priority-100
        #       slot), or
        #   (b) an opted-in slot's active min/max constraint (#943 item B),
        # and in case (b) only to satisfy that constraint: the registry has
        # already rewritten the result so the constrained axis carries the
        # composed bound edge and every other axis carries the cover's current
        # read (the outside-window pseudo-hold). A non-safety winner's OWN
        # values — default, sunset, solar, climate, position or tilt — never
        # reach hardware out here, which is what issues #215/#216/#223 are
        # about and what ``test_state_change_skips_send_outside_time_window``
        # pins. The pipeline still evaluates either way so diagnostics and
        # sensor state stay correct.
        custom_position_sensor_triggered = self._is_custom_position_sensor_trigger()
        acts_outside_window = self._pipeline_acts_outside_clock_window

        if (
            not self.clock_window_open
            and not acts_outside_window
            and not safety_release
        ):
            self.state_change = False
            self._last_state_change_entity = None
            self._custom_position_template_trigger = False
            # Nothing is admitted this cycle — this is "the next closed-clock
            # cycle that finds nothing admitted" the reconciliation docstring
            # names. See _revoke_stale_closed_clock_licences for the ordering
            # rationale (#1311/#1312).
            _revoke_stale_closed_clock_licences(self._cmd_svc)
            self.logger.debug("Outside the clock window — skipping position update")
            return

        # A user-configured bound clamped the winner this cycle (#534).  When
        # manual override holds the cover below an active floor, the clamp must
        # bypass the time/position delta gates so the raise still reaches the
        # cover.  The clamped value is not special in any other way — it is a
        # logical position that `state` interpolates and inverts exactly like
        # any other winner's (#1036).
        floor_clamp = bool(
            self._pipeline_result is not None
            and self._pipeline_result.position_constraint_applied
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
            # An admitted outside-window constraint is the same kind of driver
            # as ``floor_clamp``: a user-configured bound that must actually
            # reach the cover, so the delta gates cannot swallow it. It is
            # already implied by ``floor_clamp`` whenever the POSITION axis
            # bound; naming it separately is what covers the tilt-only case.
            or acts_outside_window
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
                else (self.pipeline_winner_name or "solar")
            )
        # Name the number and frame this loop fans out so the ordering view can
        # tell a raise from a lower (issue #1118). A per-cover hold verdict can
        # send an individual cover somewhere else (#1174), but only for a policy
        # whose ``dispatch_order_key`` is the constant default — overriding that
        # hook is one of the signals which switch per-cover judging off — so for
        # every cover type that actually reads this argument, ``state`` is still
        # exactly what each entity receives.
        for cover in self._policy.order_for_dispatch(self.entities, position=state):
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

            # Issue #1006: pass entity_id so a multi-axis policy can anchor the
            # secondary-axis expected value to what ACP last DISPATCHED for THIS
            # entity, not the mutable per-cycle pipeline result a mid-transit
            # reevaluation may have changed.
            secondary_axis_check = (
                self._policy.secondary_axis_check(
                    self._pipeline_result, self._cmd_svc, entity_id
                )
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

        # Outside the time window, only safety handlers (force override,
        # weather) and an admitted outside-window constraint (#943 item B) are
        # allowed to move covers on startup.  This prevents covers from
        # repositioning when HA restarts at midnight or another time outside the
        # configured operational window.  The per-entity outside-window licence
        # is not persisted across restarts on purpose: this first-refresh
        # admission is what re-establishes it, from a freshly evaluated result.
        if (
            not self.check_adaptive_time
            and not self._pipeline_acts_outside_clock_window
        ):
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
        # Name the number and frame this loop fans out so the ordering view can
        # tell a raise from a lower (issue #1118). A per-cover hold verdict can
        # send an individual cover somewhere else (#1174), but only for a policy
        # whose ``dispatch_order_key`` is the constant default — overriding that
        # hook is one of the signals which switch per-cover judging off — so for
        # every cover type that actually reads this argument, ``state`` is still
        # exactly what each entity receives.
        for cover in self._policy.order_for_dispatch(self.entities, position=state):
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

        Since issue #1167 ``enable_sun_tracking`` no longer changes pipeline
        composition — ``SolarHandler`` is unconditional and declines on the
        snapshot field instead — so the refresh is what actually applies the
        change. No handler factory reads the option any more, making the rebuild
        a no-op for it; the rebuild is kept only because it is cheap, total, and
        the single place composition is derived. Neither entity listeners nor
        cover geometry are affected, which is why this stays a refresh rather
        than a reload.
        """
        self._pipeline = self._build_pipeline()
        self._cached_options = dict(self.config_entry.options)
        # The Sun Tracking switch renders from config_entry.options; notify
        # listeners so service/options-flow changes redraw the entity too.
        self.async_update_listeners()
        self.state_change = True
        await self.async_refresh()

    async def async_apply_travel_calibration_update(self) -> None:
        """Take up a new travel-time table without reloading the entry.

        Registered in ``_RUNTIME_APPLICABLE_OPTIONS``. Without it, every write —
        and a calibration run writes once per pass — would reload the config
        entry, tearing down the very run that produced the numbers.

        No pipeline rebuild: the table changes nothing about how a position is
        decided, only how an in-flight move is rendered. Refreshing
        ``_cached_options`` is what keeps the listener's next reload/apply
        comparison honest.
        """
        self._cached_options = dict(self.config_entry.options)
        self.async_update_listeners()
        await self.async_refresh()

    async def _async_persist_travel_calibration(
        self, table: dict[str, dict[str, Any]]
    ) -> None:
        """Write the finished travel-time table back to the config entry."""
        from .services.options_service import apply_options_patch  # noqa: PLC0415

        await apply_options_patch(
            self.hass, self, {CONF_TRAVEL_TIME_CALIBRATION: table or None}
        )

    async def async_run_travel_calibration(self) -> None:
        """Measure every configured cover's travel time (Calibration menu).

        Runs the covers to their mechanical stops and back — several minutes of
        movement — so it is deliberately reachable only from the options flow,
        never from a dashboard entity that could be pressed by accident.
        """
        await self._travel_calibrator.async_run()

    @callback
    def async_cancel_travel_calibration(self, *, restore: bool = True) -> bool:
        """Stop a calibration run. Returns whether there was one to stop.

        ``restore=False`` is the panic-stop path: stand down without issuing the
        go-home moves, because more movement is exactly what that caller is
        trying to prevent.
        """
        return self._travel_calibrator.async_cancel(restore=restore)

    @property
    def travel_calibration(self) -> TravelTimeCalibrator:
        """The travel-time calibrator, for the options flow and the sensor."""
        return self._travel_calibrator

    def travel_estimated_position(self, entity_id: str) -> int | None:
        """Modelled position of ``entity_id`` mid-move, or None when it is idle.

        Display-only (§3b): never merged into the position the command gates
        read. Consumed by the proxy cover, which republishes it so ordinary HA
        cover cards animate.
        """
        return self._cmd_svc.estimated_position(entity_id)

    @callback
    def _on_command_sent(self, entity_id: str) -> None:
        """React to an outbound command: clock detectors, and start the tick.

        The dispatch chokepoint is also where a travel plan is created, so this
        is the earliest moment the republish tick can be needed.
        """
        self.manager.note_command_sent(entity_id)
        self._sync_travel_tick()

    @callback
    def _sync_travel_tick(self) -> None:
        """Run the 1 s republish timer exactly while some cover is mid-ramp.

        A travel plan is a clock-driven model, so nothing else would prompt a
        redraw between the command and the arrival — the proxy would show one
        frozen frame for the whole move. The timer therefore exists only for the
        duration of actual movement: it must never be left running at idle,
        where it would be a state write per second per instance forever.
        """
        active = self._cmd_svc.has_travel_plans()
        if active and self._travel_tick_unsub is None:
            self._travel_tick_unsub = async_track_time_interval(
                self.hass,
                self._travel_tick,
                dt.timedelta(seconds=TRAVEL_CALIBRATION_TICK_SECONDS),
            )
        elif not active:
            self._stop_travel_tick()

    @callback
    def _stop_travel_tick(self) -> None:
        """Cancel the republish timer if it is running."""
        if self._travel_tick_unsub is not None:
            self._travel_tick_unsub()
            self._travel_tick_unsub = None

    @callback
    def _travel_tick(self, _now: dt.datetime) -> None:
        """Redraw the ramp, then decide whether another tick is warranted."""
        self.async_update_listeners()
        # Self-limiting: the tick after the last plan clears is the one that
        # cancels the timer, so no other code path has to remember to.
        self._sync_travel_tick()

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
        # Per-cycle snapshot of the optional input template (issue #974). The HA
        # template subscription is (re)registered in async_setup_entry on every
        # reload; the template-change handler re-renders this live to engage on
        # the truthy edge.
        self.manual_override_input_template = rc.manual_override.input_template
        # Per-cycle snapshot of what the hold is measured against (issue #1044).
        # The diagnostics block and the deadline resolver both read this mirror
        # — neither re-reads the option (issue #1051). Safe as a mirror: the key
        # is not in _RUNTIME_APPLICABLE_OPTIONS, so changing it reloads the
        # config entry outright rather than patching a live coordinator.
        self.manual_override_duration_mode = rc.manual_override.duration_mode
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
            sunrise_gates_start=rc.time_window.sunrise_gates_start,
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
            severe_template=rc.weather.severe_template,
            severe_template_mode=rc.weather.severe_template_mode,
            severe_sensors=rc.weather.severe_sensors,
            timeout_seconds=rc.weather.timeout_seconds,
            enabled=rc.weather.enabled,
        )
        self._cloud_mgr.update_config(
            enabled=rc.cloud_suppression.enabled,
            hold_time_seconds=rc.cloud_suppression.hold_time_seconds,
        )
        self._climate_smoothing_mgr.update_config(
            enabled=rc.climate_smoothing.enabled,
            hold_time_seconds=rc.climate_smoothing.hold_time_seconds,
        )

        event_buffer = getattr(self, "_event_buffer", None)
        if event_buffer is not None and rc.event_buffer_size != event_buffer.maxlen:
            event_buffer.resize(rc.event_buffer_size)

        # Let the active policy refresh whatever option-derived state it caches
        # for hooks that receive no ``options`` (issue #1114). Deliberately
        # generic: the coordinator hands over the whole dict and knows nothing
        # about which cover types cache what. Runs here, at the coordinator's
        # per-cycle options seam, so the value is resolved on the event loop and
        # before both the pipeline and ``_evaluate_health_checks`` — including on
        # the very first cycle of this coordinator's lifetime. Base
        # implementation is a no-op, so most policies pay nothing.
        self._policy.sync_runtime_options(options)

    def _update_manager_and_covers(self):
        """Update manager with cover entities.

        Registers cover entities with the AdaptiveCoverManager and resets
        manual override state for all covers only when detection is
        explicitly disabled (``manual_toggle is False``); an unset toggle
        means no switch has restored yet and must not destroy state.

        """
        self.manager.add_covers(self.entities)
        # Tri-state: ``None`` means no switch entity has restored yet (the
        # platform-setup window in which ``Integration Enabled`` already fires
        # a coordinator refresh — see switch.py:358). Only an explicit ``False``
        # means the user disabled manual-override detection, and only that may
        # destroy restored override state (#1232 / #1019).
        if self._toggles.manual_toggle is False:
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

    def _build_user_command_snapshot(self, opts: dict) -> PipelineSnapshot:
        """Assemble the off-cycle snapshot a user command is judged against.

        One build serves the whole command: the min-mode floor gather and the
        pipeline-preemption check both read it, and so does
        :meth:`user_dispatch_position` when a fan-out seam asks what it is about
        to dispatch. The instance's floors and their priorities are the same for
        every cover the seam is about to command, so the answer is too.
        """
        return self._snapshot_builder.build(
            opts,
            cover_data=self._cover_data,
            cover_type=self._cover_type,
            climate_readings=self._weather_readings,
            manual_override_active=False,
            motion_timeout_active=self.is_motion_timeout_active,
            weather_override_active=self.is_weather_override_active,
            # Pure property read — the ad-hoc/preemption build must NOT
            # re-evaluate the managers (that would advance latches off-cycle).
            # Two latches it does advance, both arm-on-read and both harmless
            # here: the custom-position per-input hold (#1012), and the
            # sun-tracking gate's grace window (#1167). That one is harmless
            # here: ConditionGate.update_config compares by VALUE, so an ad-hoc
            # build carrying equal options fires no config-change reset, and an
            # earlier anchor only shortens the hold — it fails open sooner, never
            # later. As with the custom-position hold, an ad-hoc build anchors
            # that hold
            # window at the tap rather than at the next regular cycle. That is
            # the documented contract — the window starts at the first
            # indeterminate sighting, and the tap is one — but it also means no
            # hold wake is scheduled here.
            cloud_suppression_active=self._cloud_mgr.is_suppression_active,
            climate_temp_flags=self._climate_smoothing_mgr.resolved_flags,
            in_time_window=self.check_adaptive_time,
            # Same clock/gate split as the update-cycle build — and the user-move
            # clamp is DELIBERATELY WINDOW-AWARE because of it (issue #943 item
            # B, decided). The clamp reads its floors off this snapshot, so a
            # floor that outranks manual override but did NOT opt in stops
            # clamping a service-call position once the clock closes; an
            # opted-in one, and any safety slot, keeps clamping. That is a
            # behaviour change to the #472/#1170 clamp, it is intended, and it
            # moves in the same direction as the #215/#216 invariant: outside
            # the user's hours ACP stays out of the way unless it was told
            # otherwise. Pinned end to end by
            # ``tests/test_pipeline/test_outside_window_constraints.py``
            # ``::test_user_move_clamp_outside_window_follows_the_opt_in``.
            clock_window_open=self.clock_window_open,
            current_cover_position=self._compute_mean_cover_position(),
            # Same read the mean summarises — see the update-cycle build (#1174).
            cover_positions=self._live_cover_positions,
            is_glare_zone_enabled=self._is_glare_zone_enabled,
            cover_capabilities=getattr(
                getattr(self, "_snapshot", None), "cover_capabilities", None
            ),
            group_intent=self.effective_group_intent,
        )

    def _clamp_to_active_floor(
        self,
        requested: int,
        snapshot: PipelineSnapshot,
        *,
        options: Mapping[str, Any],
        trigger: str | None = None,
    ) -> int:
        """Raise a user request to the highest floor that outranks manual override.

        Priority-aware user-move clamp (issue #472): a floor only clamps a
        manual/user command when it strictly outranks manual override — the same
        predicate the preemption check in :meth:`async_apply_user_position` uses,
        and, since #1170, the same one the registry applies to that command's
        ``held_position`` on every later cycle. A default-priority (77) floor
        yields to the manual move; a floor above 80 clamps it up *before*
        dispatch. Composition onto an ordinary computed winner (``registry.py``)
        stays unconditional so auto-rule composition is unaffected (issue #463).

        **Filter, then compose** — not compose, then gate. ``effective_floor``
        takes the max across every active floor, so gating afterwards on the
        winning floor's priority let a sub-priority slot with the higher
        position discard a legitimately-outranking lower one, and no clamp
        applied at all (#1170).

        The threshold is manual override's **effective** priority, resolved
        from ``options`` by the same ``resolve_handler_priority`` the pipeline
        build and the config-flow ladder use. Reading
        ``ManualOverrideHandler.priority`` off the class returns the 80 default
        and silently ignores the 🔀 Handler Priorities step, so a cover with
        manual override raised to 85 had an 82-priority floor clamp a user move
        it should have lost to (#1170).

        ``trigger`` names the command for the log line. Callers that are only
        ASKING what a command would dispatch (:meth:`user_dispatch_position`)
        omit it: same arithmetic, but one press should not log its clamp twice.
        """
        active_floors = gather_active_floors(snapshot)
        manual_priority = resolve_handler_priority(options, ManualOverrideHandler.name)
        binding_floors = outranking(active_floors, manual_priority)
        effective_floor_pos, _binding_floor = effective_floor(binding_floors)
        # ``effective_floor`` returns 0 for an empty set, and 0 is the bottom of
        # the position range, so the max() below is a no-op when every floor
        # yielded — no separate "does a floor apply?" branch is needed.
        clamped = max(int(requested), effective_floor_pos)
        if trigger is None:
            return clamped
        # Reported on BOTH branches, not just the no-clamp one: the mixed case
        # — one floor yields while a higher-priority one still binds — is
        # exactly where "why did it stop at 50 and not 60?" gets asked, and an
        # `elif` would never answer it (#1170).
        yielded_count = len(active_floors) - len(binding_floors)
        yielded_note = (
            f" ({yielded_count} floor(s) yielded to manual override "
            f"at priority {manual_priority})"
            if yielded_count
            else ""
        )
        if clamped != requested:
            _LOGGER.info(
                "%s: requested %d clamped to %d (active min-mode floor)%s",
                trigger,
                requested,
                clamped,
                yielded_note,
            )
        else:
            # Without the note the post-filter number reads as "floor 0", which
            # cannot be told apart from "no floor configured at all".
            _LOGGER.debug(
                "%s: requested %d, floor %d — no clamping needed%s",
                trigger,
                requested,
                effective_floor_pos,
                yielded_note,
            )
        return clamped

    def user_dispatch_position(
        self, requested: int, *, options: dict | None = None
    ) -> int:
        """Return the cover-frame number a user command for ``requested`` will send.

        Every seam that fans a user command out over several covers — the My
        button, ``set_position`` / ``set_tilt`` / ``set_axes``, both group
        sliders — has to hand the policy's dispatch-ordering view the number the
        travel gate will later be asked about, because for a physically coupled
        cover type that number is what tells a raise from a lower (issue #1118).

        It is not ``requested``. An active min-mode floor that outranks manual
        override raises it first (#472), and the calibration curve reshapes it
        after (#1027) — and either transform can flip the direction:

        * a floor above the current position turns a lower into a raise;
        * a DESCENDING interpolation curve (``np.interp`` imposes no
          monotonicity on the user's table) lowers the dispatched value as the
          floor raises the logical one, inventing a raise out of a lowering.

        So this and :meth:`async_apply_user_position` reach that number through
        the SAME clamp and the SAME frame mapping. Ordering and gating cannot
        disagree about the direction of travel because there is only one
        derivation for them to disagree over. The clamp is a property of the
        INSTANCE — the composed floors and their priorities — not of any one
        cover, so a single answer serves the whole fan-out.
        """
        opts = options if options is not None else self._resolved_options
        return self._to_cover_frame(
            self._clamp_to_active_floor(
                int(requested),
                self._build_user_command_snapshot(opts),
                options=opts,
            )
        )

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
        Position button, future external triggers). Runs the min-mode floor
        clamp, the pipeline preemption check, manual-override engagement, and
        dispatch to ``CoverCommandService.apply_position``.

        The clamp and the frame mapping are shared with
        :meth:`user_dispatch_position`, so a fan-out seam can name the number
        this method will really dispatch rather than the one it was handed
        (issue #1118) — the two cannot diverge because there is one derivation.

        ``requested`` is a LOGICAL position — HA's convention, 0 = closed /
        100 = open — like every other user-facing number in this integration.
        It is mapped into the cover's dispatch frame exactly once, here, by
        :meth:`_to_cover_frame`, the same helper ``state`` uses for the
        automatic path (#1027). ``CoverCommandService`` keeps its contract of
        receiving a value that is already transformed.

        Because every caller is an explicit user action, the dispatch always
        bypasses the ``auto_control_off`` gate (``bypass_auto_control=True``):
        "automatic control off" suppresses the integration's own sun tracking,
        not the user directly commanding a cover. This is distinct from the
        internal ``force=True`` callers (solar update, override-clear) that go
        through ``apply_position`` directly and stay blocked when auto control
        is off (issue #293).

        Default behavior (``force=False``): engages manual override and
        consults the pipeline. When a handler with priority strictly greater
        than manual override's **effective** priority (safety slots and group
        locks at 100, weather at its default 90, custom-position slots
        configured above it) wins, the move is dropped and recorded via
        ``CoverCommandService.record_preempted_skip``. Effective, not the class
        default: the 🔀 Handler Priorities step can move manual override
        anywhere in 1–99, and comparing against a hardcoded 80 ignores it
        (#1170).

        ``force=True``: legacy programmatic behavior — skip the pipeline
        preemption check and skip manual-override engagement. Used by the
        ``adaptive_cover_pro.set_position`` service when callers explicitly
        opt in.
        """
        opts = options if options is not None else self._resolved_options
        snapshot = self._build_user_command_snapshot(opts)
        # Shared with ``user_dispatch_position``, which is how the fan-out seams
        # name the number this dispatch will really use rather than the one the
        # user asked for (issue #1118).
        clamped = self._clamp_to_active_floor(
            int(requested), snapshot, options=opts, trigger=trigger
        )
        manual_priority = resolve_handler_priority(opts, ManualOverrideHandler.name)

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
                if winner_priority > manual_priority:
                    _LOGGER.info(
                        "user move on %s preempted by %s (priority %d > %d)",
                        entity_id,
                        winner_name,
                        winner_priority,
                        manual_priority,
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
        # ``clamped`` is logical whether or not the floor raised it: the request
        # is a logical user value and so is the configured floor, so the max()
        # above never leaves that frame (#1036 — this used to skip the mapping
        # on a raise, which dispatched the floor's raw number).
        target = self._entity_target(
            entity_id,
            self._to_cover_frame(clamped),
            # A user command runs the same transform as the main pipeline but
            # off-cycle, so the policy's cached per-cycle decision may describe
            # a different value's frame (#993). Name BOTH halves of the frame
            # this dispatch actually used: ``inverted`` alone cannot say
            # "interpolated, not inverted", and a remapping policy handed that
            # ambiguity drops the calibration curve entirely (#1027).
            inverted=self.position_axis_inverted,
            interpolated=self._use_interpolation,
        )
        # Asked BEFORE the dispatch, and handed the stamp from the resolve two
        # lines above — the very one ``apply_position`` would mint for its own
        # gate, rather than one re-read after an intervening resolve.
        interlocked = await self._interlock_user_command(
            entity_id,
            target,
            dispatch_token=self._policy.capture_dispatch_token(entity_id),
            force=force,
        )
        if interlocked is not None:
            return interlocked
        return await self._cmd_svc.apply_position(entity_id, target, trigger, ctx)

    async def _interlock_user_command(
        self,
        entity_id: str,
        wire_target: int,
        *,
        dispatch_token: Any,
        force: bool = False,
    ) -> tuple[str, str] | None:
        """Clear the way for a user command a coupled entity would block (#1138).

        The user-seam half of the interlock. ``await_dispatch_clearance``
        answers "may ACP drive this entity right now?", and on a coupled cover
        type the honest answer for a SINGLE-entity user command is often "no" —
        the partner entity is standing where the user's target is. Left to the
        gate alone the command is withheld and latched, so it simply never
        happens: no movement, no error, nothing above DEBUG.

        Automatic control never lands there because it resolves BOTH entities
        from one logical position and dispatches them together, so the blocker
        is always already on its way out. An EXTERNAL command doesn't land there
        either — :meth:`_plan_external_interlock` observes it and corrects it.
        Only ACP's OWN user seams were left with a gate and no remedy.

        **Asked BEFORE the dispatch, not after the gate refuses it.** The gate's
        budget buys time for a leading rail that is ALREADY TRAVELLING to get
        out of the way, and it is generous on purpose: a start-confirmation
        window plus the remainder of the full clearance budget (#1140/#1145).
        Spending it here would be pure dead time — the blocker is stationary and
        nothing has been asked to move it, so no reading inside that budget can
        ever release the gate. Measured on real hardware the command sat 60 s
        before the correction was even considered, then took another 19 s of
        legitimate travel; asking first removes the 60 s outright. It also keeps
        the user's own command off the skip record, where a ``policy_deferred``
        row would claim a drop that did not happen.

        Returning the follower's own outcome — rather than dispatching a second
        time behind the correction — is what keeps that true: the executor
        re-issues the user's target itself, through the same gate, so there is
        exactly one command per user action either way.

        **Nothing is re-derived.** The policy's
        ``plan_external_command_interlock`` states the blocked test as the
        complement of the gate's own release inequality, so a plan here means
        the gate would have refused, and no plan means it would have let the
        command straight through. ``wire_target`` is the number
        ``apply_position`` is about to be handed — already through
        ``_entity_target``, already in the device frame — which is exactly the
        space the hook documents for it, and ``dispatch_token`` is the stamp
        that dispatch will be gated against, so both ends resolve it in one
        frame (#993). ``SERVICE_SET_COVER_POSITION`` names the service the
        target WOULD be sent as; the endpoint routing to ``open_cover`` /
        ``close_cover`` happens downstream in ``apply_position`` and changes
        nothing about the number or the geometry.

        **The fan-out case is the policy's to exclude, not this method's.** A
        fan-out seam (the whole-instance ``set_axes``, the group slider, the My
        button) dispatches the pair in policy order, so a follower behind its
        leader has a leader already booked to a clearing target; correcting
        there would command the leader to the FOLLOWER's target and undo the
        user's own fan-out. That exclusion lives in
        ``plan_external_command_interlock``, which answers ``None`` for it,
        because deciding it needs the coupling geometry — and a coordinator-level
        approximation of the same question got it wrong: gating on "is the
        leading entity busy?" suppressed the correction on hardware that reports
        nothing mid-travel, where busy is true for most of every move, leaving
        the command stranded between a correction that declined and a gate that
        refused. This method asks one polymorphic question and acts on the
        answer.

        **Nothing is corrected for a command that could not have been sent
        anyway.** The correction moves an entity the user did not name, so it
        has to be worth something: if this dispatch would be refused outright,
        the only effect left is the partner move, and the user is handed a rail
        they never touched moving while the one they did touch stays put — with
        both of them in manual override for the whole window. Availability is
        the reachable case (a rail drops off a mesh far more often than the
        integration is disabled mid-command) and it is the one gate that is
        settled BEFORE any dispatch could tell us, so it is asked here. Every
        other refusal — kill switch, dry run, no capable service — still lands
        on the leading dispatch and abandons the sequence loudly, which is where
        it belongs.

        ``force`` mirrors the caller's own manual-override contract. A
        ``force=True`` user command documents that it skips override
        engagement, and a correction must not smuggle it back in for BOTH
        entities; a default command engages it, as the caller would have. It is
        threaded rather than re-derived from ``manual_ignore_external``, which
        governs whether an EXTERNAL touch takes ownership and has no authority
        over ACP's own seams.

        **Registered, not awaited inline (#1156).** The correction runs through
        :meth:`_start_interlock_task`, the same pair-keyed registry the
        external listener uses — keyed by the two entities the correction
        moves, not by whichever one's command is being re-issued, so a second
        overlapping user command on EITHER rail of this pair, or a genuine
        external command on either rail, supersedes this correction instead of
        racing it. Awaiting the registered task rather than the inline
        coroutine directly means a supersede can surface here as
        ``asyncio.CancelledError``, which is translated to
        ``("skipped", "interlock_superseded")`` — except when the cancellation
        came from OUTSIDE (an entry unload, HA shutdown cancelling this very
        service-call task), which must propagate untouched rather than read as
        an ordinary skip.

        Returns the follower's dispatch outcome when the correction ran,
        ``("skipped", "interlock_superseded")`` when a later plan superseded it
        first, or ``None`` when the caller should dispatch normally.
        """
        if self._cmd_svc.is_entity_unavailable(entity_id):
            return None
        plan = self._policy.plan_external_command_interlock(
            entity_id,
            service=SERVICE_SET_COVER_POSITION,
            wire_target=wire_target,
            dispatch_token=dispatch_token,
        )
        if plan is None:
            return None
        # Nothing has reached the motor, so there is no refused-command latch to
        # clear and the follower must NOT be stopped: on an open/close-only
        # cover a stop-while-stationary is the hardware's "go to My" gesture.
        # Registered through the SAME pair-keyed seam the external listener
        # uses (#1156) rather than awaited inline, so a second overlapping
        # user command — or an external command on either rail of this pair —
        # supersedes this correction instead of racing it.
        task = self._start_interlock_task(
            replace(plan, reason=MANUAL_INTERLOCK_REASON),
            stop_follower=False,
            mark_override=not force,
        )
        try:
            return await task
        except asyncio.CancelledError:
            # ``task.cancelled()`` is true both when a LATER plan superseded
            # this one (the ordinary case: translate to a normal skip so the
            # user's own command still returns one honest outcome) and when
            # THIS service-call task was itself cancelled from outside — an
            # entry unload or HA shutdown. ``current_task().cancelling()`` is
            # non-zero only in the second case, and that one must propagate:
            # swallowing it would make a torn-down integration look like an
            # ordinary supersede instead. ``Task.cancelling()`` is 3.11+,
            # which the project already requires.
            current = asyncio.current_task()
            if task.cancelled() and (current is None or not current.cancelling()):
                return "skipped", "interlock_superseded"
            raise

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
        return self._policy.describe(
            caps=rolled, labels=labels, options=self.config_entry.options
        )

    def solar_transmittance(self, *, position: int | None) -> SolarTransmittance | None:
        """Estimated transmittance of the glazing + cover assembly (issue #1236).

        The single HA-side gathering point for the three inputs the pure engine
        needs: the options, the cover type's coverage polarity, and the
        position. Computed once per cycle at the diagnostics construction site
        and carried on the context, so every consumer reads the same value.

        ⚠️ ``position`` MUST be the LOGICAL (pre-inverse-state) position — i.e.
        ``PipelineResult.position``. NEVER ``self.state`` or
        ``DiagnosticContext.final_state``, both of which are post-inversion
        (see ``diagnostics/builder.py``'s ``final_state`` note). The two frames
        agree on every normal install and are complements on an inverse-state
        one, so the wrong frame here reads as a silent, install-specific
        inversion of ``effective_g``.

        ``position=None`` (no pipeline result yet) yields ``shaded_fraction
        None`` — no blend — rather than a guessed one. That says nothing about
        where ``g_shaded`` came from, so ``source`` still reports the real
        provenance. Returns ``None`` when the feature is not enabled.
        """
        cfg = self._config_service.get_solar_properties(self.config_entry.options)
        fraction = (
            self._policy.shaded_glass_fraction(position)
            if position is not None
            else None
        )
        return _compute_solar_transmittance(cfg, shaded_fraction=fraction)

    def glass_area(self) -> GlassArea:
        """Glazed area for the solar-gain estimate, and where it came from (#1237).

        Resolution order, and the reason for it:

        1. ``CONF_GLASS_AREA`` — the user's explicit override. Applied HERE
           rather than inside the policy so it reaches every cover type,
           including the ones whose geometry carries no glass dimensions at all.
        2. ``CoverTypePolicy.glass_area_m2`` — height × width, for the five
           types that collect both.
        3. ``unknown`` — say so. The sensor then reports ``unknown`` with a
           reason instead of a confidently wrong wattage.

        A stored override that is blank, non-numeric or non-positive falls
        through to the derived value: a cleared field must not be read as
        "zero square metres".
        """
        options = self.config_entry.options
        configured = options.get(CONF_GLASS_AREA)
        if configured is not None:
            try:
                area = float(configured)
            except (TypeError, ValueError):
                area = 0.0
            if area > 0:
                return GlassArea(area, AREA_SOURCE_CONFIGURED)
        derived = self._policy.glass_area_m2(self._config_service, dict(options))
        if derived is not None:
            return GlassArea(derived, AREA_SOURCE_DERIVED)
        return GlassArea(None, AREA_SOURCE_UNKNOWN)

    def _warn_on_unsupported_irradiance_unit(
        self, *, entity_id: str | None, unit: str | None, unit_ok: bool
    ) -> None:
        """Log, at most once per (entity, unit) situation, a gain-sensor refusal.

        ``build_diagnostic_data`` runs once per coordinator cycle, so warning
        unconditionally would spam the log forever for the lifetime of a
        misconfigured sensor (issue #1280 Fix 4). Tracks the last situation
        warned about in ``self._irradiance_unit_warned`` — the same
        remember-and-compare shape this method's caller already uses for
        ``_last_position_explanation`` — so a refusal that persists logs once,
        a refusal that changes unit logs again, and a refusal that clears and
        later recurs (even with the SAME unit) logs again too, because
        clearing resets the tracked state to ``None``.
        """
        if unit_ok:
            self._irradiance_unit_warned = None
            return
        situation = (entity_id, unit)
        if situation == self._irradiance_unit_warned:
            return
        self.logger.warning(
            "Irradiance entity %s reports unit '%s' instead of W/m² — the "
            "estimated solar gain sensor cannot use this reading and is "
            "reporting unknown. Fix the entity's unit_of_measurement, or "
            "point the irradiance sensor option at one that reports W/m², "
            "to restore it.",
            entity_id,
            unit,
        )
        self._irradiance_unit_warned = situation

    def build_diagnostic_data(self) -> dict:
        """Build diagnostic data from current coordinator state."""
        result = self._pipeline_result

        # Live cover positions and capabilities
        cover_entities = self.entities or []
        _positions = self._cover_provider.read_positions(
            cover_entities, self._policy, assumed=self._cmd_svc.get_assumed_position
        )
        _caps = self._cover_provider.read_all_capabilities(cover_entities)
        _travel_plans = self._cmd_svc.travel_plans()
        _estimated = self._cmd_svc.estimated_positions()
        _covers = {
            eid: {
                "current_position": _positions.get(eid),
                "transit_state": self._cmd_svc.get_transit_direction(eid),
                # Where the travel-time model says the cover is right now, and
                # the ramp it came from. Display-only siblings of transit_state:
                # both are present only mid-move, and neither is ever read back
                # by a command gate.
                "estimated_position": _estimated.get(eid),
                "travel_plan": _travel_plans.get(eid),
                "available": _positions.get(eid) is not None,
                "ha_state": getattr(self.hass.states.get(eid), "state", None),
                "capabilities": (
                    dataclasses.asdict(_caps[eid]) if eid in _caps else None
                ),
            }
            for eid in cover_entities
        }

        _manual_override_state = self._manual_override_diagnostics()

        # Coordinator update health
        _last_success_time = self._last_update_success_time
        _last_exc = self.last_exception

        _temp_readings = self._weather_readings
        _glass_area = self.glass_area()
        _irradiance_value = (
            _temp_readings.irradiance_value if _temp_readings is not None else None
        )
        _irradiance_entity = self.config_entry.options.get(CONF_IRRADIANCE_ENTITY)
        _irradiance_unit = self._climate_provider.read_irradiance_unit(
            _irradiance_entity
        )
        # Irradiance unit gate (#1237 admits the raw value unit-blind; #1280
        # refuses to hand it to the estimator unless it is W/m²). HA's
        # ``irradiance`` device class also permits BTU/(h·ft²) (what HA shows
        # on the imperial unit system); admitting that unconverted would
        # silently under-report gain by roughly a factor of 3, so a value that
        # ISN'T W/m² never reaches ``estimate_solar_gain`` at all — no numeric
        # reading means the gate can never fire, which is why the ``no entity
        # configured`` and ``entity unavailable`` cases both stay
        # ``irradiance_unit_ok=True`` (nothing to refuse).
        _irradiance_unit_ok = (
            _irradiance_value is None
            or _irradiance_unit == UnitOfIrradiance.WATTS_PER_SQUARE_METER
        )
        # Fix 4 (#1280): surface the refusal in the log, not just silently as
        # ``unknown`` — at most once per (entity, unit) situation.
        self._warn_on_unsupported_irradiance_unit(
            entity_id=_irradiance_entity,
            unit=_irradiance_unit,
            unit_ok=_irradiance_unit_ok,
        )
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
            clock_window_open=self.clock_window_open,
            after_start_time=self.after_start_time,
            before_end_time=self.before_end_time,
            start_time=self._time_mgr.start_time_value,
            end_time=self._end_time,
            automatic_control=self.automatic_control,
            calibrating=self._cmd_svc.calibrating,
            last_cover_action=self.last_cover_action,
            last_skipped_action=self.last_skipped_action,
            min_change=self.min_change,
            time_threshold=self.time_threshold,
            switch_mode=self._toggles.switch_mode,
            inverse_state=self._inverse_state,
            use_interpolation=self._use_interpolation,
            position_axis_inverted=self.position_axis_inverted,
            final_state=self.state,
            # The LOGICAL target, never ``self.state`` on the line above — that
            # one is post-inversion (#1236 / the #1028 invariant).
            solar_transmittance=self.solar_transmittance(
                position=result.position if result is not None else None
            ),
            # Estimated-solar-gain inputs (#1237). The raw W/m² comes from the
            # SAME climate read the cloud-suppression latch uses — no second HA
            # read — and the day of year comes from HA's clock frame, never the
            # host's, so a host in another timezone cannot shift the orbital
            # eccentricity term by a day.
            irradiance_w_m2=_irradiance_value,
            day_of_year=dt_util.now().timetuple().tm_yday,
            glass_area_m2=_glass_area.area_m2,
            glass_area_source=_glass_area.source,
            # A DELIBERATELY SEPARATE HA read from the climate cycle above —
            # see ``ClimateProvider.read_irradiance_unit``'s docstring for why
            # it cannot live inside that single-read admission path.
            # ``_irradiance_unit_ok`` itself is computed above, alongside the
            # #1280 Fix 4 one-shot warning that shares its verdict.
            irradiance_unit_ok=_irradiance_unit_ok,
            irradiance_unit=_irradiance_unit,
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
            command_queue=self._command_queue,
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
            # issue #882: instance-language reason templates, primed once at setup.
            reason_labels=self._reason_labels,
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
    def position_axis_inverted(self) -> bool:
        """Whether the primary (position) axis is effectively inverted this cycle.

        Read-time derivation from ``config_entry.options`` via the shared
        ``axis_inverted`` predicate (#1028) — the single source of truth for
        "``inverse_state`` is configured AND interpolation is not suppressing
        it". Every read-side consumer (``state``, the diagnostics builder, the
        sensor's logical-frame attributes) delegates here instead of rewriting
        the formula.
        """
        return axis_inverted(self._policy.axes[0], self.config_entry.options)

    def tilt_read_inverted(self, caps: Any) -> bool:
        """Whether the source's TILT attribute holds a re-framed value.

        Two different write-side transforms can land on
        ``current_tilt_position``, and the proxy read owes the inverse of
        whichever one actually produced the number (#1034). This reconciles
        them:

        * **The policy declares a tilt axis.** The axis descriptor already
          knows which transform ran and on which option. A venetian /
          day-night shade's SECOND axis is ``TILT_AXIS`` — keyed on
          ``inverse_tilt``, converted by the dual-axis sequencer's ``_to_wire``
          and never interpolation-gated. A tilt-PRIMARY type's only axis is
          ``TILT_AXIS_PRIMARY`` — keyed on ``inverse_state``, converted by
          :meth:`_to_cover_frame` because ``async_apply_user_tilt`` falls
          through to ``async_apply_user_position`` (#1027). ``axis_inverted``
          carries that whole asymmetry, so the descriptor answers for both and
          no formula is rewritten here.
        * **The policy declares no tilt axis, but the dispatch went there
          anyway.** ``should_use_tilt`` routes ANY policy onto
          ``set_cover_tilt_position`` when the bound entity advertises
          ``set_tilt_position`` but not ``set_position``. The value such an
          install writes was re-framed by :meth:`_to_cover_frame` on
          ``inverse_state``, so the frame is the POSITION axis's and the answer
          is :attr:`position_axis_inverted`. Deliberately not
          ``axis_inverted(select_default_axis(caps), …)``: that call hands back
          the shared ``TILT_AXIS``, which keys on ``inverse_tilt`` — an option
          this install was never offered and never set.

        A declared axis wins over the caps fallback. A venetian bound to
        tilt-only hardware still has its slats written through the sequencer,
        so the descriptor names the transform that really ran.

        Derived from axis descriptors and ``caps``, never from a cover-type
        string, so a future tilt-carrying type is covered for free. ``caps`` is
        a required argument rather than a defaulted one so a later caller
        cannot silently drop back to the axis-only answer; ``None`` is accepted
        and normalised by ``select_default_axis``.
        """
        axis = next(
            (a for a in self._policy.axes if a.state_attr == STATE_ATTR_TILT_POSITION),
            None,
        )
        if axis is not None:
            return axis_inverted(axis, self.config_entry.options)
        dispatch_axis = self._policy.select_default_axis(caps)
        if dispatch_axis.state_attr == STATE_ATTR_TILT_POSITION:
            return self.position_axis_inverted
        return False

    def _to_cover_frame(self, value: float) -> int:
        """Map a logical (HA-convention) position into this cover's dispatch frame.

        The one place the position axis crosses from the frame every user-facing
        number is expressed in — 0 = closed, 100 = open — into whatever the
        physical cover actually wants. Both producers delegate here: the
        automatic pipeline via :attr:`state`, and every user-initiated command
        via :meth:`async_apply_user_position`. Before #1027 only the first ran
        the transforms, so the same logical value drove the cover to different
        places depending on which path produced it.

        Interpolation and inverse-state are mutually exclusive by design — the
        combination is unsupported and logged — so a single ordered pass covers
        both. There is no escape hatch: every value that reaches here is
        logical, so a caller cannot opt out of the mapping on the grounds that
        some flag rode along with it (issue #1036 removed the ``skip_transforms``
        carve-out both callers used to pass).

        Deliberately NOT shared with the end-of-window sender: that seam inverts
        unconditionally of interpolation and never interpolates, which is a
        genuinely different transform (#993).
        """
        if self._use_interpolation:
            value = interpolate_position(
                value,
                self.start_value,
                self.end_value,
                self.normal_list,
                self.new_list,
            )

        if self._inverse_state and self._use_interpolation:
            self.logger.info("Inverse state is not supported with interpolation")

        if self.position_axis_inverted:
            value = inverse_state(value)

        # interpolate_position() returns numpy float64; inverse_state() returns int.
        # Always coerce to plain Python int so sensors/diagnostics never see a float.
        return int(round(value))

    @property
    def state(self) -> int:
        """Final cover position after pipeline, interpolation, and inverse_state transforms.

        The pipeline always runs, so ``_pipeline_result`` is always set, and
        :attr:`PipelineResult.position` is contractually a LOGICAL
        (pre-inversion canonical) value for every winner — a handler holding a
        raw cover read converts it before assigning the field
        (``pipeline/types.py``; ``motion_timeout`` and ``group_lock`` both do).
        So this boundary answers "what frame is this in?" from the type
        contract, never from provenance, and maps every winner unconditionally.

        Neither flag on the result says anything about the frame (issue #1036):

        - ``bypass_auto_control`` governs GATE PRECEDENCE — the position is
          applied even when Automatic Control is OFF and outside the start/end
          window (issue #767). A safety close of logical 0 must still reach an
          inverse cover as wire 100, or the safety override opens the cover.
        - ``position_constraint_applied`` records that a user-configured bound
          clamped this winner, which drives reason labelling and forces the
          dispatch through a hold (issues #534 / #809). A configured floor is a
          logical value like any other, so it is calibrated and inverted like
          any other — issue #469 skipped both transforms for it, which made a
          "minimum 25% open" floor drive an inverse cover to 75% open and made
          dispatch non-monotonic in the logical request.
        """
        return self._to_cover_frame(self._pipeline_result.position)

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

    @property
    def _pipeline_acts_outside_clock_window(self) -> bool:
        """True when this cycle's result may reach a cover outside the clock window.

        The single question all four outside-window dispatch guards ask —
        ``async_handle_state_change``, ``_async_force_send_pipeline_position``,
        ``async_handle_first_refresh``, and (through
        ``PerEntityState.acts_outside_clock_window``) reconciliation step 4. The
        OR itself lives once, in
        ``pipeline.axis_constraints.may_act_outside_clock_window``; this is just
        the coordinator's None-safe access to it.

        Strictly wider than :pyattr:`_pipeline_is_safety_handler`: it also
        admits a cycle where an opted-in slot's min/max constraint actually
        clamped something (#943 item B). Those two licences stay separate —
        ``is_safety`` keeps its own lifetime (#1226/#1165) and the constraint
        admission buys nothing beyond crossing the clock boundary.
        """
        result = self._pipeline_result
        return bool(result and result.acts_outside_clock_window)

    def _pipeline_has_active_override(self) -> bool:
        """Return True when a non-DEFAULT handler currently owns the pipeline result.

        Consulted by both fast-dispatch paths that bypass the pipeline
        registry entirely — ``_on_window_closed`` (end-of-window default) and
        ``WindowTransitionTracker.check_sunset_window`` (astronomical-sunset
        transition) — so neither force-sends the raw sunset/default position
        over a higher-priority handler's already-winning result (e.g. a
        CUSTOM_POSITION slot holding a user's sleep-mode floor). Without this
        guard the sunset transition overwrites the custom position and only
        the next refresh cycle corrects it back — a spurious double-move
        (issue #895).

        MANUAL is intentionally NOT special-cased here: it is already handled
        per-cover-entity via ``is_cover_manual`` in the sunset dispatch loop,
        whereas ``control_method`` is a single coordinator-wide decision — the
        two checks are complementary, not redundant.

        Uses ``getattr`` because some minimal test doubles construct the
        coordinator via ``object.__new__`` without running ``__init__`` (which
        is where ``_pipeline_result`` is first set to ``None``); treating a
        missing attribute the same as ``None`` mirrors real startup, where the
        pipeline hasn't evaluated yet.

        **Freshness precondition (issue #1241):** this reads whatever
        ``_pipeline_result`` a coordinator update cycle last wrote — it does
        NOT itself evaluate the pipeline. ``_pipeline_result`` is only
        reassigned inside ``_calculate_cover_state``, and this coordinator has
        no periodic update interval, so between ``end_time`` and the tick that
        notices the window closed, a stale in-window winner (SOLAR/CLIMATE/
        CLOUD/GLARE_ZONE) can sit here for up to a full reconciliation
        interval even though that handler would decline the instant it were
        re-evaluated with the window closed. A caller that needs "is a
        higher-priority handler winning *right now*" — a true one-shot with no
        further retry — must refresh the pipeline immediately before calling
        this method, the way ``_on_window_closed`` does. ``check_sunset_window``
        does not need that refresh: unlike the end-time edge, its False→True
        edge is deliberately left unresolved when this guard trips (see its
        docstring), so a stale True here only delays the sunset dispatch to
        the next reconciliation tick (≤1 minute) rather than losing it for the
        day — the edge self-heals without a refresh.
        """
        result = getattr(self, "_pipeline_result", None)
        if result is None:
            return False
        return result.control_method is not ControlMethod.DEFAULT

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

    def _clamp_to_outside_window_bounds(self, position: int, options) -> int:
        """Clamp a pipeline-bypassing send through the live position bounds.

        Shared by the two dispatches that reach a cover with the registry having
        composed nothing: the end-of-window default (``_on_window_closed``) and
        the astronomical-sunset broadcast
        (``WindowTransitionTracker.check_sunset_window``, which fires *after*
        ``end_time`` by construction — #266). Both would otherwise send a raw
        configured number past a live bound and be corrected by the very next
        refresh: a send-then-correct double move in the middle of the night.
        One helper, because it is one rule — it asks the same
        ``gather_axis_constraints`` every other consumer asks, so outside-window
        eligibility is decided in one place, and clamps through the same
        ``clamp_to_bounds``.

        ``_cover_data`` is absent until the first update cycle has run (and on
        the minimal test doubles built with ``object.__new__``), and a snapshot
        cannot be assembled without it. No snapshot means no known constraints,
        which is the same answer as "no constraints": return the position
        untouched rather than fail a send over a cosmetic clamp.
        """
        if getattr(self, "_cover_data", None) is None:
            return position
        snapshot = self._build_user_command_snapshot(options)
        low, high = compose_bounds(
            gather_axis_constraints(snapshot), AXIS_NAME_POSITION
        )
        clamped = clamp_to_bounds(position, low, high)
        if clamped != position:
            self.logger.debug(
                "Pipeline-bypassing send of %s%% clamped to %s%% by an active "
                "outside-window bound (low=%s, high=%s)",
                position,
                clamped,
                low,
                high,
            )
        return clamped

    def _resolve_broadcast_dispatch(
        self, position: int, options: dict, entities: list[str]
    ) -> tuple[int, int, list[str]]:
        """Clamp a pipeline-bypassing broadcast, then order its fan-out on the result.

        **The one statement of the rule both night broadcasts follow** — the
        end-of-window return-to-default and the astronomical-sunset send. Three
        steps that only make sense together, so they are written together
        (CODING_GUIDELINES § No Duplication):

        1. clamp the LOGICAL position through the live bounds
           (:meth:`_clamp_to_outside_window_bounds`, which owns the arithmetic);
        2. map it into the frame these two seams dispatch in — inverted iff
           inverse-state is CONFIGURED, unconditional of interpolation and
           unlike :meth:`_to_cover_frame` (#993);
        3. order the fan-out on THAT number.

        Clamp before ordering, not after: a bound that crosses the covers'
        current position turns a lowering into a raise, and the ordering view
        has to be told what will really be dispatched (#1115 / #1118). Splitting
        the pair across the two callers is how they would come to disagree.

        Returns ``(logical, wire, ordered_entities)``. Both numbers are returned
        because the callers need different halves: the tracker re-applies the
        inversion itself and wants the logical value, while the end-of-window
        loop dispatches the wire one.

        ``entities`` is the caller's own list rather than ``self.entities`` —
        identical today, but a caller that ever passes a subset must have that
        subset ordered, not silently replaced by the whole instance.
        """
        clamped = self._clamp_to_outside_window_bounds(position, options)
        to_send = flip_if(clamped, inverted=self._inverse_state)
        return (
            clamped,
            to_send,
            self._policy.order_for_dispatch(
                entities, position=to_send, inverted=self._inverse_state
            ),
        )

    def _resolve_sunset_dispatch(
        self, position: int, options: dict, entities: list[str]
    ) -> tuple[int, list[str]]:
        """Adapt :meth:`_resolve_broadcast_dispatch` to the sunset tracker's callback.

        Called by ``WindowTransitionTracker.check_sunset_window`` only once the
        False→True edge has actually fired — the tracker runs on every
        reconciliation tick, and building the off-cycle snapshot the clamp needs
        on each of them would advance the arm-on-read latches
        ``_build_user_command_snapshot`` documents. That deferral is the whole
        reason this is a callback rather than two pre-computed arguments, and
        the reason it is a method of its own rather than the shared helper
        inlined at the call site.

        The tracker applies the inverse-state flip itself, so it gets the
        LOGICAL number back and the wire value is discarded here.
        """
        clamped, _wire, ordered = self._resolve_broadcast_dispatch(
            position, options, entities
        )
        return clamped, ordered

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

            Also skips when a higher-priority handler (e.g. CUSTOM_POSITION)
            is currently the pipeline's winner — see
            ``_pipeline_has_active_override`` (issue #895).
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
            # Issue #1241: refresh BEFORE consulting the override guard below.
            # The guard's question is "is a higher-priority handler winning
            # *now that the window is closed*" — but this tick's
            # ``_pipeline_result`` was last written by whichever coordinator
            # cycle happened to run most recently, which may predate
            # ``end_time`` by up to a full reconciliation interval if no
            # tracked entity changed state in the gap. A stale in-window
            # winner (SOLAR/CLIMATE/CLOUD/GLARE_ZONE) would otherwise look
            # like an active override forever, even though every one of
            # those handlers declines the instant the clock window closes.
            # This refresh re-evaluates the pipeline with the window already
            # closed, so a window-gated handler has already stood down and a
            # genuinely active override (CUSTOM_POSITION/MANUAL/WEATHER) is
            # still correctly detected. It cannot itself dispatch anything —
            # the outside-window invariant blocks reposition — so this is
            # compute-only.
            await self.async_refresh()
            if self._pipeline_has_active_override():
                self.logger.debug(
                    "End time reached but a higher-priority handler (%s) is "
                    "active — skipping return-to-default reposition (issue #895)",
                    self._pipeline_result.control_method,
                )
                return
            options = self.config_entry.options
            effective_pos, is_sunset = self._compute_current_effective_default(options)
            # #895's sharp edge (issue #943 item B). This path bypasses the
            # pipeline and sends a POSITION-ONLY default, and a constraint-only
            # slot never becomes the winner, so ``_pipeline_has_active_override``
            # above does not see it — an opted-in ceiling of 30 would be
            # overwritten by ``default_percentage`` 100 the instant the window
            # closed. Compose the same bounds the registry would, through the
            # SAME gather and the SAME ``clamp_to_bounds``. No mirrored
            # arithmetic, and one command rather than a send-then-correct double
            # move. Slats, if any, are handled by the ``async_refresh()`` this
            # handler already triggers below.
            #
            # Which claims the gather returns is deliberately NOT assumed here.
            # This transition fires on ``is_active`` — the clock AND the daytime
            # gate — so on a gate-dark close the user's clock window is still
            # open and the gather returns every active claim, unfiltered. That
            # is the right answer for that case: in-window semantics for an
            # in-window moment. Asking the shared helper rather than reasoning
            # about which of the two predicates tripped is what keeps both cases
            # correct without a branch.
            #
            # Clamped, re-framed and ordered in one call, through the helper the
            # sunset broadcast also uses: the three steps are one rule and the
            # two seams must not state it apart. Deliberately NOT
            # ``_to_cover_frame`` — this seam inverts whenever inverse-state is
            # configured, unconditional of bypass, floor clamp and
            # interpolation, and never interpolates. #993's middle-rail
            # invariant depends on that divergence.
            effective_pos, pos_to_send, ordered_covers = (
                self._resolve_broadcast_dispatch(effective_pos, options, self.entities)
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
            # Already ordered on ``pos_to_send`` by the resolve above, in the
            # same frame ``_entity_target`` gets below — one derivation, so the
            # ordering view and the dispatch cannot disagree about the direction
            # of travel (issue #1118).
            for cover_entity in ordered_covers:
                ctx = self._build_position_context(cover_entity, options, force=False)
                await self._cmd_svc.apply_position(
                    cover_entity,
                    # ``pos_to_send`` was inverted iff inverse-state is
                    # configured (unconditional of bypass/floor-clamp/interp), so
                    # the middle-rail remap must un-invert in THAT space, not the
                    # cached main-pipeline flag (#993).
                    self._entity_target(
                        cover_entity, pos_to_send, inverted=self._inverse_state
                    ),
                    "end_time_default",
                    context=ctx,
                )
            # Trigger a normal refresh so sensor state and diagnostics reflect
            # the commands just dispatched above. Distinct from the #1241
            # refresh earlier in this function: that one runs BEFORE dispatch
            # to freshen the guard's input; this one runs AFTER dispatch to
            # publish its result. Neither makes the other redundant.
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

    def _manual_override_diagnostics(self) -> dict:
        """Build the ``manual_override_state`` diagnostics block.

        The per-entity countdown reads the manager's ``expiry_for`` authority,
        so it reflects a pinned service deadline and the configured duration
        mode alike instead of assuming ``started_at + reset_duration``.
        ``expires_at``, ``started_at_source`` and ``duration_mode`` are additive:
        every pre-existing key keeps its meaning.

        ``expires_at`` is the override's true end and the only field to derive
        it from — ``started_at + reset_duration_seconds`` has not been the end
        since the duration modes landed. ``started_at_source`` says how
        ``started_at`` was obtained: ``engaged`` is the real moment ACP engaged
        the override, ``derived_from_expiry`` is the value the reboot-restore
        path back-derives because only the expiry was ever persisted. The same
        override reports different ``started_at`` values on either side of a
        restart; this field is what makes that legible instead of silent.
        """
        now = dt.datetime.now(dt.UTC)
        reset_secs = self.manager.reset_duration.total_seconds()
        entries = {}
        # ``active_entities()`` is the shared liveness accessor (issue #1273) —
        # the same one the ``manual_override_end_time`` sensor iterates, so the
        # two surfaces can no longer report different cover sets. Membership
        # already implies ``expiry_for`` is non-None; the guard below stays as
        # the belt-and-braces read.
        for eid in self.manager.active_entities():
            started_at = self.manager.manual_control_time.get(eid)
            expires_at = self.manager.expiry_for(eid)
            if started_at is None or expires_at is None:
                continue
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=dt.UTC)
            entries[eid] = {
                # Every entry in this block is a live override by construction
                # now that the loop iterates ``active_entities()``. The key is
                # retained rather than dropped so the diagnostics schema a
                # reader (or a saved diagnostics file) already knows is unchanged.
                "active": True,
                "started_at": started_at.isoformat(),
                "started_at_source": self.manager.manual_control_start_source.get(
                    eid, STARTED_AT_SOURCE_ENGAGED
                ),
                "expires_at": expires_at.isoformat(),
                "remaining_seconds": int(max(0, (expires_at - now).total_seconds())),
            }
        return {
            "reset_duration_seconds": int(reset_secs),
            "duration_mode": self.manual_override_duration_mode,
            "tracked_covers": sorted(self.manager.covers),
            "entries": entries,
        }

    def _resolve_override_deadline(self, anchor: dt.datetime) -> dt.datetime | None:
        """Resolve a manual override's end time for the configured mode (issue #1044).

        The HA-side half of the rule, injected into ``AdaptiveCoverManager`` via
        ``set_deadline_resolver`` so the manager itself stays HA-free: it takes
        the duration mode from the per-cycle ``RuntimeConfig`` mirror, resolves
        the sunset/sunrise boundaries through :func:`.helpers.read_sun_boundaries`
        — the same definition the day/night position and the time window's
        sunrise provider use — reads the operating window's resolved end, then
        hands the arithmetic to the pure
        :func:`.helpers.resolve_override_deadline`.

        ``fixed`` — the default, and what an install that never touched the
        option gets — short-circuits before any state read, so the common case
        pays nothing per cycle.

        Args:
            anchor: The override start (when the user touched the cover) as a
                tz-aware UTC datetime. Never "now": this runs every cycle, so a
                now-anchored deadline would recede forever.

        Returns:
            The resolved absolute end as a tz-aware UTC datetime, or ``None``
            when the mode has no resolvable anchor — the caller then falls back
            to the numeric ``manual_override_duration``.

        """
        options = self.config_entry.options
        mode = self.manual_override_duration_mode
        if mode == MANUAL_OVERRIDE_DURATION_MODE_FIXED:
            return None

        boundaries = None
        cover_data = self._cover_data
        if cover_data is not None:
            boundaries = read_sun_boundaries(self.hass, options, cover_data.sun_data)

        # An unset window end is NO anchor. ``TimeWindowManager.end_time``
        # normalises the ``BLANK_TIME`` sentinel onto tomorrow's midnight by
        # design, so consulting it for an unconfigured window would produce a
        # deadline that recedes a day at every local midnight and the hold would
        # never expire. Decide off the raw options, where the sentinel is still
        # distinguishable (issue #1044).
        deadline = resolve_override_deadline(
            mode,
            dt_util.as_utc(anchor).replace(tzinfo=None),
            boundaries=boundaries,
            window_end_local_naive=(
                self._time_mgr.end_time if has_configured_window_end(options) else None
            ),
        )
        if deadline is None:
            self.logger.debug(
                "Manual override duration mode %s could not be resolved "
                "(sun data available: %s) — falling back to the fixed duration",
                mode,
                boundaries is not None,
            )
            return None
        return deadline.replace(tzinfo=dt.UTC)

    def _compute_current_effective_default(
        self, options: dict, cover_data=None
    ) -> tuple[int, bool]:
        """Return (effective_pos, is_sunset_active) for the current moment.

        A thin wrapper over :func:`helpers._read_current_effective_default`, the
        single source of truth for that computation — shared with
        ``PipelineSnapshotBuilder.build``'s fallback so the update cycle and the
        ad-hoc ``async_apply_user_position`` path can never disagree about the
        same instant (issue #1055).

        All this adds is the cover-data resolution: the ``_on_window_closed``
        transition call site has none in hand, and ``get_blind_data`` is
        coordinator business the helper must not reach into.

        Args:
            options: The config-entry options dict.
            cover_data: An already-computed cover-data object whose ``sun_data``
                is reused. When ``None`` the cover data is computed fresh via
                ``get_blind_data``.

        """
        if cover_data is None:
            cover_data = self.get_blind_data(options=options)
        return _read_current_effective_default(
            self.hass, options, cover_data.sun_data, self._time_mgr
        )

    def _sunset_window_is_open(self, options: dict) -> bool:
        """Return whether the configured SUNSET boundary owns this moment (#1287).

        A thin wrapper over :func:`helpers.read_sunset_window_open` — the
        predicate ``WindowTransitionTracker.check_sunset_window`` needs for
        its False→True edge detector. Deliberately blind to the end-of-window
        override (issue #625), unlike :meth:`_compute_current_effective_default`'s
        ``is_sunset_active``, which is True for BOTH the end-of-window
        position and the sunset position — feeding the tracker that
        overloaded flag made its edge fire at clock ``end_time`` instead of
        the configured sunset boundary.

        Unlike :meth:`_compute_current_effective_default`, this seam has no
        cover-data-reuse call site: the tracker injection (``__init__``) is
        its only caller and always invokes it with ``options`` alone, so the
        cover data is resolved fresh via ``get_blind_data`` every time.

        Args:
            options: The config-entry options dict.

        """
        cover_data = self.get_blind_data(options=options)
        return read_sunset_window_open(
            self.hass, options, cover_data.sun_data, self._time_mgr
        )

    @callback
    def _schedule_optional_wake(
        self,
        unsub: Callable[[], None] | None,
        seconds: float | None,
        on_due: Callable[[dt.datetime], Awaitable[None]],
    ) -> Callable[[], None] | None:
        """Cancel ``unsub`` if in flight, then arm one fresh wake if ``seconds`` is set.

        Shared cancel-then-schedule-if-needed shape behind every "a source is
        HOLDING its last-known verdict; nothing else would trigger its
        fall-back to a fresh reading until the next state-change or periodic
        refresh — arm a single ``async_call_later`` wake instead, cancelling
        any previous one so there is never more than one outstanding" case:
        the daytime-gate fallback wake (issue #742) and the custom-position
        per-input hold fallback wake (issue #1012) both reduce to this.
        ``seconds=None`` means no wake is needed this cycle (determinate,
        never observed, or already fallen back) — any in-flight wake is still
        cancelled, and ``None`` is returned so the caller clears its handle.
        Callers store the return value back onto their own tracking
        attribute; this method holds no state of its own.
        """
        if unsub is not None:
            unsub()
        if seconds is None:
            return None
        return async_call_later(self.hass, seconds, on_due)

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
        self._gate_fallback_unsub = self._schedule_optional_wake(
            self._gate_fallback_unsub,
            self._time_mgr.seconds_until_gate_fallback(),
            self._on_gate_fallback_due,
        )

    async def _on_gate_fallback_due(self, _now: dt.datetime) -> None:
        """Fire when the daytime-gate grace window expires: request a refresh."""
        self._gate_fallback_unsub = None
        await self.async_request_refresh()

    @callback
    def _schedule_custom_position_hold_wake(self) -> None:
        """Schedule one refresh at custom-position per-input hold expiry (issue #1012).

        Mirrors :meth:`_schedule_gate_fallback_wake` (issue #742): while a
        slot's per-input hold is HOLDING a stale sensor/template
        contribution, nothing else would trigger its fall-back to that
        input's own fresh (possibly different) reading until the next
        state-change or periodic refresh. Schedule a single
        ``async_call_later`` wake at the soonest such expiry across every
        configured slot. ``sun.sun`` is unconditionally tracked
        (``__init__.py``), giving a de-facto heartbeat already — this wake is
        a correctness/precision improvement for the exact expiry instant, not
        an outage fix.
        """
        self._custom_position_hold_unsub = self._schedule_optional_wake(
            self._custom_position_hold_unsub,
            self._snapshot_builder.seconds_until_custom_position_hold_fallback(),
            self._on_custom_position_hold_due,
        )

    async def _on_custom_position_hold_due(self, _now: dt.datetime) -> None:
        """Fire when a custom-position per-input hold expires: request a refresh."""
        self._custom_position_hold_unsub = None
        await self.async_request_refresh()

    @callback
    def _schedule_sun_tracking_gate_wake(self) -> None:
        """Schedule one refresh at sun-tracking-gate grace expiry (issue #1167).

        The third instance of the pattern established by
        :meth:`_schedule_gate_fallback_wake` (#742) and
        :meth:`_schedule_custom_position_hold_wake` (#1012): while the gate is
        HOLDING a last-known verdict, nothing else would trigger its fail-open
        back to tracking until the next state-change or periodic refresh. Arming
        one wake at the exact expiry keeps the hold window honest — it is a
        precision improvement, not an outage fix, since ``sun.sun`` is
        unconditionally tracked and already provides a de-facto heartbeat.
        """
        self._sun_tracking_gate_unsub = self._schedule_optional_wake(
            self._sun_tracking_gate_unsub,
            self._snapshot_builder.seconds_until_sun_tracking_gate_fallback(
                self.config_entry.options
            ),
            self._on_sun_tracking_gate_due,
        )

    async def _on_sun_tracking_gate_due(self, _now: dt.datetime) -> None:
        """Fire when the sun-tracking-gate grace window expires: request a refresh."""
        self._sun_tracking_gate_unsub = None
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
        full contract (issue #266) including the override-precedence guard
        (issue #895).
        """
        options = self.config_entry.options
        sunset_pos_cfg = options.get(CONF_SUNSET_POS)
        await self._window_tracker.check_sunset_window(
            track_end_time=self._track_end_time,
            automatic_control=self.automatic_control,
            sunset_pos_cfg=sunset_pos_cfg,
            options=options,
            inverse_state_enabled=self._inverse_state,
            # The list to fan out over. ``resolve_dispatch`` below is handed
            # THIS list and returns it in policy order the moment the sunset
            # edge actually fires; it is used as-is if no resolver is supplied.
            entities=self.entities,
            # Clamp the raw sunset position through the live bounds and order the
            # fan-out on the clamped number, in one derivation (#943 item B,
            # #1115/#1118). Deferred behind a callable because this method runs
            # on every reconciliation tick while the edge fires at most once a
            # night, and resolving the bounds costs an off-cycle snapshot build.
            #
            # This seam inverts iff inverse-state is CONFIGURED — unconditional
            # of interpolation, like the end-time loop and unlike
            # ``_to_cover_frame`` (#993) — so the ordering pair is stated in that
            # space. A raising sunset position ordered bottom-first would park
            # the bottom rail's gate on a middle rail nothing has commanded for a
            # whole settle budget.
            resolve_dispatch=lambda position, covers: self._resolve_sunset_dispatch(
                position, options, covers
            ),
            is_cover_manual=self.manager.is_cover_manual,
            has_active_override=self._pipeline_has_active_override(),
            build_position_context=lambda c, o: self._build_position_context(
                c, o, force=False
            ),
            apply_position=self._cmd_svc.apply_position,
            refresh=self.async_refresh,
            # The tracker inverts the sunset position iff inverse-state is
            # configured (unconditional), so bind that same space into the
            # middle-rail remap — not the cached main-pipeline flag (#993).
            entity_target=lambda c, p: self._entity_target(
                c, p, inverted=self._inverse_state
            ),
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

        # Cancel any in-flight health-check debounce timers (issue #786, #975).
        self._sensor_health.shutdown()
        self._repair.shutdown()

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

        # Cancel the custom-position per-input hold fallback wake (issue #1012).
        if self._custom_position_hold_unsub is not None:
            self._custom_position_hold_unsub()
            self._custom_position_hold_unsub = None

        # Cancel the sun-tracking-gate fallback wake (issue #1167).
        if self._sun_tracking_gate_unsub is not None:
            self._sun_tracking_gate_unsub()
            self._sun_tracking_gate_unsub = None

        # Stand down a calibration run and its republish tick. The run holds
        # ``calibrating`` on the command service, so an unload that left it
        # going would keep that flag set on an orphaned object.
        self.async_cancel_travel_calibration(restore=False)
        self._stop_travel_tick()

        # Cancel any in-flight external-command interlock correction (#1138).
        # Each is a config-entry task, so HA would wait on it during unload;
        # cancelling here means an unload never blocks on a rail-clearance wait.
        for task in self._external_interlock_tasks.values():
            if not task.done():
                task.cancel()
        self._external_interlock_tasks.clear()

        self.logger.debug("Coordinator shutdown complete")


# AdaptiveCoverManager lives in the managers/manual_override package and the
# frame converters (``inverse_state`` / ``flip_if``) in ``position_utils``
# (#1042). Both are re-imported above to maintain backward compatibility.
