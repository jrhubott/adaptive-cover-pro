"""Stop / in-flight tracking for cover_command.

Owns the cover.stop_cover service path plus the deque of ACP-originated
stop context ids. The orchestrator's ``stop_in_flight`` / ``stop_all``
emergency-shutdown paths and ``send_my_position`` route through this
tracker so the EVENT_CALL_SERVICE listener in the coordinator can
distinguish ACP-issued stops from user-issued ones (the predicate
``was_acp_stop_context`` is the gate that prevents false manual-override
detection).

The class takes :class:`HomeAssistant` and a logger by injection so the
rest of the package can use it without depending on the orchestrator.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from homeassistant.core import Context, HomeAssistant

from ...cover_types.base import CAP_HAS_STOP, caps_get


class StopTracker:
    """Tracks ACP-originated cover.stop_cover calls and gates motion checks.

    Deque is capped at 16 entries (enough for several concurrent shutdown
    flushes; older context ids fall off as new ones append).
    """

    _CONTEXT_HISTORY_SIZE = 16

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        *,
        dry_run_fn: Callable[[], bool],
        is_in_transit_fn: Callable[[str], bool] | None = None,
    ) -> None:
        """Initialize the StopTracker.

        Args:
            hass: Home Assistant instance.
            logger: Logger to use for debug / info output.
            dry_run_fn: Zero-arg callable returning the current dry-run flag.
                Captured as a callable rather than a snapshot so the
                tracker always reflects the orchestrator's live setting.
            is_in_transit_fn: Optional callable ``(entity_id) -> bool`` that
                returns True when HA reports the cover as opening or closing.
                Defaults to an inline check when omitted (standalone use).
                ``CoverCommandService`` supplies ``self._is_cover_in_transit``
                so the transit predicate lives in exactly one place.

        """
        self._hass = hass
        self._logger = logger
        self._dry_run_fn = dry_run_fn
        self._is_in_transit_fn: Callable[[str], bool] = (
            is_in_transit_fn
            if is_in_transit_fn is not None
            else self._default_in_transit
        )
        self._acp_stop_contexts: deque[str] = deque(maxlen=self._CONTEXT_HISTORY_SIZE)

    def _default_in_transit(self, entity_id: str) -> bool:
        """Inline fallback when no external predicate is injected."""
        state_obj = self._hass.states.get(entity_id)
        return state_obj is not None and state_obj.state in ("opening", "closing")

    # ------------------------------------------------------------------ #
    # Context tracking
    # ------------------------------------------------------------------ #

    def was_acp_stop_context(self, context_id: str) -> bool:
        """Whether ``context_id`` belongs to an ACP-originated stop_cover call."""
        return context_id in self._acp_stop_contexts

    def acp_stop_context_count(self, *, unique: bool = False) -> int:
        """Return the number of recorded ACP-originated stop_cover context ids.

        With ``unique=True`` returns the count of distinct ids.
        """
        if unique:
            return len(set(self._acp_stop_contexts))
        return len(self._acp_stop_contexts)

    # ------------------------------------------------------------------ #
    # HA-side stop helpers
    # ------------------------------------------------------------------ #

    def is_cover_in_motion(self, entity_id: str) -> bool:
        """Return True only if HA reports the cover as opening or closing.

        cover.stop_cover is overloaded on some hardware (Somfy "My", Hunter
        Douglas favorite, etc.) — on stationary covers it triggers a preset
        position move instead of a no-op.  Callers in shutdown/emergency
        paths must gate on actual motion to avoid triggering that preset.

        Note: ``send_my_position`` intentionally does NOT call this method —
        it deliberately sends stop_cover to a stationary cover (that is what
        triggers the My preset). This gate applies to shutdown paths only.

        Delegates to the injected ``is_in_transit_fn`` (supplied by
        ``CoverCommandService`` as ``self._is_cover_in_transit``) so the
        transit predicate lives in one place.
        """
        return self._is_in_transit_fn(entity_id)

    async def call_stop_cover(self, entity_id: str) -> None:
        """Issue cover.stop_cover and record the call context as ACP-originated.

        All ACP-initiated stop_cover calls must go through this helper so that
        the EVENT_CALL_SERVICE listener can identify and ignore them, avoiding
        false manual-override detection.
        """
        ctx = Context()
        self._acp_stop_contexts.append(ctx.id)
        await self._hass.services.async_call(
            "cover", "stop_cover", {"entity_id": entity_id}, context=ctx
        )

    async def try_stop_one(
        self, entity_id: str, caps: dict[str, bool], *, label: str
    ) -> bool:
        """Attempt a stop_cover on a single entity, honouring caps + dry-run.

        Returns ``True`` when a stop was actually sent (or would have been
        sent under dry-run), ``False`` when caps or motion state caused the
        helper to skip. Centralises the "check has_stop → check in motion →
        dry-run vs send" chain that ``stop_in_flight`` and ``stop_all``
        previously each open-coded.

        Caps are passed in rather than fetched here so the orchestrator
        keeps ownership of capability lookups (and tests can patch
        ``check_cover_features`` at the package's __init__ module path).
        """
        if not caps_get(caps, CAP_HAS_STOP):
            return False
        if not self.is_cover_in_motion(entity_id):
            state_val = getattr(self._hass.states.get(entity_id), "state", None)
            self._logger.debug(
                "%s: skipping %s — not in motion (state=%s)",
                label,
                entity_id,
                state_val,
            )
            return False
        if self._dry_run_fn():
            self._logger.info("[dry_run] would stop_cover %s", entity_id)
        else:
            await self.call_stop_cover(entity_id)
        self._logger.debug("%s: stopped %s", label, entity_id)
        return True
