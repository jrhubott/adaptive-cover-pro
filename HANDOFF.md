# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-03
**Current Version:** v2.13.0
**Branch:** `main` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

## Last Session Summary

Released **v2.13.0** — safety overrides bypass Automatic Control, config flow polish (Position Calibration rename/move, menu label alignment, copy dialog pre-selection fix, sync category improvements), Window Width moved to Cover Geometry, self-explanatory entity icons (Issue #100). See `release_notes/v2.13.0.md` for full details.

No work in progress. Main is clean.

## Tests

**1011 passing, 0 failing.**
Run: `source venv/bin/activate && python -m pytest tests/ -v`

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |

## Pending Upstream PRs

| PR | Repo | Status |
|----|------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | 🟠 OPEN — in review queue. Do not comment or create new PRs. |
