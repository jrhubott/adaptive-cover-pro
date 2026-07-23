"""Troubleshoot-finding i18n bundle (issue #970, Phase 1).

The diagnostics-triage engine (:mod:`.diagnostics.triage`) emits a stable
:class:`~.reason_i18n.Reason` payload (``code`` + ``params``) for every finding.
This module owns:

* ``_TRIAGE_TEMPLATES_EN`` — the English ``str.format`` template for every
  :class:`~.const.TriageCode`. Rule 1's and rule 8a's templates are byte-identical
  to the legacy config-summary strings they migrate off of.
* :func:`load_troubleshoot_labels` / :func:`async_prime` — overlay the shipped
  ``troubleshoot_i18n/<lang>.json`` bundle onto the English defaults (via the
  shared :mod:`.i18n_bundle` loader), cached for the coordinator to prime once.

A :class:`~.diagnostics.triage.Finding` renders through
:func:`.reason_i18n.render` against these labels — one renderer, no second one.

Pure module: stdlib only, no ``homeassistant`` import (mirrors ``reason_i18n``
and the engine's 0-HA-imports constraint). ``async_prime`` merely offloads the
sync loader to a passed-in hass executor; it imports nothing from Home Assistant.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from .const import TriageCode
from .i18n_bundle import load_bundle_overlay, merge_labels

_TROUBLESHOOT_I18N_DIR = Path(__file__).parent / "troubleshoot_i18n"


# ---------------------------------------------------------------------------
# English templates (one str.format template per TriageCode)
# ---------------------------------------------------------------------------

_TRIAGE_TEMPLATES_EN: dict[str, str] = {
    # Rule 1 — byte-identical to summary_i18n/en.json "warnings.custom_safety_bypass".
    TriageCode.CUSTOM_SAFETY_BYPASS: (
        "⚠️ Custom #{slot} is at safety priority ({safety}) — it bypasses the "
        "automatic-control toggle, manual override, and the start/end time "
        "window, so it can move the cover even when automatic control is OFF and "
        "outside your schedule. Lower its priority below {safety} to make it "
        "respect those gates."
    ),
    # Rule 2
    TriageCode.HIGHER_PRIORITY_WON: (
        "ℹ️ The {winner} handler outranks solar tracking and won this cycle, so "
        "solar never set a position. Lower-priority handlers that did not run: "
        "{skipped}. Adjust the handler priorities if solar should win."
    ),
    # Rule 3
    TriageCode.TIME_WINDOW_SUSPECT: (
        "⚠️ The active-window schedule looks off (start {start}, end {end}); the "
        "cover may never track. Review the start and end times."
    ),
    # Rule 4
    TriageCode.CLIMATE_TEMP_NONE: (
        "⚠️ Climate mode is on but the inside temperature is unavailable, so "
        "climate decisions cannot run. Check the configured temperature sensor."
    ),
    # Rule 5
    TriageCode.SUMMER_WONT_CLOSE: (
        "⚠️ It is summer with presence detected and the blind is not transparent, "
        "yet the climate handler did not close it. Review the summer close "
        "conditions and temperature thresholds."
    ),
    # Rule 6
    TriageCode.PRESENCE_DEFAULTS_TRUE: (
        "ℹ️ Climate mode is on but no presence entity or template is set, so "
        "presence defaults to always-present. Add a presence sensor if occupancy "
        "should gate climate control."
    ),
    # Rule 7
    TriageCode.CLOUD_OR_SEMANTICS: (
        "ℹ️ More than one low-light input is configured ({inputs}); they combine "
        "with OR, so any one of them tripping suppresses tracking. Currently "
        "tripped: {tripped}."
    ),
    # Rule 8a — byte-identical to config_flow.py:1216.
    TriageCode.COVER_NOT_READY: "⚠️ {eid}: not ready (unavailable)",
    # Rule 8b
    TriageCode.ENTITY_UNAVAILABLE: (
        "⚠️ {eid} is unavailable — the integration cannot read its state. Check "
        "the device or the sensor."
    ),
    # Rule 9
    TriageCode.MIN_FLOOR_BYPASSED: (
        "⚠️ A minimum position of {min}% is set, but these fall below it: "
        "{offenders}. They are raised to the floor (or the floor is ignored) — "
        "reconcile the values."
    ),
    # Rule 10
    TriageCode.ENABLE_MIN_BACKWARDS: (
        "ℹ️ Minimum position {min}% is set but 'enforce minimum only while sun "
        "tracking' is off, so the floor applies at all times (the flag reads "
        "backwards). Confirm that is intended."
    ),
}


# ---------------------------------------------------------------------------
# Bundle loading (delegates to the shared i18n_bundle loader)
# ---------------------------------------------------------------------------


@cache
def _troubleshoot_overlay(language: str) -> tuple[tuple[str, str], ...]:
    """Flattened ``troubleshoot_i18n/<language>.json`` overlay (cached, immutable)."""
    return tuple(load_bundle_overlay(_TROUBLESHOOT_I18N_DIR, language).items())


def load_troubleshoot_labels(language: str) -> dict[str, str]:
    """Build the triage templates for ``language`` (English overlaid with the bundle)."""
    return merge_labels(_TRIAGE_TEMPLATES_EN, dict(_troubleshoot_overlay(language)))


async def async_prime(hass: object, language: str) -> dict[str, str]:
    """Load + cache the triage templates for ``language`` off the event loop."""
    return await hass.async_add_executor_job(load_troubleshoot_labels, language)
