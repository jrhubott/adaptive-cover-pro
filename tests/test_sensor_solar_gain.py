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

from types import SimpleNamespace

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfPower

from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.const import ControlMethod

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
