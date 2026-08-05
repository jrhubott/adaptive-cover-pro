---
name: acp-translate
description: Sync DE/FR translations with en.json, add a new language, drop a language, or check translation status for the Adaptive Cover Pro integration. Triggers on phrases like "sync translations", "update translations", "add [language]", "drop [language]", "translate", "translation status", "retranslate".
---

# ACP Translate

Maintains **three parallel translation bundles**, each with `en.json` as the single source of truth and every other language matching its structure exactly:

1. **`custom_components/adaptive_cover_pro/translations/`** — the standard HA translation files (`title`, `config`, `options`, `entity`, `selector`, `services`). Validated by hassfest against HA's strict schema.
2. **`custom_components/adaptive_cover_pro/summary_i18n/`** — the config-summary label bundle (`en.json` / `de.json` / `fr.json`), a nested tree of dotted-key → template strings consumed by `_build_config_summary` in `config_flow.py`. This lives **outside** `translations/` on purpose: it is a custom `config_summary` category that hassfest's schema forbids as a top-level key in `translations/en.json`, so it is loaded directly by `_load_summary_labels` instead of via `async_get_translations`.
3. **`custom_components/adaptive_cover_pro/reason_i18n/`** — the pipeline-reason label bundle (`en.json` / `de.json` / `fr.json`), a nested tree of dotted reason-code → Python `str.format` template strings consumed by the pure `reason_i18n.py` resolver to localize pipeline decision-trace reasons, position explanations, and control-state reasons on `sensor.*` attributes and for the companion Lovelace card. This also lives **outside** `translations/` on purpose: it is not an HA translation category, so it is loaded directly by `reason_i18n.py`'s resolver instead of via `async_get_translations`.

⚠️ **Every operation below applies to ALL THREE directories.** When syncing, adding, or dropping a language, process `translations/<lang>.json`, `summary_i18n/<lang>.json`, _and_ `reason_i18n/<lang>.json`. The `summary_i18n/en.json` source of truth must stay byte-identical (flattened) to the code-owned `_SUMMARY_LABELS_EN` (config_flow.py) + `COVER_TYPE_LABELS_EN` / `GEOMETRY_LABELS_EN` (cover_types/\_summary_labels.py) dicts — a drift guard in `tests/test_config_flow_summary_i18n.py` / `tests/test_policy_summary_i18n.py` enforces this. Likewise, the `reason_i18n/en.json` source of truth must stay byte-identical (flattened) to the code-owned `_REASON_TEMPLATES_EN` dict (`reason_i18n.py`) — a drift guard in `tests/test_reason_i18n.py` enforces this. If you change those code dicts, regenerate `summary_i18n/en.json` and/or `reason_i18n/en.json` first, then sync de/fr.

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

> **All three bundles.** Run the diff + dispatch for `translations/`, `summary_i18n/`, **and** `reason_i18n/`. A given language's three files can have independent deltas — compute and propagate each separately. The `summary_i18n` files are small nested trees (flatten the same way); their dotpaths look like `rules.force`, `cover_types.blind`, `geometry.slat.depth`. The `reason_i18n` files are likewise small nested trees; their dotpaths look like `solar.tracking`, `manual.holding_label`, `fragment.season_summer`, `engine.direct_sun`.

1. **Diff.** Load all translation files (all three directories) via Bash+Python (see **Reading Translation Files** — do NOT use the Read tool). Flatten each to dot-path keys. For each non-en file compute:

   - `added`: keys in en but not in target
   - `removed`: keys in target but not in en
   - `changed`: keys where the en value text changed since the target was last generated. Detect by heuristic: if `target[k]` looks like an obvious placeholder (equals `en[k]` verbatim, or is a short English phrase when the rest of the file is clearly in the target language), treat it as changed. When unsure, retranslate — cost of a re-translation is negligible.

2. **If nothing to do**, report "DE/FR already in sync with en.json" and exit.

3. **Dispatch one subagent per language in parallel** (single message, two `Agent` tool calls). Each subagent handles all THREE of that language's files (`translations/<lang>.json`, `summary_i18n/<lang>.json`, and `reason_i18n/<lang>.json`) and receives:

   - Absolute paths to `translations/en.json` + `summary_i18n/en.json` + `reason_i18n/en.json` and to its own three target files
   - The list of `added` + `changed` dotpaths to translate, per file
   - The list of `removed` dotpaths to strip from each target
   - The language name and ISO code

   The subagent's job (Haiku only — no Sonnet review for Sync):

   - Extract the values at the requested dotpaths from en.json via Bash+Python (see **Reading Translation Files** — do NOT use the Read tool directly on these files).
   - Run the Haiku translation prompt (see **Subagent Prompt Templates**) on all of them.
   - Load the target file, merge in the new values, delete `removed` keys, and write it back via Bash+Python.
   - Return a one-paragraph summary: counts added/changed/removed, any placeholder-preservation warnings, cost estimate.

4. **Verify.** Run `./scripts/validate_translations.py --ci` (covers `translations/` only — it does not validate `summary_i18n/` or `reason_i18n/`) and `venv/bin/python -m pytest tests/test_translations.py tests/test_config_flow_summary_i18n.py tests/test_policy_summary_i18n.py tests/test_reason_i18n.py -q`. If either fails, report the failure verbatim and stop. Do not attempt a second auto-sync round.

5. **Report.** Use the Output Format below.

---

## Operation 2 — Add Language

Use to rebuild DE/FR from scratch **or** to add a brand-new language.

### Steps

1. **Validate.** The language code must be a valid HA locale (BCP-47 form: `de`, `fr`, `es`, `pt-BR`, `zh-Hans`, etc.). If `en`, refuse — we don't retranslate English. If the file already exists and the user did not say "rebuild" or "retranslate", confirm they want to overwrite.

2. **Delete the existing file** if rebuilding, so the subagent produces a clean file.

3. **Dispatch one subagent per requested language in parallel.** If the user says "add DE and FR", send a single message with two `Agent` tool calls. Each subagent builds all THREE files for its language and receives:

   - Absolute paths to `translations/en.json`, `summary_i18n/en.json`, AND `reason_i18n/en.json`
   - Absolute paths to the three target files it must write (`translations/<lang>.json`, `summary_i18n/<lang>.json`, `reason_i18n/<lang>.json`)
   - Language name + ISO code
   - The domain-term glossary (see below) — customized per language

   The subagent's job:

   - Load the full `translations/en.json`, `summary_i18n/en.json`, and `reason_i18n/en.json` trees via Bash+Python (see **Reading Translation Files**).
   - **Pass 1 — Haiku bulk:** Run the Haiku translation prompt on all keys from all three files. Capture output as a JSON object.
   - **Pass 2 — Sonnet review (data_descriptions only):** Filter the Haiku output to include ONLY keys containing `.data_description.` (these exist only in `translations/`, not `summary_i18n/` or `reason_i18n/`) — do not send any other keys to Sonnet. `reason_i18n` has no `data_description` keys at all, so it never enters the Sonnet pass — it stays Haiku-only, same as summary_i18n's non-data_description keys. Run the Sonnet review prompt on this filtered subset. Merge corrected values back into the Haiku output.
   - Write `translations/<lang>.json`, `summary_i18n/<lang>.json`, AND `reason_i18n/<lang>.json` via Bash+Python: each with the same nested structure as its en.json source, 2-space indent, `ensure_ascii=False`, trailing newline.
   - Return a summary: key counts written per file, how many data_description keys were reviewed by Sonnet, placeholder warnings, cost estimate.

4. **Update tooling.** After subagents return:

   - Add the language code to the `LANGUAGES` constant in `scripts/validate_translations.py` (unless already present).
   - Update any per-language lists in `tests/test_translations.py`.

5. **Verify.** Run `./scripts/validate_translations.py --ci` (covers `translations/` only — it does not validate `summary_i18n/` or `reason_i18n/`) and `venv/bin/python -m pytest tests/test_translations.py tests/test_config_flow_summary_i18n.py tests/test_policy_summary_i18n.py tests/test_reason_i18n.py -q`. Do not proceed to commit if either fails.

6. **Report.** If the language is new (not in the previously-shipped set), remind the user to update README's supported-languages list and add a release-notes line.

---

## Operation 3 — Drop Language

1. Confirm the language is not `en`, `de`, or `fr`. If the user asks to drop one of the core three, explicitly confirm with them before proceeding (this changes the officially supported set).
2. Delete ALL THREE of `translations/<lang>.json`, `summary_i18n/<lang>.json`, and `reason_i18n/<lang>.json`.
3. Remove the code from `scripts/validate_translations.py` `LANGUAGES` list.
4. Remove any language-specific expectations from `tests/test_translations.py`.
5. Run `venv/bin/python -m pytest tests/test_translations.py tests/test_config_flow_summary_i18n.py tests/test_policy_summary_i18n.py tests/test_reason_i18n.py -q` to confirm.
6. Report what was removed.

---

## Operation 4 — Status

1. Run `./scripts/validate_translations.py` (no flags) and show its dashboard output. This covers `translations/` only — it does not validate `summary_i18n/` or `reason_i18n/`.
2. Run `venv/bin/python -m pytest tests/test_translations.py tests/test_config_flow_summary_i18n.py tests/test_policy_summary_i18n.py tests/test_reason_i18n.py -q` and show pass/fail counts (covers `translations/`, `summary_i18n/`, and `reason_i18n/` parity + the en-source drift guards).
3. No subagents, no writes.

---

## Reading Translation Files

⚠️ **The `translations/` JSON files exceed the Read tool's 25,000-token limit. Never use the Read tool directly on `translations/en.json`, `de.json`, or `fr.json`.** Use Bash+Python instead. The `summary_i18n/` and `reason_i18n/` files are smaller (~10 KB) and Read-safe, but use the same Bash+Python flatten/merge flow for consistency and to keep the write format identical (2-space indent, `ensure_ascii=False`, trailing newline).

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
- occupancy → Anwesenheit / Belegung
- acceptance angle → Akzeptanzwinkel
- sunset → Sonnenuntergang

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
- occupancy → présence / occupation
- acceptance angle → angle d'acceptation
- sunset → coucher du soleil

For other languages, tell the subagent to "use the HA community standard translation of these terms for <language>; when in doubt prefer the shortest unambiguous term."

---

## File Format

Non-EN files (in ALL THREE of `translations/`, `summary_i18n/`, and `reason_i18n/`) must:

- Be valid JSON, 2-space indent.
- Use `ensure_ascii=False` (keep accented characters as-is, not `\uXXXX`).
- End with exactly one trailing newline.
- Preserve the matching en.json's nested structure exactly — for `translations/` every top-level section including `services`; for `summary_i18n/` the full nested label tree (`rules`, `weather`, `cover_types`, `geometry`, …); for `reason_i18n/` the full nested reason-code tree (`solar`, `manual`, `fragment`, `engine`, …).
- Contain no `mdi:` icon references, no zero-width characters, no empty string values.

⚠️ **Placeholder parity is critical for `summary_i18n/` and `reason_i18n/`.** Each label is a Python `str.format` template; the translated value must carry the IDENTICAL set of `{field}` placeholders — including any format specs, e.g. `{distance:.2f}` — and escaped literal `{{`/`}}` as the English source, or the consuming code raises at render time (`_build_config_summary` for `summary_i18n/`; the `reason_i18n.py` resolver for `reason_i18n/`). `tests/test_config_flow_summary_i18n.py` enforces this per key for `summary_i18n/`; `tests/test_reason_i18n.py` (`test_reason_placeholder_parity_de_fr`) enforces it for `reason_i18n/`.

---

## Safety Rules

- **Never delete `en.json`** (in any of `translations/`, `summary_i18n/`, or `reason_i18n/`).
- **Never write a translation file without running the validator and tests afterwards.**
- **Never silently drop keys from a target file.** If a key was removed from en.json, the Sync operation must list it under "removed" in the report.
- **If Haiku output fails JSON parse**, retry once; if still failing, dispatch Sonnet for that batch as a fallback. Do not return partial results.
- **Respect the cost budget.** If a projected run exceeds $0.50 for one language, stop and ask the user.

---

## Output Format

After any Sync or Add run, report:

```
Translation <sync|add> complete (branch: <current-branch>)

translations/  en: <N> keys (unchanged)
  de.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
  fr.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
summary_i18n/  en: <N> keys (unchanged)
  de.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
  fr.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
reason_i18n/  en: <N> keys (unchanged)
  de.json: +<A> added, ~<C> changed, -<R> removed → <T> keys
  fr.json: +<A> added, ~<C> changed, -<R> removed → <T> keys

Validator: ✅  Tests: ✅ (<N> passed)
Cost estimate: ~$<X> (Haiku: <N> keys; Sonnet review: <N> data_description keys or "none — Sync")

Warnings:
- <any placeholder preservation issues, or "none">
```

Drop and Status operations use a shorter free-form report.
