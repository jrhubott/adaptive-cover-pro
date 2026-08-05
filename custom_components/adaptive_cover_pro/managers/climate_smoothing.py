"""Climate-mode temperature smoothing state (issue #917).

The temperature analogue of :class:`.cloud_suppression.CloudSuppressionManager`.
Climate mode classifies the season from four instantaneous temperature crossings
(winter / summer-warm / outside-high / extreme-heat); a sensor hovering on a
threshold reclassifies the season every cycle and the cover flaps. This manager
holds cross-cycle state the pure ``ClimateCoverData`` and the frozen
``ClimateReadings`` cannot: a Schmitt latch per crossing (hysteresis) plus one
aggregate hold-timer (debounce) over the resolved four-flag tuple.

Unlike cloud suppression it resolves a MULTI-way classifier — a single OR-bool
cannot represent four independent crossings feeding a mutually-exclusive season
decision — so it hands the handler a :class:`ClimateTempFlags` instead of one
bool. The Schmitt latch and the hold-time debounce are the shared
``managers/common/smoothing`` primitives, delegated to exactly like the cloud
manager.

The manager is cover-type-agnostic — it consumes provider booleans and never
reads HA or branches on cover type. Absent config (hold-time 0, blank release
thresholds) reproduces today's instantaneous single-crossing behaviour exactly,
because the provider then emits ``release_cleared = not activate`` and the latch
collapses to ``latched == activate``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from ..const import DEFAULT_CLIMATE_TEMP_HOLD_TIME
from ..pipeline.types import ClimateTempFlags
from .common import EventRecorder, HoldDebouncer, advance_schmitt_latch

if TYPE_CHECKING:
    from ..state.climate_provider import ClimateReadings

_ALL_FALSE = ClimateTempFlags(
    winter=False, summer_warm=False, outside_high=False, extreme_heat=False
)


class ClimateSmoothingManager:
    """Hold four hysteresis latches + a hold-timer for the season crossings.

    Each crossing (winter, summer-warm, outside-high, extreme-heat) is a Schmitt
    latch: it engages on its activate edge and drops only when the value clears
    its release edge — holding in between. A hold-time debounce wraps the
    aggregate four-flag tuple: a change must persist for the configured hold-time
    before the resolved flags flip. Reverting before expiry cancels the pending
    transition (true debounce). ``hold_time == 0`` flips immediately.
    """

    def __init__(self, logger, *, event_buffer=None) -> None:
        """Initialize the manager.

        Args:
            logger: Logger for debug/info output.
            event_buffer: Shared diagnostic ring buffer (optional).

        """
        self._logger = logger
        self._events = EventRecorder(event_buffer)

        # Config (updated via update_config).
        self._enabled: bool = False
        self._hold_time: int = DEFAULT_CLIMATE_TEMP_HOLD_TIME

        # Per-crossing Schmitt latches (hysteresis memory).
        self._winter_latched: bool = False
        self._summer_warm_latched: bool = False
        self._outside_high_latched: bool = False
        self._extreme_heat_latched: bool = False

        # Aggregate resolved flags + hold-time debounce (shared primitive).
        self._debouncer = HoldDebouncer(
            logger, label="climate-temp hold", on_commit=self._on_commit
        )
        self._debouncer.reset(_ALL_FALSE)

    # --- Configuration ---

    def update_config(
        self,
        *,
        enabled: bool,
        hold_time_seconds: int = DEFAULT_CLIMATE_TEMP_HOLD_TIME,
    ) -> None:
        """Update configuration from the coordinator.

        ``enabled`` tracks climate mode being on. ``hold_time_seconds`` defaults
        to ``DEFAULT_CLIMATE_TEMP_HOLD_TIME`` (0 = instantaneous) so a caller
        predating the smoothing feature gets the historical behaviour.
        """
        self._enabled = enabled
        self._hold_time = hold_time_seconds
        if not enabled:
            self._reset()

    # --- Properties ---

    @property
    def resolved_flags(self) -> ClimateTempFlags | None:
        """Return the resolved smoothed flags, or None when disabled.

        None signals "smoothing off" — the handler then falls back to the raw
        single-crossing. When enabled the manager always returns a
        ``ClimateTempFlags`` (with hold=0 + blank releases these equal the raw
        crossings, so behaviour is byte-identical to before smoothing).
        """
        if not self._enabled:
            return None
        return self._debouncer.resolved

    @property
    def is_timeout_running(self) -> bool:
        """Return True when a hold-time debounce timer is pending."""
        return self._debouncer.is_timeout_running

    # --- Evaluation ---

    def evaluate(self, readings: ClimateReadings | None) -> str | None:
        """Fold this cycle's readings into the latches + hold-time debounce.

        Returns ``"should_start_timeout"`` when a transition is pending, the
        hold-time is non-zero, and no timer is already counting toward it — the
        coordinator owns timer creation because it holds the refresh callback.
        Returns ``None`` otherwise. When ``hold_time == 0`` the resolved flags
        are committed in-line and ``None`` is returned.
        """
        if not self._enabled or readings is None:
            self._reset()
            return None

        self._update_latches(readings)
        instantaneous = ClimateTempFlags(
            winter=self._winter_latched,
            summer_warm=self._summer_warm_latched,
            outside_high=self._outside_high_latched,
            extreme_heat=self._extreme_heat_latched,
        )
        return self._debouncer.evaluate(instantaneous, self._hold_time)

    def _update_latches(self, readings: ClimateReadings) -> None:
        """Advance each per-crossing Schmitt latch for this cycle."""
        self._winter_latched = advance_schmitt_latch(
            self._winter_latched,
            readings.temp_below_low_threshold,
            readings.temp_low_release_cleared,
        )
        self._summer_warm_latched = advance_schmitt_latch(
            self._summer_warm_latched,
            readings.temp_above_high_threshold,
            readings.temp_high_release_cleared,
        )
        self._outside_high_latched = advance_schmitt_latch(
            self._outside_high_latched,
            readings.outside_above_threshold,
            readings.outside_release_cleared,
        )
        self._extreme_heat_latched = advance_schmitt_latch(
            self._extreme_heat_latched,
            readings.outside_above_extreme_heat,
            readings.extreme_heat_release_cleared,
        )

    # --- Hold-time debounce timer ---

    def start_hold_timeout(self, refresh_callback: Callable) -> None:
        """Start the hold-time debounce timer.

        Called by the coordinator when :meth:`evaluate` signals a pending
        transition. When the timer expires (and the transition has not been
        reverted), the resolved flags commit and ``refresh_callback`` runs.
        """
        self._logger.info(
            "Climate-mode season change pending — holding %s seconds before it "
            "takes effect",
            self._hold_time,
        )
        self._debouncer.start_hold_timeout(self._hold_time, refresh_callback)

    async def _on_hold_timeout_expired(self, refresh_callback: Callable) -> None:
        """Commit the pending transition once the hold-time has elapsed."""
        await self._debouncer._on_hold_timeout_expired(refresh_callback)

    def cancel_hold_timeout(self) -> None:
        """Cancel the running hold-time timer, if any."""
        self._debouncer.cancel()

    # --- Internal helpers ---

    def _on_commit(self, previous: ClimateTempFlags, target: ClimateTempFlags) -> None:
        """Record the resolved-flags transition (fired by the debouncer)."""
        self._events.record(
            "climate_temp_smoothing_changed",
            entity_id="",
            previous=asdict(previous),
            current=asdict(target),
        )

    def _reset(self) -> None:
        """Drop all latch/resolved/pending state and cancel any timer."""
        self._winter_latched = False
        self._summer_warm_latched = False
        self._outside_high_latched = False
        self._extreme_heat_latched = False
        self._debouncer.reset(_ALL_FALSE)
