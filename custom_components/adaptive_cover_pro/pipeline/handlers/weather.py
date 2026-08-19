"""Weather override handler — safety retraction when weather conditions are severe."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class WeatherOverrideHandler(OverrideHandler):
    """Retracts covers when any configured weather condition exceeds its threshold.

    Priority 90: between force_override (100) and motion_timeout (80).

    Conditions evaluated by WeatherManager (OR logic):
    - Wind speed sensor >= threshold (optionally filtered by wind direction)
    - Rain rate sensor >= threshold
    - IsRaining / IsWindy binary sensors
    - Severe weather binary sensors (hail, frost, storm)

    The manager also applies a configurable clear-delay timeout so covers
    stay retracted for a brief period after conditions clear, preventing
    rapid toggling in gusty or intermittent conditions.
    """

    name = "weather"
    priority = 90

    @staticmethod
    def _scoped_out_of_window(snapshot: PipelineSnapshot) -> bool:
        """Whether the user scoped weather to a window that is currently shut.

        **The one statement of the #1308 gate.** ``evaluate`` and
        ``describe_skip`` both consume it, so the guard and the reason it
        produces cannot drift apart.

        Reads ``clock_window_open``, never ``in_time_window``: the latter folds
        in the sun-tracking daytime gate, which is dark mid-clock on a
        gate-configured install (#656/#632). Treating that as "outside the
        window" would strip storm protection from a 10:00 winter morning, which
        is a far larger change than this option promises.

        The min-mode floor is the other seat, and it is deliberately NOT routed
        through here: it carries ``snapshot.weather_outside_window`` onto its
        ``AxisConstraint`` and lets ``partition_axis_constraints`` supply the
        clock, so ``_window_eligible`` stays the single statement of the
        claim-side rule.
        """
        return not snapshot.weather_outside_window and not snapshot.clock_window_open

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return override position when weather conditions are active.

        When ``weather_override_min_mode`` is True, the handler defers
        (returns ``None``) so the registry can compose the configured
        position as a post-decision floor clamp on whichever lower-priority
        handler wins (issue #463).
        """
        if not snapshot.weather_override_active:
            return None
        # Second, not first, on purpose (#1308): a user with clear skies and no
        # opt-in must keep reading "weather override not active" rather than be
        # told a window gate they never configured is what stopped weather.
        # ``describe_skip`` encodes the same order.
        if self._scoped_out_of_window(snapshot):
            return None
        if snapshot.weather_override_min_mode:
            return None
        pos = snapshot.weather_override_position
        bypass = snapshot.weather_bypass_auto_control
        raw = compute_raw_calculated_position(snapshot)
        bypass_note: Reason | str = ""
        if bypass:
            bypass_note = Reason(ReasonCode.FRAGMENT_BYPASS_NOTE)
        return PipelineResult(
            position=pos,
            control_method=ControlMethod.WEATHER,
            reason_payload=Reason(
                ReasonCode.WEATHER_ACTIVE,
                {"position": pos, "bypass_note": bypass_note},
            ),
            bypass_auto_control=bypass,
            is_safety=True,
            raw_calculated_position=raw,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:
        """Reason when the weather override does not match.

        Ordered to match ``evaluate``, so the trace names the gate that
        actually stopped it: the window scope is reported only once the
        active check has passed, which keeps a user whose sensors are simply
        quiet reading "not active" instead of being pointed at a scope that is
        not what stopped weather (``solar.describe_skip``'s principle).

        In MIN MODE the two later gates both hold, and reporting the scope is
        still the truthful answer rather than a lost distinction: the min-mode
        floor is the *other* seat of the same option, and out here
        ``_window_eligible`` drops it from exactly the same
        ``weather_outside_window`` field — so nothing weather-shaped acts, and
        it is the window scope that stopped all of it. Min-mode deferral has
        never had a reason code of its own (it reads
        ``SKIP_WEATHER_NOT_ACTIVE`` with the clock open, and did before #1308),
        so no distinction is lost here; what is gained is that an ACTIVE
        override no longer reports itself as inactive.
        """
        if snapshot.weather_override_active and self._scoped_out_of_window(snapshot):
            return Reason(ReasonCode.SKIP_OUTSIDE_WINDOW)
        return Reason(ReasonCode.SKIP_WEATHER_NOT_ACTIVE)
