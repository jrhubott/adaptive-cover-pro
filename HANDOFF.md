# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-03-31
**Current Version:** v2.7.17-beta.9
**Branch:** `dev` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Current State

### Architecture (Post-Rewrite)

The integration was fully rewritten with a layered architecture:

| Layer | Package | Purpose |
|-------|---------|---------|
| HA Boundary | `state/` | `ClimateProvider`, `SunProvider` — all HA reads happen here |
| Calculation | `calculation.py`, `sun.py` | Pure math, 0 HA imports |
| Pipeline | `pipeline/` | 6 pluggable override handlers with priority ordering |
| Managers | `managers/` | 5 focused classes extracted from coordinator |
| Diagnostics | `diagnostics/` | `DiagnosticsBuilder` with decision trace |
| Coordinator | `coordinator.py` | Thin orchestrator (~1,839 lines, was 2,700) |

**Adding a new override:** Create handler in `pipeline/handlers/`, register in coordinator `__init__`. No coordinator logic changes.

### Tests

657 passing, 0 failing.
Run: `source venv/bin/activate && python -m pytest tests/ -v`

| Module | Coverage |
|--------|----------|
| `calculation.py` | 87% |
| `helpers.py` | 100% |
| `const.py` | 100% |
| `geometry.py` | 100% |
| `position_utils.py` | 100% |
| `enums.py` | 97% |
| `pipeline/` | 100% |
| `managers/` | ~96% |
| `state/` | ~95% |
| `diagnostics/` | ~90% |
| `coordinator.py` | ~34% (HA integration code, hard to unit test) |
| `config_flow.py` | 0% (UI flow) |
| `sensor.py` / `switch.py` | 0% (platform code) |
| **Total** | **58%** |

### Recent Releases

| Version | Highlights |
|---------|-----------|
| v2.7.16 | Fix position sensor showing stale sun-calculated value after end_time (Issue #66); fix max_position/min_position sliders defaulting to 1%/no value |
| v2.7.15 | Accept occupancy sensors in motion control selector (Issue #52); fix sunset_position not accepting blank value (Issue #55) |
| v2.7.14 | Add `sill_height` parameter for windows above floor level (Issue #47) — raises blind when sill provides natural sun blocking |
| v2.7.13 | Fix reset button race condition — suppress override re-detection during post-reset cover settling; add 30s timeout to wait loop |

---

## Open Issues (Backlog)

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt — architecture now supports dual-axis via pipeline |
| [#31](https://github.com/jrhubott/adaptive-cover/issues/31) | Suppress cover adjustment based on cloud coverage sensor | Create `pipeline/handlers/cloud_suppression.py` |
| [#29](https://github.com/jrhubott/adaptive-cover/issues/29) | Keep heat in — close non-sun-exposed covers in winter | Extend climate handler or add new pipeline handler |
| [#28](https://github.com/jrhubott/adaptive-cover/issues/28) | Wind speed/direction handling | Create `pipeline/handlers/wind.py` with priority 90 |
| [#27](https://github.com/jrhubott/adaptive-cover/issues/27) | Min/Max/Fixed Sunrise/Sunset overrides | Let users pin start/end sun times |

## Pending Upstream PRs

| PR | Repo | Description | Status |
|----|------|-------------|--------|
| [hacs/default #6128](https://github.com/hacs/default/pull/6128) | `hacs/default` | Add to HACS default integrations list | ❌ Closed — needs investigation |
| [home-assistant/brands #9957](https://github.com/home-assistant/brands/pull/9957) | `home-assistant/brands` | Add brand icons | ✅ Resolved — not needed (local brand/ folder) |

No open pull requests on this repo.
