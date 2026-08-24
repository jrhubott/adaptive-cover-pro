"""Tests for the pure position-frame converters in ``position_utils``.

``inverse_state`` and ``flip_if`` are the canonical position-frame primitives
for the whole integration (issues #1036 / #1042). They used to live at the
bottom of ``managers/manual_override/manager.py`` — a module that imports
``homeassistant.core.HomeAssistant`` — which forced the pipeline layer to
import a manager module purely to reach two ``int -> int`` functions. This file
pins both the arithmetic contract and the purity of their new home so the
converters can't drift back across the HA boundary.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from custom_components.adaptive_cover_pro.position_utils import (
    InterpolationCurve,
    covered_fraction,
    flip_if,
    from_cover_frame,
    interpolate_position,
    inverse_state,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_POSITION_UTILS = (
    _REPO_ROOT / "custom_components" / "adaptive_cover_pro" / "position_utils.py"
)


@pytest.mark.unit
def test_flip_if_inverts_when_inverted() -> None:
    """``inverted=True`` applies the ``100 - x`` involution."""
    assert flip_if(30, inverted=True) == 70


@pytest.mark.unit
def test_flip_if_is_identity_when_not_inverted() -> None:
    """``inverted=False`` returns the value untouched — a no-op on normal installs."""
    assert flip_if(30, inverted=False) == 30


@pytest.mark.unit
def test_flip_if_is_its_own_inverse() -> None:
    """The involution property is what makes one primitive serve both directions.

    ``flip_if`` converts cover→logical (a raw entity read on its way into
    ``PipelineResult.position``) and logical→cover (a pipeline value on its way
    to a dispatched or held number) with the same call, because ``100 - x``
    round-trips. Any future "optimisation" that breaks this breaks every
    round-tripping caller (``day_night_shade`` remaps a position out and back
    inside one function).
    """
    for value in range(101):
        assert flip_if(flip_if(value, inverted=True), inverted=True) == value


@pytest.mark.unit
def test_inverse_state_is_importable_from_position_utils() -> None:
    """``inverse_state`` is the raw involution, published from the pure module.

    Several call sites apply it unconditionally or inside an imperative guard
    rather than the ternary ``flip_if`` replaces, so it stays a public function
    in its own right.
    """
    assert inverse_state(30) == 70


@pytest.mark.unit
def test_position_utils_has_no_direct_homeassistant_import() -> None:
    """``position_utils`` must stay free of direct ``homeassistant.*`` imports.

    Same purity bar CLAUDE.md applies to ``engine/covers/base.py`` — direct
    imports only, not the full transitive graph (``.const`` pulls HA in
    transitively for both modules). This is the property that lets the pipeline
    layer reach the frame converters without importing a manager (#1042).
    """
    tree = ast.parse(_POSITION_UTILS.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name == "homeassistant"
                or alias.name.startswith("homeassistant.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "homeassistant" or module.startswith("homeassistant."):
                offenders.append(f"line {node.lineno}: from {module} import ...")
    assert not offenders, (
        "position_utils.py must not import homeassistant directly — it is the "
        "pure home for position-value transforms:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# covered_fraction — the shared position -> coverage-share primitive (#1236)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_covered_fraction_blind_polarity() -> None:
    """``open_blocks_sun=False`` (blind family): lowering the carriage covers more."""
    assert covered_fraction(25, open_blocks_sun=False) == pytest.approx(0.75)
    assert covered_fraction(0, open_blocks_sun=False) == pytest.approx(1.0)
    assert covered_fraction(100, open_blocks_sun=False) == pytest.approx(0.0)


@pytest.mark.unit
def test_covered_fraction_awning_polarity() -> None:
    """``open_blocks_sun=True`` (awning family): extending covers more."""
    assert covered_fraction(25, open_blocks_sun=True) == pytest.approx(0.25)
    assert covered_fraction(0, open_blocks_sun=True) == pytest.approx(0.0)
    assert covered_fraction(100, open_blocks_sun=True) == pytest.approx(1.0)


@pytest.mark.unit
@pytest.mark.parametrize("open_blocks_sun", [True, False])
def test_covered_fraction_clamps_outside_the_position_range(
    open_blocks_sun: bool,
) -> None:
    """A stray position never yields a fraction outside [0, 1]."""
    for position in (-40, 140):
        value = covered_fraction(position, open_blocks_sun=open_blocks_sun)
        assert 0.0 <= value <= 1.0


@pytest.mark.unit
def test_covered_fraction_accepts_floats() -> None:
    """HA can publish a float position; the primitive must not truncate it."""
    assert covered_fraction(37.5, open_blocks_sun=False) == pytest.approx(0.625)


@pytest.mark.unit
def test_day_night_shade_engine_delegates_to_the_shared_primitive() -> None:
    """The day/night fabric estimate must not keep its own inline expression.

    The polarity arithmetic is now written once in ``position_utils``; a second
    hand-rolled ``(100 - position) / 100`` in the engine is exactly the
    duplication the no-duplication rule forbids (#1236).
    """
    source = (
        _REPO_ROOT
        / "custom_components"
        / "adaptive_cover_pro"
        / "engine"
        / "covers"
        / "day_night_shade.py"
    ).read_text(encoding="utf-8")
    assert "covered_fraction(" in source
    assert "POSITION_OPEN - position" not in source


# ---------------------------------------------------------------------------
# from_cover_frame — the full inverse of coordinator._to_cover_frame (#1230)
# ---------------------------------------------------------------------------
#
# ``_to_cover_frame`` maps logical -> wire as "interpolate, then invert". This
# is the algebraic inverse, in reverse order: un-invert, then un-interpolate.
# The registry needs it because a held cover's read is compared against a
# user-configured logical bound, and ``flip_if`` alone left the calibration
# curve on the value (#1230).

#: The issue's worked example, as an explicit curve.
_ISSUE_CURVE = InterpolationCurve(start_value=20, end_value=80)


@pytest.mark.unit
@pytest.mark.parametrize("inverted", [True, False])
def test_from_cover_frame_without_a_curve_matches_flip_if(inverted: bool) -> None:
    """No curve configured -> exactly today's ``flip_if``, for every input.

    This is what keeps every uncalibrated install byte-identical: the registry
    swapped one call for the other, and on a snapshot carrying no curve the two
    have to be the same function.
    """
    for value in range(101):
        assert from_cover_frame(value, inverted=inverted) == flip_if(
            value, inverted=inverted
        )


@pytest.mark.unit
def test_from_cover_frame_inverts_the_simple_start_end_curve() -> None:
    """The two numbers issue #1230 is about, on a 20-80 device travel."""
    assert from_cover_frame(42, inverted=False, curve=_ISSUE_CURVE) == 37
    assert from_cover_frame(44, inverted=False, curve=_ISSUE_CURVE) == 40


@pytest.mark.unit
def test_from_cover_frame_inverts_a_multi_point_curve() -> None:
    """Both curve shapes go through one code path — the multi-point one too."""
    curve = InterpolationCurve(normal_list=[0, 50, 100], new_list=[10, 40, 90])
    assert from_cover_frame(25, inverted=False, curve=curve) == 25
    assert from_cover_frame(65, inverted=False, curve=curve) == 75


@pytest.mark.unit
@pytest.mark.parametrize(("start", "end"), [(20, 80), (30, 70), (0, 100), (10, 95)])
def test_from_cover_frame_round_trips_with_to_cover_frame(start: int, end: int) -> None:
    """Every reachable device read survives un-mapping and re-mapping — on a CONTRACTION.

    This is the property the whole fix rests on. Once the judge emits truly
    logical targets, ``coordinator._verdict_dispatch_target``'s ``own_read``
    short-circuit stops firing on a calibrated install and every target goes
    back out through ``_to_cover_frame``. That is only safe because the inverse
    is exact for a contraction curve: the un-mapping expands, so rounding the
    logical value can move the re-mapped device value by less than half a
    point.

    Every ``start``/``end`` pair contracts (``end - start <= 100``), which is
    why all four parameter sets here do. The bound genuinely stops there — see
    ``test_from_cover_frame_round_trip_is_off_by_one_on_an_expanding_curve``.
    """
    curve = InterpolationCurve(start_value=start, end_value=end)
    for device in range(start, end + 1):
        logical = from_cover_frame(device, inverted=False, curve=curve)
        assert round(interpolate_position(logical, start, end, None, None)) == device


@pytest.mark.unit
def test_from_cover_frame_round_trip_is_off_by_one_on_an_expanding_curve() -> None:
    """The exactness above is a contraction property, not a universal one.

    A multi-point control-point list can expand locally: ``[0, 50, 100]`` onto
    ``[0, 10, 100]`` runs at slope 1.8 above the midpoint, so the half-point
    ``from_cover_frame`` rounds away re-maps to nearly a full point and the
    round trip misses. Device 11 un-maps to logical 51, which maps back to 12.

    Pinned rather than fixed: sub-integer positions are not dispatchable, so
    there is nothing to carry the lost precision, and a one-point move dies in
    the delta gate. The reason this is written down is that
    ``_verdict_dispatch_target`` leans on the exactness claim, and a claim no
    test bounds is one that quietly grows.
    """
    normal_list = [0, 50, 100]
    new_list = [0, 10, 100]
    curve = InterpolationCurve(normal_list=normal_list, new_list=new_list)

    logical = from_cover_frame(11, inverted=False, curve=curve)
    assert logical == 51
    assert round(interpolate_position(logical, None, None, normal_list, new_list)) == 12

    # The contracting lower leg of the very same curve still round-trips exactly.
    for device in range(0, 11):
        back = from_cover_frame(device, inverted=False, curve=curve)
        assert (
            round(interpolate_position(back, None, None, normal_list, new_list))
            == device
        )


@pytest.mark.unit
def test_from_cover_frame_inverts_a_decreasing_curve() -> None:
    """A curve whose device travel runs backwards is still injective.

    ``np.interp`` needs its sample points ascending, so a descending curve is
    inverted by reversing BOTH ranges rather than by refusing to unwind it.
    """
    curve = InterpolationCurve(start_value=80, end_value=20)
    assert from_cover_frame(44, inverted=False, curve=curve) == 60
    assert from_cover_frame(80, inverted=False, curve=curve) == 0
    assert from_cover_frame(20, inverted=False, curve=curve) == 100


@pytest.mark.unit
def test_from_cover_frame_skips_a_non_monotonic_curve() -> None:
    """A forward map that is not injective has no inverse — so none is invented.

    This runs inside the judge on every update cycle, in a pure module. Raising
    would brick the loop for an install whose curve has "worked" for years and
    clamping would invent data, so the un-interpolation leg is skipped and the
    value degrades to exactly its pre-#1230 treatment. No new failure mode.
    """
    curve = InterpolationCurve(normal_list=[0, 30, 60, 100], new_list=[0, 80, 60, 100])
    assert from_cover_frame(42, inverted=False, curve=curve) == 42
    assert from_cover_frame(42, inverted=True, curve=curve) == 58


@pytest.mark.unit
def test_from_cover_frame_clamps_reads_outside_the_curve() -> None:
    """A read past either end of the calibrated travel lands on that end.

    Mirrors the forward map, which clamps the same way — ``np.interp``'s native
    endpoint behaviour, not a rule bolted on here.
    """
    assert from_cover_frame(10, inverted=False, curve=_ISSUE_CURVE) == 0
    assert from_cover_frame(90, inverted=False, curve=_ISSUE_CURVE) == 100


@pytest.mark.unit
def test_from_cover_frame_un_inverts_before_un_interpolating() -> None:
    """Order matters: ``_to_cover_frame`` interpolates first and inverts last.

    The combination is unsupported at runtime (the coordinator logs it and
    skips the inversion), but the helper is the stated algebraic inverse and
    has to compose in the right order to earn that description.

    **The curve has to be asymmetric or this test proves nothing.** For a
    ``start``/``end`` pair the two compositions are
    ``(100 - x - s) * 100 / (e - s)`` and ``100 - (x - s) * 100 / (e - s)``,
    which are algebraically equal exactly when ``s + e == 100``. ``_ISSUE_CURVE``
    is 20-80, so it satisfies that and returns 40 either way round — a guard
    that cannot fail. On the 10-95 curve below the correct order gives 40 and
    the swapped one gives 46, which is what makes the assertion discriminate.
    """
    curve = InterpolationCurve(start_value=10, end_value=95)

    assert from_cover_frame(56, inverted=True, curve=curve) == 40

    # The swapped composition, spelled out, so the 40 above is pinned against a
    # specific wrong answer instead of against nothing.
    assert inverse_state(from_cover_frame(56, inverted=False, curve=curve)) == 46


@pytest.mark.unit
def test_from_cover_frame_ignores_an_empty_curve() -> None:
    """A curve object expressing no ranges is the same as no curve at all."""
    assert from_cover_frame(42, inverted=False, curve=InterpolationCurve()) == 42


@pytest.mark.unit
def test_curve_copies_its_control_points_and_stays_hashable() -> None:
    """``frozen=True`` freezes the binding, not the list behind it.

    The builder hands this ``options.get(CONF_INTERP_LIST)`` verbatim, so the
    config entry still owns the object. Without the copy the samples could
    change under a curve that advertises itself as immutable, and the
    synthesised ``__hash__`` raised ``TypeError: unhashable type: 'list'`` the
    moment anything put one in a set or a memo dict.
    """
    normal = [0, 50, 100]
    new = [10, 40, 90]
    curve = InterpolationCurve(normal_list=normal, new_list=new)

    assert isinstance(curve.normal_list, tuple)
    assert isinstance(curve.new_list, tuple)
    # Hashable, which the frozen dataclass promised and could not deliver.
    assert hash(curve) == hash(InterpolationCurve(normal_list=normal, new_list=new))
    assert len({curve, InterpolationCurve(normal_list=normal, new_list=new)}) == 1

    # The caller's list moving on does not move the curve.
    normal.append(200)
    new[0] = 99
    assert curve.normal_list == (0, 50, 100)
    assert curve.new_list == (10, 40, 90)
    assert from_cover_frame(65, inverted=False, curve=curve) == 75

    # A tuple-built curve is the same curve, so equality survives the coercion
    # and every existing ``== InterpolationCurve(normal_list=[...])`` still holds.
    assert InterpolationCurve(normal_list=(0, 50, 100)) == InterpolationCurve(
        normal_list=[0, 50, 100]
    )
