"""Pipeline snapshot construction.

`PipelineSnapshotBuilder` aggregates HA entity reads, options, manager state,
and policy-derived glare-zone configuration into a single
:class:`PipelineSnapshot` for the pipeline registry to evaluate.

It is composed by the coordinator and constructed once at coordinator
initialisation.  The builder holds no per-cycle state: every value that
changes per cycle (manual-override flag, motion-timeout flag, weather flag,
time-window flag, current cover position, the cover engine, etc.) flows in
as a method argument.  The coordinator remains the single owner of
``_weather_readings`` — the builder returns ``ClimateReadings`` from
:meth:`read_climate` and the coordinator stores it.

Background: this code lived inline on the coordinator as five private
methods (``_read_climate_state``, ``_build_climate_options``,
``_read_force_sensor_states``, ``_read_custom_position_sensor_states``,
``_build_pipeline_snapshot``) totalling ~213 LOC.  Extracting them follows
the same composed-class pattern that Phase B established with
:class:`TimeoutController`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from collections.abc import Callable

from homeassistant.const import ATTR_FRIENDLY_NAME

from ..const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_TILT,
    CONF_ENABLE_SUN_TRACKING,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_MOTION_TIMEOUT_MODE,
    CONF_MY_POSITION_VALUE,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TILT,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_OVERRIDE_MIN_MODE,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_STATE,
    CONF_WINTER_CLOSE_INSULATION,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_CUSTOM_POSITION_TILT_ONLY,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MINIMIZE_MOVEMENTS,
    DEFAULT_MOTION_TIMEOUT_MODE,
)
from ..helpers import compute_effective_default
from .types import (
    ClimateOptions,
    CustomPositionSensorState,
    PipelineSnapshot,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_types import ConfigContextAdapter
    from ..cover_types.base import CoverTypePolicy
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..managers.toggles import ToggleManager
    from ..services.configuration_service import ConfigurationService
    from ..state.climate_provider import ClimateProvider, ClimateReadings


class PipelineSnapshotBuilder:
    """Aggregate HA reads + manager state into a :class:`PipelineSnapshot`."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: ConfigContextAdapter,
        *,
        climate_provider: ClimateProvider,
        toggles: ToggleManager,
        policy: CoverTypePolicy,
        config_service: ConfigurationService,
    ) -> None:
        """Bind the builder to its long-lived collaborators."""
        self._hass = hass
        self._logger = logger
        self._climate_provider = climate_provider
        self._toggles = toggles
        self._policy = policy
        self._config_service = config_service

    # ---- HA reads ---------------------------------------------------------

    def read_climate(self, options: dict) -> ClimateReadings:
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
            outside_entity=options.get(CONF_OUTSIDETEMP_ENTITY),
            presence_entity=options.get(CONF_PRESENCE_ENTITY),
            weather_entity=options.get(CONF_WEATHER_ENTITY),
            weather_condition=options.get(CONF_WEATHER_STATE),
            use_lux=use_lux,
            lux_entity=lux_entity,
            lux_threshold=options.get(CONF_LUX_THRESHOLD),
            use_irradiance=use_irradiance,
            irradiance_entity=irradiance_entity,
            irradiance_threshold=options.get(CONF_IRRADIANCE_THRESHOLD),
            use_cloud_coverage=cloud_suppression_enabled,
            cloud_coverage_entity=options.get(CONF_CLOUD_COVERAGE_ENTITY),
            cloud_coverage_threshold=options.get(CONF_CLOUD_COVERAGE_THRESHOLD),
            is_sunny_sensor=options.get(CONF_IS_SUNNY_SENSOR),
        )

    def read_force_sensors(self, options: dict) -> dict[str, bool]:
        """Read force override sensor states from HA into a plain dict."""
        sensors = options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
        return {
            sensor: bool(
                (state := self._hass.states.get(sensor)) and state.state == "on"
            )
            for sensor in sensors
        }

    def read_custom_position_sensors(
        self, options: dict
    ) -> list[CustomPositionSensorState]:
        """Read custom position sensor states from HA into an ordered list.

        Returns one :class:`CustomPositionSensorState` per configured slot
        (sensor and position both set).  Priority defaults to
        ``DEFAULT_CUSTOM_POSITION_PRIORITY`` (77) when not set so existing
        installations behave identically.  ``min_mode`` defaults to False;
        ``use_my`` defaults to False (when True the slot triggers the cover's
        hardware "My" preset via ``stop_cover`` instead of the slot's numeric
        position).
        """
        result: list[CustomPositionSensorState] = []
        for slot, slot_keys in CUSTOM_POSITION_SLOTS.items():
            sensor = options.get(slot_keys["sensor"])
            position = options.get(slot_keys["position"])
            enabled = bool(
                options.get(slot_keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED)
            )
            if sensor and position is not None and enabled:
                state = self._hass.states.get(sensor)
                is_on = bool(state and state.state == "on")
                sensor_name = (
                    state.attributes.get(ATTR_FRIENDLY_NAME) if state else None
                )
                priority = int(
                    options.get(slot_keys["priority"])
                    or DEFAULT_CUSTOM_POSITION_PRIORITY
                )
                min_mode = bool(options.get(slot_keys["min_mode"], False))
                use_my = bool(options.get(slot_keys["use_my"], False))
                raw_tilt = options.get(slot_keys["tilt"])
                tilt = int(raw_tilt) if raw_tilt is not None else None
                tilt_only = bool(
                    options.get(
                        slot_keys["tilt_only"], DEFAULT_CUSTOM_POSITION_TILT_ONLY
                    )
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
                result.append(
                    CustomPositionSensorState(
                        entity_id=sensor,
                        is_on=is_on,
                        position=int(position),
                        priority=priority,
                        min_mode=min_mode,
                        use_my=use_my,
                        tilt=tilt,
                        tilt_only=tilt_only,
                        sensor_name=sensor_name,
                        slot=slot,
                    )
                )
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
            cloudy_position=options.get(CONF_CLOUDY_POSITION),
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
        effective_default: int | None = None,
        is_sunset_active: bool | None = None,
        cover_capabilities: dict | None = None,
    ) -> PipelineSnapshot:
        """Assemble the per-cycle :class:`PipelineSnapshot`.

        ``effective_default`` / ``is_sunset_active`` are recomputed from
        ``options`` and ``cover_data.sun_data`` when omitted — preserving the
        fallback the original ``_build_pipeline_snapshot`` used so that
        ``async_apply_user_position`` (which evaluates a preemption check
        outside the update cycle) can still build a valid snapshot without
        knowing those values.

        ``is_glare_zone_enabled(idx)`` returns the current state of the
        per-instance glare-zone master switch for zone ``idx``.  The coordinator
        owns those switch attributes (``glare_zone_0``, ``glare_zone_1`` …);
        the builder reads them through this callable so it never reaches back
        into ``coordinator.self``.

        ``cover_capabilities`` maps each bound entity_id to its
        ``CoverCapabilities``.  It drives the sun-tracking floor rollup
        (issue #569): the 1 % floor is switched off only when *every* bound
        entity supports the policy's position axis (conservative
        mixed-instance rule), so positionable covers reach a true 0 %.  ``None``
        / empty leaves the floor active.
        """
        if effective_default is None or is_sunset_active is None:
            h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
            sunset_pos_cfg = options.get(CONF_SUNSET_POS)
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
            force_override_sensors=self.read_force_sensors(options),
            force_override_position=options.get(CONF_FORCE_OVERRIDE_POSITION, 0),
            force_override_min_mode=bool(
                options.get(CONF_FORCE_OVERRIDE_MIN_MODE, False)
            ),
            manual_override_active=manual_override_active,
            motion_timeout_active=motion_timeout_active,
            weather_override_active=weather_override_active,
            weather_override_position=options.get(CONF_WEATHER_OVERRIDE_POSITION, 0),
            weather_override_min_mode=bool(
                options.get(CONF_WEATHER_OVERRIDE_MIN_MODE, False)
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
            enable_sun_tracking=bool(options.get(CONF_ENABLE_SUN_TRACKING, True)),
            motion_timeout_mode=options.get(
                CONF_MOTION_TIMEOUT_MODE, DEFAULT_MOTION_TIMEOUT_MODE
            ),
            current_cover_position=current_cover_position,
            policy=self._policy,
            minimize_movements=bool(
                options.get(CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS)
            ),
            max_coverage_steps=int(
                options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
            ),
            default_tilt=options.get(CONF_DEFAULT_TILT),
            sunset_tilt=options.get(CONF_SUNSET_TILT),
            solar_floor_active=solar_floor_active,
        )
