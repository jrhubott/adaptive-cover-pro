"""Coordinator → ClimateProvider wiring tests.

These tests guard against the regression introduced in v2.12.0 (Issue #134) where
the refactor from climate_mode_data() to _read_climate_state() silently dropped
temp_entity, outside_entity, and presence_entity from the ClimateProvider.read()
call — causing inside_temperature, outside_temperature, and is_presence to always
be None/True regardless of configuration.

The tests here verify the coordinator passes every config key to ClimateProvider.read()
and will fail immediately if a future refactor drops a parameter.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
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


def _make_coordinator():
    """Build a minimal AdaptiveDataUpdateCoordinator with mocked dependencies.

    We only need the pieces used by _read_climate_state():
      - self._climate_provider  (ClimateProvider mock)
      - self._toggles           (toggles mock)
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord._climate_provider = MagicMock(spec=ClimateProvider)
    coord._climate_provider.read.return_value = _DUMMY_READINGS
    coord._weather_readings = None

    # Toggles: all feature flags off by default
    toggles = MagicMock()
    toggles.lux_toggle = False
    toggles.irradiance_toggle = False
    coord._toggles = toggles

    return coord


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
    # coordinator from toggles/flags rather than coming directly from options.
    _TOGGLE_DERIVED = {"use_lux", "use_irradiance", "use_cloud_coverage"}

    # These map from options key → read() kwarg name (non-obvious mappings).
    _OPTIONS_TO_READ_KWARG = {
        CONF_TEMP_ENTITY: "temp_entity",
        CONF_OUTSIDETEMP_ENTITY: "outside_entity",
        CONF_PRESENCE_ENTITY: "presence_entity",
        CONF_WEATHER_ENTITY: "weather_entity",
        CONF_WEATHER_STATE: "weather_condition",
        CONF_LUX_ENTITY: "lux_entity",
        CONF_LUX_THRESHOLD: "lux_threshold",
        CONF_IRRADIANCE_ENTITY: "irradiance_entity",
        CONF_IRRADIANCE_THRESHOLD: "irradiance_threshold",
        # cloud_coverage uses use_cloud_coverage toggle (derived); entity/threshold below
        CONF_CLOUD_COVERAGE_ENTITY: "cloud_coverage_entity",
        CONF_CLOUD_COVERAGE_THRESHOLD: "cloud_coverage_threshold",
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
    """Build a coordinator with temp_toggle for _build_climate_options tests."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    toggles = MagicMock()
    toggles.temp_toggle = False
    coord._toggles = toggles
    return coord


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
