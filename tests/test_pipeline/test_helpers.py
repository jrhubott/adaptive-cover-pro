"""Tests for pipeline shared helper functions.

Covers apply_snapshot_limits, compute_solar_position, compute_default_position,
and compute_raw_calculated_position.
"""

from __future__ import annotations

from types import SimpleNamespace


from custom_components.adaptive_cover_pro.pipeline.helpers import (
    apply_snapshot_limits,
    compute_default_position,
    compute_raw_calculated_position,
    compute_solar_position,
)


# ---------------------------------------------------------------------------
# Minimal snapshot / cover helpers
# ---------------------------------------------------------------------------

def _make_config(*, min_pos=None, max_pos=None, min_pos_sun_only=False, max_pos_sun_only=False):
    return SimpleNamespace(
        min_pos=min_pos,
        max_pos=max_pos,
        min_pos_sun_only=min_pos_sun_only,
        max_pos_sun_only=max_pos_sun_only,
    )


def _make_cover(*, direct_sun_valid=True, calc_pct=50.0):
    cover = SimpleNamespace(
        direct_sun_valid=direct_sun_valid,
    )
    cover.calculate_percentage = lambda: calc_pct
    return cover


def _make_snapshot(*, calc_pct=50.0, default_position=30, direct_sun_valid=True,
                   min_pos=None, max_pos=None,
                   min_pos_sun_only=False, max_pos_sun_only=False,
                   is_sunset_active=False):
    return SimpleNamespace(
        cover=_make_cover(direct_sun_valid=direct_sun_valid, calc_pct=calc_pct),
        config=_make_config(
            min_pos=min_pos,
            max_pos=max_pos,
            min_pos_sun_only=min_pos_sun_only,
            max_pos_sun_only=max_pos_sun_only,
        ),
        default_position=default_position,
        is_sunset_active=is_sunset_active,
    )


# ---------------------------------------------------------------------------
# apply_snapshot_limits
# ---------------------------------------------------------------------------


class TestApplySnapshotLimits:
    """Tests for apply_snapshot_limits."""

    def test_no_limits_returns_value(self):
        """Value passes through unchanged when no limits configured."""
        snap = _make_snapshot()
        assert apply_snapshot_limits(snap, 50, sun_valid=True) == 50

    def test_max_limit_applied(self):
        """Max limit clamps the value down."""
        snap = _make_snapshot(max_pos=80, max_pos_sun_only=False)
        assert apply_snapshot_limits(snap, 90, sun_valid=True) == 80

    def test_min_limit_applied(self):
        """Min limit clamps the value up."""
        snap = _make_snapshot(min_pos=20, min_pos_sun_only=False)
        assert apply_snapshot_limits(snap, 10, sun_valid=True) == 20

    def test_sun_only_limit_not_applied_when_sun_invalid(self):
        """Sun-only limits are not enforced when sun is not valid."""
        snap = _make_snapshot(min_pos=30, min_pos_sun_only=True)
        # When sun_valid=False, sun-only min limit must NOT be applied
        assert apply_snapshot_limits(snap, 5, sun_valid=False) == 5

    def test_sun_only_limit_applied_when_sun_valid(self):
        """Sun-only limits are enforced when sun is valid."""
        snap = _make_snapshot(min_pos=30, min_pos_sun_only=True)
        assert apply_snapshot_limits(snap, 5, sun_valid=True) == 30

    def test_clips_to_100(self):
        """Values above 100 are clipped to 100."""
        snap = _make_snapshot()
        assert apply_snapshot_limits(snap, 150, sun_valid=True) == 100

    def test_clips_to_0(self):
        """Negative values are clipped to 0."""
        snap = _make_snapshot()
        assert apply_snapshot_limits(snap, -10, sun_valid=True) == 0


# ---------------------------------------------------------------------------
# compute_solar_position
# ---------------------------------------------------------------------------


class TestComputeSolarPosition:
    """Tests for compute_solar_position."""

    def test_basic_calculation(self):
        """Returns calculate_percentage result (rounded, floored at 1)."""
        snap = _make_snapshot(calc_pct=65.4)
        assert compute_solar_position(snap) == 65

    def test_floors_at_1(self):
        """Result is never 0 — floored to 1 to prevent open/close-only covers closing."""
        snap = _make_snapshot(calc_pct=0.2)
        assert compute_solar_position(snap) == 1

    def test_applies_max_limit(self):
        """Max position limit is applied to solar result."""
        snap = _make_snapshot(calc_pct=90, max_pos=70)
        assert compute_solar_position(snap) == 70

    def test_applies_min_limit(self):
        """Min position limit is applied to solar result."""
        snap = _make_snapshot(calc_pct=5, min_pos=20)
        assert compute_solar_position(snap) == 20

    def test_rounding(self):
        """Float result is rounded to nearest integer before floor."""
        snap = _make_snapshot(calc_pct=45.7)
        assert compute_solar_position(snap) == 46

    def test_exactly_zero_floors_to_one(self):
        """Exactly 0% from calculate_percentage becomes 1%."""
        snap = _make_snapshot(calc_pct=0.0)
        assert compute_solar_position(snap) == 1


# ---------------------------------------------------------------------------
# compute_default_position
# ---------------------------------------------------------------------------


class TestComputeDefaultPosition:
    """Tests for compute_default_position."""

    def test_returns_default_when_no_limits(self):
        """Returns snapshot.default_position when no limits configured."""
        snap = _make_snapshot(default_position=40)
        assert compute_default_position(snap) == 40

    def test_applies_max_limit(self):
        """Max limit applied to default position."""
        snap = _make_snapshot(default_position=90, max_pos=70)
        assert compute_default_position(snap) == 70

    def test_sun_only_limits_not_applied_when_sun_invalid(self):
        """Sun-only limits do NOT clamp default position when sun is not valid."""
        snap = _make_snapshot(
            default_position=5,
            min_pos=30,
            min_pos_sun_only=True,
            direct_sun_valid=False,
        )
        # Sun is invalid → sun-only min limit must NOT be applied
        assert compute_default_position(snap) == 5

    def test_sun_only_limits_applied_when_sun_valid(self):
        """Sun-only limits ARE applied when sun is valid (edge case in some handlers)."""
        snap = _make_snapshot(
            default_position=5,
            min_pos=30,
            min_pos_sun_only=True,
            direct_sun_valid=True,
        )
        assert compute_default_position(snap) == 30

    def test_sunset_active_skips_min_limit(self):
        """Sunset position is not clamped by min_pos even when always-apply is set (#128)."""
        snap = _make_snapshot(
            default_position=0,
            min_pos=50,
            min_pos_sun_only=False,  # always apply — but sunset exempts it
            is_sunset_active=True,
        )
        assert compute_default_position(snap) == 0

    def test_sunset_active_skips_max_limit(self):
        """Sunset position is not clamped by max_pos (e.g. sunset_pos=100, max=80)."""
        snap = _make_snapshot(
            default_position=100,
            max_pos=80,
            max_pos_sun_only=False,
            is_sunset_active=True,
        )
        assert compute_default_position(snap) == 100

    def test_sunset_inactive_still_applies_min_limit(self):
        """Normal default position (not sunset) still respects min_pos. Regression guard."""
        snap = _make_snapshot(
            default_position=0,
            min_pos=50,
            min_pos_sun_only=False,
            is_sunset_active=False,
        )
        assert compute_default_position(snap) == 50


# ---------------------------------------------------------------------------
# compute_raw_calculated_position
# ---------------------------------------------------------------------------


class TestComputeRawCalculatedPosition:
    """Tests for compute_raw_calculated_position."""

    def test_returns_solar_when_sun_valid(self):
        """Returns compute_solar_position result when direct_sun_valid is True."""
        snap = _make_snapshot(calc_pct=70, direct_sun_valid=True)
        result = compute_raw_calculated_position(snap)
        assert result == compute_solar_position(snap)
        assert result == 70

    def test_returns_default_when_sun_invalid(self):
        """Returns compute_default_position result when direct_sun_valid is False."""
        snap = _make_snapshot(default_position=25, direct_sun_valid=False)
        result = compute_raw_calculated_position(snap)
        assert result == compute_default_position(snap)
        assert result == 25

    def test_solar_floors_at_1(self):
        """Solar path floors at 1 (via compute_solar_position)."""
        snap = _make_snapshot(calc_pct=0.0, direct_sun_valid=True)
        assert compute_raw_calculated_position(snap) == 1

    def test_limits_applied_in_solar_path(self):
        """Max limit is applied in the solar path."""
        snap = _make_snapshot(calc_pct=95, direct_sun_valid=True, max_pos=80)
        assert compute_raw_calculated_position(snap) == 80

    def test_limits_applied_in_default_path(self):
        """Max limit is applied in the default path."""
        snap = _make_snapshot(default_position=95, direct_sun_valid=False, max_pos=80)
        assert compute_raw_calculated_position(snap) == 80

    def test_sunset_active_skips_limits_in_default_path(self):
        """Sunset position bypasses min/max limits in the default path (#128)."""
        snap = _make_snapshot(
            default_position=0,
            min_pos=50,
            min_pos_sun_only=False,
            direct_sun_valid=False,
            is_sunset_active=True,
        )
        assert compute_raw_calculated_position(snap) == 0
