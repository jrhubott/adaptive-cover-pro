"""Horizontal sliding-curtain cover policy (#829).

A sliding curtain draws its fabric sideways across the window opening. It is a
single position-axis cover — same "open lets sun through / closed blocks sun"
semantic as a vertical blind, so ``position_for_intent``,
``more_protective_position`` and the inverse state all fall out of the base with
no override.

Part 1 modelled it as binary open/close. Part 2 adds an optional two-point shade
area (floor Cartesian points in the glare-zone frame) that lets the curtain close
a continuous fraction just wide enough to keep that floor interval in shadow. The
shade-area geometry composes on via the policy's ``geometry_schema`` /
``geometry_length_keys`` / ``summary_geometry_lines`` hooks and a populated
``SlidingCurtainConfig`` threaded into the engine — no edits to the config-flow
bodies, options menu, type picker, or registry are needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..config_types import SlidingCurtainConfig
from ..const import (
    CONF_SLIDING_ENABLE_SHADE_AREA,
    CONF_SLIDING_POINT1_X,
    CONF_SLIDING_POINT1_Y,
    CONF_SLIDING_POINT2_X,
    CONF_SLIDING_POINT2_Y,
    CONF_SLIDING_SLIDE_DIRECTION,
    CONF_WINDOW_WIDTH,
    DEFAULT_SLIDING_ENABLE_SHADE_AREA,
    DEFAULT_SLIDING_SLIDE_DIRECTION,
    SLIDING_SLIDE_DIRECTIONS,
    _RANGE_SLIDING_POINT_X,
    _RANGE_SLIDING_POINT_Y,
)
from ..engine.covers import AdaptiveSlidingCurtainCover
from ..unit_system import length_default, length_selector
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


# Option keys stored in canonical metres (config-flow unit conversion). The
# slide direction is a select and the enable flag a bool, so neither is a length.
SLIDING_LENGTH_KEYS: tuple[str, ...] = (
    CONF_WINDOW_WIDTH,
    CONF_SLIDING_POINT1_X,
    CONF_SLIDING_POINT1_Y,
    CONF_SLIDING_POINT2_X,
    CONF_SLIDING_POINT2_Y,
)

# Human-readable slide-direction summary label keys.
_SLIDE_DIR_LABEL_KEYS: dict[str, str] = {
    "left": "geometry.sliding.dir_left",
    "right": "geometry.sliding.dir_right",
    "bi_part": "geometry.sliding.dir_bi_part",
}


def geometry_sliding_curtain_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Sliding-curtain geometry schema. ``hass=None`` → metric labels."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_WINDOW_WIDTH, default=length_default(1.0, hass)
            ): length_selector(hass, min_m=0.1, max_m=50, metric_step=0.01),
            vol.Optional(
                CONF_SLIDING_ENABLE_SHADE_AREA,
                default=DEFAULT_SLIDING_ENABLE_SHADE_AREA,
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_SLIDING_SLIDE_DIRECTION,
                default=DEFAULT_SLIDING_SLIDE_DIRECTION,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SLIDING_SLIDE_DIRECTIONS),
                    translation_key="sliding_slide_direction",
                )
            ),
            vol.Optional(
                CONF_SLIDING_POINT1_X, default=length_default(0.0, hass)
            ): length_selector(
                hass,
                min_m=_RANGE_SLIDING_POINT_X[0],
                max_m=_RANGE_SLIDING_POINT_X[1],
                metric_step=0.01,
            ),
            vol.Optional(
                CONF_SLIDING_POINT1_Y, default=length_default(0.0, hass)
            ): length_selector(
                hass,
                min_m=_RANGE_SLIDING_POINT_Y[0],
                max_m=_RANGE_SLIDING_POINT_Y[1],
                metric_step=0.01,
            ),
            vol.Optional(
                CONF_SLIDING_POINT2_X, default=length_default(0.0, hass)
            ): length_selector(
                hass,
                min_m=_RANGE_SLIDING_POINT_X[0],
                max_m=_RANGE_SLIDING_POINT_X[1],
                metric_step=0.01,
            ),
            vol.Optional(
                CONF_SLIDING_POINT2_Y, default=length_default(0.0, hass)
            ): length_selector(
                hass,
                min_m=_RANGE_SLIDING_POINT_Y[0],
                max_m=_RANGE_SLIDING_POINT_Y[1],
                metric_step=0.01,
            ),
        }
    )


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_SLIDING_CURTAIN_SCHEMA = geometry_sliding_curtain_schema()


class SlidingCurtainPolicy(CoverTypePolicy, register=True):
    """Cover that slides horizontally across the window (binary or shade-area)."""

    cover_type = "cover_sliding_curtain"
    # Same "open=lets-sun-through" semantic as a vertical blind, so inverse
    # state, position_for_intent and more_protective_position all fall out of
    # the base implementation with no override.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    supports_return_to_default_switch = True

    def wiki_anchor(self) -> str:
        """Sliding-curtain geometry page."""
        return "Configuration-Sliding-Curtain"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for sliding curtains."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.sliding_curtain"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],  # noqa: ARG002
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject awning and tilt geometry fields on a sliding curtain."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the sliding-curtain geometry schema for the given locale."""
        if hass is None:
            return GEOMETRY_SLIDING_CURTAIN_SCHEMA
        return geometry_sliding_curtain_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Window width and the four shade-area point coordinates are metres."""
        return SLIDING_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the slide direction, window width, and shade-area block."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        direction = config.get(
            CONF_SLIDING_SLIDE_DIRECTION, DEFAULT_SLIDING_SLIDE_DIRECTION
        )
        dir_key = _SLIDE_DIR_LABEL_KEYS.get(direction, "geometry.sliding.dir_bi_part")
        parts: list[str] = [L["geometry.sliding.slide"].format(v=L[dir_key])]
        if (w := config.get(CONF_WINDOW_WIDTH)) is not None:
            parts.append(L["geometry.sliding.width"].format(v=w))
        if config.get(CONF_SLIDING_ENABLE_SHADE_AREA):
            parts.append(
                L["geometry.sliding.shade_area"].format(
                    x1=config.get(CONF_SLIDING_POINT1_X, 0.0),
                    y1=config.get(CONF_SLIDING_POINT1_Y, 0.0),
                    x2=config.get(CONF_SLIDING_POINT2_X, 0.0),
                    y2=config.get(CONF_SLIDING_POINT2_Y, 0.0),
                )
            )
        else:
            parts.append(L["geometry.sliding.binary"])
        return [", ".join(parts)]

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as sliding curtain but no bound cover supports "
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
        config_service: ConfigurationService,  # noqa: ARG002
        options: dict,
    ) -> AdaptiveGeneralCover:
        """Build an ``AdaptiveSlidingCurtainCover`` with any shade-area config."""
        return AdaptiveSlidingCurtainCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            sc_config=SlidingCurtainConfig.from_options(options),
        )
