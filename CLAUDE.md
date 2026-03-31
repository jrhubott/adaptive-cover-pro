# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**For comprehensive developer documentation, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** - This file contains instructions for Claude Code specifically, while docs/DEVELOPMENT.md is the human-readable developer guide.

## Session Startup

**At the start of every new session, read `HANDOFF.md` before doing anything else.** It contains:
- Current version and branch state
- Test suite status and coverage summary
- Open GitHub issues (backlog)
- Key gotchas and architectural reminders
- Release checklist

```bash
# Quick orientation at session start
cat HANDOFF.md
git status
git log --oneline -5
```

## HANDOFF.md — Keeping It Current

`HANDOFF.md` is the project's living status document. Update it whenever any of the following change:

| Event | What to update in HANDOFF.md |
|-------|------------------------------|
| New release cut | Current Version, Recent Releases table |
| Issue opened or closed | Open Issues table |
| PR merged | Recent Releases, remove from open PRs if listed |
| Test count or coverage changes | Tests section |
| New architectural pattern established | Key Patterns or Known Gotchas section |
| Bug fixed that was a known gotcha | Remove or update that gotcha |

**When to update:** At the end of any session where code was merged, a release was cut, or an issue was opened/closed. Do not update mid-feature-branch — wait until changes land on `main`.

**How to update:** Edit `HANDOFF.md` directly with the Write or Edit tool. Keep entries concise — it is a quick-reference document, not a changelog.

## Project Overview

**Adaptive Cover Pro** is a Home Assistant custom integration that automatically controls vertical blinds, horizontal awnings, and tilted/venetian blinds based on the sun's position. It calculates optimal positions to filter direct sunlight while maximizing natural light and supporting climate-aware operation.

**Language:** Python 3.11+
**Framework:** Home Assistant Core (async architecture)
**Current Version:** See `manifest.json`
**Requires:** Home Assistant 2024.5.0+

## Architecture Overview

This integration follows Home Assistant's **Data Coordinator Pattern**:

### Core Components

**`coordinator.py`** - Central hub for all state management
- `AdaptiveDataUpdateCoordinator` manages async updates, entity listeners, and position calculations
- Tracks state changes from sun position, temperature, weather, presence entities
- Handles manual override detection and control
- Orchestrates position calculations and cover service calls

**`calculation.py`** - Position calculation algorithms
- `AdaptiveVerticalCover` - Up/down blind calculations with enhanced geometric accuracy
  - `calculate_position()` - Main calculation with safety margins and window depth support
  - `_calculate_safety_margin()` - Angle-dependent safety margins (0-35% at extremes)
  - `_handle_edge_cases()` - Robust fallbacks for extreme sun angles
  - Supports optional `window_depth` parameter for advanced precision
- `AdaptiveHorizontalCover` - In/out awning calculations
- `AdaptiveTiltCover` - Slat rotation calculations
- `NormalCoverState` - Basic sun position mode
- `ClimateCoverState` - Climate-aware mode with temperature/presence/weather

**`config_flow.py`** - Multi-step UI configuration
- Separate flows for vertical/horizontal/tilt cover types
- Common options, automation settings, climate mode, blind spots
- Option validation and context-aware forms

### Platform Files

- `sensor.py` - Position, control method, start/end sun times
- `switch.py` - Automatic control, climate mode, manual override detection
- `binary_sensor.py` - Sun visibility, manual override status
- `button.py` - Manual override reset

### Data Flow

1. **Initialization:** Config flow creates `ConfigEntry` → coordinator setup
2. **Listeners:** Coordinator registers listeners on sun, temperature, weather, presence entities
3. **State Change:** Entity change triggers `async_check_entity_state_change()`
4. **Calculation:** `_async_update_data()` calls appropriate cover class to calculate position
5. **Update:** Coordinator updates data → platform entities refresh
6. **Control:** If enabled and not manually overridden → calls cover service to move blinds

## Development Environment

### Setup

```bash
./scripts/setup              # Install dev dependencies and setup pre-commit hooks
```

### Development Server

```bash
./scripts/develop            # Start Home Assistant in debug mode with this integration loaded
```

The development server:
- Creates `config/` directory if not present
- Sets `PYTHONPATH` to include `custom_components/`
- Starts Home Assistant with debug logging
- Uses `config/configuration.yaml` for test setup with mock entities

### Linting

```bash
./scripts/lint               # Run ruff linting with auto-fix
ruff check . --fix           # Direct ruff invocation
ruff format .                # Format code
```

Pre-commit hooks run automatically on commit:
- Ruff linting and formatting
- Prettier for YAML/JSON
- Trailing whitespace cleanup

## ⚠️ CRITICAL: Git & GitHub Workflow

### Branch Strategy

**BEFORE MAKING ANY CODE CHANGES:**

1. **Check current branch:** `git branch --show-current`
2. **Create feature branch from dev** (REQUIRED)

```bash
# ALWAYS branch from dev
git checkout dev
git pull origin dev

# Create feature branch
git checkout -b <prefix>/<description>
```

**Branch Naming Conventions:**

| Type | Prefix | Example |
|------|--------|---------|
| New feature | `feature/` | `feature/add-shade-control` |
| Bug fix | `fix/` | `fix/climate-mode-bug` |
| Documentation | `docs/` | `docs/update-readme` |
| GitHub issue (bug) | `fix/issue-NNN-` | `fix/issue-123-sensor-unavailable` |
| GitHub issue (feature) | `feature/issue-NNN-` | `feature/issue-67-entity-picture` |

**Rules:**
- ✅ ALWAYS create a feature branch FIRST (before any edits)
- ✅ ALWAYS branch from `dev` (never from `main` or other feature branches)
- ✅ ALWAYS create a pull request after pushing the branch
- ✅ Keep commits atomic and focused
- ✅ Test changes on the feature branch
- ❌ NEVER commit directly to `dev` or `main` branch
- ❌ NEVER skip feature branches "because it's a small change"
- ❌ NEVER merge to main without a pull request

### Working with GitHub Issues

When the user references an issue number (e.g., "fix issue #123"):

1. **Fetch details:** `gh issue view 123`
2. **Create branch:** `git checkout -b fix/issue-123-short-description` (bugs) or `feature/issue-123-...`
3. **Commit with reference** — closing keywords: `Fixes #123`, `Closes #123`, `Resolves #123`; non-closing: `Related to #123`
4. **Push and create PR immediately:**
   ```bash
   git push -u origin fix/issue-123-short-description
   gh pr create --title "fix: Short description (#123)" --body "Fixes #123" --base dev
   ```

Issues auto-close when PR is merged if body contains `Fixes #123`.

### Pull Request Workflow

**CRITICAL: All branches MUST have a PR before merging to main — always, even single-commit changes.**

**PR Title:** Conventional format (`fix:`, `feat:`, `docs:`, `chore:`, `test:`) + optional `(#123)`, under 70 chars.

**PR Body Template:**
```markdown
## Summary
What changed and why.

## Testing
- ✅ X tests passing
- ✅ Scenario tested

## Related Issues
Fixes #123
```

**Common operations:**
```bash
gh pr view / gh pr status / gh pr list
gh pr merge --squash   # or --merge / --rebase
```

### Merging and Release Workflow

**CRITICAL: Context-Aware Merge Behavior**

**When Exiting Plan Mode:**
- ✅ ALWAYS ask the user if they want to:
  1. Create a pull request (if not already created)
  2. Merge the PR and create a production release
  3. Create a beta release (without merging to main)
- Use the AskUserQuestion tool to present these options
- Wait for user confirmation before proceeding with PR creation, merge, or release

**When NOT in Plan Mode (ad-hoc changes):**
- ✅ Create a pull request after pushing changes (always required)
- ✅ Stay on the feature/fix branch after pushing changes
- ❌ DO NOT ask about merging to main
- ❌ DO NOT merge automatically
- The user will decide when to merge the PR separately

**gh CLI Quick Reference:**

| Task | Command |
|------|---------|
| View issue | `gh issue view 123` |
| List open issues | `gh issue list` |
| List by label | `gh issue list --label bug` |
| Add comment | `gh issue comment 123 --body "Message"` |
| Close manually | `gh issue close 123 --comment "Fixed in vX.Y.Z"` |
| Create PR | `gh pr create --title "Fix: Description (#123)" --body "Fixes #123"` |
| Link PR to issue | Include "Fixes #123" in PR body |
| View PR | `gh pr view 456` |
| List PRs | `gh pr list` |
| Merge PR | `gh pr merge 456 --squash` (or --merge/--rebase) |
| Check PR status | `gh pr status` |

### Commit Message Guidelines

**CRITICAL:** Git commits must NOT include Claude attribution:
- ❌ NEVER add `Co-Authored-By: Claude` lines
- ❌ NEVER add `Generated with Claude Code`
- ✅ Commit messages should only describe the changes made
- ✅ Always use first-person voice (I/me) in commit messages and PR descriptions

This applies to ALL commits (regular commits, merge commits, etc.) and release notes.

### Release Strategy

**Release Notes Directory:**
- All release notes stored in `release_notes/` directory
- Filename format: `vX.Y.Z.md` or `vX.Y.Z-beta.N.md`
- Provides historical tracking of all releases
- Committed to git for version control

**Feature Branch:**
- Create BETA releases: `./scripts/release beta --notes release_notes/v2.7.0-beta.1.md --yes`
- Beta version format: `v2.7.0-beta.1`
- Mark as prerelease for testing

**Main Branch:**
- Create STABLE releases: `./scripts/release patch --notes release_notes/v2.7.0.md --yes`
- Only merge to main AFTER successful beta testing
- Stable version format: `v2.7.0`

**⚠️ CRITICAL:** Only create releases when explicitly requested by the user.
- ❌ NEVER create a release proactively
- ✅ ONLY create releases when user explicitly asks

## Testing

**For comprehensive testing documentation, see [docs/UNIT_TESTS.md](docs/UNIT_TESTS.md)**

For algorithm testing and visualization without Home Assistant, see [Manual Testing > Jupyter Notebook Testing](#jupyter-notebook-testing) section below.

### Running Tests

**IMPORTANT:** Always activate the virtual environment first:

```bash
# Activate virtual environment (REQUIRED)
source venv/bin/activate

# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_calculation.py -v

# Run with coverage
python -m pytest tests/ --cov=custom_components/adaptive_cover_pro --cov-report=term

# One-liner (activate + run)
source venv/bin/activate && python -m pytest tests/ -v
```

### Test Coverage Status

Current test count and per-module coverage are tracked in `HANDOFF.md` (updated each session). Target: 90%+ coverage for all calculation logic.

### When to Add Tests

**CRITICAL: Always add or update tests when making code changes.**

**Add new tests when:**
- Adding new features or functionality
- Adding new classes, methods, or functions
- Implementing new calculation logic
- Adding climate mode features

**Update existing tests when:**
- Changing calculation algorithms
- Modifying state determination logic
- Changing default behaviors
- Fixing bugs (add regression test)

**Testing checklist:**
1. ✅ Write tests for new code before committing
2. ✅ Ensure all tests pass: `source venv/bin/activate && python -m pytest tests/ -v`
3. ✅ Check coverage for modified files
4. ✅ Aim for 90%+ coverage for calculation logic
5. ✅ Follow existing patterns in test files

## Release Process

### Release Script: `./scripts/release`

The release script automates version management, git tagging, and GitHub release creation.

**Usage:**
```bash
./scripts/release [VERSION_SPEC] [OPTIONS]
```

### Version Specification

| Spec | Description | Example Result |
|------|-------------|----------------|
| `patch` | Increment patch version (X.Y.Z+1) | 2.6.8 → 2.6.9 |
| `minor` | Increment minor version (X.Y+1.0) | 2.6.8 → 2.7.0 |
| `major` | Increment major version (X+1.0.0) | 2.6.8 → 3.0.0 |
| `beta` | Auto-increment beta version | 2.6.8 → 2.6.9-beta.1<br>2.6.8-beta.1 → 2.6.8-beta.2 |
| `X.Y.Z` | Explicit version number | 2.7.0 (explicit) |
| `X.Y.Z-beta.N` | Explicit beta version | 2.7.0-beta.5 (explicit) |
| *(omitted)* | Interactive mode (prompts for choice) | User selects from menu |

**How `beta` works:**
- If current version is stable (e.g., `2.6.8`) → creates `2.6.9-beta.1` (next patch's first beta)
- If current version is beta (e.g., `2.6.9-beta.1`) → creates `2.6.9-beta.2` (increment beta number)

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--dry-run` | | Preview operations without executing any changes |
| `--yes` | `-y` | Skip all confirmation prompts (CI/automation mode) |
| `--notes FILE` | | Read release notes from specified file |
| `--auto-notes` | | Use auto-generated template notes only |
| `--force-branch` | | Skip branch validation (use with caution) |
| `--help` | `-h` | Show help text and exit |

**Note:** `--editor` option exists in script but should **NOT** be used per CLAUDE.md guidelines. Always use `--notes` parameter instead.

### Common Usage Patterns

**Standard workflow (recommended):**
```bash
# 1. Create release_notes directory if it doesn't exist
mkdir -p release_notes

# 2. Generate release notes (use version number in filename)
cat > release_notes/v2.6.11.md << 'EOF'
## 🎯 Release Title

### ✨ Features
- Feature 1

### 🐛 Bug Fixes
- Fix 1

### 🧪 Testing
- Tested with Python 3.11 and 3.12
- Home Assistant 2024.5.0+
EOF

# 3. Commit the release notes to git FIRST (release script requires clean working directory)
git add release_notes/v2.6.11.md
git commit -m "docs: Add release notes for v2.6.11"

# 4. Create release (handles version bump, tag, and push automatically)
./scripts/release patch --notes release_notes/v2.6.11.md --yes
```

**CRITICAL:** The release script requires a clean working directory. You MUST commit the release notes file before running the release script, not after. The release script will automatically:
- Update manifest.json version
- Create version bump commit
- Create annotated git tag
- Push both commits and tag to GitHub
- Create GitHub release with ZIP asset

**Quick beta release:**
```bash
# Auto-increment beta, skip confirmations, use auto-generated notes
./scripts/release beta --yes --auto-notes
```

**Preview before executing:**
```bash
# See what would happen without making changes
./scripts/release patch --dry-run
```

**Explicit version:**
```bash
# Use specific version number (create release notes first)
./scripts/release 2.7.0 --notes release_notes/v2.7.0.md --yes
```

**Interactive mode:**
```bash
# Select version type from menu
./scripts/release
```

### Release Notes Guidelines

**CRITICAL Rules:**
- **NEVER** include `Co-Authored-By:` lines
- **NEVER** include Claude/AI attributions
- **ALWAYS** use `--notes` parameter with versioned filename: `release_notes/vX.Y.Z.md`
- **NEVER** use `--editor` parameter
- **ALWAYS** commit release notes to git BEFORE running the release script (it requires a clean working directory)

**File Naming Convention:**
- Production releases: `release_notes/v2.6.11.md`
- Beta releases: `release_notes/v2.7.0-beta.1.md`
- Directory: `release_notes/` (tracked in git)

**Content Guidelines:**
- Use clear, user-friendly language
- Include emoji section headers (🎯, ✨, 🐛, 📚, 🧪)
- Document: Features, Bug Fixes, Documentation, Technical Details, Installation, Testing
- For beta releases: Include testing instructions and warnings

### Branch-Based Release Strategy

| Branch Type | Release Type | Command | Version Format |
|-------------|--------------|---------|----------------|
| `feature/*`, `fix/*` | Beta (prerelease) | `./scripts/release beta --notes release_notes/v2.7.0-beta.1.md --yes` | `v2.7.0-beta.1` |
| `main` | Stable (production) | `./scripts/release patch --notes release_notes/v2.7.0.md --yes` | `v2.7.0` |

**⚠️ WARNING:** If you find yourself creating a beta release from main, STOP! You should have created a feature branch first.

**Workflow:**
1. **Feature branch** → Create beta release → Test
2. **Merge to main** → Create stable release

### Error Handling and Rollback

The script includes automatic rollback on failure:
- Deletes created tags (local and remote)
- Resets commits if version bump was created
- Prevents partial releases

If a release fails:
1. Script automatically rolls back changes
2. Check error message for cause
3. Fix issue and retry
4. Use `--dry-run` to preview before retry

### Troubleshooting

**Common issues:**

| Issue | Cause | Solution |
|-------|-------|----------|
| "Working directory not clean" | Uncommitted changes | Commit or stash changes first |
| "Tag already exists" | Tag created previously | Delete tag or use different version |
| "GitHub CLI not authenticated" | Not logged into gh | Run `gh auth login` |
| "Production releases must be from main" | Wrong branch | Switch to main or use `--force-branch` (not recommended) |
| "ZIP asset not found" | Workflow failed | Check GitHub Actions logs |

**Check workflow status:**
```bash
gh run list --workflow=publish-release.yml
```

**View release:**
```bash
gh release view <version-tag>
```

## Documentation Guidelines

Always update docs alongside code changes:

| Changed | Update |
|---------|--------|
| User-visible feature | `README.md` (Features, Entities, Variables sections) |
| Development process | `docs/DEVELOPMENT.md` |
| VS Code testing | `docs/VSCODE_TESTING_GUIDE.md` |
| Feature in "Features Planned" | Mark as `~~Completed~~` in README, mention in release notes |

## Code Standards & Patterns

### Home Assistant Patterns

- **Async-first** - All I/O is async (state tracking, cover commands)
- **Never block the event loop** - Use async/await
- **Coordinator pattern** - Use for entity updates
- **Entity naming** - `{domain}.{type}_{description}_{name}`
- **Store data** - In `coordinator.data` for entity access
- **Update handler** - Use `_handle_coordinator_update()` in entities

### Adding a New Config Option

1. Add constant to `const.py` (`CONF_*`)
2. Add to `config_flow.py` (appropriate step schema + validation)
3. Read in `coordinator.py` from `self.config_entry.options`
4. Expose via sensor/switch if needed
5. Add translations to `translations/en.json` (and other languages if feasible)
6. Update `CLAUDE.md` Configuration Structure section

**Optional numeric fields:** Use `NumberSelectorMode.SLIDER` (not `BOX`) with `vol.Optional` and no default. BOX mode does not preserve `None` — clearing the field saves `0`. Sliders correctly return `None` when empty. Code consuming the value must check `if value is not None` (not `if value`) to distinguish 0% from unset.

### Diagnostic Sensor Guidelines

When creating diagnostic sensors:

```python
class MyDiagnosticSensor(AdaptiveCoverDiagnosticSensor):
    """Diagnostic sensor description."""

    # For text/status sensors: empty unit excludes from logbook
    _attr_native_unit_of_measurement = ""  # Prevents activity log entries

    # For numeric sensors: MUST have proper unit for statistics
    _attr_native_unit_of_measurement = "retries"  # Enables statistics tracking

    # All diagnostic sensors are enabled by default (no enable_diagnostics toggle)
    _attr_entity_registry_enabled_default = True
```

**Rules:**
- ✅ Text/status sensors → empty unit `""` to exclude from logbook
- ✅ Numeric sensors → proper unit (`"retries"`, `"°"`, `PERCENTAGE`) for statistics
- ✅ History is still recorded for debugging
- ❌ Don't use empty unit for numeric sensors (breaks statistics)

### Motion Control Pattern

**Added in v2.7.5** - Occupancy-based automatic control with debouncing.

Motion control enables/disables automatic sun positioning based on room occupancy using binary motion sensors.

**Key Design:**
- **OR Logic** - ANY sensor detecting motion enables automatic positioning
- **Debounce "no motion" only** - Immediate response when motion detected, timeout when motion stops
- **Priority**: Force override (safety) > Motion timeout > Manual override
- **Asyncio task-based** - `_start_motion_timeout()` / `_cancel_motion_timeout()` / `_motion_timeout_handler()` in `coordinator.py`

**Use Cases:**
- **Glare control when present** - Use sun positioning when someone is in the room
- **Energy savings when away** - Return to default (closed) when room is empty
- **Privacy when unoccupied** - Close covers automatically after no motion
- **Multi-room coverage** - OR logic means ANY room with motion uses automatic

**Testing:**
- See `tests/test_motion_control.py` for 22 comprehensive test cases
- Tests cover OR logic, debouncing, priority, edge cases, shutdown cleanup

**Edge Cases Handled:**
- Unavailable sensors treated as "off" (no motion)
- Empty sensor list disables feature (backward compatible)
- Double-check prevents false timeout if motion detected during sleep
- Cleanup cancels task on shutdown or config change

### Inverse State Behavior

**CRITICAL: Do Not Change This Behavior**

The `inverse_state` feature handles covers that don't follow Home Assistant guidelines (0=closed, 100=open):

**For Position-Capable Covers:**
- Calculated position is inverted: `state = 100 - state`
- Inverted position is sent to the cover entity

**For Open/Close-Only Covers:**
- Calculated position is inverted: `state = 100 - state`
- Inverted position is compared to threshold

**Code Flow:**
1. Calculate position
2. Invert (if enabled and interpolation not used)
3. Apply threshold (for open/close-only covers)

**NEVER:**
- Change the order of inverse_state application and threshold checking
- Skip inverse_state for open/close-only covers when enabled
- Apply inverse_state after the threshold check

### Enhanced Geometric Accuracy

**Added in v2.7.0** to fix Issue #1 (shadow calculation accuracy at extreme angles).

The integration includes sophisticated geometric enhancements to ensure accurate sun blocking across all sun positions. These improvements are implemented in `AdaptiveVerticalCover.calculate_position()` in `calculation.py`.

#### Implementation Details

1. **Edge Case Handling** (`_handle_edge_cases()`)
   - Elevation < 2°: Returns full window coverage (h_win)
   - |Gamma| > 85°: Returns full window coverage (h_win)
   - Elevation > 88°: Uses simplified calculation (distance × tan(elevation))
   - Returns tuple: `(is_edge_case: bool, position: float)`

2. **Safety Margins** (`_calculate_safety_margin()`)
   - Returns multiplier ≥1.0 to increase blind extension
   - Gamma margin: Smoothstep interpolation from 1.0 (at 45°) to 1.2 (at 90°)
   - Low elevation margin: Linear from 1.0 (at 10°) to 1.15 (at 0°)
   - High elevation margin: Linear from 1.0 (at 75°) to 1.1 (at 90°)
   - Margins combine multiplicatively (margin = gamma_margin × elev_margin)

3. **Window Depth Parameter** (optional) — `window_depth` field on `AdaptiveVerticalCover` (default 0.0), configured via `CONF_WINDOW_DEPTH` (0.0–0.5m). When `window_depth > 0` and `|gamma| > 10°`, adds horizontal offset `depth_contribution = window_depth × sin(|gamma|)` to account for window reveals/frames.

4. **Sill Height Parameter** (optional) — `sill_height` field on `AdaptiveVerticalCover` (default 0.0), configured via `CONF_SILL_HEIGHT` (0.0–3.0m). For windows not starting at floor level, the sill provides natural sun blocking: sunlight entering at the sill height cannot reach closer than `sill_height / tan(elevation)` meters from the wall. This reduces the `effective_distance` needed, raising the blind. Not applicable to horizontal awnings or tilt covers.

5. **Calculation Flow** — edge cases → window depth offset → sill height offset → base calculation → safety margin → clip to h_win. See `calculate_position()` in `calculation.py`.

#### Testing Requirements

When modifying geometric accuracy calculations:

1. **Run existing tests** to ensure no regression:
   ```bash
   source venv/bin/activate
   python -m pytest tests/test_geometric_accuracy.py -v
   ```

2. **Test coverage requirements:**
   - Safety margin behavior at all angle ranges
   - Edge case handling at thresholds
   - Smooth transitions across ranges
   - Regression tests (<5% deviation at normal angles)
   - Backward compatibility (window_depth=0 produces identical results)

3. **Key test files:**
   - `tests/test_geometric_accuracy.py` - 34 dedicated tests
   - `tests/test_calculation.py` - Integration with existing calculation tests

#### Modification Guidelines

**DO:**
- Test all changes with full test suite (run `source venv/bin/activate && python -m pytest tests/ -v`)
- Maintain backward compatibility (existing installations unaffected)
- Keep safety margins conservative (always ≥ baseline position)
- Use smoothstep interpolation for smooth transitions
- Document changes in commit messages and release notes

**DON'T:**
- Remove or reduce safety margins without thorough testing
- Change edge case thresholds without testing transitions
- Break backward compatibility (window_depth and sill_height must default to 0.0)
- Introduce numerical instability (NaN, infinity)
- Skip regression testing at normal angles

#### Diagnostic Information

Users can monitor geometric accuracy via the always-on diagnostic sensors:
- **Calculated Position** — raw position; `position_explanation` attribute shows full decision chain
- **Sun Position** — state is azimuth; attributes include `sun_elevation` and `gamma`
- Compare "Calculated Position" to actual cover position to see safety margin effects

## Configuration Structure

**`config_entry.data`** (setup phase):
- `name` - Instance name
- `sensor_type` - cover_blind/cover_awning/cover_tilt

**`config_entry.options`** (configurable):
- Window azimuth, field of view, elevation limits
- Cover-specific dimensions (height, length, slat properties)
- Sunset behavior:
  - `sunset_position` - Optional position after sunset (None = use `default_percentage`). Use SLIDER in config_flow, not BOX.
  - `sunset_offset` / `sunrise_offset` — minutes to shift sunset/sunrise times
- Enhanced geometric accuracy:
  - `window_depth` - Optional window reveal/frame depth (0.0-0.5m, default 0.0)
  - `sill_height` - Optional height from floor to window bottom (0.0-3.0m, default 0.0). Raises the blind for windows above floor level — accounting for the sill's geometric effect of reducing sun penetration into the room
  - Safety margins and edge case handling apply automatically (not configurable)
- Position limits:
  - `min_position` / `max_position` - Absolute position boundaries (0-99%, 1-100%)
  - `enable_min_position` / `enable_max_position` - When limits apply:
    - False (default): Limits always enforced
    - True: Limits only during direct sun tracking
- Automation settings (delta position/time, start/end times, manual override)
- Force override settings:
  - `force_override_sensors` - Optional list of binary sensor entity IDs that globally disable automatic control when any sensor is "on"
  - `force_override_position` - Position (0-100%) to move covers to when force override is active (default: 0%)
- Motion control settings:
  - `motion_sensors` - Optional list of binary sensor entity IDs for occupancy-based control. When ANY sensor detects motion, covers use automatic positioning. When ALL sensors show no motion for timeout duration, covers return to default position. Empty list = feature disabled (default: [])
  - `motion_timeout` - Duration in seconds to wait after last motion before using default position. Debounces rapid sensor toggling (range: 30-3600, default: 300)
- Climate settings (temperature entities/thresholds, presence, weather)
- Light settings (lux/irradiance entities and thresholds)
- Blind spot areas

## Manual Testing

### Live Home Assistant Testing

Use `./scripts/develop` to start a development instance with the integration loaded:

```bash
./scripts/develop
```

**What it does:**
- Creates `config/` directory if not present
- Sets `PYTHONPATH` to include `custom_components/`
- Starts Home Assistant with debug logging
- Loads the integration automatically
- Uses `config/configuration.yaml` for test setup with mock entities

**Access:**
- Home Assistant UI: http://localhost:8123
- Logs: Real-time debug output in terminal
- Changes: Python file changes require restart to take effect

### Jupyter Notebook Testing

`notebooks/test_env.ipynb` — test calculation algorithms without a full HA instance. Produces visual plots of vertical and horizontal cover positions over 24 hours.

**Setup:** `./scripts/setup` installs Jupyter. Open with `jupyter notebook` or in VS Code (`code notebooks/test_env.ipynb`, requires Jupyter extension).

**Update notebook when:** dataclass signatures change in `calculation.py` or `SunData` API changes in `sun.py`. Verify by running all cells and confirming two plots appear with no errors.

**Troubleshooting:**
- `ModuleNotFoundError: adaptive_cover_pro` → ensure `sys.path.append("../custom_components")` is in first cell
- `TypeError: missing argument 'logger'` → add `MockedLogger` no-op class, pass `logger=mocked_logger` to constructors
- Plot not appearing (Jupyter) → add `%matplotlib inline` to first cell
- `SunData` unexpected kwarg → use positional args: `SunData(timezone, mocked_hass)`

### Simulation Tools

`custom_components/adaptive_cover_pro/simulation/sim_plot.png` — example plot. Regenerate by uncommenting cells 5-14 in `test_env.ipynb`.

### Development Workflow

Algorithm changes: edit `calculation.py` → validate visually in notebook → add unit tests → test live with `./scripts/develop`.

## File Organization

```
adaptive-cover/
├── custom_components/adaptive_cover_pro/
│   ├── __init__.py              # Integration entry point
│   ├── coordinator.py           # Data coordinator (primary hub)
│   ├── calculation.py           # Position calculation engine
│   ├── config_flow.py           # Configuration UI
│   ├── sensor.py                # Sensor platform
│   ├── switch.py                # Switch platform
│   ├── binary_sensor.py         # Binary sensor platform
│   ├── button.py                # Button platform
│   ├── sun.py                   # Solar calculations
│   ├── helpers.py               # Utility functions
│   ├── const.py                 # Constants
│   ├── manifest.json            # Integration metadata
│   └── translations/            # i18n files (13 languages)
├── scripts/
│   ├── setup                    # Development environment setup
│   ├── develop                  # Start Home Assistant dev server
│   ├── lint                     # Run linting
│   └── release                  # Create releases (automated)
├── tests/                       # Unit tests (410 tests)
│   ├── conftest.py              # Shared fixtures
│   ├── test_calculation.py      # Core calculation tests
│   ├── test_geometric_accuracy.py
│   ├── test_helpers.py
│   ├── test_inverse_state.py
│   ├── test_motion_control.py
│   ├── test_startup_grace_period.py
│   ├── test_force_override_sensors.py
│   ├── test_control_state_reason.py
│   ├── test_interpolation.py
│   ├── test_delta_position.py
│   ├── test_manual_override.py
│   └── test_position_explanation.py
├── release_notes/               # Historical release notes
│   ├── README.md                # Release notes documentation
│   └── vX.Y.Z.md                # Individual release notes (versioned)
├── docs/                        # Documentation directory
│   ├── ARCHITECTURE.md          # Architecture documentation
│   ├── CONTRIBUTING.md          # Contributing guidelines
│   ├── DEVELOPMENT.md           # Developer documentation
│   ├── UNIT_TESTS.md            # Unit test documentation
│   └── VSCODE_TESTING_GUIDE.md  # VS Code testing guide
├── CLAUDE.md                    # Claude Code instructions (this file)
├── README.md                    # User documentation
└── pyproject.toml               # Python project configuration
```

## Dependencies

**Production:**
- `homeassistant~=2024.5` - Core framework
- `pandas~=2.2` - Solar data calculations
- `astral` - Sun position/timing

**Development:**
- `ruff~=0.4` - Linting and formatting
- `pre-commit~=3.7` - Git hooks
- `pytest` - Testing framework
- `pvlib~=0.11` - Photovoltaic simulations
- `matplotlib~=3.9` - Plotting for simulations

## Cover Types and Operation Modes

### Cover Types
- **Vertical** (`cover_blind`) - Up/down movement
- **Horizontal** (`cover_awning`) - In/out movement
- **Tilt** (`cover_tilt`) - Slat rotation

### Operation Modes
- **Basic Mode** - Sun position-based calculation only
- **Climate Mode** - Enhanced with temperature, presence, weather
  - Winter strategy: Open fully when cold and sunny
  - Summer strategy: Close fully when hot
  - Intermediate: Use calculated position with weather awareness

## Additional Notes

### Manual Override Detection
The integration tracks when users manually change cover positions:
- Compares actual position to calculated position
- Configurable threshold and duration
- Option to reset timer on subsequent changes
- Reset button available to clear override status

### Climate Mode Weather States
Default sunny weather states: `sunny`, `windy`, `partlycloudy`, `cloudy`
- Configurable in weather options
- Used to determine if calculated position should be used vs. default

### Ruff Configuration
Configured in `pyproject.toml`:
- `select = ["ALL"]` - Enable all rules by default
- Specific ignores for formatter conflicts
- Home Assistant import conventions (cv, dr, er, ir, vol)
- Force sorting within sections for imports
