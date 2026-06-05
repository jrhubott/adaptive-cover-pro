"""Dual-panel cover policy — sheer front + blackout back, layered.

Two full-height vertical shades on one window, layered front-to-back on two
SEPARATE HA cover entities:

- the **front** is a sheer / light-filtering shade that tracks the sun exactly
  like a vertical blind, so its target is the adaptive position the pipeline
  already computed;
- the **back** is a blackout shade that stays retracted (open) and only
  deploys (closes) when a configured trigger is active — a climate full-block
  strategy (hot/cold) or the astronomical sunset / privacy window.

The two panels are independent (uncoupled): each gets its own per-entity
position target via :meth:`resolve_axis_targets`. The pipeline's solar engine
is a plain ``AdaptiveVerticalCover`` (the sheer), so the standard vertical
geometry config applies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import voluptuous as vol

from ..const import (
    CONF_DUAL_PANEL_BACK,
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT,
    DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS,
    DUAL_PANEL_TRIGGER_CLIMATE,
    DUAL_PANEL_TRIGGER_SUNSET,
    POSITION_CLOSED,
    POSITION_OPEN,
    ClimateStrategy,
    PanelRole,
)
from ..engine.covers import (
    AdaptiveVerticalCover,
    blackout_should_deploy,
    compute_layered,
)
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


class DualPanelPolicy(MultiEntityPolicy, register=True):
    """Sheer front + blackout back, on two separate HA entities."""

    cover_type = "cover_dual_panel"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    role_conf_keys: ClassVar[tuple[tuple[str, str], ...]] = (
        (CONF_DUAL_PANEL_FRONT, PanelRole.FRONT),
        (CONF_DUAL_PANEL_BACK, PanelRole.BACK),
    )

    # Climate strategies that call for a full heat block — the blackout back
    # deploys when one is active and "climate" is a configured trigger.
    _BLACKOUT_CLIMATE_STRATEGIES: ClassVar[frozenset[ClimateStrategy]] = frozenset(
        {ClimateStrategy.SUMMER_COOLING, ClimateStrategy.WINTER_INSULATION}
    )

    def __init__(self) -> None:
        """Start with the default blackout triggers until options are read."""
        self._blackout_triggers: list[str] = list(DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS)

    # ---- per-cycle config + role map ---------------------------------- #

    def panel_role_map(self, options: dict) -> dict[str, str]:
        """Refresh the blackout-trigger config each cycle, then build the map."""
        self._blackout_triggers = list(
            options.get(
                CONF_DUAL_PANEL_BLACKOUT_TRIGGERS, DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS
            )
        )
        return super().panel_role_map(options)

    def _active_blackout_triggers(self, result: PipelineResult) -> set[str]:
        """Map the pipeline result onto the set of currently-active triggers."""
        active: set[str] = set()
        if result.is_sunset_active:
            active.add(DUAL_PANEL_TRIGGER_SUNSET)
        if result.climate_strategy in self._BLACKOUT_CLIMATE_STRATEGIES:
            active.add(DUAL_PANEL_TRIGGER_CLIMATE)
        return active

    # ---- dispatch ------------------------------------------------------ #

    def resolve_axis_targets(
        self,
        result: PipelineResult,
        state: int,
        entities: list[str],
        panel_role: dict[str, str],
        inverse_state: bool = False,
    ) -> list[AxisTarget]:
        """Front tracks the sun (= ``state``); back deploys on a trigger."""
        if result is None:
            return self._role_targets(entities, panel_role, {}, state)

        # Safety / non-solar bypass winners (force override, weather) close
        # both panels — mirror the dispatched state to each role.
        if result.bypass_auto_control:
            mirror = {str(PanelRole.FRONT): state, str(PanelRole.BACK): state}
            return self._role_targets(entities, panel_role, mirror, state)

        front = state
        deploy = blackout_should_deploy(
            self._active_blackout_triggers(result), self._blackout_triggers
        )
        # Map canonical open/closed into the dispatched coordinate space. The
        # back binary bypasses the coordinator ``state`` property, so apply the
        # inverse flip here (closed↔open) when the cover reports inverted.
        open_pos, closed_pos = POSITION_OPEN, POSITION_CLOSED
        if inverse_state:
            open_pos, closed_pos = POSITION_CLOSED, POSITION_OPEN
        layered = compute_layered(
            front=front,
            deploy_blackout=deploy,
            open_position=open_pos,
            closed_position=closed_pos,
        )
        role_values = {
            str(PanelRole.FRONT): layered.front,
            str(PanelRole.BACK): layered.back,
        }
        return self._role_targets(entities, panel_role, role_values, state)

    # ---- config-flow surface ------------------------------------------ #

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Vertical-blind geometry (the sheer front's window dimensions)."""
        if hass is None:
            return GEOMETRY_VERTICAL_SCHEMA
        return geometry_vertical_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Store the four window dimensions in canonical metres."""
        return VERTICAL_LENGTH_KEYS

    def wiki_anchor(self) -> str:
        """Dual-panel docs page."""
        return "Dual-Panel-Covers"

    def display_label(self) -> str:
        """User-facing label."""
        return "Dual Panel (sheer + blackout)"

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Both panels travel the configured window height."""
        return config_service.get_vertical_data(options).h_win

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],  # noqa: ARG002
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject awning and tilt geometry fields on a vertical dual panel."""
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
        """Build the sheer front's solar engine — a plain vertical cover."""
        return AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
        )
