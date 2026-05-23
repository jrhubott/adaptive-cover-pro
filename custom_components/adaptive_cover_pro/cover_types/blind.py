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
from ..unit_system import length_default, length_selector
from ._helpers import window_dimensions_lines
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


# Keys whose stored value is canonical metres — used by config-flow steps to
# convert between stored canonical and display-unit on form load/submit.
VERTICAL_LENGTH_KEYS: tuple[str, ...] = (
    CONF_HEIGHT_WIN,
    CONF_WINDOW_WIDTH,
    CONF_WINDOW_DEPTH,
    CONF_SILL_HEIGHT,
)


def geometry_vertical_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Vertical-blind geometry schema. ``hass=None`` → metric labels."""
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
        }
    )


# Module-level constant for backward compatibility with test imports that
# inspect schema keys / call the schema as a validator. Built without hass
# (== metric labels), identical to the historical schema.
GEOMETRY_VERTICAL_SCHEMA = geometry_vertical_schema()


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

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Vertical blinds travel the configured window height."""
        return config_service.get_vertical_data(options).h_win

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the vertical-blind geometry schema for the given locale.

        Returns the cached module-level constant when no locale is supplied so
        identity-checking tests keep passing; builds a fresh schema otherwise.
        """
        if hass is None:
            return GEOMETRY_VERTICAL_SCHEMA
        return geometry_vertical_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Vertical blinds store four window dimensions in canonical metres."""
        return VERTICAL_LENGTH_KEYS

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
