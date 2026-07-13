"""Reason-string i18n foundation (issue #882).

Pipeline handlers, the diagnostics builder, and the engine emit a stable
:class:`Reason` payload (``code`` + ``params``) instead of a hardcoded English
f-string. This module owns:

* ``_REASON_TEMPLATES_EN`` — the English ``str.format`` template for every
  :class:`~..const.ReasonCode`, byte-identical to the legacy f-string output.
* :func:`render` / :func:`render_en` — resolve a ``Reason`` (recursively
  rendering nested-fragment params and joining fragment sequences with
  ``", "``) into localized prose.
* :func:`reason_to_dict` — a JSON-safe nested ``{"code", "params"}`` payload
  the Lovelace card consumes to localize with its own templates.
* :func:`load_reason_labels` / :func:`async_prime` — overlay the shipped
  ``reason_i18n/<lang>.json`` bundle onto the English defaults (via the shared
  :mod:`.i18n_bundle` loader), cached for the coordinator to prime once.

Pure module: stdlib only, no ``homeassistant`` import (mirrors the engine's
0-HA-imports constraint). ``async_prime`` merely offloads the sync loader to a
passed-in hass executor; it imports nothing from Home Assistant.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from .const import ReasonCode
from .i18n_bundle import load_bundle_overlay, merge_labels

_LOGGER = logging.getLogger(__name__)

_REASON_I18N_DIR = Path(__file__).parent / "reason_i18n"


# ---------------------------------------------------------------------------
# Reason payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reason:
    """A stable reason code plus its interpolation parameters.

    ``params`` values may be plain scalars (``int``/``float``/``str``), a
    nested :class:`Reason` fragment (rendered inline), or a sequence of
    fragments (rendered and joined with ``", "``). Frozen + slotted; the
    ``params`` default normalizes to an empty mapping in ``__post_init__``
    (a frozen dataclass cannot use ``default_factory`` with ``slots``).
    """

    code: str
    params: Mapping[str, object] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Normalize a ``None`` params default to an empty mapping."""
        if self.params is None:
            object.__setattr__(self, "params", {})


# ---------------------------------------------------------------------------
# English templates — byte-identical to the legacy f-string output
# ---------------------------------------------------------------------------

_REASON_TEMPLATES_EN: dict[str, str] = {
    # -- fragments
    ReasonCode.FRAGMENT_SUNSET_POSITION: "sunset position",
    ReasonCode.FRAGMENT_DEFAULT_POSITION: "default position",
    ReasonCode.FRAGMENT_CLOUDY_POSITION: "cloudy position",
    ReasonCode.FRAGMENT_COVERAGE_STEP: " (coverage step, max {steps})",
    ReasonCode.FRAGMENT_Z_ADJUSTED: " (Z-adjusted)",
    ReasonCode.FRAGMENT_BYPASS_NOTE: " [bypasses automatic control]",
    ReasonCode.FRAGMENT_SEASON_EXTREME_HEAT: "extreme heat",
    ReasonCode.FRAGMENT_SEASON_TRACKING_OFF: "default: tracking off this season",
    ReasonCode.FRAGMENT_SEASON_SUMMER: "summer",
    ReasonCode.FRAGMENT_SEASON_WINTER: "winter",
    ReasonCode.FRAGMENT_SEASON_GLARE_LOW_LIGHT: "glare control (low light)",
    ReasonCode.FRAGMENT_SEASON_GLARE: "glare control",
    ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY: "weather not sunny",
    ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW: "lux below threshold",
    ReasonCode.FRAGMENT_TRIGGER_IRRADIANCE_BELOW: "irradiance below threshold",
    ReasonCode.FRAGMENT_TRIGGER_CLOUD_ABOVE: "cloud coverage above threshold",
    ReasonCode.FRAGMENT_TRIGGER_SMOOTHING_HOLD: "smoothing hold",
    ReasonCode.FRAGMENT_TRIGGER_TEMPLATE: "template",
    ReasonCode.FRAGMENT_TRIGGER_FALLBACK: "trigger",
    # -- solar
    ReasonCode.SOLAR_TRACKING: "sun within acceptance angle — position {position}%{suffix}",
    # -- manual override
    ReasonCode.MANUAL_HOLDING_SOLAR: (
        "manual override active — holding {held}% (solar would-be {position}%)"
    ),
    ReasonCode.MANUAL_SOLAR_ONLY: "manual override active — solar would-be {position}%",
    ReasonCode.MANUAL_HOLDING_LABEL: (
        "manual override active — holding {held}% ({pos_label} would be {position}%)"
    ),
    ReasonCode.MANUAL_LABEL_ONLY: "manual override active — {pos_label} {position}%",
    # -- occupancy / motion timeout
    ReasonCode.OCCUPANCY_HOLDING: (
        "occupancy timeout — holding position {held}% (sun within acceptance angle)"
    ),
    ReasonCode.OCCUPANCY_LABEL: "occupancy timeout active — {pos_label} {position}%",
    # -- climate
    ReasonCode.CLIMATE_ACTIVE: "climate mode active ({season}) — position {position}%",
    # -- glare zone
    ReasonCode.GLARE_PROTECTION: (
        "glare zone protection ({zones}) — "
        "effective distance {distance:.2f}m{z_suffix} → position {position}%"
    ),
    # -- cloud suppression
    ReasonCode.CLOUD_SUPPRESSION: (
        "cloud/low-light suppression — {triggers} → {pos_label} {position}%"
    ),
    # -- weather override
    ReasonCode.WEATHER_ACTIVE: "weather override active — position {position}%{bypass_note}",
    # -- custom position
    ReasonCode.CUSTOM_HEAD_NAMED: "{name} active",
    ReasonCode.CUSTOM_HEAD_SLOT: "custom position #{slot} active ({trigger})",
    ReasonCode.CUSTOM_USE_MY: "{head} — use My position ({position}%){bypass_note}",
    ReasonCode.CUSTOM_POSITION: "{head} — position {position}%{bypass_note}",
    # -- default handler
    ReasonCode.DEFAULT_SUNSET_USE_MY: "sunset position — use My position ({position}%)",
    ReasonCode.DEFAULT_NO_CONDITION: "no active condition — {pos_label} {position}%",
    # -- group handlers
    ReasonCode.GROUP_LOCK: "group lock from group {group_id} — holding {position}%",
    ReasonCode.GROUP_SCENE: "group scene '{scene}' from group {group_id} → {position}%",
    # -- registry composition
    ReasonCode.REGISTRY_OUTPRIORITIZED: "outprioritized by {handler}",
    ReasonCode.REGISTRY_FLOOR_RAISED: (
        "floor raised winner from {from_pos}% to {to_pos}% by {label}"
    ),
    ReasonCode.REGISTRY_FLOOR_INACTIVE: (
        "floor {floor_pos}% inactive (winner {winner_pos}% above floor)"
    ),
    ReasonCode.REGISTRY_TILT_APPLIED: (
        "tilt-only: slat angle fixed at {tilt}% by {label}; position driven by {handler}"
    ),
    ReasonCode.REGISTRY_TILT_DEFERRED: (
        "tilt-only {tilt}% deferred — {handler} already set tilt {winner_tilt}%"
    ),
    # -- diagnostics builder
    ReasonCode.BUILDER_UNKNOWN: "Unknown",
    ReasonCode.BUILDER_CONTROL_OCCUPANCY_TIMEOUT: "Occupancy Timeout",
    ReasonCode.BUILDER_CONTROL_MANUAL_OVERRIDE: "Manual Override",
    ReasonCode.BUILDER_CONTROL_TRACKING_OFF_SEASON: "Default: Tracking Off This Season",
    ReasonCode.BUILDER_CONTROL_TILT_FIXED: "{reason} — tilt fixed by Custom #{slot}",
    ReasonCode.BUILDER_OUTSIDE_WINDOW: (
        "Outside time window → {pos_label} {pos}% (commands paused)"
    ),
    ReasonCode.BUILDER_MANUAL_DIVERGENCE: (
        "manual override active — holding cover at {held}% (solar would be {raw}%)"
    ),
    ReasonCode.BUILDER_TILT_FIXED: "tilt fixed at {tilt}% by Custom #{slot}",
    ReasonCode.BUILDER_INTERPOLATED: "interpolated → {final}%",
    ReasonCode.BUILDER_INVERSED: "inversed → {final}%",
    # -- engine control_state_reason
    ReasonCode.ENGINE_DIRECT_SUN: "Direct Sun",
    ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET: "Default: Sunset Offset",
    ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT: "Default: Elevation Limit",
    ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT: "Default: Acceptance Angle Exit",
    ReasonCode.ENGINE_DEFAULT_BLIND_SPOT: "Default: Blind Spot",
    ReasonCode.ENGINE_DEFAULT: "Default",
    # -- describe_skip / inactive-reason prose
    ReasonCode.SKIP_OUTSIDE_WINDOW: "outside time window",
    ReasonCode.SKIP_SUN_OUTSIDE: "sun outside acceptance angle or elevation limits",
    ReasonCode.SKIP_MANUAL_NOT_ACTIVE: "manual override not active",
    ReasonCode.SKIP_OCCUPANCY_DISABLED: "occupancy detection disabled",
    ReasonCode.SKIP_OCCUPANCY_NOT_ACTIVE: "occupancy timeout not active",
    ReasonCode.SKIP_CLIMATE_MODE_OFF: "climate mode not enabled",
    ReasonCode.SKIP_CLIMATE_READINGS_UNAVAILABLE: "climate readings or options unavailable",
    ReasonCode.SKIP_CLIMATE_DEFERRED: "deferred glare-control to solar/glare handlers",
    ReasonCode.SKIP_NO_GLARE_ZONES: "no active glare zones or sun outside acceptance angle",
    ReasonCode.SKIP_CLOUD_SKIPPED: "cloud suppression skipped (sun outside acceptance angle)",
    ReasonCode.SKIP_CLOUD_INACTIVE: (
        "cloud suppression inactive (direct sun present or feature disabled)"
    ),
    ReasonCode.SKIP_WEATHER_NOT_ACTIVE: "weather override not active",
    ReasonCode.SKIP_CUSTOM_NOT_ACTIVE: "custom position #{slot} not active",
    ReasonCode.SKIP_ALWAYS_MATCHES: "always matches",
    ReasonCode.SKIP_GROUP_SCENE_NOT_LOCK: "group intent is a scene, not a lock",
    ReasonCode.SKIP_NO_GROUP_LOCK: "no group lock intent",
    ReasonCode.SKIP_GROUP_LOCK_NOT_SCENE: "group intent is a lock, not a scene",
    ReasonCode.SKIP_NO_GROUP_SCENE: "no group scene intent",
    ReasonCode.SKIP_NOT_ACTIVE: "not active",
}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render_value(value: object, labels: Mapping[str, str] | None = None) -> object:
    """Render a single param value, recursing into fragments.

    A :class:`Reason` renders to its (localized) prose; a non-str sequence of
    values renders each element and joins with ``", "``; every other value
    passes through unchanged so the parent template's format spec (e.g.
    ``{distance:.2f}``) still applies. ``labels`` of ``None`` renders in
    English (mirrors :func:`render`).
    """
    if isinstance(value, Reason):
        return render(value, labels)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return ", ".join(str(_render_value(item, labels)) for item in value)
    return value


def render(reason: Reason, labels: Mapping[str, str] | None = None) -> str:
    """Render ``reason`` using ``labels``, falling back to English per key.

    ``labels`` is an overlay (a translated bundle): a key it lacks falls back
    to :data:`_REASON_TEMPLATES_EN`. ``None`` renders entirely in English. An
    unknown code degrades gracefully to the code string itself.
    """
    active = labels if labels is not None else _REASON_TEMPLATES_EN
    template = active.get(reason.code) or _REASON_TEMPLATES_EN.get(reason.code)
    if template is None:
        return str(reason.code)
    rendered = {key: _render_value(val, labels) for key, val in reason.params.items()}
    try:
        return template.format(**rendered)
    except (KeyError, IndexError, ValueError) as exc:
        _LOGGER.debug("reason render fallback for %s: %r", reason.code, exc)
        return template


def render_en(reason: Reason) -> str:
    """Render ``reason`` with the English templates."""
    return render(reason)


def reason_to_dict(reason: Reason) -> dict[str, object]:
    """Return a JSON-safe nested ``{"code", "params"}`` payload for the card."""
    return {
        "code": reason.code,
        "params": {key: _param_to_json(val) for key, val in reason.params.items()},
    }


def _param_to_json(value: object) -> object:
    if isinstance(value, Reason):
        return reason_to_dict(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_param_to_json(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Bundle loading (delegates to the shared i18n_bundle loader)
# ---------------------------------------------------------------------------


@cache
def _reason_overlay(language: str) -> tuple[tuple[str, str], ...]:
    """Flattened ``reason_i18n/<language>.json`` overlay (cached, immutable)."""
    return tuple(load_bundle_overlay(_REASON_I18N_DIR, language).items())


def load_reason_labels(language: str) -> dict[str, str]:
    """Build the reason templates for ``language`` (English overlaid with the bundle)."""
    return merge_labels(_REASON_TEMPLATES_EN, dict(_reason_overlay(language)))


async def async_prime(hass: object, language: str) -> dict[str, str]:
    """Load + cache the reason templates for ``language`` off the event loop.

    The coordinator can call this once at setup to warm :func:`load_reason_labels`
    without a first-render file read on the loop. File I/O is offloaded to the
    executor; nothing from Home Assistant is imported here — only the passed-in
    ``hass`` executor hook is used.
    """
    return await hass.async_add_executor_job(load_reason_labels, language)
