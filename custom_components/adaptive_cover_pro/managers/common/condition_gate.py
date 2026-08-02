"""A sensor-list + optional-Jinja condition folded into one tri-state gate.

The shape ACP uses wherever a feature asks "should this run right now?" from a
list of binary sensors OR'd together, optionally folded with a Jinja condition
via a :class:`~const.TemplateCombineMode`, held across transient indeterminacy
by :class:`~managers.common.graceful_source.GracefulSource`.

The daytime gate (issue #632, grace added in #742) was the first instance and
carried this logic inline in :class:`~managers.time_window.TimeWindowManager`.
The sun-tracking gate (issue #1167) is the second, so the logic moved here
rather than being mirrored — one implementation, two callers, and a bug fixed
once is fixed for both.

**HA-free by construction**, exactly like ``GracefulSource`` underneath it: the
caller injects ``read_state`` and ``render_condition`` instead of this module
importing ``homeassistant`` or holding a ``hass``. That keeps the kernel
unit-testable without an HA instance, and it leaves each caller's own module as
the patch surface its tests already target — extracting the calls to a shared
module would otherwise have silently un-patched every existing daytime-gate
test while they all still passed.

Composition, not inheritance: callers hold a ``ConditionGate`` and map its
tri-state answer onto their own policy (the daytime gate reads ``None`` as
"use the astronomical window"; the sun-tracking gate reads it as "track").
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from ...const import DEFAULT_TEMPLATE_COMBINE_MODE
from ...templates import combine_with_mode, is_template_string
from .graceful_source import GracefulSource, Resolution, SourceResolution


class ConditionGate:
    """Tri-state gate over a sensor list and an optional condition template.

    Args:
        grace_seconds: How long a last-known verdict is held once every source
            goes indeterminate, before the caller's fallback takes over.
        read_state: Reads one entity's state, or ``None`` when it is
            unavailable / unknown / missing. Callers pass a closure over their
            own module's ``get_safe_state`` so the read stays patchable there.
        render_condition: Renders the configured template to a bool, or ``None``
            when there is no template or it cannot render. Callers pass a
            closure over their own ``render_condition_or_none`` (carrying any
            template variables) for the same reason.
        clock: Monotonic time source, injected so tests drive the grace window
            deterministically.

    """

    def __init__(
        self,
        grace_seconds: float,
        read_state: Callable[[str], str | None],
        render_condition: Callable[[str | None], bool | None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the gate with its injected readers and grace window."""
        self._read_state = read_state
        self._render_condition = render_condition
        self._sensors: list[str] = []
        self._template: str | None = None
        self._template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
        self._graceful: GracefulSource[bool] = GracefulSource(
            grace_seconds, clock=clock
        )

    def update_config(
        self,
        sensors: Iterable[str] = (),
        template: str | None = None,
        template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
    ) -> None:
        """Apply this cycle's gate configuration.

        Callers run this every cycle, so the grace machine is reset **only when
        the configuration actually changed** (issue #742) — resetting on every
        call would re-anchor the grace window continuously and hold nothing.
        """
        new_sensors = list(sensors)
        if (
            new_sensors != self._sensors
            or template != self._template
            or template_mode != self._template_mode
        ):
            self._graceful.reset()
        self._sensors = new_sensors
        self._template = template
        self._template_mode = template_mode

    @property
    def is_configured(self) -> bool:
        """Whether any gate source — a sensor or a real template — is set.

        When False the gate has no opinion at all and the caller keeps whatever
        behaviour it had before a gate existed.
        """
        return bool(self._sensors) or is_template_string(self._template)

    def live_verdict(self) -> bool | None:
        """Return this cycle's raw verdict; ``None`` when every source is indeterminate.

        Tri-state so "the gate says no" stays distinguishable from "the gate
        cannot say" — the distinction the global fail-open contract of
        :func:`helpers.is_entity_active` cannot express (issue #742):

        * **sensor opinion** — ``None`` when there are no sensors or every one
          reads invalid; otherwise ``any`` valid sensor is ``"on"``.
        * **template opinion** — whatever ``render_condition`` returns; ``None``
          when absent or unrenderable.
        * **combine** — both ``None`` → ``None``; exactly one ``None`` → the
          other; both present → folded via the configured OR/AND mode.
        """
        sensor_states = [self._read_state(entity_id) for entity_id in self._sensors]
        valid_states = [s for s in sensor_states if s is not None]
        sensor_opinion: bool | None = (
            None if not valid_states else any(s == "on" for s in valid_states)
        )
        template_opinion = self._render_condition(self._template)

        if sensor_opinion is None and template_opinion is None:
            return None
        if sensor_opinion is None:
            return template_opinion
        if template_opinion is None:
            return sensor_opinion
        return combine_with_mode(
            template_opinion,
            sensor_opinion,
            self._template_mode,
            has_template=True,
            has_others=True,
        )

    def _resolve(self) -> Resolution[bool]:
        """Feed this cycle's verdict to the grace machine (idempotent)."""
        return self._graceful.observe(self.live_verdict())

    @property
    def effective(self) -> bool | None:
        """The grace-resolved verdict, or ``None`` when the gate has no opinion.

        ``None`` means "fall back to whatever the caller did before the gate" —
        returned when the gate is unconfigured, and also once every source has
        been indeterminate past the grace window (``FELL_BACK``). While a source
        is determinate this is the live verdict; within the grace window it is
        the held last-known one (``HOLDING``).
        """
        if not self.is_configured:
            return None
        resolution = self._resolve()
        # Belt-and-braces, carried over from the daytime gate this was extracted
        # from: ``Resolution.value`` is already ``None`` on FELL_BACK at both of
        # GracefulSource's return sites, so this branch is redundant today. It
        # stays because it states the tri-state mapping at the consuming site
        # instead of making it depend on a kernel invariant that is not visible
        # from here.
        if resolution.state is SourceResolution.FELL_BACK:
            return None
        return resolution.value

    def resolved(self, default: bool) -> bool:
        """:pyattr:`effective` with the caller's fallback substituted for ``None``."""
        effective = self.effective
        return default if effective is None else effective

    def seconds_until_fallback(self) -> float | None:
        """Seconds until a HELD verdict expires, or ``None`` when no wake is needed.

        ``None`` while the gate is determinate, never observed, already fallen
        back, or unconfigured. Callers use it to schedule a single refresh so
        the fallback engages at grace expiry rather than at the next incidental
        update.
        """
        self._resolve()
        return self._graceful.remaining()
