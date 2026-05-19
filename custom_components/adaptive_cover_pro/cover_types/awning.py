"""Horizontal-awning cover policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CONF_AWNING_ANGLE,
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_LENGTH_AWNING,
    DEFAULT_AWNING_LENGTH,
    DEFAULT_WINDOW_HEIGHT,
    MAX_AWNING_ANGLE,
)
from ..engine.covers import AdaptiveHorizontalCover
from .base import (
    CAP_HAS_SET_POSITION,
    POSITION_AXIS_OPEN_BLOCKS_SUN,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


GEOMETRY_HORIZONTAL_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_LENGTH_AWNING, default=DEFAULT_AWNING_LENGTH
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.3,
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_AWNING_ANGLE, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=MAX_AWNING_ANGLE,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
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
    }
)


class AwningPolicy(CoverTypePolicy):
    """Cover that extends horizontally (in/out)."""

    cover_type = "cover_awning"
    # Awning's "open=blocks-sun" semantic is captured on the axis itself so
    # ``position_for_intent`` falls out of the base implementation without any
    # subclass override.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS_OPEN_BLOCKS_SUN,)
    supports_return_to_default_switch = True

    def wiki_anchor(self) -> str:
        """Horizontal-awning geometry page."""
        return "Configuration-Horizontal"

    def display_label(self) -> str:
        """User-facing label for horizontal awnings."""
        return "Horizontal Awning"

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and tilt geometry fields on an awning cover."""
        return [(vertical_only, "vertical blind"), (tilt_only, "tilt")]

    def geometry_schema(self) -> vol.Schema:
        """Return the horizontal-awning geometry schema."""
        return GEOMETRY_HORIZONTAL_SCHEMA

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(self, config: dict[str, Any]) -> list[str]:
        """Render the awning-length / angle / window block."""
        parts: list[str] = []
        if (v := config.get(CONF_LENGTH_AWNING)) is not None:
            parts.append(f"{v}m awning")
        if (v := config.get(CONF_AWNING_ANGLE)) is not None:
            parts.append(f"angled at {v}°")
        if (v := config.get(CONF_HEIGHT_WIN)) is not None:
            parts.append(f"{v}m window height")
        if (v := config.get(CONF_DISTANCE)) is not None:
            parts.append(f"blocking sun {v}m from wall")
        return [", ".join(parts)] if parts else []

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as awning but no bound cover supports "
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
        """Build an ``AdaptiveHorizontalCover`` for in/out awning geometry."""
        return AdaptiveHorizontalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
            horiz_config=config_service.get_horizontal_data(options),
        )
