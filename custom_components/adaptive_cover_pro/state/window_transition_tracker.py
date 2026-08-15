"""Window-transition tracking for sun visibility and astronomical sunset.

`WindowTransitionTracker` owns two correlated bits of stateful transition
detection that previously lived inline on the coordinator:

- **Sun visibility transition.** `sun_just_appeared()` returns True when the
  cover's direct-sun-valid flag flips from False to True between calls,
  signalling that covers should immediately reposition regardless of the
  per-update delta gates.  False→True records ``sun_entered_fov`` and
  True→False records ``sun_left_fov`` in the diagnostic event buffer.

- **Astronomical sunset window.** `check_sunset_window()` detects the
  False→True transition of the astronomical sunset window (sun crosses the
  configured offset after sunset) and dispatches the configured sunset
  position to all non-manually-controlled covers.  Covers issue #266 —
  where the user's configured end_time fires before the astronomical
  sunset offset elapses, so the daytime default is sent at end_time and
  the sunset position is sent later when the window finally opens.

Both pieces of state initialise to ``None`` so a coordinator restart
mid-sunset does not spuriously dispatch — the first call seeds the prior
state and returns without acting, matching the behaviour before extraction.

The tracker is framework-light: it never imports the coordinator.
Per-cycle collaborators (entities list, manager, command service,
position-context builder, refresh callback) flow in by parameter at each
call.  Long-lived collaborators (hass, logger, event buffer, and a
closure that returns whether the configured SUNSET boundary owns this
moment — blind to the end-of-window override, issue #1287) are
constructor-injected.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..position_utils import flip_if

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_types import ConfigContextAdapter
    from ..diagnostics.event_buffer import EventBuffer
    from ..managers.cover_command import PositionContext


# Type aliases for readability of the public surface.
# Returns whether the configured SUNSET boundary owns this moment — blind to
# the end-of-window override (issue #1287). See helpers.read_sunset_window_open
# for why this must NOT be compute_effective_default's overloaded
# is_sunset_active ("a night-type default is in effect", true for both the
# end-of-window position and the sunset position).
SunsetWindowOpenFn = Callable[[dict], bool]
BuildContextFn = Callable[[str, dict], "PositionContext"]
ApplyPositionFn = Callable[..., Awaitable[Any]]
RefreshFn = Callable[[], Awaitable[Any]]
IsCoverManualFn = Callable[[str], bool]
EntityTargetFn = Callable[[str, int], int]
# (LOGICAL sunset position, entities to fan out over) →
# (LOGICAL position to send, those same entities in dispatch order).
ResolveDispatchFn = Callable[[int, list[str]], tuple[int, list[str]]]


class WindowTransitionTracker:
    """Track sun-visibility and astronomical-sunset-window transitions."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: ConfigContextAdapter,
        *,
        event_buffer: EventBuffer,
        sunset_window_open_fn: SunsetWindowOpenFn,
    ) -> None:
        """Bind collaborators and reset transition state to ``None``."""
        self._hass = hass
        self._logger = logger
        self._event_buffer = event_buffer
        self._sunset_window_open_fn = sunset_window_open_fn
        # ``None`` on first call prevents spurious dispatch when the
        # integration starts mid-transition (issue #266 / sun FoV).
        self._last_sun_validity_state: bool | None = None
        self._prev_sunset_active: bool | None = None

    # ---- Sun visibility --------------------------------------------------

    def sun_just_appeared(self, cover_data) -> bool:
        """Return True when ``direct_sun_valid`` just flipped False→True.

        Records ``sun_entered_fov`` / ``sun_left_fov`` events on every
        observed transition.  Returns False until the first call has seeded
        the prior state.
        """
        if cover_data is None:
            return False

        current_sun_valid = cover_data.direct_sun_valid

        if self._last_sun_validity_state is None:
            self._last_sun_validity_state = current_sun_valid
            return False

        sun_just_appeared = (not self._last_sun_validity_state) and current_sun_valid
        sun_just_left = self._last_sun_validity_state and (not current_sun_valid)

        self._last_sun_validity_state = current_sun_valid

        if sun_just_appeared:
            self._logger.info(
                "Sun visibility transition detected: OFF → ON (sun came into field of view)"
            )
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "sun_entered_fov",
                }
            )
        elif sun_just_left:
            self._logger.debug(
                "Sun visibility transition detected: ON → OFF (sun left field of view)"
            )
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "sun_left_fov",
                }
            )

        return sun_just_appeared

    # ---- Astronomical sunset window -------------------------------------

    async def check_sunset_window(
        self,
        *,
        track_end_time: bool,
        automatic_control: bool,
        sunset_pos_cfg: int | None,
        options: dict,
        inverse_state_enabled: bool,
        entities: list[str],
        is_cover_manual: IsCoverManualFn,
        has_active_override: bool = False,
        build_position_context: BuildContextFn,
        apply_position: ApplyPositionFn,
        refresh: RefreshFn,
        entity_target: EntityTargetFn | None = None,
        resolve_dispatch: ResolveDispatchFn | None = None,
    ) -> None:
        """Detect False→True transition of the astronomical sunset window.

        On opening transition, dispatches ``sunset_pos_cfg`` (inverted when
        the cover uses inverse state) to every non-manual cover.  No-ops
        when the user has not opted in to ``return_sunset`` tracking, when
        automatic control is disabled, when no sunset position is
        configured, on the seeding call, or when ``has_active_override`` is
        True — a higher-priority pipeline handler (e.g. a CUSTOM_POSITION
        slot) is currently winning and must not be overwritten by the raw
        sunset position (issue #895). In that last case the internal
        False→True edge is deliberately left unresolved so the dispatch fires
        on the first subsequent call where the override is no longer active,
        rather than being lost.

        ``resolve_dispatch`` maps the configured LOGICAL sunset position and
        ``entities`` onto the pair this broadcast actually needs — the position
        to send and those same entities in policy-mandated dispatch order — so
        both come from one derivation and cannot disagree about the direction of
        travel (#1118). The entity list is passed IN rather than left for the
        resolver to source, so narrowing ``entities`` at the call site narrows
        what is ordered too. It is a callable rather than two pre-computed
        arguments because resolving it costs an off-cycle snapshot build on the
        coordinator side, while this method runs on every reconciliation tick
        and the edge it guards fires at most once a night. ``None`` keeps the
        raw configured position and the ``entities`` list exactly as supplied.
        """
        if not track_end_time:
            return
        if not automatic_control:
            self._logger.debug(
                "Sunset window opened but automatic control is OFF — skipping reposition"
            )
            return
        if sunset_pos_cfg is None:
            return
        if has_active_override:
            self._logger.debug(
                "Sunset window transition detected but a higher-priority "
                "handler is currently active — skipping reposition (issue #895)"
            )
            return

        is_sunset = self._sunset_window_open_fn(options)

        if self._prev_sunset_active is None:
            self._prev_sunset_active = is_sunset
            return

        just_opened = (not self._prev_sunset_active) and is_sunset
        self._prev_sunset_active = is_sunset

        if not just_opened:
            return

        pos_logical = int(sunset_pos_cfg)
        if resolve_dispatch is not None:
            # Handed THIS call's entity list rather than left to reach for the
            # coordinator's: the two are the same list today, and a resolver
            # that quietly substituted the whole instance for a subset a caller
            # had narrowed would be a silent bug the day they diverge.
            pos_logical, entities = resolve_dispatch(pos_logical, entities)
        pos_to_send = flip_if(pos_logical, inverted=inverse_state_enabled)
        self._logger.info(
            "Sunset window opened after end_time — dispatching sunset position %s%% "
            "to %s cover(s) (issue #266)",
            pos_to_send,
            len(entities),
        )
        self._event_buffer.record(
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "sunset_window_opened",
                "position": pos_to_send,
                "cover_count": len(entities),
            }
        )
        for cover_entity in entities:
            if is_cover_manual(cover_entity):
                continue
            ctx = build_position_context(cover_entity, options)
            # Per-entity remap keeps this broadcast seam consistent with the
            # other dispatch paths: a Model C day/night middle rail is remapped
            # here too, polymorphically via ``entity_target`` — every other
            # cover type's hook is identity (#993).
            target = (
                entity_target(cover_entity, pos_to_send)
                if entity_target is not None
                else pos_to_send
            )
            await apply_position(
                cover_entity, target, "sunset_window_opened", context=ctx
            )
        await refresh()
