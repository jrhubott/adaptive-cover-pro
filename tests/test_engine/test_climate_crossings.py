"""Tests for the pure climate-crossing helpers (issue #917).

These value-based functions are the single computation site for the four
temperature-season crossings, shared by ``ClimateProvider`` (which feeds the
Schmitt latches) and ``ClimateCoverData``'s raw fallback. Zero HA imports.

Each ``*_crossing`` returns ``(activate_met, release_cleared)``. The
unavailability values are load-bearing: they must reproduce each legacy
``ClimateCoverData`` property exactly — winter/summer/extreme fail to
``(False, True)`` (inactive, cleared → no latch); outside-high FAIL-OPENS to
``(True, False)`` (active, held → latch stays engaged).
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.engine.climate_crossings import (
    extreme_heat_crossing,
    outside_high_crossing,
    resolve_current_temperature,
    summer_warm_crossing,
    winter_crossing,
)

# ---------------------------------------------------------------------------
# resolve_current_temperature — exact port of get_current_temperature
# ---------------------------------------------------------------------------


class TestResolveCurrentTemperature:
    """resolve_current_temperature ports get_current_temperature exactly."""

    def test_switch_uses_outside(self):
        assert resolve_current_temperature(10.0, 20.0, temp_switch=True) == 10.0

    def test_no_switch_uses_inside(self):
        assert resolve_current_temperature(10.0, 20.0, temp_switch=False) == 20.0

    def test_switch_but_outside_none_falls_back_to_inside(self):
        assert resolve_current_temperature(None, 20.0, temp_switch=True) == 20.0

    def test_switch_but_outside_nonnumeric_returns_none(self):
        # Legacy returns None (does NOT fall through to inside) for a present
        # but non-numeric outside reading under temp_switch.
        assert (
            resolve_current_temperature("unavailable", 20.0, temp_switch=True) is None
        )

    def test_inside_nonnumeric_returns_none(self):
        assert resolve_current_temperature(None, "unknown", temp_switch=False) is None

    def test_both_none_returns_none(self):
        assert resolve_current_temperature(None, None, temp_switch=False) is None

    def test_string_numeric_coerced(self):
        assert resolve_current_temperature(None, "21.5", temp_switch=False) == 21.5


# ---------------------------------------------------------------------------
# winter_crossing — activate = current < temp_low; fails to (False, True)
# ---------------------------------------------------------------------------


class TestWinterCrossing:
    """winter_crossing: active below temp_low; fails to (False, True)."""

    def test_below_low_activates(self):
        assert winter_crossing(18.0, temp_low=21.0, release_threshold=None) == (
            True,
            False,
        )

    def test_at_or_above_low_inactive_blank_release(self):
        assert winter_crossing(22.0, temp_low=21.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_current_fails_inactive_cleared(self):
        assert winter_crossing(None, temp_low=21.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_threshold_fails_inactive_cleared(self):
        assert winter_crossing(18.0, temp_low=None, release_threshold=None) == (
            False,
            True,
        )

    def test_release_band_holds(self):
        # Release edge ABOVE temp_low; in-band value neither activates nor clears.
        act, cleared = winter_crossing(22.0, temp_low=21.0, release_threshold=24.0)
        assert act is False
        assert cleared is False

    def test_release_edge_clears(self):
        act, cleared = winter_crossing(24.5, temp_low=21.0, release_threshold=24.0)
        assert act is False
        assert cleared is True


# ---------------------------------------------------------------------------
# summer_warm_crossing — activate = current > temp_high; fails to (False, True)
# ---------------------------------------------------------------------------


class TestSummerWarmCrossing:
    """summer_warm_crossing: active above temp_high; fails to (False, True)."""

    def test_above_high_activates(self):
        assert summer_warm_crossing(26.0, temp_high=25.0, release_threshold=None) == (
            True,
            False,
        )

    def test_at_or_below_high_inactive(self):
        assert summer_warm_crossing(24.0, temp_high=25.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_current_fails(self):
        assert summer_warm_crossing(None, temp_high=25.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_threshold_fails(self):
        assert summer_warm_crossing(26.0, temp_high=None, release_threshold=None) == (
            False,
            True,
        )

    def test_release_band_holds(self):
        # Release edge BELOW temp_high; in-band value holds.
        act, cleared = summer_warm_crossing(
            24.0, temp_high=25.0, release_threshold=22.0
        )
        assert act is False
        assert cleared is False

    def test_release_edge_clears(self):
        act, cleared = summer_warm_crossing(
            21.0, temp_high=25.0, release_threshold=22.0
        )
        assert act is False
        assert cleared is True


# ---------------------------------------------------------------------------
# outside_high_crossing — FAIL-OPENS to (True, False)
# ---------------------------------------------------------------------------


class TestOutsideHighCrossing:
    """outside_high_crossing: fails OPEN to (True, False)."""

    def test_above_threshold_activates(self):
        assert outside_high_crossing(33.0, threshold=32.0, release_threshold=None) == (
            True,
            False,
        )

    def test_at_or_below_threshold_inactive(self):
        assert outside_high_crossing(31.0, threshold=32.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_outside_fails_open(self):
        # Legacy outside_high returns True when the outside reading is missing.
        assert outside_high_crossing(None, threshold=32.0, release_threshold=None) == (
            True,
            False,
        )

    def test_none_threshold_fails_open(self):
        assert outside_high_crossing(33.0, threshold=None, release_threshold=None) == (
            True,
            False,
        )

    def test_nonnumeric_outside_fails_open(self):
        assert outside_high_crossing(
            "unavailable", threshold=32.0, release_threshold=None
        ) == (True, False)

    def test_release_band_holds(self):
        # Release edge BELOW threshold (the reporter's fix: 30 below 32).
        act, cleared = outside_high_crossing(
            31.0, threshold=32.0, release_threshold=30.0
        )
        assert act is False
        assert cleared is False

    def test_release_edge_clears(self):
        act, cleared = outside_high_crossing(
            29.0, threshold=32.0, release_threshold=30.0
        )
        assert act is False
        assert cleared is True


# ---------------------------------------------------------------------------
# extreme_heat_crossing — feature-off/unavailable fail to (False, True)
# ---------------------------------------------------------------------------


class TestExtremeHeatCrossing:
    """extreme_heat_crossing: feature-off / unavailable fail to (False, True)."""

    def test_above_threshold_activates(self):
        assert extreme_heat_crossing(41.0, threshold=40.0, release_threshold=None) == (
            True,
            False,
        )

    def test_at_or_below_inactive(self):
        assert extreme_heat_crossing(39.0, threshold=40.0, release_threshold=None) == (
            False,
            True,
        )

    def test_none_threshold_feature_off(self):
        assert extreme_heat_crossing(41.0, threshold=None, release_threshold=None) == (
            False,
            True,
        )

    def test_none_outside_fails(self):
        assert extreme_heat_crossing(None, threshold=40.0, release_threshold=None) == (
            False,
            True,
        )

    def test_nonnumeric_outside_fails(self):
        assert extreme_heat_crossing(
            "unknown", threshold=40.0, release_threshold=None
        ) == (False, True)

    def test_release_band_holds(self):
        act, cleared = extreme_heat_crossing(
            39.0, threshold=40.0, release_threshold=37.0
        )
        assert act is False
        assert cleared is False

    def test_release_edge_clears(self):
        act, cleared = extreme_heat_crossing(
            36.0, threshold=40.0, release_threshold=37.0
        )
        assert act is False
        assert cleared is True


# ---------------------------------------------------------------------------
# Zero HA imports guard
# ---------------------------------------------------------------------------


def test_module_has_no_ha_imports():
    import custom_components.adaptive_cover_pro.engine.climate_crossings as mod

    src = mod.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "homeassistant" not in text


@pytest.mark.parametrize(
    "crossing",
    [winter_crossing, summer_warm_crossing],
)
def test_blank_release_collapses(crossing):
    """Blank release ⇒ release_cleared == not activate (instantaneous)."""
    act, cleared = crossing(100.0, 50.0, None)
    assert cleared is (not act)
