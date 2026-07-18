"""Unit tests for VenetianPolicy — cover-type policy behaviour.

Covers the retract-tilt-overwrite path in ``after_position_command`` (issue
#33 comment #54): when the carriage is commanded above ``tilt_skip_above``,
the policy sends a neutral tilt to overwrite the actuator's cached value so
slats don't reassert a stale solar-cycle tilt after settle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import SERVICE_SET_COVER_POSITION

from custom_components.adaptive_cover_pro.const import (
    CONF_VENETIAN_TILT_SKIP_ABOVE,
    DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
    POSITION_OPEN,
)
from custom_components.adaptive_cover_pro.cover_types.venetian import VenetianPolicy
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext

# Zero the real-motor sleep delays — the apply-user-tilt tests route through the
# sequencer's post-tilt rebase delay otherwise.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def test_retract_threshold_constants_exist() -> None:
    """CONF and DEFAULT constants for the retract threshold must be exported."""
    assert CONF_VENETIAN_TILT_SKIP_ABOVE == "venetian_tilt_skip_above"
    assert DEFAULT_VENETIAN_TILT_SKIP_ABOVE == 95


def test_geometry_schema_accepts_post_settle_hold() -> None:
    """GEOMETRY_VENETIAN_SCHEMA accepts venetian_post_settle_hold in range [0.0, 10.0]."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_HOLD,
        DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    # Empty dict → default DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS
    result_default = GEOMETRY_VENETIAN_SCHEMA({})
    assert (
        result_default[CONF_VENETIAN_POST_SETTLE_HOLD]
        == DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS
    )

    # Custom value round-trips
    result_custom = GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_POST_SETTLE_HOLD: 5.0})
    assert result_custom[CONF_VENETIAN_POST_SETTLE_HOLD] == 5.0

    # Out-of-range raises
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_POST_SETTLE_HOLD: 10.1})
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_POST_SETTLE_HOLD: -0.1})


def test_post_settle_hold_constants_exist() -> None:
    """CONF, DEFAULT, and OPTION_RANGES for post-settle hold must be exported."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_HOLD,
        DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
        OPTION_RANGES,
    )

    assert CONF_VENETIAN_POST_SETTLE_HOLD == "venetian_post_settle_hold"
    assert DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS == 3.0
    assert OPTION_RANGES[CONF_VENETIAN_POST_SETTLE_HOLD] == (0.0, 10.0)


def test_max_tilt_constants_exist() -> None:
    """CONF_MAX_TILT, DEFAULT_MAX_TILT, and OPTION_RANGES entry must be exported."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MAX_TILT,
        DEFAULT_MAX_TILT,
        OPTION_RANGES,
    )

    assert CONF_MAX_TILT == "max_tilt"
    assert DEFAULT_MAX_TILT == 100
    assert OPTION_RANGES[CONF_MAX_TILT] == (0, 100)


def test_min_tilt_constants_exist() -> None:
    """CONF_MIN_TILT, DEFAULT_MIN_TILT, and OPTION_RANGES entry must be exported."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MIN_TILT,
        DEFAULT_MIN_TILT,
        OPTION_RANGES,
    )

    assert CONF_MIN_TILT == "min_tilt"
    assert DEFAULT_MIN_TILT == 0
    assert OPTION_RANGES[CONF_MIN_TILT] == (0, 100)


def test_geometry_schema_accepts_max_tilt() -> None:
    """GEOMETRY_VENETIAN_SCHEMA validates max_tilt: range 0–100, default 100."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import CONF_MAX_TILT
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    result_default = GEOMETRY_VENETIAN_SCHEMA({})
    assert result_default[CONF_MAX_TILT] == 100

    result_custom = GEOMETRY_VENETIAN_SCHEMA({CONF_MAX_TILT: 70})
    assert result_custom[CONF_MAX_TILT] == 70

    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_MAX_TILT: 150})


def test_geometry_schema_accepts_min_tilt() -> None:
    """GEOMETRY_VENETIAN_SCHEMA validates min_tilt: range 0–100, default 0."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import CONF_MIN_TILT
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    result_default = GEOMETRY_VENETIAN_SCHEMA({})
    assert result_default[CONF_MIN_TILT] == 0

    result_custom = GEOMETRY_VENETIAN_SCHEMA({CONF_MIN_TILT: 15})
    assert result_custom[CONF_MIN_TILT] == 15

    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_MIN_TILT: -5})


def test_tilt_safety_margin_constants_exist() -> None:
    """Issue #783 exposes the tilt safety margin CONF/DEFAULT/MIN/MAX constants."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SAFETY_MARGIN,
        DEFAULT_VENETIAN_TILT_SAFETY_MARGIN,
        MAX_VENETIAN_TILT_SAFETY_MARGIN,
        MIN_VENETIAN_TILT_SAFETY_MARGIN,
    )

    assert CONF_VENETIAN_TILT_SAFETY_MARGIN == "venetian_tilt_safety_margin"
    assert DEFAULT_VENETIAN_TILT_SAFETY_MARGIN == 0.0
    assert MIN_VENETIAN_TILT_SAFETY_MARGIN == 0.0
    assert MAX_VENETIAN_TILT_SAFETY_MARGIN == 1.0


def test_geometry_schema_accepts_tilt_safety_margin() -> None:
    """GEOMETRY_VENETIAN_SCHEMA validates the tilt safety margin: 0.0–1.0, default 0.0."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SAFETY_MARGIN,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    result_default = GEOMETRY_VENETIAN_SCHEMA({})
    assert result_default[CONF_VENETIAN_TILT_SAFETY_MARGIN] == 0.0

    result_custom = GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_TILT_SAFETY_MARGIN: 0.5})
    assert result_custom[CONF_VENETIAN_TILT_SAFETY_MARGIN] == 0.5

    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_TILT_SAFETY_MARGIN: 1.5})
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_TILT_SAFETY_MARGIN: -0.1})


def test_geometry_schema_accepts_venetian_mode() -> None:
    """GEOMETRY_VENETIAN_SCHEMA validates both allowed mode values."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_MODE,
        DEFAULT_VENETIAN_MODE,
        VENETIAN_MODE_TILT_ONLY,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    result_default = GEOMETRY_VENETIAN_SCHEMA({})
    assert result_default[CONF_VENETIAN_MODE] == DEFAULT_VENETIAN_MODE

    result_tilt_only = GEOMETRY_VENETIAN_SCHEMA(
        {CONF_VENETIAN_MODE: VENETIAN_MODE_TILT_ONLY}
    )
    assert result_tilt_only[CONF_VENETIAN_MODE] == VENETIAN_MODE_TILT_ONLY


def test_venetian_mode_constants_exist() -> None:
    """Mode constants must exist in const.py with the documented values."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_MODE,
        DEFAULT_VENETIAN_MODE,
        VENETIAN_MODE_POSITION_AND_TILT,
        VENETIAN_MODE_TILT_ONLY,
        VENETIAN_MODES,
    )

    assert CONF_VENETIAN_MODE == "venetian_mode"
    assert VENETIAN_MODE_POSITION_AND_TILT == "position_and_tilt"
    assert VENETIAN_MODE_TILT_ONLY == "tilt_only"
    assert DEFAULT_VENETIAN_MODE == VENETIAN_MODE_POSITION_AND_TILT
    assert VENETIAN_MODES == (VENETIAN_MODE_POSITION_AND_TILT, VENETIAN_MODE_TILT_ONLY)


def _make_policy(*, tilt_skip_above: int = 95) -> VenetianPolicy:
    """Return a VenetianPolicy with a fully mocked sequencer."""
    policy = VenetianPolicy()
    mock_seq = MagicMock()
    mock_seq.run_sequence = AsyncMock()
    mock_seq.stamp_position_command = MagicMock()
    policy._sequencer = mock_seq
    policy._tilt_skip_above = tilt_skip_above
    return policy


def _ctx(policy: VenetianPolicy, *, tilt: int = 80) -> PositionContext:
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


@pytest.mark.asyncio
async def test_after_position_command_sends_neutral_tilt_when_position_above_threshold() -> (
    None
):
    """When position > tilt_skip_above, the sequence fires with tilt=POSITION_OPEN.

    KNX and Shelly venetian actuators retain their last commanded tilt and
    reapply it ~1-2s after the carriage settle. When we retract above
    tilt_skip_above we must overwrite that cache with a neutral angle
    (POSITION_OPEN) — otherwise the previous solar-cycle tilt reasserts and
    closes the slats on a fully-retracted blind.
    """
    policy = _make_policy(tilt_skip_above=95)

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=98,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_called_once_with("cover.venetian_x")
    policy._sequencer.run_sequence.assert_awaited_once()
    kwargs = policy._sequencer.run_sequence.await_args.kwargs
    assert kwargs["position_target"] == 98
    assert kwargs["tilt_target"] == POSITION_OPEN


@pytest.mark.asyncio
async def test_after_position_command_sends_neutral_tilt_at_100_percent() -> None:
    """Fully retracted (position=100) must also overwrite cached tilt with POSITION_OPEN."""
    policy = _make_policy(tilt_skip_above=95)

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=100,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_called_once_with("cover.venetian_x")
    policy._sequencer.run_sequence.assert_awaited_once()
    kwargs = policy._sequencer.run_sequence.await_args.kwargs
    assert kwargs["position_target"] == 100
    assert kwargs["tilt_target"] == POSITION_OPEN


@pytest.mark.asyncio
async def test_after_position_command_ignores_context_tilt_when_retracted() -> None:
    """The retract path uses POSITION_OPEN, never context.tilt — even if context carries one."""
    policy = _make_policy(tilt_skip_above=95)
    # context carries tilt=20 (a stale solar-cycle tilt) — must be ignored.
    ctx = _ctx(policy, tilt=20)

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=100,
        context=ctx,
        reason="solar",
    )

    kwargs = policy._sequencer.run_sequence.await_args.kwargs
    assert kwargs["tilt_target"] == POSITION_OPEN
    assert kwargs["tilt_target"] != 20


def test_retract_tilt_uses_position_open_constant() -> None:
    """Guard against accidental ``100`` magic-number drift in the retract path."""
    # POSITION_OPEN is the *only* physically meaningful neutral tilt for a
    # fully retracted carriage. If this changes, the design intent has shifted
    # and the retract path needs re-examining.
    assert POSITION_OPEN == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [95, 60, 6])
async def test_after_position_command_runs_sequence_at_or_below_threshold(
    position: int,
) -> None:
    """When position is between the lower and upper thresholds, the full sequence fires."""
    policy = _make_policy(tilt_skip_above=95)

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=position,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_called_once_with("cover.venetian_x")
    policy._sequencer.run_sequence.assert_awaited_once()


@pytest.mark.asyncio
class TestVenetianMaybeUpdateTiltOnly:
    """maybe_update_tilt_only drives continuous tilt when position hasn't changed."""

    def _policy_with_last_tilt(
        self,
        *,
        tilt_value: int | None,
        suppression: bool = False,
    ) -> VenetianPolicy:
        from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

        policy = _make_policy()
        policy._venetian_mode = VENETIAN_MODE_TILT_ONLY
        policy._last_tilt = tilt_value
        mock_seq = MagicMock()
        mock_seq.update_tilt_only = AsyncMock()
        mock_seq.is_in_suppression = MagicMock(return_value=suppression)
        policy._sequencer = mock_seq
        return policy

    async def test_emits_when_last_tilt_set_and_no_suppression(self):
        policy = self._policy_with_last_tilt(tilt_value=70)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_awaited_once()

    async def test_skips_when_no_last_tilt(self):
        policy = self._policy_with_last_tilt(tilt_value=None)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_not_awaited()

    async def test_skips_when_suppression_window_open(self):
        policy = self._policy_with_last_tilt(tilt_value=70, suppression=True)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=0, context=MagicMock(), reason="solar"
        )
        policy._sequencer.update_tilt_only.assert_not_awaited()

    async def test_skips_when_no_sequencer(self):
        policy = _make_policy()
        policy._last_tilt = 70
        policy._sequencer = None
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=0, context=MagicMock(), reason="solar"
        )

    async def test_routes_tilt_to_position_open_when_above_skip_threshold(self):
        """When current_position > tilt_skip_above, the sequencer receives POSITION_OPEN.

        Mirrors the guard in after_position_command — without this, fully-retracted
        carriages storm the actuator with redundant tilt commands every solar
        recompute (issue #33, geryyyyyy's beta.10 diagnostics).
        """
        policy = self._policy_with_last_tilt(tilt_value=40)
        policy._tilt_skip_above = 95

        await policy.maybe_update_tilt_only(
            "cover.x", current_position=98, context=MagicMock(), reason="solar"
        )

        policy._sequencer.update_tilt_only.assert_awaited_once()
        kwargs = policy._sequencer.update_tilt_only.await_args.kwargs
        assert kwargs["tilt_target"] == POSITION_OPEN

    async def test_uses_last_tilt_when_at_skip_threshold(self):
        """current_position == tilt_skip_above is NOT above the threshold.

        The guard is strict `>`, matching after_position_command semantics.
        """
        policy = self._policy_with_last_tilt(tilt_value=40)
        policy._tilt_skip_above = 95

        await policy.maybe_update_tilt_only(
            "cover.x", current_position=95, context=MagicMock(), reason="solar"
        )

        policy._sequencer.update_tilt_only.assert_awaited_once()
        kwargs = policy._sequencer.update_tilt_only.await_args.kwargs
        assert kwargs["tilt_target"] == 40

    async def test_uses_last_tilt_when_current_position_is_none(self):
        """current_position=None falls back to _last_tilt — we can't evaluate the guard."""
        policy = self._policy_with_last_tilt(tilt_value=55)
        policy._tilt_skip_above = 95

        await policy.maybe_update_tilt_only(
            "cover.x", current_position=None, context=MagicMock(), reason="solar"
        )

        policy._sequencer.update_tilt_only.assert_awaited_once()
        kwargs = policy._sequencer.update_tilt_only.await_args.kwargs
        assert kwargs["tilt_target"] == 55


@pytest.mark.asyncio
async def test_after_position_command_fires_tilt_at_position_zero() -> None:
    """At position=0 the sequence MUST fire — issue #33 regression.

    The removed tilt_skip_below option silently blocked tilt at fully-closed
    positions. Default behavior must now allow tilt at position=0.
    """
    policy = _make_policy()

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=0,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_called_once_with("cover.venetian_x")
    policy._sequencer.run_sequence.assert_awaited_once()


@pytest.mark.asyncio
async def test_after_position_command_respects_custom_threshold() -> None:
    """Threshold is read from the policy instance, not a module-level constant.

    With tilt_skip_above=80 and position=81 we cross into the retract path,
    so the sequence fires with tilt=POSITION_OPEN.
    """
    policy = _make_policy(tilt_skip_above=80)

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=81,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_called_once_with("cover.venetian_x")
    policy._sequencer.run_sequence.assert_awaited_once()
    kwargs = policy._sequencer.run_sequence.await_args.kwargs
    assert kwargs["position_target"] == 81
    assert kwargs["tilt_target"] == POSITION_OPEN


@pytest.mark.asyncio
async def test_after_position_command_skips_when_service_is_not_set_position() -> None:
    """A tilt-only service call must not trigger the dual-axis sequence."""
    policy = _make_policy()

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service="set_cover_tilt_position",
        position=50,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_not_called()
    policy._sequencer.run_sequence.assert_not_awaited()


# ---------------------------------------------------------------------------
# venetian_tilt_skip_mode: suppress vs neutral (issue #748)
# ---------------------------------------------------------------------------


def test_tilt_skip_mode_constants_exist() -> None:
    """CONF/DEFAULT/value/tuple constants for the skip mode must be exported."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SKIP_MODE,
        DEFAULT_VENETIAN_TILT_SKIP_MODE,
        VENETIAN_TILT_SKIP_MODES,
        VENETIAN_TILT_SKIP_NEUTRAL,
        VENETIAN_TILT_SKIP_SUPPRESS,
    )

    assert CONF_VENETIAN_TILT_SKIP_MODE == "venetian_tilt_skip_mode"
    assert VENETIAN_TILT_SKIP_NEUTRAL == "neutral"
    assert VENETIAN_TILT_SKIP_SUPPRESS == "suppress"
    # Default MUST stay neutral — preserves the #33 cache-overwrite behaviour.
    assert DEFAULT_VENETIAN_TILT_SKIP_MODE == VENETIAN_TILT_SKIP_NEUTRAL
    assert VENETIAN_TILT_SKIP_MODES == (
        VENETIAN_TILT_SKIP_NEUTRAL,
        VENETIAN_TILT_SKIP_SUPPRESS,
    )


def test_tilt_transform_constants_exist() -> None:
    """CONF/DEFAULT/value/tuple constants for the tilt transform must exist (#957)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_TRANSFORM,
        DEFAULT_VENETIAN_TILT_TRANSFORM,
        VENETIAN_TILT_TRANSFORM_CLAMP,
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        VENETIAN_TILT_TRANSFORMS,
    )

    assert CONF_VENETIAN_TILT_TRANSFORM == "venetian_tilt_transform"
    assert VENETIAN_TILT_TRANSFORM_CLAMP == "clamp"
    assert VENETIAN_TILT_TRANSFORM_PROPORTIONAL == "proportional"
    # Default MUST stay clamp — byte-for-byte no-op / rollback-safe.
    assert DEFAULT_VENETIAN_TILT_TRANSFORM == VENETIAN_TILT_TRANSFORM_CLAMP
    assert VENETIAN_TILT_TRANSFORMS == (
        VENETIAN_TILT_TRANSFORM_CLAMP,
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )


def test_geometry_schema_includes_tilt_transform() -> None:
    """GEOMETRY_VENETIAN_SCHEMA defaults transform to clamp and rejects bad values."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_TRANSFORM,
        DEFAULT_VENETIAN_TILT_TRANSFORM,
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    assert (
        GEOMETRY_VENETIAN_SCHEMA({})[CONF_VENETIAN_TILT_TRANSFORM]
        == DEFAULT_VENETIAN_TILT_TRANSFORM
    )
    out = GEOMETRY_VENETIAN_SCHEMA(
        {CONF_VENETIAN_TILT_TRANSFORM: VENETIAN_TILT_TRANSFORM_PROPORTIONAL}
    )
    assert out[CONF_VENETIAN_TILT_TRANSFORM] == VENETIAN_TILT_TRANSFORM_PROPORTIONAL
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_TILT_TRANSFORM: "bogus"})


def test_geometry_schema_skip_mode_default_and_validation() -> None:
    """GEOMETRY_VENETIAN_SCHEMA defaults skip_mode to neutral and rejects bad values."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SKIP_MODE,
        DEFAULT_VENETIAN_TILT_SKIP_MODE,
        VENETIAN_TILT_SKIP_SUPPRESS,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    assert (
        GEOMETRY_VENETIAN_SCHEMA({})[CONF_VENETIAN_TILT_SKIP_MODE]
        == DEFAULT_VENETIAN_TILT_SKIP_MODE
    )
    out = GEOMETRY_VENETIAN_SCHEMA(
        {CONF_VENETIAN_TILT_SKIP_MODE: VENETIAN_TILT_SKIP_SUPPRESS}
    )
    assert out[CONF_VENETIAN_TILT_SKIP_MODE] == VENETIAN_TILT_SKIP_SUPPRESS
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_TILT_SKIP_MODE: "bogus"})


# ---------------------------------------------------------------------------
# venetian_post_settle_mode: fixed_delay vs entity_state (issue #801)
# ---------------------------------------------------------------------------


def test_post_settle_mode_constants_exist() -> None:
    """CONF/DEFAULT/value/tuple constants for the post-settle mode must be exported."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_MODE,
        DEFAULT_VENETIAN_POST_SETTLE_MODE,
        VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
        VENETIAN_POST_SETTLE_MODE_FIXED,
        VENETIAN_POST_SETTLE_MODES,
    )

    assert CONF_VENETIAN_POST_SETTLE_MODE == "venetian_post_settle_mode"
    assert VENETIAN_POST_SETTLE_MODE_FIXED == "fixed_delay"
    assert VENETIAN_POST_SETTLE_MODE_ENTITY_STATE == "entity_state"
    # Default MUST stay fixed_delay — preserves back-compat behaviour.
    assert DEFAULT_VENETIAN_POST_SETTLE_MODE == VENETIAN_POST_SETTLE_MODE_FIXED
    assert VENETIAN_POST_SETTLE_MODES == (
        VENETIAN_POST_SETTLE_MODE_FIXED,
        VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
    )


def test_geometry_schema_post_settle_mode_default_and_validation() -> None:
    """GEOMETRY_VENETIAN_SCHEMA defaults post_settle_mode to fixed_delay and rejects bad values."""
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_MODE,
        DEFAULT_VENETIAN_POST_SETTLE_MODE,
        VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
    )
    from custom_components.adaptive_cover_pro.cover_types.venetian import (
        GEOMETRY_VENETIAN_SCHEMA,
    )

    assert (
        GEOMETRY_VENETIAN_SCHEMA({})[CONF_VENETIAN_POST_SETTLE_MODE]
        == DEFAULT_VENETIAN_POST_SETTLE_MODE
    )
    out = GEOMETRY_VENETIAN_SCHEMA(
        {CONF_VENETIAN_POST_SETTLE_MODE: VENETIAN_POST_SETTLE_MODE_ENTITY_STATE}
    )
    assert out[CONF_VENETIAN_POST_SETTLE_MODE] == VENETIAN_POST_SETTLE_MODE_ENTITY_STATE
    with pytest.raises(vol.Invalid):
        GEOMETRY_VENETIAN_SCHEMA({CONF_VENETIAN_POST_SETTLE_MODE: "bogus"})


def test_default_skip_mode_is_neutral() -> None:
    """A freshly constructed policy defaults to neutral skip behaviour."""
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_NEUTRAL

    policy = VenetianPolicy()
    assert policy._tilt_skip_mode == VENETIAN_TILT_SKIP_NEUTRAL


def test_resolve_skip_above_tilt_neutral_returns_position_open() -> None:
    """Neutral mode keeps the #33 behaviour: above threshold → POSITION_OPEN."""
    policy = _make_policy(tilt_skip_above=95)
    assert policy._resolve_skip_above_tilt(98, 40) == POSITION_OPEN


def test_resolve_skip_above_tilt_suppress_returns_none() -> None:
    """Suppress mode returns None above the threshold so no tilt is emitted."""
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_SUPPRESS

    policy = _make_policy(tilt_skip_above=95)
    policy._tilt_skip_mode = VENETIAN_TILT_SKIP_SUPPRESS
    assert policy._resolve_skip_above_tilt(98, 40) is None


def test_resolve_skip_above_tilt_suppress_below_threshold_uses_fallback() -> None:
    """Suppress mode below the threshold still returns the fallback tilt."""
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_SUPPRESS

    policy = _make_policy(tilt_skip_above=95)
    policy._tilt_skip_mode = VENETIAN_TILT_SKIP_SUPPRESS
    assert policy._resolve_skip_above_tilt(60, 40) == 40


@pytest.mark.asyncio
async def test_after_position_command_suppress_mode_skips_tilt_above_threshold() -> (
    None
):
    """Suppress mode: above the threshold NO tilt is sequenced at the open endpoint.

    Coupled-axis exterior venetians (Somfy + Shelly) get dragged off 100 when
    ANY tilt command is sent at the fully-open endpoint, so the suppress mode
    emits nothing there (issue #748).
    """
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_SUPPRESS

    policy = _make_policy(tilt_skip_above=95)
    policy._tilt_skip_mode = VENETIAN_TILT_SKIP_SUPPRESS

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=100,
        context=_ctx(policy),
        reason="solar",
    )

    policy._sequencer.stamp_position_command.assert_not_called()
    policy._sequencer.run_sequence.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_position_command_suppress_mode_still_sends_below_threshold() -> (
    None
):
    """Suppress mode below the threshold still runs the full dual-axis sequence."""
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_SUPPRESS

    policy = _make_policy(tilt_skip_above=95)
    policy._tilt_skip_mode = VENETIAN_TILT_SKIP_SUPPRESS

    await policy.after_position_command(
        cmd_svc=MagicMock(),
        entity_id="cover.venetian_x",
        service=SERVICE_SET_COVER_POSITION,
        position=60,
        context=_ctx(policy, tilt=80),
        reason="solar",
    )

    policy._sequencer.run_sequence.assert_awaited_once()
    kwargs = policy._sequencer.run_sequence.await_args.kwargs
    assert kwargs["tilt_target"] == 80


@pytest.mark.asyncio
async def test_maybe_update_tilt_only_suppress_mode_skips_above_threshold() -> None:
    """Suppress mode: a tilt-only update above the threshold sends nothing.

    The None-guard added after ``_resolve_skip_above_tilt`` short-circuits the
    tilt-only path so coupled-axis covers are not nudged off the open endpoint.
    """
    from custom_components.adaptive_cover_pro.const import VENETIAN_TILT_SKIP_SUPPRESS

    policy = _make_policy(tilt_skip_above=95)
    policy._tilt_skip_mode = VENETIAN_TILT_SKIP_SUPPRESS
    policy._last_tilt = 40
    mock_seq = MagicMock()
    mock_seq.update_tilt_only = AsyncMock()
    mock_seq.is_in_suppression = MagicMock(return_value=False)
    policy._sequencer = mock_seq

    await policy.maybe_update_tilt_only(
        "cover.x", current_position=98, context=MagicMock(), reason="solar"
    )

    policy._sequencer.update_tilt_only.assert_not_awaited()


def test_disallowed_geometry_fields_rejects_only_awning_only() -> None:
    """Venetian accepts vertical and tilt geometry; awning-only fields are rejected."""
    policy = VenetianPolicy()
    rules = policy.disallowed_geometry_fields(
        vertical_only={"window_height"},
        awning_only={"awning_drop"},
        tilt_only={"tilt_depth"},
    )
    assert rules == [({"awning_drop"}, "awning")]


def test_capability_warnings_flags_missing_set_position() -> None:
    """An entity missing set_position produces a warning string."""
    policy = VenetianPolicy()
    warnings = policy.cover_capability_warnings(
        {
            "cover.tilt_only": {
                "has_set_position": False,
                "has_set_tilt_position": True,
            }
        }
    )
    assert len(warnings) == 1
    assert "cover.tilt_only" in warnings[0]
    assert "set_position" in warnings[0]


def test_capability_warnings_flags_missing_set_tilt_position() -> None:
    """An entity missing set_tilt_position produces its own warning string."""
    policy = VenetianPolicy()
    warnings = policy.cover_capability_warnings(
        {
            "cover.position_only": {
                "has_set_position": True,
                "has_set_tilt_position": False,
            }
        }
    )
    assert len(warnings) == 1
    assert "cover.position_only" in warnings[0]
    assert "set_tilt_position" in warnings[0]


def test_capability_warnings_empty_when_all_capable() -> None:
    """Fully capable entities produce no warnings."""
    policy = VenetianPolicy()
    warnings = policy.cover_capability_warnings(
        {
            "cover.full": {
                "has_set_position": True,
                "has_set_tilt_position": True,
            }
        }
    )
    assert warnings == []


def test_position_context_overrides_returns_tilt_when_present() -> None:
    """A pipeline result with tilt threads it into PositionContext.tilt."""
    policy = VenetianPolicy()
    result = MagicMock()
    result.tilt = 60
    assert policy.position_context_overrides(result) == {"tilt": 60}


def test_position_context_overrides_returns_empty_when_no_tilt() -> None:
    """No tilt on the result → no override (avoids stomping on default)."""
    policy = VenetianPolicy()
    result = MagicMock()
    result.tilt = None
    assert policy.position_context_overrides(result) == {}
    assert policy.position_context_overrides(None) == {}


@pytest.mark.parametrize(
    ("position", "tilt", "expect_flag"),
    [
        (0, 0, True),  # full closed endpoint
        (100, 100, True),  # full open endpoint
        (0, 100, False),  # solar 0/100 — legitimate non-endpoint
        (100, 0, False),  # carriage open, slats closed — not a full endpoint
        (0, 50, False),  # mismatched axes
        (40, 40, False),  # equal but mid-range, not a mechanical stop
    ],
)
def test_position_context_overrides_sets_full_endpoint_flag(
    position: int, tilt: int, expect_flag: bool
) -> None:
    """A paired full mechanical endpoint (0/0 or 100/100) sets the bypass flag.

    Issue #755 — the venetian policy is the only place that knows both axes,
    so it owns the "is this a full endpoint" decision and exposes it via a
    cover-type-agnostic PositionContext flag. Tilt must still be threaded in
    every case.
    """
    policy = VenetianPolicy()
    result = MagicMock()
    result.position = position
    result.tilt = tilt

    overrides = policy.position_context_overrides(result)

    assert overrides["tilt"] == tilt  # tilt always threaded
    assert overrides.get("full_endpoint_target", False) is expect_flag


def test_sequencer_property_exposes_attached_sequencer() -> None:
    """The ``sequencer`` property returns whatever attach() wired in."""
    policy = VenetianPolicy()
    assert policy.sequencer is None
    sentinel = object()
    policy._sequencer = sentinel  # type: ignore[assignment]
    assert policy.sequencer is sentinel


def test_is_in_tilt_suppression_false_without_sequencer() -> None:
    """No sequencer attached → suppression check short-circuits to False."""
    policy = VenetianPolicy()
    assert policy.is_in_tilt_suppression("cover.any", delta=10.0) is False


def test_is_in_tilt_suppression_delegates_to_sequencer() -> None:
    """With a sequencer, is_in_tilt_suppression delegates to the delta-aware gate."""
    policy = _make_policy()
    policy._sequencer.is_in_suppression_with_cap = MagicMock(return_value=True)
    assert policy.is_in_tilt_suppression("cover.x", delta=10.0) is True
    policy._sequencer.is_in_suppression_with_cap.assert_called_once_with(
        "cover.x", 10.0
    )


def test_secondary_axis_check_returns_none_without_tilt() -> None:
    """Without a resolved tilt, the manual-override secondary check is skipped."""
    policy = VenetianPolicy()
    result = MagicMock()
    result.tilt = None
    assert policy.secondary_axis_check(result, cmd_svc=MagicMock()) is None
    assert policy.secondary_axis_check(None, cmd_svc=MagicMock()) is None


def test_attach_forwards_post_settle_hold_to_sequencer() -> None:
    """attach() with post_settle_hold_seconds=7.0 wires that value into DualAxisSequencer."""
    from unittest.mock import MagicMock, patch

    policy = VenetianPolicy()
    hass = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.cover_types.venetian.policy.DualAxisSequencer"
    ) as MockSeq:
        MockSeq.return_value = MagicMock()
        policy.attach(
            hass=hass,
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            post_settle_hold_seconds=7.0,
        )
        _, kwargs = MockSeq.call_args
        assert kwargs.get("post_settle_hold_seconds") == 7.0


def test_attach_forwards_post_settle_mode_to_sequencer() -> None:
    """attach() with post_settle_mode="entity_state" wires that value into DualAxisSequencer."""
    from unittest.mock import MagicMock, patch

    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
    )

    policy = VenetianPolicy()
    hass = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.cover_types.venetian.policy.DualAxisSequencer"
    ) as MockSeq:
        MockSeq.return_value = MagicMock()
        policy.attach(
            hass=hass,
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            post_settle_mode=VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
        )
        _, kwargs = MockSeq.call_args
        assert kwargs.get("post_settle_mode") == VENETIAN_POST_SETTLE_MODE_ENTITY_STATE


def test_attach_threads_invert_tilt_callable_into_sequencer() -> None:
    """attach() with invert_tilt=lambda: True must wire that callable into the sequencer."""
    from unittest.mock import MagicMock

    policy = VenetianPolicy()
    hass = MagicMock()
    hass.services.async_call = MagicMock()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        invert_tilt=lambda: True,
    )
    assert policy.sequencer is not None
    assert policy.sequencer._invert_tilt() is True


def test_attach_invert_tilt_defaults_to_none() -> None:
    """When invert_tilt is not passed, the sequencer must have _invert_tilt=None."""
    from unittest.mock import MagicMock

    policy = VenetianPolicy()
    hass = MagicMock()
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
    )
    assert policy.sequencer is not None
    assert policy.sequencer._invert_tilt is None


def test_secondary_axis_check_carries_expected_tilt() -> None:
    """With a resolved tilt, the check exposes the expected slat angle and label.

    The suppression callback is an OR-composition of the tilt-suppression
    window and the command-grace period. When neither is active (unattached
    policy, no grace manager) it returns False.
    """
    entity_id = "cover.venetian_test"
    policy = VenetianPolicy()
    result = MagicMock()
    result.tilt = 75
    check = policy.secondary_axis_check(result, cmd_svc=MagicMock())
    assert check is not None
    assert check.expected == 75
    assert check.attribute == "current_tilt_position"
    assert check.label == "tilt"
    # Suppression must be callable with the standard (entity_id, delta) signature.
    assert callable(check.suppression)
    # With no sequencer and no grace manager, suppression must return False.
    assert check.suppression(entity_id, 0.0) is False


@pytest.mark.asyncio
class TestBeforePositionCommandTiltFirstOnOpen:
    """Issue #33: ``before_position_command`` sends tilt FIRST on opening
    transitions so the actuator's slats reach the target angle before the
    carriage starts moving.

    Without this, KNX/Shelly actuators briefly reassert their cached tilt
    against partially-closed slats during travel — visible as a "slats close
    then open" flicker. On closing transitions the existing
    position-then-tilt order is correct (slats must close after the carriage
    has finished travelling), so the tilt-first branch is opening-only.

    The dedup added in ``_send_tilt_command`` keeps total service-call count
    at 2 (position + tilt) by short-circuiting the post-settle tilt resend
    that ``after_position_command`` → ``run_sequence`` would otherwise fire.
    """

    def _make_policy_with_send_mock(
        self, *, current_position: int | None
    ) -> VenetianPolicy:
        policy = _make_policy(tilt_skip_above=95)
        policy._sequencer._send_tilt_command = AsyncMock()
        policy._sequencer._get_current_position = MagicMock(
            return_value=current_position
        )
        return policy

    async def test_open_transition_sends_tilt_with_force(self) -> None:
        """current=20 → target=80 (opening) must send tilt with force=True."""
        policy = self._make_policy_with_send_mock(current_position=20)

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service=SERVICE_SET_COVER_POSITION,
            position=80,
            context=_ctx(policy, tilt=POSITION_OPEN),
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_awaited_once()
        kwargs = policy._sequencer._send_tilt_command.await_args.kwargs
        assert kwargs["tilt_target"] == POSITION_OPEN
        assert kwargs["position_target"] == 80
        assert kwargs["force"] is True

    async def test_closing_transition_does_not_send_tilt(self) -> None:
        """current=80 → target=20 (closing) must NOT pre-send tilt."""
        policy = self._make_policy_with_send_mock(current_position=80)

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service=SERVICE_SET_COVER_POSITION,
            position=20,
            context=_ctx(policy, tilt=40),
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_not_awaited()

    async def test_unknown_current_position_does_not_send_tilt(self) -> None:
        """When current position is unreadable, fall back to the existing order."""
        policy = self._make_policy_with_send_mock(current_position=None)

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service=SERVICE_SET_COVER_POSITION,
            position=80,
            context=_ctx(policy, tilt=POSITION_OPEN),
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_not_awaited()

    async def test_non_position_service_does_not_send_tilt(self) -> None:
        """A non-position-axis service (stop_cover) must not pre-send tilt.

        ``open_cover`` / ``close_cover`` ARE position-axis moves (issue #697
        endpoint substitution), so only services like ``stop_cover`` are
        rejected by the guard.
        """
        policy = self._make_policy_with_send_mock(current_position=20)

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service="stop_cover",
            position=80,
            context=_ctx(policy, tilt=POSITION_OPEN),
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_not_awaited()

    async def test_open_cover_service_sends_tilt_on_open(self) -> None:
        """Endpoint open_cover (issue #697) is a position-axis open transition.

        current=20 → open_cover (target 100) must still pre-send tilt with
        force, exactly like set_cover_position would on an opening move.
        """
        policy = self._make_policy_with_send_mock(current_position=20)

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service="open_cover",
            position=100,
            context=_ctx(policy, tilt=POSITION_OPEN),
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_awaited_once()
        kwargs = policy._sequencer._send_tilt_command.await_args.kwargs
        assert kwargs["position_target"] == 100
        assert kwargs["force"] is True

    async def test_no_tilt_in_context_does_not_send_tilt(self) -> None:
        """Without a tilt target in context, there's nothing to pre-send."""
        policy = self._make_policy_with_send_mock(current_position=20)
        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            force=True,
            tilt=None,
            policy=policy,
        )

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service=SERVICE_SET_COVER_POSITION,
            position=80,
            context=ctx,
            reason="solar",
        )

        policy._sequencer._send_tilt_command.assert_not_awaited()

    async def test_no_sequencer_is_safe(self) -> None:
        """A detached policy (no sequencer) must not crash."""
        policy = VenetianPolicy()  # not attached
        assert policy._sequencer is None

        await policy.before_position_command(
            cmd_svc=MagicMock(),
            entity_id="cover.venetian_x",
            service=SERVICE_SET_COVER_POSITION,
            position=80,
            context=_ctx(policy, tilt=POSITION_OPEN),
            reason="solar",
        )


@pytest.mark.asyncio
class TestVenetianApplyUserTilt:
    """Issue #684: a user tilt request drives ONLY the tilt axis.

    ``apply_user_tilt`` routes the requested tilt through the sequencer's
    tilt-only path with the *current* carriage position as a reference — the
    carriage must never be commanded. ``force=True`` bypasses the
    target-unchanged dedup so a tilt equal to the last-sent value still fires
    (a user explicitly re-requesting it is not a no-op).
    """

    def _policy_with_real_sequencer(self, *, current_position: int, invert_tilt=None):
        from tests.test_cover_types.test_venetian_sequencer import _build_sequencer

        policy = VenetianPolicy()
        hass, seq = _build_sequencer(
            current_positions=[current_position, current_position, current_position],
            invert_tilt=invert_tilt,
        )
        policy._sequencer = seq
        policy._tilt_skip_above = 95
        return hass, policy

    async def test_apply_user_tilt_drives_sequencer_with_current_position(self) -> None:
        hass, policy = self._policy_with_real_sequencer(current_position=50)

        handled = await policy.apply_user_tilt(
            "cover.venetian_x", tilt=10, reason="proxy_tilt"
        )

        assert handled is True
        # Exactly one tilt service call, paired with the current carriage
        # position as reference — and NO set_cover_position.
        calls = hass.services.async_call.await_args_list
        tilt_calls = [c for c in calls if c.args[1] == "set_cover_tilt_position"]
        position_calls = [c for c in calls if c.args[1] == "set_cover_position"]
        assert len(tilt_calls) == 1, f"expected one tilt call, got {calls}"
        assert tilt_calls[0].args[2]["tilt_position"] == 10
        assert position_calls == [], f"carriage commanded: {position_calls}"

    async def test_force_path_resends_unchanged_tilt(self) -> None:
        """A tilt equal to the last-sent target still fires (force bypasses dedup)."""
        hass, policy = self._policy_with_real_sequencer(current_position=50)
        # Seed the stored target so the dedup would otherwise short-circuit.
        policy._sequencer._tilt_targets["cover.venetian_x"] = 10

        handled = await policy.apply_user_tilt(
            "cover.venetian_x", tilt=10, reason="proxy_tilt"
        )

        assert handled is True
        tilt_calls = [
            c
            for c in hass.services.async_call.await_args_list
            if c.args[1] == "set_cover_tilt_position"
        ]
        assert len(tilt_calls) == 1, "force must bypass the target-unchanged dedup"
        assert tilt_calls[0].args[2]["tilt_position"] == 10

    async def test_inverse_tilt_lands_wire_value_on_source(self) -> None:
        """invert_tilt ON: a request of 10 lands the inverted wire value (90).

        The sequencer's ``_send_tilt_command`` applies ``_to_wire`` internally,
        so routing the user tilt through ``update_tilt_only`` gets inverse
        handling for free (no parallel transform in the proxy/coordinator).
        """
        hass, policy = self._policy_with_real_sequencer(
            current_position=50, invert_tilt=lambda: True
        )

        handled = await policy.apply_user_tilt(
            "cover.venetian_x", tilt=10, reason="proxy_tilt"
        )

        assert handled is True
        tilt_calls = [
            c
            for c in hass.services.async_call.await_args_list
            if c.args[1] == "set_cover_tilt_position"
        ]
        assert len(tilt_calls) == 1, f"expected one tilt call, got {tilt_calls}"
        assert tilt_calls[0].args[2]["tilt_position"] == 90


class TestVenetianForecastSecondaryAxes:
    """VenetianPolicy.forecast_secondary_axes projects the tilt track (#724).

    The forecast hook reuses the same ``_compose_tilt`` seam the live
    ``post_pipeline_resolve`` runs, so the projected slat angle matches what
    the cover is actually commanded to at runtime — no duplicated tilt math.
    """

    def _kwargs(self, **overrides):
        from tests.cover_helpers import (
            make_cover_config,
            make_tilt_config,
            make_vertical_config,
        )

        svc = MagicMock()
        svc.get_vertical_data.return_value = make_vertical_config()
        svc.get_tilt_data.return_value = make_tilt_config()
        sun_data = MagicMock()
        sun_data.timezone = "UTC"
        kw = {
            "position": 50,
            "logger": MagicMock(),
            "sol_azi": 180.0,
            "sol_elev": 45.0,
            "sun_data": sun_data,
            "config": make_cover_config(),
            "config_service": svc,
            "options": {},
            "minimize_movements": False,
            "max_coverage_steps": 1,
        }
        kw.update(overrides)
        return kw

    def test_forecast_secondary_axes_returns_tilt_keyed_by_axis_name(self):
        from custom_components.adaptive_cover_pro.engine.covers import (
            VenetianCoverCalculation,
        )

        policy = VenetianPolicy()
        kw = self._kwargs()
        result = policy.forecast_secondary_axes(**kw)

        # Keyed by the tilt axis's name (self.axes[1].name == "tilt"), not a
        # hardcoded literal in the forecast layer.
        assert set(result) == {policy.axes[1].name}
        # Value equals the raw engine seam for the identical inputs (parity with
        # VenetianCoverCalculation.tilt_for_position).
        calc = VenetianCoverCalculation(
            config=kw["config"],
            vert_config=kw["config_service"].get_vertical_data(kw["options"]),
            tilt_config=kw["config_service"].get_tilt_data(kw["options"]),
            sun_data=kw["sun_data"],
            sol_azi=kw["sol_azi"],
            sol_elev=kw["sol_elev"],
            logger=kw["logger"],
        )
        assert result[policy.axes[1].name] == calc.tilt_for_position(kw["position"])

    def test_forecast_tilt_honors_minimize_movements_quantize(self, monkeypatch):
        from custom_components.adaptive_cover_pro.engine.covers import (
            VenetianCoverCalculation,
        )

        # Force a known intermediate slat angle so the quantize is observable.
        monkeypatch.setattr(
            VenetianCoverCalculation,
            "tilt_for_position",
            lambda self, position: 70,
        )
        policy = VenetianPolicy()
        result = policy.forecast_secondary_axes(
            **self._kwargs(minimize_movements=True, max_coverage_steps=1)
        )
        # N=1 with full-coverage-at-zero → slats fully closed (tilt 0).
        assert result["tilt"] == 0

    def test_compose_tilt_matches_post_pipeline_resolve(self):
        """The forecast tilt equals what post_pipeline_resolve puts on result.tilt.

        Proves both paths flow through the single ``_compose_tilt`` seam — the
        projected track never diverges from the live decision.
        """
        from custom_components.adaptive_cover_pro.const import ControlMethod
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

        policy = VenetianPolicy()
        kw = self._kwargs(position=50)
        forecast_tilt = policy.forecast_secondary_axes(**kw)[policy.axes[1].name]

        cover = MagicMock()
        cover.direct_sun_valid = True
        live = policy.post_pipeline_resolve(
            PipelineResult(
                position=50, control_method=ControlMethod.SOLAR, reason="test"
            ),
            logger=kw["logger"],
            sol_azi=kw["sol_azi"],
            sol_elev=kw["sol_elev"],
            sun_data=kw["sun_data"],
            config=kw["config"],
            config_service=kw["config_service"],
            options=kw["options"],
            cover=cover,
        )
        assert forecast_tilt == live.tilt

    def test_compose_tilt_matches_post_pipeline_resolve_with_quantize(self):
        """Parity must also hold under minimize-movements quantization."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_MAX_COVERAGE_STEPS,
            CONF_MINIMIZE_MOVEMENTS,
            ControlMethod,
        )
        from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

        policy = VenetianPolicy()
        options = {CONF_MINIMIZE_MOVEMENTS: True, CONF_MAX_COVERAGE_STEPS: 2}
        kw = self._kwargs(
            position=50, minimize_movements=True, max_coverage_steps=2, options=options
        )
        forecast_tilt = policy.forecast_secondary_axes(**kw)[policy.axes[1].name]

        cover = MagicMock()
        cover.direct_sun_valid = True
        live = policy.post_pipeline_resolve(
            PipelineResult(
                position=50, control_method=ControlMethod.SOLAR, reason="test"
            ),
            logger=kw["logger"],
            sol_azi=kw["sol_azi"],
            sol_elev=kw["sol_elev"],
            sun_data=kw["sun_data"],
            config=kw["config"],
            config_service=kw["config_service"],
            options=options,
            cover=cover,
        )
        assert forecast_tilt == live.tilt
