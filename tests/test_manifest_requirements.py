"""Tests for manifest.json requirements.

Hassfest rejects any manifest requirement that is already a dependency of Home
Assistant core ("must not be listed in the manifest of a custom integration").
The core set is derived from the installed `homeassistant` package metadata so
no copy of the list is kept here.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

pytestmark = pytest.mark.unit

MANIFEST_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "manifest.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest_requirement_names() -> set[str]:
    """Return canonical names of the requirements declared in manifest.json."""
    with MANIFEST_PATH.open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    return {canonicalize_name(Requirement(r).name) for r in manifest["requirements"]}


def _ha_core_dependency_names() -> set[str]:
    """Return canonical names of Home Assistant's own (non-extra) dependencies."""
    names: set[str] = set()
    for raw in importlib.metadata.requires("homeassistant") or []:
        req = Requirement(raw)
        if req.marker is not None and "extra" in str(req.marker):
            continue
        names.add(canonicalize_name(req.name))
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_manifest_requirements_not_ha_core_dependencies() -> None:
    """Manifest must not list packages Home Assistant core already depends on."""
    core = _ha_core_dependency_names()
    # Guard against a vacuous pass if the metadata lookup returns nothing.
    assert core, "No Home Assistant core dependencies found in package metadata"
    assert "astral" in core, "Expected astral among Home Assistant core dependencies"

    offenders = sorted(_manifest_requirement_names() & core)
    assert not offenders, (
        f"manifest.json requirements {offenders} are dependencies of Home "
        "Assistant itself; hassfest rejects them"
    )


def test_manifest_still_declares_pandas() -> None:
    """Pandas is the real third-party dependency and must stay declared."""
    assert "pandas" in _manifest_requirement_names()
