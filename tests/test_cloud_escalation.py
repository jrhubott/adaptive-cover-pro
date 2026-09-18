"""Cloud-suppression escalation to full open (issue #175, Layer 2).

Layer 1 gave cloud suppression a slat angle so a tilt-only venetian stops
freezing its slats under a cloud. This layer answers the other half of the
reporter's ask: *if the cloud keeps holding, stop half-measures and open the
cover fully.* They had hand-rolled it with an ``input_boolean`` named
"sonne_langer_weg" (sun's been gone a long while) plus a template-driven custom
position slot.

Four things are pinned here, and each is a separate failure mode:

* the **clock** lives in ``CloudSuppressionManager`` — a start instant plus a
  deadline *derived* on every read, so changing the delay mid-hold moves the
  deadline immediately instead of needing a recompute path;
* the **action** is emitted only from inside ``CloudSuppressionHandler``,
  downstream of the untouched guard stack, so an escalated clock can never
  command a position where suppression has no business acting (#417, #1272);
* the escalated position comes from ``position_for_intent(sun_through=True)``,
  not a hardcoded 100 — logical 100 *extends* an awning, which is the exact
  inverse of "open fully";
* the deadline **survives a restart**, because #1238 deliberately makes cloud
  suppression re-engage instantly on reload and a volatile clock would hand a
  daily-restarting install a fresh full hold forever.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from custom_components.adaptive_cover_pro.const import (
    POSITION_CLOSED,
    POSITION_OPEN,
    CloudSuppressionPhase,
    ControlMethod,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
    CloudSuppressionManager,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.reason_i18n import render_en
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

# The Layer 1 suite already owns "a snapshot whose every cloud-suppression
# guard is cleared, holding the reporter's own cloudy position". Re-declaring
# that fixture here would be two definitions of one setup, free to drift the
# moment a guard is added to ``evaluate`` — so this suite borrows it and only
# adds the escalated flag on top.
from tests.test_cloudy_tilt import _cloud_options, _cloud_snapshot
from tests.test_pipeline.conftest import _make_mock_cover

pytestmark = pytest.mark.unit


# Two hours — the reporter's own figure, and the value
# DEFAULT_MANUAL_OVERRIDE_DURATION already carries for the only other
# long-duration hold in the integration.
_DELAY = 7200
_T0 = "2026-06-15 12:00:00"


def _readings(*, is_sunny: bool) -> ClimateReadings:
    """Build readings whose only suppression trigger is the weather state."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _manager(*, escalation_delay_seconds: int | None = _DELAY):
    """Return an enabled manager with hold-time 0 and the given delay."""
    mgr = CloudSuppressionManager(logger=MagicMock())
    mgr.update_config(
        enabled=True,
        hold_time_seconds=0,
        escalation_delay_seconds=escalation_delay_seconds,
    )
    return mgr


def _engage(mgr) -> None:
    """Drive the manager onto its suppression engage edge."""
    mgr.evaluate(_readings(is_sunny=False))


def _disengage(mgr) -> None:
    """Drive the manager onto its suppression disengage edge."""
    mgr.evaluate(_readings(is_sunny=True))


# ---------------------------------------------------------------------------
# Step 8 — the manager owns the phase and the derived deadline
# ---------------------------------------------------------------------------


def test_idle_before_suppression_engages() -> None:
    """No suppression, no phase: IDLE is the absence of a hold, not a hold at 0."""
    mgr = _manager()

    assert mgr.phase is CloudSuppressionPhase.IDLE
    assert mgr.is_escalation_active is False
    assert mgr.escalation_deadline is None
    assert mgr.seconds_until_escalation() is None


def test_no_delay_configured_never_leaves_holding() -> None:
    """An absent delay means "hold for as long as the cloud lasts".

    This is the invariant that lets the option ship with no config migration:
    every install that never opened the Light & Cloud step keeps holding
    indefinitely, exactly as it did before #175. If this goes red the option is
    no longer absent-means-off and the no-migration argument has collapsed.
    """
    mgr = _manager(escalation_delay_seconds=None)
    with freeze_time(_T0) as frozen:
        _engage(mgr)
        assert mgr.phase is CloudSuppressionPhase.HOLDING
        # A week of cloud is still a hold when nobody asked for an escalation.
        frozen.tick(dt.timedelta(days=7))
        assert mgr.phase is CloudSuppressionPhase.HOLDING
        assert mgr.is_escalation_active is False
        assert mgr.escalation_deadline is None
        assert mgr.seconds_until_escalation() is None


def test_engage_edge_records_the_start_and_derives_the_deadline() -> None:
    """The engage edge is the only thing that starts the clock."""
    with freeze_time(_T0):
        mgr = _manager()
        _engage(mgr)

        started = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC)
        assert mgr.suppression_started_at == started
        assert mgr.escalation_deadline == started + dt.timedelta(seconds=_DELAY)
        assert mgr.phase is CloudSuppressionPhase.HOLDING
        assert mgr.seconds_until_escalation() == pytest.approx(_DELAY)


def test_phase_flips_to_escalated_at_the_deadline_instant() -> None:
    """Both sides of the boundary, because "after N hours" needs an edge.

    The deadline instant itself counts as escalated: a wake armed for exactly
    the remaining seconds must find the phase already flipped when it fires,
    or the escalation would need a second cycle to take effect.
    """
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)

        frozen.tick(dt.timedelta(seconds=_DELAY - 1))
        assert mgr.phase is CloudSuppressionPhase.HOLDING
        assert mgr.is_escalation_active is False
        assert mgr.seconds_until_escalation() == pytest.approx(1)

        frozen.tick(dt.timedelta(seconds=1))
        assert mgr.phase is CloudSuppressionPhase.ESCALATED
        assert mgr.is_escalation_active is True
        # Already escalated → nothing left to wake up for.
        assert mgr.seconds_until_escalation() is None


def test_a_sunny_spell_resets_the_clock() -> None:
    """The disengage edge clears the hold, so the next cloud starts over.

    Deliberate and documented (Q2): the option's own description points at the
    smoothing hold time as the debounce for riding out short gaps, rather than
    growing a third timer for a problem an existing option already solves.
    """
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)
        frozen.tick(dt.timedelta(seconds=_DELAY - 60))

        _disengage(mgr)
        assert mgr.phase is CloudSuppressionPhase.IDLE
        assert mgr.suppression_started_at is None
        assert mgr.escalation_deadline is None

        _engage(mgr)
        assert mgr.escalation_deadline == dt.datetime(
            2026, 6, 15, 12, 0, tzinfo=dt.UTC
        ) + dt.timedelta(seconds=_DELAY - 60 + _DELAY)
        assert mgr.phase is CloudSuppressionPhase.HOLDING


def test_a_continuing_hold_does_not_re_arm_the_clock() -> None:
    """Only the engage EDGE arms it — a cycle that agrees must not restart it."""
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)
        deadline = mgr.escalation_deadline

        for _ in range(5):
            frozen.tick(dt.timedelta(minutes=10))
            _engage(mgr)
            assert mgr.escalation_deadline == deadline


def test_disabling_the_feature_clears_the_clock() -> None:
    """``update_config(enabled=False)`` resets, so nothing survives a toggle-off."""
    with freeze_time(_T0):
        mgr = _manager()
        _engage(mgr)
        assert mgr.suppression_started_at is not None

        mgr.update_config(enabled=False)
        assert mgr.phase is CloudSuppressionPhase.IDLE
        assert mgr.suppression_started_at is None
        assert mgr.escalation_deadline is None


def test_a_readings_outage_clears_the_clock() -> None:
    """``evaluate(None)`` runs the same reset, so the hold ends with the data."""
    with freeze_time(_T0):
        mgr = _manager()
        _engage(mgr)

        mgr.evaluate(None)
        assert mgr.phase is CloudSuppressionPhase.IDLE
        assert mgr.suppression_started_at is None


def test_changing_the_delay_mid_hold_moves_the_deadline_immediately() -> None:
    """The deadline is derived on every read, never stored alongside the start.

    A stored deadline would need a recompute path on every options change —
    and would silently keep the old answer for anyone who edited the delay
    while a cloud was already holding.
    """
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)
        started = mgr.suppression_started_at
        frozen.tick(dt.timedelta(minutes=30))

        mgr.update_config(
            enabled=True, hold_time_seconds=0, escalation_delay_seconds=1800
        )

        assert mgr.suppression_started_at == started
        assert mgr.escalation_deadline == started + dt.timedelta(seconds=1800)
        # Half an hour in with a half-hour delay: already due.
        assert mgr.phase is CloudSuppressionPhase.ESCALATED


def test_clearing_the_delay_mid_hold_returns_to_holding() -> None:
    """Un-configuring the delay must not leave a stale escalated phase behind."""
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)
        frozen.tick(dt.timedelta(seconds=_DELAY + 60))
        assert mgr.phase is CloudSuppressionPhase.ESCALATED

        mgr.update_config(
            enabled=True, hold_time_seconds=0, escalation_delay_seconds=None
        )

        assert mgr.phase is CloudSuppressionPhase.HOLDING
        assert mgr.escalation_deadline is None


def test_update_config_defaults_the_delay_to_none() -> None:
    """A caller predating #175 gets no escalation at all (G2 / back-compat)."""
    mgr = CloudSuppressionManager(logger=MagicMock())
    mgr.update_config(enabled=True, hold_time_seconds=0)

    with freeze_time(_T0) as frozen:
        _engage(mgr)
        frozen.tick(dt.timedelta(days=1))
        assert mgr.phase is CloudSuppressionPhase.HOLDING


def test_phase_values_are_wire_stable() -> None:
    """Diagnostics and a sensor attribute publish these strings verbatim."""
    assert CloudSuppressionPhase.IDLE == "idle"
    assert CloudSuppressionPhase.HOLDING == "holding"
    assert CloudSuppressionPhase.ESCALATED == "escalated"


# ---------------------------------------------------------------------------
# Step 10 — the escalated handler branch
# ---------------------------------------------------------------------------


def _escalated_snapshot(**overrides):
    """Snapshot that clears every guard AND carries the escalated flag."""
    kwargs = {"cloud_escalation_active": True}
    kwargs.update(overrides)
    return _cloud_snapshot(**kwargs)


def test_escalated_branch_opens_to_the_unshaded_position() -> None:
    """After the delay the cover opens fully instead of holding cloudy_position.

    The whole point of Layer 2. ``cloudy_position`` is 30 here so a result of
    ``unshaded_position`` cannot have come from the hold branch.
    """
    result = CloudSuppressionHandler().evaluate(
        _escalated_snapshot(options=_cloud_options(cloudy_position=30))
    )

    assert result is not None
    assert result.control_method is ControlMethod.CLOUD
    assert result.position == POSITION_OPEN
    assert result.cloud_escalation_active is True


def test_escalated_position_comes_from_the_snapshot_not_a_literal() -> None:
    """``unshaded_position`` is the policy's answer, so the branch must read it.

    Hardcoding 100 would fully EXTEND an awning after two hours of cloud — the
    exact inverse of the ask — because logical ``open`` blocks the sun on that
    family. Feeding the branch a deliberately odd value proves it reads the
    field rather than a constant that happens to match.
    """
    result = CloudSuppressionHandler().evaluate(
        _escalated_snapshot(unshaded_position=0)
    )

    assert result is not None
    assert result.position == 0


def test_not_escalated_still_holds_the_cloudy_position() -> None:
    """The hold phase is byte-identical to Layer 1 — the flag gates everything."""
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(options=_cloud_options(cloudy_position=30))
    )

    assert result is not None
    assert result.position == 30
    assert result.cloud_escalation_active is False


def test_escalation_fires_with_no_cloudy_position_configured() -> None:
    """The two options are independently optional.

    "Open fully after N hours of cloud" is a complete feature on its own, so
    the escalated branch must sit ABOVE the ``cloudy is not None`` test rather
    than inside it — otherwise the delay would silently do nothing for anyone
    who never set a cloudy position.
    """
    result = CloudSuppressionHandler().evaluate(
        _escalated_snapshot(options=_cloud_options(cloudy_position=None))
    )

    assert result is not None
    assert result.position == POSITION_OPEN
    assert result.cloud_escalation_active is True


def test_sunset_outranks_escalation() -> None:
    """At sunset the user's sunset position wins; "open fully" is a daytime answer."""
    result = CloudSuppressionHandler().evaluate(
        _escalated_snapshot(is_sunset_active=True, default_position=20, sunset_tilt=0)
    )

    assert result is not None
    assert result.position == 20
    assert result.tilt == 0
    assert result.cloud_escalation_active is False


def test_escalated_branch_still_names_the_cloudy_tilt() -> None:
    """One tilt resolution for the whole cloud-suppression seat (Q9).

    "Slats open while it's cloudy" does not stop being true when the carriage
    rises too, and resolving the tilt once rather than per branch is one
    definition instead of two.
    """
    result = CloudSuppressionHandler().evaluate(
        _escalated_snapshot(options=_cloud_options(cloudy_tilt=100))
    )

    assert result is not None
    assert result.position == POSITION_OPEN
    assert result.tilt == 100


def test_escalated_position_is_clamped_by_max_position() -> None:
    """A user with max_position 80 escalates to 80, not 100.

    Same ``apply_snapshot_limits(..., sun_valid=False)`` treatment
    ``cloudy_position`` already gets on the very next branch, so the two
    answers cannot disagree about what the configured bounds mean.
    """
    cover = _make_mock_cover(direct_sun_valid=True)
    cover.config.max_pos = 80

    result = CloudSuppressionHandler().evaluate(_escalated_snapshot(cover=cover))

    assert result is not None
    assert result.position == 80


def test_escalated_branch_names_its_own_reason_fragment() -> None:
    """The trace must say WHY the cover opened, not just that cloud won."""
    result = CloudSuppressionHandler().evaluate(_escalated_snapshot())

    assert result is not None
    payload = result.reason_payload
    assert payload is not None
    assert payload.params["pos_label"].code == (
        ReasonCode.FRAGMENT_CLOUD_ESCALATED_POSITION
    )
    assert "opened fully" in render_en(payload)


@pytest.mark.parametrize(
    ("cover_type", "expected"),
    [
        ("cover_blind", POSITION_OPEN),
        ("cover_venetian", POSITION_OPEN),
        ("cover_tilt", POSITION_OPEN),
        ("cover_awning", POSITION_CLOSED),
    ],
)
def test_unshaded_position_is_polarity_correct_per_cover_type(
    cover_type, expected
) -> None:
    """Opening fully is a semantic intent, and awnings resolve it backwards.

    ``CoverAxis.open_blocks_sun`` is True for awnings — extending one is
    MAXIMUM shade — so escalating to logical 100 would deploy the awning after
    two hours of cloud. The snapshot carries the policy's own answer instead,
    which is why no cover-type string reaches the handler (G3).
    """
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    assert get_policy(cover_type).position_for_intent(sun_through=True) == expected


# ---------------------------------------------------------------------------
# Step 11 — R3: #417 and #1272 are preserved BY CONSTRUCTION
#
# These are characterization tests, and they are expected to pass on the first
# run. The escalation clock lives in the manager but the escalated POSITION is
# emitted only from inside ``evaluate()``, downstream of a guard stack this
# change does not touch — so there is no bug here to drive out. What they buy
# is the future: a refactor that hoists the escalation upstream of the guards,
# or hands it to the manager to command directly, turns these red instead of
# quietly re-introducing #417's symptom under a new name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guard",
    [
        {"direct_sun_valid": False},
        {"in_time_window": False},
        {"climate_readings": None},
        {"climate_extreme_heat_active": True},
        {"cloud_suppression_active": False},
    ],
    ids=[
        "outside-fov-417",
        "outside-time-window",
        "no-climate-readings",
        "extreme-heat-hold-1272",
        "suppression-not-resolved-864",
    ],
)
def test_escalation_is_not_emitted_while_any_guard_holds(guard) -> None:
    """An escalated clock must not out-rank a single one of the guards.

    #417's fix put ``direct_sun_valid`` ABOVE the resolved-bool gate precisely
    so cloud suppression can never command a position while the sun is outside
    the window FOV. An escalation that fired from anywhere other than inside
    ``evaluate()`` would reproduce that symptom under a new name: the clock
    keeps running during the hours the sun is out of view, and its expiry would
    otherwise fling the cover open with no sun anywhere near the window.
    """
    assert CloudSuppressionHandler().evaluate(_escalated_snapshot(**guard)) is None


def test_escalation_is_not_emitted_while_suppression_is_disabled() -> None:
    """The master toggle still ends the conversation before any phase matters."""
    snapshot = _escalated_snapshot(
        options=_cloud_options(cloud_suppression_enabled=False),
        cloud_suppression_active=True,
    )

    assert CloudSuppressionHandler().evaluate(snapshot) is None


def test_the_manager_never_commands_anything_itself() -> None:
    """The clock's whole public surface is read-only (R3).

    The manager publishes a phase, a deadline and a countdown. It holds no
    refresh callback for the escalation, dispatches nothing, and has no
    ``hass`` — so "the escalation only ever leaves through the handler" is a
    structural property, not a convention someone has to remember.
    """
    with freeze_time(_T0) as frozen:
        mgr = _manager()
        _engage(mgr)
        frozen.tick(dt.timedelta(seconds=_DELAY))

        assert mgr.is_escalation_active is True
        assert not hasattr(mgr, "hass")
        assert not any(
            name.startswith("command") or name.startswith("send") for name in dir(mgr)
        )


# ---------------------------------------------------------------------------
# Step 13 — the dedicated coordinator wake
# ---------------------------------------------------------------------------


def _coord_for_wake(*, secs: float | None):
    """Build a bare coordinator carrying only what the escalation wake reads."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord.hass = MagicMock()
    coord._cloud_escalation_unsub = None
    coord._refresh_after_unsub = None
    cloud_mgr = MagicMock()
    cloud_mgr.seconds_until_escalation.return_value = secs
    coord._cloud_mgr = cloud_mgr
    return coord


def test_no_wake_is_armed_when_there_is_no_deadline() -> None:
    """No hold, no delay, or already escalated — all arrive here as None."""
    from unittest.mock import patch

    coord = _coord_for_wake(secs=None)
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later"
    ) as call_later:
        coord._schedule_cloud_escalation_wake()

    call_later.assert_not_called()
    assert coord._cloud_escalation_unsub is None


def test_a_live_deadline_arms_one_wake_at_the_remaining_seconds() -> None:
    """Precision, not an outage fix — ``sun.sun`` already provides a heartbeat.

    Without the wake the escalation would land on whichever incidental update
    happens to follow the deadline, which on a quiet afternoon can be minutes
    of a dark room.
    """
    from unittest.mock import patch

    coord = _coord_for_wake(secs=42.0)
    cancel = MagicMock()
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=cancel,
    ) as call_later:
        coord._schedule_cloud_escalation_wake()

    call_later.assert_called_once()
    assert call_later.call_args.args[0] is coord.hass
    assert call_later.call_args.args[1] == 42.0
    assert coord._cloud_escalation_unsub is cancel


def test_re_arming_cancels_the_previous_wake() -> None:
    """One outstanding handle, re-armed every cycle like the other three."""
    from unittest.mock import patch

    coord = _coord_for_wake(secs=10.0)
    previous = MagicMock()
    coord._cloud_escalation_unsub = previous
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        coord._schedule_cloud_escalation_wake()

    previous.assert_called_once()


def test_escalation_wake_does_not_clobber_the_refresh_after_handle() -> None:
    """The #756 anti-clobber, asserted rather than assumed.

    ``_schedule_refresh_after`` holds a SINGLE ``_refresh_after_unsub`` and
    cancels it unconditionally — and venetian back-rotate suppression already
    owns it. Reusing that handle here would mean an escalation wake silently
    cancelling a deferred tilt-only update, and vice versa, with no test
    anywhere noticing.
    """
    from unittest.mock import patch

    coord = _coord_for_wake(secs=30.0)
    venetian_handle = MagicMock()
    coord._refresh_after_unsub = venetian_handle
    with patch(
        "custom_components.adaptive_cover_pro.coordinator.async_call_later",
        return_value=MagicMock(),
    ):
        coord._schedule_cloud_escalation_wake()

    venetian_handle.assert_not_called()
    assert coord._refresh_after_unsub is venetian_handle
    assert coord._cloud_escalation_unsub is not venetian_handle


async def test_the_due_callback_clears_the_handle_and_refreshes() -> None:
    """Fires once at the deadline; the next cycle re-reads the phase."""
    from unittest.mock import AsyncMock

    coord = _coord_for_wake(secs=None)
    coord._cloud_escalation_unsub = MagicMock()
    coord.async_request_refresh = AsyncMock()

    await coord._on_cloud_escalation_due(None)

    assert coord._cloud_escalation_unsub is None
    coord.async_request_refresh.assert_awaited_once()


async def test_async_shutdown_cancels_the_cloud_escalation_wake() -> None:
    """A two-hour wake must not outlive the entry that armed it.

    The longest-lived ``async_call_later`` in the integration: the other three
    wakes in this family measure grace windows in minutes, while this one can
    legitimately be pending for hours. An unload or reload that left it armed
    would fire a refresh against a coordinator nobody owns any more.
    """
    from tests.ha_helpers import _bare_coordinator

    cancel = MagicMock()
    coord = _bare_coordinator(cloud_escalation_unsub=cancel)

    await coord.async_shutdown()

    cancel.assert_called_once()
    assert coord._cloud_escalation_unsub is None


# ---------------------------------------------------------------------------
# Step 14 — R4: the deadline survives a restart
# ---------------------------------------------------------------------------


def _escalation_sensor(mgr):
    """Return a ``_CloudEscalationEndSensor`` wired to *mgr*, no HA machinery.

    Mirrors ``tests/test_manual_override_persistence.py::_make_sensor``: the
    restore path reads exactly one collaborator off the coordinator, so the
    rest of ``CoordinatorEntity`` is bypassed with ``object.__new__``.
    """
    from custom_components.adaptive_cover_pro.sensor import _CloudEscalationEndSensor

    coordinator = MagicMock()
    coordinator._cloud_mgr = mgr

    sensor = object.__new__(_CloudEscalationEndSensor)
    sensor.coordinator = coordinator
    sensor.hass = MagicMock()
    sensor.config_entry = MagicMock()
    sensor.entity_id = "sensor.test_cover_cloud_escalation_end_time"
    sensor._entry_id = "test_entry"
    return sensor


def _state(value):
    """Build a restored ``State`` carrying *value* as its state string."""
    from homeassistant.core import State

    return State("sensor.test_cover_cloud_escalation_end_time", value)


def test_a_future_restored_deadline_rehydrates_the_start_instant() -> None:
    """The persisted deadline is inverted back into ``started_at``.

    ``started_at_for_expiry`` is the existing inverse used by manual override's
    own restore path, so the arithmetic has one definition and cannot disagree
    with the forward direction the deadline is derived by.
    """
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        deadline = dt.datetime(2026, 6, 15, 13, 0, tzinfo=dt.UTC)

        assert sensor._restore_from_attributes({}, _state(deadline.isoformat())) is True

        # An hour of the two-hour hold is already spent: the restored start is
        # an hour BEFORE the restart, not "now".
        assert mgr.suppression_started_at == deadline - dt.timedelta(seconds=_DELAY)
        assert mgr.escalation_deadline == deadline


def test_restart_mid_hold_does_not_grant_a_fresh_delay() -> None:
    """R4 end to end — the reason this sensor exists at all.

    #1238's ``seeded=False`` makes cloud suppression re-engage INSTANTLY on
    reload, so a volatile clock would restart the two hours on every restart
    and an install that reboots daily would never escalate. Here the restart
    lands 119 minutes into a 120-minute hold, and the escalation must still
    fire one minute later — not 120.
    """
    with freeze_time("2026-06-15 13:59:00") as frozen:
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        # Armed at 12:00 before the restart, so due at 14:00.
        deadline = dt.datetime(2026, 6, 15, 14, 0, tzinfo=dt.UTC)
        sensor._restore_from_attributes({}, _state(deadline.isoformat()))

        # The world as found: cloudy, so suppression commits in-line.
        _engage(mgr)
        assert mgr.phase is CloudSuppressionPhase.HOLDING
        assert mgr.seconds_until_escalation() == pytest.approx(60)

        frozen.tick(dt.timedelta(seconds=60))
        assert mgr.phase is CloudSuppressionPhase.ESCALATED


def test_restore_then_arm_keeps_the_restored_instant() -> None:
    """The engage edge must not overwrite a deadline already restored."""
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        deadline = dt.datetime(2026, 6, 15, 13, 0, tzinfo=dt.UTC)
        sensor._restore_from_attributes({}, _state(deadline.isoformat()))

        _engage(mgr)

        assert mgr.escalation_deadline == deadline


def test_arm_then_restore_converges_on_the_restored_instant() -> None:
    """Either order lands on the same (earlier, truer) start.

    Production always restores first — platform forwarding runs before the
    first refresh — but the pair must not depend on that, because the ordering
    lives in ``__init__.py`` and nothing in this module can enforce it.
    """
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        _engage(mgr)
        assert mgr.escalation_deadline == dt.datetime(2026, 6, 15, 14, 0, tzinfo=dt.UTC)

        deadline = dt.datetime(2026, 6, 15, 13, 0, tzinfo=dt.UTC)
        sensor._restore_from_attributes({}, _state(deadline.isoformat()))

        assert mgr.escalation_deadline == deadline


def test_a_delay_configured_after_the_restore_completes_the_inversion() -> None:
    """The restore hook runs BEFORE the first cycle reads the delay.

    Platform forwarding happens before ``async_config_entry_first_refresh``, so
    at restore time ``update_config`` has never run and the delay is unknown.
    The inversion needs both halves and has to survive arriving in this order.
    """
    with freeze_time(_T0):
        mgr = CloudSuppressionManager(logger=MagicMock())
        sensor = _escalation_sensor(mgr)
        deadline = dt.datetime(2026, 6, 15, 13, 0, tzinfo=dt.UTC)
        sensor._restore_from_attributes({}, _state(deadline.isoformat()))

        mgr.update_config(
            enabled=True, hold_time_seconds=0, escalation_delay_seconds=_DELAY
        )

        assert mgr.escalation_deadline == deadline


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("2026-06-15 11:00:00+00:00", id="already-expired"),
        pytest.param("2099-01-01T00:00:00", id="naive-timestamp"),
        pytest.param("not-a-timestamp", id="unparseable"),
        pytest.param("", id="empty-string"),
        pytest.param("unknown", id="no-deadline-sentinel"),
        pytest.param("unavailable", id="unavailable-sentinel"),
        pytest.param(None, id="no-restored-state"),
    ],
)
def test_an_unusable_restored_deadline_is_dropped(raw) -> None:
    """Every corrupt or absent shape is dropped rather than raised (#1273).

    The payload is whatever some earlier build wrote, so this cannot assume its
    own output shape — and a NAIVE timestamp is the shape a rolled-back build
    could persist: it parses cleanly and then raises ``TypeError`` on the first
    comparison against an aware ``now``.

    ``already-expired`` is dropped deliberately, following the manual-override
    precedent. A deadline in the past says the escalation already fired before
    the restart; adopting one that could be weeks old would fling the cover
    open the instant any cloud appeared after a long shutdown.
    """
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)

        restored = sensor._restore_from_attributes(
            {}, None if raw is None else _state(raw)
        )

        assert restored is False
        assert mgr.suppression_started_at is None
        _engage(mgr)
        assert mgr.escalation_deadline == dt.datetime(2026, 6, 15, 14, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("sentinel", ["unknown", "unavailable", ""])
def test_the_no_deadline_sentinels_are_dropped_without_a_warning(sentinel) -> None:
    """A missing deadline is the NORMAL restart, not a corrupt payload.

    A TIMESTAMP sensor with no value persists as ``unknown``, so warning on it
    would put a scary line in the log of every install that ever restarts
    without a cloud overhead — which is most of them, most of the time.
    """
    logger = MagicMock()
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.adaptive_cover_pro.sensor._LOGGER", logger
        ):
            assert sensor._restore_from_attributes({}, _state(sentinel)) is False

    logger.warning.assert_not_called()


def test_the_sensor_publishes_the_deadline_and_the_phase() -> None:
    """The one part of this feature a user cannot debug from the outside."""
    from custom_components.adaptive_cover_pro.sensor import _SPEC_BY_SUFFIX

    spec = _SPEC_BY_SUFFIX["cloud_escalation_end_time"]
    with freeze_time(_T0):
        mgr = _manager()
        sensor = _escalation_sensor(mgr)
        _engage(mgr)

        assert spec.value_fn(sensor) == dt.datetime(2026, 6, 15, 14, 0, tzinfo=dt.UTC)
        attrs = spec.attrs_fn(sensor)
        assert attrs["phase"] == CloudSuppressionPhase.HOLDING
        assert attrs["delay_seconds"] == _DELAY
        assert attrs["started_at"] == "2026-06-15T12:00:00+00:00"


def test_the_sensor_reads_none_while_nothing_is_holding() -> None:
    """A TIMESTAMP sensor with no deadline reads None, like its precedent."""
    from custom_components.adaptive_cover_pro.sensor import _SPEC_BY_SUFFIX

    spec = _SPEC_BY_SUFFIX["cloud_escalation_end_time"]
    mgr = _manager()
    sensor = _escalation_sensor(mgr)

    assert spec.value_fn(sensor) is None
    attrs = spec.attrs_fn(sensor)
    assert attrs["phase"] == CloudSuppressionPhase.IDLE
    assert attrs["started_at"] is None


def test_the_sensor_spec_matches_its_manual_override_precedent() -> None:
    """A TIMESTAMP diagnostic with NO ``enabled_when`` (Q8).

    ``manual_override_end_time`` is the closest precedent and carries no gate,
    simply reading ``None`` when nothing is held. Gating this one on the delay
    being configured would also make the restore path conditional — and
    ``entity_registry_enabled_default`` is applied by HA only at first
    unique_id registration, so any later change of mind would reach new
    installs only.
    """
    from homeassistant.components.sensor import SensorDeviceClass

    from custom_components.adaptive_cover_pro.sensor import _SPEC_BY_SUFFIX

    spec = _SPEC_BY_SUFFIX["cloud_escalation_end_time"]
    entry = MagicMock()
    entry.options = {}

    assert spec.device_class is SensorDeviceClass.TIMESTAMP
    assert spec.should_poll is False
    assert spec.translation_key == "cloud_escalation_end_time"
    assert spec.enabled_when(entry) is True


def test_the_existing_restorable_sensors_keep_their_single_argument_hook() -> None:
    """Both #1022 / #1273 subclasses must be callable exactly as before.

    The base's hook gained a second parameter for the escalation deadline,
    which needs the restored ``State`` itself rather than a ``per_entity``
    dict. Given a safe default, neither existing subclass changes behaviour —
    and neither does the shared ``_parse_restored_expiry``, whose ``label``
    defaults to the manual-override wording.
    """
    from custom_components.adaptive_cover_pro.sensor import (
        _ACPRestorableDiagnosticSensor,
        _ManualOverrideEndSensor,
        _PositionVerificationSensor,
    )

    for cls in (
        _ACPRestorableDiagnosticSensor,
        _ManualOverrideEndSensor,
        _PositionVerificationSensor,
    ):
        sensor = object.__new__(cls)
        sensor.coordinator = MagicMock()
        sensor.coordinator.manager.covers = set()
        sensor.coordinator.entities = []
        assert sensor._restore_from_attributes({}) is False


# ---------------------------------------------------------------------------
# Step 15 — the delay's config surface
# ---------------------------------------------------------------------------


def test_the_delay_is_service_settable() -> None:
    """A FIELD_VALIDATORS entry with no service seat is dead code.

    The same test-enforced convention ``cloudy_tilt`` and
    ``weather_override_tilt`` follow: a key carrying a validator must be
    reachable from a service, or the validator never runs and the key is
    silently dropped.
    """
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY
    from custom_components.adaptive_cover_pro.services.options_service import (
        ALL_SETTABLE_KEYS,
        FIELD_VALIDATORS,
    )

    assert CONF_CLOUD_ESCALATION_DELAY in FIELD_VALIDATORS
    assert CONF_CLOUD_ESCALATION_DELAY in ALL_SETTABLE_KEYS


def test_the_delay_is_not_an_option_range() -> None:
    """A duration is not a bounded number, so it must not grow a range row."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        OPTION_RANGES,
    )

    assert CONF_CLOUD_ESCALATION_DELAY not in OPTION_RANGES


def test_the_delay_spec_carries_no_default() -> None:
    """Deliberately NOT a copy of the manual-override duration spec.

    ``_MANUAL_OVERRIDE_SPECS`` carries ``default={"hours": 2}`` — itself a
    literal duplicate of ``DEFAULT_MANUAL_OVERRIDE_DURATION``, which is the
    drift the no-magic-numbers rule warns about. Copying it here would be
    worse than a duplicate: it would turn every existing install's first visit
    to the Light & Cloud step into an opt-in to a two-hour escalation nobody
    asked for.
    """
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.config_fields import (
        FIELD_SPECS,
        ValidatorKind,
    )
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY

    spec = FIELD_SPECS[CONF_CLOUD_ESCALATION_DELAY]
    assert spec.validator is ValidatorKind.DURATION
    assert spec.clearable is True
    assert spec.marker().default is vol.UNDEFINED


def test_the_delay_renders_as_a_duration_selector() -> None:
    """A visibly different control from the hold-time slider beside it.

    One of the three mechanisms separating this option from
    ``cloud_suppression_hold_time``: a ``2:00:00`` duration field simply does
    not look like a ``0 s`` slider, so the two cannot be mistaken at a glance.
    """
    from homeassistant.helpers import selector as ha_selector

    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY

    schema = light_cloud_schema()
    sel = next(
        v for k, v in schema.schema.items() if str(k) == CONF_CLOUD_ESCALATION_DELAY
    )
    assert isinstance(sel, ha_selector.DurationSelector)


@pytest.mark.parametrize(
    ("include_tilt", "index"),
    [(True, 3), (False, 2)],
    ids=["venetian-after-the-slat-angle", "single-axis-straight-after-the-position"],
)
def test_the_delay_renders_with_the_other_behaviour_targets(
    include_tilt, index
) -> None:
    """Above every sensor field, which is #364's lesson.

    Index 3 on a venetian (behind the slat angle Layer 1 put at 2) and index 2
    on everything else — it follows whichever behaviour targets exist rather
    than claiming a fixed slot, and in both cases it sits above the sensor
    plumbing rather than twelve pickers down beside the smoothing hold-time.
    """
    from custom_components.adaptive_cover_pro.config_dynamic import light_cloud_schema
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUD_SUPPRESSION_HOLD_TIME,
    )

    keys = [str(k) for k in light_cloud_schema(include_tilt=include_tilt).schema]
    assert keys.index(CONF_CLOUD_ESCALATION_DELAY) == index
    assert keys.index(CONF_CLOUD_ESCALATION_DELAY) < keys.index("weather_entity")
    assert keys.index(CONF_CLOUD_ESCALATION_DELAY) < keys.index(
        CONF_CLOUD_SUPPRESSION_HOLD_TIME
    )


def test_the_delay_is_registered_for_selective_sync() -> None:
    """Three memberships, matching its Light & Cloud siblings.

    Unlike ``cloudy_tilt``, this key is in the UNGATED module-level
    ``LIGHT_CLOUD_SCHEMA``, so ``test_all_option_schema_keys_are_in_sync_-
    categories_or_excluded`` demands it. Asserting the full membership set
    anyway keeps it from syncing on one category and silently not the others.
    """
    from custom_components.adaptive_cover_pro.config_flow import SYNC_CATEGORIES
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUDY_POSITION,
    )

    position_categories = {
        name for name, keys in SYNC_CATEGORIES.items() if CONF_CLOUDY_POSITION in keys
    }
    delay_categories = {
        name
        for name, keys in SYNC_CATEGORIES.items()
        if CONF_CLOUD_ESCALATION_DELAY in keys
    }
    assert delay_categories == position_categories


def test_the_delay_is_stripped_when_cleared() -> None:
    """Blank must mean absent, or the option can never be turned back off."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _LIGHT_CLOUD_OPTIONAL_KEYS,
    )
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY

    assert CONF_CLOUD_ESCALATION_DELAY in _LIGHT_CLOUD_OPTIONAL_KEYS


def test_the_delay_is_a_live_option_key_for_every_cover_type() -> None:
    """Unlike the slat angle, "open fully after N hours" is universal.

    Nothing about giving up on a cloudy hold depends on having a second axis,
    so there is no policy gate here — and a gate would leave the escalation
    unavailable on exactly the single-axis covers that have no slats to open
    instead.
    """
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY
    from custom_components.adaptive_cover_pro.cover_types import (
        POLICY_REGISTRY,
        get_policy,
    )

    for cover_type in POLICY_REGISTRY:
        policy = get_policy(cover_type)
        if policy.is_orchestrator:
            continue
        assert CONF_CLOUD_ESCALATION_DELAY in policy.live_option_keys(), cover_type


def test_the_summary_renders_the_escalation_suffix() -> None:
    """G4: a behaviour-affecting option the summary does not mention is a lie."""
    from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUD_SUPPRESSION,
        CONF_CLOUDY_POSITION,
        CoverType,
    )

    summary = _build_config_summary(
        {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUDY_POSITION: 30,
            CONF_CLOUD_ESCALATION_DELAY: {"hours": 2, "minutes": 0, "seconds": 0},
        },
        sensor_type=CoverType.VENETIAN,
    )

    assert "opens fully after 2 h" in summary


def test_the_summary_omits_the_suffix_for_an_all_zero_duration() -> None:
    """A blank field stores all-zero, and the summary must not promise anything."""
    from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUD_SUPPRESSION,
        CoverType,
    )

    summary = _build_config_summary(
        {
            CONF_CLOUD_SUPPRESSION: True,
            CONF_CLOUD_ESCALATION_DELAY: {"hours": 0, "minutes": 0, "seconds": 0},
        },
        sensor_type=CoverType.VENETIAN,
    )

    assert "opens fully after" not in summary


def test_the_delay_joins_the_unified_ignored_settings_warning() -> None:
    """One ⚠️ line listing all three stranded targets, not three lines.

    The Layer 1 guard is driven by a ``(value, label, placeholder)`` table
    precisely so a third setting is a tuple rather than another ``if`` block.
    """
    from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUD_SUPPRESSION,
        CONF_CLOUDY_POSITION,
        CONF_CLOUDY_TILT,
        CoverType,
    )

    summary = _build_config_summary(
        {
            CONF_CLOUD_SUPPRESSION: False,
            CONF_CLOUDY_POSITION: 30,
            CONF_CLOUDY_TILT: 100,
            CONF_CLOUD_ESCALATION_DELAY: {"hours": 2, "minutes": 0, "seconds": 0},
        },
        sensor_type=CoverType.VENETIAN,
    )

    warning_lines = [ln for ln in summary.splitlines() if "will be ignored" in ln]
    assert len(warning_lines) == 1
    assert "30%" in warning_lines[0]
    assert "100%" in warning_lines[0]
    assert "2 h" in warning_lines[0]


def test_the_diagnostics_dump_carries_the_delay_and_the_live_phase() -> None:
    """The reporter's own evidence reached us through a diagnostics download.

    The stored delay is not enough on its own: "why has my cover not opened
    yet" needs the phase and the deadline the manager actually derived, which
    no config value reveals.
    """
    from custom_components.adaptive_cover_pro.const import CONF_CLOUD_ESCALATION_DELAY
    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        DiagnosticsBuilder,
    )

    with freeze_time(_T0):
        mgr = _manager()
        _engage(mgr)
        ctx = MagicMock()
        ctx.config_options = {
            CONF_CLOUD_ESCALATION_DELAY: {"hours": 2, "minutes": 0, "seconds": 0}
        }
        ctx.cloud_suppression_phase = mgr.phase
        ctx.cloud_escalation_deadline = mgr.escalation_deadline

        block = DiagnosticsBuilder.build_cloud_escalation_block(ctx)

    assert block["cloud_escalation_delay"] == {
        "hours": 2,
        "minutes": 0,
        "seconds": 0,
    }
    assert block["cloud_suppression_phase"] == CloudSuppressionPhase.HOLDING
    assert block["cloud_escalation_deadline"] == "2026-06-15T14:00:00+00:00"


def test_the_building_overview_compares_the_escalation_delay() -> None:
    """Two covers that differ only in the delay must read as a difference."""
    from custom_components.adaptive_cover_pro.building_overview import (
        _COMPARISON_SPECS,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_CLOUD_ESCALATION_DELAY,
        CONF_CLOUD_SUPPRESSION,
        CONF_CLOUDY_POSITION,
    )

    spec = next(s for s in _COMPARISON_SPECS if s.label == "Cloud suppression")
    base = {CONF_CLOUD_SUPPRESSION: True, CONF_CLOUDY_POSITION: 30}
    without = MagicMock()
    without.options = base
    with_delay = MagicMock()
    with_delay.options = {
        **base,
        CONF_CLOUD_ESCALATION_DELAY: {"hours": 2, "minutes": 0, "seconds": 0},
    }

    assert spec.extract(without) != spec.extract(with_delay)
    assert "2 h" in spec.extract(with_delay)
