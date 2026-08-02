"""A hold's clamp verdict is judged per cover, not against the instance mean (#1174).

``PipelineSnapshot.current_cover_position`` is the arithmetic mean of every bound
cover's position. ``ManualOverrideHandler`` / ``GroupLockHandler`` copy it into
``PipelineResult.held_position``, and the registry's axis-constraint pass compared
*that* single number against the composed floor/ceiling — so on a multi-cover
instance the cover actually being commanded never entered the comparison. Three
shades at 40/40/0 mean 27, and the whole group was judged against 27: the two
compliant shades were dragged to a 40 floor they already satisfied, and the
mirror case (100/100/0 → mean 67) hid a genuine violation entirely.

Only the *release verdict* is per cover. The clamp target, the priority gate
(#1170), the trace and every singular ``PipelineResult`` field stay shared — a
clamped cover always lands exactly on the bound edge, so the dispatched value is
still one instance-wide number; only *whether each cover receives it* is judged
against that cover's own position.
"""

from __future__ import annotations

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
