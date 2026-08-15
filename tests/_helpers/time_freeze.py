"""Freeze ``helpers.dt.datetime.now()`` for deterministic sunset/window tests.

``compute_effective_default`` (and everything built on it — the sunset-window
edge detector, the end-of-window handoff) reads wall-clock time via
``helpers.dt.datetime.now()``. Tests that need a fixed instant patch that one
call site. Per CODING_GUIDELINES § No Duplication ("cross-file test helpers
live in ``tests/_helpers/``"), this lives here rather than being copied
verbatim per test module — it previously existed identically in both
``tests/test_end_of_window_position.py`` and
``tests/test_issue_266_sunset_transition.py`` (issue #1287 audit).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch


def freeze_helpers_now(naive_utc: dt.datetime):
    """Patch ``helpers.dt.datetime.now()`` so it returns a UTC-aware ``naive_utc``."""
    aware = naive_utc.replace(tzinfo=dt.UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware},
    )
