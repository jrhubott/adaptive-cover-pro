"""Cloud-suppression smoothing state (issue #864).

The cloud-suppression *decision* (activate / deactivate) is cross-cycle state:
a Schmitt latch per numeric trigger (hysteresis) plus an aggregate hold-timer
(debounce). Per CODING_GUIDELINES "Managers Hold State, Policies Hold Behavior"
that state cannot live in the pure ``CloudSuppressionHandler`` or the frozen
``ClimateReadings``. It lives here, modelled on ``WeatherManager``: the manager
resolves a single boolean and the coordinator threads it into the snapshot; the
handler gates on that boolean and keeps only position selection + the FOV /
time-window guards.

The manager is cover-type-agnostic — it consumes provider booleans and never
reads HA or branches on cover type. Absent config (hold-time 0, blank release
thresholds) reproduces today's instantaneous single-crossing behaviour exactly.

The Schmitt latch and the hold-time debounce are the shared
``managers/common/smoothing`` primitives (issue #917) — the same code
``ClimateSmoothingManager`` delegates to.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..const import DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME
from .common import EventRecorder, HoldDebouncer, advance_schmitt_latch

if TYPE_CHECKING:
    from ..state.climate_provider import ClimateReadings


class CloudSuppressionManager:
    """Hold hysteresis latches + a hold-timer for cloud suppression.

    Each numeric trigger (lux, irradiance, cloud coverage) is a Schmitt latch:
    it engages when the activate edge is met and drops only when the value
    clears the release edge — in between it holds its prior state. ``is_sunny``
    is boolean, so it contributes directly with no hysteresis.

    The instantaneous condition is the OR of ``not is_sunny`` and the three
    latches. A hold-time debounce wraps that aggregate: a change must persist for
    the configured hold-time before the resolved bool flips. Reverting before
    expiry cancels the pending transition (true debounce). ``hold_time == 0``
    flips immediately.
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
        self._hold_time: int = DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME

        # Per-trigger Schmitt latches (hysteresis memory).
        self._lux_latched: bool = False
        self._irr_latched: bool = False
        self._cloud_latched: bool = False

        # Aggregate resolved bool + hold-time debounce (shared primitive).
        self._debouncer = HoldDebouncer(
            logger, label="cloud-suppression hold", on_commit=self._on_commit
        )
        self._debouncer.reset(False)

    # --- Configuration ---

    def update_config(
        self,
        *,
        enabled: bool,
        hold_time_seconds: int = DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
    ) -> None:
        """Update configuration from the coordinator.

        ``hold_time_seconds`` defaults to ``DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME``
        (0 = instantaneous) so a caller predating the smoothing feature gets the
        historical single-crossing behaviour.
        """
        self._enabled = enabled
        self._hold_time = hold_time_seconds
        if not enabled:
            self._reset()

    # --- Properties ---

    @property
    def is_suppression_active(self) -> bool:
        """Return the resolved suppression bool (False when disabled)."""
        return self._enabled and self._debouncer.resolved

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
        Returns ``None`` otherwise. When ``hold_time == 0`` the resolved bool is
        committed in-line and ``None`` is returned.
        """
        if not self._enabled or readings is None:
            self._reset()
            return None

        self._update_latches(readings)
        instantaneous = (
            (not readings.is_sunny)
            or self._lux_latched
            or self._irr_latched
            or self._cloud_latched
        )
        return self._debouncer.evaluate(instantaneous, self._hold_time)

    def _update_latches(self, readings: ClimateReadings) -> None:
        """Advance each per-trigger Schmitt latch for this cycle."""
        self._lux_latched = advance_schmitt_latch(
            self._lux_latched,
            readings.lux_below_threshold,
            readings.lux_release_cleared,
        )
        self._irr_latched = advance_schmitt_latch(
            self._irr_latched,
            readings.irradiance_below_threshold,
            readings.irradiance_release_cleared,
        )
        self._cloud_latched = advance_schmitt_latch(
            self._cloud_latched,
            readings.cloud_coverage_above_threshold,
            readings.cloud_coverage_release_cleared,
        )

    # --- Hold-time debounce timer ---

    def start_hold_timeout(self, refresh_callback: Callable) -> None:
        """Start the hold-time debounce timer.

        Called by the coordinator when :meth:`evaluate` signals a pending
        transition. When the timer expires (and the transition has not been
        reverted), the resolved bool commits and ``refresh_callback`` runs.
        """
        self._logger.info(
            "Cloud-suppression change pending — holding %s seconds before it takes "
            "effect",
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

    def _on_commit(self, previous: bool, target: bool) -> None:
        """Record the resolved-bool transition (fired by the debouncer)."""
        self._events.record(
            "cloud_suppression_changed",
            entity_id="",
            previous=previous,
            current=target,
        )

    def _reset(self) -> None:
        """Drop all latch/resolved/pending state and cancel any timer."""
        self._lux_latched = False
        self._irr_latched = False
        self._cloud_latched = False
        self._debouncer.reset(False)
