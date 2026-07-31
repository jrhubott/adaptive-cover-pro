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
from homeassistant.helpers.selector import ENTITY_FILTER_SELECTOR_CONFIG_SCHEMA

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_BLACKOUT_THRESHOLD,
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_DAY_NIGHT_OPACITY_BLACKOUT,
    CONF_DAY_NIGHT_OPACITY_SHEER,
    CONF_HEIGHT_WIN,
    CONF_INVERSE_STATE,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    DAY_NIGHT_MODEL_POSITION_TILT,
    DAY_NIGHT_MODEL_SPLIT_RANGE,
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
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    POSITION_AXIS,
    TILT_AXIS,
    TILT_CAPABLE_ENTITY_FILTER,
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

    def test_entity_selector_filter_requires_position(self) -> None:
        flt = DayNightShadePolicy().entity_selector_filter()
        assert flt.get("domain") == "cover"
        assert "cover.CoverEntityFeature.SET_POSITION" in flt.get(
            "supported_features", []
        )
        assert "cover.CoverEntityFeature.SET_TILT_POSITION" not in flt.get(
            "supported_features", []
        )

    def test_entity_selector_filter_admits_position_only_cover(self) -> None:
        """A ``supported_features=15`` cover (open+close+set_position+stop, no
        tilt) is exactly the Model B/C hardware #1114 reports as locked out of
        the picker. Resolve the filter through HA's own
        ``ENTITY_FILTER_SELECTOR_CONFIG_SCHEMA`` validator — the same
        machinery ``EntitySelector`` construction uses — to get the real
        bitmask, then check it against mask 15 with a bitwise AND. This
        exercises the actual mask HA computes (not just the declared string
        list) and would fail if the enum path were ever mistyped, since the
        validator raises ``vol.Invalid`` on an unresolvable feature string.
        """
        flt = DayNightShadePolicy().entity_selector_filter()
        mask = ENTITY_FILTER_SELECTOR_CONFIG_SCHEMA(flt)["supported_features"][0]
        assert 15 & mask, f"position-only cover (mask 15) not admitted by {mask=}"

        # The stricter tilt filter must NOT admit the same position-only
        # cover — regression guard for a revert of the #1114 fix back onto
        # TILT_CAPABLE_ENTITY_FILTER.
        tilt_mask = ENTITY_FILTER_SELECTOR_CONFIG_SCHEMA(TILT_CAPABLE_ENTITY_FILTER)[
            "supported_features"
        ][0]
        assert not (
            15 & tilt_mask
        ), f"tilt filter unexpectedly admits mask 15: {tilt_mask=}"

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


# ===========================================================================
# PHASE B — Model B: split position range
# ===========================================================================
# ---------------------------------------------------------------------------
# Step 14 — control-model option registered
# ---------------------------------------------------------------------------


def test_control_model_option_registered() -> None:
    """CONF_DAY_NIGHT_CONTROL_MODEL validates the two model values; default position_tilt."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_DAY_NIGHT_CONTROL_MODEL,
        DAY_NIGHT_MODEL_POSITION_TILT,
        DAY_NIGHT_MODEL_SPLIT_RANGE,
        DAY_NIGHT_SPLIT_MIDPOINT,
        DEFAULT_DAY_NIGHT_CONTROL_MODEL,
    )
    from custom_components.adaptive_cover_pro.services.options_service import (
        FIELD_VALIDATORS,
    )

    assert DAY_NIGHT_MODEL_POSITION_TILT == "position_tilt"
    assert DAY_NIGHT_MODEL_SPLIT_RANGE == "split_range"
    assert DEFAULT_DAY_NIGHT_CONTROL_MODEL == DAY_NIGHT_MODEL_POSITION_TILT
    assert DAY_NIGHT_SPLIT_MIDPOINT == 50

    validator = FIELD_VALIDATORS[CONF_DAY_NIGHT_CONTROL_MODEL]
    assert validator(DAY_NIGHT_MODEL_POSITION_TILT) == DAY_NIGHT_MODEL_POSITION_TILT
    assert validator(DAY_NIGHT_MODEL_SPLIT_RANGE) == DAY_NIGHT_MODEL_SPLIT_RANGE
    with pytest.raises(vol.Invalid):
        validator("bogus")


# ---------------------------------------------------------------------------
# Step 15 — the split-range wire mapping seam
# ---------------------------------------------------------------------------


class TestSplitRangeWire:
    """``_split_range_wire`` folds (position, blend) into one physical position."""

    @pytest.mark.parametrize(
        ("position", "blend", "expected"),
        [
            # Sheer half (blend >= 50): wire = 50 + position/2.
            (40, 100, 70),
            (0, 100, 50),
            # Blackout half (blend < 50): wire = position/2.
            (40, 0, 20),
            (0, 0, 0),
            # Fully open carriage collapses to a single physical endpoint.
            (100, 100, 100),
            (100, 0, 100),
            # Boundary: blend == 50 counts as the sheer half.
            (40, 50, 70),
        ],
    )
    def test_wire_matrix(self, position: int, blend: int, expected: int) -> None:
        policy = DayNightShadePolicy()
        assert policy._split_range_wire(position, blend) == expected


# ---------------------------------------------------------------------------
# Step 16 — model-gated post_pipeline_resolve + dispatch hooks
# ---------------------------------------------------------------------------


def _split_kw(**overrides):
    opts = {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_SPLIT_RANGE}
    opts.update(overrides.pop("options", {}))
    return _resolve_kwargs(options=opts, **overrides)


class TestSplitRangeResolve:
    """``split_range`` rewrites position to the wire; the dispatch hooks no-op."""

    def test_engine_blend_rewrites_position_to_wire(self) -> None:
        policy = DayNightShadePolicy()
        result = PipelineResult(
            position=40, control_method=ControlMethod.SOLAR, reason="solar"
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **_split_kw())
        # Sheer (100) selected → wire = 50 + 40/2 = 70; abstract blend stays.
        assert out.tilt == DAY_NIGHT_SHEER
        assert out.position == 70
        assert any(s.handler == "day_night_split_range" for s in out.decision_trace)
        assert policy._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE

    def test_handler_blend_also_rewrites_position(self) -> None:
        policy = DayNightShadePolicy()
        result = PipelineResult(
            position=40,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=0,
        )
        out = policy.post_pipeline_resolve(result, cover=_solar_cover(), **_split_kw())
        assert out.tilt == 0  # abstract blend kept
        assert out.position == 20  # blackout half: 40/2
        assert any(s.handler == "day_night_split_range" for s in out.decision_trace)

    def test_cleared_blend_leaves_position_untouched(self) -> None:
        # No fabric decision (MANUAL) → no wire rewrite; a split-range cover's
        # manual position is already in wire space.
        policy = DayNightShadePolicy()
        result = PipelineResult(
            position=60, control_method=ControlMethod.MANUAL, reason="manual"
        )
        out = policy.post_pipeline_resolve(result, cover=None, **_split_kw())
        assert out.tilt is None
        assert out.position == 60
        assert not any(s.handler == "day_night_split_range" for s in out.decision_trace)
        assert policy._control_model == DAY_NIGHT_MODEL_SPLIT_RANGE

    def test_position_context_overrides_omits_tilt(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        result = MagicMock()
        result.tilt = 100
        result.position = 70
        assert "tilt" not in policy.position_context_overrides(result)

    def test_secondary_axis_check_none(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        result = MagicMock()
        result.tilt = 100
        assert policy.secondary_axis_check(result, cmd_svc=MagicMock()) is None

    @pytest.mark.asyncio
    async def test_after_position_command_noop(self) -> None:
        policy = _make_policy_with_seq()
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        await policy.after_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.dn_x",
            service=SERVICE_SET_COVER_POSITION,
            position=70,
            context=_ctx(policy, tilt=100),
            reason="solar",
        )
        policy._sequencer.run_sequence.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_before_position_command_noop(self) -> None:
        policy = _make_policy_with_seq()
        policy._sequencer._send_tilt_command = AsyncMock()
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.dn_x",
            service=SERVICE_SET_COVER_POSITION,
            position=70,
            context=_ctx(policy, tilt=100),
            reason="solar",
        )
        policy._sequencer._send_tilt_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_maybe_update_tilt_only_noop(self) -> None:
        policy = _make_policy_with_seq()
        policy._last_blend = 100
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        await policy.maybe_update_tilt_only(
            "cover.dn_x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_not_awaited()

    def test_position_tilt_byte_identical_to_phase_a(self) -> None:
        # Default model (position_tilt): position unchanged, blend on the tilt
        # axis, NO split-range trace step, dispatch stays dual-axis.
        policy = DayNightShadePolicy()
        result = PipelineResult(
            position=50, control_method=ControlMethod.SOLAR, reason="solar"
        )
        out = policy.post_pipeline_resolve(
            result, cover=_solar_cover(), **_resolve_kwargs()
        )
        assert out.position == 50
        assert out.tilt == DAY_NIGHT_SHEER
        assert not any(s.handler == "day_night_split_range" for s in out.decision_trace)
        assert policy._control_model == DAY_NIGHT_MODEL_POSITION_TILT
        # Dual-axis dispatch still threads the blend.
        assert policy.position_context_overrides(out)["tilt"] == DAY_NIGHT_SHEER


# ---------------------------------------------------------------------------
# Step 17 — model-aware A3 tilt-capability contradiction (#991)
# ---------------------------------------------------------------------------


class TestTiltCapabilityContradiction:
    """A3 (#991): only Model A physically drives a tilt (blend) axis.

    The static ``axes = (POSITION_AXIS, TILT_AXIS)`` declaration is shared by all
    three control models, but only Model A (``position_tilt``) actually drives a
    physical tilt axis. Models B (``split_range``) and C (``dual_entity``) are
    single-carriage, position-only models — a position-only cover is their
    intended, supported hardware — so binding one to such a cover must NOT report
    a contradiction (which would raise a ``cover_tilt_unsupported`` Repair that
    never clears). Mirrors the config-time ``require_tilt`` gate.
    """

    _NO_TILT = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: False}
    _WITH_TILT = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: True}

    def test_model_a_position_only_caps_is_contradiction(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_POSITION_TILT
        assert policy.tilt_capability_contradiction(self._NO_TILT) is True

    def test_model_a_tilt_capable_caps_no_contradiction(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_POSITION_TILT
        assert policy.tilt_capability_contradiction(self._WITH_TILT) is False

    def test_model_b_split_range_position_only_no_contradiction(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_SPLIT_RANGE
        assert policy.tilt_capability_contradiction(self._NO_TILT) is False

    def test_model_c_dual_entity_position_only_no_contradiction(self) -> None:
        policy = DayNightShadePolicy()
        policy._control_model = DAY_NIGHT_MODEL_DUAL_ENTITY
        assert policy.tilt_capability_contradiction(self._NO_TILT) is False

    def test_default_model_is_a_and_contradicts_position_only(self) -> None:
        # A freshly-constructed policy defaults to the dual-axis Model A, so a
        # position-only cover is a genuine contradiction until options relax it.
        policy = DayNightShadePolicy()
        assert policy._control_model == DAY_NIGHT_MODEL_POSITION_TILT
        assert policy.tilt_capability_contradiction(self._NO_TILT) is True

    @pytest.mark.parametrize(
        "model", [DAY_NIGHT_MODEL_SPLIT_RANGE, DAY_NIGHT_MODEL_DUAL_ENTITY]
    )
    def test_sync_runtime_options_sets_model_before_first_health_check(
        self, model: str
    ) -> None:
        """#1114 audit MUST-FIX 1 (coordinator's cycle-1 false Repair).

        The coordinator calls the generic ``sync_runtime_options`` hook (from
        ``_update_options``) BEFORE ``_evaluate_health_checks`` on every cycle,
        including the very first one of its lifetime — which is also before
        ``post_pipeline_resolve`` has ever run. A Model B/C policy whose
        ``_control_model`` is still the ``__init__`` Model A default at that
        point would make ``_drives_dual_axis()`` return True and
        ``tilt_capability_contradiction`` falsely report a contradiction for
        a position-only cover, raising a ``cover_tilt_unsupported`` Repair
        that never clears. The hook must resolve the model from ``options`` so
        cycle 1 already knows it's Model B/C.
        """
        policy = DayNightShadePolicy()
        policy.sync_runtime_options({CONF_DAY_NIGHT_CONTROL_MODEL: model})
        # No post_pipeline_resolve call yet — this simulates the coordinator's
        # first update cycle, where the health check runs after the hook and
        # before post_pipeline_resolve ever executes.
        assert policy.tilt_capability_contradiction(self._NO_TILT) is False

    def test_build_calc_engine_does_not_mutate_control_model(self) -> None:
        """``build_calc_engine`` is a pure builder — no policy-state writes.

        ``forecast.build_forecast_for_coord`` calls it ~289× per strip from an
        executor thread, so caching the control model there would write live
        dispatch state off the event loop. Pinned by observing that a Model B
        ``options`` dict passed to the builder leaves ``_control_model``
        untouched; only ``sync_runtime_options`` moves it.
        """
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs(
            options={CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_SPLIT_RANGE}
        )
        policy.build_calc_engine(**kw)
        assert policy._control_model == DAY_NIGHT_MODEL_POSITION_TILT


# ---------------------------------------------------------------------------
# Step 17 — model-aware capability warnings
# ---------------------------------------------------------------------------


class TestCapabilityWarningsPerModel:
    """``capability_warnings_for_options`` relaxes the tilt requirement in Model B."""

    def test_split_range_warns_only_on_missing_set_position(self) -> None:
        policy = DayNightShadePolicy()
        opts = {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_SPLIT_RANGE}
        # Missing tilt but has set_position → no warning (single-axis is fine).
        assert (
            policy.capability_warnings_for_options(
                {"cover.a": {"has_set_position": True, "has_set_tilt_position": False}},
                opts,
            )
            == []
        )
        # Missing set_position → still warns.
        warn = policy.capability_warnings_for_options(
            {"cover.b": {"has_set_position": False, "has_set_tilt_position": True}},
            opts,
        )
        assert any("set_position" in w for w in warn)

    def test_position_tilt_requires_both_axes(self) -> None:
        policy = DayNightShadePolicy()
        opts = {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_POSITION_TILT}
        warn = policy.capability_warnings_for_options(
            {"cover.a": {"has_set_position": True, "has_set_tilt_position": False}},
            opts,
        )
        assert any("set_tilt_position" in w for w in warn)

    def test_default_options_require_both_axes(self) -> None:
        # No control model in options → default position_tilt → both required.
        policy = DayNightShadePolicy()
        warn = policy.capability_warnings_for_options(
            {"cover.a": {"has_set_position": True, "has_set_tilt_position": False}},
            {},
        )
        assert any("set_tilt_position" in w for w in warn)

    def test_default_matches_plain_cover_capability_warnings(self) -> None:
        # The additive hook with empty options must equal the plain method
        # (the base-contract guarantee, checked here for the overriding policy).
        policy = DayNightShadePolicy()
        known = {"cover.a": {"has_set_position": False, "has_set_tilt_position": False}}
        assert policy.capability_warnings_for_options(
            known, {}
        ) == policy.cover_capability_warnings(known)


# ---------------------------------------------------------------------------
# Issue #1137 — prospective capability warnings (pre-configuration confirm
# screen). The "Change Cover Type" confirm step (#1132/#1135) asks the
# DESTINATION policy a capability question before any of its own options
# exist, so it cannot know the control model yet. ``prospective_capability_
# warnings`` answers with the all-models intersection ``entity_selector_
# filter()`` already encodes — set_position only — rather than assuming
# Model A via ``DEFAULT_DAY_NIGHT_CONTROL_MODEL``.
# ---------------------------------------------------------------------------


class TestProspectiveCapabilityWarnings:
    """``DayNightShadePolicy.prospective_capability_warnings`` is tilt-relaxed."""

    def test_prospective_capability_warnings_omit_tilt(self) -> None:
        """A position-only cover (Model B/C hardware) draws no tilt warning.

        The control model is chosen on the NEXT screen (geometry_schema), so
        the pre-configuration question must not assume Model A.
        """
        policy = DayNightShadePolicy()
        known = {"cover.a": {"has_set_position": True, "has_set_tilt_position": False}}
        assert policy.prospective_capability_warnings(known) == []

    def test_prospective_capability_warnings_still_flag_missing_position(
        self,
    ) -> None:
        """Missing ``set_position`` still warns — every control model needs it.

        The tail wording must stay model-neutral (no "split-range" / "dual-
        entity" / "position_tilt" name) since no model has been chosen yet.
        """
        policy = DayNightShadePolicy()
        known = {"cover.a": {"has_set_position": False, "has_set_tilt_position": True}}
        warnings = policy.prospective_capability_warnings(known)
        assert any("set_position" in w for w in warnings)
        joined = " ".join(warnings)
        assert "split-range" not in joined
        assert "dual-entity" not in joined


# ---------------------------------------------------------------------------
# Step 18 — control-model select in the geometry schema + summary + labels
# ---------------------------------------------------------------------------


class TestControlModelSchemaAndSummary:
    """The control-model select renders in the geometry schema + config summary."""

    def test_geometry_schema_has_control_model_select(self) -> None:
        import voluptuous as vol

        schema = DayNightShadePolicy().geometry_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
        assert CONF_DAY_NIGHT_CONTROL_MODEL in keys
        # Defaults to the dual-axis model.
        out = schema({})
        assert out[CONF_DAY_NIGHT_CONTROL_MODEL] == DAY_NIGHT_MODEL_POSITION_TILT

    def test_geometry_schema_accepts_split_range(self) -> None:
        schema = DayNightShadePolicy().geometry_schema()
        out = schema({CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_SPLIT_RANGE})
        assert out[CONF_DAY_NIGHT_CONTROL_MODEL] == DAY_NIGHT_MODEL_SPLIT_RANGE

    def test_summary_renders_default_model(self) -> None:
        lines = DayNightShadePolicy().summary_geometry_lines({CONF_HEIGHT_WIN: 2.0})
        assert any("position and tilt" in line for line in lines)

    def test_summary_renders_split_range_model(self) -> None:
        lines = DayNightShadePolicy().summary_geometry_lines(
            {
                CONF_HEIGHT_WIN: 2.0,
                CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_SPLIT_RANGE,
            }
        )
        assert any("split range" in line for line in lines)


def test_control_model_selector_labels_in_en_translations() -> None:
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
    options = en["selector"]["day_night_control_model"]["options"]
    assert options[DAY_NIGHT_MODEL_POSITION_TILT]
    assert options[DAY_NIGHT_MODEL_SPLIT_RANGE]
    # Field label present in both config + options flows (geometry step).
    assert en["config"]["step"]["geometry"]["data"][CONF_DAY_NIGHT_CONTROL_MODEL]
    assert en["options"]["step"]["geometry"]["data"][CONF_DAY_NIGHT_CONTROL_MODEL]


# ===========================================================================
# PHASE C — Model C: two rail entities (dual_entity)
# ===========================================================================
# ---------------------------------------------------------------------------
# Step 21 — dual-entity middle-rail mapping
# ---------------------------------------------------------------------------

_MIDDLE = "cover.middle_rail"
_BOTTOM = "cover.bottom_rail"


def _dual_opts(*, middle: str = _MIDDLE, inverse: bool = False) -> dict:
    opts = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: middle,
    }
    if inverse:
        opts[CONF_INVERSE_STATE] = True
    return opts


def _resolve_dual(
    policy: DayNightShadePolicy,
    *,
    position: int,
    blend: int | None,
    inverse: bool = False,
    middle: str = _MIDDLE,
) -> PipelineResult:
    """Run a dual_entity ``post_pipeline_resolve`` so the mapping cache is set.

    A handler-supplied blend is honored verbatim, so ``blend`` lands on
    ``result.tilt`` and is cached for :meth:`resolve_entity_target`. ``None``
    exercises the cleared-blend path (MANUAL control method).
    """
    kw = _resolve_kwargs(options=_dual_opts(middle=middle, inverse=inverse))
    if blend is None:
        result = PipelineResult(
            position=position, control_method=ControlMethod.MANUAL, reason="manual"
        )
    else:
        result = PipelineResult(
            position=position,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=blend,
        )
    return policy.post_pipeline_resolve(result, cover=None, **kw)


class TestDualEntityMapping:
    """``resolve_entity_target`` remaps the middle rail; the bottom rail passes."""

    @pytest.mark.parametrize(
        ("position", "blend", "expected_middle"),
        [
            # blend 100 (all sheer): middle coincides with the bottom rail.
            (40, 100, 40),
            (0, 100, 0),
            # blend 0 (all blackout): middle fully open.
            (40, 0, 100),
            (0, 0, 100),
            # blend 50, P=40 → M = 100 - 50*(100-40)/100 = 70.
            (40, 50, 70),
        ],
    )
    def test_middle_rail_mapping(
        self, position: int, blend: int, expected_middle: int
    ) -> None:
        policy = DayNightShadePolicy()
        _resolve_dual(policy, position=position, blend=blend)
        assert policy.resolve_entity_target(_MIDDLE, position) == expected_middle

    def test_bottom_rail_passes_through(self) -> None:
        policy = DayNightShadePolicy()
        _resolve_dual(policy, position=40, blend=50)
        # The primary (bottom) rail is never remapped.
        assert policy.resolve_entity_target(_BOTTOM, 40) == 40

    def test_unknown_entity_passes_through(self) -> None:
        policy = DayNightShadePolicy()
        _resolve_dual(policy, position=40, blend=50)
        assert policy.resolve_entity_target("cover.some_other", 40) == 40

    def test_blend_none_is_identity(self) -> None:
        policy = DayNightShadePolicy()
        _resolve_dual(policy, position=60, blend=None)
        # No fabric decision this cycle → middle rail follows the primary.
        assert policy.resolve_entity_target(_MIDDLE, 60) == 60

    def test_non_dual_entity_model_is_identity(self) -> None:
        # A position_tilt (Model A) cycle must never remap any entity.
        policy = DayNightShadePolicy()
        kw = _resolve_kwargs()
        result = PipelineResult(
            position=40,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="custom",
            tilt=50,
        )
        policy.post_pipeline_resolve(result, cover=None, **kw)
        assert policy.resolve_entity_target(_MIDDLE, 40) == 40

    @pytest.mark.parametrize("position", range(0, 101, 10))
    @pytest.mark.parametrize("blend", range(0, 101, 10))
    def test_no_pass_invariant_middle_ge_bottom(
        self, position: int, blend: int
    ) -> None:
        # No-pass: the middle rail never drops below the bottom rail (open %).
        policy = DayNightShadePolicy()
        _resolve_dual(policy, position=position, blend=blend)
        assert policy.resolve_entity_target(_MIDDLE, position) >= position

    def test_inverse_state_round_trip(self) -> None:
        # With inverse state on, the wire value must un-invert to the same
        # open-percent middle-rail position the non-inverse mapping produces.
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )

        p_open, blend = 40, 50
        plain = DayNightShadePolicy()
        _resolve_dual(plain, position=p_open, blend=blend)
        open_middle = plain.resolve_entity_target(_MIDDLE, p_open)

        inv = DayNightShadePolicy()
        wire_p = inverse_state(p_open)  # bottom rail wire value
        _resolve_dual(inv, position=wire_p, blend=blend, inverse=True)
        wire_middle = inv.resolve_entity_target(_MIDDLE, wire_p)

        assert inverse_state(wire_middle) == open_middle
        # And the no-pass invariant still holds in wire space (inverted order).
        assert wire_middle <= wire_p


# ---------------------------------------------------------------------------
# Step 24 — dual-entity capability warnings + middle-rail picker
# ---------------------------------------------------------------------------


class TestDualEntityCapabilityWarnings:
    """``dual_entity`` needs set_position on each rail, no tilt, a valid middle."""

    def test_warns_on_missing_set_position_per_rail(self) -> None:
        policy = DayNightShadePolicy()
        opts = {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
        }
        warn = policy.capability_warnings_for_options(
            {
                _BOTTOM: {"has_set_position": True, "has_set_tilt_position": False},
                _MIDDLE: {"has_set_position": False, "has_set_tilt_position": False},
            },
            opts,
        )
        assert any("set_position" in w and _MIDDLE in w for w in warn)

    def test_requires_no_tilt(self) -> None:
        # Both rails have set_position but no tilt → no warning (Model C is a
        # pair of position axes, never a tilt axis).
        policy = DayNightShadePolicy()
        opts = {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
        }
        warn = policy.capability_warnings_for_options(
            {
                _BOTTOM: {"has_set_position": True, "has_set_tilt_position": False},
                _MIDDLE: {"has_set_position": True, "has_set_tilt_position": False},
            },
            opts,
        )
        assert not any("set_tilt_position" in w for w in warn)

    def test_warns_when_middle_rail_not_among_covers(self) -> None:
        policy = DayNightShadePolicy()
        opts = {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: "cover.not_configured",
        }
        warn = policy.capability_warnings_for_options(
            {
                _BOTTOM: {"has_set_position": True, "has_set_tilt_position": True},
                _MIDDLE: {"has_set_position": True, "has_set_tilt_position": True},
            },
            opts,
        )
        assert any("cover.not_configured" in w for w in warn)

    def test_no_middle_warning_when_middle_is_a_configured_cover(self) -> None:
        policy = DayNightShadePolicy()
        opts = {
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
            CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
        }
        warn = policy.capability_warnings_for_options(
            {
                _BOTTOM: {"has_set_position": True, "has_set_tilt_position": True},
                _MIDDLE: {"has_set_position": True, "has_set_tilt_position": True},
            },
            opts,
        )
        assert warn == []

    def test_warns_when_middle_rail_unset(self) -> None:
        """An ABSENT middle rail must warn too (issue #1115).

        ``if middle and middle not in known`` short-circuits on the unset value,
        so a Model C instance with no middle rail configured used to pass config
        validation silently and then behave like a plain vertical blind at
        runtime — both rails driven to the bottom rail's position.
        """
        policy = DayNightShadePolicy()
        opts = {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY}
        warn = policy.capability_warnings_for_options(
            {
                _BOTTOM: {"has_set_position": True, "has_set_tilt_position": True},
                _MIDDLE: {"has_set_position": True, "has_set_tilt_position": True},
            },
            opts,
        )
        assert any("middle-rail" in w for w in warn)

    def test_no_unset_warning_for_other_models(self) -> None:
        """Models A and B bind no middle rail — an absent value is not a problem."""
        policy = DayNightShadePolicy()
        known = {
            _BOTTOM: {"has_set_position": True, "has_set_tilt_position": True},
            _MIDDLE: {"has_set_position": True, "has_set_tilt_position": True},
        }
        for model in (DAY_NIGHT_MODEL_POSITION_TILT, DAY_NIGHT_MODEL_SPLIT_RANGE):
            warn = policy.capability_warnings_for_options(
                known, {CONF_DAY_NIGHT_CONTROL_MODEL: model}
            )
            assert warn == []


class TestDualEntityRequiredRoleEntity:
    """``required_role_entity_missing`` drives the B3 runtime Repair (issue #1115)."""

    def test_true_when_middle_rail_unset(self) -> None:
        policy = DayNightShadePolicy()
        assert (
            policy.required_role_entity_missing(
                {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY},
                [_BOTTOM, _MIDDLE],
            )
            is True
        )

    def test_true_when_middle_rail_not_among_covers(self) -> None:
        policy = DayNightShadePolicy()
        assert (
            policy.required_role_entity_missing(
                {
                    CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
                    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: "cover.not_configured",
                },
                [_BOTTOM, _MIDDLE],
            )
            is True
        )

    def test_false_when_coherent(self) -> None:
        policy = DayNightShadePolicy()
        assert (
            policy.required_role_entity_missing(
                {
                    CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
                    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _MIDDLE,
                },
                [_BOTTOM, _MIDDLE],
            )
            is False
        )

    @pytest.mark.parametrize(
        "model", [DAY_NIGHT_MODEL_POSITION_TILT, DAY_NIGHT_MODEL_SPLIT_RANGE]
    )
    def test_false_for_models_a_and_b(self, model: str) -> None:
        """Only Model C binds a second rail entity — A/B never report a gap."""
        policy = DayNightShadePolicy()
        assert (
            policy.required_role_entity_missing(
                {CONF_DAY_NIGHT_CONTROL_MODEL: model}, [_BOTTOM]
            )
            is False
        )


def test_geometry_schema_has_middle_rail_picker() -> None:
    import voluptuous as vol

    schema = DayNightShadePolicy().geometry_schema()
    keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
    assert CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY in keys


def test_live_option_keys_include_dual_entity_keys() -> None:
    keys = DayNightShadePolicy().live_option_keys()
    assert CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY in keys


def test_summary_renders_dual_entity_model() -> None:
    lines = DayNightShadePolicy().summary_geometry_lines(
        {
            CONF_HEIGHT_WIN: 2.0,
            CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        }
    )
    assert any("two rail" in line for line in lines)


def test_dual_entity_labels_present_in_en_translations() -> None:
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
    # The third control-model value label.
    assert en["selector"]["day_night_control_model"]["options"][
        DAY_NIGHT_MODEL_DUAL_ENTITY
    ]
    # The middle-rail field label in both config + options geometry steps.
    assert en["config"]["step"]["geometry"]["data"][CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY]
    assert en["options"]["step"]["geometry"]["data"][CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY]
