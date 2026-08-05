"""Physical-blocking tests for the MODE2 climate tilt helper (issue #1088).

Every pre-existing tilt test pins a *number* (``assert result == 75``). Because
those numbers were read off the same branch they were meant to guard, the suite
ratified a helper that pointed the slats at the open hemisphere for half the sky.

These tests assert the *physics* instead: that the angle
``TiltPolicy.climate_tilt_percentage`` returns actually blocks the direct beam,
scored by an aperture oracle that is itself validated against the production
cut-off solver rather than asserted.

The oracle
----------
A slat of chord ``w`` tilted ``theta`` from horizontal, stacked at vertical
spacing ``s``, against a beam arriving at profile angle ``beta``::

    theta = 90 - phi
    reach = w*sin(theta) + w*cos(theta)*tan(beta)
    slack = s - abs(reach)

``slack > 0`` means the aperture is still open and direct sun passes.

``reach`` collapses to ``(w/cos beta) * cos(phi - beta)``, which makes the
structure explicit: transmission peaks at ``phi = beta + 90`` (the slat lies
parallel to the beam) and the blocked band is centred on ``phi = beta``. Both
forms are checked against each other below.
"""

from __future__ import annotations

import inspect
import math
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CLIMATE_DEFAULT_TILT_ANGLE,
    CLIMATE_SUMMER_TILT_ANGLE,
    TiltMode,
)
from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy
from custom_components.adaptive_cover_pro.engine.covers.tilt import slat_cutoff_angle
from custom_components.adaptive_cover_pro.engine.sun_geometry import (
    foreshortened_slope,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate_modes import (
    ClimateContext,
    _tilt_default,
    _tilt_summer,
    _tilt_winter_mode2,
)

# Representative venetian/louvre geometries, in cm: (slat depth, slat spacing).
# Spread across depth/spacing ratios so a result that only holds for one blind
# can't pass. 17.0/15.0 mirrors the louvered-roof defaults.
SLAT_GEOMETRIES = [(8.0, 7.5), (2.5, 2.5), (5.0, 4.0), (6.0, 5.0), (17.0, 15.0)]

# Sun positions to score. Gammas are mirrored pairs on purpose — the whole point
# of #1088 is that the answer must not depend on the sign.
ELEVATIONS = [15.0, 20.0, 38.0, 45.0, 60.0, 75.0]
MIRRORED_GAMMAS = [0.0, 5.0, 28.4, 40.0, 60.0, 80.0]


def profile_angle_deg(elevation_deg: float, gamma_deg: float) -> float:
    """Beta, via the same helper the production tilt engine uses."""
    return math.degrees(math.atan(foreshortened_slope(elevation_deg, gamma_deg)))


def aperture_slack(
    phi_deg: float, beta_deg: float, depth: float, spacing: float
) -> float:
    """Remaining open aperture, in cm. ``> 0`` means direct sun passes."""
    theta = math.radians(90.0 - phi_deg)
    beta = math.radians(beta_deg)
    reach = depth * math.sin(theta) + depth * math.cos(theta) * math.tan(beta)
    return spacing - abs(reach)


def _aperture_slack_closed_form(
    phi_deg: float, beta_deg: float, depth: float, spacing: float
) -> float:
    """``slack`` rewritten as ``s - (w/cos beta)*|cos(phi - beta)|``."""
    beta = math.radians(beta_deg)
    return spacing - (depth / math.cos(beta)) * abs(
        math.cos(math.radians(phi_deg) - beta)
    )


def tilt_percentage_to_angle(percentage: int) -> float:
    """MODE2 tilt percentage back to the raw slat angle it commands."""
    return percentage * (TiltMode.MODE2.max_degrees / 100.0)


def commanded_angle(angle_deg: float, *, sun_through: bool = False) -> float:
    """Raw slat angle the climate helper commands for this target angle."""
    return tilt_percentage_to_angle(
        TiltPolicy.climate_tilt_percentage(
            angle_deg=angle_deg,
            mode=TiltMode.MODE2,
            sun_through=sun_through,
        )
    )


# ---------------------------------------------------------------------------
# The oracle is validated, not asserted
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(("depth", "spacing"), SLAT_GEOMETRIES)
@pytest.mark.parametrize("elevation", ELEVATIONS)
def test_oracle_agrees_with_production_cutoff_solver(depth, spacing, elevation):
    """At the angle the engine calls "grazing", the oracle's slack must be 0.

    ``slat_cutoff_angle`` is the production MDPI cut-off solve. It returns the
    most-open slat angle that still blocks the beam, so by construction the
    aperture is exactly closed there. If the oracle disagrees, the oracle is
    wrong and every physical assertion below is worthless — so this runs first.
    """
    for gamma in (0.0, -28.4, 28.4, 60.0):
        beta = profile_angle_deg(elevation, gamma)
        cutoff, _discriminant, negative = slat_cutoff_angle(
            math.radians(beta), spacing, depth
        )
        if negative:
            continue  # no real cut-off for this geometry; engine returns closed
        assert aperture_slack(cutoff, beta, depth, spacing) == pytest.approx(
            0.0, abs=1e-9
        ), (
            f"Oracle disagrees with slat_cutoff_angle at elev={elevation} "
            f"gamma={gamma} beta={beta:.4f} cutoff={cutoff:.4f}"
        )


@pytest.mark.unit
def test_oracle_matches_its_closed_form():
    """The raw and ``cos(phi - beta)`` forms must agree — they are one formula."""
    for phi in range(0, 181, 3):
        for beta in range(0, 90, 3):
            raw = aperture_slack(float(phi), float(beta), 8.0, 7.5)
            closed = _aperture_slack_closed_form(float(phi), float(beta), 8.0, 7.5)
            assert raw == pytest.approx(closed, abs=1e-9)


# ---------------------------------------------------------------------------
# Issue #1088 — the helper must be even in gamma, and must block
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_climate_tilt_percentage_takes_no_gamma_argument():
    """Gamma-independence is structural, not a value the helper happens to ignore.

    Slats rotate about a horizontal axis parallel to the facade, so the sun's
    left/right offset reaches the geometry only through
    ``beta = arctan(tan(elev)/cos(gamma))`` — and ``cos`` is even. An answer that
    varied with the *sign* of gamma could only be right on one side, and
    pre-#1088 the ``gamma >= 0`` side was the wrong one.

    Keeping the parameter around as an ignored no-op would leave the same trap
    for the next caller, so the signature is the guard.
    """
    params = inspect.signature(TiltPolicy.climate_tilt_percentage).parameters
    assert "gamma_deg" not in params, (
        "climate_tilt_percentage must not accept a sun-azimuth argument — slat "
        f"tilt is even in gamma (issue #1088). Got: {list(params)}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("gamma", [-1e-9, +1e-9, *MIRRORED_GAMMAS, -40.0, -80.0])
def test_live_climate_tilt_path_is_gamma_independent(gamma):
    """The climate mode's own tilt rules must not vary with the sun's side.

    Exercises the real ``climate_modes`` position functions rather than the
    helper directly, so the invariant is pinned at the layer that reads
    ``ctx.gamma_deg``. Pre-#1088 ``_tilt_summer`` stepped 25% -> 75% between
    gamma -1e-9 and +1e-9.
    """
    cover = MagicMock()
    cover.mode = TiltMode.MODE2

    def context(gamma_deg: float) -> ClimateContext:
        return ClimateContext(
            data=MagicMock(),
            cover=cover,
            default_position=50,
            solar_position=lambda: 50,
            gamma_deg=gamma_deg,
            beta_deg=30.0,
        )

    reference = context(0.0)
    candidate = context(gamma)
    for name, position_fn in (
        ("_tilt_summer", _tilt_summer),
        ("_tilt_default", _tilt_default),
        ("_tilt_winter_mode2", _tilt_winter_mode2),
    ):
        assert position_fn(candidate) == position_fn(reference), (
            f"{name} varies with gamma ({gamma} vs 0.0): "
            f"{position_fn(candidate)}% vs {position_fn(reference)}%"
        )


@pytest.mark.unit
@pytest.mark.parametrize(("depth", "spacing"), SLAT_GEOMETRIES)
@pytest.mark.parametrize("gamma", MIRRORED_GAMMAS)
@pytest.mark.parametrize("elevation", ELEVATIONS)
def test_summer_tilt_never_picks_the_leakier_hemisphere(
    depth, spacing, elevation, gamma
):
    """The commanded angle must never be the leakier of the two hemispheres.

    A fixed climate angle can't block at every sun position (it isn't the
    geometric solve — that's ``AdaptiveTiltCover.calculate_position``). What it
    *must* never do is pick the mirror angle when the mirror leaks and the other
    blocks. Pre-#1088 the ``gamma >= 0`` branch did exactly that.
    """
    for signed_gamma in (-gamma, +gamma):
        beta = profile_angle_deg(elevation, signed_gamma)
        commanded = commanded_angle(CLIMATE_SUMMER_TILT_ANGLE)
        mirror = TiltMode.MODE2.max_degrees - commanded

        commanded_slack = aperture_slack(commanded, beta, depth, spacing)
        mirror_slack = aperture_slack(mirror, beta, depth, spacing)

        assert commanded_slack <= mirror_slack + 1e-9, (
            f"Commanded slat angle {commanded:.1f}deg leaks more than its mirror "
            f"{mirror:.1f}deg at elev={elevation} gamma={signed_gamma} "
            f"beta={beta:.2f}: slack {commanded_slack:+.3f} vs "
            f"{mirror_slack:+.3f} (w={depth} s={spacing})"
        )


@pytest.mark.unit
@pytest.mark.parametrize("elevation", [20.0, 38.0, 60.0])
@pytest.mark.parametrize("gamma", [-28.4, 28.4])
def test_summer_tilt_blocks_the_beam_for_the_reported_geometry(elevation, gamma):
    """The exact table in #1088 must come out blocked, both gamma signs.

    Slat depth 8.0 cm / spacing 7.5 cm at elevations 20/38/60 with |gamma|=28.4
    is the case the issue tabulates, where the pre-fix helper returned 75%
    (phi=135deg) and let direct sun straight through (slack +4.184 / +6.867 /
    +2.018).

    Scoped to that one geometry on purpose. A *fixed* climate angle cannot block
    at every sun position for every blind — at low beta the blocked band never
    reaches 45deg — so "always blocks" is not a property this helper has, and
    asserting it would be asserting something false. The universal invariant is
    the hemisphere choice, pinned by
    ``test_summer_tilt_never_picks_the_leakier_hemisphere`` above.
    """
    depth, spacing = 8.0, 7.5
    beta = profile_angle_deg(elevation, gamma)
    commanded = commanded_angle(CLIMATE_SUMMER_TILT_ANGLE)
    slack = aperture_slack(commanded, beta, depth, spacing)
    assert slack <= 0, (
        f"Direct sun passes at elev={elevation} gamma={gamma} "
        f"(beta={beta:.2f}, commanded phi={commanded:.1f}deg, slack={slack:+.3f}, "
        f"w={depth} s={spacing})"
    )


# ---------------------------------------------------------------------------
# The sun_through (winter heating) arm
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(("depth", "spacing"), SLAT_GEOMETRIES)
@pytest.mark.parametrize("beta", [10.0, 22.48, 41.61, 63.08])
@pytest.mark.parametrize("gamma", [-40.0, 0.0, 40.0])
def test_sun_through_lands_on_maximum_transmission(depth, spacing, beta, gamma):
    """Winter heating must open the aperture fully, not close it.

    ``phi = 90 + beta`` puts the slat parallel to the beam, so the aperture is
    completely unobstructed and ``slack`` equals the full spacing. The mirror
    (``90 - beta``) is materially worse and fully blocking past ~35deg — which
    is what the pre-#1088 ``gamma < 0`` arm commanded.
    """
    commanded = commanded_angle(beta, sun_through=True)
    slack = aperture_slack(commanded, beta, depth, spacing)
    assert slack == pytest.approx(spacing, abs=0.05 * spacing), (
        f"Winter heating should open the aperture (slack ~= spacing {spacing}) "
        f"but commanded phi={commanded:.1f}deg gives slack={slack:.3f} "
        f"at beta={beta} gamma={gamma}"
    )


@pytest.mark.unit
def test_mode1_is_untouched_by_the_hemisphere_fix():
    """MODE1 returns early and has no hemisphere concept — pin that it stays so."""
    for angle_deg in (CLIMATE_SUMMER_TILT_ANGLE, CLIMATE_DEFAULT_TILT_ANGLE):
        expected = round((angle_deg / TiltMode.MODE1.max_degrees) * 100)
        for sun_through in (False, True):
            assert (
                TiltPolicy.climate_tilt_percentage(
                    angle_deg=angle_deg,
                    mode=TiltMode.MODE1,
                    sun_through=sun_through,
                )
                == expected
            )
