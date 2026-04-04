# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-04
**Current Version:** v2.13.1
**Branch:** `feature/true-sunset-sunrise-default-position` (in progress)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

**Work in progress:**
- `feature/true-sunset-sunrise-default-position` — True astronomical sunset/sunrise default position refactor. Not yet merged to main.
- `feature/pipeline-consolidation-refactor` — Pipeline logic consolidation & deduplication (branched from above). Not yet merged.

## Tests

**1083 passing, 0 failing** (+22 new tests for pipeline helpers).
Run: `source venv/bin/activate && python -m pytest tests/ -v`

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |

## Known Gotchas

- **`PipelineResult.tilt` not copied in registry (fixed v2.13.1):** `PipelineRegistry.evaluate()` now copies `tilt` alongside `climate_data`. Before v2.13.1, handler-set `tilt` values were silently dropped (only mattered for Issue #33 dual-axis).
- **`cover.default` property removed:** `AdaptiveGeneralCover` and `SunGeometry` no longer expose `.default`. Any code accessing it will get `AttributeError` immediately. Use `snapshot.default_position` in pipeline handlers; use `compute_effective_default()` elsewhere.
- **Pipeline always runs:** Even outside the start_time/end_time window the pipeline executes. The time-window gate is in `CoverCommandService.apply_position()` (the `in_time_window` check), not in the pipeline.
- **`NormalCoverState` is test-only:** `NormalCoverState` in `calculation.py` is no longer used by production code (coordinator stores `_cover_data` directly). It remains for backward compat with existing tests. Do not re-introduce it into coordinator logic.
- **`ClimateCoverState` takes a `PipelineSnapshot`:** Constructor changed from `(cover, climate_data, default_position=X)` to `(snapshot, climate_data)`. Use `make_snapshot_for_cover(cover, h_def)` from `tests/conftest.py` in tests.
- **Pipeline helpers are the canonical position helpers:** All handlers and `ClimateCoverState` use `pipeline/helpers.py` — `compute_solar_position()`, `compute_default_position()`, `apply_snapshot_limits()`, `compute_raw_calculated_position()`. Do not inline these patterns in new handlers.
- **`DiagnosticContext` uses `cover` + `pipeline_result`:** Old fields (`normal_cover_state`, `raw_calculated_position`, `climate_state`, `climate_data`, `climate_strategy`, `control_method`, `is_force_override_active`, etc.) are gone. Build a `PipelineResult` and pass it directly.

## Recent Releases

| Version | Date | Summary |
|---------|------|----------|
| [v2.13.1](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.1) | 2026-04-03 | Climate Status sensor fix (#103), pipeline tilt field propagation fix. |
| [v2.13.0](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.0) | 2026-03-28 | Safety overrides bypass Automatic Control, config flow UX (#100). |

## Pending Upstream PRs

| PR | Repo | Status |
|----|------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | 🟠 OPEN — in review queue. Do not comment or create new PRs. |
