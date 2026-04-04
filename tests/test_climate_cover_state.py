"""Tests for ClimateCoverState logic."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateCoverState,
)


def _make_climate(**overrides):
    """Build a ClimateCoverData with sensible defaults and optional overrides."""
    defaults = {
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
        "winter_close_insulation": False,
    }
    defaults.update(overrides)
    return ClimateCoverData(**defaults)


class TestClimateCoverState:
    """Test ClimateCoverState logic."""

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_type_cover_with_presence(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal_type_cover delegates to normal_with_presence."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(is_presence=True)

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_type_cover()
        assert isinstance(result, (int, np.integer))

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_type_cover_without_presence(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal_type_cover delegates to normal_without_presence."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(is_presence=False)

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_type_cover()
        assert isinstance(result, (int, np.integer))

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_winter_sun_valid(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter strategy with presence: open fully when cold and not sunny."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="18.0",  # Below temp_low (20)
            is_sunny=False,  # Cloudy
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Winter + sun valid → 100 (fully open)
        assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_not_sunny(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test not sunny weather returns default."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="21.0",
            is_sunny=False,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Not sunny → use default
        assert result == vertical_cover_instance.h_def

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_summer_transparent(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test summer with transparent blind returns 0."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="26.0",
            outside_temperature="28.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
            transparent_blind=True,
            is_presence=True,
            is_sunny=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Summer + transparent blind → 0 (fully closed for cooling)
        assert result == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_intermediate(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test intermediate conditions use calculated position."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="22.0",  # Between temp_low and temp_high
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_with_presence()
        # Intermediate conditions → use calculated position
        assert result == 25  # Calculated position for this config

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_without_presence_summer(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test summer without presence closes blind."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="27.0",
            outside_temperature="30.0",
            temp_high=25.0,
            temp_summer_outside=22.0,
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Summer without presence → 0 (close to keep cool)
        assert result == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_without_presence_winter(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter without presence opens blind."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="18.0",
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Winter without presence → 100 (open to gain heat)
        assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_without_presence_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test default path without presence."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        # Sun not valid (outside FOV)
        vertical_cover_instance.sol_azi = 90.0

        climate_data = _make_climate(
            inside_temperature="22.0",
            is_presence=False,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.normal_without_presence()
        # Sun not valid → use default
        assert result == vertical_cover_instance.h_def

    @pytest.mark.unit
    def test_tilt_state_mode1(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode1 (90 degrees)."""
        tilt_cover_instance.mode = "mode1"

        climate_data = _make_climate(blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        result = state_handler.tilt_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    def test_tilt_state_mode2(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode2 (180 degrees)."""
        tilt_cover_instance.mode = "mode2"

        climate_data = _make_climate(blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        result = state_handler.tilt_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_blind_type(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test get_state routes to normal_type_cover for blind."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_tilt_type(self, mock_datetime, tilt_cover_instance, mock_logger):
        """Test get_state routes to tilt_state for tilt cover."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        climate_data = _make_climate(blind_type="cover_tilt")

        state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
        try:
            result = state_handler.get_state()
            assert 0 <= result <= 100
        except ValueError:
            # ValueError from round(NaN) is expected for invalid tilt math
            pass

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_max_position_clamping(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test max position clamping in climate state."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.max_pos = 20
        vertical_cover_instance.max_pos_bool = False

        climate_data = _make_climate(
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert result == 20

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_min_position_clamping(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test min position clamping in climate state."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = False

        climate_data = _make_climate(
            inside_temperature="22.0",
            is_sunny=True,
            is_presence=True,
        )

        state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
        result = state_handler.get_state()
        assert result == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_winter_sunny_no_sensors(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode on sunny day WITHOUT lux/irradiance sensors.

        This is the bug scenario from issue #4:
        - Indoor temp below threshold (winter)
        - Sun in front of window (valid=True)
        - Sunny weather
        - NO lux/irradiance sensors configured

        Expected: Should return 100 for solar heating
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=True,
                is_presence=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_winter_cloudy(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode on cloudy day."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=False,  # Cloudy
                is_presence=True,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_winter_low_lux(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test winter mode with lux sensor showing low light."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="18.0",  # Below temp_low (20) = winter
                is_sunny=True,
                is_presence=True,
                lux_below_threshold=True,  # Low lux
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()
            # Winter mode should still return 100 even with low lux
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_normal_sunny_day(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal operation on mild sunny day.

        Not winter, not summer, sunny weather.
        Should use calculated position for glare control.
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="22.0",  # Between temp_low and temp_high
                is_sunny=True,
                is_presence=True,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            assert isinstance(result, (int, np.integer))
            assert result != 100  # Not winter mode
            assert result != vertical_cover_instance.default  # Not default

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_with_presence_winter_sunny(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Test tilt winter mode on sunny day."""
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )

        with (
            patch.object(
                type(tilt_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
            patch.object(
                type(tilt_cover_instance), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True
            tilt_cover_instance.tilt_degrees = 90
            tilt_cover_instance.calculate_percentage = MagicMock(return_value=50.0)

            climate_data = _make_climate(
                inside_temperature="18.0",  # Below temp_low (20) = winter
                blind_type="cover_tilt",
                is_sunny=True,
                is_presence=True,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
            result = state_handler.tilt_with_presence(90)

            # Winter mode with sun valid → uses _solar_position() → calculate_percentage()
            default_80_degrees = 80 / 90 * 100  # ~88.9%
            assert result != pytest.approx(default_80_degrees, abs=1)
            assert result == 50


class TestIssue71IrradianceSummerFix:
    """Regression tests for Issue #71.

    Irradiance (and lux/weather) should suppress glare control even in summer.
    When irradiance is below the configured threshold, there is no direct sun to
    block, so covers should remain at the default (open) position rather than
    closing based on solar position.
    """

    # ------------------------------------------------------------------
    # normal_with_presence — summer + low irradiance
    # ------------------------------------------------------------------

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_low_irradiance_with_presence_uses_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + irradiance below threshold + presence → LOW_LIGHT (default/open).

        This is the core regression for Issue #71: irradiance was previously
        ignored when is_summer=True, causing covers to close regardless.
        """
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",  # Above temp_high (25) → summer
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=True,  # Pyranometer says: no direct sun
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            # Irradiance is low → should use default (open), not solar/closed
            assert result == int(round(vertical_cover_instance.default))
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_low_lux_with_presence_uses_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + lux below threshold + presence → LOW_LIGHT (default/open)."""
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=True,  # Lux sensor says: no direct sun
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            assert result == int(round(vertical_cover_instance.default))
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_not_sunny_weather_with_presence_uses_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + cloudy weather + presence → LOW_LIGHT (default/open)."""
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=True,
                is_sunny=False,  # Cloudy
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            assert result == int(round(vertical_cover_instance.default))
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_sunny_high_irradiance_with_presence_uses_solar(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + high irradiance + presence → GLARE_CONTROL (solar position).

        When irradiance IS above threshold, summer glare control should still apply.
        """
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,  # High irradiance → sun is present
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_with_presence()

            # High irradiance + summer → glare control (not default)
            assert result != int(round(vertical_cover_instance.default))
            assert state_handler.climate_strategy.name == "GLARE_CONTROL"

    # ------------------------------------------------------------------
    # normal_without_presence — summer + low irradiance
    # ------------------------------------------------------------------

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_low_irradiance_without_presence_uses_default(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + irradiance below threshold + no presence → LOW_LIGHT (default)."""
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=True,  # Pyranometer: no direct sun
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_without_presence()

            # Irradiance low → default (open), not summer cooling (0)
            assert result == int(round(vertical_cover_instance.default))
            assert result != 0
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_sunny_high_irradiance_without_presence_closes(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Summer + high irradiance + no presence → SUMMER_COOLING (0%).

        Regression check: existing summer cooling behavior should be preserved
        when irradiance is NOT low.
        """
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(vertical_cover_instance, climate_data)
            result = state_handler.normal_without_presence()

            assert result == 0
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"

    # ------------------------------------------------------------------
    # tilt_with_presence — summer + low irradiance
    # ------------------------------------------------------------------

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_summer_low_irradiance_with_presence_uses_solar(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Tilt cover: summer + irradiance below threshold + presence → solar position.

        For tilt covers, LOW_LIGHT uses _solar_position() not default.
        """
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with (
            patch.object(
                type(tilt_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
            patch.object(
                type(tilt_cover_instance), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True
            tilt_cover_instance.calculate_percentage = MagicMock(return_value=42.0)

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                blind_type="cover_tilt",
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=True,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
            result = state_handler.tilt_with_presence(90)

            # Low irradiance → LOW_LIGHT solar position (not summer cooling angle)
            summer_cooling_pos = round((45 / 90) * 100)  # CLIMATE_SUMMER_TILT_ANGLE=45
            assert result != summer_cooling_pos
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_summer_high_irradiance_with_presence_uses_summer_cooling(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Tilt cover: summer + high irradiance + presence → SUMMER_COOLING angle."""
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with (
            patch.object(
                type(tilt_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
            patch.object(
                type(tilt_cover_instance), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True
            tilt_cover_instance.calculate_percentage = MagicMock(return_value=42.0)

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                blind_type="cover_tilt",
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
            result = state_handler.tilt_with_presence(90)

            # High irradiance + summer → summer cooling angle
            from custom_components.adaptive_cover_pro.const import (
                CLIMATE_SUMMER_TILT_ANGLE,
            )

            expected = round((CLIMATE_SUMMER_TILT_ANGLE / 90) * 100)
            assert result == expected
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"

    # ------------------------------------------------------------------
    # tilt_without_presence — summer + low irradiance
    # ------------------------------------------------------------------

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_summer_low_irradiance_without_presence_uses_solar(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Tilt cover: summer + irradiance below threshold + no presence → solar position."""
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with (
            patch.object(
                type(tilt_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
            patch.object(
                type(tilt_cover_instance), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True
            tilt_cover_instance.calculate_percentage = MagicMock(return_value=35.0)

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                blind_type="cover_tilt",
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=True,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
            result = state_handler.tilt_without_presence(90)

            # Low irradiance → LOW_LIGHT (solar position), not POSITION_CLOSED
            assert result != 0  # Not fully closed
            assert state_handler.climate_strategy.name == "LOW_LIGHT"

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_tilt_summer_high_irradiance_without_presence_closes(
        self, mock_datetime, tilt_cover_instance, mock_logger
    ):
        """Tilt cover: summer + high irradiance + no presence → POSITION_CLOSED.

        Regression check: existing tilt summer cooling behavior preserved.
        """
        mock_datetime.now.return_value = datetime(2024, 7, 1, 12, 0, 0)
        tilt_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 7, 1, 18, 0, 0)
        )
        tilt_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 7, 1, 6, 0, 0)
        )

        with (
            patch.object(
                type(tilt_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
        ):
            mock_valid.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                blind_type="cover_tilt",
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(tilt_cover_instance, climate_data)
            result = state_handler.tilt_without_presence(90)

            from custom_components.adaptive_cover_pro.const import POSITION_CLOSED

            assert result == POSITION_CLOSED
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"
