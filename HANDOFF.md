# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-03
**Current Version:** v2.13.0
**Branch:** `fix/issue-103-climate-status-sensor` (in progress)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Last Session Summary

Released **v2.13.0** — safety overrides bypass Automatic Control, config flow polish (Position Calibration rename/move, menu label alignment, copy dialog pre-selection fix, sync category improvements), Window Width moved to Cover Geometry, self-explanatory entity icons (Issue #100). See `release_notes/v2.13.0.md` for full details.

**In progress:** Fix for Issue #103 (Climate Status sensor always unknown). Branch `fix/issue-103-climate-status-sensor` — production code complete, tests passing, awaiting PR.

## Tests

**1017 passing, 0 failing.**
Run: `source venv/bin/activate && python -m pytest tests/ -v`

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| [#103](https://github.com/jrhubott/adaptive-cover-pro/issues/103) | Climate status sensor not working | Fix on branch `fix/issue-103-climate-status-sensor` — pending PR/merge. |
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |

## Known Gotchas

- **`PipelineResult.tilt` not copied in registry (pre-v2.13.0):** Fixed in the Issue #103 branch — `PipelineRegistry.evaluate()` now copies `tilt` alongside `climate_data`. Before this fix, if any handler returned a `tilt` value it would be silently dropped. No handler currently sets `tilt`, but this will matter for Issue #33 (venetian dual-axis).

## Pending Upstream PRs

| PR | Repo | Status |
|----|------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | 🟠 OPEN — in review queue. Do not comment or create new PRs. |
