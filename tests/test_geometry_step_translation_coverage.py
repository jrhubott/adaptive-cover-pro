"""Guard: every geometry-step schema field has a translated label + description.

A geometry field whose ``CONF_*`` key is missing from the ``geometry`` step's
``data`` block renders as its raw key in the UI (e.g. the untranslated
``tilt_mode`` / ``tilt_angle_0`` labels reported for venetian covers). A missing
``data_description`` entry drops the field's helper text.

This iterates every real cover type's geometry schema and asserts each field
key is present in BOTH the config-flow and options-flow geometry steps, for
both ``data`` (name) and ``data_description``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro import config_flow as cf
from custom_components.adaptive_cover_pro.const import CoverType

_EN_JSON: dict = json.loads(
    (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    ).read_text()
)

# Physical cover types that render a geometry step (the building-profile virtual
# type controls no cover and has no geometry step).
_GEOMETRY_COVER_TYPES = [
    t.value for t in CoverType if t is not CoverType.BUILDING_PROFILE
]


def _all_geometry_field_keys() -> set[str]:
    keys: set[str] = set()
    for cover_type in _GEOMETRY_COVER_TYPES:
        schema = cf._get_geometry_schema(cover_type)
        keys |= {str(k) for k in schema.schema}
    return keys


@pytest.mark.parametrize("flow", ["config", "options"])
@pytest.mark.parametrize("block", ["data", "data_description"])
def test_every_geometry_field_has_translation(flow: str, block: str) -> None:
    step = _EN_JSON[flow]["step"]["geometry"]
    entries = step.get(block, {})
    missing = sorted(k for k in _all_geometry_field_keys() if k not in entries)
    assert not missing, (
        f"{flow}.step.geometry.{block} is missing entries for geometry schema "
        f"fields {missing} — they render as raw keys in the UI. Add them to "
        "translations/en.json, then run `acp-translate` to sync DE/FR."
    )
