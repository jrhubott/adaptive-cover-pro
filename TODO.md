HACS

- ✅ PR #6130 - HACS Default Integration Submission
  - Status: OPEN, in review queue
  - Created: 2026-03-10 16:14:36Z
  - Branch: hacs/default:master ← add-adaptive-cover-pro
  - Changes: Added jrhubott/adaptive-cover-pro to integrations file
  - Label: New default repository
  - hacs-bot comment posted: "submission in review queue"
  - Processing: oldest-first queue, wait for HACS team review
  - Current version: v2.12.0
  - Related: Failed attempts #6128, #6129 (both instant rejected)
  - Action: WAIT - do not comment or create new PRs

Testing

- Add test files for coordinator.py (44%), config_flow.py (20%), sensor.py (38%), binary_sensor.py (0%), switch.py (0%)
- ~~Split test_calculation.py (2,197 lines, 142 tests) into per-class test files~~
- ~~Add end-to-end integration tests wiring state providers → calculation → pipeline → diagnostics~~
- Add boundary tests for sun.py and cover constructors (zero/negative/NaN inputs)

Code Review Follow-ups (deferred from 2026-05-01 cleanup pass)

These were identified during the full code review but kept out of the chore/tier1–tier3 cleanup plan because each carries behavior risk, test churn, or a large blast radius. Each is independently scoped — pick up as its own focused PR with full HA reload testing.

- Split `coordinator.process_entity_state_change()` (~260 lines, coordinator.py:661–917) into private helpers (`_handle_wait_for_target_grace`, `_check_transit_progress`, `_handle_step_motor_pause`). Risks subtle ordering changes in the grace-period state machine.
- Restructure `coordinator._update_options()` (coordinator.py:1722–1781) into a dedicated config-mapper class. Reads ~50 option keys inline; large surface area.
- Replace `CoverCommandService._track_action()`'s 15-parameter signature (managers/cover_command.py:1479–1527) with an `ActionSnapshot` dataclass. Cross-cuts every skip path.
- Extract `_prepare_service_call()` (managers/cover_command.py:1304–1448, 145 lines, 8 params) common branches into a `_set_target_state()` helper.
- Refactor entity instantiation in `sensor.py` (1,260 lines), `switch.py`, and `binary_sensor.py` into a dict-driven spec/factory pattern. Biggest line-count win (~1,500 → ~300) but rewires entity registry and risks unique_id drift.
- Encapsulate direct private-attr access — `coordinator._weather_mgr._override_active`, `coordinator._cover_data._last_calc_details`. Add public properties or accessor methods on the owning manager.
- Replace the 6-tuple `custom_position_sensors` in `PipelineSnapshot` (pipeline/types.py:131) with a typed `CustomPositionSensorState` dataclass. Touches every site that builds or unpacks the tuple.
- Group `CoverCommandService` per-entity dicts (`target_call`, `_sent_at`, `wait_for_target`, `_last_progress_at`, etc.) into a `PerEntityState` dataclass (managers/cover_command.py:146–227) to reduce dict-proliferation bugs.
- Make pipeline `OverrideHandler.contribute()` truly optional (pipeline/handler.py:28–41) — only `ClimateHandler` overrides it; have the registry check `hasattr` instead of inheriting an empty default.
- Move pipeline `_MERGEABLE` (pipeline/registry.py:98) into `pipeline/types.py` as a `PipelineResult` class-var with docstring, or derive it from dataclass field defaults.
- Add `last_skipped_action` enrichment review to `diagnostics/builder.py` — verify all skip codes in CLAUDE.md (`integration_disabled`, `auto_control_off`, `delta_too_small`, `time_delta_too_small`, `manual_override`, `no_capable_service`, `service_call_failed`, `dry_run`) are produced and that reason-specific extras (`position_delta`, `min_delta_required`, `elapsed_minutes`, `time_threshold_minutes`) appear when expected.
- Standardize timestamp handling in `MotionManager` (managers/motion.py:78–80, 134) — currently mixes `dt.datetime.now().timestamp()` (float) with `dt.datetime.now(dt.UTC)` (aware datetime).
- Add per-handler priority-rationale docstrings (each `pipeline/handlers/*.py`) — explain *why* the priority number is what it is, not just what the handler does.

