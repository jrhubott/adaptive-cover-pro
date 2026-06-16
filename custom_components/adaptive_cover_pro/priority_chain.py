"""Shared override-pipeline decision chain.

The override pipeline evaluates handlers in priority order (highest first). This
module is the single source of truth for that ordered chain as presented to the
user: it imports each handler's declared ``priority`` class attribute — never a
hardcoded integer — and returns the entries sorted highest-priority-first.

Two consumers render this chain:
  * ``config_flow._build_config_summary`` — the ``✅Weather → ✅Manual → …`` line.
  * the custom-slot priority-scale visual — shows where a slot's 1–100 priority
    lands against the fixed handler anchors.

Both call :func:`build_priority_chain`; each renders the returned entries in its
own format. The ordering and the priority integers live here only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .const import CUSTOM_POSITION_SAFETY_PRIORITY
from .pipeline.handlers import (
    ClimateHandler,
    CloudSuppressionHandler,
    DefaultHandler,
    GlareZoneHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
    WeatherOverrideHandler,
)

# Re-exported so callers can label a custom slot sitting at the safety priority.
SAFETY_PRIORITY = CUSTOM_POSITION_SAFETY_PRIORITY


@dataclass(frozen=True)
class PriorityChainEntry:
    """One row of the decision chain.

    ``priority`` — evaluation priority (higher wins). ``label`` — short display
    name (e.g. ``"Weather"`` or ``"Custom#1(77)"``). ``active`` — whether the
    handler is configured/enabled. ``slot`` — custom-slot number for a custom
    entry, ``None`` for a fixed anchor.
    """

    priority: int
    label: str
    active: bool
    slot: int | None = None


def build_priority_chain(
    *,
    has_weather: bool,
    has_motion: bool,
    has_cloud: bool,
    has_climate: bool,
    sun_tracking_enabled: bool,
    has_glare: bool,
    supports_glare: bool,
    custom_slots: Iterable[Sequence] = (),
) -> list[PriorityChainEntry]:
    """Return the decision chain ordered highest-priority-first.

    Fixed anchors take their priority from the handler classes. ``custom_slots``
    is an iterable of the summary's slot tuples
    ``(slot, trigger, position, priority, use_my, tilt, tilt_only)``; each is
    interleaved at its configured priority. The sort is stable, so a custom slot
    sharing a fixed handler's priority renders after that handler (fixed anchors
    are inserted first).
    """
    entries: list[PriorityChainEntry] = [
        PriorityChainEntry(WeatherOverrideHandler.priority, "Weather", has_weather),
        PriorityChainEntry(ManualOverrideHandler.priority, "Manual", True),
        PriorityChainEntry(MotionTimeoutHandler.priority, "Motion", has_motion),
        PriorityChainEntry(CloudSuppressionHandler.priority, "Cloud", has_cloud),
        PriorityChainEntry(ClimateHandler.priority, "Climate", has_climate),
        PriorityChainEntry(SolarHandler.priority, "Solar", sun_tracking_enabled),
        PriorityChainEntry(DefaultHandler.priority, "Default", True),
    ]
    if supports_glare:
        entries.append(
            PriorityChainEntry(GlareZoneHandler.priority, "Glare", has_glare)
        )
    for slot_tuple in custom_slots:
        slot = slot_tuple[0]
        priority = slot_tuple[3]
        entries.append(
            PriorityChainEntry(priority, f"Custom#{slot}({priority})", True, slot=slot)
        )
    # Stable sort highest-priority-first; ties keep insertion order (fixed
    # anchors before custom slots).
    entries.sort(key=lambda e: e.priority, reverse=True)
    return entries
