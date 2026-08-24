"""A hold's clamp verdict is judged per cover, not against the instance mean (#1174).

``PipelineSnapshot.current_cover_position`` is the arithmetic mean of every bound
cover's position. ``ManualOverrideHandler`` / ``GroupLockHandler`` copy it into
``PipelineResult.held_position``, and the registry's axis-constraint pass compared
*that* single number against the composed floor/ceiling — so on a multi-cover
instance the cover actually being commanded never entered the comparison. Three
shades at 40/40/0 mean 27, and the whole group was judged against 27: the two
compliant shades were dragged to a 40 floor they already satisfied, and the
mirror case (100/100/0 → mean 67) hid a genuine violation entirely.

Both halves of the dispatch question are per cover: whether a command goes out
to this cover, and where it sends it. One shared number could not answer either
— a floor and a ceiling that both outrank the hold bind different covers in
opposite directions, and a tilt clamp commands covers the position axis never
released. The priority gate (#1170), the trace and every singular
``PipelineResult`` field stay shared; ``hold_clamp_verdicts`` is the dispatch
authority.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_INTERP, CONF_INVERSE_STATE
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
    CoverAxis,
    CoverTypePolicy,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)

from tests._helpers.priorities import ABOVE_HOLDER, BELOW_HOLDER
from tests.test_pipeline.conftest import make_snapshot
from tests.test_pipeline.test_axis_constraint_composition import _slot
from tests.test_pipeline.test_floor_composition import (
    _climate_cover,
    _cp_handler,
    _cp_state,
    _registry_with_custom,
)

# The issue's own numbers: two shades sitting on the 40 % floor, one closed.
# ``int(round((40 + 40 + 0) / 3)) == 27`` — the mean the registry used to judge.
_ISSUE_POSITIONS = {"cover.a": 40, "cover.b": 40, "cover.c": 0}
_ISSUE_MEAN = 27
_FLOOR = 40


def _floor_vs_hold(
    *,
    cover_positions=None,
    current_cover_position: int,
    floor: int = _FLOOR,
    floor_priority: int = ABOVE_HOLDER,
    position_axis_inverted: bool = False,
    interp_curve=None,
):
    """Evaluate a manual hold against one min-mode custom-position floor."""
    registry = _registry_with_custom(
        [_cp_handler(1, floor, priority=floor_priority), ManualOverrideHandler()]
    )
    return registry.evaluate(
        make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            manual_override_active=True,
            current_cover_position=current_cover_position,
            cover_positions=cover_positions,
            default_position=100,
            direct_sun_valid=False,
            position_axis_inverted=position_axis_inverted,
            interp_curve=interp_curve,
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.floor",
                    is_on=True,
                    position=floor,
                    min_mode=True,
                    sensor_name="Default",
                    priority=floor_priority,
                )
            ],
        )
    )


def _ceiling_vs_hold(
    *,
    cover_positions,
    current_cover_position: int,
    ceiling: int,
    ceiling_priority: int = ABOVE_HOLDER,
):
    """Evaluate a manual hold against one ceiling-only custom-position slot."""
    slot = _slot(1, position_max=ceiling, priority=ceiling_priority)
    registry = _registry_with_custom(
        [
            CustomPositionHandler(slot=1, position=None, priority=ceiling_priority),
            ManualOverrideHandler(),
        ]
    )
    return registry.evaluate(
        make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            manual_override_active=True,
            current_cover_position=current_cover_position,
            cover_positions=cover_positions,
            default_position=100,
            direct_sun_valid=False,
            custom_position_sensors=[slot],
        )
    )


def _floor_and_ceiling_vs_hold(
    *,
    cover_positions,
    current_cover_position: int,
    floor: int,
    ceiling: int,
):
    """Evaluate a manual hold against a floor AND a ceiling, both outranking it."""
    sensors = [
        _slot(1, position=floor, min_mode=True, priority=ABOVE_HOLDER),
        _slot(2, position_max=ceiling, priority=ABOVE_HOLDER),
    ]
    registry = _registry_with_custom(
        [
            _cp_handler(1, floor, priority=ABOVE_HOLDER),
            CustomPositionHandler(slot=2, position=None, priority=ABOVE_HOLDER),
            ManualOverrideHandler(),
        ]
    )
    return registry.evaluate(
        make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            manual_override_active=True,
            current_cover_position=current_cover_position,
            cover_positions=cover_positions,
            default_position=100,
            direct_sun_valid=False,
            custom_position_sensors=sensors,
        )
    )


def test_floor_judges_each_cover_not_the_instance_mean() -> None:
    """The verbatim #1174 repro: 40/40/0 against a 40 % floor.

    The mean is 27, below the floor, so the pre-fix registry raised the whole
    group — including the two shades already sitting exactly on 40.
    """
    result = _floor_vs_hold(
        cover_positions=_ISSUE_POSITIONS, current_cover_position=_ISSUE_MEAN
    )

    # One shared target: the bound edge every released cover lands on.
    assert result.position == _FLOOR
    assert result.position_constraint_applied is True
    assert result.skip_command is False

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.c"].released is True
    assert verdicts["cover.a"].released is False
    assert verdicts["cover.b"].released is False
    # Each verdict reports that cover's OWN position, never the mean.
    assert verdicts["cover.a"].held_position == 40
    assert verdicts["cover.b"].held_position == 40
    assert verdicts["cover.c"].held_position == 0


def test_mean_would_have_hidden_the_violation() -> None:
    """The counterfactual: 100/100/0 means 67, which clears the 40 % floor.

    Averaging therefore under-clamps as readily as it over-clamps — the closed
    shade violates the floor and nothing fired at all. Together with the repro
    above this pins both failure directions.
    """
    result = _floor_vs_hold(
        cover_positions={"cover.a": 100, "cover.b": 100, "cover.c": 0},
        current_cover_position=67,
    )

    assert result.position_constraint_applied is True
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.c"].released is True
    assert verdicts["cover.a"].released is False
    assert verdicts["cover.b"].released is False


# ---------------------------------------------------------------------------
# Scope edges — what per-cover judging must NOT have changed
# ---------------------------------------------------------------------------


def test_priority_gate_stays_instance_wide() -> None:
    """#1170's gate runs before any judging and is presence+priority only.

    A floor at the shipped slot default (77) does not outrank the manual hold
    (80), so ``_split_bounds_against_hold`` strips it and there is nothing left
    to judge against. Per-cover judging must not leak past that gate and
    release the closed shade anyway.
    """
    result = _floor_vs_hold(
        cover_positions=_ISSUE_POSITIONS,
        current_cover_position=_ISSUE_MEAN,
        floor_priority=BELOW_HOLDER,
    )

    assert result.skip_command is True
    assert result.position_constraint_applied is False
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert [v.released for v in verdicts.values()] == [False, False, False]


def test_per_cover_judgment_converts_frames_like_the_scalar_did() -> None:
    """#1036's cover→logical conversion now runs per cover, not once on the mean.

    On an inverse install raw 20 IS logical 80 (compliant with a logical-25
    floor) while raw 90 is logical 10 (violating). The mean of the two raws is
    55 → logical 45, which clears the floor entirely: the scalar comparison saw
    no violation at all.
    """
    result = _floor_vs_hold(
        cover_positions={"cover.compliant": 20, "cover.violating": 90},
        current_cover_position=55,
        floor=25,
        position_axis_inverted=True,
    )

    assert result.position == 25
    assert result.position_constraint_applied is True
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.violating"].released is True
    assert verdicts["cover.compliant"].released is False
    # Verdicts stay in the RAW cover frame the coordinator's extras speak.
    assert verdicts["cover.violating"].held_position == 90
    assert verdicts["cover.compliant"].held_position == 20


def test_ceiling_judges_each_cover() -> None:
    """The ceiling mirror: only the cover above it is lowered to the bound."""
    result = _ceiling_vs_hold(
        cover_positions={"cover.high": 80, "cover.low": 50},
        current_cover_position=65,
        ceiling=60,
    )

    assert result.position == 60
    assert result.position_constraint_applied is True
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.high"].released is True
    assert verdicts["cover.low"].released is False


def test_scalar_snapshot_without_cover_positions_is_byte_identical() -> None:
    """No per-entity dict → the legacy singular comparison, and no verdicts.

    Every caller that predates #1174 — and every snapshot a test builds
    directly — lands here, which is what makes the change additive.
    """
    result = _floor_vs_hold(current_cover_position=_ISSUE_MEAN)

    assert result.position == _FLOOR
    assert result.position_constraint_applied is True
    assert result.skip_command is False
    assert result.hold_clamp_verdicts is None


# ---------------------------------------------------------------------------
# A cover the per-entity dict cannot speak for
# ---------------------------------------------------------------------------
#
# ``cover_positions`` is ``dict[str, int | None]``: an entity that has not
# reported a numeric position yet is present with a ``None`` value. That cover
# still has to be judged — on the summary mean, which is the only thing anyone
# knows about it — and still has to appear in the verdicts, or the coordinator
# silently falls back to the instance-wide answer for it.


def test_a_cover_with_no_position_is_judged_on_the_summary_mean() -> None:
    """``None`` position + a violating mean → released, carrying that mean.

    The reporting sibling sits exactly on the floor and is not released, so the
    two covers diverge: a ``None`` entry is neither dropped from the dict nor
    given its neighbour's answer.
    """
    result = _floor_vs_hold(
        cover_positions={"cover.reporting": _FLOOR, "cover.silent": None},
        current_cover_position=_ISSUE_MEAN,
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert set(verdicts) == {"cover.reporting", "cover.silent"}
    assert verdicts["cover.silent"].held_position == _ISSUE_MEAN
    assert verdicts["cover.silent"].released is True
    assert verdicts["cover.reporting"].released is False


def test_a_cover_with_no_position_rides_a_compliant_mean() -> None:
    """The mirror: the mean clears the floor, so the silent cover is NOT released.

    A genuine violator is released in the same cycle, so "not released" here is
    a real judgment on the mean rather than a cycle where nothing clamped.
    """
    result = _floor_vs_hold(
        # 100 and 0 average to 50, which clears the 40 floor.
        cover_positions={"cover.high": 100, "cover.low": 0, "cover.silent": None},
        current_cover_position=50,
    )

    assert result.position_constraint_applied is True
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.low"].released is True
    assert verdicts["cover.silent"].held_position == 50
    assert verdicts["cover.silent"].released is False


@pytest.mark.asyncio
async def test_a_cover_absent_from_the_dict_takes_the_instance_wide_answer() -> None:
    """A cover with no entry at all is not judged, and must not blow up.

    The registry judges exactly the covers the snapshot lists, so an entity the
    dict never saw gets no verdict — and ``_dispatch_to_cover`` falls back to
    the singular ``skip_command`` for it rather than raising ``KeyError``.
    """
    result = _floor_vs_hold(
        cover_positions=_ISSUE_POSITIONS, current_cover_position=_ISSUE_MEAN
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert "cover.ghost" not in verdicts

    coord = _dispatch_coordinator(result)
    await coord._dispatch_to_cover("cover.ghost", _FLOOR, "custom_position_1", ctx=None)

    # skip_command is False (the floor clamped), so the instance-wide answer is
    # "command it" — the same value every released cover receives.
    coord._cmd_svc.apply_position.assert_called_once_with(
        "cover.ghost", _FLOOR, "custom_position_1", context=None
    )


# ---------------------------------------------------------------------------
# A tilt clamp is a command, not a no-op (#1170 audit) — and the command it
# forces must not move the covers the position axis left alone
# ---------------------------------------------------------------------------
#
# A tilt bound that outranks the holder clears ``skip_command`` for the whole
# group: every held cover has to receive a command so the slats reach the
# hardware. There is no single position that means "hold where you are AND take
# this tilt", which is why each verdict carries its own target — a released
# cover gets the bound edge, a held one gets its own position, a value the
# same-position gate turns into a pure tilt service call.

_TILT_FILL = 10
_TILT_FLOOR = 60

# Compliant covers parked well ABOVE the floor, so "held where it is" and
# "commanded to the floor" are two different numbers. Judging with covers that
# sit exactly ON the bound cannot tell the two apart.
_ABOVE_FLOOR_POSITIONS = {"cover.a": 80, "cover.b": 80, "cover.c": 0}


def _dispatch_coordinator(result, policy=None, interp: tuple[int, int] | None = None):
    """Minimal coordinator around a ready-made result, for ``_dispatch_to_cover``.

    ``policy`` defaults to a venetian one — the cover type these tilt-clamp
    tests are about. A coupled cover type's test passes the SAME policy
    instance whose ``post_pipeline_resolve`` already ran, because that hook is
    where the per-entity dispatch cache is filled.

    ``interp`` configures a calibration curve mapping a logical 0–100 onto that
    device travel. Off by default: every test written before #943's audit runs
    uncalibrated, where the verdict's two frames coincide.
    """
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    coord._use_interpolation = interp is not None
    coord.start_value, coord.end_value = interp if interp is not None else (None, None)
    coord.normal_list = None
    coord.new_list = None
    coord.config_entry = MagicMock()
    coord.config_entry.options = {
        CONF_INVERSE_STATE: False,
        CONF_INTERP: interp is not None,
    }
    coord._policy = policy if policy is not None else get_policy("cover_venetian")
    coord._pipeline_result = result
    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc
    return coord


async def _dispatch_targets(
    result, covers, reason, policy=None, interp: tuple[int, int] | None = None
) -> dict[str, int | None]:
    """Fan ``result`` out over ``covers`` and report what each one received.

    The value is the position actually commanded, or ``None`` when the seam
    wrote a hold-skip record instead — the two outcomes ``_dispatch_to_cover``
    chooses between. Every cover is offered the same ``state`` the real cycle
    computes (``coordinator.state``), so any divergence in the result comes
    from the per-cover verdict and nothing else.
    """
    coord = _dispatch_coordinator(result, policy=policy, interp=interp)
    state = coord._to_cover_frame(result.position)
    for cover in covers:
        await coord._dispatch_to_cover(cover, state, reason, None)
    sent = {c.args[0]: c.args[1] for c in coord._cmd_svc.apply_position.call_args_list}
    skipped = {c.args[0] for c in coord._cmd_svc.record_skipped_action.call_args_list}
    assert sent.keys() | skipped == set(covers)
    assert not sent.keys() & skipped
    return {cover: sent.get(cover) for cover in covers}


def _tilt_clamp_vs_hold(
    *, cover_positions=None, with_position_floor: bool = False, interp_curve=None
):
    """Evaluate a manual hold released by an outranking TILT bound.

    A FIXED tilt-only slot below the holder fills the tilt (#514); a tilt floor
    above the holder then clamps it (#943). By default nothing claims the
    position axis, so the released command comes entirely from the tilt side —
    the shape the #1170 audit named when it ruled that a tilt clamp is a
    command. ``with_position_floor`` adds an outranking position floor so both
    axes clamp in the same cycle.

    ``interp_curve`` puts a calibration curve on the SNAPSHOT — what the judge
    reads (#1230), as distinct from ``_dispatch_coordinator``'s ``interp``,
    which calibrates the write side alone.
    """
    sensors = [
        _slot(1, tilt=_TILT_FILL, tilt_only=True, priority=BELOW_HOLDER),
        _slot(2, tilt_min=_TILT_FLOOR, priority=ABOVE_HOLDER),
    ]
    handlers = [
        CustomPositionHandler(
            slot=1, position=None, priority=BELOW_HOLDER, tilt=_TILT_FILL
        ),
        CustomPositionHandler(slot=2, position=None, priority=ABOVE_HOLDER),
        ManualOverrideHandler(),
    ]
    if with_position_floor:
        sensors.append(_slot(3, position=_FLOOR, min_mode=True, priority=ABOVE_HOLDER))
        handlers.append(_cp_handler(3, _FLOOR, priority=ABOVE_HOLDER))
    registry = _registry_with_custom(handlers)
    return registry.evaluate(
        make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            cover_type="cover_venetian",
            manual_override_active=True,
            current_cover_position=_ISSUE_MEAN,
            cover_positions=cover_positions,
            default_position=100,
            direct_sun_valid=False,
            interp_curve=interp_curve,
            custom_position_sensors=sensors,
        )
    )


def test_tilt_clamp_releases_the_hold_on_a_legacy_snapshot() -> None:
    """Baseline: no per-entity dict, so nothing has changed here since #1170."""
    result = _tilt_clamp_vs_hold()

    assert result.tilt == _TILT_FLOOR
    assert result.skip_command is False
    assert result.position_constraint_applied is False
    assert result.hold_clamp_verdicts is None


def test_a_tilt_clamp_commands_every_held_cover() -> None:
    """The production snapshot shape: ``cover_positions`` present, tilt clamped.

    Every real cycle after the first carries the dict, and with the position
    axis inert nothing there released anybody. The tilt bound releases the
    group on its own, so every verdict has to say so — dropping the dict
    instead put the whole group back on one instance-wide number.
    """
    result = _tilt_clamp_vs_hold(cover_positions=_ISSUE_POSITIONS)

    assert result.tilt == _TILT_FLOOR
    assert result.skip_command is False
    assert result.position_constraint_applied is False
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert all(v.released for v in verdicts.values())


@pytest.mark.asyncio
async def test_a_tilt_only_clamp_sends_each_cover_its_own_position() -> None:
    """Nothing claimed the position axis, so nobody's position may change.

    The command exists only to carry the slats, so each cover's target is its
    own current position — a positional no-op the same-position gate turns into
    a pure tilt service call. Sending one shared number instead drove all three
    shades to the instance mean, 27, which is the literal number in the issue
    title and nobody's position.
    """
    result = _tilt_clamp_vs_hold(cover_positions=_ISSUE_POSITIONS)

    targets = await _dispatch_targets(result, _ISSUE_POSITIONS, "manual_override")

    assert targets == dict(_ISSUE_POSITIONS)
    assert _ISSUE_MEAN not in targets.values()


@pytest.mark.asyncio
async def test_a_tilt_clamp_does_not_drag_a_compliant_cover_to_the_floor() -> None:
    """Both axes clamp: the floor moves only the cover that violates it.

    ``cover.a`` and ``cover.b`` are held at 80, well above the 40 floor, and a
    *tilt* bound is the only reason a command goes out to them at all. Letting
    that tilt command carry the position axis's clamp target closed them by 40
    points — worse than the mean-based behaviour #1174 set out to fix, which at
    least sent 53.
    """
    result = _tilt_clamp_vs_hold(
        cover_positions=_ABOVE_FLOOR_POSITIONS, with_position_floor=True
    )

    assert result.tilt == _TILT_FLOOR
    assert result.position == _FLOOR
    assert result.position_constraint_applied is True
    assert result.skip_command is False

    targets = await _dispatch_targets(
        result, _ABOVE_FLOOR_POSITIONS, "custom_position_3"
    )

    assert targets == {"cover.a": 80, "cover.b": 80, "cover.c": _FLOOR}


@pytest.mark.asyncio
async def test_a_tilt_only_clamp_is_a_no_op_on_a_calibrated_install() -> None:
    """A verdict's two halves stop sharing a frame once a curve is configured.

    ``target`` is the bound edge for a cover a bound moved and the cover's OWN
    read for one nothing moved. A configured edge is a canonical logical value
    the calibration curve still owes a mapping (#469); a read is already a
    device-frame number that only had the inversion undone on the way in
    (#1036). Sending both through ``_to_cover_frame`` re-maps the reads: on a
    20–80 curve the three shades at 80/80/0 were commanded to 68/68/20, which
    is the carriage move this whole path exists to prevent.

    The curve here is on the DISPATCH coordinator only; the snapshot carries
    ``interp_curve=None``. That combination is deliberately not a state
    production reaches — both sides read the same ``CONF_INTERP`` since #1230 —
    and it is the point: this pins the write-side #1229 behaviour against the
    NO-CURVE judge path, holding one variable still. The consistent-state
    equivalent, curve on both halves, is
    ``test_hold_clamp_interpolated_frame.py::test_a_curve_snapshot_tilt_clamp_is_still_a_no_op_end_to_end``.
    """
    result = _tilt_clamp_vs_hold(cover_positions=_ABOVE_FLOOR_POSITIONS)

    targets = await _dispatch_targets(
        result, _ABOVE_FLOOR_POSITIONS, "manual_override", interp=(20, 80)
    )

    assert targets == dict(_ABOVE_FLOOR_POSITIONS)


@pytest.mark.asyncio
async def test_a_calibrated_floor_still_reaches_its_own_device_position() -> None:
    """The other half: a bound edge keeps the calibration it has always had.

    ``cover.c`` violates the 40 floor, so its verdict carries the configured
    edge — a logical 40, which a 20–80 curve puts at device 44. Suppressing the
    curve for every verdict would under-close it, which is #469's defect in the
    opposite direction.

    As above, the curve is on the DISPATCH coordinator only and the snapshot
    carries ``interp_curve=None`` — a state production does not reach since
    #1230, held deliberately so this pins the write-side mapping against the
    no-curve judge path. The consistent-state equivalent is
    ``test_hold_clamp_interpolated_frame.py::test_a_calibrated_release_dispatches_the_floor_in_the_device_frame``.
    """
    result = _tilt_clamp_vs_hold(
        cover_positions=_ABOVE_FLOOR_POSITIONS, with_position_floor=True
    )

    targets = await _dispatch_targets(
        result, _ABOVE_FLOOR_POSITIONS, "custom_position_3", interp=(20, 80)
    )

    assert targets == {"cover.a": 80, "cover.b": 80, "cover.c": 44}


# ---------------------------------------------------------------------------
# A floor and a ceiling bind different covers in opposite directions
# ---------------------------------------------------------------------------
#
# ``PipelineResult.position`` can only carry one of the two edges — ``clamp_to_bounds``
# applies the floor last, so the floor wins the shared field. Dispatching that
# to every released cover sent the one the *ceiling* bound to the floor instead,
# a move in the wrong direction and one the pre-#1174 code never made (the mean
# sat between the bounds, so nothing clamped and the hold survived).


@pytest.mark.asyncio
async def test_a_ceiling_bound_cover_is_lowered_to_the_ceiling_not_the_floor() -> None:
    """Floor 40 + ceiling 70, covers at 20 and 90: each goes to its own bound."""
    covers = {"cover.low": 20, "cover.high": 90}
    result = _floor_and_ceiling_vs_hold(
        cover_positions=covers,
        # The mean, 55, sits inside [40, 70] — the scalar comparison saw no
        # violation at all and left the hold alone.
        current_cover_position=55,
        floor=_FLOOR,
        ceiling=70,
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.low"].released is True
    assert verdicts["cover.high"].released is True

    targets = await _dispatch_targets(result, covers, "custom_position_1")

    assert targets == {"cover.low": _FLOOR, "cover.high": 70}


# ---------------------------------------------------------------------------
# The trace has to name the bound that actually moved a cover
# ---------------------------------------------------------------------------
#
# With a floor and a ceiling binding two different covers, only ONE of them can
# be the shared ``final_pos``'s binding constraint — and the other fell through
# to the "did nothing" sweep, which reported it as *inactive* while it was busy
# moving a cover. The decision trace is a user-facing surface (it backs the
# Lovelace card), so a bound that moved something must say so.


def test_a_ceiling_that_moved_a_cover_is_not_traced_as_inactive() -> None:
    """Floor 40 + ceiling 70, covers at 20 and 90 — both bounds bound.

    ``cover.high`` is dispatched to 70 by the ceiling in slot 2, so a step
    reading "ceiling 70% inactive" is affirmatively false, and crediting only
    the floor's slot for the cycle names the wrong slot for that cover.
    """
    result = _floor_and_ceiling_vs_hold(
        cover_positions={"cover.low": 20, "cover.high": 90},
        current_cover_position=55,
        floor=_FLOOR,
        ceiling=70,
    )

    steps = {s.handler: s for s in result.decision_trace}
    assert "ceiling_clamp" in steps
    assert steps["ceiling_clamp"].matched is True
    assert steps["ceiling_clamp"].position == 70
    # Each clamp step credits the slot whose bound produced it.
    assert "slot2" in steps["ceiling_clamp"].reason
    assert "slot1" in steps["floor_clamp"].reason
    assert "inactive" not in " ".join(s.reason for s in result.decision_trace)


def test_one_sided_clamp_keeps_its_single_trace_step() -> None:
    """Only the ceiling binds → exactly one clamp step, as before this change."""
    result = _floor_and_ceiling_vs_hold(
        cover_positions={"cover.low": 50, "cover.high": 90},
        current_cover_position=70,
        floor=_FLOOR,
        ceiling=70,
    )

    handlers = [s.handler for s in result.decision_trace]
    assert handlers.count("ceiling_clamp") == 1
    assert "floor_clamp" not in handlers


# ---------------------------------------------------------------------------
# Coupled cover types are judged as ONE unit, never per cover
# ---------------------------------------------------------------------------
#
# Per-cover judging is only sound where each bound entity's position is decided
# on its own. A cover type that REMAPS the resolved position per entity
# (``resolve_entity_target``), REWRITES it after the pipeline
# (``post_pipeline_resolve``) or orders its entities because they are
# physically coupled (``dispatch_order_key``) breaks that premise in three
# different ways, so those instances stay on the shared-target path.

_DN_BOTTOM = "cover.bottom_rail"
_DN_MIDDLE = "cover.middle_rail"
_DN_SHADE = "cover.day_night"
#: All-sheer-vs-all-blackout midpoint: middle rail halfway to the bottom rail.
_DN_BLEND = 50


def _day_night_options(model: str, **extra) -> dict:
    from custom_components.adaptive_cover_pro.const import (
        CONF_DAY_NIGHT_CONTROL_MODEL,
    )

    return {CONF_DAY_NIGHT_CONTROL_MODEL: model, **extra}


def _resolve_through_policy(result, policy, options):
    """Run the coordinator's post-pipeline policy hook exactly as the cycle does.

    ``coordinator._update_cover_position`` calls this between
    ``registry.evaluate`` and the dispatch loop, so a test that skips it is not
    testing the production sequence at all.
    """
    return policy.post_pipeline_resolve(
        result,
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(),
        config=MagicMock(),
        config_service=MagicMock(),
        options=options,
        cover=None,
    )


def _coupled_hold(
    *,
    cover_type: str,
    cover_positions,
    current_cover_position: int,
    blend: int | None,
    policy=None,
    floor: int | None = None,
    floor_priority: int = ABOVE_HOLDER,
    ceiling: int | None = None,
    tilt_min: int | None = None,
    position_axis_inverted: bool = False,
    interp_curve=None,
):
    """Evaluate a manual hold on a dual-fabric shade with the named bounds active.

    Slot 1 is a tilt-only contribution below the holder, which is how the
    fabric blend reaches ``PipelineResult.tilt`` on a real day/night instance.
    ``blend=None`` emits no tilt slot at all — the shape a real hold cycle has
    once ``_resolve_blend`` has cleared the tilt.

    ``policy`` is the SAME instance the caller resolves through afterwards, which
    is what production does (``coordinator._policy`` rides every snapshot). A
    ``None`` policy makes the registry build a fresh one from ``cover_type``,
    still sitting on its ``__init__`` defaults (#1179).
    """
    sensors = []
    handlers = []
    if blend is not None:
        sensors.append(_slot(1, tilt=blend, tilt_only=True, priority=BELOW_HOLDER))
        handlers.append(
            CustomPositionHandler(
                slot=1, position=None, priority=BELOW_HOLDER, tilt=blend
            )
        )
    handlers.append(ManualOverrideHandler())
    if floor is not None:
        sensors.append(_slot(2, position=floor, min_mode=True, priority=floor_priority))
        handlers.append(_cp_handler(2, floor, priority=floor_priority))
    if ceiling is not None:
        sensors.append(_slot(3, position_max=ceiling, priority=ABOVE_HOLDER))
        handlers.append(
            CustomPositionHandler(slot=3, position=None, priority=ABOVE_HOLDER)
        )
    if tilt_min is not None:
        sensors.append(_slot(4, tilt_min=tilt_min, priority=ABOVE_HOLDER))
        handlers.append(
            CustomPositionHandler(slot=4, position=None, priority=ABOVE_HOLDER)
        )
    registry = _registry_with_custom(handlers)
    return registry.evaluate(
        make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            cover_type=cover_type,
            policy=policy,
            manual_override_active=True,
            current_cover_position=current_cover_position,
            cover_positions=cover_positions,
            default_position=100,
            direct_sun_valid=False,
            position_axis_inverted=position_axis_inverted,
            interp_curve=interp_curve,
            custom_position_sensors=sensors,
        )
    )


class _CoupledStubPolicy(CoverTypePolicy):
    """A coupled policy that names its own reference position (#1179).

    Overriding ``resolve_entity_target`` is what trips the derived independence
    gate, so this stub is coupled for exactly the reason a real remapping policy
    is — the registry is asked one polymorphic question and reads no cover-type
    string. ``reference_calls`` records whether the reduction hook was consulted
    at all, which is the half an assertion on the resolved position cannot see.
    """

    cover_type: ClassVar[str] = "cover_reference_stub"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)

    def __init__(self, reference: int | None) -> None:
        self._reference = reference
        self.reference_calls = 0

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        return MagicMock()

    def resolve_entity_target(
        self,
        entity_id: str,  # noqa: ARG002
        position: int,
        *,
        inverted: bool | None = None,  # noqa: ARG002
        interpolated: bool = False,  # noqa: ARG002
    ) -> int:
        return position

    def hold_reference_position(
        self,
        cover_positions,  # noqa: ARG002
        *,
        inverted: bool,  # noqa: ARG002
    ) -> int | None:
        self.reference_calls += 1
        return self._reference


class _IndependentStubPolicy(_CoupledStubPolicy):
    """The same stub, declaring its entities independent after all."""

    cover_type: ClassVar[str] = "cover_reference_stub_independent"

    def entities_move_independently(self) -> bool:
        """Independent by declaration; the remap above is a documented identity."""
        return True


def _stub_reference_hold(policy):
    """Build a hold whose mean (64) clears the floor while its reference does not.

    Separating the two numbers is the whole point: judging the summary mean
    leaves this hold alone, and judging the policy's named reference clamps it.
    """
    return _coupled_hold(
        cover_type="cover_blind",
        policy=policy,
        cover_positions={"cover.x": 58, "cover.y": 70},
        current_cover_position=64,
        blend=None,
        floor=60,
    )


def test_a_coupled_policy_that_names_a_reference_is_judged_on_it() -> None:
    """The seam, with zero cover-type knowledge: one hook, one judged number.

    The stub reports 40 — below the 60 floor — while the instance mean is 64 and
    clears it. So the clamp fires, the trace's ``from_pos`` names the reference
    rather than the mean, and the coupled path still emits no verdicts: the
    clamped abstract value rides ``PipelineResult.position`` through the shared
    dispatch route the coupled types already use.
    """
    policy = _CoupledStubPolicy(reference=40)
    result = _stub_reference_hold(policy)

    assert result.position == 60
    assert result.position_constraint_applied is True
    assert result.skip_command is False
    assert result.hold_clamp_verdicts is None
    assert policy.reference_calls == 1

    steps = {s.handler: s for s in result.decision_trace}
    assert steps["floor_clamp"].reason_payload.params["from_pos"] == 40


def test_an_independent_policy_never_consults_the_reduction_hook() -> None:
    """#1174's path is untouched: independence answers first, and totally.

    A policy that reports independent entities is judged per cover exactly as it
    was before #1179, and the reduction hook is not called at all — so no
    reference a policy happens to define can leak into the eleven cover types
    that never needed one.
    """
    policy = _IndependentStubPolicy(reference=40)
    result = _stub_reference_hold(policy)

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.x"].released is True
    assert verdicts["cover.y"].released is False
    assert policy.reference_calls == 0


def test_a_policy_less_snapshot_answers_from_a_default_constructed_policy() -> None:
    """``registry._policy_for``'s fallback, pinned to the FRESH instance (#1179).

    A snapshot carrying no ``policy`` makes the registry resolve
    ``get_policy(cover_type)`` — the right class, but an instance that has never
    seen ``sync_runtime_options``. Both coupling questions became
    instance-state-dependent in #1179, so the fallback answers from ``__init__``
    defaults: day/night reads as Model A and the dual panel as front-less, both
    independent, neither naming a reference — where the pre-#1179 class-derived
    predicate said coupled for either.

    Production never reaches this path (``snapshot_builder`` attaches the
    coordinator's live, synced policy to every snapshot), which is exactly why
    the answer needs pinning: it is a documented hazard for a future caller that
    skips that step, not behaviour anyone observes today.
    """
    from custom_components.adaptive_cover_pro.pipeline.registry import (
        _hold_reference_position,
        _judges_per_cover,
        _policy_for,
    )

    for cover_type in ("cover_day_night_shade", "cover_dual_panel"):
        snapshot = make_snapshot(
            cover=_climate_cover(direct_sun_valid=False),
            cover_type=cover_type,
            cover_positions={"cover.a": 40, "cover.b": 70},
        )
        assert snapshot.policy is None, cover_type
        fallback = _policy_for(snapshot)
        assert fallback.__class__ is get_policy(cover_type).__class__, cover_type
        assert _judges_per_cover(snapshot) is True, cover_type
        assert _hold_reference_position(snapshot) is None, cover_type


def _day_night_policy(options: dict | None = None):
    """Build a day/night policy already synced to ``options``, as production has it.

    ``coordinator._update_options`` drives ``sync_runtime_options`` before the
    pipeline runs, so by the time the registry asks the policy anything it knows
    its control model and its rail roles. A test that skips that step hands the
    registry a policy sitting on its ``__init__`` defaults (#1179).
    """
    from custom_components.adaptive_cover_pro.cover_types.day_night_shade import (
        DayNightShadePolicy,
    )

    policy = DayNightShadePolicy()
    if options is not None:
        policy.sync_runtime_options(options)
    return policy


def _model_c_options():
    from custom_components.adaptive_cover_pro.const import (
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
        DAY_NIGHT_MODEL_DUAL_ENTITY,
    )

    return _day_night_options(
        DAY_NIGHT_MODEL_DUAL_ENTITY,
        **{CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: _DN_MIDDLE},
    )


def _model_b_options():
    from custom_components.adaptive_cover_pro.const import DAY_NIGHT_MODEL_SPLIT_RANGE

    return _day_night_options(DAY_NIGHT_MODEL_SPLIT_RANGE)


def _model_a_options():
    from custom_components.adaptive_cover_pro.const import (
        DAY_NIGHT_MODEL_POSITION_TILT,
    )

    return _day_night_options(DAY_NIGHT_MODEL_POSITION_TILT)


def test_model_a_judges_each_shade_on_its_own_position() -> None:
    """Model A binds ordinary covers, so #1174's per-cover fix reaches it too.

    ``position_tilt`` drives one position axis and one physical tilt axis per
    entity: it remaps nothing, orders nothing, and rewrites no position after the
    pipeline. Its shades are as independent as a plain blind's, so the derived
    gate excluding them was a false negative — and the verbatim #1174 repro
    (40/40/0 against a 40 % floor, mean 27) dragged the two shades already
    sitting on the floor down to it.
    """
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=_day_night_policy(_model_a_options()),
        cover_positions=_ISSUE_POSITIONS,
        current_cover_position=_ISSUE_MEAN,
        blend=_DN_BLEND,
        floor=_FLOOR,
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.c"].released is True
    assert verdicts["cover.a"].released is False
    assert verdicts["cover.b"].released is False
    assert verdicts["cover.c"].held_position == 0


def test_a_coupled_cover_type_produces_no_verdicts_at_all() -> None:
    """One question, asked of the policy, and its answer is total.

    Same snapshot shape, same numbers, two cover types: the coupled one resolves
    ONE position for the whole geometry — its bottom rail at 40 violates the 60
    floor, so the shared result is the floor — and the independent one gets a
    verdict per cover. Nothing here reads a cover-type string;
    ``entities_move_independently`` is the whole gate.
    """
    positions = {_DN_BOTTOM: 40, _DN_MIDDLE: 70}
    kwargs = {
        "cover_positions": positions,
        "current_cover_position": 55,
        "blend": _DN_BLEND,
        "floor": 60,
    }

    coupled = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=_day_night_policy(_model_c_options()),
        **kwargs,
    )
    assert coupled.hold_clamp_verdicts is None
    assert coupled.position == 60
    assert coupled.position_constraint_applied is True

    independent = _coupled_hold(cover_type="cover_blind", **kwargs)
    assert independent.hold_clamp_verdicts is not None
    assert independent.hold_clamp_verdicts[_DN_BOTTOM].released is True
    assert independent.hold_clamp_verdicts[_DN_MIDDLE].released is False


@pytest.mark.asyncio
async def test_model_c_rails_are_never_judged_apart() -> None:
    """Model C's two rails share a track: one geometry, not two verdicts.

    The bottom rail at 40 % with a 50 % blend puts the middle rail at 70 %. A
    floor of 60 binds the bottom rail and a ceiling of 65 binds the middle one,
    so judging them separately sends the bottom to 60 and the middle to its own
    edge, 65 — which the middle-rail remap then treats as a bottom-rail value
    and folds a SECOND time, driving the rail to 82: above the very ceiling
    that bound it, and with the sheer band silently stretched.

    Coupled: the pair resolves ONE position (60), and the remap derives the
    middle rail from it exactly once → 80.
    """
    options = _model_c_options()
    policy = _day_night_policy(options)

    positions = {_DN_BOTTOM: 40, _DN_MIDDLE: 70}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=55,
        blend=_DN_BLEND,
        floor=60,
        ceiling=65,
    )
    resolved = _resolve_through_policy(result, policy, options)

    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_BOTTOM: 60, _DN_MIDDLE: 80}


@pytest.mark.asyncio
async def test_model_c_middle_rail_follows_a_floor_that_binds_the_bottom() -> None:
    """A floor that moves the bottom rail must move the middle rail with it.

    Independently judged, the middle rail at 70 clears a 60 floor and is left
    exactly where it is — which compresses the sheer band from 30 points to 20
    the moment the bottom rail rises to 60. The rails are geometry, not two
    opinions.
    """
    options = _model_c_options()
    policy = _day_night_policy(options)

    positions = {_DN_BOTTOM: 40, _DN_MIDDLE: 70}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=55,
        blend=_DN_BLEND,
        floor=60,
    )
    resolved = _resolve_through_policy(result, policy, options)

    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_BOTTOM: 60, _DN_MIDDLE: 80}


# ---------------------------------------------------------------------------
# Model C: the BOTTOM rail is the coverage the bounds are about (#1179)
# ---------------------------------------------------------------------------
#
# The middle rail is ``M = 100 - blend * (100 - P) / 100`` — a pure function of
# the bottom rail. Averaging it into ``held_position`` mixes a coverage with a
# value derived from that coverage, and the result is a number neither rail
# sits at. Judged against a floor it hides violations; against a ceiling it
# invents them, because the middle rail is always at or above the bottom one.

#: Blend used by the reduction fixtures: 20 % sheer, so the middle rail sits far
#: enough above the bottom to make the mean visibly wrong in both directions.
_DN_NARROW_BLEND = 20


@pytest.mark.asyncio
async def test_a_floor_below_the_mean_still_moves_a_violating_bottom_rail() -> None:
    """Bottom rail 40, middle 88 — the mean is 64 and clears a 60 floor.

    The shade's coverage is 40 and violates the floor, but the derived middle
    rail drags the average above it, so the whole geometry was left where it was.
    Judged on the bottom rail the floor binds, and the middle rail follows it up
    to 92 through the remap that already owns that arithmetic.
    """
    options = _model_c_options()
    policy = _day_night_policy(options)

    positions = {_DN_BOTTOM: 40, _DN_MIDDLE: 88}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=64,
        blend=_DN_NARROW_BLEND,
        floor=60,
    )

    assert result.position_constraint_applied is True
    assert result.skip_command is False
    steps = {s.handler: s for s in result.decision_trace}
    # The trace names the rail the floor actually lifted, not the mean.
    assert steps["floor_clamp"].reason_payload.params["from_pos"] == 40

    resolved = _resolve_through_policy(result, policy, options)
    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_BOTTOM: 60, _DN_MIDDLE: 92}


@pytest.mark.asyncio
async def test_a_ceiling_the_bottom_rail_satisfies_does_not_move_the_shade() -> None:
    """The mirror: bottom rail 55, middle 91, mean 73 against a 60 ceiling.

    The coverage is 55 and already clears the ceiling; only the derived middle
    rail is "above" it, and the middle rail is always above the bottom one by
    construction. Judging the mean therefore invented a violation and closed a
    hold the user had placed by hand.
    """
    options = _model_c_options()
    policy = _day_night_policy(options)

    positions = {_DN_BOTTOM: 55, _DN_MIDDLE: 91}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=73,
        blend=_DN_NARROW_BLEND,
        ceiling=60,
    )

    assert result.position_constraint_applied is False
    assert result.skip_command is True

    resolved = _resolve_through_policy(result, policy, options)
    targets = await _dispatch_targets(
        resolved, positions, "manual_override", policy=policy
    )

    assert targets == {_DN_BOTTOM: None, _DN_MIDDLE: None}


def test_a_sub_priority_bound_cannot_move_a_coupled_hold() -> None:
    """#1170's gate still runs first, and a coupled reference cannot slip past it.

    The same violating fixture, with the floor demoted below the holder: the
    bound is stripped before anything is judged, so there is nothing for the
    reduction hook's answer to be compared against.
    """
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=_day_night_policy(_model_c_options()),
        cover_positions={_DN_BOTTOM: 40, _DN_MIDDLE: 88},
        current_cover_position=64,
        blend=_DN_NARROW_BLEND,
        floor=60,
        floor_priority=BELOW_HOLDER,
    )

    assert result.skip_command is True
    assert result.position_constraint_applied is False
    assert result.hold_clamp_verdicts is None


def test_the_reduction_hook_reads_raw_and_returns_logical() -> None:
    """The frames, pinned: raw reads in, a logical position out (#993).

    On an inverse install the bottom rail's raw 90 IS logical 10 and violates a
    logical 25 floor. The raw mean of 90 and 18 is 54 → logical 46, which clears
    the floor outright, so getting the frame wrong here is not a rounding
    difference — it is the whole answer.
    """
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=_day_night_policy(_model_c_options()),
        cover_positions={_DN_BOTTOM: 90, _DN_MIDDLE: 18},
        current_cover_position=54,
        blend=_DN_NARROW_BLEND,
        floor=25,
        position_axis_inverted=True,
    )

    assert result.position == 25
    assert result.position_constraint_applied is True
    steps = {s.handler: s for s in result.decision_trace}
    assert steps["floor_clamp"].reason_payload.params["from_pos"] == 10


# ---------------------------------------------------------------------------
# Dual panel: the FRONT is the coverage the bounds are about (#1179)
# ---------------------------------------------------------------------------
#
# ``compute_layered`` discards ``front`` when it computes ``back`` — the back
# panel is a binary privacy state, not a coverage opinion. Averaging it in gives
# a number that moves with a blackout deploy the bounds were never about.

_DP_FRONT = "cover.sheer_front"
_DP_BACK = "cover.blackout_back"


def _dual_panel_policy(options: dict):
    """Build a dual-panel policy already synced to ``options``, as production has."""
    from custom_components.adaptive_cover_pro.cover_types.dual_panel import (
        DualPanelPolicy,
    )

    policy = DualPanelPolicy()
    policy.sync_runtime_options(options)
    return policy


def _dual_panel_options(front: str | None = _DP_FRONT) -> dict:
    from custom_components.adaptive_cover_pro.const import CONF_DUAL_PANEL_FRONT_ENTITY

    return {CONF_DUAL_PANEL_FRONT_ENTITY: front} if front is not None else {}


@pytest.mark.asyncio
async def test_a_floor_below_the_mixed_mean_still_moves_a_violating_front_panel() -> (
    None
):
    """Front 40, back retracted at 100 — the mean is 70 and clears a 60 floor.

    The window's coverage is the front panel's 40 and violates the floor; the
    back is simply "not deployed", a state the floor has nothing to say about.
    Judged on the front the floor binds, and the back keeps its own absolute
    target rather than being dragged onto the front's clamp (#1035).
    """
    options = _dual_panel_options()
    policy = _dual_panel_policy(options)

    positions = {_DP_FRONT: 40, _DP_BACK: 100}
    result = _coupled_hold(
        cover_type="cover_dual_panel",
        policy=policy,
        cover_positions=positions,
        current_cover_position=70,
        blend=None,
        floor=60,
    )

    assert result.position_constraint_applied is True
    assert result.skip_command is False
    assert result.hold_clamp_verdicts is None

    resolved = _resolve_through_policy(result, policy, options)
    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DP_FRONT: 60, _DP_BACK: 100}


@pytest.mark.asyncio
async def test_a_back_panel_privacy_state_cannot_drag_a_compliant_front_panel() -> None:
    """The mirror: front 80 clears the 60 floor, but a deployed back means 0.

    The mean of 80 and 0 is 40, so the floor "fired" and closed a front panel the
    user had placed at 80 — a move driven entirely by a blackout deploy that is
    not a coverage opinion at all.
    """
    options = _dual_panel_options()
    policy = _dual_panel_policy(options)

    positions = {_DP_FRONT: 80, _DP_BACK: 0}
    result = _coupled_hold(
        cover_type="cover_dual_panel",
        policy=policy,
        cover_positions=positions,
        current_cover_position=40,
        blend=None,
        floor=60,
    )

    assert result.position_constraint_applied is False
    assert result.skip_command is True

    resolved = _resolve_through_policy(result, policy, options)
    targets = await _dispatch_targets(
        resolved, positions, "manual_override", policy=policy
    )

    assert targets == {_DP_FRONT: None, _DP_BACK: None}


# ---------------------------------------------------------------------------
# Model B: one wire carries coverage AND fabric, so a hold's read must be decoded
# ---------------------------------------------------------------------------
#
# ``split_range`` has no second physical axis. The carriage's raw read is a
# composite ``(coverage, fabric)`` encoding, and comparing a coverage floor
# against it is a category error even on a SINGLE-entity instance, where the
# mean is not the culprit. And ``_resolve_blend`` clears ``tilt`` for every hold
# winner, so a clamped hold used to be dispatched unfolded — the abstract
# coverage sent straight onto the wire, halving the carriage's travel.

#: Carriage read decoding to coverage 40 behind the BLACKOUT fabric.
_DN_WIRE_BLACKOUT_40 = 20
#: Carriage read decoding to the same coverage 40, behind the SHEER fabric.
_DN_WIRE_SHEER_40 = 70


def test_a_split_range_floor_the_carriage_already_clears_leaves_it_alone() -> None:
    """Wire 20 is coverage 40, and a 30 % floor has nothing to say to it.

    Read as a raw position, 20 looks like a violation — and the fold then sent
    the "clamped" 30 back out as wire 15, closing a shade the floor was supposed
    to be opening.
    """
    options = _model_b_options()
    policy = _day_night_policy(options)

    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions={_DN_SHADE: _DN_WIRE_BLACKOUT_40},
        current_cover_position=_DN_WIRE_BLACKOUT_40,
        blend=10,
        floor=30,
    )

    assert result.position_constraint_applied is False
    assert result.skip_command is True


@pytest.mark.asyncio
async def test_a_split_range_floor_the_decoded_coverage_violates_moves_the_carriage() -> (
    None
):
    """Wire 70 is coverage 40 behind sheer, and a 60 % floor DOES bind it.

    The mirror failure: read raw, 70 clears the floor and the shade stayed at 40
    % coverage against a 60 % bound. Decoded, the floor lifts the coverage to 60
    and the fold puts it back on the wire in the same fabric half → 80.
    """
    options = _model_b_options()
    policy = _day_night_policy(options)

    positions = {_DN_SHADE: _DN_WIRE_SHEER_40}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=_DN_WIRE_SHEER_40,
        blend=80,
        floor=60,
    )

    assert result.position_constraint_applied is True
    assert result.skip_command is False

    resolved = _resolve_through_policy(result, policy, options)
    assert resolved.position == 80

    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_SHADE: 80}


@pytest.mark.asyncio
async def test_a_clamped_split_range_hold_is_dispatched_folded_not_raw() -> None:
    """No blend survives a hold, and the fold has to happen anyway.

    ``_resolve_blend`` clears ``tilt`` for every hold winner, so with no tilt slot
    at all the re-encode gate never fired and the clamped abstract coverage 60
    went onto the wire verbatim — physically 60 % of travel into the SHEER half,
    which is coverage 20 behind the wrong fabric. The carriage's own decoded
    fabric is the honest blend to fold with: same fabric, new coverage → wire 30.
    """
    options = _model_b_options()
    policy = _day_night_policy(options)

    positions = {_DN_SHADE: _DN_WIRE_BLACKOUT_40}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=_DN_WIRE_BLACKOUT_40,
        blend=None,
        floor=60,
    )

    assert result.position == 60
    resolved = _resolve_through_policy(result, policy, options)
    # A hold gains no fabricated blend on the Target Tilt sensor; only the wire
    # and the trace learn which fabric the carriage is physically behind.
    assert resolved.tilt is None
    assert resolved.position == 30

    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_SHADE: 30}


@pytest.mark.asyncio
async def test_model_b_fabric_change_still_reaches_the_hardware() -> None:
    """Model B folds coverage AND fabric into one wire — so a hold's own read is stale.

    ``split_range`` has no independent physical secondary axis: the tilt clamp's
    new blend only reaches the motor through ``post_pipeline_resolve``'s
    coverage+fabric fold, which runs AFTER the registry. Releasing the cover to
    the raw position it is already sitting at makes the command a genuine no-op
    and silently drops the clamp.

    The carriage read is a WIRE, not an abstract coverage: wire 20 is coverage 40
    behind blackout. The tilt bound raises the blend to 60, the sheer half — so
    the same coverage in the new fabric is wire 70. Folding the raw 20 instead
    treated it as abstract coverage and halved it to 20 while changing fabric,
    which is a move the fabric change never asked for.
    """
    options = _model_b_options()
    policy = _day_night_policy(options)

    positions = {_DN_SHADE: _DN_WIRE_BLACKOUT_40}
    result = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=_DN_WIRE_BLACKOUT_40,
        blend=10,
        tilt_min=60,
    )
    resolved = _resolve_through_policy(result, policy, options)

    assert resolved.tilt == 60
    assert resolved.position == 70

    targets = await _dispatch_targets(
        resolved, positions, "manual_override", policy=policy
    )

    assert targets == {_DN_SHADE: 70}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carriage", "computed_blend", "expected_wire"),
    [
        pytest.param(_DN_WIRE_BLACKOUT_40, 20, 30, id="blackout"),
        pytest.param(_DN_WIRE_SHEER_40, 80, 80, id="sheer"),
    ],
)
async def test_a_clamped_hold_dispatches_the_same_wire_as_a_computed_winner(
    carriage: int, computed_blend: int, expected_wire: int
) -> None:
    """The governing invariant: coverage 60 dispatches one wire, however it got there.

    A clamped hold at abstract coverage X must reach the hardware as exactly the
    wire a computed winner at X would — the property that makes reducing the
    per-entity reads to one abstract number defensible at all. The hold's fabric
    comes from its own carriage; the computed winner's comes from the pipeline;
    same half, same wire.
    """
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    options = _model_b_options()

    computed = _resolve_through_policy(
        PipelineResult(
            position=60,
            control_method=ControlMethod.CUSTOM_POSITION,
            reason="computed",
            tilt=computed_blend,
        ),
        _day_night_policy(options),
        options,
    )
    assert computed.position == expected_wire

    policy = _day_night_policy(options)
    positions = {_DN_SHADE: carriage}
    held = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions=positions,
        current_cover_position=carriage,
        blend=None,
        floor=60,
    )
    resolved = _resolve_through_policy(held, policy, options)

    targets = await _dispatch_targets(
        resolved, positions, "custom_position_2", policy=policy
    )

    assert targets == {_DN_SHADE: expected_wire}


def test_an_off_cycle_reference_cannot_fold_a_computed_winner() -> None:
    """Only a HOLD may read the fabric stash, because only a hold wrote it (#1179).

    The stash is a side channel between two calls on the live policy, and the two
    are NOT the only pair that can use it. ``async_apply_user_position`` evaluates
    the pipeline off-cycle on that same policy with no ``sync_runtime_options``
    ahead of it, and ``_async_update_data`` suspends twice — on the
    ``prime_cache`` executor job and on ``manager.reset_if_needed`` — between
    clearing the stash and consuming it, with no lock serialising a service call
    against the cycle. So a ``set_position`` tap landing on one of those awaits
    can leave a fabric behind that this cycle never asked for.

    What the fallback is gated on is the only thing that rules it out: this
    cycle's OWN winner being a hold. A Model B hold always writes the stash
    itself — ``held_position`` is ``current_cover_position``, the mean of the very
    ``cover_positions`` the snapshot carries, so a non-``None`` hold implies a
    numeric read; ``entities_move_independently`` is ``False`` for Model B, so the
    registry always asks ``hold_reference_position``; and that branch always
    stashes when it has a read. A computed winner reads nothing, so the leftover
    below can only be ignored.
    """
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.engine.covers.day_night_shade import (
        DAY_NIGHT_BLACKOUT,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    options = _model_b_options()
    policy = _day_night_policy(options)

    # The interleaved off-cycle evaluate: a user command's preemption check on
    # the live policy, whose winner is a hold, so the registry reduces the
    # carriage read and the Model B branch stashes its fabric half.
    off_cycle = _coupled_hold(
        cover_type="cover_day_night_shade",
        policy=policy,
        cover_positions={_DN_SHADE: _DN_WIRE_BLACKOUT_40},
        current_cover_position=_DN_WIRE_BLACKOUT_40,
        blend=None,
    )
    assert off_cycle.held_position == _DN_WIRE_BLACKOUT_40
    assert policy._split_range_hold_fabric == DAY_NIGHT_BLACKOUT

    # The suspended cycle resumes and finishes on a computed winner whose method
    # carries no fabric opinion — ``_resolve_blend`` clears ``tilt`` for it.
    # Nothing this cycle decided says anything about fabric, so nothing folds.
    resolved = _resolve_through_policy(
        PipelineResult(
            position=60,
            control_method=ControlMethod.SOLAR,
            reason="computed",
        ),
        policy,
        options,
    )

    assert resolved.tilt is None
    assert resolved.position == 60
    assert [s.handler for s in resolved.decision_trace] == []
