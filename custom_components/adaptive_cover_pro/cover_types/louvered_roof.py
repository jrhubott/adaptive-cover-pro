"""Louvered / lamella roof cover policy (#830).

A louvered roof ("Lamellendach" / bioclimatic pergola) rotates slats around a
horizontal axis lying in a horizontal or pitched roof plane and reports through
``set_cover_tilt_position`` — a single tilt axis, exactly like ``cover_tilt``.
It reuses the venetian slat geometry (depth, spacing, mode) and adds one field:
the roof-plane ``roof_pitch`` (from horizontal, default 0 = flat).

Modelled on :mod:`tilt` (slat geometry, tilt-capable entity filter, tilt-axis
declaration) and :mod:`roof_window` (the ``roof_pitch`` selector). No edits to
the config-flow bodies, options menu, type picker, or registry are needed — the
type registers itself via ``register=True`` and every config-flow surface
dispatches through the policy hooks below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..config_types import LouveredRoofConfig
from ..const import (
    CONF_MAX_SLAT_ANGLE,
    CONF_ROOF_PITCH,
    DEFAULT_LOUVERED_ROOF_PITCH,
    DEFAULT_MAX_SLAT_ANGLE,
    OPTION_RANGES,
)
from ..engine.covers import AdaptiveLouveredRoofCover
from ._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from .base import (
    CAP_HAS_SET_TILT_POSITION,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)
from .roof_window import _roof_pitch_selector
from .tilt import (
    TILT_CAPABLE_ENTITY_FILTER,
    TILT_SLAT_KEYS,
    TiltPolicy,
    geometry_tilt_schema,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


def _max_slat_angle_selector() -> selector.NumberSelector:
    """Box selector for the optional physical max slat angle (0 = use tilt mode)."""
    lo, hi = OPTION_RANGES[CONF_MAX_SLAT_ANGLE]
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=lo,
            max=hi,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="°",
        )
    )


def geometry_louvered_roof_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Slat geometry (shared with tilt) plus the roof-plane pitch. ``hass=None`` → metric."""
    fields = dict(geometry_tilt_schema(hass).schema)
    fields[vol.Required(CONF_ROOF_PITCH, default=DEFAULT_LOUVERED_ROOF_PITCH)] = (
        _roof_pitch_selector()
    )
    fields[vol.Optional(CONF_MAX_SLAT_ANGLE, default=DEFAULT_MAX_SLAT_ANGLE)] = (
        _max_slat_angle_selector()
    )
    return vol.Schema(fields)


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_LOUVERED_ROOF_SCHEMA = geometry_louvered_roof_schema()


class LouveredRoofPolicy(CoverTypePolicy, register=True):
    """Cover that rotates slats in a horizontal/pitched roof plane (louvered roof)."""

    cover_type = "cover_louvered_roof"
    axes: ClassVar[tuple[CoverAxis, ...]] = (TILT_AXIS,)

    def wiki_anchor(self) -> str:
        """Louvered-roof geometry page."""
        return "Configuration-Louvered-Roof"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for louvered roofs."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.louvered_roof"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and awning geometry; slat geometry is reused."""
        return [(vertical_only, "vertical blind"), (awning_only, "awning")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the louvered-roof (slat + pitch) geometry schema for the locale."""
        if hass is None:
            return GEOMETRY_LOUVERED_ROOF_SCHEMA
        return geometry_louvered_roof_schema(hass)

    def geometry_slat_keys(self) -> tuple[str, ...]:
        """Louvered roofs store slat depth and spacing in canonical centimetres."""
        return TILT_SLAT_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position``."""
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the shared slat block, then the roof pitch and (if set) max angle."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        lines = TiltPolicy().summary_geometry_lines(config, labels)
        parts: list[str] = []
        if (v := config.get(CONF_ROOF_PITCH)) is not None:
            parts.append(L["geometry.roof.pitch"].format(v=v))
        if m := config.get(CONF_MAX_SLAT_ANGLE):  # 0/None → omit
            parts.append(L["geometry.roof.max_slat_angle"].format(v=m))
        if parts:
            extra = ", ".join(parts)
            if lines:
                lines[0] = f"{lines[0]}, {extra}"
            else:
                lines = [extra]
        return lines

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_tilt_position``."""
        if not any(
            caps_get(caps, CAP_HAS_SET_TILT_POSITION) for caps in known.values()
        ):
            return [
                "⚠️ Configured as louvered roof but no bound cover "
                "advertises set_tilt_position."
            ]
        return []

    def build_calc_engine(
        self,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
    ) -> AdaptiveGeneralCover:
        """Build an ``AdaptiveLouveredRoofCover`` (slats in a roof plane)."""
        return AdaptiveLouveredRoofCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=config_service.get_tilt_data(options),
            roof_config=LouveredRoofConfig.from_options(options),
        )
