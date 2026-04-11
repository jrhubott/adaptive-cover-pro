# CLAUDE.md

**For comprehensive developer documentation, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**

## Session Startup

**Read `HANDOFF.md` before doing anything else.**

```bash
cat HANDOFF.md && git status && git log --oneline -5
```

## HANDOFF.md

A lean forward-looking handoff note — not a changelog. Update at end of any session where code merged, release cut, PR opened/closed/merged, or issue opened/closed. Do not update mid-feature-branch.

**Belongs:** Current version/branch/state, last-session summary, test count (pass/fail only), open GitHub issues (title + one-line note), pending upstream PRs, WIP gotchas.  
**Does NOT belong:** Architecture details, release history, per-module coverage tables, recently closed issues.

**Open PRs table:** Add rows when PRs open, update status when beta created or issue author confirms, remove when merged or closed.

## Project Overview

**Adaptive Cover Pro** is a Home Assistant custom integration that controls vertical blinds, horizontal awnings, and venetian blinds based on sun position. Calculates optimal positions to filter direct sunlight while maximizing natural light with climate-aware operation.

**Language:** Python 3.11+ | **Framework:** Home Assistant Core (async) | **Requires:** HA 2024.5.0+

## Architecture

Data Coordinator Pattern with layered architecture:

**`state/`** — HA boundary (all HA reads happen here)
- `climate_provider.py` → `ClimateReadings`; `sun_provider.py` → `SunData`; `cover_provider.py`; `snapshot.py`

**`calculation.py` + `sun.py`** — Pure calculation engine (0 HA imports)
- `AdaptiveGeneralCover` base (takes injected `SunData`, not `hass`); `AdaptiveVerticalCover`, `AdaptiveHorizontalCover`, `AdaptiveTiltCover`; `NormalCoverState`, `ClimateCoverState`, `ClimateCoverData`

**`pipeline/`** — Override priority chain
- `registry.py` evaluates handlers in priority order, builds decision trace
- Handlers: `force_override`(100) > `weather`(90) > `manual_override`(80) > `custom_position_N`(1–99, default 77) > `motion_timeout`(75) > `cloud_suppression`(60) > `climate`(50) > `glare_zone`(45) > `solar`(40) > `default`(0)
- Up to 4 custom position handlers per instance; `_build_pipeline()` in coordinator creates them at startup; `PipelineRegistry` sorts by priority

**`managers/`** — Extracted coordinator responsibilities
- `manual_override.py`, `grace_period.py`, `motion.py`, `position_verification.py`, `cover_command.py`

**`diagnostics/builder.py`** — Builds all diagnostic data from pipeline result (`DiagnosticsBuilder` + `DiagnosticContext`)

**`coordinator.py`** — Thin orchestrator (~1,591 lines); runs update cycle, routes events, schedules refreshes, delegates to managers/providers/pipeline/diagnostics

**`engine/`** — Next-gen calculation engine: `sun_geometry.py` (`SunGeometry`), `covers/venetian.py` (`VenetianCoverCalculation`)

**Data Flow:**
1. Config entry → coordinator setup (managers, providers, pipeline)
2. Entity change → coordinator event handler
3. Providers read HA → pure data (SunData, ClimateReadings)
4. Pure calc engine computes position
5. Pipeline evaluates priority chain → `PipelineResult`
6. Interpolation + inverse state applied
7. `CoverCommandService` sends commands (delta/time checks)
8. `DiagnosticsBuilder` produces diagnostic data

## Development

```bash
./scripts/setup              # Install dev dependencies + pre-commit hooks
./scripts/develop            # Start HA dev server (http://localhost:8123)
./scripts/lint               # Ruff with auto-fix
ruff format .                # Format code
```

### Ruff Config

Ruff config lives in **`.ruff.toml`** (takes precedence over `pyproject.toml`). Per-file rules:
- `tests/**/*.py` — D102, D103, D205 suppressed (docstrings not required in tests)
- `tests/*.py` — above + E402 suppressed (pytest conftest pattern allows post-assignment imports)
- `scripts/*.py` — T20, D103 suppressed (CLI scripts intentionally use `print`)

**Common linting patterns to follow:**
- Use `{}` dict literals, not `dict()` calls (C408)
- Prefix intentionally unused variables with `_` (F841)
- Use `contextlib.suppress(Exc)` instead of bare `try/except/pass` (SIM105)
- Combine nested `with` statements into one (SIM117)

```bash
venv/bin/python -m pytest tests/ -v
venv/bin/python -m pytest tests/test_calculation.py -v
venv/bin/python -m pytest tests/ --cov=custom_components/adaptive_cover_pro --cov-report=term
```

## ⚠️ Git & GitHub Workflow

### Branch Strategy

**Default: commit directly to current branch.** Only create a feature branch when explicitly asked.

| Type | Prefix | Example |
|------|--------|---------|
| New feature | `feature/` | `feature/add-shade-control` |
| Bug fix | `fix/` | `fix/climate-mode-bug` |
| Docs | `docs/` | `docs/update-readme` |
| Issue (bug) | `fix/issue-NNN-` | `fix/issue-123-sensor-unavailable` |
| Issue (feature) | `feature/issue-NNN-` | `feature/issue-67-entity-picture` |

- ✅ Always branch from current branch (pull latest first)
- ✅ Always ask before creating a PR
- ❌ Never merge to main without a PR

### Commit Messages

- ❌ NEVER add `Co-Authored-By: Claude` or `Generated with Claude Code`
- ✅ First-person voice (I/me)
- Conventional format: `fix:`, `feat:`, `docs:`, `chore:`, `test:` + optional `(#123)`, under 70 chars

### Pull Requests

**Body template:**
```markdown
## Summary
What changed and why.

## Testing
- ✅ X tests passing

## Related Issues
Refs #123
```

**When exiting Plan Mode:** Use AskUserQuestion to ask if user wants to (1) create PR, (2) merge + stable release, or (3) beta release.  
**Ad-hoc changes:** Push branch, ask if they want a PR — never create or merge automatically.

### Working with Issues

```bash
gh issue view 123
git checkout -b fix/issue-123-short-description
# Commit with: Refs #123 (issues auto-close when the fix ships in a stable release)
git push -u origin fix/issue-123-short-description
# Ask before gh pr create
```

## Release Process

```bash
# 1. Write and commit release notes FIRST (script requires clean working directory)
cat > release_notes/vX.Y.Z.md << 'EOF'
## 🎯 Title
### ✨ Features
### 🐛 Bug Fixes
### 🧪 Testing
EOF
git add release_notes/vX.Y.Z.md && git commit -m "docs: Add release notes for vX.Y.Z"

# 2. Create release
./scripts/release patch --notes release_notes/vX.Y.Z.md --yes        # stable (from main)
./scripts/release beta --notes release_notes/vX.Y.Z-beta.1.md --yes  # beta (from feature branch)
./scripts/release beta --yes --auto-notes                              # quick beta
./scripts/release --dry-run                                            # preview
```

**Version spec:** `patch` / `minor` / `major` / `beta` (auto-increment) / `X.Y.Z` (explicit)  
**`beta` behavior:** stable `2.6.8` → `2.6.9-beta.1`; beta `2.6.9-beta.1` → `2.6.9-beta.2`

| Branch | Release type | Command |
|--------|-------------|---------|
| `feature/*`, `fix/*` | Beta (prerelease) | `./scripts/release beta ...` |
| `main` | Stable | `./scripts/release patch ...` |

⚠️ If creating beta from main, STOP — create a feature branch first.  
⚠️ Only create releases when explicitly requested by the user. NEVER create proactively.

**Release notes rules:** ❌ No `Co-Authored-By:` or AI attributions. ✅ Always use `--notes release_notes/vX.Y.Z.md` (never `--editor`).

## Testing

**Always add/update tests when making code changes.**

- New features, classes, methods, calculation logic → new tests
- Changed algorithms, default behaviors, bug fixes → update + add regression test

Checklist: ✅ write tests → ✅ all pass → ✅ check coverage for modified files → ✅ 90%+ for calculation logic.

## Documentation

| Changed | Update |
|---------|--------|
| User-visible feature | `README.md` |
| Development process | `docs/DEVELOPMENT.md` |
| VS Code testing | `docs/VSCODE_TESTING_GUIDE.md` |
| Feature in "Planned" | Mark `~~Completed~~` in README, mention in release notes |

## Code Standards & Patterns

### No Code Duplication — Always Propose Unified Approaches

**Code duplication is not okay.** When two code paths need the same policy (gates, guards, logging, side-effects), extract a single shared method and have both paths delegate to it. Do not mirror guard blocks, do not copy-paste logic "for now", and do not justify duplication with "they might diverge later".

- When planning a fix that touches logic already present elsewhere, your **first** proposal must be the unified one. Only fall back to duplication if the user explicitly rejects unification.
- When generalizing an existing method to serve a second caller, preserve the original caller's behavior with safe defaults (optional params, unchanged return semantics for old callers).
- Prefer growing one method by a few lines over creating a second method that repeats the same gate checks, `force=True` semantics, or logging format.

### Configuration Summary Must Track Every Behavior-Affecting Option

`_build_config_summary()` in `config_flow.py` is the user's final sanity check before saving — shown by `async_step_summary` in both the initial setup flow (~line 2279) and the options flow (~line 3127). It is a hand-written narrative, **not** a generic key/value dump, so it does not update itself when you add a new option.

**Rule:** Any change that adds or modifies a `CONF_*` option which influences cover behavior (position targets, overrides, gates, schedules, preset routing, etc.) must, in the same change:

1. Update `_build_config_summary()` so the summary truthfully reflects the new behavior. Use an existing section where the option fits ("How It Decides", "Position Limits", "Position Map", "Decision Priority") — only add a new section if none fits.
2. If the option has a footgun (e.g. a bool flag that silently no-ops when a paired value is blank), surface it as a `⚠️` warning line in the summary.
3. Add or update tests in `tests/test_config_flow_summary.py` asserting both the rendered text and any fall-through / warning paths.

**Exempt:** purely diagnostic options that don't change what the cover does (e.g. a logging toggle). When in doubt, add the line — the summary is cheap real-estate and users rely on it.

### Home Assistant Patterns
- Async-first — all I/O is async; never block the event loop
- Coordinator pattern for entity updates; `_handle_coordinator_update()` in entities
- Store data in `coordinator.data`

### Adding a New Config Option
1. Constant in `const.py` (`CONF_*`)
2. Schema + validation in `config_flow.py`
3. Read in `coordinator.py` from `self.config_entry.options`
4. Expose via sensor/switch if needed
5. Translations in `translations/en.json`
6. Update `_build_config_summary()` + `tests/test_config_flow_summary.py` (see rule above)

**Optional numeric fields:** Use `NumberSelectorMode.SLIDER` (not `BOX`). BOX doesn't preserve `None` — clearing saves `0`. Check `if value is not None` (not `if value`) to distinguish 0% from unset.

**Position description standard:** Always cover-type aware: `0% = closed (blinds lowered / awning retracted), 100% = open (blinds raised / awning extended)`. Canonical `data_description`: `Position (0-100%) to move covers to when [context] is active. 0% = closed (blinds lowered / awning retracted), 100% = open (blinds raised / awning extended). Default: 0%.`

### `last_skipped_action` Dict Structure

Always-present keys: `entity_id`, `reason`, `calculated_position`, `current_position`, `trigger`, `inverse_state_applied`, `timestamp`

Reason-specific: `delta_too_small` → `position_delta`, `min_delta_required`; `time_delta_too_small` → `elapsed_minutes`, `time_threshold_minutes`

Skip codes: `integration_disabled`, `auto_control_off`, `delta_too_small`, `time_delta_too_small`, `manual_override`, `no_capable_service`, `service_call_failed`, `dry_run`

Signatures:
- `_skip(entity_id, reason, position, *, trigger="", inverse_state=False, current_position=None, extras=None)`
- `record_skipped_action(entity, reason, state, *, trigger="", current_position=None, inverse_state=False, extras=None)`

### Diagnostic Sensor Guidelines

```python
_attr_native_unit_of_measurement = ""        # text/status sensors: excludes from logbook
_attr_native_unit_of_measurement = "retries" # numeric sensors: enables statistics
_attr_entity_registry_enabled_default = True # all diagnostic sensors enabled by default
```

### Display Rounding (Issue #140)

Round **only at the presentation boundary** — never inside calc engine, pipeline, managers, or state providers.

| Layer | Where |
|-------|-------|
| `suggested_display_precision` | Sensor class attribute (controls HA frontend decimals) |
| Explicit `round()` | `extra_state_attributes` and `DiagnosticsBuilder` |

Rules: sun angles/gamma → 1 decimal; temperatures → 1 decimal; positions → integer; timestamps → no rounding. Wrap coordinator final return in `int(round(state))` to catch numpy float64 from `interpolate_position()`.

### Motion Control (v2.7.5+)

OR logic: ANY sensor motion → enables automatic positioning; debounce "no motion" only. Priority: force override > weather > manual override > motion timeout. Asyncio task-based in `coordinator.py`. See `tests/test_motion_control.py`.

### Special Position Delta Bypass (Issue #127)

`build_special_positions()` in `managers/cover_command.py` returns positions (0, 100, default_height, sunset_pos) that bypass delta-threshold. Bypass applies **to or from** these positions but does NOT bypass same-position short-circuit (runs after `sun_just_appeared`, before special-positions check).

### ⚠️ Inverse State — DO NOT CHANGE

Calculate → invert (`100 - state`) → send (position-capable) or compare to threshold (open/close-only). Never change the order of inversion and threshold checking.

### Enhanced Geometric Accuracy (v2.7.0+, Issue #1)

Implemented in `AdaptiveVerticalCover.calculate_position()`:
- **Edge cases** (`_handle_edge_cases()`): elevation < 2° or |gamma| > 85° → full coverage; elevation > 88° → simplified calc
- **Safety margins** (`_calculate_safety_margin()`): multiplier ≥1.0; gamma smoothstep (1.0→1.2 at 90°); low/high elevation margins; multiplicative
- **Window depth** (`window_depth`, 0.0–0.5m): adds `depth_contribution = window_depth × sin(|gamma|)` when |gamma| > 10°
- **Sill height** (`sill_height`, 0.0–3.0m): reduces effective distance, raises the blind
- **Flow:** edge cases → window depth → sill height → base calc → safety margin → clip to h_win
- `window_depth=0` and `sill_height=0` must produce identical results to pre-v2.7.0

## Configuration Structure

**`config_entry.data`:** `name`, `sensor_type` (cover_blind / cover_awning / cover_tilt)

**`config_entry.options`** (key options):
- Window: azimuth, FOV, elevation limits
- Cover dimensions: height, length, slat properties
- Sunset: `sunset_position` (None=use default), `sunset_offset`/`sunrise_offset`, `return_sunset` (force-sends default at end_time)
- Position limits: `min_position`/`max_position`, `enable_min_position`/`enable_max_position` (False=always enforce, True=only during sun tracking)
- Automation: delta position/time, start/end times, manual override
- Force override: `force_override_sensors` (list of binary sensor IDs), `force_override_position`
- Custom positions: `custom_position_sensor_1–4`, `custom_position_1–4`, `custom_position_priority_1–4` (1–99, default 77)
- Motion: `motion_sensors` (list; empty=disabled), `motion_timeout` (30–3600s, default 300)
- Climate: temp entities/thresholds, presence, weather
- Light: lux/irradiance entities and thresholds

## Manual Testing

```bash
./scripts/develop            # HA dev server (http://localhost:8123)
jupyter notebook             # Visual simulation: notebooks/simulate_cover.ipynb
```

Update `notebooks/simulate_cover.ipynb` when `calculation.py` or `SunData` API changes.

## File Organization

```
adaptive-cover/
├── custom_components/adaptive_cover_pro/
│   ├── __init__.py              # Integration entry point
│   ├── coordinator.py           # Thin orchestrator (~1,591 lines)
│   ├── calculation.py           # Pure calculation engine (0 HA imports)
│   ├── config_flow.py           # Configuration UI
│   ├── config_types.py          # CoverConfig typed dataclass
│   ├── sun.py                   # Pure solar calculations
│   ├── engine/                  # Next-gen calculation engine
│   │   ├── sun_geometry.py
│   │   └── covers/venetian.py
│   ├── managers/                # Extracted coordinator responsibilities
│   │   ├── manual_override.py, grace_period.py, motion.py
│   │   ├── position_verification.py, cover_command.py
│   ├── state/                   # HA boundary layer
│   │   ├── climate_provider.py, cover_provider.py
│   │   ├── snapshot.py, sun_provider.py
│   ├── pipeline/                # Override priority chain
│   │   ├── registry.py, types.py, handler.py
│   │   └── handlers/            # 10 priority handlers
│   ├── diagnostics/builder.py
│   ├── services/configuration_service.py
│   ├── sensor.py, switch.py, binary_sensor.py, button.py
│   ├── entity_base.py, helpers.py, const.py, enums.py
│   ├── geometry.py, position_utils.py
│   ├── manifest.json
│   └── translations/            # 13 languages
├── scripts/setup, develop, lint, release
├── tests/                       # 1,461 tests
├── release_notes/
├── docs/DEVELOPMENT.md, ARCHITECTURE.md, UNIT_TESTS.md, CONTRIBUTING.md
├── CLAUDE.md, README.md, HANDOFF.md
└── pyproject.toml
```

## Dependencies

**Production:** `homeassistant~=2024.5`, `pandas~=2.2`, `astral`  
**Development:** `ruff~=0.4`, `pre-commit~=3.7`, `pytest`, `pvlib~=0.11`, `matplotlib~=3.9`

## Cover Types

- **Vertical** (`cover_blind`) — up/down movement
- **Horizontal** (`cover_awning`) — in/out movement
- **Tilt** (`cover_tilt`) — slat rotation

**Climate Mode:** Winter → open when cold+sunny; Summer → close when hot; Intermediate → calculated position with weather awareness.
