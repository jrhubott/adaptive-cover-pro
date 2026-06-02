"""Contract tests for ``const.OPTION_RANGES``.

The numeric ``(min, max)`` for each option lives in one place
(``const.OPTION_RANGES``) and is consumed by both the programmatic validator
in ``services/options_service.py`` and the UI selectors in ``config_flow.py``.

These tests pin the contract so a future change to a range tightens (or
loosens) both consumers in one edit.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_DEFAULT_TILT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_FOV_LEFT,
    CONF_HEIGHT_WIN,
    CONF_MOTION_TIMEOUT,
    CONF_POSITION_TOLERANCE,
    CONF_SUNSET_TILT,
    CUSTOM_POSITION_SLOTS,
    OPTION_RANGES,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    FIELD_VALIDATORS,
)


@pytest.mark.unit
def test_option_ranges_covers_every_numeric_validator() -> None:
    """Every numeric ``FIELD_VALIDATORS`` entry has a matching ``OPTION_RANGES`` row.

    A field-validator that uses ``_range(key)`` will already crash at import
    time if the key isn't in ``OPTION_RANGES`` — but a future contributor who
    adds a ``_num(min, max)`` literal back into ``FIELD_VALIDATORS`` would
    silently bypass the dedup. This test catches that regression by spot-
    checking the keys we know are numeric.
    """
    expected_numeric = {
        CONF_HEIGHT_WIN,
        CONF_AZIMUTH,
        CONF_FOV_LEFT,
        CONF_DELTA_POSITION,
        CONF_DELTA_TIME,
        CONF_MOTION_TIMEOUT,
    }
    for key in expected_numeric:
        assert key in OPTION_RANGES, f"{key} should be in OPTION_RANGES"
        assert key in FIELD_VALIDATORS, f"{key} should be in FIELD_VALIDATORS"


@pytest.mark.unit
def test_position_tolerance_range_registered() -> None:
    """``CONF_POSITION_TOLERANCE`` is registered with range (0, 20) (issue #507)."""
    assert CONF_POSITION_TOLERANCE in OPTION_RANGES
    assert OPTION_RANGES[CONF_POSITION_TOLERANCE] == (0, 20)


@pytest.mark.unit
def test_option_ranges_min_max_well_formed() -> None:
    """Every ``OPTION_RANGES`` entry has ``min < max`` and both numeric."""
    for key, (min_val, max_val) in OPTION_RANGES.items():
        assert isinstance(min_val, int | float), f"{key} min not numeric: {min_val!r}"
        assert isinstance(max_val, int | float), f"{key} max not numeric: {max_val!r}"
        assert min_val < max_val, f"{key}: min={min_val} not < max={max_val}"


@pytest.mark.unit
def test_field_validator_accepts_min_and_max_for_each_range() -> None:
    """Each numeric validator accepts the boundary values from its range.

    A boundary that gets rejected would mean the validator was built with
    a different (min, max) than ``OPTION_RANGES`` declares — exactly the
    drift this dedup is preventing.
    """
    for key, (min_val, max_val) in OPTION_RANGES.items():
        validator = FIELD_VALIDATORS[key]
        # Both endpoints must validate; a rejected boundary signals drift.
        validator(min_val)
        validator(max_val)


@pytest.mark.unit
def test_field_validator_rejects_just_outside_range() -> None:
    """Each numeric validator rejects a value just past the declared max.

    Pin the upper boundary as a proxy for "the validator is actually built
    from the same range" — a validator constructed with a wider max would
    silently accept an out-of-spec value here.
    """
    for key, (_, max_val) in OPTION_RANGES.items():
        validator = FIELD_VALIDATORS[key]
        # Pick a value 10% outside the max (or +1 if max is 0). For
        # zero-width-tolerance integer ranges, +1 is enough; floats need a
        # delta proportional to the magnitude.
        delta = max(1, abs(max_val) * 0.1)
        with pytest.raises(vol.Invalid):
            validator(max_val + delta + 0.001)


@pytest.mark.unit
def test_custom_position_slots_have_ranges() -> None:
    """Each of the four custom-position slots routes through ``OPTION_RANGES``.

    Generated via a comprehension in ``options_service.FIELD_VALIDATORS``, so
    a slot mismatch would only show up at validation time.
    """
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        for sub in ("position", "priority"):
            key = slot_keys[sub]
            assert (
                key in OPTION_RANGES
            ), f"{key} ({sub} slot) missing from OPTION_RANGES"


@pytest.mark.unit
def test_custom_position_tilt_slots_have_ranges() -> None:
    """Each of the four custom-position slots has a tilt range in OPTION_RANGES."""
    for n, slot_keys in CUSTOM_POSITION_SLOTS.items():
        key = slot_keys["tilt"]
        assert key in OPTION_RANGES, f"{key} (tilt slot {n}) missing from OPTION_RANGES"
        assert OPTION_RANGES[key] == (0, 100), f"{key} range should be (0, 100)"


@pytest.mark.unit
def test_default_tilt_in_option_ranges() -> None:
    """CONF_DEFAULT_TILT must appear in OPTION_RANGES with (0, 100)."""
    assert CONF_DEFAULT_TILT in OPTION_RANGES, "default_tilt missing from OPTION_RANGES"
    assert OPTION_RANGES[CONF_DEFAULT_TILT] == (0, 100)


@pytest.mark.unit
def test_sunset_tilt_in_option_ranges() -> None:
    """CONF_SUNSET_TILT must appear in OPTION_RANGES with (0, 100)."""
    assert CONF_SUNSET_TILT in OPTION_RANGES, "sunset_tilt missing from OPTION_RANGES"
    assert OPTION_RANGES[CONF_SUNSET_TILT] == (0, 100)
