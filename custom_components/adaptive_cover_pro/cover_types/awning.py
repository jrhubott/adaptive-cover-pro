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
from ..unit_system import length_default, length_selector
from ._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from .base import (
    CAP_HAS_SET_POSITION,
    POSITION_AXIS_OPEN_BLOCKS_SUN,
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
HORIZONTAL_LENGTH_KEYS: tuple[str, ...] = (CONF_LENGTH_AWNING, CONF_HEIGHT_WIN)


def geometry_horizontal_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Horizontal-awning geometry schema. ``hass=None`` → metric labels."""
    return vol.Schema(
        {
            vol.Required(
                CONF_LENGTH_AWNING, default=length_default(DEFAULT_AWNING_LENGTH, hass)
            ): length_selector(
                hass,
                min_m=0.3,
                max_m=6,
                metric_step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
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
                CONF_HEIGHT_WIN, default=length_default(DEFAULT_WINDOW_HEIGHT, hass)
            ): length_selector(hass, min_m=0.1, max_m=50, metric_step=0.01),
        }
    )


# Module-level constant for backward compatibility with tests / re-exports.
GEOMETRY_HORIZONTAL_SCHEMA = geometry_horizontal_schema()


class AwningPolicy(CoverTypePolicy, register=True):
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

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for horizontal awnings."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.awning"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and tilt geometry fields on an awning cover."""
        return [(vertical_only, "vertical blind"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the horizontal-awning geometry schema for the given locale.

        Returns the cached module-level constant when no locale is supplied so
        identity-checking tests keep passing; builds a fresh schema otherwise.
        """
        if hass is None:
            return GEOMETRY_HORIZONTAL_SCHEMA
        return geometry_horizontal_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Awnings store awning length and window height in canonical metres."""
        return HORIZONTAL_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the awning-length / angle / window block."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        parts: list[str] = []
        if (v := config.get(CONF_LENGTH_AWNING)) is not None:
            parts.append(L["geometry.awning.length"].format(v=v))
        if (v := config.get(CONF_AWNING_ANGLE)) is not None:
            parts.append(L["geometry.awning.angle"].format(v=v))
        if (v := config.get(CONF_HEIGHT_WIN)) is not None:
            parts.append(L["geometry.window.height"].format(v=v))
        if (v := config.get(CONF_DISTANCE)) is not None:
            parts.append(L["geometry.awning.blocking_wall"].format(v=v))
        return [", ".join(parts)] if parts else []

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as awning but no bound cover supports "
                "set_position — only open/close will be issued."
            ]
        return []

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Awnings travel the configured extension length."""
        return config_service.get_horizontal_data(options).awn_length

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
