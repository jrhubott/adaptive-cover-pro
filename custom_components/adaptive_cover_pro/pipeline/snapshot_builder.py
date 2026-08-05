"""Pipeline snapshot construction.

`PipelineSnapshotBuilder` aggregates HA entity reads, options, manager state,
and policy-derived glare-zone configuration into a single
:class:`PipelineSnapshot` for the pipeline registry to evaluate.

It is composed by the coordinator and constructed once at coordinator
initialisation.  Most values that change per cycle (manual-override flag,
motion-timeout flag, weather flag, time-window flag, current cover position,
the cover engine, etc.) flow in as a method argument rather than being held
on the builder.  The one exception is the custom-position "last valid" hold
state: the builder carries a per-slot whole-slot cache (issue #1005,
``_last_valid_custom_position``) and per-slot, per-input
:class:`~managers.common.graceful_source.GracefulSource` instances (issue
#1012, ``_sensor_fold_sources`` and ``_template_active_sources``) across
cycles — see ``__init__`` — so a transient sensor/template blip does not fire
a false release edge.  The coordinator remains the single owner of
``_weather_readings`` — the builder returns ``ClimateReadings`` from
:meth:`read_climate` and the coordinator stores it.

The one manager it holds a reference to is ``TimeWindowManager``: the
effective-default fallback needs live window state (grace windows, latched
gate holds) that cannot be recomputed from ``hass`` + ``options``, so the
manager itself is injected and read through pure properties (issue #1055).

Background: this code lived inline on the coordinator as five private
methods (``_read_climate_state``, ``_build_climate_options``,
``_read_force_sensor_states``, ``_read_custom_position_sensor_states``,
``_build_pipeline_snapshot``) totalling ~213 LOC.  Extracting them follows
the same composed-class pattern that Phase B established with
:class:`TimeoutController`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from collections.abc import Callable, Mapping

from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_ON

from ..const import (
    CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_TILT,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_ENABLE_SUN_TRACKING,
    CONF_SUN_TRACKING_GATE_SENSORS,
    CONF_SUN_TRACKING_GATE_TEMPLATE,
    CONF_SUN_TRACKING_GATE_TEMPLATE_MODE,
    DEFAULT_CONDITION_GATE_GRACE_SECONDS,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_RELEASE_THRESHOLD,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_IS_SUNNY_TEMPLATE_MODE,
    CONF_LUX_ENTITY,
    CONF_LUX_RELEASE_THRESHOLD,
    CONF_LUX_THRESHOLD,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_MIN_TILT,
    CONF_MIN_TILT_SUN_ONLY,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_MOTION_TIMEOUT_MODE,
    CONF_MY_POSITION_VALUE,
    CONF_OUTSIDE_TEMP_SOURCE,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD_RELEASE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_PRESENCE_TEMPLATE_MODE,
    CONF_EXTREME_HEAT_POSITION,
    CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR,
    CONF_SUNSET_TILT,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_EXTREME_HEAT,
    CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD,
    CONF_TEMP_HIGH,
    CONF_TEMP_HIGH_RELEASE_THRESHOLD,
    CONF_TEMP_LOW,
    CONF_TEMP_LOW_RELEASE_THRESHOLD,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_OVERRIDE_MIN_MODE,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_STATE,
    CONF_TRACKING_SEASONS,
    CONF_WINTER_CLOSE_INSULATION,
    CUSTOM_POSITION_INPUT_HOLD_SECONDS,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_AUTO_RESOLVE_TEMP_FROM_AREA,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_TRACKING_SEASONS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_CUSTOM_POSITION_TILT_ONLY,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MAX_TILT,
    DEFAULT_MAX_TILT_SUN_ONLY,
    DEFAULT_MIN_TILT,
    DEFAULT_MIN_TILT_SUN_ONLY,
    DEFAULT_MINIMIZE_MOVEMENTS,
    DEFAULT_MOTION_TIMEOUT_MODE,
    DEFAULT_OUTSIDE_TEMP_SOURCE,
    DEFAULT_TEMPLATE_COMBINE_MODE,
)
from ..cover_types.base import axis_inverted
from ..helpers import (
    _read_current_effective_default,
    custom_position_slot_configured,
    custom_position_slot_name,
    custom_position_slot_sensors,
    get_safe_state,
)
from ..managers.common.graceful_source import GracefulSource, SourceResolution
from ..managers.common.condition_gate import ConditionGate
from ..templates import combine_with_mode, is_template_string, render_condition_or_none
from .types import (
    ClimateOptions,
    ClimateTempFlags,
    CustomPositionSensorState,
    GroupIntent,
    PipelineSnapshot,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_types import ConfigContextAdapter
    from ..cover_types.base import CoverTypePolicy
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..managers.time_window import TimeWindowManager
    from ..managers.toggles import ToggleManager
    from ..services.configuration_service import ConfigurationService
    from ..state.climate_provider import ClimateProvider, ClimateReadings


def _delta_time_minutes(value: object) -> int:
    """Coerce a ``delta_time`` option to whole minutes.

    Production stores ``CONF_DELTA_TIME`` as a plain int (``NumberSelector``),
    but a malformed value (e.g. a legacy duration dict) must never crash the
    whole update cycle downstream — ``anticipated_solar_position`` compares this
    with ``<=``, and a non-numeric value there raises ``TypeError`` and takes
    out the coordinator refresh. Anything non-numeric falls back to ``0``
    (anticipation disabled), the safe no-op.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _opt_int(options: dict, key: str) -> int | None:
    """Read an optional numeric option as ``int``, or ``None`` when absent.

    Absent means "the constraint is off" (issue #943) — there is deliberately
    no ``DEFAULT_*`` for the axis-constraint keys, so a missing key and an
    explicitly cleared one both read as None. ``0`` is a meaningful value and
    must survive, so this tests ``is None`` rather than truthiness.
    """
    raw = options.get(key)
    return int(raw) if raw is not None else None


class PipelineSnapshotBuilder:
    """Aggregate HA reads + manager state into a :class:`PipelineSnapshot`.

    ``time_mgr`` is the one manager the builder holds a reference to, for
    :meth:`build`'s effective-default fallback (issue #1055). It is read
    through pure properties only — never advanced — which is the same contract
    ``async_apply_user_position`` already relies on for every other manager
    flag it threads in: an ad-hoc build must not re-evaluate the managers.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: ConfigContextAdapter,
        *,
        climate_provider: ClimateProvider,
        toggles: ToggleManager,
        policy: CoverTypePolicy,
        config_service: ConfigurationService,
        time_mgr: TimeWindowManager | None = None,
        clock: Callable[[], float] = time.monotonic,
        template_variables: Mapping[str, Any] | None = None,
    ) -> None:
        """Bind the builder to its long-lived collaborators.

        ``clock`` is a monotonic time source (seconds) for the per-input
        custom-position hold window (issue #1012) — injected so tests can
        advance it deterministically, matching the convention already used by
        ``TimeWindowManager`` / ``GracefulSource`` for their grace windows.

        ``template_variables`` is the ``acp`` self-reference render context
        built once by the coordinator (issue #1159), threaded into each
        custom-position slot's condition template. Opaque here.
        """
        self._hass = hass
        self._logger = logger
        self._climate_provider = climate_provider
        self._toggles = toggles
        self._policy = policy
        self._config_service = config_service
        self._time_mgr = time_mgr
        self._clock = clock
        self._template_variables = template_variables
        # The sun-tracking gate (issue #1167). Lives here rather than on the
        # coordinator because both ``build()`` call sites — the update cycle and
        # the off-cycle user-command build — must judge tracking by the same
        # verdict; a gate the ad-hoc build could not see would let a user command
        # be evaluated against a different tracking state than the cycle that
        # follows it. Readers are closures over this module's globals, matching
        # TimeWindowManager's daytime gate, so the kernel stays HA-free.
        self._sun_tracking_gate = ConditionGate(
            DEFAULT_CONDITION_GATE_GRACE_SECONDS,
            read_state=lambda entity_id: get_safe_state(self._hass, entity_id),
            render_condition=lambda template: render_condition_or_none(
                self._hass, template, variables=self._template_variables
            ),
            clock=clock,
        )
        # Last VALID per-slot read, keyed by slot number (issue #1005). Written
        # only on a valid read; on an invalid read (transient unavailable /
        # unknown / missing sensor) the slot's activation is held to this so a
        # blip does not fire a false release edge. Per-instance state living on
        # the one builder the coordinator owns, so both per-cycle callers
        # (build() and the coordinator release-edge stamp) share it and agree.
        # A config-entry reload constructs a fresh builder, clearing it. This
        # whole-slot hold has NO time bound — it is the outer safety net for
        # the fully-invalid case, unchanged by the per-input hold below.
        self._last_valid_custom_position: dict[int, CustomPositionSensorState] = {}
        # Per-slot, per-input grace-window state machines (issue #1012),
        # lazily created on first use (see _hold_or_fresh). #1005's cache
        # above only holds the *combined* slot state when every input is
        # invalid at once. When a slot has a sensor AND a template, one input
        # can go transiently invalid while the other still opines — the
        # combined is_valid stays True, so the #1005 hold never engages, and
        # the dropped input's fresh False silently wins the fold. Each of
        # these two dicts holds one GracefulSource per slot, tracking that
        # input's own last-valid contribution and substituting it only while
        # that specific input's own validity flag is False this cycle AND the
        # hold hasn't exceeded CUSTOM_POSITION_INPUT_HOLD_SECONDS, measured
        # from that input's own FIRST INVALID SIGHTING (cadence-independent —
        # see GracefulSource's module docstring) — past that, a permanently-
        # dead input falls through to its fresh value instead of masking a
        # healthy peer forever.
        self._sensor_fold_sources: dict[
            int, GracefulSource[tuple[bool, tuple[str, ...]]]
        ] = {}
        self._template_active_sources: dict[int, GracefulSource[bool]] = {}

    # ---- HA reads ---------------------------------------------------------

    def read_climate(
        self, options: dict, *, forecast_max_outside: float | None = None
    ) -> ClimateReadings:
        """Read all climate-related entities into a fresh ``ClimateReadings``.

        Single call to :meth:`ClimateProvider.read` for each update cycle.
        Reads temperature sensors, presence, weather, lux, irradiance, and
        cloud coverage.  All pipeline handlers (ClimateHandler,
        CloudSuppressionHandler) consume the result via
        ``snapshot.climate_readings``.

        Cloud suppression is documented as independent of Climate Mode, so
        lux/irradiance are read whenever cloud suppression is enabled and the
        corresponding entity is configured — even if the climate-mode toggles
        are off.

        ``forecast_max_outside`` is the coordinator's pre-fetched forecast
        daily-high (issue #547). It is threaded through to the provider so
        the ``outside_temp_source`` switch can source it; the builder stays
        stateless (the fetch itself lives on the coordinator).
        """
        cloud_suppression_enabled = bool(options.get(CONF_CLOUD_SUPPRESSION, False))
        lux_entity = options.get(CONF_LUX_ENTITY)
        irradiance_entity = options.get(CONF_IRRADIANCE_ENTITY)
        use_lux = bool(self._toggles.lux_toggle) or (
            cloud_suppression_enabled and lux_entity is not None
        )
        use_irradiance = bool(self._toggles.irradiance_toggle) or (
            cloud_suppression_enabled and irradiance_entity is not None
        )
        return self._climate_provider.read(
            temp_entity=options.get(CONF_TEMP_ENTITY),
            temp_device_id=options.get(CONF_DEVICE_ID),
            auto_resolve_temp_from_area=bool(
                options.get(
                    CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
                    DEFAULT_AUTO_RESOLVE_TEMP_FROM_AREA,
                )
            ),
            outside_entity=options.get(CONF_OUTSIDETEMP_ENTITY),
            outside_temp_source=options.get(
                CONF_OUTSIDE_TEMP_SOURCE, DEFAULT_OUTSIDE_TEMP_SOURCE
            ),
            forecast_max_outside=forecast_max_outside,
            presence_entity=options.get(CONF_PRESENCE_ENTITY),
            presence_template=options.get(CONF_PRESENCE_TEMPLATE),
            presence_template_mode=options.get(CONF_PRESENCE_TEMPLATE_MODE)
            or DEFAULT_TEMPLATE_COMBINE_MODE,
            weather_entity=options.get(CONF_WEATHER_ENTITY),
            weather_condition=options.get(CONF_WEATHER_STATE),
            use_lux=use_lux,
            lux_entity=lux_entity,
            lux_threshold=options.get(CONF_LUX_THRESHOLD),
            lux_release_threshold=options.get(CONF_LUX_RELEASE_THRESHOLD),
            use_irradiance=use_irradiance,
            irradiance_entity=irradiance_entity,
            irradiance_threshold=options.get(CONF_IRRADIANCE_THRESHOLD),
            irradiance_release_threshold=options.get(CONF_IRRADIANCE_RELEASE_THRESHOLD),
            use_cloud_coverage=cloud_suppression_enabled,
            cloud_coverage_entity=options.get(CONF_CLOUD_COVERAGE_ENTITY),
            cloud_coverage_threshold=options.get(CONF_CLOUD_COVERAGE_THRESHOLD),
            cloud_coverage_release_threshold=options.get(
                CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD
            ),
            is_sunny_sensor=options.get(CONF_IS_SUNNY_SENSOR),
            is_sunny_template=options.get(CONF_IS_SUNNY_TEMPLATE),
            is_sunny_template_mode=options.get(CONF_IS_SUNNY_TEMPLATE_MODE)
            or DEFAULT_TEMPLATE_COMBINE_MODE,
            # Temperature-season crossing inputs (issue #917). temp_switch comes
            # from the runtime toggle (same source build_climate_options uses),
            # thresholds/release edges straight from options.
            temp_switch=bool(self._toggles.temp_toggle),
            temp_low=options.get(CONF_TEMP_LOW),
            temp_high=options.get(CONF_TEMP_HIGH),
            outside_threshold=options.get(CONF_OUTSIDE_THRESHOLD),
            temp_extreme_heat=options.get(CONF_TEMP_EXTREME_HEAT),
            temp_low_release_threshold=options.get(CONF_TEMP_LOW_RELEASE_THRESHOLD),
            temp_high_release_threshold=options.get(CONF_TEMP_HIGH_RELEASE_THRESHOLD),
            outside_threshold_release=options.get(CONF_OUTSIDE_THRESHOLD_RELEASE),
            temp_extreme_heat_release_threshold=options.get(
                CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD
            ),
        )

    def _hold_or_fresh[T](
        self,
        sources: dict[int, GracefulSource[T]],
        slot: int,
        *,
        valid: bool,
        fresh: T,
        now: float,
    ) -> T:
        """Return this cycle's value for one per-input fold through its slot's source.

        Lazily creates the slot's :class:`GracefulSource` (window
        ``CUSTOM_POSITION_INPUT_HOLD_SECONDS``, anchored at this input's own
        first indeterminate sighting — cadence-independent, see the "Anchor
        point" note in ``graceful_source``'s module docstring) on first use,
        then feeds it this cycle's verdict: ``fresh`` when ``valid``, ``None``
        (indeterminate) otherwise.

        DETERMINATE and FELL_BACK both resolve to ``fresh`` — FELL_BACK's own
        ``Resolution.value`` is ``None`` (the kernel's generic "no caller
        context" answer), but this caller always has this cycle's live
        reading in hand, so that is its own, more useful fallback than the
        kernel's ``None``. A first-ever invalid read has no prior state to
        hold either (FELL_BACK, same "no hold on cold start" contract as
        #1005's whole-slot cache). Only HOLDING substitutes the genuinely
        held last-known value.
        """
        source = sources.setdefault(
            slot,
            GracefulSource(CUSTOM_POSITION_INPUT_HOLD_SECONDS, clock=self._clock),
        )
        resolution = source.observe(fresh if valid else None, now=now)
        if resolution.state is SourceResolution.HOLDING:
            return resolution.value
        return fresh

    def seconds_until_custom_position_hold_fallback(self) -> float | None:
        """Seconds until the soonest active per-input hold falls back to fresh.

        ``None`` when no configured slot currently has a HOLDING per-input
        source — every input is either determinate, has never spoken, or has
        already fallen back. Mirrors ``TimeWindowManager.
        seconds_until_gate_fallback`` (issue #742) for the custom-position
        per-input hold (issue #1012): while a source is HOLDING, nothing else
        would prompt a refresh at the exact moment it falls back to its own
        (possibly different) fresh reading, until the next state-change or
        periodic cycle — the coordinator uses this to arm a single prompt
        wake instead.
        """
        remainings = [
            r
            for source in (
                *self._sensor_fold_sources.values(),
                *self._template_active_sources.values(),
            )
            if (r := source.remaining()) is not None
        ]
        return min(remainings) if remainings else None

    def _resolve_sun_tracking(self, options: Mapping[str, Any]) -> tuple[bool, bool]:
        """Whether sun tracking is live this cycle, and whether a gate closed it.

        Returns ``(enable_sun_tracking, gate_closed)``. The single place the
        master toggle and the gate combine (issue #1167). Everything downstream —
        ``SolarHandler``, and the glare-zone handler's sun-only limits, which
        already read the first value as "the live tracking state" — sees one
        answer. ``gate_closed`` exists only so the decision trace can name the
        real cause; it is never True when the master toggle is what stopped
        tracking.

        An unconfigured gate has no opinion and resolves to the toggle alone, so
        every existing entry is bit-identical to before the gate existed. A
        configured gate whose sources all go indeterminate holds its last verdict
        for the grace window and then fails **OPEN** to tracking (#742), never
        closed: a thermostat blinking to ``unavailable`` must not silently
        disable sun tracking, which is the #1012/#1014 failure mode.

        ``update_config`` runs before the master-toggle early-out so a config
        change is never missed while the toggle is off — otherwise the gate would
        hold a stale sensor list and apply it the instant tracking came back. The
        grace machine is kept inert in that state by
        :meth:`seconds_until_sun_tracking_gate_fallback`, which the coordinator
        calls every cycle regardless; see its own note.
        """
        self._sun_tracking_gate.update_config(
            options.get(CONF_SUN_TRACKING_GATE_SENSORS, []),
            options.get(CONF_SUN_TRACKING_GATE_TEMPLATE),
            options.get(
                CONF_SUN_TRACKING_GATE_TEMPLATE_MODE, DEFAULT_TEMPLATE_COMBINE_MODE
            ),
        )
        if not bool(options.get(CONF_ENABLE_SUN_TRACKING, True)):
            return False, False
        tracking = self._sun_tracking_gate.resolved(default=True)
        return tracking, not tracking

    def seconds_until_sun_tracking_gate_fallback(
        self, options: Mapping[str, Any]
    ) -> float | None:
        """Seconds until a HELD sun-tracking-gate verdict expires, or ``None``.

        The coordinator schedules one prompt refresh on this so the fail-open
        engages at grace expiry rather than at the next incidental update —
        matching ``TimeWindowManager.seconds_until_gate_fallback``.

        Returns ``None`` outright while the master toggle is off. The gate's
        verdict cannot change what the cover does in that state, and
        ``ConditionGate.seconds_until_fallback`` *observes* to answer — so
        without this guard a disabled cover whose gate sensor drops out would
        anchor a grace window and arm a wake that resolves to nothing.
        ``options`` is required, not defaulted: a default would let the single
        production caller stop passing it with nothing failing, which is exactly
        how this guard would rot.
        """
        if not bool(options.get(CONF_ENABLE_SUN_TRACKING, True)):
            return None
        return self._sun_tracking_gate.seconds_until_fallback()

    def read_custom_position_sensors(
        self, options: dict
    ) -> list[CustomPositionSensorState]:
        """Read custom position trigger states from HA into an ordered list.

        Returns one :class:`CustomPositionSensorState` per configured slot
        (a trigger — sensors and/or template — plus a position).  Slot
        activation is the OR of the bound sensors, folded with the optional
        condition template via :func:`templates.combine_with_mode` — template
        rendering happens here so the handlers stay pure.  Priority defaults
        to ``DEFAULT_CUSTOM_POSITION_PRIORITY`` (77) when not set so existing
        installations behave identically.  ``min_mode`` defaults to False;
        ``use_my`` defaults to False (when True the slot triggers the cover's
        hardware "My" preset via ``stop_cover`` instead of the slot's numeric
        position).

        Two layers of "hold last-valid" protect against transient invalid
        reads. Per-input (issue #1012): the sensor-OR and the template
        opinion are each held to their own last-valid contribution — via
        :meth:`_hold_or_fresh` — whenever that specific input alone is
        invalid this cycle, *before* the OR/AND fold runs. This prevents one
        dropped input (e.g. a sensor going transiently unavailable) from
        silently outvoting a template (or vice versa) that is still opining
        normally. The per-input hold is time-bounded to
        ``CUSTOM_POSITION_INPUT_HOLD_SECONDS``: past that window a
        permanently-dead input falls through to its fresh value instead of
        masking a healthy peer forever. Whole-slot (issue #1005): the
        combined ``is_on`` is held to ``_last_valid_custom_position`` — with
        no time bound — only when *every* input is invalid at once — the
        outer safety net for the fully-invalid case, unchanged by the
        per-input hold above.
        """
        now = self._clock()
        result: list[CustomPositionSensorState] = []
        for slot, slot_keys in CUSTOM_POSITION_SLOTS.items():
            enabled = bool(
                options.get(slot_keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED)
            )
            if not (custom_position_slot_configured(options, slot_keys) and enabled):
                continue
            sensors = custom_position_slot_sensors(options, slot_keys)
            # Raw state objects are kept only for friendly-name labelling.
            # Activation and validity read through get_safe_state so a
            # transient unavailable/unknown/missing sensor is distinguished
            # from a valid ``off`` (issue #1005) — get_safe_state returns None
            # for any state in helpers._INVALID_STATES or a missing entity.
            states = {s: self._hass.states.get(s) for s in sensors}
            safe_states = {s: get_safe_state(self._hass, s) for s in sensors}
            states_on = {s: v == STATE_ON for s, v in safe_states.items()}
            active = tuple(s for s, on in states_on.items() if on)
            sensors_on = bool(active)
            # The slot has a usable sensor input this cycle when at least one
            # bound sensor reported a non-invalid value.
            sensors_valid = any(v is not None for v in safe_states.values())
            # Hold the sensor side's own last-valid fold input when every
            # bound sensor is invalid this cycle, for up to
            # CUSTOM_POSITION_INPUT_HOLD_SECONDS (issue #1012) — before the
            # OR/AND fold below, so a template opining alongside a dropped
            # sensor can't let the sensor's fresh False silently win. Only
            # meaningful when at least one sensor is actually bound —
            # symmetric with the template guard below; behaviour-neutral in
            # practice, since a reconfiguration that clears a slot's sensors
            # always reaches this via a fresh builder (async_reload — none of
            # the custom_position_* keys are in _RUNTIME_APPLICABLE_OPTIONS),
            # which starts with empty caches anyway.
            if sensors:
                sensor_fold = self._hold_or_fresh(
                    self._sensor_fold_sources,
                    slot,
                    valid=sensors_valid,
                    fresh=(sensors_on, active),
                    now=now,
                )
                sensors_on, active = sensor_fold

            template = options.get(slot_keys["template"])
            has_template = is_template_string(template)
            # Tri-state render: None = no template OR the template failed to
            # render (no opinion). A rendered opinion is both the activation
            # signal and a valid input for the slot.
            template_opinion = (
                render_condition_or_none(
                    self._hass, template, variables=self._template_variables
                )
                if has_template
                else None
            )
            template_active = bool(template_opinion) if has_template else None
            template_valid = template_opinion is not None
            # Symmetric hold for the template side (issue #1012): only
            # meaningful when a template is actually configured.
            if has_template:
                template_active = self._hold_or_fresh(
                    self._template_active_sources,
                    slot,
                    valid=template_valid,
                    fresh=bool(template_active),
                    now=now,
                )
            mode = (
                options.get(slot_keys["template_mode"]) or DEFAULT_TEMPLATE_COMBINE_MODE
            )
            is_on = combine_with_mode(
                bool(template_active),
                sensors_on,
                mode,
                has_template=has_template,
                has_others=bool(sensors),
            )

            # A read is valid when any usable input (sensor or template) spoke
            # this cycle. On an invalid read, hold the last valid activation so
            # a transient blip neither deactivates the pipeline handler nor
            # fires a false release edge in the coordinator (issue #1005). A
            # first-ever invalid read has no prior state → is_on stays False.
            is_valid = sensors_valid or template_valid
            if not is_valid:
                held = self._last_valid_custom_position.get(slot)
                if held is not None:
                    is_on = held.is_on
                    active = held.active_entity_ids

            # Friendly name of the first active sensor (else the first sensor)
            # so diagnostics label the slot by what actually triggered it.
            name_source = (
                states.get(active[0] if active else sensors[0]) if sensors else None
            )
            sensor_name = (
                name_source.attributes.get(ATTR_FRIENDLY_NAME) if name_source else None
            )
            # Optional user-configured label (issue #867); empty string (a
            # cleared text box) normalizes to None, same as an absent key.
            custom_name = custom_position_slot_name(options, slot_keys)

            priority = int(
                options.get(slot_keys["priority"]) or DEFAULT_CUSTOM_POSITION_PRIORITY
            )
            min_mode = bool(options.get(slot_keys["min_mode"], False))
            use_my = bool(options.get(slot_keys["use_my"], False))
            raw_tilt = options.get(slot_keys["tilt"])
            tilt = int(raw_tilt) if raw_tilt is not None else None
            tilt_only = bool(
                options.get(slot_keys["tilt_only"], DEFAULT_CUSTOM_POSITION_TILT_ONLY)
            )
            # Mutual exclusion: tilt_only wins over min_mode / use_my
            # (decision Q3). A slot can fix only the slat angle OR claim
            # position as a floor / via My — not both. Normalize here, the
            # single read site, mirroring the existing use_my-over-min_mode
            # precedent. The config-summary surfaces a warning when a user
            # configured a conflicting combination.
            if tilt_only:
                min_mode = False
                use_my = False

            # --- Axis constraints (issue #943) --------------------------------
            # A constraint-only slot (e.g. trigger → minimum tilt) carries no
            # position; None means "makes no position claim", distinct from 0.
            position = _opt_int(options, slot_keys["position"])
            position_max = _opt_int(options, slot_keys["position_max"])
            tilt_min = _opt_int(options, slot_keys["tilt_min"])
            tilt_max = _opt_int(options, slot_keys["tilt_max"])
            # Same mutual-exclusion pass as min_mode / use_my above: tilt_only
            # fixes the slat angle and lets the position pipeline drive the
            # carriage, so the slot carries no bounds on either axis. The My
            # path is hardware-pinned — a position ceiling cannot apply to it.
            # Normalizing the stored values here (rather than only deriving
            # around them) keeps what diagnostics surface honest about what is
            # actually in effect.
            if tilt_only:
                position_max = None
                tilt_min = None
                tilt_max = None
            elif use_my:
                position_max = None

            state = CustomPositionSensorState(
                entity_ids=tuple(sensors),
                is_on=is_on,
                position=position,
                priority=priority,
                min_mode=min_mode,
                use_my=use_my,
                tilt=tilt,
                tilt_only=tilt_only,
                sensor_name=sensor_name,
                slot=slot,
                active_entity_ids=active,
                template_active=template_active,
                custom_name=custom_name,
                position_max=position_max,
                tilt_min=tilt_min,
                tilt_max=tilt_max,
                is_valid=is_valid,
            )
            # Remember the last valid read so a later fully-invalid read can
            # hold it (issue #1005). Written on every valid-combined-read
            # cycle, whether or not a per-input hold contributed to this
            # cycle's fold — a per-input hold still reflects a genuine
            # combined verdict (the other input spoke fresh), so it is as
            # trustworthy as any other valid read. This is deliberate:
            # suppressing the write whenever a per-input hold contributed
            # would make the eventual fully-invalid outcome depend on
            # whether a slot's inputs happened to die in the same cycle or
            # one cycle apart — same physical situation, different result,
            # decided only by cycle alignment (#1012 review). Effectively
            # idempotent across the two same-cycle callers: each samples the
            # clock separately, so they can straddle a per-input hold's expiry
            # boundary and disagree — a window of the microseconds between the
            # two reads out of the whole 300 s hold. When it happens the slot's
            # release edge is spent a cycle early and the move falls back to
            # the normal delta/time gates.
            if is_valid:
                self._last_valid_custom_position[slot] = state
            result.append(state)
        return result

    # ---- Pure assembly ----------------------------------------------------

    def build_climate_options(self, options: dict) -> ClimateOptions:
        """Build a :class:`ClimateOptions` from config entry options."""
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
            summer_close_bypass_sun_floor=bool(
                options.get(CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR, False)
            ),
            cloudy_position=options.get(CONF_CLOUDY_POSITION),
            temp_extreme_heat=options.get(CONF_TEMP_EXTREME_HEAT),
            extreme_heat_position=options.get(CONF_EXTREME_HEAT_POSITION),
            # Absent / None falls back to all-seasons (unchanged behaviour for
            # installs that never saw this option). An explicit list is honoured
            # literally — including the empty list, which means "never track"
            # (glare tracking is suppressed in every season).
            tracking_seasons=(
                frozenset(DEFAULT_TRACKING_SEASONS)
                if options.get(CONF_TRACKING_SEASONS) is None
                else frozenset(options[CONF_TRACKING_SEASONS])
            ),
        )

    def build(
        self,
        options: dict,
        *,
        cover_data: AdaptiveGeneralCover,
        cover_type: str,
        climate_readings: ClimateReadings | None,
        manual_override_active: bool,
        motion_timeout_active: bool,
        weather_override_active: bool,
        in_time_window: bool,
        current_cover_position: int | None,
        is_glare_zone_enabled: Callable[[int], bool],
        cover_positions: Mapping[str, int | None] | None = None,
        cloud_suppression_active: bool = False,
        climate_temp_flags: ClimateTempFlags | None = None,
        effective_default: int | None = None,
        is_sunset_active: bool | None = None,
        cover_capabilities: dict | None = None,
        group_intent: GroupIntent | None = None,
    ) -> PipelineSnapshot:
        """Assemble the per-cycle :class:`PipelineSnapshot`.

        ``effective_default`` / ``is_sunset_active``, when omitted, come from
        :func:`helpers._read_current_effective_default` — the single source of
        truth the coordinator's own ``_compute_current_effective_default``
        delegates to as well. That covers the configured sunrise/sunset time
        entities (issue #1048, up to two ``hass.states.get()`` reads; an
        instance with neither configured reads no state at all) *and* the four
        live window-state inputs this branch used to drop: the daytime gate
        (#632), the explicit window-start signal (#438/#492), and the
        end-of-window position with its active flag (#625). Until issue #1055
        the branch hand-rolled a narrower call, so it could report a different
        position than the update cycle for the very same instant.

        The fallback exists so that ``async_apply_user_position`` (which
        evaluates a preemption check outside the update cycle) can still build
        a valid snapshot without knowing those values. A ``None`` ``time_mgr``
        (test / legacy construction) degrades each window-state input to the
        pure formula's own default.

        ``is_glare_zone_enabled(idx)`` returns the current state of the
        per-instance glare-zone master switch for zone ``idx``.  The coordinator
        owns those switch attributes (``glare_zone_0``, ``glare_zone_1`` …);
        the builder reads them through this callable so it never reaches back
        into ``coordinator.self``.

        ``cover_positions`` maps each bound entity_id to its current RAW
        cover-frame position (``None`` when the entity reports none) — the
        per-entity source ``current_cover_position`` is the mean of. The
        registry judges each held cover's clamp verdict against its own entry
        (issue #1174); omitting it leaves the pre-#1174 judgment on the scalar.

        ``cover_capabilities`` maps each bound entity_id to its
        ``CoverCapabilities``.  It drives the sun-tracking floor rollup
        (issue #569): the 1 % floor is switched off only when *every* bound
        entity supports the policy's position axis (conservative
        mixed-instance rule), so positionable covers reach a true 0 %.  ``None``
        / empty leaves the floor active.
        """
        # Local import: ``pipeline.handlers`` pulls in cover-type policies, so a
        # module-level import here would form a circular chain — the same reason
        # ``axis_constraints.gather_axis_constraints`` imports the handler
        # locally. Both remain the single source of the weather priority; never
        # inline the number.
        from .handlers import (  # noqa: PLC0415
            WeatherOverrideHandler,
            resolve_handler_priority,
        )

        if effective_default is None or is_sunset_active is None:
            effective_default, is_sunset_active = _read_current_effective_default(
                self._hass, options, cover_data.sun_data, self._time_mgr
            )

        _sun_tracking, _gate_closed = self._resolve_sun_tracking(options)
        glare_zones_cfg = self._policy.glare_zones_config(self._config_service, options)
        active_zone_names: set[str] = set()
        if glare_zones_cfg is not None:
            for idx, zone in enumerate(glare_zones_cfg.zones):
                if is_glare_zone_enabled(idx):
                    active_zone_names.add(zone.name)

        # Sun-tracking floor rollup (#569): switch the 1 % floor off only when
        # every bound entity supports the policy's position axis. A mixed
        # instance (any open/close-only entity) or unknown caps keeps the floor
        # active, so a binary cover never fully retracts with sun in the FOV.
        caps = cover_capabilities or {}
        all_positionable = bool(caps) and all(
            self._policy.position_axis_supported(c) for c in caps.values()
        )
        solar_floor_active = not all_positionable

        return PipelineSnapshot(
            cover=cover_data,
            config=cover_data.config,
            cover_type=cover_type,
            default_position=effective_default,
            is_sunset_active=is_sunset_active,
            # NOTE: configured_default and configured_sunset_pos are deliberately
            # absent from PipelineSnapshot.  They are annotated onto PipelineResult
            # by the coordinator after evaluation so the raw config values are
            # never accessible to pipeline handler logic.
            climate_readings=climate_readings,
            climate_mode_enabled=self._toggles.switch_mode,
            climate_options=self.build_climate_options(options),
            manual_override_active=manual_override_active,
            motion_timeout_active=motion_timeout_active,
            weather_override_active=weather_override_active,
            weather_override_position=options.get(CONF_WEATHER_OVERRIDE_POSITION, 0),
            weather_override_min_mode=bool(
                options.get(CONF_WEATHER_OVERRIDE_MIN_MODE, False)
            ),
            weather_override_priority=resolve_handler_priority(
                options, WeatherOverrideHandler.name
            ),
            weather_bypass_auto_control=options.get(
                CONF_WEATHER_BYPASS_AUTO_CONTROL, True
            ),
            glare_zones=glare_zones_cfg,
            active_zone_names=frozenset(active_zone_names),
            in_time_window=in_time_window,
            motion_control_enabled=self._toggles.motion_control,
            custom_position_sensors=self.read_custom_position_sensors(options),
            my_position_value=options.get(CONF_MY_POSITION_VALUE),
            sunset_use_my=bool(options.get(CONF_SUNSET_USE_MY, False)),
            enable_sun_tracking=_sun_tracking,
            sun_tracking_gate_closed=_gate_closed,
            motion_timeout_mode=options.get(
                CONF_MOTION_TIMEOUT_MODE, DEFAULT_MOTION_TIMEOUT_MODE
            ),
            current_cover_position=current_cover_position,
            cover_positions=cover_positions,
            position_axis_inverted=axis_inverted(self._policy.axes[0], options),
            policy=self._policy,
            group_intent=group_intent,
            minimize_movements=bool(
                options.get(CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS)
            ),
            max_coverage_steps=int(
                options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
            ),
            default_tilt=options.get(CONF_DEFAULT_TILT),
            sunset_tilt=options.get(CONF_SUNSET_TILT),
            min_tilt=int(options.get(CONF_MIN_TILT, DEFAULT_MIN_TILT)),
            max_tilt=int(options.get(CONF_MAX_TILT, DEFAULT_MAX_TILT)),
            min_tilt_sun_only=bool(
                options.get(CONF_MIN_TILT_SUN_ONLY, DEFAULT_MIN_TILT_SUN_ONLY)
            ),
            max_tilt_sun_only=bool(
                options.get(CONF_MAX_TILT_SUN_ONLY, DEFAULT_MAX_TILT_SUN_ONLY)
            ),
            solar_floor_active=solar_floor_active,
            time_threshold_minutes=_delta_time_minutes(options.get(CONF_DELTA_TIME)),
            cloud_suppression_active=cloud_suppression_active,
            climate_temp_flags=climate_temp_flags,
        )
