"""Oscillating (drop-arm / pivoting) awning cover policy (#412).

A horizontal-awning variant whose arm sweeps through an arc as it opens, so the
fabric angle is a function of the open percentage rather than a fixed value.
This is the first cover type added purely on the declarative section API: a new
policy subclass + a calc engine + a few field specs, with no edits to the
config-flow schema bodies, the options menu, the type picker, or the registry.

It demonstrates the ``disabled_config_keys`` capability by dropping the
fixed-angle ``CONF_AWNING_ANGLE`` field that a normal awning uses — the angle is
computed from position here, so the field would be meaningless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..config_types import OscillatingConfig
from ..const import (
    CONF_ARM_LENGTH,
    CONF_AWNING_ANGLE,
    CONF_AWNING_HOUSING_OFFSET,
    CONF_AWNING_MAX_ANGLE,
    CONF_AWNING_MIN_ANGLE,
    CONF_HEIGHT_WIN,
    CONF_SILL_HEIGHT,
    CONF_WINDOW_DEPTH,
    DEFAULT_ARM_LENGTH,
    DEFAULT_AWNING_HOUSING_OFFSET,
    DEFAULT_AWNING_MAX_ANGLE,
    DEFAULT_AWNING_MIN_ANGLE,
    DEFAULT_WINDOW_HEIGHT,
)
from ..engine.covers import AdaptiveOscillatingCover
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


# Option keys stored in canonical metres (config-flow unit conversion).
OSCILLATING_LENGTH_KEYS: tuple[str, ...] = (
    CONF_HEIGHT_WIN,
    CONF_WINDOW_DEPTH,
    CONF_SILL_HEIGHT,
    CONF_ARM_LENGTH,
    CONF_AWNING_HOUSING_OFFSET,
)


def _sweep_angle_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=180,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
            unit_of_measurement="°",
        )
    )


def geometry_oscillating_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Oscillating-awning geometry schema. ``hass=None`` → metric labels."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HEIGHT_WIN, default=length_default(DEFAULT_WINDOW_HEIGHT, hass)
            ): length_selector(hass, min_m=0.1, max_m=50, metric_step=0.01),
            vol.Optional(
                CONF_WINDOW_DEPTH, default=length_default(0.0, hass)
            ): length_selector(hass, min_m=0.0, max_m=5, metric_step=0.01),
            vol.Optional(
                CONF_SILL_HEIGHT, default=length_default(0.0, hass)
            ): length_selector(hass, min_m=0.0, max_m=50, metric_step=0.01),
            vol.Required(
                CONF_ARM_LENGTH, default=length_default(DEFAULT_ARM_LENGTH, hass)
            ): length_selector(hass, min_m=0.1, max_m=3, metric_step=0.01),
            vol.Required(
                CONF_AWNING_MIN_ANGLE, default=DEFAULT_AWNING_MIN_ANGLE
            ): _sweep_angle_selector(),
            vol.Required(
                CONF_AWNING_MAX_ANGLE, default=DEFAULT_AWNING_MAX_ANGLE
            ): _sweep_angle_selector(),
            vol.Optional(
                CONF_AWNING_HOUSING_OFFSET,
                default=length_default(DEFAULT_AWNING_HOUSING_OFFSET, hass),
            ): length_selector(hass, min_m=0.0, max_m=1, metric_step=0.01),
        }
    )


GEOMETRY_OSCILLATING_SCHEMA = geometry_oscillating_schema()


class OscillatingAwningPolicy(CoverTypePolicy, register=True):
    """Cover whose arm sweeps through an arc (drop-arm awning)."""

    cover_type = "cover_oscillating_awning"
    # Same "open=blocks-sun" semantic as a normal awning.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS_OPEN_BLOCKS_SUN,)
    supports_return_to_default_switch = True

    # The fixed-angle field is meaningless here — angle is derived from the open
    # percentage — so this cover type disables it. Demonstrates the
    # ``disabled_config_keys`` capability end-to-end (#412).
    disabled_config_keys: ClassVar[frozenset[str]] = frozenset({CONF_AWNING_ANGLE})

    def wiki_anchor(self) -> str:
        """Oscillating-awning geometry page."""
        return "Configuration-Oscillating-Awning"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for oscillating awnings."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.oscillating_awning"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject fixed-awning and tilt geometry; window dimensions are reused."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the oscillating-awning geometry schema for the given locale."""
        if hass is None:
            return GEOMETRY_OSCILLATING_SCHEMA
        return geometry_oscillating_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Window dims + arm length + housing offset are stored in metres."""
        return OSCILLATING_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the arm-length / sweep / window block."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        parts: list[str] = []
        if (v := config.get(CONF_ARM_LENGTH)) is not None:
            parts.append(L["geometry.oscillating.arm"].format(v=v))
        lo = config.get(CONF_AWNING_MIN_ANGLE)
        hi = config.get(CONF_AWNING_MAX_ANGLE)
        if lo is not None and hi is not None:
            parts.append(L["geometry.oscillating.sweep"].format(lo=lo, hi=hi))
        if (v := config.get(CONF_HEIGHT_WIN)) is not None:
            parts.append(L["geometry.window.height"].format(v=v))
        if (v := config.get(CONF_AWNING_HOUSING_OFFSET)) is not None:
            parts.append(L["geometry.oscillating.housing_offset"].format(v=v))
        return [", ".join(parts)] if parts else []

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_position``."""
        if not any(caps_get(caps, CAP_HAS_SET_POSITION) for caps in known.values()):
            return [
                "⚠️ Configured as oscillating awning but no bound cover supports "
                "set_position — only open/close will be issued."
            ]
        return []

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Report the arm length as the position-axis travel scale."""
        return OscillatingConfig.from_options(options).arm_length

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
        """Build an ``AdaptiveOscillatingCover`` (arc-sweep awning geometry)."""
        return AdaptiveOscillatingCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
            osc_config=OscillatingConfig.from_options(options),
        )
