"""Declarative diagnostics-triage engine (issue #970, Phase 1).

A cover's diagnostics payload plus its raw config options and per-entity
capabilities are folded into a single *view* mapping; :func:`run_triage` walks a
declarative :data:`TRIAGE_RULES` table and turns matches into :class:`Finding`
objects. Each finding carries a :class:`~..reason_i18n.Reason` (``code`` +
``params``) rendered through the shared :func:`..reason_i18n.render` against the
``troubleshoot_i18n`` bundle — one renderer, never a second formatter — plus a
severity and the options-flow step that fixes it.

The ``inputs`` flag on each rule (CONFIG / RUNTIME) is the Phase-2 seam: the
config summary and setup wizard pass ``only=RuleInput.CONFIG`` (config-only,
coordinator-free, deterministic); the troubleshooter passes ``None`` (everything).

Pure module: stdlib + project constants only, no ``homeassistant`` import and no
``config_flow`` import (mirrors the engine's 0-HA-imports constraint and
``reason_i18n`` — the epic's explicit anti-dependency-inversion rule so the
English templates are never pulled from ``config_flow``). It does NOT branch on
the cover-type string (CODING_GUIDELINES § cover-type boundary).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, Flag, auto

from ..const import (
    BLANK_TIME,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DISTANCE,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_TIME,
    CONF_IRRADIANCE_ENTITY,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_LUX_ENTITY,
    CONF_MANUAL_OVERRIDE_PRIORITY,
    CONF_MAX_ELEVATION,
    CONF_MIN_POSITION,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_TEMPLATE,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNSET_POS,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_ENTITY,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    GLARE_ZONE_SLOTS,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlStatus,
    TriageCode,
)
from ..reason_i18n import Reason, render
from ..troubleshoot_i18n import load_troubleshoot_labels

_LOGGER = logging.getLogger(__name__)

# Pipeline handler name strings surfaced in the diagnostics ``decision_trace`` /
# ``handler_priorities`` sections (``OverrideHandler.name``). Named here rather
# than imported because ``pipeline.handlers`` pulls Home Assistant — this module
# stays HA-free. These are handler identifiers, NOT cover-type strings.
_SOLAR_HANDLER = "solar"
_CLIMATE_HANDLER = "climate"
# The chain-floor handler (priority 0): when it wins, nothing above it matched.
_DEFAULT_HANDLER = "default"

# Sub-keys of a custom-position slot that count as an axis claim (mirrors
# ``helpers.CUSTOM_POSITION_CLAIM_KEYS`` — duplicated because that module imports
# Home Assistant and this one may not; see module docstring).
_CLAIM_KEYS: tuple[str, ...] = ("position", "position_max", "tilt_min", "tilt_max")

# The built-in manual-override handler's default priority (80 —
# ``ManualOverrideHandler.priority``). Named here rather than imported from
# ``pipeline.handlers`` because that module pulls Home Assistant; this module
# stays HA-free. Used only as the FALLBACK when the user has not set
# ``manual_override_priority``; the rule reads the configured value first. A
# custom slot whose priority sits strictly between the effective manual priority
# and the safety ceiling (100) wins over a manual override.
_MANUAL_OVERRIDE_PRIORITY = 80

# Fragment TriageCodes: they carry a template but NO rule row — they render only
# as nested params of another finding's template (the skip "N minutes ago" clause
# is spliced into the three skip templates). Mirrors ReasonCode's FRAGMENT_*
# members; excluded from the rule-table bijection lock.
_TRIAGE_FRAGMENT_CODES: frozenset[str] = frozenset({TriageCode.SKIP_AGE})

# A shaded distance below this (metres) makes the geometry near-binary: the
# projected shade sweeps the window over so short a throw that the position
# resolves to only a couple of discrete steps (issue #972, rule 16).
_NEAR_BINARY_DISTANCE_M = 0.75

# A movement-hysteresis delta wider than this (%) is "wide" for rule 19 — a
# special 0/100 default paired with it means fine intermediate moves are gated.
_WIDE_DELTA_PCT = 5


# ---------------------------------------------------------------------------
# Engine shapes
# ---------------------------------------------------------------------------


class Severity(Enum):
    """Ordered severity of a triage finding."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class RuleInput(Flag):
    """Which data surface a rule reads — the config/runtime Phase-2 seam."""

    CONFIG = auto()
    RUNTIME = auto()


@dataclass(frozen=True, slots=True)
class Finding:
    """One rendered triage result: a reason payload + severity + fix target."""

    reason: Reason
    severity: Severity
    fix_step: str | None = None


@dataclass(frozen=True, slots=True)
class TriageRule:
    """A declarative rule: metadata plus a ``check`` yielding param dicts.

    ``check`` receives the view mapping and yields zero or more param dicts —
    one :class:`Finding` per yielded dict (per-entity rules emit N, single-shot
    rules emit 0 or 1). Every read inside a check goes through :func:`_get` so an
    absent input yields nothing instead of raising.
    """

    code: str
    severity: Severity
    inputs: RuleInput
    fix_step: str | None
    wiki: str
    issues: tuple[int, ...]
    check: Callable[[Mapping], Iterable[Mapping]]


# ---------------------------------------------------------------------------
# Dotted accessor
# ---------------------------------------------------------------------------


def _get(data: Mapping, path: str) -> object:
    """Return ``data`` walked by dotted ``path``, or ``None`` on any miss.

    A missing key or a non-mapping segment yields ``None`` (never raises) so a
    check reading an absent diagnostics section simply produces no finding.
    Falsy leaf values (``0``/``False``/``""``) pass through unchanged.
    """
    node: object = data
    for segment in path.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(segment)
        if node is None:
            return None
    return node


# ---------------------------------------------------------------------------
# run_triage
# ---------------------------------------------------------------------------


def run_triage(
    data: Mapping,
    *,
    only: RuleInput | None = None,
    rules: Iterable[TriageRule] | None = None,
) -> list[Finding]:
    """Evaluate ``rules`` (default :data:`TRIAGE_RULES`) against ``data``.

    With ``only`` set, a rule is kept iff every one of its input flags is in
    ``only`` (subset semantics): ``only=RuleInput.CONFIG`` drops both RUNTIME and
    mixed ``CONFIG|RUNTIME`` rows. A check that raises is logged and skipped —
    the contract is that ``run_triage`` never propagates and ``run_triage({})``
    returns ``[]``.
    """
    active = TRIAGE_RULES if rules is None else rules
    findings: list[Finding] = []
    for rule in active:
        if only is not None and (rule.inputs & only) != rule.inputs:
            continue
        try:
            for params in rule.check(data):
                findings.append(
                    Finding(
                        Reason(rule.code, dict(params)),
                        rule.severity,
                        rule.fix_step,
                    )
                )
        except Exception:  # noqa: BLE001 - a bad rule must never break triage
            _LOGGER.exception("triage rule %s check failed", rule.code)
    return findings


# ---------------------------------------------------------------------------
# Offline adapter — map a downloaded diagnostics JSON to a triage view
# ---------------------------------------------------------------------------


def build_offline_view(doc: Mapping, *, latest_version: str | None = None) -> dict:
    """Fold a downloaded diagnostics document into a :func:`run_triage` view.

    Lets the offline entry points (``scripts/triage_json.py``, the
    ``acp-diagnose`` skill) run the SAME engine as the in-product troubleshoot
    step, so a gap in one is a gap in both. ``doc`` is either the raw diagnostics
    download (``{"config_options": ..., "diagnostics": ...}``) or HA's wrapped
    form (``{"data": {...}}``) — the envelope is unwrapped when present. Missing
    keys degrade to empty rather than raising.

    Offline seam: the download carries neither per-entity ``capabilities`` nor
    the policy-derived ``axis_requirements`` (both are built live in the config
    flow), so rules 8a (COVER_NOT_READY) and 13 (COVER_FEATURE_MISMATCH) cannot
    fire offline — only their runtime counterpart 8b does. Rule 24 (STALE_VERSION)
    needs ``latest_version`` injected here; without it, it never fires.
    """
    inner = doc.get("data") if isinstance(doc.get("data"), Mapping) else doc
    options = inner.get("config_options")
    diagnostics = inner.get("diagnostics")
    view: dict = {
        "options": dict(options) if isinstance(options, Mapping) else {},
        **(dict(diagnostics) if isinstance(diagnostics, Mapping) else {}),
    }
    if latest_version is not None:
        view["latest_version"] = latest_version
    return view


# ---------------------------------------------------------------------------
# Renderer (minimal; reuses reason_i18n.render, never a second formatter)
# ---------------------------------------------------------------------------


def render_report(
    findings: Iterable[Finding], labels: Mapping[str, str] | None = None
) -> str:
    """Render ``findings`` as a bullet list, one line per finding.

    Each bullet is a plain ``- `` marker plus the reason rendered through
    :func:`..reason_i18n.render` against ``labels`` (a translated troubleshoot
    bundle). When ``labels`` is ``None`` the English troubleshoot templates
    (:func:`..troubleshoot_i18n.load_troubleshoot_labels` for ``"en"`` — I/O-free)
    are used, so a triage finding renders its English prose rather than the raw
    ``triage.*`` code string (``reason_i18n``'s own English table has no triage
    codes). No severity icon is prepended — every triage template already leads
    with its own severity emoji, so prepending one here would double it.
    Deliberately minimal — the troubleshoot step owns richer presentation; this
    exists so both surfaces share one renderer.
    """
    active = labels if labels is not None else load_troubleshoot_labels("en")
    return "\n".join(f"- {render(finding.reason, active)}" for finding in findings)


# ---------------------------------------------------------------------------
# Shared rule helpers
# ---------------------------------------------------------------------------


def _is_template(value: object) -> bool:
    """Return True when ``value`` carries Jinja2 markup (mirrors templates.py)."""
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def _slot_sensors(options: Mapping, keys: Mapping[str, str]) -> list[str]:
    """Return a custom-position slot's trigger sensors (mirrors helpers.py)."""
    sensors = options.get(keys["sensors"])
    if sensors is not None:
        return [s for s in sensors if s]
    legacy = options.get(keys["sensor"])
    return [legacy] if legacy else []


def _slot_has_trigger(options: Mapping, keys: Mapping[str, str]) -> bool:
    """Return True when a slot has at least one sensor or a template trigger."""
    return bool(_slot_sensors(options, keys)) or _is_template(
        options.get(keys["template"])
    )


def _slot_configured(options: Mapping, keys: Mapping[str, str]) -> bool:
    """Return True when a slot has a trigger AND an axis claim (mirrors helpers.py)."""
    if not _slot_has_trigger(options, keys):
        return False
    return any(options.get(keys[sub]) is not None for sub in _CLAIM_KEYS)


def _iter_custom_slots(
    options: Mapping,
) -> Iterable[tuple[int, Mapping[str, str]]]:
    """Yield ``(slot_number, slot_keys)`` for every configured custom slot.

    Shared by rules 1 and 9 — the single place that walks slots 1–10 and gates
    on ``_slot_configured`` so both agree on which slots participate.
    """
    for num, keys in CUSTOM_POSITION_SLOTS.items():
        if _slot_configured(options, keys):
            yield num, keys


def _trace_step(data: Mapping, handler: str) -> Mapping | None:
    """Return the first ``decision_trace`` step for ``handler``, or ``None``."""
    trace = _get(data, "decision_trace")
    if not isinstance(trace, list):
        return None
    for step in trace:
        if isinstance(step, Mapping) and step.get("handler") == handler:
            return step
    return None


def _matched_winner(data: Mapping) -> Mapping | None:
    """Return the ``decision_trace`` step that won (``matched is True``), or None.

    Shared by rules 2 and 11 — the single place that scans the trace for the
    handler that actually decided the cycle, so both agree on "who won".
    """
    trace = _get(data, "decision_trace")
    if not isinstance(trace, list):
        return None
    return next(
        (
            step
            for step in trace
            if isinstance(step, Mapping) and step.get("matched") is True
        ),
        None,
    )


def _handler_priority(data: Mapping, handler: str) -> int | None:
    """Return the effective priority of ``handler`` from ``handler_priorities``."""
    value = _get(data, f"handler_priorities.{handler}.priority")
    return value if isinstance(value, int) else None


# ---------------------------------------------------------------------------
# Seed rule checks (rows 1,2,3,4,5,6,7,8a,8b,9,10)
# ---------------------------------------------------------------------------


def _check_custom_safety_bypass(data: Mapping) -> Iterable[Mapping]:
    """Rule 1 — a configured custom slot at safety priority bypasses every gate."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    for num, keys in _iter_custom_slots(options):
        priority = int(
            options.get(keys["priority"]) or DEFAULT_CUSTOM_POSITION_PRIORITY
        )
        if priority >= CUSTOM_POSITION_SAFETY_PRIORITY:
            yield {"slot": num, "safety": CUSTOM_POSITION_SAFETY_PRIORITY}


def _check_higher_priority_won(data: Mapping) -> Iterable[Mapping]:
    """Rule 2 — a handler above solar won the cycle; solar never had a say."""
    winner = _matched_winner(data)
    if winner is None:
        return
    winner_name = winner.get("handler")
    if winner_name == _SOLAR_HANDLER:
        return
    solar_priority = _handler_priority(data, _SOLAR_HANDLER)
    winner_priority = _handler_priority(data, winner_name)
    if (
        solar_priority is None
        or winner_priority is None
        or winner_priority <= solar_priority
    ):
        return
    priorities = _get(data, "handler_priorities")
    lower = []
    if isinstance(priorities, Mapping):
        lower = sorted(
            name
            for name, row in priorities.items()
            if isinstance(row, Mapping)
            and isinstance(row.get("priority"), int)
            and row["priority"] < winner_priority
        )
    yield {"winner": winner_name, "skipped": ", ".join(lower) or "none"}


def _check_time_window_suspect(data: Mapping) -> Iterable[Mapping]:
    """Rule 3 — the active-window schedule looks misconfigured.

    Two genuinely-suspect signals: a start bound driven by a sun sensor
    (``sensor.sun_next_*``), or an inverted window (start after end) with *real*
    clock times. ``BLANK_TIME`` (``"00:00:00"``) is the UNSET / all-day sentinel
    — a legacy entry carrying it tracks all day legitimately, so it is never
    flagged on its own nor treated as a real bound in the start>end comparison
    (issue #970, MINOR 6).
    """
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    start_entity = options.get(CONF_START_ENTITY)
    start_time = options.get(CONF_START_TIME)
    end_time = options.get(CONF_END_TIME)

    sun_sensor_start = isinstance(start_entity, str) and start_entity.startswith(
        "sensor.sun_next_"
    )
    real_start = isinstance(start_time, str) and start_time != BLANK_TIME
    real_end = isinstance(end_time, str) and end_time != BLANK_TIME
    inverted = real_start and real_end and start_time > end_time

    if not (sun_sensor_start or inverted):
        return

    # MINOR 8: never render "end None" — fall back to the all-day sentinel when
    # the end bound is unset/missing so the message stays sensible.
    start_display = start_entity if sun_sensor_start else start_time
    end_display = end_time if real_end else BLANK_TIME
    yield {"start": start_display, "end": end_display}


def _check_climate_temp_none(data: Mapping) -> Iterable[Mapping]:
    """Rule 4 — climate mode on but the inside temperature is unavailable."""
    details = _get(data, "temperature_details")
    if isinstance(details, Mapping) and details.get("inside_temperature") is None:
        yield {}


def _check_summer_wont_close(data: Mapping) -> Iterable[Mapping]:
    """Rule 5 — summer + presence, blind isn't transparent, yet climate didn't act."""
    options = _get(data, "options")
    conditions = _get(data, "climate_conditions")
    if not isinstance(options, Mapping) or not isinstance(conditions, Mapping):
        return
    if not (conditions.get("is_summer") and conditions.get("is_presence")):
        return
    if options.get(CONF_TRANSPARENT_BLIND):
        return
    climate_step = _trace_step(data, _CLIMATE_HANDLER)
    if climate_step is not None and climate_step.get("matched") is False:
        yield {}


def _check_presence_defaults_true(data: Mapping) -> Iterable[Mapping]:
    """Rule 6 — climate mode on with no presence entity/template (defaults present)."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    if not options.get(CONF_CLIMATE_MODE):
        return
    if options.get(CONF_PRESENCE_ENTITY) or _is_template(
        options.get(CONF_PRESENCE_TEMPLATE)
    ):
        return
    yield {}


def _check_cloud_or_semantics(data: Mapping) -> Iterable[Mapping]:
    """Rule 7 — more than one cloud/low-light input; OR semantics may surprise."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    configured = [
        label
        for label, present in (
            ("lux", bool(options.get(CONF_LUX_ENTITY))),
            ("irradiance", bool(options.get(CONF_IRRADIANCE_ENTITY))),
            (
                "weather",
                bool(options.get(CONF_IS_SUNNY_SENSOR))
                or _is_template(options.get(CONF_IS_SUNNY_TEMPLATE))
                or bool(options.get(CONF_WEATHER_ENTITY)),
            ),
        )
        if present
    ]
    if len(configured) <= 1:
        return
    conditions = _get(data, "climate_conditions")
    tripped: list[str] = []
    if isinstance(conditions, Mapping):
        tripped = [
            label
            for label, key in (
                ("lux below threshold", "lux_below_threshold"),
                ("irradiance below threshold", "irradiance_below_threshold"),
                ("cloud coverage above threshold", "cloud_coverage_above_threshold"),
                ("not sunny", "is_sunny"),
            )
            if (
                conditions.get(key) is False
                if key == "is_sunny"
                else conditions.get(key)
            )
        ]
    yield {
        "inputs": ", ".join(configured),
        "tripped": ", ".join(tripped) or "none",
    }


def _check_cover_not_ready(data: Mapping) -> Iterable[Mapping]:
    """Rule 8a — a configured cover reported no capabilities (unavailable)."""
    capabilities = _get(data, "capabilities")
    if not isinstance(capabilities, Mapping):
        return
    for eid, caps in capabilities.items():
        if caps is None:
            yield {"eid": eid}


def _check_entity_unavailable(data: Mapping) -> Iterable[Mapping]:
    """Rule 8b — a configured sensor or cover entity is currently unavailable."""
    for section in ("local_sensors", "building_profile_sensors"):
        sensors = _get(data, section)
        if isinstance(sensors, list):
            for descriptor in sensors:
                if (
                    isinstance(descriptor, Mapping)
                    and descriptor.get("state") == "unavailable"
                    and descriptor.get("entity_id")
                ):
                    yield {"eid": descriptor["entity_id"]}
    covers = _get(data, "covers")
    if isinstance(covers, Mapping):
        for eid, cover in covers.items():
            if isinstance(cover, Mapping) and cover.get("available") is False:
                yield {"eid": eid}


def _check_min_floor_bypassed(data: Mapping) -> Iterable[Mapping]:
    """Rule 9 — a min-position floor is undercut by a lower fixed position."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    min_position = options.get(CONF_MIN_POSITION)
    if not isinstance(min_position, int) or min_position <= 0:
        return
    offenders: list[str] = []
    sunset = options.get(CONF_SUNSET_POS)
    if isinstance(sunset, int) and sunset < min_position:
        offenders.append("sunset position")
    for num, keys in _iter_custom_slots(options):
        pos = options.get(keys["position"])
        if (
            isinstance(pos, int)
            and pos < min_position
            and not options.get(keys["use_my"])
            and not options.get(keys["min_mode"])
        ):
            offenders.append(f"Custom #{num}")
    if offenders:
        yield {"min": min_position, "offenders": ", ".join(offenders)}


def _check_enable_min_backwards(data: Mapping) -> Iterable[Mapping]:
    """Rule 10 — enable_min_position False (only-while-tracking) with a floor set."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    min_position = options.get(CONF_MIN_POSITION)
    if (
        options.get(CONF_ENABLE_MIN_POSITION) is False
        and isinstance(min_position, int)
        and min_position > 0
    ):
        yield {"min": min_position}


# ---------------------------------------------------------------------------
# Backfill rule checks (rows 11–24)
# ---------------------------------------------------------------------------


def _check_tracking_window_truncated(data: Mapping) -> Iterable[Mapping]:
    """Rule 15 — a low ``max_elevation`` truncates the sun-tracking window."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    max_elev = options.get(CONF_MAX_ELEVATION)
    if isinstance(max_elev, int | float) and not isinstance(max_elev, bool):
        if max_elev <= 25:
            yield {"max_elevation": max_elev}


def _check_geometry_near_binary(data: Mapping) -> Iterable[Mapping]:
    """Rule 16 — a near-zero shaded distance makes the position near-binary."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    distance = options.get(CONF_DISTANCE)
    if isinstance(distance, int | float) and not isinstance(distance, bool):
        if distance < _NEAR_BINARY_DISTANCE_M:
            yield {"distance": distance}


def _check_special_pos_delta(data: Mapping) -> Iterable[Mapping]:
    """Rule 19 — a special 0/100 default with a wide delta gates fine moves."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    default = options.get(CONF_DEFAULT_HEIGHT)
    delta = options.get(CONF_DELTA_POSITION)
    if (
        default in (POSITION_CLOSED, POSITION_OPEN)
        and isinstance(delta, int | float)
        and not isinstance(delta, bool)
        and delta > _WIDE_DELTA_PCT
    ):
        yield {"default": default, "delta": delta}


def _check_custom_above_manual(data: Mapping) -> Iterable[Mapping]:
    """Rule 22 — a configured custom slot outranks manual override (not safety)."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    # Read the user's configured manual-override priority; fall back to the
    # built-in default (80) only when unset, so a user who raised or lowered it
    # is honoured rather than compared against a hardcoded 80.
    manual = options.get(CONF_MANUAL_OVERRIDE_PRIORITY)
    manual = int(manual) if isinstance(manual, int) else _MANUAL_OVERRIDE_PRIORITY
    for num, keys in _iter_custom_slots(options):
        priority = int(
            options.get(keys["priority"]) or DEFAULT_CUSTOM_POSITION_PRIORITY
        )
        if manual < priority < CUSTOM_POSITION_SAFETY_PRIORITY:
            yield {"slot": num, "priority": priority, "manual": manual}


def _glare_zone_min_distance(zone_y: float, radius: float) -> float:
    """Return the nearest window-distance a glare zone can still shade.

    A zone centred ``zone_y`` metres from the window with radius ``radius``
    reaches to ``zone_y - radius`` at its closest edge. The glare handler can
    only act on a zone whose near edge falls within the configured shaded
    distance, so this is the single formula both the fires-check and the
    rendered ``reach`` param derive from (§197).
    """
    return zone_y - radius


def _check_glare_zone_unreachable(data: Mapping) -> Iterable[Mapping]:
    """Rule 12 — a configured glare zone sits beyond the shaded distance."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    if not options.get(CONF_ENABLE_GLARE_ZONES):
        return
    # The glare handler's "beyond shaded distance" short-circuit only runs while
    # sun tracking is enabled (default True); with tracking off the zone can
    # still act against the default position, so an unreachable zone is not a
    # fault. Gate the finding on the same flag (finding 5).
    if not options.get(CONF_ENABLE_SUN_TRACKING, True):
        return
    distance = options.get(CONF_DISTANCE)
    if not isinstance(distance, int | float) or isinstance(distance, bool):
        return
    for keys in GLARE_ZONE_SLOTS.values():
        name = options.get(keys["name"])
        if not name:
            continue
        zone_y = options.get(keys["y"])
        radius = options.get(keys["radius"])
        if not isinstance(zone_y, int | float) or isinstance(zone_y, bool):
            continue
        if not isinstance(radius, int | float) or isinstance(radius, bool):
            continue
        reach = _glare_zone_min_distance(zone_y, radius)
        if reach > distance:
            # Round the reach for display so float subtraction noise
            # (0.9000000000000004) renders as a clean 0.9 m (finding 9).
            yield {"zone": name, "reach": round(reach, 2), "distance": distance}


def _check_position_matching_off(data: Mapping) -> Iterable[Mapping]:
    """Rule 23 — position matching is off while a manual override holds."""
    options = _get(data, "options")
    if not isinstance(options, Mapping):
        return
    if (
        _get(data, "control_status") == ControlStatus.MANUAL_OVERRIDE
        and options.get(CONF_ENABLE_POSITION_MATCHING) is False
    ):
        yield {}


def _check_dry_run(data: Mapping) -> Iterable[Mapping]:
    """Rule 17 — dry-run mode is still on, so commands are logged not sent."""
    if _get(data, "debug_config.dry_run") is True:
        yield {}


def _check_override_blocked_auto_off(data: Mapping) -> Iterable[Mapping]:
    """Rule 21 — automatic control is off, so overrides cannot act."""
    if _get(data, "control_status") == ControlStatus.AUTOMATIC_CONTROL_OFF:
        yield {}


def _check_azimuth_fov_mismatch(data: Mapping) -> Iterable[Mapping]:
    """Rule 11 — sun is up but outside the FOV and only the default handler ran."""
    if _get(data, "sun_validity.in_fov") is not False:
        return
    if _get(data, "sun_validity.valid_elevation") is not True:
        return
    winner = _matched_winner(data)
    if winner is not None and winner.get("handler") == _DEFAULT_HANDLER:
        yield {}


def _cap_satisfied(caps: Mapping, requirement: Mapping) -> bool:
    """Whether ``caps`` can drive the axis named by ``requirement``.

    True when the native capability flag is set, or when any fallback group is
    fully satisfied (position via open/close). Mirrors ``CoverAxis.is_drivable``
    without importing it (that path pulls Home Assistant). The capability keys
    are read straight from the ``requirement`` data — no ``has_*`` literal and no
    cover-type string appears here, keeping this module clear of the cover-type
    boundary (CODING_GUIDELINES § "Cover Type Abstraction").
    """
    if caps.get(requirement["capability"]):
        return True
    return any(
        all(caps.get(cap) for cap in group)
        for group in requirement.get("fallbacks") or ()
    )


def _check_cover_feature_mismatch(data: Mapping) -> Iterable[Mapping]:
    """Rule 13 — a bound cover lacks a capability its cover type requires.

    Reads the policy-derived ``axis_requirements`` (folded into the view at the
    HA boundary) against each entity's ``capabilities``. A cover with ``None``
    caps is unavailable — rule 8a owns that — so it is skipped here.
    """
    requirements = _get(data, "axis_requirements") or ()
    capabilities = _get(data, "capabilities") or {}
    if not isinstance(capabilities, Mapping):
        return
    for eid, caps in capabilities.items():
        if not isinstance(caps, Mapping):
            continue
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            if not _cap_satisfied(caps, requirement):
                yield {"entity": eid, "axis": requirement["axis"]}


def _check_endpoint_chase(data: Mapping) -> Iterable[Mapping]:
    """Rule 18 — a cover gave up chasing its target after repeated retries."""
    commands = _get(data, "cover_commands")
    if not isinstance(commands, Mapping):
        return
    for eid, state in commands.items():
        if isinstance(state, Mapping) and state.get("gave_up") is True:
            yield {
                "entity": eid,
                "retry_count": state.get("retry_count"),
                "target": state.get("target_call"),
            }


def _age_minutes(iso_a: object, iso_b: object) -> float | None:
    """Return whole-minute age from ISO ``iso_a`` to ISO ``iso_b``, or None.

    Single source of truth for "how long ago" arithmetic across the skip rules
    (§197). Both inputs are ISO-8601 strings from the payload; ANY failure —
    a non-string, an unparseable value, or a mixed aware/naive subtraction that
    raises ``TypeError`` — yields ``None`` rather than raising, so a finding
    still renders (without an age) instead of vanishing from the report.
    """
    if not isinstance(iso_a, str) or not isinstance(iso_b, str):
        return None
    try:
        start = datetime.fromisoformat(iso_a)
        end = datetime.fromisoformat(iso_b)
        return round((end - start).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        return None


def _skip_reason_check(
    reason: str, *, age: bool = True
) -> Callable[[Mapping], Iterable[Mapping]]:
    """Build a check firing when ``last_skipped_action.reason`` equals ``reason``.

    One factory backs all three rule-20 skip rows (§55) — each row differs only
    in the reason it matches and its severity. The yielded params carry the
    skipped entity plus a ``when`` clause: when ``age`` is set AND a skip age is
    derivable, ``when`` is a nested :class:`~..reason_i18n.Reason` fragment
    (:data:`TriageCode.SKIP_AGE`) rendering the localized "N minutes ago" text
    and ``age_minutes`` carries the raw number; when the age is unknown, ``when``
    is an empty string and no ``age_minutes`` key is emitted, so the template
    renders cleanly without an age clause (never "None minutes ago").
    """

    def _check(data: Mapping) -> Iterable[Mapping]:
        skipped = _get(data, "last_skipped_action")
        if not isinstance(skipped, Mapping) or skipped.get("reason") != reason:
            return
        params: dict = {"entity": skipped.get("entity_id"), "when": ""}
        if age:
            minutes = _age_minutes(
                skipped.get("timestamp"), _get(data, "data_window.captured_at")
            )
            if minutes is not None:
                params["age_minutes"] = minutes
                params["when"] = Reason(TriageCode.SKIP_AGE, {"age_minutes": minutes})
        yield params

    return _check


def _calver_tuple(version: object) -> tuple[int, int, int] | None:
    """Parse a CalVer/SemVer ``Year.Month.Patch`` string to a 3-int tuple, or None.

    Splits on ``.``, converts each dotted segment to an int, and normalizes to
    exactly three segments (missing trailing segments padded with 0, extras
    dropped) so ``"2026.8"`` and ``"2026.8.0"`` compare EQUAL rather than the
    shorter one reading as older. A non-string or any non-integer segment (a beta
    suffix, empty, ``"garbage"``) yields ``None`` so the rule simply produces no
    finding rather than raising.
    """
    if not isinstance(version, str):
        return None
    try:
        parts = [int(part) for part in version.split(".")]
    except ValueError:
        return None
    parts = (parts + [0, 0, 0])[:3]
    return (parts[0], parts[1], parts[2])


def _check_mixed_temp_units(data: Mapping) -> Iterable[Mapping]:
    """Rule 14 — the inside and outside temperature sensors report unlike units.

    The inside sensor's unit is emitted at ``temp_sensor.unit_of_measurement``
    (builder.py); the outside sensor rides in ``local_sensors`` /
    ``building_profile_sensors`` as the ``SensorSource`` descriptor keyed
    ``outside_temp`` (``CONF_OUTSIDETEMP_ENTITY``). Adaptive Cover Pro does not
    convert between units, so a °F-vs-°C pair makes every climate comparison
    wrong. Only the temperature descriptors are compared — a lux/irradiance unit
    is never a temperature mismatch. Every read goes through ``_get`` so absent
    keys yield nothing (Phase 0, issue #969).
    """
    inside_unit = _get(data, "temp_sensor.unit_of_measurement")
    if not isinstance(inside_unit, str):
        return
    outside_unit: str | None = None
    for section in ("local_sensors", "building_profile_sensors"):
        sensors = _get(data, section)
        if not isinstance(sensors, list):
            continue
        for descriptor in sensors:
            if (
                isinstance(descriptor, Mapping)
                and descriptor.get("key") == CONF_OUTSIDETEMP_ENTITY
            ):
                unit = descriptor.get("unit_of_measurement")
                if isinstance(unit, str):
                    outside_unit = unit
    if outside_unit is not None and outside_unit != inside_unit:
        yield {"inside_unit": inside_unit, "outside_unit": outside_unit}


def _check_stale_version(data: Mapping) -> Iterable[Mapping]:
    """Rule 24 — a newer integration release is available (CalVer compare)."""
    latest_raw = _get(data, "latest_version")
    current_raw = _get(data, "meta.integration_version")
    latest = _calver_tuple(latest_raw)
    current = _calver_tuple(current_raw)
    if latest is not None and current is not None and latest > current:
        yield {"latest": latest_raw, "current": current_raw}


# ---------------------------------------------------------------------------
# The rule table
# ---------------------------------------------------------------------------

TRIAGE_RULES: tuple[TriageRule, ...] = (
    TriageRule(
        code=TriageCode.CUSTOM_SAFETY_BYPASS,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="custom_position",
        wiki="Troubleshooting-Findings#custom-safety-bypass",
        issues=(711, 716),
        check=_check_custom_safety_bypass,
    ),
    TriageRule(
        code=TriageCode.HIGHER_PRIORITY_WON,
        severity=Severity.INFO,
        inputs=RuleInput.RUNTIME,
        fix_step="pipeline_priorities",
        wiki="Troubleshooting-Findings#higher-priority-won",
        issues=(953,),
        check=_check_higher_priority_won,
    ),
    TriageRule(
        code=TriageCode.TIME_WINDOW_SUSPECT,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="automation",
        wiki="Troubleshooting-Findings#time-window-suspect",
        issues=(953,),
        check=_check_time_window_suspect,
    ),
    TriageRule(
        code=TriageCode.CLIMATE_TEMP_NONE,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="temperature_climate",
        wiki="Troubleshooting-Findings#climate-temp-none",
        issues=(953,),
        check=_check_climate_temp_none,
    ),
    TriageRule(
        code=TriageCode.SUMMER_WONT_CLOSE,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG | RuleInput.RUNTIME,
        fix_step="temperature_climate",
        wiki="Troubleshooting-Findings#summer-wont-close",
        issues=(953,),
        check=_check_summer_wont_close,
    ),
    TriageRule(
        code=TriageCode.PRESENCE_DEFAULTS_TRUE,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="temperature_climate",
        wiki="Troubleshooting-Findings#presence-defaults-true",
        issues=(953,),
        check=_check_presence_defaults_true,
    ),
    TriageRule(
        code=TriageCode.CLOUD_OR_SEMANTICS,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG | RuleInput.RUNTIME,
        fix_step="light_cloud",
        wiki="Troubleshooting-Findings#cloud-or-semantics",
        issues=(953,),
        check=_check_cloud_or_semantics,
    ),
    TriageRule(
        code=TriageCode.COVER_NOT_READY,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="cover_entities",
        wiki="Troubleshooting-Findings#cover-not-ready",
        issues=(953,),
        check=_check_cover_not_ready,
    ),
    TriageRule(
        code=TriageCode.ENTITY_UNAVAILABLE,
        severity=Severity.WARNING,
        # No fix_step: an unavailable entity is an HA-side condition (the entity
        # itself is down) and 8b fires for heterogeneous entities — local
        # sensors AND cover entities — so no single ACP options step fixes it.
        # The finding names the entity; the troubleshoot step drops a None
        # fix_step from its menu, so nothing broken is routed.
        inputs=RuleInput.RUNTIME,
        fix_step=None,
        wiki="Troubleshooting-Findings#entity-unavailable",
        issues=(549, 953),
        check=_check_entity_unavailable,
    ),
    TriageRule(
        code=TriageCode.MIN_FLOOR_BYPASSED,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="position",
        wiki="Troubleshooting-Findings#min-floor-bypassed",
        issues=(953,),
        check=_check_min_floor_bypassed,
    ),
    TriageRule(
        code=TriageCode.ENABLE_MIN_BACKWARDS,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="position",
        wiki="Troubleshooting-Findings#enable-min-backwards",
        issues=(953,),
        check=_check_enable_min_backwards,
    ),
    TriageRule(
        code=TriageCode.TRACKING_WINDOW_TRUNCATED,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="sun_tracking",
        wiki="Troubleshooting-Findings#tracking-window-truncated",
        issues=(972,),
        check=_check_tracking_window_truncated,
    ),
    TriageRule(
        code=TriageCode.GEOMETRY_NEAR_BINARY,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="geometry",
        wiki="Troubleshooting-Findings#geometry-near-binary",
        issues=(972,),
        check=_check_geometry_near_binary,
    ),
    TriageRule(
        code=TriageCode.SPECIAL_POSITION_DELTA_BYPASS,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="behavior",
        wiki="Troubleshooting-Findings#special-position-delta-bypass",
        issues=(972,),
        check=_check_special_pos_delta,
    ),
    TriageRule(
        code=TriageCode.CUSTOM_ABOVE_MANUAL,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="custom_position",
        wiki="Troubleshooting-Findings#custom-above-manual",
        issues=(972,),
        check=_check_custom_above_manual,
    ),
    TriageRule(
        code=TriageCode.GLARE_ZONE_NEVER_FIRES,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="glare_zones",
        wiki="Troubleshooting-Findings#glare-zone-never-fires",
        issues=(972,),
        check=_check_glare_zone_unreachable,
    ),
    TriageRule(
        code=TriageCode.POSITION_MATCHING_OFF,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG | RuleInput.RUNTIME,
        fix_step="position",
        wiki="Troubleshooting-Findings#position-matching-off",
        issues=(972,),
        check=_check_position_matching_off,
    ),
    TriageRule(
        code=TriageCode.DRY_RUN_LEFT_ON,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="debug",
        wiki="Troubleshooting-Findings#dry-run-left-on",
        issues=(972,),
        check=_check_dry_run,
    ),
    TriageRule(
        code=TriageCode.OVERRIDE_BLOCKED_AUTO_OFF,
        severity=Severity.INFO,
        # No fix_step: automatic control being off is a deliberate user toggle,
        # not a misconfiguration — the finding is purely informational.
        inputs=RuleInput.RUNTIME,
        fix_step=None,
        wiki="Troubleshooting-Findings#override-blocked-auto-off",
        issues=(972,),
        check=_check_override_blocked_auto_off,
    ),
    TriageRule(
        code=TriageCode.AZIMUTH_FOV_MISMATCH,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="geometry",
        wiki="Troubleshooting-Findings#azimuth-fov-mismatch",
        issues=(972,),
        check=_check_azimuth_fov_mismatch,
    ),
    TriageRule(
        code=TriageCode.ENDPOINT_CHASE,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="position",
        wiki="Troubleshooting-Findings#endpoint-chase",
        issues=(972,),
        check=_check_endpoint_chase,
    ),
    TriageRule(
        code=TriageCode.COVER_FEATURE_MISMATCH,
        severity=Severity.CRITICAL,
        inputs=RuleInput.CONFIG,
        fix_step="cover_entities",
        wiki="Troubleshooting-Findings#cover-feature-mismatch",
        issues=(972,),
        check=_check_cover_feature_mismatch,
    ),
    TriageRule(
        code=TriageCode.SKIP_SERVICE_CALL_FAILED,
        severity=Severity.CRITICAL,
        inputs=RuleInput.RUNTIME,
        fix_step="cover_entities",
        wiki="Troubleshooting-Findings#skip-service-call-failed",
        issues=(972,),
        check=_skip_reason_check("service_call_failed"),
    ),
    TriageRule(
        code=TriageCode.SKIP_NO_CAPABLE_SERVICE,
        severity=Severity.CRITICAL,
        inputs=RuleInput.RUNTIME,
        fix_step="cover_entities",
        wiki="Troubleshooting-Findings#skip-no-capable-service",
        issues=(972,),
        check=_skip_reason_check("no_capable_service"),
    ),
    TriageRule(
        code=TriageCode.SKIP_COVER_UNAVAILABLE,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="cover_entities",
        wiki="Troubleshooting-Findings#skip-cover-unavailable",
        issues=(972,),
        check=_skip_reason_check("cover_unavailable"),
    ),
    TriageRule(
        code=TriageCode.MIXED_TEMP_UNITS,
        severity=Severity.CRITICAL,
        inputs=RuleInput.RUNTIME,
        fix_step="temperature_climate",
        wiki="Troubleshooting-Findings#mixed-temp-units",
        issues=(969, 972),
        check=_check_mixed_temp_units,
    ),
    TriageRule(
        code=TriageCode.STALE_VERSION,
        severity=Severity.WARNING,
        # No fix_step: updating the integration is an HA/HACS action, not an
        # options-flow step, so the finding is informational.
        inputs=RuleInput.RUNTIME,
        fix_step=None,
        wiki="Troubleshooting-Findings#stale-version",
        issues=(972,),
        check=_check_stale_version,
    ),
)
