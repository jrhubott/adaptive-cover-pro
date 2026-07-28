---
name: acp-diagnose
description: Analyze an Adaptive Cover Pro diagnostics JSON file and produce a triage report. Use when the user provides a diagnostics.json download from HA, attaches a diagnostics file, or asks to diagnose an Adaptive Cover Pro issue. Trigger on phrases like "analyze diagnostics", "what does this diagnostics show", "why isn't my cover moving", paired with a JSON file.
---

# ACP Diagnose

Analyze a diagnostics JSON file downloaded from Home Assistant (Settings → Devices & Services → Adaptive Cover Pro → ⋮ → Download diagnostics) by running the **same declarative rules engine** that powers the in-product Troubleshoot step, then narrating the findings.

The prose checklist this skill used to carry is gone: the checks now live in the `TRIAGE_RULES` table in `custom_components/adaptive_cover_pro/diagnostics/triage.py`, and `scripts/triage_json.py` runs them offline. **The same table drives the in-product troubleshoot step AND this offline triage** — so a gap in one is a gap in both, and closing it means adding a rule row, not editing this skill.

## How to Execute

When the user provides a diagnostics JSON file (path or attachment):

1. **Run the engine.** From the repo root, with the dev virtualenv (the package import pulls Home Assistant, which is not on the system interpreter):

   ```bash
   venv/bin/python scripts/triage_json.py <path-to-diagnostics.json>
   ```

   Add `--latest-version <X.Y.Z>` when you know the newest release — it enables the `STALE_VERSION` check (offline it cannot fetch the latest release itself). Add `--lang de` / `--lang fr` to render findings in German or French.

2. **Narrate each finding.** For every line the engine printed, restate it in plain English for the user and include its wiki deep link (the script prints one per finding). Lead with the criticals (🛑), then warnings (⚠️), then info (ℹ️).

3. **Investigate anything the engine cannot explain.** If the user's symptom is not covered by any finding, read the relevant sections of the diagnostics JSON directly (`decision_trace`, `control_status`, `cover_commands`, `last_skipped_action`, `sun_validity`, `climate_conditions`) and reason about it by hand.

   **An unexplained symptom is a missing rule row.** When you find yourself hand-explaining a class of problem the engine did not flag, that is the signal to add a rule — follow [Developer Triage Rules](https://github.com/jrhubott/adaptive-cover-pro/wiki/Developer-Triage-Rules) (four edits: one rule row, one English template + `en.json` leaf, one JSON leaf per language, one test). Offer to file an issue or open that change.

If no file is provided, ask: "Please share the diagnostics JSON (download from HA: Settings → Devices & Services → Adaptive Cover Pro → ⋮ → Download diagnostics)."

## Offline seam — what the engine cannot see in a download

The diagnostics download carries neither per-entity **capabilities** nor the policy-derived **axis requirements** (both are built live inside the config flow), so two config rules cannot fire offline:

- `COVER_NOT_READY` (rule 8a) — a cover reporting no capabilities. Its runtime twin `ENTITY_UNAVAILABLE` (8b) _does_ fire offline, reading `covers[*].available`.
- `COVER_FEATURE_MISMATCH` (rule 13) — a cover missing an axis its type needs.

And `STALE_VERSION` (rule 24) fires only when you pass `--latest-version`. If a user's problem is a capability mismatch or an out-of-date install, check those by hand — the offline run will not raise them.

## Output

Present the engine's findings as a short triage report: a one-line summary of the most likely cause and what to change, then the findings grouped by severity with their wiki links, then any hand-investigated notes for symptoms the engine did not cover. Keep it actionable.
