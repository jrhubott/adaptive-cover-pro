"""Unit tests for the Day/Night dual-fabric cover type (#993, Model A).

A day/night shade is a single HA entity that drives both ``set_cover_position``
(bottom-rail / total coverage) and ``set_cover_tilt_position`` (reinterpreted as
the sheer-vs-blackout *fabric blend*: 100 = all sheer / light-filtering,
0 = all blackout). Position rides the normal vertical pipeline; the blend is
filled post-pipeline by ``DayNightShadeCalculation`` and dispatched through the
venetian ``DualAxisSequencer`` reused by composition — zero venetian edits.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import SERVICE_SET_COVER_POSITION

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_BLACKOUT_THRESHOLD,
    CONF_DAY_NIGHT_OPACITY_BLACKOUT,
    CONF_DAY_NIGHT_OPACITY_SHEER,
    DEFAULT_DAY_NIGHT_BLACKOUT_THRESHOLD,
    DEFAULT_DAY_NIGHT_OPACITY_BLACKOUT,
    DEFAULT_DAY_NIGHT_OPACITY_SHEER,
    OPTION_RANGES,
    ControlMethod,
    CoverType,
)
from custom_components.adaptive_cover_pro.config_types import DayNightShadeConfig
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
    POSITION_AXIS,
    TILT_AXIS,
)
from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.engine.covers import (
    DayNightShadeCalculation,
    DualAxisResult,
)
from custom_components.adaptive_cover_pro.engine.covers.day_night_shade import (
    DAY_NIGHT_BLACKOUT,
    DAY_NIGHT_SHEER,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# Step 1 — enum registration
# ---------------------------------------------------------------------------


def test_cover_type_registered_in_enum() -> None:
    """The new sensor_type string resolves to a CoverType with a display name."""
    assert CoverType("cover_day_night_shade") is CoverType.DAY_NIGHT_SHADE
    assert CoverType.DAY_NIGHT_SHADE.display_name == "Day/Night Shade"


# ---------------------------------------------------------------------------
# Step 2 — config dataclass
# ---------------------------------------------------------------------------


class TestConfig:
    """DayNightShadeConfig.from_options — defaults + explicit values."""

    def test_from_options_defaults(self) -> None:
        cfg = DayNightShadeConfig.from_options({})
        assert cfg.opacity_sheer == DEFAULT_DAY_NIGHT_OPACITY_SHEER == 30
        assert cfg.opacity_blackout == DEFAULT_DAY_NIGHT_OPACITY_BLACKOUT == 100
        assert cfg.blackout_threshold == DEFAULT_DAY_NIGHT_BLACKOUT_THRESHOLD == 65

    def test_from_options_explicit(self) -> None:
        cfg = DayNightShadeConfig.from_options(
            {
                CONF_DAY_NIGHT_OPACITY_SHEER: 20,
                CONF_DAY_NIGHT_OPACITY_BLACKOUT: 95,
                CONF_DAY_NIGHT_BLACKOUT_THRESHOLD: 50,
            }
        )
        assert cfg.opacity_sheer == 20
        assert cfg.opacity_blackout == 95
        assert cfg.blackout_threshold == 50


# ---------------------------------------------------------------------------
# Step 3 — option ranges registered
# ---------------------------------------------------------------------------


def test_option_ranges_registered() -> None:
    """The three numeric options auto-register their bounds in OPTION_RANGES."""
    assert OPTION_RANGES[CONF_DAY_NIGHT_OPACITY_SHEER] == (0, 100)
    assert OPTION_RANGES[CONF_DAY_NIGHT_OPACITY_BLACKOUT] == (0, 100)
    assert OPTION_RANGES[CONF_DAY_NIGHT_BLACKOUT_THRESHOLD] == (0, 100)


# ---------------------------------------------------------------------------
# Step 4 — engine: fabric-blend matrix
# ---------------------------------------------------------------------------


def _make_calc(**dn_overrides) -> DayNightShadeCalculation:
    from tests.cover_helpers import make_cover_config, make_vertical_config

    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return DayNightShadeCalculation(
        config=make_cover_config(),
        vert_config=make_vertical_config(),
        day_night_config=(
            DayNightShadeConfig(**dn_overrides)
            if dn_overrides
            else DayNightShadeConfig()
        ),
        sun_data=sun_data,
        sol_azi=180.0,
        sol_elev=45.0,
        logger=MagicMock(),
    )


class TestEngine:
    """DayNightShadeCalculation fabric selection — the blend matrix."""

    def test_solar_comfortable_selects_sheer(self) -> None:
        calc = _make_calc()
        sel = calc.select_fabric(50, is_summer=False, allow_sheer_in_summer=True)
        assert sel.blend == DAY_NIGHT_SHEER == 100

    def test_solar_summer_transparent_sheer_escalates_to_blackout(self) -> None:
        # opacity_sheer (30) < threshold (65) → summer sun escalates to blackout.
        calc = _make_calc(opacity_sheer=30, blackout_threshold=65)
        sel = calc.select_fabric(50, is_summer=True, allow_sheer_in_summer=True)
        assert sel.blend == DAY_NIGHT_BLACKOUT == 0

    def test_solar_summer_opaque_sheer_stays_sheer(self) -> None:
        # opacity_sheer (70) >= threshold (65) → the sheer fabric blocks enough.
        calc = _make_calc(opacity_sheer=70, blackout_threshold=65)
        sel = calc.select_fabric(50, is_summer=True, allow_sheer_in_summer=True)
        assert sel.blend == DAY_NIGHT_SHEER

    def test_climate_summer_forces_blackout_unconditionally(self) -> None:
        # allow_sheer_in_summer=False (climate bucket): summer → blackout even
        # when the sheer fabric is opaque enough for the solar escalation gate.
        calc = _make_calc(opacity_sheer=70, blackout_threshold=65)
        sel = calc.select_fabric(50, is_summer=True, allow_sheer_in_summer=False)
        assert sel.blend == DAY_NIGHT_BLACKOUT

    def test_filtering_estimate_is_opacity_times_covered_fraction(self) -> None:
        # position 40 → covered fraction 0.6; sheer opacity 30 → estimate 18.
        calc = _make_calc(opacity_sheer=30)
        sel = calc.select_fabric(40, is_summer=False, allow_sheer_in_summer=True)
        assert sel.opacity == 30
        assert sel.covered_fraction == pytest.approx(0.6)
        assert sel.filtering_estimate == pytest.approx(18.0)

    def test_calculate_dual_returns_dual_axis_result(self, monkeypatch) -> None:
        calc = _make_calc()
        monkeypatch.setattr(
            DayNightShadeCalculation, "calculate_position", lambda self: 40
        )
        result = calc.calculate_dual(is_summer=False, allow_sheer_in_summer=True)
        assert isinstance(result, DualAxisResult)
        assert result.position == 40
        assert result.tilt == DAY_NIGHT_SHEER


# ---------------------------------------------------------------------------
# Step 5 — policy registration + flags
# ---------------------------------------------------------------------------


def test_policy_registered_with_flags() -> None:
    policy = get_policy("cover_day_night_shade")
    assert isinstance(policy, DayNightShadePolicy)
    assert policy.axes == (POSITION_AXIS, TILT_AXIS)
    assert policy.exposes_dual_axis_sensor is True
    assert policy.custom_position_includes_tilt is True
    assert policy.supports_fov_compute is True
    assert policy.wiki_anchor() == "Configuration-Day-Night-Shade"
    assert policy.display_label() == "Day/Night Shade"


# ---------------------------------------------------------------------------
# Step 7 — _compose_blend seam + post_pipeline_resolve
# ---------------------------------------------------------------------------


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


class TestComposeBlend:
    """The shared ``_compose_blend`` seam feeds both live + forecast paths."""

    def test_compose_blend_solar_comfortable_returns_sheer(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        blend, sel = policy._compose_blend(
            50,
            is_summer=False,
            allow_sheer_in_summer=True,
            config=kw["config"],
            config_service=kw["config_service"],
            options=kw["options"],
            sun_data=kw["sun_data"],
            sol_azi=kw["sol_azi"],
            sol_elev=kw["sol_elev"],
            logger=kw["logger"],
        )
        assert blend == DAY_NIGHT_SHEER
        assert sel.blend == DAY_NIGHT_SHEER


class TestPostPipelineResolve:
    """post_pipeline_resolve fills the fabric blend onto result.tilt."""

    def test_handler_tilt_honored_verbatim(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        result = PipelineResult(
            position=40,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=25,
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **kw)
        assert out.tilt == 25
        assert policy._last_blend == 25
        assert any(s.handler == "day_night_handler_tilt" for s in out.decision_trace)

    def test_solar_direct_sun_fills_engine_blend_with_trace(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        result = PipelineResult(
            position=50, control_method=ControlMethod.SOLAR, reason="solar"
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **kw)
        assert out.tilt == DAY_NIGHT_SHEER
        assert any(s.handler == "day_night_engine" for s in out.decision_trace)

    def test_solar_summer_transparent_sheer_escalates_to_blackout(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs(options={CONF_DAY_NIGHT_OPACITY_SHEER: 30})
        climate = MagicMock()
        climate.is_summer = True
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            climate_data=climate,
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **kw)
        assert out.tilt == DAY_NIGHT_BLACKOUT

    def test_climate_summer_forces_blackout(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        climate = MagicMock()
        climate.is_summer = True
        result = PipelineResult(
            position=0,
            control_method=ControlMethod.SUMMER,
            reason="summer",
            climate_data=climate,
        )
        out = policy.post_pipeline_resolve(result, cover=None, **kw)
        assert out.tilt == DAY_NIGHT_BLACKOUT

    def test_climate_winter_selects_sheer(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        climate = MagicMock()
        climate.is_summer = False
        result = PipelineResult(
            position=100,
            control_method=ControlMethod.WINTER,
            reason="winter",
            climate_data=climate,
        )
        out = policy.post_pipeline_resolve(result, cover=None, **kw)
        assert out.tilt == DAY_NIGHT_SHEER

    def test_non_solar_non_climate_clears_blend(self) -> None:
        policy = DayNightShadePolicy()
        policy._last_blend = 40
        kw = _resolve_kwargs()
        result = PipelineResult(
            position=60, control_method=ControlMethod.MANUAL, reason="manual"
        )
        out = policy.post_pipeline_resolve(result, cover=None, **kw)
        assert out.tilt is None
        assert policy._last_blend is None

    def test_solar_without_direct_sun_clears_blend(self) -> None:
        policy = DayNightShadePolicy()
        policy._last_blend = 40
        kw = _resolve_kwargs()
        result = PipelineResult(
            position=60, control_method=ControlMethod.SOLAR, reason="solar"
        )
        out = policy.post_pipeline_resolve(
            result, cover=_solar_cover(direct_sun_valid=False), **kw
        )
        assert out.tilt is None
        assert policy._last_blend is None

    def test_axis_constraint_clamp_applied(self) -> None:
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        # Engine wants sheer (100) but a per-slot ceiling caps the blend at 60.
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            tilt_low=0,
            tilt_high=60,
            tilt_bound_label="slot 1",
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **kw)
        assert out.tilt == 60


# ---------------------------------------------------------------------------
# Step 8 — dual-axis dispatch (sequencer reused by composition)
# ---------------------------------------------------------------------------


def _make_policy_with_seq() -> DayNightShadePolicy:
    policy = DayNightShadePolicy()
    seq = MagicMock()
    seq.run_sequence = AsyncMock()
    seq.stamp_position_command = MagicMock()
    seq.update_tilt_only = AsyncMock()
    seq.is_in_suppression = MagicMock(return_value=False)
    policy._sequencer = seq
    return policy


def _ctx(policy: DayNightShadePolicy, *, tilt: int = 100):
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        PositionContext,
    )

    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        tilt=tilt,
        policy=policy,
    )


class TestDispatch:
    """Dispatch overlays reuse the venetian DualAxisSequencer by composition."""

    def test_position_context_overrides_threads_blend(self) -> None:
        policy = DayNightShadePolicy()
        result = MagicMock()
        result.tilt = 60
        result.position = 40
        assert policy.position_context_overrides(result)["tilt"] == 60

    def test_position_context_overrides_empty_without_blend(self) -> None:
        policy = DayNightShadePolicy()
        result = MagicMock()
        result.tilt = None
        assert policy.position_context_overrides(result) == {}
        assert policy.position_context_overrides(None) == {}

    def test_attach_builds_dual_axis_sequencer(self) -> None:
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            DualAxisSequencer,
        )

        policy = DayNightShadePolicy()
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
        )
        assert isinstance(policy.sequencer, DualAxisSequencer)

    @pytest.mark.asyncio
    async def test_after_position_command_runs_sequence_with_blend(self) -> None:
        policy = _make_policy_with_seq()
        await policy.after_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.dn_x",
            service=SERVICE_SET_COVER_POSITION,
            position=40,
            context=_ctx(policy, tilt=100),
            reason="solar",
        )
        policy._sequencer.stamp_position_command.assert_called_once_with("cover.dn_x")
        policy._sequencer.run_sequence.assert_awaited_once()
        kwargs = policy._sequencer.run_sequence.await_args.kwargs
        assert kwargs["position_target"] == 40
        assert kwargs["tilt_target"] == 100

    @pytest.mark.asyncio
    async def test_after_position_command_skips_non_position_service(self) -> None:
        policy = _make_policy_with_seq()
        await policy.after_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.dn_x",
            service="set_cover_tilt_position",
            position=40,
            context=_ctx(policy),
            reason="solar",
        )
        policy._sequencer.run_sequence.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_maybe_update_tilt_only_sends_when_not_suppressed(self) -> None:
        policy = _make_policy_with_seq()
        policy._last_blend = 100
        await policy.maybe_update_tilt_only(
            "cover.dn_x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_maybe_update_tilt_only_skips_when_no_last_blend(self) -> None:
        policy = _make_policy_with_seq()
        policy._last_blend = None
        await policy.maybe_update_tilt_only(
            "cover.dn_x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_not_awaited()

    def test_secondary_axis_check_carries_blend(self) -> None:
        policy = DayNightShadePolicy()
        result = MagicMock()
        result.tilt = 100
        check = policy.secondary_axis_check(result, cmd_svc=MagicMock())
        assert check is not None
        assert check.expected == 100
        assert check.attribute == "current_tilt_position"
        assert check.label == "tilt"

    def test_secondary_axis_check_none_without_blend(self) -> None:
        policy = DayNightShadePolicy()
        result = MagicMock()
        result.tilt = None
        assert policy.secondary_axis_check(result, cmd_svc=MagicMock()) is None
        assert policy.secondary_axis_check(None, cmd_svc=MagicMock()) is None


# ---------------------------------------------------------------------------
# Step 9 — forecast parity with the live seam
# ---------------------------------------------------------------------------


def test_forecast_secondary_axes_matches_live() -> None:
    policy = DayNightShadePolicy()
    kw = _resolve_kwargs()
    forecast = policy.forecast_secondary_axes(
        position=50,
        logger=kw["logger"],
        sol_azi=kw["sol_azi"],
        sol_elev=kw["sol_elev"],
        sun_data=kw["sun_data"],
        config=kw["config"],
        config_service=kw["config_service"],
        options=kw["options"],
        minimize_movements=False,
        max_coverage_steps=1,
    )
    assert set(forecast) == {policy.axes[1].name}
    live_blend, _ = policy._compose_blend(
        50,
        is_summer=False,
        allow_sheer_in_summer=True,
        config=kw["config"],
        config_service=kw["config_service"],
        options=kw["options"],
        sun_data=kw["sun_data"],
        sol_azi=kw["sol_azi"],
        sol_elev=kw["sol_elev"],
        logger=kw["logger"],
    )
    assert forecast[policy.axes[1].name] == live_blend


# ---------------------------------------------------------------------------
# Step 10 — schemas / filter / warnings / live keys
# ---------------------------------------------------------------------------


class TestSchemas:
    """Geometry schema, entity filter, capability warnings, live option keys."""

    def test_geometry_schema_has_vertical_plus_three_sliders(self) -> None:
        import voluptuous as vol

        schema = DayNightShadePolicy().geometry_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
        # Vertical window geometry.
        from custom_components.adaptive_cover_pro.const import CONF_HEIGHT_WIN

        assert CONF_HEIGHT_WIN in keys
        # The three fabric sliders.
        assert CONF_DAY_NIGHT_OPACITY_SHEER in keys
        assert CONF_DAY_NIGHT_OPACITY_BLACKOUT in keys
        assert CONF_DAY_NIGHT_BLACKOUT_THRESHOLD in keys

    def test_geometry_schema_has_no_venetian_drift_reset(self) -> None:
        import voluptuous as vol

        from custom_components.adaptive_cover_pro.const import (
            CONF_VENETIAN_TILT_RESET_THRESHOLD,
        )

        schema = DayNightShadePolicy().geometry_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
        assert CONF_VENETIAN_TILT_RESET_THRESHOLD not in keys

    def test_geometry_schema_defaults(self) -> None:
        schema = DayNightShadePolicy().geometry_schema()
        # The vertical window-height field is Required but carries its own
        # default, so an empty payload validates and the fabric sliders fall
        # back to their defaults.
        out = schema({})
        assert out[CONF_DAY_NIGHT_OPACITY_SHEER] == DEFAULT_DAY_NIGHT_OPACITY_SHEER
        assert (
            out[CONF_DAY_NIGHT_OPACITY_BLACKOUT] == DEFAULT_DAY_NIGHT_OPACITY_BLACKOUT
        )
        assert (
            out[CONF_DAY_NIGHT_BLACKOUT_THRESHOLD]
            == DEFAULT_DAY_NIGHT_BLACKOUT_THRESHOLD
        )

    def test_entity_selector_filter_requires_tilt(self) -> None:
        flt = DayNightShadePolicy().entity_selector_filter()
        assert flt.get("domain") == "cover"
        assert "cover.CoverEntityFeature.SET_TILT_POSITION" in flt.get(
            "supported_features", []
        )

    def test_capability_warnings_flag_missing_axes(self) -> None:
        policy = DayNightShadePolicy()
        warn_pos = policy.cover_capability_warnings(
            {"cover.a": {"has_set_position": False, "has_set_tilt_position": True}}
        )
        assert any("set_position" in w for w in warn_pos)
        warn_tilt = policy.cover_capability_warnings(
            {"cover.b": {"has_set_position": True, "has_set_tilt_position": False}}
        )
        assert any("set_tilt_position" in w for w in warn_tilt)
        assert (
            policy.cover_capability_warnings(
                {"cover.c": {"has_set_position": True, "has_set_tilt_position": True}}
            )
            == []
        )

    def test_live_option_keys_include_new_keys(self) -> None:
        keys = DayNightShadePolicy().live_option_keys()
        assert CONF_DAY_NIGHT_OPACITY_SHEER in keys
        assert CONF_DAY_NIGHT_OPACITY_BLACKOUT in keys
        assert CONF_DAY_NIGHT_BLACKOUT_THRESHOLD in keys

    def test_lift_travel_metres_returns_window_height(self) -> None:
        svc = MagicMock()
        svc.get_vertical_data.return_value = MagicMock(h_win=2.4)
        assert DayNightShadePolicy().lift_travel_metres(svc, {}) == 2.4

    def test_disallowed_geometry_rejects_awning_and_tilt(self) -> None:
        rules = DayNightShadePolicy().disallowed_geometry_fields(
            vertical_only={"height_win"},
            awning_only={"awning_drop"},
            tilt_only={"tilt_depth"},
        )
        rejected = {label for _fields, label in rules}
        assert "awning" in rejected
        assert "tilt" in rejected


# ---------------------------------------------------------------------------
# Step 11 — translations (en.json picker label present)
# ---------------------------------------------------------------------------


def test_picker_label_present_in_en_translations() -> None:
    import json
    import pathlib

    en = json.loads(
        (
            pathlib.Path(__file__).resolve().parent.parent.parent
            / "custom_components"
            / "adaptive_cover_pro"
            / "translations"
            / "en.json"
        ).read_text(encoding="utf-8")
    )
    options = en["selector"]["mode"]["options"]
    assert options["cover_day_night_shade"]


def test_axis_names_are_position_then_blend() -> None:
    policy = get_policy("cover_day_night_shade")
    assert tuple(a.name for a in policy.axes) == (AXIS_NAME_POSITION, AXIS_NAME_TILT)
