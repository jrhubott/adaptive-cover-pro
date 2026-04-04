# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-04
**Current Version:** v2.13.7
**Branch:** `main` (stable)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

**Recent Merges:**
- `fix/motion-timeout-pending-ignores-new-motion` — Motion timeout pending state fix (PR #119, merged to main).
- `fix/manual-override-position-not-restored` — Manual override reset fixes (PR #117, merged to main).

## Tests

**1106 passing, 0 failing** (+6 new tests for reconciliation manual override fix).
Run: `source venv/bin/activate && python -m pytest tests/ -v`

## Open PRs (Awaiting Merge to Main)

| PR | Branch | Issue | Beta | Status | Notes |
|----|--------|-------|------|--------|-------|
| [#121](https://github.com/jrhubott/adaptive-cover-pro/pull/121) | `fix/issue-116-reconciliation-ignores-manual-override` | [#116](https://github.com/jrhubott/adaptive-cover-pro/issues/116) | [v2.13.8-beta.1](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.8-beta.1) | 🟡 Awaiting user confirmation | Reconciliation ignored manual override; user asked to test beta |

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |
| [#116](https://github.com/jrhubott/adaptive-cover-pro/issues/116) | Manual override not working when force override sensor configured | Fix in progress on `fix/issue-116-reconciliation-ignores-manual-override`. |

## Known Gotchas

- **Reconciliation ignored manual override (fixed on branch, not yet released):** `_reconcile()` in `CoverCommandService` was resending the integration's stale `target_call` position once per minute, fighting the user's manual move. Root cause: reconciliation bypassed all gate checks including `manual_override`. Fix: coordinator syncs `manager.manual_controlled` → `_cmd_svc.manual_override_entities` each update cycle; `_reconcile()` skips entities in that set. Safety handlers (force override, weather) still take effect immediately via `apply_position(force=True)` which overwrites `target_call` before reconciliation runs.

- **`PipelineResult.tilt` not copied in registry (fixed v2.13.1):** `PipelineRegistry.evaluate()` now copies `tilt` alongside `climate_data`. Before v2.13.1, handler-set `tilt` values were silently dropped (only mattered for Issue #33 dual-axis).
- **`cover.default` property removed:** `AdaptiveGeneralCover` and `SunGeometry` no longer expose `.default`. Any code accessing it will get `AttributeError` immediately. Use `snapshot.default_position` in pipeline handlers; use `compute_effective_default()` elsewhere.
- **Time window gate moved into pipeline:** `SolarHandler` and `GlareZoneHandler` now self-gate on `snapshot.in_time_window` (returning `None` when outside the window). The `in_time_window` gate has been **removed** from `CoverCommandService.apply_position()` — `PositionContext` no longer has an `in_time_window` field. The pipeline still always runs; it is each handler's responsibility to check `snapshot.in_time_window` if needed. The `auto_control` gate remains in `CoverCommandService`.
- **Motion Control switch:** A new "Motion Control" switch entity is conditionally shown when `CONF_MOTION_SENSORS` are configured.
- **Manual override reset now sends correct position (fixed):** `reset_if_needed()` now runs BEFORE `_calculate_cover_state()` so the pipeline sees the cleared state. It returns the set of expired entity IDs; the coordinator calls `_async_send_after_override_clear()` with `force=True` for those covers. The reset button now resets the override first, calls `async_refresh()` so the pipeline runs without the override, then sends `coordinator.state` (the true post-override position — climate, solar, or default — whichever handler wins). The old pre-refresh send used `ManualOverrideHandler`'s simplified solar/default position which was wrong when climate mode was active. Its state is stored in `ToggleManager.motion_control` (default `True`) and passed to `PipelineSnapshot.motion_control_enabled`. `MotionTimeoutHandler` checks this field and passes through (returns `None`) when the switch is off, allowing lower-priority handlers to run as if motion timeout were inactive.
- **`NormalCoverState` is test-only:** `NormalCoverState` in `calculation.py` is no longer used by production code (coordinator stores `_cover_data` directly). It remains for backward compat with existing tests. Do not re-introduce it into coordinator logic.
- **`ClimateCoverState` takes a `PipelineSnapshot`:** Constructor changed from `(cover, climate_data, default_position=X)` to `(snapshot, climate_data)`. Use `make_snapshot_for_cover(cover, h_def)` from `tests/conftest.py` in tests.
- **Pipeline helpers are the canonical position helpers:** All handlers and `ClimateCoverState` use `pipeline/helpers.py` — `compute_solar_position()`, `compute_default_position()`, `apply_snapshot_limits()`, `compute_raw_calculated_position()`. Do not inline these patterns in new handlers.
- **`DiagnosticContext` uses `cover` + `pipeline_result`:** Old fields (`normal_cover_state`, `raw_calculated_position`, `climate_state`, `climate_data`, `climate_strategy`, `control_method`, `is_force_override_active`, etc.) are gone. Build a `PipelineResult` and pass it directly.

## Recent Releases

| Version | Date | Summary |
|---------|------|----------|
| [v2.13.7](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.7) | 2026-04-04 | Fix: motion timeout pending state now properly detects new motion events (#119). |
| [v2.13.6](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.6) | 2026-04-04 | Reset button time_delta_too_small gate, manual override reset position (climate-aware). |
| [v2.13.6-beta.4](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.6-beta.4) | 2026-04-04 | Fix: reset button time_delta_too_small gate now bypassed with force=True. |
| [v2.13.6-beta.3](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.6-beta.3) | 2026-04-04 | Fix: manual override reset now sends correct pipeline position (climate-aware). |
| [v2.13.6-beta.2](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.6-beta.2) | 2026-04-04 | Pipeline time window gate moved to handlers; Motion Control switch. |
| [v2.13.5](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.5) | 2026-04-04 | Pipeline consolidation refactor, true sunset/sunrise default position. |
| [v2.13.1](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.1) | 2026-04-03 | Climate Status sensor fix (#103), pipeline tilt field propagation fix. |
| [v2.13.0](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.0) | 2026-03-28 | Safety overrides bypass Automatic Control, config flow UX (#100). |

## Pending Upstream PRs

| PR | Repo | Status |
|----|------|--------|
| [hacs/default #6130](https://github.com/hacs/default/pull/6130) | `hacs/default` | 🟠 OPEN — in review queue. Do not comment or create new PRs. |
