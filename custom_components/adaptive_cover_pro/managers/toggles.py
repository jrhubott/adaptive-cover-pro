"""Toggle state management for Adaptive Cover Pro."""

from __future__ import annotations


class ToggleManager:
    """Manages toggle state set by switch entities.

    Each toggle corresponds to a switch entity that users can control.
    The coordinator delegates property access to this manager.
    """

    def __init__(self) -> None:
        """Initialize all toggles to their default states."""
        self.switch_mode: bool = False
        self.temp_toggle: bool | None = None
        self.automatic_control: bool | None = None
        self.manual_toggle: bool | None = None
        self.lux_toggle: bool | None = None
        self.irradiance_toggle: bool | None = None
        self.return_to_default_toggle: bool | None = None
