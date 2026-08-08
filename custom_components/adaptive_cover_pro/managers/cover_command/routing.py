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
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
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
from .gates import filter_endpoint_specials


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

    Inverse-state ordering note (see ``CODING_GUIDELINES.md``):
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
    supports_position = caps_get(caps, axis.capability_key, default=True)

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


def is_my_preset_target(
    caps: dict[str, bool], axis: CoverAxis, target: int | None
) -> bool:
    """Whether a booked ``target`` can only have come from a My-preset dispatch.

    :func:`route_service_call` is invertible on the axis that matters. Where
    ``caps_get(caps, axis.capability_key)`` is False, exactly three branches are
    reachable: the My branch (``routed_target = state``, requires
    ``use_my_position``), the open/close threshold branch (``routed_target`` is
    0 or 100), and the no-capable-service branch — which never books at all,
    because ``_prepare_service_call`` returns before its state-mutation block.
    The endpoint-substitution branch requires ``supports_position`` and is
    unreachable there. So a booked NON-endpoint target on a
    non-position-capable axis can only have been put there by a My dispatch.

    That covers ``_prepare_service_call``. A SECOND site books
    ``plan.routed_target`` — ``apply_position``'s ``same_position`` skip (issue
    #1158) — and it has no ``service is None`` check of its own, so the
    inference has to survive it too. It does, because of the gate's arms rather
    than any explicit guard:

    * Arms 1 and 2 both require a GENUINE ``_current``, and on a
      non-position-capable axis ``read_axis_value`` falls through to
      ``get_open_close_state``, which returns only 0, 100 or ``None``. Arm 1
      books ``routed_target == position == _current`` and arm 2's own gate is
      ``_current == routed_target``, so either way the booked number is one of
      those endpoints. Arm 1's endpoint-tolerance sub-arm is the single case
      where ``routed_target`` may diverge from ``_current``, and it books
      nothing at all.
    * Arm 3 fires only when ``_current`` is ``None`` or a synthetic open/close
      mapping, and its gate IS ``last_target == plan.routed_target``
      (``_same_position_via_target_fallback``). By the time it books, the
      booking's own value-change guard is already False, so the ``set_target``
      is a no-op — it can never introduce a number that was not already there.

    A future change that let the skip branch book a ``routed_target`` the gate
    did not compare against — a new arm, or dropping ``_current_is_genuine`` —
    would break that half; the ``same_position``-booking tests in
    ``tests/test_managers/test_cover_command.py`` are what catch it. The
    routing-algebra half is pinned in the same file by
    ``test_route_service_call_blind_axis_obeys_the_my_preset_trichotomy``
    (every blind-axis capability shape) and, for the unbookability of the
    no-capable-service branch specifically,
    ``test_prepare_service_call_books_nothing_when_no_capable_service``.

    Two consequences hang off all of that, which is why the predicate is shared
    rather than spelled out twice:

    * A reconciliation resend of such a target must re-route through
      ``stop_cover`` (issue #1134). Left to ``use_my_position=False`` the resend
      falls to the threshold branch and sends ``close_cover``/``open_cover``,
      physically slamming the cover to an endpoint AND rebooking the user's My
      percent as 0 or 100.
    * Such a target can never be verified against a reported position (issue
      #990). A cover that cannot report the axis it was commanded on surfaces
      only endpoints, so a My percent never registers as reached and
      ``is_target_unreached`` must stay quiet rather than nag forever.

    Derived from CURRENT capabilities at the moment it is asked, so it needs no
    stored flag and cannot go stale — unlike a remembered ``use_my_position``,
    whose forget-value would silently mis-route.

    At the two ambiguous values (a My preset configured to exactly 0 or 100)
    this answers "not My", and the resulting ``close_cover``/``open_cover``
    reaches the identical routed target — physically equivalent, no drift.

    Args:
        caps: Capabilities for the entity, read now (not at booking time).
        axis: The policy-selected default axis for this cover.
        target: The booked target, or ``None`` when nothing is booked.

    Returns:
        True when ``target`` is a My preset on this cover.

    """
    if target is None:
        return False
    if caps_get(caps, axis.capability_key, default=True):
        return False
    return target not in (POSITION_CLOSED, POSITION_OPEN)


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

    Active position limits (issue #474): when ``CONF_MIN_POSITION`` /
    ``CONF_MAX_POSITION`` is set AND the corresponding enable flag is
    ``False`` (always-enforced, not sun-tracking-only), the limit value is
    added to the special set.  This lets a target pinned to the configured
    floor or ceiling bypass the delta gate and snap to the limit — the same
    guarantee that endpoints (0/100) and default_height already have.
    Conservative v1 restricts the bypass to always-enforced limits because
    ``build_special_positions`` does not receive sun_valid context; the
    sun-tracking-only case (enable flag = True) is handled by the ordinary
    delta comparison once the limit is actually active.

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

    # Issue #474: always-enforced position limits bypass the delta gate.
    min_position = options.get(CONF_MIN_POSITION)
    if min_position is not None and options.get(CONF_ENABLE_MIN_POSITION) is False:
        special_positions.append(min_position)

    max_position = options.get(CONF_MAX_POSITION)
    if max_position is not None and options.get(CONF_ENABLE_MAX_POSITION) is False:
        special_positions.append(max_position)

    return filter_endpoint_specials(special_positions, enforce_endpoints)
