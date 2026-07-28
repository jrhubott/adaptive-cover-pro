"""Tests for the pure position-frame converters in ``position_utils``.

``inverse_state`` and ``flip_if`` are the canonical position-frame primitives
for the whole integration (issues #1036 / #1042). They used to live at the
bottom of ``managers/manual_override/manager.py`` — a module that imports
``homeassistant.core.HomeAssistant`` — which forced the pipeline layer to
import a manager module purely to reach two ``int -> int`` functions. This file
pins both the arithmetic contract and the purity of their new home so the
converters can't drift back across the HA boundary.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from custom_components.adaptive_cover_pro.position_utils import flip_if, inverse_state

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_POSITION_UTILS = (
    _REPO_ROOT / "custom_components" / "adaptive_cover_pro" / "position_utils.py"
)


@pytest.mark.unit
def test_flip_if_inverts_when_inverted() -> None:
    """``inverted=True`` applies the ``100 - x`` involution."""
    assert flip_if(30, inverted=True) == 70


@pytest.mark.unit
def test_flip_if_is_identity_when_not_inverted() -> None:
    """``inverted=False`` returns the value untouched — a no-op on normal installs."""
    assert flip_if(30, inverted=False) == 30


@pytest.mark.unit
def test_flip_if_is_its_own_inverse() -> None:
    """The involution property is what makes one primitive serve both directions.

    ``flip_if`` converts cover→logical (a raw entity read on its way into
    ``PipelineResult.position``) and logical→cover (a pipeline value on its way
    to a dispatched or held number) with the same call, because ``100 - x``
    round-trips. Any future "optimisation" that breaks this breaks every
    round-tripping caller (``day_night_shade`` remaps a position out and back
    inside one function).
    """
    for value in range(101):
        assert flip_if(flip_if(value, inverted=True), inverted=True) == value


@pytest.mark.unit
def test_inverse_state_is_importable_from_position_utils() -> None:
    """``inverse_state`` is the raw involution, published from the pure module.

    Several call sites apply it unconditionally or inside an imperative guard
    rather than the ternary ``flip_if`` replaces, so it stays a public function
    in its own right.
    """
    assert inverse_state(30) == 70


@pytest.mark.unit
def test_position_utils_has_no_direct_homeassistant_import() -> None:
    """``position_utils`` must stay free of direct ``homeassistant.*`` imports.

    Same purity bar CLAUDE.md applies to ``engine/covers/base.py`` — direct
    imports only, not the full transitive graph (``.const`` pulls HA in
    transitively for both modules). This is the property that lets the pipeline
    layer reach the frame converters without importing a manager (#1042).
    """
    tree = ast.parse(_POSITION_UTILS.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name == "homeassistant"
                or alias.name.startswith("homeassistant.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "homeassistant" or module.startswith("homeassistant."):
                offenders.append(f"line {node.lineno}: from {module} import ...")
    assert not offenders, (
        "position_utils.py must not import homeassistant directly — it is the "
        "pure home for position-value transforms:\n  " + "\n  ".join(offenders)
    )
