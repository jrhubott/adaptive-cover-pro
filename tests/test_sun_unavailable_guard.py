"""Tests for the sun.sun unavailability guard in coordinator.get_blind_data.

When sun.sun drops out, both azimuth and elevation read as None. Falling back to
0.0/0.0 is a valid-looking on-the-horizon sun that can trigger spurious cover
commands. The guard instead drops elevation to -1.0 (below the horizon) so
valid_elevation is False and solar tracking is suppressed until the entity
recovers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)


class _FakeState:
    def __init__(self, attributes: dict) -> None:
        self.attributes = attributes


def _make_coordinator(sun_state) -> AdaptiveDataUpdateCoordinator:
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.states.get = MagicMock(
        side_effect=lambda eid: sun_state if eid == "sun.sun" else None
    )
    coord.hass = hass
    coord._sun_provider = MagicMock()
    coord._config_service = MagicMock()
    coord._policy = MagicMock()
    return coord


def _captured_elev_azi(coord) -> tuple[float, float]:
    coord.get_blind_data(options={})
    _, kwargs = coord._policy.build_calc_engine.call_args
    return kwargs["sol_elev"], kwargs["sol_azi"]


@pytest.mark.unit
def test_unavailable_sun_drops_elevation_below_horizon():
    coord = _make_coordinator(sun_state=None)  # states.get -> None: attrs are None
    sol_elev, sol_azi = _captured_elev_azi(coord)
    assert sol_elev == -1.0
    assert sol_azi == 0.0


@pytest.mark.unit
def test_unavailable_sun_logs_warning():
    coord = _make_coordinator(sun_state=None)
    coord.get_blind_data(options={})
    assert coord.logger.warning.called


@pytest.mark.unit
def test_available_sun_uses_reported_values():
    coord = _make_coordinator(
        sun_state=_FakeState({"azimuth": 180.0, "elevation": 45.0})
    )
    sol_elev, sol_azi = _captured_elev_azi(coord)
    assert sol_elev == 45.0
    assert sol_azi == 180.0
    coord.logger.warning.assert_not_called()


@pytest.mark.unit
def test_zero_elevation_at_horizon_not_treated_as_unavailable():
    """A real elevation of 0.0 must pass through, not be coerced to -1.0."""
    coord = _make_coordinator(sun_state=_FakeState({"azimuth": 90.0, "elevation": 0.0}))
    sol_elev, _ = _captured_elev_azi(coord)
    assert sol_elev == 0.0
    coord.logger.warning.assert_not_called()
