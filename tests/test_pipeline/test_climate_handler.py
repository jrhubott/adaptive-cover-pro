"""Tests for ClimateHandler — full climate strategy."""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import (
    DEFAULT_TRACKING_SEASONS,
    ClimateStrategy,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    ClimateTempFlags,
)
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
    temp_extreme_heat=None,
    extreme_heat_position=None,
    tracking_seasons=frozenset(DEFAULT_TRACKING_SEASONS),
) -> ClimateOptions:
    return ClimateOptions(
        temp_low=temp_low,
        temp_high=temp_high,
        temp_switch=temp_switch,
        transparent_blind=transparent_blind,
        temp_summer_outside=temp_summer_outside,
        cloud_suppression_enabled=False,
        winter_close_insulation=winter_close_insulation,
        temp_extreme_heat=temp_extreme_heat,
        extreme_heat_position=extreme_heat_position,
        tracking_seasons=tracking_seasons,
    )


def _make_blind_cover(
    direct_sun_valid=True,
):
    """Build a mock vertical-blind cover for climate tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.calculate_raw_percentage = MagicMock(return_value=60.0)
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


def _make_awning_cover(direct_sun_valid=True):
    """Build a mock horizontal-awning cover for climate tests (cover_type='cover_awning')."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.calculate_raw_percentage = MagicMock(return_value=60.0)
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


class TestClimateHandlerSmoothedFlags:
    """_build_climate_data threads snapshot.climate_temp_flags (issue #917)."""

    handler = ClimateHandler()

    def test_smoothed_flags_win_over_raw_temps(self) -> None:
        """Raw temps read cold (winter) but smoothed flags force summer."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            # Raw inside 10 < temp_low 18 → raw is_winter. temp_high 26.
            climate_readings=_make_readings(inside_temperature=10.0),
            climate_options=_make_options(
                temp_low=18.0, temp_high=26.0, transparent_blind=True
            ),
            climate_temp_flags=ClimateTempFlags(
                winter=False, summer_warm=True, outside_high=True, extreme_heat=False
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER

    def test_flags_none_uses_raw_behaviour(self) -> None:
        """With climate_temp_flags None the handler classifies from raw temps."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=10.0),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
            climate_temp_flags=None,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER

    def test_is_summer_needs_both_smoothed_warm_and_outside(self) -> None:
        """summer_warm True but outside_high False ⇒ not summer (composite)."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0, transparent_blind=True),
            climate_temp_flags=ClimateTempFlags(
                winter=False,
                summer_warm=True,
                outside_high=False,
                extreme_heat=False,
            ),
        )
        result = self.handler.evaluate(snap)
        # Not summer → transparent blind no longer force-closes; defers to glare.
        assert result is None or result.control_method != ControlMethod.SUMMER


class TestClimateHandlerSummerStrategy:
    """Summer cooling: temperature above high threshold."""

    handler = ClimateHandler()

    def test_summer_transparent_blind_uses_summer_control_method(self) -> None:
        """Summer + transparent blind + presence → SUMMER_COOLING (close to 0%)."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0, transparent_blind=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER

    def test_summer_non_transparent_blind_defers(self) -> None:
        """Summer + non-transparent blind + presence → GLARE_CONTROL → climate defers."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0, transparent_blind=False),
        )
        result = self.handler.evaluate(snap)
        assert result is None

    def test_summer_sets_climate_state_on_result(self) -> None:
        """climate_state is populated on PipelineResult when ClimateHandler fires."""
        cover = _make_blind_cover()
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(temp_high=26.0, transparent_blind=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_state is not None
        assert isinstance(result.climate_state, int)


class TestClimateHandlerExtremeHeat:
    """Extreme-heat force-hold: outside temp above the extreme threshold (#766)."""

    handler = ClimateHandler()

    def test_extreme_heat_uses_extreme_heat_control_method(self) -> None:
        """Outside temp above threshold → EXTREME_HEAT method + configured hold."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            # Intermediate inside temp: neither summer nor winter would fire.
            climate_readings=_make_readings(
                inside_temperature=22.0, outside_temperature=40.0
            ),
            climate_options=_make_options(
                temp_low=18.0,
                temp_high=26.0,
                temp_extreme_heat=35.0,
                extreme_heat_position=30,
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.EXTREME_HEAT
        assert result.position == 30
        assert result.climate_strategy == ClimateStrategy.EXTREME_HEAT
        assert "extreme heat" in result.reason.lower()

    def test_extreme_heat_pre_empts_winter_label(self) -> None:
        """A cold inside (winter) but hot outside labels EXTREME_HEAT, not WINTER."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=15.0, outside_temperature=40.0
            ),
            climate_options=_make_options(
                temp_low=18.0,
                temp_high=26.0,
                temp_extreme_heat=35.0,
                extreme_heat_position=30,
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.EXTREME_HEAT

    def test_disabled_when_threshold_unset(self) -> None:
        """No extreme threshold configured → normal winter label resumes."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=15.0, outside_temperature=40.0
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER


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
    """Intermediate season: climate defers to GlareZone/Solar handlers."""

    handler = ClimateHandler()

    def test_glare_control_defers_to_pipeline(self) -> None:
        """Comfortable temperature + presence + sun valid → climate returns None (defers)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is None

    def test_no_presence_intermediate_returns_default(self) -> None:
        """Comfortable temperature + no presence → climate still wins (returns default, not None)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0, is_presence=False),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_low_light_does_not_defer(self) -> None:
        """Presence + intermediate + no sun → LOW_LIGHT strategy, climate wins (not None)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=22.0, is_presence=True, is_sunny=False
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_tracking_season_gate_uses_default_control_method(self) -> None:
        """Intermediate sunny branch blocked by season gate reports DEFAULT."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            default_position=42,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0),
            climate_options=_make_options(
                temp_low=18.0,
                temp_high=26.0,
                tracking_seasons=frozenset({"summer"}),
            ),
        )
        result = self.handler.evaluate(snap)

        assert result is not None
        assert result.control_method == ControlMethod.DEFAULT
        assert result.climate_strategy == ClimateStrategy.TRACKING_SEASON_GATE
        assert (
            result.reason
            == "climate mode active (default: tracking off this season) — position 42%"
        )

    def test_describe_skip_defer_reason(self) -> None:
        """describe_skip returns defer message when climate mode is on and deferred."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        from custom_components.adaptive_cover_pro.reason_i18n import render_en

        assert "deferred" in render_en(self.handler.describe_skip(snap)).lower()


class TestClimateHandlerMetadata:
    """Test ClimateHandler metadata and behavior."""

    handler = ClimateHandler()

    def test_result_includes_climate_strategy(self) -> None:
        """PipelineResult.climate_strategy is populated when climate wins."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.climate_strategy is not None

    def test_winning_reason_payload_carries_season_fragment(self) -> None:
        """The winning result's reason_payload is CLIMATE_ACTIVE with a nested season fragment."""
        from custom_components.adaptive_cover_pro.const import ReasonCode
        from custom_components.adaptive_cover_pro.reason_i18n import Reason

        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        payload = result.reason_payload
        assert payload is not None
        assert payload.code == ReasonCode.CLIMATE_ACTIVE
        assert payload.params["position"] == result.position
        season = payload.params["season"]
        assert isinstance(season, Reason)
        # Cold inside (15 < temp_low 18) → winter season fragment.
        assert season.code == ReasonCode.FRAGMENT_SEASON_WINTER

    def test_winning_reason_payload_summer_fragment(self) -> None:
        """Hot inside above temp_high labels the season fragment as summer."""
        from custom_components.adaptive_cover_pro.const import ReasonCode
        from custom_components.adaptive_cover_pro.reason_i18n import Reason

        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0),
            climate_options=_make_options(
                temp_low=18.0, temp_high=26.0, transparent_blind=True
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER
        payload = result.reason_payload
        assert payload is not None
        assert payload.code == ReasonCode.CLIMATE_ACTIVE
        season = payload.params["season"]
        assert isinstance(season, Reason)
        assert season.code == ReasonCode.FRAGMENT_SEASON_SUMMER

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
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0),
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
            climate_options=_make_options(temp_high=26.0, transparent_blind=True),
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

    def test_insulation_disabled_defers_with_presence(self) -> None:
        """Winter + no sun in FOV + insulation DISABLED + presence → climate defers."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=False),
        )
        result = self.handler.evaluate(snap)
        # Winter + no sun + insulation off + presence → GLARE_CONTROL → climate defers
        assert result is None

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
        from custom_components.adaptive_cover_pro.const import ClimateStrategy

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
        """Summer + insulation enabled → climate defers (GLARE_CONTROL), not insulation close."""
        cover = _make_blind_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0, is_presence=True),
            climate_options=_make_options(temp_high=26.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        # Summer + non-transparent + presence → GLARE_CONTROL → climate defers
        # (winter_close_insulation has no effect in summer)
        assert result is None

    def test_insulation_no_effect_in_intermediate_season(self) -> None:
        """Intermediate temp + insulation enabled → climate defers, not insulation close."""
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
        # Intermediate + presence → GLARE_CONTROL → climate defers
        # (winter_close_insulation has no effect in non-winter)
        assert result is None

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
        from custom_components.adaptive_cover_pro.reason_i18n import render_en

        snap = self._active_snap(in_time_window=False)
        reason = render_en(self.handler.describe_skip(snap))
        assert (
            "time window" in reason.lower()
        ), f"Expected 'time window' in describe_skip reason but got: {reason!r}"

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


class TestClimateHandlerPositionLimits:
    """Climate handler must not be clamped by sun-only position limits."""

    handler = ClimateHandler()

    def test_sun_only_max_not_applied_to_winter_heating_position(self) -> None:
        """Regression #105: sun-only max limit must NOT clamp winter heating position.

        Winter heating returns 100% (fully open). A sun-only max limit of 26%
        should not clamp it — climate mode is not solar tracking.
        """
        cover = _make_blind_cover(direct_sun_valid=True)
        cover.valid = True
        cover.config.max_pos = 26
        cover.config.max_pos_sun_only = True  # "during sun tracking only"

        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=15.0,  # cold → winter heating
                is_presence=True,
                is_sunny=True,
            ),
            climate_options=_make_options(
                temp_low=18.0,  # inside below low → winter
                temp_high=26.0,
            ),
            default_position=50,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER
        assert result.position == 100, (
            f"Expected winter heating position 100 but got {result.position}. "
            "Sun-only max limit must not clamp climate handler output."
        )


# ---------------------------------------------------------------------------
# ClimateHandler.contribute() — surfaces climate_data when evaluate() defers
# ---------------------------------------------------------------------------


class TestClimateHandlerContribute:
    """ClimateHandler.contribute() must expose climate_data even when evaluate() returns None.

    Issue #240: The GLARE_CONTROL defer path returns None from evaluate() so the
    pipeline falls through to GlareZone/Solar.  contribute() is the hook the
    registry uses to harvest climate_data regardless of the evaluate() outcome.
    """

    handler = ClimateHandler()

    def test_contribute_returns_climate_data_when_deferring(self) -> None:
        """Intermediate temp + presence + sunny → evaluate() is None but contribute() yields climate_data."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=22.0,
                is_presence=True,
                is_sunny=True,
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        assert (
            self.handler.evaluate(snap) is None
        ), "sanity: should defer on GLARE_CONTROL"
        contrib = self.handler.contribute(snap)
        assert "climate_data" in contrib
        assert isinstance(contrib["climate_data"], ClimateCoverData)
        assert contrib["climate_data"].is_presence is True
        assert contrib["climate_data"].is_sunny is True

    def test_contribute_returns_empty_when_climate_mode_off(self) -> None:
        """Climate mode disabled → contribute() returns {}."""
        snap = make_snapshot(climate_mode_enabled=False)
        assert self.handler.contribute(snap) == {}

    def test_contribute_returns_empty_when_readings_none(self) -> None:
        """Missing readings → contribute() returns {}."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=None,
            climate_options=_make_options(),
        )
        assert self.handler.contribute(snap) == {}

    def test_contribute_returns_empty_when_options_none(self) -> None:
        """Missing options → contribute() returns {}."""
        snap = make_snapshot(
            climate_mode_enabled=True,
            climate_readings=_make_readings(),
            climate_options=None,
        )
        assert self.handler.contribute(snap) == {}

    def test_contribute_returns_empty_outside_time_window(self) -> None:
        """Outside the time window → contribute() returns {}."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=22.0),
            climate_options=_make_options(),
            in_time_window=False,
        )
        assert self.handler.contribute(snap) == {}

    def test_contribute_climate_data_consistent_with_evaluate_when_handler_wins(
        self,
    ) -> None:
        """When evaluate() wins, contribute() returns the same climate_data (single source of truth)."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        contrib = self.handler.contribute(snap)
        assert result is not None, "winter should win"
        assert "climate_data" in contrib
        assert (
            contrib["climate_data"].inside_temperature
            == result.climate_data.inside_temperature
        )
        assert contrib["climate_data"].is_winter == result.climate_data.is_winter


# ---------------------------------------------------------------------------
# Issue #337 — Climate handler must invert 0/100 scalars for awnings
# ---------------------------------------------------------------------------


class TestClimateHandlerAwningSemantics:
    """Climate handler hardcoded blind-semantic 100/0; awnings need inverse scalars.

    For awnings: 100=extended=sun blocked, 0=retracted=sun allowed.
    For blinds:  100=raised=sun allowed,   0=lowered=sun blocked.
    Tilt covers use angle math and are unaffected.
    """

    handler = ClimateHandler()

    def test_awning_winter_heating_with_presence_retracts(self) -> None:
        """Winter + sun-in-FOV + presence + awning → retract (0%) so sun reaches window."""
        cover = _make_awning_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_awning",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.WINTER
        assert (
            result.position == 0
        ), f"Awning winter heating must retract (0%); got {result.position}"

    def test_awning_winter_heating_without_presence_retracts(self) -> None:
        """Winter + sun-in-FOV + no presence + awning → retract (0%)."""
        cover = _make_awning_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_awning",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=False),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.position == 0
        ), f"Awning winter heating (no presence) must retract (0%); got {result.position}"

    def test_awning_summer_cooling_transparent_with_presence_extends(self) -> None:
        """Summer + transparent + presence + awning → extend (100%) to block sun."""
        cover = _make_awning_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_awning",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0, is_presence=True),
            climate_options=_make_options(temp_high=26.0, transparent_blind=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SUMMER
        assert (
            result.position == 100
        ), f"Awning summer cooling must extend (100%); got {result.position}"

    def test_awning_summer_cooling_without_presence_extends(self) -> None:
        """Summer + no presence + awning → extend (100%) to block sun."""
        cover = _make_awning_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_awning",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=30.0, is_presence=False),
            climate_options=_make_options(temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.position == 100
        ), f"Awning summer cooling (no presence) must extend (100%); got {result.position}"

    def test_awning_winter_insulation_stays_at_zero(self) -> None:
        """Winter + no sun-in-FOV + insulation + awning → 0% (retracted = closed for awnings).

        0% is correct for both blinds and awnings for insulation: blind=lowered, awning=retracted.
        This test pins the scalar so a future refactor cannot accidentally invert it.
        """
        cover = _make_awning_cover(direct_sun_valid=False)
        cover.valid = False
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_awning",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0, winter_close_insulation=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.position == 0
        ), f"Awning insulation must stay retracted (0%); got {result.position}"

    def test_blind_winter_heating_still_returns_100(self) -> None:
        """Regression guard: blind winter heating must still return 100% after the awning fix."""
        cover = _make_blind_cover(direct_sun_valid=True)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_blind",
            climate_mode_enabled=True,
            climate_readings=_make_readings(inside_temperature=15.0, is_presence=True),
            climate_options=_make_options(temp_low=18.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.position == 100
        ), f"Blind winter heating must still raise (100%) after awning fix; got {result.position}"


# ---------------------------------------------------------------------------
# Issue #33 — ClimateHandler must not emit ControlMethod.SOLAR on low-light branch
# ---------------------------------------------------------------------------


class TestClimateHandlerControlMethodOnLowLightBranch:
    """Low-light / no-sun branch of the climate handler must emit ControlMethod.DEFAULT.

    Before the fix, the else-branch in ClimateHandler.evaluate() unconditionally
    set method=ControlMethod.SOLAR even when the strategy was LOW_LIGHT. This
    caused VenetianPolicy.post_pipeline_resolve to synthesise a tilt from the
    still-drifting sun azimuth, triggering tilt commands every ~4 minutes
    overnight (issue #33).
    """

    handler = ClimateHandler()

    def test_low_light_emits_default_not_solar(self) -> None:
        """Presence + intermediate temp + is_sunny=False → ControlMethod.DEFAULT."""
        cover = _make_blind_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=22.0,
                is_presence=True,
                is_sunny=False,
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.control_method == ControlMethod.DEFAULT
        ), f"Low-light branch must emit DEFAULT, not {result.control_method}"

    def test_no_presence_low_light_emits_default_not_solar(self) -> None:
        """No presence + intermediate temp + is_sunny=False → ControlMethod.DEFAULT."""
        cover = _make_blind_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=22.0,
                is_presence=False,
                is_sunny=False,
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.control_method == ControlMethod.DEFAULT
        ), f"Low-light (no presence) branch must emit DEFAULT, not {result.control_method}"

    def test_lux_below_threshold_emits_default_not_solar(self) -> None:
        """Presence + intermediate temp + lux_below_threshold=True → ControlMethod.DEFAULT."""
        cover = _make_blind_cover(direct_sun_valid=False)
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                inside_temperature=22.0,
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=True,
            ),
            climate_options=_make_options(temp_low=18.0, temp_high=26.0),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert (
            result.control_method == ControlMethod.DEFAULT
        ), f"Lux-below-threshold branch must emit DEFAULT, not {result.control_method}"


# ---------------------------------------------------------------------------
# Issue #373 — full-pipeline coverage for tilt MODE2 + min_pos=50 GLARE_CONTROL
# ---------------------------------------------------------------------------


def _make_tilt_mode2_cover(*, gamma_deg: float, valid: bool, min_pos: int):
    """Mock AdaptiveTiltCover for the issue-#373 GLARE_CONTROL pipeline test."""
    from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover

    cover = MagicMock(spec=AdaptiveTiltCover)
    cover.direct_sun_valid = valid
    cover.valid = valid
    cover.calculate_percentage = MagicMock(return_value=50.0)
    cover.calculate_raw_percentage = MagicMock(return_value=50.0)
    cover.calculate_position = MagicMock(return_value=90.0)
    cover.gamma = gamma_deg  # SunGeometry.gamma is in degrees
    cover.beta = 0.0
    cover.mode = "mode2"
    config = MagicMock()
    config.min_pos = min_pos
    config.max_pos = 100
    config.min_pos_sun_only = False  # always enforce
    config.max_pos_sun_only = False
    cover.config = config
    return cover


class TestIssue373PipelineGlareControl:
    """End-to-end ClimateHandler verification for tilt MODE2 + min_pos=50."""

    handler = ClimateHandler()

    def test_climate_tilt_mode2_glare_control_with_inverse_state_min_pos_50(
        self,
    ) -> None:
        """Tilt MODE2 + min_pos=50 + sun out of FOV + summer climate → GLARE_CONTROL.

        Pre-fix the raw helper output was 44 → clamped to 50 (horizontal floor).
        Post-fix with positive gamma the helper returns (180-80)/180*100 ≈ 56,
        which survives the clamp and yields a meaningful blocking position.

        ``inverse_state`` is applied above the handler in coordinator.py, so
        this test asserts the un-inverted handler output.  The companion
        regression in tests/test_climate_cover_state.py covers the same code
        path at the ``ClimateCoverState`` layer; this one anchors the same
        invariant at the ``ClimateHandler.evaluate()`` boundary so a future
        snapshot/handler refactor can't silently regress it.
        """
        from custom_components.adaptive_cover_pro.const import ClimateStrategy

        cover = _make_tilt_mode2_cover(gamma_deg=90.0, valid=False, min_pos=50)
        snap = make_snapshot(
            cover=cover,
            cover_type="cover_tilt",
            climate_mode_enabled=True,
            climate_readings=_make_readings(
                outside_temperature=30.0,
                inside_temperature=27.0,
                is_presence=True,
                is_sunny=True,
                irradiance_below_threshold=False,
                lux_below_threshold=False,
            ),
            climate_options=_make_options(
                temp_low=18.0,
                temp_high=26.0,
                temp_summer_outside=22.0,
            ),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        # Post-fix: helper returns 56 (positive hemisphere, MODE2 GLARE_CONTROL
        # for angle=80, gamma>=0). 56 > 50 so the min_pos=50 clamp doesn't fire.
        assert result.position != 50, (
            f"Position must escape the horizontal floor (50%) — pre-fix was "
            f"clamped here. Got {result.position}."
        )
        assert result.position > 50, (
            f"Positive-gamma hemisphere must yield > 50 (blocking direction), "
            f"got {result.position}."
        )
        assert result.climate_strategy == ClimateStrategy.GLARE_CONTROL
