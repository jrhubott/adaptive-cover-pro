---
name: acp-diagnose
description: Analyze an Adaptive Cover Pro diagnostics JSON file and produce a triage report. Use when the user provides a diagnostics.json download from HA, attaches a diagnostics file, or asks to diagnose an Adaptive Cover Pro issue. Trigger on phrases like "analyze diagnostics", "what does this diagnostics show", "why isn't my cover moving", paired with a JSON file.
---

# ACP Diagnose

Analyze a diagnostics JSON file downloaded from Home Assistant (Settings → Devices & Services → Adaptive Cover Pro → ⋮ → Download diagnostics) and produce a structured triage report.

## How to Execute

When the user provides a diagnostics JSON file (path, paste, or attachment):

1. Read the file with the Read tool (if a path is given) or parse it from the conversation.
2. Walk through the checklist below in order.
3. Produce a report in the Output Format below.

If no file is provided, ask: "Please share the diagnostics JSON (download from HA: Settings → Devices & Services → Adaptive Cover Pro → ⋮ → Download diagnostics)."

---

## Analysis Checklist

Work through each section in order. Missing sections = "not reported" (older firmware). Never error on absent keys.

### 1. Sanity / Version

Extract from `diagnostics.meta` (may be absent on older builds):
- `integration_version` — note it
- `cover_type` — note it
- `coordinator_update.last_update_success` — if `false` → **CRITICAL**
- `coordinator_update.last_exception` — if non-null → **CRITICAL**, include the repr
- `coordinator_update.update_interval_seconds` — flag if < 30 or > 3600 as **WARNING**
- `coordinator_update.last_update_success_time` — note it

### 2. Control Status

Extract `diagnostics.control_status` and `diagnostics.control_state_reason`.

| Status | Severity | Message |
|--------|----------|---------|
| `automatic_control_off` | INFO | "Automation is paused (Auto Control switch is off)" |
| `manual_override` | INFO | Cross-reference `manual_override_state.entries` — list which covers, started_at, remaining_seconds |
| `force_override_active` | INFO | "Force override active — check which binary sensor is on: {force_override_sensors}" |
| `weather_override_active` | WARNING | "Weather override active — covers held at safe position" |
| `motion_timeout` | INFO | "Motion timeout active" |
| `sun_not_visible` | INFO | Report `sun_validity` details: valid, valid_elevation, in_blind_spot, sunset_window_active |
| `outside_time_window` | INFO | Report configured start/end vs `last_updated` |
| `active` | OK | "Normal solar tracking" |

### 3. Decision Trace

Extract `diagnostics.decision_trace` (list of `{handler, matched, reason, position}`).

- Find the first entry where `matched == true` → the **winning handler**
- For every entry with higher list-index priority that returned `matched == false`, summarize its skip reason in one line
- Flag **unexpected wins**:
  - `default` handler wins but `sun_validity.valid == true` and `sun_validity.valid_elevation == true` → likely a config issue (azimuth/FOV mismatch)
  - `manual_override` wins but user reports they haven't touched the cover → investigate `manual_override_state`
  - `weather` wins but no weather sensors are configured → stale state

If `decision_trace` is empty or absent → note "Decision trace not available (upgrade to v2.16.0+)".

### 4. Cover Command Health

Extract `diagnostics.cover_commands` (dict of entity_id → snapshot).

For each entity:
- `gave_up == true` → **CRITICAL**: "cover.X stopped accepting commands after repeated retries — check HA logs for service call errors"
- `retry_count > 2` → **WARNING**: "cover.X has {N} retries outstanding"
- `wait_for_target == true` and `target_call` set → INFO: "cover.X waiting for position {target_call} to be confirmed"
- `safety_target != null` → NOTE: "cover.X has a safety override target of {safety_target}%"
- `in_manual_override_set == true` → NOTE: "cover.X is in the manual override set inside CoverCommandService"

### 5. Position / Skip Analysis

Extract `diagnostics.last_skipped_action`.

- `reason == "delta_too_small"`:
  - Report `position_delta` vs `delta_position_threshold`
  - If delta is within 2 of threshold → suggest lowering the delta threshold
- `reason == "time_delta_too_small"`:
  - Report `elapsed_minutes` vs `time_threshold_minutes`
- `reason == "manual_override"` → cross-reference with control status
- `reason == "integration_disabled"` → integration is disabled, nothing will move
- `reason == "auto_control_off"` → same as `automatic_control_off` status

Also check `diagnostics.position_delta_from_last_action` and `diagnostics.seconds_since_last_action` for context.

### 6. Configuration Sanity

Extract `diagnostics.configuration` and `config_options`.

Flag these combinations:
- `enable_min_position == false` AND `min_position` is set and > 0 → NOTE: "min_position is configured but enforcement is always-on (not sun-tracking-only). Set enable_min_position=true if you only want it during sun tracking."
- `inverse_state == true` AND `cover_type == "cover_awning"` → NOTE: unusual combination, confirm intentional
- `force_override_sensors` is non-empty → verify sensors exist by noting them; flag if `force_override_active` is false in pipeline but the status says active (stale state)
- `motion_sensors` is non-empty but `motion_detected == false` and `motion_timeout_active == false` → motion configured but currently inactive (normal; just note)
- `interpolation == true` → note it influences final position vs calculated_position

### 7. Climate (only when `climate_mode == true`)

Extract `diagnostics.climate_control_method`, `active_temperature`, `temperature_details`, `climate_strategy`, `climate_conditions`.

- Report the active strategy and current temperature
- If `temperature_details.inside_temperature == null` and climate mode is on → WARNING: "Climate mode is on but inside temperature sensor is not returning a value"
- Report whether is_summer/is_winter/is_presence flags are set as expected

---

## Output Format

```markdown
## Adaptive Cover Pro — Diagnostics Report

**Integration:** {version} · **Cover type:** {cover_type} · **Last update:** {last_update_success_time} ({success/FAILED})

### 🔴 Critical
- [list or "(none)"]

### 🟡 Warnings
- [list or "(none)"]

### ℹ️ Findings
- Control status: {status} — {reason}
- [manual override details if relevant]
- [skip analysis if relevant]
- [config notes if relevant]

### Decision Trace
Winning handler: **{handler}** (position: {position}%, reason: "{reason}")

| Handler | Matched | Reason |
|---------|---------|--------|
| force_override | ❌ | no sensor active |
| manual_override | ✅ | user moved cover at 14:22 |

### Cover Commands
| Entity | Retries | Gave Up | Waiting | Safety Target |
|--------|---------|---------|---------|---------------|
| cover.living_room | 0 | No | No | — |

### Cover Positions (live)
| Entity | Position | Available |
|--------|----------|-----------|
| cover.living_room | 42% | ✅ |

### Manual Override State
| Entity | Active | Started | Remaining |
|--------|--------|---------|-----------|
| cover.living_room | ✅ | 14:22 UTC | ~70 min |

### Summary
[1-3 sentence plain-English summary of what's going on and what to do next]
```

Omit sections that have no data or are "not reported". Keep the summary actionable — tell the user the most likely cause and what to check or change.
