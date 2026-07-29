"""Tests for the lru_cache memoisation of the pure geometry functions.

``SafetyMarginCalculator.calculate`` and ``EdgeCaseHandler.check_and_handle``
delegate to module-level ``@lru_cache`` functions so identical sun angles are
computed once and shared across every cover/config entry. These tests pin the
numeric results (caching must not change output) and confirm the cache is hit.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.geometry import (
    EdgeCaseHandler,
    SafetyMarginCalculator,
    _edge_case,
    _safety_margin,
)

# ---------------------------------------------------------------------------
# Safety margin — pinned values (caching must not alter these)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("gamma", "sol_elev", "expected"),
    [
        (0.0, 45.0, 1.0),  # normal angles -> baseline
        (45.0, 45.0, 1.0),  # gamma at threshold (not >) -> baseline
        (90.0, 45.0, 1.2),  # extreme gamma -> +0.20 (smoothstep = 1 at 90°)
        (0.0, 0.0, 1.15),  # low elevation -> +0.15
        (0.0, 90.0, 1.10),  # high elevation -> +0.10
    ],
)
def test_safety_margin_values(gamma, sol_elev, expected):
    assert SafetyMarginCalculator.calculate(gamma, sol_elev) == pytest.approx(expected)


@pytest.mark.unit
def test_safety_margin_delegates_to_cached_function():
    assert SafetyMarginCalculator.calculate(12.3, 33.0) == _safety_margin(12.3, 33.0)


@pytest.mark.unit
def test_safety_margin_is_cached():
    _safety_margin.cache_clear()
    SafetyMarginCalculator.calculate(60.0, 20.0)
    SafetyMarginCalculator.calculate(60.0, 20.0)  # same args -> cache hit
    assert _safety_margin.cache_info().hits >= 1


@pytest.mark.unit
def test_safety_margin_distinct_keys_distinct_results():
    _safety_margin.cache_clear()
    a = SafetyMarginCalculator.calculate(90.0, 45.0)
    b = SafetyMarginCalculator.calculate(0.0, 0.0)
    assert a != b
    # Two distinct keys cached, no cross-contamination.
    assert SafetyMarginCalculator.calculate(90.0, 45.0) == a
    assert SafetyMarginCalculator.calculate(0.0, 0.0) == b


# ---------------------------------------------------------------------------
# Edge case handler — all four branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_edge_case_low_elevation():
    assert EdgeCaseHandler.check_and_handle(1.0, 0.0, 2.0, 10.0) == (True, 0.0)


@pytest.mark.unit
def test_edge_case_extreme_gamma_not_handled():
    # Issue #600: extreme gamma above the low-sun floor is no longer an edge case.
    assert EdgeCaseHandler.check_and_handle(45.0, 90.0, 2.0, 10.0) == (False, 0.0)


@pytest.mark.unit
def test_edge_case_high_elevation_not_handled():
    # Issue #600: the redundant >88° branch was removed; normal path handles it.
    assert EdgeCaseHandler.check_and_handle(89.0, 0.0, 0.05, 10.0) == (False, 0.0)


@pytest.mark.unit
def test_edge_case_normal_returns_false():
    assert EdgeCaseHandler.check_and_handle(45.0, 0.0, 2.0, 10.0) == (False, 0.0)


@pytest.mark.unit
def test_edge_case_is_cached():
    _edge_case.cache_clear()
    EdgeCaseHandler.check_and_handle(45.0, 0.0, 2.0, 10.0)
    EdgeCaseHandler.check_and_handle(45.0, 0.0, 2.0, 10.0)
    assert _edge_case.cache_info().hits >= 1
