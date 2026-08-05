"""Pure data shapes for cover_command's per-entity state.

These dataclasses are the contract between the orchestrator
(:class:`CoverCommandService`) and the cover-positioning lifecycle
(reconciliation, manual override, diagnostics).

Keeping them in a leaf module — no imports from the rest of the package —
breaks any latent circular-import risk and makes it cheap for managers,
diagnostics, and tests to depend on the shapes without dragging in the
whole service.

Today this file holds the dataclasses only. The companion
``EntityStateStore`` wrapper that owns the ``dict[str, PerEntityState]``
and the typed accessor methods is still on :class:`CoverCommandService`;
extracting it is the natural follow-up once the rest of the seams have
moved.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class TravelCalibration:
    """One entity's row in the ``travel_time_calibration`` option table.

    The typed view of what is persisted as plain JSON in ``config_entry.options``
    (shape documented at ``const.CONF_TRAVEL_TIME_CALIBRATION``). Everything that
    reads or writes a row — the calibrator, the options flow's manual-entry step,
    the diagnostic sensor, :func:`build_travel_plan` — goes through this class, so
    the key names have exactly one definition instead of a string literal per
    call site.

    ``source`` records whether the numbers were measured or typed in by hand. It
    is DISPLAY-ONLY: no behaviour may branch on it. A manual row is a first-class
    calibration — for an ``assumed_state`` cover it is the only one obtainable —
    and treating it as second-rate is how the feature stops working for the covers
    that need it most.

    ``open_seconds`` / ``close_seconds`` are ``None`` on a manual row, which
    carries a single headline figure and no per-leg detail.
    """

    full_travel_seconds: float
    open_seconds: float | None = None
    close_seconds: float | None = None
    start_delay_seconds: float = 0.0
    calibrated_at: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``config_entry.options`` (plain JSON, no dataclasses)."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TravelCalibration | None:
        """Build from a stored row, or ``None`` if it is unusable.

        Tolerant by design: this reads user-editable persisted config that an
        older build, a hand-edited ``.storage`` file, or a partially-written row
        could leave malformed. A row without a usable ``full_travel_seconds`` is
        dropped rather than raising — a missing ramp is a cosmetic loss, and
        taking down the command path over one is not a trade worth making.
        """
        try:
            full = float(raw["full_travel_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if full <= 0:
            return None

        def _leg(key: str) -> float | None:
            value = raw.get(key)
            if value is None:
                return None
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                return None
            return seconds if seconds > 0 else None

        try:
            delay = max(0.0, float(raw.get("start_delay_seconds") or 0.0))
        except (TypeError, ValueError):
            delay = 0.0

        return cls(
            full_travel_seconds=full,
            open_seconds=_leg("open_seconds"),
            close_seconds=_leg("close_seconds"),
            start_delay_seconds=delay,
            calibrated_at=raw.get("calibrated_at"),
            source=raw.get("source"),
        )

    def seconds_for(self, *, rising: bool) -> float:
        """Full-sweep seconds for a move in this direction.

        ``rising`` is in WIRE space — the raw ``current_position`` number going
        up — matching ``transit._TRANSIT_WIRE_SIGN``, because that is the frame
        the legs were measured in. A caller holding an open-percent direction on
        an inverse-state install must flip it before asking (the #993 bug class).

        Falls back to the averaged figure whenever the relevant leg is absent,
        which is every manual row.
        """
        leg = self.open_seconds if rising else self.close_seconds
        return leg if leg is not None else self.full_travel_seconds


@dataclasses.dataclass(frozen=True, slots=True)
class TravelPlan:
    """Where a cover should appear to be, at any instant of an in-flight move.

    Recorded when a position command is dispatched and cleared when the entity
    stops ``waiting``. Consumers render from it rather than being fed a ticking
    value: two state writes per move instead of a sub-second loop, and a card can
    interpolate at whatever framerate it draws at.

    ``start_delay_seconds`` is not padding. Several actuator families (Somfy IO
    via Tahoma is the documented one — see
    ``VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS``) sit still for seconds
    after accepting a command. Folding that dead time into the ramp starts every
    animation early and lands it late; holding at the origin for it does not.
    """

    from_position: int
    to_position: int
    started_at: dt.datetime
    start_delay_seconds: float
    duration_seconds: float

    def position_at(self, now: dt.datetime) -> int:
        """Modelled position at ``now``, clamped to the move's own endpoints.

        Never overshoots and never runs backwards: before the actuator is due to
        move this is the origin, after the move is due to finish it is the
        target. A cover that stalls mid-travel will therefore be reported as
        arrived — the estimate is a model, and this is the direction in which it
        is wrong.
        """
        elapsed = (now - self.started_at).total_seconds()
        if elapsed <= self.start_delay_seconds:
            return self.from_position
        if self.duration_seconds <= 0:
            return self.to_position
        progress = min(
            1.0, (elapsed - self.start_delay_seconds) / self.duration_seconds
        )
        return round(
            self.from_position + (self.to_position - self.from_position) * progress
        )

    def as_payload(self) -> dict[str, Any]:
        """JSON-safe projection for the sensor attribute / diagnostics.

        The companion card's wire contract. ``started_at`` is ISO text because
        the value is handed to HA's state machine, which will not carry a
        ``datetime`` through a recorder round-trip.
        """
        return {
            "from": self.from_position,
            "to": self.to_position,
            "started_at": self.started_at.isoformat(),
            "start_delay_seconds": self.start_delay_seconds,
            "duration_seconds": self.duration_seconds,
        }


def build_travel_plan(
    raw: Mapping[str, Any] | None,
    *,
    from_position: int | None,
    to_position: int | None,
    started_at: dt.datetime,
    span: int,
) -> TravelPlan | None:
    """Derive the ramp for one dispatched move, or ``None`` when it can't be.

    Returns ``None`` — meaning "no animation for this move", never an exception —
    for an uncalibrated cover, an unknown origin, a zero-length move, or a
    degenerate axis span. Every caller is on the command-dispatch path, where a
    missing cosmetic surface must not become a failed command.

    ``span`` is the axis's own ``value_max - value_min``, not a literal 100: the
    calibration measured a full sweep of THAT axis, so the fraction of it this
    move covers has to be taken against the same range.
    """
    calibration = TravelCalibration.from_dict(raw) if raw else None
    if calibration is None or from_position is None or to_position is None:
        return None
    delta = to_position - from_position
    if delta == 0 or span <= 0:
        return None
    seconds = calibration.seconds_for(rising=delta > 0)
    return TravelPlan(
        from_position=from_position,
        to_position=to_position,
        started_at=started_at,
        start_delay_seconds=calibration.start_delay_seconds,
        duration_seconds=seconds * abs(delta) / span,
    )


@dataclasses.dataclass(slots=True)
class PerEntityState:
    """Per-entity positioning state owned by CoverCommandService.

    Replaces a fan of parallel dicts/sets keyed by entity_id. The service
    holds a single dict[str, PerEntityState]; an entity has no state until
    apply_position / send_my_position records one.

    `target` and `sent_at` use ``None`` to mean "absent" — preserving the
    "key not in dict" semantics of the previous parallel-dict design.

    """

    target: int | None = None
    # Opaque provenance stamp for ``target``, minted by the cover-type policy
    # (``CoverTypePolicy.capture_dispatch_token``) at the moment the command was
    # booked and handed straight back to that policy when the target is later
    # re-sent (issue #1115). It describes HOW the dispatch that produced
    # ``target`` expressed it — a Model C day/night middle rail's inversion
    # frame, say — which a policy asked to re-gate a resend cannot re-derive:
    # its own per-cycle cache belongs to the last RESOLUTION, and one
    # resolve-then-skip cycle is enough to make the two disagree. Written and
    # cleared by exactly the statements that write and clear ``target`` so the
    # two can never drift apart. NEVER interpreted by this manager: it stores
    # and replays the value and nothing else (no cover-type knowledge here).
    # ``None`` means "no dispatch provenance recorded" — every policy that does
    # not need one, plus every target no dispatch produced: rehydrated after a
    # reload, observed on the cover from outside, or the user's configured My
    # percent, which ``stop_cover`` puts on the wire without a position for any
    # frame to describe.
    dispatch_token: Any = None
    sent_at: dt.datetime | None = None
    waiting: bool = False
    last_progress_at: dt.datetime | None = None
    retry_count: int = 0
    gave_up: bool = False
    is_safety: bool = False
    last_reconcile_at: dt.datetime | None = None
    # Display-only assumed position (issue #888). Set on covers with no native
    # position axis (Somfy-RTS-style open/close-only) when ACP drives them — an
    # ACP My move or an external stop that engaged the #875 override. A pure
    # fallback the reported-position surfaces return ONLY when the live HA read
    # is None; NEVER consulted by the command-dispatch gates (§3b).
    assumed_position: int | None = None
    # Synthetic travel direction for no-feedback covers. Set alongside
    # ``waiting`` on open/close-only covers (Somfy-RTS-style: no position, no
    # opening/closing state) so the companion card can render "Opening…" /
    # "Closing…" during ACP's transit-timeout window. Values: ``"opening"``,
    # ``"closing"``, or ``None``. Computed in the non-inverted display frame
    # (100=open, 0=closed) and cleared whenever ``waiting`` clears — the
    # ``transit_states()`` surface is gated on ``waiting`` so it disappears
    # exactly when the transit window closes.
    transit_direction: str | None = None
    # Position-vs-time model for the move currently in flight, or None. Written
    # at the same dispatch chokepoint as ``transit_direction`` and cleared by the
    # same ``_clear_waiting``, so the two cannot drift out of step: both describe
    # the transit window and both must vanish when it closes. Present only for
    # covers with a travel-time calibration (measured or manual).
    travel_plan: TravelPlan | None = None
    # Anti-relay latch for full-mechanical-endpoint forcing (issue #897). Holds
    # the endpoint (0 or 100) that ``apply_position`` last force-routed via
    # close_cover/open_cover. Read BEFORE computing force_endpoint and written
    # only after a successful send, so a cover that never reports the mechanical
    # state (state stays "open" at 2%) is forced exactly once instead of
    # relay-clicking every cycle (#507 preserved). Cleared when a non-endpoint
    # move fires; a flip to the other endpoint re-fires because the latched
    # value differs from the new target.
    forced_endpoint: int | None = None


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
    force: bool = False  # Skip delta/time/manual_override gates (NOT auto_control)
    is_safety: bool = (
        False  # Safety-critical target (persists across window boundaries; bypasses auto_control)
    )
    bypass_auto_control: bool = (
        False  # Sanctioned one-shot bypass of auto_control gate (e.g. switch return-to-default)
    )
    user_command: bool = (
        # An explicit user-initiated command (card Open/Close/Set, set_position /
        # set_axes service, My button). Unlike the generic ``force`` flag — which
        # recurring resends (custom_position, override-clear) also set and which
        # MUST stay deduped by the same-position gate to avoid relay clicks
        # (issue #290) — a user command must ALWAYS dispatch, even when ACP's raw
        # view already matches the target. On a no-feedback cover ACP cannot know
        # the true position, so it must trust the user (issue #900).
        False
    )
    use_my_position: bool = (
        False  # Route through send_my_position() on non-position-capable covers
    )
    # Secondary-axis target (e.g. tilt for venetian blinds). The owning
    # cover-type policy reads it inside ``after_position_command`` to decide
    # whether and how to chase the position command with a second service
    # call. ``None`` means "no secondary axis on this update cycle".
    tilt: int | None = None
    # The cover-type policy in effect. ``apply_position`` calls
    # ``policy.after_position_command`` once the position service has fired so
    # dual-axis covers can run their settle+tilt sequence without leaking the
    # logic into this shared service.
    policy: Any = None
    # Set by the owning cover-type policy (venetian) when BOTH axes target the
    # same full mechanical endpoint (0/0 or 100/100). Cover-type-agnostic: the
    # manager only honors the bool, never inspects cover type. Issue #755.
    full_endpoint_target: bool = False
    # Winning pipeline control method for this update cycle. Populated by the
    # coordinator from the PipelineResult; cover-type-agnostic (concrete type
    # ControlMethod | None, consumed by the owning policy's command hooks — e.g.
    # the venetian drift-reset scope gate, issue #808). Typed ``Any`` to keep
    # this leaf module free of package imports.
    control_method: Any = None
