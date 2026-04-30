"""Cover entity state provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..helpers import (
    check_cover_features,
    get_open_close_state,
    should_use_tilt,
    state_attr,
)
from .snapshot import CoverCapabilities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
        self, entities: list[str], cover_type: str
    ) -> dict[str, int | None]:
        """Read current positions for all managed cover entities."""
        positions = {}
        for entity in entities:
            caps = self.read_single_capabilities(entity)
            use_tilt = should_use_tilt(cover_type == "cover_tilt", caps)
            if use_tilt:
                if caps.has_set_tilt_position:
                    positions[entity] = state_attr(
                        self._hass, entity, "current_tilt_position"
                    )
                else:
                    positions[entity] = get_open_close_state(self._hass, entity)
            else:
                if caps.has_set_position:
                    positions[entity] = state_attr(
                        self._hass, entity, "current_position"
                    )
                else:
                    positions[entity] = get_open_close_state(self._hass, entity)
        return positions

    def read_single_capabilities(self, entity: str) -> CoverCapabilities:
        """Read capabilities for a single cover entity."""
        caps = check_cover_features(self._hass, entity)
        if caps is None:
            return _DEFAULT_CAPABILITIES
        return CoverCapabilities(
            has_set_position=caps.get("has_set_position", True),
            has_set_tilt_position=caps.get("has_set_tilt_position", False),
            has_open=caps.get("has_open", True),
            has_close=caps.get("has_close", True),
        )

    def read_all_capabilities(
        self, entities: list[str]
    ) -> dict[str, CoverCapabilities]:
        """Read capabilities for all managed cover entities."""
        return {entity: self.read_single_capabilities(entity) for entity in entities}
