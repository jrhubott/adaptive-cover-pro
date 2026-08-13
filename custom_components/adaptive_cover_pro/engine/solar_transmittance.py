"""Solar-energy transmittance of the glazing + cover assembly (issue #1236).

Pure module — zero Home Assistant imports, no rounding, no I/O. It answers one
question: *of the solar energy striking this window, what share reaches the
room at the cover's current position?*

The model is an **area-weighted blend**, which is the dominant first-order
term::

    g_eff(p) = f(p)·g_shaded + (1 − f(p))·g_unshaded

``f(p)`` is the share of the glazing the cover actually covers at position
``p`` — supplied by the caller from ``CoverTypePolicy.shaded_glass_fraction``
so no cover-type knowledge leaks in here. ``g_shaded`` is the fully-covered
assembly g-value (the user's direct ``g_total`` if declared, otherwise the
``(side, shade)`` preset); ``g_unshaded`` is the bare glazing.

Deliberately NOT modelled: ventilated-cavity behaviour (EN 13363-2 keys it to
the cavity air-change rate, which the integration cannot observe) and venetian
slat angle. Both are second-order next to the area blend, and a future
refinement changes only how ``g_shaded`` is derived *inside* this function —
the dataclass and the signature are stable.

Every number produced here is an ESTIMATE. ``is_estimate`` is always ``True``
in v1 and exists so consumers never have to hard-code that caveat.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config_types import SolarPropertiesConfig
from ..const import (
    DEFAULT_SOLAR_COVER_SHADE,
    DEFAULT_SOLAR_COVER_SIDE,
    SOLAR_G_PRESETS,
)

#: ``source`` values on :class:`SolarTransmittance` — PROVENANCE, and nothing
#: else. Whether the cover has an area-coverage axis to blend along is a
#: separate, orthogonal fact, and ``shaded_fraction is None`` already says it on
#: the same dataclass. Folding the two into one field erased the first: a tilt
#: cover with a hand-entered ``g_total`` reported as preset-derived, and the
#: Troubleshoot finding then quoted a shade word and a preset comparison at a
#: number the user had typed in themselves.
SOURCE_DIRECT = "direct"  # the user declared g_total outright
SOURCE_PRESET = "preset"  # looked up from (cover_side, cover_shade)

_FRACTION_MIN = 0.0
_FRACTION_MAX = 1.0


@dataclass(frozen=True, slots=True)
class SolarTransmittance:
    """The assembly's estimated solar transmittance for one cycle.

    ``effective_g`` is the headline figure — the share of incident solar energy
    the glazing + cover assembly admits at the cover's current position.

    Two independent facts, two fields, deliberately never merged:

    * ``shaded_fraction`` is ``None`` when the cover's primary axis is a
      rotation rather than an area coverage (tilt-only types). No blend is
      possible, so ``effective_g`` falls back to ``g_shaded`` rather than being
      guessed.
    * ``source`` says where ``g_shaded`` came from — the user's own
      ``g_total`` (``direct``) or the ``(side, shade)`` table (``preset``) —
      and is unaffected by which cover type is asking.

    Glass area is deliberately NOT a field here: it is a geometry quantity, not
    a transmittance one, and it belongs to the estimated-solar-gain feature.
    """

    effective_g: float
    g_unshaded: float
    g_shaded: float
    shaded_fraction: float | None
    source: str
    is_estimate: bool = True


def _preset_g_total(cover_side: str, cover_shade: str) -> float:
    """Look up the fully-covered preset, falling back to the shipped defaults.

    Total by construction: a stored select value that no longer exists (a
    renamed option, a hand-edited entry) resolves to the default pair rather
    than raising inside a diagnostics path.
    """
    try:
        return SOLAR_G_PRESETS[(cover_side, cover_shade)]
    except KeyError:
        return SOLAR_G_PRESETS[(DEFAULT_SOLAR_COVER_SIDE, DEFAULT_SOLAR_COVER_SHADE)]


def solar_transmittance(
    cfg: SolarPropertiesConfig, *, shaded_fraction: float | None
) -> SolarTransmittance | None:
    """Estimate the assembly's transmittance, or ``None`` when the feature is off.

    ``shaded_fraction`` is the share of the glazing the cover covers right now
    (0.0-1.0), or ``None`` for a cover whose primary axis is not an
    area-coverage axis. It is clamped to the unit interval so a caller that
    hands over an out-of-range position can never push ``effective_g`` outside
    ``[g_shaded, g_unshaded]``. ``None`` means there is no blend to run, so
    ``effective_g`` collapses onto ``g_shaded`` — a fact carried by the null
    fraction itself, which is why it never touches ``source``.

    Never raises: an unknown ``cover_side``/``cover_shade`` falls back to the
    default preset row. The only ``None`` return is "feature not enabled".
    """
    if not cfg.enabled:
        return None

    g_unshaded = float(cfg.g_glazing)
    # ``is not None`` on purpose — 0.0 is a real, fully-opaque declared value.
    direct = cfg.g_total is not None
    g_shaded = (
        float(cfg.g_total)
        if direct
        else _preset_g_total(cfg.cover_side, cfg.cover_shade)
    )

    if shaded_fraction is None:
        fraction = None
        effective_g = g_shaded
    else:
        fraction = min(max(float(shaded_fraction), _FRACTION_MIN), _FRACTION_MAX)
        effective_g = fraction * g_shaded + (1.0 - fraction) * g_unshaded

    return SolarTransmittance(
        effective_g=effective_g,
        g_unshaded=g_unshaded,
        g_shaded=g_shaded,
        shaded_fraction=fraction,
        source=SOURCE_DIRECT if direct else SOURCE_PRESET,
    )
