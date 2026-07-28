"""Layered dual-panel (sheer front + blackout back) calculation.

A dual-panel cover drives two SEPARATE full-height vertical shades on one
window, layered front-to-back:

- the **front** is a sheer / light-filtering shade that tracks the sun exactly
  like a normal vertical blind — it filters glare while admitting diffuse
  light — so its target is simply the adaptive position the pipeline already
  computed;
- the **back** is a blackout shade that stays retracted (open) most of the
  time and only deploys (closes) when conditions call for a full block — a
  hot-/cold-day climate strategy or the astronomical sunset / privacy window.

Unlike venetian (two axes on one HA entity, tightly sequenced) the two panels
are independent entities with no coupling, so this module only *decides* each
panel's target; the per-entity command machinery dispatches them separately.

The functions here are pure and take primitives only (no HA, no const enums),
so they unit-test in isolation. The owning ``DualPanelPolicy`` maps the
``PipelineResult`` onto the ``active_triggers`` set and the canonical
open/closed values.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LayeredResult:
    """Per-panel targets for a dual-panel (front/back) cover."""

    front: int  # 0–100 position for the sheer/front layer
    back: int  # 0–100 position for the blackout/back layer


def blackout_should_deploy(
    active_triggers: Iterable[str],
    configured_triggers: Iterable[str],
) -> bool:
    """Return whether the blackout back should deploy (close) this cycle.

    Deploys when ANY configured trigger is currently active. ``active_triggers``
    is the set of trigger keys the policy derived from the pipeline result
    (e.g. a climate full-block strategy, or the sunset window);
    ``configured_triggers`` is the user-selected subset that should drive the
    blackout. An empty ``configured_triggers`` means the back never deploys.
    """
    active = set(active_triggers)
    return any(trigger in active for trigger in configured_triggers)


def compute_layered(
    *,
    front: int,
    deploy_blackout: bool,
    open_position: int,
    closed_position: int,
) -> LayeredResult:
    """Compose the two panel targets.

    ``front`` is the already-resolved sheer position (the adaptive sun-tracking
    value). The back is ``closed_position`` when the blackout deploys, else
    ``open_position``. Callers pass the CANONICAL open/closed endpoints
    (``POSITION_OPEN`` / ``POSITION_CLOSED``); this function only *selects*
    between them and applies no cover-direction semantics. The owning policy
    maps the chosen endpoint into the device's wire space afterward —
    interpolation through the calibration curve, or inverse-state — so no
    wire-space transform is baked in here.
    """
    back = closed_position if deploy_blackout else open_position
    return LayeredResult(front=front, back=back)
