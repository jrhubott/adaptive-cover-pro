"""Tests for ClimateCoverState logic."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.const import ClimateStrategy
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateCoverState,
)
from tests.conftest import make_snapshot_for_cover
from tests.cover_helpers import build_tilt_cover


def _make_climate(**overrides):
    """Build a ClimateCoverData with sensible defaults and optional overrides.

    Translates the legacy ``blind_type="cover_X"`` shorthand used throughout
    these tests into the modern ``policy=get_policy("cover_X")`` form, so
    individual call sites can keep passing ``blind_type=`` and the helper
    handles the rename transparently.
    """
    if "blind_type" in overrides:
        overrides["policy"] = get_policy(overrides.pop("blind_type"))
    defaults = {
        "temp_low": 20.0,
        "temp_high": 25.0,
        "temp_switch": False,
        "policy": get_policy("cover_blind"),
        "transparent_blind": False,
        "temp_summer_outside": 22.0,
        "outside_temperature": None,
        "inside_temperature": None,
        "is_presence": True,
        "is_sunny": True,
        "lux_below_threshold": False,
        "irradiance_below_threshold": False,
        "winter_close_insulation": False,
        "summer_close_bypass_sun_floor": False,
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.normal_type_cover()
        # Intermediate + sunny + presence defers to solar/glare pipeline
        assert result is None

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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.normal_type_cover()
        assert isinstance(result, int | np.integer)

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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.normal_with_presence()
        # Intermediate + sunny + presence defers to solar/glare pipeline
        assert result is None

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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.normal_without_presence()
        # Sun not valid → use default
        assert result == vertical_cover_instance.h_def

    @pytest.mark.unit
    def test_tilt_state_mode1(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode1 (90 degrees)."""
        tilt_cover_instance.mode = "mode1"

        climate_data = _make_climate(policy=get_policy("cover_tilt"))

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                tilt_cover_instance, tilt_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.tilt_state()
        assert 0 <= result <= 100

    @pytest.mark.unit
    def test_tilt_state_mode2(self, tilt_cover_instance, mock_logger):
        """Test tilt_state with mode2 (180 degrees)."""
        tilt_cover_instance.mode = "mode2"

        climate_data = _make_climate(policy=get_policy("cover_tilt"))

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                tilt_cover_instance, tilt_cover_instance.config.h_def
            ),
            climate_data,
        )
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

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.get_state()
        # Intermediate + sunny + presence defers to solar/glare pipeline
        assert result is None

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

        climate_data = _make_climate(policy=get_policy("cover_tilt"))

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                tilt_cover_instance, tilt_cover_instance.config.h_def
            ),
            climate_data,
        )
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
            inside_temperature="18.0",  # Below temp_low (20) → winter → returns 100
            is_sunny=False,
            is_presence=True,
        )

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
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
            inside_temperature="27.0",  # Above temp_high (25) → summer + transparent → returns 0
            is_sunny=True,
            is_presence=True,
            transparent_blind=True,
        )

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.get_state()
        assert result == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_summer_honors_sun_tracking_only_min(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Regression for issue #631: summer close must respect min_position when
        enable_min_position=True (sun-tracking-only mode) and sun is in FOV.

        Before the fix, get_state() passed sun_valid=False to apply_snapshot_limits,
        which caused the "sun-tracking only" gate to skip the min floor entirely —
        returning 0 even when min_pos=30 was configured.  The fix passes
        self.cover.direct_sun_valid so the min is honoured when tracking.
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        # "sun tracking only" min — enable_min_position=True
        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = True

        climate_data = _make_climate(
            inside_temperature="27.0",  # Above temp_high (25) → summer
            is_sunny=True,
            is_presence=True,
            transparent_blind=True,  # Required for NORMAL_WITH_PRESENCE summer rule
        )

        # Sun is centred in the window (sol_azi=win_azi=180, gamma≈0) so
        # direct_sun_valid is True — exactly the user's "during sun tracking" window.
        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.get_state()
        # Summer closes to 0; min_pos=30 with sun in FOV must floor it to 30.
        assert result == 30

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_get_state_sun_tracking_only_min_skipped_when_sun_not_in_fov(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Night behaviour preserved: sun-tracking-only min is NOT applied when
        direct_sun_valid is False (sun has set / outside FOV).

        With enable_min_position=True, the min floor must be skipped at night so
        the cover can fully close as the user intends.
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        vertical_cover_instance.sun_data.sunset = MagicMock(
            return_value=datetime(2024, 1, 1, 18, 0, 0)
        )
        vertical_cover_instance.sun_data.sunrise = MagicMock(
            return_value=datetime(2024, 1, 1, 6, 0, 0)
        )
        # "sun tracking only" min
        vertical_cover_instance.min_pos = 30
        vertical_cover_instance.min_pos_bool = True

        with (
            patch.object(
                type(vertical_cover_instance), "valid", new_callable=PropertyMock
            ) as mock_valid,
            patch.object(
                type(vertical_cover_instance),
                "direct_sun_valid",
                new_callable=PropertyMock,
            ) as mock_dsv,
        ):
            mock_valid.return_value = False  # Sun outside FOV / has set
            mock_dsv.return_value = False

            # Intermediate temperature (not summer, not winter) + no presence
            # → NORMAL_WITHOUT_PRESENCE falls through to LOW_LIGHT → default_position.
            # default_position=0, so without a floor the result is 0.
            climate_data = _make_climate(
                inside_temperature="22.0",  # Between temp_low=20 and temp_high=25
                is_sunny=False,
                is_presence=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance,
                    default_position=0,  # Explicit 0 so the floor would be visible
                ),
                climate_data,
            )
            result = state_handler.get_state()
            # Min floor must NOT apply when sun is out of FOV → result stays at 0
            assert result is not None
            assert (
                result < 30
            ), f"Expected result < 30 (min floor skipped at night) but got {result}"

    # -----------------------------------------------------------------------
    # Issue #689: summer_close_bypass_sun_floor
    # -----------------------------------------------------------------------

    def _make_summer_bypass_handler(
        self, cover, *, bypass: bool, mock_datetime
    ) -> ClimateCoverState:
        """Build a summer-close handler with sun in FOV and a 5% sun-tracking floor.

        ``min_pos`` stays at the global 0, while ``min_pos_sun_tracking`` is the
        5% sun-in-FOV floor.  Summer raw close is 0, so the only thing that can
        lift it is the sun-tracking floor — which is exactly what the bypass flag
        controls.
        """
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        cover.sun_data.sunset = MagicMock(return_value=datetime(2024, 1, 1, 18, 0, 0))
        cover.sun_data.sunrise = MagicMock(return_value=datetime(2024, 1, 1, 6, 0, 0))
        # Global min stays at 0; the 5% floor lives in min_pos_sun_tracking.
        cover.min_pos = 0
        cover.min_pos_bool = True  # enforce min only during sun tracking
        cover.config.min_pos_sun_tracking = 5

        climate_data = _make_climate(
            inside_temperature="27.0",  # Above temp_high (25) → summer
            is_sunny=True,
            is_presence=True,
            transparent_blind=True,  # Required for NORMAL_WITH_PRESENCE summer rule
            summer_close_bypass_sun_floor=bypass,
        )
        return ClimateCoverState(
            make_snapshot_for_cover(cover, cover.config.h_def),
            climate_data,
        )

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_close_bypass_off_honors_sun_tracking_min(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Bypass off (default) → summer close honors the 5% sun-in-FOV floor (#689)."""
        handler = self._make_summer_bypass_handler(
            vertical_cover_instance, bypass=False, mock_datetime=mock_datetime
        )
        result = handler.get_state()
        # Sun in FOV → sun-tracking floor of 5 lifts the summer close from 0 to 5.
        assert result == 5

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_close_bypass_on_reaches_global_min(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Bypass on → summer close ignores the 5% floor and reaches global min 0 (#689)."""
        handler = self._make_summer_bypass_handler(
            vertical_cover_instance, bypass=True, mock_datetime=mock_datetime
        )
        result = handler.get_state()
        # Floor suppressed → summer close reaches the global min_position (0).
        assert result == 0

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_summer_close_bypass_on_leaves_winter_max_clamp_intact(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Bypass on must not touch the winter max clamp (regression #105)."""
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
            inside_temperature="18.0",  # Below temp_low (20) → winter → returns 100
            is_sunny=False,
            is_presence=True,
            summer_close_bypass_sun_floor=True,  # must be inert outside summer
        )

        state_handler = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            climate_data,
        )
        result = state_handler.get_state()
        # Winter close to 100 still clamped down to max_pos=20 — unchanged by bypass.
        assert result == 20

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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()
            # Winter mode should still return 100 even with low lux
            assert result == 100

    @pytest.mark.unit
    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_normal_with_presence_normal_sunny_day(
        self, mock_datetime, vertical_cover_instance, mock_logger
    ):
        """Test normal operation on mild sunny day.

        Not winter, not summer, sunny weather, presence detected.
        After refactor, GLARE_CONTROL defers to solar/glare pipeline → returns None.
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()

            # Intermediate + sunny + presence defers to solar/glare pipeline
            assert result is None

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
            # The solar branch reads the unrounded geometry via
            # ``calculate_raw_percentage`` (issue #978); keep it consistent with
            # the mocked percentage so the floor()-toward-coverage step yields 50.
            tilt_cover_instance.calculate_raw_percentage = MagicMock(return_value=50.0)

            climate_data = _make_climate(
                inside_temperature="18.0",  # Below temp_low (20) = winter
                policy=get_policy("cover_tilt"),
                is_sunny=True,
                is_presence=True,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()

            # Irradiance is low → should use default (open), not solar/closed
            assert result == int(round(vertical_cover_instance.config.h_def))
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()

            assert result == int(round(vertical_cover_instance.config.h_def))
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()

            assert result == int(round(vertical_cover_instance.config.h_def))
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_with_presence()

            # High irradiance + summer → glare control (not default)
            assert result != int(round(vertical_cover_instance.config.h_def))
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.normal_without_presence()

            # Irradiance low → default (open), not summer cooling (0)
            assert result == int(round(vertical_cover_instance.config.h_def))
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

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                climate_data,
            )
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
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=True,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

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
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

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
                policy=get_policy("cover_tilt"),
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=True,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_without_presence()

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
                policy=get_policy("cover_tilt"),
                is_presence=False,
                is_sunny=True,
                irradiance_below_threshold=False,
                winter_close_insulation=False,
                lux_below_threshold=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_without_presence()

            from custom_components.adaptive_cover_pro.const import POSITION_CLOSED

            assert result == POSITION_CLOSED
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"


class TestIssue373Mode2SummerTiltHemisphere:
    """Regression tests for Issue #373.

    MODE2 tilt blinds use a 0–180° range where >50% means the slat is tilted
    toward the upper/blocking hemisphere.  The summer cooling angle (45°) must
    be mapped to the far side of horizontal (135° equivalent = 75%) for MODE2,
    not to 25% (which is on the open/wrong hemisphere).
    """

    # ------------------------------------------------------------------
    # 1a — MODE2 summer with presence must land on blocking hemisphere
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_summer_mode2_with_presence_correct_hemisphere(
        self, tilt_cover_instance, mock_logger
    ):
        """MODE2 summer cooling must produce >50% (blocking hemisphere), not 25%."""
        tilt_cover_instance.mode = "mode2"

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

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

            assert result > 50, f"Expected >50 (blocking hemisphere) but got {result}"
            assert result >= 75, f"Expected >=75 but got {result}"
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"

    # ------------------------------------------------------------------
    # 1b — MODE1 summer with presence baseline unchanged (canary)
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_summer_mode1_with_presence_baseline_unchanged(
        self, tilt_cover_instance, mock_logger
    ):
        """MODE1 summer cooling formula must be unchanged after the MODE2 fix."""
        # tilt_cover_instance fixture defaults to mode1 — verify and keep it
        assert tilt_cover_instance.mode == "mode1"

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

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(
                    tilt_cover_instance, tilt_cover_instance.config.h_def
                ),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

            from custom_components.adaptive_cover_pro.const import (
                CLIMATE_SUMMER_TILT_ANGLE,
            )

            expected_mode1 = round((CLIMATE_SUMMER_TILT_ANGLE / 90) * 100)
            assert result == expected_mode1
            assert state_handler.climate_strategy.name == "SUMMER_COOLING"

    # ------------------------------------------------------------------
    # 1c — MODE2 summer with presence + min_pos=50 must not clamp to 50
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_summer_mode2_with_presence_min_pos_clamp(
        self, mock_logger, mock_sun_data
    ):
        """MODE2 summer result (75%) must survive min_pos=50 without clamping to 50.

        Mirrors the exact user symptom: min_position=50, enable_min_position=false
        (always enforce).  Pre-fix the raw result was 25 → clamped to 50 (horizontal).
        Post-fix the raw result is 75 → 75 > 50 so no clamp, stays at 75.
        """
        tilt = build_tilt_cover(
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
            min_pos=50,
            max_pos_bool=False,
            min_pos_bool=False,  # always enforce (not sun-only)
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            slat_distance=0.03,
            depth=0.02,
            mode="mode2",
        )

        with (
            patch.object(type(tilt), "valid", new_callable=PropertyMock) as mock_valid,
            patch.object(
                type(tilt), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(tilt, tilt.config.h_def),
                climate_data,
            )
            result = state_handler.tilt_state()

            assert (
                result > 50
            ), f"Expected result >50 (not clamped to horizontal) but got {result}"
            assert result == 75, f"Expected 75 but got {result}"

    # ------------------------------------------------------------------
    # 1d — Issue #373 E2E: MODE2 + min_pos=50 + out-of-FOV must not clamp
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_glare_control_mode2_min_pos_50_blocks_heat_issue_373(
        self, mock_logger, mock_sun_data
    ):
        """Issue #373 GLARE_CONTROL repro: MODE2 + min_pos=50 + sun out-of-FOV.

        Inputs mirror the user-reported scenario:
        - tilt MODE2 (0=closed-one-way, 50=horizontal/open, 100=closed-other-way)
        - min_position=50, enable_min_position=False (always enforce)
        - sun outside FOV (so cover.valid is False, climate falls to GLARE_CONTROL)
        - summer-ish climate with presence

        Pre-fix: GLARE_CONTROL fallback returns round(80/180*100) = 44, then
        apply_limits clamps up to 50 — the cover sits horizontal and does nothing.

        Post-fix: helper is gamma-aware. For positive gamma (sun east of normal
        per ACP's gamma sign convention: gamma = win_azi - sol_azi mod 360), the
        MODE2 closed-positive hemisphere gives raw 56% → survives the min_pos=50
        clamp → final 56%, no longer stuck at horizontal.

        The complementary negative-gamma case (raw 44 → still clamped to 50) is
        addressed by the config-summary ⚠️ warning that teaches users to avoid
        min_position ≥ 50 in MODE2.
        """
        tilt = build_tilt_cover(
            logger=mock_logger,
            sol_azi=90.0,  # east → gamma ≈ +90° from win_azi=180, out of FOV
            sol_elev=30.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=45,
            fov_right=45,
            win_azi=180,
            h_def=50,
            max_pos=100,
            min_pos=50,
            max_pos_bool=False,
            min_pos_bool=False,  # always enforce (not sun-only)
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            slat_distance=0.03,
            depth=0.02,
            mode="mode2",
        )

        climate_data = _make_climate(
            inside_temperature="73.0",
            outside_temperature="80.0",
            temp_low=65.0,
            temp_high=73.0,
            temp_summer_outside=70.0,
            temp_switch=False,
            policy=get_policy("cover_tilt"),
            is_presence=True,
            is_sunny=True,
            irradiance_below_threshold=False,
            lux_below_threshold=False,
            winter_close_insulation=False,
        )

        # direct_sun_valid is False because the sun is outside the FOV (gamma=90 >
        # fov_left=45).  The patch is needed because the base-class debug log in
        # direct_sun_valid eagerly evaluates sunset_valid even when valid=False,
        # which would crash on the unset mock_sun_data.sunset.
        with patch.object(
            type(tilt), "direct_sun_valid", new_callable=PropertyMock
        ) as mock_dsv:
            mock_dsv.return_value = False

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(tilt, tilt.config.h_def),
                climate_data,
            )
            # get_state() runs tilt_state() then apply_snapshot_limits — this is
            # exactly what the pipeline does and what reproduces the issue.
            result = state_handler.get_state()

        assert (
            result != 50
        ), f"Expected result != 50 (horizontal floor footgun) but got {result}"
        # Positive-gamma case: raw 56% (helper returns (180-80)/180*100 ≈ 56)
        # survives the min_pos=50 clamp.  Pre-fix returned 44 → clamped to 50.
        assert (
            result > 50
        ), f"Expected result > 50 (positive closed hemisphere) but got {result}"
        assert state_handler.climate_strategy == ClimateStrategy.GLARE_CONTROL

    # ------------------------------------------------------------------
    # 2a — MODE2 summer + presence + negative gamma: pick OTHER hemisphere
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_summer_mode2_with_presence_negative_gamma_picks_other_hemisphere(
        self, mock_logger, mock_sun_data
    ):
        """MODE2 summer with negative gamma must tilt to the negative hemisphere (25%).

        With gamma ≈ −40° (sun west of window normal under ACP's gamma sign
        convention), the open/blocking direction flips compared to positive gamma —
        the slat must tilt to the OTHER side of horizontal.  Pre-fix the code
        always returned 75% regardless of sun side.

        Note: SunGeometry.gamma = (win_azi - sol_azi + 180) % 360 - 180, so
        win_azi=180 + sol_azi=220 gives gamma ≈ -40°.
        """
        tilt = build_tilt_cover(
            logger=mock_logger,
            sol_azi=220.0,  # gamma ≈ -40° from win_azi=180
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=60,
            fov_right=60,
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
            mode="mode2",
        )

        with (
            patch.object(type(tilt), "valid", new_callable=PropertyMock) as mock_valid,
            patch.object(
                type(tilt), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(tilt, tilt.config.h_def),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

            assert result == 25, f"Expected 25 (negative hemisphere) but got {result}"
            assert state_handler.climate_strategy == ClimateStrategy.SUMMER_COOLING

    # ------------------------------------------------------------------
    # 2b — MODE2 summer + presence + positive gamma: keep 75% (canary)
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_tilt_summer_mode2_with_presence_positive_gamma_picks_75(
        self, mock_logger, mock_sun_data
    ):
        """MODE2 summer with positive gamma keeps the existing 75% answer.

        Canary for the positive-side hemisphere — the fix must not regress this
        path (which today returns 75%, the correct blocking-hemisphere answer).

        Note: SunGeometry.gamma = (win_azi - sol_azi + 180) % 360 - 180, so
        win_azi=180 + sol_azi=140 gives gamma ≈ +40°.
        """
        tilt = build_tilt_cover(
            logger=mock_logger,
            sol_azi=140.0,  # gamma ≈ +40° from win_azi=180
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=60,
            fov_right=60,
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
            mode="mode2",
        )

        with (
            patch.object(type(tilt), "valid", new_callable=PropertyMock) as mock_valid,
            patch.object(
                type(tilt), "direct_sun_valid", new_callable=PropertyMock
            ) as mock_dsv,
        ):
            mock_valid.return_value = True
            mock_dsv.return_value = True

            climate_data = _make_climate(
                inside_temperature="27.0",
                outside_temperature="30.0",
                temp_high=25.0,
                temp_summer_outside=22.0,
                policy=get_policy("cover_tilt"),
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
                winter_close_insulation=False,
            )

            state_handler = ClimateCoverState(
                make_snapshot_for_cover(tilt, tilt.config.h_def),
                climate_data,
            )
            result = state_handler.tilt_with_presence()

            assert result == 75, f"Expected 75 (positive hemisphere) but got {result}"
            assert state_handler.climate_strategy == ClimateStrategy.SUMMER_COOLING


class TestExtremeHeatRule:
    """Extreme-heat force-hold at the TOP of every climate table (issue #766).

    Crossing the outside-temp threshold holds a fixed position regardless of the
    sun's FOV, presence, or season — pre-empting even winter heating.  Below the
    threshold every existing rule resumes unchanged.
    """

    def _extreme(self, **overrides):
        """Extreme-heat climate data: hot outside, threshold crossed, hold 30%."""
        base = {
            "inside_temperature": "22.0",  # intermediate — not summer, not winter
            "outside_temperature": "40.0",  # well above the extreme threshold
            "temp_extreme_heat": 35.0,
            "extreme_heat_position": 30,
            "is_presence": True,
            "is_sunny": True,
        }
        base.update(overrides)
        return _make_climate(**base)

    @pytest.mark.unit
    def test_holds_position_with_presence(self, vertical_cover_instance, mock_logger):
        """normal_with_presence holds extreme_heat_position, strategy EXTREME_HEAT."""
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            self._extreme(is_presence=True),
        )
        result = sh.normal_with_presence()
        assert result == 30
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_holds_position_without_presence(
        self, vertical_cover_instance, mock_logger
    ):
        """normal_without_presence also holds the extreme-heat position."""
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            self._extreme(is_presence=False),
        )
        result = sh.normal_without_presence()
        assert result == 30
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_fires_regardless_of_fov(self, vertical_cover_instance, mock_logger):
        """The hold fires even when the sun calc is invalid (cover out of FOV)."""
        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = False
            sh = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                self._extreme(),
            )
            result = sh.normal_with_presence()
        assert result == 30
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_overrides_winter_heating_on_cold_flagged_morning(
        self, vertical_cover_instance, mock_logger
    ):
        """A cold inside (winter) but a heatwave outside → extreme heat pre-empts winter."""
        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True
            sh = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                self._extreme(
                    inside_temperature="18.0",  # below temp_low (20) → winter
                    outside_temperature="40.0",  # heatwave → extreme heat
                    is_sunny=True,
                ),
            )
            result = sh.normal_with_presence()
        # Winter heating would open to 100; extreme heat wins and holds 30.
        assert result == 30
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_below_threshold_resumes_normal_rules(
        self, vertical_cover_instance, mock_logger
    ):
        """Outside temp at/below the threshold → feature inert → winter rule resumes."""
        with patch.object(
            type(vertical_cover_instance), "valid", new_callable=PropertyMock
        ) as mock_valid:
            mock_valid.return_value = True
            sh = ClimateCoverState(
                make_snapshot_for_cover(
                    vertical_cover_instance, vertical_cover_instance.config.h_def
                ),
                self._extreme(
                    inside_temperature="18.0",  # winter
                    outside_temperature="30.0",  # below extreme threshold (35)
                    is_sunny=False,
                ),
            )
            result = sh.normal_with_presence()
        # Not extreme → winter heating opens fully.
        assert result == 100
        assert sh.climate_strategy == ClimateStrategy.WINTER_HEATING

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "router,tilt",
        [
            ("normal_with_presence", False),
            ("normal_without_presence", False),
            ("tilt_with_presence", True),
            ("tilt_without_presence", True),
        ],
    )
    def test_extreme_heat_label_across_all_four_tables(
        self, router, tilt, vertical_cover_instance, tilt_cover_instance, mock_logger
    ):
        """Every one of the four routers claims EXTREME_HEAT when the flag is set."""
        cover = tilt_cover_instance if tilt else vertical_cover_instance
        policy = get_policy("cover_tilt") if tilt else get_policy("cover_blind")
        is_presence = "without" not in router
        sh = ClimateCoverState(
            make_snapshot_for_cover(cover, cover.config.h_def),
            self._extreme(policy=policy, is_presence=is_presence),
        )
        result = getattr(sh, router)()
        assert result == 30
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_default_position_when_unset_is_closed(
        self, vertical_cover_instance, mock_logger
    ):
        """extreme_heat_position unset (None) → holds POSITION_CLOSED (0)."""
        from custom_components.adaptive_cover_pro.const import POSITION_CLOSED

        sh = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            self._extreme(extreme_heat_position=None),
        )
        result = sh.normal_with_presence()
        assert result == POSITION_CLOSED
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT

    @pytest.mark.unit
    def test_explicit_zero_position_holds_zero(
        self, vertical_cover_instance, mock_logger
    ):
        """An explicit extreme_heat_position=0 is honored (not treated as unset)."""
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            self._extreme(extreme_heat_position=0),
        )
        result = sh.normal_with_presence()
        assert result == 0
        assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT
