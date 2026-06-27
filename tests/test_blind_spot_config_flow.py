"""Config-flow blind-spot multi-slot rendering + per-slot validation (#701)."""

from custom_components.adaptive_cover_pro.config_dynamic import blind_spot_schema


def _schema_keys(schema) -> set[str]:
    return {str(marker) for marker in schema.schema}


def test_schema_renders_all_slot_keys():
    schema = blind_spot_schema({"fov_left": 45, "fov_right": 45})
    keys = _schema_keys(schema)
    # Slot 1 legacy keys
    assert "blind_spot_left" in keys
    assert "blind_spot_right" in keys
    # Slots 2 and 3
    assert "blind_spot_left_2" in keys
    assert "blind_spot_right_2" in keys
    assert "blind_spot_left_3" in keys
    assert "blind_spot_right_3" in keys


def test_per_slot_left_right_errors():
    from custom_components.adaptive_cover_pro.config_flow import (
        _blind_spot_step_errors,
    )

    # Slot 2 right <= left → error keyed on right_2.
    errors = _blind_spot_step_errors(
        {"blind_spot_left_2": 30, "blind_spot_right_2": 20}
    )
    assert "blind_spot_right_2" in errors

    # Valid slot 2 → no error.
    assert (
        _blind_spot_step_errors({"blind_spot_left_2": 10, "blind_spot_right_2": 30})
        == {}
    )

    # Absent slot keys → no error.
    assert _blind_spot_step_errors({"blind_spot_left": 5}) == {}
