"""Tests ensuring no numpy scalar types leak into entity attributes or diagnostics.

Issue #149: HA 2026.4.1 (Python 3.14 + updated orjson) raises TypeError when
numpy.bool_, numpy.float64, or numpy.int64 values appear in entity state
attributes.  These tests guard against regressions in the calculation engine,
geometry utilities, and diagnostics builder.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.cover_helpers import build_vertical_cover


# ---------------------------------------------------------------------------
# Helper: verify an object and all nested values are native Python types
# ---------------------------------------------------------------------------


def _check_native_types(obj, path: str = "root") -> list[str]:
    """Recursively walk *obj* and return a list of paths with numpy scalar types.

    Returns an empty list if every leaf is a native Python type (or None).
    """
    violations: list[str] = []
    numpy_scalar = (np.generic,)  # covers bool_, int64, float64, etc.

    if isinstance(obj, numpy_scalar):
        violations.append(f"{path}: {type(obj).__module__}.{type(obj).__name__}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            violations.extend(_check_native_types(v, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            violations.extend(_check_native_types(v, f"{path}[{i}]"))
    return violations


def _assert_json_serialisable(obj, label: str) -> None:
    """Assert that *obj* can be JSON-serialised without errors."""
    try:
        json.dumps(obj)
    except TypeError as exc:
        pytest.fail(f"{label} is not JSON serialisable: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _sun_data():
    return MagicMock()


@pytest.fixture
def _logger():
    return MagicMock()


@pytest.fixture
def _base_params(_sun_data, _logger):
    return {
        "logger": _logger,
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": _sun_data,
        "win_azi": 180,
        "fov_left": 90,
        "fov_right": 90,
        "h_def": 50,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
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
        "h_win": 2.1,
    }


# ---------------------------------------------------------------------------
# geometry.SafetyMarginCalculator
# ---------------------------------------------------------------------------


class TestSafetyMarginCalculatorTypes:
    """SafetyMarginCalculator.calculate() must return a native Python float."""

    def test_returns_python_float_at_threshold(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

        result = SafetyMarginCalculator.calculate(gamma=45.0, sol_elev=30.0)
        assert isinstance(result, float), f"Expected float, got {type(result)}"
        assert not isinstance(result, np.floating), "Got numpy float, expected Python float"

    def test_returns_python_float_extreme_gamma(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

        result = SafetyMarginCalculator.calculate(gamma=80.0, sol_elev=10.0)
        assert isinstance(result, float)
        assert not isinstance(result, np.floating)

    def test_returns_python_float_low_elevation(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

        result = SafetyMarginCalculator.calculate(gamma=10.0, sol_elev=5.0)
        assert isinstance(result, float)
        assert not isinstance(result, np.floating)

    def test_returns_python_float_high_elevation(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

        result = SafetyMarginCalculator.calculate(gamma=10.0, sol_elev=80.0)
        assert isinstance(result, float)
        assert not isinstance(result, np.floating)

    def test_json_serialisable(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

        result = SafetyMarginCalculator.calculate(gamma=70.0, sol_elev=8.0)
        _assert_json_serialisable(result, "SafetyMarginCalculator.calculate()")


# ---------------------------------------------------------------------------
# geometry.EdgeCaseHandler
# ---------------------------------------------------------------------------


class TestEdgeCaseHandlerTypes:
    """EdgeCaseHandler.check_and_handle() must return native Python types."""

    def test_low_elevation_returns_python_types(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import EdgeCaseHandler

        is_edge, pos = EdgeCaseHandler.check_and_handle(
            sol_elev=1.0, gamma=10.0, distance=3.0, h_win=2.5
        )
        assert isinstance(is_edge, bool), f"is_edge should be bool, got {type(is_edge)}"
        assert isinstance(pos, float), f"pos should be float, got {type(pos)}"
        assert not isinstance(pos, np.floating), "pos should be Python float, not numpy"

    def test_extreme_gamma_returns_python_types(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import EdgeCaseHandler

        is_edge, pos = EdgeCaseHandler.check_and_handle(
            sol_elev=30.0, gamma=86.0, distance=3.0, h_win=2.5
        )
        assert isinstance(is_edge, bool)
        assert isinstance(pos, float)
        assert not isinstance(pos, np.floating)

    def test_high_elevation_returns_python_float(self) -> None:
        """The high-elevation branch uses np.clip — must be wrapped in float()."""
        from custom_components.adaptive_cover_pro.geometry import EdgeCaseHandler

        is_edge, pos = EdgeCaseHandler.check_and_handle(
            sol_elev=89.0, gamma=10.0, distance=3.0, h_win=2.5
        )
        assert is_edge is True
        assert isinstance(pos, float)
        assert not isinstance(pos, np.floating)

    def test_no_edge_case_returns_python_types(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import EdgeCaseHandler

        is_edge, pos = EdgeCaseHandler.check_and_handle(
            sol_elev=30.0, gamma=20.0, distance=3.0, h_win=2.5
        )
        assert isinstance(is_edge, bool)
        assert isinstance(pos, float)

    def test_json_serialisable(self) -> None:
        from custom_components.adaptive_cover_pro.geometry import EdgeCaseHandler

        is_edge, pos = EdgeCaseHandler.check_and_handle(89.0, 10.0, 3.0, 2.5)
        _assert_json_serialisable({"is_edge": is_edge, "pos": pos}, "EdgeCaseHandler result")


# ---------------------------------------------------------------------------
# SunGeometry validity properties
# ---------------------------------------------------------------------------


class TestSunGeometryTypes:
    """SunGeometry boolean properties must return native Python bools."""

    def _make_geometry(self, gamma=10.0, elev=30.0):
        from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry
        from custom_components.adaptive_cover_pro.config_types import CoverConfig

        config = MagicMock(spec=CoverConfig)
        config.win_azi = 180
        config.fov_left = 75
        config.fov_right = 75
        config.min_elevation = None
        config.max_elevation = None
        config.blind_spot_left = None
        config.blind_spot_right = None
        config.blind_spot_on = False
        config.blind_spot_elevation = None
        config.sunset_off = 0
        config.sunrise_off = 0

        sun_data = MagicMock()
        sun_data.sunset.return_value.replace.return_value = MagicMock()
        sun_data.sunrise.return_value.replace.return_value = MagicMock()

        sol_azi = (config.win_azi - gamma) % 360
        return SunGeometry(
            sol_azi=float(sol_azi),
            sol_elev=float(elev),
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )

    def test_valid_elevation_is_python_bool(self) -> None:
        geom = self._make_geometry()
        result = geom.valid_elevation
        assert isinstance(result, bool), f"Expected bool, got {type(result)}"
        assert not isinstance(result, np.bool_), "Got numpy.bool_, expected Python bool"

    def test_valid_is_python_bool(self) -> None:
        geom = self._make_geometry()
        result = geom.valid
        assert isinstance(result, bool), f"Expected bool, got {type(result)}"
        assert not isinstance(result, np.bool_), "Got numpy.bool_, expected Python bool"

    def test_valid_false_for_sun_outside_fov(self) -> None:
        """valid should be False (Python bool) when sun is outside FOV."""
        geom = self._make_geometry(gamma=80.0)  # beyond fov_right=75
        result = geom.valid
        assert result is False
        assert isinstance(result, bool)

    def test_is_sun_in_blind_spot_is_python_bool_when_disabled(self) -> None:
        geom = self._make_geometry()
        result = geom.is_sun_in_blind_spot
        assert isinstance(result, bool), f"Expected bool, got {type(result)}"

    def test_is_sun_in_blind_spot_is_python_bool_when_enabled(self) -> None:
        geom = self._make_geometry(gamma=10.0, elev=25.0)
        geom.config.blind_spot_on = True
        geom.config.blind_spot_left = 20
        geom.config.blind_spot_right = 5
        geom.config.blind_spot_elevation = None
        result = geom.is_sun_in_blind_spot
        assert isinstance(result, bool), f"Expected bool, got {type(result)}"
        assert not isinstance(result, np.bool_)

    def test_valid_json_serialisable(self) -> None:
        geom = self._make_geometry()
        _assert_json_serialisable(
            {"valid": geom.valid, "valid_elevation": geom.valid_elevation},
            "SunGeometry bools",
        )


# ---------------------------------------------------------------------------
# position_utils.interpolate_position
# ---------------------------------------------------------------------------


class TestInterpolatePositionTypes:
    """interpolate_position() must return a Python float (not numpy.float64)."""

    def test_returns_python_float_when_interpolated(self) -> None:
        from custom_components.adaptive_cover_pro.position_utils import interpolate_position

        result = interpolate_position(50.0, 10.0, 90.0, None, None)
        assert isinstance(result, (int, float)), f"Expected numeric, got {type(result)}"
        assert not isinstance(result, np.floating), "Got numpy float"

    def test_returns_original_when_no_range(self) -> None:
        from custom_components.adaptive_cover_pro.position_utils import interpolate_position

        result = interpolate_position(50.0, None, None, None, None)
        assert not isinstance(result, np.generic)

    def test_json_serialisable_interpolated(self) -> None:
        from custom_components.adaptive_cover_pro.position_utils import interpolate_position

        result = interpolate_position(50.0, 10.0, 90.0, None, None)
        _assert_json_serialisable(result, "interpolate_position()")


# ---------------------------------------------------------------------------
# Vertical cover _last_calc_details
# ---------------------------------------------------------------------------


class TestVerticalCoverCalcDetailsTypes:
    """_last_calc_details on AdaptiveVerticalCover must contain only native types."""

    def _cover(self, params, gamma: float, sol_elev: float):
        p = params.copy()
        p["sol_azi"] = (p["win_azi"] - gamma) % 360
        p["sol_elev"] = sol_elev
        return build_vertical_cover(**p)

    def test_normal_case_details_are_native_types(self, _base_params) -> None:
        cover = self._cover(_base_params, gamma=20.0, sol_elev=30.0)
        cover.calculate_position()
        details = cover._last_calc_details
        assert details is not None
        violations = _check_native_types(details)
        assert not violations, f"numpy types in _last_calc_details: {violations}"

    def test_edge_case_high_elevation_details_are_native_types(self, _base_params) -> None:
        """High-elevation edge case branch must produce native types in details."""
        cover = self._cover(_base_params, gamma=10.0, sol_elev=89.0)
        cover.calculate_position()
        details = cover._last_calc_details
        assert details is not None
        violations = _check_native_types(details)
        assert not violations, f"numpy types in _last_calc_details: {violations}"

    def test_extreme_gamma_details_are_native_types(self, _base_params) -> None:
        """Gamma-safety-margin branch (>45°) must produce native types."""
        cover = self._cover(_base_params, gamma=75.0, sol_elev=20.0)
        cover.calculate_position()
        details = cover._last_calc_details
        violations = _check_native_types(details)
        assert not violations, f"numpy types in _last_calc_details: {violations}"

    def test_low_elevation_details_are_native_types(self, _base_params) -> None:
        """Low-elevation edge case branch must produce native types."""
        cover = self._cover(_base_params, gamma=10.0, sol_elev=1.0)
        cover.calculate_position()
        details = cover._last_calc_details
        violations = _check_native_types(details)
        assert not violations, f"numpy types in _last_calc_details: {violations}"

    def test_calc_details_json_serialisable(self, _base_params) -> None:
        cover = self._cover(_base_params, gamma=20.0, sol_elev=30.0)
        cover.calculate_position()
        _assert_json_serialisable(cover._last_calc_details, "_last_calc_details")
