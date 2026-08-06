"""CoverTypePolicy base class.

One concrete subclass per supported cover type. The coordinator selects a
single instance via ``get_policy()`` at startup; every venetian-specific
decision (calc engine choice, post-pipeline tilt fill, manual-override
secondary axis, dual-axis cover-command sequencing) lives behind a hook
on this class so the shared code paths never branch on cover type.

Three of four cover types (blind, awning, tilt) implement only
``build_calc_engine``; the rest of the hooks default to no-ops. Venetian
overrides everything.
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.const import (
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.helpers import selector

from ..const import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
    POSITION_CLOSED,
    POSITION_OPEN,
    GroupScene,
)
from ..helpers import (
    get_open_close_state,
    is_assumed_state,
    should_use_tilt,
    state_attr,
)
from ._summary_labels import AXIS_LABELS_EN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

    from ..engine.covers import AdaptiveGeneralCover
    from ..pipeline.types import PipelineResult
    from ..services.configuration_service import ConfigurationService

_LOGGER = logging.getLogger(__name__)


def _as_optional(marker: vol.Marker) -> vol.Optional:
    """Re-emit *marker* as ``vol.Optional``, preserving its default if any.

    Used so the fov sliders are not ``vol.Required`` (#565): a Required field
    triggers HA's frontend client-side "all required fields filled" check, which
    can block the "Generate FOV from measurements" button's re-render submit. The
    existing ``default`` callable is reused so no ``90`` literal is duplicated.
    """
    if marker.default is vol.UNDEFINED:
        return vol.Optional(str(marker))
    return vol.Optional(str(marker), default=marker.default)


# ---------------------------------------------------------------------------
# Axis-related string constants
# ---------------------------------------------------------------------------
# HA cover entities expose two scalar attributes for current state and two
# capability flags in supported_features. These names are part of HA's contract
# so they're stable strings — naming them here lets the policy/axis layer
# reference symbolic identifiers instead of raw literals.

STATE_ATTR_POSITION = "current_position"
STATE_ATTR_TILT_POSITION = "current_tilt_position"

CAP_HAS_SET_POSITION = "has_set_position"
CAP_HAS_SET_TILT_POSITION = "has_set_tilt_position"
CAP_HAS_OPEN = "has_open"
CAP_HAS_CLOSE = "has_close"
CAP_HAS_STOP = "has_stop"

AXIS_NAME_POSITION = "position"
AXIS_NAME_TILT = "tilt"

# Numeric range + unit every controllable axis exposes today. HA cover
# position/tilt are 0–100 %. Named here so the discovery surface (#725) and the
# ``CoverAxis`` field defaults reference one constant instead of a bare literal.
AXIS_VALUE_MIN = 0
AXIS_VALUE_MAX = 100
AXIS_VALUE_UNIT = "%"


# ---------------------------------------------------------------------------
# Config-flow entity-selector filters
# ---------------------------------------------------------------------------
# The ``EntityFilterSelectorConfig`` structures returned by
# ``entity_selector_filter()``, built from HA's own ``CoverEntityFeature`` names.
# They belong here rather than in ``const.py`` for the same reason as the
# capability flags above — they define the cover-type abstraction boundary — and
# because they are schema structures assembled at import time, not plain values.
# Both strictness levels sit together so a policy author reads them side by side
# and picks deliberately (#1114).

# Cover types that drive a tilt axis unconditionally (tilt, venetian, louvered
# roof). HA's ``supported_features`` filter is OR-of-listed, not AND, so the
# venetian's additional ``set_position`` need is surfaced as a config-flow
# capability warning rather than by a second entry here.
TILT_CAPABLE_ENTITY_FILTER = selector.EntityFilterSelectorConfig(
    domain="cover",
    supported_features=["cover.CoverEntityFeature.SET_TILT_POSITION"],
)

# Cover types whose control models all need only ``set_position`` (e.g. Day/Night
# Models B/C, #1114) — intentionally admits position-only hardware
# (``supported_features`` with no tilt bit) that the tilt filter would exclude.
POSITION_CAPABLE_ENTITY_FILTER = selector.EntityFilterSelectorConfig(
    domain="cover",
    supported_features=["cover.CoverEntityFeature.SET_POSITION"],
)

# Inverse of the filters above: HA ``supported_features`` name → the ``CAP_*``
# flag ``check_cover_features`` sets for it. Lets
# ``CoverTypePolicy.entities_satisfy_selector`` turn any policy's picker filter
# back into a predicate over already-bound covers (issue #1132) without a second
# capability matrix. Every feature any registered policy filters on must appear
# here — locked by
# ``tests/test_cover_types/test_invariants.py::test_entity_filter_features_are_all_mapped``.
ENTITY_FILTER_FEATURE_CAPS: dict[str, str] = {
    "cover.CoverEntityFeature.SET_POSITION": CAP_HAS_SET_POSITION,
    "cover.CoverEntityFeature.SET_TILT_POSITION": CAP_HAS_SET_TILT_POSITION,
    "cover.CoverEntityFeature.OPEN": CAP_HAS_OPEN,
    "cover.CoverEntityFeature.CLOSE": CAP_HAS_CLOSE,
    "cover.CoverEntityFeature.STOP": CAP_HAS_STOP,
}


@dataclass(frozen=True, slots=True)
class CoverAxis:
    """One controllable axis on a cover entity.

    Encodes everything the control code currently re-derives from the cover
    type string: the HA service to call, the service-data attribute that
    carries the target value, the state attribute that carries the current
    value, the capability flag that signals "this entity exposes this axis",
    and the cover-type semantic of "what does fully-open mean". Passing a
    ``CoverAxis`` around eliminates ``cover_type == "cover_tilt"`` checks at
    call sites.

    The trailing fields are self-discovery metadata (#725): ``label_key`` is
    the i18n key for the axis's user-facing name, and ``value_min`` /
    ``value_max`` / ``unit`` describe its numeric range. All four carry safe
    defaults so existing ``CoverAxis`` construction sites (and Liskov-conformant
    fifth-cover-type policies) keep working unchanged.

    ``drive_fallbacks`` records the alternative capability sets that let the
    integration drive this axis even when the native ``capability_key`` is
    absent. It is an OR-of-ANDs: the axis is drivable if *any* inner group has
    *all* its capability flags set. The position axis declares
    ``((CAP_HAS_OPEN, CAP_HAS_CLOSE),)`` because a cover with no
    ``set_cover_position`` is still moved to its endpoints via
    ``open_cover`` / ``close_cover`` (see ``routing.route_service_call``); the
    tilt axis has no fallback, so it stays drivable only with native tilt. This
    keeps the axis model — not a cover-type string check — the single source of
    truth for "can this axis be driven" (#886).

    ``inversion_option_key`` and ``interpolatable`` encode which config option
    reverses this axis and whether interpolation suppresses that reversal —
    the two inputs ``axis_inverted`` needs (#1028). Both carry safe defaults so
    existing construction sites and Liskov-conformant stub policies are
    unaffected.
    """

    name: str
    service: str
    service_attr: str
    state_attr: str
    capability_key: str
    open_blocks_sun: bool
    label_key: str = ""
    value_min: int = AXIS_VALUE_MIN
    value_max: int = AXIS_VALUE_MAX
    unit: str = AXIS_VALUE_UNIT
    drive_fallbacks: tuple[tuple[str, ...], ...] = ()
    inversion_option_key: str = CONF_INVERSE_STATE
    interpolatable: bool = True

    def is_drivable(self, caps: Any) -> bool:
        """Whether the integration can drive this axis on an entity with *caps*.

        True when the native capability flag is set, or when any
        ``drive_fallbacks`` group is fully satisfied (e.g. position via
        ``open_cover`` / ``close_cover`` on a cover lacking
        ``set_cover_position``). Mirrors ``routing.route_service_call``'s reach,
        so the ``set_axes`` service and the discovery ``supported`` flag agree
        with what actually dispatches.
        """
        if caps_get(caps, self.capability_key):
            return True
        return any(
            all(caps_get(caps, cap) for cap in group) for group in self.drive_fallbacks
        )


# Module-level singletons. Each policy declares ``axes`` referencing these so
# every policy describing a position axis shares one ``CoverAxis`` instance.
# Awning's "open=blocks-sun" semantic differs from blind/tilt/venetian, so
# awning declares its own ``POSITION_AXIS_OPEN_BLOCKS_SUN`` rather than
# mutating the shared singleton.

POSITION_AXIS = CoverAxis(
    name=AXIS_NAME_POSITION,
    service=SERVICE_SET_COVER_POSITION,
    service_attr=ATTR_POSITION,
    state_attr=STATE_ATTR_POSITION,
    capability_key=CAP_HAS_SET_POSITION,
    open_blocks_sun=False,
    label_key="axes.position",
    drive_fallbacks=((CAP_HAS_OPEN, CAP_HAS_CLOSE),),
)

POSITION_AXIS_OPEN_BLOCKS_SUN = CoverAxis(
    name=AXIS_NAME_POSITION,
    service=SERVICE_SET_COVER_POSITION,
    service_attr=ATTR_POSITION,
    state_attr=STATE_ATTR_POSITION,
    capability_key=CAP_HAS_SET_POSITION,
    open_blocks_sun=True,
    label_key="axes.position",
    drive_fallbacks=((CAP_HAS_OPEN, CAP_HAS_CLOSE),),
)

TILT_AXIS = CoverAxis(
    name=AXIS_NAME_TILT,
    service=SERVICE_SET_COVER_TILT_POSITION,
    service_attr=ATTR_TILT_POSITION,
    state_attr=STATE_ATTR_TILT_POSITION,
    capability_key=CAP_HAS_SET_TILT_POSITION,
    open_blocks_sun=False,
    label_key="axes.tilt",
    inversion_option_key=CONF_INVERSE_TILT,
    interpolatable=False,
)

# The tilt axis as a cover's PRIMARY (and only) axis — tilt-only types such as
# ``cover_tilt`` and ``cover_louvered_roof``. Identical to ``TILT_AXIS`` in
# every HA-facing respect (same service, same attributes, same capability), and
# differs only in the two config-semantics fields:
#
#   * ``inversion_option_key`` — ``inverse_tilt`` is offered by the venetian
#     geometry schema alone, so a tilt-only instance can never set it. What it
#     IS configured with is ``inverse_state``, the shared position-schema option
#     every cover type gets. ``TILT_AXIS``'s ``inverse_tilt`` is correct only
#     for the SECOND axis of a venetian / day-night shade, where the option is
#     real and separately configured.
#   * ``interpolatable`` — a single-axis cover runs its one axis through the
#     calibration curve like any other, and ``coordinator._to_cover_frame``
#     treats interpolation and inverse-state as mutually exclusive there.
#     Interpolation suppresses inversion on the second axis of a venetian only
#     because the sequencer's ``_to_wire`` reads ``inverse_tilt`` raw.
#
# Derived with ``dataclasses.replace`` so the shared ``TILT_AXIS`` singleton
# stays the single definition of the tilt axis's HA contract.
TILT_AXIS_PRIMARY = dataclasses.replace(
    TILT_AXIS,
    inversion_option_key=CONF_INVERSE_STATE,
    interpolatable=True,
)


def axis_inverted(axis: CoverAxis, options: Mapping[str, Any] | None) -> bool:
    """Whether *axis* is effectively reversed for the install described by *options*.

    Single source of truth for the "is this axis inverted right now" question
    (#1028). Derived at read time from ``config_entry.options`` — never cached
    on an instance — so it cannot drift from the config the way the three
    hand-written copies of this formula did.

    An axis is inverted when its ``inversion_option_key`` is set AND the
    inversion is not suppressed by interpolation. Position inversion IS
    suppressed (``coordinator.state`` logs the combination as unsupported and
    skips it); tilt inversion is not, because the venetian sequencer's
    ``_to_wire`` reads ``inverse_tilt`` directly and never consults the
    calibration curve. ``interpolatable`` on the axis carries that asymmetry
    so no caller has to know which axis it is holding.
    """
    if not options:
        return False
    if not options.get(axis.inversion_option_key):
        return False
    return not (axis.interpolatable and bool(options.get(CONF_INTERP)))


@dataclass(frozen=True, slots=True)
class AxisDescriptor:
    """Self-discovery view of one axis on a specific install (issue #725).

    A flattened, serialisable projection of a ``CoverAxis`` — everything a
    consumer (the ``cover_discovery`` sensor attribute, the ``set_axes``
    service, the companion Lovelace card) needs to render and drive an axis
    without knowing the cover type — plus ``supported``, the per-install
    rollup of whether the bound cover(s) actually expose the axis.

    ``inverted`` (#1028) states whether this install reverses the axis, so a
    consumer reading a raw cover attribute knows which frame it is in without
    guessing from the config. Defaults to ``False`` so a caller that describes
    an axis without options is unaffected.
    """

    id: str
    label: str
    label_key: str
    min: int
    max: int
    unit: str
    capability_key: str
    state_attr: str
    service_attr: str
    open_blocks_sun: bool
    supported: bool
    inverted: bool = False


@dataclass(frozen=True, slots=True)
class CoverDescriptor:
    """Self-discovery view of a cover type + its axes (issue #725)."""

    cover_type: str
    cover_label: str
    axes: tuple[AxisDescriptor, ...]


@dataclass(frozen=True, slots=True)
class ExternalInterlockPlan:
    """How to make a blocked entity's target reachable (#1138).

    The answer :meth:`CoverTypePolicy.plan_external_command_interlock` returns
    when a command cannot physically complete because a coupled entity is
    standing in the way. The name says "external" for the origin it was built
    for, and the class keeps it, but the plan is origin-agnostic: it answers the
    same question for a command that arrived from outside ACP and for one of
    ACP's own user seams that the clearance gate withheld. Pure data,
    deliberately free of any cover-type vocabulary: a "leading" entity to move
    first and a "follower" whose own command has to be re-issued behind it. The
    coordinator executes it without knowing that the two are rails, or that the
    cover is a day/night shade.

    Both targets are WIRE numbers — the device frame the command was already
    expressed in — so the executor hands them straight to
    ``CoverCommandService.apply_position``, whose contract is "already
    transformed". Running them back through the coordinator's
    ``_entity_target`` would double-apply inverse/interpolation and re-map the
    very target the user named.

    ``reason`` is the dispatch label the corrective commands carry into the
    command service, the manual-override manager and the event timeline, so the
    whole sequence is attributable to one cause in diagnostics.

    ``dispatch_token`` is the provenance stamp for BOTH targets, minted by the
    planning policy and replayed verbatim by the executor into
    ``CoverCommandService.apply_position`` — opaque to everything in between,
    exactly like the stamp :meth:`CoverTypePolicy.capture_dispatch_token`
    produces on the normal dispatch path. It travels WITH the plan because the
    plan is the seam that produced these numbers: nothing resolved them, so
    ``capture_dispatch_token`` would answer about some unrelated earlier
    dispatch instead (issue #1115's provenance bug, pointed at the corrective
    caller). The default ``None`` says "no dispatch produced this number", which
    every gate already resolves to the install's own frame — the right answer
    for a policy that has no frame of its own.
    """

    leading_entity_id: str
    leading_target: int
    follower_entity_id: str
    follower_target: int
    reason: str
    dispatch_token: Any = None


def caps_get(caps: Any, key: str, default: bool = False) -> bool:
    """Read a capability flag from either a dict or a ``CoverCapabilities``.

    ``check_cover_features`` returns a dict; ``CoverProvider`` constructs the
    dataclass form. Both shapes are consumed throughout the integration so a
    single accessor — combined with the ``CAP_*`` constants above — replaces
    hardcoded ``caps.get("has_…")`` strings at every call site.
    """
    if caps is None:
        return default
    if isinstance(caps, dict):
        return bool(caps.get(key, default))
    return bool(getattr(caps, key, default))


# Internal alias retained for backward compatibility with existing imports.
_caps_get = caps_get


# Registry of concrete cover-type policies, keyed by ``cover_type``. Populated
# automatically by ``CoverTypePolicy.__init_subclass__`` for subclasses that
# opt in with ``register=True`` (the four shipped types + future ones). Test
# stub policies omit the flag so they don't pollute the global registry.
POLICY_REGISTRY: dict[str, type[CoverTypePolicy]] = {}


class CoverTypePolicy(ABC):
    """Per-cover-type policy."""

    cover_type: ClassVar[str]

    # Whether this policy drives a physical cover (registers platforms, has at
    # least one controllable axis). The default is ``True`` so every real
    # cover-type policy is treated as a cover. Virtual entry types — the
    # building profile, which only stores shared building-level sensor IDs and
    # registers no platforms — set this ``False`` so cover-contract suites,
    # cover-only menus, and the setup path can filter them out by capability
    # rather than by branching on the cover-type string.
    controls_cover: ClassVar[bool] = True

    # Whether this policy orchestrates *other* covers instead of driving a
    # geometry pipeline of its own. Only the cover group sets this ``True``:
    # it controls covers (``controls_cover = True``) but setup must build a
    # ``GroupCoordinator`` rather than the sun/geometry coordinator. A second
    # capability flag keeps that branch off the cover-type string, same as
    # ``controls_cover`` (issue #790).
    is_orchestrator: ClassVar[bool] = False

    # Whether this policy is a named command queue rather than anything that
    # moves. Only the Command Queue entry type sets this ``True`` (issue #1189).
    # It shares ``controls_cover = False`` with the Building Profile, so the
    # existing "virtual entry type" branches would otherwise treat it as a
    # profile — propagating sensor keys to nothing and offering it in the
    # profile-link dropdown. A third capability flag keeps that discrimination
    # off the cover-type string, exactly as ``is_orchestrator`` does for groups.
    is_command_queue: ClassVar[bool] = False

    def __init_subclass__(cls, *, register: bool = False, **kwargs: Any) -> None:
        """Auto-register a concrete policy by its ``cover_type``.

        A new cover type becomes available simply by defining its policy
        subclass with ``register=True`` — no edit to a central registry dict.
        """
        super().__init_subclass__(**kwargs)
        if register:
            POLICY_REGISTRY[cls.cover_type] = cls

    # Ordered tuple: the primary axis comes first. ``select_default_axis``
    # consults this when picking which HA service to call. Single-axis covers
    # (blind, awning, tilt) declare one entry; venetian declares two.
    axes: ClassVar[tuple[CoverAxis, ...]] = ()

    # Whether this cover type can shield specific floor zones from direct sun
    # (the "glare zones" feature). Only meaningful for vertical blinds today,
    # but a future cover type that gains the same capability flips this on
    # without touching every gate site.
    supports_glare_zones: ClassVar[bool] = False

    # Whether the "Return to default when disabled" switch is exposed for this
    # cover type. Currently only single-axis position covers (blind, awning)
    # have a meaningful "default height" semantic; tilt-only covers don't, and
    # venetian's default is driven through the dual-axis sequencer rather than
    # a fire-and-forget position. Replaces the legacy string list at
    # ``switch.py`` that hardcoded ``("cover_blind", "cover_awning")``.
    supports_return_to_default_switch: ClassVar[bool] = False

    # Whether the diagnostic surface exposes a dual-axis target sensor (the
    # "Target Tilt" sensor in ``sensor.py``). Only meaningful for cover types
    # that drive both position and tilt on a single HA entity — venetian today.
    # Replaces the literal ``CoverType.VENETIAN ==`` lambda gate that used to
    # live on ``sensor.py:807``.
    exposes_dual_axis_sensor: ClassVar[bool] = False

    # Whether the custom-position config-flow UI surfaces per-slot tilt sliders
    # and the global default/sunset tilt sliders. Only meaningful for cover
    # types whose policy can act on tilt independently — venetian today.
    # Replaces the ``is_venetian = sensor_type == CoverType.VENETIAN`` branch
    # in ``config_flow._build_custom_position_schema_dict``.
    custom_position_includes_tilt: ClassVar[bool] = False

    # Whether the sun-tracking step exposes the "Generate FOV from measurements"
    # button (#565) — a toggle that fills fov_left/right from the window width +
    # reveal depth. Set on the cover types that carry window geometry (vertical
    # blinds + venetians); awnings/tilt keep the plain fov sliders.
    supports_fov_compute: ClassVar[bool] = False

    def fov_compute_schema(self, base: vol.Schema) -> vol.Schema:
        """Insert the "Generate FOV from measurements" toggle before the sliders.

        Returns *base* unchanged unless this policy sets
        ``supports_fov_compute``. When it does, the ``CONF_FOV_COMPUTE`` toggle
        is inserted immediately before the ``fov_left``/``fov_right`` sliders.
        The toggle is a transient button: ticking it fills the sliders from the
        window width + reveal depth on submit (handled in
        ``config_flow._resolve_fov_compute_submit``), after which the form
        re-renders un-ticked with the sliders populated. The sliders are always
        shown and editable; they are made ``vol.Optional`` so the frontend
        Required check never blocks the button's re-render submit (#565). Shared
        here so vertical blinds and venetians get identical behaviour with no
        duplication.
        """
        if not self.supports_fov_compute:
            return base
        from .. import config_fields as cf
        from ..const import CONF_FOV_COMPUTE, CONF_FOV_LEFT, CONF_FOV_RIGHT

        spec = cf.FIELD_SPECS[CONF_FOV_COMPUTE]
        toggle_marker, toggle_selector = spec.to_marker(None, None)

        rebuilt: dict = {}
        inserted = False
        for marker, sel in base.schema.items():
            if str(marker) in (CONF_FOV_LEFT, CONF_FOV_RIGHT):
                if not inserted:
                    rebuilt[toggle_marker] = toggle_selector
                    inserted = True
                rebuilt[_as_optional(marker)] = sel
                continue
            rebuilt[marker] = sel
        if not inserted:
            rebuilt[toggle_marker] = toggle_selector
        return vol.Schema(rebuilt)

    def sync_runtime_options(self, options: dict) -> None:  # noqa: ARG002
        """Refresh option-derived policy state for this update cycle.

        The coordinator calls this once per cycle from ``_update_options`` — on
        the event loop, before the pipeline runs and before the health checks
        ask their predicates — so a policy that caches an option-derived mode
        has it resolved before anything reads it, including on the coordinator's
        very first cycle. Generic on purpose: the coordinator must not know
        which cover types cache what, and must never call a type-specific method
        (CODING_GUIDELINES.md "No String Branches Outside ``cover_types/``").

        Default: no-op. Most policies derive everything from the ``options``
        dict each hook already receives and need no cache.

        This exists so ``build_calc_engine`` can stay a pure builder:
        ``forecast.build_forecast_for_coord`` calls it ~289× per forecast from
        an executor thread, so mutating live policy state there would write
        off the event loop.
        """

    @abstractmethod
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
        """Instantiate the calculation engine for this cover type."""

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
        """Enrich the pipeline result. Default: identity."""
        return result

    def forecast_secondary_axes(
        self,
        *,
        position: int,  # noqa: ARG002
        logger,  # noqa: ARG002
        sol_azi: float,  # noqa: ARG002
        sol_elev: float,  # noqa: ARG002
        sun_data,  # noqa: ARG002
        config,  # noqa: ARG002
        config_service: ConfigurationService,  # noqa: ARG002
        options: dict,  # noqa: ARG002
        minimize_movements: bool,  # noqa: ARG002
        max_coverage_steps: int,  # noqa: ARG002
    ) -> dict[str, int]:
        """Project this cover's non-primary axes at one forecast step (#724).

        The forecast loop calls this alongside the primary ``position`` on each
        *solar* sample and carries the result in ``ForecastSample.axes`` (keyed
        by ``CoverAxis.name``). Single-axis covers have no secondary axis to
        project, so the Liskov-safe default returns an empty map — the forecast
        asks this polymorphic hook rather than branching on the cover type.
        Multi-axis policies (venetian) override it, reusing the same tilt math
        the live path runs so the projected track matches runtime.
        """
        return {}

    def targets_full_mechanical_endpoint(self, result: PipelineResult) -> bool:
        """Whether this update drives the position axis to a full mechanical stop.

        Single source of truth for the ``full_endpoint_target`` flag (issue #897,
        generalizing #755). When True and ``endpoint_use_open_close`` is on, the
        command manager forces close_cover/open_cover instead of dropping the
        final approach to 0/100 as ``same_position`` — so a cover that settles a
        step short of its true stop still seats there. The base default covers
        every single-axis position cover (blind, awning, sliding_curtain, …):
        the target is a full endpoint iff it is 0 or 100. VenetianPolicy narrows
        this to the paired dual-axis endpoint; TiltPolicy (no position axis)
        widens it to never.
        """
        if result is None or result.position is None:
            return False
        return result.position in (POSITION_CLOSED, POSITION_OPEN)

    def position_context_overrides(self, result: PipelineResult) -> dict[str, Any]:
        """Extra kwargs for ``PositionContext``.

        Carries the cover-type-agnostic ``full_endpoint_target`` flag derived
        from :meth:`targets_full_mechanical_endpoint` so the command manager can
        force open_cover/close_cover at the mechanical stops (issue #897).
        """
        return {"full_endpoint_target": self.targets_full_mechanical_endpoint(result)}

    def secondary_axis_check(
        self, result: PipelineResult, cmd_svc, entity_id: str | None = None
    ) -> Any | None:
        """Return a manual-override secondary-axis check, or ``None``.

        ``entity_id`` (issue #1006) lets a multi-axis policy anchor the check's
        expected value to the value ACP last DISPATCHED for that entity rather
        than the mutable per-cycle ``result`` — see the module rule in
        ``managers/manual_override/secondary_axis.py``. Additive with a safe
        default so single-axis policies and legacy 2-arg callers are unaffected.
        """
        return None

    def resolve_entity_target(
        self,
        entity_id: str,  # noqa: ARG002
        position: int,
        *,
        inverted: bool | None = None,  # noqa: ARG002
        interpolated: bool = False,  # noqa: ARG002
    ) -> int:
        """Adjust the dispatched position for one specific entity.

        The coordinator resolves a single ``position`` per update cycle, then
        sends it to every bound entity. A cover type that drives *several*
        physical entities to *different* positions from that one resolved value
        overrides this hook (the Model C day/night shade remaps its middle-rail
        entity while the bottom rail passes through unchanged). The Liskov-safe
        default is identity, so the coordinator dispatch seam asks this
        polymorphic hook rather than branching on the cover type — every other
        cover type keeps sending the resolved position verbatim.

        ``inverted`` and ``interpolated`` together name the DISPATCH FRAME of
        the supplied ``position`` — which of the two mutually exclusive
        logical→wire transforms the caller already applied — so a remapping
        policy can reproduce or undo it.

        **Transform vs. substitute — the rule that decides whether to consult
        them at all.** A policy that *transforms* the supplied ``position``
        into its entity's target (the Model C day/night middle rail, derived
        from the bottom rail's dispatched value) MUST consult the frame, or it
        will undo a transform that was never applied. A policy that *replaces*
        it with an absolute target (the dual panel's blackout, a pure
        open/closed decision independent of the front) MUST ignore the frame
        and derive its own wire space from ``options`` — the caller's frame
        describes a value that policy never consumes, and honouring it
        dispatches into the wrong space whenever the two diverge (#1035).
        ``options`` does not reach this hook: a substituting policy caches its
        wire space in ``post_pipeline_resolve``, which is where ``options``
        arrives, and reads that cache here (see ``DualPanelPolicy``).

        * ``inverted=None`` (the default, and the only value the identity
          implementation ever needs) means "use the policy's own cached
          per-cycle decision": the main pipeline dispatch path, whose frame the
          policy already recorded in ``post_pipeline_resolve``. ``interpolated``
          is not consulted in this mode.
        * An explicit ``inverted=True``/``False`` names the frame outright, and
          ``interpolated`` completes it. The broadcast seams (sunset-window
          transition, end-time-default, auto-control-off return) dispatch an
          absolute default that is inverted-or-not but never interpolated, so
          they leave ``interpolated`` at its default. A user command
          (``async_apply_user_position``) rides the SAME transform as the main
          pipeline but off-cycle, so it names both dimensions rather than
          trusting a cache built for a different value (#993 / #1027). What a
          *substituting* policy does with that frame is governed by the rule
          above: nothing.

        ``interpolated`` exists because ``inverted`` alone cannot describe an
        interpolating install: "interpolated, not inverted" and "neither" both
        collapsed to ``inverted=False``, which silently dropped a calibrated
        cover type's curve. Backward-compatible: every non-remapping policy
        ignores both.
        """
        return position

    def dispatch_order_key(
        self,
        entity_id: str,  # noqa: ARG002
        *,
        position: int | None = None,  # noqa: ARG002
        inverted: bool | None = None,  # noqa: ARG002
    ) -> int:
        """Sort key ordering this cover type's entities within one dispatch cycle.

        The coordinator resolves one position per cycle and fans it out to every
        bound entity. A cover type whose entities are PHYSICALLY coupled — the
        Model C day/night shade's stacked rails, where the middle rail cannot
        travel past the bottom rail — needs its blocking entity commanded first,
        because the per-entity command hooks cannot reach back and reorder the
        caller's loop. Overriding this hook expresses that constraint once; every
        dispatch seam consumes the same ordered view (issue #1115).

        Which entity blocks can depend on the DIRECTION of travel: on a stacked
        pair the rail downstream of the move has to vacate first, and that is the
        bottom rail when the shade lowers but the middle rail when it raises
        (issue #1118). So a seam may NAME the number it is about to fan out and
        the inversion frame it is fanning it out in — the same ``(position,
        inverted)`` pair it hands ``resolve_entity_target`` in the same loop
        body. Omitting them is legal and yields the cover type's
        direction-blind default order; a seam whose per-entity value only comes
        into existence further down its own call chain has no honest pair to
        name and should not invent one.

        The Liskov-safe default is a constant, which makes ``sorted(...)`` a
        stable-sort no-op: the user's config-flow pick order survives verbatim
        for every cover type with independent entities.

        The ``position`` a seam names is the one number that cycle fans out, and
        that stays true for every policy which actually overrides this hook:
        overriding it is one of the three signals
        :meth:`entities_move_independently` reads, so such a policy is never
        dispatched per entity in the first place (#1174).
        """
        return 0

    def entities_move_independently(self) -> bool:
        """Whether each bound entity's position may be decided on its own (#1174).

        A hold (manual override, group lock) keeps every bound cover where
        something authoritative already put it, and a composed floor/ceiling
        then asks "does this cover still satisfy the bound?". On an instance of
        N unrelated covers that question has N answers, and collapsing them into
        one — the arithmetic mean of every cover's position — dragged compliant
        covers to a floor they already satisfied and hid a lone violator behind
        its siblings. So the registry judges and dispatches each cover
        separately, but ONLY where doing so is meaningful.

        It is not meaningful whenever this cover type's entities are not
        independent, and the three hooks that express non-independence are
        exactly the ones consulted here:

        * :meth:`resolve_entity_target` — a per-entity REMAP. The value handed to
          it is a shared position that the policy turns into this entity's own;
          feeding it a value already resolved for that entity applies the remap
          twice (the Model C middle rail derived from a middle-rail number).
        * :meth:`post_pipeline_resolve` — a rewrite of ``PipelineResult.position``
          that runs AFTER the registry. A per-cover target is computed before it
          and therefore bypasses it: the Model B day/night shade folds coverage
          and fabric into one wire there, so a cover released to its own raw
          read carries neither.
        * :meth:`dispatch_order_key` — physical COUPLING. Entities that have to
          be commanded in a mandated order share a track; a bound that moves one
          of them moves the geometry, not one opinion out of several.

        Derived rather than declared, so a new cover type is safe by
        construction: touch any of the three and the per-entity path switches
        itself off, leaving that type on the shared-target behaviour it had
        before #1174. A policy that overrides one of them harmlessly may say so
        by overriding this predicate — and owes the argument for why, in its own
        docstring, because nothing else here can check it.

        Answering ``False`` no longer means "judge the group's mean": it means
        "ask :meth:`hold_reference_position` where this ONE geometry is". See
        there for what a coupled type owes in return (#1179).
        """
        cls = type(self)
        return (
            cls.resolve_entity_target is CoverTypePolicy.resolve_entity_target
            and cls.post_pipeline_resolve is CoverTypePolicy.post_pipeline_resolve
            and cls.dispatch_order_key is CoverTypePolicy.dispatch_order_key
        )

    def hold_reference_position(
        self,
        cover_positions: Mapping[str, int | None],  # noqa: ARG002
        *,
        inverted: bool,  # noqa: ARG002
    ) -> int | None:
        """Reduce these raw reads to ``PipelineResult.position``'s frame (#1179).

        The algebraic INVERSE of the dispatch chain. Dispatch expands one
        abstract position into per-entity wire values —
        :meth:`post_pipeline_resolve` → ``coordinator._to_cover_frame`` →
        :meth:`resolve_entity_target` — and this reduces the per-entity reads
        back to that one abstract position. Only the policy knows the mapping,
        which is why the registry could previously do nothing better than
        average the reads.

        Consulted ONLY when :meth:`entities_move_independently` is ``False``. A
        coupled type's entities express one geometry, so a composed floor or
        ceiling is a statement about that geometry, and the number it has to be
        compared against is the geometry's own position — not the arithmetic
        mean of values from different coordinate systems (a Model C middle rail
        derived from the bottom rail's coverage, a dual-panel back panel's
        binary privacy state, a Model B wire that folds coverage and fabric
        together).

        ``cover_positions`` are **RAW** cover-frame reads, exactly as
        ``PipelineSnapshot.cover_positions`` carries them, and ``inverted``
        names their frame — the same kwarg :meth:`resolve_entity_target` takes,
        so the frame is stated and never guessed (#993). The return value is
        **LOGICAL**: an override that reads a wire encoding must undo the
        inversion and decode in wire space *before* returning
        (CODING_GUIDELINES § "Inverse State").

        ``None`` means "no single answer" — an unconfigured entity role, an
        unreadable anchor, or a cover type with genuinely independent entities
        — and keeps that cycle on the legacy summary mean. That is the base
        answer, so a policy which never touches the dispatch hooks is never
        asked and never has to care.

        ⚠️ **The inverse is partial: interpolation is not unwound.** The forward
        chain ends at ``coordinator._to_cover_frame``, which applies the
        calibration curve *and* the inversion to every dispatched value, but an
        implementation here is only asked to undo the inversion — the reads it
        receives from an interpolated install sit on the motor's own scale and
        are treated as if they were linear. Pre-existing and deliberate: the
        summary mean this hook replaced ignored interpolation identically, so no
        install's frame changed, and ``day_night_shade.resolve_entity_target``
        already declines to unwind it in the forward direction for the same
        reason. It only bites where a read's linear meaning feeds a DECODE
        rather than a bare comparison — a Model B wire near the fabric boundary
        can stash the wrong fabric half, and the clamped hold then re-folds
        behind a fabric the shade is not physically behind. Mapping a motor
        reading back onto the linear scale is #925's territory, not this hook's.
        """
        return None

    def order_for_dispatch(
        self,
        entities: Iterable[str],
        *,
        position: int | None = None,
        inverted: bool | None = None,
    ) -> list[str]:
        """Return ``entities`` in this cover type's mandated dispatch order.

        The single shared ordering mechanism every dispatch seam consumes — the
        main state-change loop, the startup loop, the force-send path, the
        end-time-default and sunset-window broadcasts, and the auto-control-off
        return loop. One view, six consumers: the ordering rule is stated once
        as :meth:`dispatch_order_key` rather than mirrored per seam
        (CODING_GUIDELINES.md "No Code Duplication", issue #1115).

        ``position`` / ``inverted`` are forwarded verbatim to
        :meth:`dispatch_order_key`; see there for what naming them buys and when
        a seam legitimately cannot.

        ``sorted`` is stable, so a policy that leaves ``dispatch_order_key`` at
        its constant default gets the input order back unchanged.
        """
        return sorted(
            entities,
            key=lambda entity_id: self.dispatch_order_key(
                entity_id, position=position, inverted=inverted
            ),
        )

    def required_role_entity_missing(
        self,
        options: Mapping[str, Any],  # noqa: ARG002
        entities: Iterable[str],  # noqa: ARG002
    ) -> bool:
        """Whether an entity this cover type binds to a named role is unfilled (B3).

        A cover type may bind a SECOND entity to a specific physical role — the
        Model C day/night shade's middle rail. With that pick unset, or naming a
        cover outside the instance's own list, the cover type cannot do its job
        and degrades silently into a lesser one. This predicate is the single
        source of truth behind the cover-type boundary for the B3 runtime Repair,
        so the coordinator never branches on cover type (mirrors A3's
        ``tilt_capability_contradiction``). Liskov-safe default: cover types that
        bind no role entity have nothing to report (issue #1115).
        """
        return False

    def attach(self, **kwargs: Any) -> None:
        """Bind late-resolved dependencies (cmd_svc, grace_mgr, …).

        Called by the coordinator after ``CoverCommandService`` is built.
        Policies that need a long-lived helper (e.g. ``VenetianPolicy``'s
        dual-axis sequencer) construct it here. Default: no-op.
        """
        return

    def has_pending_secondary_axis(
        self,
        entity_id: str,  # noqa: ARG002
    ) -> bool:
        """Return whether a secondary-axis command is deferred for this entity.

        Issue #756: dual-axis covers (venetian) can defer a tilt-only update
        when the back-rotate suppression window from the prior position
        sequence is still open. While such a tilt is pending the coordinator
        must not record the resolved-target signature as dispatched — otherwise
        the deferred tilt would never be re-attempted. Single-axis cover types
        have no second axis to defer, so the safe Liskov default is ``False``.
        """
        return False

    def is_in_tilt_suppression(
        self,
        entity_id: str,  # noqa: ARG002
        delta: float = 0.0,  # noqa: ARG002
    ) -> bool:
        """Return whether the tilt-axis suppression window is open.

        ``delta`` is the magnitude of the observed change on the suppressed
        axis; ``VenetianPolicy`` uses it to gate small motor-drift values
        while letting larger user moves fall through. Cover types without a
        back-rotating tilt axis ignore the argument and return ``False``.

        The signature matches the ``Callable[[str, float], bool]`` contract
        consumed by ``SecondaryAxisCheck.suppression`` so the method can be
        passed as that callback directly without an adapter lambda.
        """
        return False

    def primary_axis_suppression(
        self,
        entity_id: str,  # noqa: ARG002
        delta: float = 0.0,  # noqa: ARG002
    ) -> bool:
        """Return True when a primary-axis state change should be ignored.

        Issue #33 cross-axis fix: slow-bus actuators (Somfy IO via Tahoma,
        KNX, Fibaro/Shelly republish) can publish a late
        ``current_position`` tens of seconds after the cover has physically
        stopped. Without a suppression window the position-axis branch of
        ``AdaptiveCoverManager.handle_state_change`` reads the stale
        publish as a 100 % delta versus the commanded target and trips a
        false ``manual_override_set``.

        Default: no suppression. Single-axis cover types (blind, awning,
        tilt) have no back-rotating partner axis and no equivalent
        publish-lag signature, so they opt out. ``VenetianPolicy``
        overrides to consult the same three-tier window that already
        protects the tilt axis — a single predicate shared across both
        axes per CODING_GUIDELINES.md § "Code duplication is not okay".

        Liskov: ``delta=0.0`` and ``entity_id`` are the same shape every
        subclass accepts. Adding a new required parameter on a subclass
        would crash callers holding a ``CoverTypePolicy`` reference; the
        base default of ``False`` keeps non-venetian dispatch safe.
        """
        return False

    async def maybe_update_tilt_only(
        self,
        entity_id: str,  # noqa: ARG002
        *,
        current_position: int | None,  # noqa: ARG002
        context: Any,  # noqa: ARG002
        reason: str,  # noqa: ARG002
    ) -> None:
        """Send a tilt-only update when no position command will fire.

        Default: no-op for cover types without a tilt axis. VenetianPolicy
        overrides this to drive continuous tilt updates.
        """
        return

    async def apply_user_tilt(
        self,
        entity_id: str,  # noqa: ARG002
        *,
        tilt: int,  # noqa: ARG002
        reason: str,  # noqa: ARG002
    ) -> bool:
        """Apply a user-requested tilt on the dedicated tilt axis.

        Returns ``True`` when the request was handled on a real tilt axis;
        ``False`` (the default) when the cover type has no independent tilt
        axis, so the coordinator falls back to its position path.

        This is correct for ``cover_tilt``, whose *primary* axis already IS
        the tilt slats — a user tilt request there is just a position move and
        belongs in ``async_apply_user_position``. Only dual-axis covers
        (venetian) override this to drive tilt without touching the carriage
        (issue #684).
        """
        return False

    async def before_position_command(
        self,
        cmd_svc,  # noqa: ARG002
        entity_id: str,  # noqa: ARG002
        *,
        service: str,  # noqa: ARG002
        position: int,  # noqa: ARG002
        context,  # noqa: ARG002
        reason: str,  # noqa: ARG002
    ) -> None:
        """Run any pre-command SIDE EFFECTS before the position service fires.

        Effects only — whether the command may go out at all is
        :meth:`await_dispatch_clearance`'s question, asked earlier and against a
        command that has not been booked yet. Keeping the two apart is what lets
        this hook run after the dry-run gate (a simulated command must not
        pre-send anything) while the decision runs before the outbound command
        is recorded.

        Default: no-op. ``VenetianPolicy`` overrides this to send tilt-first on
        opening transitions (issue #33) so the actuator's slats reach the target
        angle before the carriage starts moving.
        """
        return

    def capture_dispatch_token(
        self,
        entity_id: str,  # noqa: ARG002
    ) -> Any:
        """Return an opaque stamp describing HOW this dispatch expressed its value.

        The provenance half of :meth:`await_dispatch_clearance`. A policy that
        has to un-transform a dispatched number later — either rail of a Model C
        day/night pair, whose clearance test compares its target against the
        OTHER rail's live reading in open-percent space — cannot re-derive the
        transform after the fact: its own per-cycle cache describes the last
        RESOLUTION, and
        ``resolve_entity_target`` runs as an ARGUMENT to
        ``CoverCommandService.apply_position``, so a cycle that resolves and then
        skips on a delta gate restates that cache for a command which never went
        out (issue #1115, the #993 inversion class).

        So the transform travels WITH the value instead. ``CoverCommandService``
        asks for this stamp ONCE per dispatch, and asks for it BEFORE it puts
        :meth:`await_dispatch_clearance`'s question: one stamp both answers that
        dispatch's own clearance question and is stored beside the target it
        books, so the number is gated and recorded against a single frame rather
        than two reads of a moving one. The reconciliation timer then hands the
        stored stamp back verbatim when it re-sends that target. The manager
        never interprets it — it is this policy's own datum, round-tripped.

        Liskov-safe default: ``None``, i.e. "no provenance needed", which is
        every cover type whose dispatched values need no later un-transforming.
        """
        return None

    async def await_dispatch_clearance(
        self,
        entity_id: str,  # noqa: ARG002
        *,
        position: int,  # noqa: ARG002
        reason: str,  # noqa: ARG002
        wait: bool = True,  # noqa: ARG002
        dispatch_token: Any = None,  # noqa: ARG002
    ) -> bool:
        """Whether this entity may be driven to ``position`` right now.

        The physical-coupling question on its own, separated from
        :meth:`before_position_command` because that hook carries pre-send SIDE
        EFFECTS (the venetian tilt-first command) a caller asking only for the
        go/no-go must not trigger. Which entity gets withheld may depend on the
        direction of travel — on a Model C pair the rail downstream of the move
        leads and the other one waits, so EITHER rail can be the one gated
        (issue #1118). Both position paths —
        ``CoverCommandService.apply_position`` and its reconciliation resend —
        funnel into ONE implementation per policy; the rule is never written
        twice (issue #1115).

        ``wait`` says whether the caller can afford to BLOCK until the coupled
        entity clears. The dispatch path can and must: it has just issued the
        blocking entity's command and nothing else will re-drive this one this
        cycle. A caller that is itself a periodic retry loop passes ``False``
        for a single-shot "is it clear right now?" — blocking there would let a
        pass whose budget matches the timer interval overlap the next one, with
        two live passes mutating the same per-entity state (issue #1115). Same
        eventual behaviour either way; the next tick re-asks.

        ``dispatch_token`` names the dispatch that produced ``position`` — the
        stamp :meth:`capture_dispatch_token` minted for it. BOTH position paths
        supply one: ``apply_position`` captures the stamp just before asking
        this question and books that same stamp with the target, and the resend
        hands the stored stamp straight back. So the answer is computed against
        the dispatch the number actually came from rather than against whatever
        a later resolve left in the policy's per-cycle cache (issue #1115).

        ``None`` therefore does NOT mean "the dispatch path". It means the
        number has no dispatch behind it to describe: a target booked from
        outside one — ``CoverCommandService.restore_target`` rehydrating a
        persisted target after a reload, the coordinator recording an
        externally-observed My move, ``send_my_position`` booking the user's
        configured My percent. Those are raw cover-frame numbers, so a policy
        that needs provenance answers them from what it knows about the INSTALL
        rather than from any one dispatch — nothing expressed them in a
        dispatch's frame for a stamp to name.

        Returns ``False`` to withhold, ``True`` to proceed. Withholding is
        expected to latch (:meth:`has_pending_secondary_axis`) so a later pass
        re-attempts the command. Liskov-safe default: a cover type whose
        entities are physically independent is always clear.
        """
        return True

    def plan_external_command_interlock(
        self,
        entity_id: str,  # noqa: ARG002
        *,
        service: str,  # noqa: ARG002
        wire_target: int,  # noqa: ARG002
        dispatch_token: Any = None,  # noqa: ARG002
    ) -> ExternalInterlockPlan | None:
        """How to make a blocked command on ``entity_id`` reachable (#1138).

        The corrective counterpart of :meth:`await_dispatch_clearance`. That
        hook answers "may ACP drive this entity right now?" about a command ACP
        is about to issue. This one answers "that command cannot complete
        because a coupled entity is standing where it needs to go — what would
        clear the way?" Two origins reach it, and the answer is the same for
        both because the physics is:

        * an EXTERNAL command, already gone out and unvetoable —
          ``EVENT_CALL_SERVICE`` fires as the call executes, so the only remedy
          is corrective: move the blocker and re-issue the user's own target
          behind it;
        * one of ACP's OWN user seams, which :meth:`await_dispatch_clearance`
          just withheld — same remedy, asked one step earlier, before anything
          reached the motor.

        Only the automatic path never asks: it resolves every coupled entity
        from one logical position and dispatches them together, so the blocker
        is always already on its way out.

        DECISION ONLY. The returned :class:`ExternalInterlockPlan` names two
        entities and two targets; the coordinator owns the side effects
        (manual-override marking, the stop, the two dispatches, the event-timeline
        rows) because it owns the command service and the managers. Behaviour and
        arithmetic stay here — the "managers hold state, policies hold behaviour"
        split.

        ``service`` is the ``cover.*`` service this target would be sent as and
        ``wire_target`` the position it implies in the DEVICE frame
        (``close_cover`` → 0, ``open_cover`` → 100, ``set_cover_position`` → its
        ``position``). HA's cover services are defined in that frame regardless
        of ACP's ``inverse_state``, which describes how ACP's own logical
        numbers map onto it — so a policy that compares against a live reading
        must convert both sides through the same frame rule it uses everywhere
        else (the #993 bug class).

        ``dispatch_token`` names WHICH dispatch produced ``wire_target``, in the
        same opaque form :meth:`capture_dispatch_token` mints and
        :meth:`await_dispatch_clearance` consumes. The user seam passes the very
        stamp its withheld dispatch was gated against, so the blocked test here
        and the gate's release test cannot resolve the number in different
        frames — they are the same inequality read from opposite ends, and a
        divergent frame inverts one of them (#993 / #1115). ``None`` is honest
        for an external command: nothing ACP resolved produced that number, so
        it falls back to the install's own frame.

        Liskov-safe default: ``None`` — "nothing blocks it", which is every
        cover type whose entities are physically independent.
        """
        return None

    def travel_calibration_clearance(
        self,
        subject: str,  # noqa: ARG002
        entities: Sequence[str],  # noqa: ARG002
        options: Mapping[str, Any] | None,  # noqa: ARG002
    ) -> dict[str, int]:
        """Where coupled entities must be parked before ``subject`` can sweep.

        The third member of the coupled-entity family, alongside
        :meth:`await_dispatch_clearance` ("may this move start yet?") and
        :meth:`plan_external_command_interlock` ("what would unblock this
        move?"). This one answers a question only travel-time calibration asks:
        a normal command moves a cover part-way, but calibration must drive it
        from one mechanical stop to the other and time the result — so anything
        physically in the way has to be moved out first, once, before the run's
        legs begin.

        Returns ``{entity_id: wire position}``. **Wire**, not logical: the
        calibrator hands these straight to
        ``CoverCommandService._execute_command``, whose contract is "already
        transformed". A policy that reasons in open-percent must apply
        :func:`axis_inverted` itself — the calibrator has no frame knowledge and
        must not acquire any.

        ``options`` is passed rather than read from any per-cycle cache the
        policy keeps. A calibration run can be started from the options flow
        before a single update cycle has primed those caches, and a stale prime
        would park the wrong entity — which, for stacked rails, means driving
        one into the other.

        Liskov-safe default: ``{}`` — nothing to move, which is every cover type
        whose entities are physically independent.
        """
        return {}

    async def after_position_command(
        self,
        cmd_svc,
        entity_id: str,
        *,
        service: str,
        position: int,
        context,
        reason: str,
    ) -> None:
        """Run any post-command work (default: no-op).

        Receives the actually-emitted ``service`` so policies can branch on
        which axis just fired (e.g. venetian only sequences after a position
        command, not after a direct tilt command).
        """
        return

    # ---- Axis routing -------------------------------------------------- #

    def axis_requirements(self) -> tuple[dict[str, Any], ...]:
        """Serialise each declared axis as a capability requirement (issue #972).

        One ``{"axis", "capability", "fallbacks"}`` dict per axis, projected off
        the existing ``axes`` / ``CoverAxis`` data. The diagnostics-triage engine
        (which is Home-Assistant-free and must never branch on the cover-type
        string) reads this to flag a cover whose entity lacks a required
        capability: the capability key and its OR-of-ANDs ``fallbacks`` travel as
        plain data, so no ``has_*`` literal or cover-type comparison leaks into
        ``diagnostics/triage.py``. A fifth cover type is covered automatically.
        """
        return tuple(
            {
                "axis": axis.name,
                "capability": axis.capability_key,
                "fallbacks": axis.drive_fallbacks,
            }
            for axis in self.axes
        )

    def select_default_axis(self, caps: Any) -> CoverAxis:
        """Pick the axis ``CoverCommandService`` should target for this entity.

        Built on top of ``should_use_tilt`` so the existing fallback rule —
        "an entity that only advertises set_tilt_position routes to tilt
        regardless of declared cover type" — is preserved bit-for-bit.

        ``caps=None`` happens when ``check_cover_features`` could not read the
        entity (HA hasn't initialised it yet, or it's unavailable). The legacy
        callers normalised that to an empty dict; doing the same here means
        callers don't have to guard at every call site.
        """
        primary = self.axes[0]
        is_tilt_default = primary.name == AXIS_NAME_TILT
        if should_use_tilt(is_tilt_default, caps if caps is not None else {}):
            # A tilt-primary policy returns its OWN axis object: it declares
            # ``TILT_AXIS_PRIMARY``, which matches the shared singleton on every
            # HA-facing field but carries primary-axis config semantics. Handing
            # back ``TILT_AXIS`` would give callers an axis that disagrees with
            # ``self.axes[0]`` about which option inverts it.
            return primary if is_tilt_default else TILT_AXIS
        return primary

    def tilt_capability_contradiction(self, caps: Any) -> bool:
        """Whether this cover type drives a tilt axis the device can't (#991, A3).

        True when the policy declares a tilt axis (``capability_key ==
        CAP_HAS_SET_TILT_POSITION``) that ``caps`` cannot drive. The tilt axis
        has no ``drive_fallbacks``, so ``is_drivable`` reduces to "native tilt
        present" — a tilt-declaring type (tilt / louvered_roof / venetian) bound
        to a cover lacking ``set_tilt_position`` returns ``True``; a
        position-only cover reached via ``open_cover`` / ``close_cover`` never
        declares a tilt axis, so it stays ``False`` (issue #991's out-of-scope
        carve-out). Single source of truth behind the cover-type boundary for
        the runtime A3 Repair, so the coordinator never branches on cover type
        or a hardcoded capability literal. Liskov-safe base default — no
        subclass override needed.
        """
        return any(
            a.capability_key == CAP_HAS_SET_TILT_POSITION and not a.is_drivable(caps)
            for a in self.axes
        )

    def supported_axes(self, caps: Any) -> tuple[CoverAxis, ...]:
        """Return the declared axes this entity's capabilities actually expose.

        Single source of truth (issue #725) for both the ``set_axes`` service's
        unsupported-axis rejection and the discovery descriptor's ``supported``
        flag. Filters ``self.axes`` by each axis's ``is_drivable`` check, which
        honours the open/close fallback so a position-only cover reached via
        ``open_cover`` / ``close_cover`` is not falsely rejected (#886) — no
        hardcoded ``caps.get("has_X")`` literal leaks out.
        """
        return tuple(a for a in self.axes if a.is_drivable(caps))

    def describe_axis(
        self,
        axis: CoverAxis,
        caps: Any = None,
        labels: dict[str, str] | None = None,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> AxisDescriptor:
        """Project one ``CoverAxis`` to a serialisable ``AxisDescriptor``.

        ``labels`` overlays translated ``axes.*`` strings on the English base;
        ``None`` keeps English (back-compat). ``supported`` reflects whether
        *caps* expose the axis. ``options`` are this entry's config options —
        the only thing that can answer ``inverted`` — and default to ``None``
        so the Liskov contract for a partial fifth-cover-type policy holds.
        """
        label = {**AXIS_LABELS_EN, **(labels or {})}.get(axis.label_key, axis.label_key)
        return AxisDescriptor(
            id=axis.name,
            label=label,
            label_key=axis.label_key,
            min=axis.value_min,
            max=axis.value_max,
            unit=axis.unit,
            capability_key=axis.capability_key,
            state_attr=axis.state_attr,
            service_attr=axis.service_attr,
            open_blocks_sun=axis.open_blocks_sun,
            supported=axis.is_drivable(caps),
            inverted=axis_inverted(axis, options),
        )

    def describe(
        self,
        caps: Any = None,
        labels: dict[str, str] | None = None,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> CoverDescriptor:
        """Assemble the self-discovery descriptor for this cover type (#725).

        Cover-type id + localized label + one ``AxisDescriptor`` per declared
        axis. Every axis appears (so a consumer sees the full axis set); the
        per-axis ``supported`` flag carries whether *caps* expose it. A ninth
        cover type inherits this unchanged — the discovery builder never
        branches on the cover-type string.
        """
        return CoverDescriptor(
            cover_type=self.cover_type,
            cover_label=self.display_label(labels),
            axes=tuple(
                self.describe_axis(a, caps, labels, options=options) for a in self.axes
            ),
        )

    def position_axis_supported(self, caps: Any) -> bool:
        """Whether *this entity* exposes the policy's primary (position) axis.

        Reads the capability flag named by ``axes[0].capability_key`` so the
        check stays behind the policy/axis abstraction — no hardcoded
        capability-key literal at the call site. Used by the
        solar floor gate (#569): a set-position-capable cover can be commanded
        to a true 0 % during sun tracking, so the 1 % open/close-only floor
        must not apply to it.

        ``caps=None`` (entity not yet readable) defaults to ``True`` — the
        instance-level rollup in the snapshot builder applies the conservative
        mixed-instance rule on top of this.
        """
        return _caps_get(caps, self.axes[0].capability_key, default=True)

    def position_for_intent(self, *, sun_through: bool) -> int:
        """Map a semantic intent to the numeric value for the primary axis.

        ``sun_through=True`` → "let sun reach the window" (winter heating).
        ``sun_through=False`` → "block sun" (summer cooling).

        Awning's "open=blocks-sun" semantic flips the answer compared to
        blind/tilt/venetian; the flip lives on ``axes[0].open_blocks_sun``
        rather than on the policy class itself.
        """
        primary = self.axes[0]
        if sun_through:
            return POSITION_CLOSED if primary.open_blocks_sun else POSITION_OPEN
        return POSITION_OPEN if primary.open_blocks_sun else POSITION_CLOSED

    def position_for_scene(self, scene: GroupScene) -> int:
        """Map a cover-group scene to this cover type's primary-axis position.

        Scenes are semantic intents resolved per member (issue #790):

          - ``ALL_OPEN`` / ``ALL_CLOSED`` follow HA cover semantics — 100 =
            open (blinds raised / awning extended), 0 = closed.
          - ``PRIVACY`` means maximum coverage, delegated to the existing
            ``position_for_intent(sun_through=False)`` polymorphism so the
            awning's open-blocks-sun axis flips the answer.

        Never called on axis-less virtual policies (group, building profile)
        — the group resolves scenes through each *member's* policy.
        """
        if scene is GroupScene.ALL_OPEN:
            return POSITION_OPEN
        if scene is GroupScene.ALL_CLOSED:
            return POSITION_CLOSED
        return self.position_for_intent(sun_through=False)

    def more_protective_position(
        self, a: int, b: int, *, cover: AdaptiveGeneralCover | None = None
    ) -> int:
        """Return whichever of two primary-axis positions blocks more sun.

        Polymorphic over cover type via ``axes[0].open_blocks_sun``:

          - ``open_blocks_sun=False`` (blind/tilt/venetian): lower % = more
            coverage → ``min``
          - ``open_blocks_sun=True`` (awning): higher % = more coverage → ``max``

        The anticipation helper (issue #616) folds the live solar target plus
        every valid future-window sample through this comparator so the
        commanded position protects against the most-shaded moment in the
        upcoming throttle interval.

        That axis-level answer is the whole story only where coverage is
        MONOTONIC in the percentage — the same assumption
        ``round_toward_coverage`` had to give up (issue #1090). A bi-directional
        slat closes at BOTH ends and is most open mid-travel, so ``min`` picks
        the LESS protective of two positions above the pivot: on MODE2, 55 % is
        99° (9° off horizontal) and 60 % is 108° (18° off) — ``min`` commands the
        one that lets more sun through. Passing *cover* lets the comparator ask
        the engine where coverage bottoms out and rank by DISTANCE from it
        instead (issue #1104).

        Percentage distance is the right metric because the percentage↔angle map
        is globally linear on every scale this hook sees (MODE1, MODE2, the
        louvered roof's ``max_slat_angle``, and the affine ``specify_angles``
        calibration), so ``|pct − pivot_pct|`` is proportional to
        ``|angle − 90°|`` on both sides at once — no per-side scaling needed.
        Linearity is also why this comparator needs no range check on the pivot:
        proportionality does not care whether the pivot is reachable, so a scale
        calibrated entirely to one side of horizontal (``max_slat_angle`` under
        90°, a one-sided ``specify_angles`` pair) is still ranked correctly — and
        an INVERTED such calibration, where ``axes[0]``'s static flag has the
        covering end backwards, is ranked correctly only this way. The
        coverage-step quantiser reaches the same conclusion from the same pivot
        by a different route (it clamps the pivot onto the reachable travel,
        because it anchors arithmetic on it rather than ordering by it), and the
        two must not disagree about which end of one engine covers.

        *cover* is keyword-only and defaults to ``None`` so callers with no
        engine in scope, and every monotonic axis, keep the exact behaviour they
        had. Equal distances fall through to the axis rule rather than being
        decided here, which keeps the symmetric straddle (``30``/``70`` on MODE2,
        both 36° off horizontal) answering ``30`` as it always has.
        """
        if cover is not None:
            pivot = cover.coverage_pivot_percentage()
            if pivot is not None:
                distance_a, distance_b = abs(a - pivot), abs(b - pivot)
                if distance_a != distance_b:
                    return a if distance_a > distance_b else b
        if self.axes[0].open_blocks_sun:
            return max(a, b)
        return min(a, b)

    def read_axis_value(
        self,
        hass: HomeAssistant,
        entity: str,
        caps: Any,
        *,
        state_obj: State | None = None,
        assumed: int | None = None,
    ) -> int | None:
        """Read the current value on the axis this policy targets by default.

        Single source of truth for the four call sites that historically did
        the same ``should_use_tilt → branch on attribute`` dance:
        ``CoverCommandService._read_position_with_capabilities``,
        ``CoverProvider.read_positions``, manual_override state-change
        handling, and the position-capability check inside ``_prepare_service_call``.

        ``assumed`` (issue #888) is a display-only fallback surfaced ONLY on the
        open/close-only branch. It wins in two cases: after the live open/close
        read yields ``None``, and — for an ``assumed_state`` cover (issue #888
        follow-up) — over the open/close mapping itself, because for such covers
        ``open``/``closed`` is the last-command direction, not a real position.
        On a non-assumed open/close cover a real open/closed read still wins and
        is never masked; a position-capable cover never reaches the fallback.
        Callers on the command-dispatch read path leave ``assumed=None`` so the
        gates stay raw (§3b) — only the reported-position surfaces pass it.
        """
        axis = self.select_default_axis(caps)
        if _caps_get(caps, axis.capability_key, default=True):
            if state_obj is not None:
                return state_obj.attributes.get(axis.state_attr)
            return state_attr(hass, entity, axis.state_attr)
        live = get_open_close_state(hass, entity, state_obj=state_obj)
        if assumed is not None:
            st = state_obj if state_obj is not None else hass.states.get(entity)
            if is_assumed_state(st):
                # Issue #888 follow-up: for an assumed-state cover, open/closed is
                # the last-command direction, not a real position. A recorded
                # high-confidence assumed value (a My arrival) is more specific, so
                # surface it over the open/close mapping. Invalidation (manual-override
                # transition + per-command refresh) keeps it from going stale.
                return assumed
        if live is None and assumed is not None:
            return assumed
        return live

    # ---- Declarative section configuration ----------------------------- #

    # Base CONF_* keys this cover type drops even though the common section
    # would otherwise include them (e.g. an oscillating awning disables the
    # fixed ``angle`` field because its angle is position-derived). Default
    # empty → inherit every common field.
    disabled_config_keys: ClassVar[frozenset[str]] = frozenset()

    def section_order(self, options: dict | None = None) -> tuple[str, ...]:
        """Ordered config sections this cover type supports.

        The base prepends the geometry section to the common order (every real
        cover type has geometry). Concrete policies override to insert extra
        sections — ``BlindPolicy`` adds glare zones. Used both for the options
        menu and to compute ``live_option_keys``.
        """
        from .. import config_fields

        return (config_fields.SECTION_GEOMETRY, *config_fields.COMMON_SECTION_ORDER)

    def extra_field_keys(self, section: str) -> tuple[str, ...]:  # noqa: ARG002
        """Type-specific CONF_* keys this policy adds to *section*.

        Beyond the common fields — e.g. the glare-zones enable toggle that
        ``BlindPolicy`` adds to sun tracking, or venetian's per-slot tilt
        fields. Default: none.
        """
        return ()

    def build_section_schema(
        self,
        name: str,
        hass: HomeAssistant | None = None,
        options: dict | None = None,
    ) -> vol.Schema:
        """Build the config-flow schema for one section, for this cover type.

        The single seam ``config_flow`` consumes: it dispatches to the right
        builder (static FieldSpec generation, the per-type geometry hook, or a
        dynamic sensor-unit/locale builder), appends this policy's extra fields,
        and removes any disabled keys.
        """
        from .. import config_dynamic as cd
        from .. import config_fields as cf

        opts = options or {}
        if name == cf.SECTION_GEOMETRY:
            # Per-window facing fields (azimuth / FOV / shaded distance) are shared
            # across every cover type, so they compose onto each policy's geometry
            # schema through the single ``window_facing_schema`` seam (#778) rather
            # than being duplicated into every ``geometry_schema``. This keeps them
            # in ``live_option_keys`` for all types. The FOV-from-measurements
            # button is NOT added here — it is a transient toggle layered on in
            # ``config_flow._get_geometry_schema`` only, never a persisted key.
            base = self.geometry_schema(hass, opts)
            base = base.extend(
                cd.window_facing_schema(
                    hass, include_distance=self.includes_shaded_distance()
                ).schema
            )
        elif name == cf.SECTION_SUN_TRACKING:
            base = cd.sun_tracking_schema(hass)
        elif name == cf.SECTION_BLIND_SPOT:
            base = cd.blind_spot_schema(opts)
        elif name == cf.SECTION_GLARE_ZONES:
            base = cd.glare_zones_schema(opts, hass)
        elif name == cf.SECTION_WEATHER_OVERRIDE:
            base = cd.weather_override_schema(hass, opts)
        elif name == cf.SECTION_LIGHT_CLOUD:
            base = cd.light_cloud_schema(hass, opts)
        elif name == cf.SECTION_TEMPERATURE_CLIMATE:
            base = cd.temperature_climate_schema(hass, opts)
        elif name == cf.SECTION_CUSTOM_POSITION:
            include_tilt = bool(self.extra_field_keys(name))
            base = cf.custom_position_schema(include_tilt=include_tilt)
        else:
            # Static section — generate markers straight from the registry.
            markers: dict = {}
            for key in cf.section_keys(name):
                spec = cf.FIELD_SPECS[key]
                if spec.make_selector is None:
                    continue
                marker, sel = spec.to_marker(hass, opts)
                markers[marker] = sel
            base = vol.Schema(markers)

        schema_dict: dict = dict(base.schema)
        present = {str(m) for m in schema_dict}
        for key in self.extra_field_keys(name):
            if key in present:
                continue
            spec = cf.FIELD_SPECS.get(key)
            if spec is None or spec.make_selector is None:
                continue
            marker, sel = spec.to_marker(hass, opts)
            schema_dict[marker] = sel
        if self.disabled_config_keys:
            schema_dict = {
                m: s
                for m, s in schema_dict.items()
                if str(m) not in self.disabled_config_keys
            }
        return vol.Schema(schema_dict)

    def live_option_keys(self) -> frozenset[str]:
        """Every CONF_* key valid for this cover type across its sections.

        The single seam ``options_service`` consumes to reject keys that don't
        belong to this cover type. Computed by rendering each supported section
        (``hass=None`` → keys only) and unioning the markers.
        """
        keys: set[str] = set()
        for section in self.section_order():
            keys.update(str(m) for m in self.build_section_schema(section).schema)
        return frozenset(keys - self.disabled_config_keys)

    # ---- Config-flow / options-service helpers ------------------------- #

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Return user-facing warnings about the bound covers' capabilities.

        Default: no warnings — vertical / awning / tilt logic still lives in
        ``helpers.check_cover_capabilities``. ``VenetianPolicy``
        overrides to express its dual-axis capability requirement.
        """
        return []

    def capability_warnings_for_options(
        self, known: dict[str, dict], options: dict
    ) -> list[str]:  # noqa: ARG002
        """Options-aware capability warnings for the bound covers.

        Additive extension of :meth:`cover_capability_warnings` for cover types
        whose capability requirement depends on a per-instance option (e.g. a
        day/night shade's control model relaxes the tilt requirement in its
        single-axis split-range mode). The Liskov-safe default delegates to
        :meth:`cover_capability_warnings`, so every other policy is unchanged —
        the single ``helpers.check_cover_capabilities`` call site can move
        to this hook without touching any existing behaviour.
        """
        return self.cover_capability_warnings(known)

    def glare_zones_config(self, config_service, options: dict) -> Any | None:
        """Return a ``GlareZonesConfig`` for this cover, or ``None``.

        Default ``None`` — only ``BlindPolicy`` reads its glare-zone config
        from options. Lets the coordinator populate the snapshot without
        branching on cover type.
        """
        return None

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,  # noqa: ARG002
        options: dict,  # noqa: ARG002
    ) -> float | None:
        """Travel range of the position axis in canonical metres, or ``None``.

        Returns ``None`` for cover types whose primary axis is not linear
        (tilt-only). The Target Position sensor multiplies this by the
        published position percentage to expose a physical-distance attribute
        alongside the existing percentage value.
        """
        return None

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """List ``(field_set, type_label)`` pairs that are invalid for this cover.

        ``options_service.validate_options_patch`` uses this to decide which
        cross-type geometry fields to reject. Default returns nothing — the
        caller must use this method to opt in (each registered policy
        implements it explicitly so we don't silently fail open).
        """
        return []

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Return the config-flow entity-selector filter for this cover type.

        Default: the plain ``cover`` domain with no capability requirement.
        Override only when the cover type needs to require a specific feature
        flag at selection time (e.g. ``TiltPolicy`` filters to tilt-capable
        entities).
        """
        return selector.EntityFilterSelectorConfig(domain="cover")

    def prospective_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Capability advice valid for EVERY configuration of this type.

        Asked before the type's own options exist — the "Change Cover Type"
        confirm step (#1132/#1135) evaluates the DESTINATION type while
        ``self.options`` still belong to the outgoing one, so an option-aware
        question (:meth:`capability_warnings_for_options`) cannot be asked yet
        (issue #1137). Must agree with :meth:`entities_satisfy_selector`: a
        type the picker admitted cannot be told on the next screen that its
        covers are unfit for it.

        The Liskov-safe default delegates to :meth:`cover_capability_warnings`,
        so every policy that has no per-instance option affecting its
        capability requirement is unchanged. ``DayNightShadePolicy`` overrides
        this to relax the tilt requirement, since its control model — the
        thing that decides whether tilt is required — is not chosen until the
        following geometry step.

        Unlike :meth:`entities_satisfy_selector`'s *known*, entries here must
        already be resolved capability dicts, not ``None`` — the delegate,
        :meth:`cover_capability_warnings`, has no unavailable-entity skip, and
        ``caps_get(None, key)`` silently reads as "missing every capability".
        The only caller (``helpers.check_cover_capabilities``) already filters
        ``None`` entries out before calling this hook.
        """
        return self.cover_capability_warnings(known)

    def entities_satisfy_selector(
        self, known: Mapping[str, Mapping[str, bool] | None]
    ) -> bool:
        """Report whether this policy's picker would admit every bound cover.

        The predicate form of :meth:`entity_selector_filter`, used by the
        "Change Cover Type" options step (issue #1132) to decide whether an
        instance's already-bound covers could switch to this type. Derived
        generically from the filter, so a new cover type answers it for free —
        there is no second capability matrix to keep in sync.

        *known* is the ``entity_id → capability dict`` map
        ``helpers.check_cover_features`` produces. A ``None`` entry (entity
        unavailable / not yet initialised) is **skipped, not failed**: the
        create-time picker could not have judged it either. An empty map is
        satisfied.

        HA's ``supported_features`` filter is OR-of-listed, so the predicate
        mirrors that — an entity satisfies the filter when it advertises at
        least one of the listed features.

        A feature name with no ``CAP_*`` counterpart is a bug, not a no-op: it
        would answer "satisfied" for a requirement nothing tested, offering the
        type to hardware that cannot drive it *and* dropping it from the
        explained blocked list. It fails the type shut and says so.
        """
        required: list[str] = []
        for feature in self.entity_selector_filter().get("supported_features") or ():
            cap = ENTITY_FILTER_FEATURE_CAPS.get(feature)
            if cap is None:
                _LOGGER.warning(
                    "Cover type %s filters its entity picker on %s, which "
                    "ENTITY_FILTER_FEATURE_CAPS does not map to a capability "
                    "flag; treating the type as ineligible",
                    self.cover_type,
                    feature,
                )
                return False
            required.append(cap)
        if not required:
            return True
        return all(
            any(caps_get(caps, cap) for cap in required)
            for caps in known.values()
            if caps is not None
        )

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,  # noqa: ARG002
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the config-flow geometry sub-schema for this cover type.

        Default: empty schema. Override to surface cover-type-specific
        geometry inputs (window dimensions, awning angle, slat depth, etc.).

        *hass* and *options* let subclasses adapt the schema to the user's
        configured unit system or to currently-stored values. The default
        ignores both — passing them is backward-compatible.
        """
        return vol.Schema({})

    def includes_shaded_distance(self) -> bool:
        """Whether the shared ``CONF_DISTANCE`` (shaded distance) field applies.

        The per-window shaded-distance field composes onto every geometry
        schema through ``window_facing_schema``. Cover types whose engine
        never reads it (the tilt-only louvered roof) override this to
        ``False`` so the inert marker is omitted from the form and from
        ``live_option_keys`` / the geometry unit-key set. Default ``True`` —
        every other type keeps the field.
        """
        return True

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Return option keys stored as canonical metres.

        Used by the config-flow step handlers to convert these keys between
        canonical (metres) and the user's display unit (m or in) on form
        load / submit. Default empty so cover types without length fields
        are no-ops.
        """
        return ()

    def geometry_slat_keys(self) -> tuple[str, ...]:
        """Return option keys stored as canonical centimetres.

        Used by the config-flow step handlers to convert these keys between
        canonical (centimetres) and the user's display unit (cm or in) on
        form load / submit. Default empty.
        """
        return ()

    def summary_geometry_lines(
        self,
        config: dict[str, Any],  # noqa: ARG002
        labels: dict[str, str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        """Return the user-facing geometry summary lines for the config flow.

        Default: no geometry summary. Override to render the
        cover-type-specific geometry block in ``_build_config_summary``.
        ``labels`` overlays translated ``geometry.*`` templates on the English
        base; ``None`` keeps English (back-compat).
        """
        return []

    def wiki_anchor(self) -> str:
        """Return the wiki page anchor for this cover type's geometry docs.

        ``config_flow._geometry_wiki_link`` composes the full URL by
        appending this fragment to the wiki base. Default is the generic
        cover-types overview — every concrete policy overrides to its own
        page. Replaces the legacy ``_GEOMETRY_WIKI_URL`` dict in
        ``config_flow.py`` that mapped ``CoverType`` literals to URLs.
        """
        return "Cover-Types"

    def display_label(
        self, labels: dict[str, str] | None = None
    ) -> str:  # noqa: ARG002
        """Return the human-readable label for this cover type.

        Used by ``config_flow._build_config_summary`` and any other UI
        surface that names the cover type. Default falls back to the
        ``cover_type`` slug for stub policies; every concrete policy
        overrides to its user-facing name. Replaces the legacy
        ``type_labels`` dict in ``config_flow.py``.

        ``labels`` is the translated ``cover_types.*`` bundle; the base
        default is only reached for unknown/stub types and has no key, so it
        ignores ``labels`` and returns the titlecased slug.
        """
        return self.cover_type.removeprefix("cover_").replace("_", " ").title()
