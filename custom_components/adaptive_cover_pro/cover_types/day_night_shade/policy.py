"""Day/Night dual-fabric shade cover policy (#993, Model A).

A day/night shade drives ``set_cover_position`` (carriage / total coverage) and
``set_cover_tilt_position`` on a single HA entity, but the tilt axis is
reinterpreted as the *fabric blend*: 100 = all sheer (light-filtering fully
deployed), 0 = all blackout. Position is resolved by the same pipeline handlers
as ``cover_blind`` (a vertical calculation engine); the blend is filled
post-pipeline by ``DayNightShadeCalculation`` and threaded through the
position-context so the venetian ``DualAxisSequencer`` — reused BY COMPOSITION,
never subclassed — runs the dual-axis sequence.

The one seam ``_compose_blend`` owns fabric selection and feeds BOTH the live
``post_pipeline_resolve`` and the projected ``forecast_secondary_axes`` (the
no-duplication rule). There is NO slat geometry here — the venetian slat-angle
math is replaced by a pure fabric-choice decision keyed on the season.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.const import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.helpers import selector

from ...config_types import DayNightShadeConfig
from ...const import (
    CONF_DAY_NIGHT_BLACKOUT_THRESHOLD,
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_OPACITY_BLACKOUT,
    CONF_DAY_NIGHT_OPACITY_SHEER,
    DAY_NIGHT_CONTROL_MODELS,
    DAY_NIGHT_MODEL_POSITION_TILT,
    DAY_NIGHT_MODEL_SPLIT_RANGE,
    DAY_NIGHT_SPLIT_MIDPOINT,
    DEFAULT_DAY_NIGHT_BLACKOUT_THRESHOLD,
    DEFAULT_DAY_NIGHT_CONTROL_MODEL,
    DEFAULT_DAY_NIGHT_OPACITY_BLACKOUT,
    DEFAULT_DAY_NIGHT_OPACITY_SHEER,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlMethod,
)
from ...engine.covers import AdaptiveVerticalCover, DayNightShadeCalculation
from ...engine.covers.day_night_shade import (
    DAY_NIGHT_BLACKOUT,
    FabricSelection,
)
from ...managers.manual_override import SecondaryAxisCheck
from ...pipeline.axis_constraints import clamp_to_bounds, tilt_clamp_step
from ...pipeline.types import DecisionStep
from .._helpers import window_dimensions_lines
from .._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from ..base import (
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    POSITION_AXIS,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)
from ..blind import VERTICAL_LENGTH_KEYS, geometry_vertical_schema
from ..tilt import TILT_CAPABLE_ENTITY_FILTER
from ..venetian import DualAxisSequencer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ...engine.covers import AdaptiveGeneralCover
    from ...pipeline.types import PipelineResult
    from ...services.configuration_service import ConfigurationService


# Position-axis services the dual-axis sequencer must treat as a carriage move.
# The endpoint open/close substitution (issue #697) routes a target of 100 to
# ``open_cover`` and 0 to ``close_cover``; both drive the carriage to an endpoint
# exactly like ``set_cover_position``, so the fabric sequence must still run.
_POSITION_AXIS_SERVICES = frozenset(
    {SERVICE_SET_COVER_POSITION, SERVICE_OPEN_COVER, SERVICE_CLOSE_COVER}
)

# Control methods whose fabric choice keys on the climate season rather than the
# solar geometry. Summer forces blackout; every other season keeps sheer.
_CLIMATE_METHODS = frozenset(
    {ControlMethod.SUMMER, ControlMethod.WINTER, ControlMethod.EXTREME_HEAT}
)


def _pct_slider() -> selector.NumberSelector:
    """Return a 0–100 % slider selector for the fabric opacity/threshold fields."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
            unit_of_measurement="%",
        )
    )


def _control_model_select() -> selector.SelectSelector:
    """Return the translated control-model dropdown (Model A vs Model B).

    Mirrors the venetian mode select: a bare ``vol.In`` renders raw enum values
    untranslated, so a ``translation_key`` + a ``selector.day_night_control_model``
    options block is used instead.
    """
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(DAY_NIGHT_CONTROL_MODELS),
            mode=selector.SelectSelectorMode.LIST,
            translation_key="day_night_control_model",
        )
    )


def geometry_day_night_shade_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Vertical window geometry plus the three fabric sliders. ``hass=None`` → metric."""
    return geometry_vertical_schema(hass).extend(
        {
            vol.Optional(
                CONF_DAY_NIGHT_OPACITY_SHEER,
                default=DEFAULT_DAY_NIGHT_OPACITY_SHEER,
            ): _pct_slider(),
            vol.Optional(
                CONF_DAY_NIGHT_OPACITY_BLACKOUT,
                default=DEFAULT_DAY_NIGHT_OPACITY_BLACKOUT,
            ): _pct_slider(),
            vol.Optional(
                CONF_DAY_NIGHT_BLACKOUT_THRESHOLD,
                default=DEFAULT_DAY_NIGHT_BLACKOUT_THRESHOLD,
            ): _pct_slider(),
            vol.Optional(
                CONF_DAY_NIGHT_CONTROL_MODEL,
                default=DEFAULT_DAY_NIGHT_CONTROL_MODEL,
            ): _control_model_select(),
        }
    )


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_DAY_NIGHT_SHADE_SCHEMA = geometry_day_night_shade_schema()


class DayNightShadePolicy(CoverTypePolicy, register=True):
    """Dual-fabric shade (single HA entity, position + sheer/blackout blend)."""

    cover_type = "cover_day_night_shade"
    # Position drives the carriage; the tilt axis carries the fabric blend. Order
    # matters — ``select_default_axis`` returns the first entry by default, so a
    # fully-capable entity routes ``set_cover_position`` through the position
    # axis. The blend is filled in ``post_pipeline_resolve`` and dispatched
    # separately by the ``DualAxisSequencer``.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS, TILT_AXIS)
    exposes_dual_axis_sensor: ClassVar[bool] = True
    custom_position_includes_tilt: ClassVar[bool] = True
    # Carries the same window geometry (width + reveal depth) + fov sliders as a
    # vertical blind, so it gets the "Generate FOV from measurements" button too.
    supports_fov_compute: ClassVar[bool] = True

    def __init__(self) -> None:
        """Initialise without a sequencer; ``attach()`` wires one up later."""
        self._sequencer: DualAxisSequencer | None = None
        self._grace_mgr = None
        # Last resolved fabric blend, replayed by ``maybe_update_tilt_only`` when
        # the position axis won't fire this cycle. Cleared on every suppressed
        # branch of ``post_pipeline_resolve`` (mirrors venetian's ``_last_tilt``).
        self._last_blend: int | None = None
        self._schedule_refresh_after: Any | None = None
        # Per-instance control model (Model A vs B). Read from options and cached
        # once per cycle in ``post_pipeline_resolve`` so the downstream dispatch
        # hooks — which don't receive ``options`` — can gate on it. Defaults to
        # the dual-axis Model A so an un-resolved cycle behaves like Phase A.
        self._control_model: str = DEFAULT_DAY_NIGHT_CONTROL_MODEL

    # ---- Identity / labels -------------------------------------------- #

    def extra_field_keys(self, section: str) -> tuple[str, ...]:
        """Add per-slot + global fabric-blend (tilt) fields to custom position."""
        from ... import config_fields as cf

        if section == cf.SECTION_CUSTOM_POSITION:
            return cf.CUSTOM_POSITION_TILT_KEYS
        return ()

    def wiki_anchor(self) -> str:
        """Day/Night shade wiki page."""
        return "Configuration-Day-Night-Shade"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for day/night shades."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.day_night_shade"]

    # ---- Geometry / config-flow surfaces ------------------------------ #

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Accept vertical geometry; reject awning and slat (tilt) geometry.

        The blend axis is a fabric choice, not a slat, so slat depth/spacing
        (tilt-only geometry) is inert here — reject it alongside awning fields.
        """
        return [(awning_only, "awning"), (tilt_only, "tilt")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the vertical + fabric geometry schema for the given locale."""
        if hass is None:
            return GEOMETRY_DAY_NIGHT_SHADE_SCHEMA
        return geometry_day_night_shade_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Day/night shades carry the vertical-blind window dimensions in metres."""
        return VERTICAL_LENGTH_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position`` (both axes needed)."""
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the window-dimensions block plus the control-model line."""
        lines = window_dimensions_lines(config, labels)
        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        model = config.get(
            CONF_DAY_NIGHT_CONTROL_MODEL, DEFAULT_DAY_NIGHT_CONTROL_MODEL
        )
        model_label = {
            DAY_NIGHT_MODEL_POSITION_TILT: L["geometry.day_night.model_position_tilt"],
            DAY_NIGHT_MODEL_SPLIT_RANGE: L["geometry.day_night.model_split_range"],
        }.get(model, model)
        lines.append(L["geometry.slat.mode"].format(v=model_label))
        return lines

    def _missing_axis_warnings(
        self, known: dict[str, dict], *, require_tilt: bool
    ) -> list[str]:
        """Warn about covers missing the axes this control model needs.

        Single source for both the default (dual-axis, Model A) requirement and
        the split-range (single-axis, Model B) relaxation — the ``require_tilt``
        flag is the only difference, so the warning-building logic isn't
        duplicated (CODING_GUIDELINES.md "No Code Duplication").
        """
        both = "day/night shade requires both set_position and set_tilt_position."
        pos_tail = (
            both
            if require_tilt
            else "a split-range day/night shade requires set_position."
        )
        warnings: list[str] = []
        missing_pos = [
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_POSITION)
        ]
        if missing_pos:
            warnings.append(
                "⚠️ Configured as day/night shade but "
                f"{', '.join(missing_pos)} does not support set_position — "
                f"{pos_tail}"
            )
        if require_tilt:
            missing_tilt = [
                eid
                for eid, caps in known.items()
                if not caps_get(caps, CAP_HAS_SET_TILT_POSITION)
            ]
            if missing_tilt:
                warnings.append(
                    "⚠️ Configured as day/night shade but "
                    f"{', '.join(missing_tilt)} does not support set_tilt_position — "
                    f"{both}"
                )
        return warnings

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Require both ``set_position`` and ``set_tilt_position`` on every entity.

        The option-free default assumes the dual-axis Model A; the option-aware
        :meth:`capability_warnings_for_options` relaxes the tilt requirement for
        the single-axis split-range model.
        """
        return self._missing_axis_warnings(known, require_tilt=True)

    def capability_warnings_for_options(
        self, known: dict[str, dict], options: dict
    ) -> list[str]:
        """Relax the tilt requirement when the split-range control model is set."""
        model = str(
            options.get(CONF_DAY_NIGHT_CONTROL_MODEL, DEFAULT_DAY_NIGHT_CONTROL_MODEL)
        )
        return self._missing_axis_warnings(
            known, require_tilt=model != DAY_NIGHT_MODEL_SPLIT_RANGE
        )

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Return the configured window height the carriage travels."""
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
        """Build a vertical calc engine; the blend is filled post-pipeline."""
        return AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
        )

    # ---- Fabric-blend seam (single source of truth) ------------------- #

    def _compose_blend(
        self,
        position: int,
        *,
        is_summer: bool,
        allow_sheer_in_summer: bool,
        config,
        config_service: ConfigurationService,
        options: dict,
        sun_data,
        sol_azi: float,
        sol_elev: float,
        logger,
    ) -> tuple[int, FabricSelection]:
        """Single source of truth for the fabric blend that pairs with ``position``.

        BOTH the live ``post_pipeline_resolve`` and the projected
        ``forecast_secondary_axes`` flow through this method, so the fabric
        choice never diverges between them (CODING_GUIDELINES.md "No Code
        Duplication"). Returns ``(blend, selection)`` so the live path can also
        surface the filtering estimate in its trace.
        """
        calc = DayNightShadeCalculation(
            config=config,
            vert_config=config_service.get_vertical_data(options),
            day_night_config=DayNightShadeConfig.from_options(options),
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )
        selection = calc.select_fabric(
            position,
            is_summer=is_summer,
            allow_sheer_in_summer=allow_sheer_in_summer,
        )
        return selection.blend, selection

    def _clear_last_blend(self) -> None:
        """Forget the last resolved blend so tilt-only cycles don't replay it."""
        self._last_blend = None

    # ---- Split-range wire mapping (Model B) --------------------------- #

    def _split_range_wire(self, position: int, blend: int) -> int:
        """Fold the abstract ``(position, blend)`` pair into one physical position.

        Model B (``split_range``) drives a SINGLE physical axis whose 0–100 %
        travel is split into two fabric halves: the blackout fabric occupies the
        lower half ``0``–``DAY_NIGHT_SPLIT_MIDPOINT`` and the sheer fabric the
        upper half ``DAY_NIGHT_SPLIT_MIDPOINT``–``100``. The abstract coverage
        ``position`` scales into whichever half the blend selects (sheer when
        ``blend >= DAY_NIGHT_SPLIT_MIDPOINT``, blackout below it), so half the
        physical travel encodes coverage and the split encodes fabric.

        A fully-open carriage (``position == POSITION_OPEN``) means "no coverage
        at all", which maps to the single physical fully-open endpoint regardless
        of the (now irrelevant) fabric choice.
        """
        if position == POSITION_OPEN:
            return POSITION_OPEN
        half = round(position / 2)
        if blend >= DAY_NIGHT_SPLIT_MIDPOINT:
            return DAY_NIGHT_SPLIT_MIDPOINT + half
        return half

    def forecast_secondary_axes(
        self,
        *,
        position: int,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        minimize_movements: bool,  # noqa: ARG002
        max_coverage_steps: int,  # noqa: ARG002
    ) -> dict[str, int]:
        """Project the fabric blend that pairs with ``position`` (#724).

        Delegates to the shared :meth:`_compose_blend` seam so the forecast
        strip's blend track matches the live cover. The forecast loop is
        solar-only and carries no climate context, so it projects the
        comfortable-sheer default (``is_summer=False``). Keyed by the blend
        axis's ``name`` — no hardcoded ``"tilt"`` literal.
        """
        blend, _ = self._compose_blend(
            position,
            is_summer=False,
            allow_sheer_in_summer=True,
            config=config,
            config_service=config_service,
            options=options,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )
        return {self.axes[1].name: blend}

    def post_pipeline_resolve(
        self,
        result: PipelineResult,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        cover: AdaptiveGeneralCover | None = None,
    ) -> PipelineResult:
        """Resolve the fabric blend, then map it onto the configured control model.

        The blend resolution (:meth:`_resolve_blend`) is model-independent and
        byte-identical to Model A. The per-instance control model is cached here
        from ``options`` — the downstream dispatch hooks don't receive
        ``options``, so they read the cached value. In ``split_range`` (Model B)
        a single physical axis encodes BOTH coverage and fabric, so the resolved
        blend is folded into ``result.position`` via :meth:`_split_range_wire`
        while the abstract blend is kept on ``result.tilt`` for the Target Tilt
        sensor / forecast / diagnostics. In ``position_tilt`` (Model A, default)
        the resolved result passes straight through.
        """
        if result is None:
            return result

        self._control_model = str(
            options.get(CONF_DAY_NIGHT_CONTROL_MODEL, DEFAULT_DAY_NIGHT_CONTROL_MODEL)
        )
        resolved = self._resolve_blend(
            result,
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            config_service=config_service,
            options=options,
            cover=cover,
        )
        if (
            self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE
            and resolved.tilt is not None
        ):
            return self._apply_split_range(resolved)
        return resolved

    def _apply_split_range(self, resolved: PipelineResult) -> PipelineResult:
        """Fold the abstract ``(position, blend)`` pair into the split-range wire.

        Keeps the abstract blend on ``result.tilt`` (the diagnostic/forecast Target
        Tilt value) and rewrites ``result.position`` to the single physical
        position that encodes both. Records the fold as a terminal
        ``day_night_split_range`` trace step.
        """
        wire = self._split_range_wire(resolved.position, resolved.tilt)
        trace = list(resolved.decision_trace)
        trace.append(
            DecisionStep(
                handler="day_night_split_range",
                matched=True,
                reason=(
                    f"split-range wire {wire}% "
                    f"(coverage {resolved.position}%, blend {resolved.tilt}%)"
                ),
                position=wire,
                tilt=resolved.tilt,
            )
        )
        return replace(resolved, position=wire, decision_trace=trace)

    def _resolve_blend(
        self,
        result: PipelineResult,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        cover: AdaptiveGeneralCover | None = None,
    ) -> PipelineResult:
        """Fill the fabric blend that pairs with the pipeline-resolved position."""
        # Handler-supplied blend is explicit user intent — honor it unconditionally
        # (custom-position slots, default/sunset tilt via custom_position_includes_tilt).
        if result.tilt is not None:
            handler_blend = result.tilt
            trace = list(result.decision_trace)
            trace.append(
                DecisionStep(
                    handler="day_night_handler_tilt",
                    matched=True,
                    reason=f"handler-supplied fabric blend {handler_blend}% honored",
                    position=result.position,
                    tilt=handler_blend,
                )
            )
            self._last_blend = handler_blend
            return replace(result, decision_trace=trace)

        # No handler blend: pick a fabric only on SOLAR (with direct sun) or on a
        # climate-season win. Every other method clears the blend so a tilt-only
        # cycle doesn't replay a stale fabric.
        method = result.control_method
        is_summer = bool(getattr(result.climate_data, "is_summer", False))
        if method == ControlMethod.SOLAR:
            if cover is None or not cover.direct_sun_valid:
                self._clear_last_blend()
                return replace(result, tilt=None)
            allow_sheer_in_summer = True
        elif method in _CLIMATE_METHODS:
            allow_sheer_in_summer = False
        else:
            self._clear_last_blend()
            return replace(result, tilt=None)

        blend, selection = self._compose_blend(
            result.position,
            is_summer=is_summer,
            allow_sheer_in_summer=allow_sheer_in_summer,
            config=config,
            config_service=config_service,
            options=options,
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )
        trace = list(result.decision_trace)

        # Axis constraints (issue #943): a custom-position slot's tilt bounds may
        # cap the blend the engine just produced. Apply them through the same
        # shared clamp the registry uses, keeping the arithmetic in one helper.
        bounded = clamp_to_bounds(blend, result.tilt_low, result.tilt_high)
        if bounded != blend:
            trace.append(
                tilt_clamp_step(
                    from_tilt=blend,
                    to_tilt=bounded,
                    label=result.tilt_bound_label or "constraint",
                    source="tilt_clamp",
                )
            )
            blend = bounded

        fabric = "blackout" if blend == DAY_NIGHT_BLACKOUT else "sheer"
        trace.append(
            DecisionStep(
                handler="day_night_engine",
                matched=True,
                reason=(
                    f"fabric {fabric} — blend {blend}%, filtering estimate "
                    f"{round(selection.filtering_estimate)}%"
                ),
                position=result.position,
                tilt=blend,
            )
        )
        self._last_blend = blend
        return replace(result, tilt=blend, decision_trace=trace)

    def targets_full_mechanical_endpoint(self, result: PipelineResult) -> bool:
        """Report a full mechanical endpoint only when BOTH axes reach it.

        A day/night shade is dual-axis: position 0 with blend 100 (all sheer, a
        legitimate solar state) is NOT a mechanical stop. Only this policy knows
        the paired blend, so the dual-axis decision stays here (mirrors venetian).
        """
        return (
            result is not None
            and result.tilt is not None
            and result.position is not None
            and result.position == result.tilt
            and result.position in (POSITION_CLOSED, POSITION_OPEN)
        )

    def position_context_overrides(self, result: PipelineResult) -> dict[str, Any]:
        """Thread the resolved blend into ``PositionContext.tilt``."""
        if result is None:
            return {}
        # Model B drives a single physical axis whose position is already the
        # split-range wire — no tilt to thread, but the wire's own endpoints
        # (0/100) should still force open/close for seating (issue #897).
        if self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE:
            return {
                "full_endpoint_target": result.position
                in (POSITION_CLOSED, POSITION_OPEN)
            }
        if result.tilt is None:
            return {}
        overrides: dict[str, Any] = {"tilt": result.tilt}
        if self.targets_full_mechanical_endpoint(result):
            overrides["full_endpoint_target"] = True
        return overrides

    # ---- Dual-axis dispatch (sequencer reused by composition) ---------- #

    def attach(self, **kwargs: Any) -> None:  # noqa: D401
        """Construct the dual-axis sequencer once cmd_svc is available.

        Reuses the venetian ``DualAxisSequencer`` verbatim by composition — the
        sequencer is cover-type-agnostic. The coordinator passes the same kwargs
        to every policy's ``attach``; the venetian-specific tuning kwargs (skip
        threshold, drift reset) are read via ``kwargs.get`` and default harmlessly
        for a day/night entry that never configures them.
        """
        self._grace_mgr = kwargs.get("grace_mgr")
        self._sequencer = DualAxisSequencer(
            hass=kwargs["hass"],
            logger=kwargs["logger"],
            grace_mgr=kwargs["grace_mgr"],
            get_current_position=kwargs["get_current_position"],
            set_commanded_position=kwargs["set_commanded_position"],
            position_tolerance=kwargs["position_tolerance"],
            is_dry_run=kwargs["is_dry_run"],
            get_state=kwargs.get("get_state"),
            get_current_tilt_position=kwargs.get("get_current_tilt_position"),
            event_buffer=kwargs.get("event_buffer"),
            invert_tilt=kwargs.get("invert_tilt"),
            get_min_change=kwargs.get("get_min_change"),
            get_enforce_delta_at_endpoints=kwargs.get("get_enforce_delta_at_endpoints"),
        )
        self._schedule_refresh_after = kwargs.get("schedule_refresh_after")

    @property
    def sequencer(self) -> DualAxisSequencer | None:
        """Expose the sequencer for diagnostics / tests."""
        return self._sequencer

    def is_in_tilt_suppression(self, entity_id: str, delta: float = 0.0) -> bool:
        """Delegate the back-rotate suppression gate to the sequencer."""
        if self._sequencer is None:
            return False
        return self._sequencer.is_in_suppression_with_cap(entity_id, delta)

    def primary_axis_suppression(self, entity_id: str, delta: float = 0.0) -> bool:
        """Apply the blend-axis publish-lag window to the position axis too."""
        if self._sequencer is None:
            return False
        return self._sequencer.is_in_suppression_with_cap(entity_id, delta) or (
            self._grace_mgr is not None
            and self._grace_mgr.is_in_command_grace_period(entity_id)
        )

    def secondary_axis_check(
        self, result: PipelineResult, cmd_svc
    ) -> SecondaryAxisCheck | None:
        """Build the per-cycle blend-axis manual-override check."""
        # Model B is single-axis — no secondary (blend) axis to check.
        if self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE:
            return None
        if result is None or result.tilt is None:
            return None
        return SecondaryAxisCheck(
            expected=result.tilt,
            attribute="current_tilt_position",
            label="tilt",
            suppression=self.primary_axis_suppression,
        )

    async def maybe_update_tilt_only(
        self,
        entity_id: str,
        *,
        current_position: int | None,
        context: Any,  # noqa: ARG002
        reason: str,
    ) -> None:
        """Send a blend-only update when the position axis won't fire this cycle."""
        if self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE:
            return
        if self._sequencer is None or self._last_blend is None:
            return
        if self._sequencer.is_in_suppression(entity_id):
            return
        await self._sequencer.update_tilt_only(
            entity_id,
            tilt_target=self._last_blend,
            current_position=current_position,
            reason=reason,
        )

    async def before_position_command(
        self,
        cmd_svc,  # noqa: ARG002
        entity_id: str,
        *,
        service: str,
        position: int,
        context,
        reason: str,
    ) -> None:
        """Send the blend FIRST on opening transitions, before the carriage moves.

        Mirrors the venetian tilt-first order (issue #33): sending the fabric
        before the carriage starts opening lets the actuator settle the blend
        into the target rather than reasserting a cached value mid-travel.
        """
        # Model B has no separate blend axis to pre-send — the wire position
        # carries everything on the single carriage move.
        if self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE:
            return
        if service not in _POSITION_AXIS_SERVICES:
            return
        seq = self._sequencer
        if seq is None:
            return
        blend_target = getattr(context, "tilt", None)
        if blend_target is None:
            return
        current = seq._get_current_position(entity_id)
        if current is None or position <= current:
            return
        await seq._send_tilt_command(
            entity_id,
            tilt_target=blend_target,
            position_target=position,
            reason=reason,
            force=True,
            verify=False,
        )

    async def after_position_command(
        self,
        cmd_svc,  # noqa: ARG002
        entity_id: str,
        *,
        service: str,
        position: int,
        context,
        reason: str,
    ) -> None:
        """Run the dual-axis sequence after a successful ``set_cover_position``."""
        # Model B is single-axis — the carriage move is complete, nothing to
        # sequence afterward.
        if self._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE:
            return
        if service not in _POSITION_AXIS_SERVICES:
            return
        seq = self._sequencer
        if seq is None:
            return
        blend_target = getattr(context, "tilt", None)
        if blend_target is None:
            return
        seq.stamp_position_command(entity_id)
        await seq.run_sequence(
            entity_id,
            position_target=position,
            tilt_target=blend_target,
            reason=reason,
        )
