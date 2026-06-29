"""Pure routing helpers for the cover command service.

This module owns the *no-side-effects* half of the cover_command surface:

- :class:`ServiceCallPlan` and :func:`route_service_call` — pick the HA
  service (``set_cover_position`` / ``set_cover_tilt_position`` /
  ``stop_cover`` / ``open_cover`` / ``close_cover``) for a given cover
  state and capability set, without mutating any per-entity bookkeeping.
- :func:`build_special_positions` — build the list of "always allowed"
  positions (0, 100, default_height, sunset_pos, my_position) that bypass
  the delta-threshold gate.

Keeping these out of the orchestrator class lets them be unit-tested as
pure functions, and lets the rest of the package depend on them without
pulling in :class:`CoverCommandService`.
"""

from __future__ import annotations

import dataclasses

from homeassistant.const import ATTR_ENTITY_ID

from ...const import (
    CONF_DEFAULT_HEIGHT,
    CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    CONF_MY_POSITION_VALUE,
    CONF_SUNSET_POS,
    DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
    DEFAULT_ENFORCE_DELTA_AT_ENDPOINTS,
    POSITION_CLOSED,
    POSITION_OPEN,
)
from ...cover_types.base import (
    AXIS_NAME_POSITION,
    CAP_HAS_CLOSE,
    CAP_HAS_OPEN,
    CAP_HAS_STOP,
    CoverAxis,
    caps_get,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceCallPlan:
    """Pure result of routing a cover state to an HA service call.

    Produced by :func:`route_service_call`. ``CoverCommandService`` consumes
    it to issue the actual service call and to update per-entity bookkeeping
    via ``PerEntityState``.

    Attributes:
        service: HA service name (``set_cover_position`` /
            ``set_cover_tilt_position`` / ``stop_cover`` / ``open_cover`` /
            ``close_cover``), or ``None`` when the cover lacks any capable
            service for this state.
        service_data: Kwargs to pass to ``hass.services.async_call``, or
            ``None`` when ``service`` is ``None``.
        supports_position: Whether the routed service accepts an explicit
            position (set_position / set_tilt_position).
        routed_target: Value the orchestrator must record as
            ``PerEntityState.target`` — equal to ``state`` for set_position
            and the My-stop branch, ``100`` for ``open_cover``, ``0`` for
            ``close_cover``.

    """

    service: str | None
    service_data: dict | None
    supports_position: bool
    routed_target: int


def route_service_call(
    entity: str,
    state: int,
    caps: dict[str, bool],
    *,
    axis: CoverAxis,
    use_my_position: bool,
    open_close_threshold: int,
    endpoint_use_open_close: bool = DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
) -> ServiceCallPlan:
    """Pick the HA service to issue for a cover/state, ignoring side effects.

    Pure helper extracted from ``CoverCommandService._prepare_service_call``.
    Routing precedence: position-capable axis → My-position stop → open/close
    threshold → no capable service.

    Inverse-state ordering note (``CODING_GUIDELINES.md`` line 221):
    ``state`` here is the value the caller wants the cover to land at, after
    any inverse-state transformation upstream. This helper does NOT touch
    inverse state.

    Args:
        entity: Cover entity ID (carried into ``service_data``).
        state: Already-inverted target position (0–100).
        caps: Pre-fetched capabilities dict for the entity.
        axis: The policy-selected default axis for this cover. The caller
            (``CoverCommandService``) computes this via
            ``policy.select_default_axis(caps)`` so the routing function
            stays free of cover-type imports.
        use_my_position: Send ``stop_cover`` to trigger the hardware "My"
            preset when the cover lacks ``set_cover_position`` but has
            ``stop_cover``.
        open_close_threshold: Position cutoff for the open/close fallback.
        endpoint_use_open_close: When True (issue #697), a position-capable
            cover commanded to the 100 endpoint is sent ``open_cover`` and the
            0 endpoint ``close_cover`` instead of ``set_cover_position``. Only
            applies to the position axis; falls back to ``set_cover_position``
            when the matching open/close capability is missing.

    Returns:
        :class:`ServiceCallPlan` describing what the orchestrator must do.

    """
    supports_position = caps.get(axis.capability_key, True)

    if (
        supports_position
        and endpoint_use_open_close
        and axis.name == AXIS_NAME_POSITION
    ):
        if state >= POSITION_OPEN and caps_get(caps, CAP_HAS_OPEN):
            return ServiceCallPlan(
                service="open_cover",
                service_data={ATTR_ENTITY_ID: entity},
                supports_position=False,
                routed_target=POSITION_OPEN,
            )
        if state <= POSITION_CLOSED and caps_get(caps, CAP_HAS_CLOSE):
            return ServiceCallPlan(
                service="close_cover",
                service_data={ATTR_ENTITY_ID: entity},
                supports_position=False,
                routed_target=POSITION_CLOSED,
            )
        # Endpoint requested but the matching open/close service is missing;
        # fall through to set_cover_position below.

    if supports_position:
        return ServiceCallPlan(
            service=axis.service,
            service_data={ATTR_ENTITY_ID: entity, axis.service_attr: state},
            supports_position=True,
            routed_target=state,
        )

    if use_my_position and caps_get(caps, CAP_HAS_STOP):
        return ServiceCallPlan(
            service="stop_cover",
            service_data={ATTR_ENTITY_ID: entity},
            supports_position=False,
            routed_target=state,
        )

    has_open = caps_get(caps, CAP_HAS_OPEN)
    has_close = caps_get(caps, CAP_HAS_CLOSE)
    if not has_open or not has_close:
        return ServiceCallPlan(
            service=None,
            service_data=None,
            supports_position=False,
            routed_target=state,
        )

    if state >= open_close_threshold:
        return ServiceCallPlan(
            service="open_cover",
            service_data={ATTR_ENTITY_ID: entity},
            supports_position=False,
            routed_target=100,
        )
    return ServiceCallPlan(
        service="close_cover",
        service_data={ATTR_ENTITY_ID: entity},
        supports_position=False,
        routed_target=0,
    )


def build_special_positions(options: dict) -> list[int]:
    """Build list of special positions from options.

    Special positions (0, 100, default_height, sunset_pos) bypass the
    *delta-threshold* check so covers are always allowed to transition
    TO or FROM these key values even when the position change is smaller
    than ``min_change``.  They do NOT bypass the same-position short-circuit in
    ``apply_position`` — if the cover is already at or within
    endpoint-tolerance of the target,
    no command is sent regardless of whether the target is special.

    When ``CONF_ENFORCE_DELTA_AT_ENDPOINTS`` is enabled (issue #679), the 0
    and 100 endpoints are omitted so the normal delta gate runs for those
    targets too. Default (off) preserves issue #629's always-send-to-0/100
    guarantee byte-for-byte. Useful on mechanically coupled covers where
    commanding a full endpoint disturbs the tilt axis.

    """
    enforce_endpoints = options.get(
        CONF_ENFORCE_DELTA_AT_ENDPOINTS, DEFAULT_ENFORCE_DELTA_AT_ENDPOINTS
    )
    special_positions = [] if enforce_endpoints else [0, 100]
    default_height = options.get(CONF_DEFAULT_HEIGHT)
    sunset_pos = options.get(CONF_SUNSET_POS)
    my_position_value = options.get(CONF_MY_POSITION_VALUE)
    if default_height is not None:
        special_positions.append(default_height)
    if sunset_pos is not None:
        special_positions.append(sunset_pos)
    if my_position_value is not None:
        special_positions.append(my_position_value)
    return special_positions
