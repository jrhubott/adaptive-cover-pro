"""Runtime resolution of templated config options (issue #577).

Two flavours of optional template field share this module:

* **Numeric thresholds** (:data:`config_fields.TEMPLATABLE_KEYS`) — a template
  that renders to a *number*. :class:`TemplateResolver` renders these once per
  coordinator cycle so the pure engine and ``RuntimeConfig`` never see a raw
  template string.
* **Boolean conditions** — an optional template that renders to a *truthy/falsy*
  value, used as an extra "is this condition active?" signal (e.g. the motion
  occupancy template). :func:`render_condition` is the reusable primitive; it is
  the baseline pattern for adding condition-template fields to other screens.

Rendering failures never propagate: a numeric failure drops the key (field falls
back to its default); a condition failure returns the supplied default.

Both flavours render against the per-instance ``acp`` namespace (issue #1159)
built by :func:`build_acp_template_variables` — see that function for the
contract.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template, result_as_boolean
from jinja2.exceptions import UndefinedError

from .config_fields import TEMPLATABLE_KEYS

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The ``acp`` self-reference namespace (issue #1159)
# ---------------------------------------------------------------------------

#: Canonical namespace key → ``(entity domain, registry translation_key)``.
#:
#: The resolver keys on ``RegistryEntry.translation_key`` and NEVER on
#: ``unique_id``: unique_id is not uniform across ACP's platforms (switches and
#: one button derive theirs from the English display name, spaces and all)
#: whereas every platform sets ``_attr_translation_key`` to its key. Canonical
#: key and translation_key are therefore the same token today; the pair is kept
#: explicit so the map states the full registry coordinate it matches on.
#:
#: The map is uniform across cover types on purpose. Which of these entities a
#: given instance actually publishes is decided by the platform gates (glare
#: zones, climate mode, motion sensors …); the resolver answers that question by
#: looking the row up in the registry, never by branching on the cover type.
ACP_TEMPLATE_ENTITY_KEYS: dict[str, tuple[str, str]] = {
    # binary_sensor
    "sun_motion": ("binary_sensor", "sun_motion"),
    "manual_override": ("binary_sensor", "manual_override"),
    "glare_active": ("binary_sensor", "glare_active"),
    "position_mismatch": ("binary_sensor", "position_mismatch"),
    # switch
    "enabled_toggle": ("switch", "enabled_toggle"),
    "automatic_control": ("switch", "automatic_control"),
    "sun_tracking": ("switch", "sun_tracking"),
    "manual_toggle": ("switch", "manual_toggle"),
    "return_to_default_toggle": ("switch", "return_to_default_toggle"),
    "motion_control": ("switch", "motion_control"),
    "switch_mode": ("switch", "switch_mode"),
    "temp_toggle": ("switch", "temp_toggle"),
    "lux_toggle": ("switch", "lux_toggle"),
    "irradiance_toggle": ("switch", "irradiance_toggle"),
    "glare_zone_0": ("switch", "glare_zone_0"),
    "glare_zone_1": ("switch", "glare_zone_1"),
    "glare_zone_2": ("switch", "glare_zone_2"),
    "glare_zone_3": ("switch", "glare_zone_3"),
    # sensor — discrete/enum only. Continuously-varying sensors (target
    # position, solar calculation, sun position, forecasts) are deliberately
    # excluded: a template reading one would re-render on every cycle and, via
    # the tracked-template refresh, drive its own input.
    "control_status": ("sensor", "control_status"),
    "motion_status": ("sensor", "motion_status"),
    "climate_status": ("sensor", "climate_status"),
}

#: Display-slug → canonical key. The ONLY place the "what the user sees in the
#: entity_id" / "what the code calls it" mismatch is encoded — e.g. the Sun
#: Infront binary sensor's internal key is ``sun_motion``, and the Occupancy
#: Status sensor's is ``motion_status``.
ACP_TEMPLATE_KEY_ALIASES: dict[str, str] = {
    "sun_infront": "sun_motion",
    "occupancy_status": "motion_status",
    "integration_enabled": "enabled_toggle",
    "manual_override_detection": "manual_toggle",
    "return_to_default_when_disabled": "return_to_default_toggle",
    "climate_mode": "switch_mode",
    "outside_temperature": "temp_toggle",
    "lux": "lux_toggle",
    "irradiance": "irradiance_toggle",
}

_ACP_NAMESPACE_RE = re.compile(r"\bacp(?:_entity|_state)?\b")


def uses_acp_namespace(template_str) -> bool:
    """Return True when *template_str* references the ``acp`` namespace.

    Selects which tracked templates get the refresh coalescer in
    ``__init__._coalesce_namespace_refreshes`` — a self-reference is the one
    shape that can drive its own input, so it is the one shape whose refreshes
    need bounding. Everything else keeps its untouched immediacy.

    Deliberately a cheap textual test rather than a Jinja parse: a false
    positive costs at most a one-second trailing refresh on a template that
    never needed it, and a false negative is what we must not have.
    """
    return isinstance(template_str, str) and bool(
        _ACP_NAMESPACE_RE.search(template_str)
    )


class _AcpNamespaceBase:
    """Attribute/item access facade over a single ``_lookup`` implementation.

    ``acp.sun_infront`` and ``acp['sun_infront']`` are the same lookup, so the
    access plumbing lives here once and each concrete namespace supplies only
    what a resolved key yields.
    """

    def _lookup(self, key: str) -> str:
        # Abstract: every concrete namespace overrides it, so this never runs.
        raise NotImplementedError  # pragma: no cover

    def __getattr__(self, name: str) -> str:
        # Private / dunder probes (the Jinja sandbox's ``unsafe_callable`` and
        # ``alters_data`` checks, copy, pickle) must read as ordinary missing
        # attributes — not as a bad template key.
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lookup(name)

    def __getitem__(self, key: str) -> str:
        return self._lookup(key)


class _AcpEntityNamespace(_AcpNamespaceBase):
    """Resolve one config entry's own entity_ids by canonical namespace key.

    Every access does a fresh entity-registry lookup — nothing is cached — so a
    rename, or an entity that only appears once its platform gate opens, is
    picked up on the next render with no reload.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind the namespace to *hass* and the owning config entry."""
        self._hass = hass
        self._entry_id = entry_id

    def resolve(self, key: str) -> str:
        """Return the entity_id this instance publishes for *key*.

        Raises :exc:`jinja2.exceptions.UndefinedError` — a ``TemplateError``
        subclass, so HA converts it and the caller's existing fail-soft contract
        applies — when *key* is unknown, or when this instance publishes no such
        entity (a gated feature that is switched off).
        """
        canonical = ACP_TEMPLATE_KEY_ALIASES.get(key, key)
        spec = ACP_TEMPLATE_ENTITY_KEYS.get(canonical)
        if spec is None:
            raise UndefinedError(
                f"'{key}' is not an Adaptive Cover Pro entity key "
                f"(instance {self._instance_label()})"
            )
        domain, translation_key = spec
        registry = er.async_get(self._hass)
        for entry in er.async_entries_for_config_entry(registry, self._entry_id):
            if entry.domain == domain and entry.translation_key == translation_key:
                return entry.entity_id
        raise UndefinedError(
            f"Adaptive Cover Pro instance {self._instance_label()} publishes no "
            f"{domain} entity for '{key}'"
        )

    def _lookup(self, key: str) -> str:
        return self.resolve(key)

    def _instance_label(self) -> str:
        """Name the instance in an error message, falling back to its entry id."""
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry is not None else self._entry_id


class _AcpStateNamespace(_AcpNamespaceBase):
    """Read the *state* of one config entry's own entities by namespace key.

    Composed on :class:`_AcpEntityNamespace` so there is exactly one resolve
    path. Only ever handed to untracked numeric renders — see
    :func:`build_acp_template_variables`.
    """

    def __init__(self, hass: HomeAssistant, entities: _AcpEntityNamespace) -> None:
        """Bind to *hass* and the entity namespace that does the resolving."""
        self._hass = hass
        self._entities = entities

    def _lookup(self, key: str) -> str:
        state = self._hass.states.get(self._entities.resolve(key))
        return STATE_UNKNOWN if state is None else state.state


def build_acp_template_variables(
    hass: HomeAssistant, entry_id: str, *, include_state: bool = False
) -> dict[str, Any]:
    """Build the ``acp`` render context for one config entry (issue #1159).

    The single factory behind every render site and every ``TrackTemplate``, so
    the tracker's render and the manager's cycle render can never disagree.

    Always returns the two *entity_id* forms:

    * ``acp.<key>`` — the entity_id string, e.g.
      ``{{ is_state(acp.sun_infront, 'on') }}``
    * ``acp_entity('<key>')`` — the same resolve, spelled as a call

    ``acp_state.<key>`` (the *value*) is added only when *include_state* is set,
    and is set only for the untracked numeric threshold renders. A value read is
    invisible to ``RenderInfo``, so a *tracked* template written against
    ``acp_state`` would register zero state listeners and silently lose the
    sensor-grade immediacy that ``_register_template_tracker`` exists to provide
    (#577/#563/#639/#632/#974). Withholding it turns that silent regression into
    a visible render failure. Uniform rule: condition fields get the entity
    forms, numeric fields get all three.
    """
    entities = _AcpEntityNamespace(hass, entry_id)
    variables: dict[str, Any] = {"acp": entities, "acp_entity": entities.resolve}
    if include_state:
        variables["acp_state"] = _AcpStateNamespace(hass, entities)
    return variables


def render_condition(
    hass: HomeAssistant,
    template_str,
    *,
    default: bool = False,
    variables: Mapping[str, Any] | None = None,
) -> bool:
    """Render an optional Jinja2 *condition* template to a boolean.

    The reusable primitive for optional "extra condition" template fields — a
    template that answers a yes/no question (issue #577 follow-up). Returns
    *default* when the value is empty / not a template, or when rendering fails.
    HA's :func:`result_as_boolean` decides truthiness (``"on"``/``"true"``/``1``
    → True), matching how conditions read elsewhere in Home Assistant.

    *variables* is the extra render context — in practice the ``acp``
    self-reference namespace from :func:`build_acp_template_variables` (#1159).
    An unresolvable namespace key raises ``UndefinedError``, a ``TemplateError``
    subclass, so it lands on the same fail-soft path as any other bad template.
    """
    if not is_template_string(template_str):
        return default
    try:
        result = Template(template_str, hass).async_render(variables)
    except TemplateError as err:
        _LOGGER.debug("Condition template %r failed to render: %s", template_str, err)
        return default
    return result_as_boolean(result)


def render_condition_or_none(
    hass: HomeAssistant,
    template_str,
    *,
    variables: Mapping[str, Any] | None = None,
) -> bool | None:
    """Render an optional condition template to a tri-state boolean-or-None.

    The "no opinion" counterpart to :func:`render_condition`: returns ``None``
    when *template_str* is empty, not a template, or fails to render, and the
    rendered boolean otherwise. Callers that must fall through to a different
    source when a template is silent (is_sunny / presence / weather-override
    condition templates, issue #639) use this instead of forcing a default.

    Implemented on top of :func:`render_condition` (no separate Jinja eval):
    a failing render returns whichever default it is given, so rendering with
    both defaults and comparing detects the failure without duplicating the
    rendering logic.
    """
    if not is_template_string(template_str):
        return None
    rendered = render_condition(hass, template_str, default=False, variables=variables)
    if rendered != render_condition(
        hass, template_str, default=True, variables=variables
    ):
        return None  # unstable across defaults → render failed → no opinion
    return rendered


def combine_with_mode(
    template_truthy: bool,
    others_truthy: bool,
    mode: str,
    *,
    has_template: bool,
    has_others: bool,
) -> bool:
    """Combine a condition template's result with the screen's other conditions.

    The reusable counterpart to :func:`render_condition`: once a field renders
    to a bool, this decides how it folds into the rest of that screen's signal.
    ``mode`` is a :class:`~const.TemplateCombineMode` value.

    * ``"and"`` *only* when both a template and other conditions are present →
      ``template_truthy and others_truthy`` (the template gates the others).
    * Everything else (``"or"``, an unknown value, or only one source present) →
      ``template_truthy or others_truthy``. With a single source the absent
      operand is falsy, so OR collapses to that source — which is also why
      ``AND`` degenerates to the lone source rather than being stuck false.

    ``mode`` is taken as a plain string so this module needs no enum import;
    callers pass the enum's value (``StrEnum`` compares equal to its value).
    """
    if has_template and has_others and mode == "and":
        return template_truthy and others_truthy
    return template_truthy or others_truthy


def fold_condition_template(
    hass: HomeAssistant,
    template_str,
    mode: str,
    *,
    others_truthy: bool,
    has_others: bool,
    variables: Mapping[str, Any] | None = None,
) -> bool | None:
    """Fold an optional condition template with a screen's other source.

    The single-source combine used by the boolean condition-template fields
    that pair a Jinja template with a companion entity/sensor and must fall
    through to a separate fallback when neither source is authoritative
    (is_sunny / presence in the climate provider, is-raining / is-windy in the
    weather manager — issue #639). Wraps the shared trio
    (:func:`render_condition_or_none` + :func:`combine_with_mode`) so callers
    never re-implement the tri-state eval:

    * ``has_others`` gates ``others_truthy`` — a fail-open default (e.g.
      ``is_entity_active(None)``) can't leak in as a phantom True.
    * A template that is empty / not Jinja / fails to render gives "no
      opinion": the result reduces to the other source, or — when there is no
      other source either — to ``None`` so the caller uses its own fallback.
    """
    others = others_truthy if has_others else False
    template_opinion = render_condition_or_none(hass, template_str, variables=variables)
    if template_opinion is None:
        return others if has_others else None
    return combine_with_mode(
        template_opinion,
        others,
        mode,
        has_template=True,
        has_others=has_others,
    )


def is_template_string(value) -> bool:
    """Return True if *value* is a string carrying Jinja2 template markup.

    Stricter than :func:`_looks_templated`: a plain numeric string like
    ``"1000"`` is *not* a template. Shared by the service validators and the
    diagnostics builder so "is this actually a template?" is decided in one
    place.
    """
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def _looks_templated(value) -> bool:
    """Return True if *value* is a string that needs rendering.

    Any string is a candidate: a plain numeric string (``"1000"``) renders to
    itself, and a Jinja string (``"{{ ... }}"``) renders to its result. Numeric
    values stored by the legacy ``NumberSelector`` are ``int``/``float`` and are
    passed through untouched.
    """
    return isinstance(value, str)


class TemplateResolver:
    """Render templated threshold options to numbers, once per cycle."""

    def __init__(
        self, hass: HomeAssistant, variables: Mapping[str, Any] | None = None
    ) -> None:
        """Store *hass* and the shared render context for template rendering.

        *variables* is the ``acp`` self-reference namespace (#1159). These
        renders are untracked and happen once per coordinator cycle, so they are
        the one flavour that also gets the ``acp_state`` value form.
        """
        self._hass = hass
        self._variables = variables
        # Keys currently in a failed-render state — used to log each failure
        # transition once instead of every cycle.
        self._failed: set[str] = set()

    def resolve(self, options: dict) -> dict:
        """Return *options* with templatable string values rendered to floats.

        Fast path: when no templatable key holds a string, return *options*
        unchanged (no copy). Otherwise return a shallow copy with each rendered
        key replaced by its float result, or stripped if rendering failed.
        """
        if not any(_looks_templated(options.get(key)) for key in TEMPLATABLE_KEYS):
            self._failed.clear()
            return options

        resolved = dict(options)
        for key in TEMPLATABLE_KEYS:
            value = resolved.get(key)
            if not _looks_templated(value):
                continue
            rendered = self._render(key, value)
            if rendered is None:
                # Drop so the consumer falls back to the field default.
                resolved.pop(key, None)
            else:
                resolved[key] = rendered
        return resolved

    def _render(self, key: str, value: str) -> float | None:
        """Render *value* to a float, or None on failure."""
        try:
            result = Template(value, self._hass).async_render(
                self._variables, parse_result=False
            )
            number = float(str(result).strip())
        except (TemplateError, ValueError, TypeError) as err:
            if key not in self._failed:
                self._failed.add(key)
                _LOGGER.warning(
                    "Template for %s failed to render to a number (%r): %s; "
                    "falling back to default",
                    key,
                    value,
                    err,
                )
            return None
        self._failed.discard(key)
        return number
