"""Tests asserting each CONF_* key lives on its correct config-flow step."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro import config_flow as cf
from custom_components.adaptive_cover_pro.const import (
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_TRANSIT_TIMEOUT,
)


def _schema_keys(schema) -> set[str]:
    return {str(k) for k in schema.schema}


@pytest.mark.parametrize(
    "conf_key, expected_schema_name, forbidden_schema_name",
    [
        (CONF_TRANSIT_TIMEOUT, "MANUAL_OVERRIDE_SCHEMA", "DEBUG_SCHEMA"),
        (CONF_DEBUG_EVENT_BUFFER_SIZE, "DEBUG_SCHEMA", "MANUAL_OVERRIDE_SCHEMA"),
        (CONF_MANUAL_OVERRIDE_DURATION, "MANUAL_OVERRIDE_SCHEMA", "DEBUG_SCHEMA"),
        (CONF_DEBUG_MODE, "DEBUG_SCHEMA", "MANUAL_OVERRIDE_SCHEMA"),
    ],
)
def test_conf_key_lives_on_correct_step(
    conf_key: str, expected_schema_name: str, forbidden_schema_name: str
) -> None:
    expected = _schema_keys(getattr(cf, expected_schema_name))
    forbidden = _schema_keys(getattr(cf, forbidden_schema_name))
    assert conf_key in expected, f"{conf_key} should be in {expected_schema_name}"
    assert (
        conf_key not in forbidden
    ), f"{conf_key} should NOT be in {forbidden_schema_name}"
