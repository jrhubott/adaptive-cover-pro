"""Horizontal sliding-curtain cover policy (#829, Part 1).

A sliding curtain draws its fabric sideways across the window opening. Part 1
models it as a *binary* position-axis cover — same "open lets sun through /
closed blocks sun" semantic as a vertical blind, so ``position_for_intent``,
``more_protective_position`` and the inverse state all fall out of the base with
no override. The engine (:class:`AdaptiveSlidingCurtainCover`) fully closes when
direct sun would strike the shade target and fully opens otherwise.

Modelled on :mod:`blind` for the capability warning and cross-type geometry
rejection. Window width is deferred to Part 2 (the two-point shade-area
percentage model), so the type-specific geometry schema stays empty here and the
shared window-facing fields (azimuth / FOV) compose on via
``base.build_section_schema``. No edits to the config-flow bodies, options menu,
type picker, or registry are needed — the type registers itself via
``register=True`` and every config-flow surface dispatches through the policy
hooks below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from homeassistant.helpers import selector

from ..engine.covers import AdaptiveSlidingCurtainCover
from ._summary_labels import COVER_TYPE_LABELS_EN
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


class SlidingCurtainPolicy(CoverTypePolicy, register=True):
    """Cover that slides horizontally across the window (binary open/close)."""

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

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — no extra capability requirement."""
        return selector.EntityFilterSelectorConfig(domain="cover")

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
        options: dict,  # noqa: ARG002
    ) -> AdaptiveGeneralCover:
        """Build an ``AdaptiveSlidingCurtainCover`` (binary open/close)."""
        return AdaptiveSlidingCurtainCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
        )
