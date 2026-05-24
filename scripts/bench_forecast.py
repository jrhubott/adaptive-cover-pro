#!/usr/bin/env python3
"""Microbenchmark for the position-forecast hot path.

Originally written to validate the fix for issue #437 (forecast computing
on every state read, blocking the event loop). Kept as a regression guard
and a baseline for further forecast-perf work (see the perf-followup
issues filed off that PR).

Usage:

    ./scripts/bench_forecast.py            # executable form
    venv/bin/python scripts/bench_forecast.py

The script imports `custom_components.adaptive_cover_pro` directly, so
running it against a different branch just requires `git checkout`. It
prints one section per measurement plus a comparison column showing the
delta versus the PR #440 baseline (pre-fix `main` and post-fix `after`).
Numbers from #440 were collected on macOS dev hardware; HA Green ARM
runs 10-100x slower in absolute terms but the speedup ratios hold.
"""

from __future__ import annotations

import gc
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Re-exec under the project venv so `./scripts/bench_forecast.py` works
# without a manual `source venv/bin/activate` — system python3 lacks
# astral / pandas. No-op when already inside any venv.
_VENV_PYTHON = Path(__file__).resolve().parent.parent / "venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and _VENV_PYTHON.exists():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), __file__, *sys.argv[1:]])

# Baseline numbers reported in PR #440 (macOS dev hardware). "before" is
# pre-fix main; "after" is the PR #440 branch tip. Used to print a delta
# alongside the current measurement so we can spot regressions without
# checking out the old branches.
BASELINE_PR440: dict[str, dict[str, float]] = {
    "sd.times (2nd read)": {"before": 0.192, "after": 0.013},
    "sd.solar_azimuth (2nd)": {"before": 9.48, "after": 0.013},
    "sd.solar_elevation (2nd)": {"before": 9.36, "after": 0.014},
    "build_forecast mean / call": {"before": 46.6, "after": 25.2},
    "boot fan-out 1 entry (28 calls)": {"before": 1187.0, "after": 690.0},
    "boot fan-out 10 entries (280 calls)": {"before": 11900.0, "after": 7000.0},
}


def _fmt_delta(current_ms: float, key: str) -> str:
    """Return a `  vs #440: before X ms (Yx), after Z ms (Wx)` suffix."""
    b = BASELINE_PR440.get(key)
    if b is None:
        return ""
    before_ratio = b["before"] / current_ms if current_ms else float("inf")
    after_ratio = b["after"] / current_ms if current_ms else float("inf")
    return (
        f"   [vs #440  before {b['before']:8.3f} ms ({before_ratio:5.2f}x)"
        f"   after {b['after']:8.3f} ms ({after_ratio:5.2f}x)]"
    )


# Resolve the repo root from this script's location so `git checkout` to
# another branch keeps imports working without editing the script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from astral import LocationInfo  # noqa: E402
from astral.location import Location  # noqa: E402

from custom_components.adaptive_cover_pro.forecast import build_forecast  # noqa: E402
from custom_components.adaptive_cover_pro.sun import SunData  # noqa: E402


class StubCover:
    """Mimic the two attributes `build_forecast` actually reads."""

    def __init__(self, azi: float, ele: float) -> None:
        """Capture sun angles; only `direct_sun_valid` ends up consumed."""
        self.direct_sun_valid = ele > 0

    def calculate_percentage(self) -> int:
        """Return a constant — the benchmark measures iteration cost, not math."""
        return 50


def cover_factory(azi: float, ele: float) -> StubCover:
    return StubCover(azi, ele)


def make_sun_data() -> SunData:
    info = LocationInfo("Paris", "France", "Europe/Paris", 48.8566, 2.3522)
    return SunData(timezone=info.timezone, location=Location(info), elevation=10.0)


def time_ms(fn, *args, **kwargs) -> tuple[float, object]:
    gc.collect()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    t1 = time.perf_counter()
    return (t1 - t0) * 1000.0, result


_TZ = ZoneInfo("Europe/Paris")
_NOW = datetime.now(tz=_TZ).replace(hour=10, minute=0, second=0, microsecond=0)


def measure_cold_sundata() -> None:
    print(
        "=== Measurement 1: Cold SunData property access (fresh instance each time) ==="
    )
    sd = make_sun_data()
    ms, _ = time_ms(lambda: sd.times)
    print(f"  sd.times (first read):          {ms:8.2f} ms")

    sd = make_sun_data()
    ms, _ = time_ms(lambda: sd.solar_azimuth)
    print(f"  sd.solar_azimuth (first read):  {ms:8.2f} ms")

    sd = make_sun_data()
    ms, _ = time_ms(lambda: sd.solar_elevation)
    print(f"  sd.solar_elevation (first):     {ms:8.2f} ms")


def measure_hot_sundata() -> None:
    print("\n=== Measurement 2: Hot SunData property re-access (same instance) ===")
    sd = make_sun_data()
    _ = sd.times  # prime
    _ = sd.solar_azimuth
    _ = sd.solar_elevation

    ms, _ = time_ms(lambda: sd.times)
    print(
        f"  sd.times (2nd read):            {ms:8.4f} ms{_fmt_delta(ms, 'sd.times (2nd read)')}"
    )
    ms, _ = time_ms(lambda: sd.solar_azimuth)
    print(
        f"  sd.solar_azimuth (2nd):         {ms:8.4f} ms{_fmt_delta(ms, 'sd.solar_azimuth (2nd)')}"
    )
    ms, _ = time_ms(lambda: sd.solar_elevation)
    print(
        f"  sd.solar_elevation (2nd):       {ms:8.4f} ms{_fmt_delta(ms, 'sd.solar_elevation (2nd)')}"
    )


def measure_build_forecast_repeated() -> None:
    print("\n=== Measurement 3: build_forecast — 5 calls on same SunData instance ===")
    sd = make_sun_data()
    times_ms: list[float] = []
    sample_count = 0
    for i in range(5):
        ms, fc = time_ms(
            build_forecast,
            sun_data=sd,
            cover_factory=cover_factory,
            default_position=50,
            now=_NOW,
        )
        times_ms.append(ms)
        sample_count = len(fc.samples)
        print(
            f"  Call {i + 1}:                       {ms:8.2f} ms  ({sample_count} samples)"
        )
    avg = sum(times_ms) / len(times_ms)
    print(
        f"  Mean:                           {avg:8.2f} ms / call{_fmt_delta(avg, 'build_forecast mean / call')}"
    )


def measure_boot_fanout_single_entry() -> None:
    print("\n=== Measurement 4: Boot fan-out — 28 build_forecast calls (1 entry) ===")
    print(
        "    Pre-fix observed cost: ~14 switches × 2 sensor reads = 28 calls per entry"
    )
    sd = make_sun_data()
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(28):
        build_forecast(
            sun_data=sd,
            cover_factory=cover_factory,
            default_position=50,
            now=_NOW,
        )
    t1 = time.perf_counter()
    total_ms = (t1 - t0) * 1000.0
    print(
        f"  28 calls total:                 {total_ms:8.2f} ms{_fmt_delta(total_ms, 'boot fan-out 1 entry (28 calls)')}"
    )
    print(f"  Mean per call:                  {total_ms / 28:8.2f} ms")


def measure_boot_fanout_ten_entries() -> None:
    print("\n=== Measurement 5: Full boot — 10 entries × 28 calls = 280 calls ===")
    print(
        "    User reported: 10 ACP instances pushed past HA bootstrap stage 2 timeout"
    )
    sds = [make_sun_data() for _ in range(10)]
    gc.collect()
    t0 = time.perf_counter()
    for sd in sds:
        for _ in range(28):
            build_forecast(
                sun_data=sd,
                cover_factory=cover_factory,
                default_position=50,
                now=_NOW,
            )
    t1 = time.perf_counter()
    total_ms = (t1 - t0) * 1000.0
    print(
        f"  280 calls total:                {total_ms:8.2f} ms  ({total_ms / 1000:.2f} s)"
        f"{_fmt_delta(total_ms, 'boot fan-out 10 entries (280 calls)')}"
    )
    print(f"  Mean per call:                  {total_ms / 280:8.2f} ms")


def _measure_for_summary() -> dict[str, float]:
    """Run the comparable measurements again and capture numbers for the summary table.

    Cheap enough (sub-second) that re-running is simpler than wiring state
    through the existing print-driven measurement functions.
    """
    results: dict[str, float] = {}

    sd = make_sun_data()
    _ = sd.times
    _ = sd.solar_azimuth
    _ = sd.solar_elevation
    results["sd.times (2nd read)"], _ = time_ms(lambda: sd.times)
    results["sd.solar_azimuth (2nd)"], _ = time_ms(lambda: sd.solar_azimuth)
    results["sd.solar_elevation (2nd)"], _ = time_ms(lambda: sd.solar_elevation)

    sd = make_sun_data()
    samples = [
        time_ms(
            build_forecast,
            sun_data=sd,
            cover_factory=cover_factory,
            default_position=50,
            now=_NOW,
        )[0]
        for _ in range(5)
    ]
    results["build_forecast mean / call"] = sum(samples) / len(samples)

    sd = make_sun_data()
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(28):
        build_forecast(
            sun_data=sd, cover_factory=cover_factory, default_position=50, now=_NOW
        )
    results["boot fan-out 1 entry (28 calls)"] = (time.perf_counter() - t0) * 1000.0

    sds = [make_sun_data() for _ in range(10)]
    gc.collect()
    t0 = time.perf_counter()
    for sd in sds:
        for _ in range(28):
            build_forecast(
                sun_data=sd, cover_factory=cover_factory, default_position=50, now=_NOW
            )
    results["boot fan-out 10 entries (280 calls)"] = (time.perf_counter() - t0) * 1000.0

    return results


def print_summary_table(current: dict[str, float]) -> None:
    print("\n=== Diff vs PR #440 baseline ===")
    print(
        f"{'Measurement':<38} {'before #440':>12} {'after #440':>12} {'current':>12} {'vs before':>10} {'vs after':>10}"
    )
    print("-" * 100)
    for key, baseline in BASELINE_PR440.items():
        cur = current.get(key, float("nan"))
        before = baseline["before"]
        after = baseline["after"]
        vs_before = (before / cur) if cur else float("inf")
        vs_after = (after / cur) if cur else float("inf")
        print(
            f"{key:<38} {before:>10.3f} ms {after:>10.3f} ms {cur:>10.3f} ms"
            f" {vs_before:>9.2f}x {vs_after:>9.2f}x"
        )
    print(
        "\nNote: speedups > 1.0 mean current is faster; < 1.0 means slower."
        "\nBaseline numbers from macOS dev hw — absolute ms vary by host but ratios are stable."
    )


def main() -> None:
    try:
        from custom_components.adaptive_cover_pro.const import (
            FORECAST_RECOMPUTE_INTERVAL_MIN,
        )

        banner = f"AFTER #437 (FORECAST_RECOMPUTE_INTERVAL_MIN = {FORECAST_RECOMPUTE_INTERVAL_MIN})"
    except ImportError:
        banner = "BEFORE #437 (no FORECAST_RECOMPUTE_INTERVAL_MIN const)"
    print(f"### Branch state: {banner}\n")

    measure_cold_sundata()
    measure_hot_sundata()
    measure_build_forecast_repeated()
    measure_boot_fanout_single_entry()
    measure_boot_fanout_ten_entries()

    summary = _measure_for_summary()
    print_summary_table(summary)


if __name__ == "__main__":
    main()
