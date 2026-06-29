"""Tests for the end-of-window position feature (issue #625).

The coordinator seam ``_compute_current_effective_default`` reads
``CONF_END_OF_WINDOW_POS`` and derives ``window_is_closed`` from
``not self._time_mgr.before_end_time``, then forwards both into the single
``compute_effective_default`` decision function. This single seam drives BOTH
the one-shot end-time send (``_on_window_closed``) AND the live pipeline
snapshot (stickiness across evening refreshes).
"""

from __future__ import annotations

import datetime as _dt
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_END_OF_WINDOW_POS,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from tests.test_pipeline.conftest import make_snapshot


def _coord_with_window(
    *,
    before_end_time: bool,
    sunset_hour: int = 20,
    sunrise_hour: int = 6,
):
    """Minimal coordinator stub for _compute_current_effective_default tests.

    ``before_end_time`` drives ``window_is_closed = not before_end_time``: when
    the window is still open (now < end) the end-of-window override must NOT
    fire; once it is clock-closed (now >= end) it does.
    """
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord.hass.states.get.return_value = None  # no sunset/sunrise time entities

    time_mgr = MagicMock()
    time_mgr.before_end_time = before_end_time
    time_mgr.gate_is_daytime = True
    time_mgr.gate_is_dark = False
    time_mgr.gate_is_configured = False  # astral path, no gate
    time_mgr.effective_daytime_gate = None  # no gate → astral (issue #742)
    time_mgr.window_explicitly_started = False
    coord._time_mgr = time_mgr

    sun_data = MagicMock()
    today = _dt.date.today()
    sun_data.sunset.return_value = _dt.datetime(
        today.year, today.month, today.day, sunset_hour, 0, 0
    )
    sun_data.sunrise.return_value = _dt.datetime(
        today.year, today.month, today.day, sunrise_hour, 0, 0
    )
    cover_data = MagicMock()
    cover_data.sun_data = sun_data
    coord.get_blind_data = MagicMock(return_value=cover_data)
    return coord


def _freeze_helpers_now(naive_utc: _dt.datetime):
    from unittest.mock import patch

    aware = naive_utc.replace(tzinfo=_dt.UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware},
    )


class TestCoordinatorSeamReadsEndOfWindow:
    """_compute_current_effective_default threads the eow option + window state."""

    def test_window_closed_before_sunset_returns_eow(self):
        """Window clock-closed, before astral sunset → end-of-window position."""
        coord = _coord_with_window(before_end_time=False, sunset_hour=20)
        options = {
            CONF_DEFAULT_HEIGHT: 80,
            CONF_SUNSET_POS: 20,
            CONF_END_OF_WINDOW_POS: 0,
        }
        today = _dt.date.today()
        # 19:30 — after the window end but before astral sunset (20:00).
        now = _dt.datetime(today.year, today.month, today.day, 19, 30, 0)
        with _freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
        assert eff == 0
        assert is_sunset is True

    def test_window_open_does_not_apply_eow(self):
        """Window still open (before end) → eow override does not fire."""
        coord = _coord_with_window(before_end_time=True, sunset_hour=20)
        options = {
            CONF_DEFAULT_HEIGHT: 80,
            CONF_SUNSET_POS: 20,
            CONF_END_OF_WINDOW_POS: 0,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
        assert eff == 80
        assert is_sunset is False

    def test_window_closed_after_sunset_hands_off_to_sunset(self):
        """Window closed, after astral sunset → astral sunset_pos (phase 2)."""
        coord = _coord_with_window(before_end_time=False, sunset_hour=20)
        options = {
            CONF_DEFAULT_HEIGHT: 80,
            CONF_SUNSET_POS: 20,
            CONF_END_OF_WINDOW_POS: 0,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        with _freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
        assert eff == 20
        assert is_sunset is True

    def test_window_closed_no_sunset_pos_persists(self):
        """Window closed, no sunset_pos handoff target → eow persists."""
        coord = _coord_with_window(before_end_time=False, sunset_hour=20)
        options = {
            CONF_DEFAULT_HEIGHT: 80,
            CONF_SUNSET_POS: None,
            CONF_END_OF_WINDOW_POS: 0,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with _freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
        assert eff == 0
        assert is_sunset is True

    def test_eow_unset_is_no_regression(self):
        """No end-of-window option → today's astral behavior (open default)."""
        coord = _coord_with_window(before_end_time=False, sunset_hour=20)
        options = {
            CONF_DEFAULT_HEIGHT: 80,
            CONF_SUNSET_POS: 20,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 19, 30, 0)
        with _freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
        assert eff == 80
        assert is_sunset is False


class TestLivePipelineStickiness:
    """The eow value flows through the live snapshot → DefaultHandler.

    The coordinator passes (effective_default, is_sunset_active) into the
    snapshot builder, so a routine mid-evening refresh keeps the closed position
    — not just the one-shot window-close transition.
    """

    def test_default_handler_emits_eow_when_active(self):
        # Simulate what the coordinator seam produces mid-evening (eow=0 active).
        snap = make_snapshot(is_sunset_active=True, default_position=0)
        result = DefaultHandler().evaluate(snap)
        assert result.position == 0
        assert "sunset position" in result.reason
