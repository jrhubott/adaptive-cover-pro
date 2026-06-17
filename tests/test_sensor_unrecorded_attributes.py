"""Regression guard for recorder exclusion of heavy sensor attributes.

The listed attribute keys are consumed live by the companion Lovelace card
and the live state UI — they have no recorder-history use case. HA reads
``_unrecorded_attributes`` at class init, so the spec-driven class composition
in ``sensor.py`` must surface each spec's frozenset on its resolved class.
"""

import pytest

from custom_components.adaptive_cover_pro.sensor import (
    _DIAGNOSTIC_CLASSES,
    _DIAGNOSTIC_SPECS,
    _STANDARD_CLASSES,
    _STANDARD_SPECS,
)

EXPECTED: dict[str, set[str]] = {
    "Cover_Position": {
        "actual_positions",
        "actual_distances",
        "position_explanation",
    },
    "control_status": {"manual_covers"},
    "decision_trace": {"trace", "custom_position_slots", "enabled_handlers"},
    "position_forecast": {"forecast", "events"},
    "position_verification": {"per_entity"},
}


def _resolved_cls(suffix: str) -> type:
    return _STANDARD_CLASSES.get(suffix) or _DIAGNOSTIC_CLASSES[suffix]


@pytest.mark.parametrize(("suffix", "keys"), list(EXPECTED.items()))
def test_unrecorded_attributes_present(suffix: str, keys: set[str]) -> None:
    cls = _resolved_cls(suffix)
    assert keys.issubset(
        cls._unrecorded_attributes
    ), f"{suffix}: expected {keys} ⊆ {set(cls._unrecorded_attributes)}"


def test_specs_with_unrecorded_attrs_propagate_to_classes() -> None:
    for spec in (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS):
        if not spec.unrecorded_attributes:
            continue
        cls = _resolved_cls(spec.suffix)
        assert spec.unrecorded_attributes.issubset(cls._unrecorded_attributes)


def test_specs_without_unrecorded_attrs_reuse_default_class() -> None:
    """Specs that don't opt in must still resolve to the original base class.

    Prevents the resolver from accidentally allocating fresh subclasses for
    every spec — that would defeat HA's class-cached state_info packing.
    """
    from custom_components.adaptive_cover_pro.sensor import (
        _ACPDiagnosticSensor,
        _ACPSensor,
        _SPEC_OVERRIDES,
    )

    for spec in _STANDARD_SPECS:
        if spec.unrecorded_attributes:
            continue
        assert _STANDARD_CLASSES[spec.suffix] is _ACPSensor

    for spec in _DIAGNOSTIC_SPECS:
        if spec.unrecorded_attributes:
            continue
        expected = _SPEC_OVERRIDES.get(spec.suffix, _ACPDiagnosticSensor)
        assert _DIAGNOSTIC_CLASSES[spec.suffix] is expected
