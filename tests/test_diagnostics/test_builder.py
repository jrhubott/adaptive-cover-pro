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
import datetime as dt
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    ControlStatus,
)
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
        control_state_reason=control_state_reason,
    )
    if calc_details is not None:
        cover._last_calc_details = calc_details
    return cover


def _make_pr(
    *,
    position: int = 50,
    control_method: ControlMethod = ControlMethod.SOLAR,
    reason: str = "sun in FOV — position 50%",
    raw_calculated_position: int = 50,
    climate_state: int | None = None,
    climate_strategy: ClimateStrategy | None = None,
    climate_data=None,
    default_position: int = 0,
    is_sunset_active: bool = False,
    configured_default: int = 0,
    configured_sunset_pos: int | None = None,
    configured_cloudy_pos: int | None = None,
    bypass_auto_control: bool = False,
) -> PipelineResult:
    """Build a PipelineResult with sensible defaults."""
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason=reason,
        raw_calculated_position=raw_calculated_position,
        climate_state=climate_state,
        climate_strategy=climate_strategy,
        climate_data=climate_data,
        default_position=default_position,
        is_sunset_active=is_sunset_active,
        configured_default=configured_default,
        configured_sunset_pos=configured_sunset_pos,
        configured_cloudy_pos=configured_cloudy_pos,
        bypass_auto_control=bypass_auto_control,
    )


def _base_ctx(**overrides) -> DiagnosticContext:
    """Return a DiagnosticContext with sensible defaults."""
    defaults = {
        "pos_sun": [180.0, 45.0],
        "cover": _make_cover(),
        "pipeline_result": _make_pr(),
        "climate_mode": False,
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

    def test_sun_values_rounded_to_one_decimal(self, builder: DiagnosticsBuilder):
        """Raw float sun values are rounded to 1 decimal place."""
        diag, _ = builder.build(_base_ctx(pos_sun=[200.5678, 30.2341]))
        assert diag["sun_azimuth"] == 200.6
        assert diag["sun_elevation"] == 30.2

    def test_sun_azimuth_none_handling(self, builder: DiagnosticsBuilder):
        """None azimuth is preserved as None (not raised as error)."""
        diag, _ = builder.build(_base_ctx(pos_sun=[None, 30.2]))
        assert diag["sun_azimuth"] is None

    def test_sun_elevation_none_handling(self, builder: DiagnosticsBuilder):
        """None elevation is preserved as None."""
        diag, _ = builder.build(_base_ctx(pos_sun=[180.0, None]))
        assert diag["sun_elevation"] is None

    def test_gamma_present_when_cover_has_it(self, builder: DiagnosticsBuilder):
        """Gamma is included when cover has the attribute."""
        diag, _ = builder.build(_base_ctx())
        assert "gamma" in diag

    def test_gamma_rounded_to_one_decimal(self, builder: DiagnosticsBuilder):
        """Gamma is rounded to 1 decimal place."""
        diag, _ = builder.build(_base_ctx(cover=_make_cover(gamma=12.3456)))
        assert diag["gamma"] == 12.3

    def test_gamma_absent_when_no_cover(self, builder: DiagnosticsBuilder):
        """Gamma is absent when cover is None."""
        diag, _ = builder.build(_base_ctx(cover=None))
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
        """Returns FORCE_OVERRIDE_ACTIVE when force override is the winning method."""
        pr = _make_pr(control_method=ControlMethod.FORCE)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.FORCE_OVERRIDE_ACTIVE

    def test_motion_timeout(self, builder: DiagnosticsBuilder):
        """Returns MOTION_TIMEOUT when motion timeout is the winning method."""
        pr = _make_pr(control_method=ControlMethod.MOTION)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.MOTION_TIMEOUT

    def test_manual_override_via_pipeline(self, builder: DiagnosticsBuilder):
        """Returns MANUAL_OVERRIDE when pipeline says manual."""
        pr = _make_pr(control_method=ControlMethod.MANUAL)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.MANUAL_OVERRIDE

    def test_outside_time_window(self, builder: DiagnosticsBuilder):
        """Returns OUTSIDE_TIME_WINDOW when not in adaptive time."""
        diag, _ = builder.build(_base_ctx(check_adaptive_time=False))
        assert diag["control_status"] == ControlStatus.OUTSIDE_TIME_WINDOW

    def test_sun_not_visible(self, builder: DiagnosticsBuilder):
        """Returns SUN_NOT_VISIBLE when cover is not valid."""
        cover = _make_cover(valid=False)
        pr = _make_pr(control_method=ControlMethod.DEFAULT)
        diag, _ = builder.build(_base_ctx(cover=cover, pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.SUN_NOT_VISIBLE

    def test_active(self, builder: DiagnosticsBuilder):
        """Returns ACTIVE in normal conditions."""
        diag, _ = builder.build(_base_ctx())
        assert diag["control_status"] == ControlStatus.ACTIVE

    def test_priority_force_over_motion(self, builder: DiagnosticsBuilder):
        """Force override takes priority over motion timeout (higher priority handler wins)."""
        pr = _make_pr(control_method=ControlMethod.FORCE)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.FORCE_OVERRIDE_ACTIVE


# ---------------------------------------------------------------------------
# Control state reason
# ---------------------------------------------------------------------------


class TestControlStateReason:
    """Control state reason string tests."""

    def test_force_override(self, builder: DiagnosticsBuilder):
        """Force override reason string."""
        pr = _make_pr(control_method=ControlMethod.FORCE)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_state_reason"] == "Force Override"

    def test_motion_timeout(self, builder: DiagnosticsBuilder):
        """Motion timeout reason string."""
        pr = _make_pr(control_method=ControlMethod.MOTION)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_state_reason"] == "Motion Timeout"

    def test_manual_override(self, builder: DiagnosticsBuilder):
        """Manual override reason string."""
        pr = _make_pr(control_method=ControlMethod.MANUAL)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_state_reason"] == "Manual Override"

    def test_cover_reason_passthrough(self, builder: DiagnosticsBuilder):
        """Cover-level reason passes through when no overrides active."""
        cover = _make_cover(control_state_reason="Sun below min elevation")
        diag, _ = builder.build(_base_ctx(cover=cover))
        assert diag["control_state_reason"] == "Sun below min elevation"

    def test_unknown_when_no_cover(self, builder: DiagnosticsBuilder):
        """Returns Unknown when no cover available."""
        diag, _ = builder.build(_base_ctx(cover=None))
        assert diag["control_state_reason"] == "Unknown"


# ---------------------------------------------------------------------------
# Position explanation
# ---------------------------------------------------------------------------


class TestPositionExplanation:
    """Position explanation string tests."""

    def test_force_override_explanation(self, builder: DiagnosticsBuilder):
        """Force override produces correct explanation."""
        pr = _make_pr(
            control_method=ControlMethod.FORCE,
            reason="force override active (sensor.x) — position 75% [bypasses automatic control]",
            position=75,
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "force override" in explanation.lower()
        assert "75%" in explanation

    def test_motion_timeout_explanation(self, builder: DiagnosticsBuilder):
        """Motion timeout produces correct explanation."""
        pr = _make_pr(
            control_method=ControlMethod.MOTION,
            reason="motion timeout active — default position 30%",
            position=30,
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "motion" in explanation.lower()
        assert "30%" in explanation

    def test_manual_override_explanation(self, builder: DiagnosticsBuilder):
        """Manual override produces correct explanation."""
        pr = _make_pr(
            control_method=ControlMethod.MANUAL,
            reason="manual override active — holding solar position 50%",
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "manual" in explanation.lower()

    def test_outside_time_window_sunset_pos(self, builder: DiagnosticsBuilder):
        """Outside time window with sunset_pos active → shows 'sunset position'."""
        pr = _make_pr(
            default_position=20, is_sunset_active=True, configured_sunset_pos=20
        )
        _, explanation = builder.build(
            _base_ctx(pipeline_result=pr, check_adaptive_time=False)
        )
        assert "sunset position" in explanation.lower()
        assert "20%" in explanation
        assert "commands paused" in explanation

    def test_outside_time_window_default(self, builder: DiagnosticsBuilder):
        """Outside time window with no sunset_pos → shows 'default position'."""
        pr = _make_pr(default_position=10, is_sunset_active=False)
        _, explanation = builder.build(
            _base_ctx(pipeline_result=pr, check_adaptive_time=False)
        )
        assert "default position" in explanation.lower()
        assert "10%" in explanation
        assert "commands paused" in explanation

    def test_sun_tracking_explanation(self, builder: DiagnosticsBuilder):
        """Sun tracking reason propagates through."""
        pr = _make_pr(reason="sun in FOV — position 65%", raw_calculated_position=65)
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "65%" in explanation

    def test_climate_mode_explanation(self, builder: DiagnosticsBuilder):
        """Climate mode reason propagates through."""
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            reason="climate mode active (winter) — position 100%",
            climate_state=100,
            climate_strategy=ClimateStrategy.WINTER_HEATING,
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr, switch_mode=True))
        assert "climate" in explanation.lower()
        assert "100%" in explanation

    def test_inverse_state_explanation(self, builder: DiagnosticsBuilder):
        """Inverse state appends inversed label when value changed."""
        pr = _make_pr(position=72)
        _, explanation = builder.build(
            _base_ctx(pipeline_result=pr, inverse_state=True, final_state=28)
        )
        assert "invers" in explanation.lower()
        assert "28%" in explanation

    def test_interpolation_explanation(self, builder: DiagnosticsBuilder):
        """Interpolation appends interpolated label."""
        pr = _make_pr(position=72)
        _, explanation = builder.build(
            _base_ctx(pipeline_result=pr, use_interpolation=True, final_state=42)
        )
        assert "interpolated" in explanation
        assert "42%" in explanation

    def test_no_result_returns_unknown(self, builder: DiagnosticsBuilder):
        """Returns 'Unknown' when pipeline_result is None."""
        _, explanation = builder.build(_base_ctx(pipeline_result=None))
        assert explanation == "Unknown"


# ---------------------------------------------------------------------------
# Position diagnostics
# ---------------------------------------------------------------------------


class TestPositionDiagnostics:
    """Position diagnostics section tests."""

    def test_calculated_position(self, builder: DiagnosticsBuilder):
        """Calculated position appears in output from pipeline result."""
        pr = _make_pr(raw_calculated_position=42)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["calculated_position"] == 42

    def test_climate_position_present(self, builder: DiagnosticsBuilder):
        """Climate position appears when pipeline result has climate state."""
        pr = _make_pr(climate_state=80)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["calculated_position_climate"] == 80

    def test_climate_position_absent(self, builder: DiagnosticsBuilder):
        """Climate position absent when climate state is None."""
        pr = _make_pr(climate_state=None)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert "calculated_position_climate" not in diag

    def test_delta_thresholds(self, builder: DiagnosticsBuilder):
        """Delta thresholds are included."""
        diag, _ = builder.build(_base_ctx(min_change=5, time_threshold=10))
        assert diag["delta_position_threshold"] == 5
        assert diag["delta_time_threshold_minutes"] == 10

    def test_position_delta_from_last_action(self, builder: DiagnosticsBuilder):
        """Position delta from last action is computed."""
        pr = _make_pr(raw_calculated_position=60)
        diag, _ = builder.build(
            _base_ctx(
                pipeline_result=pr,
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
        diag, _ = builder.build(_base_ctx(cover=cover))
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
        """Sun validity fields are present when cover exists."""
        diag, _ = builder.build(_base_ctx())
        sv = diag["sun_validity"]
        assert sv["valid"] is True
        assert sv["valid_elevation"] is True
        assert sv["in_blind_spot"] is False

    def test_sun_validity_absent_when_no_cover(self, builder: DiagnosticsBuilder):
        """Sun validity absent when no cover."""
        diag, _ = builder.build(_base_ctx(cover=None))
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
        """Climate data fields appear when climate mode is enabled and result has climate_data."""
        cd = self._make_climate_data()
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            climate_state=100,
            climate_strategy=ClimateStrategy.WINTER_HEATING,
            climate_data=cd,
        )
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        assert diag["active_temperature"] == 22.5
        assert diag["climate_strategy"] == "winter_heating"
        assert "temperature_details" in diag
        assert "climate_conditions" in diag

    def test_active_temperature_rounded_to_one_decimal(
        self, builder: DiagnosticsBuilder
    ):
        """active_temperature is rounded to 1 decimal place."""
        cd = ClimateCoverData(
            temp_low=20.0,
            temp_high=25.0,
            temp_switch=True,
            blind_type="cover_blind",
            transparent_blind=False,
            temp_summer_outside=22.5,
            outside_temperature="22.567",  # string from HA entity
            inside_temperature="23.0",
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            winter_close_insulation=False,
        )
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            climate_data=cd,
        )
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        assert diag["active_temperature"] == 22.6  # 22.567 rounded to 1 dp

    def test_temperature_details_rounded_to_one_decimal(
        self, builder: DiagnosticsBuilder
    ):
        """inside/outside temperatures in temperature_details are rounded to 1 decimal."""
        cd = ClimateCoverData(
            temp_low=20.0,
            temp_high=25.0,
            temp_switch=False,
            blind_type="cover_blind",
            transparent_blind=False,
            temp_summer_outside=22.5,
            outside_temperature="19.8765",
            inside_temperature="21.2341",
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            winter_close_insulation=False,
        )
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            climate_data=cd,
        )
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        details = diag["temperature_details"]
        assert details["inside_temperature"] == 21.2
        assert details["outside_temperature"] == 19.9

    def test_temperature_details_none_preserved(self, builder: DiagnosticsBuilder):
        """None temperatures pass through without error."""
        cd = ClimateCoverData(
            temp_low=20.0,
            temp_high=25.0,
            temp_switch=False,
            blind_type="cover_blind",
            transparent_blind=False,
            temp_summer_outside=22.5,
            outside_temperature=None,
            inside_temperature=None,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            winter_close_insulation=False,
        )
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            climate_data=cd,
        )
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        details = diag["temperature_details"]
        assert details["inside_temperature"] is None
        assert details["outside_temperature"] is None

    def test_climate_data_absent_when_not_climate_mode(
        self, builder: DiagnosticsBuilder
    ):
        """Climate data absent when climate mode is off."""
        diag, _ = builder.build(_base_ctx(climate_mode=False))
        assert "active_temperature" not in diag
        assert "climate_strategy" not in diag

    def test_climate_conditions_includes_cloud_coverage_above_threshold(
        self, builder: DiagnosticsBuilder
    ):
        """climate_conditions must include cloud_coverage_above_threshold (Issue #222)."""
        cd = self._make_climate_data()
        pr = _make_pr(
            control_method=ControlMethod.WINTER,
            climate_data=cd,
        )
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        conditions = diag["climate_conditions"]
        assert "cloud_coverage_above_threshold" in conditions
        assert conditions["cloud_coverage_above_threshold"] is False

    def test_climate_conditions_cloud_coverage_true_when_active(
        self, builder: DiagnosticsBuilder
    ):
        """cloud_coverage_above_threshold is True when the flag is set."""
        cd = ClimateCoverData(
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
            cloud_coverage_above_threshold=True,
        )
        pr = _make_pr(control_method=ControlMethod.CLOUD, climate_data=cd)
        diag, _ = builder.build(_base_ctx(climate_mode=True, pipeline_result=pr))
        assert diag["climate_conditions"]["cloud_coverage_above_threshold"] is True


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
            "manual_toggle",
            "enabled_toggle",
            "cloud_suppression_enabled",
            "cloudy_position",
        }
        assert expected_keys == set(config.keys())

    def test_configuration_reflects_context(self, builder: DiagnosticsBuilder):
        """Configuration reflects pipeline result and context state values."""
        pr = _make_pr(control_method=ControlMethod.FORCE)
        diag, _ = builder.build(
            _base_ctx(
                pipeline_result=pr,
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
        assert "meta" in diag
        assert "decision_trace" in diag
        assert "covers" in diag
        assert "cover_commands" in diag
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_explanation_matches_diagnostics(self, builder: DiagnosticsBuilder):
        """The explanation returned as second element matches the one in dict."""
        diag, explanation = builder.build(_base_ctx())
        assert diag["position_explanation"] == explanation


# ---------------------------------------------------------------------------
# Meta section
# ---------------------------------------------------------------------------


class TestMeta:
    """Tests for the meta section (integration version, cover type, update health)."""

    def test_meta_section_present(self, builder: DiagnosticsBuilder):
        """Meta key is always present in output."""
        diag, _ = builder.build(_base_ctx())
        assert "meta" in diag

    def test_meta_integration_version(self, builder: DiagnosticsBuilder):
        """integration_version is surfaced from context."""
        ctx = _base_ctx(integration_version="2.15.0")
        diag, _ = builder.build(ctx)
        assert diag["meta"]["integration_version"] == "2.15.0"

    def test_meta_cover_type(self, builder: DiagnosticsBuilder):
        """cover_type is surfaced from context."""
        ctx = _base_ctx(cover_type="cover_blind")
        diag, _ = builder.build(ctx)
        assert diag["meta"]["cover_type"] == "cover_blind"

    def test_meta_coordinator_update_success(self, builder: DiagnosticsBuilder):
        """coordinator_update.last_update_success reflects context value."""
        ctx = _base_ctx(
            last_update_success=False, last_exception_repr="RuntimeError('boom')"
        )
        diag, _ = builder.build(ctx)
        update = diag["meta"]["coordinator_update"]
        assert update["last_update_success"] is False
        assert update["last_exception"] == "RuntimeError('boom')"

    def test_meta_update_interval(self, builder: DiagnosticsBuilder):
        """update_interval_seconds is surfaced from context."""
        ctx = _base_ctx(update_interval_seconds=30.0)
        diag, _ = builder.build(ctx)
        assert diag["meta"]["coordinator_update"]["update_interval_seconds"] == 30.0

    def test_meta_defaults_when_not_provided(self, builder: DiagnosticsBuilder):
        """Meta section has stable shape when optional fields are absent."""
        diag, _ = builder.build(_base_ctx())
        meta = diag["meta"]
        assert meta["integration_version"] is None
        assert meta["cover_type"] is None
        assert meta["coordinator_update"]["last_exception"] is None


# ---------------------------------------------------------------------------
# Decision trace section
# ---------------------------------------------------------------------------


class TestDecisionTrace:
    """Tests for the decision_trace section."""

    def test_empty_trace_when_no_pipeline_result(self, builder: DiagnosticsBuilder):
        """decision_trace is an empty list when pipeline_result is None."""
        ctx = _base_ctx(pipeline_result=None)
        diag, _ = builder.build(ctx)
        assert diag["decision_trace"] == []

    def test_empty_trace_when_pipeline_has_no_steps(self, builder: DiagnosticsBuilder):
        """decision_trace is an empty list when PipelineResult has no steps."""
        diag, _ = builder.build(_base_ctx())  # default _make_pr() has empty trace
        assert diag["decision_trace"] == []

    def test_trace_serialized_correctly(self, builder: DiagnosticsBuilder):
        """Each DecisionStep is serialized to the expected dict shape."""
        steps = [
            DecisionStep(
                handler="force_override",
                matched=False,
                reason="no sensor active",
                position=None,
            ),
            DecisionStep(
                handler="manual_override",
                matched=True,
                reason="user moved cover",
                position=50,
            ),
        ]
        pr = PipelineResult(
            position=50,
            control_method=ControlMethod.MANUAL,
            reason="manual_override",
            decision_trace=steps,
        )
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        trace = diag["decision_trace"]
        assert len(trace) == 2
        assert trace[0] == {
            "handler": "force_override",
            "matched": False,
            "reason": "no sensor active",
            "position": None,
        }
        assert trace[1] == {
            "handler": "manual_override",
            "matched": True,
            "reason": "user moved cover",
            "position": 50,
        }

    def test_trace_preserves_order(self, builder: DiagnosticsBuilder):
        """Trace order matches the order of DecisionStep entries."""
        steps = [
            DecisionStep(handler="a", matched=False, reason="skip a", position=None),
            DecisionStep(handler="b", matched=False, reason="skip b", position=None),
            DecisionStep(handler="c", matched=True, reason="matched c", position=30),
        ]
        pr = PipelineResult(
            position=30,
            control_method=ControlMethod.SOLAR,
            reason="c",
            decision_trace=steps,
        )
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        handlers = [s["handler"] for s in diag["decision_trace"]]
        assert handlers == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Covers section
# ---------------------------------------------------------------------------


class TestCovers:
    """Tests for the covers section (live cover entity state)."""

    def test_covers_empty_by_default(self, builder: DiagnosticsBuilder):
        """Covers is an empty dict when no covers context is provided."""
        diag, _ = builder.build(_base_ctx())
        assert diag["covers"] == {}

    def test_covers_surfaced_from_context(self, builder: DiagnosticsBuilder):
        """Covers dict from context is surfaced verbatim."""
        covers = {
            "cover.living_room": {
                "current_position": 42,
                "available": True,
                "capabilities": {
                    "has_set_position": True,
                    "has_set_tilt_position": False,
                    "has_open": True,
                    "has_close": True,
                },
            }
        }
        diag, _ = builder.build(_base_ctx(covers=covers))
        assert diag["covers"]["cover.living_room"]["current_position"] == 42
        assert diag["covers"]["cover.living_room"]["available"] is True

    def test_covers_unavailable_entity(self, builder: DiagnosticsBuilder):
        """An unavailable cover (None position) is represented correctly."""
        covers = {
            "cover.bedroom": {
                "current_position": None,
                "available": False,
                "capabilities": None,
            }
        }
        diag, _ = builder.build(_base_ctx(covers=covers))
        assert diag["covers"]["cover.bedroom"]["available"] is False
        assert diag["covers"]["cover.bedroom"]["current_position"] is None


# ---------------------------------------------------------------------------
# cover_commands always-on
# ---------------------------------------------------------------------------


class TestCoverCommands:
    """Tests for the always-on cover_commands section."""

    def test_cover_commands_always_present(self, builder: DiagnosticsBuilder):
        """cover_commands key is always present even with no command state."""
        diag, _ = builder.build(_base_ctx())
        assert "cover_commands" in diag
        assert isinstance(diag["cover_commands"], dict)

    def test_cover_commands_empty_when_no_state(self, builder: DiagnosticsBuilder):
        """cover_commands is an empty dict when cover_command_state is None."""
        diag, _ = builder.build(_base_ctx(cover_command_state=None))
        assert diag["cover_commands"] == {}

    def test_cover_commands_surfaced_from_context(self, builder: DiagnosticsBuilder):
        """cover_command_state context value is emitted under cover_commands."""
        state = {"cover.living_room": {"retry_count": 2, "gave_up": False}}
        diag, _ = builder.build(_base_ctx(cover_command_state=state))
        assert diag["cover_commands"]["cover.living_room"]["retry_count"] == 2

    def test_cover_command_state_key_absent(self, builder: DiagnosticsBuilder):
        """Old cover_command_state key is NOT present — replaced by cover_commands."""
        diag, _ = builder.build(_base_ctx())
        assert "cover_command_state" not in diag


# ---------------------------------------------------------------------------
# Manual override state section
# ---------------------------------------------------------------------------


class TestManualOverrideState:
    """Tests for the manual_override_state section."""

    def test_absent_when_not_provided(self, builder: DiagnosticsBuilder):
        """manual_override_state section is absent when context field is None."""
        diag, _ = builder.build(_base_ctx(manual_override_state=None))
        assert "manual_override_state" not in diag

    def test_state_surfaced_from_context(self, builder: DiagnosticsBuilder):
        """manual_override_state dict from context is surfaced under the expected key."""
        now = dt.datetime(2026, 4, 10, 14, 22, 0, tzinfo=dt.UTC)
        state = {
            "reset_duration_seconds": 7200,
            "tracked_covers": ["cover.living_room"],
            "entries": {
                "cover.living_room": {
                    "active": True,
                    "started_at": now.isoformat(),
                    "remaining_seconds": 4200,
                }
            },
        }
        diag, _ = builder.build(_base_ctx(manual_override_state=state))
        mo = diag["manual_override_state"]
        assert mo["reset_duration_seconds"] == 7200
        assert "cover.living_room" in mo["entries"]
        assert mo["entries"]["cover.living_room"]["remaining_seconds"] == 4200

    def test_remaining_seconds_non_negative(self, builder: DiagnosticsBuilder):
        """remaining_seconds is always >= 0 (expired overrides show 0)."""
        state = {
            "reset_duration_seconds": 7200,
            "tracked_covers": ["cover.bedroom"],
            "entries": {
                "cover.bedroom": {
                    "active": False,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "remaining_seconds": 0,
                }
            },
        }
        diag, _ = builder.build(_base_ctx(manual_override_state=state))
        assert (
            diag["manual_override_state"]["entries"]["cover.bedroom"][
                "remaining_seconds"
            ]
            == 0
        )


# ---------------------------------------------------------------------------
# cloudy_position diagnostics (Issue #311)
# ---------------------------------------------------------------------------


class TestCloudyPositionDiagnostics:
    """Verify cloudy_position appears in the correct diagnostic sections."""

    def test_configuration_includes_cloudy_position_when_set(
        self, builder: DiagnosticsBuilder
    ):
        """configuration.cloudy_position surfaces the configured value."""
        options = {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUDY_POSITION: 25}
        diag, _ = builder.build(_base_ctx(config_options=options))
        assert diag["configuration"]["cloudy_position"] == 25

    def test_configuration_cloudy_position_none_when_absent(
        self, builder: DiagnosticsBuilder
    ):
        """configuration.cloudy_position is None when option is not configured."""
        diag, _ = builder.build(_base_ctx(config_options={}))
        assert diag["configuration"]["cloudy_position"] is None

    def test_configuration_includes_cloud_suppression_enabled(
        self, builder: DiagnosticsBuilder
    ):
        """configuration.cloud_suppression_enabled surfaces the toggle state."""
        options = {CONF_CLOUD_SUPPRESSION: True}
        diag, _ = builder.build(_base_ctx(config_options=options))
        assert diag["configuration"]["cloud_suppression_enabled"] is True

    def test_configuration_cloud_suppression_enabled_false_when_absent(
        self, builder: DiagnosticsBuilder
    ):
        """configuration.cloud_suppression_enabled defaults to False."""
        diag, _ = builder.build(_base_ctx(config_options={}))
        assert diag["configuration"]["cloud_suppression_enabled"] is False

    def test_default_position_includes_configured_cloudy_pos(
        self, builder: DiagnosticsBuilder
    ):
        """default_position.configured_cloudy_pos surfaces PipelineResult.configured_cloudy_pos."""
        pr = _make_pr(configured_cloudy_pos=25)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["default_position"]["configured_cloudy_pos"] == 25

    def test_default_position_configured_cloudy_pos_none_when_not_set(
        self, builder: DiagnosticsBuilder
    ):
        """configured_cloudy_pos is None when not configured (not 0)."""
        pr = _make_pr()
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["default_position"]["configured_cloudy_pos"] is None

    def test_decision_trace_preserves_cloudy_position_reason(
        self, builder: DiagnosticsBuilder
    ):
        """Handler reason mentioning 'cloudy position' is preserved in decision_trace."""
        pr = PipelineResult(
            position=25,
            control_method=ControlMethod.CLOUD,
            reason="cloud/low-light suppression — weather not sunny → cloudy position 25%",
            decision_trace=[
                DecisionStep(
                    handler="cloud_suppression",
                    matched=True,
                    reason="cloud/low-light suppression — weather not sunny → cloudy position 25%",
                    position=25,
                )
            ],
        )
        diag, explanation = builder.build(_base_ctx(pipeline_result=pr))
        trace = diag["decision_trace"]
        cloud_step = next(
            (s for s in trace if s["handler"] == "cloud_suppression"), None
        )
        assert cloud_step is not None
        assert "cloudy position 25%" in cloud_step["reason"]
        assert "cloudy position 25%" in explanation
