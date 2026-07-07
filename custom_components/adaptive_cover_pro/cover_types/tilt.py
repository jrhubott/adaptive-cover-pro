"""Tilt-only cover policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CLIMATE_TILT_PCT_NEGATIVE_HEMISPHERE_OFFSET,
    CONF_TILT_ANGLE_0,
    CONF_TILT_ANGLE_100,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    DEFAULT_TILT_ANGLE_0,
    DEFAULT_TILT_ANGLE_100,
)
from ..engine.covers import AdaptiveTiltCover
from ..const import TiltMode
from ..unit_system import slat_default, slat_selector
from ._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from .base import (
    CAP_HAS_SET_TILT_POSITION,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.covers import AdaptiveGeneralCover
    from ..services.configuration_service import ConfigurationService


# Keys whose stored value is canonical centimetres — used by config-flow steps
# to convert between stored canonical and display-unit on form load/submit.
TILT_SLAT_KEYS: tuple[str, ...] = (CONF_TILT_DEPTH, CONF_TILT_DISTANCE)


# Default slat dimensions (canonical centimetres).
_DEFAULT_TILT_DEPTH_CM = 3.0
_DEFAULT_TILT_DISTANCE_CM = 2.0


def geometry_tilt_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Tilt-only geometry schema. ``hass=None`` → metric labels."""
    return vol.Schema(
        {
            vol.Required(
                CONF_TILT_DEPTH, default=slat_default(_DEFAULT_TILT_DEPTH_CM, hass)
            ): slat_selector(hass, min_cm=0.1, max_cm=15),
            vol.Required(
                CONF_TILT_DISTANCE,
                default=slat_default(_DEFAULT_TILT_DISTANCE_CM, hass),
            ): slat_selector(hass, min_cm=0.1, max_cm=15),
            vol.Required(CONF_TILT_MODE, default="mode2"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["mode1", "mode2", "specify_angles"],
                    translation_key="tilt_mode",
                )
            ),
            vol.Required(
                CONF_TILT_ANGLE_0, default=DEFAULT_TILT_ANGLE_0
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180, max=180, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_TILT_ANGLE_100, default=DEFAULT_TILT_ANGLE_100
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=360, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


# Module-level constant for backward compatibility with tests / re-exports.
# Built without hass (== metric labels), identical to the historical schema.
GEOMETRY_TILT_SCHEMA = geometry_tilt_schema()


# Filter shared by tilt and venetian: cover entities that expose
# ``set_tilt_position``. HA's ``supported_features`` filter is OR-of-listed,
# not AND, so venetian uses this same filter and surfaces the
# missing-set_position case as a config-flow capability warning.
TILT_CAPABLE_ENTITY_FILTER = selector.EntityFilterSelectorConfig(
    domain="cover",
    supported_features=["cover.CoverEntityFeature.SET_TILT_POSITION"],
)


class TiltPolicy(CoverTypePolicy, register=True):
    """Cover that rotates slats only (no vertical movement)."""

    cover_type = "cover_tilt"
    axes: ClassVar[tuple[CoverAxis, ...]] = (TILT_AXIS,)

    def wiki_anchor(self) -> str:
        """Slat-tilt geometry page."""
        return "Configuration-Tilt"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for tilt-only covers."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.tilt"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and awning geometry fields on a tilt-only cover."""
        return [(vertical_only, "vertical blind"), (awning_only, "awning")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the slat-only geometry schema for the given locale.

        Returns the cached module-level constant when no locale is supplied so
        identity-checking tests keep passing; builds a fresh schema otherwise.
        """
        if hass is None:
            return GEOMETRY_TILT_SCHEMA
        return geometry_tilt_schema(hass)

    def geometry_slat_keys(self) -> tuple[str, ...]:
        """Tilt covers store slat depth and spacing in canonical centimetres."""
        return TILT_SLAT_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position``."""
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the slat-depth / spacing / mode block."""
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        parts: list[str] = []
        if (v := config.get(CONF_TILT_DEPTH)) is not None:
            parts.append(L["geometry.slat.depth"].format(v=v))
        if (v := config.get(CONF_TILT_DISTANCE)) is not None:
            parts.append(L["geometry.slat.spacing"].format(v=v))
        if (v := config.get(CONF_TILT_MODE)) is not None:
            parts.append(L["geometry.slat.mode"].format(v=v))
        if config.get(CONF_TILT_MODE) == TiltMode.SPECIFY_ANGLES.value:
            if (v := config.get(CONF_TILT_ANGLE_0)) is not None:
                parts.append(L["geometry.slat.angle_0"].format(v=v))
            if (v := config.get(CONF_TILT_ANGLE_100)) is not None:
                parts.append(L["geometry.slat.angle_100"].format(v=v))
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

    @staticmethod
    def is_mode2(mode: TiltMode | str | None) -> bool:
        """Return True when *mode* is MODE2 (bi-directional 0–180°)."""
        return mode == TiltMode.MODE2 or mode == TiltMode.MODE2.value

    @staticmethod
    def climate_tilt_percentage(
        *,
        angle_deg: float,
        mode: TiltMode | str,
        gamma_deg: float,
        sun_through: bool = False,
    ) -> int:
        """Convert a target slat angle to a tilt percentage that blocks the sun.

        Single source of truth for the climate handler's angle → percent
        translation across MODE1/MODE2 and positive/negative sun hemispheres.

        Args:
            angle_deg: Target slat angle in degrees (e.g. CLIMATE_SUMMER_TILT_ANGLE).
            mode: Tilt mode — TiltMode enum value or its string ("mode1"/"mode2").
            gamma_deg: Sun azimuth offset from window normal, in degrees.
                When negative, the sun is on the opposite hemisphere and MODE2 must
                flip its answer onto the other closed side.
            sun_through: When True, return the OPEN hemisphere instead of closed
                (winter heating: let sun reach the window).  Mirrors the
                ``sun_through`` flag on ``position_for_intent``.

        Returns:
            Tilt percentage (0–100) for the cover entity.

        """
        # Normalise mode (accept enum or string for backward compatibility with
        # call sites that historically compared against both forms).
        if not TiltPolicy.is_mode2(mode):
            # MODE1: 0° → 0%, 90° → 100%.
            return round((angle_deg / TiltMode.MODE1.max_degrees) * 100)

        # MODE2: bi-directional 0–180° scale where 50% is horizontal/open.
        # Choose hemisphere by sun side (gamma) and intent (sun_through).
        max_degrees = TiltMode.MODE2.max_degrees
        # Closed-hemisphere mapping for MODE2:
        #   gamma >= 0 → angle on the positive-side closed hemisphere
        #               → (180 - angle) / 180 * 100  (== 100 - mode1_pct/2)
        #   gamma <  0 → angle on the negative-side closed hemisphere
        #               → angle / 180 * 100
        # sun_through (winter heating) flips to the open hemisphere by mirroring
        # the angle across horizontal (+90° offset).
        if sun_through:
            effective_angle = (
                CLIMATE_TILT_PCT_NEGATIVE_HEMISPHERE_OFFSET + angle_deg
                if gamma_deg >= 0
                else CLIMATE_TILT_PCT_NEGATIVE_HEMISPHERE_OFFSET - angle_deg
            )
        else:
            effective_angle = max_degrees - angle_deg if gamma_deg >= 0 else angle_deg
        return round((effective_angle / max_degrees) * 100)

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
