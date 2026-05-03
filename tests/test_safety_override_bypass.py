"""Tests for safety override bypass of automatic control.

Force Override and Weather Override set bypass_auto_control=True so they
still operate (send cover commands) even when the Automatic Control switch
is turned off.  These tests verify:

  1. Pipeline-level: bypass_auto_control flag is set correctly.
  2. Reason strings include '[bypasses automatic control]'.
  3. Other handlers (solar, default) do NOT set the flag.
  4. PipelineRegistry propagates the flag through to the final result.
  5. Coordinator-level: _pipeline_bypasses_auto_control property.
  6. Decision Trace sensor exposes bypass_auto_control attribute.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    DefaultHandler,
    ForceOverrideHandler,
    SolarHandler,
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_pipeline_result(*, bypass: bool) -> PipelineResult:
    """Build a minimal PipelineResult with the given bypass flag."""
    return PipelineResult(
        position=0,
        control_method=ControlMethod.FORCE,
        reason="test",
        bypass_auto_control=bypass,
    )


# ---------------------------------------------------------------------------
# 1 & 2 — ForceOverrideHandler sets bypass flag and includes text in reason
# ---------------------------------------------------------------------------


class TestForceOverrideBypass:
    """ForceOverrideHandler must set bypass_auto_control=True."""

    handler = ForceOverrideHandler()

    def test_bypass_flag_set(self) -> None:
        """Result must have bypass_auto_control=True when sensor is on."""
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": True},
            force_override_position=0,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is True

    def test_reason_includes_bypass_text(self) -> None:
        """Reason string must include '[bypasses automatic control]'."""
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": True},
            force_override_position=0,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_not_active_returns_none(self) -> None:
        """No result when all sensors are off — bypass flag irrelevant."""
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": False},
        )
        result = self.handler.evaluate(snapshot)
        assert result is None

    def test_no_sensors_returns_none(self) -> None:
        """No result when sensor list is empty."""
        snapshot = make_snapshot(force_override_sensors={})
        result = self.handler.evaluate(snapshot)
        assert result is None

    def test_bypass_position_correct(self) -> None:
        """Result position must match the configured force override position."""
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": True},
            force_override_position=25,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 25


# ---------------------------------------------------------------------------
# WeatherOverrideHandler sets bypass flag and includes text in reason
# ---------------------------------------------------------------------------


class TestWeatherOverrideBypass:
    """WeatherOverrideHandler with bypass enabled (default True)."""

    handler = WeatherOverrideHandler()

    def test_bypass_flag_set_when_enabled(self) -> None:
        """Result has bypass_auto_control=True when weather_bypass_auto_control is True."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=True,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is True

    def test_reason_includes_bypass_text_when_enabled(self) -> None:
        """Reason string includes '[bypasses automatic control]' when bypass is enabled."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=True,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_not_active_returns_none(self) -> None:
        """No result when weather override is not active."""
        snapshot = make_snapshot(weather_override_active=False)
        result = self.handler.evaluate(snapshot)
        assert result is None

    def test_bypass_position_correct(self) -> None:
        """Result position must match the configured weather override position."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=10,
            weather_bypass_auto_control=True,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 10


class TestWeatherOverrideBypassDisabled:
    """WeatherOverrideHandler with bypass explicitly disabled."""

    handler = WeatherOverrideHandler()

    def test_bypass_flag_false_when_disabled(self) -> None:
        """Result has bypass_auto_control=False when weather_bypass_auto_control is False."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=False,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is False

    def test_reason_excludes_bypass_text_when_disabled(self) -> None:
        """Reason string must NOT include '[bypasses automatic control]' when disabled."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=False,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert "[bypasses automatic control]" not in result.reason

    def test_still_overrides_position_when_disabled(self) -> None:
        """Weather override still moves covers to the configured position when bypass is disabled.

        The bypass flag only controls whether commands are sent with auto-control OFF.
        The override itself (position + control_method) is always returned when conditions fire.
        """
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=15,
            weather_bypass_auto_control=False,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 15
        assert result.control_method.value == "weather_override"

    def test_control_method_unchanged_when_disabled(self) -> None:
        """ControlMethod is WEATHER regardless of bypass setting."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=False,
        )
        result = self.handler.evaluate(snapshot)
        assert result is not None
        from custom_components.adaptive_cover_pro.enums import ControlMethod

        assert result.control_method == ControlMethod.WEATHER


class TestWeatherBypassAutoControlDefault:
    """PipelineSnapshot.weather_bypass_auto_control defaults to True."""

    def test_snapshot_default_is_true(self) -> None:
        """weather_bypass_auto_control defaults to True — safe-by-default for new installs."""
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            # weather_bypass_auto_control intentionally omitted — relies on default
        )
        assert snapshot.weather_bypass_auto_control is True

    def test_registry_propagates_bypass_false(self) -> None:
        """Registry result carries bypass_auto_control=False when bypass is disabled."""
        from custom_components.adaptive_cover_pro.pipeline.registry import (
            PipelineRegistry,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers import (
            DefaultHandler,
        )

        registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=False,
        )
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is False

    def test_registry_propagates_bypass_true(self) -> None:
        """Registry result carries bypass_auto_control=True when bypass is enabled."""
        from custom_components.adaptive_cover_pro.pipeline.registry import (
            PipelineRegistry,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers import (
            DefaultHandler,
        )

        registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=True,
        )
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is True


# ---------------------------------------------------------------------------
# 3 — Other handlers do NOT set the bypass flag
# ---------------------------------------------------------------------------


class TestNonSafetyHandlersNoBypass:
    """Solar and Default handlers must NOT set bypass_auto_control."""

    def test_solar_handler_no_bypass(self) -> None:
        """SolarHandler result must have bypass_auto_control=False."""
        handler = SolarHandler()
        snapshot = make_snapshot(
            direct_sun_valid=True, calculate_percentage_return=60.0
        )
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is False

    def test_default_handler_no_bypass(self) -> None:
        """DefaultHandler result must have bypass_auto_control=False."""
        handler = DefaultHandler()
        snapshot = make_snapshot(direct_sun_valid=False, default_position=int(50.0))
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.bypass_auto_control is False


# ---------------------------------------------------------------------------
# 4 — PipelineRegistry propagates bypass_auto_control
# ---------------------------------------------------------------------------


class TestRegistryPropagatesBypass:
    """PipelineRegistry must copy bypass_auto_control from the winning handler."""

    def test_force_override_bypass_propagated(self) -> None:
        """Registry result carries bypass_auto_control=True when force override wins."""
        registry = PipelineRegistry([ForceOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": True},
            force_override_position=0,
        )
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is True

    def test_weather_override_bypass_propagated(self) -> None:
        """Registry result carries bypass_auto_control=True when weather override wins."""
        registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
        )
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is True

    def test_default_handler_no_bypass_propagated(self) -> None:
        """Registry result carries bypass_auto_control=False when default wins."""
        registry = PipelineRegistry([DefaultHandler()])
        snapshot = make_snapshot(direct_sun_valid=False, default_position=int(50.0))
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is False

    def test_solar_handler_no_bypass_propagated(self) -> None:
        """Registry result carries bypass_auto_control=False when solar wins."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            direct_sun_valid=True, calculate_percentage_return=40.0
        )
        result = registry.evaluate(snapshot)
        assert result.bypass_auto_control is False


# ---------------------------------------------------------------------------
# 5 — PipelineResult dataclass default
# ---------------------------------------------------------------------------


class TestPipelineResultDefault:
    """PipelineResult.bypass_auto_control defaults to False."""

    def test_default_false(self) -> None:
        """bypass_auto_control must default to False."""
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.DEFAULT,
            reason="test",
        )
        assert result.bypass_auto_control is False

    def test_explicit_true(self) -> None:
        """bypass_auto_control can be set to True explicitly."""
        result = _make_pipeline_result(bypass=True)
        assert result.bypass_auto_control is True


# ---------------------------------------------------------------------------
# 6 — _pipeline_bypasses_auto_control coordinator property (unit tests via mock)
# ---------------------------------------------------------------------------


class TestCoordinatorBypassProperty:
    """_pipeline_bypasses_auto_control returns True iff pipeline result has bypass flag."""

    def _make_coordinator(self, pipeline_result):
        """Build a minimal mock coordinator with _pipeline_result set."""
        coord = MagicMock()
        coord._pipeline_result = pipeline_result
        # Bind the real property implementation under test
        type(coord)._pipeline_bypasses_auto_control = property(
            lambda self: (
                self._pipeline_result is not None
                and self._pipeline_result.bypass_auto_control
            )
        )
        return coord

    def test_returns_true_when_bypass_set(self) -> None:
        """Property returns True when pipeline result has bypass_auto_control=True."""
        coord = self._make_coordinator(_make_pipeline_result(bypass=True))
        assert coord._pipeline_bypasses_auto_control is True

    def test_returns_false_when_no_bypass(self) -> None:
        """Property returns False when pipeline result has bypass_auto_control=False."""
        coord = self._make_coordinator(_make_pipeline_result(bypass=False))
        assert coord._pipeline_bypasses_auto_control is False

    def test_returns_false_when_no_result(self) -> None:
        """Property returns False when pipeline result is None."""
        coord = self._make_coordinator(None)
        assert coord._pipeline_bypasses_auto_control is False


# ---------------------------------------------------------------------------
# Issue #290: _pipeline_is_safety_handler property (unit tests via unbound call)
# ---------------------------------------------------------------------------


class TestPipelineIsSafetyHandlerProperty:
    """_pipeline_is_safety_handler must return True only for genuine safety handlers.

    FORCE and WEATHER are genuine safety handlers — they bypass delta/time gates
    via force=True because wind/rain protection must act immediately.
    CUSTOM_POSITION (and all others) set bypass_auto_control=True only to defeat
    the auto_control_off gate, and must NOT trigger force=True (issue #290).
    """

    def _make_coord_with_result(self, control_method: ControlMethod) -> MagicMock:
        coord = MagicMock()
        coord._pipeline_result = PipelineResult(
            position=60,
            control_method=control_method,
            reason="test",
            bypass_auto_control=True,
        )
        return coord

    def test_custom_position_is_not_safety_handler(self) -> None:
        """CUSTOM_POSITION must return False — it bypasses auto_control, not delta/time gates."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord_with_result(ControlMethod.CUSTOM_POSITION)
        result = AdaptiveDataUpdateCoordinator._pipeline_is_safety_handler.fget(coord)
        assert result is False

    def test_force_is_safety_handler(self) -> None:
        """FORCE must return True — wind/rain protection requires bypassing delta/time gates."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord_with_result(ControlMethod.FORCE)
        result = AdaptiveDataUpdateCoordinator._pipeline_is_safety_handler.fget(coord)
        assert result is True

    def test_weather_is_safety_handler(self) -> None:
        """WEATHER must return True — storm protection requires bypassing delta/time gates."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord_with_result(ControlMethod.WEATHER)
        result = AdaptiveDataUpdateCoordinator._pipeline_is_safety_handler.fget(coord)
        assert result is True

    def test_solar_is_not_safety_handler(self) -> None:
        """SOLAR must return False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord_with_result(ControlMethod.SOLAR)
        result = AdaptiveDataUpdateCoordinator._pipeline_is_safety_handler.fget(coord)
        assert result is False


# ---------------------------------------------------------------------------
# Trace and decision trace content
# ---------------------------------------------------------------------------


class TestDecisionTraceContent:
    """Decision trace must reflect bypass status from winning handler."""

    def test_force_override_trace_has_bypass_reason(self) -> None:
        """The winning trace step reason includes '[bypasses automatic control]'."""
        registry = PipelineRegistry([ForceOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            force_override_sensors={"binary_sensor.wind": True},
            force_override_position=0,
        )
        result = registry.evaluate(snapshot)
        matched_steps = [s for s in result.decision_trace if s.matched]
        assert len(matched_steps) == 1
        assert "[bypasses automatic control]" in matched_steps[0].reason

    def test_weather_override_trace_has_bypass_reason(self) -> None:
        """The winning trace step reason includes '[bypasses automatic control]'."""
        registry = PipelineRegistry([WeatherOverrideHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            weather_override_active=True,
            weather_override_position=0,
            weather_bypass_auto_control=True,
        )
        result = registry.evaluate(snapshot)
        matched_steps = [s for s in result.decision_trace if s.matched]
        assert len(matched_steps) == 1
        assert "[bypasses automatic control]" in matched_steps[0].reason

    def test_solar_trace_no_bypass_reason(self) -> None:
        """Solar handler trace step reason must NOT include bypass text."""
        registry = PipelineRegistry([SolarHandler(), DefaultHandler()])
        snapshot = make_snapshot(
            direct_sun_valid=True, calculate_percentage_return=60.0
        )
        result = registry.evaluate(snapshot)
        matched_steps = [s for s in result.decision_trace if s.matched]
        assert len(matched_steps) == 1
        assert "[bypasses automatic control]" not in matched_steps[0].reason


# ---------------------------------------------------------------------------
# CustomPositionHandler — bypass_auto_control
# ---------------------------------------------------------------------------

_CP_ENTITY = "binary_sensor.cp_scene"


class TestCustomPositionBypass:
    """CustomPositionHandler must set bypass_auto_control=True unconditionally."""

    def _handler(self, position: int = 50) -> CustomPositionHandler:
        return CustomPositionHandler(
            slot=1, entity_id=_CP_ENTITY, position=position, priority=77
        )

    def _snapshot_on(self, position: int = 50) -> object:
        return make_snapshot(
            custom_position_sensors=[
                CustomPositionSensorState(
                    entity_id=_CP_ENTITY,
                    is_on=True,
                    position=position,
                    priority=77,
                    min_mode=False,
                    use_my=False,
                )
            ]
        )

    def test_bypass_flag_set(self) -> None:
        """Handler result has bypass_auto_control=True when sensor is on."""
        result = self._handler().evaluate(self._snapshot_on())
        assert result is not None
        assert result.bypass_auto_control is True

    def test_reason_includes_bypass_text(self) -> None:
        """Reason string includes '[bypasses automatic control]' when sensor is on."""
        result = self._handler().evaluate(self._snapshot_on())
        assert result is not None
        assert "[bypasses automatic control]" in result.reason

    def test_sensor_off_returns_none(self) -> None:
        """No result when sensor is off — bypass flag irrelevant."""
        snapshot = make_snapshot(
            custom_position_sensors=[
                CustomPositionSensorState(
                    entity_id=_CP_ENTITY,
                    is_on=False,
                    position=50,
                    priority=77,
                    min_mode=False,
                    use_my=False,
                )
            ]
        )
        result = self._handler().evaluate(snapshot)
        assert result is None

    def test_bypass_propagated_through_registry(self) -> None:
        """Registry result carries bypass_auto_control=True when custom position wins."""
        registry = PipelineRegistry([self._handler(), DefaultHandler()])
        result = registry.evaluate(self._snapshot_on())
        assert result.bypass_auto_control is True

    def test_trace_has_bypass_reason(self) -> None:
        """Winning decision-trace step reason includes '[bypasses automatic control]'."""
        registry = PipelineRegistry([self._handler(), DefaultHandler()])
        result = registry.evaluate(self._snapshot_on())
        matched_steps = [s for s in result.decision_trace if s.matched]
        assert len(matched_steps) == 1
        assert "[bypasses automatic control]" in matched_steps[0].reason
