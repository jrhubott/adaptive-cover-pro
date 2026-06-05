"""Dual-section (split-panel) calculation — one fabric, top + bottom sections.

A split-panel cover is a single vertical shade split into a TOP section and a
BOTTOM section, driven as two SEPARATE HA cover entities. In the
"bottom blocks, top free" behaviour:

- the **bottom** section tracks the sun exactly like a normal vertical blind
  (it rises from the sill to block the direct-sun band), so its target is the
  adaptive position the pipeline already computed;
- the **top** section stays open (retracted at the head) for daylight and view.

Because the top section is always open, the two sections of the one shared
fabric never overlap (bottom coverage + 0 ≤ 100), so the one-fabric coupling
constraint is satisfied by construction — no clamp is needed for this mode.

The function here is pure and takes primitives only, so it unit-tests in
isolation; the owning ``SplitPanelPolicy`` supplies the dispatched-space
values (inverse-state already applied to the top-open constant).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DualSectionResult:
    """Per-section targets for a split-panel (top/bottom) cover."""

    top: int  # 0–100 position for the upper section
    bottom: int  # 0–100 position for the lower section


def bottom_blocks_top_free(*, bottom: int, top_open: int) -> DualSectionResult:
    """Compose the two section targets.

    ``bottom`` is the already-resolved sun-tracking position (the adaptive
    blind value); ``top_open`` is the value meaning "fully open" already mapped
    into the dispatched coordinate space (inverse-state applied by the caller).
    """
    return DualSectionResult(top=top_open, bottom=bottom)
