"""en.json coverage for the non-sensor health-check Repairs (issue #975).

Each Repair id maps to an ``issues.<id>`` entry with a title and a description,
and the description must carry every placeholder token the coordinator passes so
HA renders them (a description whose placeholder set differs from the code's is
dropped by HA). en.json is the source of truth; DE/FR parity is enforced
separately by ``tests/test_translations.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_EN = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
    / "en.json"
)

# {translation_key: required placeholder tokens in the description}
_HEALTH_ISSUES = {
    "cover_unavailable": {"{name}", "{entity_id}"},
    "sun_unavailable": {"{name}"},
    "config_position_envelope": {"{name}", "{min}", "{max}"},
    "config_time_window": {"{name}", "{start}", "{end}"},
}


@pytest.fixture(scope="module")
def issues() -> dict:
    with _EN.open(encoding="utf-8") as fh:
        return json.load(fh)["issues"]


@pytest.mark.parametrize("key", sorted(_HEALTH_ISSUES))
def test_health_issue_has_title_and_description(issues, key):
    assert key in issues, f"en.json issues.* missing '{key}'"
    entry = issues[key]
    assert entry.get("title"), f"issues.{key}.title is empty"
    assert entry.get("description"), f"issues.{key}.description is empty"


@pytest.mark.parametrize("key", sorted(_HEALTH_ISSUES))
def test_health_issue_description_has_placeholders(issues, key):
    description = issues[key]["description"]
    for token in _HEALTH_ISSUES[key]:
        assert token in description, f"issues.{key}.description missing {token}"
