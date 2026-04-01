"""Tests for climate strategy tracking and position explanation diagnostics (Issue #68).

Tests cover:
- ClimateCoverState.climate_strategy is set correctly for each decision branch
- _build_position_explanation produces correct strings for all scenarios
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

from custom_components.adaptive_cover_pro.calculation import (
    ClimateCoverData,
    ClimateCoverState,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_MIN_POSITION,
    CONF_MIN_POSITION,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.enums import ClimateStrategy, ControlMethod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_climate_data(hass, mock_logger, **overrides):
    """Build a ClimateCoverData with minimal defaults."""
    defaults = {
        "logger": mock_logger,
        "temp_low": 20.0,
        "temp_high": 25.0,
        "temp_switch": False,
        "blind_type": "cover_blind",
        "transparent_blind": False,
        "temp_summer_outside": 22.0,
        "outside_temperature": None,
        "inside_temperature": None,
        "is_presence": True,
        "is_sunny": True,
        "lux_below_threshold": False,
        "irradiance_below_threshold": False,
    }
    defaults.update(overrides)
    # Remove keys not in ClimateCoverData (e.g. 'hass' passed by callers)
    valid_keys = {
        "logger",
        "temp_low",
        "temp_high",
        "temp_switch",
        "blind_type",
        "transparent_blind",
        "temp_summer_outside",
        "outside_temperature",
        "inside_temperature",
        "is_presence",
        "is_sunny",
        "lux_below_threshold",
        "irradiance_below_threshold",
    }
    filtered = {k: v for k, v in defaults.items() if k in valid_keys}
    return ClimateCoverData(**filtered)


def make_climate_state(cover, climate_data):
    """Build a ClimateCoverState, mocking sun data to avoid HA calls."""
    state = ClimateCoverState(cover, climate_data)
    return state


def _make_cover(
    *,
    direct_sun_valid=True,
    sunset_valid=False,
    sunset_pos=None,
    control_state_reason="Sun in FOV",
    default=50.0,
):
    """Create a minimal cover mock for position explanation tests."""
    return SimpleNamespace(
        gamma=10.0,
        valid=True,
        valid_elevation=True,
        in_blind_spot=False,
        direct_sun_valid=direct_sun_valid,
        sunset_valid=sunset_valid,
        sunset_pos=sunset_pos,
        default=default,
        control_state_reason=control_state_reason,
    )


def _make_ncs(cover=None):
    """Wrap a cover mock in a NormalCoverState-like object."""
    if cover is None:
        cover = _make_cover()
    return SimpleNamespace(cover=cover)


def _base_ctx(**overrides):
    """Return a DiagnosticContext with sensible defaults."""
    defaults = {  # noqa: C408
        "pos_sun": [180.0, 45.0],
        "normal_cover_state": _make_ncs(),
        "raw_calculated_position": 50,
        "climate_state": None,
        "climate_data": None,
        "climate_strategy": None,
        "climate_mode": False,
        "control_method": ControlMethod.SOLAR,
        "pipeline_result": None,
        "is_force_override_active": False,
        "is_motion_timeout_active": False,
        "is_manual_override_active": False,
        "check_adaptive_time": True,
        "after_start_time": True,
        "before_end_time": True,
        "start_time": None,
        "end_time": None,
        "automatic_control": True,
        "last_cover_action": {},
        "last_skipped_action": {},
        "min_change": 1,
        "time_threshold": 2,
        "switch_mode": False,
        "inverse_state": False,
        "use_interpolation": False,
        "default_state": 50,
        "final_state": 50,
        "config_options": {},
        "motion_detected": True,
        "motion_timeout_active": False,
        "force_override_sensors": [],
        "force_override_position": 0,
    }
    defaults.update(overrides)
    return DiagnosticContext(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sun_data():
    """Mock SunData instance."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return sun_data


@pytest.fixture
def mock_logger():
    """Mock logger."""
    logger = MagicMock()
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def vertical_cover(mock_sun_data, mock_logger):
    """Vertical cover with sun directly in front."""
    from tests.cover_helpers import build_vertical_cover

    return build_vertical_cover(
        logger=mock_logger,
        sol_azi=180.0,
        sol_elev=45.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=mock_sun_data,
        fov_left=45,
        fov_right=45,
        win_azi=180,
        h_def=50,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=0.5,
        h_win=2.0,
    )


@pytest.fixture
def builder():
    """Create a DiagnosticsBuilder instance."""
    return DiagnosticsBuilder()


# ---------------------------------------------------------------------------
# Climate Strategy Tests — normal_with_presence
# ---------------------------------------------------------------------------


class TestClimateStrategyNormalWithPresence:
    """ClimateCoverState sets climate_strategy correctly for normal_with_presence."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_winter_heating_strategy(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Winter + sun valid → WINTER_HEATING."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = make_climate_data(
            hass,
            mock_logger,
            is_presence=True,
            temp_low=20.0,
            temp_high=25.0,
        )

        # Force winter + sun valid
        with (
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "valid",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            result = state_handler.normal_with_presence()

        assert result == 100
        assert state_handler.climate_strategy == ClimateStrategy.WINTER_HEATING

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_low_light_strategy(self, mock_datetime, hass, mock_logger, vertical_cover):
        """Not summer + low lux → LOW_LIGHT."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = make_climate_data(
            hass, mock_logger, is_presence=True, lux_below_threshold=True
        )

        with (
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            state_handler.normal_with_presence()

        assert state_handler.climate_strategy == ClimateStrategy.LOW_LIGHT

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_cooling_strategy(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Summer + transparent blind → SUMMER_COOLING."""
        mock_datetime.utcnow.return_value = datetime(2024, 6, 21, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 6, 21, 21, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 6, 21, 5, 0, 0)
        )

        climate_data = make_climate_data(
            hass,
            mock_logger,
            transparent_blind=True,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
        )

        with (
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            result = state_handler.normal_with_presence()

        assert result == 0
        assert state_handler.climate_strategy == ClimateStrategy.SUMMER_COOLING

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_glare_control_strategy(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Normal sunny conditions with presence → GLARE_CONTROL."""
        mock_datetime.utcnow.return_value = datetime(2024, 6, 21, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 6, 21, 21, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 6, 21, 5, 0, 0)
        )

        climate_data = make_climate_data(
            hass,
            mock_logger,
            transparent_blind=False,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
        )

        with (
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            state_handler.normal_with_presence()

        assert state_handler.climate_strategy == ClimateStrategy.GLARE_CONTROL


# ---------------------------------------------------------------------------
# Climate Strategy Tests — normal_without_presence
# ---------------------------------------------------------------------------


class TestClimateStrategyNormalWithoutPresence:
    """ClimateCoverState sets climate_strategy correctly for normal_without_presence."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_cooling_without_presence(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Summer + sun valid + no presence → SUMMER_COOLING."""
        mock_datetime.utcnow.return_value = datetime(2024, 6, 21, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 6, 21, 21, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 6, 21, 5, 0, 0)
        )

        climate_data = make_climate_data(hass, mock_logger, is_presence=False)

        with (
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "valid",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            result = state_handler.normal_without_presence()

        assert result == 0
        assert state_handler.climate_strategy == ClimateStrategy.SUMMER_COOLING

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_winter_heating_without_presence(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Winter + sun valid + no presence → WINTER_HEATING."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = make_climate_data(hass, mock_logger, is_presence=False)

        with (
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(vertical_cover),
                "valid",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            result = state_handler.normal_without_presence()

        assert result == 100
        assert state_handler.climate_strategy == ClimateStrategy.WINTER_HEATING

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_low_light_without_presence_no_sun(
        self, mock_datetime, hass, mock_logger, vertical_cover
    ):
        """Sun not valid + no presence → LOW_LIGHT (default position)."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = make_climate_data(hass, mock_logger, is_presence=False)

        with (
            patch.object(
                type(climate_data),
                "is_summer",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_winter",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(vertical_cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            state_handler = make_climate_state(vertical_cover, climate_data)
            state_handler.normal_without_presence()

        assert state_handler.climate_strategy == ClimateStrategy.LOW_LIGHT


# ---------------------------------------------------------------------------
# Position Explanation Tests
# ---------------------------------------------------------------------------


class TestBuildPositionExplanation:
    """DiagnosticsBuilder._build_position_explanation returns correct strings."""

    def test_force_override(self, builder):
        """Force override active → explains override position."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                is_force_override_active=True,
                force_override_position=0,
            )
        )
        assert "Force override" in result
        assert "0%" in result

    def test_motion_timeout(self, builder):
        """Motion timeout active → explains default position."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(is_motion_timeout_active=True, default_state=30)
        )
        assert "motion" in result.lower()
        assert "30%" in result

    def test_manual_override(self, builder):
        """Manual override → explains manual control."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(is_manual_override_active=True)
        )
        assert "manual" in result.lower()

    def test_outside_time_window_with_sunset_position(self, builder):
        """Outside time window with sunset position set → shows Sunset Position."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                check_adaptive_time=False,
                config_options={CONF_SUNSET_POS: 30},
            )
        )
        assert "Sunset Position" in result
        assert "30%" in result

    def test_outside_time_window_without_sunset_position(self, builder):
        """Outside time window without sunset position → shows Default Position."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                check_adaptive_time=False,
                config_options={CONF_DEFAULT_HEIGHT: 100},
            )
        )
        assert "Default Position" in result
        assert "100%" in result

    def test_sunset_offset_with_sunset_position(self, builder):
        """In window but sunset_valid with sunset_pos set → shows Sunset Position."""
        cover = _make_cover(direct_sun_valid=False, sunset_valid=True, sunset_pos=20)
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(normal_cover_state=_make_ncs(cover))
        )
        assert "Sunset Position" in result
        assert "20%" in result

    def test_default_fov_exit_without_sunset(self, builder):
        """In window, FOV exit, no sunset → shows Default Position."""
        cover = _make_cover(
            direct_sun_valid=False,
            sunset_valid=False,
            sunset_pos=None,
            control_state_reason="Default: FOV Exit",
            default=100,
        )
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(normal_cover_state=_make_ncs(cover))
        )
        assert "FOV Exit" in result
        assert "Default Position" in result
        assert "100%" in result

    def test_sun_tracking_no_limits(self, builder):
        """Sun tracking, no limits, no climate → shows raw tracking position."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(raw_calculated_position=65)
        )
        assert "Sun tracking" in result
        assert "65%" in result

    def test_sun_tracking_with_min_limit(self, builder):
        """Sun tracking below min_position → shows limit applied."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                raw_calculated_position=30,
                default_state=60,
                config_options={
                    CONF_MIN_POSITION: 60,
                    CONF_ENABLE_MIN_POSITION: True,
                },
            )
        )
        assert "Sun tracking" in result
        assert "min limit" in result
        assert "60%" in result

    def test_climate_winter_heating(self, builder):
        """Sun tracking + climate winter heating → shows winter heating decision."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                raw_calculated_position=45,
                switch_mode=True,
                climate_state=100,
                climate_strategy=ClimateStrategy.WINTER_HEATING,
                final_state=100,
            )
        )
        assert "Sun tracking" in result
        assert "Winter Heating" in result
        assert "100%" in result

    def test_climate_summer_cooling(self, builder):
        """Sun tracking + climate summer cooling → shows summer cooling."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                raw_calculated_position=45,
                switch_mode=True,
                climate_state=0,
                climate_strategy=ClimateStrategy.SUMMER_COOLING,
                final_state=0,
            )
        )
        assert "Summer Cooling" in result
        assert "0%" in result

    def test_climate_low_light(self, builder):
        """Default position + climate low light strategy."""
        cover = _make_cover(
            direct_sun_valid=False,
            control_state_reason="Default: FOV Exit",
            default=0,
        )
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                normal_cover_state=_make_ncs(cover),
                raw_calculated_position=0,
                switch_mode=True,
                climate_state=0,
                climate_strategy=ClimateStrategy.LOW_LIGHT,
                final_state=0,
            )
        )
        assert "FOV Exit" in result
        assert "Low Light" in result

    def test_sun_tracking_with_interpolation(self, builder):
        """Interpolation applied → shows interpolated final value."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                raw_calculated_position=72,
                use_interpolation=True,
                final_state=65,
            )
        )
        assert "interpolated" in result
        assert "65%" in result

    def test_sun_tracking_with_inverse(self, builder):
        """Inverse state applied → shows inversed final value."""
        result = DiagnosticsBuilder._build_position_explanation(
            _base_ctx(
                raw_calculated_position=72,
                inverse_state=True,
                final_state=28,
            )
        )
        assert "invers" in result.lower()
        assert "28%" in result


# ---------------------------------------------------------------------------
# Position Explanation Change Detection Logging Tests (A1)
# ---------------------------------------------------------------------------


class TestPositionExplanationChangeDetection:
    """build_diagnostic_data logs position explanation only when it changes.

    These tests verify the change-detection logging that happens in the
    coordinator's build_diagnostic_data delegate.  Since the builder itself
    is a pure function, we test the coordinator's thin wrapper behavior.
    """

    def _make_coordinator_mock(self, explanation="Sun tracking (50%)"):
        """Build a mock coordinator with a real DiagnosticsBuilder."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord._diagnostics_builder = DiagnosticsBuilder()
        coord._last_position_explanation = ""
        coord.logger = MagicMock()

        # Minimal stubs for DiagnosticContext construction
        coord.pos_sun = [180.0, 45.0]
        coord.normal_cover_state = _make_ncs()
        coord.raw_calculated_position = 50
        coord.climate_state = None
        coord.climate_data = None
        coord.climate_strategy = None
        coord._climate_mode = False
        coord.control_method = ControlMethod.SOLAR
        coord._pipeline_result = None
        type(coord).is_force_override_active = PropertyMock(return_value=False)
        type(coord).is_motion_timeout_active = PropertyMock(return_value=False)
        coord.manager = MagicMock()
        coord.manager.binary_cover_manual = False
        type(coord).check_adaptive_time = PropertyMock(return_value=True)
        type(coord).after_start_time = PropertyMock(return_value=True)
        type(coord).before_end_time = PropertyMock(return_value=True)
        coord._start_time = None
        coord._end_time = None
        type(coord).automatic_control = PropertyMock(return_value=True)
        type(coord).last_cover_action = PropertyMock(return_value={})
        type(coord).last_skipped_action = PropertyMock(return_value={})
        coord.min_change = 5
        coord.time_threshold = 2
        coord._switch_mode = False
        coord._inverse_state = False
        coord._use_interpolation = False
        coord.default_state = 50
        type(coord).state = PropertyMock(return_value=50)
        coord.config_entry = MagicMock()
        coord.config_entry.options = {}
        type(coord).is_motion_detected = PropertyMock(return_value=True)
        coord._motion_mgr = MagicMock()
        coord._motion_mgr._motion_timeout_active = False

        # Bind the real method
        coord.build_diagnostic_data = (
            AdaptiveDataUpdateCoordinator.build_diagnostic_data.__get__(coord)
        )
        return coord

    def test_logs_on_first_call(self):
        """First call logs the explanation (empty → something)."""
        coord = self._make_coordinator_mock()
        coord.build_diagnostic_data()
        coord.logger.debug.assert_called()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("Position explanation changed" in c for c in calls)

    def test_logs_on_change(self):
        """Logs when explanation changes between calls."""
        coord = self._make_coordinator_mock()
        coord._last_position_explanation = "Sun tracking (40%)"
        coord.build_diagnostic_data()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("Position explanation changed" in c for c in calls)

    def test_no_log_when_unchanged(self):
        """Does NOT log when explanation is the same as last time."""
        coord = self._make_coordinator_mock()
        # First call to set the explanation
        coord.build_diagnostic_data()
        coord.logger.debug.reset_mock()
        # Second call with same state — should NOT log
        coord.build_diagnostic_data()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert not any("Position explanation changed" in c for c in calls)

    def test_updates_stored_explanation(self):
        """Stored explanation is updated after a change."""
        coord = self._make_coordinator_mock()
        coord._last_position_explanation = "Sun tracking (40%)"
        coord.build_diagnostic_data()
        # The explanation should now match the current state
        assert coord._last_position_explanation != "Sun tracking (40%)"
        assert len(coord._last_position_explanation) > 0
