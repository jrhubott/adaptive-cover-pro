"""Tests for ClimateCoverData properties."""

import pytest

from custom_components.adaptive_cover_pro.pipeline.handlers.climate import ClimateCoverData


def _make_climate(mock_logger, **overrides):
    """Build a ClimateCoverData with sensible defaults and optional overrides."""
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
    return ClimateCoverData(**defaults)


class TestClimateCoverData:
    """Test ClimateCoverData properties."""

    @pytest.mark.unit
    def test_outside_temperature_field(self, mock_logger):
        """Test outside temperature is a simple field."""
        climate_data = _make_climate(mock_logger, outside_temperature="22.5")
        assert climate_data.outside_temperature == "22.5"

    @pytest.mark.unit
    def test_inside_temperature_field(self, mock_logger):
        """Test inside temperature is a simple field."""
        climate_data = _make_climate(mock_logger, inside_temperature="23.0")
        assert climate_data.inside_temperature == "23.0"

    @pytest.mark.unit
    def test_get_current_temperature_outside(self, mock_logger):
        """Test get_current_temperature with temp_switch=True (outside)."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=True,
            outside_temperature="22.5",
            inside_temperature="21.0",
        )
        assert climate_data.get_current_temperature == 22.5

    @pytest.mark.unit
    def test_get_current_temperature_inside(self, mock_logger):
        """Test get_current_temperature with temp_switch=False (inside)."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=False,
            inside_temperature="21.0",
        )
        assert climate_data.get_current_temperature == 21.0

    @pytest.mark.unit
    def test_is_presence_true(self, mock_logger):
        """Test is_presence when pre-read as True."""
        climate_data = _make_climate(mock_logger, is_presence=True)
        assert climate_data.is_presence is True

    @pytest.mark.unit
    def test_is_presence_false(self, mock_logger):
        """Test is_presence when pre-read as False."""
        climate_data = _make_climate(mock_logger, is_presence=False)
        assert climate_data.is_presence is False

    @pytest.mark.unit
    def test_is_winter_true(self, mock_logger):
        """Test is_winter when temperature below threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="18.0",
            temp_low=20.0,
        )
        assert climate_data.is_winter is True

    @pytest.mark.unit
    def test_is_winter_false(self, mock_logger):
        """Test is_winter when temperature above threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            temp_low=20.0,
        )
        assert climate_data.is_winter is False

    @pytest.mark.unit
    def test_is_summer_true(self, mock_logger):
        """Test is_summer when temperature above threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="26.0",
            outside_temperature="24.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
        )
        assert climate_data.is_summer is True

    @pytest.mark.unit
    def test_is_summer_false(self, mock_logger):
        """Test is_summer when temperature below threshold."""
        climate_data = _make_climate(
            mock_logger,
            inside_temperature="22.0",
            temp_high=25.0,
        )
        assert climate_data.is_summer is False

    @pytest.mark.unit
    def test_outside_high_true(self, mock_logger):
        """Test outside_high when outside temp above threshold."""
        climate_data = _make_climate(
            mock_logger,
            outside_temperature="24.0",
            temp_summer_outside=22.0,
        )
        assert climate_data.outside_high is True

    @pytest.mark.unit
    def test_is_sunny_true(self, mock_logger):
        """Test is_sunny when pre-read as True."""
        climate_data = _make_climate(mock_logger, is_sunny=True)
        assert climate_data.is_sunny is True

    @pytest.mark.unit
    def test_is_sunny_false(self, mock_logger):
        """Test is_sunny when pre-read as False."""
        climate_data = _make_climate(mock_logger, is_sunny=False)
        assert climate_data.is_sunny is False

    @pytest.mark.unit
    def test_lux_below_threshold(self, mock_logger):
        """Test lux property when below threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=True)
        assert climate_data.lux is True

    @pytest.mark.unit
    def test_lux_above_threshold(self, mock_logger):
        """Test lux property when above threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_lux_disabled(self, mock_logger):
        """Test lux property when disabled (default False)."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_irradiance_below_threshold(self, mock_logger):
        """Test irradiance property when below threshold."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=True)
        assert climate_data.irradiance is True

    @pytest.mark.unit
    def test_irradiance_disabled(self, mock_logger):
        """Test irradiance property when disabled (default False)."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=False)
        assert climate_data.irradiance is False

    @pytest.mark.unit
    def test_lux_with_none_value(self, mock_logger):
        """Test lux property returns False when not below threshold."""
        climate_data = _make_climate(mock_logger, lux_below_threshold=False)
        assert climate_data.lux is False

    @pytest.mark.unit
    def test_irradiance_with_none_value(self, mock_logger):
        """Test irradiance property returns False when not below threshold."""
        climate_data = _make_climate(mock_logger, irradiance_below_threshold=False)
        assert climate_data.irradiance is False

    @pytest.mark.unit
    def test_get_current_temperature_with_none_values(self, mock_logger):
        """Test get_current_temperature returns None when all temps unavailable."""
        climate_data = _make_climate(
            mock_logger,
            temp_switch=True,
            outside_temperature=None,
            inside_temperature=None,
        )
        assert climate_data.get_current_temperature is None

    @pytest.mark.unit
    def test_outside_high_with_none_value(self, mock_logger):
        """Test outside_high returns True (default) when outside temp is None."""
        climate_data = _make_climate(
            mock_logger,
            outside_temperature=None,
            temp_summer_outside=30.0,
        )
        assert climate_data.outside_high is True
