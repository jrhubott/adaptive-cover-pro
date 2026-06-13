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
"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template, result_as_boolean

from .config_fields import TEMPLATABLE_KEYS

_LOGGER = logging.getLogger(__name__)


def render_condition(
    hass: HomeAssistant, template_str, *, default: bool = False
) -> bool:
    """Render an optional Jinja2 *condition* template to a boolean.

    The reusable primitive for optional "extra condition" template fields — a
    template that answers a yes/no question (issue #577 follow-up). Returns
    *default* when the value is empty / not a template, or when rendering fails.
    HA's :func:`result_as_boolean` decides truthiness (``"on"``/``"true"``/``1``
    → True), matching how conditions read elsewhere in Home Assistant.
    """
    if not is_template_string(template_str):
        return default
    try:
        result = Template(template_str, hass).async_render()
    except TemplateError as err:
        _LOGGER.debug("Condition template %r failed to render: %s", template_str, err)
        return default
    return result_as_boolean(result)


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

    def __init__(self, hass: HomeAssistant) -> None:
        """Store *hass* for template rendering."""
        self._hass = hass
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
            result = Template(value, self._hass).async_render(parse_result=False)
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
