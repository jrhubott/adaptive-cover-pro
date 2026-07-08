"""Extreme-heat mode: hold position rides the shared cover-type path (issue #766).

Phase 3 of the extreme-heat work. These tests prove the extreme-heat force-hold
flows through the SAME ``get_state()`` → ``apply_snapshot_limits`` → interpolation
+ inverse-state path that summer close uses — because it is a ``ClimateRule`` in
the tables, not a parallel branch. A failure here means an accidental parallel
path leaked in.

No new production code is expected to make these pass — they exercise the
already-wired reuse.
"""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    POSITION_CLOSED,
    ClimateStrategy,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.coordinator import inverse_state
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateCoverState,
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.conftest import make_snapshot_for_cover
from tests.test_pipeline.conftest import make_snapshot


def _extreme_data(policy_name: str, *, position: int | None, is_presence: bool = True):
    """Extreme-heat climate data: outside 40° over a 35° threshold, holds ``position``."""
    return ClimateCoverData(
        temp_low=20.0,
        temp_high=25.0,
        temp_switch=False,
        policy=get_policy(policy_name),
        transparent_blind=False,
        temp_summer_outside=22.0,
        outside_temperature="40.0",
        inside_temperature="22.0",  # intermediate — not summer, not winter
        is_presence=is_presence,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        winter_close_insulation=False,
        temp_extreme_heat=35.0,
        extreme_heat_position=position,
    )


@pytest.mark.unit
def test_tilt_cover_holds_position_as_slat_percentage(tilt_cover_instance, mock_logger):
    """On a tilt cover the hold is interpreted as a slat tilt % (no cover-type branch)."""
    data = _extreme_data("cover_tilt", position=40)
    with patch.object(
        type(tilt_cover_instance), "direct_sun_valid", new_callable=PropertyMock
    ) as mock_dsv:
        mock_dsv.return_value = False
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                tilt_cover_instance, tilt_cover_instance.config.h_def
            ),
            data,
        )
        result = sh.get_state()
    assert result == 40
    assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT


@pytest.mark.unit
def test_awning_retracts_at_zero(horizontal_cover_instance, mock_logger):
    """On an awning, 0 = retracted — the same POSITION_CLOSED summer close uses."""
    data = _extreme_data("cover_awning", position=POSITION_CLOSED)
    with patch.object(
        type(horizontal_cover_instance), "direct_sun_valid", new_callable=PropertyMock
    ) as mock_dsv:
        mock_dsv.return_value = False
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                horizontal_cover_instance,
                horizontal_cover_instance.config.h_def,
                cover_type="cover_awning",
            ),
            data,
        )
        result = sh.get_state()
    assert result == POSITION_CLOSED
    assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT


@pytest.mark.unit
def test_min_position_clamp_behaves_like_summer_close(
    vertical_cover_instance, mock_logger
):
    """An always-enforce min floor clamps the extreme-heat hold, exactly as summer close.

    Mirrors ``test_get_state_min_position_clamping``: min_pos=30 with
    enable_min_position=False (always enforce) lifts a 0 % hold to 30.
    """
    vertical_cover_instance.min_pos = 30
    vertical_cover_instance.min_pos_bool = False  # always enforce
    data = _extreme_data("cover_blind", position=0)
    with patch.object(
        type(vertical_cover_instance), "direct_sun_valid", new_callable=PropertyMock
    ) as mock_dsv:
        mock_dsv.return_value = False
        sh = ClimateCoverState(
            make_snapshot_for_cover(
                vertical_cover_instance, vertical_cover_instance.config.h_def
            ),
            data,
        )
        result = sh.get_state()
    assert result == 30
    assert sh.climate_strategy == ClimateStrategy.EXTREME_HEAT


def _make_readings(**overrides):
    base = {
        "outside_temperature": 40.0,
        "inside_temperature": 22.0,
        "is_presence": True,
        "is_sunny": True,
        "lux_below_threshold": False,
        "irradiance_below_threshold": False,
        "cloud_coverage_above_threshold": False,
    }
    base.update(overrides)
    return ClimateReadings(**base)


def _make_options(**overrides):
    base = {
        "temp_low": 18.0,
        "temp_high": 26.0,
        "temp_switch": False,
        "transparent_blind": False,
        "temp_summer_outside": None,
        "cloud_suppression_enabled": False,
        "winter_close_insulation": False,
        "temp_extreme_heat": 35.0,
        "extreme_heat_position": 30,
    }
    base.update(overrides)
    return ClimateOptions(**base)


@pytest.mark.unit
def test_extreme_heat_result_is_ordinary_not_a_bypass_path():
    """The extreme-heat PipelineResult rides the standard interpolation/inverse path.

    It carries none of the short-circuit flags the coordinator's ``state``
    property honors (``is_safety`` / ``floor_clamp_applied`` /
    ``bypass_auto_control``), so its position is post-processed like every other
    climate position — no parallel path.
    """
    from unittest.mock import MagicMock

    cover = MagicMock()
    cover.direct_sun_valid = False
    cover.valid = False
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config

    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_make_readings(),
        climate_options=_make_options(),
    )
    result = ClimateHandler().evaluate(snap)
    assert result is not None
    assert result.control_method == ControlMethod.EXTREME_HEAT
    assert result.position == 30
    # None of the short-circuit flags are set → coordinator.state will
    # interpolate + inverse this position exactly like summer/winter/glare.
    assert result.is_safety is False
    assert result.floor_clamp_applied is False
    assert result.bypass_auto_control is False


@pytest.mark.unit
def test_inverse_state_inverts_the_hold_like_any_other_position():
    """Documents that the hold is inverted by the shared inverse_state() transform."""
    # A 30 % hold on an inverse-state cover is dispatched as 70 %.
    assert inverse_state(30) == 70


@pytest.mark.unit
def test_diagnostics_labels_extreme_heat():
    """Diagnostics maps the new strategy/method to human labels (#766)."""
    from custom_components.adaptive_cover_pro.const import ControlStatus
    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        _CLIMATE_STRATEGY_LABELS,
        _METHOD_TO_STATUS,
    )

    assert _CLIMATE_STRATEGY_LABELS[ClimateStrategy.EXTREME_HEAT] == "Extreme Heat"
    assert _METHOD_TO_STATUS[ControlMethod.EXTREME_HEAT] == ControlStatus.ACTIVE
