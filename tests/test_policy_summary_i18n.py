"""Tests for policy-owned i18n of the config summary (follow-up to #258).

The cover-type label (``display_label``) and the physical-dimension /
geometry block (``summary_geometry_lines`` + the shared helper) were deferred
from #258 as policy-owned. This file covers the new machinery:

* the ``labels`` override param on ``display_label`` and
  ``summary_geometry_lines`` (and the shared ``window_dimensions_lines``),
* English back-compat when ``labels`` is ``None`` or a key is untranslated,
* a drift guard that ``en.json``'s ``config_summary.cover_types`` /
  ``config_summary.geometry`` subtrees are byte-identical to the code-owned
  ``COVER_TYPE_LABELS_EN`` / ``GEOMETRY_LABELS_EN`` dicts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
)
from custom_components.adaptive_cover_pro.cover_types._summary_labels import (
    COVER_TYPE_LABELS_EN,
    GEOMETRY_LABELS_EN,
)
from custom_components.adaptive_cover_pro.cover_types.awning import AwningPolicy
from custom_components.adaptive_cover_pro.cover_types.blind import BlindPolicy
from custom_components.adaptive_cover_pro.cover_types.oscillating_awning import (
    OscillatingAwningPolicy,
)
from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy
from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
    VenetianPolicy,
)

pytestmark = pytest.mark.unit

TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
)


# ---------------------------------------------------------------------------
# (a) display_label override + English back-compat
# ---------------------------------------------------------------------------


def test_display_label_override_and_default() -> None:
    """A labels override wins; ``labels=None`` keeps the English default."""
    assert BlindPolicy().display_label(labels={"cover_types.blind": "FOO"}) == "FOO"
    assert BlindPolicy().display_label() == "Vertical Blind"
    # Each concrete policy's English default stays exactly what #258 shipped.
    assert AwningPolicy().display_label() == "Horizontal Awning"
    assert TiltPolicy().display_label() == "Venetian / Tilt Blind"
    assert OscillatingAwningPolicy().display_label() == "Oscillating Awning"
    assert VenetianPolicy().display_label() == "Venetian Blind (Dual-Axis)"


def test_display_label_untranslated_key_falls_back_to_english() -> None:
    """An override dict missing the policy's key still yields English."""
    assert AwningPolicy().display_label(labels={"cover_types.blind": "X"}) == (
        "Horizontal Awning"
    )


# ---------------------------------------------------------------------------
# (b) summary_geometry_lines override leaves non-overridden lines English
# ---------------------------------------------------------------------------


def test_tilt_geometry_override_one_line_other_stays_english() -> None:
    """Overriding one geometry template translates only that line."""
    config = {CONF_TILT_DEPTH: 3.0, CONF_TILT_DISTANCE: 2.0, CONF_TILT_MODE: "mode2"}
    labels = {"geometry.slat.depth": "Lamellentiefe {v}cm"}
    out = TiltPolicy().summary_geometry_lines(config, labels=labels)
    joined = ", ".join(out)
    assert "Lamellentiefe 3.0cm" in joined  # overridden
    assert "spacing 2.0cm" in joined  # non-overridden → English
    assert "mode: mode2" in joined  # non-overridden → English


def test_tilt_geometry_default_is_english() -> None:
    """``labels=None`` yields byte-identical English (back-compat)."""
    config = {CONF_TILT_DEPTH: 3.0, CONF_TILT_DISTANCE: 2.0, CONF_TILT_MODE: "mode2"}
    out = TiltPolicy().summary_geometry_lines(config)
    assert out == ["slat depth 3.0cm, spacing 2.0cm, mode: mode2"]


def test_blind_window_dims_override() -> None:
    """The shared window-dimensions helper honors a labels override."""
    config = {CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5}
    labels = {"geometry.window.tall": "{h}m hohes Fenster"}
    out = BlindPolicy().summary_geometry_lines(config, labels=labels)
    joined = ", ".join(out)
    assert "2.1m hohes Fenster" in joined  # overridden
    assert "blocking sun 0.5m from the glass" in joined  # English


# ---------------------------------------------------------------------------
# (c) drift guard — en.json subtrees byte-identical to the code dicts
# ---------------------------------------------------------------------------


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(node, str):
        out[prefix] = node
    return out


def _en_config_summary() -> dict:
    with (TRANSLATIONS_DIR / "en.json").open(encoding="utf-8") as fh:
        return json.load(fh)["config_summary"]


def test_cover_type_labels_match_en_json() -> None:
    """``en.json['config_summary']['cover_types']`` == ``COVER_TYPE_LABELS_EN``."""
    en = _flatten(_en_config_summary().get("cover_types", {}))
    expected = {
        k.removeprefix("cover_types."): v for k, v in COVER_TYPE_LABELS_EN.items()
    }
    assert en == expected


def test_geometry_labels_match_en_json() -> None:
    """``en.json['config_summary']['geometry']`` == ``GEOMETRY_LABELS_EN``."""
    en = _flatten(_en_config_summary().get("geometry", {}))
    expected = {k.removeprefix("geometry."): v for k, v in GEOMETRY_LABELS_EN.items()}
    assert en == expected
