"""Tests for the pure solar-transmittance engine module (issue #1236).

``engine/solar_transmittance.py`` owns the area-weighted blend that turns a
cover's coverage fraction into an assembly g-value. It has zero Home Assistant
imports and rounds nothing — every value here is exact.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.config_types import SolarPropertiesConfig
from custom_components.adaptive_cover_pro.const import (
    DEFAULT_SOLAR_G_GLAZING,
    SOLAR_G_PRESETS,
)
from custom_components.adaptive_cover_pro.engine.solar_transmittance import (
    SolarTransmittance,
    solar_transmittance,
)


def _cfg(**overrides) -> SolarPropertiesConfig:
    """Return a configured (enabled) external/dark cover unless overridden."""
    defaults = {
        "enabled": True,
        "cover_side": "external",
        "cover_shade": "dark",
        "g_total": None,
        "g_glazing": DEFAULT_SOLAR_G_GLAZING,
    }
    defaults.update(overrides)
    return SolarPropertiesConfig(**defaults)


# ---------------------------------------------------------------------------
# The blend formula
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.0, 0.70), (0.25, 0.58), (1.0, 0.22)],
)
def test_blend_across_coverage_fraction(fraction: float, expected: float) -> None:
    """g_eff = f·g_shaded + (1-f)·g_unshaded, exact at both ends and mid-travel."""
    result = solar_transmittance(_cfg(), shaded_fraction=fraction)
    assert result is not None
    assert result.effective_g == pytest.approx(expected)
    assert result.g_unshaded == pytest.approx(0.70)
    assert result.g_shaded == pytest.approx(0.22)
    assert result.shaded_fraction == pytest.approx(fraction)
    assert result.source == "preset"
    assert result.is_estimate is True


@pytest.mark.unit
def test_result_is_a_frozen_slotted_dataclass() -> None:
    """The seam #1237 consumes is an immutable value object, not a dict."""
    import dataclasses

    result = solar_transmittance(_cfg(), shaded_fraction=0.5)
    assert isinstance(result, SolarTransmittance)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.effective_g = 0.1  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.__dict__  # noqa: B018


@pytest.mark.unit
def test_glass_area_is_not_a_field_of_this_dataclass() -> None:
    """Glass area belongs to #1237, deliberately not to the transmittance seam."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(SolarTransmittance)}
    assert names == {
        "effective_g",
        "g_unshaded",
        "g_shaded",
        "shaded_fraction",
        "source",
        "is_estimate",
    }


@pytest.mark.unit
def test_fraction_is_clamped_to_the_unit_interval() -> None:
    """Out-of-range fractions never push g_eff outside [g_shaded, g_unshaded]."""
    low = solar_transmittance(_cfg(), shaded_fraction=-0.5)
    high = solar_transmittance(_cfg(), shaded_fraction=1.5)
    assert low is not None
    assert high is not None
    assert low.effective_g == pytest.approx(0.70)
    assert high.effective_g == pytest.approx(0.22)


# ---------------------------------------------------------------------------
# Preset table
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("side", "shade", "g_total"),
    [
        ("external", "light", 0.12),
        ("external", "medium", 0.18),
        ("external", "dark", 0.22),
        ("internal", "light", 0.45),
        ("internal", "medium", 0.50),
        ("internal", "dark", 0.55),
    ],
)
def test_every_preset_row(side: str, shade: str, g_total: float) -> None:
    """Each (side, shade) pair resolves to its documented normative midpoint."""
    assert SOLAR_G_PRESETS[(side, shade)] == pytest.approx(g_total)
    result = solar_transmittance(
        _cfg(cover_side=side, cover_shade=shade), shaded_fraction=1.0
    )
    assert result is not None
    assert result.g_shaded == pytest.approx(g_total)
    assert result.effective_g == pytest.approx(g_total)
    assert result.source == "preset"


@pytest.mark.unit
def test_default_glazing_constant_is_the_unshaded_value() -> None:
    """One constant serves the unshaded g-value (and #1237's fallback later)."""
    assert pytest.approx(0.70) == DEFAULT_SOLAR_G_GLAZING


@pytest.mark.unit
def test_unknown_side_or_shade_falls_back_to_a_preset_row() -> None:
    """A junk select value never raises — the function is total."""
    result = solar_transmittance(
        _cfg(cover_side="sideways", cover_shade="chartreuse"), shaded_fraction=1.0
    )
    assert result is not None
    assert 0.0 <= result.g_shaded <= 1.0


# ---------------------------------------------------------------------------
# Direct override
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_direct_g_total_beats_the_preset() -> None:
    """A configured g_total wins over the (side, shade) lookup."""
    result = solar_transmittance(_cfg(g_total=0.33), shaded_fraction=1.0)
    assert result is not None
    assert result.g_shaded == pytest.approx(0.33)
    assert result.effective_g == pytest.approx(0.33)
    assert result.source == "direct"


@pytest.mark.unit
def test_zero_g_total_is_honoured_not_treated_as_unset() -> None:
    """0.0 is a real (fully opaque) value — the read must use ``is not None``."""
    result = solar_transmittance(_cfg(g_total=0.0), shaded_fraction=1.0)
    assert result is not None
    assert result.g_shaded == pytest.approx(0.0)
    assert result.effective_g == pytest.approx(0.0)
    assert result.source == "direct"


# ---------------------------------------------------------------------------
# Feature off / axis-less covers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_disabled_returns_none() -> None:
    """Feature off ⇒ nothing at all, so every downstream surface stays inert."""
    assert solar_transmittance(_cfg(enabled=False), shaded_fraction=0.5) is None


@pytest.mark.unit
def test_shaded_fraction_none_reports_shaded_only() -> None:
    """A rotation axis has no covered fraction — the caller must not guess."""
    result = solar_transmittance(_cfg(), shaded_fraction=None)
    assert result is not None
    assert result.shaded_fraction is None
    assert result.effective_g == pytest.approx(result.g_shaded)
    assert result.effective_g == pytest.approx(0.22)
    assert result.source == "shaded_only"


@pytest.mark.unit
def test_shaded_only_wins_over_direct_source_label() -> None:
    """``shaded_only`` describes the *blend*, so it outranks the g-value origin."""
    result = solar_transmittance(_cfg(g_total=0.33), shaded_fraction=None)
    assert result is not None
    assert result.source == "shaded_only"
    assert result.g_shaded == pytest.approx(0.33)


# ---------------------------------------------------------------------------
# No rounding in the engine (CODING_GUIDELINES § display rounding)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_engine_rounds_nothing() -> None:
    """Full float precision survives — rounding is the builder's job."""
    result = solar_transmittance(
        _cfg(g_glazing=0.6789, g_total=0.1234), shaded_fraction=0.3333
    )
    assert result is not None
    expected = 0.3333 * 0.1234 + (1 - 0.3333) * 0.6789
    assert result.effective_g == pytest.approx(expected, abs=0.0)
