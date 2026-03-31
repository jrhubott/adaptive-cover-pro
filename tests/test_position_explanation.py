"""Tests for climate strategy tracking and position explanation diagnostics (Issue #68).

Tests cover:
- ClimateCoverState.climate_strategy is set correctly for each decision branch
- _build_position_explanation produces correct strings for all scenarios
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

from custom_components.adaptive_cover_pro.calculation import (
    ClimateCoverData,
    ClimateCoverState,
)
from custom_components.adaptive_cover_pro.enums import ClimateStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_climate_data(hass, mock_logger, **overrides):
    """Build a ClimateCoverData with minimal defaults."""
    defaults = {
        "hass": hass,
        "logger": mock_logger,
        "temp_entity": None,
        "temp_low": 20.0,
        "temp_high": 25.0,
        "presence_entity": None,
        "weather_entity": None,
        "weather_condition": ["sunny"],
        "outside_entity": None,
        "temp_switch": False,
        "blind_type": "cover_blind",
        "transparent_blind": False,
        "lux_entity": None,
        "irradiance_entity": None,
        "lux_threshold": 5000,
        "irradiance_threshold": 300,
        "temp_summer_outside": 22.0,
        "_use_lux": False,
        "_use_irradiance": False,
    }
    defaults.update(overrides)
    return ClimateCoverData(**defaults)


def make_climate_state(cover, climate_data):
    """Build a ClimateCoverState, mocking sun data to avoid HA calls."""
    state = ClimateCoverState(cover, climate_data)
    return state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def mock_sun_data():
    """Mock SunData instance."""
    return MagicMock()


@pytest.fixture
def hass():
    """Mock HomeAssistant (used by ClimateCoverData)."""
    h = MagicMock()
    h.states.get.return_value = None
    h.config.units.temperature_unit = "°C"
    return h


@pytest.fixture
def vertical_cover(mock_sun_data, mock_logger):
    """Vertical cover with sun directly in front."""
    from custom_components.adaptive_cover_pro.calculation import AdaptiveVerticalCover

    return AdaptiveVerticalCover(
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


# ---------------------------------------------------------------------------
# Climate Strategy Tests — normal_with_presence
# ---------------------------------------------------------------------------


class TestClimateStrategyNormalWithPresence:
    """ClimateCoverState sets climate_strategy correctly for normal_with_presence."""

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        # Simulate winter (low temperature)
        temp_state = MagicMock()
        temp_state.state = "10.0"
        hass.states.get.side_effect = lambda eid: (
            temp_state if "temp" in eid else MagicMock(state="on")
        )

        climate_data = make_climate_data(
            hass,
            mock_logger,
            temp_entity="sensor.temp",
            temp_low=20.0,
            temp_high=25.0,
            presence_entity="binary_sensor.presence",
            weather_condition=["sunny"],
        )

        # Force winter + presence + sun valid
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
                type(climate_data),
                "is_presence",
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
            result = state_handler.normal_with_presence()

        assert result == 100
        assert state_handler.climate_strategy == ClimateStrategy.WINTER_HEATING

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
    def test_low_light_strategy(self, mock_datetime, hass, mock_logger, vertical_cover):
        """Not summer + low lux → LOW_LIGHT."""
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = make_climate_data(hass, mock_logger)

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
                type(climate_data),
                "is_presence",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(climate_data), "lux", new_callable=PropertyMock, return_value=True
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

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        # transparent_blind is a dataclass field — set it directly
        climate_data = make_climate_data(hass, mock_logger, transparent_blind=True)

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
                type(climate_data),
                "is_presence",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(climate_data), "lux", new_callable=PropertyMock, return_value=False
            ),
            patch.object(
                type(climate_data),
                "irradiance",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_sunny",
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

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        # transparent_blind=False is the default; set explicitly to be clear
        climate_data = make_climate_data(hass, mock_logger, transparent_blind=False)

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
                type(climate_data),
                "is_presence",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                type(climate_data), "lux", new_callable=PropertyMock, return_value=False
            ),
            patch.object(
                type(climate_data),
                "irradiance",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(climate_data),
                "is_sunny",
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
            state_handler.normal_with_presence()

        assert state_handler.climate_strategy == ClimateStrategy.GLARE_CONTROL


# ---------------------------------------------------------------------------
# Climate Strategy Tests — normal_without_presence
# ---------------------------------------------------------------------------


class TestClimateStrategyNormalWithoutPresence:
    """ClimateCoverState sets climate_strategy correctly for normal_without_presence."""

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        climate_data = make_climate_data(hass, mock_logger)

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
                type(climate_data),
                "is_presence",
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

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        climate_data = make_climate_data(hass, mock_logger)

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
                type(climate_data),
                "is_presence",
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

        assert result == 100
        assert state_handler.climate_strategy == ClimateStrategy.WINTER_HEATING

    @patch("custom_components.adaptive_cover_pro.calculation.datetime")
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

        climate_data = make_climate_data(hass, mock_logger)

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
                type(climate_data),
                "is_presence",
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
    """_build_position_explanation returns correct strings for all scenarios."""

    def _make_coordinator(self):
        """Build a minimal mock coordinator with the real method bound."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        # Bind the real method
        coord._build_position_explanation = (
            AdaptiveDataUpdateCoordinator._build_position_explanation.__get__(coord)
        )
        coord._CLIMATE_STRATEGY_LABELS = (
            AdaptiveDataUpdateCoordinator._CLIMATE_STRATEGY_LABELS
        )

        # Defaults — no overrides
        coord.is_force_override_active = False
        coord.is_motion_timeout_active = False
        coord.manager = MagicMock()
        coord.manager.binary_cover_manual = False
        coord.check_adaptive_time = True
        coord._switch_mode = False
        coord._use_interpolation = False
        coord._inverse_state = False
        coord.climate_state = None
        coord.climate_strategy = None
        coord.default_state = 50
        coord.raw_calculated_position = 50

        # Normal cover state mock
        cover_mock = MagicMock()
        cover_mock.direct_sun_valid = True
        cover_mock.sunset_valid = False
        cover_mock.sunset_pos = None
        cover_mock.control_state_reason = "Direct Sun"
        cover_mock.default = 50
        normal_state = MagicMock()
        normal_state.cover = cover_mock
        coord.normal_cover_state = normal_state

        coord.config_entry = MagicMock()
        coord.config_entry.options = {}

        # state property
        type(coord).state = PropertyMock(return_value=50)

        return coord

    def test_force_override(self):
        """Force override active → explains override position."""
        coord = self._make_coordinator()
        coord.is_force_override_active = True
        coord.config_entry.options = {"force_override_position": 0}

        result = coord._build_position_explanation()
        assert "Force override" in result
        assert "0%" in result

    def test_motion_timeout(self):
        """Motion timeout active → explains default position."""
        coord = self._make_coordinator()
        coord.is_motion_timeout_active = True
        coord.default_state = 30

        result = coord._build_position_explanation()
        assert "motion" in result.lower()
        assert "30%" in result

    def test_manual_override(self):
        """Manual override → explains manual control."""
        coord = self._make_coordinator()
        coord.manager.binary_cover_manual = True

        result = coord._build_position_explanation()
        assert "manual" in result.lower()

    def test_outside_time_window_with_sunset_position(self):
        """Outside time window with sunset position set → shows Sunset Position."""
        coord = self._make_coordinator()
        coord.check_adaptive_time = False
        coord.config_entry.options = {"sunset_position": 30}

        result = coord._build_position_explanation()
        assert "Sunset Position" in result
        assert "30%" in result

    def test_outside_time_window_without_sunset_position(self):
        """Outside time window without sunset position → shows Default Position."""
        coord = self._make_coordinator()
        coord.check_adaptive_time = False
        coord.config_entry.options = {"default_percentage": 100}

        result = coord._build_position_explanation()
        assert "Default Position" in result
        assert "100%" in result

    def test_sunset_offset_with_sunset_position(self):
        """In window but sunset_valid with sunset_pos set → shows Sunset Position."""
        coord = self._make_coordinator()
        coord.normal_cover_state.cover.direct_sun_valid = False
        coord.normal_cover_state.cover.sunset_valid = True
        coord.normal_cover_state.cover.sunset_pos = 20

        result = coord._build_position_explanation()
        assert "Sunset Position" in result
        assert "20%" in result

    def test_default_fov_exit_without_sunset(self):
        """In window, FOV exit, no sunset → shows Default Position."""
        coord = self._make_coordinator()
        coord.normal_cover_state.cover.direct_sun_valid = False
        coord.normal_cover_state.cover.sunset_valid = False
        coord.normal_cover_state.cover.sunset_pos = None
        coord.normal_cover_state.cover.control_state_reason = "Default: FOV Exit"
        coord.normal_cover_state.cover.default = 100

        result = coord._build_position_explanation()
        assert "FOV Exit" in result
        assert "Default Position" in result
        assert "100%" in result

    def test_sun_tracking_no_limits(self):
        """Sun tracking, no limits, no climate → shows raw tracking position."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 65
        coord.normal_cover_state.cover.direct_sun_valid = True

        result = coord._build_position_explanation()
        assert "Sun tracking" in result
        assert "65%" in result

    def test_sun_tracking_with_min_limit(self):
        """Sun tracking below min_position → shows limit applied."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 30
        coord.default_state = 60  # min limit bumped it to 60
        coord.normal_cover_state.cover.direct_sun_valid = True
        coord.config_entry.options = {
            "min_position": 60,
            "enable_min_position": True,
        }

        result = coord._build_position_explanation()
        assert "Sun tracking" in result
        assert "min limit" in result
        assert "60%" in result

    def test_climate_winter_heating(self):
        """Sun tracking + climate winter heating → shows winter heating decision."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 45
        coord._switch_mode = True
        coord.climate_state = 100
        coord.climate_strategy = ClimateStrategy.WINTER_HEATING
        type(coord).state = PropertyMock(return_value=100)

        result = coord._build_position_explanation()
        assert "Sun tracking" in result
        assert "Winter Heating" in result
        assert "100%" in result

    def test_climate_summer_cooling(self):
        """Sun tracking + climate summer cooling → shows summer cooling."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 45
        coord._switch_mode = True
        coord.climate_state = 0
        coord.climate_strategy = ClimateStrategy.SUMMER_COOLING
        type(coord).state = PropertyMock(return_value=0)

        result = coord._build_position_explanation()
        assert "Summer Cooling" in result
        assert "0%" in result

    def test_climate_low_light(self):
        """Default position + climate low light strategy."""
        coord = self._make_coordinator()
        coord.normal_cover_state.cover.direct_sun_valid = False
        coord.normal_cover_state.cover.control_state_reason = "Default: FOV Exit"
        coord.normal_cover_state.cover.default = 0
        coord.raw_calculated_position = 0
        coord._switch_mode = True
        coord.climate_state = 0
        coord.climate_strategy = ClimateStrategy.LOW_LIGHT
        type(coord).state = PropertyMock(return_value=0)

        result = coord._build_position_explanation()
        assert "FOV Exit" in result
        assert "Low Light" in result

    def test_sun_tracking_with_interpolation(self):
        """Interpolation applied → shows interpolated final value."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 72
        coord._use_interpolation = True
        type(coord).state = PropertyMock(return_value=65)

        result = coord._build_position_explanation()
        assert "interpolated" in result
        assert "65%" in result

    def test_sun_tracking_with_inverse(self):
        """Inverse state applied → shows inversed final value."""
        coord = self._make_coordinator()
        coord.raw_calculated_position = 72
        coord._inverse_state = True
        type(coord).state = PropertyMock(return_value=28)

        result = coord._build_position_explanation()
        assert "invers" in result.lower()
        assert "28%" in result


# ---------------------------------------------------------------------------
# Position Explanation Change Detection Logging Tests (A1)
# ---------------------------------------------------------------------------


class TestPositionExplanationChangeDetection:
    """_build_position_diagnostics logs position explanation only when it changes."""

    def _make_coordinator_with_diagnostics(self):
        """Build a minimal mock coordinator with real _build_position_diagnostics bound."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord._build_position_explanation = MagicMock(return_value="Sun tracking (50%)")
        coord._last_position_explanation = ""
        coord.logger = MagicMock()
        coord.logger.debug = MagicMock()

        # Minimal stubs needed by the real _build_position_diagnostics
        coord.raw_calculated_position = 50
        coord.climate_state = None
        coord._determine_control_status = MagicMock(return_value="solar")
        coord._get_control_state_reason = MagicMock(return_value="Direct Sun")
        coord.min_change = 5
        coord.time_threshold = 2
        coord.last_cover_action = {
            "entity_id": None,
            "position": None,
            "timestamp": None,
        }
        coord.last_skipped_action = {"entity_id": None}
        coord.normal_cover_state = None

        coord._build_position_diagnostics = (
            AdaptiveDataUpdateCoordinator._build_position_diagnostics.__get__(coord)
        )
        return coord

    def test_logs_on_first_call(self):
        """First call logs the explanation (empty → something)."""
        coord = self._make_coordinator_with_diagnostics()
        coord._build_position_diagnostics()
        coord.logger.debug.assert_called()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("Position explanation changed" in c for c in calls)

    def test_logs_on_change(self):
        """Logs when explanation changes between calls."""
        coord = self._make_coordinator_with_diagnostics()
        coord._last_position_explanation = "Sun tracking (40%)"
        coord._build_position_explanation.return_value = "Sun tracking (50%)"
        coord._build_position_diagnostics()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert any("Position explanation changed" in c for c in calls)

    def test_no_log_when_unchanged(self):
        """Does NOT log when explanation is the same as last time."""
        coord = self._make_coordinator_with_diagnostics()
        coord._last_position_explanation = "Sun tracking (50%)"
        coord._build_position_explanation.return_value = "Sun tracking (50%)"
        coord._build_position_diagnostics()
        calls = [str(c) for c in coord.logger.debug.call_args_list]
        assert not any("Position explanation changed" in c for c in calls)

    def test_updates_stored_explanation(self):
        """Stored explanation is updated after a change."""
        coord = self._make_coordinator_with_diagnostics()
        coord._last_position_explanation = "Sun tracking (40%)"
        coord._build_position_explanation.return_value = "Manual override active"
        coord._build_position_diagnostics()
        assert coord._last_position_explanation == "Manual override active"
