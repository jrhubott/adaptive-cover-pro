"""Cover command service for Adaptive Cover Pro."""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import deque
from typing import Any

from homeassistant.components.cover.const import DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.template import state_attr

from ..const import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CONF_DEFAULT_HEIGHT,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_POS,
    MAX_POSITION_RETRIES,
    POSITION_CHECK_INTERVAL_MINUTES,
    POSITION_TOLERANCE_PERCENT,
)
from ..helpers import check_cover_features, get_last_updated, get_open_close_state, should_use_tilt


@dataclasses.dataclass
class PositionContext:
    """Context passed to apply_position() describing current coordinator state.

    The coordinator builds this each time it wants to move a cover, passing in
    all the contextual flags that govern whether the command should actually be
    sent. CoverCommandService uses these instead of reaching back into the
    coordinator.

    """

    auto_control: bool
    manual_override: bool
    sun_just_appeared: bool
    min_change: int
    time_threshold: int
    special_positions: list[int]
    inverse_state: bool = False
    force: bool = False  # Skip all gate checks when True
    is_safety: bool = (
        False  # Safety-critical target (persists across window boundaries)
    )
    use_my_position: bool = (
        False  # Route through send_my_position() on non-position-capable covers
    )


class CoverCommandService:
    """Self-contained service for positioning cover entities.

    Owns the full cover positioning lifecycle:
    - Gate checks (auto control, time window, delta, time, manual override)
    - Service call preparation and execution
    - Target tracking (wait_for_target, target_call)
    - Reconciliation timer: every minute, re-sends target if cover missed it
    - Diagnostic tracking (last action, last skipped action)

    Usage:
        1. Call ``start()`` after HA is ready (first refresh).
        2. Call ``apply_position(entity_id, position, reason, context=ctx)``
           whenever the desired position changes.
        3. Call ``stop()`` on shutdown/unload.
        4. Call ``check_target_reached(entity_id, reported_position)`` from
           the coordinator's cover-state-change handler.

    """

    # How long to wait before force-clearing wait_for_target (seconds).
    # Covers that never report final position won't be stuck indefinitely.
    _WAIT_FOR_TARGET_TIMEOUT_SECONDS = 30

    # Default capabilities for covers when entity not ready
    _DEFAULT_CAPABILITIES = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        cover_type: str,
        grace_mgr,
        open_close_threshold: int = 50,
        check_interval_minutes: int = POSITION_CHECK_INTERVAL_MINUTES,
        position_tolerance: int = POSITION_TOLERANCE_PERCENT,
        max_retries: int = MAX_POSITION_RETRIES,
        on_tick=None,
    ) -> None:
        """Initialize the CoverCommandService.

        Args:
            hass: Home Assistant instance
            logger: Logger instance
            cover_type: Cover type string (cover_blind, cover_awning, cover_tilt)
            grace_mgr: GracePeriodManager instance
            open_close_threshold: Threshold (0-100) for open/close-only covers
            check_interval_minutes: How often reconciliation runs (minutes)
            position_tolerance: Allowed deviation between target and actual (%)
            max_retries: Max reconciliation attempts per target before giving up
            on_tick: Optional async callable(now) invoked at the start of each
                reconciliation tick. Use for coordinator-level periodic work
                (e.g. time window transition checks) that must run on the same
                interval without an extra timer.

        """
        self._hass = hass
        self._logger = logger
        self._cover_type = cover_type
        self._grace_mgr = grace_mgr
        self._open_close_threshold = open_close_threshold
        self._check_interval_minutes = check_interval_minutes
        self._position_tolerance = position_tolerance
        self._max_retries = max_retries
        self._on_tick = on_tick

        # Per-entity positioning state
        # target_call: last position we decided to send (the "desired" position)
        # _sent_at: when we last executed a service call for this entity
        # wait_for_target: True while cover is expected to still be moving
        # _retry_counts: how many reconciliation retries for the current target
        # _gave_up: entities that have hit max_retries (cleared on new target)
        self.target_call: dict[str, int] = {}
        self._sent_at: dict[str, dt.datetime] = {}
        self.wait_for_target: dict[str, bool] = {}
        self._retry_counts: dict[str, int] = {}
        self._gave_up: set[str] = set()

        # Context ids of cover.stop_cover calls that ACP itself originated.
        # The EVENT_CALL_SERVICE listener in the coordinator uses this to
        # distinguish our own stop commands from user-initiated stops.
        self._acp_stop_contexts: deque[str] = deque(maxlen=16)

        # Entities currently under manual override — reconciliation skips these
        # so it doesn't fight the user by resending the old integration target.
        # Updated by the coordinator after every manual override state change.
        # Safety handlers (force override, weather) overwrite target_call via
        # apply_position(is_safety=True) so they always take effect regardless.
        self._manual_override_entities: set[str] = set()

        # Whether automatic control is currently enabled.  Synced by the
        # coordinator each update cycle (alongside manual_override_entities).
        # Reconciliation skips non-safety targets when this is False so it
        # doesn't fight the user's intention to pause automation.
        self._auto_control_enabled: bool = True

        # Entities whose current target_call was set via apply_position(is_safety=True).
        # These are safety targets (force override, weather) and reconciliation
        # must still resend them even when _auto_control_enabled is False or the
        # time window is closed.  Cleared when a non-safety target overwrites the entry.
        self._safety_targets: set[str] = set()

        # Whether the coordinator's operational time window is currently active.
        # Synced by the coordinator each update cycle (alongside auto_control_enabled).
        # Reconciliation skips non-safety targets when this is False so stale
        # daytime targets are not resent overnight.
        self._in_time_window: bool = True

        # Master kill switch — when False, ALL outbound cover commands are blocked,
        # including safety handlers (force override, weather) and reconciliation.
        # Synced by the coordinator each update cycle from the Integration Enabled switch.
        self._enabled: bool = True

        # Dry-run mode — when True, no outbound cover commands are sent, but the
        # full update cycle (pipeline, diagnostics, sensors) runs normally.
        # Synced by the coordinator each update cycle from the Debug & Diagnostics option.
        self._dry_run: bool = False

        # Last reconciliation timestamps per entity (for diagnostics sensor)
        self._last_reconcile_time: dict[str, dt.datetime] = {}

        # Diagnostic tracking
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

        # Reconciliation timer handle (async_track_time_interval unsubscribe fn)
        self._reconcile_unsub = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the internal reconciliation timer.

        Call once after first refresh. Safe to call multiple times (no-op if
        already running).

        """
        if self._reconcile_unsub is not None:
            return  # Already started

        interval = dt.timedelta(minutes=self._check_interval_minutes)
        self._reconcile_unsub = async_track_time_interval(
            self._hass,
            self._reconcile,
            interval,
        )
        self._logger.debug(
            "CoverCommandService: reconciliation timer started (interval: %s)", interval
        )

    def stop(self) -> None:
        """Stop the internal reconciliation timer.

        Call on integration unload / coordinator shutdown.

        """
        if self._reconcile_unsub is not None:
            self._reconcile_unsub()
            self._reconcile_unsub = None
            self._logger.debug("CoverCommandService: reconciliation timer stopped")

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def is_tilt_cover(self) -> bool:
        """Check if this is a tilt cover."""
        return self._cover_type == "cover_tilt"

    @property
    def manual_override_entities(self) -> set[str]:
        """Return the set of entities currently under manual override."""
        return self._manual_override_entities

    @manual_override_entities.setter
    def manual_override_entities(self, entities: set[str]) -> None:
        """Update the set of entities under manual override.

        Called by the coordinator after each update cycle so reconciliation
        knows which entities to skip.  Safety handlers (force override,
        weather) overwrite target_call via apply_position(is_safety=True) so
        they always take effect regardless of this set.
        """
        self._manual_override_entities = set(entities)

    @property
    def auto_control_enabled(self) -> bool:
        """Whether automatic control is currently enabled."""
        return self._auto_control_enabled

    @auto_control_enabled.setter
    def auto_control_enabled(self, value: bool) -> None:
        """Update the automatic control flag.

        Called by the coordinator each update cycle so reconciliation knows
        whether to resend non-safety targets.  When False, only targets that
        were sent via apply_position(is_safety=True) — i.e. safety overrides —
        are eligible for reconciliation resends.
        """
        self._auto_control_enabled = value

    @property
    def in_time_window(self) -> bool:
        """Whether the coordinator's operational time window is currently active."""
        return self._in_time_window

    @in_time_window.setter
    def in_time_window(self, value: bool) -> None:
        """Update the time window flag.

        Called by the coordinator each update cycle so reconciliation knows
        whether to resend non-safety targets.  When False, only safety targets
        (sent via apply_position(is_safety=True)) are eligible for reconciliation.
        """
        self._in_time_window = value

    @property
    def enabled(self) -> bool:
        """Whether the integration is enabled (master kill switch)."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Update the integration enabled flag.

        When False, ALL outbound cover commands are blocked — including safety
        handlers (force override, weather) and reconciliation.  Synced by the
        coordinator each update cycle from the Integration Enabled switch.
        """
        self._enabled = value

    @property
    def dry_run(self) -> bool:
        """Whether dry-run mode is active (no cover commands sent)."""
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        """Update the dry-run flag.

        When True, the full update cycle runs normally (pipeline, diagnostics,
        sensors) but no outbound cover commands are sent.  Synced by the
        coordinator each update cycle from the Debug & Diagnostics option.
        """
        self._dry_run = value

    def get_entity_state_snapshot(self, entity_id: str) -> dict:
        """Return a diagnostic snapshot of per-entity positioning state."""
        sent_at = self._sent_at.get(entity_id)
        reconcile_time = self._last_reconcile_time.get(entity_id)
        return {
            "target_call": self.target_call.get(entity_id),
            "wait_for_target": self.wait_for_target.get(entity_id, False),
            "retry_count": self._retry_counts.get(entity_id, 0),
            "gave_up": entity_id in self._gave_up,
            "last_command_sent_at": sent_at.isoformat() if sent_at else None,
            "in_manual_override_set": entity_id in self._manual_override_entities,
            "safety_target": entity_id in self._safety_targets,
            "last_reconcile_time": reconcile_time.isoformat()
            if reconcile_time
            else None,
        }

    def get_all_entity_state_snapshots(self) -> dict[str, dict]:
        """Return diagnostic snapshots for all tracked entities."""
        all_ids = set(self.target_call) | set(self.wait_for_target)
        return {eid: self.get_entity_state_snapshot(eid) for eid in sorted(all_ids)}

    def clear_non_safety_targets(self) -> None:
        """Remove non-safety target_call entries so stale targets cannot be resent.

        Called by the coordinator when the time window transitions from
        active to inactive.  Safety targets (force override, weather) are
        preserved so reconciliation can still drive covers to their safe
        position.
        """
        stale = [eid for eid in self.target_call if eid not in self._safety_targets]
        for eid in stale:
            del self.target_call[eid]
            self.wait_for_target.pop(eid, None)
            self._retry_counts.pop(eid, None)
            self._gave_up.discard(eid)
        if stale:
            self._logger.debug(
                "Cleared %d stale non-safety target(s) on window close: %s",
                len(stale),
                stale,
            )

    def discard_target(self, entity_id: str) -> None:
        """Remove all tracking state for an entity, including safety targets.

        Called when a manual override starts for an entity so that any
        pre-existing integration target (including safety-tagged end-time
        defaults) cannot be resurrected by reconciliation while — or after
        — the user is controlling the cover.

        Args:
            entity_id: Cover entity ID to clear.

        """
        had_target = entity_id in self.target_call
        self.target_call.pop(entity_id, None)
        self.wait_for_target.pop(entity_id, None)
        self._sent_at.pop(entity_id, None)
        self._retry_counts.pop(entity_id, None)
        self._gave_up.discard(entity_id)
        self._safety_targets.discard(entity_id)
        if had_target:
            self._logger.debug(
                "Discarded stale target for %s on manual override start",
                entity_id,
            )

    # ------------------------------------------------------------------ #
    # Stop helpers — bypass _enabled gate (shutdown / emergency paths)
    # ------------------------------------------------------------------ #

    def _is_cover_in_motion(self, entity_id: str) -> bool:
        """Return True only if HA reports the cover as opening or closing.

        cover.stop_cover is overloaded on some hardware (Somfy "My", Hunter
        Douglas favorite, etc.) — on stationary covers it triggers a preset
        position move instead of a no-op.  Callers in shutdown/emergency
        paths must gate on actual motion to avoid triggering that preset.

        Note: send_my_position() intentionally does NOT call this method —
        it deliberately sends stop_cover to a stationary cover (that is what
        triggers the My preset).  This gate applies to shutdown paths only.
        """
        state_obj = self._hass.states.get(entity_id)
        if state_obj is None:
            return False
        return state_obj.state in ("opening", "closing")

    async def _call_stop_cover(self, entity_id: str) -> None:
        """Issue cover.stop_cover and record the call context as ACP-originated.

        All ACP-initiated stop_cover calls must go through this helper so that
        the EVENT_CALL_SERVICE listener can identify and ignore them, avoiding
        false manual-override detection.

        Args:
            entity_id: Cover entity_id to stop.

        """
        ctx = Context()
        self._acp_stop_contexts.append(ctx.id)
        await self._hass.services.async_call(
            "cover", "stop_cover", {"entity_id": entity_id}, context=ctx
        )

    async def stop_in_flight(self, entities: set[str] | None = None) -> list[str]:
        """Send stop_cover to every ACP-in-flight entity that supports STOP.

        Intentionally bypasses the ``_enabled`` gate — this IS the shutdown path
        and must fire before the gate closes.

        Args:
            entities: Optional subset of entity_ids to consider.  None = all
                      entries in wait_for_target.

        Returns:
            List of entity_ids that were actually stopped.

        """
        stopped: list[str] = []
        candidates = {
            eid
            for eid, waiting in self.wait_for_target.items()
            if waiting and (entities is None or eid in entities)
        }
        for eid in candidates:
            caps = check_cover_features(self._hass, eid)
            if not (caps and caps.get("has_stop")):
                continue
            if not self._is_cover_in_motion(eid):
                state_val = getattr(self._hass.states.get(eid), "state", None)
                self._logger.debug(
                    "stop_in_flight: skipping %s — not in motion (state=%s); "
                    "clearing stale wait_for_target",
                    eid,
                    state_val,
                )
                self.wait_for_target[eid] = False
                self._sent_at.pop(eid, None)
                continue
            if self._dry_run:
                self._logger.info("[dry_run] would stop_cover %s", eid)
            else:
                await self._call_stop_cover(eid)
            self.wait_for_target[eid] = False
            self._sent_at.pop(eid, None)
            stopped.append(eid)
            self._logger.debug("stop_in_flight: stopped %s", eid)
        return stopped

    async def stop_all(self, entity_ids: list[str]) -> list[str]:
        """Send stop_cover to every entity in entity_ids that supports STOP.

        Used by emergency_stop — does NOT check wait_for_target (blanket stop).
        Intentionally bypasses the ``_enabled`` gate.

        Args:
            entity_ids: List of cover entity_ids to stop.

        Returns:
            List of entity_ids that were actually stopped.

        """
        stopped: list[str] = []
        for eid in entity_ids:
            caps = check_cover_features(self._hass, eid)
            if not (caps and caps.get("has_stop")):
                continue
            if not self._is_cover_in_motion(eid):
                state_val = getattr(self._hass.states.get(eid), "state", None)
                self._logger.debug(
                    "stop_all: skipping %s — not in motion (state=%s)",
                    eid,
                    state_val,
                )
                continue
            if self._dry_run:
                self._logger.info("[dry_run] would stop_cover %s", eid)
            else:
                await self._call_stop_cover(eid)
            stopped.append(eid)
            self._logger.debug("stop_all: stopped %s", eid)
        return stopped

    # ------------------------------------------------------------------ #
    # "My" position (Somfy / favorite preset)
    # ------------------------------------------------------------------ #

    async def send_my_position(self, entity_id: str, target: int) -> bool:
        """Trigger the cover's hardware-stored "My" preset via cover.stop_cover.

        Unlike stop_all/stop_in_flight this DELIBERATELY sends stop_cover to a
        stationary cover — Somfy RTS motors interpret stop-while-stationary as
        "move to My".  The caller has already verified the cover lacks
        set_cover_position and that has_stop is True.

        Records target_call / wait_for_target / _sent_at so reconciliation
        and delta logic treat this exactly like any other positioning command.

        Note: _is_cover_in_motion() is intentionally NOT called here.  That
        gate belongs to the shutdown paths (stop_all / stop_in_flight).
        Sending stop_cover to a stationary cover is the entire point of this
        method — the two paths have opposite requirements.

        Args:
            entity_id: Cover entity_id to trigger.
            target:    The position (0–100) that My represents (user-configured).

        Returns:
            True if the command was sent (or dry-run logged), False if the
            cover lacks has_stop capability.

        """
        caps = check_cover_features(self._hass, entity_id)
        if not (caps and caps.get("has_stop")):
            self._logger.debug(
                "send_my_position: skipping %s — cover does not support STOP", entity_id
            )
            return False
        if self._dry_run:
            self._logger.info(
                "[dry_run] would stop_cover %s (My position = %d%%)", entity_id, target
            )
        else:
            await self._call_stop_cover(entity_id)
        now = dt.datetime.now(dt.UTC)
        self.target_call[entity_id] = target
        self.wait_for_target[entity_id] = True
        self._sent_at[entity_id] = now
        self._retry_counts.pop(entity_id, None)
        self._gave_up.discard(entity_id)
        self._logger.debug(
            "send_my_position: stop_cover sent to %s (My = %d%%)", entity_id, target
        )
        return True

    # ------------------------------------------------------------------ #
    # Threshold update (called by coordinator on options change)
    # ------------------------------------------------------------------ #

    def update_threshold(self, threshold: int) -> None:
        """Update the open/close threshold.

        Args:
            threshold: New threshold value (0-100)

        """
        self._open_close_threshold = threshold

    # ------------------------------------------------------------------ #
    # Capability detection
    # ------------------------------------------------------------------ #

    def get_cover_capabilities(self, entity: str) -> dict[str, bool]:
        """Get cover capabilities with fallback to safe defaults."""
        caps = check_cover_features(self._hass, entity)
        if caps is None:
            self._logger.debug("Cover %s not ready, using safe defaults", entity)
            return self._DEFAULT_CAPABILITIES.copy()
        return caps

    # ------------------------------------------------------------------ #
    # Position reading
    # ------------------------------------------------------------------ #

    def _read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Read position based on cover type and capabilities."""
        use_tilt = should_use_tilt(self.is_tilt_cover, caps)

        if use_tilt:
            if caps.get("has_set_tilt_position", True):
                if state_obj:
                    return state_obj.attributes.get("current_tilt_position")
                return state_attr(self._hass, entity, "current_tilt_position")
        else:
            if caps.get("has_set_position", True):
                if state_obj:
                    return state_obj.attributes.get("current_position")
                return state_attr(self._hass, entity, "current_position")

        return get_open_close_state(self._hass, entity)

    def read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Public wrapper for reading position based on cover capabilities."""
        return self._read_position_with_capabilities(entity, caps, state_obj)

    def _get_current_position(self, entity: str) -> int | None:
        """Get current position of cover (position-capable or open/close-only)."""
        caps = self.get_cover_capabilities(entity)
        return self._read_position_with_capabilities(entity, caps)

    # ------------------------------------------------------------------ #
    # Gate checks (used internally by apply_position)
    # ------------------------------------------------------------------ #

    def _check_position_delta(
        self,
        entity: str,
        target: int,
        min_change: int,
        special_positions: list[int],
        sun_just_appeared: bool = False,
    ) -> bool:
        """Return True if a command should be sent based on position delta.

        Bypasses delta check for:
        - sun_just_appeared (cover may need to re-confirm same position)
        - moves to/from special positions (0, 100, default, sunset)

        """
        position = self._get_current_position(entity)
        if position is None:
            return True  # Unknown position — send command to be safe

        if sun_just_appeared:
            self._logger.debug(
                "Delta check bypassed (sun appeared): %s current=%s target=%s",
                entity,
                position,
                target,
            )
            return True

        if position == target:
            self._logger.debug(
                "Delta check: %s already at target %s%% — no command needed",
                entity,
                target,
            )
            return False  # Cover is already at the desired position; skip regardless of special status

        if target in special_positions:
            self._logger.debug(
                "Delta check bypassed (special target %s): %s", target, entity
            )
            return True

        if position in special_positions:
            self._logger.debug(
                "Delta check bypassed (special current %s): %s", position, entity
            )
            return True

        delta = abs(position - target)
        passes = delta >= min_change
        self._logger.debug(
            "Delta check: %s current=%s target=%s delta=%s min=%s pass=%s",
            entity,
            position,
            target,
            delta,
            min_change,
            passes,
        )
        return passes

    def _check_time_delta(self, entity: str, time_threshold: int) -> bool:
        """Return True if enough time has passed since last command."""
        now = dt.datetime.now(dt.UTC)
        last_updated = get_last_updated(entity, self._hass)
        if last_updated is None:
            return True
        elapsed = now - last_updated
        passes = elapsed >= dt.timedelta(minutes=time_threshold)
        self._logger.debug(
            "Time delta check: %s elapsed=%s threshold=%smin pass=%s",
            entity,
            elapsed,
            time_threshold,
            passes,
        )
        return passes

    # ------------------------------------------------------------------ #
    # Primary entry point
    # ------------------------------------------------------------------ #

    async def apply_position(
        self,
        entity_id: str,
        position: int,
        reason: str,
        context: PositionContext,
    ) -> tuple[str, str]:
        """Evaluate gates and send a cover position command if appropriate.

        This is the single entry point for all cover positioning.  The
        coordinator calls this method from every code path that wants to
        move a cover (solar update, startup, sunset, reconciliation retry,
        motion/weather timeout callbacks, etc.).

        Args:
            entity_id: Cover entity ID to control
            position: Desired target position (0-100, post-interpolation,
                post-inverse already applied by the time it arrives here)
            reason: Human-readable source ("solar", "startup", "sunset",
                "reconciliation", "force_override", ...)
            context: Current coordinator state used for gate checks

        Returns:
            Tuple of (outcome, detail) where outcome is "sent" or "skipped"
            and detail is the service name or skip reason.

        """
        # ----- gate checks (bypassed when context.force is True) -----
        _trigger = reason
        _inverse = context.inverse_state
        _current = self._get_current_position(entity_id)

        # Hard kill switch — blocks ALL commands, including safety overrides and
        # force=True calls.  Must be checked before the context.force branch.
        if not self._enabled:
            return self._skip(
                entity_id,
                "integration_disabled",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        if not context.force:
            if not context.auto_control:
                return self._skip(
                    entity_id,
                    "auto_control_off",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                )

            if not self._check_position_delta(
                entity_id,
                position,
                context.min_change,
                context.special_positions,
                sun_just_appeared=context.sun_just_appeared,
            ):
                _delta = abs(_current - position) if _current is not None else None
                return self._skip(
                    entity_id,
                    "delta_too_small",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                    extras={
                        "position_delta": _delta,
                        "min_delta_required": context.min_change,
                    },
                )

            if not self._check_time_delta(entity_id, context.time_threshold):
                _elapsed = self._elapsed_minutes(entity_id)
                return self._skip(
                    entity_id,
                    "time_delta_too_small",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                    extras={
                        "elapsed_minutes": _elapsed,
                        "time_threshold_minutes": context.time_threshold,
                    },
                )

            if context.manual_override:
                return self._skip(
                    entity_id,
                    "manual_override",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                )

        # ----- send command -----
        service, service_data, supports_position = self._prepare_service_call(
            entity_id,
            position,
            context.inverse_state,
            is_safety=context.is_safety,
            use_my_position=context.use_my_position,
        )
        if service is None:
            return self._skip(
                entity_id,
                "no_capable_service",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        # ----- dry-run gate -----
        if self._dry_run:
            self._logger.info(
                "[dry_run] would send cover.%s %s → %s%%",
                service,
                entity_id,
                position,
            )
            self._track_action(
                entity_id, service, position, supports_position, context.inverse_state
            )
            self.last_cover_action["dry_run"] = True
            return self._skip(
                entity_id,
                "dry_run",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
                extras={"would_send_service": service},
            )

        self._logger.info(
            "[%s] Positioning %s → %s%%",
            reason,
            entity_id,
            position,
        )

        try:
            await self._hass.services.async_call(COVER_DOMAIN, service, service_data)
        except HomeAssistantError as err:
            self._logger.warning(
                "Service call %s.%s failed for %s: %s",
                COVER_DOMAIN,
                service,
                entity_id,
                err,
            )
            return self._skip(
                entity_id,
                "service_call_failed",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        self._track_action(
            entity_id, service, position, supports_position, context.inverse_state
        )
        return "sent", service

    # ------------------------------------------------------------------ #
    # Target-reached notification (called by coordinator state-change handler)
    # ------------------------------------------------------------------ #

    def check_target_reached(
        self, entity_id: str, reported_position: int | None
    ) -> bool:
        """Check whether cover has reached its target within tolerance.

        Called from the coordinator's cover-state-change handler whenever
        the cover entity reports a new position.  Uses tolerance instead of
        exact equality so covers that round to 5% increments don't get
        stuck with ``wait_for_target=True`` forever.

        Args:
            entity_id: Cover entity ID
            reported_position: Position reported by the cover entity

        Returns:
            True if target reached (wait_for_target cleared), False otherwise.

        """
        if entity_id not in self.target_call:
            return False

        if reported_position is None:
            return False

        target = self.target_call[entity_id]
        if abs(reported_position - target) <= self._position_tolerance:
            self.wait_for_target[entity_id] = False
            self._retry_counts.pop(entity_id, None)
            self._logger.debug(
                "Target reached for %s (reported=%s target=%s)",
                entity_id,
                reported_position,
                target,
            )
            return True

        return False

    # ------------------------------------------------------------------ #
    # Reconciliation timer
    # ------------------------------------------------------------------ #

    async def _reconcile(self, now: dt.datetime) -> None:
        """Periodic reconciliation: re-send target if cover missed it.

        Runs every ``check_interval_minutes``. Calls the optional ``on_tick``
        callback first (used by coordinator for time window transition checks).

        For each tracked entity:

        1. If ``wait_for_target`` has been True for >30 s → force-clear it
           (timeout fallback for covers that never report final position).
        2. If ``wait_for_target`` is still True → cover is moving, skip.
        3. If entity is in ``_manual_override_entities`` → skip resend so
           reconciliation does not fight the user's intentional move.
           Safety handlers (force override, weather) overwrite ``target_call``
           via ``apply_position(is_safety=True)`` so they are always protected.
        4. If ``_auto_control_enabled`` is False and the entity is not in
           ``_safety_targets`` → skip.  Safety targets (set via
           ``apply_position(is_safety=True)``) are still resent so covers reach
           a safe position regardless of the automatic control toggle.
        5. If ``_in_time_window`` is False and entity is not in ``_safety_targets``
           → skip.  Prevents stale daytime targets from being resent overnight.
        6. Compare actual position to ``target_call`` within tolerance.
        7. If match → reset retry count, done.
        8. If mismatch → resend the same target (up to ``max_retries``).

        Note: reconciliation does *not* go through gate checks — the target
        was already validated when ``apply_position`` was called.

        """
        # Coordinator hook: time window transition checks, etc.
        if self._on_tick is not None:
            await self._on_tick(now)

        # Hard kill switch — skip ALL reconciliation when integration is disabled.
        if not self._enabled:
            return

        for entity_id, target in list(self.target_call.items()):
            self._last_reconcile_time[entity_id] = now

            # 1. Timeout: clear stuck wait_for_target
            if self.wait_for_target.get(entity_id, False):
                sent_at = self._sent_at.get(entity_id)
                if sent_at is not None:
                    elapsed = (now - sent_at).total_seconds()
                    if elapsed > self._WAIT_FOR_TARGET_TIMEOUT_SECONDS:
                        self._logger.debug(
                            "wait_for_target timeout for %s (elapsed %.0fs > %ds) — clearing",
                            entity_id,
                            elapsed,
                            self._WAIT_FOR_TARGET_TIMEOUT_SECONDS,
                        )
                        self.wait_for_target[entity_id] = False
                    else:
                        # Cover still expected to be moving
                        continue
                else:
                    continue  # No sent_at recorded yet

            # 2. Skip entities under manual override — the user moved the cover
            # intentionally; resending the integration's stale target would fight
            # the user.  Safety handlers (force override, weather) bypass this by
            # calling apply_position(is_safety=True) which overwrites target_call
            # with the safety position, so they are always protected by reconciliation.
            if entity_id in self._manual_override_entities:
                self._logger.debug(
                    "Reconcile: %s in manual override — skipping resend", entity_id
                )
                continue

            # 3. Skip non-safety targets when automatic control is off.  Safety
            # targets (force override, weather) are still resent because they
            # were placed via apply_position(is_safety=True) and are tracked in
            # _safety_targets — covers must reach a safe position regardless of
            # the automatic control toggle.
            if not self._auto_control_enabled and entity_id not in self._safety_targets:
                self._logger.debug(
                    "Reconcile: %s skipped — automatic control off", entity_id
                )
                continue

            # 4. Skip non-safety targets outside the operational time window.
            # Prevents stale daytime targets from being resent overnight.
            # Safety targets (force override, weather, end_time_default) are
            # always resent regardless of the time window.
            if not self._in_time_window and entity_id not in self._safety_targets:
                self._logger.debug(
                    "Reconcile: %s skipped — outside time window", entity_id
                )
                continue

            # 5. Read actual position
            actual = self._get_current_position(entity_id)
            if actual is None:
                self._logger.debug(
                    "Reconcile: cannot read position for %s, skipping", entity_id
                )
                continue

            # 6. Check match
            if abs(actual - target) <= self._position_tolerance:
                self._retry_counts.pop(entity_id, None)
                self._logger.debug(
                    "Reconcile: %s at target (actual=%s target=%s)",
                    entity_id,
                    actual,
                    target,
                )
                continue

            # 7. Mismatch — retry up to max_retries
            retry_count = self._retry_counts.get(entity_id, 0)
            if retry_count >= self._max_retries:
                if entity_id not in self._gave_up:
                    # Log warning exactly once; subsequent ticks are silent
                    self._logger.warning(
                        "Reconcile: max retries (%d) exceeded for %s "
                        "(actual=%s target=%s) — giving up until next target change",
                        self._max_retries,
                        entity_id,
                        actual,
                        target,
                    )
                    self._gave_up.add(entity_id)
                else:
                    self._logger.debug(
                        "Reconcile: %s still off target (actual=%s target=%s), max retries reached",
                        entity_id,
                        actual,
                        target,
                    )
                continue

            self._retry_counts[entity_id] = retry_count + 1
            self._logger.debug(
                "Reconcile: %s missed target (actual=%s target=%s) — retry %d/%d",
                entity_id,
                actual,
                target,
                retry_count + 1,
                self._max_retries,
            )
            await self._execute_command(entity_id, target)

    # ------------------------------------------------------------------ #
    # Diagnostic helpers
    # ------------------------------------------------------------------ #

    def get_diagnostics(self, entity_id: str) -> dict[str, Any]:
        """Return per-entity positioning diagnostics for sensor display.

        Args:
            entity_id: Cover entity ID

        Returns:
            Dict with target, actual, at_target, retry_count,
            last_reconcile_time, wait_for_target.

        """
        actual = self._get_current_position(entity_id)
        target = self.target_call.get(entity_id)
        at_target = (
            target is not None
            and actual is not None
            and abs(actual - target) <= self._position_tolerance
        )
        last_recon = self._last_reconcile_time.get(entity_id)
        return {
            "target": target,
            "actual": actual,
            "at_target": at_target,
            "retry_count": self._retry_counts.get(entity_id, 0),
            "last_reconcile_time": last_recon.isoformat() if last_recon else None,
            "wait_for_target": self.wait_for_target.get(entity_id, False),
        }

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
        """Record a skipped cover action for diagnostic tracking.

        Kept as a public method so the coordinator can still record skips that
        happen before apply_position is reached (e.g. outside time window checks
        done at a higher level).

        Args:
            entity: Cover entity ID.
            reason: Machine-readable skip reason code.
            state: Calculated target position that was skipped.
            trigger: Source that triggered the positioning attempt
                (e.g. "solar", "startup", "sunset").  Empty string when unknown.
            current_position: Actual cover position at skip time, or None if unknown.
            inverse_state: Whether inverse-state mapping was in effect.
            extras: Optional dict of reason-specific context fields (e.g.
                position_delta, elapsed_minutes) merged into the record.

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

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _elapsed_minutes(self, entity_id: str) -> float | None:
        """Return minutes elapsed since last command to entity_id, or None."""
        last_updated = get_last_updated(entity_id, self._hass)
        if last_updated is None:
            return None
        elapsed = dt.datetime.now(dt.UTC) - last_updated
        return round(elapsed.total_seconds() / 60, 2)

    def _skip(
        self,
        entity_id: str,
        reason: str,
        position: int,
        *,
        trigger: str = "",
        inverse_state: bool = False,
        current_position: int | None = None,
        extras: dict | None = None,
    ) -> tuple[str, str]:
        """Record and return a skip result.

        Args:
            entity_id: Cover entity that was skipped.
            reason: Machine-readable skip reason code.
            position: Calculated target position that would have been sent.
            trigger: Source that triggered the positioning attempt.
            inverse_state: Whether inverse-state mapping was in effect.
            current_position: Actual cover position at skip time.
            extras: Reason-specific diagnostic fields merged into the record.

        """
        self._logger.debug(
            "Skipped %s → %s%% (%s) [trigger=%s]", entity_id, position, reason, trigger
        )
        self.record_skipped_action(
            entity_id,
            reason,
            position,
            trigger=trigger,
            current_position=current_position,
            inverse_state=inverse_state,
            extras=extras,
        )
        return "skipped", reason

    def _prepare_service_call(
        self,
        entity: str,
        state: int,
        inverse_state: bool = False,  # noqa: FBT001 — kept for signature clarity
        caps: dict[str, bool] | None = None,
        reset_retries: bool = True,
        is_safety: bool = False,
        use_my_position: bool = False,  # noqa: FBT001
    ) -> tuple[str | None, dict | None, bool]:
        """Build the HA service call for this cover/state.

        Updates ``wait_for_target``, ``target_call``, ``_sent_at``, and
        starts the command grace period.

        Args:
            entity: Cover entity ID
            state: Target position (0-100)
            inverse_state: Whether inverse state is applied (for tracking)
            caps: Pre-fetched capabilities dict; fetched internally if None
            reset_retries: If True (default), clears retry count and gave_up flag
                for this entity when a new target is recorded. Pass False from
                ``_execute_command`` so reconciliation retries do not reset the
                counter they themselves manage.
            is_safety: If True, this target was set via a safety override
                (force override, weather handler).  Adds the entity to
                ``_safety_targets`` so reconciliation will resend it even when
                automatic control is off or outside the time window.
                Non-safety targets remove the entity from ``_safety_targets``.
            use_my_position: If True and the cover lacks set_cover_position,
                send cover.stop_cover to trigger the hardware My preset instead
                of falling back to open/close threshold routing.

        Returns:
            (service_name, service_data, supports_position).
            (None, None, False) if cover is not capable.

        """
        if caps is None:
            caps = self.get_cover_capabilities(entity)

        # Determine whether to use tilt services for this entity.
        use_tilt = should_use_tilt(self.is_tilt_cover, caps)

        supports_position = (
            caps.get("has_set_tilt_position", True)
            if use_tilt
            else caps.get("has_set_position", True)
        )

        self._logger.debug(
            "Prepare service call: %s supports_position=%s caps=%s",
            entity,
            supports_position,
            caps,
        )

        now = dt.datetime.now(dt.UTC)

        if supports_position:
            service = (
                SERVICE_SET_COVER_TILT_POSITION
                if use_tilt
                else SERVICE_SET_COVER_POSITION
            )
            service_data = {ATTR_ENTITY_ID: entity}
            if use_tilt:
                service_data[ATTR_TILT_POSITION] = state
            else:
                service_data[ATTR_POSITION] = state

            self.target_call[entity] = state
            self.wait_for_target[entity] = True
            self._sent_at[entity] = now
            if reset_retries:
                self._retry_counts.pop(entity, None)  # New target resets retry count
                self._gave_up.discard(entity)  # Allow warnings again for new target
            # Track whether this target was set by a safety override so
            # reconciliation knows whether to resend it when auto_control is off.
            if is_safety:
                self._safety_targets.add(entity)
            else:
                self._safety_targets.discard(entity)
            self._grace_mgr.start_command_grace_period(entity)
            return service, service_data, True

        # "My" position path (non-position-capable covers only).
        # stop_cover sent to a stationary Somfy RTS cover triggers the user's
        # hardware-programmed My preset.  Position-capable covers skip this
        # branch and fall through to set_cover_position above.
        if use_my_position and caps.get("has_stop"):
            self._logger.debug(
                "My-position routing: stop_cover → %s (My = %d%%)", entity, state
            )
            self.target_call[entity] = state
            self.wait_for_target[entity] = True
            self._sent_at[entity] = now
            if reset_retries:
                self._retry_counts.pop(entity, None)
                self._gave_up.discard(entity)
            if is_safety:
                self._safety_targets.add(entity)
            else:
                self._safety_targets.discard(entity)
            self._grace_mgr.start_command_grace_period(entity)
            return "stop_cover", {ATTR_ENTITY_ID: entity}, False

        # Open/close-only cover
        has_open = caps.get("has_open", False)
        has_close = caps.get("has_close", False)
        if not has_open or not has_close:
            self._logger.warning(
                "Cover %s does not support both open and close. Skipping.", entity
            )
            return None, None, False

        if state >= self._open_close_threshold:
            service = "open_cover"
            self.target_call[entity] = 100
        else:
            service = "close_cover"
            self.target_call[entity] = 0

        service_data = {ATTR_ENTITY_ID: entity}
        self.wait_for_target[entity] = True
        self._sent_at[entity] = now
        if reset_retries:
            self._retry_counts.pop(entity, None)
            self._gave_up.discard(entity)
        # Track safety target status for open/close-only covers too.
        if is_safety:
            self._safety_targets.add(entity)
        else:
            self._safety_targets.discard(entity)
        self._grace_mgr.start_command_grace_period(entity)
        self._logger.debug(
            "Open/close control: state=%s threshold=%s service=%s",
            state,
            self._open_close_threshold,
            service,
        )
        return service, service_data, False

    async def _execute_command(self, entity_id: str, target: int) -> None:
        """Send command directly, bypassing gate checks (reconciliation use only).

        Does NOT reset the retry count — the caller (_reconcile) owns that.
        """
        service, service_data, _ = self._prepare_service_call(
            entity_id, target, reset_retries=False
        )
        if service is None:
            return
        if self._dry_run:
            self._logger.info(
                "[dry_run] reconciliation would send cover.%s %s → %s%%",
                service,
                entity_id,
                target,
            )
            return
        try:
            await self._hass.services.async_call(COVER_DOMAIN, service, service_data)
        except HomeAssistantError as err:
            self._logger.warning(
                "Reconciliation service call %s.%s failed for %s: %s",
                COVER_DOMAIN,
                service,
                entity_id,
                err,
            )

    def _track_action(
        self,
        entity: str,
        service: str,
        state: int,
        supports_position: bool,
        inverse_state: bool = False,
    ) -> None:
        """Update last_cover_action diagnostic dict."""
        self.last_cover_action = {
            "entity_id": entity,
            "service": service,
            "position": state if supports_position else self.target_call.get(entity),
            "calculated_position": state,
            "threshold_used": self._open_close_threshold
            if not supports_position
            else None,
            "inverse_state_applied": inverse_state,
            "timestamp": dt.datetime.now().isoformat(),
            "covers_controlled": 1,
        }


def build_special_positions(options: dict) -> list[int]:
    """Build list of special positions from options.

    Special positions (0, 100, default_height, sunset_pos) bypass the
    *delta-threshold* check so covers are always allowed to transition
    TO or FROM these key values even when the position change is smaller
    than ``min_change``.  They do NOT bypass the same-position short-circuit
    added in ``_check_position_delta`` — if the cover is already at the
    target, no command is sent regardless of whether the target is special.

    """
    special_positions = [0, 100]
    default_height = options.get(CONF_DEFAULT_HEIGHT)
    sunset_pos = options.get(CONF_SUNSET_POS)
    my_position_value = options.get(CONF_MY_POSITION_VALUE)
    if default_height is not None:
        special_positions.append(default_height)
    if sunset_pos is not None:
        special_positions.append(sunset_pos)
    if my_position_value is not None:
        special_positions.append(my_position_value)
    return special_positions
