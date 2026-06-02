"""Tests for the shared ``is_state_in_transit`` helper.

Issue #33: the cover-command service and the dual-axis sequencer used to
each carry their own ``state in ("opening", "closing")`` literal. The
state classifier carried five copies. With the publish-lag fix coming in
behind a new policy hook, both axes consult the same predicate — so the
in-transit literal needs to live in one place too. This file pins the
agreement between the new helper and the existing
``CoverCommandService._is_cover_in_transit`` method that other tests
already lock down indirectly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command.transit import (
    is_state_in_transit,
)


@pytest.mark.parametrize(
    "state",
    ["opening", "closing", "open", "closed", "unknown", "unavailable", None, ""],
)
def test_is_state_in_transit_helper_agrees_with_command_service(state) -> None:
    """The free helper and ``CoverCommandService._is_cover_in_transit`` agree everywhere.

    Drives both predicates against the same set of HA cover states. The
    helper is pure (string → bool); the service goes through ``hass.states``
    so we stub a state object. Both must return ``True`` for the two
    transitional states and ``False`` for every other input — including
    odd unknowns / empty strings / ``None``.
    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )

    eid = "cover.transit_probe"
    hass = MagicMock()
    if state is None:
        hass.states.get.return_value = None
    else:
        state_obj = MagicMock()
        state_obj.state = state
        hass.states.get.return_value = state_obj

    # We only need ``_is_cover_in_transit`` to be callable — bypass the full
    # __init__ by binding via ``object.__new__`` and stamping ``_hass``.
    svc = object.__new__(CoverCommandService)
    svc._hass = hass

    expected = state in ("opening", "closing")
    assert is_state_in_transit(state) is expected
    assert svc._is_cover_in_transit(eid) is expected
    assert is_state_in_transit(state) == svc._is_cover_in_transit(eid)
