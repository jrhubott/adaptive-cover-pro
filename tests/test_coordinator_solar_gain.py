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
from homeassistant.const import UnitOfIrradiance

from custom_components.adaptive_cover_pro.const import CONF_GLASS_AREA
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.engine.solar_gain import (
    UNKNOWN_NO_IRRADIANCE,
    UNKNOWN_UNSUPPORTED_IRRADIANCE_UNIT,
)
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)
from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateReadings,
)
from tests._helpers.diagnostic_coordinator import make_diagnostic_coordinator

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


# ---------------------------------------------------------------------------
# Unsupported irradiance unit (issue #1280) — refuse to compute, don't convert
# ---------------------------------------------------------------------------


def _coordinator_with_irradiance(
    *, irradiance_value: float | None, irradiance_unit: str | None
) -> MagicMock:
    """Build a coordinator stub for the gate itself — a STUBBED climate provider.

    ``read_irradiance_unit`` is stubbed directly rather than driven through a
    real HA state read (that end-to-end path, including the state read, is
    covered by ``tests/test_sensor_solar_gain.py``'s real-chain tests): this
    helper isolates the ONE thing worth pinning here — that
    ``build_diagnostic_data`` combines the value and the unit into
    ``irradiance_unit_ok`` correctly, in particular that a missing VALUE
    (entity unavailable) is never confused with an unsupported UNIT.
    """
    climate_provider = MagicMock()
    climate_provider.read_irradiance_unit.return_value = irradiance_unit
    weather_readings = ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=False,
        is_sunny=False,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
        irradiance_value=irradiance_value,
    )
    return make_diagnostic_coordinator(
        climate_provider=climate_provider,
        weather_readings=weather_readings,
        irradiance_entity="sensor.solar",
    )


def test_a_missing_value_reports_no_irradiance_even_with_an_unsupported_unit() -> None:
    """The short-circuit this whole fix hinges on.

    An irradiance sensor that has gone momentarily ``unavailable`` reports NO
    value — but ``read_irradiance_unit`` still answers with whatever unit it
    last carried (HA does not clear ``unit_of_measurement`` just because the
    state went unavailable). Without the ``_irradiance_value is None``
    short-circuit in ``build_diagnostic_data``, a BTU-configured sensor going
    briefly unavailable would flip from "nothing to read" to "refused" on
    every such blip. THIS is the case the audit proved the old test suite
    never actually exercised.
    """
    coord = _coordinator_with_irradiance(
        irradiance_value=None, irradiance_unit="BTU/(h⋅ft²)"
    )
    diag = coord.build_diagnostic_data()
    assert diag["solar_gain"]["unknown_reason"] == UNKNOWN_NO_IRRADIANCE


def test_a_missing_value_reports_no_irradiance_with_a_supported_unit_too() -> None:
    """Regression guard: the ordinary 'nothing configured/available' path."""
    coord = _coordinator_with_irradiance(
        irradiance_value=None,
        irradiance_unit=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
    )
    diag = coord.build_diagnostic_data()
    assert diag["solar_gain"]["unknown_reason"] == UNKNOWN_NO_IRRADIANCE


def test_a_present_value_in_an_unsupported_unit_is_refused() -> None:
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0, irradiance_unit="BTU/(h⋅ft²)"
    )
    diag = coord.build_diagnostic_data()
    assert diag["solar_gain"]["unknown_reason"] == UNKNOWN_UNSUPPORTED_IRRADIANCE_UNIT
    assert diag["solar_gain"]["ghi_w_m2"] is None
    assert diag["solar_gain"]["irradiance_unit"] == "BTU/(h⋅ft²)"


def test_a_present_value_in_the_supported_unit_computes_a_real_gain() -> None:
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0,
        irradiance_unit=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
    )
    diag = coord.build_diagnostic_data()
    assert diag["solar_gain"]["unknown_reason"] is None
    assert diag["solar_gain"]["gain_w"] is not None
    assert (
        diag["solar_gain"]["irradiance_unit"] == UnitOfIrradiance.WATTS_PER_SQUARE_METER
    )


def test_the_accepted_unit_is_home_assistants_own_constant() -> None:
    """No hardcoded ``"W/m²"`` literal — HA's own enum is the source of truth."""
    import inspect

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.build_diagnostic_data)
    assert "UnitOfIrradiance.WATTS_PER_SQUARE_METER" in source
    assert '"W/m' not in source
    assert "'W/m" not in source


# ---------------------------------------------------------------------------
# ⚠️ The refusal is logged, exactly once per (entity, unit) situation
# (issue #1280 Fix 4) — a user must not have to open the entity's attributes
# to discover why the gain sensor reports ``unknown``, but
# ``build_diagnostic_data`` runs every cycle, so an unconditional warning
# would spam the log forever.
# ---------------------------------------------------------------------------


def test_no_warning_on_the_healthy_metric_path() -> None:
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0,
        irradiance_unit=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
    )
    coord.build_diagnostic_data()
    coord.logger.warning.assert_not_called()


def test_no_warning_when_no_value_is_available() -> None:
    """A momentarily-unavailable sensor is not itself refused — no warning."""
    coord = _coordinator_with_irradiance(
        irradiance_value=None, irradiance_unit="BTU/(h⋅ft²)"
    )
    coord.build_diagnostic_data()
    coord.logger.warning.assert_not_called()


def test_warns_once_naming_the_entity_and_the_unit() -> None:
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0, irradiance_unit="BTU/(h⋅ft²)"
    )
    coord.build_diagnostic_data()
    coord.logger.warning.assert_called_once()
    message = (
        coord.logger.warning.call_args[0][0] % coord.logger.warning.call_args[0][1:]
    )
    assert "sensor.solar" in message
    assert "BTU/(h⋅ft²)" in message
    assert "unknown" in message


def test_does_not_repeat_for_an_identical_refusal_every_cycle() -> None:
    """The whole point: a persistent misconfiguration logs ONCE, not forever."""
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0, irradiance_unit="BTU/(h⋅ft²)"
    )
    coord.build_diagnostic_data()
    coord.build_diagnostic_data()
    coord.build_diagnostic_data()
    coord.logger.warning.assert_called_once()


def test_warns_again_when_the_unsupported_unit_changes() -> None:
    """A different unsupported unit is a NEW situation — it must be reported."""
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0, irradiance_unit="BTU/(h⋅ft²)"
    )
    coord.build_diagnostic_data()
    coord._climate_provider.read_irradiance_unit.return_value = "cd"
    coord.build_diagnostic_data()
    assert coord.logger.warning.call_count == 2


def test_warns_again_after_the_refusal_clears_and_recurs() -> None:
    """Fix, then re-break: the user must see the warning again, not silence."""
    coord = _coordinator_with_irradiance(
        irradiance_value=600.0, irradiance_unit="BTU/(h⋅ft²)"
    )
    coord.build_diagnostic_data()
    assert coord.logger.warning.call_count == 1

    # The unit becomes acceptable — refusal clears.
    coord._climate_provider.read_irradiance_unit.return_value = (
        UnitOfIrradiance.WATTS_PER_SQUARE_METER
    )
    coord.build_diagnostic_data()
    assert coord.logger.warning.call_count == 1

    # Same unsupported unit as before, but the refusal recurred — must warn again.
    coord._climate_provider.read_irradiance_unit.return_value = "BTU/(h⋅ft²)"
    coord.build_diagnostic_data()
    assert coord.logger.warning.call_count == 2


def test_no_warning_when_no_entity_is_configured() -> None:
    coord = make_diagnostic_coordinator(
        climate_provider=MagicMock(read_irradiance_unit=MagicMock(return_value=None)),
        weather_readings=ClimateReadings(
            outside_temperature=None,
            inside_temperature=None,
            is_presence=False,
            is_sunny=False,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
            irradiance_value=None,
        ),
        irradiance_entity=None,
    )
    coord.build_diagnostic_data()
    coord.logger.warning.assert_not_called()
