"""Policy tests for the louvered (lamella) roof cover type (#830)."""

from __future__ import annotations

import voluptuous as vol
import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_MAX_SLAT_ANGLE,
    CONF_ROOF_PITCH,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    DEFAULT_MAX_SLAT_ANGLE,
)
from custom_components.adaptive_cover_pro.config_types import LouveredRoofConfig
from custom_components.adaptive_cover_pro.const import CoverType
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import AXIS_NAME_TILT
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveLouveredRoofCover,
)

pytestmark = pytest.mark.unit

COVER_TYPE = "cover_louvered_roof"


class TestLouveredRoofConfig:
    """The ``LouveredRoofConfig`` dataclass and the enum member."""

    def test_enum_value(self) -> None:
        assert CoverType.LOUVERED_ROOF.value == "cover_louvered_roof"

    def test_from_options_defaults_pitch_to_zero(self) -> None:
        assert LouveredRoofConfig.from_options({}).roof_pitch == 0.0

    def test_from_options_reads_roof_pitch(self) -> None:
        assert LouveredRoofConfig.from_options({CONF_ROOF_PITCH: 30}).roof_pitch == 30.0

    def test_from_options_defaults_max_slat_angle_to_zero(self) -> None:
        assert LouveredRoofConfig.from_options({}).max_slat_angle == 0

    def test_from_options_reads_max_slat_angle(self) -> None:
        cfg = LouveredRoofConfig.from_options({CONF_MAX_SLAT_ANGLE: 160})
        assert cfg.max_slat_angle == 160.0


def _schema_keys(schema: vol.Schema) -> set[str]:
    return {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}


def _schema_default(schema: vol.Schema, key: str):
    for marker in schema.schema:
        name = marker.schema if isinstance(marker, vol.Marker) else marker
        if name == key and isinstance(marker, vol.Marker):
            return marker.default() if callable(marker.default) else marker.default
    raise KeyError(key)


def _schema_value_validator(schema: vol.Schema, key: str):
    for marker, value in schema.schema.items():
        name = marker.schema if isinstance(marker, vol.Marker) else marker
        if name == key:
            return value
    raise KeyError(key)


def test_tilt_schema_accepts_value_at_raised_slat_cap() -> None:
    # The shared slat depth/spacing cap was raised to 30 cm globally, so the
    # interior tilt/venetian schema must validate a value at the new cap too.
    from custom_components.adaptive_cover_pro.cover_types.tilt import (
        geometry_tilt_schema,
    )

    schema = geometry_tilt_schema()
    depth_validator = _schema_value_validator(schema, CONF_TILT_DEPTH)
    assert depth_validator(30.0) == 30.0


class TestLouveredRoofPolicy:
    """Policy hooks for the louvered-roof cover type."""

    def test_registered(self) -> None:
        assert get_policy(COVER_TYPE) is not None

    def test_single_tilt_axis(self) -> None:
        policy = get_policy(COVER_TYPE)
        assert tuple(a.name for a in policy.axes) == (AXIS_NAME_TILT,)

    def test_tilt_capable_entity_filter(self) -> None:
        policy = get_policy(COVER_TYPE)
        filt = policy.entity_selector_filter()
        assert filt["domain"] == "cover"
        assert (
            "cover.CoverEntityFeature.SET_TILT_POSITION" in filt["supported_features"]
        )

    def test_non_empty_display_label(self) -> None:
        assert get_policy(COVER_TYPE).display_label()

    def test_wiki_anchor(self) -> None:
        assert get_policy(COVER_TYPE).wiki_anchor() == "Configuration-Louvered-Roof"

    def test_geometry_schema_has_slat_and_pitch_fields(self) -> None:
        keys = _schema_keys(get_policy(COVER_TYPE).geometry_schema())
        assert {
            CONF_TILT_DEPTH,
            CONF_TILT_DISTANCE,
            CONF_TILT_MODE,
            CONF_ROOF_PITCH,
        } <= keys

    def test_roof_pitch_defaults_to_zero(self) -> None:
        schema = get_policy(COVER_TYPE).geometry_schema()
        assert _schema_default(schema, CONF_ROOF_PITCH) == 0

    def test_geometry_schema_has_max_slat_angle(self) -> None:
        keys = _schema_keys(get_policy(COVER_TYPE).geometry_schema())
        assert CONF_MAX_SLAT_ANGLE in keys

    def test_max_slat_angle_defaults_to_zero(self) -> None:
        schema = get_policy(COVER_TYPE).geometry_schema()
        assert _schema_default(schema, CONF_MAX_SLAT_ANGLE) == DEFAULT_MAX_SLAT_ANGLE

    def test_disallows_vertical_and_awning_geometry(self) -> None:
        policy = get_policy(COVER_TYPE)
        vertical_only = {"distance_shaded_area"}
        awning_only = {"length_awning"}
        tilt_only = {"tilt_depth"}
        rejected = policy.disallowed_geometry_fields(
            vertical_only=vertical_only,
            awning_only=awning_only,
            tilt_only=tilt_only,
        )
        rejected_sets = [s for s, _label in rejected]
        assert vertical_only in rejected_sets
        assert awning_only in rejected_sets
        assert tilt_only not in rejected_sets

    def test_capability_warning_when_no_set_tilt_position(self) -> None:
        policy = get_policy(COVER_TYPE)
        warnings = policy.cover_capability_warnings(
            {"cover.x": {"has_set_tilt_position": False}}
        )
        assert warnings and "set_tilt_position" in warnings[0]
        # No warning when at least one entity supports set_tilt_position.
        assert (
            policy.cover_capability_warnings(
                {"cover.x": {"has_set_tilt_position": True}}
            )
            == []
        )

    def test_summary_geometry_lines_include_slat_and_pitch(self) -> None:
        policy = get_policy(COVER_TYPE)
        config = {
            CONF_TILT_DEPTH: 3.0,
            CONF_TILT_DISTANCE: 2.0,
            CONF_TILT_MODE: "mode2",
            CONF_ROOF_PITCH: 15,
        }
        lines = policy.summary_geometry_lines(config)
        joined = " ".join(lines)
        assert "slat depth 3.0cm" in joined
        assert "spacing 2.0cm" in joined
        assert "roof pitch 15° from horizontal" in joined

    def test_geometry_slat_keys(self) -> None:
        keys = get_policy(COVER_TYPE).geometry_slat_keys()
        assert CONF_TILT_DEPTH in keys
        assert CONF_TILT_DISTANCE in keys

    def test_summary_geometry_lines_empty_config(self) -> None:
        assert get_policy(COVER_TYPE).summary_geometry_lines({}) == []

    def test_summary_geometry_lines_pitch_only(self) -> None:
        # Roof pitch with no slat fields still renders a pitch-only line.
        lines = get_policy(COVER_TYPE).summary_geometry_lines({CONF_ROOF_PITCH: 12})
        assert lines == ["roof pitch 12° from horizontal"]

    def test_summary_includes_max_slat_angle_when_set(self) -> None:
        policy = get_policy(COVER_TYPE)
        config = {
            CONF_TILT_DEPTH: 3.0,
            CONF_TILT_DISTANCE: 2.0,
            CONF_TILT_MODE: "mode2",
            CONF_ROOF_PITCH: 15,
            CONF_MAX_SLAT_ANGLE: 160,
        }
        joined = " ".join(policy.summary_geometry_lines(config))
        assert "roof pitch 15° from horizontal" in joined
        assert "max slat angle 160°" in joined

    def test_summary_omits_max_slat_angle_when_zero(self) -> None:
        policy = get_policy(COVER_TYPE)
        config = {
            CONF_TILT_DEPTH: 3.0,
            CONF_TILT_DISTANCE: 2.0,
            CONF_TILT_MODE: "mode2",
            CONF_ROOF_PITCH: 15,
            CONF_MAX_SLAT_ANGLE: 0,
        }
        assert "max slat angle" not in " ".join(policy.summary_geometry_lines(config))

    def test_geometry_schema_localized_branch(self) -> None:
        from unittest.mock import MagicMock

        # hass provided → the non-cached (localized) schema-builder branch runs.
        hass = MagicMock()
        hass.config.units.length_unit = "m"
        schema = get_policy(COVER_TYPE).geometry_schema(hass=hass)
        assert CONF_ROOF_PITCH in _schema_keys(schema)

    def test_louvered_schema_accepts_pergola_scale_slats(self) -> None:
        # Pergola slats are much larger than interior venetian blinds. The
        # louvered geometry schema must validate a 17 cm depth / 15 cm spacing
        # rig (the reference installation from #830). The old 15 cm cap
        # rejected the 17 cm depth.
        schema = get_policy(COVER_TYPE).geometry_schema()
        depth_validator = _schema_value_validator(schema, CONF_TILT_DEPTH)
        distance_validator = _schema_value_validator(schema, CONF_TILT_DISTANCE)
        assert depth_validator(17.0) == 17.0
        assert distance_validator(15.0) == 15.0

    def test_louvered_slat_defaults_are_pergola_scale(self) -> None:
        from custom_components.adaptive_cover_pro.const import (
            DEFAULT_LOUVERED_SLAT_DEPTH_CM,
            DEFAULT_LOUVERED_SLAT_DISTANCE_CM,
        )

        schema = get_policy(COVER_TYPE).geometry_schema()
        assert (
            _schema_default(schema, CONF_TILT_DEPTH) == DEFAULT_LOUVERED_SLAT_DEPTH_CM
        )
        assert (
            _schema_default(schema, CONF_TILT_DISTANCE)
            == DEFAULT_LOUVERED_SLAT_DISTANCE_CM
        )
        assert DEFAULT_LOUVERED_SLAT_DEPTH_CM == 17.0
        assert DEFAULT_LOUVERED_SLAT_DISTANCE_CM == 15.0

    def test_includes_shaded_distance_false(self) -> None:
        # The tilt-only louvered engine never reads the shaded-distance field,
        # so the policy hides it (#830 follow-up). A vertical blind still shows it.
        assert get_policy(COVER_TYPE).includes_shaded_distance() is False
        assert get_policy("cover_blind").includes_shaded_distance() is True

    def test_composed_geometry_schema_omits_shaded_distance(self) -> None:
        # The shared window-facing bundle is composed onto every geometry schema
        # via _get_geometry_schema; for louvered the shaded-distance marker must
        # be dropped, while every other type keeps it.
        from custom_components.adaptive_cover_pro import config_flow as cf
        from custom_components.adaptive_cover_pro.const import CONF_DISTANCE

        louvered_keys = _schema_keys(cf._get_geometry_schema(COVER_TYPE))
        assert CONF_DISTANCE not in louvered_keys

        blind_keys = _schema_keys(cf._get_geometry_schema("cover_blind"))
        assert CONF_DISTANCE in blind_keys

    def test_geometry_unit_keys_omit_shaded_distance(self) -> None:
        from custom_components.adaptive_cover_pro import config_flow as cf
        from custom_components.adaptive_cover_pro.const import CONF_DISTANCE

        louvered_length_keys, _ = cf._geometry_unit_keys(COVER_TYPE)
        assert CONF_DISTANCE not in louvered_length_keys

        blind_length_keys, _ = cf._geometry_unit_keys("cover_blind")
        assert CONF_DISTANCE in blind_length_keys

    def test_build_calc_engine_returns_louvered_engine(self) -> None:
        from unittest.mock import MagicMock

        policy = get_policy(COVER_TYPE)
        config_service = MagicMock()
        config_service.get_tilt_data.return_value = MagicMock()
        engine = policy.build_calc_engine(
            logger=MagicMock(),
            sol_azi=180.0,
            sol_elev=40.0,
            sun_data=MagicMock(),
            config=MagicMock(),
            config_service=config_service,
            options={CONF_ROOF_PITCH: 0},
        )
        assert isinstance(engine, AdaptiveLouveredRoofCover)
        assert engine.roof_pitch == 0.0
