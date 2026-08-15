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
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from tests._helpers.time_freeze import freeze_helpers_now
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
        with freeze_helpers_now(now):
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
        with freeze_helpers_now(now):
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
        with freeze_helpers_now(now):
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
        with freeze_helpers_now(now):
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
        with freeze_helpers_now(now):
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


class TestSunsetBoundaryPredicateIsNotIsSunsetActive:
    """Issue #1287: pin ``read_sunset_window_open`` apart from ``is_sunset_active``.

    ``compute_effective_default``'s returned ``is_sunset_active`` means "a
    night-type default is in effect" — True for BOTH the end-of-window
    position (#625) AND the sunset position. The sunset-window edge detector
    (``WindowTransitionTracker.check_sunset_window``) needs a narrower
    predicate: "has the configured SUNSET boundary actually passed?" These
    tests prove the two answers diverge during an end-of-window hold —
    exactly the #1287 defect — and that ``compute_effective_default`` keeps
    returning its existing (unchanged) values throughout.

    Config mirrors the reporter's: end-of-window position 60, sunset
    position 16, astral sunset 21:04 local + a 60-minute offset → the real
    sunset BOUNDARY is 22:04.
    """

    def _hass_time_mgr_sun_data(self):
        hass = MagicMock()
        hass.states.get.return_value = None  # no sunset/sunrise time entities

        time_mgr = MagicMock()
        time_mgr.effective_daytime_gate = None  # no gate — astral path (#742)
        time_mgr.window_explicitly_started = False
        time_mgr.before_end_time = False  # window already clock-closed

        today = _dt.date.today()
        sun_data = MagicMock()
        sun_data.sunset.return_value = _dt.datetime(
            today.year, today.month, today.day, 21, 4, 0
        )
        sun_data.sunrise.return_value = _dt.datetime(
            today.year, today.month, today.day, 6, 0, 0
        )
        return hass, time_mgr, sun_data

    def test_window_open_false_during_end_of_window_hold(self):
        """19:30 — eow position holds (is_sunset_active True), boundary NOT passed."""
        from custom_components.adaptive_cover_pro.helpers import (
            read_sunset_window_open,
        )

        hass, time_mgr, sun_data = self._hass_time_mgr_sun_data()
        options = {
            CONF_DEFAULT_HEIGHT: 100,
            CONF_SUNSET_POS: 16,
            CONF_END_OF_WINDOW_POS: 60,
            CONF_SUNSET_OFFSET: 60,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 19, 30, 0)

        coord = _coord_with_window(before_end_time=False)
        coord.hass = hass
        coord._time_mgr = time_mgr
        coord.get_blind_data = MagicMock(return_value=MagicMock(sun_data=sun_data))
        with freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
            window_open = read_sunset_window_open(hass, options, sun_data, time_mgr)

        # compute_effective_default is unchanged: the eow position holds and
        # is still reported with the existing "night-type default" flag.
        assert (eff, is_sunset) == (60, True)
        # The narrower predicate says the SUNSET boundary itself has not
        # passed — this is the fix: the tracker must key off THIS, not
        # is_sunset_active.
        assert window_open is False

    def test_window_open_true_after_configured_sunset_boundary(self):
        """22:05 — past the configured boundary (astral sunset + offset)."""
        from custom_components.adaptive_cover_pro.helpers import (
            read_sunset_window_open,
        )

        hass, time_mgr, sun_data = self._hass_time_mgr_sun_data()
        options = {
            CONF_DEFAULT_HEIGHT: 100,
            CONF_SUNSET_POS: 16,
            CONF_END_OF_WINDOW_POS: 60,
            CONF_SUNSET_OFFSET: 60,
        }
        today = _dt.date.today()
        now = _dt.datetime(today.year, today.month, today.day, 22, 5, 0)

        coord = _coord_with_window(before_end_time=False)
        coord.hass = hass
        coord._time_mgr = time_mgr
        coord.get_blind_data = MagicMock(return_value=MagicMock(sun_data=sun_data))
        with freeze_helpers_now(now):
            eff, is_sunset = coord._compute_current_effective_default(options)
            window_open = read_sunset_window_open(hass, options, sun_data, time_mgr)

        # compute_effective_default has handed off to phase 2 (astral
        # sunset_pos) — still the SAME "night-type default" flag as before.
        assert (eff, is_sunset) == (16, True)
        # The boundary predicate now agrees — this is the edge the tracker
        # must fire on.
        assert window_open is True

    def test_window_open_false_when_sunset_position_not_configured(self):
        """No sunset position configured → the boundary predicate is inert."""
        from custom_components.adaptive_cover_pro.helpers import (
            read_sunset_window_open,
        )

        hass, time_mgr, sun_data = self._hass_time_mgr_sun_data()
        options = {
            CONF_DEFAULT_HEIGHT: 100,
            CONF_SUNSET_POS: None,
            CONF_END_OF_WINDOW_POS: 60,
            CONF_SUNSET_OFFSET: 60,
        }

        window_open = read_sunset_window_open(hass, options, sun_data, time_mgr)

        assert window_open is False
