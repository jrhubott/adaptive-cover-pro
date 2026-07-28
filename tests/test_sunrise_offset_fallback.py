"""Pins the sunrise-offset fallback semantic across every consumer (issue #1050).

"Sunrise offset falls back to the sunset offset when unset" used to be spelled out
independently in five places, and ``config_flow._build_config_summary`` defaulted to
``0`` instead — so the Configuration Summary described a night window the runtime did
not use. Every call site now routes through ``helpers.resolve_sunrise_offset``.

The parity tests below exercise each consumer with the same options dict, so a sixth
copy that reintroduces a sixth variant fails here rather than shipping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_flow import (
    _build_config_summary,
)
from custom_components.adaptive_cover_pro.config_types import CoverConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_SENSOR_TYPE,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.helpers import (
    _read_sun_boundary_options,
    resolve_sunrise_offset,
    resolve_sunset_offset,
)
from custom_components.adaptive_cover_pro.services.export_service import (
    async_handle_export,
)

# The case the fallback exists to serve: only a sunset offset was ever configured.
SUNSET_ONLY = {CONF_SUNSET_OFFSET: 30}


# ---------------------------------------------------------------------------
# The pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sunset_offset_defaults_to_zero():
    """An unset sunset offset resolves to 0, not None."""
    assert resolve_sunset_offset({}) == 0


@pytest.mark.unit
def test_sunset_offset_coerces_float_to_int():
    """HA's NumberSelector hands back floats; the offset is minutes as int."""
    assert resolve_sunset_offset({CONF_SUNSET_OFFSET: 30.0}) == 30


@pytest.mark.unit
def test_sunset_offset_none_reads_as_zero():
    """A stored ``None`` is treated as unset rather than crashing on int(None)."""
    assert resolve_sunset_offset({CONF_SUNSET_OFFSET: None}) == 0


@pytest.mark.unit
def test_sunrise_offset_falls_back_to_sunset():
    """Unset sunrise offset inherits the sunset offset — a symmetric night window."""
    assert resolve_sunrise_offset(SUNSET_ONLY) == 30


@pytest.mark.unit
def test_sunrise_offset_explicit_value_wins():
    """A configured sunrise offset is used verbatim, never the sunset offset."""
    assert (
        resolve_sunrise_offset({CONF_SUNSET_OFFSET: 30, CONF_SUNRISE_OFFSET: -15})
        == -15
    )


@pytest.mark.unit
def test_sunrise_offset_explicit_zero_wins():
    """An explicit 0 is a real choice, not "unset" — it must not fall back to 30."""
    assert resolve_sunrise_offset({CONF_SUNSET_OFFSET: 30, CONF_SUNRISE_OFFSET: 0}) == 0


@pytest.mark.unit
def test_sunrise_offset_none_falls_back_to_sunset():
    """A stored ``None`` is unset, so it takes the sunset offset like an absent key."""
    assert (
        resolve_sunrise_offset({CONF_SUNSET_OFFSET: 30, CONF_SUNRISE_OFFSET: None})
        == 30
    )


@pytest.mark.unit
def test_sunrise_offset_zero_when_neither_configured():
    """Nothing configured means no offset on either boundary."""
    assert resolve_sunrise_offset({}) == 0


# ---------------------------------------------------------------------------
# Every consumer agrees for the same options dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sun_boundary_reader_applies_the_fallback():
    """The coordinator/snapshot reader (helpers._read_sun_boundary_options)."""
    hass = MagicMock()
    hass.states.get.return_value = None
    bounds = _read_sun_boundary_options(hass, SUNSET_ONLY)
    assert (bounds.sunset_off, bounds.sunrise_off) == (30, 30)


@pytest.mark.unit
def test_cover_config_applies_the_fallback():
    """The pure calc engine's CoverConfig.from_options."""
    cfg = CoverConfig.from_options(dict(SUNSET_ONLY))
    assert (cfg.sunset_off, cfg.sunrise_off) == (30, 30)


@pytest.mark.asyncio
async def test_export_service_applies_the_fallback():
    """The export_config service payload."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {"name": "Test Cover", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = dict(SUNSET_ONLY)

    hass = MagicMock()
    hass.config.latitude = 32.939
    hass.config.longitude = -117.156
    hass.config.elevation = 10
    hass.config.time_zone = "America/Los_Angeles"
    hass.config_entries.async_get_entry.return_value = entry

    call = MagicMock()
    call.data = {"config_entry_id": "test-entry-id"}
    call.hass = hass

    common = (await async_handle_export(call))["common"]
    assert (common[CONF_SUNSET_OFFSET], common[CONF_SUNRISE_OFFSET]) == (30, 30)


@pytest.mark.unit
def test_config_summary_applies_the_fallback():
    """The Configuration Summary's After sunrise line (the divergent copy)."""
    summary = _build_config_summary(
        {**SUNSET_ONLY, CONF_SUNSET_POS: 80, CONF_DEFAULT_HEIGHT: 0},
        CoverType.BLIND,
    )
    assert "After sunset (+30 min)" in summary
    assert "After sunrise (+30 min)" in summary
