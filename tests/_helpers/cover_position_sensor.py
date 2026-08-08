"""A stub sensor for driving ``sensor._cover_position_attrs`` in unit tests.

Three test modules had grown their own near-identical version of this builder,
each wiring the same handful of coordinator members that ``_cover_position_attrs``
touches on its way to the one branch that module cared about. Per
CODING_GUIDELINES § cross-file test helpers, they share this one instead.

Every knob defaults to the inert value — no diagnostics, no snapshot positions,
no transit or travel state, no lift travel (which short-circuits
``_compute_distance_attrs`` to ``None``) — so a caller sets only what its branch
needs and the rest stays out of the way.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy


def inverted_for(options: dict, *, cover_type: str = "cover_blind") -> bool:
    """Evaluate the real ``position_axis_inverted`` property against *options*.

    The effective inversion is derived from the entry options through the
    policy's position axis (#1028), so a test that needs it must ask the
    production property rather than hardcoding a bool.
    """
    coord = SimpleNamespace(
        _policy=get_policy(cover_type),
        config_entry=SimpleNamespace(options=options),
    )
    return AdaptiveDataUpdateCoordinator.position_axis_inverted.fget(coord)


def make_cover_position_sensor(
    *,
    states: dict[str, Any] | None = None,
    options: dict | None = None,
    diagnostics: dict | None = None,
    positions: dict[str, int | None] | None = None,
    pipeline_result: Any = None,
    lift_travel_metres: float | None = None,
    cover_type: str = "cover_blind",
    position_tolerance: int = 2,
) -> MagicMock:
    """Build a stub ``_ACPSensor`` wired for ``_cover_position_attrs``.

    ``pipeline_result`` is passed through as-is — ``None`` (the default) means
    "no cycle has run yet". Pass a ``SimpleNamespace``/``PipelineResult`` to
    exercise the branches that read one; note that a bare ``MagicMock`` makes
    every flag on it truthy, which silently sends ``_target_position`` down the
    clamped branch.
    """
    options = options if options is not None else {}
    s = MagicMock()
    s.data.attributes = {}
    s.data.states = (
        states
        if states is not None
        else {"control": "solar", "state": 70, "held_position": None}
    )
    s.coordinator._pipeline_result = pipeline_result
    s.coordinator.data.diagnostics = diagnostics
    s.coordinator._cmd_svc.transit_states.return_value = {}
    s.coordinator._cmd_svc.travel_plans.return_value = {}
    s.coordinator._cmd_svc._position_tolerance = position_tolerance
    s.coordinator.config_entry.options = options
    s.coordinator._snapshot = (
        SimpleNamespace(cover_positions=positions) if positions is not None else None
    )
    s.coordinator._policy.lift_travel_metres.return_value = lift_travel_metres
    s.coordinator.position_axis_inverted = inverted_for(options, cover_type=cover_type)
    return s
