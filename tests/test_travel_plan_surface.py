"""Travel plans — turning a measured travel time into a position-vs-time ramp.

A cover that reports position sparsely (or not at all) leaves a dashboard card
with nothing to animate. Once ``CONF_TRAVEL_TIME_CALIBRATION`` knows how long a
full sweep takes, every dispatched command can carry a ``TravelPlan``: where the
cover started, where it is going, when it was told, how long it dawdles before
moving, and how long the move should take. The card interpolates locally; ACP
writes two states per move instead of ticking.

Sibling surface to ``transit_direction`` (tests/test_transit_direction.py) — the
two share the ``_prepare_service_call`` write and the ``_clear_waiting`` clear,
so their lifecycles must stay locked together.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    TRAVEL_CALIBRATION_SOURCE_MANUAL,
    TRAVEL_CALIBRATION_SOURCE_MEASURED,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from custom_components.adaptive_cover_pro.managers.cover_command.state_store import (
    TravelPlan,
    build_travel_plan,
)

pytestmark = pytest.mark.integration

_T0 = dt.datetime(2026, 8, 2, 12, 0, 0, tzinfo=dt.UTC)

_OPEN_CLOSE_CAPS = {
    "has_set_position": False,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}
_POSITION_CAPS = {
    "has_set_position": True,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}

# 40 s to raise, 20 s to drop — the gravity-assisted asymmetry that averaging
# would throw away. 2 s of actuator dead time before either leg begins.
_ASYMMETRIC = {
    "full_travel_seconds": 30.0,
    "open_seconds": 40.0,
    "close_seconds": 20.0,
    "start_delay_seconds": 2.0,
    "calibrated_at": _T0.isoformat(),
    "source": TRAVEL_CALIBRATION_SOURCE_MEASURED,
}
# What manual entry produces: one headline number, no per-leg detail.
_MANUAL_ONLY = {
    "full_travel_seconds": 30.0,
    "open_seconds": None,
    "close_seconds": None,
    "start_delay_seconds": 0.0,
    "calibrated_at": _T0.isoformat(),
    "source": TRAVEL_CALIBRATION_SOURCE_MANUAL,
}


def _svc(*, calibration=None, caps=None):
    """Build a CoverCommandService with a stubbed calibration table."""
    h = MagicMock()
    h.services.async_call = AsyncMock()
    table = calibration or {}
    svc = CoverCommandService(
        hass=h,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        position_tolerance=3,
        get_travel_calibration=lambda eid: table.get(eid),
    )
    if caps is not None:
        svc.get_cover_capabilities = lambda _eid: caps
    return svc


# ------------------------------------------------------------------ #
# build_travel_plan — the pure ramp derivation
# ------------------------------------------------------------------ #


def test_directional_duration_uses_the_open_leg_when_rising():
    """0→50 on a 40 s open leg is half of 40, not half of the 30 s average."""
    plan = build_travel_plan(
        _ASYMMETRIC, from_position=0, to_position=50, started_at=_T0, span=100
    )
    assert plan.duration_seconds == pytest.approx(20.0)


def test_directional_duration_uses_the_close_leg_when_falling():
    """100→50 on a 20 s close leg is half of 20 — the asymmetry survives."""
    plan = build_travel_plan(
        _ASYMMETRIC, from_position=100, to_position=50, started_at=_T0, span=100
    )
    assert plan.duration_seconds == pytest.approx(10.0)


def test_manual_entry_falls_back_to_full_travel_for_both_directions():
    rising = build_travel_plan(
        _MANUAL_ONLY, from_position=0, to_position=50, started_at=_T0, span=100
    )
    falling = build_travel_plan(
        _MANUAL_ONLY, from_position=100, to_position=50, started_at=_T0, span=100
    )
    assert rising.duration_seconds == pytest.approx(15.0)
    assert falling.duration_seconds == pytest.approx(15.0)


def test_direction_is_judged_in_wire_space_not_open_percent():
    """The leg is picked by the raw number's direction, matching _TRANSIT_WIRE_SIGN.

    An inverse-state install flips what "open" means but not which way the wire
    number travels, and the calibration legs were measured against the wire.
    Judging in open-percent would pick the wrong leg on exactly those installs.
    """
    # Wire 20 → 80 is the number RISING, so it must be the open leg (40 s),
    # regardless of what the install calls "open".
    plan = build_travel_plan(
        _ASYMMETRIC, from_position=20, to_position=80, started_at=_T0, span=100
    )
    assert plan.duration_seconds == pytest.approx(40.0 * 0.6)


def test_span_scales_the_fraction():
    """Duration is a fraction of the AXIS span, never of a hardcoded 100."""
    plan = build_travel_plan(
        _MANUAL_ONLY, from_position=0, to_position=25, started_at=_T0, span=50
    )
    assert plan.duration_seconds == pytest.approx(15.0)


def test_no_plan_without_a_calibration():
    assert (
        build_travel_plan(
            None, from_position=0, to_position=50, started_at=_T0, span=100
        )
        is None
    )


def test_no_plan_for_a_zero_length_move():
    assert (
        build_travel_plan(
            _ASYMMETRIC, from_position=50, to_position=50, started_at=_T0, span=100
        )
        is None
    )


# ------------------------------------------------------------------ #
# TravelPlan.position_at — the ramp itself
# ------------------------------------------------------------------ #


def _ramp(**kw):
    defaults = {
        "from_position": 0,
        "to_position": 100,
        "started_at": _T0,
        "start_delay_seconds": 2.0,
        "duration_seconds": 40.0,
    }
    return TravelPlan(**{**defaults, **kw})


def test_position_holds_at_origin_during_the_start_delay():
    """The actuator has not moved yet, so neither may the rendered position."""
    plan = _ramp()
    assert plan.position_at(_T0) == 0
    assert plan.position_at(_T0 + dt.timedelta(seconds=1.9)) == 0


def test_position_ramps_linearly_after_the_delay():
    plan = _ramp()
    # 2 s delay + half of 40 s = the midpoint.
    assert plan.position_at(_T0 + dt.timedelta(seconds=22)) == 50


def test_position_clamps_at_the_target_and_never_overshoots():
    plan = _ramp()
    assert plan.position_at(_T0 + dt.timedelta(seconds=42)) == 100
    assert plan.position_at(_T0 + dt.timedelta(seconds=600)) == 100


def test_position_ramps_downward_too():
    plan = _ramp(from_position=100, to_position=0)
    assert plan.position_at(_T0 + dt.timedelta(seconds=22)) == 50
    assert plan.position_at(_T0 + dt.timedelta(seconds=600)) == 0


# ------------------------------------------------------------------ #
# Recording at the dispatch chokepoint
# ------------------------------------------------------------------ #


def test_plan_recorded_on_dispatch(monkeypatch):
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)

    svc._prepare_service_call("cover.a", 50)

    plan = svc.state("cover.a").travel_plan
    assert plan is not None
    assert (plan.from_position, plan.to_position) == (0, 50)
    assert plan.duration_seconds == pytest.approx(20.0)
    assert plan.start_delay_seconds == pytest.approx(2.0)


def test_no_plan_recorded_when_the_cover_is_uncalibrated(monkeypatch):
    svc = _svc(caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)

    svc._prepare_service_call("cover.a", 50)

    assert svc.state("cover.a").travel_plan is None


def test_no_plan_recorded_when_the_origin_is_unknown(monkeypatch):
    """No from-position means no ramp — an estimate needs both ends."""
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: None)

    svc._prepare_service_call("cover.a", 50)

    assert svc.state("cover.a").travel_plan is None


def test_assumed_position_supplies_the_origin_for_a_no_feedback_cover(monkeypatch):
    """The whole point of manual entry: a cover with no position still ramps.

    The live read is None, so the display-only assumed position (#888) is the
    only origin available. It must be read BEFORE _record_assumed_if_blind
    overwrites it with this command's own target.
    """
    svc = _svc(calibration={"cover.a": _MANUAL_ONLY}, caps=_OPEN_CLOSE_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: None)
    svc.record_assumed_position("cover.a", 100)

    svc._prepare_service_call("cover.a", 0)

    plan = svc.state("cover.a").travel_plan
    assert plan is not None
    assert (plan.from_position, plan.to_position) == (100, 0)


# ------------------------------------------------------------------ #
# Lifecycle — locked to `waiting`, exactly like transit_direction
# ------------------------------------------------------------------ #


def test_plan_cleared_when_waiting_clears(monkeypatch):
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)
    svc._prepare_service_call("cover.a", 50)

    svc._clear_waiting(svc.state("cover.a"))

    assert svc.state("cover.a").travel_plan is None


def test_travel_plans_gated_on_waiting(monkeypatch):
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)
    svc._prepare_service_call("cover.a", 50)
    assert "cover.a" in svc.travel_plans()

    svc.state("cover.a").waiting = False
    assert svc.travel_plans() == {}


def test_travel_plans_payload_shape(monkeypatch):
    """The card's wire contract — keys are load-bearing, so pin them."""
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)
    svc._prepare_service_call("cover.a", 50)

    payload = svc.travel_plans()["cover.a"]
    assert set(payload) == {
        "from",
        "to",
        "started_at",
        "start_delay_seconds",
        "duration_seconds",
    }
    assert payload["from"] == 0
    assert payload["to"] == 50
    # Serialisable — the attribute is JSON'd into the state machine.
    assert isinstance(payload["started_at"], str)


def test_estimated_positions_only_covers_entities_with_a_live_plan(monkeypatch):
    svc = _svc(
        calibration={"cover.a": _ASYMMETRIC, "cover.b": _ASYMMETRIC},
        caps=_POSITION_CAPS,
    )
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)
    svc._prepare_service_call("cover.a", 100)

    now = svc.state("cover.a").travel_plan.started_at + dt.timedelta(seconds=22)
    assert svc.estimated_positions(now) == {"cover.a": 50}
    assert svc.estimated_position("cover.b", now) is None


def test_has_travel_plans_drives_the_proxy_tick(monkeypatch):
    """The 1 s proxy timer starts and stops on this predicate — it must be exact."""
    svc = _svc(calibration={"cover.a": _ASYMMETRIC}, caps=_POSITION_CAPS)
    monkeypatch.setattr(svc, "_read_position_with_capabilities", lambda *a, **k: 0)
    assert svc.has_travel_plans() is False

    svc._prepare_service_call("cover.a", 50)
    assert svc.has_travel_plans() is True

    svc._clear_waiting(svc.state("cover.a"))
    assert svc.has_travel_plans() is False


# ------------------------------------------------------------------ #
# Malformed stored rows
#
# This table is persisted user-editable config. An older build, a hand-edited
# .storage file or a half-written row can all put nonsense here, and every
# consumer is on the command-dispatch path — so a bad row must cost the ramp,
# never the command.
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({}, id="no-full-travel-key"),
        pytest.param({"full_travel_seconds": "abc"}, id="non-numeric"),
        pytest.param({"full_travel_seconds": None}, id="null"),
        pytest.param({"full_travel_seconds": 0}, id="zero"),
        pytest.param({"full_travel_seconds": -5}, id="negative"),
    ],
)
def test_an_unusable_row_yields_no_plan_rather_than_raising(row):
    assert (
        build_travel_plan(
            row, from_position=0, to_position=50, started_at=_T0, span=100
        )
        is None
    )


def test_a_corrupt_leg_falls_back_to_the_headline_figure():
    row = {"full_travel_seconds": 30.0, "open_seconds": "nope", "close_seconds": 0}
    rising = build_travel_plan(
        row, from_position=0, to_position=100, started_at=_T0, span=100
    )
    falling = build_travel_plan(
        row, from_position=100, to_position=0, started_at=_T0, span=100
    )
    assert rising.duration_seconds == pytest.approx(30.0)
    assert falling.duration_seconds == pytest.approx(30.0)


def test_a_corrupt_start_delay_is_treated_as_none():
    row = {"full_travel_seconds": 30.0, "start_delay_seconds": "soon"}
    plan = build_travel_plan(
        row, from_position=0, to_position=100, started_at=_T0, span=100
    )
    assert plan.start_delay_seconds == 0.0


def test_a_zero_duration_ramp_reports_the_target_immediately():
    """Degenerate but reachable — a stored row rounded down to nothing."""
    plan = TravelPlan(
        from_position=0,
        to_position=100,
        started_at=_T0,
        start_delay_seconds=0.0,
        duration_seconds=0.0,
    )
    assert plan.position_at(_T0 + dt.timedelta(seconds=1)) == 100
