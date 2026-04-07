"""Tests for ClimateHandler — full climate strategy."""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot


def _make_readings(
    *,
    outside_temperature=None,
    inside_temperature=25.0,
    is_presence=True,
    is_sunny=True,
    lux_below_threshold=False,
    irradiance_below_threshold=False,
    cloud_coverage_above_threshold=False,
) -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=outside_temperature,
        inside_temperature=inside_temperature,
        is_presence=is_presence,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
    )


def _make_options(
    *,
    temp_low=18.0,
    temp_high=26.0,
    temp_switch=False,
    transparent_blind=False,
    temp_summer_outside=None,
    winter_close_insulation=False,
) -> ClimateOptions:
    return ClimateOptions(
        temp_low=temp_low,
        temp_high=temp_high,
        temp_switch=temp_switch,
        transparent_blind=transparent_blind,
        temp_summer_outside=temp_summer_outside,
        cloud_suppression_enabled=False,
        winter_close_insulation=winter_close_insulation,
    )


def _make_blind_cover(
    direct_sun_valid=True,
):
    """Build a mock cover for climate tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


class TestClimateHandlerGating:
    """Test that ClimateHandler respects enable/disable conditions."""

    handler = ClimateHandler()

    def test_returns_none_when_climate_disabled(self) -> None:
        """Climate disabled → handler returns None."""
        snap = make_snapshot(climate_mode_enabled=False)
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_readings(self) -> None:
        """Missing climate readings → handler returns None."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=None,
            climate_options=_make_options(),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_options(self) -> None:
        """Missing climate options → handler returns None."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_make_readings(),
            climate_options=None,
        )
        assert self.handler.evaluate(snap) is None


class TestClimateHandlerSummerStrategy:
    """Summer cooling: temperature above high threshold."""

    handler = ClimateHandler()

    def test_summer_uses_summer_control_method(self) -> None:
        """High inside temperature → SUMMER control method."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER

    def test_summer_sets_climate_state_on_result(self) -> None:
        """climate_state is populated on PipelineResult when ClimateHandler fires."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_state is not None
        assert isinstance(result.climate_state, int)


class TestClimateHandlerWinterStrategy:
    """Winter heating: temperature below low threshold."""

    handler = ClimateHandler()

    def test_winter_uses_winter_control_method(self) -> None:
        """Low inside temperature → WINTER control method."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER


class TestClimateHandlerGlareControl:
    """Intermediate: glare control (neither summer nor winter)."""

    handler = ClimateHandler()

    def test_intermediate_uses_solar_method(self) -> None:
        """Comfortable temperature → SOLAR method (glare control)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SOLAR


class TestClimateHandlerMetadata:
    """Test ClimateHandler metadata and behavior."""

    handler = ClimateHandler()

    def test_result_includes_climate_strategy(self) -> None:
        """PipelineResult.climate_strategy is populated."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_strategy is not None

    def test_priority_is_50(self) -> None:
        """ClimateHandler has priority 50."""
        assert ClimateHandler.priority == 50

    def test_name(self) -> None:
        """ClimateHandler name is 'climate'."""
        assert ClimateHandler.name == "climate"

    def test_climate_data_populated_on_result(self) -> None:
        """PipelineResult.climate_data is a ClimateCoverData when ClimateHandler fires."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_data is not None
        assert isinstance(result.climate_data, ClimateCoverData)

    def test_climate_data_reflects_readings(self) -> None:
        """climate_data on result carries the actual sensor readings."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=30.0,
                is_presence=True,
                is_sunny=True,
            ),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        cd = result.climate_data
        assert cd is not None
        assert cd.is_summer is True
        assert cd.is_presence is True
        assert cd.is_sunny is True

    def test_climate_data_none_when_handler_skipped(self) -> None:
        """climate_data is None when ClimateHandler does not fire (mode off)."""
        snap = make_snapshot(climate_mode_enabled=False)
        result = self.handler.evaluate(snap)
        assert result is None  # handler returns None — no PipelineResult at all


class TestWinterInsulation:
    """Tests for the winter insulation feature (Issue #29).

    When winter_close_insulation=True and it is winter and the sun is NOT
    in the window's FOV, the cover should close (0%) for heat retention.
    Priority: winter heating (sun in FOV) > winter insulation (sun not in FOV).
    """

    handler = ClimateHandler()

    # ------------------------------------------------------------------
    # normal_with_presence
    # ------------------------------------------------------------------

    def test_insulation_closes_cover_with_presence(self) -> None:
        """Winter + no sun in FOV + insulation enabled + presence → close (0%)."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 0

    def test_insulation_disabled_uses_default_with_presence(self) -> None:
        """Winter + no sun in FOV + insulation DISABLED + presence → glare/default strategy."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=False),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        # Insulation is off → strategy is GLARE_CONTROL (not WINTER_INSULATION)
        from custom_components.adaptive_cover_pro.enums import ClimateStrategy

        assert result.climate_strategy != ClimateStrategy.WINTER_INSULATION

    def test_winter_heating_takes_priority_over_insulation_with_presence(self) -> None:
        """Winter + sun in FOV → open (100%), not closed for insulation."""
        cover = _make_blind_cover(direct_sun_valid=True)
        cover.valid = True
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 100

    # ------------------------------------------------------------------
    # normal_without_presence
    # ------------------------------------------------------------------

    def test_insulation_closes_cover_without_presence(self) -> None:
        """Winter + no sun in FOV + insulation enabled + no presence → close (0%)."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=False),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 0

    def test_insulation_disabled_without_presence_uses_default(self) -> None:
        """Winter + no sun in FOV + insulation DISABLED + no presence → LOW_LIGHT strategy."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=False),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=False),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        # Insulation is off → strategy is LOW_LIGHT (not WINTER_INSULATION)
        from custom_components.adaptive_cover_pro.enums import ClimateStrategy

        assert result.climate_strategy != ClimateStrategy.WINTER_INSULATION

    def test_winter_heating_takes_priority_without_presence(self) -> None:
        """Winter + sun in FOV → open (100%), even when insulation is enabled."""
        cover = _make_blind_cover(direct_sun_valid=True)
        cover.valid = True
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=False),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 100

    # ------------------------------------------------------------------
    # Not-winter: insulation has no effect
    # ------------------------------------------------------------------

    def test_insulation_no_effect_in_summer(self) -> None:
        """Summer + insulation enabled → summer cooling logic, not insulation."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0, is_presence=True),
            climate_options=_make_options(temp_high=26.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        # Summer cooling with transparent_blind=False → solar position, not 0
        assert result.control_method == ControlMethod.SUMMER

    def test_insulation_no_effect_in_intermediate_season(self) -> None:
        """Intermediate temp + insulation enabled → glare/default, not closed."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0, is_presence=True),
            climate_options=_make_options(
                temp_low=18.0, temp_high=26.0, winter_close_insulation=True
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SOLAR

    # ------------------------------------------------------------------
    # climate mode off: insulation has no effect
    # ------------------------------------------------------------------

    def test_insulation_no_effect_when_climate_mode_off(self) -> None:
        """Climate mode disabled → handler skips entirely regardless of insulation."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=False,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is None


# ---------------------------------------------------------------------------
# Issue #145 — ClimateHandler must respect in_time_window
# ---------------------------------------------------------------------------


class TestClimateHandlerTimeWindow:
    """ClimateHandler must return None when outside the configured time window.

    Before the fix, ClimateHandler ignored ``snapshot.in_time_window``, which
    caused covers to move based on temperature strategy (e.g. full-open for
    winter heating) even when the user had configured start/end time limits.
    """

    handler = ClimateHandler()

    def _active_snap(self, *, in_time_window: bool) -> object:
        """Build a snapshot that would normally trigger climate action."""
        cover = _make_blind_cover(direct_sun_valid=True)
        cover.valid = True
        return make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=15.0,  # winter — would normally open to 100%
                is_presence=True,
            ),
            climate_options=_make_options(temp_low=18.0),
            in_time_window=in_time_window,
        )

    def test_returns_none_outside_time_window(self) -> None:
        """Climate handler must return None when in_time_window=False."""
        snap = self._active_snap(in_time_window=False)
        result = self.handler.evaluate(snap)
        assert result is None, (
            "ClimateHandler should return None outside the time window "
            f"but returned {result}"
        )

    def test_returns_result_inside_time_window(self) -> None:
        """Climate handler must return a result when in_time_window=True."""
        snap = self._active_snap(in_time_window=True)
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_describe_skip_outside_window(self) -> None:
        """describe_skip() should mention 'time window' when outside window."""
        snap = self._active_snap(in_time_window=False)
        reason = self.handler.describe_skip(snap)
        assert "time window" in reason.lower(), (
            f"Expected 'time window' in describe_skip reason but got: {reason!r}"
        )

    def test_summer_returns_none_outside_window(self) -> None:
        """Summer cooling must also be gated by time window."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=30.0,
                is_presence=True,
                is_sunny=True,
            ),
            climate_options=_make_options(
                temp_high=26.0,
                temp_summer_outside=20.0,
            ),
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None
