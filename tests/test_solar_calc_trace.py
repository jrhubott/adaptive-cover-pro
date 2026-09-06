"""Tests for the raw solar-calculation trace (`_last_calc_details`) — issue #682.

Each calc engine records a per-cycle raw geometric trace consumed by the new
`solar_calculation` diagnostic sensor and the diagnostics download. These tests
assert the stable key set per cover type, the guard-branch flags, and that the
trace is engine-shaped (not borrowed from a super call).
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from custom_components.adaptive_cover_pro.config_types import SlidingCurtainConfig
from custom_components.adaptive_cover_pro.const import (
    TRACE_KEY_GAMMA_DEG,
    TRACE_KEY_POSITION_PCT,
    TRACE_KEY_SOL_ELEV_DEG,
)
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveSlidingCurtainCover,
)
from tests.cover_helpers import make_cover_config, make_daytime_sun_data


def _check_native_types(obj, path: str = "root") -> list[str]:
    """Return paths whose leaves are numpy scalar types (must be empty)."""
    violations: list[str] = []
    if isinstance(obj, np.generic):
        violations.append(f"{path}: {type(obj).__module__}.{type(obj).__name__}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            violations.extend(_check_native_types(v, f"{path}.{k}"))
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            violations.extend(_check_native_types(v, f"{path}[{i}]"))
    return violations


# ---------------------------------------------------------------------------
# Vertical (cover_blind)
# ---------------------------------------------------------------------------


class TestVerticalTrace:
    """AdaptiveVerticalCover raw trace."""

    _NORMAL_KEYS = {
        TRACE_KEY_SOL_ELEV_DEG,
        TRACE_KEY_GAMMA_DEG,
        TRACE_KEY_POSITION_PCT,
        "edge_case_detected",
        "effective_distance_m",
        "effective_distance_source",
        "window_depth_contribution_m",
        "sill_height_offset_m",
        "safety_margin",
        "glare_zones_active",
        "cos_gamma",
        "cos_gamma_clamped",
        "path_length_m",
        "base_height_m",
        "adjusted_height_m",
        "clamped_to_window",
    }

    def test_normal_branch_keys(self, vertical_cover_instance):
        vertical_cover_instance.calculate_position()
        details = vertical_cover_instance._last_calc_details
        assert set(details) == self._NORMAL_KEYS
        assert details["edge_case_detected"] is False
        assert details["effective_distance_source"] == "base"
        assert details[TRACE_KEY_SOL_ELEV_DEG] == pytest.approx(45.0)
        assert details[TRACE_KEY_GAMMA_DEG] == pytest.approx(0.0)
        # position_pct is the raw vertical percentage (height / h_win * 100).
        assert details[TRACE_KEY_POSITION_PCT] == 25  # 0.5m / 2.0m
        # No safety margin on the vertical axis (#1173): the normal
        # fall-through path must report safety_margin=1.0 and
        # adjusted_height_m == base_height_m * safety_margin, matching the
        # invariant already pinned on the edge-case and lintel-gate branches.
        # Asserting the identity alone would be vacuously satisfiable by a
        # reintroduced margin applied to both sides, so pin the margin value
        # too (#1169-style audit precedent).
        assert details["safety_margin"] == 1.0
        assert details["adjusted_height_m"] == pytest.approx(
            details["base_height_m"] * details["safety_margin"]
        )

    def test_normal_branch_native_types(self, vertical_cover_instance):
        vertical_cover_instance.calculate_position()
        violations = _check_native_types(vertical_cover_instance._last_calc_details)
        assert not violations, violations

    def test_clamped_to_window_flag(self, vertical_cover_instance):
        """High sun clips height to h_win → clamped_to_window True."""
        vertical_cover_instance.sol_elev = 80.0
        vertical_cover_instance.calculate_position()
        details = vertical_cover_instance._last_calc_details
        assert details["clamped_to_window"] is True
        assert details[TRACE_KEY_POSITION_PCT] == 100

    def test_not_clamped_flag(self, vertical_cover_instance):
        vertical_cover_instance.calculate_position()
        assert vertical_cover_instance._last_calc_details["clamped_to_window"] is False

    def test_edge_case_branch(self, vertical_cover_instance):
        """Very-low-elevation edge case → edge_case_detected True + native types."""
        vertical_cover_instance.sol_elev = 1.0  # below EDGE_CASE_LOW_ELEVATION (2.0)
        vertical_cover_instance.calculate_position()
        details = vertical_cover_instance._last_calc_details
        assert details["edge_case_detected"] is True
        assert details["effective_distance_source"] == "edge_case"
        assert details["safety_margin"] == 1.0
        assert TRACE_KEY_SOL_ELEV_DEG in details
        assert TRACE_KEY_GAMMA_DEG in details
        assert TRACE_KEY_POSITION_PCT in details
        assert not _check_native_types(details)

    def test_glare_zone_source(self, vertical_cover_instance):
        vertical_cover_instance.calculate_position(effective_distance_override=1.0)
        details = vertical_cover_instance._last_calc_details
        assert details["effective_distance_source"] == "glare_zone"
        assert details["effective_distance_m"] == pytest.approx(1.0)

    def test_lintel_gate_branch(self, vertical_cover_instance):
        """Lintel gate return (#1169) must share the same key set as every
        other branch, and window_depth_contribution_m must carry the lintel
        shadow height (window_depth * tan(elev) / cos(gamma)) in metres.
        """
        vertical_cover_instance.sol_elev = 60.0  # gamma stays 0 (win_azi==sol_azi)
        vertical_cover_instance.vert_config.distance = 0.2
        vertical_cover_instance.vert_config.h_win = 0.62
        vertical_cover_instance.vert_config.window_depth = 0.18
        result = vertical_cover_instance.calculate_position()
        details = vertical_cover_instance._last_calc_details

        # Pins the branch this test means to exercise: the gate fired.
        assert result == pytest.approx(0.62)
        assert set(details) == self._NORMAL_KEYS
        expected_lintel_shadow = (
            0.18 * np.tan(np.radians(60.0)) / np.cos(np.radians(0.0))
        )
        assert details["window_depth_contribution_m"] == pytest.approx(
            expected_lintel_shadow, abs=1e-6
        )
        # The gate returns before the safety margin is applied, so it must
        # still uphold the adjusted = base * margin relation every other
        # branch does. Reverting that broke no test before this line existed.
        assert details["adjusted_height_m"] == pytest.approx(
            details["base_height_m"] * details["safety_margin"]
        )
        assert not _check_native_types(details)


# ---------------------------------------------------------------------------
# Tilt (cover_tilt)
# ---------------------------------------------------------------------------


class TestTiltTrace:
    """AdaptiveTiltCover raw trace."""

    _KEYS = {
        TRACE_KEY_SOL_ELEV_DEG,
        TRACE_KEY_GAMMA_DEG,
        TRACE_KEY_POSITION_PCT,
        "beta_rad",
        "discriminant",
        "negative_discriminant",
        "slat_angle_raw_deg",
        "nan_result",
        "max_degrees",
        "tilt_mode",
        "safety_margin",
        # Reflected-beam floor (#1282) — published on every branch so the key
        # set is stable, ``None`` where no slat angle was ever solved.
        "reflected_beam_elevation_deg",
        "reflected_beam_min_elevation_deg",
        "reflected_beam_constrained",
    }

    def test_normal_branch_keys(self, tilt_cover_instance):
        # depth > slat_distance keeps the discriminant positive (normal path).
        tilt_cover_instance.depth = 0.05
        tilt_cover_instance.calculate_position()
        details = tilt_cover_instance._last_calc_details
        assert set(details) == self._KEYS
        assert details["negative_discriminant"] is False
        assert details["nan_result"] is False
        assert details["slat_angle_raw_deg"] is not None
        assert details["max_degrees"] == 90  # mode1
        assert details["tilt_mode"] == "mode1"
        assert details[TRACE_KEY_SOL_ELEV_DEG] == pytest.approx(45.0)

    def test_normal_branch_native_types(self, tilt_cover_instance):
        tilt_cover_instance.depth = 0.05
        tilt_cover_instance.calculate_position()
        assert not _check_native_types(tilt_cover_instance._last_calc_details)

    def test_negative_discriminant_branch(self, tilt_cover_instance):
        """slat_distance >> depth makes discriminant negative → guard returns 0°."""
        tilt_cover_instance.slat_distance = 1.0
        tilt_cover_instance.depth = 0.01
        result = tilt_cover_instance.calculate_position()
        details = tilt_cover_instance._last_calc_details
        assert result == 0.0
        assert details["negative_discriminant"] is True
        assert details["discriminant"] < 0
        assert details["slat_angle_raw_deg"] is None
        assert details[TRACE_KEY_POSITION_PCT] == 0
        assert not _check_native_types(details)

    def test_nan_result_branch(self, tilt_cover_instance, monkeypatch):
        """A NaN slat result trips the nan guard → nan_result True, returns 0°."""
        import custom_components.adaptive_cover_pro.engine.covers.tilt as tilt_mod

        # Positive-discriminant geometry so we reach the rad2deg/NaN guard.
        tilt_cover_instance.depth = 0.05
        monkeypatch.setattr(tilt_mod.np, "rad2deg", lambda _x: np.float64("nan"))
        result = tilt_cover_instance.calculate_position()
        details = tilt_cover_instance._last_calc_details
        assert result == 0.0
        assert details["nan_result"] is True
        assert details["negative_discriminant"] is False
        assert not _check_native_types(details)

    def test_mode2_max_degrees(self, tilt_cover_instance):
        tilt_cover_instance.tilt_config.mode = "mode2"
        tilt_cover_instance.calculate_position()
        details = tilt_cover_instance._last_calc_details
        assert details["max_degrees"] == 180
        assert details["tilt_mode"] == "mode2"


# ---------------------------------------------------------------------------
# Horizontal (cover_awning) — incl. latent-overwrite regression
# ---------------------------------------------------------------------------


class TestHorizontalTrace:
    """AdaptiveHorizontalCover raw trace and the super-call overwrite regression."""

    _KEYS = {
        TRACE_KEY_SOL_ELEV_DEG,
        TRACE_KEY_GAMMA_DEG,
        TRACE_KEY_POSITION_PCT,
        "awn_angle_deg",
        "shade_mode",
        "cos_gamma",
        "profile_angle_deg",
        "denom",
        "length_m",
        "clamped_to_awn_length",
    }

    def test_trace_is_horizontal_not_vertical(self, horizontal_cover_instance):
        """The horizontal trace shape must win — horizontal keys, not vertical
        ones like effective_distance_m (issue #682 / #1025 rewrite).
        """
        horizontal_cover_instance.calculate_position()
        details = horizontal_cover_instance._last_calc_details
        assert set(details) == self._KEYS
        # The vertical shape must not leak through.
        assert "effective_distance_m" not in details
        assert "awn_angle_deg" in details

    def test_normal_branch_values(self, horizontal_cover_instance):
        # gamma=0, elev=45, awn_angle=0, h_win=2, distance=0.5 → L = 2·1/1 − 0.5.
        horizontal_cover_instance.calculate_position()
        details = horizontal_cover_instance._last_calc_details
        assert details["shade_mode"] == "window"
        assert details[TRACE_KEY_SOL_ELEV_DEG] == pytest.approx(45.0)
        assert details["cos_gamma"] == pytest.approx(1.0)
        assert details["profile_angle_deg"] == pytest.approx(45.0)  # atan(tan45/1)
        assert details["length_m"] == pytest.approx(1.5)

    def test_native_types(self, horizontal_cover_instance):
        horizontal_cover_instance.calculate_position()
        assert not _check_native_types(horizontal_cover_instance._last_calc_details)

    def test_retracts_below_horizon(self, horizontal_cover_instance):
        """sol_elev ≤ 0 → no direct sun → overhang retracts (length 0)."""
        horizontal_cover_instance.sol_elev = 0.0
        result = horizontal_cover_instance.calculate_position()
        details = horizontal_cover_instance._last_calc_details
        assert result == 0.0
        assert details["length_m"] == pytest.approx(0.0)
        assert details["denom"] == pytest.approx(0.0)
        assert not _check_native_types(details)

    def test_area_mode_trace(self, horizontal_cover_instance):
        """Area mode reports full extension in the trace regardless of gamma."""
        horizontal_cover_instance.horiz_config.shade_mode = "area"
        result = horizontal_cover_instance.calculate_position()
        details = horizontal_cover_instance._last_calc_details
        assert details["shade_mode"] == "area"
        assert result == pytest.approx(horizontal_cover_instance.awn_length)
        assert details["length_m"] == pytest.approx(
            horizontal_cover_instance.awn_length
        )
        assert not _check_native_types(details)

    def test_clamped_to_awn_length_flag(self, horizontal_cover_instance):
        """A short awning forces the geometric length over the limit → clamp flag."""
        horizontal_cover_instance.horiz_config.awn_length = 0.01
        horizontal_cover_instance.sol_elev = 20.0
        horizontal_cover_instance.calculate_position()
        details = horizontal_cover_instance._last_calc_details
        assert details["clamped_to_awn_length"] is True


# ---------------------------------------------------------------------------
# Sliding curtain (cover_sliding_curtain)
# ---------------------------------------------------------------------------


class TestSlidingCurtainTrace:
    """AdaptiveSlidingCurtainCover raw trace."""

    _KEYS = {
        TRACE_KEY_SOL_ELEV_DEG,
        TRACE_KEY_GAMMA_DEG,
        TRACE_KEY_POSITION_PCT,
        "calculation_mode",
        "direct_sun_valid",
        "slide_direction",
        "window_width_m",
        "shade_point1_x_m",
        "shade_point1_y_m",
        "shade_point2_x_m",
        "shade_point2_y_m",
        "projected_point1_x_m",
        "projected_point2_x_m",
        "raw_interval_start_m",
        "raw_interval_end_m",
        "covered_interval_start_m",
        "covered_interval_end_m",
        "covered_width_m",
        "interval_status",
        "position_basis_m",
        "unclamped_position_pct",
    }

    def _curtain(
        self, sc_config: SlidingCurtainConfig | None
    ) -> AdaptiveSlidingCurtainCover:
        return AdaptiveSlidingCurtainCover(
            logger=MagicMock(),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=make_daytime_sun_data(),
            config=make_cover_config(win_azi=180, fov_left=90, fov_right=90),
            sc_config=sc_config,
        )

    def test_binary_trace_has_numeric_position(self):
        curtain = self._curtain(None)
        result = curtain.calculate_percentage()
        details = curtain._last_calc_details

        assert result == 0
        assert set(details) == self._KEYS
        assert details[TRACE_KEY_POSITION_PCT] == 0.0
        assert details["calculation_mode"] == "binary"
        assert details["direct_sun_valid"] is True
        assert details["interval_status"] == "not_applicable"
        assert not _check_native_types(details)

    def test_shade_area_trace_exposes_interval_and_percentage_basis(self):
        curtain = self._curtain(
            SlidingCurtainConfig(
                enabled=True,
                slide_direction="bi_part",
                window_width=2.0,
                point1_x=0.4,
                point1_y=3.0,
                point2_x=0.8,
                point2_y=3.0,
            )
        )
        result = curtain.calculate_percentage()
        details = curtain._last_calc_details

        assert result == pytest.approx(40.0)
        assert set(details) == self._KEYS
        assert details["calculation_mode"] == "shade_area"
        assert details["interval_status"] == "covered"
        assert details["projected_point1_x_m"] == pytest.approx(0.4)
        assert details["projected_point2_x_m"] == pytest.approx(0.8)
        assert details["covered_interval_start_m"] == pytest.approx(0.4)
        assert details["covered_interval_end_m"] == pytest.approx(0.8)
        assert details["covered_width_m"] == pytest.approx(0.4)
        assert details["position_basis_m"] == pytest.approx(0.8)
        assert details["unclamped_position_pct"] == pytest.approx(40.0)
        assert details[TRACE_KEY_POSITION_PCT] == pytest.approx(40.0)
        assert not _check_native_types(details)

    def test_off_window_trace_explains_open_result(self):
        curtain = self._curtain(
            SlidingCurtainConfig(
                enabled=True,
                slide_direction="left",
                window_width=2.0,
                point1_x=3.0,
                point1_y=3.0,
                point2_x=4.0,
                point2_y=3.0,
            )
        )
        result = curtain.calculate_position()
        details = curtain._last_calc_details

        assert result == 100
        assert set(details) == self._KEYS
        assert details["interval_status"] == "outside_window"
        assert details["raw_interval_start_m"] == pytest.approx(3.0)
        assert details["covered_interval_start_m"] is None
        assert details[TRACE_KEY_POSITION_PCT] == 100.0
        assert not _check_native_types(details)


# ---------------------------------------------------------------------------
# solar_calculation diagnostic sensor (issue #682)
# ---------------------------------------------------------------------------


class TestSolarCalculationSensorSpec:
    """The new solar_calculation diagnostic sensor spec wiring."""

    def _spec(self):
        from custom_components.adaptive_cover_pro.sensor import _DIAGNOSTIC_SPECS

        for spec in _DIAGNOSTIC_SPECS:
            if spec.suffix == "solar_calculation":
                return spec
        raise AssertionError("solar_calculation spec not registered")

    def _stub_sensor(self, calc_details):
        from types import SimpleNamespace

        return SimpleNamespace(
            data=SimpleNamespace(
                diagnostics=(
                    {"calculation_details": calc_details}
                    if calc_details is not None
                    else {}
                )
            )
        )

    def test_spec_registered(self):
        spec = self._spec()
        assert spec.translation_key == "solar_calculation"
        assert spec.unit == "%"
        assert spec.suggested_display_precision == 0

    def test_state_class_measurement(self):
        from homeassistant.components.sensor import SensorStateClass

        assert self._spec().state_class == SensorStateClass.MEASUREMENT

    def test_value_is_raw_position_pct(self):
        spec = self._spec()
        s = self._stub_sensor({"cover_type": "cover_blind", "position_pct": 42})
        assert spec.value_fn(s) == 42

    def test_value_none_when_no_calc_details(self):
        spec = self._spec()
        s = self._stub_sensor(None)
        assert spec.value_fn(s) is None

    def test_value_for_venetian_uses_top_level_position(self):
        """Venetian: state is the lift/position axis (top-level position_pct)."""
        spec = self._spec()
        s = self._stub_sensor(
            {
                "cover_type": "cover_venetian",
                "position_pct": 30,
                "tilt": {"position_pct": 70},
            }
        )
        assert spec.value_fn(s) == 30

    def test_attrs_are_full_trace(self):
        spec = self._spec()
        trace = {"cover_type": "cover_blind", "position_pct": 42, "gamma_deg": 5.0}
        s = self._stub_sensor(trace)
        assert spec.attrs_fn(s) == trace

    def test_attrs_none_when_no_calc_details(self):
        spec = self._spec()
        s = self._stub_sensor(None)
        assert spec.attrs_fn(s) is None

    def test_unrecorded_attributes_match_all(self):
        from homeassistant.const import MATCH_ALL

        assert MATCH_ALL in self._spec().unrecorded_attributes

    def test_resolved_class_has_match_all(self):
        from homeassistant.const import MATCH_ALL

        from custom_components.adaptive_cover_pro.sensor import _DIAGNOSTIC_CLASSES

        cls = _DIAGNOSTIC_CLASSES["solar_calculation"]
        assert MATCH_ALL in cls._unrecorded_attributes

    def test_is_diagnostic_and_enabled_by_default(self):
        from types import SimpleNamespace

        spec = self._spec()
        assert spec.diagnostic is True
        # enabled_when defaults to always-True (no per-type gating).
        entry = SimpleNamespace(options={}, data={})
        assert spec.enabled_when(entry) is True
