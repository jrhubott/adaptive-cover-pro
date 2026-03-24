# Duplicate & Sync Covers — Design Spec

**Date:** 2026-03-23
**Issue:** [#54](https://github.com/jrhubott/adaptive-cover/issues/54)
**Status:** Approved

---

## Problem

Users with many windows (e.g. 20) must re-enter identical parameters for each cover. There is no way to copy settings from one cover to another.

---

## Solution Overview

Two related features sharing a single shared-options helper:

1. **Duplicate** — create a new cover pre-filled from an existing one (in the "Add Integration" flow)
2. **Sync** — push shared settings from one cover to one or more other covers of the same type (in the Options flow)

---

## Shared Helper: `_extract_shared_options`

```python
def _extract_shared_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return options that are safe to copy across covers.

    Excludes per-window fields: CONF_ENTITIES, CONF_AZIMUTH, CONF_DEVICE_ID.
    """
```

This is the single source of truth for what "shared" means. Both duplicate and sync call it. Excludes:
- `CONF_ENTITIES` — each window has its own cover entities
- `CONF_AZIMUTH` — each window faces a different direction
- `CONF_DEVICE_ID` — each window links to a different physical device

All other options are considered shared: window dimensions, sill height, window depth, automation settings (including `CONF_ENABLE_DIAGNOSTICS`, `CONF_RETURN_SUNSET`), climate settings, position limits, motion control, force override, interpolation lists, blind spot configuration, etc.

Implementation: iterate over `entry.options.items()` and include all keys not in the exclusion set.

---

## Feature 1: Duplicate (Add Integration Flow)

### Entry Point

`async_step_user` (called with no input) now detects both legacy entries (`LEGACY_DOMAIN`) and existing ACP entries (`DOMAIN`). Menu options are built as:
- Always include `"create_new"`
- Add `"import_legacy"` if any loaded legacy entries exist
- Add `"duplicate_existing"` if any ACP entries exist

All three can appear simultaneously if both legacy entries and ACP entries exist. The `description_placeholders` dict still includes `{legacy_count}` (set to `"0"` when no legacy entries).

### Steps

#### `async_step_duplicate_select`
- Displays a single-select dropdown of all existing `adaptive_cover_pro` config entries
- Label: `entry.title` (e.g., `"Vertical Living Room"`), value: `entry_id`
- User selects the source cover to duplicate from
- Stores `selected_source_entry_id` on `self`

#### `async_step_duplicate_configure`
- Displays a form with exactly three fields:
  - **Name** (`name`) — text input, blank default
  - **Cover entities** (`CONF_ENTITIES`) — entity selector (domain: `cover`, multiple), blank default
  - **Azimuth** (`CONF_AZIMUTH`) — number selector (0–359°), pre-filled from source entry's options
- On submit:
  1. Looks up source entry by `selected_source_entry_id`; aborts with `"source_not_found"` if missing
  2. Calls `_extract_shared_options(source_entry)` to get all shared options (includes blind spot, interpolation, climate entities, etc. — copied verbatim without user review)
  3. Merges in `name`, `CONF_ENTITIES`, `CONF_AZIMUTH` from user input
  4. Applies `_ensure_unique_name` with suffix `" (Copy)"` (not `" (Imported)"`) if name conflicts
  5. Creates entry directly via `async_create_entry` (same `data`/`options` structure as `async_step_update`)
  6. `CONF_DEVICE_ID` is not set on the new entry (device association step is skipped entirely)
- Cover type (`CONF_SENSOR_TYPE`, stored in `entry.data`) is inherited from source — not shown, not editable

### Notes
- All non-excluded options are copied silently — the user does not review automation, climate, blind spot, or interpolation settings. These can be adjusted later via the Options flow.
- `CONF_SENSOR_TYPE` is read from `source_entry.data[CONF_SENSOR_TYPE]`, not from options.
- Device association is always skipped; `CONF_DEVICE_ID` is absent from the new entry's options.

---

## Feature 2: Sync (Options Flow)

### Entry Point

`OptionsFlowHandler.async_step_init` always includes `"sync"` in its menu options list (unconditional).

### Steps

#### `async_step_sync`
- Queries all other `adaptive_cover_pro` entries where `entry.data[CONF_SENSOR_TYPE]` matches the current entry's type
- If none exist, aborts with reason `"no_covers_to_sync"`
- Displays a multi-select of matching entries, labelled by `entry.title` (e.g., `"Vertical Bedroom"`)
- User selects one or more targets
- Stores `selected_sync_targets` (list of entry IDs) on `self`

#### `async_step_sync_confirm`
- Shows a confirmation form with:
  - Description placeholder `{entries_summary}` listing the selected target titles (one per line)
  - Single boolean `"confirm"` field (default `True`)
- On confirm (`True`):
  1. Calls `_extract_shared_options(self._config_entry)` to get current entry's shared options
  2. For each target entry ID, retrieves the target entry and calls `hass.config_entries.async_update_entry(target_entry, options={**target_entry.options, **shared_options})` — merging shared options in, preserving target's `CONF_ENTITIES`, `CONF_AZIMUTH`, `CONF_DEVICE_ID`
  3. Aborts with reason `"sync_complete"` (does not call `_update_options()` — no changes to the current entry are needed, and using abort avoids a spurious re-save of the current entry)
- On cancel (`False`): aborts with reason `"user_cancelled"`

### Notes
- `CONF_SENSOR_TYPE` filter uses `entry.data[CONF_SENSOR_TYPE]`, not `entry.options`.
- Sync does not modify the source (current) cover's own options.
- Targets retain their own name, entities, azimuth, and device association.

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| No ACP entries exist when adding | `duplicate_existing` not shown in menu |
| Source entry missing at configure step | Abort `"source_not_found"` |
| Name conflict during duplicate | `_ensure_unique_name` appends `" (Copy)"` / `" (Copy 2)"` |
| No same-type covers exist for sync | Abort `"no_covers_to_sync"` |
| User cancels sync confirmation | Abort `"user_cancelled"` |
| Sync completes successfully | Abort `"sync_complete"` |

---

## Translation Keys Required

New keys needed in `translations/en.json`:

**Config flow:**
- `config.step.duplicate_select` — title + description
- `config.step.duplicate_configure` — title + description
- `config.menu_options.duplicate_existing` — menu label
- `config.abort.source_not_found`

**Options flow:**
- `options.step.sync` — title + description
- `options.step.sync_confirm` — title + description + `{entries_summary}` placeholder
- `options.menu_options.sync` — menu label
- `options.abort.no_covers_to_sync`
- `options.abort.user_cancelled`
- `options.abort.sync_complete`

---

## Files Changed

- `custom_components/adaptive_cover_pro/config_flow.py` — all logic
- `custom_components/adaptive_cover_pro/translations/en.json` — new translation keys

No new files. No new constants needed.

---

## Testing

**Duplicate:**
- `_extract_shared_options` unit test: assert returned dict does NOT contain `CONF_ENTITIES`, `CONF_AZIMUTH`, `CONF_DEVICE_ID`; and DOES contain representative keys from each category: `CONF_HEIGHT_WIN` (dimensions), `CONF_DELTA_POSITION` (automation), `CONF_CLIMATE_MODE` (climate), `CONF_MIN_POSITION` (position limits), `CONF_MOTION_SENSORS` (motion), `CONF_ENABLE_BLIND_SPOT` (blind spot)
- Duplicate creates entry with all shared options copied correctly
- Duplicate preserves cover type from `source_entry.data[CONF_SENSOR_TYPE]`
- Name, entities, azimuth come from user input (not source)
- Device ID is absent from duplicated entry
- Name conflict produces `" (Copy)"` suffix

**Sync:**
- Sync updates all selected targets' options
- Sync does not modify the source cover
- Sync only lists same-type covers (reads from `entry.data[CONF_SENSOR_TYPE]`)
- Target's `CONF_ENTITIES`, `CONF_AZIMUTH`, `CONF_DEVICE_ID` are preserved after sync
- No same-type covers → abort `"no_covers_to_sync"`
