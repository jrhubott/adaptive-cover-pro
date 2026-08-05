"""Cover entity state provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..cover_types.base import (
    CAP_HAS_CLOSE,
    CAP_HAS_OPEN,
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    caps_get,
)
from ..helpers import check_cover_features
from .snapshot import CoverCapabilities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..cover_types.base import CoverTypePolicy

_DEFAULT_CAPABILITIES = CoverCapabilities(
    has_set_position=True,
    has_set_tilt_position=False,
    has_open=True,
    has_close=True,
)


class CoverProvider:
    """Reads cover entity positions and capabilities from HA."""

    def __init__(self, hass: HomeAssistant, logger) -> None:
        """Initialize with HA instance and logger."""
        self._hass = hass
        self.logger = logger

    def read_positions(
        self,
        entities: list[str],
        policy: CoverTypePolicy,
        assumed: Callable[[str], int | None] | None = None,
    ) -> dict[str, int | None]:
        """Read current positions for all managed cover entities.

        Delegates the per-entity axis routing to ``policy.read_axis_value`` so
        the same "pick the axis, fall back to open/close" rule used by
        ``CoverCommandService`` lives in exactly one place.

        ``assumed`` (issue #888) is a per-entity lookup for the display-only
        assumed position. When supplied, its value is passed through to
        ``read_axis_value``, which surfaces it ONLY on the open/close-only
        branch when the live read is None. This is a reported-position surface,
        never the command-dispatch read path — so it never affects the gates.
        """
        positions: dict[str, int | None] = {}
        for entity in entities:
            caps = self.read_single_capabilities(entity)
            positions[entity] = policy.read_axis_value(
                self._hass,
                entity,
                caps,
                assumed=assumed(entity) if assumed is not None else None,
            )
        return positions

    def read_single_capabilities(self, entity: str) -> CoverCapabilities:
        """Read capabilities for a single cover entity."""
        caps = check_cover_features(self._hass, entity)
        if caps is None:
            return _DEFAULT_CAPABILITIES
        return CoverCapabilities(
            has_set_position=caps_get(caps, CAP_HAS_SET_POSITION, default=True),
            has_set_tilt_position=caps_get(
                caps, CAP_HAS_SET_TILT_POSITION, default=False
            ),
            has_open=caps_get(caps, CAP_HAS_OPEN, default=True),
            has_close=caps_get(caps, CAP_HAS_CLOSE, default=True),
        )

    def read_all_capabilities(
        self, entities: list[str]
    ) -> dict[str, CoverCapabilities]:
        """Read capabilities for all managed cover entities."""
        return {entity: self.read_single_capabilities(entity) for entity in entities}
