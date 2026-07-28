"""Tests for VenetianPolicy.post_pipeline_resolve.

Covers the SOLAR gate (tilt is only computed when the solar pipeline won)
and the tilt-only mode position rewrite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types.venetian import VenetianPolicy
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


def _make_result(method: ControlMethod, position: int = 50) -> PipelineResult:
    return PipelineResult(position=position, control_method=method, reason="test")


def _make_policy() -> VenetianPolicy:
    return VenetianPolicy()


def _make_cover(*, direct_sun_valid: bool = True):
    """Build a minimal cover mock for post_pipeline_resolve tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    return cover


def _config_service_stub():
    """Minimal config_service stub that returns objects the engine can use."""
    from tests.cover_helpers import make_tilt_config, make_vertical_config

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_tilt_data.return_value = make_tilt_config()
    return svc


def _solar_kwargs():
    """Kwargs suitable for a SOLAR post_pipeline_resolve call (direct sun valid)."""
    from tests.cover_helpers import make_cover_config

    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return {
        "cover": _make_cover(direct_sun_valid=True),
        "logger": MagicMock(),
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": sun_data,
        "config": make_cover_config(),
        "config_service": _config_service_stub(),
        "options": {},
    }


def _non_solar_kwargs():
    """Kwargs for a non-SOLAR call — dependencies should never be touched."""
    return {
        "logger": MagicMock(),
        "sol_azi": 0.0,
        "sol_elev": -10.0,
        "sun_data": MagicMock(),
        "config": MagicMock(),
        "config_service": MagicMock(),
        "options": {},
    }


class TestPostPipelineResolveSolarGate:
    """Tilt is meaningful only when the solar handler drove the position decision."""

    def test_tilt_set_when_control_method_is_solar(self):
        policy = _make_policy()
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR), **_solar_kwargs()
        )
        assert out.tilt is not None

    @pytest.mark.parametrize(
        "method",
        [
            ControlMethod.DEFAULT,
            ControlMethod.MANUAL,
            ControlMethod.WEATHER,
            ControlMethod.FORCE,
            ControlMethod.MOTION,
            ControlMethod.CUSTOM_POSITION,
            ControlMethod.SUMMER,
            ControlMethod.WINTER,
            ControlMethod.CLOUD,
            ControlMethod.GLARE_ZONE,
        ],
    )
    def test_tilt_is_none_for_non_solar_control_method(self, method):
        policy = _make_policy()
        out = policy.post_pipeline_resolve(_make_result(method), **_non_solar_kwargs())
        assert out.tilt is None

    def test_non_solar_position_is_unchanged(self):
        """The position must not be altered for non-solar decisions."""
        policy = _make_policy()
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.WEATHER, position=75), **_non_solar_kwargs()
        )
        assert out.position == 75

    def test_none_result_returned_unchanged(self):
        """Guard against coordinator passing None on cold-start."""
        policy = _make_policy()
        out = policy.post_pipeline_resolve(None, **_non_solar_kwargs())
        assert out is None


class TestPostPipelineResolveTiltOnlyMode:
    """tilt_only mode forces position to 0 when solar drives the decision."""

    def test_tilt_only_rewrites_position_to_zero_for_solar(self):
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR, position=50), **_solar_kwargs()
        )
        assert out.position == 0
        assert out.tilt is not None

    def test_tilt_only_records_venetian_mode_trace_step(self):
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR, position=50), **_solar_kwargs()
        )
        handler_names = [s.handler for s in out.decision_trace]
        assert "venetian_mode" in handler_names

    def test_tilt_only_does_not_rewrite_for_non_solar(self):
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.WEATHER, position=80), **_non_solar_kwargs()
        )
        assert out.position == 80
        assert out.tilt is None


class TestPostPipelineResolveCoverageSteps:
    """Movement minimization quantizes the slat tilt toward full coverage."""

    @staticmethod
    def _patch_tilt(monkeypatch, value: int) -> None:
        """Force the engine-computed slat angle to a known intermediate value."""
        from custom_components.adaptive_cover_pro.engine.covers import (
            VenetianCoverCalculation,
        )

        monkeypatch.setattr(
            VenetianCoverCalculation,
            "tilt_for_position",
            lambda self, position: value,
        )

    def test_n1_snaps_tilt_fully_closed(self, monkeypatch):
        from custom_components.adaptive_cover_pro.const import (
            CONF_MAX_COVERAGE_STEPS,
            CONF_MINIMIZE_MOVEMENTS,
        )

        self._patch_tilt(monkeypatch, 70)
        policy = _make_policy()
        kwargs = _solar_kwargs()
        kwargs["options"] = {CONF_MINIMIZE_MOVEMENTS: True, CONF_MAX_COVERAGE_STEPS: 1}
        out = policy.post_pipeline_resolve(_make_result(ControlMethod.SOLAR), **kwargs)
        assert out.tilt == 0  # tilt 0% = slats fully closed = full coverage

    def test_n2_rounds_tilt_toward_coverage(self, monkeypatch):
        from custom_components.adaptive_cover_pro.const import (
            CONF_MAX_COVERAGE_STEPS,
            CONF_MINIMIZE_MOVEMENTS,
        )

        self._patch_tilt(monkeypatch, 70)
        policy = _make_policy()
        kwargs = _solar_kwargs()
        kwargs["options"] = {CONF_MINIMIZE_MOVEMENTS: True, CONF_MAX_COVERAGE_STEPS: 2}
        out = policy.post_pipeline_resolve(_make_result(ControlMethod.SOLAR), **kwargs)
        # coverage 0.30 → rounds up to the 0.50 level → tilt 50%.
        assert out.tilt == 50

    def test_disabled_leaves_tilt_unquantized(self, monkeypatch):
        from custom_components.adaptive_cover_pro.const import CONF_MINIMIZE_MOVEMENTS

        self._patch_tilt(monkeypatch, 70)
        policy = _make_policy()
        kwargs = _solar_kwargs()
        kwargs["options"] = {CONF_MINIMIZE_MOVEMENTS: False}
        out = policy.post_pipeline_resolve(_make_result(ControlMethod.SOLAR), **kwargs)
        assert out.tilt == 70

    def test_position_and_tilt_mode_does_not_rewrite_position(self):
        """Default mode must not collapse position to 0."""
        policy = _make_policy()
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR, position=50), **_solar_kwargs()
        )
        assert out.position == 50

    def test_tilt_only_honors_explicit_custom_position(self):
        """tilt_only must not rewrite position when a custom-position handler supplied it.

        Regression for issue #499: CUSTOM_POSITION + tilt_only silently dropped
        the user-configured position by collapsing it to POSITION_CLOSED.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=100,
            control_method=ControlMethod.CUSTOM_POSITION,
            tilt=100,
            reason="test",
        )
        out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
        assert out.position == 100
        assert out.tilt == 100


class TestPostPipelineResolveTiltOnlyContribution:
    """Per-slot tilt-only overlay (issue #514) honored; position stays solar."""

    def test_overlaid_tilt_honored_position_stays_solar(self):
        """SOLAR winner + overlaid tilt → tilt honored, position unchanged.

        Default venetian mode (position_and_tilt): the registry overlaid a
        tilt-only slot's slat angle onto a SOLAR result. The position pipeline
        drives the carriage; the overlaid tilt rides through unchanged.
        """
        policy = _make_policy()
        result = PipelineResult(
            position=60,
            control_method=ControlMethod.SOLAR,
            tilt=25,
            tilt_only_contribution_active=True,
            reason="test",
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.position == 60
        assert out.tilt == 25

    def test_global_tilt_only_suppressed_when_contribution_active(self):
        """Per-slot tilt-only suppresses the global tilt-only carriage-close.

        When the global venetian mode is tilt_only AND a per-slot tilt-only
        contribution drives the slat angle, the carriage must stay at the
        position the pipeline resolved (solar) instead of being forced closed
        (decision Q2).
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=60,
            control_method=ControlMethod.SOLAR,
            tilt=25,
            tilt_only_contribution_active=True,
            reason="test",
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.position == 60
        assert out.tilt == 25
        # The global tilt-only carriage-close trace step must NOT appear.
        assert "venetian_mode" not in [s.handler for s in out.decision_trace]

    def test_global_tilt_only_still_closes_without_contribution(self):
        """Without a per-slot contribution, global tilt-only still closes."""
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=60,
            control_method=ControlMethod.SOLAR,
            tilt=25,
            tilt_only_contribution_active=False,
            reason="test",
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.position == 0
        assert "venetian_mode" in [s.handler for s in out.decision_trace]


class TestPostPipelineResolveNoSunStrip:
    """Tilt must be stripped when SOLAR is emitted but direct sun is not hitting the window.

    Issue #33: the climate handler emits ControlMethod.SOLAR on its LOW_LIGHT
    branch even when cover.direct_sun_valid=False (post-sunset). Without a
    direct_sun_valid guard, post_pipeline_resolve synthesises a tilt from the
    still-drifting sun azimuth and the DualAxisSequencer sends tilt commands
    every ~4 minutes overnight.
    """

    def test_tilt_stripped_when_solar_but_direct_sun_invalid(self):
        """ControlMethod.SOLAR + direct_sun_valid=False → tilt must be None."""
        policy = _make_policy()
        cover = _make_cover(direct_sun_valid=False)
        kwargs = _solar_kwargs()
        kwargs["cover"] = cover
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR),
            **kwargs,
        )
        assert out.tilt is None

    def test_tilt_stripped_when_solar_and_sunset_valid(self):
        """SOLAR + direct_sun_valid=False + sunset_valid=True → tilt still None.

        sunset_valid does not grant a direct-sun exemption; only direct_sun_valid does.
        """
        policy = _make_policy()
        cover = _make_cover(direct_sun_valid=False)
        cover.sunset_valid = True
        kwargs = _solar_kwargs()
        kwargs["cover"] = cover
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR),
            **kwargs,
        )
        assert out.tilt is None

    def test_tilt_computed_when_solar_and_direct_sun_valid(self):
        """Regression guard: SOLAR + direct_sun_valid=True → tilt must still be computed."""
        policy = _make_policy()
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR),
            **_solar_kwargs(),
        )
        assert out.tilt is not None

    def test_last_tilt_not_updated_when_sun_invalid(self):
        """When tilt is stripped due to invalid sun, _last_tilt must remain None."""
        policy = _make_policy()
        cover = _make_cover(direct_sun_valid=False)
        kwargs = _solar_kwargs()
        kwargs["cover"] = cover
        policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR),
            **kwargs,
        )
        assert policy._last_tilt is None


class TestPostPipelineResolveClearsLastTilt:
    """Issue #33: a suppressed cycle must reset ``_last_tilt`` so the next
    ``maybe_update_tilt_only`` cycle doesn't replay the prior solar tilt.

    Without this, a solar cycle (which sets ``_last_tilt = N``) followed by a
    non-SOLAR / no-direct-sun cycle leaves ``_last_tilt`` armed, and the
    tilt-only refresh keeps firing the stale solar tilt against an actuator
    that should be neutral. The user sees HA reporting e.g. 100/55 forever.
    """

    def test_suppressed_call_clears_prior_solar_last_tilt(self):
        """Non-SOLAR control method must clear a primed ``_last_tilt``."""
        policy = _make_policy()
        policy._last_tilt = 70  # simulate prior solar cycle's resolved tilt
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.WEATHER), **_non_solar_kwargs()
        )
        assert policy._last_tilt is None
        assert out.tilt is None

    def test_solar_with_no_direct_sun_clears_prior_last_tilt(self):
        """SOLAR with ``direct_sun_valid=False`` must clear a primed ``_last_tilt``.

        This is the climate-handler low-light branch — pipeline emits SOLAR
        but the cover engine reports the sun isn't on the window.
        """
        policy = _make_policy()
        policy._last_tilt = 55
        kwargs = _solar_kwargs()
        kwargs["cover"] = _make_cover(direct_sun_valid=False)
        out = policy.post_pipeline_resolve(_make_result(ControlMethod.SOLAR), **kwargs)
        assert policy._last_tilt is None
        assert out.tilt is None

    def test_none_result_does_not_clobber_last_tilt(self):
        """The ``result is None`` early-return must not touch ``_last_tilt``."""
        policy = _make_policy()
        policy._last_tilt = 42
        policy.post_pipeline_resolve(None, **_non_solar_kwargs())
        assert policy._last_tilt == 42


class TestPostPipelineResolveHandlerTilt:
    """Steps 9-10-11: when result.tilt is set by a handler, venetian policy honors it
    (only when SOLAR + direct_sun_valid — suppression check runs first).
    """

    def test_handler_tilt_honored_for_solar_with_direct_sun(self):
        """SOLAR + direct_sun_valid + result.tilt=35 → resolved.tilt=35 (not engine tilt)."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=35,
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.tilt == 35

    def test_handler_tilt_zero_honored_for_solar(self):
        """tilt=0 is a valid explicit value — not treated as falsy None."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=0,
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.tilt == 0

    def test_handler_tilt_trace_step_on_solar_path(self):
        """SOLAR + direct_sun_valid + handler tilt → trace has 'venetian_handler_tilt'."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=35,
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        handler_names = [s.handler for s in out.decision_trace]
        assert "venetian_handler_tilt" in handler_names

    def test_handler_tilt_honored_for_custom_position(self):
        """Handler-supplied tilt on CUSTOM_POSITION must survive (issue #369 regression)."""
        policy = _make_policy()
        for handler_tilt in (42, 0):
            result = PipelineResult(
                position=50,
                control_method=ControlMethod.CUSTOM_POSITION,
                reason="test",
                tilt=handler_tilt,
            )
            out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
            assert out.tilt == handler_tilt
            assert out.position == 50

    def test_handler_tilt_honored_for_default_path(self):
        """Handler-supplied tilt on DEFAULT (default_tilt / sunset_tilt) must survive."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.DEFAULT,
            reason="test",
            tilt=30,
        )
        out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
        assert out.tilt == 30

    def test_handler_tilt_honored_when_direct_sun_invalid(self):
        """Explicit handler tilt bypasses the direct_sun_valid gate."""
        policy = _make_policy()
        cover = _make_cover(direct_sun_valid=False)
        kwargs = _solar_kwargs()
        kwargs["cover"] = cover
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=50,
        )
        out = policy.post_pipeline_resolve(result, **kwargs)
        assert out.tilt == 50

    def test_handler_tilt_survives_non_solar_suppression(self):
        """Non-SOLAR with handler-supplied tilt must honor it (was the bug in #369)."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.DEFAULT,
            reason="test",
            tilt=35,
        )
        out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
        assert out.tilt == 35

    def test_engine_tilt_used_when_result_tilt_is_none_on_solar(self):
        """SOLAR + direct_sun_valid + result.tilt=None → engine computes tilt (not None)."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=None,
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert out.tilt is not None

    def test_engine_trace_step_used_when_result_tilt_is_none_on_solar(self):
        """When no handler tilt, 'venetian_engine' trace step is emitted (not handler_tilt)."""
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            tilt=None,
        )
        out = policy.post_pipeline_resolve(result, **_solar_kwargs())
        handler_names = [s.handler for s in out.decision_trace]
        assert "venetian_engine" in handler_names
        assert "venetian_handler_tilt" not in handler_names


class TestPostPipelineResolveTiltSubTrace:
    """The venetian tilt sub-trace is merged into the position engine's trace.

    Issue #682: the tilt engine inside ``post_pipeline_resolve`` is transient and
    its ``_last_calc_details`` was discarded. The merge writes it under a ``tilt``
    sub-key on the position engine's (``cover``) ``_last_calc_details`` so the
    live ``solar_calculation`` sensor and the diagnostics download both surface
    both axes.
    """

    @staticmethod
    def _cover_with_trace(*, direct_sun_valid: bool = True):
        """Build a cover stub with a real-dict _last_calc_details (position trace)."""
        cover = MagicMock()
        cover.direct_sun_valid = direct_sun_valid
        # Real dict, as the vertical engine would have set during the pipeline.
        cover._last_calc_details = {
            "sol_elev_deg": 45.0,
            "gamma_deg": 0.0,
            "position_pct": 25,
            "effective_distance_m": 0.5,
        }
        return cover

    def test_tilt_subtrace_present_after_solar_resolve(self):
        policy = _make_policy()
        kwargs = dict(_solar_kwargs())
        cover = self._cover_with_trace()
        kwargs["cover"] = cover
        policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR, position=50), **kwargs
        )
        details = cover._last_calc_details
        assert "tilt" in details
        # The tilt sub-trace carries the tilt-engine keys.
        assert "beta_rad" in details["tilt"]
        assert "tilt_mode" in details["tilt"]
        # The position (vertical) keys remain at the top level.
        assert "effective_distance_m" in details

    def test_no_tilt_subtrace_when_tilt_suppressed(self):
        """Suppressed-tilt branch must NOT merge a tilt sub-trace."""
        policy = _make_policy()
        cover = self._cover_with_trace(direct_sun_valid=False)
        kwargs = dict(_solar_kwargs())
        kwargs["cover"] = cover
        # direct_sun_valid False → tilt suppressed; the merge must be guarded.
        policy.post_pipeline_resolve(
            _make_result(ControlMethod.SOLAR, position=80), **kwargs
        )
        assert "tilt" not in cover._last_calc_details


# ---------------------------------------------------------------------------
# Engine-tilt clamping against carried tilt bounds — issue #943
#
# Tilt can resolve AFTER the pipeline: the registry has no tilt to clamp when
# the venetian engine is the thing that produces it. The registry therefore
# carries the composed bounds on the result and this policy applies them —
# through the same shared clamp the registry uses, so the arithmetic stays in
# one place while the cover-type-specific behavior stays in cover_types/.
# ---------------------------------------------------------------------------


class TestEngineTiltBounds:
    """post_pipeline_resolve clamps the engine tilt to the carried bounds."""

    @staticmethod
    def _resolve(monkeypatch, engine_tilt: int, **bounds):
        policy = _make_policy()
        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="test",
            **bounds,
        )
        monkeypatch.setattr(
            VenetianPolicy,
            "_compose_tilt",
            lambda self, *a, **kw: (engine_tilt, MagicMock()),
        )
        monkeypatch.setattr(
            VenetianPolicy, "_engine_tilt_suppressed", lambda self, r, c: False
        )
        return policy, policy.post_pipeline_resolve(result, **_solar_kwargs())

    def test_engine_tilt_raised_to_tilt_low(self, monkeypatch) -> None:
        """The reporter's case: engine 30 with a minimum of 50 → 50."""
        _, out = self._resolve(monkeypatch, 30, tilt_low=50)
        assert out.tilt == 50

    def test_engine_tilt_above_low_unchanged(self, monkeypatch) -> None:
        """The reporter's acceptance pair: engine 75 with min 50 stays 75."""
        _, out = self._resolve(monkeypatch, 75, tilt_low=50)
        assert out.tilt == 75

    def test_engine_tilt_lowered_to_tilt_high(self, monkeypatch) -> None:
        """The ceiling mirror."""
        _, out = self._resolve(monkeypatch, 90, tilt_high=60)
        assert out.tilt == 60

    def test_clamped_tilt_recorded_as_last_tilt(self, monkeypatch) -> None:
        """Drift tracking must see the value actually sent, not the raw one."""
        policy, _ = self._resolve(monkeypatch, 30, tilt_low=50)
        assert policy._last_tilt == 50

    def test_clamp_appends_trace_step(self, monkeypatch) -> None:
        """The clamp is visible in the decision trace."""
        _, out = self._resolve(monkeypatch, 30, tilt_low=50)
        assert any(s.handler == "tilt_clamp" for s in out.decision_trace)

    def test_no_bounds_leaves_engine_tilt_untouched(self, monkeypatch) -> None:
        """Without bounds the behavior is byte-identical to before #943."""
        _, out = self._resolve(monkeypatch, 30)
        assert out.tilt == 30
        assert not any(s.handler == "tilt_clamp" for s in out.decision_trace)

    def test_range_bounds_clamp_both_ways(self, monkeypatch) -> None:
        """A carried range bounds the engine tilt on both sides."""
        _, low = self._resolve(monkeypatch, 10, tilt_low=40, tilt_high=80)
        _, high = self._resolve(monkeypatch, 95, tilt_low=40, tilt_high=80)
        assert (low.tilt, high.tilt) == (40, 80)
