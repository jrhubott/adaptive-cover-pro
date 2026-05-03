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

Coordinator Refactoring (Break up coordinator.py)

Analysed 2026-05-03 using Opus. `coordinator.py` is 2,446 lines / 115 methods with 9 distinct responsibility clusters. Goal: thin orchestrator (~700 lines) + `coordinator/` subpackage. HA core reviewers flag files >1,000 lines.

**Existing managers already extracted:** `managers/manual_override.py`, `grace_period.py`, `motion.py`, `position_verification.py`, `cover_command.py` (~3,000 lines already moved out).

### Target layout

```
coordinator.py                         # ~700 lines: __init__, _async_update_data, lifecycle, state property
coordinator/
  __init__.py                          # re-exports for back-compat
  events.py                            # EventListenerService — the 5 async_check_* HA listener methods (~280 lines)
  transit_state_machine.py             # TransitStateMachine — process_entity_state_change + _target_just_reached + _pending_cover_events (~310 lines)
  update_cycle.py                      # async_handle_state_change, async_handle_cover_state_change, async_handle_first_refresh, _async_send_after_override_clear, _build_position_context (~250 lines)
  pipeline_runner.py                   # PipelineRunner — _calculate_cover_state, _build_pipeline, _build_climate_options, _read_*_sensor_states, _read_climate_state (~250 lines)
  cover_factory.py                     # build_cover_engine() — dispatches get_blind_data to Vertical/Horizontal/Tilt (~90 lines)
  transitions.py                       # SunValidityTracker, SunsetWindowTracker, _compute_current_effective_default, _check_time_window_transition (~220 lines)
  options_loader.py                    # CoordinatorOptions frozen dataclass + from_options() loader, replaces _update_options (~150 lines)
  delegates.py                         # _CoordinatorDelegatesMixin — all 19 toggle pairs, identity props, manager pass-throughs (~150 lines)
  diagnostics_assembly.py              # build_diagnostic_context() free function, thin wrapper around DiagnosticsBuilder (~140 lines)
```

### PR sequence (lowest risk first)

| #   | What                                                                                                      | Risk    | Key files touched                                                    |
| --- | --------------------------------------------------------------------------------------------------------- | ------- | -------------------------------------------------------------------- |
| 1   | Extract `cover_factory.build_cover_engine()`                                                              | Low     | coordinator.py, new cover_factory.py                                 |
| 2   | Extract `coordinator/diagnostics_assembly.py`                                                             | Low     | coordinator.py, diagnostics/builder.py                               |
| 3   | Extract `coordinator/transitions.py`                                                                      | Low-Med | coordinator.py, tests/test_coordinator_integration.py                |
| 4   | Extract `coordinator/pipeline_runner.py`                                                                  | Med     | coordinator.py, pipeline/                                            |
| 5   | Extract `coordinator/options_loader.py` with `CoordinatorOptions` dataclass                               | Med     | coordinator.py, const.py                                             |
| 6   | Extract `coordinator/delegates.py` mixin                                                                  | Low     | coordinator.py, sensor.py, switch.py, binary_sensor.py               |
| 7   | Extract `coordinator/events.py`                                                                           | Med     | coordinator.py                                                       |
| 8   | Extract `coordinator/transit_state_machine.py` — verbatim move first                                      | High    | coordinator.py, tests that call process_entity_state_change directly |
| 9   | Decompose TransitStateMachine into helper methods (\_handle_grace_period, \_check_transit_progress, etc.) | Med     | coordinator/transit_state_machine.py only                            |
| 10  | Extract `coordinator/update_cycle.py`                                                                     | High    | coordinator.py, test_coordinator_integration.py                      |

### Critical rules for every PR

- **Keep forwarder methods on `coordinator`** for anything tests mock directly — the public surface must stay identical throughout
- `coordinator._pipeline_result` → expose as `runner.last_result`, keep forwarder property on coordinator (6 readers in tests)
- `coordinator._build_position_context` and `coordinator._check_sun_validity_transition` — 20+ mock sites in tests; must stay callable on coordinator
- `coordinator._async_send_after_override_clear` — mocked in test_override_expiry_time_window.py and test_reset_button_time_window.py; keep forwarder until PR #10
- Do NOT reorder `__init__` dependency construction (event_buffer → grace_mgr → cmd_svc ordering is load-bearing)
- Do NOT move `_async_update_data` itself until PR #10; extract only its callees in PRs 1–9

### Riskiest single extraction

`process_entity_state_machine` (PR #8) — 260-line grace-period/transit/step-motor ordering with documented invariants; 5 tests invoke it as unbound method. Strategy: move verbatim first (no decomposition), confirm tests pass, then decompose in PR #9.

### Files to read before starting

- `coordinator.py` — the target (2,446 lines)
- `tests/test_coordinator_integration.py` — heaviest mock file; read before PR #4 and PR #10
- `tests/test_override_expiry_time_window.py`, `tests/test_reset_button_time_window.py` — mock `_async_send_after_override_clear`; gate PR #10
- `tests/test_manual_override.py`, `tests/test_manual_override_persistence.py` — mock `_build_position_context`

HA Core Readiness

- Remove pandas dependency — replace all three uses with stdlib equivalents:
  - `sun.py`: `pd.date_range` → `range()` + `datetime + timedelta`
  - `helpers.py`: `pd.to_timedelta` → `datetime.timedelta` or `dateutil`
  - `engine/sun_geometry.py`: DataFrame FOV entry/exit filtering → plain loop over `solar_azimuth`/`solar_elevation` lists
  - Motivation: pandas (~40 MB) is a blocker for HA core inclusion; all three uses are trivially replaceable

Code Review Follow-ups (deferred from 2026-05-01 cleanup pass)

These were identified during the full code review but kept out of the chore/tier1–tier3 cleanup plan because each carries behavior risk, test churn, or a large blast radius. Each is independently scoped — pick up as its own focused PR with full HA reload testing.

- Split `coordinator.process_entity_state_change()` (~260 lines, coordinator.py:661–917) into private helpers (`_handle_wait_for_target_grace`, `_check_transit_progress`, `_handle_step_motor_pause`). Risks subtle ordering changes in the grace-period state machine.
- Restructure `coordinator._update_options()` (coordinator.py:1722–1781) into a dedicated config-mapper class. Reads ~50 option keys inline; large surface area.
- Replace `CoverCommandService._track_action()`'s 15-parameter signature (managers/cover_command.py:1479–1527) with an `ActionSnapshot` dataclass. Cross-cuts every skip path.
- Extract `_prepare_service_call()` (managers/cover_command.py:1304–1448, 145 lines, 8 params) common branches into a `_set_target_state()` helper.
- Refactor entity instantiation in `sensor.py` (1,260 lines), `switch.py`, and `binary_sensor.py` into a dict-driven spec/factory pattern. Biggest line-count win (~1,500 → ~300) but rewires entity registry and risks unique_id drift.
- Encapsulate direct private-attr access — `coordinator._weather_mgr._override_active`, `coordinator._cover_data._last_calc_details`. Add public properties or accessor methods on the owning manager.
- ~~Replace the 6-tuple `custom_position_sensors` in `PipelineSnapshot` (pipeline/types.py:131) with a typed `CustomPositionSensorState` dataclass. Touches every site that builds or unpacks the tuple.~~ (done — frozen `CustomPositionSensorState` in pipeline/types.py; CLAUDE.md now codifies "dataclass over multi-field tuple" as project policy)
- ~~Group `CoverCommandService` per-entity dicts (`target_call`, `_sent_at`, `wait_for_target`, `_last_progress_at`, etc.) into a `PerEntityState` dataclass (managers/cover_command.py:146–227) to reduce dict-proliferation bugs.~~ (done — 8 parallel dicts/sets collapsed into a single `dict[str, PerEntityState]`; typed accessors `get_target`/`set_target`/`is_waiting_for_target`/`set_waiting`/`state(eid)` replace direct dict pokes everywhere)
- Make pipeline `OverrideHandler.contribute()` truly optional (pipeline/handler.py:28–41) — only `ClimateHandler` overrides it; have the registry check `hasattr` instead of inheriting an empty default.
- Move pipeline `_MERGEABLE` (pipeline/registry.py:98) into `pipeline/types.py` as a `PipelineResult` class-var with docstring, or derive it from dataclass field defaults.
- Add `last_skipped_action` enrichment review to `diagnostics/builder.py` — verify all skip codes in CLAUDE.md (`integration_disabled`, `auto_control_off`, `delta_too_small`, `time_delta_too_small`, `manual_override`, `no_capable_service`, `service_call_failed`, `dry_run`) are produced and that reason-specific extras (`position_delta`, `min_delta_required`, `elapsed_minutes`, `time_threshold_minutes`) appear when expected.
- ~~Standardize timestamp handling in `MotionManager` (managers/motion.py:78–80, 134) — currently mixes `dt.datetime.now().timestamp()` (float) with `dt.datetime.now(dt.UTC)` (aware datetime).~~ (done — PR #322 commit 2: routed all 5 sites through a single `_now()` UTC-aware helper)
- Add per-handler priority-rationale docstrings (each `pipeline/handlers/*.py`) — explain _why_ the priority number is what it is, not just what the handler does.
