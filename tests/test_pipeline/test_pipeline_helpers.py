"""Tests for pipeline shared helper functions.

Covers apply_snapshot_limits, compute_solar_position, compute_default_position,
and compute_raw_calculated_position.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.adaptive_cover_pro.pipeline.helpers import (
    SOLAR_TRACKING_FLOOR_PCT,
    anticipated_solar_position,
    apply_snapshot_limits,
    compute_default_position,
    compute_raw_calculated_position,
    compute_solar_position,
    solar_floor,
    solar_position_from_geometry,
)
from tests.cover_helpers import attach_coverage_rounding, build_louvered_roof_cover

# ---------------------------------------------------------------------------
# Minimal snapshot / cover helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    min_pos=None,
    max_pos=None,
    min_pos_sun_only=False,
    max_pos_sun_only=False,
    min_pos_sun_tracking=None,
):
    return SimpleNamespace(
        min_pos=min_pos,
        max_pos=max_pos,
        min_pos_sun_only=min_pos_sun_only,
        max_pos_sun_only=max_pos_sun_only,
        min_pos_sun_tracking=min_pos_sun_tracking,
    )


def _make_cover(*, direct_sun_valid=True, calc_pct=50.0):
    cover = SimpleNamespace(
        direct_sun_valid=direct_sun_valid,
    )
    cover.calculate_percentage = lambda: int(round(calc_pct))
    cover.calculate_raw_percentage = lambda: calc_pct
    return cover


def _make_snapshot(
    *,
    calc_pct=50.0,
    default_position=30,
    direct_sun_valid=True,
    min_pos=None,
    max_pos=None,
    min_pos_sun_only=False,
    max_pos_sun_only=False,
    min_pos_sun_tracking=None,
    is_sunset_active=False,
    enable_sun_tracking=True,
    solar_floor_active=True,
):
    return SimpleNamespace(
        cover=_make_cover(direct_sun_valid=direct_sun_valid, calc_pct=calc_pct),
        config=_make_config(
            min_pos=min_pos,
            max_pos=max_pos,
            min_pos_sun_only=min_pos_sun_only,
            max_pos_sun_only=max_pos_sun_only,
            min_pos_sun_tracking=min_pos_sun_tracking,
        ),
        default_position=default_position,
        is_sunset_active=is_sunset_active,
        enable_sun_tracking=enable_sun_tracking,
        solar_floor_active=solar_floor_active,
    )


# ---------------------------------------------------------------------------
# apply_snapshot_limits
# ---------------------------------------------------------------------------


class TestApplySnapshotLimits:
    """Tests for apply_snapshot_limits."""

    def test_uses_sun_tracking_min_when_set_and_sun_valid(self):
        """When CoverConfig.min_pos_sun_tracking is set, sun_valid=True paths use it."""
        snap = _make_snapshot(min_pos=0, min_pos_sun_tracking=15)
        assert apply_snapshot_limits(snap, value=5, sun_valid=True) == 15
        assert apply_snapshot_limits(snap, value=5, sun_valid=False) == 5

    def test_suppress_sun_tracking_min_threads_through(self):
        """suppress_sun_tracking_min=True bypasses the sun floor (issue #689)."""
        snap = _make_snapshot(min_pos=0, min_pos_sun_tracking=15)
        # Floor honored by default.
        assert apply_snapshot_limits(snap, value=5, sun_valid=True) == 15
        # Suppressed → falls back to min_pos=0 → raw 5 reaches through.
        assert (
            apply_snapshot_limits(
                snap, value=5, sun_valid=True, suppress_sun_tracking_min=True
            )
            == 5
        )

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
# solar_floor — the named, capability-gated floor primitive (#569)
# ---------------------------------------------------------------------------


class TestSolarFloor:
    """The single source of truth for the solar-tracking 1 % floor."""

    def test_constant_is_one(self):
        assert SOLAR_TRACKING_FLOOR_PCT == 1

    def test_floors_zero_when_active(self):
        assert solar_floor(0, floor_active=True) == 1

    def test_passes_through_above_floor_when_active(self):
        assert solar_floor(40, floor_active=True) == 40

    def test_reaches_zero_when_inactive(self):
        assert solar_floor(0, floor_active=False) == 0

    def test_passes_through_above_floor_when_inactive(self):
        assert solar_floor(40, floor_active=False) == 40


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
        snap = _make_snapshot(calc_pct=0.2, solar_floor_active=True)
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
        """Exactly 0% from calculate_percentage becomes 1% when the floor is active."""
        snap = _make_snapshot(calc_pct=0.0, solar_floor_active=True)
        assert compute_solar_position(snap) == 1

    def test_positionable_reaches_zero(self):
        """Set-position-capable instance (floor off) reaches a true 0% (#569)."""
        snap = _make_snapshot(calc_pct=0.0, solar_floor_active=False)
        assert compute_solar_position(snap) == 0

    def test_positionable_low_value_not_floored(self):
        """A sub-1% geometry rounds to 0 and is NOT floored when positionable (#569)."""
        snap = _make_snapshot(calc_pct=0.4, solar_floor_active=False)
        assert compute_solar_position(snap) == 0

    def test_solar_position_from_geometry_survives_fractional_louvered_denominator(
        self,
    ):
        """A sub-1° ``max_slat_angle`` must not crash the pipeline seam (#1105).

        Locks the observable behavior at the exact "propagates uncaught out of
        pipeline/helpers.py" path the issue describes: this calls
        ``solar_position_from_geometry`` directly against a real
        ``AdaptiveLouveredRoofCover`` configured with ``max_slat_angle=0.4`` — a
        value the old ``int()`` truncation in ``_effective_max_degrees()``
        collapsed onto a literal ``0`` denominator, raising
        ``ZeroDivisionError`` uncaught on the tilt-only solar branch. At this
        geometry the raw angle (180°) saturates against the 0.4° ceiling, so
        the concrete answer is pinned at 100 rather than merely "some int in
        range" — verified empirically, not assumed.
        """
        cover = build_louvered_roof_cover(
            sol_azi=180, sol_elev=65, roof_pitch=0.0, max_slat_angle=0.4
        )
        policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])

        result = solar_position_from_geometry(
            cover,
            _make_config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
        )

        assert result == 100


# ---------------------------------------------------------------------------
# snap_closed_below wiring in solar_position_from_geometry (issue #1379)
# ---------------------------------------------------------------------------


def _make_geometry_cover(*, raw_pct: float):
    """Build a minimal solar-tracking cover double with real coverage hooks.

    Binds the production ``round_toward_coverage`` / ``coverage_pivot_percentage``
    / ``coverage_travel_bounds`` implementations (monotonic base-class defaults:
    floor/ceil, no pivot, full [0, 100] band) so this exercises the same
    arithmetic the real engine uses, with a fully controllable raw percentage.
    """
    return attach_coverage_rounding(
        SimpleNamespace(calculate_raw_percentage=lambda: raw_pct)
    )


def _make_pivot_cover(*, round_result: int, pivot: float | None):
    """Build a cover double with a fully controllable ``round_toward_coverage``
    result and an explicit ``coverage_pivot_percentage()`` answer.

    Used to reproduce the tilt-axis snap-scoping bug directly: a real
    ``AdaptiveTiltCover`` MODE2 solve for a specific mirror-pair of
    near-horizontal percentages (49/51) is fragile to reverse-engineer from
    raw sun geometry, whereas the bug's entire mechanism is "does this cover
    report a non-``None`` pivot" — controlling that directly pins the exact
    reported failure without depending on unrelated geometry solving.
    """
    return SimpleNamespace(
        calculate_raw_percentage=lambda: float(round_result),
        round_toward_coverage=lambda pct, full_coverage_at_zero: round_result,  # noqa: ARG005
        coverage_pivot_percentage=lambda: pivot,
        coverage_travel_bounds=lambda: (0.0, 100.0),
    )


class TestSolarPositionFromGeometrySnapClosedBelow:
    """The snap step must run after quantize and before the floor/limit clamp."""

    def test_snap_runs_after_quantize_not_before(self):
        """Snap sees the QUANTIZED value, not the raw round_toward_coverage one.

        Raw geometry floors to 10 (a blind, full_coverage_at_zero=True) — on
        its own that is not inside the (0, 10) snap band, so a snap that ran
        BEFORE quantize would be a no-op and the result would stay at 10.
        Quantizing into 25 discrete coverage levels moves it to 8 (still
        nonzero), which the snap then collapses to 0 — proving the snap reads
        quantize's output.
        """
        cover = _make_geometry_cover(raw_pct=10.9)
        policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])

        result = solar_position_from_geometry(
            cover,
            _make_config(),
            minimize_movements=True,
            max_coverage_steps=25,
            policy=policy,
            floor_active=False,
            snap_closed_below=True,
            snap_closed_threshold=10,
        )

        assert result == 0

    def test_active_min_pos_floor_wins_over_a_snap_to_zero(self):
        """An active min_pos floor always wins over the snap (#472/#943 rule).

        The snap collapses a 2 % demand to 0, but ``apply_config_limits`` runs
        AFTER the snap and raises it back to the configured floor — the same
        floor-wins-on-conflict precedence ``clamp_to_bounds`` already
        guarantees (axis_constraints.py). This is the regression guard for
        the snap never undercutting an active minimum-position floor.
        """
        cover = _make_geometry_cover(raw_pct=2.5)
        policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])

        result = solar_position_from_geometry(
            cover,
            _make_config(min_pos=20),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
            snap_closed_below=True,
            snap_closed_threshold=10,
        )

        assert result == 20

    def test_disabled_by_default_is_a_no_op(self):
        """Omitting the new kwargs entirely preserves pre-#1379 behavior."""
        cover = _make_geometry_cover(raw_pct=2.5)
        policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])

        result = solar_position_from_geometry(
            cover,
            _make_config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
        )

        assert result == 2

    def test_snap_does_not_fire_on_a_bidirectional_tilt_axis(self):
        """The snap is scoped to the monotonic position axis only (audit fix).

        Drives the REAL ``TiltPolicy`` (``axes[0]`` is ``TILT_AXIS_PRIMARY``,
        same as it is for ``cover_louvered_roof``) through
        ``solar_position_from_geometry`` end-to-end. MODE2 puts the horizontal
        (least-covering) slat at a 50 % pivot; a solve of 49 sits one point
        short of horizontal — near-open, not near-closed. Before the fix,
        ``covered_fraction(49, open_blocks_sun=True) * 100 == 49 < 50``
        satisfied the naive (position-axis) band check and slammed the
        near-open slats fully shut. 51 — the mirror value, identically
        near-open on the other side of the pivot — was untouched, which is
        the asymmetry that gave the bug away. After the fix neither fires:
        the axis carries a real pivot (50.0, not ``None``), which is exactly
        the discriminator ``quantize_to_coverage_steps`` already uses one
        line above to detect a bi-directional axis.
        """
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        policy = get_policy("cover_tilt")

        for tilt_solve in (49, 51):
            cover = _make_pivot_cover(round_result=tilt_solve, pivot=50.0)
            result = solar_position_from_geometry(
                cover,
                _make_config(),
                minimize_movements=False,
                max_coverage_steps=1,
                policy=policy,
                floor_active=False,
                snap_closed_below=True,
                snap_closed_threshold=50,
            )
            assert result == tilt_solve

    def test_snap_still_fires_on_the_position_axis(self):
        """The tilt fix leaves the genuinely monotonic position axis alone.

        Same real-policy end-to-end shape as the tilt test above, but with
        ``cover_blind`` (``axes[0]`` is ``POSITION_AXIS``, whose base
        ``coverage_pivot_percentage()`` returns ``None``) — the snap must
        still collapse a barely-open demand to fully closed.
        """
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        policy = get_policy("cover_blind")
        cover = _make_pivot_cover(round_result=3, pivot=None)

        result = solar_position_from_geometry(
            cover,
            _make_config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
            snap_closed_below=True,
            snap_closed_threshold=10,
        )

        assert result == 0


class TestComputeSolarPositionSnapClosedBelow:
    """``compute_solar_position`` reads the snap fields off the snapshot."""

    def test_forwards_snap_fields_from_snapshot(self):
        snap = _make_snapshot(calc_pct=2.5, solar_floor_active=False)
        snap.policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])
        snap.cover = attach_coverage_rounding(
            SimpleNamespace(calculate_raw_percentage=lambda: 2.5)
        )
        snap.snap_closed_below = True
        snap.snap_closed_threshold = 10

        assert compute_solar_position(snap) == 0

    def test_absent_snap_fields_default_to_off(self):
        """A snapshot built before this feature existed stays byte-identical."""
        snap = _make_snapshot(calc_pct=2.5, solar_floor_active=False)
        snap.policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])
        snap.cover = attach_coverage_rounding(
            SimpleNamespace(calculate_raw_percentage=lambda: 2.5)
        )
        # snap_closed_below / snap_closed_threshold intentionally absent.

        assert compute_solar_position(snap) == 2


class TestAnticipatedSolarPositionSnapClosedBelow:
    """The anticipation adapter — what ``SolarHandler`` actually calls — must
    also honor the snap fields, since it is the primary live sun-tracking
    codepath (not ``compute_solar_position``, which other handlers use).
    """

    def test_forwards_snap_fields_from_snapshot(self):
        snap = _make_snapshot(calc_pct=2.5, solar_floor_active=False)
        snap.policy = SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=False)])
        snap.cover = attach_coverage_rounding(
            SimpleNamespace(calculate_raw_percentage=lambda: 2.5, direct_sun_valid=True)
        )
        snap.time_threshold_minutes = 0
        snap.snap_closed_below = True
        snap.snap_closed_threshold = 10

        assert anticipated_solar_position(snap) == 0


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

    def test_sun_only_limits_not_applied_to_default_even_when_sun_valid(self):
        """Sun-only limits are NOT applied to default position even when sun is geometrically valid.

        The default position is not a sun-tracking position, so sun-only
        limits should never constrain it — regardless of whether the sun
        happens to be in the FOV. (Regression test for cloud-suppression bug #105.)
        """
        snap = _make_snapshot(
            default_position=5,
            min_pos=30,
            min_pos_sun_only=True,
            direct_sun_valid=True,
        )
        assert compute_default_position(snap) == 5

    def test_cloud_suppression_scenario_max_not_clamped(self):
        """Regression #105: cloud suppression should not clamp default to sun-only max.

        User has default=50, max_pos=26 (sun-only). When cloud suppression
        is active, compute_default_position should return 50, not 26.
        """
        snap = _make_snapshot(
            default_position=50,
            max_pos=26,
            max_pos_sun_only=True,
            direct_sun_valid=True,
        )
        assert compute_default_position(snap) == 50

    def test_sun_only_min_not_applied_to_default(self):
        """Sun-only min limit should not raise the default position."""
        snap = _make_snapshot(
            default_position=5,
            min_pos=30,
            min_pos_sun_only=True,
            direct_sun_valid=True,
        )
        assert compute_default_position(snap) == 5

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

    def test_returns_default_when_sun_valid_but_tracking_disabled(self):
        """Returns default when direct_sun_valid=True but enable_sun_tracking=False.

        Regression test for #264: the raw baseline must reflect what the
        pipeline would actually command, not the solar geometry result.
        """
        snap = _make_snapshot(
            calc_pct=70,
            default_position=30,
            direct_sun_valid=True,
            enable_sun_tracking=False,
        )
        assert compute_raw_calculated_position(snap) == 30

    def test_min_mode_floor_uses_default_when_tracking_disabled(self):
        """Min-mode floor is measured against default, not solar, when tracking is off.

        Regression test for #264 core scenario: default=100, sun geometrically
        valid (solar=29), tracking off.  max(80, raw) must be max(80, 100)=100,
        not max(80, 29)=80.
        """
        snap = _make_snapshot(
            calc_pct=29,
            default_position=100,
            direct_sun_valid=True,
            enable_sun_tracking=False,
        )
        assert compute_raw_calculated_position(snap) == 100

    def test_tracking_enabled_still_returns_solar_when_sun_valid(self):
        """Regression guard: solar result unchanged when enable_sun_tracking=True."""
        snap = _make_snapshot(
            calc_pct=70,
            default_position=30,
            direct_sun_valid=True,
            enable_sun_tracking=True,
        )
        assert compute_raw_calculated_position(snap) == 70

    def test_tracking_disabled_default_path_applies_limits(self):
        """Default path applies max_pos when tracking is disabled."""
        snap = _make_snapshot(
            calc_pct=90,
            default_position=90,
            direct_sun_valid=True,
            enable_sun_tracking=False,
            max_pos=80,
        )
        assert compute_raw_calculated_position(snap) == 80
