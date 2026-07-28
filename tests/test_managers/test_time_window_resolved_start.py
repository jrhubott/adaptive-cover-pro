"""Tests for TimeWindowManager.resolved_start_time (issue #975, B2 predicate).

The config-time-window health check compares the resolved start against the end
time. ``resolved_start_time`` exposes the same start-resolution ``_start_has_passed``
already performs — static/entity parse, blank-sentinel → None — without the
"has it passed now?" comparison, so the predicate can be evaluated regardless of
the wall clock. This is a behavior-preserving extraction: ``is_active`` /
``after_start_time`` must be unchanged.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from freezegun import freeze_time

from custom_components.adaptive_cover_pro.const import BLANK_TIME
from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager

pytestmark = pytest.mark.unit


class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state
        self.attributes: dict = {}


class _FakeStates:
    def __init__(self) -> None:
        self._d: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str) -> None:
        self._d[entity_id] = _FakeState(state)

    def get(self, entity_id: str):
        return self._d.get(entity_id)


class _FakeHass:
    def __init__(self) -> None:
        self.states = _FakeStates()


@pytest.fixture
def mgr():
    return TimeWindowManager(_FakeHass(), logging.getLogger("test_resolved_start"))


def test_static_start_resolves_to_datetime(mgr):
    """A static start time resolves to a datetime at that time-of-day."""
    mgr.update_config(
        start_time="08:00:00",
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )
    resolved = mgr.resolved_start_time
    assert isinstance(resolved, dt.datetime)
    assert resolved.time() == dt.time(8, 0)


def test_blank_start_resolves_to_none(mgr):
    """The blank sentinel means 'no explicit start' → None."""
    mgr.update_config(
        start_time=BLANK_TIME,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )
    assert mgr.resolved_start_time is None


def test_unset_start_resolves_to_none(mgr):
    """No entity and no static value → None."""
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )
    assert mgr.resolved_start_time is None


# Freeze to inside the 08:00-18:00 window: ``is_active`` is
# ``before_end_time and after_start_time and ...``, which short-circuits before
# ``after_start_time`` (the property that populates ``_cached_start_time``) when
# the wall clock is past the end time. Without a fixed clock this test passes
# only when it happens to run mid-window and fails on any evening CI run.
@freeze_time("2026-07-19 10:00:00")
def test_resolved_start_matches_value_is_active_uses(mgr):
    """``resolved_start_time`` equals the cached start ``is_active`` compares."""
    mgr.update_config(
        start_time="08:00:00",
        start_time_entity=None,
        end_time="18:00:00",
        end_time_entity=None,
    )
    # Drive is_active so _cached_start_time (the value is_active compares) is set.
    _ = mgr.is_active
    assert mgr.resolved_start_time == mgr.start_time_value
