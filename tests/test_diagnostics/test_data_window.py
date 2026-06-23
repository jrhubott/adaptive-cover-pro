"""Tests for the diagnostics data_window metadata (issue #656).

``data_window`` records the time span covered by the event timeline plus the
moment the snapshot was captured, so a downloaded diagnostics file is
self-describing: a triager can tell at a glance how recent and how wide the
captured window is.
"""

from __future__ import annotations

import datetime as dt

from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticsBuilder,
)
from tests.test_diagnostics.test_builder import _base_ctx

# ---------------------------------------------------------------------------
# _compute_data_window — staticmethod unit tests
# ---------------------------------------------------------------------------


def test_compute_data_window_spans_earliest_to_latest():
    """Start == earliest ts, end == latest ts across the timeline."""
    timeline = [
        {"ts": "2026-06-22T20:05:00+00:00", "event": "b"},
        {"ts": "2026-06-22T20:00:00+00:00", "event": "a"},
        {"ts": "2026-06-22T20:10:00+00:00", "event": "c"},
    ]
    window = DiagnosticsBuilder._compute_data_window(timeline)

    assert window["start"] == "2026-06-22T20:00:00+00:00"
    assert window["end"] == "2026-06-22T20:10:00+00:00"
    captured = dt.datetime.fromisoformat(window["captured_at"])
    assert captured.tzinfo == dt.UTC


def test_compute_data_window_empty_timeline_has_none_bounds():
    """Empty (or None) timeline → start None, end None, captured_at still valid."""
    for timeline in ([], None):
        window = DiagnosticsBuilder._compute_data_window(timeline)
        assert window["start"] is None
        assert window["end"] is None
        captured = dt.datetime.fromisoformat(window["captured_at"])
        assert captured.tzinfo == dt.UTC


def test_compute_data_window_captured_at_parses_as_utc():
    """captured_at parses with fromisoformat and carries UTC tzinfo."""
    window = DiagnosticsBuilder._compute_data_window(
        [{"ts": "2026-06-22T20:00:00+00:00"}]
    )
    captured = dt.datetime.fromisoformat(window["captured_at"])
    assert captured.tzinfo == dt.UTC


# ---------------------------------------------------------------------------
# data_window emitted alongside event_timeline in debug info
# ---------------------------------------------------------------------------


def test_data_window_sits_alongside_event_timeline():
    """_build_debug_info emits data_window matching the timeline bounds."""
    timeline = [
        {"ts": "2026-06-22T20:00:00+00:00", "event": "manual_override_armed"},
        {"ts": "2026-06-22T20:08:00+00:00", "event": "manual_override_cleared"},
    ]
    diag = DiagnosticsBuilder._build_debug_info(_base_ctx(event_timeline=timeline))

    assert diag["event_timeline"] == timeline
    assert diag["data_window"]["start"] == "2026-06-22T20:00:00+00:00"
    assert diag["data_window"]["end"] == "2026-06-22T20:08:00+00:00"


def test_data_window_emitted_even_when_timeline_empty():
    """data_window is emitted unconditionally, with None bounds when no events."""
    diag = DiagnosticsBuilder._build_debug_info(_base_ctx(event_timeline=None))

    assert diag["data_window"]["start"] is None
    assert diag["data_window"]["end"] is None
    captured = dt.datetime.fromisoformat(diag["data_window"]["captured_at"])
    assert captured.tzinfo == dt.UTC
