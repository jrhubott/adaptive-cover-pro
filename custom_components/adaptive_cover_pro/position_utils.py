"""Position calculation utilities for Adaptive Cover Pro."""

from __future__ import annotations

import numpy as np


def interpolate_position(
    state: float,
    start_value: float | None,
    end_value: float | None,
    normal_list: list | None,
    new_list: list | None,
) -> float:
    """Interpolate state using custom ranges.

    Maps position from normal range to custom range using linear interpolation.
    Supports both simple start/end values or complex multi-point lists.

    Args:
        state: Position in normal range (0-100)
        start_value: Start of custom range (or None)
        end_value: End of custom range (or None)
        normal_list: Multi-point normal range values (or None)
        new_list: Multi-point custom range values (or None)

    Returns:
        Interpolated position in custom range, or original state if no
        interpolation configured

    """
    normal_range = [0, 100]
    new_range: list = []
    if start_value is not None and end_value is not None:
        new_range = [start_value, end_value]
    if normal_list and new_list:
        normal_range = list(map(int, normal_list))
        new_range = list(map(int, new_list))
    if new_range:
        state = float(np.interp(state, normal_range, new_range))
    return state


class PositionConverter:
    """Handles position-to-percentage conversions and limit application."""

    @staticmethod
    def to_percentage(position: float, max_value: float) -> int:
        """Convert position to percentage.

        Args:
            position: Position value (height, length, angle, etc.)
            max_value: Maximum possible value (window height, awning length, max degrees)

        Returns:
            Percentage value (0-100), rounded to nearest integer

        """
        percentage = (position / max_value) * 100
        return round(percentage)

    @staticmethod
    def apply_limits(
        value: int,
        min_pos: int | None,
        max_pos: int | None,
        apply_min: bool,
        apply_max: bool,
        sun_valid: bool,
        sun_tracking_min_pos: int | None = None,
    ) -> int:
        """Apply min/max position limits.

        Args:
            value: Position value to constrain (0-100)
            min_pos: Minimum position limit
            max_pos: Maximum position limit
            apply_min: Whether min limit applies (when False, always apply)
            apply_max: Whether max limit applies (when False, always apply)
            sun_valid: Whether sun is in valid position (direct sunlight)
            sun_tracking_min_pos: Optional separate minimum floor that applies
                only during sun tracking (sun_valid=True). When set, overrides
                min_pos for sun-tracking paths. None means fall back to min_pos.

        Returns:
            Constrained position value (0-100)

        Note:
            When apply_min/apply_max is False, limits are always enforced.
            When True, limits only apply during direct sun tracking (sun_valid=True).

        """
        # First clip to valid range
        result = np.clip(value, 0, 100)

        # Apply max position limit
        if max_pos is not None and max_pos != 100:
            # Always apply if enable flag is False, or if sun is valid
            if not apply_max or sun_valid:
                result = min(result, max_pos)

        # Sun-tracking floor: when sun_tracking_min_pos is set and sun is valid,
        # use it as the effective min floor instead of min_pos.
        # None means "fall back to min_pos" — preserves existing behavior exactly.
        # Guard: only use if it's an int (not None or any non-numeric sentinel).
        _use_sun_tracking = (
            isinstance(sun_tracking_min_pos, int) and sun_valid
        )
        effective_min = sun_tracking_min_pos if _use_sun_tracking else min_pos

        # Apply min position limit
        if effective_min is not None and effective_min != 0:
            # Always apply if enable flag is False, or if sun is valid
            if not apply_min or sun_valid:
                result = max(result, effective_min)

        return int(result)
