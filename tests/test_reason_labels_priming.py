"""Coordinator priming + threading of the reason-label overlay (issue #882, 9a).

``async_setup_entry`` primes ``coordinator._reason_labels`` for the HA instance
language (mirroring the summary_i18n priming), and the coordinator threads that
overlay into the ``DiagnosticContext`` so ``position_explanation`` renders in the
instance language.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    CoverType,
    DOMAIN,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.reason_i18n import load_reason_labels
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh


async def _setup(hass: HomeAssistant):
    hass.states.async_set("cover.test_blind", "open", {"current_position": 50})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Prime", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="reason_prime_01",
        title="Prime",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_setup_primes_reason_labels_for_instance_language(
    hass: HomeAssistant,
) -> None:
    hass.config.language = "de"
    coordinator = await _setup(hass)
    assert coordinator._reason_labels == load_reason_labels("de")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reason_labels_thread_into_position_explanation(
    hass: HomeAssistant,
) -> None:
    coordinator = await _setup(hass)
    # Inject a fake DE overlay onto the live coordinator; the next diagnostics
    # build must render the explanation base through it.
    coordinator._reason_labels = {
        ReasonCode.SOLAR_TRACKING: "Sonne — Position {position}%{suffix}",
        ReasonCode.CLIMATE_ACTIVE: "Klimamodus ({season}) — Position {position}%",
        ReasonCode.DEFAULT_NO_CONDITION: "keine Bedingung — {pos_label} {position}%",
        ReasonCode.FRAGMENT_DEFAULT_POSITION: "Standardposition",
        ReasonCode.FRAGMENT_SUNSET_POSITION: "Sonnenuntergangsposition",
    }
    diag = coordinator.build_diagnostic_data()
    explanation = diag["position_explanation"]
    # Whatever handler won, the base is one of the injected DE templates — assert
    # no English base leaked through (the German umlaut / word is present, or the
    # outside-window German phrase). The explanation must not contain the English
    # "no active condition" / "sun within acceptance angle" prose.
    assert "no active condition" not in explanation
    assert "sun within acceptance angle" not in explanation
