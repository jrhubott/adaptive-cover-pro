"""Config-flow selector for the outdoor-temp source option (issue #547)."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.config_dynamic import (
    temperature_climate_schema,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_OUTSIDE_TEMP_SOURCE,
    OutsideTempSource,
)


def _key(schema: vol.Schema, name: str):
    for k in schema.schema:
        if str(k) == name:
            return k
    return None


@pytest.mark.unit
def test_schema_contains_outside_temp_source_with_default_live():
    schema = temperature_climate_schema()
    key = _key(schema, CONF_OUTSIDE_TEMP_SOURCE)
    assert key is not None, "CONF_OUTSIDE_TEMP_SOURCE missing from climate schema"
    assert key.default() == OutsideTempSource.LIVE.value


@pytest.mark.unit
def test_selector_offers_three_options():
    schema = temperature_climate_schema()
    key = _key(schema, CONF_OUTSIDE_TEMP_SOURCE)
    selector = schema.schema[key]
    raw_options = selector.config["options"]
    option_values = {(o["value"] if isinstance(o, dict) else o) for o in raw_options}
    assert option_values == {
        "live",
        "forecast_max",
        "max_of_live_and_forecast",
    }
