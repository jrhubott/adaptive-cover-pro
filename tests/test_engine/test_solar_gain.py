"""Pure solar-gain physics — ``engine/solar_gain.py`` (issue #1237).

Three independent layers, tested independently:

1. ``extraterrestrial_normal_irradiance`` — the eccentricity correction, a
   closed form with published extremes at perihelion and aphelion.
2. ``erbs_diffuse_fraction`` — the Erbs (1982) ``kt → kd`` correlation, whose
   two published breakpoints and monotonicity are the whole contract.
3. ``plane_of_array_irradiance`` — isotropic (Liu-Jordan) transposition, pinned
   by its two degenerate cases (flat and vertical) where the view factors have
   exact closed-form values, plus the never-negative beam guard.

Then ``estimate_solar_gain`` on top: TOTAL (never raises), and every missing
term produces a populated ``unknown_reason`` instead of a wrong number.

No rounding anywhere in the module (#140) — that belongs to the diagnostics
builder and the sensor.
"""

from __future__ import annotations

import math

import pytest

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_GROUND_ALBEDO,
    IRRADIANCE_PLANE_HORIZONTAL,
    IRRADIANCE_PLANE_WINDOW,
    MIN_GAIN_SUN_ELEVATION_DEG,
    SOLAR_CONSTANT_W_M2,
    VERTICAL_GLASS_PITCH_DEG,
)
from custom_components.adaptive_cover_pro.engine.solar_gain import (
    AREA_SOURCE_CONFIGURED,
    AREA_SOURCE_DERIVED,
    AREA_SOURCE_UNKNOWN,
    MODEL_ISOTROPIC_ERBS,
    MODEL_PASSTHROUGH,
    NOTE_SUN_TOO_LOW,
    UNKNOWN_EFFECTIVE_G,
    UNKNOWN_GLASS_AREA,
    UNKNOWN_NO_IRRADIANCE,
    SolarGainEstimate,
    erbs_diffuse_fraction,
    estimate_solar_gain,
    extraterrestrial_normal_irradiance,
    plane_of_array_irradiance,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Extraterrestrial normal irradiance
# ---------------------------------------------------------------------------


class TestExtraterrestrialNormalIrradiance:
    """``I0 = S · (1 + 0.033·cos(2πn/365))`` — the orbital eccentricity term."""

    @pytest.mark.parametrize(
        ("day_of_year", "expected"),
        [(1, 1412.1043), (80, 1375.6817), (172, 1322.6239), (365, 1412.1110)],
    )
    def test_closed_form_values(self, day_of_year: int, expected: float) -> None:
        assert extraterrestrial_normal_irradiance(day_of_year) == pytest.approx(
            expected, abs=1e-3
        )

    def test_perihelion_is_the_annual_maximum(self) -> None:
        """Early January: Earth is closest, so I0 peaks at S·1.033."""
        peak = extraterrestrial_normal_irradiance(365)
        assert peak == pytest.approx(SOLAR_CONSTANT_W_M2 * 1.033, abs=1e-6)
        assert all(
            extraterrestrial_normal_irradiance(n) <= peak + 1e-9 for n in range(1, 366)
        )

    def test_aphelion_is_the_annual_minimum(self) -> None:
        trough = min(extraterrestrial_normal_irradiance(n) for n in range(1, 366))
        assert trough == pytest.approx(SOLAR_CONSTANT_W_M2 * 0.967, abs=0.5)

    def test_the_swing_is_about_seven_percent(self) -> None:
        values = [extraterrestrial_normal_irradiance(n) for n in range(1, 366)]
        assert (max(values) - min(values)) / SOLAR_CONSTANT_W_M2 == pytest.approx(
            0.066, abs=0.002
        )

    @pytest.mark.parametrize("day_of_year", [0, 366, 400, -5])
    def test_out_of_range_days_never_raise(self, day_of_year: int) -> None:
        """The cosine is periodic — a leap-year 366 is not an error."""
        assert extraterrestrial_normal_irradiance(day_of_year) > 0


# ---------------------------------------------------------------------------
# Erbs diffuse fraction
# ---------------------------------------------------------------------------


class TestErbsDiffuseFraction:
    """Erbs et al. (1982) clearness-index → diffuse-fraction correlation."""

    @pytest.mark.parametrize(
        ("kt", "expected"),
        [
            (0.0, 1.0),  # no beam at all: everything is diffuse
            (0.1, 0.991),
            (0.22, 0.9802),  # the low breakpoint belongs to the linear branch
            (0.3, 0.948596),
            (0.5, 0.65915),
            (0.7, 0.24398),
            (0.8, 0.16527),  # the high breakpoint belongs to the polynomial
            (0.9, 0.165),  # above 0.80: the flat clear-sky floor
            (1.0, 0.165),
        ],
    )
    def test_published_values(self, kt: float, expected: float) -> None:
        assert erbs_diffuse_fraction(kt) == pytest.approx(expected, abs=1e-6)

    @pytest.mark.parametrize("breakpoint_kt", [0.22, 0.80])
    def test_branches_are_continuous_at_the_breakpoints(
        self, breakpoint_kt: float
    ) -> None:
        """The correlation is piecewise but not discontinuous."""
        below = erbs_diffuse_fraction(breakpoint_kt - 1e-6)
        above = erbs_diffuse_fraction(breakpoint_kt + 1e-6)
        assert below == pytest.approx(above, abs=5e-4)

    def test_monotonically_non_increasing_up_to_the_polynomial_tail(self) -> None:
        """A clearer sky cannot mean a larger diffuse share.

        Strictly true over ``[0, 0.78]``. Past that the PUBLISHED quartic has a
        shallow local minimum near ``kt ≈ 0.79`` and ticks back up by ~6e-4
        before the 0.80 breakpoint — an artefact of Erbs' fit, not of this
        implementation, so the tail is pinned separately below rather than
        smoothed away here.
        """
        values = [erbs_diffuse_fraction(i / 200) for i in range(157)]  # 0.00-0.78
        assert all(b <= a + 1e-9 for a, b in zip(values, values[1:], strict=False))

    def test_the_quartic_tail_wobble_is_negligible(self) -> None:
        """The known non-monotone tail must stay far below anything observable.

        Measures the UPWARD excursion specifically — the whole 0.78-0.80 span
        also falls, and a plain max-minus-min would hide the artefact behind
        that fall instead of bounding it.
        """
        tail = [erbs_diffuse_fraction(0.78 + i / 1000) for i in range(21)]
        rises = [b - a for a, b in zip(tail, tail[1:], strict=False) if b > a]
        assert rises, "the fit's local minimum should be inside this span"
        assert sum(rises) < 1e-3
        assert all(0.16 < v < 0.17 for v in tail)

    def test_the_overall_trend_is_strongly_decreasing(self) -> None:
        assert erbs_diffuse_fraction(0.0) == 1.0
        assert erbs_diffuse_fraction(0.5) < 0.7
        assert erbs_diffuse_fraction(1.0) < 0.2

    @pytest.mark.parametrize("kt", [-1.0, -0.001, 1.5, 99.0])
    def test_out_of_range_clearness_is_clamped_not_raised(self, kt: float) -> None:
        result = erbs_diffuse_fraction(kt)
        assert 0.0 <= result <= 1.0

    def test_fraction_always_within_the_unit_interval(self) -> None:
        assert all(0.0 <= erbs_diffuse_fraction(i / 100) <= 1.0 for i in range(101))


# ---------------------------------------------------------------------------
# Isotropic transposition
# ---------------------------------------------------------------------------


class TestPlaneOfArrayIrradiance:
    """Liu-Jordan isotropic sky + ground-reflected transposition."""

    def test_flat_plane_reproduces_the_horizontal_reading(self) -> None:
        """β = 0 with cos(AOI) = sin(h): the model must be self-consistent."""
        sin_elev = math.sin(math.radians(50.0))
        plane = plane_of_array_irradiance(
            ghi=700.0,
            sin_elev=sin_elev,
            cos_aoi=sin_elev,
            plane_tilt_deg=0.0,
            day_of_year=172,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.poa_w_m2 == pytest.approx(700.0, rel=1e-9)

    def test_vertical_plane_view_factors_are_both_one_half(self) -> None:
        sin_elev = math.sin(math.radians(40.0))
        plane = plane_of_array_irradiance(
            ghi=650.0,
            sin_elev=sin_elev,
            cos_aoi=0.6,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=100,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        expected = (
            plane.dni_w_m2 * 0.6
            + plane.dhi_w_m2 * 0.5
            + 650.0 * DEFAULT_GROUND_ALBEDO * 0.5
        )
        assert plane.poa_w_m2 == pytest.approx(expected, rel=1e-9)

    def test_sun_behind_the_plane_contributes_no_beam(self) -> None:
        sin_elev = math.sin(math.radians(40.0))
        kwargs = {
            "ghi": 650.0,
            "sin_elev": sin_elev,
            "plane_tilt_deg": VERTICAL_GLASS_PITCH_DEG,
            "day_of_year": 100,
            "albedo": DEFAULT_GROUND_ALBEDO,
        }
        behind = plane_of_array_irradiance(cos_aoi=-0.5, **kwargs)
        grazing = plane_of_array_irradiance(cos_aoi=0.0, **kwargs)
        assert behind.poa_w_m2 == pytest.approx(grazing.poa_w_m2, rel=1e-9)
        assert behind.poa_w_m2 > 0.0  # sky + ground still reach the glass

    @pytest.mark.parametrize("cos_aoi", [-1.0, -0.5, 0.0, 0.5, 1.0])
    @pytest.mark.parametrize("tilt", [0.0, 30.0, 90.0])
    def test_poa_is_never_negative(self, cos_aoi: float, tilt: float) -> None:
        plane = plane_of_array_irradiance(
            ghi=500.0,
            sin_elev=math.sin(math.radians(25.0)),
            cos_aoi=cos_aoi,
            plane_tilt_deg=tilt,
            day_of_year=1,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.poa_w_m2 >= 0.0

    def test_beam_and_diffuse_reconstruct_the_horizontal_reading(self) -> None:
        """DNI·sin(h) + DHI == GHI, the identity the decomposition rests on."""
        sin_elev = math.sin(math.radians(55.0))
        plane = plane_of_array_irradiance(
            ghi=820.0,
            sin_elev=sin_elev,
            cos_aoi=0.8,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.dni_w_m2 * sin_elev + plane.dhi_w_m2 == pytest.approx(820.0)

    def test_clearness_index_is_clamped_to_the_unit_interval(self) -> None:
        """A sensor reading above the extraterrestrial beam cannot mean kt > 1."""
        plane = plane_of_array_irradiance(
            ghi=5000.0,
            sin_elev=math.sin(math.radians(20.0)),
            cos_aoi=0.5,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.clearness_index == pytest.approx(1.0)

    # -- the beam is bounded by physics, not just by the clearness index ----

    def test_dni_cannot_exceed_the_extraterrestrial_beam(self) -> None:
        """No surface can receive more direct beam than the top of the atmosphere.

        Clamping ``kt`` alone does not bound ``DNI``: the beam is
        ``(GHI − DHI) / sin h`` computed from the RAW reading, so a mis-scaled
        sensor — or a tilted one left on the ``horizontal`` setting — divides a
        far-too-large number by a small ``sin h`` and invents beam energy that
        multiplies straight into POA.
        """
        day = 172
        i0 = extraterrestrial_normal_irradiance(day)
        plane = plane_of_array_irradiance(
            ghi=5000.0,
            sin_elev=math.sin(math.radians(10.0)),
            cos_aoi=1.0,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=day,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.dni_w_m2 <= i0

    @pytest.mark.parametrize("ghi", [1500.0, 5000.0, 100_000.0])
    @pytest.mark.parametrize("elevation_deg", [0.5, 3.0, 10.0, 45.0])
    def test_beam_stays_bounded_for_any_over_scaled_reading(
        self, ghi: float, elevation_deg: float
    ) -> None:
        day = 200
        i0 = extraterrestrial_normal_irradiance(day)
        plane = plane_of_array_irradiance(
            ghi=ghi,
            sin_elev=math.sin(math.radians(elevation_deg)),
            cos_aoi=1.0,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=day,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.dni_w_m2 <= i0
        assert math.isfinite(plane.poa_w_m2)
        # The beam term is capped at I0, the sky and ground terms at the reading
        # itself — so POA cannot run away with a bad number.
        assert plane.poa_w_m2 <= i0 + ghi

    def test_the_clamp_leaves_a_plausible_reading_untouched(self) -> None:
        """The bound must bite only on nonsense, never on a real clear day."""
        sin_elev = math.sin(math.radians(55.0))
        plane = plane_of_array_irradiance(
            ghi=820.0,
            sin_elev=sin_elev,
            cos_aoi=0.8,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.dni_w_m2 * sin_elev + plane.dhi_w_m2 == pytest.approx(820.0)

    def test_overcast_reading_is_almost_all_diffuse(self) -> None:
        sin_elev = math.sin(math.radians(45.0))
        plane = plane_of_array_irradiance(
            ghi=90.0,
            sin_elev=sin_elev,
            cos_aoi=0.7,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.clearness_index < 0.15
        assert plane.dhi_w_m2 > 0.9 * 90.0

    def test_zero_ghi_yields_zero_everywhere(self) -> None:
        plane = plane_of_array_irradiance(
            ghi=0.0,
            sin_elev=math.sin(math.radians(30.0)),
            cos_aoi=0.5,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=200,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert plane.poa_w_m2 == pytest.approx(0.0)
        assert plane.dni_w_m2 == pytest.approx(0.0)
        assert plane.dhi_w_m2 == pytest.approx(0.0)

    @pytest.mark.parametrize("sin_elev", [0.0, -0.5, 1e-12])
    def test_non_positive_sun_never_divides_by_zero(self, sin_elev: float) -> None:
        plane = plane_of_array_irradiance(
            ghi=300.0,
            sin_elev=sin_elev,
            cos_aoi=0.5,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=200,
            albedo=DEFAULT_GROUND_ALBEDO,
        )
        assert math.isfinite(plane.poa_w_m2)
        assert plane.poa_w_m2 >= 0.0


# ---------------------------------------------------------------------------
# estimate_solar_gain — totality
# ---------------------------------------------------------------------------


_DEFAULTS = {
    "ghi_w_m2": 700.0,
    "irradiance_plane": IRRADIANCE_PLANE_HORIZONTAL,
    "sol_elev_deg": 45.0,
    "cos_aoi": 0.8,
    "plane_tilt_deg": VERTICAL_GLASS_PITCH_DEG,
    "day_of_year": 172,
    "area_m2": 3.0,
    "area_source": AREA_SOURCE_DERIVED,
    "effective_g": 0.55,
    "effective_g_source": "preset",
}


def _estimate(**overrides) -> SolarGainEstimate:
    return estimate_solar_gain(**{**_DEFAULTS, **overrides})


class TestEstimateSolarGain:
    """The public entry point: total, never raising, always self-describing."""

    def test_nominal_gain_is_area_times_poa_times_g(self) -> None:
        est = _estimate()
        assert est.gain_w == pytest.approx(3.0 * est.poa_w_m2 * 0.55)
        assert est.unknown_reason is None
        assert est.model_note is None
        assert est.model == MODEL_ISOTROPIC_ERBS

    def test_every_input_is_echoed_back_for_audit(self) -> None:
        est = _estimate()
        assert est.ghi_w_m2 == pytest.approx(700.0)
        assert est.area_m2 == pytest.approx(3.0)
        assert est.area_source == AREA_SOURCE_DERIVED
        assert est.effective_g == pytest.approx(0.55)
        assert est.effective_g_source == "preset"
        assert est.cos_aoi == pytest.approx(0.8)
        assert est.plane_tilt_deg == pytest.approx(VERTICAL_GLASS_PITCH_DEG)
        assert est.irradiance_plane == IRRADIANCE_PLANE_HORIZONTAL

    # -- missing terms ------------------------------------------------------

    def test_missing_irradiance(self) -> None:
        est = _estimate(ghi_w_m2=None)
        assert est.gain_w is None
        assert est.unknown_reason == UNKNOWN_NO_IRRADIANCE
        assert est.poa_w_m2 is None

    def test_missing_glass_area(self) -> None:
        est = _estimate(area_m2=None, area_source=AREA_SOURCE_UNKNOWN)
        assert est.gain_w is None
        assert est.unknown_reason == UNKNOWN_GLASS_AREA
        # The irradiance work is still reported — the user can see the model ran.
        assert est.poa_w_m2 is not None

    def test_missing_effective_g(self) -> None:
        est = _estimate(effective_g=None, effective_g_source="unknown")
        assert est.gain_w is None
        assert est.unknown_reason == UNKNOWN_EFFECTIVE_G

    def test_missing_irradiance_outranks_missing_area(self) -> None:
        """One reason, and it names the most fundamental gap."""
        est = _estimate(ghi_w_m2=None, area_m2=None, area_source=AREA_SOURCE_UNKNOWN)
        assert est.unknown_reason == UNKNOWN_NO_IRRADIANCE

    # -- the low-sun guard --------------------------------------------------

    @pytest.mark.parametrize("elevation", [-10.0, 0.0, 2.99])
    def test_low_sun_reports_a_hard_zero_not_unknown(self, elevation: float) -> None:
        """The gain genuinely IS ~zero, so 0 W is a fact, not missing data."""
        est = _estimate(sol_elev_deg=elevation)
        assert est.gain_w == 0.0
        assert est.model_note == NOTE_SUN_TOO_LOW
        assert est.unknown_reason is None

    def test_the_guard_edge_is_the_named_constant(self) -> None:
        assert _estimate(sol_elev_deg=MIN_GAIN_SUN_ELEVATION_DEG).model_note is None
        assert (
            _estimate(sol_elev_deg=MIN_GAIN_SUN_ELEVATION_DEG - 1e-6).model_note
            == NOTE_SUN_TOO_LOW
        )

    def test_low_sun_skips_the_unstable_decomposition(self) -> None:
        est = _estimate(sol_elev_deg=1.0)
        assert est.poa_w_m2 is None
        assert est.dni_w_m2 is None
        assert est.clearness_index is None

    def test_missing_elevation_is_treated_as_too_low(self) -> None:
        est = _estimate(sol_elev_deg=None)
        assert est.gain_w == 0.0
        assert est.model_note == NOTE_SUN_TOO_LOW

    def test_a_missing_term_still_wins_over_the_low_sun_zero(self) -> None:
        """An install that can NEVER produce a number must not report 0 W."""
        est = _estimate(sol_elev_deg=1.0, area_m2=None, area_source=AREA_SOURCE_UNKNOWN)
        assert est.gain_w is None
        assert est.unknown_reason == UNKNOWN_GLASS_AREA
        assert est.model_note == NOTE_SUN_TOO_LOW

    # -- passthrough --------------------------------------------------------

    def test_window_plane_passes_the_reading_straight_through(self) -> None:
        est = _estimate(irradiance_plane=IRRADIANCE_PLANE_WINDOW)
        assert est.model == MODEL_PASSTHROUGH
        assert est.poa_w_m2 == pytest.approx(700.0)
        assert est.gain_w == pytest.approx(3.0 * 700.0 * 0.55)

    def test_window_plane_reports_no_decomposition_terms(self) -> None:
        est = _estimate(irradiance_plane=IRRADIANCE_PLANE_WINDOW)
        assert est.dni_w_m2 is None
        assert est.dhi_w_m2 is None
        assert est.clearness_index is None

    def test_window_plane_ignores_the_plane_geometry(self) -> None:
        """No transposition means cos(AOI) and tilt cannot move the answer."""
        a = _estimate(irradiance_plane=IRRADIANCE_PLANE_WINDOW, cos_aoi=0.1)
        b = _estimate(
            irradiance_plane=IRRADIANCE_PLANE_WINDOW, cos_aoi=0.9, plane_tilt_deg=20.0
        )
        assert a.gain_w == pytest.approx(b.gain_w)

    def test_unknown_plane_string_falls_back_to_the_model(self) -> None:
        """A hand-edited entry must not silently become a passthrough."""
        assert _estimate(irradiance_plane="nonsense").model == MODEL_ISOTROPIC_ERBS

    # -- the low-sun guard belongs to the decomposition, not to the reading --

    def test_a_passthrough_reading_survives_a_low_sun(self) -> None:
        """A window-plane sensor at dusk is reporting a MEASUREMENT, not noise.

        The guard exists because ``1/sin h`` is numerically meaningless near the
        horizon — and that term appears nowhere on this path. Zeroing here threw
        away a real reading and produced an internally contradictory result:
        ``gain_w=0.0`` sitting next to ``poa_w_m2=None``.
        """
        est = _estimate(
            irradiance_plane=IRRADIANCE_PLANE_WINDOW, sol_elev_deg=1.0, ghi_w_m2=15.0
        )
        assert est.poa_w_m2 == pytest.approx(15.0)
        assert est.gain_w == pytest.approx(3.0 * 15.0 * 0.55)
        assert est.model_note is None
        assert est.unknown_reason is None

    def test_the_low_sun_guard_still_zeroes_the_transposition_model(self) -> None:
        """Same dusk reading, horizontal sensor: the unstable path is unchanged."""
        est = _estimate(
            irradiance_plane=IRRADIANCE_PLANE_HORIZONTAL,
            sol_elev_deg=1.0,
            ghi_w_m2=15.0,
        )
        assert est.gain_w == 0.0
        assert est.model_note == NOTE_SUN_TOO_LOW
        assert est.poa_w_m2 is None

    @pytest.mark.parametrize("elevation", [None, -20.0, 0.0, 2.99])
    def test_passthrough_never_pairs_a_gain_with_a_missing_poa(
        self, elevation: float | None
    ) -> None:
        """Internal consistency: a reported wattage always has POA behind it."""
        est = _estimate(
            irradiance_plane=IRRADIANCE_PLANE_WINDOW, sol_elev_deg=elevation
        )
        assert est.poa_w_m2 is not None
        assert est.gain_w == pytest.approx(3.0 * est.poa_w_m2 * 0.55)

    def test_passthrough_at_night_still_reports_zero_when_the_sensor_does(self) -> None:
        """The honest zero comes from the reading itself, not from a guard."""
        est = _estimate(
            irradiance_plane=IRRADIANCE_PLANE_WINDOW, sol_elev_deg=-10.0, ghi_w_m2=0.0
        )
        assert est.gain_w == 0.0
        assert est.poa_w_m2 == 0.0

    # -- totality -----------------------------------------------------------

    @pytest.mark.parametrize("ghi", [None, -100.0, 0.0, 1400.0])
    @pytest.mark.parametrize("elevation", [None, -90.0, 0.0, 45.0, 90.0])
    @pytest.mark.parametrize("cos_aoi", [-1.0, 0.0, 1.0])
    @pytest.mark.parametrize("area", [None, 0.5, 40.0])
    @pytest.mark.parametrize(
        "plane", [IRRADIANCE_PLANE_HORIZONTAL, IRRADIANCE_PLANE_WINDOW]
    )
    def test_never_raises_for_any_input_combination(
        self, ghi, elevation, cos_aoi, area, plane
    ) -> None:
        est = estimate_solar_gain(
            ghi_w_m2=ghi,
            irradiance_plane=plane,
            sol_elev_deg=elevation,
            cos_aoi=cos_aoi,
            plane_tilt_deg=VERTICAL_GLASS_PITCH_DEG,
            day_of_year=172,
            area_m2=area,
            area_source=AREA_SOURCE_CONFIGURED if area else AREA_SOURCE_UNKNOWN,
            effective_g=0.7,
            effective_g_source="default",
        )
        assert isinstance(est, SolarGainEstimate)
        assert est.gain_w is None or (est.gain_w >= 0.0 and math.isfinite(est.gain_w))
        assert (est.gain_w is None) == (est.unknown_reason is not None)

    def test_negative_irradiance_never_produces_negative_watts(self) -> None:
        est = _estimate(ghi_w_m2=-50.0)
        assert est.gain_w == pytest.approx(0.0)

    def test_the_estimate_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            _estimate().gain_w = 1.0  # type: ignore[misc]

    # -- no rounding in the engine (#140) -----------------------------------

    def test_nothing_is_rounded_in_the_engine(self) -> None:
        est = _estimate(ghi_w_m2=713.7, area_m2=2.37, effective_g=0.583)
        assert est.gain_w != round(est.gain_w)
        assert est.poa_w_m2 != round(est.poa_w_m2, 3)
