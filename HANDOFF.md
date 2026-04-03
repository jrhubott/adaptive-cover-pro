# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-03
**Current Version:** v2.13.1
**Branch:** `main` (clean)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

Released **v2.13.1** — Fixed Climate Status sensor staying unknown when climate mode enabled (Issue #103). Climate data now flows through pipeline to diagnostics. Also fixed latent bug: pipeline registry now copies `tilt` field (preparation for Issue #33). See `release_notes/v2.13.1.md` for full details.

No work in progress. Main is clean.

## Tests

**1017 passing, 0 failing.**
Run: `source venv/bin/activate && python -m pytest tests/ -v`

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |

## Known Gotchas

- **`PipelineResult.tilt` not copied in registry (fixed v2.13.1):** `PipelineRegistry.evaluate()` now copies `tilt` alongside `climate_data`. Before v2.13.1, handler-set `tilt` values were silently dropped (only mattered for Issue #33 dual-axis).

## Recent Releases

| Version | Date | Summary |
|---------|------|----------|
| [v2.13.1](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.1) | 2026-04-03 | Climate Status sensor fix (#103), pipeline tilt field propagation fix. |
| [v2.13.0](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.0) | 2026-03-28 | Safety overrides bypass Automatic Control, config flow UX (#100). |

## Pending Upstream PRs

| PR | Repo | Status |
|----|------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | 🟠 OPEN — in review queue. Do not comment or create new PRs. |
