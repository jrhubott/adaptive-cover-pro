"""Tests asserting each CONF_* key lives on its correct config-flow step."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro import config_flow as cf
from custom_components.adaptive_cover_pro.const import (
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DEBUG_MODE,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_POSITION_TOLERANCE,
    CONF_TRANSIT_TIMEOUT,
    CONF_VENETIAN_MODE,
)


def _schema_keys(schema) -> set[str]:
    return {str(k) for k in schema.schema}


def test_position_tolerance_in_position_schema_with_default_three() -> None:
    """CONF_POSITION_TOLERANCE lives on the position step, default 3 (issue #591)."""
    keys = _schema_keys(cf.POSITION_SCHEMA)
    assert CONF_POSITION_TOLERANCE in keys
    # It is a position concept, not a timing one — must not remain on automation.
    assert CONF_POSITION_TOLERANCE not in _schema_keys(cf.AUTOMATION_SCHEMA)
    marker = next(
        k for k in cf.POSITION_SCHEMA.schema if str(k) == CONF_POSITION_TOLERANCE
    )
    assert marker.default() == 3


def test_enable_position_matching_in_position_schema() -> None:
    """CONF_ENABLE_POSITION_MATCHING lives on the position step, default False (#591)."""
    keys = _schema_keys(cf.POSITION_SCHEMA)
    assert CONF_ENABLE_POSITION_MATCHING in keys
    assert CONF_ENABLE_POSITION_MATCHING not in _schema_keys(cf.AUTOMATION_SCHEMA)
    marker = next(
        k for k in cf.POSITION_SCHEMA.schema if str(k) == CONF_ENABLE_POSITION_MATCHING
    )
    assert marker.default() is False


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


def test_venetian_mode_in_geometry_venetian_schema() -> None:
    """CONF_VENETIAN_MODE must live on the venetian geometry step, not elsewhere."""
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    keys = _schema_keys(GEOMETRY_VENETIAN_SCHEMA)
    assert CONF_VENETIAN_MODE in keys


# ---------------------------------------------------------------------------
# Per-slot tilt slider — venetian only (Step 12)
# ---------------------------------------------------------------------------


def test_custom_position_schema_venetian_includes_tilt_slots() -> None:
    """_build_custom_position_schema_dict(sensor_type='cover_venetian') includes tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert slot_keys["tilt"] in keys, f"{slot_keys['tilt']} missing for venetian"


def test_custom_position_schema_blind_excludes_tilt_slots() -> None:
    """_build_custom_position_schema_dict(sensor_type='cover_blind') must not include tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt"] not in keys
        ), f"{slot_keys['tilt']} should not be in blind"


def test_custom_position_schema_awning_excludes_tilt_slots() -> None:
    """Awning schema must not include tilt keys."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.AWNING)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt"] not in keys
        ), f"{slot_keys['tilt']} should not be in awning"


# ---------------------------------------------------------------------------
# Per-slot tilt-only boolean — venetian only (issue #514)
# ---------------------------------------------------------------------------


def test_custom_position_schema_venetian_includes_tilt_only_slots() -> None:
    """Venetian schema includes the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] in keys
        ), f"{slot_keys['tilt_only']} missing for venetian"


def test_custom_position_schema_blind_excludes_tilt_only_slots() -> None:
    """Blind schema must not include the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] not in keys
        ), f"{slot_keys['tilt_only']} should not be in blind"


def test_custom_position_schema_awning_excludes_tilt_only_slots() -> None:
    """Awning schema must not include the per-slot tilt_only boolean."""
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SLOTS,
        CoverType,
    )

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.AWNING)
    keys = {str(k) for k in schema}
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        assert (
            slot_keys["tilt_only"] not in keys
        ), f"{slot_keys['tilt_only']} should not be in awning"


# ---------------------------------------------------------------------------
# Default/sunset tilt sliders — venetian only (Step 13)
# ---------------------------------------------------------------------------


def test_default_tilt_in_venetian_custom_position_schema() -> None:
    """CONF_DEFAULT_TILT must be in the venetian custom_position schema."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    assert CONF_DEFAULT_TILT in keys, "default_tilt missing from venetian schema"


def test_sunset_tilt_in_venetian_custom_position_schema() -> None:
    """CONF_SUNSET_TILT must be in the venetian custom_position schema."""
    from custom_components.adaptive_cover_pro.const import CONF_SUNSET_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.VENETIAN)
    keys = {str(k) for k in schema}
    assert CONF_SUNSET_TILT in keys, "sunset_tilt missing from venetian schema"


def test_default_tilt_absent_in_blind_schema() -> None:
    """CONF_DEFAULT_TILT must not appear in the blind schema."""
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    assert CONF_DEFAULT_TILT not in keys, "default_tilt should not be in blind schema"


def test_sunset_tilt_absent_in_blind_schema() -> None:
    """CONF_SUNSET_TILT must not appear in the blind schema."""
    from custom_components.adaptive_cover_pro.const import CONF_SUNSET_TILT, CoverType

    schema = cf._build_custom_position_schema_dict(sensor_type=CoverType.BLIND)
    keys = {str(k) for k in schema}
    assert CONF_SUNSET_TILT not in keys, "sunset_tilt should not be in blind schema"
