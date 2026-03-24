# Duplicate & Sync Covers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add duplicate-from-existing to the "Add Integration" flow and sync-to-others to the Options flow, sharing a common `_extract_shared_options` helper.

**Architecture:** A module-level helper `_extract_shared_options(entry)` returns a copy of all options except `CONF_ENTITIES`, `CONF_AZIMUTH`, and `CONF_DEVICE_ID`. The duplicate flow adds two steps (`duplicate_select`, `duplicate_configure`) to `ConfigFlowHandler`. The sync flow adds two steps (`sync`, `sync_confirm`) to `OptionsFlowHandler`. Both features use the same helper.

**Tech Stack:** Python 3.11, Home Assistant ConfigFlow/OptionsFlow, voluptuous schemas, HA selector library, pytest with unittest.mock.

---

## File Map

| File | Change |
|------|--------|
| `custom_components/adaptive_cover_pro/config_flow.py` | Add helper, new init vars, 4 new step methods, update user/init menus, update `_ensure_unique_name` |
| `custom_components/adaptive_cover_pro/translations/en.json` | Add step/menu/abort translation keys |
| `tests/test_duplicate_sync.py` | New test file for helper and flow logic |

---

## Task 1: Create Feature Branch

**Files:** none

- [ ] **Step 1: Create and checkout feature branch**

```bash
git checkout main
git pull origin main
git checkout -b feature/issue-54-duplicate-sync-covers
```

Expected: new branch `feature/issue-54-duplicate-sync-covers` checked out.

---

## Task 2: Add `_extract_shared_options` Helper + Unit Tests (TDD)

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_flow.py` (after line 581, before `ConfigFlowHandler`)
- Create: `tests/test_duplicate_sync.py`

- [ ] **Step 1: Write failing tests for `_extract_shared_options`**

Create `tests/test_duplicate_sync.py`:

```python
"""Tests for duplicate and sync cover features."""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_flow import _extract_shared_options
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_DELTA_POSITION,
    CONF_DEVICE_ID,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENTITIES,
    CONF_HEIGHT_WIN,
    CONF_MIN_POSITION,
    CONF_MOTION_SENSORS,
)


def _make_entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.options = options
    return entry


class TestExtractSharedOptions:
    """Tests for _extract_shared_options."""

    def test_excludes_entities(self):
        entry = _make_entry({CONF_ENTITIES: ["cover.test"], CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_ENTITIES not in result

    def test_excludes_azimuth(self):
        entry = _make_entry({CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_AZIMUTH not in result

    def test_excludes_device_id(self):
        entry = _make_entry({CONF_DEVICE_ID: "abc123", CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_DEVICE_ID not in result

    def test_includes_window_dimensions(self):
        entry = _make_entry({CONF_HEIGHT_WIN: 2.1, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_HEIGHT_WIN] == 2.1

    def test_includes_automation_settings(self):
        entry = _make_entry({CONF_DELTA_POSITION: 5, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_DELTA_POSITION] == 5

    def test_includes_climate_mode(self):
        entry = _make_entry({CONF_CLIMATE_MODE: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_CLIMATE_MODE] is True

    def test_includes_position_limits(self):
        entry = _make_entry({CONF_MIN_POSITION: 10, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_MIN_POSITION] == 10

    def test_includes_motion_sensors(self):
        entry = _make_entry({CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_MOTION_SENSORS] == ["binary_sensor.motion"]

    def test_includes_blind_spot(self):
        entry = _make_entry({CONF_ENABLE_BLIND_SPOT: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_ENABLE_BLIND_SPOT] is True

    def test_empty_options_returns_empty(self):
        entry = _make_entry({})
        result = _extract_shared_options(entry)
        assert result == {}

    def test_only_excluded_fields_returns_empty(self):
        entry = _make_entry({
            CONF_ENTITIES: ["cover.test"],
            CONF_AZIMUTH: 180,
            CONF_DEVICE_ID: "abc",
        })
        result = _extract_shared_options(entry)
        assert result == {}

    def test_returns_copy_not_reference(self):
        options = {CONF_HEIGHT_WIN: 2.1}
        entry = _make_entry(options)
        result = _extract_shared_options(entry)
        result[CONF_HEIGHT_WIN] = 99.0
        assert entry.options[CONF_HEIGHT_WIN] == 2.1
```

- [ ] **Step 2: Run tests to verify they fail (ImportError expected)**

```bash
source venv/bin/activate && python -m pytest tests/test_duplicate_sync.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name '_extract_shared_options'`

- [ ] **Step 3: Add the `_extract_shared_options` helper to `config_flow.py`**

Add after the `_get_devices_from_entities` function (around line 581), before `class ConfigFlowHandler`:

```python
_SHARED_OPTIONS_EXCLUDED = {CONF_ENTITIES, CONF_AZIMUTH, CONF_DEVICE_ID}


def _extract_shared_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return options safe to copy across covers.

    Excludes per-window fields: CONF_ENTITIES, CONF_AZIMUTH, CONF_DEVICE_ID.
    All other options (dimensions, automation, climate, motion, etc.) are shared.
    """
    return {k: v for k, v in entry.options.items() if k not in _SHARED_OPTIONS_EXCLUDED}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
source venv/bin/activate && python -m pytest tests/test_duplicate_sync.py -v
```

Expected: 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_duplicate_sync.py custom_components/adaptive_cover_pro/config_flow.py
git commit -m "feat: add _extract_shared_options helper for duplicate/sync (Issue #54)"
```

---

## Task 3: Update `_ensure_unique_name` to Accept a Suffix Parameter

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_flow.py` (`_ensure_unique_name` method, around line 1046)
- Modify: `tests/test_duplicate_sync.py`

- [ ] **Step 1: Add tests for `_ensure_unique_name` with suffix**

Append to `tests/test_duplicate_sync.py`:

```python
from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler


class TestEnsureUniqueName:
    """Tests for _ensure_unique_name with suffix support."""

    def _make_handler_with_names(self, existing_names: list[str]) -> ConfigFlowHandler:
        handler = ConfigFlowHandler.__new__(ConfigFlowHandler)
        entries = []
        for name in existing_names:
            e = MagicMock()
            e.data = {"name": name}
            entries.append(e)
        handler.hass = MagicMock()
        handler.hass.config_entries.async_entries.return_value = entries
        return handler

    @pytest.mark.asyncio
    async def test_unique_name_returned_unchanged(self):
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Bedroom")
        assert result == "Bedroom"

    @pytest.mark.asyncio
    async def test_default_suffix_is_imported(self):
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Living Room")
        assert result == "Living Room (Imported)"

    @pytest.mark.asyncio
    async def test_copy_suffix(self):
        handler = self._make_handler_with_names(["Living Room"])
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy)"

    @pytest.mark.asyncio
    async def test_copy_suffix_increments(self):
        handler = self._make_handler_with_names(["Living Room", "Living Room (Copy)"])
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy 2)"

    @pytest.mark.asyncio
    async def test_copy_suffix_increments_further(self):
        handler = self._make_handler_with_names(
            ["Living Room", "Living Room (Copy)", "Living Room (Copy 2)"]
        )
        result = await handler._ensure_unique_name("Living Room", suffix="Copy")
        assert result == "Living Room (Copy 3)"
```

- [ ] **Step 2: Run tests to verify the new tests fail**

```bash
source venv/bin/activate && python -m pytest tests/test_duplicate_sync.py::TestEnsureUniqueName -v
```

Expected: failures because `_ensure_unique_name` doesn't accept `suffix` yet.

Note: If `pytest-asyncio` is not installed, run `pip install pytest-asyncio` and add `asyncio_mode = "auto"` to `pyproject.toml`'s `[tool.pytest.ini_options]`. Check `pyproject.toml` first:

```bash
grep -A5 "pytest" /Users/jrhubott/Repositories/adaptive-cover-pro/pyproject.toml
```

If `asyncio_mode` is missing, add it to `[tool.pytest.ini_options]`.

- [ ] **Step 3: Update `_ensure_unique_name` to accept a `suffix` parameter**

Find `_ensure_unique_name` in `config_flow.py` (around line 1046). Replace the method body:

```python
async def _ensure_unique_name(self, name: str, suffix: str = "Imported") -> str:
    """Ensure name doesn't conflict with existing entries.

    Appends ' (suffix)' or ' (suffix N)' if a conflict exists.
    """
    existing_entries = self.hass.config_entries.async_entries(DOMAIN)
    existing_names = {e.data.get("name") for e in existing_entries}

    if name not in existing_names:
        return name

    suffixed_name = f"{name} ({suffix})"
    if suffixed_name not in existing_names:
        return suffixed_name

    counter = 2
    while f"{name} ({suffix} {counter})" in existing_names:
        counter += 1

    return f"{name} ({suffix} {counter})"
```

- [ ] **Step 4: Run tests**

```bash
source venv/bin/activate && python -m pytest tests/test_duplicate_sync.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full suite to check no regressions**

```bash
source venv/bin/activate && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all 358+ tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_duplicate_sync.py custom_components/adaptive_cover_pro/config_flow.py
git commit -m "feat: update _ensure_unique_name to accept suffix parameter (Issue #54)"
```

---

## Task 4: Add Duplicate Steps to `ConfigFlowHandler`

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_flow.py`

- [ ] **Step 1: Add `selected_source_entry_id` to `ConfigFlowHandler.__init__`**

Find `__init__` of `ConfigFlowHandler` (around line 586). Add after `self.imported_count: int = 0`:

```python
self.selected_source_entry_id: str | None = None
```

- [ ] **Step 2: Update `async_step_user` to detect ACP entries and show the duplicate menu option**

Find `async_step_user` (around line 602). Replace the block inside `if not user_input and not hasattr(self, "_legacy_detected"):`:

```python
if not user_input and not hasattr(self, "_legacy_detected"):
    self._legacy_detected = True
    legacy_entries = await self._detect_legacy_entries(self.hass)
    acp_entries = self.hass.config_entries.async_entries(DOMAIN)

    if legacy_entries or acp_entries:
        menu_options = ["create_new"]
        if legacy_entries:
            menu_options.append("import_legacy")
        if acp_entries:
            menu_options.append("duplicate_existing")
        return self.async_show_menu(
            step_id="user",
            menu_options=menu_options,
            description_placeholders={"legacy_count": str(len(legacy_entries))},
        )
```

- [ ] **Step 3: Add `async_step_duplicate_existing` redirect**

Add after `async_step_create_new` (around line 994):

```python
async def async_step_duplicate_existing(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Handle duplicate existing configuration flow."""
    return await self.async_step_duplicate_select(user_input)
```

- [ ] **Step 4: Add `async_step_duplicate_select`**

Add after `async_step_duplicate_existing`:

```python
async def async_step_duplicate_select(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Select the source cover to duplicate from."""
    acp_entries = self.hass.config_entries.async_entries(DOMAIN)

    if user_input is not None:
        self.selected_source_entry_id = user_input["source_entry"]
        return await self.async_step_duplicate_configure()

    return self.async_show_form(  # type: ignore[return-value]
        step_id="duplicate_select",
        data_schema=vol.Schema(
            {
                vol.Required("source_entry"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": e.entry_id, "label": e.title}
                            for e in acp_entries
                        ],
                    )
                )
            }
        ),
    )
```

- [ ] **Step 5: Add `async_step_duplicate_configure`**

Add after `async_step_duplicate_select`:

```python
async def async_step_duplicate_configure(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Configure the unique fields for the duplicated cover."""
    source_entry = self.hass.config_entries.async_get_entry(
        self.selected_source_entry_id or ""
    )
    if not source_entry:
        return self.async_abort(reason="source_not_found")  # type: ignore[return-value]

    if user_input is not None:
        shared_options = _extract_shared_options(source_entry)
        sensor_type = source_entry.data.get(CONF_SENSOR_TYPE)
        new_name = await self._ensure_unique_name(user_input["name"], suffix="Copy")

        type_mapping = {
            "cover_blind": "Vertical",
            "cover_awning": "Horizontal",
            "cover_tilt": "Tilt",
        }

        return self.async_create_entry(  # type: ignore[return-value]
            title=f"{type_mapping.get(sensor_type, 'Cover')} {new_name}",
            data={"name": new_name, CONF_SENSOR_TYPE: sensor_type},
            options={
                **shared_options,
                CONF_ENTITIES: user_input.get(CONF_ENTITIES, []),
                CONF_AZIMUTH: user_input[CONF_AZIMUTH],
            },
        )

    source_azimuth = source_entry.options.get(CONF_AZIMUTH, 180)

    schema = vol.Schema(
        {
            vol.Required("name"): selector.TextSelector(),
            vol.Optional(CONF_ENTITIES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="cover",
                    multiple=True,
                )
            ),
            vol.Required(CONF_AZIMUTH, default=source_azimuth): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=359,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="°",
                )
            ),
        }
    )

    return self.async_show_form(  # type: ignore[return-value]
        step_id="duplicate_configure",
        data_schema=schema,
    )
```

- [ ] **Step 6: Run full test suite**

```bash
source venv/bin/activate && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass (config flow steps are not unit-tested by the existing suite, but no regressions).

- [ ] **Step 7: Commit**

```bash
git add custom_components/adaptive_cover_pro/config_flow.py
git commit -m "feat: add duplicate cover steps to config flow (Issue #54)"
```

---

## Task 5: Add Sync Steps to `OptionsFlowHandler`

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_flow.py`

- [ ] **Step 1: Add `selected_sync_targets` to `OptionsFlowHandler.__init__`**

Find `OptionsFlowHandler.__init__` (around line 1279). Add after `self.sensor_type` assignment:

```python
self.selected_sync_targets: list[str] = []
```

- [ ] **Step 2: Add `"sync"` to the Options flow menu in `async_step_init`**

Find `async_step_init` in `OptionsFlowHandler` (around line 1289). Change:

```python
options = ["automation", "blind", "device"]
```

to:

```python
options = ["automation", "blind", "device", "sync"]
```

- [ ] **Step 3: Add `async_step_sync`**

Add after `async_step_device` (around line 1363):

```python
async def async_step_sync(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Select target covers to sync settings to."""
    current_type = self._config_entry.data.get(CONF_SENSOR_TYPE)
    other_entries = [
        e
        for e in self.hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != self._config_entry.entry_id
        and e.data.get(CONF_SENSOR_TYPE) == current_type
    ]

    if not other_entries:
        return self.async_abort(reason="no_covers_to_sync")  # type: ignore[return-value]

    if user_input is not None:
        self.selected_sync_targets = user_input.get("target_entries", [])
        return await self.async_step_sync_confirm()

    return self.async_show_form(  # type: ignore[return-value]
        step_id="sync",
        data_schema=vol.Schema(
            {
                vol.Required("target_entries"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        multiple=True,
                        options=[
                            {"value": e.entry_id, "label": e.title}
                            for e in other_entries
                        ],
                    )
                )
            }
        ),
    )
```

- [ ] **Step 4: Add `async_step_sync_confirm`**

Add after `async_step_sync`:

```python
async def async_step_sync_confirm(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Confirm and execute sync to selected covers."""
    if user_input is not None:
        if user_input.get("confirm"):
            shared_options = _extract_shared_options(self._config_entry)
            for entry_id in self.selected_sync_targets:
                target = self.hass.config_entries.async_get_entry(entry_id)
                if target:
                    self.hass.config_entries.async_update_entry(
                        target,
                        options={**target.options, **shared_options},
                    )
            return self.async_abort(reason="sync_complete")  # type: ignore[return-value]
        return self.async_abort(reason="user_cancelled")  # type: ignore[return-value]

    # Build summary of selected targets
    target_titles = []
    for entry_id in self.selected_sync_targets:
        target = self.hass.config_entries.async_get_entry(entry_id)
        if target:
            target_titles.append(f"• {target.title}")

    return self.async_show_form(  # type: ignore[return-value]
        step_id="sync_confirm",
        data_schema=vol.Schema(
            {vol.Required("confirm", default=True): selector.BooleanSelector()}
        ),
        description_placeholders={
            "entries_summary": "\n".join(target_titles) or "(none selected)"
        },
    )
```

- [ ] **Step 5: Run full test suite**

```bash
source venv/bin/activate && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/adaptive_cover_pro/config_flow.py
git commit -m "feat: add sync cover steps to options flow (Issue #54)"
```

---

## Task 6: Add Translation Keys

**Files:**
- Modify: `custom_components/adaptive_cover_pro/translations/en.json`

- [ ] **Step 1: Add translation keys**

Open `translations/en.json`. Make the following additions:

**In `config.step.user.menu_options`**, add:
```json
"duplicate_existing": "Duplicate existing cover"
```

**In `config.step`**, add two new step entries:
```json
"duplicate_select": {
  "title": "Duplicate Cover — Select Source",
  "description": "Choose which existing cover to duplicate settings from.",
  "data": {
    "source_entry": "Source cover"
  }
},
"duplicate_configure": {
  "title": "Duplicate Cover — Configure",
  "description": "Enter the unique settings for the new cover. All other settings will be copied from the source.",
  "data": {
    "name": "Name",
    "entities": "Cover entities",
    "azimuth": "Window azimuth"
  }
}
```

**In `config.abort`**, add:
```json
"source_not_found": "The source cover no longer exists."
```

**In `options.step.init.menu_options`**, add:
```json
"sync": "Sync settings to other covers"
```

**In `options.step`**, add two new step entries:
```json
"sync": {
  "title": "Sync Settings — Select Targets",
  "description": "Choose which covers to sync settings to. All shared settings (dimensions, automation, climate, etc.) will be overwritten on the selected covers.",
  "data": {
    "target_entries": "Target covers"
  }
},
"sync_confirm": {
  "title": "Sync Settings — Confirm",
  "description": "The following covers will have their shared settings overwritten:\n\n{entries_summary}\n\nEach cover will keep its own name, entities, azimuth, and device association.",
  "data": {
    "confirm": "Confirm sync"
  }
}
```

**In `options.abort`** (create this key if it doesn't exist), add:
```json
"no_covers_to_sync": "No other covers of the same type exist to sync to.",
"user_cancelled": "Sync cancelled.",
"sync_complete": "Settings synced successfully."
```

- [ ] **Step 2: Validate JSON is well-formed**

```bash
python3 -c "import json; json.load(open('custom_components/adaptive_cover_pro/translations/en.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 3: Run lint**

```bash
source venv/bin/activate && ruff check custom_components/adaptive_cover_pro/config_flow.py --fix && ruff format custom_components/adaptive_cover_pro/config_flow.py
```

Expected: no errors (or auto-fixed).

- [ ] **Step 4: Run full test suite**

```bash
source venv/bin/activate && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/adaptive_cover_pro/translations/en.json
git commit -m "feat: add translation keys for duplicate/sync covers (Issue #54)"
```

---

## Task 7: Push Branch and Open PR

**Files:** none

- [ ] **Step 1: Push branch**

```bash
git push -u origin feature/issue-54-duplicate-sync-covers
```

- [ ] **Step 2: Create PR linked to Issue #54**

```bash
gh pr create \
  --title "feat: Duplicate and sync covers in config flow (Fixes #54)" \
  --base main \
  --body "$(cat <<'EOF'
## Summary
- Add **Duplicate** option to the Add Integration flow: select a source cover, enter only name/entities/azimuth, and all shared settings are copied automatically
- Add **Sync** option to the Options flow: push shared settings from one cover to one or more covers of the same type
- Both features share a `_extract_shared_options` helper that excludes per-window fields (entities, azimuth, device ID)
- `_ensure_unique_name` updated to accept a configurable suffix (default: "Imported"; duplicate uses "Copy")

## Testing
- ✅ 358+ existing tests passing
- ✅ New `tests/test_duplicate_sync.py` with 17 unit tests for helper and name-uniqueness logic

## Related Issues
Fixes #54
EOF
)"
```

---

## Verification Checklist

Before declaring done:

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `ruff check . --fix && ruff format .` — no lint errors
- [ ] `python3 -c "import json; json.load(open('custom_components/adaptive_cover_pro/translations/en.json'))"` — JSON valid
- [ ] PR created and linked to Issue #54
