"""Split-panel cover policy — one fabric, top + bottom sections.

A single vertical shade split into a TOP section and a BOTTOM section, driven
as two SEPARATE HA cover entities. Behaviour: "bottom blocks, top free" — the
bottom section tracks the sun like a normal vertical blind (rising from the
sill to block direct sun) while the top section stays open for daylight/view.

Because the top section stays open, the two sections of the one shared fabric
never overlap, so the one-fabric coupling constraint holds by construction.
The pipeline's solar engine is a plain ``AdaptiveVerticalCover`` (the bottom
section), so the standard vertical geometry config applies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import voluptuous as vol

from ..const import (
    CONF_SPLIT_PANEL_BOTTOM,
    CONF_SPLIT_PANEL_TOP,
    POSITION_CLOSED,
    POSITION_OPEN,
    PanelRole,
)
from ..engine.covers import AdaptiveVerticalCover, bottom_blocks_top_free
from ._multi_entity import MultiEntityPolicy
from .base import POSITION_AXIS, CoverAxis
from .blind import (
    GEOMETRY_VERTICAL_SCHEMA,
    VERTICAL_LENGTH_KEYS,
    geometry_vertical_schema,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.covers import AdaptiveGeneralCover
    from ..pipeline.types import PipelineResult
    from ..services.configuration_service import ConfigurationService
    from .axis_target import AxisTarget


class SplitPanelPolicy(MultiEntityPolicy, register=True):
    """One fabric split into top + bottom sections, on two separate entities."""

    cover_type = "cover_split_panel"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    role_conf_keys: ClassVar[tuple[tuple[str, str], ...]] = (
        (CONF_SPLIT_PANEL_TOP, PanelRole.TOP),
        (CONF_SPLIT_PANEL_BOTTOM, PanelRole.BOTTOM),
    )

    # ---- dispatch ------------------------------------------------------ #

    def resolve_axis_targets(
        self,
        result: PipelineResult,
        state: int,
        entities: list[str],
        panel_role: dict[str, str],
        inverse_state: bool = False,
    ) -> list[AxisTarget]:
        """Bottom tracks the sun (= ``state``); top stays open."""
        if result is None:
            return self._role_targets(entities, panel_role, {}, state)

        # Safety / non-solar bypass winners (force override, weather) close both
        # sections — mirror the dispatched state to each role.
        if result.bypass_auto_control:
            mirror = {str(PanelRole.TOP): state, str(PanelRole.BOTTOM): state}
            return self._role_targets(entities, panel_role, mirror, state)

        # "Top open" in the dispatched coordinate space. The top constant
        # bypasses the coordinator ``state`` property, so apply the inverse
        # flip here when the cover reports inverted.
        top_open = POSITION_CLOSED if inverse_state else POSITION_OPEN
        sections = bottom_blocks_top_free(bottom=state, top_open=top_open)
        role_values = {
            str(PanelRole.TOP): sections.top,
            str(PanelRole.BOTTOM): sections.bottom,
        }
        return self._role_targets(entities, panel_role, role_values, state)

    # ---- config-flow surface ------------------------------------------ #

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Vertical-blind geometry (the window dimensions both sections share)."""
        if hass is None:
            return GEOMETRY_VERTICAL_SCHEMA
        return geometry_vertical_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Store the four window dimensions in canonical metres."""
        return VERTICAL_LENGTH_KEYS

    def wiki_anchor(self) -> str:
        """Split-panel docs page."""
        return "Split-Panel-Covers"

    def display_label(self) -> str:
        """User-facing label."""
        return "Split Panel (top + bottom)"

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Both sections travel the configured window height."""
        return config_service.get_vertical_data(options).h_win

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],  # noqa: ARG002
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject awning and tilt geometry fields on a vertical split panel."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

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
        """Build the bottom section's solar engine — a plain vertical cover."""
        return AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
        )
