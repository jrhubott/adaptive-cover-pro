"""PipelineSnapshotBuilder → ClimateProvider wiring tests.

These tests guard against the regression introduced in v2.12.0 (Issue #134) where
the refactor from climate_mode_data() to the per-cycle climate-read step silently
dropped temp_entity, outside_entity, and presence_entity from the
``ClimateProvider.read()`` call — causing inside_temperature, outside_temperature,
and is_presence to always be None/True regardless of configuration.

Phase D moved the climate-read step onto :class:`PipelineSnapshotBuilder`; these
tests now drive the builder directly via its public surface.  The wiring contract
(every option key reaches ``ClimateProvider.read``) is unchanged.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_AUTO_RESOLVE_TEMP_FROM_AREA,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DEVICE_ID,
    CONF_EXTREME_HEAT_POSITION,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_RELEASE_THRESHOLD,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_IS_SUNNY_TEMPLATE_MODE,
    CONF_LUX_ENTITY,
    CONF_LUX_RELEASE_THRESHOLD,
    CONF_LUX_THRESHOLD,
    CONF_OUTSIDE_TEMP_SOURCE,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD_RELEASE,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_PRESENCE_TEMPLATE_MODE,
    CONF_TEMP_ENTITY,
    CONF_TEMP_EXTREME_HEAT,
    CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD,
    CONF_TEMP_HIGH,
    CONF_TEMP_HIGH_RELEASE_THRESHOLD,
    CONF_TEMP_LOW,
    CONF_TEMP_LOW_RELEASE_THRESHOLD,
    CONF_TRACKING_SEASONS,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    ClimateStrategy,
    TrackingSeason,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverState,
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateProvider,
    ClimateReadings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_READINGS = ClimateReadings(
    outside_temperature=None,
    inside_temperature=None,
    is_presence=True,
    is_sunny=True,
    lux_below_threshold=False,
    irradiance_below_threshold=False,
    cloud_coverage_above_threshold=False,
)


def _make_builder(
    *,
    lux_toggle: bool | None = False,
    irradiance_toggle: bool | None = False,
    temp_toggle: bool = False,
):
    """Build a :class:`PipelineSnapshotBuilder` with mocked collaborators."""
    climate_provider = MagicMock(spec=ClimateProvider)
    climate_provider.read.return_value = _DUMMY_READINGS

    toggles = MagicMock()
    toggles.lux_toggle = lux_toggle
    toggles.irradiance_toggle = irradiance_toggle
    toggles.temp_toggle = temp_toggle

    builder = PipelineSnapshotBuilder(
        hass=MagicMock(),
        logger=MagicMock(),
        climate_provider=climate_provider,
        toggles=toggles,
        policy=MagicMock(),
        config_service=MagicMock(),
    )
    return builder, climate_provider


def _make_coordinator():
    """Backward-compat shim used by the original test bodies.

    Returns an object exposing ``_climate_provider`` and ``_read_climate_state``
    so the call-sites below stay readable.  The implementation routes through
    the builder under test.
    """

    class _Shim:
        def __init__(self):
            self._builder, self._climate_provider = _make_builder()
            self._weather_readings = None

        def _read_climate_state(self, options, forecast_max_outside=None):
            self._weather_readings = self._builder.read_climate(
                options, forecast_max_outside=forecast_max_outside
            )

        @property
        def _toggles(self):
            return self._builder._toggles  # noqa: SLF001 — internal mock view

    return _Shim()


# ---------------------------------------------------------------------------
# Individual parameter wiring tests — one per missing parameter (Issue #134)
# ---------------------------------------------------------------------------


class TestClimateStateWiring:
    """Each test verifies one config key is forwarded to ClimateProvider.read()."""

    @pytest.mark.unit
    def test_temp_entity_forwarded(self):
        """CONF_TEMP_ENTITY must be passed as temp_entity to ClimateProvider.read()."""
        coord = _make_coordinator()
        options = {CONF_TEMP_ENTITY: "sensor.living_room_temp"}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("temp_entity") == "sensor.living_room_temp", (
            "REGRESSION (Issue #134): temp_entity was not forwarded to "
            "ClimateProvider.read() — inside_temperature will always be None."
        )

    @pytest.mark.unit
    def test_outside_entity_forwarded(self):
        """CONF_OUTSIDETEMP_ENTITY must be passed as outside_entity."""
        coord = _make_coordinator()
        options = {CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp"}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("outside_entity") == "sensor.outside_temp", (
            "REGRESSION (Issue #134): outside_entity was not forwarded to "
            "ClimateProvider.read() — outside_temperature will always be None."
        )

    @pytest.mark.unit
    def test_presence_entity_forwarded(self):
        """CONF_PRESENCE_ENTITY must be passed as presence_entity."""
        coord = _make_coordinator()
        options = {CONF_PRESENCE_ENTITY: "binary_sensor.occupancy"}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("presence_entity") == "binary_sensor.occupancy", (
            "REGRESSION (Issue #134): presence_entity was not forwarded to "
            "ClimateProvider.read() — is_presence will always be True."
        )

    @pytest.mark.unit
    def test_weather_entity_forwarded(self):
        """CONF_WEATHER_ENTITY must be passed as weather_entity."""
        coord = _make_coordinator()
        options = {CONF_WEATHER_ENTITY: "weather.home"}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("weather_entity") == "weather.home"

    @pytest.mark.unit
    def test_weather_condition_forwarded(self):
        """CONF_WEATHER_STATE must be passed as weather_condition."""
        coord = _make_coordinator()
        options = {CONF_WEATHER_STATE: ["sunny", "partlycloudy"]}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("weather_condition") == ["sunny", "partlycloudy"]

    @pytest.mark.unit
    def test_lux_entity_forwarded_when_toggle_on(self):
        """CONF_LUX_ENTITY forwarded as lux_entity when lux toggle is enabled."""
        coord = _make_coordinator()
        coord._toggles.lux_toggle = True
        options = {CONF_LUX_ENTITY: "sensor.lux", CONF_LUX_THRESHOLD: 5000}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("lux_entity") == "sensor.lux"
        assert kwargs.get("lux_threshold") == 5000
        assert kwargs.get("use_lux") is True

    @pytest.mark.unit
    def test_irradiance_entity_forwarded_when_toggle_on(self):
        """CONF_IRRADIANCE_ENTITY forwarded as irradiance_entity when toggle is enabled."""
        coord = _make_coordinator()
        coord._toggles.irradiance_toggle = True
        options = {
            CONF_IRRADIANCE_ENTITY: "sensor.solar",
            CONF_IRRADIANCE_THRESHOLD: 300,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("irradiance_entity") == "sensor.solar"
        assert kwargs.get("irradiance_threshold") == 300
        assert kwargs.get("use_irradiance") is True

    @pytest.mark.unit
    def test_is_sunny_sensor_forwarded(self):
        """CONF_IS_SUNNY_SENSOR forwarded as is_sunny_sensor (issue #363)."""
        coord = _make_coordinator()
        options = {CONF_IS_SUNNY_SENSOR: "binary_sensor.sun_on_window"}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("is_sunny_sensor") == "binary_sensor.sun_on_window"

    @pytest.mark.unit
    def test_cloud_coverage_forwarded_when_enabled(self):
        """Cloud coverage entity and threshold forwarded when suppression is enabled."""
        coord = _make_coordinator()
        options = {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
            CONF_CLOUD_COVERAGE_THRESHOLD: 75,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("cloud_coverage_entity") == "sensor.cloud"
        assert kwargs.get("cloud_coverage_threshold") == 75
        assert kwargs.get("use_cloud_coverage") is True


class TestOutsideTempSourceWiring:
    """Guard forecast-aware outdoor-temp source forwarding (issue #547)."""

    @pytest.mark.unit
    def test_outside_temp_source_and_forecast_max_forwarded(self):
        """Source option + pre-fetched forecast max both reach read()."""
        coord = _make_coordinator()
        options = {
            CONF_OUTSIDETEMP_ENTITY: "sensor.outside",
            CONF_OUTSIDE_TEMP_SOURCE: "max_of_live_and_forecast",
        }
        coord._read_climate_state(options, forecast_max_outside=26.0)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("outside_temp_source") == "max_of_live_and_forecast"
        assert kwargs.get("forecast_max_outside") == 26.0

    @pytest.mark.unit
    def test_outside_temp_source_defaults_to_live(self):
        """Absent option → live, and forecast_max defaults to None."""
        coord = _make_coordinator()
        coord._read_climate_state({})
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("outside_temp_source") == "live"
        assert kwargs.get("forecast_max_outside") is None


class TestClimateStateWiringDefaults:
    """Verify graceful fallback when options dict is empty."""

    @pytest.mark.unit
    def test_missing_keys_pass_none_not_raise(self):
        """Empty options dict must not raise — all optional entities default to None."""
        coord = _make_coordinator()
        # Must not raise KeyError
        coord._read_climate_state({})
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("temp_entity") is None
        assert kwargs.get("outside_entity") is None
        assert kwargs.get("presence_entity") is None
        assert kwargs.get("weather_entity") is None

    @pytest.mark.unit
    def test_weather_readings_stored_after_call(self):
        """_read_climate_state stores the provider result in _weather_readings."""
        coord = _make_coordinator()
        coord._read_climate_state({})
        assert coord._weather_readings is _DUMMY_READINGS

    @pytest.mark.unit
    def test_full_options_all_keys_forwarded(self):
        """All climate config keys are forwarded in a single read() call."""
        coord = _make_coordinator()
        coord._toggles.lux_toggle = True
        coord._toggles.irradiance_toggle = True
        options = {
            CONF_TEMP_ENTITY: "sensor.temp",
            CONF_OUTSIDETEMP_ENTITY: "sensor.outside",
            CONF_PRESENCE_ENTITY: "binary_sensor.pres",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_WEATHER_STATE: ["sunny"],
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_LUX_THRESHOLD: 5000,
            CONF_IRRADIANCE_ENTITY: "sensor.solar",
            CONF_IRRADIANCE_THRESHOLD: 300,
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
            CONF_CLOUD_COVERAGE_THRESHOLD: 80,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args

        assert kwargs["temp_entity"] == "sensor.temp"
        assert kwargs["outside_entity"] == "sensor.outside"
        assert kwargs["presence_entity"] == "binary_sensor.pres"
        assert kwargs["weather_entity"] == "weather.home"
        assert kwargs["weather_condition"] == ["sunny"]
        assert kwargs["lux_entity"] == "sensor.lux"
        assert kwargs["lux_threshold"] == 5000
        assert kwargs["irradiance_entity"] == "sensor.solar"
        assert kwargs["irradiance_threshold"] == 300
        assert kwargs["cloud_coverage_entity"] == "sensor.cloud"
        assert kwargs["cloud_coverage_threshold"] == 80
        assert kwargs["use_lux"] is True
        assert kwargs["use_irradiance"] is True
        assert kwargs["use_cloud_coverage"] is True


# ---------------------------------------------------------------------------
# Structural regression guard
# ---------------------------------------------------------------------------


class TestClimateProviderApiCoverage:
    """Guard against new ClimateProvider.read() parameters being silently un-wired.

    If a developer adds a new keyword parameter to ClimateProvider.read() and
    forgets to wire it in _read_climate_state(), this test fails immediately.
    """

    # These parameters are intentionally excluded: they are derived by the
    # coordinator from toggles/flags or supplied as a pre-fetched value
    # (forecast_max_outside, issue #547) rather than coming directly from options.
    _TOGGLE_DERIVED = {
        "use_lux",
        "use_irradiance",
        "use_cloud_coverage",
        "forecast_max_outside",
        # temp_switch is derived from the temp-source toggle, not an options key.
        "temp_switch",
    }

    # These map from options key → read() kwarg name (non-obvious mappings).
    _OPTIONS_TO_READ_KWARG = {
        CONF_TEMP_ENTITY: "temp_entity",
        CONF_DEVICE_ID: "temp_device_id",
        CONF_AUTO_RESOLVE_TEMP_FROM_AREA: "auto_resolve_temp_from_area",
        CONF_OUTSIDETEMP_ENTITY: "outside_entity",
        CONF_OUTSIDE_TEMP_SOURCE: "outside_temp_source",
        CONF_PRESENCE_ENTITY: "presence_entity",
        CONF_WEATHER_ENTITY: "weather_entity",
        CONF_WEATHER_STATE: "weather_condition",
        CONF_LUX_ENTITY: "lux_entity",
        CONF_LUX_THRESHOLD: "lux_threshold",
        CONF_LUX_RELEASE_THRESHOLD: "lux_release_threshold",
        CONF_IRRADIANCE_ENTITY: "irradiance_entity",
        CONF_IRRADIANCE_THRESHOLD: "irradiance_threshold",
        CONF_IRRADIANCE_RELEASE_THRESHOLD: "irradiance_release_threshold",
        # cloud_coverage uses use_cloud_coverage toggle (derived); entity/threshold below
        CONF_CLOUD_COVERAGE_ENTITY: "cloud_coverage_entity",
        CONF_CLOUD_COVERAGE_THRESHOLD: "cloud_coverage_threshold",
        CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD: "cloud_coverage_release_threshold",
        CONF_IS_SUNNY_SENSOR: "is_sunny_sensor",
        CONF_IS_SUNNY_TEMPLATE: "is_sunny_template",
        CONF_IS_SUNNY_TEMPLATE_MODE: "is_sunny_template_mode",
        CONF_PRESENCE_TEMPLATE: "presence_template",
        CONF_PRESENCE_TEMPLATE_MODE: "presence_template_mode",
        # Temperature-season crossing inputs (issue #917).
        CONF_TEMP_LOW: "temp_low",
        CONF_TEMP_HIGH: "temp_high",
        CONF_OUTSIDE_THRESHOLD: "outside_threshold",
        CONF_TEMP_EXTREME_HEAT: "temp_extreme_heat",
        CONF_TEMP_LOW_RELEASE_THRESHOLD: "temp_low_release_threshold",
        CONF_TEMP_HIGH_RELEASE_THRESHOLD: "temp_high_release_threshold",
        CONF_OUTSIDE_THRESHOLD_RELEASE: "outside_threshold_release",
        CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD: (
            "temp_extreme_heat_release_threshold"
        ),
    }

    @pytest.mark.unit
    def test_all_provider_params_are_wired(self):
        """Every non-self, non-default-only parameter of ClimateProvider.read()
        must be present in the coordinator's call (either toggle-derived or
        options-mapped).  If this test fails, update _read_climate_state() and
        the _OPTIONS_TO_READ_KWARG mapping above.
        """
        sig = inspect.signature(ClimateProvider.read)
        provider_params = {
            name for name, param in sig.parameters.items() if name != "self"
        }

        # All params should be covered: either toggle-derived or options-mapped
        covered = self._TOGGLE_DERIVED | set(self._OPTIONS_TO_READ_KWARG.values())
        uncovered = provider_params - covered

        assert uncovered == set(), (
            f"ClimateProvider.read() has parameter(s) not wired in "
            f"_read_climate_state(): {uncovered!r}. "
            "Add the missing parameter(s) to the coordinator call and to "
            "TestClimateProviderApiCoverage._OPTIONS_TO_READ_KWARG."
        )

    @pytest.mark.unit
    def test_coordinator_passes_options_entity_to_provider(self):
        """Spot-check: coordinator call includes every options-key → kwarg mapping.

        Uses a full options dict and verifies the exact kwargs passed to read().
        This catches key-name typos (e.g., 'temp_entity' vs 'temp_sensor').
        """
        coord = _make_coordinator()
        coord._toggles.lux_toggle = True
        coord._toggles.irradiance_toggle = True

        # Build options from the canonical options→kwarg map
        options = {
            CONF_TEMP_ENTITY: "sensor.temp",
            CONF_DEVICE_ID: "device_abc",
            CONF_AUTO_RESOLVE_TEMP_FROM_AREA: True,
            CONF_OUTSIDETEMP_ENTITY: "sensor.outside",
            CONF_OUTSIDE_TEMP_SOURCE: "max_of_live_and_forecast",
            CONF_PRESENCE_ENTITY: "binary_sensor.pres",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_WEATHER_STATE: ["sunny"],
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_LUX_THRESHOLD: 5000,
            CONF_LUX_RELEASE_THRESHOLD: 8000,
            CONF_IRRADIANCE_ENTITY: "sensor.solar",
            CONF_IRRADIANCE_THRESHOLD: 300,
            CONF_IRRADIANCE_RELEASE_THRESHOLD: 500,
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud",
            CONF_CLOUD_COVERAGE_THRESHOLD: 80,
            CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD: 50,
            CONF_IS_SUNNY_SENSOR: "binary_sensor.sunny",
            CONF_IS_SUNNY_TEMPLATE: "{{ true }}",
            CONF_IS_SUNNY_TEMPLATE_MODE: "or",
            CONF_PRESENCE_TEMPLATE: "{{ false }}",
            CONF_PRESENCE_TEMPLATE_MODE: "or",
            CONF_TEMP_LOW: 21,
            CONF_TEMP_HIGH: 25,
            CONF_OUTSIDE_THRESHOLD: 32,
            CONF_TEMP_EXTREME_HEAT: 40,
            CONF_TEMP_LOW_RELEASE_THRESHOLD: 23,
            CONF_TEMP_HIGH_RELEASE_THRESHOLD: 23,
            CONF_OUTSIDE_THRESHOLD_RELEASE: 30,
            CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD: 37,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args

        for opt_key, read_kwarg in self._OPTIONS_TO_READ_KWARG.items():
            expected = options[opt_key]
            assert kwargs.get(read_kwarg) == expected, (
                f"Options key {opt_key!r} should map to read() kwarg "
                f"{read_kwarg!r}={expected!r}, but got {kwargs.get(read_kwarg)!r}"
            )


# ---------------------------------------------------------------------------
# Cloud suppression lux/irradiance wiring (Issue #268)
# ---------------------------------------------------------------------------


class TestCloudSuppressionWiring:
    """Guard that cloud suppression can read lux/irradiance without Climate Mode.

    Cloud suppression is documented as independent of climate mode. These tests
    enforce that use_lux/use_irradiance are True whenever cloud_suppression is
    enabled and the matching entity is configured — regardless of the legacy
    lux/irradiance toggle switches (which only exist in Climate Mode).
    """

    @pytest.mark.unit
    def test_cloud_suppression_forces_use_lux_when_lux_entity_configured(self):
        """use_lux must be True when cloud_suppression=True and lux_entity is set,
        even when lux_toggle is None (climate mode off).
        """
        coord = _make_coordinator()
        coord._toggles.lux_toggle = None
        options = {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_LUX_THRESHOLD: 1000,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("use_lux") is True, (
            "REGRESSION (Issue #268): use_lux must be True when cloud_suppression "
            "is enabled with a lux entity — cloud suppression does not require "
            "Climate Mode."
        )

    @pytest.mark.unit
    def test_cloud_suppression_forces_use_irradiance_when_irradiance_entity_configured(
        self,
    ):
        """use_irradiance must be True when cloud_suppression=True and irradiance_entity
        is set, even when irradiance_toggle is None (climate mode off).
        """
        coord = _make_coordinator()
        coord._toggles.irradiance_toggle = None
        options = {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_IRRADIANCE_ENTITY: "sensor.solar",
            CONF_IRRADIANCE_THRESHOLD: 150,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("use_irradiance") is True, (
            "REGRESSION (Issue #268): use_irradiance must be True when cloud_suppression "
            "is enabled with an irradiance entity — cloud suppression does not require "
            "Climate Mode."
        )

    @pytest.mark.unit
    def test_cloud_suppression_without_lux_entity_keeps_use_lux_false(self):
        """use_lux stays False when cloud_suppression=True but no lux_entity is
        configured — nothing to read.
        """
        coord = _make_coordinator()
        coord._toggles.lux_toggle = None
        options = {CONF_CLOUD_SUPPRESSION: True}
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("use_lux") is False

    @pytest.mark.unit
    def test_cloud_suppression_off_with_lux_entity_keeps_use_lux_false(self):
        """When cloud_suppression=False and lux_toggle=False, use_lux is False —
        existing toggle gating is preserved.
        """
        coord = _make_coordinator()
        coord._toggles.lux_toggle = False
        options = {
            CONF_CLOUD_SUPPRESSION: False,
            CONF_LUX_ENTITY: "sensor.lux",
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("use_lux") is False

    @pytest.mark.unit
    def test_cloud_suppression_on_overrides_lux_toggle_false(self):
        """use_lux must be True when cloud_suppression=True + lux_entity configured,
        even when lux_toggle=False (user disabled lux for Climate handler).
        Cloud suppression is independent; the Climate handler gates itself via
        climate_mode_enabled, not via use_lux.
        """
        coord = _make_coordinator()
        coord._toggles.lux_toggle = False
        options = {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_LUX_THRESHOLD: 1000,
        }
        coord._read_climate_state(options)
        _, kwargs = coord._climate_provider.read.call_args
        assert kwargs.get("use_lux") is True, (
            "REGRESSION (Issue #268): cloud suppression must be able to read lux "
            "even when the climate-mode lux toggle is off."
        )


# ---------------------------------------------------------------------------
# cloudy_position wiring (Issue #311)
# ---------------------------------------------------------------------------


def _make_coordinator_with_toggles():
    """Shim for the cloudy-position tests: exposes ``_build_climate_options``."""
    builder, _ = _make_builder()

    class _Shim:
        def __init__(self):
            self._builder = builder

        def _build_climate_options(self, options):
            return self._builder.build_climate_options(options)

    return _Shim()


class TestCloudyPositionWiring:
    """Guard that CONF_CLOUDY_POSITION flows through into ClimateOptions."""

    @pytest.mark.unit
    def test_cloudy_position_passed_to_climate_options(self):
        """CONF_CLOUDY_POSITION is forwarded as cloudy_position in ClimateOptions."""
        coord = _make_coordinator_with_toggles()
        options = {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUDY_POSITION: 30}
        result = coord._build_climate_options(options)
        assert result.cloudy_position == 30

    @pytest.mark.unit
    def test_cloudy_position_none_when_absent(self):
        """cloudy_position is None when CONF_CLOUDY_POSITION is not in options."""
        coord = _make_coordinator_with_toggles()
        options = {CONF_CLOUD_SUPPRESSION: True}
        result = coord._build_climate_options(options)
        assert result.cloudy_position is None

    @pytest.mark.unit
    def test_cloudy_position_zero_is_distinct_from_unset(self):
        """CONF_CLOUDY_POSITION=0 must be preserved as 0, not coerced to None."""
        coord = _make_coordinator_with_toggles()
        options = {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUDY_POSITION: 0}
        result = coord._build_climate_options(options)
        assert result.cloudy_position == 0


class TestCloudSuppressionCoordinatorWiring:
    """Coordinator wiring for the cloud-suppression manager (issue #864)."""

    @pytest.mark.unit
    def test_runtime_config_cloud_suppression_slice(self):
        """RuntimeConfig.from_options carries the enable + hold-time slice."""
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
        from custom_components.adaptive_cover_pro.const import (
            CONF_CLOUD_SUPPRESSION,
            CONF_CLOUD_SUPPRESSION_HOLD_TIME,
        )

        rc = RuntimeConfig.from_options(
            {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUD_SUPPRESSION_HOLD_TIME: 120}
        )
        assert rc.cloud_suppression.enabled is True
        assert rc.cloud_suppression.hold_time_seconds == 120

    @pytest.mark.unit
    def test_runtime_config_cloud_suppression_defaults(self):
        """Absent keys default to disabled + the DEFAULT hold-time constant."""
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
        from custom_components.adaptive_cover_pro.const import (
            DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
        )

        rc = RuntimeConfig.from_options({})
        assert rc.cloud_suppression.enabled is False
        assert (
            rc.cloud_suppression.hold_time_seconds
            == DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME
        )

    @pytest.mark.unit
    def test_reconcile_flips_immediately_when_hold_zero(self):
        """A cycle calls evaluate; hold=0 flips the resolved bool in-line."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
            CloudSuppressionManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._cloud_mgr = CloudSuppressionManager(logger=MagicMock())
        coord._cloud_mgr.update_config(enabled=True, hold_time_seconds=0)
        coord._start_cloud_hold_timeout = MagicMock()

        coord._reconcile_cloud_suppression(
            ClimateReadings(
                outside_temperature=None,
                inside_temperature=None,
                is_presence=True,
                is_sunny=False,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
        )
        assert coord._cloud_mgr.is_suppression_active is True
        coord._start_cloud_hold_timeout.assert_not_called()

    @pytest.mark.unit
    def test_reconcile_starts_timeout_when_pending(self):
        """A pending transition (hold>0) triggers the hold-timeout start."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
            CloudSuppressionManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._cloud_mgr = CloudSuppressionManager(logger=MagicMock())
        coord._cloud_mgr.update_config(enabled=True, hold_time_seconds=120)
        coord._start_cloud_hold_timeout = MagicMock()

        coord._reconcile_cloud_suppression(
            ClimateReadings(
                outside_temperature=None,
                inside_temperature=None,
                is_presence=True,
                is_sunny=False,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
        )
        # Not flipped yet — waiting for the hold-time — but timer requested.
        assert coord._cloud_mgr.is_suppression_active is False
        coord._start_cloud_hold_timeout.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_start_cloud_hold_timeout_callback_triggers_refresh(self):
        """The refresh callback sets state_change and refreshes on expiry."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
            CloudSuppressionManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord.state_change = False
        coord.async_refresh = AsyncMock()
        coord._cloud_mgr = MagicMock(spec=CloudSuppressionManager)

        captured = None

        def _capture(refresh_callback):
            nonlocal captured
            captured = refresh_callback

        coord._cloud_mgr.start_hold_timeout.side_effect = _capture
        coord._start_cloud_hold_timeout()

        assert captured is not None
        await captured()
        assert coord.state_change is True
        coord.async_refresh.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_coordinator_creates_and_configures_cloud_mgr(self, hass):
        """A real setup creates the manager and _update_options wires it."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_CLOUD_SUPPRESSION,
            CONF_CLOUD_SUPPRESSION_HOLD_TIME,
        )
        from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
            CloudSuppressionManager,
        )
        from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

        options = {
            **VERTICAL_OPTIONS,
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_SUPPRESSION_HOLD_TIME: 0,
        }
        entry = await setup_integration(
            hass, options=options, entry_id="cloud_mgr_wire_01"
        )
        coord = entry.runtime_data
        assert isinstance(coord._cloud_mgr, CloudSuppressionManager)

        # _update_options wires the enable + hold-time from options onto the mgr.
        coord._update_options(options)
        coord._reconcile_cloud_suppression(
            ClimateReadings(
                outside_temperature=None,
                inside_temperature=None,
                is_presence=True,
                is_sunny=False,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
        )
        assert coord._cloud_mgr.is_suppression_active is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_coordinator_threads_cloud_bool_into_snapshot(self, hass):
        """A full update cycle passes cloud_suppression_active into build()."""
        from custom_components.adaptive_cover_pro.const import CONF_CLOUD_SUPPRESSION
        from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

        entry = await setup_integration(
            hass,
            options={**VERTICAL_OPTIONS, CONF_CLOUD_SUPPRESSION: True},
            entry_id="cloud_thread_01",
        )
        coord = entry.runtime_data

        captured: dict = {}
        real_build = coord._snapshot_builder.build

        def _spy(*args, **kwargs):
            captured.update(kwargs)
            return real_build(*args, **kwargs)

        coord._snapshot_builder.build = _spy
        await coord.async_refresh()
        assert "cloud_suppression_active" in captured


def _winter_readings() -> ClimateReadings:
    """ClimateReadings whose winter crossing is active (and held)."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
        temp_below_low_threshold=True,
        temp_low_release_cleared=False,
        outside_above_threshold=False,
        outside_release_cleared=True,
    )


class TestClimateSmoothingCoordinatorWiring:
    """Coordinator wiring for the climate-smoothing manager (issue #917)."""

    @pytest.mark.unit
    def test_runtime_config_climate_smoothing_slice(self):
        """RuntimeConfig carries the enable (climate mode) + hold-time slice."""
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
        from custom_components.adaptive_cover_pro.const import (
            CONF_CLIMATE_MODE,
            CONF_CLIMATE_TEMP_HOLD_TIME,
        )

        rc = RuntimeConfig.from_options(
            {CONF_CLIMATE_MODE: True, CONF_CLIMATE_TEMP_HOLD_TIME: 120}
        )
        assert rc.climate_smoothing.enabled is True
        assert rc.climate_smoothing.hold_time_seconds == 120

    @pytest.mark.unit
    def test_runtime_config_climate_smoothing_defaults(self):
        """Absent keys default to disabled + the DEFAULT hold-time constant."""
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
        from custom_components.adaptive_cover_pro.const import (
            DEFAULT_CLIMATE_TEMP_HOLD_TIME,
        )

        rc = RuntimeConfig.from_options({})
        assert rc.climate_smoothing.enabled is False
        assert rc.climate_smoothing.hold_time_seconds == DEFAULT_CLIMATE_TEMP_HOLD_TIME

    @pytest.mark.unit
    def test_reconcile_flips_immediately_when_hold_zero(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
            ClimateSmoothingManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._climate_smoothing_mgr = ClimateSmoothingManager(logger=MagicMock())
        coord._climate_smoothing_mgr.update_config(enabled=True, hold_time_seconds=0)
        coord._start_climate_temp_hold_timeout = MagicMock()

        coord._reconcile_climate_smoothing(_winter_readings())
        assert coord._climate_smoothing_mgr.resolved_flags.winter is True
        coord._start_climate_temp_hold_timeout.assert_not_called()

    @pytest.mark.unit
    def test_reconcile_starts_timeout_when_pending(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
            ClimateSmoothingManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._climate_smoothing_mgr = ClimateSmoothingManager(logger=MagicMock())
        coord._climate_smoothing_mgr.update_config(enabled=True, hold_time_seconds=120)
        coord._start_climate_temp_hold_timeout = MagicMock()

        coord._reconcile_climate_smoothing(_winter_readings())
        assert coord._climate_smoothing_mgr.resolved_flags.winter is False
        coord._start_climate_temp_hold_timeout.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_start_climate_temp_hold_timeout_triggers_refresh(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
            ClimateSmoothingManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord.state_change = False
        coord.async_refresh = AsyncMock()
        coord._climate_smoothing_mgr = MagicMock(spec=ClimateSmoothingManager)

        captured = None

        def _capture(refresh_callback):
            nonlocal captured
            captured = refresh_callback

        coord._climate_smoothing_mgr.start_hold_timeout.side_effect = _capture
        coord._start_climate_temp_hold_timeout()

        assert captured is not None
        await captured()
        assert coord.state_change is True
        coord.async_refresh.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_coordinator_creates_and_configures_climate_mgr(self, hass):
        from custom_components.adaptive_cover_pro.const import (
            CONF_CLIMATE_MODE,
            CONF_CLIMATE_TEMP_HOLD_TIME,
        )
        from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
            ClimateSmoothingManager,
        )
        from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

        options = {
            **VERTICAL_OPTIONS,
            CONF_CLIMATE_MODE: True,
            CONF_CLIMATE_TEMP_HOLD_TIME: 0,
        }
        entry = await setup_integration(
            hass, options=options, entry_id="climate_mgr_wire_01"
        )
        coord = entry.runtime_data
        assert isinstance(coord._climate_smoothing_mgr, ClimateSmoothingManager)

        coord._update_options(options)
        coord._reconcile_climate_smoothing(_winter_readings())
        assert coord._climate_smoothing_mgr.resolved_flags.winter is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_coordinator_threads_climate_temp_flags_into_snapshot(self, hass):
        """A full update cycle passes climate_temp_flags into build()."""
        from custom_components.adaptive_cover_pro.const import CONF_CLIMATE_MODE
        from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

        entry = await setup_integration(
            hass,
            options={**VERTICAL_OPTIONS, CONF_CLIMATE_MODE: True},
            entry_id="climate_thread_01",
        )
        coord = entry.runtime_data

        captured: dict = {}
        real_build = coord._snapshot_builder.build

        def _spy(*args, **kwargs):
            captured.update(kwargs)
            return real_build(*args, **kwargs)

        coord._snapshot_builder.build = _spy
        await coord.async_refresh()
        assert "climate_temp_flags" in captured


class TestExtremeHeatWiring:
    """Guard that the extreme-heat options flow through into ClimateOptions (#766)."""

    @pytest.mark.unit
    def test_extreme_heat_threshold_and_position_forwarded(self):
        """Both keys forward into ClimateOptions untouched."""
        coord = _make_coordinator_with_toggles()
        options = {CONF_TEMP_EXTREME_HEAT: 35, CONF_EXTREME_HEAT_POSITION: 40}
        result = coord._build_climate_options(options)
        assert result.temp_extreme_heat == 35
        assert result.extreme_heat_position == 40

    @pytest.mark.unit
    def test_extreme_heat_none_when_absent(self):
        """Both keys are None when unset — the feature-off default."""
        coord = _make_coordinator_with_toggles()
        result = coord._build_climate_options({})
        assert result.temp_extreme_heat is None
        assert result.extreme_heat_position is None

    @pytest.mark.unit
    def test_extreme_heat_position_zero_is_distinct_from_unset(self):
        """extreme_heat_position=0 (fully closed) must survive as 0, not None."""
        coord = _make_coordinator_with_toggles()
        options = {CONF_TEMP_EXTREME_HEAT: 35, CONF_EXTREME_HEAT_POSITION: 0}
        result = coord._build_climate_options(options)
        assert result.extreme_heat_position == 0


# ---------------------------------------------------------------------------
# tracking_seasons end-to-end wiring (Issue: season-scope glare tracking)
# ---------------------------------------------------------------------------


class TestTrackingSeasonsWiring:
    """End-to-end: a ``tracking_seasons`` option reaches the climate rule tables.

    The unit tests in ``test_tracking_seasons.py`` exercise the rule tables with
    a hand-built ``ClimateContext``.  This case verifies the wiring those tests
    take for granted — the chain the option value rides through, using the real
    production code at every hop:

        option dict
          → build_climate_options       → ClimateOptions.tracking_seasons
          → ClimateHandler._build_climate_data → ClimateCoverData.tracking_seasons
          → ClimateCoverState._build_context   → ClimateContext.tracking_seasons
          → evaluate_rules

    A refactor that drops the field at any one of those forwards (the parts most
    likely to break silently, since they are thin field copies) fails here.
    """

    _SUMMER_ONLY = frozenset({TrackingSeason.SUMMER.value})

    @staticmethod
    def _readings():
        """Intermediate-season, sunny, occupied, well-lit readings.

        No temp thresholds are configured, so the cover is neither winter nor
        summer (intermediate); with presence + sun + ample light this reaches
        the NORMAL_WITH_PRESENCE glare branch — exactly the branch the season
        gate governs.
        """
        return ClimateReadings(
            outside_temperature=None,
            inside_temperature=None,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )

    def _snapshot(self, climate_options):
        """Build a minimal snapshot carrying the climate options through the handler."""
        return SimpleNamespace(
            in_time_window=True,
            climate_mode_enabled=True,
            climate_readings=self._readings(),
            climate_options=climate_options,
            climate_temp_flags=None,
            policy=MagicMock(),
            cover_type="cover_blind",
            cover=MagicMock(),
            default_position=42,
        )

    @pytest.mark.unit
    def test_summer_only_option_gates_glare_tracking_end_to_end(self):
        """summer-only option → intermediate season is out of scope → gate → default."""
        builder, _ = _make_builder()

        # 1. option dict → ClimateOptions (real frozenset conversion).
        opts = builder.build_climate_options(
            {CONF_TRACKING_SEASONS: [TrackingSeason.SUMMER.value]}
        )
        assert opts.tracking_seasons == self._SUMMER_ONLY

        # 2. ClimateOptions → ClimateCoverData (real _build_climate_data forward).
        snapshot = self._snapshot(opts)
        data = ClimateHandler()._build_climate_data(snapshot)
        assert data is not None
        assert data.tracking_seasons == self._SUMMER_ONLY

        # 3. ClimateCoverData → ClimateContext (real _build_context forward).
        state = ClimateCoverState(snapshot, data)
        ctx = state._build_context(tilt=False)
        assert ctx.tracking_seasons == self._SUMMER_ONLY

        # 4. Rule eval: glare tracking is gated to the cover default in the
        #    intermediate season because only summer is in scope.
        position = state.normal_with_presence()
        assert state.climate_strategy == ClimateStrategy.TRACKING_SEASON_GATE
        assert position == 42

    @pytest.mark.unit
    def test_default_all_seasons_defers_to_glare_end_to_end(self):
        """No option set → all-seasons default → glare branch is never gated."""
        builder, _ = _make_builder()
        opts = builder.build_climate_options({})
        snapshot = self._snapshot(opts)
        data = ClimateHandler()._build_climate_data(snapshot)
        state = ClimateCoverState(snapshot, data)

        position = state.normal_with_presence()
        assert state.climate_strategy == ClimateStrategy.GLARE_CONTROL
        assert position is None
