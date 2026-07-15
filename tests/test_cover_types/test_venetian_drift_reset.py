"""Tests for the venetian drift-reset feature (issue #663).

For venetian blinds, the sequencer accumulates per-entity commanded tilt-%
change across real (non-deduped, non-dry-run, non-gated) sends. When the
accumulator crosses ``CONF_VENETIAN_TILT_RESET_THRESHOLD`` it performs a
two-step drift-reset: drive the slats to logical ``POSITION_OPEN`` (force,
no verify), settle, re-send the original target (force, verify), then zero
the accumulator. A recursion guard stops the reset's own two sends from
re-accumulating or re-triggering.

These exercise ``DualAxisSequencer`` in isolation; the plumbing tests at the
top exercise the config/runtime wiring.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_VENETIAN_TILT_RESET_DIRECTION,
    CONF_VENETIAN_TILT_RESET_SCOPE,
    CONF_VENETIAN_TILT_RESET_THRESHOLD,
    DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
    DEFAULT_VENETIAN_TILT_RESET_SCOPE,
    DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
    POSITION_CLOSED,
    POSITION_OPEN,
    VENETIAN_TILT_RESET_CLOSE,
    VENETIAN_TILT_RESET_OPEN,
    VENETIAN_TILT_RESET_SCOPE_ALL,
    VENETIAN_TILT_RESET_SCOPE_SOLAR,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types.venetian.sequencer import (
    DualAxisSequencer,
)
from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer

pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


def _build_sequencer(
    *,
    current_positions=None,
    dry_run=False,
    get_current_tilt_position=None,
    event_buffer=None,
    invert_tilt=None,
    get_min_change=None,
    get_tilt_reset_threshold=None,
    get_tilt_reset_direction=None,
):
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    if current_positions is None:
        current_positions = []
    iter_positions = iter(current_positions)
    # Only thread the direction lambda through when supplied so back-compat
    # tests exercise the ctor's default (open) path with no kwarg present.
    extra: dict = {}
    if get_tilt_reset_direction is not None:
        extra["get_tilt_reset_direction"] = get_tilt_reset_direction
    return (
        hass,
        DualAxisSequencer(
            hass=hass,
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: next(iter_positions, None),
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: dry_run,
            get_current_tilt_position=get_current_tilt_position,
            event_buffer=event_buffer,
            invert_tilt=invert_tilt,
            get_min_change=get_min_change,
            get_tilt_reset_threshold=get_tilt_reset_threshold,
            **extra,
        ),
    )


# ---------------------------------------------------------------------------
# Step 0 — Plumbing scaffold (const + range + FieldSpec + VenetianSlice read).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRuntimeConfigPlumbing:
    """``RuntimeConfig.from_options`` reads the new threshold into VenetianSlice."""

    def test_reads_threshold(self):
        rc = RuntimeConfig.from_options({CONF_VENETIAN_TILT_RESET_THRESHOLD: 300})
        assert rc.venetian.tilt_reset_threshold == 300

    def test_default_is_zero(self):
        rc = RuntimeConfig.from_options({})
        assert rc.venetian.tilt_reset_threshold == 0
        assert DEFAULT_VENETIAN_TILT_RESET_THRESHOLD == 0

    def test_round_trip(self):
        rc = RuntimeConfig.from_options({CONF_VENETIAN_TILT_RESET_THRESHOLD: 1234})
        assert rc.venetian.tilt_reset_threshold == 1234

    def test_range_in_option_ranges(self):
        from custom_components.adaptive_cover_pro.const import OPTION_RANGES

        assert OPTION_RANGES[CONF_VENETIAN_TILT_RESET_THRESHOLD] == (0, 5000)

    def test_out_of_range_rejected_by_validator(self):
        from custom_components.adaptive_cover_pro.services.options_service import (
            FIELD_VALIDATORS,
        )

        validator = FIELD_VALIDATORS[CONF_VENETIAN_TILT_RESET_THRESHOLD]
        with pytest.raises(Exception):
            validator(6000)
        with pytest.raises(Exception):
            validator(-1)
        # In-range is accepted.
        assert validator(300) == 300


# ---------------------------------------------------------------------------
# Step 1 — Selector + summary.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectorAndSummary:
    """Extras schema carries the selector; the summary renders the line."""

    def test_extras_schema_has_selector(self):
        from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
            _venetian_extras_schema,
        )
        import voluptuous as vol

        schema = _venetian_extras_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema}
        assert CONF_VENETIAN_TILT_RESET_THRESHOLD in keys

    def test_summary_line_present_when_enabled(self):
        from custom_components.adaptive_cover_pro.config_flow import (
            _build_config_summary,
        )

        config = {CONF_VENETIAN_TILT_RESET_THRESHOLD: 300}
        summary = _build_config_summary(config, "cover_venetian")
        assert "drift" in summary.lower()
        assert "300" in summary

    def test_summary_line_omitted_when_disabled(self):
        from custom_components.adaptive_cover_pro.config_flow import (
            _build_config_summary,
        )

        config = {CONF_VENETIAN_TILT_RESET_THRESHOLD: 0}
        summary = _build_config_summary(config, "cover_venetian")
        assert "drift-reset" not in summary.lower()


# ---------------------------------------------------------------------------
# Step 2 — attach() forwards a LIVE lambda to the sequencer.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAttachForwardsLiveLambda:
    """The policy threads ``get_tilt_reset_threshold`` into the sequencer ctor."""

    def test_live_lambda_reads_current_value(self):
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        policy = get_policy("cover_venetian")
        box = {"value": 300}
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            get_tilt_reset_threshold=lambda: box["value"],
        )
        seq = policy.sequencer
        assert seq._get_tilt_reset_threshold() == 300
        box["value"] = 999
        assert seq._get_tilt_reset_threshold() == 999

    def test_default_threshold_lambda_returns_zero(self):
        _, seq = _build_sequencer()
        assert seq._get_tilt_reset_threshold() == 0


# ---------------------------------------------------------------------------
# Step 3 — Accumulator increments on a real send.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAccumulatorIncrements:
    """A real, non-gated tilt send adds ``abs(target - anchor)`` to the dict."""

    async def test_increments_from_seeded_anchor(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        seq._tilt_targets["cover.x"] = 20  # seed the anchor
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 30

    async def test_accumulates_across_sends(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        seq._tilt_targets["cover.x"] = 20
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        await seq.update_tilt_only(
            "cover.x", tilt_target=80, current_position=40, reason="solar"
        )
        # 30 (20→50) + 30 (50→80) = 60.
        assert seq._accumulated_tilt["cover.x"] == 60


# ---------------------------------------------------------------------------
# Step 4 — First-send with no anchor accumulates 0.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFirstSendNoAnchor:
    """Cold start (no resolvable anchor) accumulates 0, not the full target."""

    async def test_first_send_accumulates_zero(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        # No stored target, no get_current_tilt_position → anchor is None.
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 0

    async def test_second_send_uses_seeded_anchor(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        await seq.update_tilt_only(
            "cover.x", tilt_target=90, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 30


# ---------------------------------------------------------------------------
# Step 5 — Dedup / dry-run / min-delta-gated sends do NOT accumulate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGatedSendsDoNotAccumulate:
    """Only real mechanical travel counts."""

    async def test_dedup_does_not_accumulate(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        first = seq._accumulated_tilt["cover.x"]
        # Same target again — dedup swallows it.
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == first

    async def test_dry_run_does_not_accumulate(self):
        _, seq = _build_sequencer(dry_run=True, get_tilt_reset_threshold=lambda: 99999)
        seq._tilt_targets["cover.x"] = 20
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt.get("cover.x", 0) == 0

    async def test_min_delta_gated_does_not_accumulate(self):
        _, seq = _build_sequencer(
            get_min_change=lambda: 10, get_tilt_reset_threshold=lambda: 99999
        )
        seq._tilt_targets["cover.x"] = 50
        # 3% move below the 10% min-delta gate.
        await seq.update_tilt_only(
            "cover.x", tilt_target=53, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt.get("cover.x", 0) == 0


# ---------------------------------------------------------------------------
# Step 6 / 7 — Threshold crossing triggers two-step reset; accumulator zeroes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResetTrigger:
    """Crossing the threshold drives open then back to target."""

    async def test_two_step_reset_order_and_force(self):
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50, event_buffer=buf
        )
        seq._tilt_targets["cover.x"] = 0  # anchor 0 → delta 60 ≥ 50
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        # Sequence: original 60, POSITION_OPEN, return 60.
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert tilt_values == [60, POSITION_OPEN, 60]
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_triggered" in events
        assert "tilt_reset_open" in events
        assert "tilt_reset_return" in events

    async def test_accumulator_zeroes_after_reset(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 50)
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 0

    async def test_subsequent_send_accumulates_from_zero(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 50)
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        # After reset the stored target is 60 (return step). Next move 60→70.
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 10


# ---------------------------------------------------------------------------
# Step 8 — Recursion guard.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRecursionGuard:
    """The reset's own two sends never re-trigger or re-accumulate."""

    async def test_threshold_one_emits_exactly_three_calls(self):
        hass, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 1)
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        # original + open + return — no runaway recursion.
        assert hass.services.async_call.call_count == 3
        assert seq._accumulated_tilt["cover.x"] == 0
        assert "cover.x" not in seq._reset_in_progress


# ---------------------------------------------------------------------------
# Step 9 — 0 = disabled never resets.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDisabled:
    """Threshold 0 means no reset ever fires."""

    async def test_zero_threshold_never_resets(self):
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 0, event_buffer=buf
        )
        targets = [0, 100, 0, 100, 0]
        prev = None
        for t in targets:
            await seq.update_tilt_only(
                "cover.x", tilt_target=t, current_position=40, reason="solar"
            )
            prev = t
        assert prev is not None
        events = [e["event"] for e in buf.snapshot()]
        assert not any(ev.startswith("tilt_reset_") for ev in events)
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        # No POSITION_OPEN reset send injected — only the real sweeps.
        assert tilt_values == [0, 100, 0, 100, 0]


# ---------------------------------------------------------------------------
# Step 10 — Inverse correctness of the POSITION_OPEN step.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInverseOpenStep:
    """The open step sends logical POSITION_OPEN through ``_to_wire`` once."""

    async def test_inverted_wire_value(self):
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50, invert_tilt=lambda: True
        )
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        wire_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        # Open step is the second call. Logical 100 inverted → 0.
        assert wire_values[1] == seq._to_wire(POSITION_OPEN)
        assert wire_values[1] == 100 - POSITION_OPEN

    async def test_non_inverted_wire_value(self):
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50, invert_tilt=lambda: False
        )
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        wire_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert wire_values[1] == POSITION_OPEN


# ---------------------------------------------------------------------------
# Step 11 — clear_tilt_targets resets the accumulator + guard.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestClearTiltTargets:
    """``clear_tilt_targets`` wipes the accumulator and the recursion guard."""

    async def test_clear_resets_state(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 99999)
        seq._tilt_targets["cover.x"] = 20
        await seq.update_tilt_only(
            "cover.x", tilt_target=50, current_position=40, reason="solar"
        )
        assert seq._accumulated_tilt["cover.x"] == 30
        seq._reset_in_progress.add("cover.y")  # simulate a leaked guard
        seq.clear_tilt_targets()
        assert seq._accumulated_tilt == {}
        assert seq._reset_in_progress == set()


# ---------------------------------------------------------------------------
# Step 12 — Configurable reset direction (issue #686).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResetDirection:
    """The drift-reset endpoint follows ``get_tilt_reset_direction``."""

    async def test_close_direction_drives_to_closed(self):
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50,
            get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_CLOSE,
            event_buffer=buf,
        )
        seq._tilt_targets["cover.x"] = 0  # anchor 0 → delta 60 ≥ 50
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        # First reset send drives to the CLOSED endpoint, then back to target.
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert tilt_values == [60, POSITION_CLOSED, 60]
        # Event NAME stays stable for the Lovelace card; only the position
        # value reflects the chosen endpoint.
        snap = buf.snapshot()
        reset_open = next(e for e in snap if e["event"] == "tilt_reset_open")
        assert reset_open["tilt_position"] == POSITION_CLOSED
        triggered = next(e for e in snap if e["event"] == "tilt_reset_triggered")
        assert triggered["direction"] == VENETIAN_TILT_RESET_CLOSE

    async def test_open_direction_drives_to_open(self):
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50,
            get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_OPEN,
            event_buffer=buf,
        )
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert tilt_values == [60, POSITION_OPEN, 60]
        snap = buf.snapshot()
        reset_open = next(e for e in snap if e["event"] == "tilt_reset_open")
        assert reset_open["tilt_position"] == POSITION_OPEN
        triggered = next(e for e in snap if e["event"] == "tilt_reset_triggered")
        assert triggered["direction"] == VENETIAN_TILT_RESET_OPEN

    async def test_default_direction_is_open(self):
        """A sequencer built WITHOUT the direction lambda still resets open."""
        hass, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 50)
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert tilt_values == [60, POSITION_OPEN, 60]
        assert DEFAULT_VENETIAN_TILT_RESET_DIRECTION == VENETIAN_TILT_RESET_OPEN


@pytest.mark.unit
class TestResetDirectionPlumbing:
    """Config + policy wiring for the reset direction option."""

    def test_runtime_config_reads_direction(self):
        rc = RuntimeConfig.from_options(
            {CONF_VENETIAN_TILT_RESET_DIRECTION: VENETIAN_TILT_RESET_CLOSE}
        )
        assert rc.venetian.tilt_reset_direction == VENETIAN_TILT_RESET_CLOSE

    def test_runtime_config_default_direction(self):
        rc = RuntimeConfig.from_options({})
        assert rc.venetian.tilt_reset_direction == DEFAULT_VENETIAN_TILT_RESET_DIRECTION

    def test_extras_schema_has_direction_selector(self):
        import voluptuous as vol

        from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
            _venetian_extras_schema,
        )

        schema = _venetian_extras_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema}
        assert CONF_VENETIAN_TILT_RESET_DIRECTION in keys

    def test_validator_rejects_invalid_direction(self):
        from custom_components.adaptive_cover_pro.services.options_service import (
            FIELD_VALIDATORS,
        )

        validator = FIELD_VALIDATORS[CONF_VENETIAN_TILT_RESET_DIRECTION]
        assert validator(VENETIAN_TILT_RESET_CLOSE) == VENETIAN_TILT_RESET_CLOSE
        with pytest.raises(Exception):
            validator("sideways")

    def test_attach_forwards_live_direction_lambda(self):
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        policy = get_policy("cover_venetian")
        box = {"value": VENETIAN_TILT_RESET_OPEN}
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            get_tilt_reset_direction=lambda: box["value"],
        )
        seq = policy.sequencer
        assert seq._get_tilt_reset_direction() == VENETIAN_TILT_RESET_OPEN
        box["value"] = VENETIAN_TILT_RESET_CLOSE
        assert seq._get_tilt_reset_direction() == VENETIAN_TILT_RESET_CLOSE


# ---------------------------------------------------------------------------
# Step 13 — Configurable drift-reset SCOPE (issue #808).
# ---------------------------------------------------------------------------


def _attach_venetian_policy(*, scope=None, threshold, current_position=40):
    """Attach a real venetian policy wired for drift-reset scope tests.

    Returns ``(hass, policy, event_buffer)``. The sequencer's drift-reset
    threshold and (optionally) scope lambdas are live so tests can drive the
    policy hooks and observe whether a reset fires.
    """
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    buf = EventBuffer(maxlen=100)
    policy = get_policy("cover_venetian")
    extra: dict = {}
    if scope is not None:
        extra["get_tilt_reset_scope"] = lambda: scope
    policy.attach(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: current_position,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        event_buffer=buf,
        get_tilt_reset_threshold=lambda: threshold,
        **extra,
    )
    return hass, policy, buf


@pytest.mark.asyncio
class TestDriftResetScope:
    """Drift-reset eligibility is gated by scope + winning control method."""

    async def test_eligible_false_suppresses_reset(self):
        """A tilt-only send flagged ineligible never triggers a reset."""
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 1)
        seq._tilt_targets["cover.x"] = 0  # anchor 0 → delta 60 ≥ 1
        buf = EventBuffer(maxlen=100)
        seq._event_buffer = buf
        await seq.update_tilt_only(
            "cover.x",
            tilt_target=60,
            current_position=40,
            reason="solar",
            drift_reset_eligible=False,
        )
        events = [e["event"] for e in buf.snapshot()]
        assert not any(ev.startswith("tilt_reset_") for ev in events)
        # No accumulation either — the pre-send anchor resolve is skipped.
        assert seq._accumulated_tilt.get("cover.x", 0) == 0

    async def test_eligible_true_still_resets(self):
        """Regression guard: the default eligible path fires as before."""
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 1, event_buffer=buf
        )
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x",
            tilt_target=60,
            current_position=40,
            reason="solar",
            drift_reset_eligible=True,
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_triggered" in events

    async def test_solar_only_scope_suppresses_custom_position(self):
        """sun_tracking_only: a CUSTOM_POSITION win does not trigger a reset."""
        hass, policy, buf = _attach_venetian_policy(
            scope=VENETIAN_TILT_RESET_SCOPE_SOLAR, threshold=1
        )
        policy._last_tilt = 60
        policy.sequencer._tilt_targets["cover.x"] = 0
        ctx = SimpleNamespace(force=False, control_method=ControlMethod.CUSTOM_POSITION)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=40, context=ctx, reason="solar"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert not any(ev.startswith("tilt_reset_") for ev in events)

    async def test_solar_only_scope_allows_solar(self):
        """sun_tracking_only: a SOLAR win still triggers the reset."""
        hass, policy, buf = _attach_venetian_policy(
            scope=VENETIAN_TILT_RESET_SCOPE_SOLAR, threshold=1
        )
        policy._last_tilt = 60
        policy.sequencer._tilt_targets["cover.x"] = 0
        ctx = SimpleNamespace(force=False, control_method=ControlMethod.SOLAR)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=40, context=ctx, reason="solar"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_triggered" in events

    async def test_all_scope_allows_custom_position(self):
        """all_tilt_commands (default): a CUSTOM_POSITION win still resets."""
        hass, policy, buf = _attach_venetian_policy(
            scope=VENETIAN_TILT_RESET_SCOPE_ALL, threshold=1
        )
        policy._last_tilt = 60
        policy.sequencer._tilt_targets["cover.x"] = 0
        ctx = SimpleNamespace(force=False, control_method=ControlMethod.CUSTOM_POSITION)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=40, context=ctx, reason="solar"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_triggered" in events

    async def test_default_scope_is_all_allows_custom_position(self):
        """No scope lambda → default all_tilt_commands → reset fires."""
        hass, policy, buf = _attach_venetian_policy(scope=None, threshold=1)
        policy._last_tilt = 60
        policy.sequencer._tilt_targets["cover.x"] = 0
        ctx = SimpleNamespace(force=False, control_method=ControlMethod.CUSTOM_POSITION)
        await policy.maybe_update_tilt_only(
            "cover.x", current_position=40, context=ctx, reason="solar"
        )
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_triggered" in events


@pytest.mark.unit
class TestPositionContextControlMethod:
    """PositionContext carries a neutral control_method for the scope gate."""

    def test_field_defaults_none(self):
        from custom_components.adaptive_cover_pro.managers.cover_command.state_store import (  # noqa: E501
            PositionContext,
        )

        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[],
        )
        assert ctx.control_method is None

    def test_field_accepts_control_method(self):
        from custom_components.adaptive_cover_pro.managers.cover_command.state_store import (  # noqa: E501
            PositionContext,
        )

        ctx = PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[],
            control_method=ControlMethod.SOLAR,
        )
        assert ctx.control_method is ControlMethod.SOLAR

    def test_coordinator_populates_from_pipeline_result(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        from custom_components.adaptive_cover_pro.managers.toggles import (
            ToggleManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord._toggles = ToggleManager()
        # automatic_control=True short-circuits the bypass property in
        # _build_position_context, so no _pipeline_result is required there.
        coord.automatic_control = True
        coord.min_change = 1
        coord.time_threshold = 0
        coord._inverse_state = False
        coord.manager = MagicMock()
        coord.manager.is_cover_manual.return_value = False
        coord._policy = MagicMock()
        coord._policy.position_context_overrides.return_value = {}
        coord._pipeline_result = SimpleNamespace(
            control_method=ControlMethod.CUSTOM_POSITION,
            use_my_position=False,
            bypass_auto_control=False,
        )
        ctx = coord._build_position_context("cover.x", {})
        assert ctx.control_method is ControlMethod.CUSTOM_POSITION

    def test_coordinator_populates_none_without_pipeline_result(self):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        from custom_components.adaptive_cover_pro.managers.toggles import (
            ToggleManager,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord._toggles = ToggleManager()
        # automatic_control=True short-circuits the bypass property in
        # _build_position_context, so no _pipeline_result is required there.
        coord.automatic_control = True
        coord.min_change = 1
        coord.time_threshold = 0
        coord._inverse_state = False
        coord.manager = MagicMock()
        coord.manager.is_cover_manual.return_value = False
        coord._policy = MagicMock()
        coord._policy.position_context_overrides.return_value = {}
        coord._pipeline_result = None
        ctx = coord._build_position_context("cover.x", {})
        assert ctx.control_method is None


@pytest.mark.unit
class TestSummaryScope:
    """The config summary shows the drift-reset scope only when narrowed."""

    def test_summary_shows_scope_when_narrowed(self):
        from custom_components.adaptive_cover_pro.config_flow import (
            _build_config_summary,
        )

        config = {
            CONF_VENETIAN_TILT_RESET_THRESHOLD: 300,
            CONF_VENETIAN_TILT_RESET_SCOPE: VENETIAN_TILT_RESET_SCOPE_SOLAR,
        }
        summary = _build_config_summary(config, "cover_venetian")
        assert "drift" in summary.lower()
        assert "sun-tracking" in summary.lower()

    def test_summary_no_scope_when_default(self):
        from custom_components.adaptive_cover_pro.config_flow import (
            _build_config_summary,
        )

        config = {CONF_VENETIAN_TILT_RESET_THRESHOLD: 300}  # default = all
        summary = _build_config_summary(config, "cover_venetian")
        assert "drift" in summary.lower()
        assert "sun-tracking" not in summary.lower()


@pytest.mark.unit
class TestRuntimeConfigScopePlumbing:
    """Config + validator wiring for the drift-reset scope option."""

    def test_runtime_config_reads_scope(self):
        rc = RuntimeConfig.from_options(
            {CONF_VENETIAN_TILT_RESET_SCOPE: VENETIAN_TILT_RESET_SCOPE_SOLAR}
        )
        assert rc.venetian.tilt_reset_scope == VENETIAN_TILT_RESET_SCOPE_SOLAR

    def test_runtime_config_default_scope(self):
        rc = RuntimeConfig.from_options({})
        assert rc.venetian.tilt_reset_scope == DEFAULT_VENETIAN_TILT_RESET_SCOPE
        assert DEFAULT_VENETIAN_TILT_RESET_SCOPE == VENETIAN_TILT_RESET_SCOPE_ALL

    def test_scope_not_in_option_ranges(self):
        from custom_components.adaptive_cover_pro.const import OPTION_RANGES

        assert CONF_VENETIAN_TILT_RESET_SCOPE not in OPTION_RANGES

    def test_validator_accepts_and_rejects(self):
        from custom_components.adaptive_cover_pro.services.options_service import (
            FIELD_VALIDATORS,
        )

        validator = FIELD_VALIDATORS[CONF_VENETIAN_TILT_RESET_SCOPE]
        assert (
            validator(VENETIAN_TILT_RESET_SCOPE_SOLAR)
            == VENETIAN_TILT_RESET_SCOPE_SOLAR
        )
        assert validator(VENETIAN_TILT_RESET_SCOPE_ALL) == VENETIAN_TILT_RESET_SCOPE_ALL
        with pytest.raises(Exception):
            validator("sometimes")

    def test_extras_schema_has_scope_selector(self):
        import voluptuous as vol

        from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
            _venetian_extras_schema,
        )

        schema = _venetian_extras_schema()
        keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema}
        assert CONF_VENETIAN_TILT_RESET_SCOPE in keys

    def test_attach_forwards_live_scope_lambda(self):
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        policy = get_policy("cover_venetian")
        box = {"value": VENETIAN_TILT_RESET_SCOPE_ALL}
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: None,
            set_commanded_position=lambda *_: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            get_tilt_reset_scope=lambda: box["value"],
        )
        assert policy._get_tilt_reset_scope() == VENETIAN_TILT_RESET_SCOPE_ALL
        box["value"] = VENETIAN_TILT_RESET_SCOPE_SOLAR
        assert policy._get_tilt_reset_scope() == VENETIAN_TILT_RESET_SCOPE_SOLAR


# ---------------------------------------------------------------------------
# Issue #927 — the drift-reset endpoint excursion records a one-shot,
# VALUE-matched, time-boxed suppression so a stale endpoint state publish
# arriving after the command grace closes is not misread as a manual move.
# Matching is on the published WIRE tilt value (the endpoint the reset drove
# to), never on a reconstructed delta and never reading ``_tilt_targets``.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResetExcursionPublishSuppression:
    """``is_reset_excursion_publish`` recognises ACP's own endpoint excursion."""

    async def _run_reset(
        self,
        *,
        target,
        anchor,
        direction=VENETIAN_TILT_RESET_CLOSE,
        invert_tilt=None,
        threshold=1,
        seq=None,
    ):
        """Drive one close/open drift reset through the public path.

        Seeds the tilt anchor so the accumulator crosses ``threshold`` and the
        two-step endpoint excursion runs. ``get_current_tilt_position`` is left
        unwired so the verify step is a no-op and ``_tilt_targets`` ends at the
        restored ``target``. Pass ``seq`` to run a second reset on the SAME
        sequencer (multi-slot coverage).
        """
        if seq is None:
            _, seq = _build_sequencer(
                get_tilt_reset_threshold=lambda: threshold,
                get_tilt_reset_direction=lambda: direction,
                invert_tilt=invert_tilt,
            )
        seq._tilt_targets["cover.x"] = anchor
        await seq.update_tilt_only(
            "cover.x", tilt_target=target, current_position=40, reason="solar"
        )
        return seq

    async def test_reset_stamps_excursion_record(self):
        seq = await self._run_reset(target=79, anchor=0)
        records = seq._reset_excursion["cover.x"]
        assert [r.endpoint for r in records] == [POSITION_CLOSED]

    async def test_matching_value_within_window_suppresses(self):
        # Close reset drove tilt to the wire endpoint 0; the stale publish is
        # current_tilt_position=0 — that VALUE (not the delta 79) is what matches.
        seq = await self._run_reset(target=79, anchor=0)
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True

    async def test_match_is_one_shot(self):
        seq = await self._run_reset(target=79, anchor=0)
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True
        # Consumed — a second identical query no longer suppresses.
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is False

    async def test_non_matching_value_not_suppressed_and_record_retained(self):
        seq = await self._run_reset(target=79, anchor=0)
        # An intermediate non-endpoint publish (value 40, endpoint 0).
        assert seq.is_reset_excursion_publish("cover.x", 40.0) is False
        # The real endpoint publish still matches — record was NOT burned.
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True

    async def test_user_move_to_mirror_value_not_suppressed(self):
        """Finding #1: the old delta-based match swallowed a user move to the
        mirror value ``2·target − endpoint`` (same delta, different value).

        target=35, wire endpoint 0 → mirror = 70. The value-based predicate
        matches on 0, not on the 35-delta, so a genuine move to tilt 70 is NOT
        suppressed and the record survives for the real endpoint publish.
        """
        seq = await self._run_reset(target=35, anchor=0)
        assert seq.is_reset_excursion_publish("cover.x", 70.0) is False
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True

    async def test_diverged_stored_target_still_suppressed_by_value(self):
        """Finding #6: matching must not read ``_tilt_targets``.

        A diverged stored target (e.g. tilt-skip-above open mode stored 100
        while the restored target differs) must not make the guard inert. With
        the value-based predicate the endpoint publish (value 0) still matches
        regardless of what ``_tilt_targets`` holds.
        """
        seq = await self._run_reset(target=79, anchor=0)
        seq._tilt_targets["cover.x"] = 100  # diverged from the endpoint
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True

    async def test_window_expiry_not_suppressed(self):
        seq = await self._run_reset(target=79, anchor=0)
        records = seq._reset_excursion["cover.x"]
        seq._reset_excursion["cover.x"] = [
            dataclasses.replace(
                r,
                at=r.at
                - dt.timedelta(seconds=seq._backrotate_publish_lag_seconds + 1.0),
            )
            for r in records
        ]
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is False
        # The stale record is dropped on the expiry sweep.
        assert "cover.x" not in seq._reset_excursion

    async def test_multi_slot_two_resets_both_endpoint_publishes_suppressed(self):
        """Finding #4: two resets inside the window keep two records.

        A single-slot store would let the second reset clobber the first, so
        only one endpoint publish would suppress. With a list, both do — in
        either order — and a third query then falls through.
        """
        seq = await self._run_reset(target=79, anchor=0)  # record 1, endpoint 0
        # Second reset on the SAME sequencer; anchor now the restored target 79.
        await self._run_reset(target=20, anchor=79, seq=seq)  # record 2, endpoint 0
        assert len(seq._reset_excursion["cover.x"]) == 2
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is True
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is False

    async def test_dry_run_does_not_stamp(self):
        """Finding #5: no stamp when the endpoint send is dry-run skipped.

        ``_maybe_drift_reset`` is invoked directly with dry-run active so the
        endpoint ``_send_tilt_command`` is skipped; stamping anyway would leave
        a record with no possible matching publish.
        """
        _, seq = _build_sequencer(
            dry_run=True,
            get_tilt_reset_threshold=lambda: 1,
            get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_CLOSE,
        )
        await seq._maybe_drift_reset(
            "cover.x", original_target=79, position_target=50, pre_send_anchor=0
        )
        assert "cover.x" not in seq._reset_excursion

    async def test_stamp_not_recorded_when_endpoint_send_fails(self):
        """No stamp when the endpoint ``set_cover_tilt_position`` raises.

        If the endpoint send fails the slats never leave their angle, so no
        stale endpoint publish is coming. Recording the excursion stamp anyway
        would leave a one-shot that later swallows a genuine user move to that
        value. The stamp is recorded only after the endpoint send dispatches
        successfully.
        """
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 1,
            get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_CLOSE,
        )
        hass.services.async_call = AsyncMock(
            side_effect=HomeAssistantError("service unavailable")
        )
        await seq._maybe_drift_reset(
            "cover.x", original_target=79, position_target=50, pre_send_anchor=0
        )
        assert "cover.x" not in seq._reset_excursion

    async def test_open_direction_endpoint_match(self):
        seq = await self._run_reset(
            target=21, anchor=100, direction=VENETIAN_TILT_RESET_OPEN
        )
        assert [r.endpoint for r in seq._reset_excursion["cover.x"]] == [POSITION_OPEN]
        # Open reset drove tilt to wire endpoint 100; the publish value is 100.
        assert seq.is_reset_excursion_publish("cover.x", 100.0) is True

    async def test_inverted_tilt_endpoint_match(self):
        seq = await self._run_reset(target=60, anchor=0, invert_tilt=lambda: True)
        # Logical endpoint POSITION_CLOSED (0) → wire inverse_state(0) = 100.
        # The published wire value is 100; the naive logical 0 must NOT match
        # (proves _to_wire is applied to the recorded endpoint).
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is False
        assert seq.is_reset_excursion_publish("cover.x", 100.0) is True

    async def test_no_reset_no_suppression(self):
        _, seq = _build_sequencer(get_tilt_reset_threshold=lambda: 0)
        seq._tilt_targets["cover.x"] = 0
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        assert "cover.x" not in seq._reset_excursion
        assert seq.is_reset_excursion_publish("cover.x", 0.0) is False

    async def test_reset_two_step_send_sequence_unchanged(self):
        buf = EventBuffer(maxlen=100)
        hass, seq = _build_sequencer(
            get_tilt_reset_threshold=lambda: 50,
            get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_CLOSE,
            event_buffer=buf,
        )
        seq._tilt_targets["cover.x"] = 0  # anchor 0 → delta 60 ≥ 50
        await seq.update_tilt_only(
            "cover.x", tilt_target=60, current_position=40, reason="solar"
        )
        tilt_values = [
            c.args[2]["tilt_position"] for c in hass.services.async_call.call_args_list
        ]
        assert tilt_values == [60, POSITION_CLOSED, 60]
        events = [e["event"] for e in buf.snapshot()]
        assert "tilt_reset_open" in events
        assert "tilt_reset_return" in events
