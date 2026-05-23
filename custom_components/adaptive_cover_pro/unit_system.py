"""Home Assistant unit-system helpers for the Adaptive Cover Pro config UI.

The integration stores every geometry value in canonical units (metres for
window/awning dimensions, centimetres for slat geometry, sensor-unit for
temperature / wind / rain thresholds). The calculation engine and providers
read those canonical values directly — none of them are touched by this
module.

What this module owns is the **display boundary**: the config-flow selectors
present values in the unit system the user has chosen for Home Assistant
itself. For US-customary users we deliberately display lengths and slat
dimensions in **inches**, never decimal feet — tape measures don't show
``5.25 ft``, they show ``5 ft 3 in`` (and ``63 in``), and the HA
``NumberSelector`` is a single-unit numeric field.

For sensor-driven thresholds (temperature, wind speed, rain rate, irradiance,
lux) the threshold is **interpreted in the sensor's own unit**, not in HA's
locale unit. :func:`sensor_unit_label` reads the configured sensor entity's
``unit_of_measurement`` and falls back to HA's configured unit when no
sensor is set yet.

Step-handler integration pattern
--------------------------------
A config-flow step that owns length / slat fields wraps its data flow with
two helpers from this module:

* On form load (options flow): pre-fill via
  ``add_suggested_values_to_schema(schema, options_to_display(hass, options,
  length_keys=(...), slat_keys=(...)))`` — converts stored canonical values
  into display units so the user sees the right number.
* On submit: ``user_input = user_input_to_canonical(hass, user_input,
  length_keys=(...), slat_keys=(...))`` before persisting — converts the
  user-entered display value back to canonical metres/cm.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfLength
from homeassistant.helpers import selector
from homeassistant.util.unit_conversion import DistanceConverter
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# --- Mode detection -------------------------------------------------------- #


def is_imperial(hass: HomeAssistant | None) -> bool:
    """Return True when HA is configured for US-customary units.

    ``hass=None`` returns False — that's the path tests and module-level
    schema constants use, so the schema falls back to metric labels.
    """
    if hass is None:
        return False
    return hass.config.units is US_CUSTOMARY_SYSTEM


# --- Display-unit labels --------------------------------------------------- #


def length_display_unit(hass: HomeAssistant | None) -> str:
    """Return ``"m"`` (metric) or ``"in"`` (imperial).

    Inches, not feet — see the module docstring.
    """
    return "in" if is_imperial(hass) else "m"


def slat_display_unit(hass: HomeAssistant | None) -> str:
    """Return ``"cm"`` (metric) or ``"in"`` (imperial)."""
    return "in" if is_imperial(hass) else "cm"


# --- Value conversions ----------------------------------------------------- #


def to_display_length(value_m: float, hass: HomeAssistant | None) -> float:
    """Convert canonical metres → user's display unit (m or in)."""
    if not is_imperial(hass):
        return value_m
    return DistanceConverter.convert(value_m, UnitOfLength.METERS, UnitOfLength.INCHES)


def from_display_length(value: float, hass: HomeAssistant | None) -> float:
    """Convert display-unit value (m or in) → canonical metres."""
    if not is_imperial(hass):
        return value
    return DistanceConverter.convert(value, UnitOfLength.INCHES, UnitOfLength.METERS)


def to_display_slat(value_cm: float, hass: HomeAssistant | None) -> float:
    """Convert canonical centimetres → user's display unit (cm or in)."""
    if not is_imperial(hass):
        return value_cm
    return DistanceConverter.convert(
        value_cm, UnitOfLength.CENTIMETERS, UnitOfLength.INCHES
    )


def from_display_slat(value: float, hass: HomeAssistant | None) -> float:
    """Convert display-unit slat value (cm or in) → canonical centimetres."""
    if not is_imperial(hass):
        return value
    return DistanceConverter.convert(
        value, UnitOfLength.INCHES, UnitOfLength.CENTIMETERS
    )


# --- Sensor-driven labels -------------------------------------------------- #


def sensor_unit_label(
    hass: HomeAssistant | None, entity_id: str | None, fallback_unit: str
) -> str:
    """Return the unit string to display next to a sensor-driven threshold.

    The threshold value is interpreted in the **sensor's** unit, never
    Home Assistant's locale unit, so the label must reflect the sensor.
    When no sensor is configured (or its state is unavailable) the
    selector falls back to *fallback_unit* — usually
    ``hass.config.units.<unit>`` — so the field is still labelled.
    """
    if hass is None or not entity_id:
        return fallback_unit
    state = hass.states.get(entity_id)
    if state is None:
        return fallback_unit
    unit = state.attributes.get("unit_of_measurement")
    if not unit:
        return fallback_unit
    return str(unit)


# --- Rounding helpers ------------------------------------------------------ #


def _round_step_down(value: float, step: float) -> float:
    """Round *value* DOWN to the nearest multiple of *step* (toward −∞)."""
    if step <= 0:
        return value
    n = value / step
    floored = int(n) if n >= 0 or n == int(n) else int(n) - 1
    return floored * step


def _round_step_up(value: float, step: float) -> float:
    """Round *value* UP to the nearest multiple of *step* (toward +∞)."""
    if step <= 0:
        return value
    n = value / step
    if n == int(n):
        return n * step
    ceiled = int(n) + 1 if n >= 0 else int(n)
    return ceiled * step


def _round_to_step(value: float, step: float) -> float:
    """Round *value* to the nearest multiple of *step*."""
    if step <= 0:
        return value
    return round(value / step) * step


# --- Selector factories ---------------------------------------------------- #


def length_selector(
    hass: HomeAssistant | None,
    *,
    min_m: float,
    max_m: float,
    metric_step: float = 0.01,
    imperial_step: float = 0.5,
    mode: selector.NumberSelectorMode = selector.NumberSelectorMode.BOX,
) -> selector.NumberSelector:
    """Build a metres-stored, locale-aware ``NumberSelector``.

    *min_m* / *max_m* / *metric_step* are canonical metres. When HA is on
    US-customary the helper converts those bounds to inches, rounds outward
    so existing canonical extremes are still selectable, and substitutes
    *imperial_step* for an inch-friendly tick size.
    """
    if is_imperial(hass):
        min_in = _round_step_down(to_display_length(min_m, hass), imperial_step)
        max_in = _round_step_up(to_display_length(max_m, hass), imperial_step)
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=round(min_in, 4),
                max=round(max_in, 4),
                step=imperial_step,
                mode=mode,
                unit_of_measurement="in",
            )
        )
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_m,
            max=max_m,
            step=metric_step,
            mode=mode,
            unit_of_measurement="m",
        )
    )


def slat_selector(
    hass: HomeAssistant | None,
    *,
    min_cm: float,
    max_cm: float,
    metric_step: float = 0.1,
    imperial_step: float = 0.05,
    mode: selector.NumberSelectorMode = selector.NumberSelectorMode.SLIDER,
) -> selector.NumberSelector:
    """Build a centimetres-stored, locale-aware ``NumberSelector``."""
    if is_imperial(hass):
        min_in = _round_step_down(to_display_slat(min_cm, hass), imperial_step)
        max_in = _round_step_up(to_display_slat(max_cm, hass), imperial_step)
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=round(min_in, 4),
                max=round(max_in, 4),
                step=imperial_step,
                mode=mode,
                unit_of_measurement="in",
            )
        )
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_cm,
            max=max_cm,
            step=metric_step,
            mode=mode,
            unit_of_measurement="cm",
        )
    )


def length_default(
    canonical_m: float, hass: HomeAssistant | None, *, imperial_step: float = 0.5
) -> float:
    """Return a schema ``default=`` value in the locale's display unit."""
    if not is_imperial(hass):
        return canonical_m
    return round(_round_to_step(to_display_length(canonical_m, hass), imperial_step), 4)


def slat_default(
    canonical_cm: float, hass: HomeAssistant | None, *, imperial_step: float = 0.05
) -> float:
    """Return a schema ``default=`` value in the locale's display unit."""
    if not is_imperial(hass):
        return canonical_cm
    return round(_round_to_step(to_display_slat(canonical_cm, hass), imperial_step), 4)


# --- Dict-level conversion (step handlers) -------------------------------- #


def options_to_display(
    hass: HomeAssistant | None,
    options: Mapping[str, Any],
    *,
    length_keys: Iterable[str] = (),
    slat_keys: Iterable[str] = (),
    display_precision: int = 1,
) -> dict[str, Any]:
    """Return a copy of *options* with length/slat keys converted to display units.

    Use before passing options to ``add_suggested_values_to_schema`` so the
    user sees imperial values when on imperial. Rounds to *display_precision*
    decimal places in display units to keep pre-fill values readable
    (``2.1 m → 82.7 in``, not ``2.1 m → 82.6771653543307 in``).
    """
    out: dict[str, Any] = dict(options)
    if not is_imperial(hass):
        return out
    for k in length_keys:
        if k in out and out[k] is not None:
            out[k] = round(to_display_length(float(out[k]), hass), display_precision)
    for k in slat_keys:
        if k in out and out[k] is not None:
            out[k] = round(to_display_slat(float(out[k]), hass), display_precision)
    return out


def user_input_to_canonical(
    hass: HomeAssistant | None,
    user_input: Mapping[str, Any],
    *,
    length_keys: Iterable[str] = (),
    slat_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a copy of *user_input* with length/slat keys converted to canonical.

    Call from the step handler before ``self.config.update(...)`` /
    ``self.options.update(...)``. No rounding — the canonical value is
    whatever ``DistanceConverter`` produces, so a metric user editing an
    imperial-entered value sees the original metres back, modulo float
    noise.
    """
    out: dict[str, Any] = dict(user_input)
    if not is_imperial(hass):
        return out
    for k in length_keys:
        if k in out and out[k] is not None:
            out[k] = from_display_length(float(out[k]), hass)
    for k in slat_keys:
        if k in out and out[k] is not None:
            out[k] = from_display_slat(float(out[k]), hass)
    return out
