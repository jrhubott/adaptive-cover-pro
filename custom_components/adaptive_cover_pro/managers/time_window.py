"""Time window management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_context_adapter import ConfigContextAdapter

from ..const import (
    BLANK_TIME,
    DEFAULT_CONDITION_GATE_GRACE_SECONDS,
    DEFAULT_TEMPLATE_COMBINE_MODE,
)
from ..helpers import get_datetime_from_str, get_safe_state, local_now_naive
from ..templates import render_condition_or_none
from .common import EventRecorder
from .common.condition_gate import ConditionGate


def _bound_is_configured(entity: str | None, static_value: str | None) -> bool:
    """Whether a start/end time bound has a real (non-blank) value configured.

    True when an entity is wired, or a static value is set and isn't the
    blank sentinel ``BLANK_TIME``. Single definition of "configured" for a
    time-window bound, shared so a future symmetric check (e.g. "is the
    *start* bound configured?" for the blank-end case) delegates here rather
    than re-deriving the same predicate (CODING_GUIDELINES no-duplication
    rule). Currently consulted once, from :pyattr:`TimeWindowManager.after_start_time`.
    """
    return entity is not None or (
        static_value is not None and static_value != BLANK_TIME
    )


class TimeWindowManager:
    """Manages operational time window checks.

    Determines whether the current time falls within the configured
    start/end time window for automatic cover control.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: ConfigContextAdapter,
        *,
        event_buffer=None,
        clock: Callable[[], float] = time.monotonic,
        template_variables: Mapping[str, Any] | None = None,
        sunrise_provider: Callable[[], dt.datetime | None] | None = None,
    ) -> None:
        """Initialize time window manager.

        Args:
            hass: Home Assistant instance
            logger: Context-aware logger
            event_buffer: Shared diagnostic ring buffer (optional).
            clock: Monotonic time source (seconds) for the daytime-gate grace
                window. Injected so tests drive the grace timer deterministically.
            template_variables: Opaque render context threaded into the
                daytime-gate template — the ``acp`` self-reference namespace
                built once by the coordinator (issue #1159).
            sunrise_provider: Optional zero-arg closure returning today's
                astronomical sunrise as naive-local wall-clock time (or
                ``None`` when unresolvable). Consulted only by
                :pyattr:`after_start_time`, only when no start time is
                configured AND an end bound is configured, so a blank start
                anchors the window's lower bound to sunrise instead of
                midnight (issue #1256) rather than leaving the window open
                all night. Injected — like the ``ConditionGate`` readers
                below — so tests drive it deterministically; omitting it
                (the default) preserves the pre-#1256 fail-open-to-True
                behaviour.

        """
        self._hass = hass
        self.logger = logger
        self._event_buffer = event_buffer
        self._template_variables = template_variables
        self._sunrise_provider = sunrise_provider
        self._events = EventRecorder(event_buffer)
        self._last_time_window_state: bool | None = None

        # Config values — set via update_config()
        self._start_time: str | None = None
        self._start_time_entity: str | None = None
        self._end_time_config: str | None = None
        self._end_time_entity: str | None = None

        # The daytime gate (issue #632) — sensors and/or a Jinja condition folded
        # into one tri-state verdict, holding its last-known answer for a grace
        # window when every source goes indeterminate (issue #742) before falling
        # back to the astronomical window. The fold, the grace wiring, and the
        # reset-on-config-change rule live in the shared ConditionGate kernel so
        # the sun-tracking gate (issue #1167) reuses them rather than mirroring.
        #
        # The readers are injected as closures over THIS module's globals on
        # purpose: it keeps the kernel HA-free, and it keeps
        # ``managers.time_window.get_safe_state`` / ``.render_condition_or_none``
        # the patch surface every existing daytime-gate test already targets.
        self._gate = ConditionGate(
            DEFAULT_CONDITION_GATE_GRACE_SECONDS,
            read_state=lambda entity_id: get_safe_state(self._hass, entity_id),
            render_condition=lambda template: render_condition_or_none(
                self._hass, template, variables=self._template_variables
            ),
            clock=clock,
        )

        # Cached start time from last evaluation (for diagnostics)
        self._cached_start_time: dt.datetime | None = None

    def update_config(
        self,
        start_time: str | None,
        start_time_entity: str | None,
        end_time: str | None,
        end_time_entity: str | None,
        gate_sensors: list[str] = (),
        gate_template: str | None = None,
        gate_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
    ) -> None:
        """Update configuration values.

        Args:
            start_time: Static start time string
            start_time_entity: Entity ID providing start time
            end_time: Static end time string
            end_time_entity: Entity ID providing end time
            gate_sensors: Daytime-gate binary-entity IDs (on/active = daytime)
            gate_template: Optional daytime-gate Jinja condition (truthy = daytime)
            gate_template_mode: How ``gate_template`` folds with the sensors
                (a :class:`~const.TemplateCombineMode` value, or/and)

        """
        self._start_time = start_time
        self._start_time_entity = start_time_entity
        self._end_time_config = end_time
        self._end_time_entity = end_time_entity
        # Runs every cycle; the kernel forgets a held verdict only when the gate
        # config actually changed (issue #742).
        self._gate.update_config(gate_sensors, gate_template, gate_template_mode)

    @property
    def is_active(self) -> bool:
        """Check if current time is within operational window.

        Returns:
            True if current time is after start time and before end time,
            False otherwise. Returns True if no time restrictions configured.

        """
        if (
            self._cached_start_time
            and self.end_time
            and self._cached_start_time > self.end_time
        ):
            self.logger.error("Start time is after end time")
        # The clock (start/end) is an OUTER CLAMP layered onto the daytime gate
        # (issue #632): a configured gate that reads "dark" closes the window even
        # mid-clock, so the solar handler skips and the default handler runs. When
        # the gate is unconfigured ``gate_is_daytime`` is True (fail-open) and this
        # collapses to the pre-gate astronomical behavior.
        return self.before_end_time and self.after_start_time and self.gate_is_daytime

    @property
    def clock_window_open(self) -> bool:
        """Whether the user's start/end CLOCK window is open, ignoring the daytime gate.

        This is :pyattr:`is_active` without the ``gate_is_daytime`` factor.
        ``is_active`` conflates "outside the user's start/end clock" (ACP must stay
        hands-off — #215/#216) with "the daytime gate reads dark" (ACP has a
        well-defined night/default position it should still send — #656).
        Suppression sites that only care about the clock consult THIS; the
        gate-dark case is exposed separately via :pyattr:`gate_is_dark`.
        """
        return self.before_end_time and self.after_start_time

    @property
    def gate_is_configured(self) -> bool:
        """Return True when a daytime gate source — sensor or template — is set.

        Single source for "does the gate own the day/night boundary?". When False
        the coordinator uses the astronomical sunset/sunrise calc (issue #632).
        """
        return self._gate.is_configured

    @property
    def effective_daytime_gate(self) -> bool | None:
        """Tri-state gate verdict the coordinator forwards to the astral engine.

        ``None`` means "no gate opinion → use the astronomical sunset/sunrise
        window" — the single value passed as ``daytime_gate`` to
        ``compute_effective_default`` (issue #632/#742). It is ``None`` when the
        gate is unconfigured, and also when every gate source has been
        indeterminate past the grace window (FELL_BACK). While a source is
        determinate it is the live verdict; within the grace window it is the
        held last-known verdict (HOLDING).
        """
        return self._gate.effective

    @property
    def gate_is_daytime(self) -> bool:
        """Whether the daytime gate reports "daytime" (ACP should sun-track).

        Derived from :pyattr:`effective_daytime_gate`: ``None`` (unconfigured or
        grace-expired fallback) reads as daytime so the clock factor of
        :pyattr:`is_active` collapses to the pre-gate astronomical behaviour.
        """
        return self._gate.resolved(default=True)

    @property
    def gate_is_dark(self) -> bool:
        """Whether a *configured* gate currently reports "dark".

        False when the gate is unconfigured or has fallen back to astronomical
        (``effective_daytime_gate`` is ``None``), so the gate-dark night position
        only fires on a genuine dark verdict (live or held).
        """
        return self.gate_is_configured and not self.gate_is_daytime

    def seconds_until_gate_fallback(self) -> float | None:
        """Seconds until a HELD gate verdict expires to the astronomical fallback.

        ``None`` when no prompt wake is needed (gate determinate, never observed,
        already fell back, or unconfigured). The coordinator uses this to schedule
        a single ``async_call_later`` refresh so the fallback engages promptly at
        grace expiry instead of waiting for the next state-change/periodic cycle.
        """
        return self._gate.seconds_until_fallback()

    def _normalize_to_today(self, time: dt.datetime) -> dt.datetime:
        """Normalize a future-dated entity time to today's date.

        Sun entity sensors (e.g., sensor.sun_next_rising) roll forward to
        tomorrow's datetime once the event passes. This method pins such times
        back to today so time window comparisons work correctly for the
        remainder of the current day.

        Args:
            time: Parsed datetime from an entity sensor.

        Returns:
            The datetime with today's date if the original was a future date,
            otherwise unchanged.

        """
        today = local_now_naive().date()
        if time.date() > today:
            return time.replace(year=today.year, month=today.month, day=today.day)
        return time

    def _resolve_start_datetime(self) -> dt.datetime | None:
        """Resolve the configured start time to a datetime, clock-independent.

        The pure start-resolution ``_start_has_passed`` performs, without the
        "has it passed now?" comparison: entity → parse + normalize-to-today,
        static non-blank config → parse. Returns ``None`` when there is no real
        start time (no entity and the static value is unset or the blank
        sentinel ``BLANK_TIME``) or the entity/config value could not be parsed.

        """
        if self._start_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self._hass, self._start_time_entity)
            )
            if time is None:
                self.logger.debug(
                    "Start time entity %s returned None, treating as no start set",
                    self._start_time_entity,
                )
                return None
            return self._normalize_to_today(time)
        if self._start_time is not None and self._start_time != BLANK_TIME:
            time = get_datetime_from_str(self._start_time)
            if time is None:
                self.logger.debug(
                    "Start time config value could not be parsed, treating as no start set"
                )
                return None
            return time
        return None

    @property
    def resolved_start_time(self) -> dt.datetime | None:
        """Resolved operational-window start datetime, or ``None`` when unset.

        Clock-independent view of the start time (issue #975) — the config
        time-window health check compares this against :pyattr:`end_time` to flag
        a start-after-end misconfiguration without keying on the wall clock.
        ``None`` means "no explicit start" (no entity, blank/unset static value,
        or an unparseable value) — distinct from an explicit 00:00 start.
        """
        return self._resolve_start_datetime()

    def _start_has_passed(self) -> bool | None:
        """Evaluate the configured start time against now.

        Returns:
            ``True``/``False`` when a *real* start time (entity or non-blank
            static config) is configured — whether ``now`` is at/after it.
            ``None`` when there is no real start time: no entity and the static
            value is either unset or the blank sentinel ``BLANK_TIME``, or the
            entity/config value could not be parsed. ``None`` means "no explicit
            operational-window start" — distinct from an explicit 00:00 start.

        """
        resolved = self._resolve_start_datetime()
        if resolved is None:
            return None
        now = local_now_naive()
        self.logger.debug(
            "Start time: %s, now: %s, now >= time: %s", resolved, now, now >= resolved
        )
        self._cached_start_time = resolved
        return now >= resolved

    @property
    def after_start_time(self) -> bool:
        """Check if current time is after start time.

        Returns:
            True if current time is after configured start time (from entity
            or static config), False otherwise. When no start time is
            configured (including the blank sentinel) the result depends on
            whether an end bound is configured: with no end bound the window
            is unbounded on both sides and this fails open to True — "no
            start restriction" (unchanged pre-#1256 behaviour). Once an end
            bound IS configured, a blank start anchors the window's lower
            bound to astronomical sunrise instead of midnight (issue #1256),
            via the injected ``sunrise_provider`` — matching the ``start_time``
            option's documented "leave blank to start at sunrise". Falls back
            to True (fail-open) when no ``sunrise_provider`` was injected or
            it returns ``None``.

        """
        passed = self._start_has_passed()
        if passed is not None:
            return passed
        if _bound_is_configured(self._end_time_entity, self._end_time_config):
            sunrise = self._sunrise_provider() if self._sunrise_provider else None
            if sunrise is not None:
                return local_now_naive() >= sunrise
        return True

    @property
    def window_explicitly_started(self) -> bool:
        """Whether a real (non-blank) start time is configured AND has passed.

        Distinct from :pyattr:`after_start_time`, which returns True for the
        no-start / blank-sentinel case. Used by ``compute_effective_default`` to
        suppress the overnight position only when the user's operational window
        has genuinely opened — not when the start time is merely blank
        (issue #492). Returns False when no real start is configured.

        """
        passed = self._start_has_passed()
        return False if passed is None else passed

    @property
    def end_time(self) -> dt.datetime | None:
        """Get end time from entity or config.

        Returns:
            End time datetime object from end_time_entity state or end_time
            config value. Handles midnight (00:00) by adding one day. Returns
            None if no end time configured.

        """
        time = None
        if self._end_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self._hass, self._end_time_entity)
            )
            if time is not None:
                time = self._normalize_to_today(time)
        elif self._end_time_config is not None:
            time = get_datetime_from_str(self._end_time_config)
            if time is not None and time.time() == dt.time(0, 0):
                time = time + dt.timedelta(days=1)
        return time

    @property
    def before_end_time(self) -> bool:
        """Check if current time is before end time.

        Returns:
            True if current time is before configured end time (from entity
            or static config), False otherwise. Returns True if no end time
            configured.

        """
        end = self.end_time
        if end is not None:
            now = local_now_naive()
            self.logger.debug(
                "End time: %s, now: %s, now < time: %s",
                end,
                now,
                now < end,
            )
            return now < end
        return True

    @property
    def start_time_value(self) -> dt.datetime | None:
        """Get cached start time from last evaluation (for diagnostics)."""
        return self._cached_start_time

    async def check_transition(
        self,
        track_end_time: bool,
        refresh_callback,
        on_window_open=None,
    ) -> None:
        """Check if time window state has changed and trigger refresh if needed.

        Detects when the operational time window changes state
        (e.g., when end time is reached) and triggers appropriate actions.
        Provides <1 minute response time for time window changes.

        Args:
            track_end_time: Whether to track end time transitions
            refresh_callback: Async callback invoked when window closes
            on_window_open: Optional async callback invoked when window opens
                (inactive→active), so covers reposition at the start of the day

        """
        # Initialize tracking on first call
        if self._last_time_window_state is None:
            self._last_time_window_state = self.is_active
            return

        current_state = self.is_active

        # If state changed, trigger appropriate action
        if current_state != self._last_time_window_state:
            self.logger.info(
                "Time window state changed: %s → %s",
                "active" if self._last_time_window_state else "inactive",
                "active" if current_state else "inactive",
            )
            self._events.record(
                "time_window_changed",
                entity_id="",
                previous=self._last_time_window_state,
                current=current_state,
            )
            self._last_time_window_state = current_state

            if current_state and on_window_open is not None:
                self.logger.info("Time window opened, repositioning covers")
                await on_window_open()
            elif not current_state and track_end_time:
                self.logger.info(
                    "End time reached, returning covers to default position"
                )
                await refresh_callback()
