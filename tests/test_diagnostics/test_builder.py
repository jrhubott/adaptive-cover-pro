"""Tests for the DiagnosticsBuilder."""

from __future__ import annotations

from types import SimpleNamespace
import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
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
    FORECAST_STEP_MINUTES,
    ControlStatus,
)
from custom_components.adaptive_cover_pro.const import ClimateStrategy, ControlMethod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cover(
    *,
    gamma: float = 10.0,
    sol_elev: float = 30.0,
    valid: bool = True,
    valid_elevation: bool = True,
    is_sun_in_blind_spot: bool = False,
    direct_sun_valid: bool = True,
    sunset_valid: bool = False,
    control_state_reason: str = "Sun in FOV",
    in_fov: bool = True,
    calc_details: dict | None = None,
) -> SimpleNamespace:
    """Create a minimal cover mock."""
    cover = SimpleNamespace(
        gamma=gamma,
        sol_elev=sol_elev,
        valid=valid,
        valid_elevation=valid_elevation,
        is_sun_in_blind_spot=is_sun_in_blind_spot,
        direct_sun_valid=direct_sun_valid,
        sunset_valid=sunset_valid,
        control_state_reason=control_state_reason,
        in_fov=in_fov,
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
    is_safety: bool = False,
    tilt: int | None = None,
    tilt_only_slot: int | None = None,
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
        is_safety=is_safety,
        tilt=tilt,
        tilt_only_slot=tilt_only_slot,
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

    def test_safety_custom_position_active(self, builder: DiagnosticsBuilder):
        """A safety-priority custom position reports ACTIVE control status (#563)."""
        pr = _make_pr(control_method=ControlMethod.CUSTOM_POSITION, is_safety=True)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.ACTIVE

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

    def test_deprecated_force_method_unmapped(self, builder: DiagnosticsBuilder):
        """ControlMethod.FORCE is no longer mapped to a status (deprecated, #563)."""
        pr = _make_pr(control_method=ControlMethod.FORCE)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_status"] == ControlStatus.ACTIVE


# ---------------------------------------------------------------------------
# Control state reason
# ---------------------------------------------------------------------------


class TestControlStateReason:
    """Control state reason string tests."""

    def test_motion_timeout(self, builder: DiagnosticsBuilder):
        """Motion timeout reason string."""
        pr = _make_pr(control_method=ControlMethod.MOTION)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_state_reason"] == "Occupancy Timeout"

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

    def test_safety_custom_position_explanation(self, builder: DiagnosticsBuilder):
        """A safety-priority custom position produces the slot's reason (#563)."""
        pr = _make_pr(
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom position #5 active (sensor.x) — position 75% [bypasses automatic control]",
            position=75,
            is_safety=True,
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "custom position #5" in explanation.lower()
        assert "75%" in explanation

    def test_motion_timeout_explanation(self, builder: DiagnosticsBuilder):
        """Motion timeout produces correct explanation."""
        pr = _make_pr(
            control_method=ControlMethod.MOTION,
            reason="occupancy timeout active — default position 30%",
            position=30,
        )
        _, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "occupancy" in explanation.lower()
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
        """Calculation details from cover are surfaced, with cover_type stamped."""
        details = {"edge_case": True, "safety_margin": 1.1}
        cover = _make_cover(calc_details=details)
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        out = diag["calculation_details"]
        # Original keys are preserved; the builder stamps the cover_type discriminator.
        assert out["edge_case"] is True
        assert out["safety_margin"] == pytest.approx(1.1)
        assert out["cover_type"] == "cover_blind"


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


class TestEndOfWindowDiagnostics:
    """issue #625: end-of-window position surfaced in diagnostics."""

    def test_configured_value_and_active_flag_when_closed(
        self, builder: DiagnosticsBuilder
    ):
        from custom_components.adaptive_cover_pro.const import CONF_END_OF_WINDOW_POS

        ctx = _base_ctx(
            config_options={CONF_END_OF_WINDOW_POS: 20},
            before_end_time=False,
            end_of_window_active=True,
        )
        diag, _ = builder.build(ctx)
        assert diag["configuration"]["end_of_window_position"] == 20
        assert diag["default_position"]["configured_end_of_window_pos"] == 20
        assert diag["default_position"]["end_of_window_active"] is True

    def test_active_flag_false_when_window_open(self, builder: DiagnosticsBuilder):
        from custom_components.adaptive_cover_pro.const import CONF_END_OF_WINDOW_POS

        ctx = _base_ctx(
            config_options={CONF_END_OF_WINDOW_POS: 20},
            before_end_time=True,
            end_of_window_active=False,
        )
        diag, _ = builder.build(ctx)
        assert diag["default_position"]["end_of_window_active"] is False

    def test_unset_value_is_none(self, builder: DiagnosticsBuilder):
        ctx = _base_ctx(config_options={})
        diag, _ = builder.build(ctx)
        assert diag["configuration"]["end_of_window_position"] is None
        assert diag["default_position"]["configured_end_of_window_pos"] is None
        assert diag["default_position"]["end_of_window_active"] is False


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

    def test_sun_validity_includes_in_fov(self, builder: DiagnosticsBuilder):
        """sun_validity includes in_fov field."""
        diag, _ = builder.build(_base_ctx())
        sv = diag["sun_validity"]
        assert sv["in_fov"] is True

    def test_sun_validity_includes_direct_sun_valid(self, builder: DiagnosticsBuilder):
        """sun_validity includes direct_sun_valid field."""
        diag, _ = builder.build(_base_ctx())
        sv = diag["sun_validity"]
        assert sv["direct_sun_valid"] is True

    def test_sun_state_hitting_when_direct_sun_valid(self, builder: DiagnosticsBuilder):
        """sun_state is 'hitting' when direct_sun_valid=True."""
        cover = _make_cover(direct_sun_valid=True, in_fov=True)
        diag, _ = builder.build(_base_ctx(cover=cover))
        assert diag["sun_validity"]["sun_state"] == "hitting"

    def test_sun_state_in_fov_not_valid_when_in_fov_but_not_direct(
        self, builder: DiagnosticsBuilder
    ):
        """sun_state is 'in_fov_not_valid' when in_fov=True but direct_sun_valid=False."""
        cover = _make_cover(direct_sun_valid=False, in_fov=True, valid=True)
        diag, _ = builder.build(_base_ctx(cover=cover))
        assert diag["sun_validity"]["sun_state"] == "in_fov_not_valid"

    def test_sun_state_outside_fov_when_not_in_fov(self, builder: DiagnosticsBuilder):
        """sun_state is 'outside_fov' when in_fov=False."""
        cover = _make_cover(direct_sun_valid=False, in_fov=False, valid=False)
        diag, _ = builder.build(_base_ctx(cover=cover))
        assert diag["sun_validity"]["sun_state"] == "outside_fov"

    def test_sun_state_hitting_takes_priority_over_in_fov(
        self, builder: DiagnosticsBuilder
    ):
        """sun_state is 'hitting' (not 'in_fov_not_valid') when direct_sun_valid=True."""
        cover = _make_cover(direct_sun_valid=True, in_fov=True)
        diag, _ = builder.build(_base_ctx(cover=cover))
        assert diag["sun_validity"]["sun_state"] == "hitting"


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
            policy=get_policy("cover_blind"),
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
            policy=get_policy("cover_blind"),
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
            policy=get_policy("cover_blind"),
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
            policy=get_policy("cover_blind"),
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

    def test_climate_diagnostics_include_resolved_temp_sensor(
        self, builder: DiagnosticsBuilder
    ):
        """Area-resolved temp sensor + provenance surface in climate diagnostics (#786)."""
        cd = self._make_climate_data()
        pr = _make_pr(control_method=ControlMethod.WINTER, climate_data=cd)
        diag, _ = builder.build(
            _base_ctx(
                climate_mode=True,
                pipeline_result=pr,
                temp_sensor_entity_id="sensor.bedroom_temp",
                temp_sensor_source="area",
                temp_sensor_area_id="area_bedroom",
            )
        )
        assert diag["temp_sensor"] == {
            "entity_id": "sensor.bedroom_temp",
            "source": "area",
            "area_id": "area_bedroom",
        }

    def test_temperature_details_include_outside_temp_source(
        self, builder: DiagnosticsBuilder
    ):
        """Outdoor-temp source provenance surfaces in temperature_details (#547)."""
        cd = self._make_climate_data()
        pr = _make_pr(control_method=ControlMethod.WINTER, climate_data=cd)
        diag, _ = builder.build(
            _base_ctx(
                climate_mode=True,
                pipeline_result=pr,
                outside_temp_source="forecast_max",
            )
        )
        assert diag["temperature_details"]["outside_temperature_source"] == (
            "forecast_max"
        )

    def test_temp_sensor_absent_when_source_none(self, builder: DiagnosticsBuilder):
        """No temp_sensor block when nothing resolved (source none)."""
        cd = self._make_climate_data()
        pr = _make_pr(control_method=ControlMethod.WINTER, climate_data=cd)
        diag, _ = builder.build(
            _base_ctx(
                climate_mode=True,
                pipeline_result=pr,
                temp_sensor_source="none",
            )
        )
        assert "temp_sensor" not in diag

    def test_climate_conditions_cloud_coverage_true_when_active(
        self, builder: DiagnosticsBuilder
    ):
        """cloud_coverage_above_threshold is True when the flag is set."""
        cd = ClimateCoverData(
            temp_low=20.0,
            temp_high=25.0,
            temp_switch=True,
            policy=get_policy("cover_blind"),
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
            "blind_spot_elevation_mode",
            "blind_spot_left",
            "blind_spot_right",
            "blind_spot_left_2",
            "blind_spot_right_2",
            "blind_spot_elevation_2",
            "blind_spot_elevation_mode_2",
            "blind_spot_left_3",
            "blind_spot_right_3",
            "blind_spot_elevation_3",
            "blind_spot_elevation_mode_3",
            "min_position",
            "min_position_sun_tracking",
            "max_position",
            "enable_min_position",
            "enable_max_position",
            "position_tolerance",
            "enable_position_matching",
            "inverse_state",
            "interpolation",
            "force_override_active",
            "motion_sensors",
            "motion_template",
            "motion_template_active",
            "motion_template_mode",
            "motion_timeout",
            "motion_detected",
            "motion_timeout_active",
            "motion_hold_active",
            "manual_toggle",
            "manual_ignore_external",
            "enabled_toggle",
            "cloud_suppression_enabled",
            "cloudy_position",
            "end_of_window_position",
            "is_sunny_source",
            "templated_thresholds",
        }
        assert expected_keys == set(config.keys())

    def test_configuration_reflects_context(self, builder: DiagnosticsBuilder):
        """Configuration reflects pipeline result and context state values.

        force_override_active is kept one release for the companion card and
        is True when a safety-priority custom position wins (#563).
        """
        pr = _make_pr(control_method=ControlMethod.CUSTOM_POSITION, is_safety=True)
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

    def test_trace_includes_priority_when_set(self, builder: DiagnosticsBuilder):
        """A step's priority is surfaced; absent when None (synthetic step)."""
        steps = [
            DecisionStep(
                handler="weather",
                matched=True,
                reason="storm",
                position=100,
                priority=90,
            ),
            DecisionStep(
                handler="floor_clamp", matched=True, reason="floor", position=40
            ),
        ]
        pr = PipelineResult(
            position=100,
            control_method=ControlMethod.WEATHER,
            reason="weather",
            decision_trace=steps,
        )
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        trace = diag["decision_trace"]
        assert trace[0]["priority"] == 90
        assert "priority" not in trace[1]  # None → omitted


class TestHandlerPriorities:
    """Tests for the handler_priorities section."""

    def test_defaults_when_no_overrides(self, builder: DiagnosticsBuilder):
        diag, _ = builder.build(_base_ctx(config_options={}))
        rows = diag["handler_priorities"]
        assert rows["weather"] == {
            "priority": 90,
            "default": 90,
            "overridden": False,
        }
        # Ordered highest-priority first.
        assert list(rows) == [
            "weather",
            "manual_override",
            "motion_timeout",
            "cloud_suppression",
            "climate",
            "glare_zone",
            "solar",
        ]

    def test_override_marked_and_reordered(self, builder: DiagnosticsBuilder):
        diag, _ = builder.build(
            _base_ctx(config_options={"solar_priority": 95, "weather_priority": 20})
        )
        rows = diag["handler_priorities"]
        assert rows["solar"] == {"priority": 95, "default": 40, "overridden": True}
        assert rows["weather"] == {"priority": 20, "default": 90, "overridden": True}
        # Solar now sorts first; weather drops below climate.
        assert list(rows)[0] == "solar"
        assert list(rows).index("weather") > list(rows).index("climate")


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

    def test_is_sunny_source_template_when_only_template_set(
        self, builder: DiagnosticsBuilder
    ):
        """is_sunny_source == '[template]' when only the template is configured (#639)."""
        from custom_components.adaptive_cover_pro.const import CONF_IS_SUNNY_TEMPLATE

        options = {CONF_IS_SUNNY_TEMPLATE: "{{ true }}"}
        diag, _ = builder.build(_base_ctx(config_options=options))
        assert diag["configuration"]["is_sunny_source"] == "[template]"

    def test_is_sunny_source_sensor_takes_priority_over_template(
        self, builder: DiagnosticsBuilder
    ):
        """A configured sensor wins over the template in is_sunny_source (#639)."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_IS_SUNNY_SENSOR,
            CONF_IS_SUNNY_TEMPLATE,
        )

        options = {
            CONF_IS_SUNNY_SENSOR: "binary_sensor.sunny",
            CONF_IS_SUNNY_TEMPLATE: "{{ true }}",
        }
        diag, _ = builder.build(_base_ctx(config_options=options))
        assert diag["configuration"]["is_sunny_source"] == "binary_sensor.sunny"

    def test_is_sunny_source_weather_state_when_neither_set(
        self, builder: DiagnosticsBuilder
    ):
        """Falls back to 'weather_state' when no sensor and no template (#639)."""
        diag, _ = builder.build(_base_ctx(config_options={}))
        assert diag["configuration"]["is_sunny_source"] == "weather_state"

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


# ---------------------------------------------------------------------------
# Issue #33: primary-axis suppression counts in debug section
# ---------------------------------------------------------------------------


class TestPrimaryAxisSuppressionCounts:
    """Verify the new per-entity suppression counter surfaces in diagnostics."""

    def test_primary_axis_suppression_counts_surface_in_debug_info(
        self, builder: DiagnosticsBuilder
    ):
        """Non-empty counter dict surfaces under ``primary_axis_suppression_last_24h``.

        The coordinator threads
        ``ManualOverrideManager.primary_axis_suppression_counts()`` into
        ``DiagnosticContext`` so a user looking at a diagnostic file can
        immediately tell whether the new publish-lag guard is firing for
        their actuator.
        """
        ctx = _base_ctx(primary_axis_suppression_counts={"cover.living_room": 7})
        diag, _ = builder.build(ctx)
        assert diag["primary_axis_suppression_last_24h"] == {"cover.living_room": 7}

    def test_primary_axis_suppression_counts_omitted_when_empty(
        self, builder: DiagnosticsBuilder
    ):
        """Empty counter dict → key absent from diagnostics.

        Keeps the noise level down: only surfaces when the new guard has
        actually fired at least once. Mirrors the pattern used by
        ``event_timeline``.
        """
        ctx = _base_ctx(primary_axis_suppression_counts={})
        diag, _ = builder.build(ctx)
        assert "primary_axis_suppression_last_24h" not in diag


# ---------------------------------------------------------------------------
# Position forecast
# ---------------------------------------------------------------------------


class TestForecast:
    """Verify the rest-of-day forecast section."""

    @staticmethod
    def _make_forecast():
        from custom_components.adaptive_cover_pro.forecast import (
            Forecast,
            ForecastEvent,
            ForecastSample,
        )

        t0 = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.UTC)
        return Forecast(
            samples=(
                ForecastSample(t=t0, position=40, handler="solar"),
                ForecastSample(
                    t=t0 + dt.timedelta(minutes=15), position=0, handler="default"
                ),
            ),
            events=(
                ForecastEvent(
                    t=t0, kind="fov_exit", label="Sun exits acceptance angle"
                ),
            ),
        )

    def test_omitted_when_no_forecast(self, builder: DiagnosticsBuilder):
        """No cached forecast → key absent (background recompute not done yet)."""
        diag, _ = builder.build(_base_ctx(position_forecast=None))
        assert "position_forecast" not in diag

    def test_present_when_forecast_cached(self, builder: DiagnosticsBuilder):
        """Cached forecast surfaces under ``position_forecast`` with samples/events."""
        ctx = _base_ctx(position_forecast=self._make_forecast())
        diag, _ = builder.build(ctx)
        section = diag["position_forecast"]
        assert section["step_minutes"] == FORECAST_STEP_MINUTES
        assert section["forecast"][0] == {
            "t": "2026-06-14T12:00:00+00:00",
            "position": 40,
            "handler": "solar",
        }
        assert section["events"][0]["kind"] == "fov_exit"

    def test_labeled_solar_only(self, builder: DiagnosticsBuilder):
        """A reader must be told the projection ignores non-solar handlers."""
        ctx = _base_ctx(position_forecast=self._make_forecast())
        diag, _ = builder.build(ctx)
        description = diag["position_forecast"]["description"].lower()
        assert "solar-tracking-only" in description
        assert "does not model" in description
        assert "decision_trace" in description


# ---------------------------------------------------------------------------
# Issue #667: surface an active tilt-only custom slot in the Control Status
# tracker entity (position_explanation + control_state_reason).
# ---------------------------------------------------------------------------


class TestTiltOnlyCustomSlotSurfacing:
    """An applied tilt-only custom slot is visible on the Control Status tracker."""

    def test_position_explanation_appends_tilt_only_slot(
        self, builder: DiagnosticsBuilder
    ):
        """position_explanation mentions the tilt-only slot alongside the winner."""
        pr = _make_pr(
            reason="solar position 45%",
            tilt=30,
            tilt_only_slot=1,
        )
        diag, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "solar position 45%" in explanation
        assert "tilt fixed at 30% by Custom #1" in explanation
        assert explanation == diag["position_explanation"]

    def test_control_state_reason_appends_tilt_only_slot(
        self, builder: DiagnosticsBuilder
    ):
        """control_state_reason gains a tilt-only suffix for the tracker entity."""
        pr = _make_pr(reason="solar position 45%", tilt=30, tilt_only_slot=2)
        diag, _ = builder.build(_base_ctx(pipeline_result=pr))
        assert diag["control_state_reason"] == "Sun in FOV — tilt fixed by Custom #2"

    def test_no_tilt_only_slot_leaves_strings_unchanged(
        self, builder: DiagnosticsBuilder
    ):
        """With no applied tilt-only slot, neither string mentions a tilt fix."""
        pr = _make_pr(reason="solar position 45%", tilt=None, tilt_only_slot=None)
        diag, explanation = builder.build(_base_ctx(pipeline_result=pr))
        assert "tilt fixed" not in explanation
        assert "tilt fixed" not in diag["control_state_reason"]
        assert diag["control_state_reason"] == "Sun in FOV"


# ---------------------------------------------------------------------------
# Solar-calculation trace (issue #682) — cover_type stamp + boundary rounding
# ---------------------------------------------------------------------------


class TestCalculationDetailsTrace:
    """`_build_position_calc_details` stamps cover_type and rounds at the boundary."""

    def _raw_vertical_trace(self) -> dict:
        """Return a raw, deliberately un-rounded vertical trace as an engine emits."""
        return {
            "sol_elev_deg": 45.123456,
            "gamma_deg": -12.98765,
            "position_pct": 25,
            "edge_case_detected": False,
            "effective_distance_m": 0.512345,
            "effective_distance_source": "base",
            "window_depth_contribution_m": 0.0,
            "sill_height_offset_m": 0.0,
            "safety_margin": 1.234567,
            "glare_zones_active": [],
            "cos_gamma": 0.974370,
            "cos_gamma_clamped": 0.974370,
            "path_length_m": 0.525678,
            "base_height_m": 0.525678,
            "adjusted_height_m": 0.648911,
            "clamped_to_window": False,
        }

    def test_cover_type_stamped_from_ctx(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        assert details["cover_type"] == "cover_blind"

    def test_cover_type_matches_ctx_cover_type(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        ctx = _base_ctx(cover=cover, cover_type="cover_venetian")
        diag, _ = builder.build(ctx)
        assert diag["calculation_details"]["cover_type"] == ctx.cover_type

    def test_angles_rounded_one_decimal(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        assert details["sol_elev_deg"] == 45.1
        assert details["gamma_deg"] == -13.0

    def test_distances_rounded_three_decimals(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        assert details["effective_distance_m"] == 0.512
        assert details["path_length_m"] == 0.526

    def test_position_pct_is_integer(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details_pct = diag["calculation_details"]["position_pct"]
        assert isinstance(details_pct, int)
        assert details_pct == 25

    def test_ratios_rounded(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        # safety_margin / cos_gamma are unit-less ratios → kept at a few decimals.
        assert details["safety_margin"] == pytest.approx(1.235, abs=1e-3)

    def test_booleans_and_strings_passthrough(self, builder: DiagnosticsBuilder):
        cover = _make_cover(calc_details=self._raw_vertical_trace())
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        assert details["edge_case_detected"] is False
        assert details["effective_distance_source"] == "base"
        assert details["glare_zones_active"] == []

    def test_does_not_mutate_engine_trace(self, builder: DiagnosticsBuilder):
        """The builder must round into a copy, not mutate the engine's dict."""
        raw = self._raw_vertical_trace()
        cover = _make_cover(calc_details=raw)
        builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        # Engine's dict keeps full precision.
        assert raw["sol_elev_deg"] == 45.123456

    def test_nested_tilt_subtrace_rounded(self, builder: DiagnosticsBuilder):
        """Venetian nested tilt sub-trace is rounded the same way."""
        trace = self._raw_vertical_trace()
        trace["tilt"] = {
            "sol_elev_deg": 45.987654,
            "gamma_deg": 5.55555,
            "position_pct": 60,
            "beta_rad": 0.7853981,
            "discriminant": 1.234567,
            "negative_discriminant": False,
            "slat_angle_raw_deg": 88.7654,
            "nan_result": False,
            "max_degrees": 90,
            "tilt_mode": "mode1",
        }
        cover = _make_cover(calc_details=trace)
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_venetian"))
        tilt = diag["calculation_details"]["tilt"]
        assert tilt["sol_elev_deg"] == 46.0
        assert tilt["slat_angle_raw_deg"] == 88.8
        assert tilt["position_pct"] == 60

    def test_status_stamped_on_full_trace(self, builder: DiagnosticsBuilder):
        """The reason string is stamped as ``status`` on the direct-sun trace too."""
        cover = _make_cover(
            calc_details=self._raw_vertical_trace(),
            control_state_reason="Direct Sun",
        )
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        assert diag["calculation_details"]["status"] == "Direct Sun"


class TestCalculationDetailsFallback:
    """No-solar-target fallback trace (issue #682 follow-up).

    When the sun is outside the window no engine trace is recorded, but the
    builder still surfaces the sun inputs + reason so the live
    ``solar_calculation`` sensor's attributes explain the geometry. The state
    (``position_pct``) stays None — there is no solar target.
    """

    def test_fallback_emitted_when_no_engine_trace(self, builder: DiagnosticsBuilder):
        cover = _make_cover(
            calc_details=None,
            valid=False,
            direct_sun_valid=False,
            control_state_reason="Default: Acceptance Angle Exit",
            sol_elev=-3.21,
            gamma=41.04,
        )
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_blind"))
        details = diag["calculation_details"]
        assert details["position_pct"] is None
        assert details["status"] == "Default: Acceptance Angle Exit"
        assert details["cover_type"] == "cover_blind"

    def test_fallback_sun_inputs_rounded(self, builder: DiagnosticsBuilder):
        cover = _make_cover(
            calc_details=None,
            direct_sun_valid=False,
            sol_elev=-3.21987,
            gamma=41.04321,
        )
        diag, _ = builder.build(_base_ctx(cover=cover, cover_type="cover_tilt"))
        details = diag["calculation_details"]
        assert details["sol_elev_deg"] == -3.2
        assert details["gamma_deg"] == 41.0


class TestCalculationDetailsAllCoverTypes:
    """Single-source proof (issue #682): the same calculation_details rail that
    feeds the live solar_calculation sensor carries enriched, type-correct traces
    for every cover type in the diagnostics build — no extra download plumbing.
    """

    def _build_for(self, cover, cover_type: str) -> dict:
        # Exercise the exact rail (_build_position_calc_details) that both the
        # live sensor and the diagnostics download read — no full build() needed.
        out = DiagnosticsBuilder._build_position_calc_details(
            _base_ctx(cover=cover, cover_type=cover_type)
        )
        return out["calculation_details"]

    def test_vertical_trace_in_diagnostics(self):
        from tests.cover_helpers import build_vertical_cover

        cover = build_vertical_cover(
            logger=SimpleNamespace(debug=lambda *a, **k: None),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=SimpleNamespace(),
            win_azi=180,
            distance=0.5,
            h_win=2.0,
        )
        cover.calculate_position()
        details = self._build_for(cover, "cover_blind")
        assert details["cover_type"] == "cover_blind"
        assert "effective_distance_m" in details
        assert "position_pct" in details

    def test_horizontal_trace_in_diagnostics(self):
        from tests.cover_helpers import build_horizontal_cover

        cover = build_horizontal_cover(
            logger=SimpleNamespace(debug=lambda *a, **k: None),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=SimpleNamespace(),
            win_azi=180,
            distance=0.5,
            h_win=2.0,
            awn_length=2.0,
            awn_angle=0,
        )
        cover.calculate_position()
        details = self._build_for(cover, "cover_awning")
        assert details["cover_type"] == "cover_awning"
        # Horizontal-shaped, not vertical (latent-overwrite regression).
        assert "awn_angle_deg" in details
        assert "effective_distance_m" not in details

    def test_tilt_trace_in_diagnostics(self):
        from tests.cover_helpers import build_tilt_cover

        cover = build_tilt_cover(
            logger=SimpleNamespace(debug=lambda *a, **k: None),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=SimpleNamespace(),
            win_azi=180,
            slat_distance=0.03,
            depth=0.05,
            mode="mode1",
        )
        cover.calculate_position()
        details = self._build_for(cover, "cover_tilt")
        assert details["cover_type"] == "cover_tilt"
        assert "beta_rad" in details
        assert "tilt_mode" in details

    def test_venetian_nested_tilt_in_diagnostics(self):
        """Venetian: position keys at top level + nested tilt sub-key, both rounded."""
        from tests.cover_helpers import build_vertical_cover

        cover = build_vertical_cover(
            logger=SimpleNamespace(debug=lambda *a, **k: None),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=SimpleNamespace(),
            win_azi=180,
            distance=0.5,
            h_win=2.0,
        )
        cover.calculate_position()
        # Simulate the venetian policy merge: tilt engine trace under `tilt`.
        from tests.cover_helpers import build_tilt_cover

        tilt = build_tilt_cover(
            logger=SimpleNamespace(debug=lambda *a, **k: None),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=SimpleNamespace(),
            win_azi=180,
            slat_distance=0.03,
            depth=0.05,
            mode="mode1",
        )
        tilt.calculate_position()
        cover._last_calc_details["tilt"] = tilt._last_calc_details
        details = self._build_for(cover, "cover_venetian")
        assert details["cover_type"] == "cover_venetian"
        assert "tilt" in details
        assert "beta_rad" in details["tilt"]
        # Top-level position axis present.
        assert "position_pct" in details
