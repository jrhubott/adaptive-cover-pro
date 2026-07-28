"""Time window management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_context_adapter import ConfigContextAdapter

from ..const import (
    BLANK_TIME,
    DEFAULT_DAYTIME_GATE_GRACE_SECONDS,
    DEFAULT_TEMPLATE_COMBINE_MODE,
)
from ..helpers import get_datetime_from_str, get_safe_state
from ..templates import (
    combine_with_mode,
    is_template_string,
    render_condition_or_none,
)
from .common import EventRecorder
from .common.graceful_source import GracefulSource, Resolution, SourceResolution


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
    ) -> None:
        """Initialize time window manager.

        Args:
            hass: Home Assistant instance
            logger: Context-aware logger
            event_buffer: Shared diagnostic ring buffer (optional).
            clock: Monotonic time source (seconds) for the daytime-gate grace
                window. Injected so tests drive the grace timer deterministically.

        """
        self._hass = hass
        self.logger = logger
        self._event_buffer = event_buffer
        self._events = EventRecorder(event_buffer)
        self._last_time_window_state: bool | None = None

        # Config values — set via update_config()
        self._start_time: str | None = None
        self._start_time_entity: str | None = None
        self._end_time_config: str | None = None
        self._end_time_entity: str | None = None

        # Daytime-gate config (issue #632) — set via update_config()
        self._gate_sensors: list[str] = []
        self._gate_template: str | None = None
        self._gate_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE

        # Daytime-gate graceful-fallback state machine (issue #742): holds the
        # last-known daytime/dark verdict for a grace window when every gate
        # source goes indeterminate, then falls back to the astronomical window.
        self._graceful = GracefulSource(DEFAULT_DAYTIME_GATE_GRACE_SECONDS, clock=clock)

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
        # update_config runs every cycle, so only forget the held verdict when the
        # gate config actually changed (issue #742) — otherwise a steady config
        # would reset the grace machine each cycle and never hold anything.
        new_sensors = list(gate_sensors)
        if (
            new_sensors != self._gate_sensors
            or gate_template != self._gate_template
            or gate_template_mode != self._gate_template_mode
        ):
            self._graceful.reset()
        self._gate_sensors = new_sensors
        self._gate_template = gate_template
        self._gate_template_mode = gate_template_mode

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
        return bool(self._gate_sensors) or is_template_string(self._gate_template)

    def _gate_verdict(self) -> bool | None:
        """Read the gate's *live* daytime verdict, or ``None`` when indeterminate.

        Tri-state — isolates the "is the gate indeterminate?" rule so the global
        fail-open contract of :func:`is_entity_active` (other features depend on
        it) stays untouched (issue #742):

        * **sensor opinion** — ``None`` when there are no sensors or every
          configured sensor reads invalid (``get_safe_state`` is ``None`` —
          unavailable/unknown/missing); otherwise ``any`` valid sensor is ``"on"``.
        * **template opinion** — :func:`render_condition_or_none` gives ``None``
          when the template is absent or unrenderable, else its boolean.
        * **combine** — both ``None`` → ``None`` (fully indeterminate); exactly
          one ``None`` → the other; both present → folded via the configured
          OR/AND mode (matching the pre-#742 gate evaluation).
        """
        sensor_states = [get_safe_state(self._hass, sid) for sid in self._gate_sensors]
        valid_states = [s for s in sensor_states if s is not None]
        sensor_opinion: bool | None = (
            None if not valid_states else any(s == "on" for s in valid_states)
        )
        template_opinion = render_condition_or_none(self._hass, self._gate_template)

        if sensor_opinion is None and template_opinion is None:
            return None
        if sensor_opinion is None:
            return template_opinion
        if template_opinion is None:
            return sensor_opinion
        return combine_with_mode(
            template_opinion,
            sensor_opinion,
            self._gate_template_mode,
            has_template=True,
            has_others=True,
        )

    def _resolve(self) -> Resolution:
        """Feed this cycle's gate verdict to the grace machine (idempotent)."""
        return self._graceful.observe(self._gate_verdict())

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
        if not self.gate_is_configured:
            return None
        resolution = self._resolve()
        if resolution.state is SourceResolution.FELL_BACK:
            return None
        return resolution.value

    @property
    def gate_is_daytime(self) -> bool:
        """Whether the daytime gate reports "daytime" (ACP should sun-track).

        Derived from :pyattr:`effective_daytime_gate`: ``None`` (unconfigured or
        grace-expired fallback) reads as daytime so the clock factor of
        :pyattr:`is_active` collapses to the pre-gate astronomical behaviour.
        """
        eff = self.effective_daytime_gate
        return True if eff is None else eff

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
        self._resolve()
        return self._graceful.remaining()

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
        today = dt.date.today()
        if time.date() > today:
            return time.replace(year=today.year, month=today.month, day=today.day)
        return time

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
        now = dt.datetime.now()
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
            time = self._normalize_to_today(time)
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s ", time, now, now >= time
            )
            self._cached_start_time = time
            return now >= time
        if self._start_time is not None and self._start_time != BLANK_TIME:
            time = get_datetime_from_str(self._start_time)
            if time is None:
                self.logger.debug(
                    "Start time config value could not be parsed, treating as no start set"
                )
                return None
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s", time, now, now >= time
            )
            self._cached_start_time = time
            return now >= time
        return None

    @property
    def after_start_time(self) -> bool:
        """Check if current time is after start time.

        Returns:
            True if current time is after configured start time (from entity
            or static config), False otherwise. Returns True if no start time
            configured (including the blank sentinel) — the active-window logic
            keys on this meaning "no start restriction".

        """
        passed = self._start_has_passed()
        return True if passed is None else passed

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
            now = dt.datetime.now()
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
