"""The Estimated Solar Gain diagnostic sensor (issue #1237).

One diagnostic entity, created only when the user actually has an irradiance
sensor, whose state is whole watts and whose attributes carry every input and
its provenance — so a user who does not believe the number can audit it rather
than guess.

The sensor itself owns no physics: it reads ``diagnostics["solar_gain"]``, the
same block the diagnostics download surfaces, exactly as the ``solar_calculation``
sensor reads ``calculation_details``. One computation, one source.
"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfPower

from homeassistant.const import UnitOfIrradiance

from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.engine.solar_gain import (
    UNKNOWN_NO_IRRADIANCE,
    UNKNOWN_UNSUPPORTED_IRRADIANCE_UNIT,
    estimate_solar_gain,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateProvider
from custom_components.adaptive_cover_pro.const import (
    ControlMethod,
    IRRADIANCE_PLANE_HORIZONTAL,
    VERTICAL_GLASS_PITCH_DEG,
)
from tests._helpers.diagnostic_coordinator import make_diagnostic_coordinator

pytestmark = pytest.mark.unit

_SUFFIX = "solar_gain"


def _spec():
    from custom_components.adaptive_cover_pro.sensor import _DIAGNOSTIC_SPECS

    for spec in _DIAGNOSTIC_SPECS:
        if spec.suffix == _SUFFIX:
            return spec
    raise AssertionError("solar_gain spec is not registered")


def _stub_sensor(block):
    return SimpleNamespace(
        data=SimpleNamespace(
            diagnostics={"solar_gain": block} if block is not None else {}
        )
    )


# ---------------------------------------------------------------------------
# Spec wiring
# ---------------------------------------------------------------------------


class TestSolarGainSensorSpec:
    """Entity identity and HA metadata — all of it is a stability contract."""

    def test_unique_id_suffix_is_locked(self):
        assert _spec().suffix == "solar_gain"

    def test_it_is_a_power_sensor_in_watts(self):
        spec = _spec()
        assert spec.device_class == SensorDeviceClass.POWER
        assert spec.unit == UnitOfPower.WATT
        assert spec.state_class == SensorStateClass.MEASUREMENT
        assert spec.suggested_display_precision == 0

    def test_it_is_named_as_an_estimate(self):
        """The name is the first and cheapest of the three estimate signals."""
        spec = _spec()
        assert "Estimated" in spec.display_name
        assert spec.translation_key == "solar_gain"

    def test_it_is_a_diagnostic_entity(self):
        assert _spec().diagnostic is True

    def test_attributes_are_recorded(self):
        """Small scalars, unlike the solar_calculation trace — history is useful."""
        assert _spec().unrecorded_attributes == frozenset()

    def test_the_entity_class_resolves(self):
        from custom_components.adaptive_cover_pro.sensor import _DIAGNOSTIC_CLASSES

        assert _SUFFIX in _DIAGNOSTIC_CLASSES


class TestSolarGainSensorCreation:
    """Created only for installs that can actually produce a number."""

    @pytest.mark.parametrize(
        ("options", "expected"),
        [
            ({}, False),
            ({"irradiance_entity": None}, False),
            ({"irradiance_entity": ""}, False),
            ({"irradiance_entity": "sensor.solar"}, True),
        ],
    )
    def test_gated_on_the_irradiance_entity(self, options, expected):
        entry = SimpleNamespace(options=options, data={})
        assert _spec().enabled_when(entry) is expected

    def test_creation_does_not_depend_on_cloud_suppression(self):
        """The gain feature is independent of the suppression latch."""
        entry = SimpleNamespace(
            options={"irradiance_entity": "sensor.solar", "cloud_suppression": False},
            data={},
        )
        assert _spec().enabled_when(entry) is True


# ---------------------------------------------------------------------------
# State and attributes
# ---------------------------------------------------------------------------


class TestSolarGainSensorValue:
    """``native_value`` is integer watts, or ``None`` when a term is missing."""

    def test_whole_watts(self):
        assert _spec().value_fn(_stub_sensor({"gain_w": 812.63})) == 813

    def test_returns_an_int_not_a_float(self):
        value = _spec().value_fn(_stub_sensor({"gain_w": 812.0}))
        assert isinstance(value, int)

    def test_zero_watts_is_reported_as_zero_not_unknown(self):
        """Sun down is a fact, and 0 must survive the falsy trap."""
        assert _spec().value_fn(_stub_sensor({"gain_w": 0.0})) == 0

    def test_none_when_a_term_is_missing(self):
        assert _spec().value_fn(_stub_sensor({"gain_w": None})) is None

    def test_none_when_the_block_is_absent(self):
        assert _spec().value_fn(_stub_sensor(None)) is None

    def test_none_before_the_first_update(self):
        assert _spec().value_fn(SimpleNamespace(data=None)) is None


class TestSolarGainSensorAttributes:
    """Every input and its provenance rides along, so the number is auditable."""

    _BLOCK = {
        "gain_w": 812.6,
        "poa_w_m2": 493.1,
        "dni_w_m2": 700.0,
        "dhi_w_m2": 120.0,
        "clearness_index": 0.62,
        "ghi_w_m2": 700.0,
        "area_m2": 3.0,
        "area_source": "derived",
        "effective_g": 0.55,
        "effective_g_source": "preset",
        "cos_aoi": 0.8,
        "plane_tilt_deg": 90.0,
        "irradiance_plane": "horizontal",
        "model": "isotropic_erbs",
        "model_note": None,
        "unknown_reason": None,
        "position_pct": 25,
        "position_source": "target",
        "shaded_fraction": 0.75,
    }

    @pytest.mark.parametrize(
        "key",
        [
            "area_source",
            "effective_g_source",
            "irradiance_plane",
            "model",
            "model_note",
            "unknown_reason",
            "position_source",
        ],
    )
    def test_provenance_attributes_are_present(self, key):
        attrs = _spec().attrs_fn(_stub_sensor(dict(self._BLOCK)))
        assert key in attrs

    def test_the_state_value_is_not_duplicated_into_the_attributes(self):
        attrs = _spec().attrs_fn(_stub_sensor(dict(self._BLOCK)))
        assert "gain_w" not in attrs

    def test_every_intermediate_survives(self):
        attrs = _spec().attrs_fn(_stub_sensor(dict(self._BLOCK)))
        for key in ("poa_w_m2", "dni_w_m2", "dhi_w_m2", "clearness_index", "ghi_w_m2"):
            assert attrs[key] == self._BLOCK[key]

    def test_none_when_the_block_is_absent(self):
        assert _spec().attrs_fn(_stub_sensor(None)) is None


# ---------------------------------------------------------------------------
# ⚠️ Inverse state — the #1028 guard, inherited through #1236's seam
# ---------------------------------------------------------------------------


def _gain_ctx(*, inverse_state: bool, logical_position: int) -> DiagnosticContext:
    cover = SimpleNamespace(
        gamma=10.0,
        sol_elev=45.0,
        valid=True,
        valid_elevation=True,
        is_sun_in_blind_spot=False,
        direct_sun_valid=True,
        sunset_valid=False,
        control_state_reason="Sun in FOV",
        in_fov=True,
        cos_aoi=0.8,
        plane_tilt_deg=90.0,
    )
    from custom_components.adaptive_cover_pro.config_types import (
        SolarPropertiesConfig,
    )
    from custom_components.adaptive_cover_pro.engine.solar_transmittance import (
        solar_transmittance,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    fraction = get_policy("cover_blind").shaded_glass_fraction(logical_position)
    return DiagnosticContext(
        pos_sun=[180.0, 45.0],
        cover=cover,
        pipeline_result=PipelineResult(
            position=logical_position,
            control_method=ControlMethod.SOLAR,
            reason="sun in FOV",
            raw_calculated_position=logical_position,
        ),
        climate_mode=False,
        check_adaptive_time=True,
        after_start_time=True,
        before_end_time=True,
        start_time=None,
        end_time=None,
        automatic_control=True,
        inverse_state=inverse_state,
        position_axis_inverted=inverse_state,
        # The POST-inversion published value — set deliberately, and nothing on
        # the gain path may read it.
        final_state=(100 - logical_position if inverse_state else logical_position),
        solar_transmittance=solar_transmittance(
            SolarPropertiesConfig(
                enabled=True, cover_side="internal", cover_shade="dark"
            ),
            shaded_fraction=fraction,
        ),
        config_options={
            "irradiance_entity": "sensor.solar",
            "solar_properties_enabled": True,
            "solar_cover_side": "internal",
            "solar_cover_shade": "dark",
        },
        irradiance_w_m2=700.0,
        day_of_year=172,
        glass_area_m2=3.0,
        glass_area_source="derived",
    )


@pytest.mark.parametrize("logical_position", [0, 25, 50, 75, 100])
def test_an_inverse_state_install_reports_the_same_watts(logical_position: int):
    """Same physical position, same wattage — whatever the wire frame is.

    An inverse-state install publishes the complement of the logical position.
    If any step between the pipeline and this sensor reads that wire value, a
    blind three-quarters down reports the gain of one three-quarters OPEN — a
    silent, install-specific inversion that every non-inverted test would miss.
    """
    builder = DiagnosticsBuilder()
    normal, _ = builder.build(
        _gain_ctx(inverse_state=False, logical_position=logical_position)
    )
    inverted, _ = builder.build(
        _gain_ctx(inverse_state=True, logical_position=logical_position)
    )
    spec = _spec()
    assert spec.value_fn(_stub_sensor(normal["solar_gain"])) == spec.value_fn(
        _stub_sensor(inverted["solar_gain"])
    )
    assert (
        normal["solar_gain"]["shaded_fraction"]
        == inverted["solar_gain"]["shaded_fraction"]
    )
    assert normal["solar_gain"]["position_pct"] == logical_position


def test_the_watt_figure_moves_with_the_cover_position():
    """A guard against a constant: closing the blind must reduce the gain."""
    builder = DiagnosticsBuilder()
    open_diag, _ = builder.build(_gain_ctx(inverse_state=False, logical_position=100))
    shut_diag, _ = builder.build(_gain_ctx(inverse_state=False, logical_position=0))
    assert shut_diag["solar_gain"]["gain_w"] < open_diag["solar_gain"]["gain_w"]


# ---------------------------------------------------------------------------
# ⚠️ Non-finite irradiance — the whole chain, end to end
# ---------------------------------------------------------------------------


def test_a_non_finite_reading_reports_unknown_instead_of_crashing_the_sensor():
    """A sensor publishing ``nan`` must produce ``unknown``, not a ValueError.

    Driven through the REAL chain — a ``ClimateProvider`` read, then the pure
    estimator, then the sensor's own ``value_fn`` — because that is exactly the
    route the crash took: ``float("nan")`` parses, survives ``max(nan, 0.0)``
    inside the transposition, multiplies into ``gain_w``, and detonates in
    ``int(round(gain_w))`` on every update, forever. A unit test on any single
    link would have stayed green while the assembled path was broken.
    """
    hass = MagicMock()
    state = MagicMock()
    state.entity_id = "sensor.solar"
    state.state = "nan"
    state.attributes = {}
    hass.states.get.return_value = state

    provider = ClimateProvider(hass=hass, logger=MagicMock())
    readings = provider.read(use_irradiance=False, irradiance_entity="sensor.solar")

    estimate = estimate_solar_gain(
        ghi_w_m2=readings.irradiance_value,
        irradiance_plane=IRRADIANCE_PLANE_HORIZONTAL,
        sol_elev_deg=45.0,
        cos_aoi=0.8,
        plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
        day_of_year=172,
        area_m2=3.0,
        area_source="derived",
        effective_g=0.55,
        effective_g_source="preset",
    )
    assert _spec().value_fn(_stub_sensor(asdict(estimate))) is None


# ---------------------------------------------------------------------------
# ⚠️ Irradiance unit (issue #1280) — the whole chain, end to end
#
# HA's ``irradiance`` device class permits BOTH W/m² and BTU/(h·ft²) — the
# latter is what HA presents on the imperial unit system — and nothing
# upstream of this fix ever checked which one an irradiance entity reports.
# 1 BTU/(h·ft²) = 3.15 W/m², so admitting a BTU reading as W/m² silently
# under-reports gain by roughly a factor of 3. The chosen fix is REFUSAL, not
# conversion: an unsupported (or absent) unit must produce ``unknown`` with a
# reason distinct from "no entity configured".
#
# These tests drive the REAL glue the coordinator uses: a ``ClimateProvider``
# read for the magnitude (unit-blind, exactly as issue #1237 left it) plus its
# SEPARATE ``read_irradiance_unit`` for the unit, combined by actually calling
# ``AdaptiveDataUpdateCoordinator.build_diagnostic_data`` through a coordinator
# stub — not by re-deriving its ``value is None or unit == WATTS_PER_SQUARE_METER``
# gate formula here. A hand-rolled second copy of that formula is exactly what
# let the #1280 audit delete the coordinator's short-circuit unnoticed: this
# file's own test suite kept passing because it was checking its OWN copy of
# the rule, not the coordinator's. See ``tests/test_coordinator_solar_gain.py``
# for the dedicated gate-boolean tests that pin the short-circuit itself.
# ---------------------------------------------------------------------------


def _mock_irradiance_state(state: str, unit: str | None):
    s = MagicMock()
    s.entity_id = "sensor.solar"
    s.state = state
    s.attributes = {"unit_of_measurement": unit} if unit is not None else {}
    return s


def _real_chain_estimate(
    *, entity: str | None, state: str | None = None, unit: str | None = None
):
    """Reproduce the coordinator's exact irradiance→gain glue, end to end.

    A real ``ClimateProvider`` reads the magnitude and (separately) the unit
    from a mocked HA state, exactly as the coordinator does; the combination
    of the two into ``irradiance_unit_ok`` is then exercised by calling the
    REAL ``build_diagnostic_data`` through a coordinator stub, so this test
    can never drift from what the coordinator actually computes.

    ``entity=None`` is the one exception: with no entity configured, the
    ``solar_gain`` block is gated off entirely at the diagnostics layer (see
    ``test_gated_on_the_irradiance_entity``) — there would be no block to
    inspect. That case exercises the pure estimator directly instead, with
    ``irradiance_unit_ok`` hardcoded True — the coordinator's own docstring
    on the gate documents this as the trivial, nothing-to-refuse case.
    """
    hass = MagicMock()
    hass.states.get.return_value = (
        _mock_irradiance_state(state, unit) if entity is not None else None
    )

    provider = ClimateProvider(hass=hass, logger=MagicMock())
    readings = provider.read(
        use_irradiance=True,
        irradiance_entity=entity,
        irradiance_threshold=300,
    )

    if entity is None:
        estimate = estimate_solar_gain(
            ghi_w_m2=readings.irradiance_value,
            irradiance_unit_ok=True,
            irradiance_plane=IRRADIANCE_PLANE_HORIZONTAL,
            sol_elev_deg=45.0,
            cos_aoi=0.8,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            area_m2=3.0,
            area_source="derived",
            effective_g=0.55,
            effective_g_source="preset",
        )
        return readings, asdict(estimate)

    coord = make_diagnostic_coordinator(
        climate_provider=provider,
        weather_readings=readings,
        irradiance_entity=entity,
    )
    diagnostics = coord.build_diagnostic_data()
    return readings, diagnostics.get("solar_gain", {})


def test_a_metric_reading_computes_gain_exactly_as_before():
    """Regression guard: W/m² is the shipped, unaffected happy path."""
    readings, block = _real_chain_estimate(
        entity="sensor.solar",
        state="600",
        unit=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
    )
    assert readings.irradiance_value == pytest.approx(600.0)
    assert block["unknown_reason"] is None
    assert block["gain_w"] is not None
    assert _spec().value_fn(_stub_sensor(block)) is not None


def test_an_imperial_reading_is_refused_not_converted():
    """The BTU number is NOT silently divided by 3 — it is refused outright."""
    readings, block = _real_chain_estimate(
        entity="sensor.solar", state="190", unit="BTU/(h⋅ft²)"
    )
    # The state-layer read stays unit-blind (issue #1237's own contract) —
    # only the GAIN estimator refuses the number.
    assert readings.irradiance_value == pytest.approx(190.0)
    assert block["gain_w"] is None
    assert block["ghi_w_m2"] is None
    assert block["unknown_reason"] == UNKNOWN_UNSUPPORTED_IRRADIANCE_UNIT
    assert block["unknown_reason"] != UNKNOWN_NO_IRRADIANCE
    assert _spec().value_fn(_stub_sensor(block)) is None


def test_an_absent_unit_takes_the_same_refusal_path():
    """A sensor with no ``unit_of_measurement`` at all is treated the same way."""
    readings, block = _real_chain_estimate(
        entity="sensor.solar", state="612.5", unit=None
    )
    assert readings.irradiance_value == pytest.approx(612.5)
    assert block["gain_w"] is None
    assert block["unknown_reason"] == UNKNOWN_UNSUPPORTED_IRRADIANCE_UNIT


def test_no_entity_configured_still_reports_no_irradiance():
    """Regression guard: the pre-existing 'nothing configured' path is unchanged."""
    readings, block = _real_chain_estimate(entity=None)
    assert readings.irradiance_value is None
    assert block["gain_w"] is None
    assert block["unknown_reason"] == UNKNOWN_NO_IRRADIANCE


def test_cloud_suppression_threshold_is_unaffected_by_an_imperial_unit():
    """The guard that proves the shared threshold path was never touched.

    Cloud suppression compares the raw number against a user-tuned threshold —
    a BTU-unit user has already calibrated that threshold against the numbers
    they observe, so this must produce IDENTICAL booleans regardless of unit.
    """

    def _threshold_reading(unit: str | None):
        hass = MagicMock()
        hass.states.get.return_value = _mock_irradiance_state("250", unit)
        provider = ClimateProvider(hass=hass, logger=MagicMock())
        return provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )

    metric = _threshold_reading("W/m²")
    imperial = _threshold_reading("BTU/(h⋅ft²)")
    absent = _threshold_reading(None)

    for readings in (metric, imperial, absent):
        assert readings.irradiance_below_threshold is True
        assert readings.irradiance_release_cleared is False
        assert readings.irradiance_value == pytest.approx(250.0)
