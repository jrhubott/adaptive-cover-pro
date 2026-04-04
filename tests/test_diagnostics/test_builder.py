"""Tests for the DiagnosticsBuilder."""

from __future__ import annotations

from types import SimpleNamespace
import pytest

from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
)
from custom_components.adaptive_cover_pro.const import ControlStatus
from custom_components.adaptive_cover_pro.enums import ClimateStrategy, ControlMethod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cover(
    *,
    gamma: float = 10.0,
    valid: bool = True,
    valid_elevation: bool = True,
    is_sun_in_blind_spot: bool = False,
    direct_sun_valid: bool = True,
    sunset_valid: bool = False,
    sunset_pos: float | None = None,
    default: float = 0.0,
    control_state_reason: str = "Sun in FOV",
    calc_details: dict | None = None,
) -> SimpleNamespace:
    """Create a minimal cover mock."""
    cover = SimpleNamespace(
        gamma=gamma,
        valid=valid,
        valid_elevation=valid_elevation,
        is_sun_in_blind_spot=is_sun_in_blind_spot,
        direct_sun_valid=direct_sun_valid,
        sunset_valid=sunset_valid,
        sunset_pos=sunset_pos,
        default=default,
        control_state_reason=control_state_reason,
    )
    if calc_details is not None:
        cover._last_calc_details = calc_details
    return cover


def _make_normal_cover_state(cover=None) -> SimpleNamespace:
    """Wrap a cover mock in a NormalCoverState-like object."""
    if cover is None:
        cover = _make_cover()
    return SimpleNamespace(cover=cover)


def _base_ctx(**overrides) -> DiagnosticContext:
    """Return a DiagnosticContext with sensible defaults.

    All overrides are passed as keyword arguments.
    """
    defaults = {
        "pos_sun": [180.0, 45.0],
        "normal_cover_state": _make_normal_cover_state(),
        "raw_calculated_position": 50,
        "climate_state": None,
        "climate_data": None,
        "climate_strategy": None,
        "climate_mode": False,
        "control_method": ControlMethod.SOLAR,
        "pipeline_result": None,
        "is_force_override_active": False,
        "is_weather_override_active": False,
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
        "default_state": 0,
        "final_state": 50,
        "config_options": {},
        "motion_detected": True,
        "motion_timeout_active": False,
        "force_override_sensors": [],
        "force_override_position": 0,
    }
    defaults.update(overrides)
    return DiagnosticContext(**defaults)


@pytest.fixture
def builder() -> DiagnosticsBuilder:
    """Create a DiagnosticsBuilder instance."""
    return DiagnosticsBuilder()


# ---------------------------------------------------------------------------
# build() returns tuple
# ---------------------------------------------------------------------------


class TestBuildReturnType:
    """Verify build() returns (dict, str)."""

    def test_returns_tuple(self, builder: DiagnosticsBuilder):
        """Build returns a 2-tuple."""
        ctx = _base_ctx()
        result = builder.build(ctx)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_diagnostics_is_dict(self, builder: DiagnosticsBuilder):
        """First element is a dict."""
        diag, _ = builder.build(_base_ctx())
        assert isinstance(diag, dict)

    def test_explanation_is_str(self, builder: DiagnosticsBuilder):
        """Second element is a string."""
        _, explanation = builder.build(_base_ctx())
        assert isinstance(explanation, str)


# ---------------------------------------------------------------------------
# Solar diagnostics
# ---------------------------------------------------------------------------


class TestSolarDiagnostics:
    """Solar diagnostics section tests."""

    def test_sun_azimuth_and_elevation(self, builder: DiagnosticsBuilder):
        """Sun azimuth and elevation appear in output."""
        diag, _ = builder.build(_base_ctx(pos_sun=[200.5, 30.2]))
        assert diag["sun_azimuth"] == 200.5
        assert diag["sun_elevation"] == 30.2

    def test_gamma_present_when_cover_has_it(self, builder: DiagnosticsBuilder):
        """Gamma is included when cover state has the attribute."""
        diag, _ = builder.build(_base_ctx())
        assert "gamma" in diag

    def test_gamma_absent_when_no_cover_state(self, builder: DiagnosticsBuilder):
        """Gamma is absent when no cover state."""
        diag, _ = builder.build(_base_ctx(normal_cover_state=None))
        assert "gamma" not in diag


# ---------------------------------------------------------------------------
# Control status determination
# ---------------------------------------------------------------------------


class TestControlStatus:
    """Control status determination tests."""

    def test_automatic_control_off(self, builder: DiagnosticsBuilder):
        """Returns AUTOMATIC_CONTROL_OFF when automatic control is disabled."""
        diag, _ = builder.build(_base_ctx(automatic_control=False))
        assert diag["control_status"] == ControlStatus.AUTOMATIC_CONTROL_OFF

    def test_force_override_active(self, builder: DiagnosticsBuilder):
        """Returns FORCE_OVERRIDE_ACTIVE when force override is on."""
        diag, _ = builder.build(_base_ctx(is_force_override_active=True))
        assert diag["control_status"] == ControlStatus.FORCE_OVERRIDE_ACTIVE

    def test_motion_timeout(self, builder: DiagnosticsBuilder):
        """Returns MOTION_TIMEOUT when motion timeout is active."""
        diag, _ = builder.build(_base_ctx(is_motion_timeout_active=True))
        assert diag["control_status"] == ControlStatus.MOTION_TIMEOUT

    def test_manual_override_via_pipeline(self, builder: DiagnosticsBuilder):
        """Returns MANUAL_OVERRIDE when pipeline says manual."""
        pr = SimpleNamespace(control_method=ControlMethod.MANUAL)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.MANUAL_OVERRIDE

    def test_outside_time_window(self, builder: DiagnosticsBuilder):
        """Returns OUTSIDE_TIME_WINDOW when not in adaptive time."""
        diag, _ = builder.build(_base_ctx(check_adaptive_time=False))
        assert diag["control_status"] == ControlStatus.OUTSIDE_TIME_WINDOW

    def test_sun_not_visible(self, builder: DiagnosticsBuilder):
        """Returns SUN_NOT_VISIBLE when cover is not valid."""
        cover = _make_cover(valid=False)
        ncs = _make_normal_cover_state(cover)
        diag, _ = builder.build(_base_ctx(normal_cover_state=ncs))
        assert diag["control_status"] == ControlStatus.SUN_NOT_VISIBLE

    def test_active(self, builder: DiagnosticsBuilder):
        """Returns ACTIVE in normal conditions."""
        diag, _ = builder.build(_base_ctx())
        assert diag["control_status"] == ControlStatus.ACTIVE

    def test_priority_force_over_motion(self, builder: DiagnosticsBuilder):
        """Force override takes priority over motion timeout."""
        diag, _ = builder.build(
            _base_ctx(
                is_force_override_active=True,
                is_motion_timeout_active=True,
            )
        )
        assert diag["control_status"] == ControlStatus.FORCE_OVERRIDE_ACTIVE


# ---------------------------------------------------------------------------
# Control state reason
# ---------------------------------------------------------------------------


class TestControlStateReason:
    """Control state reason string tests."""

    def test_force_override(self, builder: DiagnosticsBuilder):
        """Force override reason string."""
        diag, _ = builder.build(_base_ctx(is_force_override_active=True))
        assert diag["control_state_reason"] == "Force Override"

    def test_motion_timeout(self, builder: DiagnosticsBuilder):
        """Motion timeout reason string."""
        diag, _ = builder.build(_base_ctx(is_motion_timeout_active=True))
        assert diag["control_state_reason"] == "Motion Timeout"

    def test_manual_override(self, builder: DiagnosticsBuilder):
        """Manual override reason string."""
        diag, _ = builder.build(_base_ctx(is_manual_override_active=True))
        assert diag["control_state_reason"] == "Manual Override"

    def test_cover_reason_passthrough(self, builder: DiagnosticsBuilder):
        """Cover-level reason passes through when no overrides active."""
        cover = _make_cover(control_state_reason="Sun below min elevation")
        ncs = _make_normal_cover_state(cover)
        diag, _ = builder.build(_base_ctx(normal_cover_state=ncs))
        assert diag["control_state_reason"] == "Sun below min elevation"

    def test_unknown_when_no_cover(self, builder: DiagnosticsBuilder):
        """Returns Unknown when no cover state available."""
        diag, _ = builder.build(_base_ctx(normal_cover_state=None))
        assert diag["control_state_reason"] == "Unknown"


# ---------------------------------------------------------------------------
# Position explanation
# ---------------------------------------------------------------------------


class TestPositionExplanation:
    """Position explanation string tests."""

    def test_force_override_explanation(self, builder: DiagnosticsBuilder):
        """Force override produces correct explanation."""
        _, explanation = builder.build(
            _base_ctx(
                is_force_override_active=True,
                force_override_position=75,
            )
        )
        assert "Force override active" in explanation
        assert "75%" in explanation

    def test_motion_timeout_explanation(self, builder: DiagnosticsBuilder):
        """Motion timeout produces correct explanation."""
        _, explanation = builder.build(
            _base_ctx(is_motion_timeout_active=True, default_state=30)
        )
        assert "No motion detected" in explanation
        assert "30%" in explanation

    def test_manual_override_explanation(self, builder: DiagnosticsBuilder):
        """Manual override produces correct explanation."""
        _, explanation = builder.build(_base_ctx(is_manual_override_active=True))
        assert "Manual override active" in explanation

    def test_outside_time_window_sunset_pos(self, builder: DiagnosticsBuilder):
        """Outside time window, past actual sunset, with sunset position configured."""
        from custom_components.adaptive_cover_pro.const import CONF_SUNSET_POS

        cover = _make_cover(sunset_valid=True, sunset_pos=20.0)
        ncs = _make_normal_cover_state(cover)
        _, explanation = builder.build(
            _base_ctx(
                check_adaptive_time=False,
                normal_cover_state=ncs,
                config_options={CONF_SUNSET_POS: 20},
            )
        )
        assert "Sunset Position" in explanation
        assert "20%" in explanation

    def test_outside_time_window_sunset_pos_before_sunset(self, builder: DiagnosticsBuilder):
        """Outside time window but before actual sunset → shows Default Position."""
        from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT, CONF_SUNSET_POS

        cover = _make_cover(sunset_valid=False)
        ncs = _make_normal_cover_state(cover)
        _, explanation = builder.build(
            _base_ctx(
                check_adaptive_time=False,
                normal_cover_state=ncs,
                config_options={CONF_SUNSET_POS: 20, CONF_DEFAULT_HEIGHT: 50},
            )
        )
        assert "Default Position" in explanation
        assert "50%" in explanation

    def test_outside_time_window_default(self, builder: DiagnosticsBuilder):
        """Outside time window with default height."""
        from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

        _, explanation = builder.build(
            _base_ctx(
                check_adaptive_time=False,
                config_options={CONF_DEFAULT_HEIGHT: 10},
            )
        )
        assert "Default Position" in explanation
        assert "10%" in explanation

    def test_sun_tracking_explanation(self, builder: DiagnosticsBuilder):
        """Sun tracking shows raw calculated position."""
        _, explanation = builder.build(_base_ctx(raw_calculated_position=65))
        assert "Sun tracking" in explanation
        assert "65%" in explanation

    def test_climate_mode_explanation(self, builder: DiagnosticsBuilder):
        """Climate mode shows strategy label and position."""
        _, explanation = builder.build(
            _base_ctx(
                switch_mode=True,
                climate_state=100,
                climate_strategy=ClimateStrategy.WINTER_HEATING,
            )
        )
        assert "Climate: Winter Heating" in explanation
        assert "100%" in explanation

    def test_inverse_state_explanation(self, builder: DiagnosticsBuilder):
        """Inverse state shows inversed label."""
        _, explanation = builder.build(_base_ctx(inverse_state=True, final_state=50))
        assert "inversed" in explanation

    def test_interpolation_explanation(self, builder: DiagnosticsBuilder):
        """Interpolation shows interpolated label."""
        _, explanation = builder.build(
            _base_ctx(use_interpolation=True, final_state=42)
        )
        assert "interpolated" in explanation
        assert "42%" in explanation

    def test_sunset_position_during_time_window(self, builder: DiagnosticsBuilder):
        """Sunset position within time window shows sunset label."""
        cover = _make_cover(
            direct_sun_valid=False,
            sunset_valid=True,
            sunset_pos=15.0,
        )
        ncs = _make_normal_cover_state(cover)
        _, explanation = builder.build(_base_ctx(normal_cover_state=ncs))
        assert "Sunset Position" in explanation
        assert "15%" in explanation


# ---------------------------------------------------------------------------
# Position diagnostics
# ---------------------------------------------------------------------------


class TestPositionDiagnostics:
    """Position diagnostics section tests."""

    def test_calculated_position(self, builder: DiagnosticsBuilder):
        """Calculated position appears in output."""
        diag, _ = builder.build(_base_ctx(raw_calculated_position=42))
        assert diag["calculated_position"] == 42

    def test_climate_position_present(self, builder: DiagnosticsBuilder):
        """Climate position appears when climate state is set."""
        diag, _ = builder.build(_base_ctx(climate_state=80))
        assert diag["calculated_position_climate"] == 80

    def test_climate_position_absent(self, builder: DiagnosticsBuilder):
        """Climate position absent when climate state is None."""
        diag, _ = builder.build(_base_ctx(climate_state=None))
        assert "calculated_position_climate" not in diag

    def test_delta_thresholds(self, builder: DiagnosticsBuilder):
        """Delta thresholds are included."""
        diag, _ = builder.build(_base_ctx(min_change=5, time_threshold=10))
        assert diag["delta_position_threshold"] == 5
        assert diag["delta_time_threshold_minutes"] == 10

    def test_position_delta_from_last_action(self, builder: DiagnosticsBuilder):
        """Position delta from last action is computed."""
        diag, _ = builder.build(
            _base_ctx(
                raw_calculated_position=60,
                last_cover_action={"position": 50},
            )
        )
        assert diag["position_delta_from_last_action"] == 10

    def test_last_updated_present(self, builder: DiagnosticsBuilder):
        """Last updated timestamp is present."""
        diag, _ = builder.build(_base_ctx())
        assert "last_updated" in diag

    def test_calculation_details_included(self, builder: DiagnosticsBuilder):
        """Calculation details from cover are included."""
        details = {"edge_case": True, "safety_margin": 1.1}
        cover = _make_cover(calc_details=details)
        ncs = _make_normal_cover_state(cover)
        diag, _ = builder.build(_base_ctx(normal_cover_state=ncs))
        assert diag["calculation_details"] == details


# ---------------------------------------------------------------------------
# Time window diagnostics
# ---------------------------------------------------------------------------


class TestTimeWindowDiagnostics:
    """Time window diagnostics section tests."""

    def test_time_window_keys(self, builder: DiagnosticsBuilder):
        """Time window keys are present."""
        diag, _ = builder.build(_base_ctx())
        tw = diag["time_window"]
        assert "check_adaptive_time" in tw
        assert "after_start_time" in tw
        assert "before_end_time" in tw
        assert "start_time" in tw
        assert "end_time" in tw


# ---------------------------------------------------------------------------
# Sun validity diagnostics
# ---------------------------------------------------------------------------


class TestSunValidityDiagnostics:
    """Sun validity diagnostics section tests."""

    def test_sun_validity_present(self, builder: DiagnosticsBuilder):
        """Sun validity fields are present when cover state exists."""
        diag, _ = builder.build(_base_ctx())
        sv = diag["sun_validity"]
        assert sv["valid"] is True
        assert sv["valid_elevation"] is True
        assert sv["in_blind_spot"] is False

    def test_sun_validity_absent_when_no_cover(self, builder: DiagnosticsBuilder):
        """Sun validity absent when no cover state."""
        diag, _ = builder.build(_base_ctx(normal_cover_state=None))
        assert "sun_validity" not in diag


# ---------------------------------------------------------------------------
# Climate diagnostics
# ---------------------------------------------------------------------------


class TestClimateDiagnostics:
    """Climate diagnostics section tests."""

    def _make_climate_data(self):
        return ClimateCoverData(
            temp_low=20.0,
            temp_high=25.0,
            temp_switch=True,
            blind_type="cover_blind",
            transparent_blind=False,
            temp_summer_outside=22.5,
            outside_temperature="22.5",
            inside_temperature="23.0",
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            winter_close_insulation=False,
        )

    def test_climate_data_present(self, builder: DiagnosticsBuilder):
        """Climate data fields appear when climate mode is enabled."""
        cd = self._make_climate_data()
        diag, _ = builder.build(
            _base_ctx(
                climate_mode=True,
                climate_data=cd,
                climate_strategy=ClimateStrategy.WINTER_HEATING,
                control_method=ControlMethod.WINTER,
            )
        )
        assert diag["active_temperature"] == 22.5
        assert diag["climate_strategy"] == "winter_heating"
        assert "temperature_details" in diag
        assert "climate_conditions" in diag

    def test_climate_data_absent_when_not_climate_mode(
        self, builder: DiagnosticsBuilder
    ):
        """Climate data absent when climate mode is off."""
        diag, _ = builder.build(_base_ctx(climate_mode=False))
        assert "active_temperature" not in diag
        assert "climate_strategy" not in diag


# ---------------------------------------------------------------------------
# Last action diagnostics
# ---------------------------------------------------------------------------


class TestLastActionDiagnostics:
    """Last action diagnostics section tests."""

    def test_last_cover_action_present(self, builder: DiagnosticsBuilder):
        """Last cover action appears when entity_id is set."""
        action = {"entity_id": "cover.test", "position": 50}
        diag, _ = builder.build(_base_ctx(last_cover_action=action))
        assert diag["last_cover_action"]["entity_id"] == "cover.test"

    def test_last_cover_action_absent(self, builder: DiagnosticsBuilder):
        """Last cover action absent when empty."""
        diag, _ = builder.build(_base_ctx(last_cover_action={}))
        assert "last_cover_action" not in diag

    def test_last_skipped_action_present(self, builder: DiagnosticsBuilder):
        """Last skipped action appears when entity_id is set."""
        action = {"entity_id": "cover.skip", "reason": "delta"}
        diag, _ = builder.build(_base_ctx(last_skipped_action=action))
        assert diag["last_skipped_action"]["entity_id"] == "cover.skip"


# ---------------------------------------------------------------------------
# Configuration diagnostics
# ---------------------------------------------------------------------------


class TestConfigurationDiagnostics:
    """Configuration diagnostics section tests."""

    def test_configuration_keys(self, builder: DiagnosticsBuilder):
        """All expected configuration keys are present."""
        diag, _ = builder.build(_base_ctx())
        config = diag["configuration"]
        expected_keys = {
            "azimuth",
            "fov_left",
            "fov_right",
            "min_elevation",
            "max_elevation",
            "enable_blind_spot",
            "blind_spot_elevation",
            "blind_spot_left",
            "blind_spot_right",
            "min_position",
            "max_position",
            "enable_min_position",
            "enable_max_position",
            "inverse_state",
            "interpolation",
            "force_override_sensors",
            "force_override_position",
            "force_override_active",
            "motion_sensors",
            "motion_timeout",
            "motion_detected",
            "motion_timeout_active",
        }
        assert expected_keys == set(config.keys())

    def test_configuration_reflects_context(self, builder: DiagnosticsBuilder):
        """Configuration reflects context state values."""
        diag, _ = builder.build(
            _base_ctx(
                is_force_override_active=True,
                motion_detected=False,
                motion_timeout_active=True,
            )
        )
        config = diag["configuration"]
        assert config["force_override_active"] is True
        assert config["motion_detected"] is False
        assert config["motion_timeout_active"] is True


# ---------------------------------------------------------------------------
# Full integration
# ---------------------------------------------------------------------------


class TestFullBuild:
    """Full build integration tests."""

    def test_all_sections_present(self, builder: DiagnosticsBuilder):
        """Verify that build() produces all expected top-level keys."""
        diag, explanation = builder.build(_base_ctx())
        assert "sun_azimuth" in diag
        assert "calculated_position" in diag
        assert "control_status" in diag
        assert "time_window" in diag
        assert "sun_validity" in diag
        assert "configuration" in diag
        assert "position_explanation" in diag
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_explanation_matches_diagnostics(self, builder: DiagnosticsBuilder):
        """The explanation returned as second element matches the one in dict."""
        diag, explanation = builder.build(_base_ctx())
        assert diag["position_explanation"] == explanation
