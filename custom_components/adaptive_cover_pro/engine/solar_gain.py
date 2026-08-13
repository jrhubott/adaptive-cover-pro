"""Estimated instantaneous solar gain through the window (issue #1237).

Pure module — zero Home Assistant imports, no I/O, no rounding. It answers one
question: *given a measured irradiance reading, roughly how many watts of solar
energy are entering the room through this window right now?*

    gain_W ≈ area_m² × POA_W/m² × g_eff

The integration already owns the area (window geometry) and ``g_eff`` (issue
#1236's transmittance estimate). This module supplies the middle term, which is
the only genuinely hard one: a user's irradiance sensor is almost always a
horizontally-mounted pyranometer, and the window is a tilted — usually
vertical — plane facing some particular way.

**The model.** Textbook two-stage transposition, both stages chosen for being
defensible and exactly testable rather than for being the most accurate:

1. *Decomposition* — Erbs et al. (1982) correlates the clearness index
   ``kt = GHI / (I0·sin h)`` to the diffuse fraction ``kd``, splitting the
   horizontal reading into its beam and diffuse parts.
2. *Transposition* — Liu & Jordan's isotropic sky model projects those parts
   onto the window plane using two view factors that depend only on the plane's
   tilt, plus a ground-reflected term.

**Deliberately NOT Perez.** Perez needs air mass plus sky brightness/clearness
binning for a modest accuracy gain on what is already a comfort-grade estimate,
and every extra coefficient is another thing to get wrong. Isotropic is the
standard defensible floor.

**Accuracy.** Isotropic under-predicts plane-of-array irradiance on clear days
for vertical surfaces by roughly 5-15 % against Perez, and the Erbs correlation
itself carries ±10-20 % scatter. Combined with a preset ``g_total`` the shipped
figure should be read as **±30 % or worse**. Everything this module produces is
an ESTIMATE, and every intermediate is surfaced so a user can audit any number
they do not believe.

``estimate_solar_gain`` is TOTAL: it never raises, for any input combination.
A missing term yields ``gain_w=None`` plus a populated ``unknown_reason``,
never a confidently wrong number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..const import (
    DEFAULT_GROUND_ALBEDO,
    IRRADIANCE_PLANE_WINDOW,
    MIN_GAIN_SUN_ELEVATION_DEG,
    SOLAR_CONSTANT_W_M2,
)

# --- Erbs (1982) correlation coefficients ---------------------------------
# Erbs, D.G., Klein, S.A. & Duffie, J.A., "Estimation of the diffuse radiation
# fraction for hourly, daily and monthly-average global radiation", Solar
# Energy 28(4), 1982. Three branches over the clearness index:
#   kt <= 0.22          kd = 1 - 0.09·kt
#   0.22 < kt <= 0.80   kd = quartic below
#   kt >  0.80          kd = 0.165 (the clear-sky floor)
# These live here, beside the only function that evaluates them, rather than in
# const.py: they are one correlation's coefficients, meaningless apart.
_ERBS_LOW_KT = 0.22
_ERBS_HIGH_KT = 0.80
_ERBS_LOW_SLOPE = 0.09
_ERBS_CLEAR_SKY_FLOOR = 0.165
_ERBS_C0 = 0.9511
_ERBS_C1 = -0.1604
_ERBS_C2 = 4.388
_ERBS_C3 = -16.638
_ERBS_C4 = 12.336

# Days in the eccentricity correction's annual cycle.
_DAYS_PER_YEAR = 365.0
# Amplitude of the Earth-Sun distance correction (±3.3 % over the year).
_ECCENTRICITY_AMPLITUDE = 0.033

# Below this ``sin(elevation)`` the ``1/sin h`` term in the decomposition is
# numerically meaningless. Distinct from ``MIN_GAIN_SUN_ELEVATION_DEG``, which
# is the USER-facing guard; this one only protects the arithmetic.
_MIN_SIN_ELEVATION = 1e-6

#: ``model`` values on :class:`SolarGainEstimate`.
MODEL_ISOTROPIC_ERBS = "isotropic_erbs"
MODEL_PASSTHROUGH = "passthrough"

#: ``model_note`` values — the model ran, but under a stated caveat.
NOTE_SUN_TOO_LOW = "sun_too_low"

#: ``unknown_reason`` values — a term was missing, so there is no watt figure.
UNKNOWN_NO_IRRADIANCE = "no_irradiance"
UNKNOWN_GLASS_AREA = "glass_area_unknown"
UNKNOWN_EFFECTIVE_G = "effective_g_unknown"

#: ``area_source`` values — where the glazed area came from.
AREA_SOURCE_CONFIGURED = "configured"  # the user's explicit override
AREA_SOURCE_DERIVED = "derived"  # height × width from the cover's geometry
AREA_SOURCE_UNKNOWN = "unknown"  # this cover type carries no glass dimensions

#: ``effective_g_source`` value used when issue #1236's feature is switched off.
EFFECTIVE_G_SOURCE_DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class GlassArea:
    """A resolved glazed area and where it came from.

    A dataclass rather than a 2-tuple because the pair appears in a type
    annotation and the two halves are easy to swap positionally — and because
    ``source`` is what makes ``None`` legible to the user ("this cover type
    records no glass dimensions") instead of merely absent.
    """

    area_m2: float | None
    source: str


@dataclass(frozen=True, slots=True)
class PlaneIrradiance:
    """One transposition result: the window-plane irradiance and its parts.

    A dataclass rather than a 4-tuple because it appears in a type annotation
    and carries more than two fields — the project's stated rule. ``dni_w_m2``,
    ``dhi_w_m2`` and ``clearness_index`` are the audit trail behind
    ``poa_w_m2``, and are what let a user check the model instead of trusting
    it.
    """

    poa_w_m2: float
    dni_w_m2: float
    dhi_w_m2: float
    clearness_index: float


@dataclass(frozen=True, slots=True)
class SolarGainEstimate:
    """One cycle's estimated solar gain, with every input that produced it.

    ``gain_w`` is ``None`` exactly when ``unknown_reason`` is populated. It is
    ``0.0`` — a real, asserted zero — when the sun is too low to deliver
    anything, because "no gain right now" is a fact, not missing data.

    Every other field exists so the number is auditable: which plane the
    reading was taken in, which model ran, where the area and the transmittance
    came from, and each irradiance component along the way.
    """

    gain_w: float | None
    poa_w_m2: float | None
    dni_w_m2: float | None
    dhi_w_m2: float | None
    clearness_index: float | None
    ghi_w_m2: float | None
    area_m2: float | None
    area_source: str
    effective_g: float | None
    effective_g_source: str
    cos_aoi: float
    plane_tilt_deg: float
    irradiance_plane: str
    model: str
    unknown_reason: str | None
    model_note: str | None = None


def extraterrestrial_normal_irradiance(day_of_year: int) -> float:
    """Beam irradiance at the top of the atmosphere on day ``n``, W/m².

    ``I0 = S · (1 + 0.033·cos(2πn/365))`` — the solar constant corrected for
    the Earth's orbital eccentricity, peaking at perihelion (early January) and
    troughing at aphelion (early July), a swing of about 6.6 %.

    The cosine is periodic, so a leap-year day 366 or an out-of-range value is
    answered rather than rejected.
    """
    angle = 2.0 * math.pi * float(day_of_year) / _DAYS_PER_YEAR
    return SOLAR_CONSTANT_W_M2 * (1.0 + _ECCENTRICITY_AMPLITUDE * math.cos(angle))


def erbs_diffuse_fraction(kt: float) -> float:
    """Diffuse share of global horizontal irradiance for clearness index ``kt``.

    The Erbs (1982) correlation. Returns 1.0 for a fully overcast sky (all the
    energy arrives diffuse) falling to the 0.165 clear-sky floor. ``kt`` is
    clamped to ``[0, 1]`` first, so a sensor reading above the extraterrestrial
    beam — or a negative one — can never push the fraction outside the unit
    interval.
    """
    clamped = min(max(float(kt), 0.0), 1.0)
    if clamped <= _ERBS_LOW_KT:
        return 1.0 - _ERBS_LOW_SLOPE * clamped
    if clamped <= _ERBS_HIGH_KT:
        return (
            _ERBS_C0
            + _ERBS_C1 * clamped
            + _ERBS_C2 * clamped**2
            + _ERBS_C3 * clamped**3
            + _ERBS_C4 * clamped**4
        )
    return _ERBS_CLEAR_SKY_FLOOR


def plane_of_array_irradiance(
    *,
    ghi: float,
    sin_elev: float,
    cos_aoi: float,
    plane_tilt_deg: float,
    day_of_year: int,
    albedo: float = DEFAULT_GROUND_ALBEDO,
) -> PlaneIrradiance:
    """Transpose a global HORIZONTAL reading onto a tilted plane.

    ``POA = DNI·max(cos AOI, 0) + DHI·(1+cos β)/2 + GHI·ρ·(1−cos β)/2``

    The two view factors are the fraction of the sky dome and of the ground the
    plane sees: a flat skylight (β = 0) sees the whole sky and no ground; a
    vertical window (β = 90) sees half of each. ``max(cos AOI, 0)`` is what
    keeps the sun behind the plane from subtracting energy.

    Beam is clamped at zero and the ``1/sin h`` division is guarded, so the
    result is finite and non-negative for every input — including a sun below
    the horizon, which the caller should normally have short-circuited already.
    """
    ghi_clamped = max(float(ghi), 0.0)
    safe_sin = max(float(sin_elev), _MIN_SIN_ELEVATION)
    i0 = extraterrestrial_normal_irradiance(day_of_year)

    kt = min(max(ghi_clamped / (i0 * safe_sin), 0.0), 1.0)
    dhi = erbs_diffuse_fraction(kt) * ghi_clamped
    dni = max((ghi_clamped - dhi) / safe_sin, 0.0)

    cos_tilt = math.cos(math.radians(float(plane_tilt_deg)))
    sky_view = (1.0 + cos_tilt) / 2.0
    ground_view = (1.0 - cos_tilt) / 2.0
    poa = (
        dni * max(float(cos_aoi), 0.0)
        + dhi * sky_view
        + ghi_clamped * float(albedo) * ground_view
    )
    return PlaneIrradiance(
        poa_w_m2=max(poa, 0.0),
        dni_w_m2=dni,
        dhi_w_m2=dhi,
        clearness_index=kt,
    )


def estimate_solar_gain(
    *,
    ghi_w_m2: float | None,
    irradiance_plane: str,
    sol_elev_deg: float | None,
    cos_aoi: float,
    plane_tilt_deg: float,
    day_of_year: int,
    area_m2: float | None,
    area_source: str,
    effective_g: float | None,
    effective_g_source: str,
    albedo: float = DEFAULT_GROUND_ALBEDO,
) -> SolarGainEstimate:
    """Estimate the instantaneous solar gain through one window, in watts.

    ``ghi_w_m2`` is whatever the user's irradiance sensor reports;
    ``irradiance_plane`` says which plane that reading is taken in. In
    ``window_plane`` mode the reading is already plane-of-array and passes
    straight through — zero transposition error. Otherwise it is treated as
    global horizontal and run through the decomposition + transposition above.

    Below ``MIN_GAIN_SUN_ELEVATION_DEG`` the gain is reported as a hard
    ``0.0`` with ``model_note='sun_too_low'``: the true figure is negligible
    and the ``1/sin h`` term is unstable, so a zero is both truthful and safe.
    A genuinely MISSING term still wins over that zero — an install that can
    never produce a number must say so rather than report 0 W all night and
    ``unknown`` all day.

    Never raises.
    """
    passthrough = irradiance_plane == IRRADIANCE_PLANE_WINDOW
    model = MODEL_PASSTHROUGH if passthrough else MODEL_ISOTROPIC_ERBS
    sun_too_low = (
        sol_elev_deg is None or float(sol_elev_deg) < MIN_GAIN_SUN_ELEVATION_DEG
    )
    model_note = NOTE_SUN_TOO_LOW if sun_too_low else None

    plane: PlaneIrradiance | None = None
    if ghi_w_m2 is not None and not sun_too_low:
        if passthrough:
            plane = PlaneIrradiance(
                poa_w_m2=max(float(ghi_w_m2), 0.0),
                dni_w_m2=None,  # type: ignore[arg-type]
                dhi_w_m2=None,  # type: ignore[arg-type]
                clearness_index=None,  # type: ignore[arg-type]
            )
        else:
            plane = plane_of_array_irradiance(
                ghi=float(ghi_w_m2),
                sin_elev=math.sin(math.radians(float(sol_elev_deg))),
                cos_aoi=cos_aoi,
                plane_tilt_deg=plane_tilt_deg,
                day_of_year=day_of_year,
                albedo=albedo,
            )

    unknown_reason: str | None = None
    gain_w: float | None = None
    if ghi_w_m2 is None:
        unknown_reason = UNKNOWN_NO_IRRADIANCE
    elif area_m2 is None:
        unknown_reason = UNKNOWN_GLASS_AREA
    elif effective_g is None:
        unknown_reason = UNKNOWN_EFFECTIVE_G
    elif sun_too_low or plane is None:
        gain_w = 0.0
    else:
        gain_w = float(area_m2) * plane.poa_w_m2 * float(effective_g)

    return SolarGainEstimate(
        gain_w=gain_w,
        poa_w_m2=plane.poa_w_m2 if plane is not None else None,
        dni_w_m2=plane.dni_w_m2 if plane is not None else None,
        dhi_w_m2=plane.dhi_w_m2 if plane is not None else None,
        clearness_index=plane.clearness_index if plane is not None else None,
        ghi_w_m2=float(ghi_w_m2) if ghi_w_m2 is not None else None,
        area_m2=float(area_m2) if area_m2 is not None else None,
        area_source=area_source,
        effective_g=float(effective_g) if effective_g is not None else None,
        effective_g_source=effective_g_source,
        cos_aoi=float(cos_aoi),
        plane_tilt_deg=float(plane_tilt_deg),
        irradiance_plane=irradiance_plane,
        model=model,
        unknown_reason=unknown_reason,
        model_note=model_note,
    )
