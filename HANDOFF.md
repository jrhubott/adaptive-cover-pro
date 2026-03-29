# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-03-22
**Current Version:** v2.7.16
**Branch:** `main` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Current State

### Tests

358 passing, 0 failing.
Run: `source venv/bin/activate && python -m pytest tests/ -v`

| Module | Coverage |
|--------|----------|
| `calculation.py` | 87% |
| `helpers.py` | 100% |
| `const.py` | 100% |
| `geometry.py` | 100% |
| `position_utils.py` | 100% |
| `enums.py` | 97% |
| `coordinator.py` | 34% (HA integration code, hard to unit test) |
| `config_flow.py` | 0% (UI flow) |
| `sensor.py` / `switch.py` | 0% (platform code) |
| **Total** | **38%** |

### Recent Releases

| Version | Highlights |
|---------|-----------|
| v2.7.16 | Fix max_position/min_position sliders defaulting to 1%/no value — now default to 100%/0% |
| v2.7.15 | Accept occupancy sensors in motion control selector (Issue #52); fix sunset_position not accepting blank value (Issue #55) |
| v2.7.14 | Add `sill_height` parameter for windows above floor level (Issue #47) — raises blind when sill provides natural sun blocking |
| v2.7.13 | Fix reset button race condition — suppress override re-detection during post-reset cover settling; add 30s timeout to wait loop |
| v2.7.12 | Fix horizontal covers closing before end sun time — clamp position to ≥1% when sun is in FOV |
| v2.7.11 | Optional device association — link entities to a physical device instead of standalone virtual device; orphaned device cleanup fixes |
| v2.7.10 | Local brand icons (`brand/` folder in integration); HACS default + home-assistant/brands PRs submitted |
| v2.7.9 | Diagnostic sensor cleanup — removed redundant attributes from `sun_elevation` sensor |
| v2.7.8 | Fix Control Method sensor showing `ControlMethod.DEFAULT` — Python 3.11+ str(Enum) behavior change; use `.value` explicitly |
| v2.7.7 | Motion Timeout End Time sensor (P0), Force Override Triggers sensor (P1), Last Motion Time sensor (P1), Manual Override End Time sensor (P0); default manual override duration increased to 2h |
| v2.7.6 | ControlMethod enum with 7 values (solar, summer, winter, default, manual_override, motion_timeout, force_override); renames `intermediate` → `solar`; fixes stale control method across cycles |
| v2.7.5 | Motion control (occupancy-based auto positioning), control state reason sensor, force override binary sensors, solar times accuracy fix |
| v2.7.0 | Enhanced geometric accuracy (safety margins, window depth), position limits |

---

## Open Issues (Backlog)

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt — current arch requires two separate HA entities |
| [#31](https://github.com/jrhubott/adaptive-cover/issues/31) | Suppress cover adjustment based on cloud coverage sensor | Add lux/cloud sensor as a suppression condition |
| [#29](https://github.com/jrhubott/adaptive-cover/issues/29) | Keep heat in — close non-sun-exposed covers in winter | Extend climate mode to close shaded-side covers |
| [#28](https://github.com/jrhubott/adaptive-cover/issues/28) | Wind speed/direction handling | Safety retraction when wind exceeds a threshold sensor |
| [#27](https://github.com/jrhubott/adaptive-cover/issues/27) | Min/Max/Fixed Sunrise/Sunset overrides | Let users pin start/end sun times instead of pure solar calculation |

## Pending Upstream PRs

These are external PRs awaiting review/merge by third-party maintainers. Check back periodically and update this section when accepted.

| PR | Repo | Description | Status |
|----|------|-------------|--------|
| [hacs/default #6128](https://github.com/hacs/default/pull/6128) | `hacs/default` | Add `jrhubott/adaptive-cover-pro` to HACS default integrations list | ❌ Closed — needs investigation |
| [home-assistant/brands #9957](https://github.com/home-assistant/brands/pull/9957) | `home-assistant/brands` | Add brand icons for `adaptive_cover_pro` (CDN, older HA versions) | ✅ Resolved — not needed |

**home-assistant/brands:** Auto-closed by bot. As of HA 2026.3.0, custom integrations serve their own icons via the local `brand/` folder — which we already ship in v2.7.10. No further action needed.

**hacs/default:** Closed with no explanation. Needs investigation — likely requires a different submission format or checklist. Check HACS contributing docs before re-submitting.

No open pull requests on this repo.
