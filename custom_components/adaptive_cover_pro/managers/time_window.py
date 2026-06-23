"""Time window management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_context_adapter import ConfigContextAdapter

from ..const import BLANK_TIME, DEFAULT_TEMPLATE_COMBINE_MODE
from ..helpers import get_datetime_from_str, get_safe_state, is_entity_active
from ..templates import combine_with_mode, is_template_string, render_condition
from .common import EventRecorder


class TimeWindowManager:
    """Manages operational time window checks.

    Determines whether the current time falls within the configured
    start/end time window for automatic cover control.
    """

    def __init__(
        self, hass: HomeAssistant, logger: ConfigContextAdapter, *, event_buffer=None
    ) -> None:
        """Initialize time window manager.

        Args:
            hass: Home Assistant instance
            logger: Context-aware logger
            event_buffer: Shared diagnostic ring buffer (optional).

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
        self._gate_sensors = list(gate_sensors)
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

    @property
    def gate_is_daytime(self) -> bool:
        """Whether the daytime gate reports "daytime" (ACP should sun-track).

        Mirrors :pyattr:`MotionManager.is_motion_detected`: the gate template and
        the gate sensors are combined per the configured mode. Returns True when
        the gate is unconfigured (feature disabled → astronomical fallback). The
        template renders fail-open to *daytime* (``default=True``) — unlike motion's
        ``default=False`` — so a broken template never forces a premature sunset.
        """
        if not self.gate_is_configured:
            return True  # Unconfigured → daytime → astronomical fallback

        has_template = is_template_string(self._gate_template)
        # default=True ONLY when a template is actually set: fail-open to daytime so
        # a broken template never slams the cover to the sunset position (motion's
        # gate fails closed; this one must not, because "dark" here ends sun
        # tracking). With no template the source must be neutral for the combine
        # (False), or OR would read daytime even when a sensor says dark.
        template_truthy = (
            render_condition(self._hass, self._gate_template, default=True)
            if has_template
            else False
        )
        sensors_active = any(
            is_entity_active(self._hass, sid) for sid in self._gate_sensors
        )
        return combine_with_mode(
            template_truthy,
            sensors_active,
            self._gate_template_mode,
            has_template=has_template,
            has_others=bool(self._gate_sensors),
        )

    @property
    def gate_is_dark(self) -> bool:
        """Whether a *configured* gate reports "dark" (apply the sunset position).

        ``None``-safe inverse of :pyattr:`gate_is_daytime` that stays False when the
        gate is unconfigured, so the coordinator can pass it straight through as the
        ``daytime_gate`` override to ``compute_effective_default`` (issue #632):
        ``True`` forces the sunset position, ``False`` (here, unconfigured) leaves
        the astronomical decision untouched.
        """
        return self.gate_is_configured and not self.gate_is_daytime

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
