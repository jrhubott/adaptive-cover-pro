"""Deep-audit fixes for the Day/Night dual-fabric shade (issue #993).

Guards the behavioral corrections layered on top of Models A/B/C:

* HIGH — a user tilt/blend command drives the FABRIC BLEND (tilt axis), not the
  carriage (``apply_user_tilt`` override, mirroring venetian #684).
* MED — ``EXTREME_HEAT`` forces the blackout fabric regardless of season.
* MED — a deferred blend update landing in the back-rotate suppression window is
  RECORDED pending + a refresh scheduled, never dropped (#756 parity).
* MED — the Model C middle-rail remap stays consistent with the ACTUAL space of
  the dispatched value (bypass / floor-clamp / interpolation skip inversion).
* LOW — the ``dual_entity`` capability warning names the dual-entity model.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import SERVICE_SET_COVER_POSITION

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    VENETIAN_TILT_SUPPRESSION_SECONDS,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.engine.covers.day_night_shade import (
    DAY_NIGHT_BLACKOUT,
    DAY_NIGHT_SHEER,
)
from custom_components.adaptive_cover_pro.managers.manual_override import inverse_state
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

_MIDDLE = "cover.middle_rail"
_BOTTOM = "cover.bottom_rail"
_ENTITY = "cover.dn_x"


def _resolve_kwargs(**overrides):
    from tests.cover_helpers import make_cover_config, make_vertical_config

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    kw = {
        "logger": MagicMock(),
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": sun_data,
        "config": make_cover_config(),
        "config_service": svc,
        "options": {},
    }
    kw.update(overrides)
    return kw


def _solar_cover(direct_sun_valid: bool = True) -> MagicMock:
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    return cover


# ---------------------------------------------------------------------------
# HIGH — apply_user_tilt drives the blend axis, never the carriage
# ---------------------------------------------------------------------------


def _policy_with_mock_seq() -> DayNightShadePolicy:
    policy = DayNightShadePolicy()
    seq = MagicMock()
    seq.update_tilt_only = AsyncMock()
    seq._get_current_position = MagicMock(return_value=30)
    policy._sequencer = seq
    return policy


class TestApplyUserTilt:
    """A user tilt/blend command sets the fabric blend, not the carriage."""

    @pytest.mark.asyncio
    async def test_blend_zero_drives_tilt_axis_not_carriage(self) -> None:
        # Model A (default). A user blend=0 must send a tilt/blend command via the
        # sequencer — NOT slam the carriage to position 0.
        policy = _policy_with_mock_seq()
        handled = await policy.apply_user_tilt(_ENTITY, tilt=0, reason="user_tilt")
        assert handled is True
        policy._sequencer.update_tilt_only.assert_awaited_once()
        kwargs = policy._sequencer.update_tilt_only.await_args.kwargs
        assert kwargs["tilt_target"] == 0
        assert kwargs["force"] is True

    @pytest.mark.asyncio
    async def test_unattached_policy_returns_not_handled(self) -> None:
        # No sequencer yet → fall back to the position path (returns False).
        policy = DayNightShadePolicy()
        assert await policy.apply_user_tilt(_ENTITY, tilt=0, reason="x") is False


# ---------------------------------------------------------------------------
# MED — EXTREME_HEAT forces blackout regardless of interior season
# ---------------------------------------------------------------------------


def test_extreme_heat_forces_blackout_with_cool_interior() -> None:
    policy = DayNightShadePolicy()
    kw = _resolve_kwargs()
    climate = MagicMock()
    # AC-cool interior: is_summer is False, but EXTREME_HEAT fires on the
    # OUTDOOR temperature and must block it unconditionally.
    climate.is_summer = False
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.EXTREME_HEAT,
        reason="extreme_heat",
        climate_data=climate,
    )
    out = policy.post_pipeline_resolve(result, cover=None, **kw)
    assert out.tilt == DAY_NIGHT_BLACKOUT


def test_extreme_heat_still_blackout_when_summer() -> None:
    # Regression guard: EXTREME_HEAT in summer stays blackout (unchanged).
    policy = DayNightShadePolicy()
    kw = _resolve_kwargs()
    climate = MagicMock()
    climate.is_summer = True
    result = PipelineResult(
        position=0,
        control_method=ControlMethod.EXTREME_HEAT,
        reason="extreme_heat",
        climate_data=climate,
    )
    out = policy.post_pipeline_resolve(result, cover=None, **kw)
    assert out.tilt == DAY_NIGHT_BLACKOUT


# ---------------------------------------------------------------------------
# MED — deferred blend update recorded pending, not dropped (#756 parity)
# ---------------------------------------------------------------------------


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def schedule_refresh_after():
    return MagicMock()


@pytest.fixture
def attached_policy(hass, schedule_refresh_after):
    policy = DayNightShadePolicy()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: 0,
        set_commanded_position=MagicMock(),
        position_tolerance=5,
        is_dry_run=lambda: True,
        schedule_refresh_after=schedule_refresh_after,
    )
    return policy


class TestDeferredBlend756:
    """A blend-only update inside the suppression window is queued, not lost."""

    def test_base_reports_no_pending_before_defer(self, attached_policy) -> None:
        assert attached_policy.has_pending_secondary_axis(_ENTITY) is False

    @pytest.mark.asyncio
    async def test_blend_deferred_and_refresh_scheduled(
        self, attached_policy, schedule_refresh_after
    ) -> None:
        attached_policy._last_blend = 0
        # Open the back-rotate suppression window.
        attached_policy._sequencer.stamp_position_command(_ENTITY)
        assert attached_policy._sequencer.is_in_suppression(_ENTITY)

        await attached_policy.maybe_update_tilt_only(
            _ENTITY,
            current_position=0,
            context=SimpleNamespace(force=False),
            reason="custom_position",
        )

        # Queued, not sent — and a wake scheduled at suppression expiry.
        assert attached_policy.has_pending_secondary_axis(_ENTITY) is True
        schedule_refresh_after.assert_called_once()
        secs = schedule_refresh_after.call_args.args[0]
        assert 0 < secs <= VENETIAN_TILT_SUPPRESSION_SECONDS

    @pytest.mark.asyncio
    async def test_pending_blend_fires_and_clears_after_window(
        self, attached_policy
    ) -> None:
        attached_policy._last_blend = 0
        attached_policy._sequencer.stamp_position_command(_ENTITY)
        await attached_policy.maybe_update_tilt_only(
            _ENTITY,
            current_position=0,
            context=SimpleNamespace(force=False),
            reason="custom_position",
        )
        assert attached_policy.has_pending_secondary_axis(_ENTITY) is True

        # Backdate the suppression stamp so the window has closed.
        attached_policy._sequencer._suppression_at[_ENTITY] = dt.datetime.now(
            dt.UTC
        ) - dt.timedelta(seconds=VENETIAN_TILT_SUPPRESSION_SECONDS + 5)
        assert not attached_policy._sequencer.is_in_suppression(_ENTITY)

        await attached_policy.maybe_update_tilt_only(
            _ENTITY,
            current_position=0,
            context=SimpleNamespace(force=False),
            reason="custom_position",
        )
        assert attached_policy.has_pending_secondary_axis(_ENTITY) is False


# ---------------------------------------------------------------------------
# MED — Model C middle-rail inverse-state on bypass/floor-clamp/interpolation
# ---------------------------------------------------------------------------


def _dual_opts(*, inverse: bool = False, interp: bool = False) -> dict:
    opts = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
    }
    if inverse:
        opts[CONF_INVERSE_STATE] = True
    if interp:
        opts[CONF_INTERP] = True
    return opts


def _cache_dual(
    policy: DayNightShadePolicy,
    *,
    position: int,
    blend: int,
    inverse: bool = False,
    interp: bool = False,
    floor_clamp: bool = False,
    bypass: bool = False,
) -> None:
    """Run post_pipeline_resolve with a handler-supplied blend + dispatch flags."""
    kw = _resolve_kwargs(options=_dual_opts(inverse=inverse, interp=interp))
    result = PipelineResult(
        position=position,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom",
        tilt=blend,
        floor_clamp_applied=floor_clamp,
        bypass_auto_control=bypass,
    )
    policy.post_pipeline_resolve(result, cover=None, **kw)


class TestDualEntityInverseConsistency:
    """The middle-rail remap tracks the ACTUAL dispatched space (#993).

    Still the requirement; the derivation moved. ``coordinator.state`` maps
    every winner through ``_to_cover_frame``, so the dispatched space is
    "inverse configured AND interpolation off" for all of them — a bypass or
    floor-clamp flag riding the result no longer changes it (#1036).
    """

    def test_floor_clamp_summer_blackout_middle_opens_not_closes(self) -> None:
        # The audit's exact failure: inverse_state=True + a min-position floor
        # clamp + summer solar (blend 0). The middle rail must open fully
        # (physical 100), NOT be slammed to physical 0.
        #
        # Re-derived for #1036: a floor-clamped winner's position is LOGICAL
        # like every other winner, so coordinator.state dispatches
        # inverse_state(30) rather than a raw 30. Feed the remap what state
        # actually sends and assert the invariant in physical terms.
        policy = DayNightShadePolicy()
        # Reproduce the summer-solar blend-0 outcome deterministically via a
        # handler blend of 0 with the floor-clamp flag set + inverse configured.
        _cache_dual(policy, position=30, blend=0, inverse=True, floor_clamp=True)
        middle_wire = policy.resolve_entity_target(_MIDDLE, inverse_state(30))
        assert inverse_state(middle_wire) == 100
        assert inverse_state(middle_wire) != 0

    def test_bypass_slot_carrying_blend_uses_dispatched_space(self) -> None:
        # A safety/bypass slot's position is logical too (#1036), so the value
        # that reaches the wire is inverse_state(40) = 60. The remap must
        # un-invert it, compose in open-space, and re-invert.
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=40, blend=50, inverse=True, bypass=True)
        # open-space: M = 100 - 50*(100-40)/100 = 70.
        middle_wire = policy.resolve_entity_target(_MIDDLE, inverse_state(40))
        assert inverse_state(middle_wire) == 70

    @pytest.mark.parametrize("floor_clamp", [False, True])
    @pytest.mark.parametrize("bypass", [False, True])
    @pytest.mark.parametrize("interp", [False, True])
    @pytest.mark.parametrize("inverse", [False, True])
    @pytest.mark.parametrize("position", [0, 30, 60, 100])
    @pytest.mark.parametrize("blend", [0, 40, 100])
    def test_no_pass_holds_in_physical_space(
        self,
        floor_clamp: bool,
        bypass: bool,
        interp: bool,
        inverse: bool,
        position: int,
        blend: int,
    ) -> None:
        # Whatever the dispatched space, the middle rail must never physically
        # pass below the bottom rail. Inversion is ACTUALLY applied when inverse
        # is on AND interpolation is off — the flags ride the result but no
        # longer buy an exemption from the frame transform (#1036), so they must
        # not appear in the derivation of "what space is the wire value in".
        policy = DayNightShadePolicy()
        _cache_dual(
            policy,
            position=position,
            blend=blend,
            inverse=inverse,
            interp=interp,
            floor_clamp=floor_clamp,
            bypass=bypass,
        )
        actually_inverted = inverse and not interp

        def phys(v: int) -> int:
            return inverse_state(v) if actually_inverted else v

        middle = policy.resolve_entity_target(_MIDDLE, position)
        assert phys(middle) >= phys(position)


class TestBroadcastSeamInverseSpace:
    """The middle-rail remap honors the SEAM's explicit inversion space (#993).

    ``resolve_entity_target`` accepts an explicit ``inverted`` per dispatch so
    the three broadcast seams — which dispatch in a DIFFERENT inversion space
    than the cached main-pipeline flag — remap the middle rail correctly. The
    cached flag is deliberately primed to DIVERGE from the seam's space; the
    explicit ``inverted`` argument must win.
    """

    def test_explicit_inverted_overrides_diverging_cached_flag(self) -> None:
        # Cache the main-pipeline flag as False (a non-inverse cycle), then
        # dispatch a value the sunset/end-time seam produced in INVERTED space
        # (wire 100 for logical 0). The seam passes inverted=True; the middle
        # rail must un-invert 100 → open 0 → wire 0 (physically OPEN), NOT
        # reuse the cached False.
        #
        # The divergence used to be produced with a floor-clamped inverse cycle;
        # #1036 removed that lever (the clamp no longer un-sets the cached
        # flag), so it is produced from the config instead. The requirement is
        # unchanged: an explicit seam-supplied ``inverted`` beats the cache.
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=30, blend=0, inverse=False)
        assert policy._dual_entity_inverse is False  # cached main-path space
        wire = policy.resolve_entity_target(_MIDDLE, 100, inverted=True)
        assert inverse_state(wire) == 100  # open-space fully open

    def test_explicit_not_inverted_overrides_diverging_cached_flag(self) -> None:
        # Cache the main-pipeline flag as True (plain inverse cycle), then
        # dispatch a raw open-space value the auto-off seam produced (never
        # inverts). The seam passes inverted=False; the middle rail must remap
        # in open space (default 60, blend 50 → 80), NOT reuse the cached True.
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=60, blend=50, inverse=True)
        assert policy._dual_entity_inverse is True  # cached main-path space
        assert policy.resolve_entity_target(_MIDDLE, 60, inverted=False) == 80

    def test_inverted_none_falls_back_to_cached_flag(self) -> None:
        # The main pipeline path passes no explicit space, so inverted=None must
        # preserve the existing cached-flag behavior byte-for-byte.
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=40, blend=50, inverse=False)
        assert policy.resolve_entity_target(
            _MIDDLE, 40
        ) == policy.resolve_entity_target(_MIDDLE, 40, inverted=None)
        assert policy.resolve_entity_target(_MIDDLE, 40) == 70

    @pytest.mark.parametrize("cached_true", [False, True])
    @pytest.mark.parametrize("seam_inverted", [False, True])
    @pytest.mark.parametrize("logical_pos", [0, 30, 60, 100])
    @pytest.mark.parametrize("blend", [0, 40, 100])
    def test_no_pass_holds_across_seam_spaces(
        self,
        cached_true: bool,
        seam_inverted: bool,
        logical_pos: int,
        blend: int,
    ) -> None:
        # Regardless of the cached main-pipeline flag, when a broadcast seam
        # states its own inversion via ``inverted`` the middle rail must never
        # physically pass below the bottom rail. The seam's wire value is the
        # logical position mapped into its OWN inversion space.
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=logical_pos, blend=blend, inverse=cached_true)
        assert policy._dual_entity_inverse is cached_true

        bottom_wire = inverse_state(logical_pos) if seam_inverted else logical_pos
        middle_wire = policy.resolve_entity_target(
            _MIDDLE, bottom_wire, inverted=seam_inverted
        )

        def open_space(v: int) -> int:
            return inverse_state(v) if seam_inverted else v

        assert open_space(middle_wire) >= open_space(bottom_wire)

    def test_audit_repro_sunset_100_blend_50_no_cross(self) -> None:
        # sunset_pos=100 (fully open), blend 50, inverse ON. The seam sends wire
        # inverse_state(100)=0; the middle must stay physically >= the bottom
        # rail (open-space 100), never drop to open-space 50 (the buggy cross).
        policy = DayNightShadePolicy()
        _cache_dual(policy, position=100, blend=50, inverse=True)
        middle_wire = policy.resolve_entity_target(_MIDDLE, 0, inverted=True)
        assert inverse_state(middle_wire) >= 100


# ---------------------------------------------------------------------------
# LOW — dual-entity capability warning names the dual-entity model
# ---------------------------------------------------------------------------


def test_dual_entity_missing_position_warning_names_dual_entity() -> None:
    policy = DayNightShadePolicy()
    opts = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
    }
    warn = policy.capability_warnings_for_options(
        {_BOTTOM: {"has_set_position": False, "has_set_tilt_position": False}},
        opts,
    )
    joined = " ".join(warn)
    assert "set_position" in joined
    # The message must not mislabel a dual-entity shade as "split-range".
    assert "split-range" not in joined
    assert "dual-entity" in joined


# Keep the sheer polarity constant referenced so an import-only lint pass over
# the blend polarity stays honest.
assert DAY_NIGHT_SHEER == 100
assert SERVICE_SET_COVER_POSITION
