"""Tests for the engine ``control_state_reason_code`` property (issue #882).

The engine emits a stable :class:`ReasonCode` for its control-state branches; the
legacy ``control_state_reason`` prose property is a one-line ``render_en`` shim over
that code. These tests lock:

* each geometry branch → the correct frozen ``ReasonCode``,
* ``render_en(Reason(code))`` is byte-identical to the legacy English literal,
* the prose property equals the rendered code (shim consistency),

on both ``SunGeometry`` (the pure geometry object) and ``AdaptiveGeneralCover``
(via ``AdaptiveVerticalCover``), the two engine sites that own the property.
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from custom_components.adaptive_cover_pro.calculation import AdaptiveVerticalCover
from custom_components.adaptive_cover_pro.config_types import CoverConfig
from custom_components.adaptive_cover_pro.const import ReasonCode
from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry
from custom_components.adaptive_cover_pro.reason_i18n import Reason, render_en
from tests.cover_helpers import build_vertical_cover

# Frozen code → legacy English literal that the engine emitted before #882.
ENGINE_CODE_LITERALS = {
    ReasonCode.ENGINE_DIRECT_SUN: "Direct Sun",
    ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET: "Default: Sunset Offset",
    ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT: "Default: Elevation Limit",
    ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT: "Default: Acceptance Angle Exit",
    ReasonCode.ENGINE_DEFAULT_BLIND_SPOT: "Default: Blind Spot",
    ReasonCode.ENGINE_DEFAULT: "Default",
}


class TestEngineReasonCodeRendersLegacyLiteral:
    """render_en(Reason(code)) must equal the exact pre-#882 prose."""

    def test_each_engine_code_renders_legacy_literal(self):
        for code, literal in ENGINE_CODE_LITERALS.items():
            assert render_en(Reason(code)) == literal


# ------------------------------------------------------------------
# SunGeometry.control_state_reason_code
# ------------------------------------------------------------------


def _make_config(**overrides) -> CoverConfig:
    defaults = {
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "h_def": 50,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_sun_only": False,
        "min_pos_sun_only": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
    }
    defaults.update(overrides)
    return CoverConfig(**defaults)


def _make_logger():
    logger = MagicMock()
    logger.debug = Mock()
    return logger


def _make_sun_data():
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return sun_data


def _sun_data_daytime():
    sun_data = _make_sun_data()
    sun_data.sunset.return_value = datetime(2024, 1, 1, 18, 0, 0)
    sun_data.sunrise.return_value = datetime(2024, 1, 1, 6, 0, 0)
    return sun_data


class TestSunGeometryReasonCode:
    """SunGeometry.control_state_reason_code returns the frozen code per branch."""

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_direct_sun(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sg = SunGeometry(
            180.0, 45.0, _sun_data_daytime(), _make_config(), _make_logger()
        )
        assert sg.control_state_reason_code == ReasonCode.ENGINE_DIRECT_SUN
        assert sg.control_state_reason == render_en(
            Reason(sg.control_state_reason_code)
        )

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_fov_exit(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        sg = SunGeometry(
            10.0, 45.0, _sun_data_daytime(), _make_config(), _make_logger()
        )
        assert (
            sg.control_state_reason_code
            == ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT
        )
        assert sg.control_state_reason == render_en(
            Reason(sg.control_state_reason_code)
        )

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_sunset_offset(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 1, 19, 0, 0)
        sg = SunGeometry(
            180.0, 45.0, _sun_data_daytime(), _make_config(), _make_logger()
        )
        assert sg.control_state_reason_code == ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET
        assert sg.control_state_reason == render_en(
            Reason(sg.control_state_reason_code)
        )

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_elevation_limit(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        config = _make_config(min_elevation=20)
        sg = SunGeometry(180.0, 5.0, _sun_data_daytime(), config, _make_logger())
        assert sg.control_state_reason_code == ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT
        assert sg.control_state_reason == render_en(
            Reason(sg.control_state_reason_code)
        )

    @patch("custom_components.adaptive_cover_pro.engine.sun_geometry.datetime")
    def test_blind_spot(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 1, 12, 0, 0)
        config = _make_config(
            blind_spot_left=35, blind_spot_right=-15, blind_spot_on=True
        )
        # gamma = 180 - 160 = 20, signed-gamma wedge [15, 35] (#247)
        sg = SunGeometry(160.0, 45.0, _sun_data_daytime(), config, _make_logger())
        assert sg.control_state_reason_code == ReasonCode.ENGINE_DEFAULT_BLIND_SPOT
        assert sg.control_state_reason == render_en(
            Reason(sg.control_state_reason_code)
        )


# ------------------------------------------------------------------
# AdaptiveGeneralCover.control_state_reason_code (via AdaptiveVerticalCover)
# ------------------------------------------------------------------


def _make_cover(mock_sun_data, mock_logger, **overrides) -> AdaptiveVerticalCover:
    defaults = {
        "logger": mock_logger,
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "sun_data": mock_sun_data,
        "fov_left": 45,
        "fov_right": 45,
        "win_azi": 180,
        "h_def": 50,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_bool": False,
        "min_pos_bool": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
        "distance": 0.5,
        "h_win": 2.0,
    }
    defaults.update(overrides)
    return build_vertical_cover(**defaults)


class TestCoverReasonCode:
    """AdaptiveGeneralCover.control_state_reason_code mirrors each branch."""

    def test_direct_sun(self, mock_sun_data, mock_logger):
        cover = _make_cover(mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=45.0)
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            assert cover.control_state_reason_code == ReasonCode.ENGINE_DIRECT_SUN
            assert cover.control_state_reason == render_en(
                Reason(cover.control_state_reason_code)
            )

    def test_fov_exit(self, mock_sun_data, mock_logger):
        cover = _make_cover(mock_sun_data, mock_logger, sol_azi=0.0, sol_elev=45.0)
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            assert (
                cover.control_state_reason_code
                == ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT
            )
            assert cover.control_state_reason == render_en(
                Reason(cover.control_state_reason_code)
            )

    def test_sunset_offset(self, mock_sun_data, mock_logger):
        cover = _make_cover(mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=45.0)
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            assert (
                cover.control_state_reason_code
                == ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET
            )
            assert cover.control_state_reason == render_en(
                Reason(cover.control_state_reason_code)
            )

    def test_elevation_limit(self, mock_sun_data, mock_logger):
        cover = _make_cover(
            mock_sun_data, mock_logger, sol_azi=180.0, sol_elev=5.0, min_elevation=10
        )
        with patch.object(
            type(cover), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            assert (
                cover.control_state_reason_code
                == ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT
            )
            assert cover.control_state_reason == render_en(
                Reason(cover.control_state_reason_code)
            )

    def test_blind_spot(self, mock_sun_data, mock_logger):
        cover = _make_cover(
            mock_sun_data,
            mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            blind_spot_left=10,
            blind_spot_right=30,
            blind_spot_on=True,
            fov_left=45,
        )
        with (
            patch.object(
                type(cover),
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                type(cover),
                "is_sun_in_blind_spot",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            assert (
                cover.control_state_reason_code == ReasonCode.ENGINE_DEFAULT_BLIND_SPOT
            )
            assert cover.control_state_reason == render_en(
                Reason(cover.control_state_reason_code)
            )
