"""Issue #806: Maximum Position must accept 0.

A roof-window impulse motor can only be fully open or fully closed, so the user
needs ``max_position = 0`` ("always keep it closed") — a value the field's help
text already advertises as in-range (0-100%). Four sites conspired to reject it:
the shared range constant floored at 1, both rendered selectors floored at 1, and
``CoverConfig.from_options`` coerced a stored 0 back to 100 via ``or 100``.

These are the regression guards for all four fix points, plus a characterization
test proving ``max_pos = 0`` is semantically valid downstream (forces closed).
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro import config_fields as cf
from custom_components.adaptive_cover_pro.config_flow import POSITION_SCHEMA
from custom_components.adaptive_cover_pro.config_types import CoverConfig
from custom_components.adaptive_cover_pro.const import CONF_MAX_POSITION, OPTION_RANGES
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.position_utils import PositionConverter
from custom_components.adaptive_cover_pro.services.options_service import (
    FIELD_VALIDATORS,
)


def _selector_min(schema, key) -> float:
    """Return the ``min`` of the NumberSelector for ``key`` in *schema*."""
    for marker, value in schema.schema.items():
        if str(marker) == key:
            return value.config["min"]
    raise AssertionError(f"key {key!r} not found in schema")


@pytest.mark.unit
def test_option_ranges_max_position_allows_zero() -> None:
    """The single-source range constant floors at 0, not 1 (drives the validator)."""
    assert OPTION_RANGES[CONF_MAX_POSITION] == (0, 100)


@pytest.mark.unit
def test_field_validator_accepts_max_position_zero() -> None:
    """The service/API validation path accepts 0 without raising."""
    FIELD_VALIDATORS[CONF_MAX_POSITION](0)  # should not raise


@pytest.mark.unit
def test_config_flow_position_schema_max_selector_floors_at_zero() -> None:
    """The config-flow POSITION_SCHEMA slider lets the user pick 0."""
    assert _selector_min(POSITION_SCHEMA, CONF_MAX_POSITION) == 0


@pytest.mark.unit
def test_options_flow_section_schema_max_selector_floors_at_zero() -> None:
    """The rendered options-flow position section slider lets the user pick 0."""
    schema = get_policy("cover_blind").build_section_schema(cf.SECTION_POSITION)
    assert _selector_min(schema, CONF_MAX_POSITION) == 0


@pytest.mark.unit
def test_cover_config_preserves_max_position_zero() -> None:
    """A stored 0 survives ``from_options`` instead of being coerced to 100."""
    config = CoverConfig.from_options({CONF_MAX_POSITION: 0})
    assert config.max_pos == 0


@pytest.mark.unit
def test_cover_config_defaults_max_position_when_absent() -> None:
    """Omitting the key still falls back to the 100 default (guards the fix)."""
    config = CoverConfig.from_options({})
    assert config.max_pos == 100


@pytest.mark.unit
def test_apply_limits_max_position_zero_forces_closed(
    mock_sun_data, mock_logger
) -> None:
    """max_pos=0 clamps any commanded value to fully closed (semantics check)."""
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=0,
        apply_min=False,
        apply_max=False,  # always enforce
        sun_valid=True,
    )
    assert result == 0
