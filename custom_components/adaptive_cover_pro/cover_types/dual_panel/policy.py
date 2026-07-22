"""Dual-panel shade cover policy (#996).

Two INDEPENDENT HA shade entities over one window:

* the **front** is a sheer / light-filtering shade that tracks the sun exactly
  like a plain vertical blind — its target is the adaptive position the pipeline
  already computed, so ``resolve_entity_target`` returns it verbatim;
* the **back** is a blackout shade that stays retracted (open) most of the time
  and deploys (closes) only when one of the user-configured triggers
  ``{heat, privacy, night}`` is active.

The defining difference from the day/night Model C shade (#993) is that the two
panels are INDEPENDENT — there is no ``M >= P`` no-pass coupling between them.
Each panel's target is decided once through the pure ``engine/covers/layered``
helpers (``blackout_should_deploy`` / ``compute_layered``) and dispatched on the
shipped ``resolve_entity_target`` / ``post_pipeline_resolve`` seam, which the
coordinator already routes every per-entity command through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ...const import (
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT_ENTITY,
    CONF_INVERSE_STATE,
    DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS,
    DUAL_PANEL_BLACKOUT_TRIGGERS,
    DUAL_PANEL_TRIGGER_HEAT,
    DUAL_PANEL_TRIGGER_NIGHT,
    DUAL_PANEL_TRIGGER_PRIVACY,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlMethod,
)
from ...engine.covers import AdaptiveVerticalCover
from ...engine.covers.layered import blackout_should_deploy, compute_layered
from ...managers.manual_override import inverse_state
from ...pipeline.types import DecisionStep
from .._helpers import window_dimensions_lines
from .._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from ..base import (
    CAP_HAS_SET_POSITION,
    POSITION_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)
from ..blind import VERTICAL_LENGTH_KEYS, geometry_vertical_schema

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ...engine.covers import AdaptiveGeneralCover
    from ...pipeline.types import PipelineResult
    from ...services.configuration_service import ConfigurationService


# Control methods whose full-block strategy counts as the "heat" trigger — a
# climate summer close or an extreme-heat force-hold. Kept here (not branched
# inline) so the trigger derivation reads off one set.
_HEAT_METHODS = frozenset({ControlMethod.SUMMER, ControlMethod.EXTREME_HEAT})


def _front_entity_select() -> selector.EntitySelector:
    """Return a single-cover entity picker for the front/sheer panel."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="cover"))


def _blackout_triggers_select() -> selector.SelectSelector:
    """Return the multi-select of blackout triggers ({heat, privacy, night})."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(DUAL_PANEL_BLACKOUT_TRIGGERS),
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="dual_panel_blackout_triggers",
        )
    )


def geometry_dual_panel_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Vertical window geometry plus the front-entity + trigger-set options."""
    return geometry_vertical_schema(hass).extend(
        {
            # Which of the instance's cover entities is the front/sheer panel
            # (the remainder is the back/blackout). Optional with no default so
            # an un-set value is simply absent from options — and the policy
            # then degrades gracefully to a plain vertical blind.
            vol.Optional(CONF_DUAL_PANEL_FRONT_ENTITY): _front_entity_select(),
            vol.Optional(
                CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
                default=list(DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS),
            ): _blackout_triggers_select(),
        }
    )


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_DUAL_PANEL_SCHEMA = geometry_dual_panel_schema()


class DualPanelPolicy(CoverTypePolicy, register=True):
    """Two independent shades (sheer front sun-tracks; blackout back on trigger)."""

    cover_type = "cover_dual_panel"
    # Single position axis — the front sheer sun-tracks exactly like a vertical
    # blind, and the back is a pure open/closed decision on its own position
    # axis. No tilt axis, so no dual-axis sensor / custom-position tilt sliders.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    supports_glare_zones: ClassVar[bool] = True
    supports_return_to_default_switch: ClassVar[bool] = True
    supports_fov_compute: ClassVar[bool] = True
    exposes_dual_axis_sensor: ClassVar[bool] = False
    custom_position_includes_tilt: ClassVar[bool] = False

    def __init__(self) -> None:
        """Initialise the per-cycle dispatch cache (filled post-pipeline)."""
        # Cached once per cycle by ``post_pipeline_resolve`` because the
        # coordinator dispatch seam receives no ``options`` and no pipeline
        # result. ``_front_entity`` names the sheer panel; ``_deploy_back``
        # records the blackout decision; ``_back_inverse`` mirrors whether
        # ``coordinator.state`` actually inverted the dispatched value this
        # cycle; ``_back_bypass`` records a safety/bypass cycle in which every
        # entity passes through untouched.
        self._front_entity: str | None = None
        self._deploy_back: bool = False
        self._back_inverse: bool = False
        self._back_bypass: bool = False

    # ---- Identity / labels -------------------------------------------- #

    def wiki_anchor(self) -> str:
        """Dual-panel shade wiki page."""
        return "Configuration-Dual-Panel"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for dual-panel shades."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.dual_panel"]

    # ---- Geometry / config-flow surfaces ------------------------------ #

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Accept vertical geometry; reject awning and slat (tilt) geometry."""
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the vertical + dual-panel geometry schema for the given locale."""
        if hass is None:
            return GEOMETRY_DUAL_PANEL_SCHEMA
        return geometry_dual_panel_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Dual-panel shades carry the vertical-blind window dimensions in metres."""
        return VERTICAL_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Plain ``cover`` domain — both panels are ordinary position covers."""
        return selector.EntityFilterSelectorConfig(domain="cover")

    def glare_zones_config(self, config_service, options: dict):
        """Return the glare-zones config (the front sheer sun-tracks, #996)."""
        return config_service.get_glare_zones_config(options)

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the window block plus the front-entity + trigger-list lines."""
        lines = window_dimensions_lines(config, labels)
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        front = config.get(CONF_DUAL_PANEL_FRONT_ENTITY)
        if front:
            lines.append(L["geometry.dual_panel.front_entity"].format(v=front))
        triggers = config.get(
            CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
            list(DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS),
        )
        trig_str = ", ".join(triggers) if triggers else L["geometry.dual_panel.none"]
        lines.append(L["geometry.dual_panel.triggers"].format(v=trig_str))
        return lines

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn about covers that don't support ``set_position``.

        Both panels are driven to absolute positions, so every bound cover must
        expose ``set_position``. Mirrors the vertical-blind requirement but
        names each offending entity (both panels matter here).
        """
        missing = [
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_POSITION)
        ]
        if missing:
            return [
                "⚠️ Configured as dual-panel shade but "
                f"{', '.join(missing)} does not support set_position — "
                "both the front and back panel need set_position."
            ]
        return []

    def capability_warnings_for_options(
        self, known: dict[str, dict], options: dict
    ) -> list[str]:
        """Add a front-entity validity check to the per-panel capability warnings.

        The front-entity designator must name one of the instance's configured
        covers; a typo / stale pick warns just like the day/night middle rail.
        """
        warnings = self.cover_capability_warnings(known)
        front = options.get(CONF_DUAL_PANEL_FRONT_ENTITY)
        if front and front not in known:
            warnings.append(
                "⚠️ Configured as a dual-panel shade but the front-panel entity "
                f"{front} is not one of this instance's covers — add it to the "
                "cover list or pick a configured cover."
            )
        return warnings

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Return the configured window height the front carriage travels."""
        return config_service.get_vertical_data(options).h_win

    # ---- Calculation engine ------------------------------------------- #

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
        """Build a vertical calc engine (the front sheer is a vertical blind)."""
        from dataclasses import replace

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

    # ---- Dual-panel dispatch seam (per-cycle cache + resolve) --------- #

    def _derive_active_triggers(
        self, result: PipelineResult, sol_elev: float
    ) -> tuple[str, ...]:
        """Map the pipeline result onto the currently-active blackout triggers.

        ``heat`` fires on a climate summer / extreme-heat full block; ``privacy``
        fires inside the sunset window; ``night`` fires when the sun is below the
        horizon. The configured subset (``blackout_should_deploy``) then decides
        whether any of these actually deploys the back — this method only reports
        what is happening, never what the user opted into.
        """
        active: list[str] = []
        if result.control_method in _HEAT_METHODS:
            active.append(DUAL_PANEL_TRIGGER_HEAT)
        if result.is_sunset_active:
            active.append(DUAL_PANEL_TRIGGER_PRIVACY)
        if sol_elev < 0:
            active.append(DUAL_PANEL_TRIGGER_NIGHT)
        return tuple(active)

    def post_pipeline_resolve(
        self,
        result: PipelineResult,
        *,
        logger,  # noqa: ARG002
        sol_azi: float,  # noqa: ARG002
        sol_elev: float,
        sun_data,  # noqa: ARG002
        config,  # noqa: ARG002
        config_service: ConfigurationService,  # noqa: ARG002
        options: dict,
        cover: AdaptiveGeneralCover | None = None,  # noqa: ARG002
    ) -> PipelineResult:
        """Cache the per-cycle dual-panel dispatch state; leave position/tilt as-is.

        The coordinator dispatch seam (``resolve_entity_target``) receives no
        ``options`` and no pipeline result, so the front-entity designator, the
        blackout deploy decision, the inverse-state flag, and the bypass flag are
        stashed here once per cycle. The front panel keeps the pipeline's
        adaptive position untouched — only the back panel's target is substituted
        at dispatch — so this hook does not rewrite ``result.position``.
        """
        if result is None:
            return result

        active = self._derive_active_triggers(result, sol_elev)
        configured = options.get(
            CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
            list(DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS),
        )
        self._front_entity = options.get(CONF_DUAL_PANEL_FRONT_ENTITY)
        self._deploy_back = blackout_should_deploy(active, configured)
        self._back_bypass = result.bypass_auto_control
        self._back_inverse = self._resolve_inverse(result, options)

        trace = list(result.decision_trace)
        trace.append(
            DecisionStep(
                handler="dual_panel_back",
                matched=self._deploy_back,
                reason=(
                    f"blackout {'deploy' if self._deploy_back else 'retract'} — "
                    f"active triggers: {', '.join(active) if active else 'none'}; "
                    f"configured: {', '.join(configured) if configured else 'none'}"
                ),
                position=result.position,
            )
        )
        from dataclasses import replace

        return replace(result, decision_trace=trace)

    @staticmethod
    def _resolve_inverse(result: PipelineResult, options: dict) -> bool:
        """Mirror whether ``coordinator.state`` actually inverted this cycle.

        ``state`` returns ``result.position`` RAW (no inverse) on the
        safety/bypass path, for a floor-clamped winner, and under interpolation;
        so the back panel must invert its open/closed target in exactly the same
        cases the primary dispatch did — never merely when inverse-state is
        configured. Byte-identical to the day/night Model C ``_cache_dual_entity``
        inverse rule (#993).
        """
        from ...const import CONF_INTERP

        inverse_cfg = bool(options.get(CONF_INVERSE_STATE, False))
        use_interp = bool(options.get(CONF_INTERP, False))
        return (
            inverse_cfg
            and not use_interp
            and not (result.bypass_auto_control or result.floor_clamp_applied)
        )

    def resolve_entity_target(
        self, entity_id: str, position: int, *, inverted: bool | None = None
    ) -> int:
        """Substitute the back panel's blackout target; the front passes through.

        The front/sheer panel dispatches the pipeline's adaptive position
        verbatim. Every other bound entity is the back/blackout panel, whose
        target is a pure open/closed decision — INDEPENDENT of the front, with no
        ``M >= P`` no-pass clamp (the defining difference from day/night Model C).
        When no front is designated the cover degrades to a plain vertical blind
        (identity for all); a safety/bypass cycle also passes every entity
        through untouched so the safety winner is never overwritten.

        ``inverted`` names the wire value's inversion space: ``None`` reuses the
        cached per-cycle decision (the main dispatch path); the broadcast seams
        pass an explicit ``True``/``False`` for their own divergent space.
        """
        if self._front_entity is None or self._back_bypass:
            return position
        if entity_id == self._front_entity:
            return position
        layered = compute_layered(
            front=position,
            deploy_blackout=self._deploy_back,
            open_position=POSITION_OPEN,
            closed_position=POSITION_CLOSED,
        )
        inverse = self._back_inverse if inverted is None else inverted
        return inverse_state(layered.back) if inverse else layered.back
