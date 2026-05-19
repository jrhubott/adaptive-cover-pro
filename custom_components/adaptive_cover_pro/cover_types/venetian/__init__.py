"""Venetian cover-type package — policy + dual-axis sequencer.

Re-exports the public surface so existing imports
(``from cover_types.venetian import VenetianPolicy``) continue to work
after the venetian.py → venetian/ package conversion. The legacy
``managers.dual_axis_sequencer`` import path has been retired —
import ``DualAxisSequencer`` from this package instead.
"""

from __future__ import annotations

from .policy import GEOMETRY_VENETIAN_SCHEMA, VenetianPolicy
from .sequencer import DualAxisSequencer

__all__ = [
    "DualAxisSequencer",
    "GEOMETRY_VENETIAN_SCHEMA",
    "VenetianPolicy",
]
