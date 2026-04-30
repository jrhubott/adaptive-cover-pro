---
name: acp-translate
description: Sync DE/FR translations with en.json, add a new language, drop a language, or check translation status for the Adaptive Cover Pro integration. Triggers on phrases like "sync translations", "update translations", "add [language]", "drop [language]", "translate", "translation status", "retranslate".
---

# ACP Translate

Maintains the `custom_components/adaptive_cover_pro/translations/` directory. `en.json` is the single source of truth; every other language file must match its structure exactly, including the `services` section.

Officially shipped languages: **en, de, fr**. Any new language is added only on explicit maintainer request via this skill.

## Picking the Operation

Match what the user asks for:

| User says…                                                                    | Operation         |
| ----------------------------------------------------------------------------- | ----------------- |
| "sync translations", "update translations", "propagate the en.json changes"   | **Sync**          |
| "add [language]", "rebuild [language] from scratch", "retranslate [language]" | **Add language**  |
| "drop [language]", "remove [language]", "stop shipping [language]"            | **Drop language** |
| "translation status", "how are translations doing", "check translations"      | **Status**        |

If ambiguous, ask the user one clarifying question before proceeding.

---

## Model Strategy

| Operation                       | Model                      | Scope                                                                 |
| ------------------------------- | -------------------------- | --------------------------------------------------------------------- |
| **Sync** (incremental)          | Haiku only                 | All changed/added keys — no Sonnet review                             |
| **Add language** (full rebuild) | Haiku bulk → Sonnet review | Haiku: all 766 keys; Sonnet: only `data_description` keys (see below) |

**Why Haiku-only for Sync:** Incremental changes are small and build on an existing high-quality baseline. Placeholder preservation is verified by tests. Sonnet review is not cost-justified for small diffs.

**Why Sonnet for Add language data_descriptions:** Full rebuilds produce ~286 long help-text strings with domain concepts (azimuth, elevation, glare zones, climate modes). These benefit from a register/accuracy pass. Step descriptions, labels, and error/abort messages are simple enough for Haiku alone.

**Sonnet review-pass key pattern** (Add language only):

- Any dotpath containing `.data_description.` — these are the long help text strings

Everything else stays with Haiku output.

**Cost budget:** ~$0.03 per 2-language sync; ~$0.25 per 2-language full rebuild. If a run seems headed above $0.50 for one language, stop and ask the user.

---

## Operation 1 — Sync

Use when `translations/en.json` has changed and DE/FR need to catch up.

### Steps

1. **Diff.** Load all three translation files via Bash+Python (see **Reading Translation Files** — do NOT use the Read tool). Flatten each to dot-path keys. For each non-en file compute:

   - `added`: keys in en but not in target
   - `removed`: keys in target but not in en
   - `changed`: keys where the en value text changed since the target was last generated. Detect by heuristic: if `target[k]` looks like an obvious placeholder (equals `en[k]` verbatim, or is a short English phrase when the rest of the file is clearly in the target language), treat it as changed. When unsure, retranslate — cost of a re-translation is negligible.

2. **If nothing to do**, report "DE/FR already in sync with en.json" and exit.

3. **Dispatch one subagent per language in parallel** (single message, two `Agent` tool calls). Each subagent receives:

   - Absolute path to `translations/en.json` and to its own target file
   - The list of `added` + `changed` dotpaths to translate
   - The list of `removed` dotpaths to strip from the target
   - The language name and ISO code

   The subagent's job (Haiku only — no Sonnet review for Sync):

   - Extract the values at the requested dotpaths from en.json via Bash+Python (see **Reading Translation Files** — do NOT use the Read tool directly on these files).
   - Run the Haiku translation prompt (see **Subagent Prompt Templates**) on all of them.
   - Load the target file, merge in the new values, delete `removed` keys, and write it back via Bash+Python.
   - Return a one-paragraph summary: counts added/changed/removed, any placeholder-preservation warnings, cost estimate.

4. **Verify.** Run `./scripts/validate_translations.py --ci` and `venv/bin/python -m pytest tests/test_translations.py -q`. If either fails, report the failure verbatim and stop. Do not attempt a second auto-sync round.

5. **Report.** Use the Output Format below.

---

## Operation 2 — Add Language

Use to rebuild DE/FR from scratch **or** to add a brand-new language.

### Steps

1. **Validate.** The language code must be a valid HA locale (BCP-47 form: `de`, `fr`, `es`, `pt-BR`, `zh-Hans`, etc.). If `en`, refuse — we don't retranslate English. If the file already exists and the user did not say "rebuild" or "retranslate", confirm they want to overwrite.

2. **Delete the existing file** if rebuilding, so the subagent produces a clean file.

3. **Dispatch one subagent per requested language in parallel.** If the user says "add DE and FR", send a single message with two `Agent` tool calls. Each subagent receives:

   - Absolute path to `translations/en.json`
   - Absolute path to the target file it must write
   - Language name + ISO code
   - The domain-term glossary (see below) — customized per language

   The subagent's job:

   - Load the full en.json tree via Bash+Python (see **Reading Translation Files**).
   - **Pass 1 — Haiku bulk:** Run the Haiku translation prompt on all keys. Capture output as a JSON object.
   - **Pass 2 — Sonnet review (data_descriptions only):** Filter the Haiku output to include ONLY keys containing `.data_description.` — do not send any other keys to Sonnet. Run the Sonnet review prompt on this filtered subset. Merge corrected values back into the Haiku output.
   - Write `translations/<lang>.json` via Bash+Python: same nested structure as en.json, 2-space indent, `ensure_ascii=False`, trailing newline.
   - Return a summary: key count written, how many data_description keys were reviewed by Sonnet, placeholder warnings, cost estimate.

4. **Update tooling.** After subagents return:

   - Add the language code to the `LANGUAGES` constant in `scripts/validate_translations.py` (unless already present).
   - Update any per-language lists in `tests/test_translations.py`.

5. **Verify.** Run `./scripts/validate_translations.py --ci` and `venv/bin/python -m pytest tests/test_translations.py -q`. Do not proceed to commit if either fails.

6. **Report.** If the language is new (not in the previously-shipped set), remind the user to update README's supported-languages list and add a release-notes line.

---

## Operation 3 — Drop Language

1. Confirm the language is not `en`, `de`, or `fr`. If the user asks to drop one of the core three, explicitly confirm with them before proceeding (this changes the officially supported set).
2. Delete `translations/<lang>.json`.
3. Remove the code from `scripts/validate_translations.py` `LANGUAGES` list.
4. Remove any language-specific expectations from `tests/test_translations.py`.
5. Run `venv/bin/python -m pytest tests/test_translations.py -q` to confirm.
6. Report what was removed.

---

## Operation 4 — Status

1. Run `./scripts/validate_translations.py` (no flags) and show its dashboard output.
2. Run `venv/bin/python -m pytest tests/test_translations.py -q` and show pass/fail counts.
3. No subagents, no writes.

---

## Reading Translation Files

⚠️ **The translation JSON files exceed the Read tool's 25,000-token limit. Never use the Read tool directly on `en.json`, `de.json`, or `fr.json`.** Use Bash+Python instead.

**Extract specific dotpath values from en.json (Sync):**

```bash
python3 << 'EOF'
import json, functools

def get_path(d, dotpath):
    return functools.reduce(lambda x, k: x[k], dotpath.split('.'), d)

with open('/path/to/en.json') as f:
    en = json.load(f)

dotpaths = ['config.step.blind_spot.data.blind_spot_left', ...]
print(json.dumps({p: get_path(en, p) for p in dotpaths}, ensure_ascii=False, indent=2))
EOF
```

**Load full en.json tree (Add language):**

```bash
python3 -c "
import json
with open('/path/to/en.json') as f:
    d = json.load(f)
print(json.dumps(d, ensure_ascii=False))
"
```

**Load, update, and write back a target file:**

```bash
python3 << 'EOF'
import json

with open('/path/to/de.json') as f:
    target = json.load(f)

# Apply changes (set dotpath values, remove keys, etc.)
# target['config']['step']['blind_spot']['data']['blind_spot_left'] = 'Neuer Wert'

with open('/path/to/de.json', 'w') as f:
    json.dump(target, f, ensure_ascii=False, indent=2)
    f.write('\n')
EOF
```

---

## Subagent Prompt Templates

Use these verbatim when dispatching. Substitute `<...>` placeholders before sending.

### Haiku translation prompt (used for ALL operations)

```
You are translating Home Assistant integration UI strings from English to <LANGUAGE_NAME> (<LANG_CODE>).

Source file: <ABSOLUTE_PATH_TO_EN_JSON>
⚠️ Do NOT use the Read tool on this file — it exceeds the token limit and will error.
Use the Bash tool with Python to extract the values you need.

Translate ONLY these dotpath keys (flattened form):
<LIST_OF_DOTPATHS>

To extract source values, use the Bash tool:
  python3 -c "
  import json, functools
  def get(d, p): return functools.reduce(lambda x,k: x[k], p.split('.'), d)
  en = json.load(open('<ABSOLUTE_PATH_TO_EN_JSON>'))
  paths = [<COMMA_SEPARATED_QUOTED_DOTPATHS>]
  print(json.dumps({p: get(en, p) for p in paths}, ensure_ascii=False, indent=2))
  "

To update the target file, use the Bash tool:
  python3 -c "
  import json, functools
  def set_path(d, p, v):
      keys = p.split('.'); functools.reduce(lambda x,k: x[k], keys[:-1], d)[keys[-1]] = v
  with open('<ABSOLUTE_PATH_TO_TARGET_JSON>') as f: t = json.load(f)
  # set_path(t, 'config.step.blind_spot.data.blind_spot_left', 'translated value')
  with open('<ABSOLUTE_PATH_TO_TARGET_JSON>', 'w') as f:
      json.dump(t, f, ensure_ascii=False, indent=2); f.write('\n')
  "

Translation rules — non-negotiable:
1. Preserve every placeholder exactly as-is: {summary}, {entity}, {position}, {hours}, {minutes}, {name}, {version}, etc.
2. Preserve markdown and formatting: **bold**, newlines (\n), bullet markers (-), numbered lists, backticks.
3. Preserve HTML/XML-style tags if present (<br>, <b>, etc.).
4. Preserve unit symbols (%, °, m, cm, K) and numeric values verbatim.
5. Use these domain terms consistently — do NOT invent alternatives:
<DOMAIN_GLOSSARY_FOR_LANGUAGE>
6. Keep the register close to Home Assistant's UI voice: clear, concise, second-person imperative for instructions ("Select…", "Enter…", "Configure…").
7. Do NOT translate proper names, entity IDs, integration names, or mdi: icon references.

Self-review — before outputting, check each translation for:
- ✅ All {placeholders} present and unchanged
- ✅ Domain terms match the glossary above
- ✅ No English words left in output (except proper names and technical terms from the glossary)
- ✅ Register is natural for a technical UI (not overly formal or casual)

Output format: a single JSON object mapping each input dotpath to its translation. Nothing else. No commentary, no markdown fences.

Example output:
{"config.step.geometry.title": "Géométrie du cache", "config.step.geometry.description": "Configurez les dimensions..."}
```

### Sonnet review prompt (Add language only — data_description keys only)

```
Review these <LANGUAGE_NAME> translations of Home Assistant config-flow help text (data_description fields). These are long strings explaining configuration options to end users — they require accurate domain terminology and natural register.

Fix translations that:
- Sound stiff, machine-translated, or overly literal
- Misrepresent a technical concept (azimuth, elevation, tilt, glare zone, cover pipeline, FOV)
- Use inconsistent register with HA's UI voice
- Drop or alter a placeholder — if a placeholder is missing, flag it as ERROR: do not silently fix

Domain terms that must be used consistently:
<DOMAIN_GLOSSARY_FOR_LANGUAGE>

Do NOT change translations that are already correct and natural.

Return the SAME JSON shape with the same keys, corrected values only where needed. Output JSON only, no commentary.

⚠️ Input contains ONLY data_description keys. Do not add, remove, or rename any keys.

Input:
<FILTERED_HAIKU_OUTPUT — DATA_DESCRIPTION_KEYS_ONLY>
```

### Domain glossary (append to both Haiku and Sonnet prompts per language)

**German (de):**

- azimuth → Azimut
- elevation → Höhe
- tilt → Neigung
- slat → Lamelle
- glare zone → Blendungszone
- cover → Beschattung
- awning → Markise
- blind → Jalousie
- venetian blind → Jalousie (mit Lamellen)
- climate mode → Klimamodus
- override → Übersteuerung / Überschreibung
- manual override → Manuelle Übersteuerung
- force override → Zwangsübersteuerung
- motion sensor → Bewegungssensor
- presence → Anwesenheit
- field of view / FOV → Sichtfeld / FOV

**French (fr):**

- azimuth → azimut
- elevation → élévation
- tilt → inclinaison
- slat → lamelle
- glare zone → zone d'éblouissement
- cover → protection / store
- awning → store banne
- blind → store
- venetian blind → store vénitien
- climate mode → mode climatique
- override → dérogation
- manual override → dérogation manuelle
- force override → dérogation forcée
- motion sensor → détecteur de mouvement
- presence → présence
- field of view / FOV → champ de vision / FOV

For other languages, tell the subagent to "use the HA community standard translation of these terms for <language>; when in doubt prefer the shortest unambiguous term."

---

## File Format

Non-EN translation files must:

- Be valid JSON, 2-space indent.
- Use `ensure_ascii=False` (keep accented characters as-is, not `\uXXXX`).
- End with exactly one trailing newline.
- Preserve en.json's nested structure for every top-level section, including `services`.
- Contain no `mdi:` icon references, no zero-width characters, no empty string values.

---

## Safety Rules

- **Never delete `en.json`.**
- **Never write a translation file without running the validator and tests afterwards.**
- **Never silently drop keys from a target file.** If a key was removed from en.json, the Sync operation must list it under "removed" in the report.
- **If Haiku output fails JSON parse**, retry once; if still failing, dispatch Sonnet for that batch as a fallback. Do not return partial results.
- **Respect the cost budget.** If a projected run exceeds $0.50 for one language, stop and ask the user.

---

## Output Format

After any Sync or Add run, report:

```
Translation <sync|add> complete (branch: <current-branch>)

en.json: <N> keys (unchanged)
de.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
fr.json: +<A> added, ~<C> changed, -<R> removed → <T> keys

Validator: ✅  Tests: ✅ (<N> passed)
Cost estimate: ~$<X> (Haiku: <N> keys; Sonnet review: <N> data_description keys or "none — Sync")

Warnings:
- <any placeholder preservation issues, or "none">
```

Drop and Status operations use a shorter free-form report.
