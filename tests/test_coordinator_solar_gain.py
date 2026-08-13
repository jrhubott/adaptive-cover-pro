"""``coordinator.glass_area`` and the solar-gain context wiring (#1237).

The area term is the one input the estimate cannot derive for every cover type,
so its resolution order is the contract: an explicit ``glass_area`` override
wins for EVERY cover type, the policy's height × width answers for the five
that carry both dimensions, and everything else says so out loud rather than
guessing a number.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_GLASS_AREA
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)

pytestmark = pytest.mark.unit


_WINDOW = {"window_height": 2.0, "window_width": 1.5}


def _coordinator(cover_type: str = "cover_blind", options: dict | None = None):
    """Coordinator stub carrying only the seams ``glass_area`` reads.

    Same pattern as ``tests/test_coordinator_solar_transmittance.py``: a
    ``MagicMock`` for the surrounding object with the REAL unbound method bound
    onto it, so the code under test is the shipped code.
    """
    coord = MagicMock()
    coord._policy = get_policy(cover_type)
    coord.config_entry.options = dict(options or {})
    coord._config_service = ConfigurationService(
        hass=MagicMock(),
        config_entry=coord.config_entry,
        logger=MagicMock(),
        cover_type=cover_type,
        temp_toggle=None,
        lux_toggle=None,
        irradiance_toggle=None,
    )
    coord.glass_area = types.MethodType(AdaptiveDataUpdateCoordinator.glass_area, coord)
    return coord


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------


def _assert_area(result, expected_m2, expected_source) -> None:
    if expected_m2 is None:
        assert result.area_m2 is None
    else:
        assert result.area_m2 == pytest.approx(expected_m2)
    assert result.source == expected_source


def test_derives_height_times_width_when_the_policy_can_answer() -> None:
    _assert_area(_coordinator(options=_WINDOW).glass_area(), 3.0, "derived")


def test_configured_override_wins_over_the_derived_area() -> None:
    coord = _coordinator(options={**_WINDOW, CONF_GLASS_AREA: 2.4})
    _assert_area(coord.glass_area(), 2.4, "configured")


@pytest.mark.parametrize(
    "cover_type", ["cover_awning", "cover_tilt", "cover_louvered_roof"]
)
def test_override_works_for_cover_types_that_cannot_derive(cover_type: str) -> None:
    """The override lands ABOVE the policy hook, so it reaches every type."""
    coord = _coordinator(cover_type, {CONF_GLASS_AREA: 4.5})
    _assert_area(coord.glass_area(), 4.5, "configured")


@pytest.mark.parametrize(
    "cover_type", ["cover_awning", "cover_tilt", "cover_sliding_curtain"]
)
def test_unknown_when_nothing_can_answer(cover_type: str) -> None:
    _assert_area(_coordinator(cover_type, dict(_WINDOW)).glass_area(), None, "unknown")


def test_unknown_when_the_window_dimensions_are_missing() -> None:
    _assert_area(_coordinator(options={}).glass_area(), None, "unknown")


def test_the_result_is_a_named_pair_not_a_positional_tuple() -> None:
    """Two same-typed-ish halves that are trivial to swap — name them."""
    import dataclasses

    result = _coordinator(options=_WINDOW).glass_area()
    assert dataclasses.is_dataclass(result)
    assert {f.name for f in dataclasses.fields(result)} == {"area_m2", "source"}


# ---------------------------------------------------------------------------
# The override must be a real number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored", [0, -1.0, "big", None])
def test_an_unusable_override_falls_back_to_the_derived_area(stored) -> None:
    """A cleared or hand-edited value must not report 0 m² as a measurement."""
    coord = _coordinator(options={**_WINDOW, CONF_GLASS_AREA: stored})
    _assert_area(coord.glass_area(), 3.0, "derived")


def test_a_string_override_that_parses_is_accepted() -> None:
    coord = _coordinator(options={**_WINDOW, CONF_GLASS_AREA: "2.75"})
    _assert_area(coord.glass_area(), 2.75, "configured")


# ---------------------------------------------------------------------------
# Diagnostics call site
# ---------------------------------------------------------------------------


def test_day_of_year_comes_from_the_home_assistant_clock_frame() -> None:
    """A host in a different timezone must not shift the eccentricity term.

    ``date.today()`` reads the HOST clock; HA's own frame is the only one the
    rest of the integration uses, and near midnight the two disagree by a day.
    """
    import inspect

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.build_diagnostic_data)
    assert "dt_util.now().timetuple().tm_yday" in source
    assert "date.today()" not in source


def test_the_context_is_handed_every_gain_input() -> None:
    import inspect

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.build_diagnostic_data)
    for field in ("irradiance_w_m2=", "day_of_year=", "glass_area_m2="):
        assert field in source, f"{field} missing from the diagnostics context"


def test_irradiance_reaches_the_context_from_the_climate_readings() -> None:
    import inspect

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.build_diagnostic_data)
    assert "irradiance_value" in source
