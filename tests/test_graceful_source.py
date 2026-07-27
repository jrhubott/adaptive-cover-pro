"""Unit tests for the GracefulSource grace-period state machine (issue #742).

GracefulSource is a pure, HA-free, asyncio-free kernel: it tracks a tri-state
"verdict" source (bool / bool / None=indeterminate) and decides, on each
``observe``, whether the caller should use the live verdict (DETERMINATE), the
last-known verdict during a grace window (HOLDING), or apply its own fallback
(FELL_BACK). A fake monotonic clock drives time so the grace window is exact.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.managers.common.graceful_source import (
    GracefulSource,
    Resolution,
    SourceResolution,
)


@pytest.fixture
def clock():
    """Return a mutable fake clock: ``t[0]`` is "now", read via the callable."""
    t = [0.0]
    return t


def _src(clock, grace: float = 120.0) -> GracefulSource:
    return GracefulSource(grace, clock=lambda: clock[0])


def test_determinate_records_last_known(clock):
    src = _src(clock)
    assert src.observe(True) == Resolution(SourceResolution.DETERMINATE, True)
    assert src.last_known is True
    assert src.observe(False) == Resolution(SourceResolution.DETERMINATE, False)
    assert src.last_known is False


def test_holding_returns_last_known_within_grace(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 60.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    clock[0] = 119.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)


def test_fell_back_after_grace(clock):
    src = _src(clock)
    src.observe(True)
    # Indeterminacy begins now: the first observe(None) starts the grace window.
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    clock[0] = 121.0
    # Still indeterminate past the grace window → fall back.
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_no_last_known_falls_back_immediately(clock):
    src = _src(clock)
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)
    # Never armed the timer → remaining stays None.
    assert src.remaining() is None


def test_recovery_cancels_grace_and_rearms(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 60.0
    assert src.observe(None).state is SourceResolution.HOLDING
    # A real verdict clears the grace window.
    assert src.observe(True) == Resolution(SourceResolution.DETERMINATE, True)
    assert src.remaining() is None
    # The machine is re-armed: a later indeterminacy starts a fresh window.
    clock[0] = 200.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)


def test_idempotent_observe_does_not_advance(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 50.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    # Repeated observe(None) at the SAME clock value must not move the anchor.
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    # The anchor is fixed at first sight (t=50), so at t=170 (120 later) it flips.
    clock[0] = 171.0
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_remaining_drives_wake(clock):
    src = _src(clock)
    src.observe(True)
    # Indeterminate at t=0 → full grace remaining.
    assert src.observe(None).state is SourceResolution.HOLDING
    assert src.remaining() == pytest.approx(120.0)
    clock[0] = 119.0
    assert src.remaining() == pytest.approx(1.0)
    clock[0] = 121.0
    assert src.remaining() is None
    # Determinate again → no wake needed.
    src.observe(True)
    assert src.remaining() is None


def test_reset_forgets_state(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 30.0
    assert src.observe(None).state is SourceResolution.HOLDING
    src.reset()
    assert src.last_known is None
    # With no last-known, the next indeterminacy falls back immediately.
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


# ---------------------------------------------------------------------------
# Generic payload (issue #1012): GracefulSource[bool] is not the only shape —
# the custom-position per-input hold needs GracefulSource[tuple[bool,
# tuple[str, ...]]]. The state machine mechanics (DETERMINATE / HOLDING /
# FELL_BACK, the grace window, idempotent re-observation) must work
# identically regardless of the payload's shape.
# ---------------------------------------------------------------------------


def test_generic_tuple_payload_holds_and_falls_back(clock):
    """A non-bool payload (a 2-tuple, as the custom-position sensor fold uses)
    goes through the exact same DETERMINATE/HOLDING/FELL_BACK mechanics as a
    bool payload.
    """
    src: GracefulSource[tuple[bool, tuple[str, ...]]] = _src(clock)
    fresh = (True, ("binary_sensor.rain",))
    assert src.observe(fresh) == Resolution(SourceResolution.DETERMINATE, fresh)
    assert src.last_known == fresh

    # Indeterminacy begins now (t=0): the first observe(None) starts the
    # grace window (default anchor = first indeterminate sighting).
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, fresh)

    clock[0] = 121.0
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_falsy_tuple_payload_is_not_mistaken_for_indeterminate(clock):
    """A genuinely-read but entirely falsy payload (``False``, an empty
    tuple) must be recorded as a real DETERMINATE verdict, never confused
    with the ``None`` indeterminate sentinel — the sentinel check is
    ``is not None``, not truthiness.
    """
    src: GracefulSource[tuple[bool, tuple[str, ...]]] = _src(clock)
    falsy = (False, ())
    assert src.observe(falsy) == Resolution(SourceResolution.DETERMINATE, falsy)
    assert src.last_known == falsy

    # A later indeterminate reading holds the falsy (not "missing") payload.
    clock[0] = 10.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, falsy)


def test_bool_false_payload_is_not_mistaken_for_indeterminate(clock):
    """Same distinction for a plain ``bool`` payload: a genuine ``False``
    verdict is DETERMINATE, not FELL_BACK — the pre-existing gate consumer
    already relies on this (``test_determinate_records_last_known``); this
    pins it down explicitly as part of the generic-payload contract.
    """
    src = _src(clock)
    assert src.observe(False) == Resolution(SourceResolution.DETERMINATE, False)
    assert src.last_known is False


# ---------------------------------------------------------------------------
# anchor_at_last_known (issue #1012 audit): the daytime gate anchors the
# grace window at the FIRST INDETERMINATE SIGHTING (the default, preserved
# above and locked by test_idempotent_observe_does_not_advance /
# test_seconds_until_gate_fallback_phases). The custom-position per-input
# hold instead anchors at the source's LAST KNOWN-GOOD observation. These two
# anchors genuinely diverge whenever time passes between the last valid
# reading and the first indeterminate one — these tests pin down that the
# opt-in constructor flag produces the second behaviour without touching the
# default.
# ---------------------------------------------------------------------------


def test_anchor_at_last_known_measures_from_last_valid_reading(clock):
    """With ``anchor_at_last_known=True``, the grace window is measured from
    the last DETERMINATE observation, not from when indeterminacy began —
    the opposite of the default (see
    ``test_idempotent_observe_does_not_advance`` for the default).
    """
    src = GracefulSource(120.0, clock=lambda: clock[0], anchor_at_last_known=True)
    src.observe(True)  # last known-good at t=0.

    # First indeterminate sighting is much later (t=100) — under the default
    # anchor this would leave a nearly-full grace window remaining; anchored
    # at last-known (t=0) instead, only 20s of the 120s window remain.
    clock[0] = 100.0
    resolution = src.observe(None)
    assert resolution == Resolution(SourceResolution.HOLDING, True)
    assert src.remaining() == pytest.approx(20.0)

    # Past the last-known anchor's expiry (t=120) → FELL_BACK, even though
    # only 20s have elapsed since the first indeterminate sighting.
    clock[0] = 121.0
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_anchor_at_last_known_defaults_to_first_indeterminate(clock):
    """The constructor default (``anchor_at_last_known=False``) is unchanged
    from the pre-#1012 behaviour — this is what keeps the daytime gate
    (issue #742) byte-for-byte compatible without passing the new flag.
    """
    src = GracefulSource(120.0, clock=lambda: clock[0])
    src.observe(True)
    clock[0] = 100.0
    # First indeterminate sighting anchors here (t=100) → full grace left,
    # NOT the ~20s that anchor_at_last_known would report.
    src.observe(None)
    assert src.remaining() == pytest.approx(120.0)
