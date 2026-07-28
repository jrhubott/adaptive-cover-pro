"""Opt-in proxy cover platform for Adaptive Cover Pro.

When ``CONF_ENABLE_PROXY_COVER`` is True, one ``AdaptiveProxyCover`` entity is
created per source cover in ``CONF_ENTITIES``. The proxy mirrors source state
verbatim (no inverse-state transform) and routes user commands through
``Coordinator.async_apply_user_position`` so min-mode custom-position floors
are honoured. ``stop_cover`` forwards directly to the source.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import slugify

from .const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    DEFAULT_ENABLE_PROXY_COVER,
    TRIGGER_PROXY_CLOSE,
    TRIGGER_PROXY_OPEN,
    TRIGGER_PROXY_POSITION,
    TRIGGER_PROXY_TILT,
)
from .cover_types.base import (
    CAP_HAS_SET_TILT_POSITION,
    STATE_ATTR_POSITION,
    STATE_ATTR_TILT_POSITION,
    caps_get,
)
from .entity_base import _SENTINEL, AdaptiveCoverBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up proxy cover entities for an ACP config entry."""
    if not entry.options.get(CONF_ENABLE_PROXY_COVER, DEFAULT_ENABLE_PROXY_COVER):
        return

    sources: list[str] = list(entry.options.get(CONF_ENTITIES) or [])
    if not sources:
        return

    coordinator: AdaptiveDataUpdateCoordinator = entry.runtime_data
    multi = len(sources) > 1

    entities: list[AdaptiveProxyCover] = [
        AdaptiveProxyCover(
            entry_id=entry.entry_id,
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            source_entity_id=src,
            multi=multi,
        )
        for src in sources
    ]
    async_add_entities(entities)


def _source_friendly_label(hass: HomeAssistant, entity_id: str) -> str:
    """Return a human label for a source entity_id (registry > object_id)."""
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry and (entry.original_name or entry.name):
        return entry.name or entry.original_name
    state = hass.states.get(entity_id)
    if state is not None:
        friendly = state.attributes.get("friendly_name")
        if friendly:
            return friendly
    return entity_id.split(".", 1)[-1].replace("_", " ").title()


class AdaptiveProxyCover(AdaptiveCoverBaseEntity, CoverEntity):
    """Proxy cover that mirrors a source and routes commands through ACP."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        *,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        source_entity_id: str,
        multi: bool,
    ) -> None:
        """Initialise a proxy cover bound to ``source_entity_id``."""
        super().__init__(entry_id, hass, config_entry, coordinator)
        self._source_entity_id = source_entity_id
        self._attr_unique_id = f"{entry_id}_proxy_{slugify(source_entity_id)}"
        title = config_entry.title or config_entry.data.get("name") or "Adaptive"
        if multi:
            label = _source_friendly_label(hass, source_entity_id)
            self._attr_name = f"{title} Managed ({label})"
        else:
            self._attr_name = f"{title} Managed"
        # Render signature of the last source-mirror write. Kept separate from
        # the base-class coordinator-update gate because the proxy renders from
        # source state, not coordinator.data — the two write paths must not
        # share one cache field.
        self._proxy_source_sig: object = _SENTINEL

    # ---- availability + mirroring -------------------------------------- #

    def _source_state(self) -> Any:
        """Return the current HA state object for the source entity, or None."""
        return self.hass.states.get(self._source_entity_id)

    @property
    def available(self) -> bool:
        """Mirror source availability."""
        state = self._source_state()
        if state is None:
            return False
        return state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    @property
    def is_opening(self) -> bool:
        """True when the source cover reports it is opening."""
        state = self._source_state()
        return state is not None and state.state == CoverState.OPENING

    @property
    def is_closing(self) -> bool:
        """True when the source cover reports it is closing."""
        state = self._source_state()
        return state is not None and state.state == CoverState.CLOSING

    @property
    def current_cover_position(self) -> int | None:
        """Mirror source current_position verbatim (no inverse transform)."""
        state = self._source_state()
        if state is None:
            return None
        value = state.attributes.get(STATE_ATTR_POSITION)
        return int(value) if value is not None else None

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Mirror source current_tilt_position verbatim."""
        state = self._source_state()
        if state is None:
            return None
        value = state.attributes.get(STATE_ATTR_TILT_POSITION)
        return int(value) if value is not None else None

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Mirror source supported_features."""
        state = self._source_state()
        if state is None:
            return CoverEntityFeature(0)
        return CoverEntityFeature(int(state.attributes.get("supported_features", 0)))

    @property
    def is_closed(self) -> bool | None:
        """Derived from mirrored current_position (0 = closed)."""
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    async def async_added_to_hass(self) -> None:
        """Subscribe to source state changes once mounted."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                self._handle_source_event,
            )
        )

    @callback
    def _handle_source_event(self, event: Event) -> None:
        """Mirror the source cover, skipping writes that carry no new state.

        Rapid OPENING/CLOSING intermediate events often repeat the same
        observable state; writing each one floods HA with no-op updates. Gate on
        the rendered surface (state flags, position, tilt, supported features).
        Fails open so a comparison error can never stall the mirror.
        """
        try:
            sig = (
                self.available,
                self.is_opening,
                self.is_closing,
                self.current_cover_position,
                self.current_cover_tilt_position,
                int(self.supported_features),
            )
        except Exception:  # noqa: BLE001 - never let a signature error suppress a write
            self._proxy_source_sig = _SENTINEL
            self.async_write_ha_state()
            return
        if sig == self._proxy_source_sig:
            return
        self._proxy_source_sig = sig
        self.async_write_ha_state()

    # ---- command routing ---------------------------------------------- #

    def _source_available(self) -> bool:
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            _LOGGER.debug(
                "proxy %s: source %s unavailable — dropping command",
                self.entity_id,
                self._source_entity_id,
            )
            return False
        return True

    def _source_caps(self) -> dict[str, bool]:
        feats = int(self.supported_features)
        return {
            "has_set_position": bool(feats & CoverEntityFeature.SET_POSITION),
            "has_set_tilt_position": bool(feats & CoverEntityFeature.SET_TILT_POSITION),
            "has_open": bool(feats & CoverEntityFeature.OPEN),
            "has_close": bool(feats & CoverEntityFeature.CLOSE),
            "has_stop": bool(feats & CoverEntityFeature.STOP),
        }

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Route slider position via the floor-clamping helper."""
        if not self._source_available():
            return
        position = int(kwargs["position"])
        await self.coordinator.async_apply_user_position(
            self._source_entity_id, position, trigger=TRIGGER_PROXY_POSITION
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open command routed through the helper as position=100."""
        if not self._source_available():
            return
        await self.coordinator.async_apply_user_position(
            self._source_entity_id, 100, trigger=TRIGGER_PROXY_OPEN
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close command routed through the helper (clamp applies intentionally)."""
        if not self._source_available():
            return
        await self.coordinator.async_apply_user_position(
            self._source_entity_id, 0, trigger=TRIGGER_PROXY_CLOSE
        )

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Route the requested tilt onto the dedicated tilt axis (issue #684).

        Dual-axis covers (venetian) must move only the slats — routing a tilt
        through ``async_apply_user_position`` previously drove the carriage to
        the requested value and left the slats untouched. ``async_apply_user_tilt``
        dispatches through the cover-type policy so the carriage stays put.
        """
        if not self._source_available():
            return
        if not caps_get(self._source_caps(), CAP_HAS_SET_TILT_POSITION):
            return
        tilt = int(kwargs["tilt_position"])
        await self.coordinator.async_apply_user_tilt(
            self._source_entity_id, tilt, trigger=TRIGGER_PROXY_TILT
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop forwards directly to the source (no clamp)."""
        if not self._source_available():
            return
        await self.hass.services.async_call(
            "cover",
            "stop_cover",
            {ATTR_ENTITY_ID: self._source_entity_id},
            blocking=False,
        )
