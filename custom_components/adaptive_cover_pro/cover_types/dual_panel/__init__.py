"""Dual-panel (sheer front + blackout back) cover-type package (#996).

Re-exports the public surface so ``from cover_types.dual_panel import
DualPanelPolicy`` resolves. The policy drives TWO independent HA shade entities
over one window: a sheer FRONT that sun-tracks like a plain vertical blind, and
a blackout BACK that deploys only on a configured trigger. It rides the shipped
``resolve_entity_target`` / ``post_pipeline_resolve`` seam — but, unlike the
day/night Model C shade, the two panels are NOT coupled (no ``M >= P`` no-pass
invariant).
"""

from __future__ import annotations

from .policy import GEOMETRY_DUAL_PANEL_SCHEMA, DualPanelPolicy

__all__ = [
    "GEOMETRY_DUAL_PANEL_SCHEMA",
    "DualPanelPolicy",
]
