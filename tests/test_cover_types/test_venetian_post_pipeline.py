"""Tests for VenetianPolicy.post_pipeline_resolve.

Covers the SOLAR gate (tilt is only computed when the solar pipeline won)
and the tilt-only mode position rewrite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types.venetian import VenetianPolicy
from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    ControlMethod,
    ReasonCode,
)
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

    def test_tilt_only_pins_carriage_for_non_solar_non_explicit_winner(self):
        """Issue #1153 finding 2: the pin must not depend on a leaked tilt.

        A non-SOLAR, non-explicit-user-position winner (e.g. a climate SUMMER
        decision) reaches the engine-suppressed early-return branch with
        ``result.tilt`` genuinely ``None`` — no handler and no engine ever
        supply one. Before the fix, that branch returned early and skipped
        the tilt-only carriage pin entirely, so the carriage opened to the
        winner's raw position instead of staying closed — the pin only
        worked when a (possibly leaked) tilt happened to route through the
        handler-tilt branch instead. ``ControlMethod.WEATHER`` above is
        exempt (an explicit user position, see ``_EXPLICIT_USER_POSITION_METHODS``);
        SUMMER is not, so the pin must apply here.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SUMMER, position=70), **_non_solar_kwargs()
        )
        assert out.position == 0
        assert out.tilt is None
        assert "venetian_mode" in [s.handler for s in out.decision_trace]

    def test_tilt_only_does_not_pin_group_scene_winner(self):
        """Issue #1153 round-2 finding 1: GROUP_SCENE is explicit user intent.

        ``GroupSceneHandler`` sets ``bypass_auto_control=True`` with the
        comment "Explicit user intent: runs even with automatic control off
        — same semantics as custom-position slots"
        (pipeline/handlers/group_scene.py) and never supplies a tilt, so this
        exercises the engine-suppressed exit path (finding 1's probe:
        ``engine_suppressed contrib=0 group_scene out_pos=100 -> out_pos=0``
        before this fix). A user picking a scene like "All open" must not
        have the venetian tilt-only pin silently rewrite the carriage back
        to closed.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.GROUP_SCENE, position=100),
            **_non_solar_kwargs(),
        )
        assert out.position == 100
        assert out.tilt is None
        assert "venetian_mode" not in [s.handler for s in out.decision_trace]

    def test_tilt_only_does_not_pin_group_lock_winner(self):
        """Issue #1153 round-2 finding 1: GROUP_LOCK holds the current position.

        ``GroupLockHandler`` carries ``skip_command=True`` — nothing
        physically moves — but the published target must still agree with
        its own ``held_position`` rather than being rewritten to
        ``POSITION_CLOSED``. It is the third member of the hold family;
        ``MANUAL`` and ``MOTION`` are both already exempt.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.GROUP_LOCK, position=65),
            **_non_solar_kwargs(),
        )
        assert out.position == 65
        assert out.tilt is None
        assert "venetian_mode" not in [s.handler for s in out.decision_trace]

    def test_tilt_only_does_not_pin_default_winner_at_default_percentage(self):
        """Issue #1153 round-2 finding 2: DEFAULT is not a tilt-only-pinned method.

        Tilt-only mode is a sun-tracking-*window* behavior; ``DEFAULT`` is by
        definition the no-handler-fired fallback that wins OUTSIDE that
        window and carries ``default_percentage``. Pinning it would silently
        disable the option's documented carriage movement for every
        tilt-only install (probe:
        ``engine_suppressed contrib=0 default out_pos=100 -> out_pos=0``
        before this fix — including the reporter's own diagnostics:
        ``default_percentage = 100``, ``venetian_mode = tilt_only``).
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.DEFAULT, position=100), **_non_solar_kwargs()
        )
        assert out.position == 100
        assert out.tilt is None
        assert "venetian_mode" not in [s.handler for s in out.decision_trace]

    def test_tilt_only_does_not_pin_default_winner_during_sunset(self):
        """Issue #1153 round-2 finding 2: DEFAULT carries sunset_position too.

        ``post_pipeline_resolve`` only sees the resolved ``PipelineResult`` —
        ``is_sunset_active`` is upstream state ``DefaultHandler`` consulted
        when it picked ``sunset_position`` for ``position`` — but the same
        DEFAULT exemption must hold regardless of which of the three
        fallback options (default_percentage / sunset_position /
        return_sunset) supplied the value.

        This is an intent-documenting test, not independent coverage: it
        rides the exact same code path as
        ``test_tilt_only_does_not_pin_default_winner_at_default_percentage``
        above — ``_pin_tilt_only_carriage`` never reads ``is_sunset_active``,
        so nothing here can fail in a way that test wouldn't already catch.
        It stays because ``sunset_position`` / ``return_sunset`` are exactly
        the fallback options the DEFAULT exemption was written to rescue,
        and a future reader should not have to re-derive that from the
        production code.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=45,
            control_method=ControlMethod.DEFAULT,
            reason="test",
            is_sunset_active=True,
        )
        out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
        assert out.position == 45
        assert out.tilt is None
        assert "venetian_mode" not in [s.handler for s in out.decision_trace]

    def test_tilt_only_pin_skips_every_per_cover_judged_winner(self):
        """The premise ``entities_move_independently`` rests on, locked as a SET.

        That override says the venetian policy's only write to
        ``PipelineResult.position`` — the tilt-only carriage pin — never fires
        for a winner the registry hands per-cover ``hold_clamp_verdicts``. Its
        docstring used to justify that with "``MANUAL`` and ``GROUP_LOCK``, the
        only two winners that ever set ``held_position``", which #943 item B
        made false: ``_as_outside_window_pseudo_hold`` sets ``held_position`` on
        whichever non-safety handler computed a closed-clock cycle, and then
        strips it again before the result leaves the registry — so nothing
        arriving here even looks like a hold.

        Each method below already has its own test above; none of them tied the
        set to the premise, so a seventh per-cover-judgable winner could be
        added with the pin still firing on it and nothing would fail. This is
        that tie. ``SUMMER`` is the negative control: it is NOT per-cover
        judgable (a windowed handler cannot win outside the clock window, and it
        sets no ``held_position`` inside one), and the pin does fire on it.

        The convertible half is derived from the handlers, not counted: a
        handler qualifies when it neither gates on ``snapshot.in_time_window``
        nor sets ``held_position``, and is not ``is_safety``. ``MOTION`` passes
        because only its hold_position branch reads ``in_time_window`` — the
        return-to-default branch it falls through to outside the window is
        ungated and sets nothing.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        # Real holds set ``held_position`` themselves; the other four are the
        # non-safety, non-windowed winners the outside-window pseudo-hold can
        # convert (#943 item B).
        per_cover_judged = (
            ControlMethod.MANUAL,
            ControlMethod.GROUP_LOCK,
            ControlMethod.DEFAULT,
            ControlMethod.CUSTOM_POSITION,
            ControlMethod.MOTION,
            ControlMethod.GROUP_SCENE,
        )
        for method in per_cover_judged:
            policy = _make_policy()
            policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
            out = policy.post_pipeline_resolve(
                _make_result(method, position=72), **_non_solar_kwargs()
            )
            assert out.position == 72, f"{method} was pinned"
            assert "venetian_mode" not in [s.handler for s in out.decision_trace]

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(ControlMethod.SUMMER, position=72), **_non_solar_kwargs()
        )
        assert out.position == 0
        assert "venetian_mode" in [s.handler for s in out.decision_trace]

    def test_tilt_only_does_not_pin_default_winner_with_handler_tilt(self):
        """Issue #1153 audit finding 1: the DEFAULT exemption on the OTHER exit path.

        The two tests above both exercise the *engine-suppressed* branch
        (``result.tilt is None`` going in). This test drives
        ``default_tilt`` being configured, so ``DefaultHandler`` stamps a
        non-``None`` tilt directly onto its own winning result — that routes
        ``post_pipeline_resolve`` through the *handler-tilt* branch instead
        (``result.tilt is not None`` at the top of the method), a different
        call site of ``_pin_tilt_only_carriage``. Before this issue's fix
        that branch had no DEFAULT exemption at all: the develop code pinned
        the carriage shut here, silently disabling ``default_tilt`` on every
        tilt-only install. It is safe today only because one shared helper
        now serves all three call sites — this test locks that coverage
        down instead of leaving it to depend on that fact.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=100,
            tilt=55,
            control_method=ControlMethod.DEFAULT,
            reason="test",
        )
        out = policy.post_pipeline_resolve(result, **_non_solar_kwargs())
        handler_names = [s.handler for s in out.decision_trace]
        # Prove the handler-tilt branch ran, not the engine-suppressed one.
        assert "venetian_handler_tilt" in handler_names
        assert out.position == 100
        assert out.tilt == 55
        assert "venetian_mode" not in handler_names


class TestIssue1214DefaultTiltEndpointHandoff:
    """Issue #1214: a DEFAULT winner now carrying the effective default/sunset
    tilt (climate LOW_LIGHT, cloud suppression, or motion return-to-default)
    must flow through the same handler-tilt honor path as any other explicit
    tilt -- staying exempt from the tilt-only carriage pin (#1153 round-2
    finding 2) -- and, when that pairs position=0 with tilt=0, correctly trip
    the #755 full-mechanical-endpoint flag rather than being silently
    absorbed. This is the reporter's own SomfyIO/#985 hardware family, so the
    endpoint decision here matters: it must be the intended #755 "force
    close_cover" behaviour, not a spurious re-fire.
    """

    def test_default_winner_with_tilt_takes_handler_tilt_branch(self):
        """DEFAULT + tilt=0 honors the handler tilt and stays exempt from the
        tilt-only carriage pin -- the DEFAULT exemption (#1153 round-2 finding
        2) must still hold now that DEFAULT winners routinely carry a tilt.
        """
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        result = PipelineResult(
            position=0,
            tilt=0,
            control_method=ControlMethod.DEFAULT,
            reason="climate low-light — sunset tilt",
        )
        resolved = policy.post_pipeline_resolve(result, **_non_solar_kwargs())

        handler_names = [s.handler for s in resolved.decision_trace]
        assert "venetian_handler_tilt" in handler_names
        assert "venetian_mode" not in handler_names
        assert resolved.tilt == 0
        assert resolved.position == 0

    def test_default_winner_at_0_0_sets_full_endpoint_target(self):
        """The 0/0 pairing this fix now lets a DEFAULT winner reach is exactly
        the state issue #755 designed the endpoint flag for.
        """
        policy = _make_policy()
        result = PipelineResult(
            position=0, tilt=0, control_method=ControlMethod.DEFAULT, reason="test"
        )
        resolved = policy.post_pipeline_resolve(result, **_non_solar_kwargs())

        overrides = policy.position_context_overrides(resolved)
        assert overrides["tilt"] == 0
        assert overrides["full_endpoint_target"] is True

    @pytest.mark.parametrize("method", [ControlMethod.CLOUD, ControlMethod.MOTION])
    def test_cloud_and_motion_winners_at_position_0_nonzero_tilt_do_not_force_endpoint(
        self, method
    ):
        """A CLOUD/MOTION winner at position=0 with a non-zero default_tilt
        (e.g. default_tilt=50, not sunset) is NOT a mechanical stop -- the
        endpoint flag must stay clear, exactly as #755 intends.
        """
        policy = _make_policy()
        result = PipelineResult(
            position=0, tilt=50, control_method=method, reason="test"
        )
        resolved = policy.post_pipeline_resolve(result, **_non_solar_kwargs())

        overrides = policy.position_context_overrides(resolved)
        assert overrides["tilt"] == 50
        assert overrides.get("full_endpoint_target", False) is False


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


class TestTiltBoundFromTiltOnlySlotWithoutFixedTilt:
    """issue #1215: the reporter's exact configuration, end-to-end.

    A custom-position slot is ``tilt_only=True`` with NO fixed slat angle and
    a ``tilt_min`` of 50 — the exact stored options from the reporter's
    diagnostics. Solar wins the pipeline with ``tilt=None`` (a venetian
    resolves its slat angle only after the pipeline runs, in
    ``post_pipeline_resolve``); the bound must still be carried onto the
    ``PipelineResult`` and clamp whatever the venetian engine computes.
    """

    @staticmethod
    def _pipeline_result():
        """Run the real registry — SolarHandler + the reporter's slot."""
        from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
            CustomPositionHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
            DefaultHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
            SolarHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.registry import (
            PipelineRegistry,
        )
        from custom_components.adaptive_cover_pro.pipeline.types import (
            CustomPositionSensorState,
        )
        from tests.test_pipeline.conftest import make_snapshot

        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.slot1",),
            is_on=True,
            position=None,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=None,
            tilt_only=True,
            slot=1,
            active_entity_ids=("binary_sensor.slot1",),
            tilt_min=50,
        )
        registry = PipelineRegistry(
            [
                SolarHandler(),
                DefaultHandler(),
                CustomPositionHandler(
                    slot=1,
                    position=None,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                    tilt=None,
                ),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[state],
            default_position=0,
            direct_sun_valid=True,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.SOLAR
        assert result.tilt is None
        # Nothing resolved a tilt yet, so the bound rides the result instead
        # of being clamped in-pipeline (registry.py:1072-1078) — this is the
        # carry the venetian engine consumes below.
        assert (result.tilt_low, result.tilt_high) == (50, None)
        return result

    def test_tilt_bound_from_tilt_only_slot_clamps_engine_tilt(
        self, monkeypatch
    ) -> None:
        """The reporter's exact scenario: engine resolves 46 → clamped to 50."""
        pipeline_result = self._pipeline_result()
        policy = _make_policy()
        monkeypatch.setattr(
            VenetianPolicy,
            "_compose_tilt",
            lambda self, *a, **kw: (46, MagicMock()),
        )
        monkeypatch.setattr(
            VenetianPolicy, "_engine_tilt_suppressed", lambda self, r, c: False
        )
        resolved = policy.post_pipeline_resolve(pipeline_result, **_solar_kwargs())
        assert resolved.tilt == 50
        # Distinguish the applied clamp (REGISTRY_TILT_CLAMPED, matched=True)
        # from the earlier "bound carried, nothing to clamp yet" step the
        # registry also files under handler="tilt_clamp" (registry.py:1079-1093).
        assert any(
            s.reason_payload is not None
            and s.reason_payload.code == ReasonCode.REGISTRY_TILT_CLAMPED
            for s in resolved.decision_trace
        )

    def test_engine_tilt_above_bound_passes_through(self, monkeypatch) -> None:
        """The reporter's acceptance pair: a calculated 75% stays 75%."""
        pipeline_result = self._pipeline_result()
        policy = _make_policy()
        monkeypatch.setattr(
            VenetianPolicy,
            "_compose_tilt",
            lambda self, *a, **kw: (75, MagicMock()),
        )
        monkeypatch.setattr(
            VenetianPolicy, "_engine_tilt_suppressed", lambda self, r, c: False
        )
        resolved = policy.post_pipeline_resolve(pipeline_result, **_solar_kwargs())
        assert resolved.tilt == 75
        assert not any(
            s.reason_payload is not None
            and s.reason_payload.code == ReasonCode.REGISTRY_TILT_CLAMPED
            for s in resolved.decision_trace
        )


class TestPerCoverHoldDispatchPremise:
    """The proof behind ``VenetianPolicy.entities_move_independently`` (#1174).

    Venetian is the one shipped policy that overrides a per-entity dispatch
    hook and still opts INTO per-cover hold judging. It is allowed to because
    its ``post_pipeline_resolve`` never touches ``PipelineResult.position``
    under a hold — the only winner kind that is ever judged per cover. If that
    ever stops being true, the per-cover targets would silently bypass a
    position rewrite, which is the day/night Model B failure this gate exists
    to prevent. So the premise is pinned here rather than left to a docstring.
    """

    #: Winners that carry ``held_position`` and therefore produce verdicts —
    #: ``ManualOverrideHandler`` and ``GroupLockHandler`` are the only two
    #: handlers that set it.
    HOLD_METHODS = (ControlMethod.MANUAL, ControlMethod.GROUP_LOCK)

    @pytest.mark.parametrize("method", HOLD_METHODS)
    def test_tilt_only_pin_never_fires_under_a_hold(self, method):
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        out = policy.post_pipeline_resolve(
            _make_result(method, position=80), **_non_solar_kwargs()
        )
        assert out.position == 80

    @pytest.mark.parametrize("method", HOLD_METHODS)
    def test_a_hold_with_a_handler_tilt_keeps_its_position(self, method):
        """The handler-tilt branch is the one a clamped tilt bound arrives on."""
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY
        from dataclasses import replace

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        held = replace(_make_result(method, position=35), tilt=60)
        out = policy.post_pipeline_resolve(held, **_non_solar_kwargs())
        assert out.position == 35
        assert out.tilt == 60

    def test_venetian_opts_into_per_cover_hold_dispatch(self):
        assert _make_policy().entities_move_independently() is True


class TestReporterNightWindowContact:
    """Issue #943 item B, end-to-end on the reporter's own configuration.

    "OG-Küche": ``cover_venetian`` in ``position_and_tilt`` mode,
    ``default_percentage`` 100, ``default_tilt`` None, start time 07:00 with no
    end time. At 03:00 the clock window is CLOSED, so every windowed handler
    declines and ``DefaultHandler`` wins at 100% — fully open. Slot 1
    ("Lüften") is a window-contact slot: ``tilt_only`` with no slat angle,
    ``tilt_min`` 50, priority 77, and now the outside-window opt-in.

    What must come out: the slats reach 50 and the carriage stays at 40, where
    the cover actually is. The DEFAULT winner's own 100 must never leave the
    registry, because sending it is precisely issues #215/#216/#223.
    """

    @staticmethod
    def _evaluate():
        from custom_components.adaptive_cover_pro.cover_types import get_policy
        from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (  # noqa: E501
            CustomPositionHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
            DefaultHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
            SolarHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.registry import (
            PipelineRegistry,
        )
        from custom_components.adaptive_cover_pro.pipeline.types import (
            CustomPositionSensorState,
        )
        from tests.test_pipeline.conftest import make_snapshot

        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.myggbett_door_window_sensor_tur_2",),
            is_on=True,
            position=None,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=None,
            tilt_only=True,
            slot=1,
            active_entity_ids=("binary_sensor.myggbett_door_window_sensor_tur_2",),
            tilt_min=50,
            outside_window=True,
        )
        registry = PipelineRegistry(
            [
                SolarHandler(),
                DefaultHandler(),
                CustomPositionHandler(
                    slot=1,
                    position=None,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                    tilt=None,
                ),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[state],
            cover_type="cover_venetian",
            policy=get_policy("cover_venetian"),
            default_position=100,
            default_tilt=None,
            in_time_window=False,
            clock_window_open=False,
            current_cover_position=40,
            cover_positions={"cover.og_kuche": 40},
        )
        return registry.evaluate(snap)

    def test_reporter_night_window_contact_clamps_slats_without_moving_carriage(
        self,
    ) -> None:
        result = self._evaluate()

        assert result.control_method is ControlMethod.DEFAULT
        assert result.tilt == 50
        # The carriage stays where the cover is — NOT at the default's 100.
        assert result.position == 40
        assert result.hold_clamp_verdicts["cover.og_kuche"].released is True
        assert result.hold_clamp_verdicts["cover.og_kuche"].target == 40
        # Admitted, and admitted WITHOUT inheriting safety semantics.
        assert result.outside_window_constraint_active is True
        assert result.acts_outside_clock_window is True
        assert result.is_safety is False
        assert result.skip_command is False
        # ``held_position`` is stripped: the result must not start claiming to
        # hold anything (the Target Position sensor and the Model B stash
        # replay both key on it).
        assert result.held_position is None

    def test_reporter_night_result_survives_post_pipeline_resolve(self) -> None:
        """The policy's "handler tilt honored" path carries the resolved edge.

        The registry resolves the edge itself precisely because the alternative
        — carrying ``tilt_low`` — lands on the engine-suppressed branch, which
        returns ``tilt=None`` and drops the bounds. This pins that the value
        survives the policy for a DEFAULT winner with no engine tilt.
        """
        policy = _make_policy()
        resolved = policy.post_pipeline_resolve(self._evaluate(), **_solar_kwargs())

        assert resolved.tilt == 50
        assert resolved.position == 40

    def test_reporter_night_slot_without_the_opt_in_stays_hands_off(self) -> None:
        """Positive control: drop the opt-in and nothing is admitted at 03:00."""
        from custom_components.adaptive_cover_pro.cover_types import get_policy
        from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
            DefaultHandler,
        )
        from custom_components.adaptive_cover_pro.pipeline.registry import (
            PipelineRegistry,
        )
        from custom_components.adaptive_cover_pro.pipeline.types import (
            CustomPositionSensorState,
        )
        from tests.test_pipeline.conftest import make_snapshot

        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.myggbett_door_window_sensor_tur_2",),
            is_on=True,
            position=None,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt=None,
            tilt_only=True,
            slot=1,
            tilt_min=50,
        )
        result = PipelineRegistry([DefaultHandler()]).evaluate(
            make_snapshot(
                custom_position_sensors=[state],
                cover_type="cover_venetian",
                policy=get_policy("cover_venetian"),
                default_position=100,
                default_tilt=None,
                in_time_window=False,
                clock_window_open=False,
                current_cover_position=40,
                cover_positions={"cover.og_kuche": 40},
            )
        )
        assert result.outside_window_constraint_active is False
        assert result.hold_clamp_verdicts is None
        assert result.tilt is None
