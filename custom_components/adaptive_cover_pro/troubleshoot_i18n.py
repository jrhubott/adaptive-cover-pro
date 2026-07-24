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
    # Rule 15
    TriageCode.TRACKING_WINDOW_TRUNCATED: (
        "⚠️ Maximum tracking elevation is {max_elevation}°, so sun tracking stops "
        "once the sun climbs above it — a low value truncates the active window. "
        "Raise it if the cover should keep tracking a higher sun."
    ),
    # Rule 16
    TriageCode.GEOMETRY_NEAR_BINARY: (
        "ℹ️ The shaded distance is only {distance} m, so the shade sweeps the "
        "window in just a couple of steps and the position is nearly all-or-"
        "nothing. Increase the shaded distance if you want finer positions."
    ),
    # Rule 19
    TriageCode.SPECIAL_POSITION_DELTA_BYPASS: (
        "ℹ️ The default position is a special {default}% with a movement delta of "
        "{delta}% — fine intermediate moves below the delta are skipped, so the "
        "cover jumps between endpoints. Lower the delta for smoother steps."
    ),
    # Rule 22
    TriageCode.CUSTOM_ABOVE_MANUAL: (
        "ℹ️ Custom #{slot} has priority {priority}, above manual override "
        "({manual}), so it overrides a manual move without acting as a safety "
        "slot. Lower it below {manual} if a manual override should win."
    ),
    # Rule 12
    TriageCode.GLARE_ZONE_NEVER_FIRES: (
        "⚠️ Glare zone '{zone}' reaches to {reach} m from the window but the "
        "shaded distance is only {distance} m, so the zone is always beyond reach "
        "and never triggers. Move the zone closer or increase the shaded distance."
    ),
    # Rule 23
    TriageCode.POSITION_MATCHING_OFF: (
        "ℹ️ A manual override is active and position matching is off, so the "
        "integration will not clear the override when the cover returns to the "
        "calculated position. Enable position matching if it should self-clear."
    ),
    # Rule 17
    TriageCode.DRY_RUN_LEFT_ON: (
        "⚠️ Dry-run mode is on: commands are logged but never sent, so the cover "
        "never actually moves. Turn dry-run off in the debug options once you are "
        "done testing."
    ),
    # Rule 21
    TriageCode.OVERRIDE_BLOCKED_AUTO_OFF: (
        "ℹ️ Automatic control is off for this cover, so the integration will not "
        "move it and overrides cannot act. Turn automatic control back on when you "
        "want tracking to resume."
    ),
    # Rule 11
    TriageCode.AZIMUTH_FOV_MISMATCH: (
        "⚠️ The sun is above the horizon but outside this window's field of view, "
        "so only the default handler ran and no sun tracking happened. Check the "
        "window azimuth and FOV if the sun should be reaching this window."
    ),
    # Rule 18
    TriageCode.ENDPOINT_CHASE: (
        "⚠️ {entity} gave up reaching its target of {target}% after {retry_count} "
        "retries — it keeps settling short of the commanded position. Check the "
        "cover's travel calibration or position feedback."
    ),
    # Rule 13
    TriageCode.COVER_FEATURE_MISMATCH: (
        "🛑 {entity} does not support the {axis} axis this cover type needs, so "
        "the integration cannot drive it correctly. Pick a cover that exposes the "
        "{axis} axis, or choose a cover type that matches this entity."
    ),
    # Rule 20a — ``{when}`` is the optional localized age clause (SKIP_AGE) or "".
    TriageCode.SKIP_SERVICE_CALL_FAILED: (
        "🛑 The last command to {entity} was skipped{when} because the service "
        "call failed. Check the cover integration and the HA log for the "
        "underlying error."
    ),
    # Rule 20b
    TriageCode.SKIP_NO_CAPABLE_SERVICE: (
        "🛑 The last command to {entity} was skipped{when} because no capable "
        "service was found to move it. The entity may not expose the position or "
        "open/close services the integration needs."
    ),
    # Rule 20c
    TriageCode.SKIP_COVER_UNAVAILABLE: (
        "⚠️ The last command to {entity} was skipped{when} because the cover was "
        "unavailable. Check the device — it was offline when the integration "
        "tried to move it."
    ),
    # Fragment (not a rule) — the localized age clause spliced into the skip
    # templates above via their ``{when}`` param. Leads with a space so it slots
    # in after "skipped"; absent when the skip age is unknown.
    TriageCode.SKIP_AGE: " {age_minutes} minutes ago",
    # Rule 24
    TriageCode.STALE_VERSION: (
        "⚠️ A newer release is available: you are on {current} but {latest} has "
        "shipped. Update the integration through HACS to pick up the latest fixes."
    ),
    # Rule 14
    TriageCode.MIXED_TEMP_UNITS: (
        "🛑 The inside temperature sensor reports {inside_unit} but the outside "
        "temperature sensor reports {outside_unit}. Adaptive Cover Pro does not "
        "convert between units, so climate comparisons will be wrong. Set both "
        "temperature sensors to the same unit."
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
