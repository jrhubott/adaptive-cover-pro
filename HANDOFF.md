# Adaptive Cover Pro — Developer Handoff

**Date:** 2026-04-07
**Current Version:** v2.14.2
**Branch:** `feature/comprehensive-ha-interface-testing` (off `main`)

> Quick start: read this file, then `git status && git log --oneline -5`.
> Architecture, patterns, and workflow rules: see `CLAUDE.md`.

---

**Recent Merges:**
- `fix/issues-145-147-149-time-window-manual-override-numpy` — numpy serialization fix (#149), time window gate for climate/cloud handlers (#145), manual override detection during wait_for_target (#147). PR #150, merged to main.
- `fix/issues-146-148-config-summary-duration-icons` — Format DurationSelector dict in config summary (#148: raw dict rendered as `{'hours': 5...}`); remove emojis from all 13 translation files (#146). PR #151, merged to main.
- `fix/issue-140-display-rounding` — Round display values at presentation boundary; Target Position shows `42%` not `42.0%`, Sun Position shows `180.5°` not `180.456°` (PR #143, merged to main).
- `fix/issue-139-auto-control-off-covers-still-move` — Skip cover reposition on override expiry when automatic control is OFF (PR #141, merged to main).
- `fix/issue-134-climate-temp-not-read` — Climate mode never read temp/presence entities; summer/winter strategies never activated (PR #135, merged to main).
- `fix/issue-127-constant-repositioning-and-custom-position-priorities` — Stop constant repositioning at 0%/100% + configurable custom position priorities (PR #130, merged to main).

## Tests

**1633 passing, 3 xfailed, 0 failing** (+172 new tests from comprehensive HA interface testing branch).
Run: `source venv/bin/activate && python -m pytest tests/ -v`

New test files added (all on `feature/comprehensive-ha-interface-testing`):
`test_config_flow_integration.py`, `test_entity_registration.py`, `test_coordinator_lifecycle.py`,
`test_services_integration.py`, `test_diagnostics_integration.py`, `test_event_driven_integration.py`,
`test_translations.py`, `test_property_based.py`, `test_error_resilience.py`, `test_performance.py`;
extended `test_multi_cover_integration.py`; renamed `hass` fixture → `mock_hass` throughout.

## Open PRs (Awaiting Merge to Main)

| PR | Branch | Issue | Beta | Status | Notes |
|----|--------|-------|------|--------|-------|

## Open Issues

| # | Title | Notes |
|---|-------|-------|
| NEW | **Tilt cover returns >100% for narrow FOV** | `AdaptiveTiltCover.calculate_position()` returns values >100 (e.g. 101.4) for FOV angles ≤5° combined with high sun elevation (~60°). Found by hypothesis property-based test `test_tilt_position_always_0_to_100` (xfail). Fix: add `max(0, min(100, position))` clamp at end of `calculate_position()`. Affects real users if FOV is misconfigured very small. See `tests/test_property_based.py`. |
| NEW | **Duplicate unique_id: Manual Override binary sensor and switch** | Both `binary_sensor.py` (`manual_override` binary sensor) and `switch.py` (`Manual Override` switch) produce unique_id `{entry_id}_Manual Override`. HA entity registry de-duplication may silently drop one of these entities. Found by `test_unique_ids_are_unique` (xfail). Fix: change binary sensor unique_id to use the `key` field (e.g. `{entry_id}_binary_manual_override`) rather than the display name. See `tests/test_entity_registration.py`. |
| NEW | **diagnostics.py returns mappingproxy — not directly JSON-serializable** | `async_get_config_entry_diagnostics` in `diagnostics.py` returns `config_entry.options` as a raw `mappingproxy`, which fails `json.dumps()` without a custom encoder. HA's own download handler uses an encoder that handles this, so users don't see failures today; but any code that calls `json.dumps(diagnostics)` directly will crash. Fix: wrap options in `dict()` before returning. See `tests/test_diagnostics_integration.py::test_diagnostics_result_is_json_serializable`. |
| [#33](https://github.com/jrhubott/adaptive-cover/issues/33) | Better support for venetian blinds | KNX: single entity exposes both position + tilt. Needs config flow enhancement for dual-axis single-entity covers. |
| [#132](https://github.com/jrhubott/adaptive-cover-pro/issues/132) | Cover oscillates from 100% to 98% despite 10% delta threshold | Possible interaction with position limits or delta checking logic. |
| [#131](https://github.com/jrhubott/adaptive-cover-pro/issues/131) | Erratic behavior with multiple covers / unavailable entity | Most underlying bugs fixed in v2.14.2 — awaiting user confirmation. |
| [#145](https://github.com/jrhubott/adaptive-cover-pro/issues/145) | Start and end time not respected | Fixed in v2.14.2 / PR #150 — awaiting issue close by author. |
| [#147](https://github.com/jrhubott/adaptive-cover-pro/issues/147) | Manual override ignored during morning operations | Fixed in v2.14.2 / PR #150 — awaiting issue close by author. |
| [#149](https://github.com/jrhubott/adaptive-cover-pro/issues/149) | Download diagnostics HTTP error | Fixed in v2.14.2 / PR #150 — awaiting issue close by author. |
| [#146](https://github.com/jrhubott/adaptive-cover-pro/issues/146) | Remove icons from translations | Fixed in PR #151 / main — awaiting issue close by author. |
| [#148](https://github.com/jrhubott/adaptive-cover-pro/issues/148) | Configuration Summary unformatted time | Fixed in PR #151 / main — awaiting issue close by author. |


## Known Gotchas

- **[BUG, UNFIXED] Tilt cover position can exceed 100% for narrow FOV:** `AdaptiveTiltCover.calculate_position()` returns values >100 (e.g. 101.4) for very narrow FOV angles (≤5°) combined with high sun elevation (~60°). Found by hypothesis property-based test `test_tilt_narrow_fov_edge_case_known_bug` (xfail). Fix: add `max(0, min(100, position))` clamp at end of `calculate_position()` in `calculation.py`. This is unlikely to affect normal users (FOV ≤5° is unusually narrow) but is a correctness gap. See `tests/test_property_based.py`.

- **[BUG, UNFIXED] Duplicate unique_id: Manual Override binary sensor and switch:** Both `binary_sensor.py` (Manual Override binary sensor) and `switch.py` (Manual Override switch) produce `unique_id = f"{entry_id}_Manual Override"`. This collision causes HA entity registry de-duplication to silently merge the two entities. Found by `test_unique_ids_are_unique` (xfail). Fix: change binary sensor unique_id to use the `key` field instead of display name (e.g. `f"{entry_id}_binary_manual_override"`). See `custom_components/adaptive_cover_pro/binary_sensor.py` line 104.

- **[BUG, UNFIXED] diagnostics.py returns mappingproxy instead of dict:** `async_get_config_entry_diagnostics()` in `diagnostics.py` returns `config_entry.options` as a raw `mappingproxy`. HA's UI download handler uses a custom JSON encoder that handles this, so users don't see failures; but `json.dumps(diagnostics)` without a custom encoder will raise `TypeError`. Fix: wrap `config_entry.options` in `dict()` before returning. See `tests/test_diagnostics_integration.py`.

- **Override expiry forced reposition despite auto_control=False (fixed issue #139, branch):** `_async_send_after_override_clear()` used `force=True`, bypassing the `auto_control` gate. If the user turned Automatic Control OFF while a manual override was active, the cover would be force-repositioned to the pipeline position when the override timer expired. Fixed by adding an `automatic_control` guard before the entity loop (same pattern as the existing time-window guard). Regression: this path (force=True, inside window, auto_control=ON) continues to work as before.

- **Climate mode temp/presence never read (fixed PR #135, unreleased):** `_read_weather_conditions()` (now `_read_climate_state()`) was missing `temp_entity`, `outside_entity`, and `presence_entity` kwargs in the `ClimateProvider.read()` call since v2.12.0. `inside_temperature`/`outside_temperature` were always `None`; `is_presence` was always `True`. Summer/winter strategies never activated — climate mode silently degraded to solar tracking. Fixed in PR #135 with structural regression guard test.

- **Covers at 0%/100% received redundant commands every time_threshold minutes (fixed v2.14.0):** `_check_position_delta()` bypassed the delta check for special positions (0, 100, default, sunset) but didn't check if the cover was *already* at the target. Fixed by a same-position short-circuit placed after `sun_just_appeared` but before the special-positions bypass. See `managers/cover_command.py`.

- **Custom position handlers are now per-instance (v2.14.0):** `CustomPositionHandler` takes `(slot, entity_id, position, priority)`. One handler per configured slot, created in `coordinator._build_pipeline()`. `PipelineSnapshot.custom_position_sensors` is now a 4-tuple `(entity_id, is_on, position, priority)`. Update any test code building these tuples.

- **Force commands were blocked by time_delta (fixed on branch):** `async_handle_state_change` was calling `_build_position_context` without `force=True` even when the pipeline result had `bypass_auto_control=True`. Safety handlers (ForceOverride, Weather) produced the correct position but it could be blocked by the time_delta or position_delta gate. Fix: `async_handle_state_change` now checks `_pipeline_bypasses_auto_control` and passes `force=True` when True. Reason string also uses the pipeline's `control_method.value` (e.g. "force", "weather") instead of always saying "solar".

- **Reconciliation ignored auto_control=False (fixed on branch):** After disabling Automatic Control, the reconciliation timer kept resending the old solar target once per minute. `CoverCommandService` gains `auto_control_enabled` (bool, synced by coordinator each cycle) and `_safety_targets` (entities whose target was set via `force=True`). Reconciliation now skips non-safety targets when `auto_control_enabled` is False, but still resends safety targets (force override, weather) regardless of the toggle.

- **Reconciliation ignored manual override (fixed on branch, not yet released):** `_reconcile()` in `CoverCommandService` was resending the integration's stale `target_call` position once per minute, fighting the user's manual move. Root cause: reconciliation bypassed all gate checks including `manual_override`. Fix: coordinator syncs `manager.manual_controlled` → `_cmd_svc.manual_override_entities` each update cycle; `_reconcile()` skips entities in that set. Safety handlers (force override, weather) still take effect immediately via `apply_position(force=True)` which overwrites `target_call` before reconciliation runs.

- **`PipelineResult.tilt` not copied in registry (fixed v2.13.1):** `PipelineRegistry.evaluate()` now copies `tilt` alongside `climate_data`. Before v2.13.1, handler-set `tilt` values were silently dropped (only mattered for Issue #33 dual-axis).
- **`cover.default` property removed:** `AdaptiveGeneralCover` and `SunGeometry` no longer expose `.default`. Any code accessing it will get `AttributeError` immediately. Use `snapshot.default_position` in pipeline handlers; use `compute_effective_default()` elsewhere.
- **Time window gate moved into pipeline:** `SolarHandler` and `GlareZoneHandler` now self-gate on `snapshot.in_time_window` (returning `None` when outside the window). The `in_time_window` gate has been **removed** from `CoverCommandService.apply_position()` — `PositionContext` no longer has an `in_time_window` field. The pipeline still always runs; it is each handler's responsibility to check `snapshot.in_time_window` if needed. The `auto_control` gate remains in `CoverCommandService`.
- **Motion Control switch:** A new "Motion Control" switch entity is conditionally shown when `CONF_MOTION_SENSORS` are configured.
- **Manual override reset now sends correct position (fixed):** `reset_if_needed()` now runs BEFORE `_calculate_cover_state()` so the pipeline sees the cleared state. It returns the set of expired entity IDs; the coordinator calls `_async_send_after_override_clear()` with `force=True` for those covers. The reset button now resets the override first, calls `async_refresh()` so the pipeline runs without the override, then sends `coordinator.state` (the true post-override position — climate, solar, or default — whichever handler wins). The old pre-refresh send used `ManualOverrideHandler`'s simplified solar/default position which was wrong when climate mode was active. Its state is stored in `ToggleManager.motion_control` (default `True`) and passed to `PipelineSnapshot.motion_control_enabled`. `MotionTimeoutHandler` checks this field and passes through (returns `None`) when the switch is off, allowing lower-priority handlers to run as if motion timeout were inactive.
- **Manual override is higher priority than motion timeout (80 > 75):** A user manually moving the cover takes precedence over the "no occupancy" motion timeout. Previous ordering (motion 80, manual 70) caused motion timeout to fight user moves in occupied-but-still rooms. Config flow menu, Decision Priority chain, and README table all updated to reflect the new order.
- **Sync flow no longer aborts on empty selection:** Previously, submitting the "Copy to Other Covers" form with no targets or no categories selected would abort the entire options flow (losing all unsaved changes). Now it returns to the main options menu.
- **`NormalCoverState` is test-only:** `NormalCoverState` in `calculation.py` is no longer used by production code (coordinator stores `_cover_data` directly). It remains for backward compat with existing tests. Do not re-introduce it into coordinator logic.
- **`ClimateCoverState` takes a `PipelineSnapshot`:** Constructor changed from `(cover, climate_data, default_position=X)` to `(snapshot, climate_data)`. Use `make_snapshot_for_cover(cover, h_def)` from `tests/conftest.py` in tests.
- **Pipeline helpers are the canonical position helpers:** All handlers and `ClimateCoverState` use `pipeline/helpers.py` — `compute_solar_position()`, `compute_default_position()`, `apply_snapshot_limits()`, `compute_raw_calculated_position()`. Do not inline these patterns in new handlers.
- **`DiagnosticContext` uses `cover` + `pipeline_result`:** Old fields (`normal_cover_state`, `raw_calculated_position`, `climate_state`, `climate_data`, `climate_strategy`, `control_method`, `is_force_override_active`, etc.) are gone. Build a `PipelineResult` and pass it directly.

## Recent Releases

| Version | Date | Summary |
|---------|------|----------|
| [v2.14.2](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.14.2) | 2026-04-07 | numpy serialization fix (#149), time window gate for climate/cloud (#145), manual override detection (#147). |
| [v2.14.1](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.14.1) | 2026-04-07 | Format duration in config summary (#148), remove icons from translations (#146). |
| [v2.14.0](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.14.0) | 2026-04-05 | Fix constant repositioning at 0%/100% (#127); configurable priority per custom position slot. |
| [v2.13.9](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.9) | 2026-04-05 | Custom position pipeline handler (priority 77). |
| [v2.13.8](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.8) | 2026-04-05 | Config summary sunrise display, position settings reorganization, false manual override fix. |
| [v2.13.8-beta.4](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.8-beta.4) | 2026-04-05 | Config summary sunrise display + position settings reorganization (PR #124). |
| [v2.13.8-beta.3](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.8-beta.3) | 2026-04-04 | Fix: false manual override on automation positioning (race condition). |
| [v2.13.8-beta.2](https://github.com/jrhubott/adaptive-cover-pro/releases/tag/v2.13.8-beta.2) | 2026-04-04 | Fix: Motion Status no longer shows waiting_for_data after reload when sensor is on. |
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
