# VS Code Debugging Environment - Testing Guide

This guide walks you through testing the VS Code debugging setup for Adaptive Cover Pro.

## Prerequisites Check

### 1. Verify Home Assistant Installation

Home Assistant is already installed in your venv. Verify it's working:

```bash
source venv/bin/activate
python -c "import homeassistant; print(f'Home Assistant {homeassistant.__version__}')"
```

**Expected output:** `Home Assistant 2026.1.3` (or similar)

### 2. Verify Development Dependencies

```bash
source venv/bin/activate
python -m pytest --version  # Should show pytest version
ruff --version              # Should show ruff version
```

If anything is missing, run:

```bash
./scripts/setup
```

### 3. Reopen VS Code

**IMPORTANT:** Close and reopen VS Code to load the new configuration:

```bash
# Close VS Code completely, then:
code .
```

### 4. Install Recommended Extensions

When VS Code opens, you should see a notification:

> **This workspace has extension recommendations**

Click **"Show Recommendations"** and install:

- **ms-python.python** - Core Python support ✓
- **ms-python.vscode-pylance** - IntelliSense ✓
- **ms-python.debugpy** - Python debugger ✓
- **charliermarsh.ruff** - Linting and formatting ✓

Or install via command line:

```bash
code --install-extension ms-python.python
code --install-extension ms-python.vscode-pylance
code --install-extension ms-python.debugpy
code --install-extension charliermarsh.ruff
code --install-extension keesschollaart.vscode-home-assistant
code --install-extension redhat.vscode-yaml
code --install-extension esbenp.prettier-vscode
```

## Testing Checklist

### ✅ Test 1: Python Interpreter Selection

**Goal:** Verify VS Code is using the correct Python interpreter

**Steps:**
1. Open Command Palette (Cmd+Shift+P / Ctrl+Shift+P)
2. Type "Python: Select Interpreter"
3. Verify `./venv/bin/python` is selected (should have a checkmark)
4. Look at bottom-right status bar - should show Python version from venv

**Expected Result:** Status bar shows `Python 3.11.x ('venv': venv)` or similar

**Troubleshooting:**
- If venv not listed, reload window: Cmd+Shift+P → "Developer: Reload Window"
- If venv still not showing, run `./scripts/setup` to recreate it

---

### ✅ Test 2: Terminal Integration

**Goal:** Verify PYTHONPATH and venv auto-activation

**Steps:**
1. Open integrated terminal (Cmd+\` / Ctrl+\`)
2. Check prompt shows `(venv)` prefix
3. Run: `echo $PYTHONPATH`
4. Run: `python -c "from custom_components.adaptive_cover_pro import coordinator; print('Import successful')"`

**Expected Results:**
- Prompt: `(venv) user@machine adaptive-cover %`
- PYTHONPATH includes: `.../custom_components`
- Import succeeds without errors

**Troubleshooting:**
- If venv not activated: Check `.vscode/settings.json` has `python.terminal.activateEnvironment: true`
- If PYTHONPATH empty: Close terminal and open new one
- On Windows: Terminal env vars work differently - PYTHONPATH may not be set (this is normal)

---

### ✅ Test 3: Ruff Linting (Inline Errors)

**Goal:** Verify Ruff is highlighting errors inline

**Steps:**
1. Open `custom_components/adaptive_cover_pro/coordinator.py`
2. Add a deliberate error on a new line: `import this_module_does_not_exist`
3. Save the file (Cmd+S / Ctrl+S)
4. Look for red squiggly underline under the import
5. Open Problems panel (Cmd+Shift+M / Ctrl+Shift+M)
6. Verify error appears: "Unable to resolve 'this_module_does_not_exist'"
7. Delete the bad import and save

**Expected Results:**
- Red squiggly underline appears immediately
- Error shows in Problems panel
- Hovering shows error message
- Error disappears after deleting and saving

**Troubleshooting:**
- No squiggly: Check Ruff extension is installed and enabled
- Check Output → Ruff for error logs
- Verify `.vscode/settings.json` has `"ruff.enable": true`

---

### ✅ Test 4: Format on Save

**Goal:** Verify code auto-formats when saving

**Steps:**
1. Open `custom_components/adaptive_cover_pro/coordinator.py`
2. Mess up formatting on a line:
   ```python
   # Change this (properly formatted):
   _LOGGER.debug("Updating Adaptive Cover data")

   # To this (badly formatted):
   _LOGGER.debug(    "Updating Adaptive Cover data"   )
   ```
3. Save file (Cmd+S / Ctrl+S)
4. Watch the line auto-format back to proper spacing

**Expected Result:** Line is automatically reformatted to remove extra spaces

**Troubleshooting:**
- Not formatting: Check `.vscode/settings.json` has `"editor.formatOnSave": true` under `[python]`
- Wrong formatter: Check `"editor.defaultFormatter": "charliermarsh.ruff"` is set
- Verify Ruff extension is installed and activated

---

### ✅ Test 5: Test Discovery

**Goal:** Verify Test Explorer finds all 172 tests

**Steps:**
1. Open Test Explorer (click beaker icon in left sidebar)
2. Wait 10-20 seconds for test discovery
3. Expand `tests` folder
4. Count test files: should see `test_calculation.py`, `test_helpers.py`, `test_inverse_state.py`
5. Expand `test_calculation.py` - should show ~129 tests

**Expected Results:**
- Test Explorer shows folder tree with all test files
- Total of 172 tests discovered
- Tests organized by file and function

**Troubleshooting:**
- "No tests discovered":
  - Check Python interpreter is `venv/bin/python`
  - Open Command Palette → "Python: Discover Tests"
  - Check Output → Python Test Log for errors
- Slow discovery: This is normal for large test suites (wait 30 seconds)
- Missing tests: Run `source venv/bin/activate && python -m pytest --collect-only` in terminal

---

### ✅ Test 6: Run Single Test from Test Explorer

**Goal:** Run an individual test via UI

**Steps:**
1. Open Test Explorer
2. Navigate to `test_calculation.py` → expand
3. Find test `test_gamma_angle`
4. Click the ▶️ play icon next to it
5. Watch test run in terminal at bottom
6. Verify ✓ checkmark appears when test passes

**Expected Results:**
- Test runs in integrated terminal
- Output shows: `test_calculation.py::test_gamma_angle PASSED`
- Green checkmark appears in Test Explorer

**Troubleshooting:**
- Test fails: This is a known good test, so check venv has correct dependencies
- No output: Check test panel at bottom is visible
- Timeout: Increase pytest timeout in settings

---

### ✅ Test 7: Debug Single Test with Breakpoint

**Goal:** Hit a breakpoint in a test

**Steps:**
1. Open `tests/test_calculation.py`
2. Find the `test_gamma_angle` function (around line 20-30)
3. Click in the gutter (left of line numbers) inside the test function to set a breakpoint
   - Red dot should appear
4. Right-click the test in Test Explorer
5. Select **"Debug Test"**
6. Debugger should start and pause at your breakpoint
7. In Debug panel (left sidebar), expand **Variables** → inspect `vertical_cover`
8. Press **F5** to continue, test should complete

**Expected Results:**
- Execution pauses at breakpoint
- Variables panel shows test fixtures (`hass`, `coordinator`, etc.)
- Can step through with F10, F11
- Test completes successfully after F5

**Troubleshooting:**
- Breakpoint not hit:
  - Verify red dot is solid (not gray/hollow)
  - Check Python interpreter is from venv
  - Try "Debug Current Test File" from debug dropdown instead
- Can't see variables: Click "Variables" dropdown in Debug panel
- Debugger hangs: Check for infinite loops, use Shift+F5 to stop

---

### ✅ Test 8: Debug Configuration - "Debug Current Test File"

**Goal:** Test context-aware test file debugging

**Steps:**
1. Open `tests/test_helpers.py`
2. Set a breakpoint in any test (e.g., `test_sun_path_no_end`)
3. Make sure this file is active/focused
4. Press **F5** (or click Run and Debug icon)
5. Select **"Debug Current Test File"** from dropdown
6. Debugger should run all tests in this file and hit your breakpoint

**Expected Results:**
- Debugger launches automatically
- Runs all tests in `test_helpers.py`
- Pauses at your breakpoint
- Shows test file path in debug console

**Troubleshooting:**
- Wrong file debugged: Make sure `test_helpers.py` is the active editor tab
- Can't find config: Check `.vscode/launch.json` exists
- No breakpoint hit: Verify breakpoint is in a test that runs (not skipped)

---

### ✅ Test 9: Debug Configuration - "Debug Home Assistant"

**Goal:** Debug the running Home Assistant integration

**Steps:**
1. Open `custom_components/adaptive_cover_pro/coordinator.py`
2. Find the `_async_update_data` function (around line 337)
3. Set a breakpoint on the line: `_LOGGER.debug("Updating Adaptive Cover data")`
4. Press **F5**
5. Select **"Debug Home Assistant"** from dropdown
6. **Note:** Any running HA instance is automatically stopped first
7. Wait for Home Assistant to start (takes 30-60 seconds)
8. Watch debug console for startup logs
9. **Trigger a coordinator update** (happens automatically every few minutes, or change a sensor value)
10. Breakpoint should be hit

**Expected Results:**
- If HA was running, you'll see "Stopping Home Assistant gracefully..."
- Home Assistant starts at http://localhost:8123
- Debug console shows HA logs
- Breakpoint is hit during coordinator update
- Can inspect `self._config_entry`, `self._cover`, etc.

**Troubleshooting:**
- HA won't start:
  - Check `config/configuration.yaml` exists
  - Look for errors in debug console
  - Try `./scripts/stop` to manually stop any stuck instances
- Breakpoint not hit:
  - Coordinator updates are infrequent - wait a few minutes
  - Or trigger manually by changing sun position (if configured)
  - Check breakpoint is solid red (not hollow)
- Can't access localhost:8123:
  - This is normal - you're debugging, not using the UI
  - Focus is on code debugging, not web interface

---

### ✅ Test 10: Debug Configuration - "Debug Specific Test"

**Goal:** Debug tests by name pattern

**Steps:**
1. Press **F5**
2. Select **"Debug Specific Test"**
3. When prompted, enter: `gamma`
4. Debugger runs all tests with "gamma" in the name
5. Set a breakpoint in `test_gamma_angle` before running
6. Should hit breakpoint

**Expected Results:**
- Input prompt appears for test pattern
- Only matching tests run (e.g., `test_gamma_angle`, `test_gamma_integration`)
- Breakpoint is hit if set
- Non-matching tests are skipped

**Troubleshooting:**
- No prompt: Check `.vscode/launch.json` has `inputs` section
- No tests match: Try broader pattern like "vertical" or "test_"
- All tests run: Pattern might be too broad (like "test")

---

### ✅ Test 11: Development Task - "Lint with Ruff"

**Goal:** Run linting via task runner with problem matcher

**Steps:**
1. Open Command Palette (Cmd+Shift+P)
2. Type "Tasks: Run Task"
3. Select **"Lint with Ruff"**
4. Wait for task to complete
5. Check terminal output for linting results
6. If errors exist, click on file:line links to navigate

**Expected Results:**
- Task runs in integrated terminal
- Shows linting results (hopefully "All checks passed!")
- File paths are clickable links
- Problems panel (Cmd+Shift+M) shows errors

**Troubleshooting:**
- Command not found: Run `./scripts/setup` to install ruff in venv
- No output: Check terminal panel is visible at bottom
- Can't click links: This is normal in some terminals - use Problems panel instead

---

### ✅ Test 12: Development Task - "Run All Tests"

**Goal:** Run full test suite via task

**Steps:**
1. Command Palette → "Tasks: Run Task"
2. Select **"Run All Tests"**
3. Watch all 172 tests execute
4. Verify all tests pass (may take 30-60 seconds)

**Expected Results:**
- All tests run with verbose output
- Shows: `172 passed in X.XXs`
- Green success message at end

**Troubleshooting:**
- Tests fail:
  - Check venv dependencies are installed: `./scripts/setup`
  - Run tests manually: `source venv/bin/activate && python -m pytest tests/ -v`
- Task not found: Check `.vscode/tasks.json` exists
- Timeout: This is normal for large suites, wait 2-3 minutes

---

### ✅ Test 13: Development Task - "Run Tests with Coverage"

**Goal:** Generate HTML coverage report

**Steps:**
1. Run Task → **"Run Tests with Coverage"**
2. Wait for completion
3. Terminal shows coverage report with percentages
4. Open `htmlcov/index.html` in browser (right-click → Open with Live Server, or just open in browser)
5. Browse coverage by file

**Expected Results:**
- Coverage report shows in terminal
- HTML report generated in `htmlcov/` folder
- Can view detailed coverage in browser
- Shows 28% overall coverage (as documented)

**Troubleshooting:**
- No HTML report: Check terminal for errors
- Can't find htmlcov/: Run `ls -la htmlcov/` to verify it exists
- Coverage seems wrong: Clear `.coverage` file and re-run

---

### ✅ Test 14: Development Task - "Format Code"

**Goal:** Auto-format all Python files

**Steps:**
1. Make some formatting errors in a file
2. Run Task → **"Format Code"**
3. Watch terminal output
4. Check files are reformatted

**Expected Results:**
- Task runs ruff format on all files
- Terminal shows "X files reformatted" or "All files already formatted"
- Files are properly formatted

**Troubleshooting:**
- No changes: Files were already formatted (this is good!)
- Errors: Check ruff is installed in venv

---

### ✅ Test 15: Auto-Import Organization

**Goal:** Verify imports are automatically organized on save

**Steps:**
1. Open `custom_components/adaptive_cover_pro/coordinator.py`
2. Mess up import order at top of file:
   ```python
   # Move an import out of order, like move:
   from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
   # Above:
   from homeassistant.core import HomeAssistant
   ```
3. Save file (Cmd+S)
4. Watch imports automatically reorganize

**Expected Results:**
- Imports are automatically sorted into correct order
- Standard library → third party → local imports
- Alphabetically sorted within each group

**Troubleshooting:**
- Not organizing: Check `.vscode/settings.json` has `"source.organizeImports": "explicit"` in `codeActionsOnSave`
- Wrong order: Ruff's import sorting follows PEP 8 conventions

---

## Advanced Testing

### Test Conditional Breakpoints

1. Open `custom_components/adaptive_cover_pro/calculation.py`
2. Find the `calculate_position` method
3. Set a breakpoint
4. Right-click breakpoint → **"Edit Breakpoint"** → **"Expression"**
5. Enter condition: `self._window_azimuth == 180`
6. Run "Debug All Tests"
7. Breakpoint only hits when condition is true

### Test Logpoints

1. In same file, right-click gutter → **"Add Logpoint"**
2. Enter message: `Position: {position}, Azimuth: {self._window_azimuth}`
3. Run tests - messages appear in Debug Console without pausing

### Test Watch Expressions

1. During debugging session, go to Debug panel
2. Click **"Watch"** section
3. Click **+** to add watch expression
4. Enter: `self._config_entry.options`
5. Watch value change as you step through code

### Test Exception Breakpoints

1. In Debug panel, click **"Breakpoints"** section
2. Check **"Raised Exceptions"**
3. Run tests - debugger breaks on any exception raised
4. Useful for finding silent failures

---

## Verification Checklist

After completing all tests, verify:

- [ ] Python interpreter is `venv/bin/python`
- [ ] Terminal auto-activates venv (shows `(venv)` prompt)
- [ ] PYTHONPATH includes `custom_components/`
- [ ] Ruff shows inline errors (red squiggles)
- [ ] Format on save works (auto-formats Python files)
- [ ] Test Explorer discovers 172 tests
- [ ] Can run individual tests from Test Explorer
- [ ] Can debug tests with breakpoints
- [ ] Can debug Home Assistant with breakpoints
- [ ] Tasks run successfully (lint, test, format)
- [ ] Problems panel shows clickable errors
- [ ] Auto-import organization works on save

---

## Common Issues and Solutions

### Issue: "No module named 'custom_components'"

**Solution:**
```bash
# Verify PYTHONPATH in debug config
# Check .vscode/launch.json has:
"env": {
  "PYTHONPATH": "${workspaceFolder}/custom_components"
}

# Or set manually in terminal:
export PYTHONPATH="${PWD}/custom_components"
```

### Issue: "Debugger is slow or hangs"

**Solution:**
- Use `--no-cov` flag (already configured)
- Set `"justMyCode": true` if you don't need to step into HA core
- Debug specific tests instead of full suite
- Check for infinite loops in code

### Issue: "Breakpoints show as gray/hollow circles"

**Solution:**
- Verify Python interpreter is from venv
- Check file is saved
- Reload window: Cmd+Shift+P → "Developer: Reload Window"
- Rebuild: Delete `__pycache__/` and `.pytest_cache/`

### Issue: "Test discovery doesn't find tests"

**Solution:**
```bash
# Verify pytest works manually:
source venv/bin/activate
python -m pytest --collect-only

# Check test configuration:
# Command Palette → "Python: Configure Tests"
# Select pytest
# Select tests/ directory

# Force rediscovery:
# Command Palette → "Python: Discover Tests"
```

### Issue: "Format on save not working"

**Solution:**
- Install Ruff extension: `code --install-extension charliermarsh.ruff`
- Check settings: `.vscode/settings.json` → `"[python]"` → `"editor.formatOnSave": true`
- Reload window
- Try manual format: Right-click → "Format Document"

### Issue: "Home Assistant won't start in debugger"

**Solution:**
```bash
# Check config exists:
ls config/configuration.yaml

# Check port is free:
lsof -i :8123  # Should be empty

# Check HA can start manually:
./scripts/develop

# Check debug console for specific errors
```

---

## Success Criteria

Your VS Code debugging environment is fully working when:

✅ **All 15 tests pass** from the checklist above
✅ **Verification checklist is complete** (all items checked)
✅ **No errors in Output panels** (Python, Ruff, Test Log)
✅ **Can debug both tests and Home Assistant** with breakpoints
✅ **Code quality tools work automatically** (lint, format on save)

---

## Next Steps

Once testing is complete:

1. **Use the debugger** - Set breakpoints and step through code during development
2. **Leverage Test Explorer** - Run/debug individual tests as you write them
3. **Let formatting happen automatically** - Just save files, Ruff handles the rest
4. **Run tasks regularly** - Lint before commits, check coverage periodically
5. **Customize as needed** - Add your own debug configs, tasks, or settings

Refer to the **[Developer Debugging](https://github.com/jrhubott/adaptive-cover-pro/wiki/Developer-Debugging)** wiki page for comprehensive debugging documentation, advanced tips, and troubleshooting.

---

## Automatic Instance Management

The development environment automatically manages Home Assistant instances to prevent conflicts.

### Auto-Stop Feature

Both `./scripts/develop` and the VS Code "Debug Home Assistant" configuration automatically stop any running Home Assistant instances before starting a new one.

**How it works:**
1. Detects running HA processes using your config directory
2. Sends graceful shutdown signal (SIGTERM)
3. Waits up to 10 seconds for clean shutdown
4. Force kills if necessary
5. Starts new instance

**Manual control:**

```bash
# Stop all running HA instances
./scripts/stop

# Start HA (stops any running instances first)
./scripts/develop
```

**Benefits:**
- No more "Another Home Assistant instance is already running" errors
- Clean restarts every time
- Safe for debugging - preserves log files and state

### Managing Stuck Instances

If Home Assistant becomes unresponsive:

```bash
# Force stop all instances
./scripts/stop

# Verify no processes running
ps aux | grep "[h]ass --config"

# Start fresh
./scripts/develop
```

---

## Quick Reference

| Action | Shortcut |
|--------|----------|
| Start Debugging | **F5** |
| Toggle Breakpoint | **F9** |
| Step Over | **F10** |
| Step Into | **F11** |
| Continue | **F5** |
| Stop Debugging | **Shift+F5** |
| Run Task | **Cmd+Shift+P** → Tasks: Run Task |
| Test Explorer | Click beaker icon in sidebar |
| Problems Panel | **Cmd+Shift+M** |
| Command Palette | **Cmd+Shift+P** |
| Stop Home Assistant | `./scripts/stop` |

Happy debugging! 🐛🔍
