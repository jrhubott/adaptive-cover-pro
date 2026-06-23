"""Tests for ``CoverTypePolicy.more_protective_position``.

The polymorphic comparator the anticipation helper (issue #616) folds future
sun-tracked samples through. "More protective" = "blocks more direct sun", which
is cover-type dependent:

  - blind / tilt / venetian (``open_blocks_sun=False``) → lower % = more coverage
  - awning (``open_blocks_sun=True``)                    → higher % = more coverage

The direction lives entirely on ``axes[0].open_blocks_sun`` so no cover-type
string branch or hardcoded min/max leaks outside ``cover_types/``.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy

# Cover types whose primary axis closes (lower %) to block the sun.
_LOWER_IS_PROTECTIVE = ["cover_blind", "cover_tilt", "cover_venetian"]


@pytest.mark.unit
@pytest.mark.parametrize("cover_type", _LOWER_IS_PROTECTIVE)
def test_lower_percentage_is_more_protective(cover_type: str) -> None:
    policy = get_policy(cover_type)
    assert policy.more_protective_position(30, 70) == 30
    # Argument order must not matter.
    assert policy.more_protective_position(70, 30) == 30


@pytest.mark.unit
def test_higher_percentage_is_more_protective_for_awning() -> None:
    policy = get_policy("cover_awning")
    assert policy.more_protective_position(30, 70) == 70
    assert policy.more_protective_position(70, 30) == 70


@pytest.mark.unit
@pytest.mark.parametrize(
    "cover_type", ["cover_blind", "cover_awning", "cover_tilt", "cover_venetian"]
)
def test_equal_values_are_idempotent(cover_type: str) -> None:
    assert get_policy(cover_type).more_protective_position(50, 50) == 50
