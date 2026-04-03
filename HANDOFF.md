# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-03
**Current Version:** v2.12.0
**Branch:** `main` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Current State

### Architecture (Post-Plugin Refactor)

The integration was fully rewritten with a layered architecture. The pipeline now uses a plugin architecture where each handler is self-contained:

| Layer | Package | Purpose |
|-------|---------|---------|
| HA Boundary | `state/` | `ClimateProvider`, `SunProvider`, `CoverProvider`, `SunSnapshot`, `CoverStateSnapshot` — all HA reads happen here |
| Calculation | `calculation.py`, `sun.py` | Pure math, 0 HA imports |
| Engine | `engine/` | `SunGeometry`, `VenetianCoverCalculation` — next-gen calculation engine |
| Config Types | `config_types.py` | `CoverConfig` typed dataclass |
| Pipeline | `pipeline/` | 9 pluggable override handlers (self-contained plugins) |
| Managers | `managers/` | 5 focused classes extracted from coordinator |
| Diagnostics | `diagnostics/` | `DiagnosticsBuilder` with decision trace |
| Coordinator | `coordinator.py` | Thin orchestrator (~1,477 lines) |

**Adding a new override:** Create one handler file + register in `pipeline/handlers/__init__`. Each handler is self-contained: owns its condition evaluation AND position computation. No coordinator logic changes.

**Handler architecture:** Each handler in `pipeline/handlers/` is independent:
- Condition evaluation: Does this handler apply to the current snapshot?
- Position computation: If matched, what position should be used?
- `ClimateHandler` contains `ClimateCoverData` + `ClimateCoverState` (moved from `calculation.py`)
- `GlareZoneHandler` (priority 45) — glare zone protection
- `WeatherHandler` (priority 90) — wind/rain/severe weather handling

**Handlers (priority order) — v2.12.0:**
```
force_override(100) > weather(90) > motion_timeout(80) > manual_override(70) > 
cloud_suppression(60) > climate(50) > glare_zone(45) > solar(40) > default(0)
```

### Tests

981 passing, 0 failing.
Run: `venv/bin/python -m pytest tests/ -v`

| Module | Coverage |
|--------|----------|
| `calculation.py` | 87% |
| `helpers.py` | 100% |
| `const.py` | 100% |
| `geometry.py` | 100% |
| `position_utils.py` | 100% |
| `enums.py` | 97% |
| `pipeline/handlers/climate.py` | ~79% |
| `pipeline/` (other) | 100% |
| `managers/` | ~96% |
| `state/` | ~95% |
| `diagnostics/` | ~90% |
| `engine/` | ~90% |
| `coordinator.py` | ~34% (HA integration code, hard to unit test) |
| `config_flow.py` | ~0% (UI flow, hard to unit test) |
| `sensor.py` / `switch.py` | 0% (platform code) |
| **Total** | **61%** |

### Recent Releases

| Version | Highlights |
|---------|-----------|
| v2.12.0 | Glare Zones (Issue #64): 4 named floor zones per blind with per-zone switches & binary sensors. Winter Insulation Mode (Issue #29): close non-sun covers in winter for heat retention. Climate accuracy fix (Issue #71): irradiance/lux/weather now suppress glare in summer. Override priority visualization in config summary. Split override config screens. 981 tests. |
| v2.11.0 | Cloud coverage sensor support (Issue #94): percentage-based sensor as fourth OR signal for cloud suppression; CloudSuppressionHandler now registered in pipeline at priority 60. |
| v2.10.0 | Sync category selection, duplicate cover flow, remove legacy import dead code. |
| v2.9.4 | Hotfix: `AttributeError` on `_position_tolerance` crashing position mismatch binary sensors on startup and during updates. |
| v2.9.3 | Translation fixes and config flow polish: missing menu labels, climate mode help text, cloud suppression clarification, reordered climate settings. |
| v2.9.2 | Suppress glare control in low light (Issue #65): new toggle in Climate Settings uses weather/lux/irradiance to skip glare control when no real sun. Config flow menu fixes. 800 tests. |
| v2.9.1 | Config flow menu ordering fixes; climate menu renamed to Climate Configuration. |
| v2.9.0 | Config flow redesign: 10 focused steps replacing per-type mega-forms, options menu mirrors initial flow, climate always accessible, manual override and motion as dedicated steps, legacy AdaptiveCover import removed. 778 tests. |
| v2.8.0 | Major architecture rewrite: pure calc engine, state providers, override pipeline, manager classes, diagnostics builder, typed config. Decision Trace sensor, enriched Cover Position attrs, Venetian engine. 751 tests, 61% coverage. Consolidates 12 betas. |
| v2.7.16 | Fix position sensor showing stale sun-calculated value after end_time (Issue #66); fix max_position/min_position sliders defaulting to 1%/no value |
| v2.7.15 | Accept occupancy sensors in motion control selector (Issue #52); fix sunset_position not accepting blank value (Issue #55) |
| v2.7.14 | Add `sill_height` parameter for windows above floor level (Issue #47) — raises blind when sill provides natural sun blocking |

---

## Open Issues (Backlog)

Only **1 issue remains open:**

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Architecture supports dual-axis via pipeline; needs config flow enhancement to expose dual-axis setup for single entity covers |

**Recently Closed:**
| # | Title | Resolution |
|---|-------|------------|
| [#100](https://github.com/jrhubott/adaptive-cover/issues/100) | Self-explanatory icons for switch entities | ✅ Fixed in PR #101 — all entities now have semantic icons via `icons.json` |

## Pending Upstream PRs

| PR | Repo | Description | Status |
|----|------|-------------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | Adds new integration [jrhubott/adaptive-cover-pro] | 🟠 OPEN — in review queue |
| [home-assistant/brands #9957](https://github.com/home-assistant/brands/pull/9957) | `home-assistant/brands` | Add brand icons | ✅ Resolved — not needed (local brand/ folder) |

No open pull requests on this repo.

---

## Key Patterns & Architecture Notes

### Pipeline Plugin Architecture

**What Changed:** Handlers are now fully self-contained plugins that own both condition evaluation and position computation. This eliminates coordinator logic for adding new overrides.

**How It Works:**
1. Each handler in `pipeline/handlers/` implements `OverrideHandler.evaluate(snapshot)` 
2. Handler decides whether to activate: returns `PipelineResult` (with position) or `None` (skip me)
3. No coordinator changes needed — just create handler file + register in `__init__`

**Handler Independence:**
- `ClimateHandler` owns all climate logic + `ClimateCoverData` + `ClimateCoverState` (previously in `calculation.py`)
- `GlareZoneHandler` owns glare zone distance calculation (extracted from `AdaptiveVerticalCover.calculate_position()`)
- Vertical cover's `calculate_position()` no longer has glare loop; pass `effective_distance_override` to use pre-computed glare distances
- Each handler is a complete business unit: condition + computation + result building

**Adding a Handler (Template):**
1. Create `pipeline/handlers/my_handler.py`
2. Subclass `OverrideHandler` with `priority` (0-100) and `name` ("my_override")
3. Implement `evaluate(snapshot)` → return `PipelineResult` or `None`
4. Register in `pipeline/handlers/__init__.py` imports and in coordinator

**Testing:** 854 tests cover all 9 handlers with full condition and computation coverage (100% pipeline module).
