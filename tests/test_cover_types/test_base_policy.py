"""Default CoverTypePolicy hook behaviour for non-venetian cover types."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.cover_types.blind import BlindPolicy

from .stub_policy import StubSingleAxisPolicy


@pytest.mark.asyncio
async def test_maybe_update_tilt_only_default_is_noop() -> None:
    """Base-class maybe_update_tilt_only must return None without side-effects."""
    policy = BlindPolicy()
    result = await policy.maybe_update_tilt_only(
        "cover.x",
        current_position=42,
        context=MagicMock(),
        reason="solar",
    )
    assert result is None


def test_entity_selector_filter_default_is_plain_cover_domain() -> None:
    """The base default returns a plain cover-domain selector config."""
    flt = StubSingleAxisPolicy().entity_selector_filter()
    assert flt["domain"] == "cover"
    # No capability requirement should be advertised by the default.
    assert "supported_features" not in flt


def test_geometry_schema_default_is_empty() -> None:
    """The base default returns an empty vol.Schema, not None."""
    schema = StubSingleAxisPolicy().geometry_schema()
    assert isinstance(schema, vol.Schema)
    assert schema({}) == {}


def test_summary_geometry_lines_default_is_empty_list() -> None:
    """The base default returns ``[]`` so summary builders can extend unconditionally."""
    assert StubSingleAxisPolicy().summary_geometry_lines({}) == []
