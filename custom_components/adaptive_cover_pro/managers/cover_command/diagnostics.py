"""Diagnostic recording for cover_command.

Owns the ``last_cover_action`` and ``last_skipped_action`` snapshot dicts
plus the event-buffer recording for skipped/sent commands. Keeping these
out of :class:`CoverCommandService` lets the orchestrator focus on
positioning while diagnostics evolves separately (new fields, new event
shapes, etc.).

Skip code stability note (``CODING_GUIDELINES.md`` line 186): the
``last_skipped_action`` dict shape and the ``cover_command_skipped`` event
shape are part of the integration's diagnostic contract — see
``tests/test_skip_reason_guard.py`` and the documented schema. This
module preserves both verbatim.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ...diagnostics.event_buffer import EventBuffer


class DiagnosticsRecorder:
    """Records last-action / last-skip snapshots and pushes them into the event buffer.

    The two snapshot dicts are mutable and exposed directly to callers:
    coordinator.py reads ``last_cover_action`` for the diagnostics builder,
    and ``apply_position`` occasionally pokes a ``dry_run`` key into the
    same dict from outside this module. That direct-mutation contract is
    preserved.
    """

    def __init__(self, event_buffer: EventBuffer | None = None) -> None:
        """Initialize the recorder.

        Args:
            event_buffer: Optional shared diagnostic ring buffer. When
                provided, skip and sent events are appended.

        """
        self._event_buffer = event_buffer
        self.last_cover_action: dict[str, Any] = {
            "entity_id": None,
            "service": None,
            "position": None,
            "calculated_position": None,
            "threshold_used": None,
            "inverse_state_applied": False,
            "timestamp": None,
            "covers_controlled": 0,
        }
        self.last_skipped_action: dict[str, Any] = {
            "entity_id": None,
            "reason": None,
            "calculated_position": None,
            "current_position": None,
            "trigger": None,
            "inverse_state_applied": False,
            "timestamp": None,
        }

    # ------------------------------------------------------------------ #
    # Skipped action
    # ------------------------------------------------------------------ #

    def record_skipped_action(
        self,
        entity: str,
        reason: str,
        state: int,
        *,
        trigger: str = "",
        current_position: int | None = None,
        inverse_state: bool = False,
        extras: dict | None = None,
    ) -> None:
        """Record a skipped cover action snapshot.

        Builds the diagnostic dict and stores it as ``last_skipped_action``.
        Does NOT push to the event buffer — that's
        :meth:`record_skip_event`'s job, kept separate so ``record_skipped_action``
        can be called from contexts (e.g. the coordinator's pre-pipeline
        skip path) where event-buffer recording is not appropriate.
        """
        record: dict[str, Any] = {
            "entity_id": entity,
            "reason": reason,
            "calculated_position": state,
            "current_position": current_position,
            "trigger": trigger or None,
            "inverse_state_applied": inverse_state,
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        }
        if extras:
            record.update(extras)
        self.last_skipped_action = record

    def record_skip_event(
        self,
        entity_id: str,
        reason: str,
        position: int,
        *,
        trigger: str = "",
        inverse_state: bool = False,
        current_position: int | None = None,
        extras: dict | None = None,
    ) -> None:
        """Push a ``cover_command_skipped`` event into the event buffer."""
        if self._event_buffer is None:
            return
        event: dict = {
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "event": "cover_command_skipped",
            "entity_id": entity_id,
            "reason": reason,
            "calculated_position": position,
            "current_position": current_position,
            "trigger": trigger,
            "inverse_state_applied": inverse_state,
        }
        if extras:
            event.update(extras)
        self._event_buffer.record(event)

    # ------------------------------------------------------------------ #
    # Sent action
    # ------------------------------------------------------------------ #

    def record_action(
        self,
        entity: str,
        service: str,
        state: int,
        supports_position: bool,
        *,
        threshold_used: int | None,
        recorded_target: int | None,
        inverse_state: bool = False,
        target_source: str = "",
        force: bool = False,
        is_safety: bool = False,
        trigger: str = "",
        auto_control_at_call: bool | None = None,
        manual_override_at_call: bool | None = None,
        in_time_window_at_call: bool | None = None,
        enabled_at_call: bool | None = None,
        pipeline_handler: str | None = None,
        pipeline_control_method: str | None = None,
        pipeline_bypass_auto_control: bool | None = None,
        decision_trace_at_call: list | None = None,
        gates_evaluated: dict | None = None,
        queue_fields: dict | None = None,
    ) -> None:
        """Update last_cover_action and push a ``cover_command_sent`` event.

        ``recorded_target`` is what the orchestrator wrote into
        ``PerEntityState.target`` for this entity — used as ``position`` in
        the snapshot when the cover lacks set_position (open/close routing
        records 0 or 100, not the requested state).

        ``queue_fields`` carries the command-queue outcome for this dispatch
        (issue #1189) and is merged in ONLY for a queued cover, so an unqueued
        cover's record keeps exactly the shape it had before the queue existed.
        """
        ts = dt.datetime.now(dt.UTC).isoformat()
        position = state if supports_position else recorded_target
        self.last_cover_action = {
            "entity_id": entity,
            "service": service,
            "position": position,
            "calculated_position": state,
            "threshold_used": threshold_used,
            "inverse_state_applied": inverse_state,
            "timestamp": ts,
            "covers_controlled": 1,
            "target_source": target_source,
            "force": force,
            "is_safety": is_safety,
            "trigger": trigger,
            "auto_control_at_call": auto_control_at_call,
            "manual_override_at_call": manual_override_at_call,
            "in_time_window_at_call": in_time_window_at_call,
            "enabled_at_call": enabled_at_call,
            "pipeline_handler": pipeline_handler,
            "pipeline_control_method": pipeline_control_method,
            "pipeline_bypass_auto_control": pipeline_bypass_auto_control,
            "decision_trace_at_call": decision_trace_at_call,
            "gates_evaluated": gates_evaluated,
        }
        if queue_fields:
            self.last_cover_action.update(queue_fields)
        if self._event_buffer is not None:
            event = {
                "ts": ts,
                "event": "cover_command_sent",
                "entity_id": entity,
                "service": service,
                "position": position,
                "calculated_position": state,
                "inverse_state_applied": inverse_state,
                "supports_position": supports_position,
                "trigger": trigger,
                "target_source": target_source,
                "force": force,
                "is_safety": is_safety,
            }
            if queue_fields:
                event.update(queue_fields)
            self._event_buffer.record(event)
