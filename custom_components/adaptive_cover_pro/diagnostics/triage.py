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
from enum import Enum, Flag, auto

from ..const import (
    BLANK_TIME,
    CONF_CLIMATE_MODE,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_TIME,
    CONF_IRRADIANCE_ENTITY,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_LUX_ENTITY,
    CONF_MIN_POSITION,
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

# Sub-keys of a custom-position slot that count as an axis claim (mirrors
# ``helpers.CUSTOM_POSITION_CLAIM_KEYS`` — duplicated because that module imports
# Home Assistant and this one may not; see module docstring).
_CLAIM_KEYS: tuple[str, ...] = ("position", "position_max", "tilt_min", "tilt_max")


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
    trace = _get(data, "decision_trace")
    if not isinstance(trace, list):
        return
    winner = next(
        (
            step
            for step in trace
            if isinstance(step, Mapping) and step.get("matched") is True
        ),
        None,
    )
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
# The rule table
# ---------------------------------------------------------------------------

TRIAGE_RULES: tuple[TriageRule, ...] = (
    TriageRule(
        code=TriageCode.CUSTOM_SAFETY_BYPASS,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="custom_position",
        wiki="Configuration-Custom-Positions#safety-priority",
        issues=(711, 716),
        check=_check_custom_safety_bypass,
    ),
    TriageRule(
        code=TriageCode.HIGHER_PRIORITY_WON,
        severity=Severity.INFO,
        inputs=RuleInput.RUNTIME,
        fix_step="pipeline_priorities",
        wiki="Pipeline-Priorities#priority-order",
        issues=(953,),
        check=_check_higher_priority_won,
    ),
    TriageRule(
        code=TriageCode.TIME_WINDOW_SUSPECT,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="automation",
        wiki="Configuration-Common#time-window",
        issues=(953,),
        check=_check_time_window_suspect,
    ),
    TriageRule(
        code=TriageCode.CLIMATE_TEMP_NONE,
        severity=Severity.WARNING,
        inputs=RuleInput.RUNTIME,
        fix_step="temperature_climate",
        wiki="Configuration-Climate#temperature-sensor",
        issues=(953,),
        check=_check_climate_temp_none,
    ),
    TriageRule(
        code=TriageCode.SUMMER_WONT_CLOSE,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG | RuleInput.RUNTIME,
        fix_step="temperature_climate",
        wiki="Configuration-Climate#summer-close",
        issues=(953,),
        check=_check_summer_wont_close,
    ),
    TriageRule(
        code=TriageCode.PRESENCE_DEFAULTS_TRUE,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="temperature_climate",
        wiki="Configuration-Climate#presence",
        issues=(953,),
        check=_check_presence_defaults_true,
    ),
    TriageRule(
        code=TriageCode.CLOUD_OR_SEMANTICS,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG | RuleInput.RUNTIME,
        fix_step="light_cloud",
        wiki="Configuration-Common#cloud-suppression",
        issues=(953,),
        check=_check_cloud_or_semantics,
    ),
    TriageRule(
        code=TriageCode.COVER_NOT_READY,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="cover_entities",
        wiki="Troubleshooting#cover-not-ready",
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
        wiki="Troubleshooting#unavailable-entities",
        issues=(549, 953),
        check=_check_entity_unavailable,
    ),
    TriageRule(
        code=TriageCode.MIN_FLOOR_BYPASSED,
        severity=Severity.WARNING,
        inputs=RuleInput.CONFIG,
        fix_step="position",
        wiki="Configuration-Common#min-position",
        issues=(953,),
        check=_check_min_floor_bypassed,
    ),
    TriageRule(
        code=TriageCode.ENABLE_MIN_BACKWARDS,
        severity=Severity.INFO,
        inputs=RuleInput.CONFIG,
        fix_step="position",
        wiki="Configuration-Common#enable-min-position",
        issues=(953,),
        check=_check_enable_min_backwards,
    ),
)
