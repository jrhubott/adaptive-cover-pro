"""Offline-triage adapter: mapping a downloaded diagnostics JSON to a view.

``build_offline_view`` is the pure seam that lets ``scripts/triage_json.py`` and
the ``acp-diagnose`` skill run the SAME ``run_triage`` engine against a file a
user downloaded from HA — closing the loop so a gap in the in-product step is a
gap in offline triage too. It unwraps HA's ``{"data": {...}}`` diagnostics
envelope, folds ``config_options`` + the ``diagnostics`` payload into one view,
and documents the offline seam: ``capabilities`` / ``axis_requirements`` are NOT
in the download, so rules 8a and 13 cannot fire offline.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import TriageCode
from custom_components.adaptive_cover_pro.diagnostics.triage import (
    build_offline_view,
    run_triage,
)

pytestmark = pytest.mark.unit


def _download(**diagnostics) -> dict:
    """Return a minimal diagnostics-download doc (diagnostics/__init__.py shape)."""
    return {
        "title": "Kitchen Blind",
        "config_data": {"name": "Kitchen Blind"},
        "config_options": {"max_elevation": 20},
        "diagnostics": dict(diagnostics),
    }


def test_build_offline_view_folds_options_and_diagnostics() -> None:
    doc = _download(control_status="automatic_control_off")
    view = build_offline_view(doc)
    assert view["options"] == {"max_elevation": 20}
    assert view["control_status"] == "automatic_control_off"


def test_build_offline_view_unwraps_data_envelope() -> None:
    doc = {"data": _download(control_status="active")}
    view = build_offline_view(doc)
    assert view["options"] == {"max_elevation": 20}
    assert view["control_status"] == "active"


def test_build_offline_view_missing_keys_yield_empty_no_raise() -> None:
    view = build_offline_view({})
    assert view["options"] == {}
    # run_triage tolerates the near-empty view without raising.
    assert run_triage(view) == []


def test_build_offline_view_injects_latest_version_when_given() -> None:
    doc = _download()
    view = build_offline_view(doc, latest_version="2026.8.0")
    assert view["latest_version"] == "2026.8.0"


def test_build_offline_view_omits_latest_version_by_default() -> None:
    assert "latest_version" not in build_offline_view(_download())


def test_offline_view_has_no_capabilities_or_axis_requirements() -> None:
    # The offline seam: the download carries neither capabilities (rule 8a) nor
    # policy-derived axis_requirements (rule 13), so those config rules cannot
    # fire offline — only their runtime counterparts (8b) do.
    view = build_offline_view(_download())
    assert "capabilities" not in view
    assert "axis_requirements" not in view


def test_offline_view_feeds_run_triage_and_config_rule_fires() -> None:
    # max_elevation 20 → TRACKING_WINDOW_TRUNCATED fires through the offline view.
    view = build_offline_view(_download())
    codes = {f.reason.code for f in run_triage(view)}
    assert TriageCode.TRACKING_WINDOW_TRUNCATED in codes


def test_offline_stale_version_fires_only_with_latest_version() -> None:
    doc = _download()
    doc["diagnostics"]["meta"] = {"integration_version": "2026.7.0"}
    without = {f.reason.code for f in run_triage(build_offline_view(doc))}
    assert TriageCode.STALE_VERSION not in without
    with_latest = {
        f.reason.code
        for f in run_triage(build_offline_view(doc, latest_version="2026.9.0"))
    }
    assert TriageCode.STALE_VERSION in with_latest
