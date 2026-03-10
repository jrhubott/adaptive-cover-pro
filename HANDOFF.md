# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-03-10
**Current Version:** v2.7.7
**Branch:** `main` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Current State

### Tests

313 passing, 0 failing.
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
| **Total** | **36%** |

### Recent Releases

| Version | Highlights |
|---------|-----------|
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

No open pull requests.
