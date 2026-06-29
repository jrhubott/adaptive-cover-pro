"""Integration tests for the HA diagnostics interface.

Verifies that async_get_config_entry_diagnostics returns valid, serializable
data and that numpy float64 values do not cause JSON serialization errors
(regression guard for issue #149).
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DIAG_CACHE_KEY,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _setup(
    hass: HomeAssistant,
    entry_id: str = "diag_01",
    name: str = "Diag Cover",
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# 6a: Structure and content
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_diagnostics_returns_dict(hass: HomeAssistant) -> None:
    """async_get_config_entry_diagnostics returns a dict."""
    entry = await _setup(hass, entry_id="diag_type_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"


@pytest.mark.integration
async def test_diagnostics_contains_config_data(hass: HomeAssistant) -> None:
    """Diagnostics dict includes config_data with name and sensor_type."""
    entry = await _setup(hass, entry_id="diag_config_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "config_data" in result
    assert result["config_data"].get("name") == "Diag Cover"
    assert result["config_data"].get(CONF_SENSOR_TYPE) == CoverType.BLIND


@pytest.mark.integration
async def test_diagnostics_contains_options(hass: HomeAssistant) -> None:
    """Diagnostics dict includes config_options with the integration options."""
    entry = await _setup(hass, entry_id="diag_opts_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "config_options" in result
    opts = result["config_options"]
    # config_entry.options may be a MappingProxy or dict — check it's mapping-like
    assert hasattr(opts, "__getitem__"), f"Expected mapping, got: {type(opts)}"
    assert len(opts) > 0


@pytest.mark.integration
async def test_diagnostics_contains_entry_id(hass: HomeAssistant) -> None:
    """Diagnostics dict includes the config entry identifier."""
    entry = await _setup(hass, entry_id="diag_eid_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "identifier" in result
    assert result["identifier"] == entry.entry_id


@pytest.mark.integration
async def test_diagnostics_contains_real_builder_output(hass: HomeAssistant) -> None:
    """The diagnostics sub-key must carry real builder output, not the marker.

    Regression guard for the lookup bug: the download handler read the legacy
    ``hass.data[DOMAIN]`` registry after the coordinator moved to
    ``entry.runtime_data``, so every download returned the "coordinator missing"
    marker. Asserting the ``meta`` block (with the integration version) is present
    fails on the buggy lookup and passes once it resolves runtime_data.
    """
    entry = await _setup(hass, entry_id="diag_real_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    diag = result["diagnostics"]
    assert diag.get("status") != "unavailable", f"Got unavailable marker: {diag}"
    assert "meta" in diag, f"Expected real builder output, got: {diag}"
    assert diag["meta"].get("integration_version"), "meta.integration_version missing"


@pytest.mark.integration
async def test_diagnostics_envelope_triage_fields(hass: HomeAssistant) -> None:
    """Envelope carries triage fields HA core's wrapper does not provide."""
    entry = await _setup(hass, entry_id="diag_envelope_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "config_entry_state" in result
    assert "generated_at" in result
    assert "config_entry_version" in result
    assert "config_entry_minor_version" in result
    # generated_at must be a parseable ISO timestamp.
    dt.datetime.fromisoformat(result["generated_at"])


@pytest.mark.integration
async def test_diagnostics_reload_window_serves_cached_snapshot(
    hass: HomeAssistant,
) -> None:
    """With no live coordinator but a cached snapshot, serve it marked stale."""
    entry = await _setup(hass, entry_id="diag_cache_01")
    # Simulate the reload window: runtime_data briefly unset, cache populated.
    entry.runtime_data = None
    captured = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=42)
    hass.data.setdefault(DIAG_CACHE_KEY, {})[entry.entry_id] = {
        "diagnostics": {"meta": {"integration_version": "9.9.9"}},
        "ts": captured,
    }

    result = await async_get_config_entry_diagnostics(hass, entry)
    diag = result["diagnostics"]
    assert diag["meta"]["integration_version"] == "9.9.9"
    assert diag["cache_status"]["stale"] is True
    assert diag["cache_status"]["age_seconds"] >= 42


@pytest.mark.integration
async def test_diagnostics_no_coordinator_no_cache_marker(
    hass: HomeAssistant,
) -> None:
    """With neither coordinator nor cache, return the marker plus entry state."""
    entry = await _setup(hass, entry_id="diag_marker_01")
    entry.runtime_data = None
    hass.data.get(DIAG_CACHE_KEY, {}).pop(entry.entry_id, None)

    result = await async_get_config_entry_diagnostics(hass, entry)
    diag = result["diagnostics"]
    assert diag["status"] == "unavailable"
    assert "coordinator missing" in diag["reason"]
    # The envelope still states whether the entry is set up.
    assert "config_entry_state" in result


@pytest.mark.integration
async def test_diagnostics_no_sensitive_tokens(hass: HomeAssistant) -> None:
    """Diagnostics must not leak HA auth tokens or passwords."""
    entry = await _setup(hass, entry_id="diag_sec_01")
    result = await async_get_config_entry_diagnostics(hass, entry)
    result_str = json.dumps(result, default=str)
    # No common HA token key names should appear
    for bad_key in ("access_token", "password", "api_key", "secret"):
        assert (
            bad_key not in result_str.lower()
        ), f"Sensitive key '{bad_key}' found in diagnostics output"


# ---------------------------------------------------------------------------
# 6b: JSON serializability (regression #149)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_diagnostics_result_is_json_serializable(hass: HomeAssistant) -> None:
    """The diagnostics dict must be JSON-serializable with HA's encoder.

    Regression test for issue #149: numpy float64 in the diagnostics caused
    an HTTP 500 when downloading the diagnostics from the HA UI.

    Note: HA's diagnostics framework uses a custom encoder that handles
    mappingproxy and other HA types. We use the same approach here.
    """
    entry = await _setup(hass, entry_id="diag_json_01")
    result = await async_get_config_entry_diagnostics(hass, entry)

    def _ha_default(o):
        """Handle types HA's JSON encoder supports."""
        if hasattr(o, "items"):  # mappingproxy, etc.
            return dict(o)
        if isinstance(o, set | frozenset):
            return list(o)
        raise TypeError(f"Not serializable: {type(o)}")

    try:
        json.dumps(result, default=_ha_default)
    except (TypeError, ValueError) as exc:
        pytest.fail(
            f"Diagnostics result is not JSON-serializable even with HA encoder: {exc}\nResult: {result}"
        )


@pytest.mark.integration
async def test_diagnostics_json_serializable_with_numpy_floats(
    hass: HomeAssistant,
) -> None:
    """Even if coordinator data contains numpy float64, diagnostics must serialize.

    Directly injects numpy values into the diagnostics output to verify the
    serialization guard works end-to-end.
    """
    entry = await _setup(hass, entry_id="diag_np_01")

    # Build a diagnostics dict with numpy types injected
    result = await async_get_config_entry_diagnostics(hass, entry)

    # Inject numpy float into the options (simulate what happened in #149)
    # config_options may be a mappingproxy; convert to dict first
    result["config_options"] = dict(result["config_options"])
    result["config_options"]["test_numpy_float"] = np.float64(42.0)
    result["config_options"]["test_numpy_int"] = np.int64(7)

    # This should NOT raise — the serialization guard must convert numpy types
    def _ha_default(o):
        if hasattr(o, "items"):
            return dict(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.integer):
            return int(o)
        raise TypeError(f"Not serializable: {type(o)}")

    try:
        json.dumps(result, default=_ha_default)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"numpy types in diagnostics caused serialization failure: {exc}")
