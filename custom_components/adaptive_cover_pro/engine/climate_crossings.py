"""Pure temperature-season crossing helpers (issue #917).

The single computation site for the four climate crossings, shared by
``state.climate_provider.ClimateProvider`` (which turns them into Schmitt-latch
inputs) and ``pipeline.handlers.climate.ClimateCoverData``'s raw fallback. Zero
Home Assistant imports — value in, tuple out.

Each ``*_crossing`` returns ``(activate_met, release_cleared)``:

* ``activate_met`` — the existing single-crossing comparison (the raw property
  value when smoothing is off).
* ``release_cleared`` — True once the value has passed the release edge so a
  downstream Schmitt latch may drop. A blank ``release_threshold`` collapses the
  band to zero width → ``release_cleared = not activate_met``, reproducing
  today's instantaneous behaviour.

Unavailability values are load-bearing — they reproduce each legacy
``ClimateCoverData`` property exactly:

* winter / summer-warm / extreme-heat → ``(False, True)`` (inactive + cleared →
  no latch created or held);
* outside-high **fails open** to ``(True, False)`` (active + held → the latch
  stays engaged), matching ``ClimateCoverData.outside_high`` which returns True
  when the threshold is unset or the outside reading is missing/non-numeric.
"""

from __future__ import annotations


def resolve_current_temperature(
    outside: float | str | None,
    inside: float | str | None,
    *,
    temp_switch: bool,
) -> float | None:
    """Resolve the season-driving temperature (exact port of the property).

    Under ``temp_switch`` the outside reading wins when present; a present but
    non-numeric outside reading returns None (it does NOT fall through to
    inside), while a missing outside reading falls back to the inside sensor.
    """
    if temp_switch and outside is not None:
        try:
            return float(outside)
        except (ValueError, TypeError):
            return None
    if inside is not None:
        try:
            return float(inside)
        except (ValueError, TypeError):
            return None
    return None


def _pair(
    activate_met: bool,
    value: float,
    release_threshold: float | None,
    release_cleared_when,
) -> tuple[bool, bool]:
    """Assemble ``(activate_met, release_cleared)`` for an available reading.

    ``release_cleared_when(value, release_threshold)`` decides the release edge;
    a blank ``release_threshold`` collapses to ``not activate_met``.
    """
    if release_threshold is None:
        return activate_met, not activate_met
    return activate_met, release_cleared_when(value, release_threshold)


def winter_crossing(
    current_temp: float | None,
    temp_low: float | None,
    release_threshold: float | None,
) -> tuple[bool, bool]:
    """Winter edge: active when ``current_temp < temp_low``.

    Missing current temp or threshold → ``(False, True)`` (legacy is_winter
    returns False). Release edge is set ABOVE ``temp_low``; the latch drops once
    current rises to/above it.
    """
    if temp_low is None or current_temp is None:
        return False, True
    activate = current_temp < temp_low
    return _pair(activate, current_temp, release_threshold, lambda v, r: v >= r)


def summer_warm_crossing(
    current_temp: float | None,
    temp_high: float | None,
    release_threshold: float | None,
) -> tuple[bool, bool]:
    """Summer-warm edge: active when ``current_temp > temp_high``.

    Missing current temp or threshold → ``(False, True)``. Release edge is set
    BELOW ``temp_high``; the latch drops once current falls to/below it. This is
    the warm half of ``is_summer`` — the composite ANDs it with outside-high.
    """
    if temp_high is None or current_temp is None:
        return False, True
    activate = current_temp > temp_high
    return _pair(activate, current_temp, release_threshold, lambda v, r: v <= r)


def outside_high_crossing(
    outside_temperature: float | str | None,
    threshold: float | None,
    release_threshold: float | None,
) -> tuple[bool, bool]:
    """Outside-high edge: active when ``outside_temperature > threshold``.

    FAILS OPEN to ``(True, False)`` when the threshold is unset or the outside
    reading is missing / non-numeric — matching the legacy ``outside_high``
    property, which returns True in those cases. The held ``release_cleared =
    False`` keeps a Schmitt latch engaged. Release edge is set BELOW
    ``threshold``; the latch drops once outside falls to/below it.
    """
    if threshold is None or outside_temperature is None:
        return True, False
    try:
        value = float(outside_temperature)
    except (ValueError, TypeError):
        return True, False
    activate = value > threshold
    return _pair(activate, value, release_threshold, lambda v, r: v <= r)


def extreme_heat_crossing(
    outside_temperature: float | str | None,
    threshold: float | None,
    release_threshold: float | None,
) -> tuple[bool, bool]:
    """Extreme-heat edge: active when ``outside_temperature > threshold``.

    Keys on the OUTSIDE reading (never the inside sensor). Feature off
    (threshold None), missing, or non-numeric outside → ``(False, True)``,
    matching the legacy ``is_extreme_heat`` property. Release edge set BELOW the
    threshold; the latch drops once outside falls to/below it.
    """
    if threshold is None or outside_temperature is None:
        return False, True
    try:
        value = float(outside_temperature)
        fthreshold = float(threshold)
    except (ValueError, TypeError):
        return False, True
    activate = value > fthreshold
    return _pair(activate, value, release_threshold, lambda v, r: v <= r)
