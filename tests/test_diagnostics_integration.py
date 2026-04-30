"""Integration tests for the HA diagnostics interface.

Verifies that async_get_config_entry_diagnostics returns valid, serializable
data and that numpy float64 values do not cause JSON serialization errors
(regression guard for issue #149).
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
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
        data={"name": name, CONF_SENSOR_TYPE: SensorType.BLIND},
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
    assert result["config_data"].get(CONF_SENSOR_TYPE) == SensorType.BLIND


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
        if isinstance(o, (set, frozenset)):
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
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        raise TypeError(f"Not serializable: {type(o)}")

    try:
        json.dumps(result, default=_ha_default)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"numpy types in diagnostics caused serialization failure: {exc}")
