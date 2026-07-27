"""Helper functions."""

import datetime as dt
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd
from dateutil import parser
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.util import dt as dt_util

from .const import (
    BLANK_TIME,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_OF_WINDOW_POS,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_MANUAL_OVERRIDE_DURATION_MODE,
    CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_MOTION_MEDIA_PLAYERS,
    CONF_MOTION_SENSORS,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_TILT_ONLY,
    DEFAULT_MANUAL_OVERRIDE_DURATION_MODE,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
    TIME_STRING_RE,
    TriageCode,
)
from .templates import is_template_string

if TYPE_CHECKING:
    from homeassistant.core import State

    # TYPE_CHECKING only: managers.time_window imports from this module at
    # runtime, so a real import here is a hard circular import.
    from .managers.time_window import TimeWindowManager
    from .sun import SunData

_LOGGER = logging.getLogger(__name__)

# Entity states that mean "no usable value" — used by the safe-read helpers
# below so the same set is checked everywhere instead of inline literals.
_INVALID_STATES: frozenset[str] = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})


def usable_coordinator(entry: ConfigEntry) -> Any:
    """Return an entry's coordinator, or ``None`` when it is not usable yet.

    Single source of truth for "may I write to this entry's coordinator?", used
    by both ``services.loaded_coordinators`` (walks every ACP entry) and
    ``GroupCoordinator.resolved_members`` (walks one group's roster).

    Two things have to hold. The entry must have reached ``LOADED``: HA assigns
    ``runtime_data`` *before* forwarding platform setup, so between those points
    a reloading entry already exposes a freshly-built coordinator whose switch
    entities have not been added yet — a toggle written onto it is silently
    undone moments later when those switches restore their previous state
    (issue #1063). And ``runtime_data`` must actually hold something: virtual
    entries (e.g. the Building Profile, ``controls_cover == False``) reach
    ``LOADED`` without building a coordinator at all. ``getattr`` is robust
    whether the running HA leaves ``runtime_data`` unset or defaults it to
    ``None``.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return getattr(entry, "runtime_data", None)


def restored_bool(last_state: "State | None", default: bool) -> bool:
    """Read a restored switch state, ignoring anything that is not on/off.

    An entity that was ``unavailable`` or ``unknown`` when HA last snapshotted
    state carries no information about what the user wanted, so it must not be
    read as ``off``. Any raise inside a coordinator update flips
    ``last_update_success``, which renders every ACP entity ``unavailable``
    (``AdaptiveCoverBaseEntity.available``), and the restore store snapshots on
    a timer — so an unclean shutdown during that window persists it. Folding
    that into ``off`` turns a transient error into a silent, sticky "somebody
    switched this off": the restored ``off`` is what the next shutdown records.

    Shared by the per-cover switches (where the restored value is written
    straight onto the coordinator, so a wrong read disables the thing) and the
    group's bulk switches (issue #1063).
    """
    if last_state is None or last_state.state not in (STATE_ON, STATE_OFF):
        return default
    return last_state.state == STATE_ON


def climate_mode_configured(options: Mapping) -> bool:
    """Whether this entry is set up for climate mode at all.

    Single source of truth for "may climate mode be switched on here?". The
    pipeline gates on the runtime ``switch_mode`` toggle rather than on this
    option, so an unconfigured entry really would start acting on climate if
    something set the toggle — but it exposes no Climate Mode switch to persist
    that, and its coordinator re-seeds ``switch_mode`` from this option at every
    startup. Both the switch platform (which decides whether to create the
    entity) and the group's bulk control (which decides whom to command) read
    this so the two agree on who can hold the state.
    """
    return bool(options.get(CONF_CLIMATE_MODE))


def motion_entities(options: Mapping) -> list[str]:
    """Return the combined motion sensor + media_player entity IDs.

    Single source of truth for "is motion configured?": any non-empty result
    means motion tracking is active. Media players count as occupancy under the
    same OR logic as binary sensors (see ``MotionManager.update_config``), so
    every "motion configured" gate must consider both lists, not sensors alone.
    """
    return list(options.get(CONF_MOTION_SENSORS, [])) + list(
        options.get(CONF_MOTION_MEDIA_PLAYERS, [])
    )


def manual_override_input_entities(options: Mapping) -> list[str]:
    """Return the input binary sensors that engage manual override (issue #688).

    Single source of truth for "is input-sensor override configured?": any
    non-empty result means an off→on edge on one of these entities engages
    manual override across every cover in the instance. Mirrors
    :func:`motion_entities` so the ``__init__`` subscription guard and any
    "configured?" checks stay consistent.
    """
    return list(options.get(CONF_MANUAL_OVERRIDE_INPUT_ENTITIES, []))


def has_configured_window_end(options: Mapping) -> bool:
    """Return True when a *real* operating-window end is configured.

    Single source of truth for "does this instance have a window end at all?",
    shared by the ``until_window_end`` manual-override deadline resolver and the
    configuration summary's ⚠️ warning so the two can never disagree about which
    configs have no anchor (issue #1044).

    ``BLANK_TIME`` (``"00:00:00"``) is the project's UNSET / all-day sentinel —
    a cleared TimeSelector writes it, ``adaptive_cover_pro.set_options``
    tolerates it, and legacy pre-#492 entries carry it. It is *truthy*, so a
    bare falsiness test reads it as a configured 22:00-style end; it also cannot
    be told apart from a deliberate midnight end once
    ``TimeWindowManager.end_time`` has normalised it (that property maps a
    static midnight onto **tomorrow's** midnight, a value that advances a day at
    every local midnight). So the sentinel must be recognised here, on the raw
    option values, exactly as ``config_flow`` and ``diagnostics.triage`` already
    do.
    """
    return any(
        options.get(key) not in (None, "", BLANK_TIME)
        for key in (CONF_END_TIME, CONF_END_ENTITY)
    )


def manual_hold_is_unanchored(options: Mapping) -> bool:
    """Return True when the configured manual-override hold has no boundary.

    Single source of truth for "does this config's hold fall back to the numeric
    ``manual_override_duration``?", shared by the configuration summary's ⚠️ and
    the Building Profile overview's comparison table so the two surfaces can
    never describe the same config differently (issue #1051).

    Today that is exactly ``until_window_end`` with no operating-window end:
    :func:`resolve_override_deadline` finds no anchor and the caller falls back
    to the duration. A future mode that also needs an anchor belongs **here**,
    not in either surface's own render — two hand-rolled copies are how the
    overview came to print a boundary the hold never reaches.
    """
    mode = (
        options.get(CONF_MANUAL_OVERRIDE_DURATION_MODE)
        or DEFAULT_MANUAL_OVERRIDE_DURATION_MODE
    )
    return mode == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END and (
        not has_configured_window_end(options)
    )


def normalize_time_string(value: Any) -> Any:
    """Return *value* as a canonical ``HH:MM:SS`` string when it parses as a time.

    HA's ``TimeSelector`` validates a submission via ``dt_util.parse_time`` but
    stores it **unnormalized**, so a non-frontend flow client (websocket/REST
    flow API, a custom card) can persist ``"00:00"``, ``"7:30"`` or
    ``"٠٧:٣٠:٠٠"`` verbatim. None of those equal ``BLANK_TIME``, so every
    literal sentinel comparison downstream reads them as a configured window
    end while ``TimeWindowManager`` resolves them to tomorrow's midnight —
    issue #1049.

    Callers that have no one to send a bad value back to normalize with this
    and keep going — the options flow (the user picked a real time, just in a
    shape the picker never emits) and ``import_config`` (an export file predates
    the validation). ``set_options`` is the exception: it rejects outright,
    because its caller wrote the patch by hand and can fix it. The full
    per-path table lives at ``const.TIME_STRING_RE``.

    Values already canonical, and values no parser can rescue, are returned
    unchanged — inventing a time for the latter would mask the problem. Callers
    must therefore re-check the result against ``TIME_STRING_RE`` to tell
    "canonicalised" from "could not be rescued"; what they do with the latter
    differs (import errors, the migration drops the key).
    """
    if not isinstance(value, str) or TIME_STRING_RE.match(value):
        return value
    parsed = dt_util.parse_time(value)
    if parsed is None:
        return value
    return parsed.strftime("%H:%M:%S")


def custom_position_slot_sensors(
    options: Mapping, slot_keys: Mapping[str, str]
) -> list[str]:
    """Return a custom-position slot's trigger sensors.

    The new ``sensors`` list key wins whenever present (issue #563); otherwise
    the legacy single-sensor key is wrapped, so entries never saved through the
    multi-sensor UI keep working — including after a rollback-then-upgrade
    cycle where only the legacy key was edited.
    """
    sensors = options.get(slot_keys["sensors"])
    if sensors is not None:
        return [s for s in sensors if s]
    legacy = options.get(slot_keys["sensor"])
    return [legacy] if legacy else []


def copy_legacy_slot_sensors_to_list(options: dict) -> bool:
    """Promote each slot's legacy single-sensor key into the new list key.

    Called by the v3.2 → v3.3 migration (issue #563). For every custom-position
    slot where the new ``sensors`` list key is absent AND the legacy ``sensor``
    key holds a non-empty value, the legacy value is wrapped in a one-element
    list and written under the ``sensors`` key. The legacy key is left intact
    (additive / rollback-safe: same invariant as the v3.2 migration). Slots
    whose list key already exists, or that have no legacy sensor configured,
    are skipped. Returns ``True`` when at least one slot was updated.
    """
    changed = False
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        if slot_keys["sensors"] in options:
            continue
        legacy = options.get(slot_keys["sensor"])
        if legacy:
            options[slot_keys["sensors"]] = [legacy]
            changed = True
    return changed


def mirror_legacy_slot_sensor_keys(options: dict) -> None:
    """Mirror each slot's first sensor into the legacy single-sensor key.

    Called after every save path that writes the ``sensors`` list (config
    flow, options flow, ``set_custom_position`` service) so a rollback to the
    previous integration version still finds a working single-sensor config
    (issue #563). Slots whose list key is absent are left untouched — their
    legacy key is still the live source via the read fallback.
    """
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        if slot_keys["sensors"] not in options:
            continue
        sensors = options[slot_keys["sensors"]] or []
        if sensors:
            options[slot_keys["sensor"]] = sensors[0]
        elif options.get(slot_keys["sensor"]):
            # Slot cleared: null the stale mirror so neither old nor new code
            # resurrects it. Never-configured slots get no key at all.
            options[slot_keys["sensor"]] = None


# Sub-keys whose presence gives a slot something to contribute on its own,
# even without a `position` claim (issue #943). A trigger + "minimum tilt 50%"
# is a complete slot: it constrains the axis and lets the pipeline resolve the
# rest. Single source of truth for the claim vocabulary.
CUSTOM_POSITION_CLAIM_KEYS: tuple[str, ...] = (
    "position",
    "position_max",
    "tilt_min",
    "tilt_max",
)


def custom_position_slot_configured(
    options: Mapping, slot_keys: Mapping[str, str]
) -> bool:
    """Return True when a custom-position slot is fully configured.

    Single source of truth for the "slot participates" gate: a slot needs a
    trigger (at least one sensor, or a condition template) and at least one
    claim on an axis — a `position`, or one of the axis constraints added by
    issue #943. A trigger alone still contributes nothing.
    """
    has_trigger = bool(
        custom_position_slot_sensors(options, slot_keys)
    ) or is_template_string(options.get(slot_keys["template"]))
    has_claim = any(
        options.get(slot_keys[sub]) is not None for sub in CUSTOM_POSITION_CLAIM_KEYS
    )
    return has_trigger and has_claim


def custom_position_slot_delivers_fixed_position(
    options: Mapping, slot_keys: Mapping[str, str]
) -> bool:
    """Return True when a slot would deliver an exact (FIXED) cover-position claim.

    Single source of truth for "does this custom-position slot pin the position
    axis to a stored value?", shared by the pipeline handler's position-axis
    determination and the B1 position-envelope health check (issue #975). A slot
    pins an exact position only when it is neither ``use_my`` (routes to the
    hardware My preset, ignoring the stored position) nor ``tilt_only`` (fixes the
    slat angle and lets solar drive the carriage) and its position mode resolves
    to ``FIXED``. Every other mode — a floor (``min_mode``), a ceiling/range, or
    no position claim — composes as a constraint that the min/max envelope clamps,
    so it can never contradict the envelope with an out-of-range exact value.

    Reuses :attr:`CustomPositionSensorState.position_mode` — the same derivation
    the handler and axis-constraint composer use — so B1 and the pipeline agree.
    """
    from .const import AxisConstraintMode
    from .pipeline.types import CustomPositionSensorState

    if bool(options.get(slot_keys["use_my"], False)):
        return False
    raw_pos = options.get(slot_keys["position"])
    raw_pos_max = options.get(slot_keys["position_max"])
    state = CustomPositionSensorState(
        entity_ids=(),
        is_on=False,
        position=int(raw_pos) if raw_pos is not None else None,
        priority=0,
        min_mode=bool(options.get(slot_keys["min_mode"], False)),
        use_my=False,
        tilt_only=bool(
            options.get(slot_keys["tilt_only"], DEFAULT_CUSTOM_POSITION_TILT_ONLY)
        ),
        position_max=int(raw_pos_max) if raw_pos_max is not None else None,
    )
    return state.position_mode is AxisConstraintMode.FIXED


def custom_position_slot_name(
    options: Mapping, slot_keys: Mapping[str, str]
) -> str | None:
    """Return a custom-position slot's user-configured label, or None.

    Single source of truth for reading ``custom_position_name_N`` (issue
    #867): an empty string (a cleared text box) and an absent key both
    normalize to ``None`` so every consumer treats "unset" the same way.
    """
    return options.get(slot_keys["name"]) or None


def get_safe_state(hass: HomeAssistant, entity_id: str):
    """Get a safe state value if not available."""
    state = hass.states.get(entity_id)
    if not state or state.state in _INVALID_STATES:
        return None
    return state.state


def state_attr(hass: HomeAssistant, entity_id: str, attribute: str):
    """Return an entity attribute value, or None if the entity or attribute is absent.

    Replaces homeassistant.helpers.template.state_attr, which was removed from
    the public Python API in HA 2026.5. Same contract: None when the entity is
    unknown or the attribute is absent, otherwise the raw value.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get(attribute)


def get_domain(entity: str):
    """Get domain of entity."""
    if entity is not None:
        domain, object_id = split_entity_id(entity)
        return domain


def is_entity_active(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return True when an entity reports an active/present state.

    Domain-aware evaluation:
      - device_tracker / person → state == "home"
      - zone                    → occupant count > 0
      - binary_sensor / input_boolean / switch / schedule → state == "on"
      - media_player → any state but off / unavailable / unknown (fail-closed)
      - None / missing / unknown / unavailable / other domains → True (fail-open)
    """
    if entity_id is None:
        return True
    domain = get_domain(entity_id)
    if domain == "media_player":
        # Occupancy via playback: a missing/unavailable/unknown/off player is
        # NOT occupancy (fail-closed) — read state directly to bypass the
        # fail-open None handling below.
        state = hass.states.get(entity_id)
        return bool(
            state and state.state not in _INVALID_STATES and state.state != "off"
        )
    raw = get_safe_state(hass, entity_id)
    if raw is None:
        return True
    if domain in ("device_tracker", "person"):
        return raw == "home"
    if domain == "zone":
        try:
            return int(raw) > 0
        except (TypeError, ValueError):
            return False
    if domain in ("binary_sensor", "input_boolean", "switch", "schedule"):
        return raw == "on"
    return True


def get_timedelta_str(string: str):
    """Convert string to timedelta."""
    if string is not None:
        return pd.to_timedelta(string)


def get_datetime_from_str(string: str):
    """Convert a datetime string to a naive-local datetime.

    Tz-aware inputs (e.g., sun-sensor UTC values like "2026-04-18T04:46:00+00:00")
    are converted to HA's configured local timezone and then stripped of tzinfo so
    downstream naive comparisons work correctly in non-UTC installs.
    Tz-naive inputs (e.g., static "06:30") are returned unchanged.
    """
    if string is None:
        return None
    parsed = parser.parse(string)
    if parsed.tzinfo is not None:
        parsed = dt_util.as_local(parsed).replace(tzinfo=None)
    return parsed


def get_last_updated(entity_id: str, hass: HomeAssistant):
    """Get last updated attribute from entity."""
    if entity_id is not None:
        if hass.states.get(entity_id):
            return hass.states.get(entity_id).last_updated


def check_time_passed(time: dt.datetime):
    """Check if time is passed for datetime."""
    now = dt.datetime.now()
    return now >= time


def dt_check_time_passed(time: dt.datetime):
    """Check if time is passed for UTC datetime."""
    now = dt.datetime.now(dt.UTC)
    return now >= time


def check_cover_features(hass: HomeAssistant, entity_id: str) -> dict[str, bool] | None:
    """Check which features a cover entity supports.

    Returns:
        Dict of capabilities if entity is ready, None if not yet initialized

    Dict keys:
    - has_set_position: bool
    - has_set_tilt_position: bool
    - has_open: bool
    - has_close: bool

    """
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

    state = hass.states.get(entity_id)
    if not state:
        _LOGGER.debug("Cover %s state not available yet", entity_id)
        return None

    # STATE_UNAVAILABLE means the entity has no data at all — skip it.
    # STATE_UNKNOWN is safe to proceed: Z-Wave covers often report unknown
    # positional state permanently but still populate supported_features.
    if state.state == STATE_UNAVAILABLE:
        _LOGGER.debug("Cover %s unavailable, skipping capability check", entity_id)
        return None

    if state.state == STATE_UNKNOWN and "supported_features" not in state.attributes:
        _LOGGER.debug("Cover %s unknown state with no features, skipping", entity_id)
        return None

    if state.state == STATE_UNKNOWN and "supported_features" in state.attributes:
        _LOGGER.debug(
            "Cover %s: unknown state but supported_features=%s present — proceeding with capability check",
            entity_id,
            state.attributes.get("supported_features"),
        )

    # Check if supported_features attribute exists
    if "supported_features" not in state.attributes:
        _LOGGER.debug(
            "Cover %s missing supported_features attribute, assuming position control",
            entity_id,
        )
        # Return optimistic defaults for entities without explicit capabilities
        return {
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        }

    supported_features = state.attributes.get("supported_features", 0)

    _LOGGER.debug(
        "Cover %s supported_features: %s (binary: %s)",
        entity_id,
        supported_features,
        bin(supported_features),
    )

    return {
        "has_set_position": bool(supported_features & CoverEntityFeature.SET_POSITION),
        "has_set_tilt_position": bool(
            supported_features & CoverEntityFeature.SET_TILT_POSITION
        ),
        "has_open": bool(supported_features & CoverEntityFeature.OPEN),
        "has_close": bool(supported_features & CoverEntityFeature.CLOSE),
        "has_stop": bool(supported_features & CoverEntityFeature.STOP),
    }


def check_cover_capabilities(
    config: dict,
    sensor_type: str | None,
    hass: HomeAssistant | None,
) -> tuple[dict[str, dict[str, bool] | None], list[str]]:
    """Inspect bound cover entities and return capabilities + warning lines.

    Returns:
        cap_map:  entity_id → feature dict (None if entity unavailable)
        warnings: list of ⚠️ strings — per-entity and cross-entity issues

    Moved here from ``config_flow.py`` (issue #1059) so ``diagnostics/resolve.py``
    can import it at module level instead of reaching into ``config_flow`` —
    ``config_flow`` already imports ``diagnostics.triage``, so the old
    ``diagnostics -> config_flow`` edge made the two modules mutually dependent
    (working only because both directions deferred the import). The
    ``cover_types`` import below stays function-local: ``cover_types.base``
    imports THIS module at module level, so importing ``cover_types`` back from
    here at module scope would be circular.

    """
    entities: list[str] = config.get(CONF_ENTITIES) or []
    if hass is None or not entities:
        return {}, []

    warnings: list[str] = []

    from .cover_types import get_policy
    from .cover_types.base import CAP_HAS_SET_POSITION, caps_get
    from .diagnostics.triage import RuleInput, run_triage
    from .reason_i18n import render
    from .troubleshoot_i18n import load_troubleshoot_labels

    # The "not ready (unavailable)" line (rule 8a) is rendered from the shared
    # triage engine so the summary and troubleshoot surfaces share one rule and
    # one string (issue #970). Build the capability map up front, then index the
    # COVER_NOT_READY findings by entity so the per-entity warning loop below
    # keeps its exact ordering (and its interleaving with the open/close-only and
    # assumed_state lines). Rendered in English to match the legacy raw f-string,
    # which was hardcoded English regardless of the summary language.
    cap_map: dict[str, dict[str, bool] | None] = {
        eid: check_cover_features(hass, eid) for eid in entities
    }
    _not_ready = {
        f.reason.params["eid"]: f
        for f in run_triage({"capabilities": cap_map}, only=RuleInput.CONFIG)
        if f.reason.code == TriageCode.COVER_NOT_READY
    }
    _triage_labels = load_troubleshoot_labels("en")

    for eid in entities:
        caps = cap_map[eid]
        if caps is None:
            warnings.append(render(_not_ready[eid].reason, _triage_labels))
        else:
            if not caps_get(caps, CAP_HAS_SET_POSITION):
                warnings.append(
                    f"⚠️ {eid} is open/close-only — will be driven via "
                    "threshold compare, not set_position."
                )
            state = hass.states.get(eid)
            if state and state.attributes.get("assumed_state"):
                warnings.append(
                    f"⚠️ {eid} has assumed_state — real position cannot be "
                    "read back, which may affect position verification and delta-bypass."
                )

    known: dict[str, dict[str, bool]] = {
        eid: caps for eid, caps in cap_map.items() if caps is not None
    }

    if known:
        has_pos = {
            eid for eid, caps in known.items() if caps_get(caps, CAP_HAS_SET_POSITION)
        }
        no_pos = {
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_POSITION)
        }

        if has_pos and no_pos:
            warnings.append(
                "⚠️ Mixed capabilities: some covers support set_position, "
                "others are open/close-only — they will be driven differently."
            )

        if sensor_type is not None:
            warnings.extend(
                get_policy(sensor_type).capability_warnings_for_options(known, config)
            )

        min_pos_val = config.get(CONF_MIN_POSITION)
        max_pos_val = config.get(CONF_MAX_POSITION)
        enable_min_val = config.get(CONF_ENABLE_MIN_POSITION)
        enable_max_val = config.get(CONF_ENABLE_MAX_POSITION)
        limits_in_use = (
            (min_pos_val is not None and min_pos_val != 0)
            or (max_pos_val is not None and max_pos_val != 100)
            or enable_min_val
            or enable_max_val
        )
        oc_only = [eid for eid in no_pos if eid in known]
        if limits_in_use and oc_only:
            oc_str = ", ".join(oc_only)
            warnings.append(
                f"⚠️ Position limits are configured but {oc_str} "
                "is open/close-only — limits will be ignored on that cover."
            )

    return cap_map, warnings


def _eval_time_to_utc_naive(eval_time: dt.datetime) -> dt.datetime:
    """Normalize an evaluation time to naive-UTC for sunset/sunrise comparison.

    Accepts either a tz-aware datetime (e.g. a sample time from
    ``SunData.times``, which is tz-aware local) — converted to UTC — or a
    naive-local wall-clock value, interpreted via :func:`_local_naive_to_utc_naive`.
    Mirrors how ``compute_effective_default`` normalizes its ``now`` reference.
    """
    if eval_time.tzinfo is not None:
        return dt_util.as_utc(eval_time).replace(tzinfo=None)
    return _local_naive_to_utc_naive(eval_time)


def _local_naive_to_utc_naive(local_naive: dt.datetime) -> dt.datetime:
    """Convert a naive-local wall-clock datetime to a naive-UTC datetime.

    Interprets *local_naive* as a wall-clock time in HA's configured local
    timezone (``dt_util.DEFAULT_TIME_ZONE``), converts it to UTC, and strips
    tzinfo so the result is comparable to other naive-UTC values.

    This is the single conversion point for entity-derived sunset/sunrise
    boundaries inside ``compute_effective_default``.  DST transitions are
    handled correctly because ``dt_util.as_local`` / ``dt_util.as_utc`` use
    the HA-configured zoneinfo database.
    """
    aware_local = local_naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(aware_local).replace(tzinfo=None)


def _resolve_boundary(
    local_naive: dt.datetime | None,
    astral: Callable[[], dt.datetime],
    offset_minutes: int,
) -> dt.datetime:
    """Resolve ONE sunset/sunrise occurrence to a naive-UTC instant.

    The integration's boundary rule in one place: a configured override entity
    (issues #411/#415) fully replaces astral, then the configured minute offset
    applies on top either way.
    """
    base = (
        _local_naive_to_utc_naive(local_naive)
        if local_naive is not None
        else astral().replace(tzinfo=None)
    )
    return base + timedelta(minutes=offset_minutes)


@dataclass(frozen=True, slots=True)
class SunBoundaries:
    """The integration's resolved sunset/sunrise instants, in naive UTC.

    ``sunset`` / ``sunrise`` are today's occurrences with their configured
    minute offsets already applied, so callers compare against them directly.

    ``next_sunset`` / ``next_sunrise`` are the following day's occurrences —
    the roll-forward candidates a deadline anchored after today's event needs
    (issue #1044). Their *astral* form resolves on first read: the day/night
    position boundary (:func:`compute_effective_default`) never asks for
    tomorrow, and it runs once per forecast sample, so eagerly paying two extra
    astral walks per call would be pure waste in the hot path.
    """

    sunset: dt.datetime
    sunrise: dt.datetime
    _next_sunset: Callable[[], dt.datetime]
    _next_sunrise: Callable[[], dt.datetime]

    @property
    def next_sunset(self) -> dt.datetime:
        """Tomorrow's sunset boundary (naive UTC, offset applied)."""
        return self._next_sunset()

    @property
    def next_sunrise(self) -> dt.datetime:
        """Tomorrow's sunrise boundary (naive UTC, offset applied)."""
        return self._next_sunrise()


def _next_boundary_resolver(
    local_naive: dt.datetime | None,
    astral_next: Callable[[], dt.datetime],
    offset_minutes: int,
) -> Callable[[], dt.datetime]:
    """Return a zero-arg resolver for tomorrow's occurrence of one boundary.

    An entity-derived boundary is rolled and resolved immediately: rolling
    happens in *local* wall-clock space (``+1 day``) with the UTC conversion
    applied afterwards, so a DST transition between the two days shortens or
    lengthens the real gap correctly instead of drifting an hour. Resolving it
    up front is free — it is pure timezone arithmetic — and keeps every field
    of a :class:`SunBoundaries` consistent with the moment it was built.

    Only the astral branch is deferred; that walk is the expensive part.
    """
    if local_naive is not None:
        resolved = _resolve_boundary(
            local_naive + timedelta(days=1), astral_next, offset_minutes
        )
        return lambda: resolved
    return partial(_resolve_boundary, None, astral_next, offset_minutes)


class SunBoundaryOptions(NamedTuple):
    """The four HA-side inputs that define what "sunset"/"sunrise" mean here.

    Produced by :func:`_read_sun_boundary_options`
    and consumed by both the day/night position boundary and the manual-override
    sun deadline. Field names match :func:`resolve_sun_boundaries`'
    keyword arguments.
    """

    sunset_time: dt.datetime | None
    sunrise_time: dt.datetime | None
    sunset_off: int
    sunrise_off: int


def _read_time_entity(hass: HomeAssistant, entity_id: str | None) -> dt.datetime | None:
    """Read an entity whose state is an ISO-8601 datetime.

    Returns naive-local datetime on success; None if entity_id is None,
    the entity is unavailable, or the state cannot be parsed.

    The entity's *time-of-day* is re-anchored onto today's local date and the
    original date component is discarded. This makes a "next-event" sensor
    (e.g. ``sensor.sun_next_setting``, which rolls over to *tomorrow's*
    setting the instant today's sun sets) behave like a fixed daily wall-clock
    time. ``compute_effective_default`` already compares the boundary against
    *today's* clock, so a future-dated boundary would otherwise make the
    ``after_sunset`` comparison structurally unreachable (issue #531 follow-up).
    """
    if entity_id is None:
        return None
    raw = get_safe_state(hass, entity_id)
    if raw is None:
        return None
    try:
        parsed = get_datetime_from_str(raw)
        today_local = dt_util.now().date()
        return parsed.replace(
            year=today_local.year,
            month=today_local.month,
            day=today_local.day,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not parse time entity %s state %r as datetime",
            entity_id,
            raw,
        )
        return None


def resolve_sunset_offset(options: Mapping) -> int:
    """Return the configured sunset offset in minutes, 0 when unset.

    A stored ``None`` counts as unset — the key can exist with no value on an
    entry whose form field was never filled in.
    """
    return int(options.get(CONF_SUNSET_OFFSET) or 0)


def resolve_sunrise_offset(options: Mapping) -> int:
    """Return the configured sunrise offset in minutes.

    Falls back to :func:`resolve_sunset_offset` when unset, so an install that
    only ever set one offset gets a symmetric night window. An explicit ``0`` is
    a real choice and does *not* fall back; a stored ``None`` does, matching
    :func:`resolve_sunset_offset`.

    The single source of truth for this rule, which used to be spelled out
    independently in five places — one of them defaulting to ``0`` instead of to
    the sunset offset, so the Configuration Summary described a night window the
    runtime never used (issue #1050, CODING_GUIDELINES.md § no-duplication).
    """
    raw = options.get(CONF_SUNRISE_OFFSET)
    return resolve_sunset_offset(options) if raw is None else int(raw)


def _read_sun_boundary_options(
    hass: HomeAssistant, options: Mapping
) -> SunBoundaryOptions:
    """Read the four HA-side inputs that define sunset/sunrise for this instance.

    The single source of truth for the *reads* feeding :func:`resolve_sun_boundaries`'
    arithmetic. Two direct callers: the day/night position boundary
    (:func:`_read_current_effective_default`) and the manual-override sun deadline
    (``_resolve_override_deadline``). Both consumers of the day/night boundary —
    the coordinator's ``_compute_current_effective_default`` and
    ``PipelineSnapshotBuilder.build``'s fallback branch — reach here indirectly
    through that first caller (issue #1055). So a later fifth input lands in one
    place and no call site can disagree about what "sunset" means
    (CODING_GUIDELINES.md § no-duplication).

    The offset arithmetic itself lives in :func:`resolve_sunset_offset` /
    :func:`resolve_sunrise_offset`, which the hass-less consumers
    (``CoverConfig.from_options``, the export service, the config-flow summary)
    share — see issue #1050.
    """
    return SunBoundaryOptions(
        sunset_time=_read_time_entity(hass, options.get(CONF_SUNSET_TIME_ENTITY)),
        sunrise_time=_read_time_entity(hass, options.get(CONF_SUNRISE_TIME_ENTITY)),
        sunset_off=resolve_sunset_offset(options),
        sunrise_off=resolve_sunrise_offset(options),
    )


def resolve_sun_boundaries(
    sun_data: "SunData",
    *,
    sunset_time: dt.datetime | None = None,
    sunrise_time: dt.datetime | None = None,
    sunset_off: int = 0,
    sunrise_off: int = 0,
) -> SunBoundaries:
    """Return the integration's definition of sunset/sunrise as naive-UTC instants.

    Single source of truth for what "sunset" and "sunrise" *mean* here:
    configured override entities win over astral, then the configured minute
    offsets are added. ``compute_effective_default`` (the day/night position
    boundary) and the manual-override sun deadline resolver both delegate here
    so the two can never drift apart.

    Args:
        sun_data: ``SunData`` providing the astral fallbacks.
        sunset_time: Optional naive-local sunset override (entity-derived).
        sunrise_time: Optional naive-local sunrise override (entity-derived).
        sunset_off: Minutes added to the sunset boundary.
        sunrise_off: Minutes added to the sunrise boundary.

    """
    return SunBoundaries(
        sunset=_resolve_boundary(sunset_time, sun_data.sunset, sunset_off),
        sunrise=_resolve_boundary(sunrise_time, sun_data.sunrise, sunrise_off),
        _next_sunset=_next_boundary_resolver(
            sunset_time, sun_data.next_sunset, sunset_off
        ),
        _next_sunrise=_next_boundary_resolver(
            sunrise_time, sun_data.next_sunrise, sunrise_off
        ),
    )


def _sun_deadline_candidates(
    mode: str, boundaries: SunBoundaries
) -> tuple[dt.datetime, ...]:
    """Return the boundary instants a sun-anchored mode may expire on.

    An empty tuple means the mode is not sun-anchored (``fixed``,
    ``until_window_end``, or an unrecognised value), so the caller resolves it
    another way or falls back to the numeric duration.
    """
    if mode == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET:
        return (boundaries.sunset, boundaries.next_sunset)
    if mode == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE:
        return (boundaries.sunrise, boundaries.next_sunrise)
    if mode == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT:
        return (
            boundaries.sunset,
            boundaries.next_sunset,
            boundaries.sunrise,
            boundaries.next_sunrise,
        )
    return ()


# The operating window's end is a local wall-clock time normalized onto today
# (or tomorrow, for a midnight end), so at most two rolls are ever needed to
# clear an anchor. Three bounds the loop with room to spare and guarantees it
# terminates whatever a caller passes in.
_MAX_WINDOW_END_ROLL_DAYS = 3


def _next_window_end_after(
    anchor: dt.datetime, window_end_local_naive: dt.datetime | None
) -> dt.datetime | None:
    """Return the first occurrence of the window end strictly after *anchor*.

    Rolls in *local* wall-clock space so a DST transition between the anchor
    day and the deadline day changes the real elapsed hours correctly. Returns
    ``None`` when no window end is configured at all.
    """
    if window_end_local_naive is None:
        return None
    local = window_end_local_naive
    for _ in range(_MAX_WINDOW_END_ROLL_DAYS):
        candidate = _local_naive_to_utc_naive(local)
        if candidate > anchor:
            return candidate
        local += timedelta(days=1)
    return None


def resolve_override_deadline(
    mode: str,
    anchor: dt.datetime,
    *,
    boundaries: SunBoundaries | None = None,
    window_end_local_naive: dt.datetime | None = None,
) -> dt.datetime | None:
    """Resolve a manual-override deadline for *mode*, anchored on the override start.

    The deadline is the **first occurrence of the mode's event strictly after
    the anchor**. Strictly, because an anchor landing exactly on the boundary
    would otherwise produce a zero-length hold that expires on the very cycle
    that engaged it.

    The anchor is the moment the user touched the cover — never "now". This
    runs on every coordinator cycle, so anchoring on now would make the
    deadline recede forever and the override would never expire.

    ``None`` means "this mode has no resolvable deadline here" — ``fixed``, an
    unrecognised mode, a sun mode with no ``SunData`` yet (before the first
    coordinator cycle), or ``until_window_end`` with no window configured. Every
    such case falls back to the numeric ``manual_override_duration``.

    Args:
        mode: One of ``MANUAL_OVERRIDE_DURATION_MODES``.
        anchor: The override start, as a naive-UTC datetime.
        boundaries: Resolved sun boundaries, or ``None`` when unavailable.
        window_end_local_naive: The operating window's resolved end as a
            naive-local wall-clock datetime, or ``None`` when unconfigured.

    """
    if mode == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END:
        return _next_window_end_after(anchor, window_end_local_naive)
    if boundaries is None:
        return None
    upcoming = [c for c in _sun_deadline_candidates(mode, boundaries) if c > anchor]
    return min(upcoming) if upcoming else None


def compute_effective_default(
    h_def: int,
    sunset_pos: int | None,
    sun_data: "SunData",
    sunset_off: int,
    sunrise_off: int,
    *,
    sunset_time: dt.datetime | None = None,
    sunrise_time: dt.datetime | None = None,
    window_explicitly_started: bool = False,
    eval_time: dt.datetime | None = None,
    daytime_gate: bool | None = None,
    end_of_window_pos: int | None = None,
    end_of_window_active: bool = False,
) -> tuple[int, bool]:
    """Return the effective default cover position based on astronomical sunset/sunrise.

    If a ``sunset_pos`` is configured and the current wall-clock time falls
    within the astronomical sunset/sunrise window (i.e. after
    ``sunset + sunset_off`` minutes **or** before
    ``sunrise + sunrise_off`` minutes) the sunset position is active.

    Unlike the legacy timer-based approach this function is stateless and
    re-evaluated every coordinator update cycle, so a Home Assistant restart
    during the sunset window immediately returns the correct position without
    any timer re-scheduling.

    Args:
        h_def:      Configured default position (0–100 %).
        sunset_pos: Configured sunset/night position, or ``None`` when not set.
        sun_data:   ``SunData`` instance providing today's sunset/sunrise times.
        sunset_off: Minutes *added* to astronomical sunset before the window opens.
        sunrise_off: Minutes *added* to astronomical sunrise before the window closes.
        sunset_time: Optional override for the sunset boundary (naive-local datetime).
            When provided, replaces the astral-computed sunset. ``sunset_off`` still
            applies on top. Falls back to astral when ``None``.
        sunrise_time: Optional override for the sunrise boundary (naive-local datetime).
            When provided, replaces the astral-computed sunrise. ``sunrise_off`` still
            applies on top. Falls back to astral when ``None``.
        window_explicitly_started: When ``True``, a *real* (non-blank) start_time
            or start entity is configured and has already passed. In that case the
            ``before_sunrise`` branch is suppressed so that a start_time earlier than
            astronomical sunrise (a valid config on short winter days, issue #438)
            does not incorrectly apply the sunset/night position once the user's
            window opens. This is distinct from a window that is merely "open"
            because no start time is set: the blank sentinel ``BLANK_TIME``
            ("00:00:00") must NOT suppress the night position (issue #492), so it
            maps to ``False`` here even though the active-window check treats blank
            as "no start restriction". Defaults to ``False`` for call sites without
            start_time context.
        eval_time: Optional time at which to evaluate the sunset/sunrise window.
            When provided (tz-aware or naive-local), it replaces wall-clock now —
            this lets the forecast project the effective default at each future
            sample time instead of "now". ``None`` (default) preserves the live
            behavior of evaluating against the current moment.
        daytime_gate: Optional override from a configured "daytime gate" (issue
            #632). ``None`` (default, no gate) keeps the astronomical decision —
            zero regression. ``True`` (gate says daytime) forces
            ``is_sunset_active=False`` regardless of astral times (the
            bright-evening / pre-sunrise-dark cases). ``False`` (gate says dark)
            forces ``is_sunset_active=True``. When set, the gate OWNS the boundary
            and short-circuits the astral ``after_sunset``/``before_sunrise`` math
            and the ``window_explicitly_started`` branch.
        end_of_window_pos: Optional end-of-window position (0–100, issue #625) or
            ``None`` (default, disabled — zero regression). When set AND
            ``end_of_window_active`` is ``True`` it overrides the astronomical
            effective default with a TWO-PHASE astral handoff:
              - When ``sunset_pos`` is also set: the end-of-window position holds
                from window-end UNTIL astral sunset (phase 1, gated on
                ``not after_sunset``), then the astral ``sunset_pos`` takes over
                (phase 2, automatic fall-through).
              - When ``sunset_pos`` is ``None`` (no handoff target): the
                end-of-window position persists the whole evening (a top-of-body
                short-circuit that outranks even a configured ``daytime_gate``).
            A configured ``daytime_gate`` still OWNS the boundary in the
            ``sunset_pos`` set case (phase 1 does not override it).
        end_of_window_active: ``True`` when the operating window is clock-closed
            (now is at/after the configured/entity end time). Only meaningful when
            ``end_of_window_pos`` is set. Defaults to ``False``.

    Returns:
        A ``(effective_default, is_sunset_active)`` tuple where
        ``is_sunset_active`` is ``True`` when the sunset position is in effect.

    """
    # End-of-window with no astral sunset handoff target (issue #625): the
    # end-of-window position must persist the whole evening. This outranks even a
    # configured daytime_gate, and must precede the ``sunset_pos is None`` guard
    # (which would otherwise return h_def before the eow check is reached).
    if end_of_window_active and end_of_window_pos is not None and sunset_pos is None:
        return int(end_of_window_pos), True

    if sunset_pos is None:
        return h_def, False

    # A configured daytime gate OWNS the day/night boundary: it fully replaces the
    # astronomical sunset/sunrise calc below (issue #632). Astral is the fallback
    # only when the gate is unconfigured (``daytime_gate is None``).
    if daytime_gate is not None:
        is_sunset_active = daytime_gate is False
        effective = int(sunset_pos) if is_sunset_active else int(h_def)
        return effective, is_sunset_active

    boundaries = resolve_sun_boundaries(
        sun_data,
        sunset_time=sunset_time,
        sunrise_time=sunrise_time,
        sunset_off=sunset_off,
        sunrise_off=sunrise_off,
    )
    now_naive = (
        _eval_time_to_utc_naive(eval_time)
        if eval_time is not None
        else dt.datetime.now(UTC).replace(tzinfo=None)
    )

    after_sunset = now_naive > boundaries.sunset
    before_sunrise = now_naive < boundaries.sunrise

    # End-of-window phase 1 (issue #625): once the operating window is
    # clock-closed, the end-of-window position holds from window-end UNTIL astral
    # sunset; then phase 2 (the astral sunset_pos branch below) takes over. Gated
    # on ``not after_sunset`` so it yields to astral at the handoff. The
    # daytime_gate branch above intentionally still owns the boundary here.
    if end_of_window_active and end_of_window_pos is not None and not after_sunset:
        return int(end_of_window_pos), True

    # Suppress before_sunrise only when the operational window has *explicitly*
    # started: a real start_time < astronomical_sunrise is a valid user config
    # (e.g. start at 08:00, sunrise at 08:15 in winter, issue #438) and once that
    # window opens nighttime rules end. A blank start_time (issue #492) does NOT
    # count as explicitly started, so the night position holds after midnight.
    is_sunset_active = after_sunset or (
        before_sunrise and not window_explicitly_started
    )

    effective = int(sunset_pos) if is_sunset_active else int(h_def)
    return effective, is_sunset_active


def _read_current_effective_default(
    hass: HomeAssistant,
    options: Mapping,
    sun_data: "SunData",
    time_mgr: "TimeWindowManager | None" = None,
) -> tuple[int, bool]:
    """Return ``(effective_pos, is_sunset_active)`` for the current moment.

    The single source of truth for *calling* :func:`compute_effective_default`
    with live state. Both consumers delegate here: the coordinator's
    ``_compute_current_effective_default`` (the update cycle and the window
    transitions) and ``PipelineSnapshotBuilder.build``'s fallback, which
    ``async_apply_user_position`` reaches when it assembles an ad-hoc snapshot
    outside the cycle.

    The builder used to re-derive the answer from a narrower input set, so four
    live window-state inputs — ``window_explicitly_started`` (#438/#492),
    ``daytime_gate`` (#632), and ``end_of_window_pos`` /
    ``end_of_window_active`` (#625) — silently took the formula's own defaults
    there and the two paths could disagree about the very same instant
    (issue #1055, CODING_GUIDELINES.md § no-duplication).

    Every ``time_mgr`` read below is a pure property read — nothing advances
    manager state, so the ad-hoc path calls this without perturbing the cycle.

    Args:
        hass: Used only for the boundary-entity reads in
            :func:`_read_sun_boundary_options`. An instance with neither time
            entity configured reads no state at all.
        options: The config-entry options mapping.
        sun_data: Already-resolved ``SunData``. Callers own how they get it —
            the coordinator falls back to ``get_blind_data`` when it has none
            in hand, which is coordinator business and stays there.
        time_mgr: The live ``TimeWindowManager``, or ``None``. ``None`` is the
            contract for a builder constructed without one (tests, legacy call
            sites): the three manager-derived kwargs degrade to exactly the
            defaults :func:`compute_effective_default` would have applied
            anyway. ``end_of_window_pos`` is options-derived, so it is still
            read — but every end-of-window branch in that function also
            requires ``end_of_window_active``, which is ``False`` here, so the
            value is inert rather than defaulted. Production always injects
            the live manager.

    """
    h_def = int(options.get(CONF_DEFAULT_HEIGHT, 0))
    sunset_pos_cfg = options.get(CONF_SUNSET_POS)
    bounds = _read_sun_boundary_options(hass, options)
    # A configured daytime gate (issue #632) OWNS the day/night boundary:
    # ``effective_daytime_gate`` is the single tri-state verdict to forward —
    # True=daytime→no sunset, False=dark→apply sunset position, None=defer to
    # the astronomical decision. None covers both an unconfigured gate and the
    # graceful fallback after every gate source has been indeterminate past the
    # grace window (issue #742).
    daytime_gate = time_mgr.effective_daytime_gate if time_mgr is not None else None
    window_started = (
        time_mgr.window_explicitly_started if time_mgr is not None else False
    )
    # End-of-window position (issue #625): an optional, clearable position
    # applied once the operating window is clock-closed. ``before_end_time``
    # is True all morning (end is later today), so the override only fires in
    # the evening — never before the start time. compute_effective_default
    # owns the two-phase astral handoff; here we only read the inputs.
    eow_pos = options.get(CONF_END_OF_WINDOW_POS)
    window_is_closed = (not time_mgr.before_end_time) if time_mgr is not None else False
    return compute_effective_default(
        h_def=h_def,
        sunset_pos=sunset_pos_cfg,
        sun_data=sun_data,
        sunset_off=bounds.sunset_off,
        sunrise_off=bounds.sunrise_off,
        sunset_time=bounds.sunset_time,
        sunrise_time=bounds.sunrise_time,
        window_explicitly_started=window_started,
        daytime_gate=daytime_gate,
        end_of_window_pos=eow_pos,
        end_of_window_active=window_is_closed,
    )


def should_use_tilt(is_tilt_cover: bool, caps) -> bool:
    """Return True if tilt services/attributes should be used for this cover.

    Activates when the cover is configured as tilt OR when the entity only
    supports tilt operations (has SET_TILT_POSITION but not SET_POSITION),
    regardless of config-level sensor_type.

    Args:
        is_tilt_cover: Whether the cover is configured as ``cover_tilt``.
        caps: Capability source — either a ``dict`` (from ``check_cover_features``)
              or a ``CoverCapabilities`` dataclass.

    """
    if is_tilt_cover:
        return True
    # Local import — cover_types/base.py imports from helpers, so a top-level
    # import here would cycle. The constants and accessor are cheap to fetch.
    from .cover_types.base import (
        CAP_HAS_SET_POSITION,
        CAP_HAS_SET_TILT_POSITION,
        caps_get,
    )

    has_position = caps_get(caps, CAP_HAS_SET_POSITION, default=True)
    has_tilt = caps_get(caps, CAP_HAS_SET_TILT_POSITION, default=False)
    return not has_position and has_tilt


def get_open_close_state(
    hass: HomeAssistant,
    entity_id: str,
    *,
    state_obj: "State | None" = None,
) -> int | None:
    """Map open/closed state to position value for open/close-only covers.

    When ``state_obj`` is supplied (typically the new_state from a state-changed
    event) it is used as the source of truth instead of the live registry value.
    This matters for manual-override detection on assumed-state covers: between
    the event firing and the queued handler running, ACP's reconciliation can
    counter-command the cover, flipping the live state back. Reading the
    event payload pins the comparison to the state that triggered detection.

    Returns:
    - 0 if closed
    - 100 if open
    - None if state is unknown/unavailable

    """
    state = state_obj if state_obj is not None else hass.states.get(entity_id)
    if not state or state.state in _INVALID_STATES:
        return None

    if state.state == "closed":
        return 0
    elif state.state == "open":
        return 100

    return None


def climate_mode_from_diagnostics(diagnostics: dict | None) -> str | None:
    """Map a coordinator's diagnostics payload to its climate-mode string.

    Single source for the ``summer_mode`` / ``winter_mode`` / ``intermediate``
    vocabulary — shared by the per-cover Climate Status sensor and the
    cover-group climate rollup (issue #790, Phase 3). ``None`` when climate
    mode is off or the diagnostics have not been built yet.
    """
    if diagnostics is None:
        return None
    data = diagnostics.get("climate_conditions")
    if data is None:
        return None
    if data.get("is_summer"):
        return "summer_mode"
    if data.get("is_winter"):
        return "winter_mode"
    return "intermediate"
