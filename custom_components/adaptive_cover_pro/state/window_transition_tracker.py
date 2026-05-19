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
closure that returns the current effective default + sunset flag) are
constructor-injected.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..managers.manual_override import inverse_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_types import ConfigContextAdapter
    from ..diagnostics.event_buffer import EventBuffer
    from ..managers.cover_command import PositionContext


# Type aliases for readability of the public surface.
EffectiveDefaultFn = Callable[[dict], tuple[int, bool]]
BuildContextFn = Callable[[str, dict], "PositionContext"]
ApplyPositionFn = Callable[..., Awaitable[Any]]
RefreshFn = Callable[[], Awaitable[Any]]
IsCoverManualFn = Callable[[str], bool]


class WindowTransitionTracker:
    """Track sun-visibility and astronomical-sunset-window transitions."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: ConfigContextAdapter,
        *,
        event_buffer: EventBuffer,
        effective_default_fn: EffectiveDefaultFn,
    ) -> None:
        """Bind collaborators and reset transition state to ``None``."""
        self._hass = hass
        self._logger = logger
        self._event_buffer = event_buffer
        self._effective_default_fn = effective_default_fn
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
        build_position_context: BuildContextFn,
        apply_position: ApplyPositionFn,
        refresh: RefreshFn,
    ) -> None:
        """Detect False→True transition of the astronomical sunset window.

        On opening transition, dispatches ``sunset_pos_cfg`` (inverted when
        the cover uses inverse state) to every non-manual cover.  No-ops
        when the user has not opted in to ``return_sunset`` tracking, when
        automatic control is disabled, when no sunset position is
        configured, or on the seeding call.
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

        _effective_pos, is_sunset = self._effective_default_fn(options)

        if self._prev_sunset_active is None:
            self._prev_sunset_active = is_sunset
            return

        just_opened = (not self._prev_sunset_active) and is_sunset
        self._prev_sunset_active = is_sunset

        if not just_opened:
            return

        pos_to_send = (
            inverse_state(int(sunset_pos_cfg))
            if inverse_state_enabled
            else int(sunset_pos_cfg)
        )
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
            await apply_position(
                cover_entity, pos_to_send, "sunset_window_opened", context=ctx
            )
        await refresh()
