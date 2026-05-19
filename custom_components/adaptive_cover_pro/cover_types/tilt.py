"""Tilt-only cover policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import CONF_TILT_DEPTH, CONF_TILT_DISTANCE, CONF_TILT_MODE
from ..engine.covers import AdaptiveTiltCover
from .base import (
    CAP_HAS_SET_TILT_POSITION,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


GEOMETRY_TILT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TILT_DEPTH, default=3): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_DISTANCE, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_MODE, default="mode2"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["mode1", "mode2"], translation_key="tilt_mode"
            )
        ),
    }
)


# Filter shared by tilt and venetian: cover entities that expose
# ``set_tilt_position``. HA's ``supported_features`` filter is OR-of-listed,
# not AND, so venetian uses this same filter and surfaces the
# missing-set_position case as a config-flow capability warning.
TILT_CAPABLE_ENTITY_FILTER = selector.EntityFilterSelectorConfig(
    domain="cover",
    supported_features=["cover.CoverEntityFeature.SET_TILT_POSITION"],
)


class TiltPolicy(CoverTypePolicy):
    """Cover that rotates slats only (no vertical movement)."""

    cover_type = "cover_tilt"
    axes: ClassVar[tuple[CoverAxis, ...]] = (TILT_AXIS,)

    def wiki_anchor(self) -> str:
        """Slat-tilt geometry page."""
        return "Configuration-Tilt"

    def display_label(self) -> str:
        """User-facing label for tilt-only covers."""
        return "Venetian / Tilt Blind"

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and awning geometry fields on a tilt-only cover."""
        return [(vertical_only, "vertical blind"), (awning_only, "awning")]

    def geometry_schema(self) -> vol.Schema:
        """Return the slat-only geometry schema."""
        return GEOMETRY_TILT_SCHEMA

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position``."""
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(self, config: dict[str, Any]) -> list[str]:
        """Render the slat-depth / spacing / mode block."""
        parts: list[str] = []
        if (v := config.get(CONF_TILT_DEPTH)) is not None:
            parts.append(f"slat depth {v}cm")
        if (v := config.get(CONF_TILT_DISTANCE)) is not None:
            parts.append(f"spacing {v}cm")
        if (v := config.get(CONF_TILT_MODE)) is not None:
            parts.append(f"mode: {v}")
        return [", ".join(parts)] if parts else []

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_tilt_position``."""
        if not any(
            caps_get(caps, CAP_HAS_SET_TILT_POSITION) for caps in known.values()
        ):
            return [
                "⚠️ Configured as tilt (venetian) but no bound cover "
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
        """Build an ``AdaptiveTiltCover`` for slat-only covers."""
        return AdaptiveTiltCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=config_service.get_tilt_data(options),
        )
