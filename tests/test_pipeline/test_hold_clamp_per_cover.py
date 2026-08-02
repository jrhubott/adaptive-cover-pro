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

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_INVERSE_STATE
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
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


def _dispatch_coordinator(result):
    """Minimal coordinator around a ready-made result, for ``_dispatch_to_cover``."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    coord._use_interpolation = False
    coord.config_entry = MagicMock()
    coord.config_entry.options = {CONF_INVERSE_STATE: False}
    coord._policy = get_policy("cover_venetian")
    coord._pipeline_result = result
    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc
    return coord


async def _dispatch_targets(result, covers, reason) -> dict[str, int | None]:
    """Fan ``result`` out over ``covers`` and report what each one received.

    The value is the position actually commanded, or ``None`` when the seam
    wrote a hold-skip record instead — the two outcomes ``_dispatch_to_cover``
    chooses between. Every cover is offered the same ``state`` the real cycle
    computes (``coordinator.state``), so any divergence in the result comes
    from the per-cover verdict and nothing else.
    """
    coord = _dispatch_coordinator(result)
    state = coord._to_cover_frame(result.position)
    for cover in covers:
        await coord._dispatch_to_cover(cover, state, reason, None)
    sent = {c.args[0]: c.args[1] for c in coord._cmd_svc.apply_position.call_args_list}
    skipped = {c.args[0] for c in coord._cmd_svc.record_skipped_action.call_args_list}
    assert sent.keys() | skipped == set(covers)
    assert not sent.keys() & skipped
    return {cover: sent.get(cover) for cover in covers}


def _tilt_clamp_vs_hold(*, cover_positions=None, with_position_floor: bool = False):
    """Evaluate a manual hold released by an outranking TILT bound.

    A FIXED tilt-only slot below the holder fills the tilt (#514); a tilt floor
    above the holder then clamps it (#943). By default nothing claims the
    position axis, so the released command comes entirely from the tilt side —
    the shape the #1170 audit named when it ruled that a tilt clamp is a
    command. ``with_position_floor`` adds an outranking position floor so both
    axes clamp in the same cycle.
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
