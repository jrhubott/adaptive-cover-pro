"""Roof / skylight window cover policy (#212).

A roof window travels *down the slope* across pitched glass, controlled like a
vertical blind on a single position axis (``open_blocks_sun=False``, inverse
state inherited unchanged). It reuses the vertical window geometry — height,
width, reveal depth, sill — and adds two fields: the glass ``roof_pitch`` (from
horizontal) and the along-slope ``roof_height_above`` the window that drives the
ridge occlusion gate.

Modelled on :mod:`blind` (window geometry, FOV-from-measurements) and
:mod:`oscillating_awning` (extra geometry fields + a dedicated config
dataclass). No edits to the config-flow bodies, options menu, type picker, or
registry are needed — the type registers itself via ``register=True`` and every
config-flow surface dispatches through the policy hooks below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..config_types import RoofWindowConfig
from ..const import (
    CONF_HEIGHT_WIN,
    CONF_ROOF_HEIGHT_ABOVE,
    CONF_ROOF_PITCH,
    CONF_SILL_HEIGHT,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    DEFAULT_ROOF_HEIGHT_ABOVE,
    DEFAULT_ROOF_PITCH,
    DEFAULT_WINDOW_HEIGHT,
    MAX_WINDOW_DEPTH,
    _RANGE_ROOF_HEIGHT_ABOVE,
    _RANGE_ROOF_PITCH,
)
from ..engine.covers import AdaptiveRoofWindowCover
from ..unit_system import length_default, length_selector
from ._helpers import window_dimensions_lines
from ._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from .base import (
    CAP_HAS_SET_POSITION,
    POSITION_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


# Option keys stored in canonical metres (config-flow unit conversion). Pitch is
# an angle, so it is NOT a length key; roof_height_above IS a length.
ROOF_WINDOW_LENGTH_KEYS: tuple[str, ...] = (
    CONF_HEIGHT_WIN,
    CONF_WINDOW_WIDTH,
    CONF_WINDOW_DEPTH,
    CONF_SILL_HEIGHT,
    CONF_ROOF_HEIGHT_ABOVE,
)


def _roof_pitch_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=_RANGE_ROOF_PITCH[0],
            max=_RANGE_ROOF_PITCH[1],
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
            unit_of_measurement="°",
        )
    )


def geometry_roof_window_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Roof-window geometry schema. ``hass=None`` → metric labels."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HEIGHT_WIN,
                default=length_default(DEFAULT_WINDOW_HEIGHT, hass),
            ): length_selector(hass, min_m=0.1, max_m=50, metric_step=0.01),
            vol.Optional(
                CONF_WINDOW_WIDTH, default=length_default(1.0, hass)
            ): length_selector(hass, min_m=0.1, max_m=50, metric_step=0.01),
            vol.Optional(
                CONF_WINDOW_DEPTH, default=length_default(0.0, hass)
            ): length_selector(
                hass,
                min_m=0.0,
                max_m=MAX_WINDOW_DEPTH,
                metric_step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
            ),
            vol.Optional(
                CONF_SILL_HEIGHT, default=length_default(0.0, hass)
            ): length_selector(hass, min_m=0.0, max_m=50, metric_step=0.01),
            vol.Required(
                CONF_ROOF_PITCH, default=DEFAULT_ROOF_PITCH
            ): _roof_pitch_selector(),
            vol.Optional(
                CONF_ROOF_HEIGHT_ABOVE,
                default=length_default(DEFAULT_ROOF_HEIGHT_ABOVE, hass),
            ): length_selector(
                hass,
                min_m=_RANGE_ROOF_HEIGHT_ABOVE[0],
                max_m=_RANGE_ROOF_HEIGHT_ABOVE[1],
                metric_step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
            ),
        }
    )


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_ROOF_WINDOW_SCHEMA = geometry_roof_window_schema()


class RoofWindowPolicy(CoverTypePolicy, register=True):
    """Cover that travels down-slope across pitched glass (roof / skylight)."""

    cover_type = "cover_roof_window"
    # Same "open=lets-sun-through" semantic as a vertical blind, so inverse
    # state, position_for_intent and more_protective_position all fall out of
    # the base implementation with no override.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    supports_return_to_default_switch = True
    supports_fov_compute = True

    def wiki_anchor(self) -> str:
        """Roof-window geometry page."""
        return "Configuration-Roof-Window"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for roof windows."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.roof_window"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject awning and tilt geometry; window dimensions are reused."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the roof-window geometry schema for the given locale."""
        if hass is None:
            return GEOMETRY_ROOF_WINDOW_SCHEMA
        return geometry_roof_window_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Window dims + roof-height-above are stored in metres (pitch is an angle)."""
        return ROOF_WINDOW_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the window-dimensions block plus the roof pitch / ridge height."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        lines = window_dimensions_lines(config, labels)
        parts: list[str] = []
        if (v := config.get(CONF_ROOF_PITCH)) is not None:
            parts.append(L["geometry.roof.pitch"].format(v=v))
        if (v := config.get(CONF_ROOF_HEIGHT_ABOVE)) is not None and v > 0:
            parts.append(L["geometry.roof.height_above"].format(v=v))
        if parts:
            lines.append(", ".join(parts))
        return lines

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as roof window but no bound cover supports "
                "set_position — only open/close will be issued."
            ]
        return []

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Roof windows travel the configured window height down the slope."""
        return config_service.get_vertical_data(options).h_win

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
        """Build an ``AdaptiveRoofWindowCover`` (pitched-glass geometry)."""
        return AdaptiveRoofWindowCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
            roof_config=RoofWindowConfig.from_options(options),
        )
