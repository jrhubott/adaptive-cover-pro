"""Cover-state-change classification for manual-override detection.

`StateClassifier` is the single piece of code that decides whether a
post-command cover state change represents the integration's own transit
(grace period, mid-transit pause, forward progress, settle) or genuine
user activity (manual override).  The logic stays *byte-for-byte
equivalent* to the body that lived inline on the coordinator before this
extraction — every issue-fix comment is preserved verbatim.  Refactoring
this code is not in scope; relocating it is.

Background — the inline implementation accumulated five issue-numbered
behaviour fixes over its lifetime:

- **#147** — clearing wait_for_target on intermediate states caused
  user-initiated moves to be mis-attributed to ACP.
- **#172** — startup motor-engagement delay must keep wait_for_target so
  ACP-commanded covers aren't false-flagged before they move.
- **#186** — step-motor covers briefly report a non-transitional state
  between pulses; restart the grace period instead of clearing.
- **#271** — progress-aware backstop: clear wait_for_target only after
  *transit_timeout* seconds without forward progress, so slow-but-moving
  covers are not prematurely cleared.
- **#285** — direction/progress check runs for covers that never emit
  "opening"/"closing", based purely on position delta.

The classifier is composed by :class:`CoverCommandService` and accessed
through its public :meth:`classify_state_change` wrapper.  External state
flows in as keyword arguments:

- ``target_just_reached`` is a ``set[str]`` owned by the coordinator and
  *mutated in place* — the coordinator reads it from another handler
  inside the same event lifecycle, and changing that contract is out of
  scope for this phase.
- ``grace_mgr`` is the same :class:`GracePeriodManager` the cover
  command service already composes; it is passed by argument here so the
  classifier never reaches back into the service for it.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from .transit import is_state_in_transit

if TYPE_CHECKING:
    from homeassistant.core import Event

    from ...diagnostics.event_buffer import EventBuffer
    from ...managers.grace_period import GracePeriodManager


DebugLogFn = Callable[..., None]


class StateClassifier:
    """Classify post-command cover state changes for manual-override detection."""

    def __init__(
        self,
        service: Any,
        *,
        event_buffer: EventBuffer | None,
        debug_log: DebugLogFn,
    ) -> None:
        """Bind the classifier to its long-lived collaborators.

        ``service`` is the :class:`CoverCommandService` instance — the
        classifier reads waiting / target / capability / progress state
        through its public surface.
        """
        self._service = service
        self._event_buffer = event_buffer
        self._debug_log = debug_log
        self._logger = getattr(service, "_logger", None)

    def classify(  # noqa: C901
        self,
        event: Event,
        *,
        ignore_intermediate_states: bool,
        target_just_reached: set[str],
        grace_mgr: GracePeriodManager,
    ) -> None:
        """Decide whether ``event`` is ACP-driven transit or a manual move.

        Mutates ``target_just_reached`` and (via the bound service) clears
        ``wait_for_target`` when the cover has settled at the commanded
        position, runs out of progress, or otherwise enters a state where
        the manual-override detector should run on the next event.
        """
        svc = self._service
        cmd_svc = svc  # legacy name from inline body
        logger = self._logger
        entity_id = event.entity_id
        if logger is not None:
            logger.debug("Processing state change event: %s", event)
        if ignore_intermediate_states and is_state_in_transit(event.new_state.state):
            if logger is not None:
                logger.debug("Ignoring intermediate state change for %s", entity_id)
            return
        if cmd_svc.is_waiting_for_target(entity_id):
            # Check if still in grace period
            if grace_mgr.is_in_command_grace_period(entity_id):
                if logger is not None:
                    logger.debug(
                        "Position change for %s ignored (in grace period)", entity_id
                    )
                return  # Ignore ALL position changes during grace period

            # Grace period expired — check if cover reached target (tolerance-based)
            caps = cmd_svc.get_cover_capabilities(entity_id)
            position = cmd_svc.read_position_with_capabilities(
                entity_id, caps, event.new_state
            )
            reached = cmd_svc.check_target_reached(entity_id, position)
            if reached:
                # Mark this entity so async_handle_cover_state_change() skips the
                # manual override comparison for this event.  The cover has just
                # settled at its commanded position (within position tolerance) —
                # any small positional difference is motor rounding, not a user
                # action.  The set is cleared at the end of that handler.
                target_just_reached.add(entity_id)
                self._debug_log(
                    "manual_override",
                    "Target just reached for %s — skipping manual override check for this event",
                    entity_id,
                )
            else:
                # Grace period expired but the cover is not at the commanded target.
                # Determine whether the cover is still actively moving toward the
                # target (integration-initiated transit) or has stopped / moved away
                # (user action — Issue #147).
                #
                # HA covers report transitional states ("opening"/"closing") while
                # moving, then a final state ("stopped"/"open"/"closed") when done.
                # If ignore_intermediate_states is True, those transitional events
                # are already filtered out above, so we only reach here with final
                # states and always clear wait_for_target.
                #
                # However, some covers (French volet-roulant, some Zigbee rolling
                # shutters) never emit transitional states at all — they stay "open"
                # or "closed" throughout transit and simply update current_position.
                # The direction/progress check therefore runs for ALL covers based
                # on position delta, regardless of the HA state string (Issue #285).
                cover_is_transitioning = is_state_in_transit(event.new_state.state)

                old_position = cmd_svc.read_position_with_capabilities(  # noqa: SLF001
                    entity_id, caps, event.old_state
                )
                target = cmd_svc.get_target(entity_id)

                # Step-motor pause (Issue #186) takes highest priority: some covers
                # briefly report "open"/"closed" at an intermediate position between
                # motor pulses before resuming transit.  If the *new* state is
                # non-transitional and the *old* state was transitional, the cover
                # just paused mid-transit — restart grace period to let the motor
                # resume.  This must be checked before the direction/progress block
                # so that forward-progress detection (Issue #285) does not short-
                # circuit the grace-period restart.
                was_transitioning = event.old_state is not None and is_state_in_transit(
                    event.old_state.state
                )
                if (
                    not cover_is_transitioning
                    and was_transitioning
                    and target is not None
                    and position is not None
                    and position != target
                ):
                    grace_mgr.start_command_grace_period(entity_id)
                    self._debug_log(
                        "manual_override",
                        "Cover %s paused mid-transit at %s (target %s) "
                        "— restarting grace period",
                        entity_id,
                        position,
                        target,
                    )
                    if logger is not None:
                        logger.debug("Wait for target: %s", cmd_svc.waiting_entities())
                    return

                # Optimistic-target guard (Issue #518): see _check_optimistic_guard.
                now = dt.datetime.now(dt.UTC)
                if self._check_optimistic_guard(
                    event, entity_id, old_position, position, target,
                    cmd_svc=cmd_svc, grace_mgr=grace_mgr, now=now, logger=logger,
                ):
                    return

                # Direction/progress check: runs for all covers where positions and
                # target are known, EXCEPT when HA explicitly reports "stopped" (an
                # unambiguous halt signal).  This extends the progress-aware backstop
                # from Issue #271 to covers that never emit "opening"/"closing".
                if (
                    old_position is not None
                    and position is not None
                    and target is not None
                    and event.new_state.state != "stopped"
                ):
                    old_distance = abs(old_position - target)
                    new_distance = abs(position - target)

                    if new_distance < old_distance:
                        # Unambiguously moving toward target — still in transit.
                        # Reset the progress clock so the backstop window extends.
                        now = dt.datetime.now(dt.UTC)
                        cmd_svc.record_progress(entity_id, now)  # noqa: SLF001
                        self._debug_log(
                            "manual_override",
                            "Grace expired but %s still in transit toward "
                            "target %s (was %s, now %s, state=%s) "
                            "— keeping wait_for_target",
                            entity_id,
                            target,
                            old_position,
                            position,
                            event.new_state.state,
                        )
                        if self._event_buffer is not None:
                            self._event_buffer.record(
                                {
                                    "ts": now.isoformat(),
                                    "event": "transit_progress_forward",
                                    "entity_id": entity_id,
                                    "old_position": old_position,
                                    "new_position": position,
                                    "target": target,
                                    "old_distance": old_distance,
                                    "new_distance": new_distance,
                                    "cover_state": event.new_state.state,
                                }
                            )
                        if logger is not None:
                            logger.debug(
                                "Wait for target: %s", cmd_svc.waiting_entities()
                            )
                        return

                    # Progress-aware backstop: if no forward progress has been
                    # observed for longer than the configured transit timeout,
                    # the cover is stuck or stalled — clear wait_for_target so
                    # manual override detection can run.  The clock resets each
                    # time record_progress() is called (when new_distance <
                    # old_distance above), so slow-but-moving covers are not
                    # prematurely cleared.  Covers that never report intermediate
                    # positions fall back to measuring from _sent_at.
                    now = dt.datetime.now(dt.UTC)
                    elapsed = cmd_svc.transit_elapsed_without_progress(entity_id, now)
                    if elapsed is not None:
                        timeout = cmd_svc.transit_timeout_seconds
                        if elapsed > timeout:
                            cmd_svc.set_waiting(entity_id, False)
                            self._debug_log(
                                "manual_override",
                                "Transit timeout for %s (%.0fs > %ds without progress) "
                                "— clearing wait_for_target",
                                entity_id,
                                elapsed,
                                timeout,
                            )
                            if self._event_buffer is not None:
                                self._event_buffer.record(
                                    {
                                        "ts": now.isoformat(),
                                        "event": "transit_timeout_cleared",
                                        "entity_id": entity_id,
                                        "elapsed_seconds": round(elapsed, 1),
                                        "timeout_seconds": timeout,
                                        "position": position,
                                        "target": target,
                                        "cover_state": event.new_state.state,
                                    }
                                )
                            if logger is not None:
                                logger.debug(
                                    "Wait for target: %s", cmd_svc.waiting_entities()
                                )
                            return

                    if new_distance == old_distance:
                        # Positions equal — could be startup delay or stall.
                        # Startup delay: motor just engaged; state transitions from
                        # non-transitional (e.g. "closed") to something else.
                        # Stall: state didn't change and cover was already in transit;
                        # fall through to clear (e.g. opening→opening same position).
                        # open→open same position with no state change is also a
                        # genuine stop — fall through (Issue #172 regression guard).
                        old_state_str = (
                            event.old_state.state
                            if event.old_state is not None
                            else None
                        )
                        new_state_str = event.new_state.state
                        state_changed = old_state_str != new_state_str
                        if not is_state_in_transit(old_state_str) and state_changed:
                            self._debug_log(
                                "manual_override",
                                "Grace expired but %s position unchanged at %s "
                                "(startup delay — old state was %s) "
                                "— keeping wait_for_target",
                                entity_id,
                                position,
                                old_state_str,
                            )
                            if self._event_buffer is not None:
                                self._event_buffer.record(
                                    {
                                        "ts": dt.datetime.now(dt.UTC).isoformat(),
                                        "event": "transit_startup_delay",
                                        "entity_id": entity_id,
                                        "position": position,
                                        "old_state": old_state_str,
                                        "new_state": new_state_str,
                                        "target": target,
                                    }
                                )
                            if logger is not None:
                                logger.debug(
                                    "Wait for target: %s", cmd_svc.waiting_entities()
                                )
                            return

                # Clear wait_for_target to allow manual override detection.
                cmd_svc.set_waiting(entity_id, False)
                self._debug_log(
                    "manual_override",
                    "Grace period expired, cover %s not in transit "
                    "— clearing wait_for_target "
                    "(position=%s, state=%s)",
                    entity_id,
                    position,
                    event.new_state.state,
                )
                if self._event_buffer is not None:
                    self._event_buffer.record(
                        {
                            "ts": dt.datetime.now(dt.UTC).isoformat(),
                            "event": "transit_cleared",
                            "entity_id": entity_id,
                            "position": position,
                            "cover_state": event.new_state.state,
                            "old_position": old_position,
                            "target": target,
                        }
                    )
            if logger is not None:
                logger.debug("Wait for target: %s", cmd_svc.waiting_entities())
        else:
            if logger is not None:
                logger.debug("No wait for target call for %s", entity_id)

    def _check_optimistic_guard(
        self,
        event: Any,
        entity_id: str,
        old_position: int | None,
        position: int | None,
        target: int | None,
        *,
        cmd_svc: Any,
        grace_mgr: Any,
        now: dt.datetime,
        logger: Any,
    ) -> bool:
        """Detect and handle the optimistic-target-replay signature (Issue #518).

        Some cover firmware reports the commanded target position immediately
        (before the motor moves), then updates to the real intermediate position
        as the carriage travels.  When the grace period expires, the classifier
        sees ``old_position == target`` (the optimistic report) and
        ``position != target`` (the real intermediate), computing
        ``old_distance = 0``, ``new_distance > 0`` — which the direction/progress
        block misidentifies as drift-away and incorrectly clears ``wait_for_target``.

        Detection signature: ``old_position == target AND position != target``
        AND still within the 45-second transit-timeout backstop window.  The
        backstop is the ultimate safety net: an optimistic cover that stalls
        off-target is still cleared after 45 s.  True drift-away (Issue #285)
        always starts from ``old_position != target``, so this guard leaves that
        case untouched.

        Returns:
            ``True`` if the guard fired and ``classify`` should return immediately
            (``wait_for_target`` is kept), ``False`` otherwise.

        """
        if not (
            target is not None
            and old_position is not None
            and old_position == target
            and position is not None
            and position != target
        ):
            return False

        elapsed = cmd_svc.transit_elapsed_without_progress(entity_id, now)
        timeout = cmd_svc.transit_timeout_seconds
        if elapsed is not None and elapsed >= timeout:
            # Backstop exceeded — let the caller fall through to clear.
            return False

        grace_mgr.start_command_grace_period(entity_id)
        cmd_svc.record_progress(entity_id, now)
        self._debug_log(
            "manual_override",
            "Cover %s reported optimistic target %s before moving; "
            "real position now %s — restarting grace period "
            "(transit_elapsed=%.1fs)",
            entity_id,
            target,
            position,
            elapsed if elapsed is not None else 0.0,
        )
        if self._event_buffer is not None:
            self._event_buffer.record(
                {
                    "ts": now.isoformat(),
                    "event": "transit_optimistic_target_replay",
                    "entity_id": entity_id,
                    "old_position": old_position,
                    "position": position,
                    "target": target,
                    "cover_state": event.new_state.state,
                }
            )
        if logger is not None:
            logger.debug("Wait for target: %s", cmd_svc.waiting_entities())
        return True
