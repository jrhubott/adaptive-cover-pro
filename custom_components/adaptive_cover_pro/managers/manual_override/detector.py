"""Pluggable manual-override detection strategy interface.

A detector is a pure decision strategy — neither a manager (it holds no
per-instance HA state) nor a ``CoverTypePolicy`` (it is not keyed by cover
type). It inspects an immutable :class:`DetectionContext` and returns an
:class:`OverrideDecision` describing what the engine should do. The engine
(:class:`..manager.AdaptiveCoverManager`) owns all state and side effects;
a detector never mutates the engine.

``detect`` is the only abstract method. Every other hook has a
behaviour-preserving default so a new detection pattern overrides only what
it needs — and, because :class:`DetectionContext` already carries every
signal, a new pattern is a drop-in (one new file + one registry line) with no
coordinator changes.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class DetectionContext:
    """Complete immutable snapshot of one cover state change.

    Carries every signal a detector could need so a new detection pattern
    never requires new wiring through the coordinator.
    """

    entity_id: str
    our_state: int  # commanded/expected primary-axis position
    new_state: Any  # HA State: .state / .attributes / .last_updated / .context
    old_state: Any | None
    new_position: int | None  # resolved via policy.read_axis_value
    old_position: int | None  # prior primary-axis value (resolved from old_state)
    caps: Any  # CoverCapabilities
    policy: Any  # CoverTypePolicy
    manual_threshold: int | None
    # Whether ACP has a recorded command target for this entity. When False,
    # ``our_state`` is the pipeline's theoretical default rather than a
    # commanded position, so a numeric delta against it is meaningless (#546).
    has_recorded_target: bool
    allow_reset: bool
    is_acp_context: bool  # precomputed was_acp_position_context(ctx.id)
    context_user_id: str | None
    context_id: str | None
    seconds_since_command: float | None  # engine-tracked since note_command_sent
    secondary_axis_check: Any | None
    is_waiting: Callable[[str], bool]
    is_in_command_grace: Callable[[str], bool]
    is_in_transit: Callable[[str], bool]
    now: dt.datetime


@dataclass(frozen=True, slots=True)
class OverrideDecision:
    """What the engine should do in response to a detector verdict.

    The empty ``OverrideDecision()`` means "nothing happens" (no event, no
    mark). ``record_primary_axis_suppression`` lets a detector ask the engine
    to perform the Issue-#33 bookkeeping without touching that state itself.
    ``allow_reset`` overrides the context's value when setting the timestamp.
    """

    mark_manual: bool = False
    event_name: str | None = None
    event_kwargs: dict[str, Any] | None = None
    record_primary_axis_suppression: bool = False
    suppression_delta: float | None = None
    allow_reset: bool | None = None


@dataclass(frozen=True, slots=True)
class UserContextChange:
    """Input for the user-context channel (HA-context-confirmed user action)."""

    entity_id: str
    new_state: Any
    allow_reset: bool
    context_user_id: str | None
    context_id: str | None


@dataclass(frozen=True, slots=True)
class StopToMy:
    """Input for the stop-service channel (user stop_cover to the 'My' preset)."""

    entity_id: str
    my_position_value: int
    is_waiting: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Config bundle handed to a detector at construction and on option changes.

    ``command_window_seconds`` comes from ``CONF_TRANSIT_TIMEOUT``. The
    manual-override slice fields (``reset``/``duration``/``ignore_external``)
    are engine-level but exposed so a detector MAY use them.
    """

    manual_threshold: int | None
    command_window_seconds: float
    reset: bool
    duration: dict
    ignore_external: bool


def default_user_context_decision(change: UserContextChange) -> OverrideDecision:
    """Today's behaviour: a confirmed user-context change marks manual override."""
    return OverrideDecision(
        mark_manual=True,
        event_name="manual_override_set",
        event_kwargs={
            "our_state": None,
            "new_position": None,
            "reason": (
                f"user-initiated state change "
                f"(context user_id={change.context_user_id}, id={change.context_id})"
            ),
        },
    )


def default_stop_to_my_decision(stop: StopToMy) -> OverrideDecision | None:
    """Today's behaviour: stop_cover to 'My' marks manual unless mid-command.

    Returns ``None`` when the cover is still moving toward a commanded target
    (the stop is the cover reaching that target, not a user touch).
    """
    if stop.is_waiting(stop.entity_id):
        return None
    return OverrideDecision(
        mark_manual=True,
        event_name="manual_override_set",
        event_kwargs={
            "our_state": stop.my_position_value,
            "new_position": stop.my_position_value,
            "reason": "user stop_cover to My position",
        },
    )


class OverrideDetector(ABC):
    """Pluggable manual-override detection strategy.

    ``detect`` is the only abstract method; every other hook has a
    behaviour-preserving default so a subclass overrides only what its pattern
    needs. Detectors DECIDE; the engine owns state and side effects (a detector
    never mutates the manager).
    """

    strategy_id: ClassVar[str]

    @classmethod
    def from_config(cls, config: DetectorConfig) -> OverrideDetector:  # noqa: ARG003
        """Build a detector instance from config. Default ignores config."""
        return cls()

    # --- core decision (required) ---
    @abstractmethod
    def detect(self, context: DetectionContext) -> OverrideDecision:
        """Decide whether a primary-axis state change is a manual override."""

    # --- channel hooks (defaults reproduce today's behaviour) ---
    def on_user_context_change(
        self, change: UserContextChange
    ) -> OverrideDecision | None:
        """HA-context-confirmed user action. Default: mark manual."""
        return default_user_context_decision(change)

    def on_stop_to_my(self, stop: StopToMy) -> OverrideDecision | None:
        """User stop_cover to the hardware 'My' preset. Default: mark unless waiting."""
        return default_stop_to_my_decision(stop)

    # --- lifecycle hooks (default no-op) ---
    def note_command_sent(self, entity_id: str, now: dt.datetime) -> None:
        """ACP just issued a command to ``entity_id`` (stateful detectors hook here)."""

    def on_marked(self, entity_id: str) -> None:
        """Handle a cover transitioning into manual override."""

    def on_reset(self, entity_id: str) -> None:
        """Handle a cover's manual override being cleared."""

    def on_covers_added(self, entities) -> None:
        """Covers were registered for tracking."""

    # --- runtime config + persistence (defaults: no-op / stateless) ---
    def update_config(self, config: DetectorConfig) -> None:
        """Apply an options change without a reload."""

    def serialize_state(self) -> dict:
        """Return detector-specific state to persist across restart (default: none)."""
        return {}

    def restore_state(self, data: dict) -> None:
        """Rehydrate detector-specific state from :meth:`serialize_state`."""
