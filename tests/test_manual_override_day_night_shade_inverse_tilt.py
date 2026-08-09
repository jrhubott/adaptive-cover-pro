"""Day/night-shade Model A inverse-tilt manual-override tests (issue #1227).

Mirrors ``tests/test_manual_override_venetian.py``'s inverse-tilt coverage.
Model A (``position_tilt``, the default control model) drives its blend
(tilt) axis through the SAME ``DualAxisSequencer`` venetian reuses by
composition — the same ``_tilt_targets`` store, the same ``_to_wire``
symmetric involution. It therefore inherits the identical wire/logical frame
mismatch the root cause describes: the sequencer dispatches the LOGICAL blend
but the actuator's ``current_tilt_position`` publishes the WIRE value.

``DayNightShadePolicy.secondary_axis_check`` (``cover_types/day_night_shade/
policy.py``) built its ``SecondaryAxisCheck`` without wiring the
normalisation the venetian policy gained — PR #1227's own review flagged this
as the unaddressed sibling gap (day/night shade is reachable via
``import_config``'s wholesale public-key merge even though ``inverse_tilt``
is not in its config-flow schema; see PR #1038's precedent for fixing it in
the same pass).
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    SecondaryAxisCheck,
)

# The venetian sequencer (reused by composition) otherwise waits on real-motor
# post-tilt delays.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def _make_event(entity_id: str, *, position: int | None, tilt: int | None):
    """Build a fake StateChangedData reporting both axes."""
    attrs: dict = {}
    if position is not None:
        attrs["current_position"] = position
    if tilt is not None:
        attrs["current_tilt_position"] = tilt
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = attrs
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    return event


def _make_manager(entity_id: str) -> AdaptiveCoverManager:
    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    mgr.add_covers([entity_id])
    return mgr


def _make_inverse_dns_policy() -> DayNightShadePolicy:
    """Build a Model A DayNightShadePolicy over an ``inverse_tilt=True`` sequencer.

    Model A is the default control model (``DEFAULT_DAY_NIGHT_CONTROL_MODEL``
    == ``DAY_NIGHT_MODEL_POSITION_TILT``), so no explicit ``configure()`` call
    is needed to drive the dual-axis blend. Mirrors
    ``test_manual_override_venetian.py``'s ``_make_inverse_policy`` builder —
    same sequencer, same ``invert_tilt=lambda: True`` — swapped onto the
    day/night-shade policy so the two files exercise identical wiring on the
    two policies that share the sequencer.
    """
    policy = DayNightShadePolicy()
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = lambda _eid: False
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=grace_mgr,
        get_current_position=lambda _eid: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: "open",
        invert_tilt=lambda: True,
    )
    assert policy._drives_dual_axis(), "default DNS control model must be Model A"
    return policy


def _secondary_check(policy: DayNightShadePolicy, expected: int) -> SecondaryAxisCheck:
    """Build the blend ``SecondaryAxisCheck`` via the production policy path."""
    return policy.secondary_axis_check(SimpleNamespace(tilt=expected), None)


def test_dns_inverse_tilt_wire_publish_of_verified_target_is_not_manual_override() -> (
    None
):
    """An inverse_tilt actuator publishing the WIRE blend of the verified
    target must NOT trip. ACP dispatched logical 96 (wire 4); the actuator
    publishes ``current_tilt_position=4``. Without normalisation ``evaluate``
    compares logical 96 against raw 4 -> delta 92 -> false
    ``manual_override_set``. Mirrors
    ``test_inverse_tilt_wire_publish_of_verified_target_is_not_manual_override``
    in ``test_manual_override_venetian.py``.
    """
    entity_id = "cover.day_night_shade_kueche"
    policy = _make_inverse_dns_policy()
    check = _secondary_check(policy, 96)  # expected = logical 96

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=2, tilt=4),  # wire 4 == to_wire(96)
        our_state=2,
        policy=get_policy("cover_day_night_shade"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=10,
        secondary_axis_check=check,
    )

    assert not mgr.is_cover_manual(entity_id), (
        "DNS Model A wire publish of the verified logical blend target must NOT "
        "trip manual override"
    )


def test_dns_inverse_tilt_genuine_off_target_move_still_trips() -> None:
    """The inversion fix must not mask a real move: ACP dispatched logical 96
    (wire 4); the user twists the shade to logical 30 -> the actuator publishes
    wire 70. Normalised back to logical 30, delta |96-30|=66 >= threshold ->
    trips. Mirrors ``test_inverse_tilt_genuine_off_target_move_still_trips``.

    This is an over-suppression guard, not a repro of the #1227 bug: the delta
    here is large enough that it trips whether or not the wire->logical
    normalisation runs at all (with the fix removed, delta |96-70|=26 also
    clears the threshold). It proves the fix doesn't introduce a false
    negative on a genuine move -- it does NOT by itself prove the
    normalisation is correct; see
    ``test_dns_inverse_tilt_wire_publish_of_verified_target_is_not_manual_override``
    for that.
    """
    entity_id = "cover.day_night_shade_kueche_user"
    policy = _make_inverse_dns_policy()
    check = _secondary_check(policy, 96)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(
            entity_id, position=2, tilt=70
        ),  # wire 70 -> logical 30
        our_state=2,
        policy=get_policy("cover_day_night_shade"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=10,
        secondary_axis_check=check,
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "a genuine off-target blend move must still trip even with inversion normalised"


def test_dns_inverse_tilt_normalises_the_dispatched_anchor() -> None:
    """The PRODUCTION anchor path (real ``entity_id``, ``last_tilt_target``
    populated) must normalise identically to the ``entity_id=None`` fallback
    the other DNS inverse-tilt tests exercise. Mirrors
    ``test_inverse_tilt_normalises_the_dispatched_anchor`` in
    ``test_manual_override_venetian.py`` — Model A drives its blend axis
    through the SAME ``DualAxisSequencer`` (and the same ``_tilt_targets``
    store) venetian uses, so it owes the identical anchor-path normalisation
    (issue #1227 review finding #6: this file only exercised the
    ``entity_id=None`` fallback, never the ``last_tilt_target`` anchor Model A
    installs actually take).

    Seeds ``_tilt_targets`` directly so ``resolve_dispatched_secondary_expected``
    takes the ``sequencer.last_tilt_target(entity_id)`` branch real installs
    use, rather than the ``result.tilt`` fallback. ACP dispatched logical 96
    (wire 4); the actuator publishes the verified wire value, which must NOT
    trip.
    """
    entity_id = "cover.day_night_shade_anchor"
    policy = _make_inverse_dns_policy()
    policy.sequencer._tilt_targets[entity_id] = 96  # seed the dispatched anchor
    # A stale/different reevaluated result.tilt proves the anchor — not the
    # fallback — is what the check actually uses.
    check = policy.secondary_axis_check(SimpleNamespace(tilt=20), None, entity_id)
    assert check is not None
    assert check.expected == 96, (
        "expected must come from the dispatched anchor (96), not the "
        f"reevaluated result.tilt fallback (20); got {check.expected}"
    )

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=2, tilt=4),  # wire 4 == to_wire(96)
        our_state=2,
        policy=get_policy("cover_day_night_shade"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=10,
        secondary_axis_check=check,
    )

    assert not mgr.is_cover_manual(entity_id), (
        "wire publish of the verified logical blend target via the production "
        "anchor path must NOT trip manual override"
    )
