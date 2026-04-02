"""Pytest fixtures for Adaptive Cover Pro tests."""

from unittest.mock import MagicMock, Mock

import pytest

from homeassistant.core import State

from custom_components.adaptive_cover_pro.config_context_adapter import (
    ConfigContextAdapter,
)

from .cover_helpers import (  # noqa: F401 — re-exported for convenience
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
    make_cover_config,
    make_horizontal_config,
    make_tilt_config,
    make_vertical_config,
)


@pytest.fixture
def hass():
    """Return a mock HomeAssistant instance."""
    hass_mock = MagicMock()
    hass_mock.states.get.return_value = None
    hass_mock.config.units.temperature_unit = "°C"
    return hass_mock


@pytest.fixture
def mock_logger():
    """Return a mock ConfigContextAdapter logger."""
    logger = MagicMock(spec=ConfigContextAdapter)
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def mock_sun_data():
    """Return a mock SunData instance."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return sun_data


@pytest.fixture
def sample_vertical_config():
    """Return standard vertical cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 50,
        "d_top": 0.0,
        "d_bottom": 2.0,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_horizontal_config():
    """Return standard horizontal cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 100,
        "length": 2.0,
        "awning_angle": 0,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_tilt_config():
    """Return standard tilt cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 50,
        "slat_depth": 0.02,
        "slat_distance": 0.03,
        "tilt_mode": "mode1",
        "tilt_distance": 0.5,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_climate_config():
    """Return standard climate mode configuration for testing."""
    return {
        "temp_entity": "sensor.outside_temperature",
        "temp_low": 20.0,
        "temp_high": 25.0,
        "presence_entity": "binary_sensor.presence",
        "weather_entity": "weather.home",
        "weather_state": ["sunny", "partlycloudy"],
        "lux_entity": None,
        "lux_threshold": None,
        "irradiance_entity": None,
        "irradiance_threshold": None,
    }


@pytest.fixture
def mock_state():
    """Return a mock Home Assistant state object."""

    def _create_state(entity_id: str, state: str, attributes: dict | None = None):
        state_obj = MagicMock(spec=State)
        state_obj.entity_id = entity_id
        state_obj.state = state
        state_obj.attributes = attributes or {}
        return state_obj

    return _create_state


@pytest.fixture
def vertical_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveVerticalCover instance for testing."""
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
def horizontal_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveHorizontalCover instance for testing."""
    return build_horizontal_cover(
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
        h_def=100,
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
        awn_length=2.0,
        awn_angle=0,
    )


@pytest.fixture
def tilt_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveTiltCover instance for testing."""
    return build_tilt_cover(
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
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
    )


@pytest.fixture
def climate_data_instance(mock_logger):
    """ClimateCoverData instance with pre-read values."""
    from custom_components.adaptive_cover_pro.pipeline.handlers.climate import ClimateCoverData

    return ClimateCoverData(
        logger=mock_logger,
        temp_low=20.0,
        temp_high=25.0,
        temp_switch=True,
        blind_type="cover_blind",
        transparent_blind=False,
        temp_summer_outside=22.0,
        outside_temperature="22.5",
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
    )
