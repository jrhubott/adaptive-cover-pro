"""A hold is judged on its LOGICAL position, calibration curve unwound (#1230).

``_judge_position_axis`` compares a held cover's read against the user's
configured floor/ceiling. The read is a RAW device-frame number and the bounds
are logical user values, so the read is converted first — but until #1230 that
conversion was ``flip_if`` alone, which knows about ``inverse_state`` and
nothing about an interpolation calibration curve. Worse, ``axis_inverted``
*suppresses* inversion whenever the position axis is interpolated
(``cover_types/base.py``), so on a calibrated install ``flip_if`` is the
identity and the judge compared the device number directly against a logical
bound.

The issue's own numbers: on a 20–80 curve a shade reading device 42 is
logically at 36.67, which violates a 40 % floor — and the judge saw ``42 >= 40``
and called it compliant. The floor never fired. The fix routes both conversion
sites through ``position_utils.from_cover_frame``, the full algebraic inverse of
``coordinator._to_cover_frame``: un-invert, then un-interpolate.

The curve reaches the pure pipeline as plain data on
``PipelineSnapshot.interp_curve`` — the registry never holds a coordinator.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_START,
    CUSTOM_POSITION_SLOTS,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
    TILT_AXIS,
    CoverAxis,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.position_utils import InterpolationCurve

from tests._helpers.priorities import ABOVE_HOLDER, BELOW_HOLDER
from tests.test_pipeline.test_floor_composition import (
    _climate_cover,
    _cp_handler,
    _registry_with_custom,
)
from tests.test_pipeline.test_hold_clamp_per_cover import (
    _ABOVE_FLOOR_POSITIONS,
    _FLOOR,
    _TILT_FILL,
    _TILT_FLOOR,
    _CoupledStubPolicy,
    _coupled_hold,
    _dispatch_targets,
    _floor_vs_hold,
    _tilt_clamp_vs_hold,
)
from tests.test_pipeline.test_snapshot_builder import _make_builder

#: The issue's worked example: a 20–80 device travel calibrated from 0–100.
_CURVE = (20, 80)
#: The device read that started it all, and the logical position it really is.
_DEVICE_READ = 42
_LOGICAL_READ = 37  # round((42 - 20) / 0.6) == round(36.67)
#: Where the logical floor lands on the wire: 20 + 0.6 * 40.
_FLOOR_ON_THE_WIRE = 44

_SLOT_1 = CUSTOM_POSITION_SLOTS[1]
_SLOT_2 = CUSTOM_POSITION_SLOTS[2]

_ONLY_COVER = "cover.only"


def _curve_options(**slot_keys: object) -> dict:
    """Build a calibrated install's options, plus whatever slot keys a test needs."""
    return {
        CONF_INTERP: True,
        CONF_INTERP_START: _CURVE[0],
        CONF_INTERP_END: _CURVE[1],
        **slot_keys,
    }


def _sensor_state(name: str = "on") -> MagicMock:
    state = MagicMock()
    state.state = name
    state.attributes = {}
    return state


def _build_via_builder(
    options: dict,
    *,
    cover_type: str,
    sensors: tuple[str, ...],
    current_cover_position: int,
    cover_positions=None,
):
    """Build a snapshot the way production does — from options, through the builder.

    The curve has to travel from ``config_entry.options`` to the snapshot for
    the judge to see it at all, so these tests refuse the shortcut of handing
    ``make_snapshot`` a ready-made curve: the option→snapshot binding is half of
    what #1230 is about.
    """
    config_service = MagicMock()
    config_service.get_glare_zones_config.return_value = None
    builder, _, _ = _make_builder(
        states={eid: _sensor_state() for eid in sensors},
        policy=get_policy(cover_type),
        config_service=config_service,
    )
    return builder.build(
        options,
        cover_data=_climate_cover(direct_sun_valid=False),
        cover_type=cover_type,
        climate_readings=None,
        manual_override_active=True,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=current_cover_position,
        cover_positions=cover_positions,
        is_glare_zone_enabled=lambda idx: True,  # noqa: ARG005
        effective_default=100,
        is_sunset_active=False,
    )


def _calibrated_floor_hold(*, cover_positions=None):
    """Evaluate a manual hold at device 42 against an outranking logical 40 % floor."""
    options = _curve_options(
        **{
            _SLOT_1["sensor"]: "binary_sensor.floor",
            _SLOT_1["position"]: _FLOOR,
            _SLOT_1["min_mode"]: True,
            _SLOT_1["priority"]: ABOVE_HOLDER,
        }
    )
    snapshot = _build_via_builder(
        options,
        cover_type="cover_blind",
        sensors=("binary_sensor.floor",),
        current_cover_position=_DEVICE_READ,
        cover_positions=cover_positions,
    )
    registry = _registry_with_custom(
        [_cp_handler(1, _FLOOR, priority=ABOVE_HOLDER), ManualOverrideHandler()]
    )
    return registry.evaluate(snapshot)


def _calibrated_tilt_clamp_hold():
    """Evaluate a manual hold released by a TILT bound alone, on a calibrated install.

    The position axis is inert — slot 1 fixes the slat angle only, slot 2 raises
    it — so the command that goes out exists purely to carry the slats, and the
    carriage must not move. ``cover_positions`` is deliberately absent: this is
    the SINGULAR path, where no per-cover verdict stands between
    ``result.position`` and the wire.
    """
    options = _curve_options(
        **{
            _SLOT_1["sensor"]: "binary_sensor.tilt_fill",
            # A tilt-only slot claims nothing on the position axis
            # (``position_mode`` is NONE), but the slot-configured gate wants a
            # claim key present before the slot participates at all.
            _SLOT_1["position"]: 0,
            _SLOT_1["tilt"]: _TILT_FILL,
            _SLOT_1["tilt_only"]: True,
            _SLOT_1["priority"]: BELOW_HOLDER,
            _SLOT_2["sensor"]: "binary_sensor.tilt_floor",
            _SLOT_2["tilt_min"]: _TILT_FLOOR,
            _SLOT_2["priority"]: ABOVE_HOLDER,
        }
    )
    snapshot = _build_via_builder(
        options,
        cover_type="cover_venetian",
        sensors=("binary_sensor.tilt_fill", "binary_sensor.tilt_floor"),
        current_cover_position=_DEVICE_READ,
    )
    registry = _registry_with_custom(
        [
            CustomPositionHandler(
                slot=1, position=None, priority=BELOW_HOLDER, tilt=_TILT_FILL
            ),
            CustomPositionHandler(slot=2, position=None, priority=ABOVE_HOLDER),
            ManualOverrideHandler(),
        ]
    )
    return registry.evaluate(snapshot)


# ---------------------------------------------------------------------------
# The defect, end to end from options
# ---------------------------------------------------------------------------


def test_a_calibrated_floor_judges_the_logical_position_not_the_device_read() -> None:
    """The issue verbatim: device 42 on a 20–80 curve violates a logical 40 floor.

    ``42 >= 40`` is a comparison between two different frames. The cover is
    logically at 36.67, below the floor, and the release has to fire.
    """
    result = _calibrated_floor_hold(cover_positions={_ONLY_COVER: _DEVICE_READ})

    assert result.position == _FLOOR
    assert result.position_constraint_applied is True
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts[_ONLY_COVER].released is True
    assert verdicts[_ONLY_COVER].target == _FLOOR
    # The verdict's held position stays RAW — the frame contract #1174 pinned
    # and this fix deliberately does not touch.
    assert verdicts[_ONLY_COVER].held_position == _DEVICE_READ


@pytest.mark.asyncio
async def test_a_calibrated_release_dispatches_the_floor_in_the_device_frame() -> None:
    """The released cover reaches the floor's own device position, not the floor.

    ``target`` is a logical bound edge, so the dispatch seam owes it the whole
    calibration: 40 % logical is device 44 on a 20–80 travel.
    """
    result = _calibrated_floor_hold(cover_positions={_ONLY_COVER: _DEVICE_READ})

    targets = await _dispatch_targets(
        result,
        [_ONLY_COVER],
        "custom_position_1",
        policy=get_policy("cover_blind"),
        interp=_CURVE,
    )

    assert targets == {_ONLY_COVER: _FLOOR_ON_THE_WIRE}


@pytest.mark.asyncio
async def test_a_tilt_only_clamp_no_longer_drifts_the_singular_path_under_a_curve() -> (
    None
):
    """The unclamped-fallback site, which per-cover verdicts do not protect.

    Nothing claims the position axis, so ``_release_hold_for_tilt_clamp`` writes
    ``effective_winner_pos`` into ``result.position`` and the singular dispatch
    maps THAT through ``_to_cover_frame``. Feeding it the raw device read made
    the round trip a double interpolation: ``20 + 0.6 * 42 == 45``, a three-point
    phantom carriage move on a command that was only ever about the slats.
    """
    result = _calibrated_tilt_clamp_hold()

    assert result.tilt == _TILT_FLOOR
    assert result.skip_command is False
    assert result.hold_clamp_verdicts is None
    # The logical position the device read really names — what the wire mapping
    # has to start from for the round trip to land back on 42.
    assert result.position == _LOGICAL_READ

    targets = await _dispatch_targets(
        result, [_ONLY_COVER], "manual_override", interp=_CURVE
    )

    assert targets == {_ONLY_COVER: _DEVICE_READ}


# ---------------------------------------------------------------------------
# The judge's own contract, per cover
# ---------------------------------------------------------------------------


def test_per_cover_judgment_unwinds_the_curve_before_the_floor() -> None:
    """Two covers, one curve: each is un-mapped on its own read, then judged.

    ``cover.low`` reads device 42 — logically 36.67, under the floor. Its
    sibling reads device 80, the top of the calibrated travel, which is
    logically wide open. One is released and one is not, off a shared curve.
    """
    result = _floor_vs_hold(
        cover_positions={"cover.low": 42, "cover.high": 80},
        current_cover_position=61,
        interp_curve=InterpolationCurve(start_value=_CURVE[0], end_value=_CURVE[1]),
    )

    assert result.position == _FLOOR
    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.low"].released is True
    assert verdicts["cover.low"].target == _FLOOR
    assert verdicts["cover.high"].released is False
    assert verdicts["cover.high"].target == 100
    # Both held positions stay RAW — #1174's frame contract, untouched.
    assert verdicts["cover.low"].held_position == 42
    assert verdicts["cover.high"].held_position == 80


def test_a_multi_point_curve_is_unwound_per_cover() -> None:
    """The control-point curve shape reaches the judge through the same field.

    Device 25 sits on the lower leg (logically 25, violating), device 65 on the
    upper one (logically 75, compliant) — a split a simple start/end pair
    cannot express, so the multi-point branch is genuinely exercised.
    """
    result = _floor_vs_hold(
        cover_positions={"cover.low": 25, "cover.high": 65},
        current_cover_position=45,
        interp_curve=InterpolationCurve(
            normal_list=[0, 50, 100], new_list=[10, 40, 90]
        ),
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts["cover.low"].released is True
    assert verdicts["cover.low"].target == _FLOOR
    assert verdicts["cover.high"].released is False
    assert verdicts["cover.high"].target == 75


def test_a_non_monotonic_curve_leaves_the_judgment_on_the_device_read() -> None:
    """A folded forward map has no inverse, so the judge keeps the raw read.

    Pins the degraded path as a decision rather than an accident: the cover is
    judged exactly as it was before #1230 — ``42 >= 40``, compliant — because
    inventing a logical position for a non-injective curve would be worse than
    reproducing today's answer.
    """
    result = _floor_vs_hold(
        cover_positions={_ONLY_COVER: _DEVICE_READ},
        current_cover_position=_DEVICE_READ,
        interp_curve=InterpolationCurve(
            normal_list=[0, 30, 60, 100], new_list=[0, 80, 60, 100]
        ),
    )

    verdicts = result.hold_clamp_verdicts
    assert verdicts is not None
    assert verdicts[_ONLY_COVER].released is False
    assert result.position_constraint_applied is False


# ---------------------------------------------------------------------------
# The #1229 interaction: a curve on the snapshot AND on the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_curve_snapshot_tilt_clamp_is_still_a_no_op_end_to_end() -> None:
    """Nobody moves when the judge and the dispatch both know the curve.

    #1229's ``own_read`` short-circuit exists because a verdict's ``target`` was
    two frames at once. Now that every target is genuinely logical, the
    short-circuit stops firing for a cover whose read differs from its own
    logical value — and the fall-through has to land back on that read. Two
    mechanisms carry the group, and both have to hold:

    * ``cover.a`` / ``cover.b`` read device 80, logically 100. The short-circuit
      does NOT fire (``100 != 80``) and ``_to_cover_frame(100)`` round-trips to
      80. This is the contraction-curve exactness the fix rests on.
    * ``cover.c`` reads device 0, *below* the calibrated travel, so un-mapping
      clamps it to logical 0 and the round trip would open it to 20. The
      short-circuit fires instead (``0 == 0``) and returns the read untouched —
      which is precisely why #1229's branch must NOT be removed as redundant.
    """
    result = _tilt_clamp_vs_hold(
        cover_positions=_ABOVE_FLOOR_POSITIONS,
        interp_curve=InterpolationCurve(start_value=_CURVE[0], end_value=_CURVE[1]),
    )

    targets = await _dispatch_targets(
        result, _ABOVE_FLOOR_POSITIONS, "manual_override", interp=_CURVE
    )

    assert targets == dict(_ABOVE_FLOOR_POSITIONS)


# ---------------------------------------------------------------------------
# The invariant: a policy's own reference is never un-mapped twice
# ---------------------------------------------------------------------------
#
# ``CoverTypePolicy.hold_reference_position`` returns a value the policy has
# already reduced to the frame ``PipelineResult.position`` speaks. Running the
# new curve leg over it would un-interpolate a value that was never
# interpolated, silently re-scaling a coupled instance's position. The hook's
# own docstring reserves interpolation unwinding to #925; these tests make that
# reservation a lock rather than a comment.


class _CoupledTiltStubPolicy(_CoupledStubPolicy):
    """The coupled reference stub, with a tilt axis so a tilt clamp can fire."""

    cover_type: ClassVar[str] = "cover_reference_stub_tilt"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS, TILT_AXIS)


#: A reference deliberately OFF the curve's fixed point: ``from_cover_frame``
#: maps 44 to 40 on a 20–80 travel, so "was it un-mapped?" has two different
#: answers and the assertion cannot pass by coincidence. (50 would be useless
#: here — it is a fixed point of this curve's inverse.)
_REFERENCE = 44
_REFERENCE_IF_WRONGLY_UNMAPPED = 40


def test_a_policy_reference_is_never_uninterpolated_a_second_time() -> None:
    """The judged number is the policy's reference verbatim, curve or no curve.

    The floor sits at 60, above both candidates, so it clamps either way and
    ``result.position`` cannot tell them apart. The trace's ``from_pos`` can:
    it carries what the judge actually compared.
    """
    policy = _CoupledStubPolicy(reference=_REFERENCE)
    result = _coupled_hold(
        cover_type="cover_blind",
        policy=policy,
        cover_positions={"cover.x": 58, "cover.y": 70},
        current_cover_position=64,
        blend=None,
        floor=60,
        interp_curve=InterpolationCurve(start_value=_CURVE[0], end_value=_CURVE[1]),
    )

    assert policy.reference_calls == 1
    steps = {s.handler: s for s in result.decision_trace}
    from_pos = steps["floor_clamp"].reason_payload.params["from_pos"]
    assert from_pos == _REFERENCE
    assert from_pos != _REFERENCE_IF_WRONGLY_UNMAPPED


def test_a_policy_reference_rides_the_tilt_release_un_curved() -> None:
    """The unclamped-fallback arm keeps the same exemption.

    Nothing binds the position axis, so a tilt clamp releases the hold and
    ``effective_winner_pos`` — the reference — is written straight into
    ``result.position``. That branch sits inside the expression #1230 changed,
    so it is pinned here too.
    """
    policy = _CoupledTiltStubPolicy(reference=_REFERENCE)
    result = _coupled_hold(
        cover_type="cover_blind",
        policy=policy,
        cover_positions={"cover.x": 58, "cover.y": 70},
        current_cover_position=64,
        blend=_TILT_FILL,
        tilt_min=_TILT_FLOOR,
        interp_curve=InterpolationCurve(start_value=_CURVE[0], end_value=_CURVE[1]),
    )

    assert result.tilt == _TILT_FLOOR
    assert result.skip_command is False
    assert result.position == _REFERENCE
    assert result.position != _REFERENCE_IF_WRONGLY_UNMAPPED
