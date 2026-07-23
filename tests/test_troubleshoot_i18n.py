"""Tests for the troubleshoot-finding i18n bundle (issue #970, Phase 1).

Mirrors ``test_reason_i18n.py``: the ``_TRIAGE_TEMPLATES_EN`` code defaults, the
shipped ``troubleshoot_i18n/{en,de,fr}.json`` bundle, its three parity locks
(via the shared ``tests/_helpers/i18n_parity`` helper), the English-fallback
loader, and a real ``Finding`` rendered through ``reason_i18n.render``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro import reason_i18n, troubleshoot_i18n
from custom_components.adaptive_cover_pro.const import TriageCode
from custom_components.adaptive_cover_pro.diagnostics.triage import (
    Finding,
    Severity,
)
from custom_components.adaptive_cover_pro.reason_i18n import Reason
from custom_components.adaptive_cover_pro.troubleshoot_i18n import (
    _TRIAGE_TEMPLATES_EN,
    load_troubleshoot_labels,
)
from tests._helpers import i18n_parity

pytestmark = pytest.mark.unit

TROUBLESHOOT_I18N_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "troubleshoot_i18n"
)


# ---------------------------------------------------------------------------
# Loader behavior
# ---------------------------------------------------------------------------


def test_load_english_returns_code_defaults() -> None:
    assert load_troubleshoot_labels("en") == _TRIAGE_TEMPLATES_EN


def test_unknown_language_falls_back_to_english() -> None:
    assert load_troubleshoot_labels("zz") == _TRIAGE_TEMPLATES_EN


def test_malformed_language_falls_back_to_english(tmp_path) -> None:
    # load_bundle_overlay swallows OSError/ValueError -> empty overlay -> English.
    assert load_troubleshoot_labels("") == _TRIAGE_TEMPLATES_EN


def test_every_triage_code_has_a_template() -> None:
    assert set(_TRIAGE_TEMPLATES_EN) == {c.value for c in TriageCode}


async def test_async_prime_offloads_to_executor() -> None:
    class _FakeHass:
        def __init__(self) -> None:
            self.calls = 0

        async def async_add_executor_job(self, func, *args):
            self.calls += 1
            return func(*args)

    hass = _FakeHass()
    labels = await troubleshoot_i18n.async_prime(hass, "en")
    assert hass.calls == 1
    assert labels == _TRIAGE_TEMPLATES_EN


# ---------------------------------------------------------------------------
# A Finding renders through reason_i18n.render with a real template
# ---------------------------------------------------------------------------


def test_finding_renders_through_reason_render() -> None:
    labels = load_troubleshoot_labels("en")
    finding = Finding(
        Reason(TriageCode.COVER_NOT_READY, {"eid": "cover.kitchen"}),
        Severity.WARNING,
        "cover_entities",
    )
    assert (
        reason_i18n.render(finding.reason, labels)
        == "⚠️ cover.kitchen: not ready (unavailable)"
    )


def test_finding_renders_multi_param_template() -> None:
    labels = load_troubleshoot_labels("en")
    finding = Finding(
        Reason(TriageCode.CUSTOM_SAFETY_BYPASS, {"slot": 5, "safety": 100}),
        Severity.WARNING,
        "custom_position",
    )
    rendered = reason_i18n.render(finding.reason, labels)
    assert rendered.startswith("⚠️ Custom #5 is at safety priority (100)")
    assert "Lower its priority below 100" in rendered


# ---------------------------------------------------------------------------
# Bundle parity locks (shared helper)
# ---------------------------------------------------------------------------


def test_troubleshoot_en_matches_code_defaults() -> None:
    i18n_parity.assert_en_matches_defaults(TROUBLESHOOT_I18N_DIR, _TRIAGE_TEMPLATES_EN)


def test_troubleshoot_key_parity_de_fr() -> None:
    i18n_parity.assert_key_parity(TROUBLESHOOT_I18N_DIR)


def test_troubleshoot_placeholder_parity_de_fr() -> None:
    i18n_parity.assert_placeholder_parity(TROUBLESHOOT_I18N_DIR)
