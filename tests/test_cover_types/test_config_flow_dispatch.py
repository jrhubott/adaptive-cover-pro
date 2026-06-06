"""Verify config-flow / options-service dispatch hooks on each policy.

Pins the contract for ``geometry_schema``, ``entity_selector_filter``, and
``summary_geometry_lines`` so future cover-type policies don't silently
short-circuit a config-flow entry point.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.cover_types import (
    AwningPolicy,
    BlindPolicy,
    TiltPolicy,
    VenetianPolicy,
    get_policy,
)
from custom_components.adaptive_cover_pro.cover_types.awning import (
    GEOMETRY_HORIZONTAL_SCHEMA,
)
from custom_components.adaptive_cover_pro.cover_types.blind import (
    GEOMETRY_VERTICAL_SCHEMA,
)
from custom_components.adaptive_cover_pro.cover_types.tilt import (
    GEOMETRY_TILT_SCHEMA,
    TILT_CAPABLE_ENTITY_FILTER,
)
from custom_components.adaptive_cover_pro.cover_types.venetian import (
    GEOMETRY_VENETIAN_SCHEMA,
)


@pytest.mark.unit
class TestGeometrySchemaDispatch:
    """``policy.geometry_schema()`` returns the right schema per cover type."""

    def test_blind(self):
        assert BlindPolicy().geometry_schema() is GEOMETRY_VERTICAL_SCHEMA

    def test_awning(self):
        assert AwningPolicy().geometry_schema() is GEOMETRY_HORIZONTAL_SCHEMA

    def test_tilt(self):
        assert TiltPolicy().geometry_schema() is GEOMETRY_TILT_SCHEMA

    def test_venetian(self):
        assert VenetianPolicy().geometry_schema() is GEOMETRY_VENETIAN_SCHEMA


@pytest.mark.unit
class TestEntitySelectorFilter:
    """``entity_selector_filter`` reflects each policy's capability needs."""

    def test_blind_no_tilt_capability_required(self):
        f = BlindPolicy().entity_selector_filter()
        assert f["domain"] == "cover"
        assert "supported_features" not in f or not f.get("supported_features")

    def test_awning_no_tilt_capability_required(self):
        f = AwningPolicy().entity_selector_filter()
        assert f["domain"] == "cover"
        assert "supported_features" not in f or not f.get("supported_features")

    def test_tilt_requires_tilt_position(self):
        assert TiltPolicy().entity_selector_filter() is TILT_CAPABLE_ENTITY_FILTER

    def test_venetian_requires_tilt_position(self):
        # Venetian shares the same filter (HA's supported_features is OR-of-listed,
        # not AND, so we filter on the rarer capability and surface the
        # missing-set_position case via cover_capability_warnings).
        assert VenetianPolicy().entity_selector_filter() is TILT_CAPABLE_ENTITY_FILTER


@pytest.mark.unit
class TestSummaryGeometryLines:
    """``summary_geometry_lines`` renders the right geometry block per type."""

    def test_blind_renders_window_dimensions(self):
        lines = BlindPolicy().summary_geometry_lines(
            {"window_height": 2.1, "distance_shaded_area": 0.5}
        )
        assert lines == ["2.1m tall window, blocking sun 0.5m from the glass"]

    def test_awning_renders_length_angle_window(self):
        lines = AwningPolicy().summary_geometry_lines(
            {
                "length_awning": 2.0,
                "angle": 30,
                "window_height": 2.1,
                "distance_shaded_area": 1.0,
            }
        )
        assert len(lines) == 1
        assert "2.0m awning" in lines[0]
        assert "angled at 30°" in lines[0]

    def test_tilt_renders_slat_block(self):
        lines = TiltPolicy().summary_geometry_lines(
            {"slat_depth": 3, "slat_distance": 2, "tilt_mode": "mode2"}
        )
        assert lines == ["slat depth 3cm, spacing 2cm, mode: mode2"]

    def test_venetian_renders_window_then_slat_block(self):
        lines = VenetianPolicy().summary_geometry_lines(
            {
                "window_height": 2.1,
                "distance_shaded_area": 0.5,
                "slat_depth": 3,
                "slat_distance": 2,
                "tilt_mode": "mode2",
            }
        )
        assert lines == [
            "2.1m tall window, blocking sun 0.5m from the glass",
            "slat depth 3cm, spacing 2cm, mode: mode2",
            "skip tilt when position > 95%",
            "mode: position and tilt",
            "min tilt 0%",
            "max tilt 100%",
            "post-settle hold 3.0s",
            "back-rotate publish lag 45.0s",
        ]

    def test_empty_config_renders_nothing(self):
        for cls in (BlindPolicy, AwningPolicy, TiltPolicy):
            assert cls().summary_geometry_lines({}) == []

    def test_venetian_empty_config_renders_threshold_default(self):
        lines = VenetianPolicy().summary_geometry_lines({})
        assert lines == [
            "skip tilt when position > 95%",
            "mode: position and tilt",
            "min tilt 0%",
            "max tilt 100%",
            "post-settle hold 3.0s",
            "back-rotate publish lag 45.0s",
        ]

    def test_venetian_summary_shows_inverse_tilt_when_set(self):
        from custom_components.adaptive_cover_pro.const import CONF_INVERSE_TILT

        lines = VenetianPolicy().summary_geometry_lines({CONF_INVERSE_TILT: True})
        assert "Inverse tilt" in lines

    def test_venetian_summary_omits_inverse_tilt_when_false(self):
        from custom_components.adaptive_cover_pro.const import CONF_INVERSE_TILT

        lines = VenetianPolicy().summary_geometry_lines({CONF_INVERSE_TILT: False})
        assert "Inverse tilt" not in lines

    def test_venetian_geometry_schema_accepts_inverse_tilt(self):
        from custom_components.adaptive_cover_pro.const import CONF_INVERSE_TILT
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            GEOMETRY_VENETIAN_SCHEMA,
        )

        result = GEOMETRY_VENETIAN_SCHEMA({CONF_INVERSE_TILT: True})
        assert result[CONF_INVERSE_TILT] is True

    def test_venetian_geometry_schema_inverse_tilt_defaults_to_false(self):
        from custom_components.adaptive_cover_pro.const import CONF_INVERSE_TILT
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            GEOMETRY_VENETIAN_SCHEMA,
        )

        result = GEOMETRY_VENETIAN_SCHEMA({})
        assert result[CONF_INVERSE_TILT] is False

    def test_venetian_summary_shows_tilt_only_mode(self):
        from custom_components.adaptive_cover_pro.const import (
            CONF_VENETIAN_MODE,
            VENETIAN_MODE_TILT_ONLY,
        )

        lines = VenetianPolicy().summary_geometry_lines(
            {CONF_VENETIAN_MODE: VENETIAN_MODE_TILT_ONLY}
        )
        assert "mode: tilt only" in lines

    def test_venetian_summary_shows_position_and_tilt_mode(self):
        from custom_components.adaptive_cover_pro.const import (
            CONF_VENETIAN_MODE,
            VENETIAN_MODE_POSITION_AND_TILT,
        )

        lines = VenetianPolicy().summary_geometry_lines(
            {CONF_VENETIAN_MODE: VENETIAN_MODE_POSITION_AND_TILT}
        )
        assert "mode: position and tilt" in lines


@pytest.mark.unit
class TestGetPolicyAcceptsBothForms:
    """``get_policy`` accepts plain strings and ``StrEnum`` members."""

    def test_string_input(self):
        assert isinstance(get_policy("cover_blind"), BlindPolicy)

    def test_strenum_input(self):
        from custom_components.adaptive_cover_pro.const import CoverType

        assert isinstance(get_policy(CoverType.VENETIAN), VenetianPolicy)


@pytest.mark.unit
class TestSupportsGlareZones:
    """``supports_glare_zones`` is the single seam for the blind-only feature."""

    def test_blind_supports(self):
        assert BlindPolicy.supports_glare_zones is True

    def test_awning_does_not_support(self):
        assert AwningPolicy.supports_glare_zones is False

    def test_tilt_does_not_support(self):
        assert TiltPolicy.supports_glare_zones is False

    def test_venetian_does_not_support(self):
        # Venetian could grow this later — the flag is the single switch.
        assert VenetianPolicy.supports_glare_zones is False
