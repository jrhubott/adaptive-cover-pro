"""Tests for the manual-override duration MODE (issue #1044).

The hold can now run until the next sun event or the operating window's end
instead of a fixed clock duration. Covered here:

* ``resolve_override_deadline`` — the pure roll-forward deadline rule.
* ``AdaptiveCoverManager.expiry_for`` — the single end-time authority.
* ``reset_if_needed`` routed through it.
* The coordinator resolver that supplies the HA-side reads.
* The config surface, the sensor/diagnostics surfaces, and the restore path.
"""

from __future__ import annotations

import datetime as dt
import inspect
import zoneinfo
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from custom_components.adaptive_cover_pro.const import (
    CONF_MANUAL_OVERRIDE_DURATION_MODE,
    DEFAULT_MANUAL_OVERRIDE_DURATION_MODE,
    MANUAL_OVERRIDE_DURATION_MODE_FIXED,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
    MANUAL_OVERRIDE_DURATION_MODES,
)
from custom_components.adaptive_cover_pro.helpers import (
    resolve_override_deadline,
    resolve_sun_boundaries,
)
from custom_components.adaptive_cover_pro.sun import SunData

pytestmark = pytest.mark.unit

_NY = zoneinfo.ZoneInfo("America/New_York")


def _boundaries(
    *,
    sunset: dt.datetime,
    sunrise: dt.datetime,
    next_sunset: dt.datetime,
    next_sunrise: dt.datetime,
):
    """Build SunBoundaries from four naive-UTC astral instants."""
    sun_data = MagicMock()
    sun_data.sunset.return_value = sunset.replace(tzinfo=dt.UTC)
    sun_data.sunrise.return_value = sunrise.replace(tzinfo=dt.UTC)
    sun_data.next_sunset.return_value = next_sunset.replace(tzinfo=dt.UTC)
    sun_data.next_sunrise.return_value = next_sunrise.replace(tzinfo=dt.UTC)
    return resolve_sun_boundaries(sun_data)


def _summer_boundaries():
    """Return a typical mid-latitude summer day: sunrise 04:00, sunset 21:00 UTC."""
    return _boundaries(
        sunrise=dt.datetime(2026, 7, 2, 4, 0),
        sunset=dt.datetime(2026, 7, 2, 21, 0),
        next_sunrise=dt.datetime(2026, 7, 3, 4, 1),
        next_sunset=dt.datetime(2026, 7, 3, 20, 59),
    )


def _resolve(mode, anchor, *, boundaries=None, window_end=None):
    return resolve_override_deadline(
        mode, anchor, boundaries=boundaries, window_end_local_naive=window_end
    )


class TestResolveOverrideDeadline:
    """The pure deadline rule: first occurrence strictly after the anchor."""

    def test_fixed_mode_returns_none(self):
        """``fixed`` has no event to roll to — the caller falls back to the duration."""
        assert (
            _resolve(
                MANUAL_OVERRIDE_DURATION_MODE_FIXED,
                dt.datetime(2026, 7, 2, 14, 0),
                boundaries=_summer_boundaries(),
                window_end=dt.datetime(2026, 7, 2, 18, 0),
            )
            is None
        )

    def test_until_sunset_after_todays_sunset_rolls_to_tomorrow(self):
        """Engaged at 22:00 with sunset at 21:00 → tomorrow's sunset."""
        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
            dt.datetime(2026, 7, 2, 22, 0),
            boundaries=_summer_boundaries(),
        ) == dt.datetime(2026, 7, 3, 20, 59)

    def test_until_sunset_before_todays_sunset_uses_today(self):
        """Engaged at 14:00 with sunset at 21:00 → today's sunset."""
        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
            dt.datetime(2026, 7, 2, 14, 0),
            boundaries=_summer_boundaries(),
        ) == dt.datetime(2026, 7, 2, 21, 0)

    def test_until_sunrise_engaged_at_2200_rolls_to_tomorrow(self):
        """The privacy-at-dusk case: hold all night, release at dawn."""
        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE,
            dt.datetime(2026, 7, 2, 22, 0),
            boundaries=_summer_boundaries(),
        ) == dt.datetime(2026, 7, 3, 4, 1)

    def test_until_sunrise_engaged_at_0500_uses_today_short_hold(self):
        """Engaged an hour before sunrise → a deliberate 1-hour hold, not 25."""
        boundaries = _boundaries(
            sunrise=dt.datetime(2026, 12, 2, 6, 0),
            sunset=dt.datetime(2026, 12, 2, 16, 0),
            next_sunrise=dt.datetime(2026, 12, 3, 6, 1),
            next_sunset=dt.datetime(2026, 12, 3, 15, 59),
        )
        anchor = dt.datetime(2026, 12, 2, 5, 0)

        deadline = _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE, anchor, boundaries=boundaries
        )

        assert deadline == dt.datetime(2026, 12, 2, 6, 0)
        assert deadline - anchor == dt.timedelta(hours=1)

    def test_until_next_sun_event_picks_the_earlier_of_the_two(self):
        """min(next sunset, next sunrise) — whichever lands first after the anchor."""
        boundaries = _summer_boundaries()

        # 22:00 → tomorrow's sunrise (04:01) beats tomorrow's sunset (20:59).
        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
            dt.datetime(2026, 7, 2, 22, 0),
            boundaries=boundaries,
        ) == dt.datetime(2026, 7, 3, 4, 1)

        # 14:00 → today's sunset (21:00) beats tomorrow's sunrise (04:01).
        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
            dt.datetime(2026, 7, 2, 14, 0),
            boundaries=boundaries,
        ) == dt.datetime(2026, 7, 2, 21, 0)

    def test_deadline_is_strictly_after_the_anchor_never_equal(self):
        """An anchor landing exactly on the boundary rolls to the next one.

        Otherwise the override would expire on the same coordinator cycle that
        engaged it — a zero-length hold.
        """
        boundaries = _summer_boundaries()

        assert _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
            dt.datetime(2026, 7, 2, 21, 0),
            boundaries=boundaries,
        ) == dt.datetime(2026, 7, 3, 20, 59)

    def test_window_end_rolls_forward_by_whole_local_days(self):
        """The window end is a local wall-clock time; rolling happens in local space."""
        window_end = dt.datetime(2026, 7, 2, 18, 0)  # 18:00 EDT = 22:00 UTC

        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", _NY):
            before = _resolve(
                MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
                dt.datetime(2026, 7, 2, 12, 0),
                window_end=window_end,
            )
            after = _resolve(
                MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
                dt.datetime(2026, 7, 2, 23, 0),
                window_end=window_end,
            )

        assert before == dt.datetime(2026, 7, 2, 22, 0)
        assert after == dt.datetime(2026, 7, 3, 22, 0)

    def test_window_end_roll_is_dst_correct(self):
        """A roll across a DST start is 23 real hours, not 24."""
        # 21:00 on 2026-03-07 is EST (UTC-5) → 02:00 UTC on 03-08.
        # 21:00 on 2026-03-08 is EDT (UTC-4) → 01:00 UTC on 03-09.
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", _NY):
            deadline = _resolve(
                MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
                dt.datetime(2026, 3, 8, 2, 30),
                window_end=dt.datetime(2026, 3, 7, 21, 0),
            )

        assert deadline == dt.datetime(2026, 3, 9, 1, 0)

    def test_window_end_returns_none_when_no_window_configured(self):
        """No end_time and no end entity → unresolvable → fixed-duration fallback."""
        assert (
            _resolve(
                MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END,
                dt.datetime(2026, 7, 2, 12, 0),
                window_end=None,
            )
            is None
        )

    def test_sun_modes_return_none_without_boundaries(self):
        """Before the first coordinator cycle there is no SunData to resolve against."""
        for mode in (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET,
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE,
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
        ):
            assert _resolve(mode, dt.datetime(2026, 7, 2, 12, 0)) is None

    def test_polar_sentinels_bound_the_hold(self):
        """At polar latitudes SunData's sentinels keep the hold finite."""
        location = MagicMock()
        location.sunset.side_effect = ValueError("never sets")
        location.sunrise.side_effect = ValueError("never rises")
        sun_data = SunData(timezone="UTC", location=location, elevation=0)
        boundaries = resolve_sun_boundaries(sun_data)
        anchor = dt.datetime.now(dt.UTC).replace(tzinfo=None)

        deadline = _resolve(
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_NEXT_SUN_EVENT,
            anchor,
            boundaries=boundaries,
        )

        assert deadline is not None
        assert dt.timedelta(0) < deadline - anchor <= dt.timedelta(days=2)

    def test_default_mode_is_fixed(self):
        """An install that never touched the option keeps today's behaviour."""
        assert DEFAULT_MANUAL_OVERRIDE_DURATION_MODE == (
            MANUAL_OVERRIDE_DURATION_MODE_FIXED
        )


# ---------------------------------------------------------------------------
# expiry_for() — the single end-time authority
# ---------------------------------------------------------------------------


def _manager(covers=("cover.x",), *, reset_duration=None):
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration=reset_duration or {"hours": 2},
        logger=MagicMock(),
    )
    manager.add_covers(list(covers))
    return manager


def _state(last_updated: dt.datetime):
    """Minimal state double carrying only ``last_updated``."""
    return MagicMock(last_updated=last_updated)


class TestExpiryFor:
    """``expiry_for()`` replaces five hand-rolled copies of ``start + duration``."""

    def test_fixed_mode_matches_start_plus_duration_exactly(self):
        """With no resolver configured the answer is byte-identical to today's."""
        mgr = _manager(reset_duration={"hours": 2})
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)

        assert mgr.expiry_for("cover.x") == start + dt.timedelta(hours=2)

    def test_returns_none_for_untracked_entity(self):
        """A cover with no override has no end time."""
        mgr = _manager()
        assert mgr.expiry_for("cover.x") is None
        assert mgr.expiry_for("cover.never_heard_of_it") is None

    def test_pinned_expiry_wins_over_the_resolver(self):
        """An explicit service ``end_time`` outranks the configured mode."""
        mgr = _manager()
        resolver_end = dt.datetime(2026, 7, 3, 4, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: resolver_end)

        pinned = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
        mgr.engage_override("cover.x", end_time=pinned, duration=None, reason="service")

        assert abs((mgr.expiry_for("cover.x") - pinned).total_seconds()) < 1

    def test_resolver_none_falls_back_to_fixed_duration(self):
        """An unresolvable sun/window anchor never means "hold forever"."""
        mgr = _manager(reset_duration={"hours": 3})
        mgr.set_deadline_resolver(lambda _anchor: None)
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)

        assert mgr.expiry_for("cover.x") == start + dt.timedelta(hours=3)

    def test_resolver_receives_the_override_start_not_now(self):
        """Anchoring on ``now`` would make the deadline recede forever."""
        mgr = _manager()
        seen: list[dt.datetime] = []
        mgr.set_deadline_resolver(lambda anchor: seen.append(anchor) or None)
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)

        mgr.expiry_for("cover.x")
        mgr.expiry_for("cover.x")

        assert seen == [start, start]

    def test_reset_clears_both_dicts(self):
        """Expiry bookkeeping must not outlive the override it describes."""
        mgr = _manager()
        mgr.engage_override(
            "cover.x",
            end_time=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            duration=None,
            reason="service",
        )
        assert mgr.manual_control_expiry.get("cover.x") is not None

        mgr.reset("cover.x")

        assert "cover.x" not in mgr.manual_control_time
        assert "cover.x" not in mgr.manual_control_expiry
        assert mgr.expiry_for("cover.x") is None

    def test_naive_anchor_is_normalised_to_utc(self):
        """A naive stored start is interpreted as UTC, never left naive."""
        mgr = _manager(reset_duration={"hours": 2})
        naive = dt.datetime(2026, 7, 2, 12, 0)
        seen: list[dt.datetime] = []
        mgr.set_deadline_resolver(lambda anchor: seen.append(anchor) or None)
        mgr.set_last_updated("cover.x", _state(naive), allow_reset=True)

        expiry = mgr.expiry_for("cover.x")

        assert expiry == naive.replace(tzinfo=dt.UTC) + dt.timedelta(hours=2)
        assert expiry.tzinfo is not None
        assert seen == [naive.replace(tzinfo=dt.UTC)]

    def test_rearming_clears_a_stale_pin(self):
        """A fresh touch re-arms from the mode; it must not inherit an old pin."""
        mgr = _manager(reset_duration={"hours": 2})
        pinned = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
        mgr.engage_override("cover.x", end_time=pinned, duration=None, reason="service")

        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)

        assert "cover.x" not in mgr.manual_control_expiry
        assert mgr.expiry_for("cover.x") == start + dt.timedelta(hours=2)

    def test_allow_reset_false_does_not_extend_either_dict(self):
        """``allow_reset=False`` keeps the original start AND the original end."""
        mgr = _manager(reset_duration={"hours": 2})
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=False)
        first_expiry = mgr.expiry_for("cover.x")

        later = start + dt.timedelta(minutes=45)
        mgr.set_last_updated("cover.x", _state(later), allow_reset=False)

        assert mgr.manual_control_time["cover.x"] == start
        assert mgr.expiry_for("cover.x") == first_expiry

    def test_mark_user_command_does_not_extend_the_window(self):
        """Successive proxy drags keep the first start (``setdefault`` semantics)."""
        mgr = _manager(reset_duration={"hours": 2})
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)

        mgr.mark_user_command("cover.x", reason="proxy_managed")

        assert mgr.manual_control_time["cover.x"] == start
        assert mgr.expiry_for("cover.x") == start + dt.timedelta(hours=2)


class TestResetIfNeeded:
    """The expiry poll compares ``now`` against ``expiry_for()``, strictly."""

    async def test_fixed_mode_boundary_is_strictly_greater_not_gte(self):
        """At exactly ``start + duration`` the override still holds.

        The byte-identical translation of the legacy
        ``now - last_updated > reset_duration``.
        """
        mgr = _manager(reset_duration={"hours": 2})
        start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        mgr.set_last_updated("cover.x", _state(start), allow_reset=True)
        mgr.mark_manual_control("cover.x")

        with freeze_time("2026-07-02 14:00:00"):
            assert await mgr.reset_if_needed() == set()
        assert mgr.is_cover_manual("cover.x") is True

        with freeze_time("2026-07-02 14:00:01"):
            assert await mgr.reset_if_needed() == {"cover.x"}
        assert mgr.is_cover_manual("cover.x") is False

    async def test_sun_mode_does_not_expire_before_the_deadline(self):
        """A sun deadline outlives the numeric duration it replaces."""
        mgr = _manager(reset_duration={"hours": 2})
        sunset = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: sunset)
        mgr.set_last_updated(
            "cover.x", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.x")

        # 6 h in — three times the fixed duration, still held.
        with freeze_time("2026-07-02 18:00:00"):
            assert await mgr.reset_if_needed() == set()
        # And at the deadline itself, still held (strictly-after rule).
        with freeze_time("2026-07-02 21:00:00"):
            assert await mgr.reset_if_needed() == set()
        assert mgr.is_cover_manual("cover.x") is True

    async def test_sun_mode_expires_at_the_resolved_deadline(self):
        """Past the resolved deadline the override clears like any other."""
        mgr = _manager(reset_duration={"hours": 2})
        sunset = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: sunset)
        mgr.set_last_updated(
            "cover.x", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.x")

        with freeze_time("2026-07-02 21:00:01"):
            assert await mgr.reset_if_needed() == {"cover.x"}

        assert mgr.is_cover_manual("cover.x") is False
        assert "cover.x" not in mgr.manual_control_time


# ---------------------------------------------------------------------------
# Coordinator resolver — the HA-side half of the rule
# ---------------------------------------------------------------------------


def _coordinator(
    options: dict,
    *,
    sun_data=None,
    window_end: dt.datetime | None = None,
    time_entity_state: str | None = None,
):
    """Return a coordinator stub with only what ``_resolve_override_deadline`` reads."""
    from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock()
    coord.config_entry.options = options
    # The duration mode reaches the resolver through the per-cycle
    # ``RuntimeConfig`` mirror, never the options dict (issue #1051) — publish
    # it here exactly as ``_update_options`` does.
    coord.manual_override_duration_mode = RuntimeConfig.from_options(
        options
    ).manual_override.duration_mode
    coord.logger = MagicMock()
    coord._cover_data = None if sun_data is None else MagicMock(sun_data=sun_data)
    coord._time_mgr.end_time = window_end
    coord.hass.states.get.return_value = (
        None if time_entity_state is None else MagicMock(state=time_entity_state)
    )
    coord._resolve_override_deadline = (
        AdaptiveDataUpdateCoordinator._resolve_override_deadline.__get__(coord)
    )
    return coord


def _astral_sun_data(
    *,
    sunset=dt.datetime(2026, 7, 2, 21, 0),
    sunrise=dt.datetime(2026, 7, 2, 4, 0),
    next_sunset=dt.datetime(2026, 7, 3, 20, 59),
    next_sunrise=dt.datetime(2026, 7, 3, 4, 1),
):
    sun_data = MagicMock()
    sun_data.sunset.return_value = sunset.replace(tzinfo=dt.UTC)
    sun_data.sunrise.return_value = sunrise.replace(tzinfo=dt.UTC)
    sun_data.next_sunset.return_value = next_sunset.replace(tzinfo=dt.UTC)
    sun_data.next_sunrise.return_value = next_sunrise.replace(tzinfo=dt.UTC)
    return sun_data


class TestCoordinatorResolver:
    """The injected resolver performs every HA read the pure rule needs."""

    def test_resolver_honours_sunset_time_entity_override(self):
        """A configured sunset entity (#411/#415) replaces astral entirely."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_MANUAL_OVERRIDE_DURATION_MODE,
            CONF_SUNSET_TIME_ENTITY,
        )

        today = dt.date(2026, 7, 2)
        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                ),
                CONF_SUNSET_TIME_ENTITY: "sensor.my_sunset",
            },
            sun_data=_astral_sun_data(),
            time_entity_state="18:30:00",
        )

        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC),
            patch(
                "custom_components.adaptive_cover_pro.coordinator.dt_util.now",
                return_value=dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC),
            ),
        ):
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )

        # The entity says 18:30, not astral's 21:00.
        assert deadline == dt.datetime(
            today.year, today.month, today.day, 18, 30, tzinfo=dt.UTC
        )

    def test_resolver_applies_configured_offsets(self):
        """``sunset_offset`` shifts the deadline the same way it shifts the night position."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_MANUAL_OVERRIDE_DURATION_MODE,
            CONF_SUNSET_OFFSET,
        )

        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                ),
                CONF_SUNSET_OFFSET: -45,
            },
            sun_data=_astral_sun_data(),
        )

        deadline = coord._resolve_override_deadline(
            dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        )

        assert deadline == dt.datetime(2026, 7, 2, 20, 15, tzinfo=dt.UTC)

    def test_resolver_uses_window_end_from_time_window_manager(self):
        """``until_window_end`` reads the resolved end, not the raw option string.

        The raw option is still what says a window end *exists* — a non-``None``
        ``TimeWindowManager.end_time`` is only reachable when one of the two end
        keys is set — but the instant itself comes from the manager, entity
        resolution and all.
        """
        from custom_components.adaptive_cover_pro.const import (
            CONF_END_TIME,
            CONF_MANUAL_OVERRIDE_DURATION_MODE,
        )

        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END
                ),
                CONF_END_TIME: "17:00:00",
            },
            sun_data=_astral_sun_data(),
            window_end=dt.datetime(2026, 7, 2, 17, 0),
        )

        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC):
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )

        assert deadline == dt.datetime(2026, 7, 2, 17, 0, tzinfo=dt.UTC)

    def test_resolver_returns_none_when_window_end_unset(self):
        """No window configured → the numeric duration takes over."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_MANUAL_OVERRIDE_DURATION_MODE,
        )

        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END
                ),
            },
            sun_data=_astral_sun_data(),
            window_end=None,
        )

        assert (
            coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )
            is None
        )

    def test_resolver_returns_none_before_first_cycle_when_cover_data_is_none(self):
        """No SunData yet → fixed fallback now, correct deadline next cycle."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_MANUAL_OVERRIDE_DURATION_MODE,
        )

        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
                ),
            },
            sun_data=None,
        )

        assert (
            coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )
            is None
        )

    def test_resolver_short_circuits_fixed_mode_without_reading_hass(self):
        """The default mode must not pay for sun/window reads on every cycle."""
        coord = _coordinator({}, sun_data=_astral_sun_data())

        assert (
            coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )
            is None
        )
        coord.hass.states.get.assert_not_called()


# ---------------------------------------------------------------------------
# One reader owns the sunset/sunrise HA reads
# ---------------------------------------------------------------------------

_CED = "custom_components.adaptive_cover_pro.helpers.compute_effective_default"
# ONE live binding, and one patch target that reaches both consumers. The
# deadline path used to spread the four inputs itself through a
# ``coordinator``-side from-import, so proving both consumers moved together
# meant patching two namespaces. It now calls ``helpers.read_sun_boundaries``,
# which does that read as a module global (issue #1340), exactly as the
# effective-default path already did inside
# ``helpers._read_current_effective_default`` (issue #1055). A single patch here
# is therefore the whole proof.
_READ_BOUNDS_HELPERS = (
    "custom_components.adaptive_cover_pro.helpers._read_sun_boundary_options"
)


class TestSunBoundarySource:
    """Sunset must mean one instant per instance, not one per call site.

    The night-position boundary and the manual-override sun deadline both need
    the same four inputs (the two override entities and the two offsets). Two
    hand-rolled copies of that read block would let a later third input land on
    one site only.
    """

    def _bind(self, coord):
        """Bind the real ``_compute_current_effective_default`` onto a stub."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord._compute_current_effective_default = (
            AdaptiveDataUpdateCoordinator._compute_current_effective_default.__get__(
                coord
            )
        )
        return coord

    def test_patching_the_single_reader_moves_both_consumers(self):
        """Both paths must observe a patched reader — proof of one shared source."""
        from types import SimpleNamespace

        coord = self._bind(
            _coordinator(
                {
                    CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                        MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
                    )
                },
                sun_data=_astral_sun_data(),
            )
        )
        patched = SimpleNamespace(
            sunset_time=None,
            sunrise_time=dt.datetime(2026, 7, 2, 6, 30),
            sunset_off=0,
            sunrise_off=15,
        )

        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC),
            patch(_READ_BOUNDS_HELPERS, return_value=patched),
            patch(_CED, return_value=(0, False)) as ced,
        ):
            coord._compute_current_effective_default(
                {}, cover_data=MagicMock(sun_data=_astral_sun_data())
            )
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 3, 0, tzinfo=dt.UTC)
            )

        kwargs = ced.call_args.kwargs
        assert kwargs["sunrise_time"] == patched.sunrise_time
        assert kwargs["sunrise_off"] == patched.sunrise_off
        assert kwargs["sunset_time"] == patched.sunset_time
        assert kwargs["sunset_off"] == patched.sunset_off
        # 06:30 local (UTC in this test) + the 15-minute offset.
        assert deadline == dt.datetime(2026, 7, 2, 6, 45, tzinfo=dt.UTC)

    def test_both_paths_agree_on_the_resolved_sunrise_instant(self):
        """A sunrise entity plus a non-default offset lands identically on both."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_SUNRISE_OFFSET,
            CONF_SUNRISE_TIME_ENTITY,
        )

        options = {
            CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
            ),
            CONF_SUNRISE_TIME_ENTITY: "sensor.my_sunrise",
            CONF_SUNRISE_OFFSET: 20,
        }
        coord = self._bind(
            _coordinator(
                options, sun_data=_astral_sun_data(), time_entity_state="05:00:00"
            )
        )

        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC),
            patch(
                "custom_components.adaptive_cover_pro.coordinator.dt_util.now",
                return_value=dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC),
            ),
            patch(_CED, return_value=(0, False)) as ced,
        ):
            coord._compute_current_effective_default(
                options, cover_data=MagicMock(sun_data=_astral_sun_data())
            )
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 3, 0, tzinfo=dt.UTC)
            )

        kwargs = ced.call_args.kwargs
        assert kwargs["sunrise_time"] == dt.datetime(2026, 7, 2, 5, 0)
        assert kwargs["sunrise_off"] == 20
        # The entity's 05:00, not astral's 04:00, plus the same 20-minute offset.
        assert deadline == dt.datetime(2026, 7, 2, 5, 20, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# BLANK_TIME window end — the sentinel must read as "no window"
# ---------------------------------------------------------------------------


def _blank_end_coordinator(**overrides):
    """Coordinator whose window end is the unset sentinel ``"00:00:00"``.

    ``TimeWindowManager.end_time`` maps a static midnight end onto *tomorrow's*
    midnight, so the resolved value the resolver would otherwise consult
    advances a whole day at every local midnight.
    """
    from custom_components.adaptive_cover_pro.const import BLANK_TIME, CONF_END_TIME

    options = {
        CONF_MANUAL_OVERRIDE_DURATION_MODE: (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END
        ),
        CONF_END_TIME: BLANK_TIME,
    }
    options.update(overrides)
    return _coordinator(
        options,
        sun_data=_astral_sun_data(),
        # What TimeWindowManager hands back for a "00:00:00" static end.
        window_end=dt.datetime(2026, 7, 3, 0, 0),
    )


class TestBlankWindowEnd:
    """``BLANK_TIME`` is the project's UNSET sentinel — never a real deadline."""

    def test_resolver_returns_none_for_the_blank_time_sentinel(self):
        """No anchor at all → ``None`` → the numeric duration takes over."""
        coord = _blank_end_coordinator()

        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC):
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 10, 0, tzinfo=dt.UTC)
            )

        assert deadline is None

    def test_resolver_still_uses_a_genuinely_configured_end(self):
        """A real ``end_time`` is untouched by the sentinel guard."""
        from custom_components.adaptive_cover_pro.const import CONF_END_TIME

        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_WINDOW_END
                ),
                CONF_END_TIME: "18:00:00",
            },
            sun_data=_astral_sun_data(),
            window_end=dt.datetime(2026, 7, 2, 18, 0),
        )

        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC):
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )

        assert deadline == dt.datetime(2026, 7, 2, 18, 0, tzinfo=dt.UTC)

    def test_resolver_still_uses_a_configured_end_entity(self):
        """An end *entity* is a real window end even with a blank static value."""
        from custom_components.adaptive_cover_pro.const import CONF_END_ENTITY

        coord = _blank_end_coordinator(**{CONF_END_ENTITY: "input_datetime.window_end"})
        coord._time_mgr.end_time = dt.datetime(2026, 7, 2, 19, 0)

        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC):
            deadline = coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )

        assert deadline == dt.datetime(2026, 7, 2, 19, 0, tzinfo=dt.UTC)

    async def test_blank_end_override_expires_after_the_numeric_duration(self):
        """The ship-blocker: the hold must not suspend auto-control forever."""
        coord = _blank_end_coordinator()
        mgr = _manager(["cover.x"], reset_duration={"hours": 2})
        mgr.set_deadline_resolver(coord._resolve_override_deadline)
        mgr.set_last_updated(
            "cover.x", _state(dt.datetime(2026, 7, 2, 10, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.x")

        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC),
            freeze_time("2026-07-02 12:00:01"),
        ):
            assert await mgr.reset_if_needed() == {"cover.x"}

        assert mgr.is_cover_manual("cover.x") is False

    async def test_blank_end_override_holds_until_the_numeric_duration(self):
        """Falling back to ``fixed`` must not shorten the hold either."""
        coord = _blank_end_coordinator()
        mgr = _manager(["cover.x"], reset_duration={"hours": 2})
        mgr.set_deadline_resolver(coord._resolve_override_deadline)
        mgr.set_last_updated(
            "cover.x", _state(dt.datetime(2026, 7, 2, 10, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.x")

        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", dt.UTC),
            freeze_time("2026-07-02 11:59:59"),
        ):
            assert await mgr.reset_if_needed() == set()

        assert mgr.is_cover_manual("cover.x") is True


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------


class TestConfigSurface:
    """The option must be reachable, validated, and sync-covered."""

    def test_mode_key_is_in_manual_override_sync_category(self):
        """A schema key missing from SYNC_CATEGORIES fails the sync-coverage lock."""
        from custom_components.adaptive_cover_pro.config_flow import (
            MANUAL_OVERRIDE_SCHEMA,
            SYNC_CATEGORIES,
        )

        keys = {str(k) for k in MANUAL_OVERRIDE_SCHEMA.schema}
        assert CONF_MANUAL_OVERRIDE_DURATION_MODE in keys
        assert CONF_MANUAL_OVERRIDE_DURATION_MODE in SYNC_CATEGORIES["manual_override"]

    def test_mode_field_uses_select_selector_with_translation_key(self):
        """A LIST select keyed for translation, exactly like ``motion_timeout_mode``."""
        from homeassistant.helpers import selector

        from custom_components.adaptive_cover_pro.config_fields import (
            FIELD_SPECS,
            ValidatorKind,
        )

        spec = FIELD_SPECS[CONF_MANUAL_OVERRIDE_DURATION_MODE]
        assert spec.validator is ValidatorKind.SELECT
        assert spec.default == DEFAULT_MANUAL_OVERRIDE_DURATION_MODE
        assert tuple(spec.select_options) == MANUAL_OVERRIDE_DURATION_MODES

        built = spec.make_selector(None, {})
        assert isinstance(built, selector.SelectSelector)
        assert built.config["translation_key"] == CONF_MANUAL_OVERRIDE_DURATION_MODE
        assert built.config["mode"] == selector.SelectSelectorMode.LIST

    def test_absent_key_defaults_to_fixed(self):
        """An install that never saw the option keeps today's behaviour."""
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig

        rc = RuntimeConfig.from_options({})
        assert rc.manual_override.duration_mode == MANUAL_OVERRIDE_DURATION_MODE_FIXED

    def test_runtime_config_reads_a_configured_mode(self):
        from custom_components.adaptive_cover_pro.config_types import RuntimeConfig

        rc = RuntimeConfig.from_options(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                )
            }
        )
        assert rc.manual_override.duration_mode == (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
        )

    def test_field_validator_rejects_unknown_mode(self):
        import voluptuous as vol

        from custom_components.adaptive_cover_pro.services.options_service import (
            FIELD_VALIDATORS,
        )

        validator = FIELD_VALIDATORS[CONF_MANUAL_OVERRIDE_DURATION_MODE]
        for mode in MANUAL_OVERRIDE_DURATION_MODES:
            assert validator(mode) == mode
        with pytest.raises(vol.Invalid):
            validator("until_the_cows_come_home")

    def test_mode_is_a_runtime_mutable_manual_override_field(self):
        """``set_manual_override`` must accept the new field."""
        from custom_components.adaptive_cover_pro.services.options_service import (
            _SECTION_MANUAL_OVERRIDE,
        )

        assert CONF_MANUAL_OVERRIDE_DURATION_MODE in _SECTION_MANUAL_OVERRIDE


class TestSliceIsTheSingleModeSource:
    """``ManualOverrideSlice.duration_mode`` is the one read of the mode.

    Issue #1051 item 1: the slice field was declared and written but never
    read, while both runtime consumers went to ``config_entry.options``
    themselves — two sources for one value, contrary to the "read options once
    per cycle into ``RuntimeConfig``" rule. These pin the slice as the source
    and the coordinator mirror as the only way either consumer sees the mode.

    Each test deliberately sets a mirror that *contradicts* the options dict:
    a consumer that still reads options would return the options value and
    fail, which is what makes these tests about the wiring and not the value.
    """

    def test_update_options_publishes_the_mode_mirror(self):
        """``_update_options`` propagates the slice like every other mirror."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock()
        AdaptiveDataUpdateCoordinator._update_options(
            coord,
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                )
            },
        )

        assert coord.manual_override_duration_mode == (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
        )

    def test_mirror_is_seeded_before_the_first_update_cycle(self):
        """The end-time sensor can reach ``expiry_for`` before cycle 1.

        Same reason the venetian drift-reset mirrors are seeded in ``__init__``
        from ``_rc_attach``: an unseeded attribute is an ``AttributeError``, not
        a stale value.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        source = inspect.getsource(AdaptiveDataUpdateCoordinator.__init__)
        assert "self.manual_override_duration_mode" in source
        assert "_rc_attach.manual_override.duration_mode" in source

    def test_resolver_reads_the_mirror_not_the_options_dict(self):
        """``_resolve_override_deadline`` resolves off the slice mirror."""
        coord = _coordinator({}, sun_data=_astral_sun_data())
        coord.manual_override_duration_mode = MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET

        deadline = coord._resolve_override_deadline(
            dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
        )

        assert deadline == dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)

    def test_resolver_short_circuits_on_a_fixed_mirror(self):
        """A ``fixed`` mirror short-circuits even when options say otherwise."""
        coord = _coordinator(
            {
                CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                    MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                )
            },
            sun_data=_astral_sun_data(),
        )
        coord.manual_override_duration_mode = MANUAL_OVERRIDE_DURATION_MODE_FIXED

        assert (
            coord._resolve_override_deadline(
                dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
            )
            is None
        )

    def test_diagnostics_reports_the_mirror_not_the_options_dict(self):
        """The diagnostics block reports the slice mirror."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        mgr.set_last_updated(
            "cover.a", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.a")

        coord = MagicMock()
        coord.manager = mgr
        coord.config_entry.options = {}
        coord.manual_override_duration_mode = (
            MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE
        )

        with freeze_time("2026-07-02 13:00:00"):
            state = AdaptiveDataUpdateCoordinator._manual_override_diagnostics(coord)

        assert state["duration_mode"] == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNRISE


# ---------------------------------------------------------------------------
# Sensor / diagnostics / restore surfaces
# ---------------------------------------------------------------------------


class TestSurfaces:
    """Every surface reports the resolved deadline, not ``start + duration``."""

    def test_end_time_sensor_reports_the_resolved_sun_deadline(self):
        from custom_components.adaptive_cover_pro.sensor import (
            _manual_override_end_value,
        )

        mgr = _manager(["cover.a", "cover.b"], reset_duration={"hours": 2})
        sunset = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: sunset)
        for eid in ("cover.a", "cover.b"):
            mgr.set_last_updated(
                eid, _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
            )

        sensor = MagicMock()
        sensor.coordinator.manager = mgr

        assert _manual_override_end_value(sensor) == sunset

    def test_end_time_attrs_report_per_entity_resolved_deadlines(self):
        from custom_components.adaptive_cover_pro.sensor import (
            _manual_override_end_attrs,
        )

        mgr = _manager(["cover.a", "cover.b"], reset_duration={"hours": 2})
        pinned = dt.datetime(2026, 7, 2, 15, 30, tzinfo=dt.UTC)
        sunset = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: sunset)
        mgr.set_last_updated(
            "cover.a", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.restore_override("cover.b", pinned)

        sensor = MagicMock()
        sensor.coordinator.manager = mgr

        attrs = _manual_override_end_attrs(sensor)

        assert attrs["per_entity"] == {
            "cover.a": sunset.isoformat(),
            "cover.b": pinned.isoformat(),
        }

    def test_restore_pins_the_persisted_expiry_in_sun_mode(self):
        """The persisted absolute end survives; it is not re-derived through the inverse."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        mgr.set_deadline_resolver(
            lambda _anchor: dt.datetime(2026, 7, 3, 4, 0, tzinfo=dt.UTC)
        )
        expiry = dt.datetime.now(dt.UTC) + dt.timedelta(hours=5)

        mgr.restore_override("cover.a", expiry)

        assert mgr.is_cover_manual("cover.a") is True
        assert mgr.expiry_for("cover.a") == expiry
        # The derived start is still written for the diagnostics display.
        assert mgr.manual_control_time["cover.a"] == expiry - mgr.reset_duration

    def test_diagnostics_remaining_seconds_uses_resolved_deadline(self):
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        deadline = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: deadline)
        mgr.set_last_updated(
            "cover.a", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.a")

        with freeze_time("2026-07-02 20:00:00"):
            state = _build_manual_override_diagnostics(mgr, {})

        # 1 h left — not the 0 s a 2 h fixed duration would have reported.
        assert state["entries"]["cover.a"]["remaining_seconds"] == 3600

    def test_diagnostics_reports_duration_mode_and_expires_at(self):
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        deadline = dt.datetime(2026, 7, 2, 21, 0, tzinfo=dt.UTC)
        mgr.set_deadline_resolver(lambda _anchor: deadline)
        mgr.set_last_updated(
            "cover.a", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.a")

        with freeze_time("2026-07-02 20:00:00"):
            state = _build_manual_override_diagnostics(
                mgr,
                {
                    CONF_MANUAL_OVERRIDE_DURATION_MODE: (
                        MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
                    )
                },
            )

        assert state["duration_mode"] == MANUAL_OVERRIDE_DURATION_MODE_UNTIL_SUNSET
        assert state["entries"]["cover.a"]["expires_at"] == deadline.isoformat()
        # Additive only — the legacy keys keep their meaning.
        assert state["reset_duration_seconds"] == 7200
        assert state["entries"]["cover.a"]["started_at"] == (
            dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC).isoformat()
        )
        assert state["entries"]["cover.a"]["active"] is True


class TestStartedAtProvenance:
    """``started_at`` means two different things — the payload must say which.

    ``engage_override`` records the honest moment the override was engaged.
    ``restore_override`` can't: the persisted payload is an absolute expiry and
    nothing else, so the start is *reconstructed* through the
    ``expiry - reset_duration`` inverse. Both are legitimate; a consumer just
    has to be able to tell them apart, because the same override reports a
    different ``started_at`` before and after a restart.
    """

    def test_engaged_override_reports_an_honest_start(self):
        """The service path knows exactly when it engaged — and says so."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        end = dt.datetime(2026, 7, 2, 12, 30, tzinfo=dt.UTC)

        with freeze_time("2026-07-02 12:00:00"):
            mgr.engage_override("cover.a", end_time=end, reason="service")
            state = _build_manual_override_diagnostics(mgr, {})

        entry = state["entries"]["cover.a"]
        assert entry["started_at_source"] == "engaged"
        assert entry["started_at"] == (
            dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC).isoformat()
        )
        # The true end is unambiguous regardless of how started_at was obtained.
        assert entry["expires_at"] == end.isoformat()

    def test_restored_override_reports_a_derived_start(self):
        """After a restart the same override's start is a reconstruction."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        end = dt.datetime(2026, 7, 2, 12, 30, tzinfo=dt.UTC)

        with freeze_time("2026-07-02 12:00:00"):
            mgr.restore_override("cover.a", end)
            state = _build_manual_override_diagnostics(mgr, {})

        entry = state["entries"]["cover.a"]
        assert entry["started_at_source"] == "derived_from_expiry"
        # Back-dated through the inverse — NOT the 12:00 the engage path reported.
        assert entry["started_at"] == (
            dt.datetime(2026, 7, 2, 10, 30, tzinfo=dt.UTC).isoformat()
        )
        assert entry["expires_at"] == end.isoformat()

    def test_detected_override_reports_an_honest_start(self):
        """The ordinary "user moved the cover" path is an honest start too."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        mgr.set_last_updated(
            "cover.a", _state(dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)), True
        )
        mgr.mark_manual_control("cover.a")

        with freeze_time("2026-07-02 12:30:00"):
            state = _build_manual_override_diagnostics(mgr, {})

        assert state["entries"]["cover.a"]["started_at_source"] == "engaged"

    def test_re_engaging_a_restored_override_returns_to_an_honest_start(self):
        """A fresh touch re-arms provenance along with the start and the pin."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})

        with freeze_time("2026-07-02 12:00:00"):
            mgr.restore_override(
                "cover.a", dt.datetime(2026, 7, 2, 12, 30, tzinfo=dt.UTC)
            )
        with freeze_time("2026-07-02 12:10:00"):
            mgr.engage_override("cover.a", reason="input_sensor")
            state = _build_manual_override_diagnostics(mgr, {})

        entry = state["entries"]["cover.a"]
        assert entry["started_at_source"] == "engaged"
        assert entry["started_at"] == (
            dt.datetime(2026, 7, 2, 12, 10, tzinfo=dt.UTC).isoformat()
        )

    def test_reset_clears_the_provenance_with_the_rest_of_the_state(self):
        """No stale provenance may outlive the override it described."""
        mgr = _manager(["cover.a"], reset_duration={"hours": 2})
        mgr.restore_override("cover.a", dt.datetime.now(dt.UTC) + dt.timedelta(hours=1))

        mgr.reset("cover.a")

        assert mgr.manual_control_start_source == {}


def _build_manual_override_diagnostics(manager, options: dict) -> dict:
    """Drive the coordinator's manual-override diagnostics block in isolation."""
    from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock()
    coord.manager = manager
    coord.config_entry.options = options
    # Same mirror the resolver reads — the diagnostics block reports the slice,
    # not a second read of the option (issue #1051).
    coord.manual_override_duration_mode = RuntimeConfig.from_options(
        options
    ).manual_override.duration_mode
    return AdaptiveDataUpdateCoordinator._manual_override_diagnostics(coord)
