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

from collections.abc import Iterable, Mapping
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
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_DAY_NIGHT_OPACITY_BLACKOUT,
    CONF_DAY_NIGHT_OPACITY_SHEER,
    DAY_NIGHT_CONTROL_MODELS,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
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
    DAY_NIGHT_SHEER,
    FabricSelection,
)
from ...managers.manual_override import (
    SecondaryAxisCheck,
    resolve_dispatched_secondary_expected,
)
from ...pipeline.axis_constraints import clamp_to_bounds, tilt_clamp_step
from ...pipeline.types import DecisionStep
from ...position_utils import flip_if
from .._helpers import window_dimensions_lines
from .._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from ..base import (
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    POSITION_AXIS,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    axis_inverted,
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


def _middle_rail_select() -> selector.EntitySelector:
    """Return a single-cover entity picker for the Model C middle rail."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="cover"))


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
            # Model C: which of the instance's cover entities is the middle rail
            # (the other is the bottom rail / primary). Only relevant when the
            # control model is ``dual_entity``; inert otherwise, like the model
            # select itself. Optional with no default so an un-set value is
            # simply absent from options.
            vol.Optional(CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY): _middle_rail_select(),
        }
    )


# Module-level constant for hass=None (metric) identity, matching the other
# policies so schema-identity tests keep passing.
GEOMETRY_DAY_NIGHT_SHADE_SCHEMA = geometry_day_night_shade_schema()


class DayNightShadePolicy(CoverTypePolicy, register=True):
    """Dual-fabric shade (single HA entity, position + sheer/blackout blend).

    **The physical shade, and why Model C is not just arithmetic.** All three
    control models describe the same hardware: one headbox, sheer fabric spanning
    the head rail down to a MIDDLE rail, blackout fabric spanning the middle rail
    down to the BOTTOM rail. Coverage is set by how far the bottom rail has
    descended; the sheer-vs-blackout blend is set by where the middle rail sits
    between the head rail and the bottom rail. Model A encodes the blend on a
    tilt axis and Model B folds it into one carriage's split travel range, so
    both drive a single HA entity and the blend is a number.

    Model C (``dual_entity``) drives the two rails as two real HA cover entities
    on a SHARED TRACK, which adds a constraint no arithmetic can express:

    * the middle rail can never be below the bottom rail (hence the no-pass
      clamp ``M >= P`` in :meth:`resolve_entity_target`, in open-percent space);
    * and, because they are physically stacked, the middle rail cannot TRAVEL to
      a target the bottom rail is currently sitting above — so on any cycle that
      lowers the shade the bottom rail must start positioning first, and the
      middle rail is free to move only once the bottom rail is below the middle
      rail's target.

    Those last two are what :meth:`dispatch_order_key` and
    :meth:`_gate_middle_rail_clearance` enforce (issue #1115). Without them a
    perfectly valid target pair still drives the middle motor into a mechanical
    block: stall, over-current trip, or lost calibration.
    """

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
        # Both bound in ``attach``; only the Model C travel gate reads them, and
        # it never runs before ``attach`` (it needs the sequencer).
        self._logger: Any | None = None
        self._position_tolerance: int = 0
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
        # Model C (dual_entity) dispatch cache, filled in ``post_pipeline_resolve``
        # and read by ``resolve_entity_target`` at the coordinator dispatch seam
        # (which receives no ``options``). The blend folds into the middle rail's
        # absolute position; the inverse flag keeps both rails inverting
        # identically; the role map records which entity is the middle rail.
        self._dual_entity_blend: int | None = None
        self._dual_entity_inverse: bool = False
        self._dual_entity_middle_rail: str | None = None
        # The inversion frame the most recent middle-rail dispatch was resolved
        # in, recorded by ``resolve_entity_target`` and replayed by the travel
        # gate so both read the SAME answer (issue #1115). Scoped to the
        # DISPATCH, not to a resolve cycle: reconciliation re-sends the number
        # that dispatch produced, so that dispatch's frame is the one the number
        # speaks. ``None`` = no middle rail has been dispatched at all yet, which
        # ``_dispatch_frame`` maps onto the cached per-cycle decision — the
        # identical rule the dispatch seam uses.
        self._dual_entity_dispatch_inverse: bool | None = None
        # The instance's configured covers, snapshotted in ``attach``. Model C
        # needs to know which entity is the BOTTOM rail (the one that isn't the
        # middle rail) to gate the middle rail's travel against it. Every options
        # change except the sun-tracking toggle reloads the config entry, which
        # rebuilds the coordinator and re-runs ``attach``, so the snapshot cannot
        # go stale behind a changed cover list.
        self._attached_entities: tuple[str, ...] = ()
        # Middle-rail position commands withheld because the bottom rail had not
        # cleared the target yet (issue #1115). Drives
        # ``has_pending_secondary_axis``, which makes the coordinator withhold
        # this cycle's dispatched-target signature so the next cycle re-attempts
        # the send — the same defer-and-retry latch shape as #756's tilt tail.
        self._pending_middle_rail: set[str] = set()

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
            DAY_NIGHT_MODEL_DUAL_ENTITY: L["geometry.day_night.model_dual_entity"],
        }.get(model, model)
        lines.append(L["geometry.slat.mode"].format(v=model_label))
        return lines

    def _missing_axis_warnings(
        self,
        known: dict[str, dict],
        *,
        require_tilt: bool,
        single_axis_label: str = "split-range",
    ) -> list[str]:
        """Warn about covers missing the axes this control model needs.

        Single source for both the default (dual-axis, Model A) requirement and
        the single-carriage (split-range / dual-entity) relaxation — the
        ``require_tilt`` flag is the only behavioural difference, so the
        warning-building logic isn't duplicated (CODING_GUIDELINES.md "No Code
        Duplication"). ``single_axis_label`` names the model in the
        set_position tail so a dual-entity shade isn't mislabelled as
        split-range (only consulted when ``require_tilt`` is False).
        """
        both = "day/night shade requires both set_position and set_tilt_position."
        pos_tail = (
            both
            if require_tilt
            else f"a {single_axis_label} day/night shade requires set_position."
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
        """Warn per the control model's axis requirement + Model C's rail map.

        Only the dual-axis Model A requires ``set_tilt_position``; both
        single-carriage models (``split_range``, ``dual_entity``) need only
        ``set_position``. Model C additionally binds a second rail entity, so it
        warns when ``CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY`` names an entity that is
        not among the instance's configured covers (a typo / stale pick) — and,
        since issue #1115, when the middle rail is not configured AT ALL. The old
        ``if middle and middle not in known`` short-circuited on the absent value,
        so an unset rail warned about nothing and the shade then silently ran as a
        plain vertical blind (both rails driven to the bottom rail's position).
        The runtime twin of this warning is the B3 Repair fed by
        :meth:`required_role_entity_missing`.
        """
        model = str(
            options.get(CONF_DAY_NIGHT_CONTROL_MODEL, DEFAULT_DAY_NIGHT_CONTROL_MODEL)
        )
        single_axis_label = (
            "dual-entity" if model == DAY_NIGHT_MODEL_DUAL_ENTITY else "split-range"
        )
        warnings = self._missing_axis_warnings(
            known,
            require_tilt=model == DAY_NIGHT_MODEL_POSITION_TILT,
            single_axis_label=single_axis_label,
        )
        if model == DAY_NIGHT_MODEL_DUAL_ENTITY:
            middle = options.get(CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY)
            if not middle:
                warnings.append(
                    "⚠️ Configured as a dual-entity day/night shade but no "
                    "middle-rail entity is selected — without it both rails get "
                    "the same position and the shade behaves like a plain "
                    "vertical blind."
                )
            elif middle not in known:
                warnings.append(
                    "⚠️ Configured as a dual-entity day/night shade but the "
                    f"middle-rail entity {middle} is not one of this instance's "
                    "covers — add it to the cover list or pick a configured cover."
                )
        return warnings

    def required_role_entity_missing(
        self, options: Mapping[str, Any], entities: Iterable[str]
    ) -> bool:
        """Report an unfilled Model C middle-rail role (B3 Repair, issue #1115).

        True when the control model is ``dual_entity`` and the middle-rail pick is
        either absent or names a cover outside this instance's own list. Both
        cases collapse to the same runtime symptom: ``resolve_entity_target``
        never matches an entity, so both rails are driven to the bottom rail's
        position and the shade silently degrades into a plain vertical blind — no
        warning, no log line, until #1115 no Repair either.

        Reads the model from ``options`` rather than the cached
        ``_control_model`` so the health check is correct on the very first cycle,
        before ``post_pipeline_resolve`` has run. Models A and B bind no second
        rail, so they always report ``False``.
        """
        model = str(
            options.get(CONF_DAY_NIGHT_CONTROL_MODEL, DEFAULT_DAY_NIGHT_CONTROL_MODEL)
        )
        if model != DAY_NIGHT_MODEL_DUAL_ENTITY:
            return False
        middle = options.get(CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY)
        return not middle or middle not in set(entities)

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

    def _drives_dual_axis(self) -> bool:
        """Whether the current model drives a physical blend (tilt) axis.

        Only Model A (``position_tilt``) runs the ``DualAxisSequencer`` and the
        tilt-first / tilt-only / secondary-axis machinery. Both single-carriage
        models — ``split_range`` (one axis encodes coverage AND fabric) and
        ``dual_entity`` (the blend folds into a second entity's *position* axis,
        never a tilt axis) — leave the sequencer hooks inert. Single source for
        that gate so the hooks don't each enumerate the position-only models.
        """
        return self._control_model == DAY_NIGHT_MODEL_POSITION_TILT

    def tilt_capability_contradiction(self, caps: Any) -> bool:
        """Report an A3 tilt contradiction only when the model needs a tilt axis.

        The static ``axes`` declaration always lists ``TILT_AXIS``, but only
        Model A (``position_tilt``) drives a *physical* tilt axis. The
        single-carriage models — ``split_range`` and ``dual_entity`` — need only
        ``set_position``, so a position-only cover is their intended, supported
        hardware: reporting a contradiction there would raise a
        ``cover_tilt_unsupported`` Repair that never clears (#991). Gated on the
        same ``_drives_dual_axis`` model predicate as every other blend-axis
        hook, so runtime A3 and the config-time ``require_tilt`` warning agree on
        one source of truth. Kept behind the cover-type boundary — the
        coordinator never branches on model here.
        """
        if not self._drives_dual_axis():
            return False
        return super().tilt_capability_contradiction(caps)

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
        if self._control_model == DAY_NIGHT_MODEL_DUAL_ENTITY:
            self._cache_dual_entity(resolved, options)
        return resolved

    def _cache_dual_entity(self, resolved: PipelineResult, options: dict) -> None:
        """Cache the Model C dispatch state for ``resolve_entity_target``.

        The coordinator dispatch seam receives no ``options`` and no pipeline
        result, so the resolved blend, the inverse-state flag, and the
        middle-rail role map are stashed here once per cycle. The abstract blend
        stays on ``resolved.tilt`` (kept for the Target Tilt sensor / forecast /
        diagnostics) — Model C folds it into the middle rail's absolute position
        at dispatch rather than driving a physical tilt axis.

        The cached ``_dual_entity_inverse`` flag records whether inversion was
        ACTUALLY applied to the dispatched value this cycle — mirroring
        ``coordinator.state`` exactly. If the middle-rail remap un-inverted a
        value that was never inverted, it would drive the rail to the wrong
        physical position (bottom raw 30 → middle physical 0 instead of fully
        open). Keeping the flag in the dispatched value's OWN space is what
        preserves the physical no-pass invariant (#993).

        ``state`` now maps EVERY winner through ``_to_cover_frame``, so mirroring
        it is exactly ``axis_inverted`` — the same predicate the coordinator's
        own ``position_axis_inverted`` reads, which already folds in
        interpolation's suppression of inverse-state. The bypass / floor-clamp
        exclusion this used to carry described a dispatch carve-out that no
        longer exists (#1036); the broadcast seams still dispatch in a divergent
        space and still say so explicitly — see ``resolve_entity_target``.
        """
        self._dual_entity_blend = resolved.tilt
        self._dual_entity_inverse = axis_inverted(self.axes[0], options)
        self._dual_entity_middle_rail = options.get(CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY)
        # NB: ``_dual_entity_dispatch_inverse`` is deliberately NOT reset here.
        # It records the frame of the last middle-rail RESOLUTION — which is
        # exactly right for its one reader, the DISPATCH path: ``apply_position``
        # asks the travel gate immediately after ``resolve_entity_target``
        # restated it for the very value being dispatched, with nothing in
        # between. Clearing it here would hand that gate a cached per-cycle
        # decision instead of the seam's own answer (issue #1115).
        #
        # It is emphatically NOT the frame a RESEND speaks. ``resolve_entity_target``
        # is evaluated as an argument to ``apply_position``, so a cycle that
        # resolves and then skips on a delta gate restates this field for a
        # command that never went out, while the recorded target reconciliation
        # replays still belongs to an older dispatch in an older frame. That
        # provenance travels with the target instead: ``capture_dispatch_token``
        # stamps the frame onto the command as it is BOOKED, and the gate
        # un-transforms the number it is really resending against that stamp.

    def _is_dual_entity_middle_rail(self, entity_id: str) -> bool:
        """Whether ``entity_id`` is THIS cycle's Model C middle rail.

        Single source of truth for "is this the rail that must not pass the
        bottom rail?", consumed by the dispatch-ordering key and the travel gate
        so neither re-states the model/role test (issue #1115). False for every
        other model — a coordinator instance has exactly one control model — and
        for an unconfigured middle rail, which is Defect 2's silent-degradation
        case rather than something the gate can fix.
        """
        return (
            self._control_model == DAY_NIGHT_MODEL_DUAL_ENTITY
            and self._dual_entity_middle_rail is not None
            and entity_id == self._dual_entity_middle_rail
        )

    def dispatch_order_key(self, entity_id: str) -> int:
        """Command the bottom rail before the middle rail (Model C, issue #1115).

        The two rails share one track: the middle rail cannot travel below the
        bottom rail. When a cycle lowers both, the bottom rail must START moving
        first — otherwise the middle rail is commanded toward a target the
        still-stacked bottom rail is physically blocking. The per-entity command
        hooks fire inside the caller's loop and cannot reorder it, so the order
        has to be expressed here, where the rail roles are known.

        Model A and Model B keep the constant default (a stable-sort no-op):
        neither binds a second rail entity.
        """
        return 1 if self._is_dual_entity_middle_rail(entity_id) else 0

    def _dispatch_frame(self, inverted: bool | None) -> bool:
        """Resolve which inversion frame a dispatched middle-rail value speaks.

        The ONE place that answer is computed. ``None`` means "the caller did
        not name a frame, so reuse this cycle's cached decision" (the main
        pipeline path); an explicit ``True``/``False`` is a seam naming its own
        divergent space. Both ``resolve_entity_target`` — which produces the
        wire value — and :meth:`_gate_middle_rail_clearance` — which has to
        un-transform it to compare against a live rail reading — go through
        here, so the two can never disagree about what frame the number in
        flight is in (the #993 bug class; issue #1115).
        """
        return self._dual_entity_inverse if inverted is None else inverted

    def capture_dispatch_token(self, entity_id: str) -> bool | None:
        """Stamp the middle rail's dispatch frame onto the command being booked.

        Resolves the frame through the SAME :meth:`_dispatch_frame` rule the
        gate and ``resolve_entity_target`` use, so the stamp is literally the
        answer the dispatch-path gate computed for this command — no second
        inversion path (the #993 bug class; issue #1115).

        ``CoverCommandService`` takes the stamp once per dispatch — before the
        gate, so the same bool answers that dispatch's own clearance question
        and is stored beside the target it books — and hands it back when the
        reconciliation timer re-sends that target, which is the only way the
        gate can un-transform a number dispatched by an earlier seam: this
        policy's own per-cycle cache has by then been restated by every
        intervening resolve, dispatched or skipped.

        ``None`` for the bottom rail and for every non-Model-C instance — no
        rail there needs its dispatched value un-transformed later.
        """
        if not self._is_dual_entity_middle_rail(entity_id):
            return None
        return self._dispatch_frame(self._dual_entity_dispatch_inverse)

    def resolve_entity_target(
        self,
        entity_id: str,
        position: int,
        *,
        inverted: bool | None = None,
        interpolated: bool = False,  # noqa: ARG002
    ) -> int:
        """Remap the middle-rail entity's absolute position (Model C only).

        The bottom rail (primary) and every non-middle entity pass through
        unchanged; only the configured middle rail is remapped. The middle rail
        sits at ``M = 100 - blend * (100 - P) / 100`` in OPEN-percent space,
        where ``P`` is the bottom rail's coverage and ``blend`` the sheer share
        (100 = all sheer → middle coincides with the bottom rail; 0 = all
        blackout → middle fully open). The dispatched ``position`` is already in
        wire space, so it is un-inverted to open-percent, remapped,
        no-pass-clamped (``M >= P``), then re-inverted — keeping both rails
        inverting identically. A cleared blend (or any non-dual-entity model) is
        identity.

        ``inverted`` states the wire value's inversion space explicitly. The
        main pipeline dispatch caches its inversion decision in
        ``_dual_entity_inverse`` (mirroring ``coordinator.state``) and passes
        ``None`` here to reuse it. The broadcast seams dispatch in a divergent
        space — the sunset/end-time loops invert iff inverse-state is CONFIGURED
        (unconditional of interpolation), the auto-off return loop never
        inverts — so each passes its own explicit ``True``/``False``.
        Reusing the cached flag there would un-invert with the wrong assumption
        and cross the bottom rail physically (#993).

        ``interpolated`` is accepted for seam parity and deliberately NOT
        consulted: the remap is arithmetic in open-percent space and never
        unwinds the calibration curve. That is consistent in both modes —
        interpolation forces ``axis_inverted`` False, so the cached flag is
        False too and the interpolated wire value is treated as open-percent
        either way. Mapping a motor reading back onto the linear scale is
        #925's territory, not this hook's.
        """
        if (
            self._control_model != DAY_NIGHT_MODEL_DUAL_ENTITY
            or entity_id != self._dual_entity_middle_rail
        ):
            return position
        # Record the frame BEFORE the blend check: even a cycle that resolves no
        # blend still dispatches this rail, and the travel gate that follows has
        # to un-transform that value against the same frame this call named.
        inverse = self._dispatch_frame(inverted)
        self._dual_entity_dispatch_inverse = inverse
        if self._dual_entity_blend is None:
            return position
        blend = self._dual_entity_blend
        p_open = flip_if(position, inverted=inverse)
        m_open = round(
            POSITION_OPEN - blend * (POSITION_OPEN - p_open) / DAY_NIGHT_SHEER
        )
        # Belt-and-braces no-pass clamp (the arithmetic already guarantees it).
        m_open = max(m_open, p_open)
        return flip_if(m_open, inverted=inverse)

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
            if method == ControlMethod.EXTREME_HEAT:
                # EXTREME_HEAT blocks extreme OUTDOOR heat and fires in any
                # season on the outdoor temperature alone — so it forces the
                # blackout fabric even with an AC-cool interior (is_summer
                # False). Presenting is_summer=True to the (unconditional,
                # allow_sheer_in_summer=False) climate selector yields blackout
                # without duplicating the fabric-choice arithmetic (#993).
                is_summer = True
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
        # Single-carriage models (B split-range, C dual-entity) drive a position
        # axis directly — no tilt to thread — but the primary position's own
        # endpoints (0/100) should still force open/close for seating (#897).
        if not self._drives_dual_axis():
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
        self._logger = kwargs["logger"]
        self._position_tolerance = kwargs["position_tolerance"]
        # Model C rail-role resolution needs the instance's cover list; additive
        # kwarg, read via ``kwargs.get`` so every other policy ignores it.
        self._attached_entities = tuple(kwargs.get("entities") or ())
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
        self, result: PipelineResult, cmd_svc, entity_id: str | None = None
    ) -> SecondaryAxisCheck | None:
        """Build the per-cycle blend-axis manual-override check.

        Issue #1006: Model A drives its blend axis through the SAME
        ``DualAxisSequencer`` (and the same ``_tilt_targets`` store) venetian
        uses, so ``expected`` is anchored to the value ACP last DISPATCHED via
        the shared :func:`resolve_dispatched_secondary_expected` rule — never the
        mutable per-cycle ``result.tilt`` a mid-transit reevaluation may have
        changed. When no blend has been dispatched (empty anchor) the helper
        returns ``None`` and the check yields no independent blend
        manual-detection. ``entity_id`` carries the per-entity anchor context.
        """
        # Single-carriage models (B, C) have no physical blend axis to check.
        if not self._drives_dual_axis():
            return None
        if result is None or result.tilt is None:
            return None
        return SecondaryAxisCheck(
            expected=resolve_dispatched_secondary_expected(
                self._sequencer, entity_id, result
            ),
            attribute="current_tilt_position",
            label="tilt",
            suppression=self.primary_axis_suppression,
        )

    async def maybe_update_tilt_only(
        self,
        entity_id: str,
        *,
        current_position: int | None,
        context: Any,
        reason: str,
    ) -> None:
        """Send a blend-only update when the position axis won't fire this cycle.

        A blend update that lands inside the prior sequence's back-rotate
        suppression window cannot send yet, but it must NOT be dropped (#756):
        it is recorded pending and a single refresh is scheduled at suppression
        expiry so it fires promptly rather than waiting for the next unrelated
        tracked-entity change. ``has_pending_secondary_axis`` keeps the
        coordinator re-attempting dispatch meanwhile. A forced handler
        transition (``context.force`` — e.g. ``custom_position_released``) is
        new handler intent, not motor back-rotate drift, so it bypasses the
        deferral and sends immediately — but only once the carriage has stopped
        moving, preserving the mid-travel guard (#770). Mirrors venetian.
        """
        if not self._drives_dual_axis():
            return
        if self._sequencer is None or self._last_blend is None:
            return
        if self._sequencer.is_in_suppression(entity_id):
            if context.force and not self._sequencer.is_carriage_moving(entity_id):
                await self._sequencer.update_tilt_only(
                    entity_id,
                    tilt_target=self._last_blend,
                    current_position=current_position,
                    reason=reason,
                    force=True,
                )
                return
            self._sequencer.record_pending_tilt(
                entity_id,
                tilt_target=self._last_blend,
                current_position=current_position,
                reason=reason,
            )
            if self._schedule_refresh_after is not None:
                remaining = self._sequencer.suppression_remaining_seconds(entity_id)
                if remaining is not None:
                    self._schedule_refresh_after(remaining)
            return
        await self._sequencer.update_tilt_only(
            entity_id,
            tilt_target=self._last_blend,
            current_position=current_position,
            reason=reason,
        )

    def has_pending_secondary_axis(self, entity_id: str) -> bool:
        """Return True while a deferred axis/rail command is queued.

        Two deferral sources share this latch, because the coordinator's response
        to both is identical — withhold this cycle's dispatched-target signature
        so the command is re-attempted next cycle: a Model A blend-only update
        deferred by the back-rotate suppression window (#756), and a Model C
        middle-rail position command withheld while the bottom rail still blocks
        its target (#1115).
        """
        if entity_id in self._pending_middle_rail:
            return True
        if self._sequencer is None:
            return False
        return self._sequencer.has_pending_tilt(entity_id)

    async def apply_user_tilt(
        self,
        entity_id: str,
        *,
        tilt: int,
        reason: str,
    ) -> bool:
        """Drive a user-requested fabric blend on the BLEND axis only (#684).

        A day/night Model A shade is dual-axis: the ``set_tilt`` service / the
        ``set_axes`` tilt axis / the proxy tilt slider set the fabric blend
        (sheer↔blackout) WITHOUT moving the carriage. Mirrors venetian — read
        the current carriage position purely as the sequencer's pairing
        reference (never commanded) and force the send so a user re-requesting
        the current blend still fires. Single-carriage models (B split-range,
        C dual-entity) have no independent blend axis, so they return
        not-handled and the coordinator falls back to the position path.
        """
        if not self._drives_dual_axis() or self._sequencer is None:
            return False
        current_position = self._sequencer._get_current_position(entity_id)
        await self._sequencer.update_tilt_only(
            entity_id,
            tilt_target=tilt,
            current_position=current_position,
            reason=reason,
            force=True,
        )
        return True

    @property
    def _dual_entity_bottom_rail(self) -> str | None:
        """The Model C bottom (primary) rail — the configured cover that isn't middle.

        Derived rather than stored: the middle rail is the only role the user
        picks, so the bottom rail is whatever else this instance controls. ``None``
        when ``attach`` was never given the cover list, or the list holds nothing
        but the middle rail — a degenerate config the travel gate cannot act on.
        """
        middle = self._dual_entity_middle_rail
        return next((eid for eid in self._attached_entities if eid != middle), None)

    async def _gate_middle_rail_clearance(
        self,
        entity_id: str,
        position: int,
        reason: str,
        *,
        wait: bool,
        dispatch_token: bool | None,
    ) -> bool:
        """Hold the middle rail until the bottom rail has cleared its target.

        **The physical model.** One headbox carries two rails on a shared track.
        Sheer fabric spans head rail → middle rail; blackout fabric spans middle
        rail → bottom rail. The bottom rail is the lower of the two and the
        middle rail cannot pass below it: whatever the arithmetic says, the middle
        rail can only reach a target the bottom rail is not currently occupying or
        sitting above. So the bottom rail has to start positioning first, and the
        middle rail is free to move the moment the bottom rail is below the middle
        rail's target.

        The no-pass clamp in :meth:`resolve_entity_target` (``M >= P``) states
        that invariant about two NUMBERS — the two eventual targets. It cannot see
        where either rail actually IS. When a cycle lowers a shade whose bottom
        rail is currently stacked up near the head rail, the middle rail's target
        sits BELOW the bottom rail's live position, and commanding it there drives
        a motor into a mechanical block: stall, over-current trip, or lost
        calibration (issue #1115).

        The comparison is in OPEN-PERCENT space on both sides, via ``flip_if``
        against the DISPATCH FRAME — the frame of the dispatch that produced
        THIS number, replayed through the one shared :meth:`_dispatch_frame`
        rule. ``M >= P`` in open-percent is ``M <= P`` on the wire, so a raw
        comparison inverts the verdict for every inverse-state install (the #993
        bug class), and a second, divergent inversion path is exactly what must
        not be introduced.

        Which dispatch that is depends on the caller, so the caller says.
        ``dispatch_token`` is the stamp :meth:`capture_dispatch_token` minted
        when the command was BOOKED. BOTH position paths pass one: the dispatch
        path captures the stamp immediately before this gate and books that very
        value with the target, and the resend replays what was stored, because
        the number it is putting back on the wire is the one that dispatch
        produced. On a Model C middle rail the stamp is always a ``bool``, so
        neither position path ever reaches the fallback below.

        ``None`` reaches here only for a target no dispatch produced, and the
        callers that book one are countable: ``restore_target`` rehydrating a
        persisted target after a reload, the coordinator recording an
        externally-observed My move, and ``send_my_position`` booking the user's
        configured My percent — a number ``stop_cover`` puts on the wire without
        a position, so nothing ever expressed it in a frame. There is no stamp
        to replay for those, so the live per-cycle record answers: it is the
        only frame evidence left, and unlike a frozen stamp invented at booking
        time it re-converges on the install's own frame the moment the pipeline
        resolves this rail again. Reading that record on a RESEND that DOES have
        a stamp is the provenance bug this parameter exists to close: the record
        belongs to the last RESOLUTION, and one resolve-then-skip cycle is
        enough to flip the verdict on a target no resolve has touched
        (issue #1115).

        It is emphatically NOT ``context.inverse_state``. That names the
        install's configured flag, and the broadcast seams dispatch in a
        divergent space on purpose — the auto-control-off return loop sends the
        raw default un-inverted. Flipping the middle rail's target against the
        install flag while the seam built it un-inverted withholds a command
        that is already physically clear, on every inverse-state Model C
        install. Both sides of the comparison ride the same frame: the bottom
        rail was commanded by the same seam, so its live reading speaks it too.

        ``wait`` picks how long the bottom rail gets. The dispatch path waits
        out the full budget: it has just issued the bottom rail's command and
        nothing else will re-drive the middle rail this cycle. A caller that is
        already a periodic retry loop passes ``False`` and takes a single read —
        see :meth:`await_dispatch_clearance`. The clearance PREDICATE is
        identical in both modes; only the number of chances the rail gets to
        satisfy it differs.

        Returns ``True`` to let the command through, ``False`` to withhold it.
        Withholding latches the entity pending so the coordinator keeps
        re-attempting on later cycles; it never drops the command. Unreadable or
        never-clearing is deliberately fail-CLOSED (withhold), because a rail
        whose position cannot be read cannot be proven safe to drive into — while
        an un-resolvable bottom rail is fail-OPEN (proceed), because there is then
        no coupled rail to protect and withholding forever would brick the shade.
        """
        seq = self._sequencer
        bottom_rail = self._dual_entity_bottom_rail
        if seq is None or bottom_rail is None:
            self._debug(
                "Model C rail gate inert for %s: no bottom rail resolved", entity_id
            )
            return True

        inverse = self._dispatch_frame(
            self._dual_entity_dispatch_inverse
            if dispatch_token is None
            else dispatch_token
        )
        middle_open = flip_if(position, inverted=inverse)
        tolerance = self._position_tolerance

        def _cleared(bottom_wire: int) -> bool:
            return flip_if(bottom_wire, inverted=inverse) <= middle_open + tolerance

        if await seq.wait_until_position(
            bottom_rail, _cleared, timeout_seconds=None if wait else 0
        ):
            self._pending_middle_rail.discard(entity_id)
            return True
        self._pending_middle_rail.add(entity_id)
        self._debug(
            "Model C rail gate deferring %s → %s%% (%s): bottom rail %s has not "
            "cleared the middle rail's target",
            entity_id,
            position,
            reason,
            bottom_rail,
        )
        return False

    async def await_dispatch_clearance(
        self,
        entity_id: str,
        *,
        position: int,
        reason: str,
        wait: bool = True,
        dispatch_token: bool | None = None,
    ) -> bool:
        """Hold a Model C middle-rail command until the bottom rail has cleared.

        The single gate every position path consults: ``apply_position`` before
        it books an outbound command, and ``run_reconciliation_pass`` before it
        re-sends a recorded target. Neither can trigger the Model A blend
        pre-send that lives in ``before_position_command``.

        ``wait=False`` takes ONE reading instead of polling out the budget —
        what the reconciliation timer passes, because that budget is its own
        interval and a blocked rail would otherwise keep a pass alive into the
        next one. The bottom rail is not going anywhere in the meantime: the
        withhold latches and the next tick re-asks.

        ``dispatch_token`` names WHICH dispatch ``position`` came from — the
        stamp :meth:`capture_dispatch_token` minted when that command was
        booked. Both paths supply it: the dispatch path the stamp it just took,
        the resend path the stamp it stored. So the gate un-transforms the
        number against the frame it is actually expressed in rather than
        against a per-cycle record a later resolve may have restated
        (issue #1115). ``None`` arrives only from a target no dispatch produced
        — see :meth:`_gate_middle_rail_clearance` for the fallback and who
        relies on it.
        """
        if not self._is_dual_entity_middle_rail(entity_id):
            return True
        return await self._gate_middle_rail_clearance(
            entity_id, position, reason, wait=wait, dispatch_token=dispatch_token
        )

    def _debug(self, msg: str, *args) -> None:
        """Emit a debug line once ``attach`` has supplied a logger."""
        if self._logger is not None:
            self._logger.debug(msg, *args)

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
        """Send Model A's blend before the carriage command fires.

        Mirrors the venetian tilt-first order (issue #33): sending the fabric
        before the carriage starts opening lets the actuator settle the blend
        into the target rather than reasserting a cached value mid-travel.

        Model C's rail-travel gate is deliberately NOT here — it is a decision,
        not an effect, and lives in :meth:`await_dispatch_clearance`, which the
        dispatch path asks before it books the outbound command (issue #1115).
        """
        # Single-carriage models (B, C) have no separate blend axis to pre-send
        # — the position command carries everything on one carriage move.
        if not self._drives_dual_axis():
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
        # Single-carriage models (B, C) — the carriage move is complete, there
        # is no blend axis to sequence afterward.
        if not self._drives_dual_axis():
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
