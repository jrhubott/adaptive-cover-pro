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

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.const import (
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.helpers import selector

from ..const import ATTR_POSITION, ATTR_TILT_POSITION, POSITION_CLOSED, POSITION_OPEN
from ..helpers import get_open_close_state, should_use_tilt, state_attr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

    from ..engine.covers import AdaptiveGeneralCover
    from ..pipeline.types import PipelineResult
    from ..services.configuration_service import ConfigurationService


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
    """

    name: str
    service: str
    service_attr: str
    state_attr: str
    capability_key: str
    open_blocks_sun: bool


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
)

POSITION_AXIS_OPEN_BLOCKS_SUN = CoverAxis(
    name=AXIS_NAME_POSITION,
    service=SERVICE_SET_COVER_POSITION,
    service_attr=ATTR_POSITION,
    state_attr=STATE_ATTR_POSITION,
    capability_key=CAP_HAS_SET_POSITION,
    open_blocks_sun=True,
)

TILT_AXIS = CoverAxis(
    name=AXIS_NAME_TILT,
    service=SERVICE_SET_COVER_TILT_POSITION,
    service_attr=ATTR_TILT_POSITION,
    state_attr=STATE_ATTR_TILT_POSITION,
    capability_key=CAP_HAS_SET_TILT_POSITION,
    open_blocks_sun=False,
)


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

    # Whether the sun-tracking step exposes the two-mode FOV selector (#565):
    # "Angles" (manual fov_left/right sliders) vs "Measurements" (FOV derived
    # from window width + reveal depth). Only meaningful for vertical blinds —
    # awnings/tilt always use the plain fov sliders. BlindPolicy flips this on
    # and overrides ``fov_mode_schema`` to do the per-mode field shaping.
    supports_fov_mode: ClassVar[bool] = False

    def fov_mode_schema(
        self,
        base: vol.Schema,
        mode: str | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Shape the sun-tracking schema's FOV fields for the given mode.

        The default (every cover type except vertical blinds) returns *base*
        unchanged — no mode selector, plain fov_left/right sliders. BlindPolicy
        overrides this to insert the mode selector and hide the fov sliders in
        Measurements mode. Keeping the logic on the policy keeps cover-type
        branching inside ``cover_types/`` (cover-type-abstraction guideline).
        """
        return base

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

    def position_context_overrides(self, result: PipelineResult) -> dict[str, Any]:
        """Extra kwargs for ``PositionContext``. Default: empty."""
        return {}

    def secondary_axis_check(self, result: PipelineResult, cmd_svc) -> Any | None:
        """Return a manual-override secondary-axis check, or ``None``."""
        return None

    def attach(self, **kwargs: Any) -> None:
        """Bind late-resolved dependencies (cmd_svc, grace_mgr, …).

        Called by the coordinator after ``CoverCommandService`` is built.
        Policies that need a long-lived helper (e.g. ``VenetianPolicy``'s
        dual-axis sequencer) construct it here. Default: no-op.
        """
        return

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
        """Run any pre-command work before the position service fires.

        Default: no-op. ``VenetianPolicy`` overrides this to send tilt-first
        on opening transitions (issue #33) so the actuator's slats reach the
        target angle before the carriage starts moving.
        """
        return

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
            return TILT_AXIS
        return primary

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

    def read_axis_value(
        self,
        hass: HomeAssistant,
        entity: str,
        caps: Any,
        *,
        state_obj: State | None = None,
    ) -> int | None:
        """Read the current value on the axis this policy targets by default.

        Single source of truth for the four call sites that historically did
        the same ``should_use_tilt → branch on attribute`` dance:
        ``CoverCommandService._read_position_with_capabilities``,
        ``CoverProvider.read_positions``, manual_override state-change
        handling, and the position-capability check inside ``_prepare_service_call``.
        """
        axis = self.select_default_axis(caps)
        if _caps_get(caps, axis.capability_key, default=True):
            if state_obj is not None:
                return state_obj.attributes.get(axis.state_attr)
            return state_attr(hass, entity, axis.state_attr)
        return get_open_close_state(hass, entity, state_obj=state_obj)

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
            base = self.geometry_schema(hass, opts)
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
        ``config_flow._check_cover_capabilities``. ``VenetianPolicy``
        overrides to express its dual-axis capability requirement.
        """
        return []

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
