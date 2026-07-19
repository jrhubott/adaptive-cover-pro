"""Helper functions."""

import datetime as dt
import logging
from collections.abc import Mapping
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from dateutil import parser
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    CONF_MOTION_MEDIA_PLAYERS,
    CONF_MOTION_SENSORS,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_TILT_ONLY,
)
from .templates import is_template_string

if TYPE_CHECKING:
    from homeassistant.core import State

    from .sun import SunData

_LOGGER = logging.getLogger(__name__)

# Entity states that mean "no usable value" — used by the safe-read helpers
# below so the same set is checked everywhere instead of inline literals.
_INVALID_STATES: frozenset[str] = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})


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

    sunset = (
        _local_naive_to_utc_naive(sunset_time)
        if sunset_time is not None
        else sun_data.sunset().replace(tzinfo=None)
    )
    sunrise = (
        _local_naive_to_utc_naive(sunrise_time)
        if sunrise_time is not None
        else sun_data.sunrise().replace(tzinfo=None)
    )
    now_naive = (
        _eval_time_to_utc_naive(eval_time)
        if eval_time is not None
        else dt.datetime.now(UTC).replace(tzinfo=None)
    )

    after_sunset = now_naive > (sunset + timedelta(minutes=sunset_off))
    before_sunrise = now_naive < (sunrise + timedelta(minutes=sunrise_off))

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
