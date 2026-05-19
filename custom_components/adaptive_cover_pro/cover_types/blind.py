"""Vertical-blind cover policy."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CONF_HEIGHT_WIN,
    CONF_SILL_HEIGHT,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    DEFAULT_WINDOW_HEIGHT,
    MAX_WINDOW_DEPTH,
)
from ..engine.covers import AdaptiveVerticalCover
from ._helpers import window_dimensions_lines
from .base import (
    CAP_HAS_SET_POSITION,
    POSITION_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


GEOMETRY_VERTICAL_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_HEIGHT_WIN, default=DEFAULT_WINDOW_HEIGHT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_WIDTH, default=1.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_DEPTH, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=MAX_WINDOW_DEPTH,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_SILL_HEIGHT, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=50,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="m",
            )
        ),
    }
)


class BlindPolicy(CoverTypePolicy):
    """Cover that moves vertically (raise/lower)."""

    cover_type = "cover_blind"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    supports_glare_zones = True
    supports_return_to_default_switch = True

    def wiki_anchor(self) -> str:
        """Vertical-blind geometry page."""
        return "Configuration-Vertical"

    def display_label(self) -> str:
        """User-facing label for vertical blinds."""
        return "Vertical Blind"

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject awning and tilt geometry fields on a vertical blind."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def glare_zones_config(self, config_service, options: dict):
        """Return the glare-zones config for this cover (vertical-only feature)."""
        return config_service.get_glare_zones_config(options)

    def geometry_schema(self) -> vol.Schema:
        """Return the vertical-blind geometry schema."""
        return GEOMETRY_VERTICAL_SCHEMA

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(self, config: dict[str, Any]) -> list[str]:
        """Render the window-dimensions block."""
        return window_dimensions_lines(config)

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as vertical blind but no bound cover supports "
                "set_position — only open/close will be issued."
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
        """Build an ``AdaptiveVerticalCover``, threading glare zones if any."""
        vert_config = config_service.get_vertical_data(options)
        glare_zones_cfg = config_service.get_glare_zones_config(options)
        if glare_zones_cfg is not None:
            vert_config = replace(vert_config, glare_zones=glare_zones_cfg)
        return AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=vert_config,
        )
