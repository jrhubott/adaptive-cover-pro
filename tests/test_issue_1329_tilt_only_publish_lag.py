"""Regression tests for issue #1329 — tilt-only publish-lag suppression gap.

A venetian **tilt-only** command (``update_tilt_only``, the dispatch path used
whenever the position axis is delta-gated but the tilt axis keeps solar
tracking — the normal steady state on a slow-changing sun day) opens **no**
back-rotate suppression window at all: ``update_tilt_only`` deliberately never
calls ``stamp_position_command`` (issue #33 follow-on — that stamp is shared
with the position axis and pops ``_settled_at``). Once the 5s command-grace
tail expires, an ordinary tilt-only send is completely unguarded against the
actuator's own late re-publish of the tilt it just settled at, and that late
publish trips a false ``manual_override_set``.

Reproduces the reporter's diagnostics timeline: ``update_tilt_only`` dispatches
tilt=85, the actuator republishes tilt=81 (delta 4%) 16s later — past the 5s
grace, well inside the user's configured 90s ``venetian_backrotate_publish_lag``
— and (pre-fix) nothing suppresses it because the position-axis suppression
window (``is_in_suppression``) was never opened by a tilt-only send.

Wired through ``VenetianPolicy.secondary_axis_check`` exactly as production
composes it — this test builds a real ``DualAxisSequencer`` via
``VenetianPolicy.attach`` (mirroring the wiring helper in
``tests/test_manual_override_venetian.py``) but, deliberately, never seeds a
position command: issue #1329 is exclusively about the tilt-only path, which
never stamps the position-axis window in the first place.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError

import pytest

from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
    VenetianPolicy,
)

# Zero the real-motor sleep delays — update_tilt_only drives the real
# sequencer's post-tilt verify/rebase waits, which otherwise add real idle
# time to this suite.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def _make_attached_policy(
    *,
    grace_expired: bool = True,
    backrotate_publish_lag_seconds: float = 90,
    async_call: AsyncMock | None = None,
) -> tuple[VenetianPolicy, MagicMock]:
    """Build a ``VenetianPolicy`` wired to a real ``DualAxisSequencer``.

    Mirrors ``tests/test_manual_override_venetian.py``'s
    ``_make_sequencer_suppression`` wiring, but goes through
    ``VenetianPolicy.attach`` (so ``is_in_tilt_suppression`` runs exactly as
    production composes it) and deliberately seeds NO position command — no
    ``stamp_position_command`` call anywhere. ``grace_expired=True`` (the
    default) models the state every routine tilt-only send is dispatched
    into: ``maybe_update_tilt_only`` only ever fires once the suppression
    window is already closed (issue #756), so by the time a tilt-only send's
    own publish lands, the shared 5s command grace has normally elapsed too.
    """
    hass = MagicMock()
    hass.services.async_call = async_call if async_call is not None else AsyncMock()
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = MagicMock(return_value=not grace_expired)
    policy = VenetianPolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=grace_mgr,
        get_current_position=lambda _eid: 19,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "stopped",
        backrotate_publish_lag_seconds=backrotate_publish_lag_seconds,
    )
    return policy, hass


async def test_tilt_only_publish_after_grace_within_verify_tolerance_is_suppressed() -> (
    None
):
    """A tilt-only send's own late publish must be absorbed within the publish-lag window.

    ``update_tilt_only(tilt_target=85)`` dispatches with no position command
    in flight. The actuator republishes ``current_tilt_position=81`` (delta
    4%, exactly the reported issue #1329 delta) 16s later — past the 5s
    command grace, inside the user's 90s ``venetian_backrotate_publish_lag``
    — and within ``VENETIAN_TILT_VERIFY_TOLERANCE`` (5) of the commanded
    value, the same band ``_verify_and_record_tilt`` already treats as
    "arrived". That publish must be suppressed as ACP's own tilt settling,
    not read as a manual override.
    """
    entity_id = "cover.venetian_tilt_only"
    policy, hass = _make_attached_policy()

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )
    assert hass.services.async_call.call_count == 1

    # Advance the clock to T+16s past the tilt dispatch — past the 5s grace
    # (mocked expired via grace_mgr above), still inside the 90s publish-lag
    # window.
    policy._sequencer._tilt_sent_at[entity_id] -= dt.timedelta(seconds=16)

    # Built via the real production wiring (not hand-rolled) so this test
    # breaks if VenetianPolicy.secondary_axis_check is mis-wired.
    check = policy.secondary_axis_check(
        SimpleNamespace(tilt=85), None, entity_id=entity_id
    )
    new_state = SimpleNamespace(attributes={"current_tilt_position": 81})

    result = check.evaluate(entity_id, new_state, manual_threshold=3)

    assert result.is_manual is False
    # NOT consumed (issue #930 finding #2): a tilt-only send never touches
    # the position axis, so unlike the position-anchored suppression window
    # this rejection must not blind the position axis' own independent
    # manual-override check for the cycle.
    assert result.consumed is False
    assert result.event_name == "manual_override_rejected_tilt_suppression"


async def test_tilt_only_dispatch_leaves_position_suppression_closed() -> None:
    """Shape lock: the fix must never call ``stamp_position_command`` from the tilt path.

    That stamp is shared with the position axis via ``primary_axis_suppression``
    and pops ``_settled_at`` (issue #33 follow-on) — a tilt-only send that
    reached for it would blind the position axis' own manual-override
    detection for the full suppression window. This must hold both before and
    after the fix.
    """
    entity_id = "cover.venetian_tilt_only_shape_lock"
    policy, _hass = _make_attached_policy()

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert policy._sequencer.is_in_suppression(entity_id) is False


async def test_failed_tilt_send_opens_no_publish_lag_window() -> None:
    """A failed ``set_cover_tilt_position`` call must not open the publish-lag window.

    Matches the #927 precedent that a dispatch-anchored stamp records only
    once the move really went out: ``_tilt_sent_at`` must be written AFTER a
    successful ``services.async_call``, not before it. Otherwise a
    ``HomeAssistantError`` would still leave a stale stamp behind, opening a
    publish-lag window for a tilt command that was never actually sent.
    """
    entity_id = "cover.venetian_tilt_only_failed_send"
    policy, _hass = _make_attached_policy(
        async_call=AsyncMock(side_effect=HomeAssistantError("boom"))
    )

    await policy._sequencer.update_tilt_only(
        entity_id, tilt_target=85, current_position=19, reason="solar_tracking"
    )

    assert policy._sequencer.is_in_tilt_publish_lag(entity_id, delta=0.0) is False
