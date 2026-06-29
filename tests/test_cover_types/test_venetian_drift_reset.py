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

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_VENETIAN_TILT_RESET_DIRECTION,
    CONF_VENETIAN_TILT_RESET_THRESHOLD,
    DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
    DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
    POSITION_CLOSED,
    POSITION_OPEN,
    VENETIAN_TILT_RESET_CLOSE,
    VENETIAN_TILT_RESET_OPEN,
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
