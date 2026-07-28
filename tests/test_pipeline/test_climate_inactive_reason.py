"""Tests for ClimateHandler.inactive_reason() slug helper — Issue #589.

TDD RED step: these tests will fail until ClimateInactiveReason is added to
const.py and inactive_reason() is implemented in pipeline/handlers/climate.py.
"""

from __future__ import annotations


from custom_components.adaptive_cover_pro.const import ClimateInactiveReason
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    inactive_reason,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)
from tests.test_pipeline.conftest import make_snapshot


def _make_result_with_trace(
    *,
    climate_matched: bool = False,
    climate_reason: str = "climate mode not enabled",
    winner_handler: str = "default",
    position: int = 50,
) -> PipelineResult:
    """Build a minimal PipelineResult with a climate step in decision_trace."""
    from custom_components.adaptive_cover_pro.const import ControlMethod

    climate_step = DecisionStep(
        handler="climate",
        matched=climate_matched,
        reason=climate_reason,
        position=None if not climate_matched else position,
    )
    winner_step = DecisionStep(
        handler=winner_handler,
        matched=True,
        reason="winner",
        position=position,
    )
    return PipelineResult(
        position=position,
        control_method=ControlMethod.DEFAULT,
        reason="test",
        decision_trace=[climate_step, winner_step],
    )


def _make_result_climate_winner(*, position: int = 50) -> PipelineResult:
    """Build a PipelineResult where climate is the winning handler."""
    from custom_components.adaptive_cover_pro.const import ControlMethod

    climate_step = DecisionStep(
        handler="climate",
        matched=True,
        reason="climate mode active (summer)",
        position=position,
    )
    return PipelineResult(
        position=position,
        control_method=ControlMethod.SUMMER,
        reason="climate mode active",
        decision_trace=[climate_step],
    )


class TestClimateInactiveReasonSlugs:
    """inactive_reason() must return the correct ClimateInactiveReason slug."""

    def test_mode_off_when_climate_not_enabled(self) -> None:
        """climate_mode_enabled=False → ClimateInactiveReason.MODE_OFF."""
        snap = make_snapshot(climate_mode_enabled=False)
        result = _make_result_with_trace(
            climate_matched=False, climate_reason="climate mode not enabled"
        )
        assert inactive_reason(snap, result) == ClimateInactiveReason.MODE_OFF

    def test_outside_time_window(self) -> None:
        """in_time_window=False → ClimateInactiveReason.OUTSIDE_TIME_WINDOW."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=False,
        )
        result = _make_result_with_trace(
            climate_matched=False, climate_reason="outside time window"
        )
        assert (
            inactive_reason(snap, result) == ClimateInactiveReason.OUTSIDE_TIME_WINDOW
        )

    def test_readings_unavailable(self) -> None:
        """climate_readings=None with mode enabled → ClimateInactiveReason.READINGS_UNAVAILABLE."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=None,
            climate_options=None,
        )
        result = _make_result_with_trace(
            climate_matched=False,
            climate_reason="climate readings or options unavailable",
        )
        assert (
            inactive_reason(snap, result) == ClimateInactiveReason.READINGS_UNAVAILABLE
        )

    def test_thresholds_not_met_when_deferred(self) -> None:
        """Climate enabled + readings available + deferred → THRESHOLDS_NOT_MET."""
        from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
        from custom_components.adaptive_cover_pro.state.climate_provider import (
            ClimateReadings,
        )

        opts = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=readings,
            climate_options=opts,
        )
        result = _make_result_with_trace(
            climate_matched=False,
            climate_reason="deferred glare-control to solar/glare handlers",
        )
        assert inactive_reason(snap, result) == ClimateInactiveReason.THRESHOLDS_NOT_MET

    def test_other_mode_active_when_outprioritized(self) -> None:
        """Climate outprioritized by a higher handler → OTHER_MODE_ACTIVE."""
        from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
        from custom_components.adaptive_cover_pro.state.climate_provider import (
            ClimateReadings,
        )

        opts = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=15.0,  # winter — would win
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=readings,
            climate_options=opts,
        )
        # Build result where climate is outprioritized by manual_override
        from custom_components.adaptive_cover_pro.const import ControlMethod

        climate_step = DecisionStep(
            handler="climate",
            matched=False,
            reason="outprioritized by manual_override",
            position=None,
        )
        winner_step = DecisionStep(
            handler="manual_override",
            matched=True,
            reason="manual override active",
            position=70,
        )
        result = PipelineResult(
            position=70,
            control_method=ControlMethod.MANUAL,
            reason="manual override",
            decision_trace=[climate_step, winner_step],
        )
        assert inactive_reason(snap, result) == ClimateInactiveReason.OTHER_MODE_ACTIVE

    def test_active_when_climate_is_winner(self) -> None:
        """Climate is the winning handler → ClimateInactiveReason.ACTIVE."""
        from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
        from custom_components.adaptive_cover_pro.state.climate_provider import (
            ClimateReadings,
        )

        opts = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=15.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=readings,
            climate_options=opts,
        )
        result = _make_result_climate_winner()
        assert inactive_reason(snap, result) == ClimateInactiveReason.ACTIVE

    def test_result_none_returns_mode_off(self) -> None:
        """When pipeline_result is None (startup/unknown), returns MODE_OFF."""
        snap = make_snapshot(climate_mode_enabled=False)
        assert inactive_reason(snap, None) == ClimateInactiveReason.MODE_OFF

    def test_inactive_reason_value_outside_time_window(self) -> None:
        """OUTSIDE_TIME_WINDOW slug value matches ControlStatus.OUTSIDE_TIME_WINDOW."""
        from custom_components.adaptive_cover_pro.const import ControlStatus

        assert (
            ClimateInactiveReason.OUTSIDE_TIME_WINDOW
            == ControlStatus.OUTSIDE_TIME_WINDOW
        )


class TestClimateInactiveReasonSlugsDescribeSkipConsistency:
    """describe_skip must be consistent with inactive_reason slugs — one source of truth."""

    handler_cls = None

    def setup_method(self):
        from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
            ClimateHandler,
        )

        self.handler = ClimateHandler()

    def test_describe_skip_outside_time_window_still_works(self) -> None:
        """Refactored describe_skip must still mention 'time window' for outside-window case."""
        snap = make_snapshot(climate_mode_enabled=True, in_time_window=False)
        assert "time window" in self.handler.describe_skip(snap).lower()

    def test_describe_skip_mode_off_still_works(self) -> None:
        """Refactored describe_skip must still mention 'not enabled' for mode-off case."""
        snap = make_snapshot(climate_mode_enabled=False)
        assert "not enabled" in self.handler.describe_skip(snap).lower()

    def test_describe_skip_unavailable_still_works(self) -> None:
        """Refactored describe_skip must still mention 'unavailable' for readings-missing case."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=None,
            climate_options=None,
        )
        assert "unavailable" in self.handler.describe_skip(snap).lower()

    def test_describe_skip_deferred_still_works(self) -> None:
        """Refactored describe_skip must still mention 'deferred' for threshold-not-met case."""
        from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
        from custom_components.adaptive_cover_pro.state.climate_provider import (
            ClimateReadings,
        )

        opts = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = make_snapshot(
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=readings,
            climate_options=opts,
        )
        assert "deferred" in self.handler.describe_skip(snap).lower()
